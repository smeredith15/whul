library(wehoop)
library(dplyr)
library(ggplot2)
library(tidyr)
library(jsonlite)

# ==============================================================================
# 1. BULLETPROOF HELPER FUNCTIONS
# ==============================================================================
clean_names <- function(df) {
  if (inherits(df, "data.frame")) df <- jsonlite::flatten(df)
  names(df) <- tolower(gsub("[^a-zA-Z0-9_]+", "_", names(df)))
  names(df) <- gsub("^_+|_+$", "", names(df))
  return(df)
}

get_col <- function(df, candidate_cols, default = NA) {
  found <- intersect(candidate_cols, names(df))
  if (length(found) == 0) return(as.character(rep(default, nrow(df))))
  cols <- lapply(found, function(c) as.character(df[[c]]))
  do.call(coalesce, cols)
}

if (!dir.exists("WNBA/Players")) dir.create("WNBA/Players", recursive = TRUE)
if (!dir.exists("WNBA/Teams")) dir.create("WNBA/Teams", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

seasons_to_pull <- 2020:2025

# ==============================================================================
# 2. WNBA TEAM SCORING & DISTRIBUTION
# ==============================================================================
message("Loading WNBA Schedules (2020-2025)...")
schedules_raw <- load_wnba_schedule(seasons = seasons_to_pull) %>% clean_names()

schedules <- schedules_raw %>%
  mutate(
    raw_season = suppressWarnings(as.numeric(get_col(., c("season", "season_year", "year")))),
    game_id_clean = get_col(., c("game_id", "id", "event_id")),
    game_date_str = get_col(., c("game_date", "date", "game_date_time")),
    game_date_clean = as.Date(substr(game_date_str, 1, 10)),
    game_year = as.numeric(format(game_date_clean, "%Y")),
    season = ifelse(!is.na(raw_season), raw_season, game_year),
    
    st_num = suppressWarnings(as.numeric(get_col(., c("season_type", "season_type_id", "type", "game_type")))),
    season_type_clean = ifelse(is.na(st_num), 2, st_num),
    
    home_score_num = suppressWarnings(as.numeric(get_col(., c("home_score", "home_team_score", "home_points", "home_pts", "home_score_value")))),
    away_score_num = suppressWarnings(as.numeric(get_col(., c("away_score", "away_team_score", "away_points", "away_pts", "away_score_value")))),
    
    home_team_clean = get_col(., c("home_abbreviation", "home_team_abbrev", "home_team_abbreviation", "home_short_display_name", "home_display_name", "home_name")),
    away_team_clean = get_col(., c("away_abbreviation", "away_team_abbrev", "away_team_abbreviation", "away_short_display_name", "away_display_name", "away_name")),
    notes_clean = if ("notes_headline" %in% names(.)) ifelse(is.na(notes_headline), "", notes_headline) else ""
  ) %>%
  filter(!is.na(season) & season %in% seasons_to_pull)

completed_games <- schedules %>%
  filter(!is.na(home_score_num) & !is.na(away_score_num) & (home_score_num + away_score_num > 0))

home_games <- completed_games %>%
  transmute(
    season, game_id = game_id_clean, game_date = game_date_clean, season_type = season_type_clean,
    team = home_team_clean, opp = away_team_clean,
    pts_for = home_score_num, pts_against = away_score_num, notes = notes_clean
  )

away_games <- completed_games %>%
  transmute(
    season, game_id = game_id_clean, game_date = game_date_clean, season_type = season_type_clean,
    team = away_team_clean, opp = home_team_clean,
    pts_for = away_score_num, pts_against = home_score_num, notes = notes_clean
  )

team_games <- bind_rows(home_games, away_games) %>%
  filter(!is.na(team) & team != "") %>%
  mutate(
    margin = pts_for - pts_against,
    is_win = margin > 0,
    is_big_win = margin >= 15,
    is_reg = season_type == 2 | (!grepl("Playoff|Finals", notes, ignore.case = TRUE) & season_type != 3),
    is_playoff = season_type == 3 | grepl("Playoff|Finals", notes, ignore.case = TRUE),
    is_comm_cup = grepl("Commissioner's Cup", notes, ignore.case = TRUE)
  )

team_season_summary <- team_games %>%
  group_by(season, team) %>%
  summarise(
    games_played = n(),
    reg_wins = sum(is_win & is_reg, na.rm = TRUE),
    reg_big_wins = sum(is_big_win & is_reg, na.rm = TRUE),
    point_diff = sum(margin[is_reg], na.rm = TRUE),
    playoff_appearance = ifelse(any(is_playoff, na.rm = TRUE), 1, 0),
    playoff_wins = sum(is_win & is_playoff, na.rm = TRUE),
    playoff_series_wins = floor(playoff_wins / 3), # WNBA best-of series approximation
    comm_cup_wins = sum(is_win & is_comm_cup, na.rm = TRUE),
    comm_cup_champ = ifelse(any(is_win & is_comm_cup & grepl("Championship|Final", notes, ignore.case = TRUE), na.rm = TRUE), 1, 0),
    .groups = "drop"
  ) %>%
  filter(games_played >= 10)

team_scored <- team_season_summary %>%
  mutate(
    pts_wins = reg_wins * 2,
    pts_big_wins = reg_big_wins * 1,
    pts_playoff_app = playoff_appearance * 10,
    pts_playoff_wins = playoff_wins * 3,
    pts_series_wins = playoff_series_wins * 5,
    pts_comm_cup_wins = comm_cup_wins * 2,
    pts_comm_cup_champ = comm_cup_champ * 8,
    pts_point_diff = point_diff * 0.05,
    
    total_team_fantasy_pts = pts_wins + pts_big_wins + pts_playoff_app + 
      pts_playoff_wins + pts_series_wins + 
      pts_comm_cup_wins + pts_comm_cup_champ + pts_point_diff
  )

message("\nFinal Scored WNBA Teams Per Season:")
print(table(team_scored$season))

plot_team_overall <- ggplot(team_scored, aes(x = total_team_fantasy_pts)) +
  geom_histogram(aes(y = after_stat(density)), binwidth = 10, fill = "#FF671F", color = "white", alpha = 0.7) +
  geom_density(color = "#000000", linewidth = 1.2) + theme_minimal() +
  labs(title = "WNBA Team Fantasy Points Distribution (2020-2025)", x = "Total Points", y = "Density")

ggsave("WNBA/Teams/team_overall_distribution.png", plot = plot_team_overall, width = 9, height = 6, dpi = 300)

# ==============================================================================
# 3. WNBA PLAYER SCORING
# ==============================================================================
message("\nLoading WNBA Player Box Scores (2020-2025)...")
player_box_raw <- load_wnba_player_box(seasons = seasons_to_pull) %>% clean_names()

season_map <- schedules %>% select(game_id = game_id_clean, corrected_season = season) %>% distinct()

player_season <- player_box_raw %>%
  mutate(
    raw_season = suppressWarnings(as.numeric(get_col(., c("season", "season_year", "year")))),
    game_id_clean = get_col(., c("game_id", "event_id", "id")),
    athlete_id_clean = get_col(., c("athlete_id", "athlete_id_num", "player_id", "id")),
    athlete_name = get_col(., c("athlete_display_name", "athlete_name", "player_name", "name", "athlete_short_name")),
    pts = suppressWarnings(as.numeric(get_col(., c("points", "pts", "athlete_points", "score")))),
    reb = suppressWarnings(as.numeric(get_col(., c("rebounds", "reb", "total_rebounds", "tot_reb")))),
    ast = suppressWarnings(as.numeric(get_col(., c("assists", "ast")))),
    stl = suppressWarnings(as.numeric(get_col(., c("steals", "stl")))),
    blk = suppressWarnings(as.numeric(get_col(., c("blocks", "blk")))),
    to  = suppressWarnings(as.numeric(get_col(., c("turnovers", "to", "turnover")))),
    fg3 = suppressWarnings(as.numeric(get_col(., c("three_point_field_goals_made", "fg3m", "three_points_made")))),
    pm  = suppressWarnings(as.numeric(get_col(., c("plus_minus", "pm")))),
    pos = get_col(., c("athlete_position_abbreviation", "position_abbreviation", "pos", "position")),
    pos = ifelse(is.na(pos) | pos == "", "G/F", pos)
  ) %>%
  left_join(season_map, by = c("game_id_clean" = "game_id")) %>%
  mutate(season = ifelse(!is.na(raw_season), raw_season, corrected_season)) %>%
  filter(!is.na(athlete_id_clean) & !is.na(pts) & !is.na(season) & season %in% seasons_to_pull) %>%
  mutate(
    reb = ifelse(is.na(reb), 0, reb), ast = ifelse(is.na(ast), 0, ast), stl = ifelse(is.na(stl), 0, stl),
    blk = ifelse(is.na(blk), 0, blk), to = ifelse(is.na(to), 0, to), fg3 = ifelse(is.na(fg3), 0, fg3), pm = ifelse(is.na(pm), 0, pm),
    
    dd = ifelse((pts >= 10) + (reb >= 10) + (ast >= 10) + (stl >= 10) + (blk >= 10) >= 2, 1, 0),
    td = ifelse((pts >= 10) + (reb >= 10) + (ast >= 10) + (stl >= 10) + (blk >= 10) >= 3, 1, 0),
    game_fantasy_pts = (pts * 1) + (reb * 1.2) + (ast * 1.5) + (stl * 3) + (blk * 3) + (to * -1) + (fg3 * 0.5) + (dd * 1.5) + (td * 3)
  ) %>%
  group_by(season, athlete_id = athlete_id_clean, athlete_display_name = athlete_name, pos) %>%
  summarise(
    games_played = n(),
    total_fantasy_pts = sum(game_fantasy_pts, na.rm = TRUE),
    total_plus_minus = sum(pm, na.rm = TRUE),
    final_player_score = total_fantasy_pts + (total_plus_minus * 0.1),
    .groups = "drop"
  ) %>%
  filter(games_played >= 10 & final_player_score > 30)

message("\nFinal Scored WNBA Players Per Season:")
print(table(player_season$season))

top_60_players <- player_season %>% group_by(season) %>% slice_max(final_player_score, n = 60, with_ties = FALSE)
q_pos <- player_season %>%
  mutate(pos_clean = case_when(
    pos %in% c("G", "PG", "SG") ~ "Guards (Top 24)", 
    pos %in% c("F", "SF", "PF") ~ "Forwards (Top 24)", 
    pos %in% c("C") ~ "Centers (Top 12)", 
    TRUE ~ "Flex (Top 60)")) %>%
  group_by(season, pos_clean) %>% slice_max(final_player_score, n = 24, with_ties = FALSE)

plot_player_top60 <- ggplot(top_60_players, aes(x = final_player_score, fill = factor(season))) +
  geom_density(alpha = 0.4) + facet_wrap(~ factor(season), ncol = 2) + theme_minimal() +
  labs(title = "WNBA Top 60 Players Score Distribution", x = "Total Score", y = "Density") + theme(legend.position = "none")

ggsave("WNBA/Players/top_60_overall_distribution.png", plot = plot_player_top60, width = 9, height = 10, dpi = 300)

plot_player_pos <- ggplot(q_pos, aes(x = final_player_score, fill = pos_clean)) +
  geom_density(alpha = 0.5) + facet_wrap(~ pos_clean, scales = "free_y", ncol = 2) + theme_minimal() +
  labs(title = "WNBA Positional Distributions (2020-2025)", x = "Total Score", y = "Density", fill = "Position") + theme(legend.position = "none")

ggsave("WNBA/Players/positional_distribution.png", plot = plot_player_pos, width = 11, height = 8, dpi = 300)

# ==============================================================================
# 4. MASTER CSV EXPORT (PLAYERS & TEAMS)
# ==============================================================================

# A. Export Players to Master CSV
master_players_file <- "Master_Data/master_players.csv"
max_wnba_season_player <- max(player_season$season, na.rm = TRUE)

recent_wnba_players <- player_season %>%
  filter(season == max_wnba_season_player) %>%
  transmute(
    Player = athlete_display_name,
    Team = "WNBA",
    League = "WNBA",
    Role = pos,
    Season = season,
    Total_Points = final_player_score
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "WNBA" & master_players$Season == max_wnba_season_player)) {
    write.table(recent_wnba_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_wnba_players), "WNBA players to Master CSV."))
  } else {
    message("WNBA Player data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_wnba_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added WNBA players.")
}

# B. Export Teams to Master CSV
master_teams_file <- "Master_Data/master_teams.csv"
max_wnba_season_team <- max(team_scored$season, na.rm = TRUE)

recent_wnba_teams <- team_scored %>%
  filter(season == max_wnba_season_team) %>%
  transmute(
    Team = team,
    League = "WNBA",
    Season = season,
    Total_Points = total_team_fantasy_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  if (!any(master_teams$League == "WNBA" & master_teams$Season == max_wnba_season_team)) {
    write.table(recent_wnba_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_wnba_teams), "WNBA teams to Master CSV."))
  } else {
    message("WNBA Team data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_wnba_teams, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added WNBA teams.")
}

message("\nWNBA analysis and Master CSV exports complete!")