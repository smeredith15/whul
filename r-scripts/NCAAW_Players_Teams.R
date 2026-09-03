library(wehoop)
library(dplyr)
library(ggplot2)
library(tidyr)
library(jsonlite)

# ==============================================================================
# 1. BULLETPROOF HELPER FUNCTIONS & DIRECTORY CREATION
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

if (!dir.exists("NCAAW/Players")) dir.create("NCAAW/Players", recursive = TRUE)
if (!dir.exists("NCAAW/Teams")) dir.create("NCAAW/Teams", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

seasons_to_pull <- 2020:2025

# ==============================================================================
# 2. NCAA WOMEN'S BASKETBALL TEAM SCORING (ALL YEARS)
# ==============================================================================
message("Loading NCAA WBB Schedules (2020-2025)...")
schedules_raw <- load_wbb_schedule(seasons = seasons_to_pull) %>% clean_names()

schedules <- schedules_raw %>%
  mutate(
    raw_season = suppressWarnings(as.numeric(get_col(., c("season", "season_year", "year")))),
    game_id_clean = get_col(., c("game_id", "id", "event_id")),
    game_date_str = get_col(., c("game_date", "date", "game_date_time")),
    game_date_clean = as.Date(substr(game_date_str, 1, 10)),
    game_year = as.numeric(format(game_date_clean, "%Y")),
    game_month = as.numeric(format(game_date_clean, "%m")),
    calc_season = ifelse(game_month >= 10, game_year + 1, game_year),
    season = ifelse(!is.na(raw_season), raw_season, calc_season),
    
    st_num = suppressWarnings(as.numeric(get_col(., c("season_type", "season_type_id", "type", "game_type")))),
    season_type_clean = ifelse(is.na(st_num), 2, st_num),
    
    home_score_num = suppressWarnings(as.numeric(get_col(., c("home_score", "home_team_score", "home_points", "home_pts", "home_score_value")))),
    away_score_num = suppressWarnings(as.numeric(get_col(., c("away_score", "away_team_score", "away_points", "away_pts", "away_score_value")))),
    
    home_team_clean = get_col(., c("home_abbreviation", "home_team_abbrev", "home_team_abbreviation", "home_short_display_name", "home_display_name", "home_name", "home_location")),
    away_team_clean = get_col(., c("away_abbreviation", "away_team_abbrev", "away_team_abbreviation", "away_short_display_name", "away_display_name", "away_name", "away_location")),
    
    home_conf_clean = get_col(., c("home_conference_abbrev", "home_conference_name", "home_conference", "home_conf", "home_conference_id")),
    away_conf_clean = get_col(., c("away_conference_abbrev", "away_conference_name", "away_conference", "away_conf", "away_conference_id")),
    home_conf_clean = ifelse(is.na(home_conf_clean) | home_conf_clean == "", "Ind", home_conf_clean),
    away_conf_clean = ifelse(is.na(away_conf_clean) | away_conf_clean == "", "Ind", away_conf_clean),
    notes_clean = if ("notes_headline" %in% names(.)) ifelse(is.na(notes_headline), "", notes_headline) else ""
  ) %>%
  filter(!is.na(season) & season %in% seasons_to_pull)

completed_games <- schedules %>%
  filter(!is.na(home_score_num) & !is.na(away_score_num) & (home_score_num + away_score_num > 0))

home_games <- completed_games %>%
  transmute(
    season, game_id = game_id_clean, game_date = game_date_clean, season_type = season_type_clean,
    team = home_team_clean, opp = away_team_clean, conference = home_conf_clean, opp_conference = away_conf_clean,
    pts_for = home_score_num, pts_against = away_score_num, notes = notes_clean
  )

away_games <- completed_games %>%
  transmute(
    season, game_id = game_id_clean, game_date = game_date_clean, season_type = season_type_clean,
    team = away_team_clean, opp = home_team_clean, conference = away_conf_clean, opp_conference = home_conf_clean,
    pts_for = away_score_num, pts_against = home_score_num, notes = notes_clean
  )

team_games <- bind_rows(home_games, away_games) %>%
  filter(!is.na(team) & team != "") %>%
  mutate(
    margin = pts_for - pts_against,
    is_win = margin > 0,
    is_conf_game = !is.na(conference) & !is.na(opp_conference) & (conference == opp_conference) & (conference != "Ind"),
    is_big_win = case_when(is_win & !is_conf_game & margin >= 25 ~ TRUE, is_win & is_conf_game & margin >= 15 ~ TRUE, TRUE ~ FALSE),
    is_reg = season_type == 2 | (!grepl("Tournament|NCAA|March Madness", notes, ignore.case = TRUE) & season_type != 3),
    is_conf_tourney = (season_type == 3 | grepl("Tournament", notes, ignore.case = TRUE)) & !grepl("NCAA|March Madness", notes, ignore.case = TRUE),
    is_march_madness = grepl("NCAA Tournament|March Madness|First Four|First Round|Second Round|Sweet 16|Elite Eight|Final Four|National Championship", notes, ignore.case = TRUE)
  )

team_season_summary <- team_games %>%
  group_by(season, team, conference) %>%
  summarise(
    games_played = n(),
    reg_wins = sum(is_win & is_reg, na.rm = TRUE),
    big_wins = sum(is_big_win, na.rm = TRUE),
    conf_wins = sum(is_win & is_reg & is_conf_game, na.rm = TRUE),
    point_diff = sum(margin[is_reg], na.rm = TRUE),
    conf_tourney_wins = sum(is_win & is_conf_tourney, na.rm = TRUE),
    conf_tourney_champ = ifelse(any(is_win & is_conf_tourney & grepl("Championship|Final", notes, ignore.case = TRUE), na.rm = TRUE), 1, 0),
    mm_appearance = ifelse(any(is_march_madness, na.rm = TRUE), 1, 0),
    mm_wins = sum(is_win & is_march_madness, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter(games_played >= 6) %>%
  group_by(season, conference) %>%
  mutate(is_reg_champ = ifelse(conf_wins == max(conf_wins) & conf_wins > 0, 1, 0), reg_champ_ties = sum(is_reg_champ)) %>%
  ungroup()

team_scored <- team_season_summary %>%
  mutate(
    total_team_fantasy_pts = (reg_wins * 2) + (big_wins * 1.5) + (conf_wins * 1) + 
      ifelse(reg_champ_ties > 0, is_reg_champ * (8 / reg_champ_ties), 0) +
      (conf_tourney_wins * 2) + (conf_tourney_champ * 6) +
      (mm_appearance * 8) + (mm_wins * 5) + (point_diff * 0.03)
  )

message("\nFinal Scored Teams Per Season:")
print(table(team_scored$season))

plot_team_overall <- ggplot(team_scored, aes(x = total_team_fantasy_pts)) +
  geom_histogram(aes(y = ..density..), binwidth = 10, fill = "#800020", color = "white", alpha = 0.7) +
  geom_density(color = "#FFD700", linewidth = 1.2) + theme_minimal() +
  labs(title = "NCAA Women's Basketball Team Fantasy Points (2020-2025)", x = "Total Points", y = "Density")

ggsave("NCAAW/Teams/team_overall_distribution.png", plot = plot_team_overall, width = 9, height = 6, dpi = 300)

plot_team_yearly <- ggplot(team_scored, aes(x = total_team_fantasy_pts, fill = factor(season))) +
  geom_density(alpha = 0.4) + facet_wrap(~ factor(season), ncol = 2) + theme_minimal() +
  labs(title = "NCAA WBB Team Score Distribution by Season", x = "Total Points", y = "Density") + theme(legend.position = "none")

ggsave("NCAAW/Teams/team_yearly_distribution.png", plot = plot_team_yearly, width = 9, height = 10, dpi = 300)

# ==============================================================================
# 3. NCAA WOMEN'S BASKETBALL PLAYER SCORING (ALL YEARS)
# ==============================================================================
message("\nLoading NCAA WBB Player Box Scores (2020-2025)...")
player_box_raw <- load_wbb_player_box(seasons = seasons_to_pull) %>% clean_names()

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
  filter(games_played >= 6 & final_player_score > 30)

message("\nFinal Scored Players Per Season:")
print(table(player_season$season))

top_150_players <- player_season %>% group_by(season) %>% slice_max(final_player_score, n = 150, with_ties = FALSE)
q_pos <- player_season %>%
  mutate(pos_clean = case_when(pos %in% c("G", "PG", "SG") ~ "Guards (Top 60)", pos %in% c("F", "SF", "PF") ~ "Forwards (Top 60)", pos %in% c("C") ~ "Centers (Top 30)", TRUE ~ "Flex (Top 150)")) %>%
  group_by(season, pos_clean) %>% slice_max(final_player_score, n = 60, with_ties = FALSE)

plot_player_top150 <- ggplot(top_150_players, aes(x = final_player_score, fill = factor(season))) +
  geom_density(alpha = 0.4) + facet_wrap(~ factor(season), ncol = 2) + theme_minimal() +
  labs(title = "NCAA WBB Top 150 Players Score Distribution", x = "Total Score", y = "Density") + theme(legend.position = "none")

ggsave("NCAAW/Players/top_150_overall_distribution.png", plot = plot_player_top150, width = 9, height = 10, dpi = 300)

plot_player_pos <- ggplot(q_pos, aes(x = final_player_score, fill = pos_clean)) +
  geom_density(alpha = 0.5) + facet_wrap(~ pos_clean, scales = "free_y", ncol = 2) + theme_minimal() +
  labs(title = "NCAA WBB Positional Distributions (2020-2025)", x = "Total Score", y = "Density") + theme(legend.position = "none")

ggsave("NCAAW/Players/positional_distribution.png", plot = plot_player_pos, width = 11, height = 8, dpi = 300)

# ==============================================================================
# 4. MASTER CSV EXPORT (PLAYERS & TEAMS)
# ==============================================================================

# A. Export Players to Master CSV
master_players_file <- "Master_Data/master_players.csv"
max_ncaaw_season_player <- max(player_season$season, na.rm = TRUE)

recent_ncaaw_players <- player_season %>%
  filter(season == max_ncaaw_season_player) %>%
  transmute(
    Player = athlete_display_name,
    Team = "NCAAW",
    League = "NCAAW",
    Role = pos,
    Season = season,
    Total_Points = final_player_score
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "NCAAW" & master_players$Season == max_ncaaw_season_player)) {
    write.table(recent_ncaaw_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_ncaaw_players), "NCAAW players to Master CSV."))
  } else {
    message("NCAAW Player data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_ncaaw_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added NCAAW players.")
}

# B. Export Teams to Master CSV
master_teams_file <- "Master_Data/master_teams.csv"
max_ncaaw_season_team <- max(team_scored$season, na.rm = TRUE)

recent_ncaaw_teams <- team_scored %>%
  filter(season == max_ncaaw_season_team) %>%
  transmute(
    Team = team,
    League = "NCAAW",
    Season = season,
    Total_Points = total_team_fantasy_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  if (!any(master_teams$League == "NCAAW" & master_teams$Season == max_ncaaw_season_team)) {
    write.table(recent_ncaaw_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_ncaaw_teams), "NCAAW teams to Master CSV."))
  } else {
    message("NCAAW Team data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_ncaaw_teams, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added NCAAW teams.")
}

message("\nNCAA Women's Basketball analysis and Master CSV exports complete!")