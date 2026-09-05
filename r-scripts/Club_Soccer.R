library(dplyr)
library(ggplot2)
library(stringr)

# ==============================================================================
# 1. SETUP & UTILITIES
# ==============================================================================
if (!dir.exists("Soccer_Club/Rankings")) dir.create("Soccer_Club/Rankings", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

# Custom function to safely map columns without crashing if multiple matches exist
map_column <- function(df, new_col, possible_names) {
  match_col <- intersect(possible_names, colnames(df))
  if (length(match_col) > 0) {
    df[[new_col]] <- df[[match_col[1]]]
  } else {
    df[[new_col]] <- NA
  }
  return(df)
}

# Function to force exact data types before bind_rows to prevent crashes
enforce_schema <- function(df) {
  if (nrow(df) > 0 && "Player" %in% colnames(df)) {
    df %>% mutate(
      Player = as.character(Player),
      Squad  = as.character(Squad),
      Comp   = as.character(Comp),
      Season = as.character(Season),
      Pos    = as.character(Pos),
      Min    = as.numeric(Min),
      Gls    = as.numeric(Gls),
      Ast    = as.numeric(Ast),
      CrdY   = as.numeric(CrdY),
      CrdR   = as.numeric(CrdR)
    )
  } else {
    df
  }
}

# ==============================================================================
# 2. TEAM SCORING (Matches)
# ==============================================================================
message("Loading Team Matches and Assigning Cup/Europe points to Domestic Clubs...")

# A. Load European Matches
euro_matches <- data.frame()
if (file.exists("football_matches.csv")) {
  matches_raw <- read.csv("football_matches.csv", stringsAsFactors = FALSE)
  if (nrow(matches_raw) > 0) {
    home_m <- matches_raw %>% 
      select(date = utcDate, comp = competition.name, team = homeTeam.name, gf = score.fullTime.home, ga = score.fullTime.away, status) %>%
      mutate(date = as.character(date), gf = as.numeric(str_extract(gf, "^\\d+")), ga = as.numeric(str_extract(ga, "^\\d+")))
    
    away_m <- matches_raw %>% 
      select(date = utcDate, comp = competition.name, team = awayTeam.name, gf = score.fullTime.away, ga = score.fullTime.home, status) %>%
      mutate(date = as.character(date), gf = as.numeric(str_extract(gf, "^\\d+")), ga = as.numeric(str_extract(ga, "^\\d+")))
    
    euro_matches <- bind_rows(home_m, away_m) %>% 
      filter((status == "FINISHED" | is.na(status)) & !is.na(gf) & !is.na(ga)) %>%
      select(-status)
  }
}

# B. Load MLS Matches
mls_matches <- data.frame()
if (file.exists("mls_results.csv")) {
  mls_raw <- read.csv("mls_results.csv", stringsAsFactors = FALSE)
  if (nrow(mls_raw) > 0) {
    home_mls <- mls_raw %>% 
      select(date, team = home_team, gf = home_score, ga = away_score) %>% 
      mutate(comp = "MLS", date = as.character(date), gf = as.numeric(str_extract(gf, "^\\d+")), ga = as.numeric(str_extract(ga, "^\\d+")))
    
    away_mls <- mls_raw %>% 
      select(date, team = away_team, gf = away_score, ga = home_score) %>% 
      mutate(comp = "MLS", date = as.character(date), gf = as.numeric(str_extract(gf, "^\\d+")), ga = as.numeric(str_extract(ga, "^\\d+")))
    
    mls_matches <- bind_rows(home_mls, away_mls) %>% filter(!is.na(gf) & !is.na(ga))
  }
}

# C. Load NWSL Matches
nwsl_matches <- data.frame()
if (file.exists("shooting.csv")) {
  nwsl_raw <- read.csv("shooting.csv", stringsAsFactors = FALSE)
  if (nrow(nwsl_raw) > 0) {
    nwsl_matches <- nwsl_raw %>% 
      select(date, team, gf, ga) %>% 
      mutate(comp = "NWSL", date = as.character(date), gf = as.numeric(str_extract(gf, "^\\d+")), ga = as.numeric(str_extract(ga, "^\\d+"))) %>% 
      filter(!is.na(gf) & !is.na(ga))
  }
}

all_team_matches <- bind_rows(euro_matches, mls_matches, nwsl_matches)

if (nrow(all_team_matches) > 0) {
  target_leagues <- "Premier League|Primera Division|Serie A|Bundesliga|Ligue 1|MLS|NWSL"
  
  team_league_map <- all_team_matches %>%
    filter(grepl(target_leagues, comp, ignore.case = TRUE)) %>%
    group_by(team) %>%
    count(primary_league = comp) %>%
    slice_max(n, n = 1, with_ties = FALSE) %>%
    select(team, primary_league) %>%
    mutate(primary_league = case_when(
      grepl("Primera Division", primary_league, ignore.case = TRUE) ~ "La Liga",
      grepl("Premier League", primary_league, ignore.case = TRUE) ~ "Premier League",
      TRUE ~ primary_league
    ))
  
  scored_teams <- all_team_matches %>%
    inner_join(team_league_map, by = "team") %>%
    mutate(
      match_month = as.numeric(str_sub(date, 6, 7)),
      match_year = as.numeric(str_sub(date, 1, 4)),
      season_year = ifelse(match_month > 7 & !grepl("MLS|NWSL", primary_league), match_year + 1, match_year),
      
      is_win = gf > ga,
      margin = gf - ga,
      clean_sheet = (ga == 0),
      
      base_win_pts = case_when(
        !is_win ~ 0,
        grepl("Champions League|Play-off|Playoff", comp, ignore.case = TRUE) ~ 5,
        grepl("Europa League|Conference|Cup|Pokal|Coppa|Copa|Trophy", comp, ignore.case = TRUE) ~ 4,
        TRUE ~ 3
      ),
      
      fantasy_pts = base_win_pts + ifelse(is_win & margin >= 2, 1, 0) + ifelse(is_win & clean_sheet, 1, 0)
    )
  
  team_season_totals <- scored_teams %>%
    group_by(primary_league, team, season_year) %>%
    summarise(matches_played = n(), total_fantasy_pts = sum(fantasy_pts, na.rm = TRUE), .groups = "drop") %>%
    filter(matches_played >= 10)
  
  write.csv(team_season_totals, "Soccer_Club/Rankings/club_team_totals.csv", row.names = FALSE)
  
  plot_teams <- ggplot(team_season_totals, aes(x = total_fantasy_pts, fill = primary_league)) +
    geom_density(alpha = 0.6) +
    facet_wrap(~ primary_league, ncol = 4, scales = "free_y") +
    theme_minimal() +
    labs(
      title = "Club Soccer Team Fantasy Points Distribution",
      subtitle = "All Historical Team-Seasons (Base Wins, Big Wins, Clean Sheets, Cup/UCL Multipliers)",
      x = "Total Team Fantasy Points", y = "Density", fill = "League"
    ) +
    theme(legend.position = "bottom")
  
  ggsave("Soccer_Club/Rankings/club_team_distribution.png", plot = plot_teams, width = 14, height = 8, dpi = 300)
}

# ==============================================================================
# 3. PLAYER SCORING
# ==============================================================================
message("Scoring Players (FPL Rules)...")

player_men_clean <- data.frame()
if (file.exists("fifa_fbref_merged.csv")) {
  player_raw_men <- read.csv("fifa_fbref_merged.csv", stringsAsFactors = FALSE)
  if (nrow(player_raw_men) > 0) {
    p_men <- map_column(player_raw_men, "Player", c("player", "short_name", "player_name"))
    p_men <- map_column(p_men, "Squad", c("club_name", "team", "squad"))
    p_men <- map_column(p_men, "Comp", c("league_name", "league", "comp"))
    p_men <- map_column(p_men, "Season", c("season", "year"))
    p_men <- map_column(p_men, "Pos", c("player_positions", "pos", "position"))
    p_men <- map_column(p_men, "Min", c("Playing.Time_Min", "Playing Time_Min", "min", "minutes"))
    p_men <- map_column(p_men, "Ninetys", c("Playing.Time_90s", "Playing Time_90s", "X90s", "90s"))
    p_men <- map_column(p_men, "Gls_90", c("Per.90.Minutes_Gls", "Per 90 Minutes_Gls", "Gls_90"))
    p_men <- map_column(p_men, "Ast_90", c("Per.90.Minutes_Ast", "Per 90 Minutes_Ast", "Ast_90"))
    p_men <- map_column(p_men, "CrdY_90", c("Per.90.Minutes_CrdY", "Per 90 Minutes_CrdY", "CrdY_90"))
    p_men <- map_column(p_men, "CrdR_90", c("Per.90.Minutes_CrdR", "Per 90 Minutes_CrdR", "CrdR_90"))
    
    player_men_clean <- p_men %>%
      filter(!is.na(Player) & !is.na(Min) & as.numeric(Min) > 0 & !is.na(Ninetys)) %>%
      mutate(
        Min = as.numeric(Min),
        Ninetys = as.numeric(Ninetys),
        Gls = round(Ninetys * as.numeric(str_extract(Gls_90, "^\\d+\\.?\\d*"))),
        Ast = round(Ninetys * as.numeric(str_extract(Ast_90, "^\\d+\\.?\\d*"))),
        CrdY = round(Ninetys * as.numeric(str_extract(CrdY_90, "^\\d+\\.?\\d*"))),
        CrdR = round(Ninetys * as.numeric(str_extract(CrdR_90, "^\\d+\\.?\\d*")))
      ) %>%
      select(Player, Squad, Comp, Season, Pos, Min, Gls, Ast, CrdY, CrdR) %>%
      enforce_schema()
  }
}

player_nwsl_clean <- data.frame()
if (file.exists("nwsl_stats.csv")) {
  player_raw_nwsl <- read.csv("nwsl_stats.csv", stringsAsFactors = FALSE)
  if (nrow(player_raw_nwsl) > 0) {
    p_nwsl <- map_column(player_raw_nwsl, "Player", c("Player", "player", "player_name"))
    p_nwsl <- map_column(p_nwsl, "Squad", c("Squad", "squad", "team"))
    p_nwsl <- map_column(p_nwsl, "Pos", c("Pos", "pos", "position"))
    p_nwsl <- map_column(p_nwsl, "Ninetys", c("X90s", "90s"))
    p_nwsl <- map_column(p_nwsl, "Gls", c("Gls", "gls", "goals"))
    
    # Calculate Min if missing but 90s exists
    if (!"Min" %in% colnames(p_nwsl) && "Ninetys" %in% colnames(p_nwsl)) {
      p_nwsl <- p_nwsl %>% mutate(Min = as.numeric(Ninetys) * 90)
    } else {
      p_nwsl <- map_column(p_nwsl, "Min", c("Min", "min", "minutes"))
    }
    
    p_nwsl <- map_column(p_nwsl, "Ast", c("Ast", "ast", "assists"))
    p_nwsl <- map_column(p_nwsl, "CrdY", c("CrdY", "crdy", "yellow_cards"))
    p_nwsl <- map_column(p_nwsl, "CrdR", c("CrdR", "crdr", "red_cards"))
    
    player_nwsl_clean <- p_nwsl %>%
      mutate(Comp = "NWSL", Season = "2025")
    
    for (col in c("Min", "Gls", "Ast", "CrdY", "CrdR")) {
      if (!col %in% colnames(player_nwsl_clean)) player_nwsl_clean[[col]] <- 0
    }
    
    player_nwsl_clean <- player_nwsl_clean %>%
      mutate(across(c(Min, Gls, Ast, CrdY, CrdR), ~as.numeric(str_extract(as.character(.), "^\\d+\\.?\\d*")))) %>%
      mutate(across(c(Min, Gls, Ast, CrdY, CrdR), ~ifelse(is.na(.), 0, .))) %>%
      filter(!is.na(Player) & Min > 0) %>%
      select(Player, Squad, Comp, Season, Pos, Min, Gls, Ast, CrdY, CrdR) %>%
      enforce_schema()
  }
}

player_combined <- bind_rows(player_men_clean, player_nwsl_clean)

if (nrow(player_combined) > 0) {
  player_scored <- player_combined %>%
    mutate(
      Pos = ifelse(is.na(Pos), "FW", as.character(Pos)),
      primary_pos = toupper(str_sub(Pos, 1, 2)),
      
      pts_minutes = ifelse(Min >= 60, 2, 1),
      pts_goals = case_when(
        primary_pos %in% c("DF", "CB", "RB", "LB", "GK") ~ Gls * 6,
        primary_pos %in% c("MF", "CM", "CD", "LM", "RM") ~ Gls * 5,
        TRUE ~ Gls * 4
      ),
      pts_assists = Ast * 3,
      pts_cards = (CrdY * -1) + (CrdR * -3),
      
      total_fantasy_pts = pts_minutes + pts_goals + pts_assists + pts_cards,
      
      Comp_clean = case_when(
        grepl("ENG-Premier|Premier League", Comp, ignore.case = TRUE) ~ "Premier League",
        grepl("ESP-La Liga|La Liga|Primera", Comp, ignore.case = TRUE) ~ "La Liga",
        grepl("ITA-Serie A|Serie A", Comp, ignore.case = TRUE) ~ "Serie A",
        grepl("GER-Bundesliga|Bundesliga", Comp, ignore.case = TRUE) ~ "Bundesliga",
        grepl("FRA-Ligue 1|Ligue 1", Comp, ignore.case = TRUE) ~ "Ligue 1",
        grepl("USA-MLS|MLS|Major League", Comp, ignore.case = TRUE) ~ "MLS",
        grepl("NWSL", Comp, ignore.case = TRUE) ~ "NWSL",
        TRUE ~ "Other"
      )
    ) %>%
    filter(Comp_clean != "Other")
  
  top_players <- player_scored %>%
    group_by(Comp_clean, Player) %>%
    slice_max(total_fantasy_pts, n = 1, with_ties = FALSE) %>%
    ungroup() %>%
    group_by(Comp_clean) %>%
    slice_max(total_fantasy_pts, n = 50, with_ties = FALSE) %>%
    ungroup()
  
  if (nrow(top_players) > 0) {
    write.csv(top_players, "Soccer_Club/Rankings/club_player_totals.csv", row.names = FALSE)
    
    plot_players <- ggplot(top_players, aes(x = total_fantasy_pts, fill = Comp_clean)) +
      geom_density(alpha = 0.6) +
      facet_wrap(~ Comp_clean, ncol = 4, scales = "free_y") +
      scale_fill_viridis_d(option = "plasma") + 
      theme_minimal() +
      labs(
        title = "Club Soccer Player Fantasy Points Distribution",
        subtitle = "Top 50 Players per League's Best Single Season (Goals, Assists, Mins, Cards)",
        x = "Total Player Fantasy Points", y = "Density", fill = "League"
      ) +
      theme(legend.position = "bottom")
    
    ggsave("Soccer_Club/Rankings/club_player_distribution.png", plot = plot_players, width = 14, height = 8, dpi = 300)
  }
} else {
  message("Warning: No player datasets loaded successfully.")
}

message("Club Soccer Engine complete! Raw scores and plots saved to Soccer_Club/Rankings/")

# ==============================================================================
# 4. MASTER CSV EXPORT (PLAYERS & TEAMS)
# ==============================================================================

# A. Export Players to Master CSV
if (exists("top_players") && nrow(top_players) > 0) {
  master_players_file <- "Master_Data/master_players.csv"
  
  # Export the Peak single-season (Top 50 per league) into the Master CSV
  recent_club_players <- top_players %>%
    transmute(
      Player = Player,
      Team = Squad,
      League = Comp_clean,
      Role = primary_pos,
      Season = Season,
      Total_Points = total_fantasy_pts
    )
  
  leagues_to_add_p <- unique(recent_club_players$League)
  
  if (file.exists(master_players_file)) {
    master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
    if (!any(master_players$League %in% leagues_to_add_p)) {
      write.table(recent_club_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
      message(paste("Successfully appended", nrow(recent_club_players), "Club Soccer players across multiple leagues to Master CSV."))
    } else {
      message("Club Soccer Player data for these leagues already exists in Master CSV. Skipping append.")
    }
  } else {
    write.csv(recent_club_players, master_players_file, row.names = FALSE)
    message("Created Master_Data/master_players.csv and added Club Soccer players.")
  }
}

# B. Export Teams to Master CSV
if (exists("team_season_totals") && nrow(team_season_totals) > 0) {
  master_teams_file <- "Master_Data/master_teams.csv"
  
  # Extract the most recent season available PER LEAGUE
  recent_club_teams <- team_season_totals %>%
    group_by(primary_league) %>%
    filter(season_year == max(season_year, na.rm = TRUE)) %>%
    ungroup() %>%
    transmute(
      Team = team,
      League = primary_league,
      Season = season_year,
      Total_Points = total_fantasy_pts
    )
  
  leagues_to_add_t <- unique(recent_club_teams$League)
  
  if (file.exists(master_teams_file)) {
    master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
    if (!any(master_teams$League %in% leagues_to_add_t)) {
      write.table(recent_club_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
      message(paste("Successfully appended", nrow(recent_club_teams), "Club Soccer teams across multiple leagues to Master CSV."))
    } else {
      message("Club Soccer Team data for these leagues already exists in Master CSV. Skipping append.")
    }
  } else {
    write.csv(recent_club_teams, master_teams_file, row.names = FALSE)
    message("Created Master_Data/master_teams.csv and added Club Soccer teams.")
  }
}

message("Club Soccer Master CSV exports complete!")