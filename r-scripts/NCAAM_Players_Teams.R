library(hoopR)
library(dplyr)
library(ggplot2)
library(tidyr)

# ==============================================================================
# 1. DIRECTORY CREATION
# ==============================================================================
if (!dir.exists("NCAAM/Players")) dir.create("NCAAM/Players", recursive = TRUE)
if (!dir.exists("NCAAM/Teams")) dir.create("NCAAM/Teams", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

seasons_to_pull <- 2020:2025

# ==============================================================================
# 2. NCAA MEN'S BASKETBALL TEAM SCORING & DISTRIBUTION
# ==============================================================================
message("Loading NCAA MBB Schedules (2020-2025)...")
schedules <- load_mbb_schedule(seasons = seasons_to_pull)

# Dynamically resolve conference column names
home_conf_col <- intersect(c("home_conference_abbrev", "home_conference_name", "home_conference", "home_conference_id"), names(schedules))[1]
away_conf_col <- intersect(c("away_conference_abbrev", "away_conference_name", "away_conference", "away_conference_id"), names(schedules))[1]

# Filter completed games with valid scores
completed_games <- schedules %>%
  filter(!is.na(home_score) & !is.na(away_score) & (home_score + away_score > 0))

home_games <- completed_games %>%
  transmute(
    season, game_id, game_date, season_type,
    team = home_abbreviation, opp = away_abbreviation,
    conference = if (!is.null(home_conf_col)) .data[[home_conf_col]] else NA_character_,
    opp_conference = if (!is.null(away_conf_col)) .data[[away_conf_col]] else NA_character_,
    pts_for = home_score, pts_against = away_score,
    notes = if ("notes_headline" %in% names(.)) ifelse(is.na(notes_headline), "", notes_headline) else ""
  )

away_games <- completed_games %>%
  transmute(
    season, game_id, game_date, season_type,
    team = away_abbreviation, opp = home_abbreviation,
    conference = if (!is.null(away_conf_col)) .data[[away_conf_col]] else NA_character_,
    opp_conference = if (!is.null(home_conf_col)) .data[[home_conf_col]] else NA_character_,
    pts_for = away_score, pts_against = home_score,
    notes = if ("notes_headline" %in% names(.)) ifelse(is.na(notes_headline), "", notes_headline) else ""
  )

team_games <- bind_rows(home_games, away_games) %>%
  filter(!is.na(team)) %>%
  mutate(
    margin = pts_for - pts_against,
    is_win = margin > 0,
    is_conf_game = !is.na(conference) & !is.na(opp_conference) & (conference == opp_conference),
    
    # Big wins: 25+ pts non-conference, 15+ pts conference
    is_big_win = case_when(
      is_win & !is_conf_game & margin >= 25 ~ TRUE,
      is_win & is_conf_game & margin >= 15 ~ TRUE,
      TRUE ~ FALSE
    ),
    is_reg = season_type == 2,
    is_conf_tourney = season_type == 3 & grepl("Tournament", notes, ignore.case = TRUE) & !grepl("NCAA|March Madness", notes, ignore.case = TRUE),
    is_march_madness = grepl("NCAA Tournament|March Madness|First Four|First Round|Second Round|Sweet 16|Elite Eight|Final Four|National Championship", notes, ignore.case = TRUE) | (season_type == 3 & !is_conf_tourney)
  )

# Aggregate Team Performance
team_season_summary <- team_games %>%
  group_by(season, team, conference) %>%
  summarise(
    games_played = n(),
    reg_wins = sum(is_win & is_reg),
    big_wins = sum(is_big_win),
    conf_wins = sum(is_win & is_reg & is_conf_game),
    point_diff = sum(margin[is_reg]),
    
    # Conference Tournament
    conf_tourney_wins = sum(is_win & is_conf_tourney),
    conf_tourney_champ = ifelse(any(is_win & is_conf_tourney & grepl("Championship|Final", notes, ignore.case = TRUE)), 1, 0),
    
    # March Madness
    mm_appearance = ifelse(any(is_march_madness), 1, 0),
    mm_wins = sum(is_win & is_march_madness),
    .groups = "drop"
  ) %>%
  # Filter out non-DI / low-sample team entries
  filter(games_played >= 10)

# Identify Regular Season Conference Champions (Splitting ties evenly)
team_season_summary <- team_season_summary %>%
  group_by(season, conference) %>%
  mutate(
    is_reg_champ = ifelse(conf_wins == max(conf_wins) & conf_wins > 0, 1, 0),
    reg_champ_ties = sum(is_reg_champ)
  ) %>%
  ungroup()

# Apply Team Scoring Formula
team_scored <- team_season_summary %>%
  mutate(
    pts_wins = reg_wins * 2,
    pts_big_wins = big_wins * 1.5,
    pts_conf_wins = conf_wins * 1,
    pts_reg_champ = ifelse(reg_champ_ties > 0, is_reg_champ * (8 / reg_champ_ties), 0),
    pts_conf_tourney_wins = conf_tourney_wins * 2,
    pts_conf_tourney_champ = conf_tourney_champ * 6,
    pts_mm_app = mm_appearance * 8,
    pts_mm_wins = mm_wins * 5,
    pts_point_diff = point_diff * 0.03,
    
    total_team_fantasy_pts = pts_wins + pts_big_wins + pts_conf_wins + pts_reg_champ +
      pts_conf_tourney_wins + pts_conf_tourney_champ +
      pts_mm_app + pts_mm_wins + pts_point_diff
  )

# Save Team Plots
plot_team_overall <- ggplot(team_scored, aes(x = total_team_fantasy_pts)) +
  geom_histogram(aes(y = after_stat(density)), binwidth = 10, fill = "#003366", color = "white", alpha = 0.7) +
  geom_density(color = "#FF6600", linewidth = 1.2) +
  theme_minimal() +
  labs(title = "NCAA Men's Basketball Team Fantasy Points Distribution (2020-2025)", x = "Total Points", y = "Density")

ggsave("NCAAM/Teams/team_overall_distribution.png", plot = plot_team_overall, width = 9, height = 6, dpi = 300)

plot_team_yearly <- ggplot(team_scored, aes(x = total_team_fantasy_pts, fill = factor(season))) +
  geom_density(alpha = 0.4) +
  facet_wrap(~ season, ncol = 1) +
  theme_minimal() +
  labs(title = "NCAA MBB Team Score Distribution by Season", x = "Total Points", y = "Density", fill = "Season") +
  theme(legend.position = "none")

ggsave("NCAAM/Teams/team_yearly_distribution.png", plot = plot_team_yearly, width = 9, height = 12, dpi = 300)

# ==============================================================================
# 3. NCAA MEN'S BASKETBALL PLAYER SCORING & DISTRIBUTION
# ==============================================================================
message("Loading NCAA MBB Player Box Scores (2020-2025)...")
player_box <- load_mbb_player_box(seasons = seasons_to_pull)

# Fantasy Box Score Calculation
player_season <- player_box %>%
  filter(!is.na(athlete_id) & !is.na(points)) %>%
  mutate(
    pts = as.numeric(points),
    reb = as.numeric(rebounds),
    ast = as.numeric(assists),
    stl = as.numeric(steals),
    blk = as.numeric(blocks),
    to  = as.numeric(turnovers),
    fg3 = as.numeric(three_point_field_goals_made),
    pm  = ifelse("plus_minus" %in% names(.), suppressWarnings(as.numeric(plus_minus)), 0),
    pm  = ifelse(is.na(pm), 0, pm),
    
    dd = ifelse((pts >= 10) + (reb >= 10) + (ast >= 10) + (stl >= 10) + (blk >= 10) >= 2, 1, 0),
    td = ifelse((pts >= 10) + (reb >= 10) + (ast >= 10) + (stl >= 10) + (blk >= 10) >= 3, 1, 0),
    
    game_fantasy_pts = (pts * 1) + (reb * 1.2) + (ast * 1.5) + (stl * 3) + 
      (blk * 3) + (to * -1) + (fg3 * 0.5) + (dd * 1.5) + (td * 3)
  ) %>%
  group_by(season, athlete_id, athlete_display_name, athlete_position_abbreviation) %>%
  summarise(
    games_played = n(),
    total_fantasy_pts = sum(game_fantasy_pts, na.rm = TRUE),
    total_plus_minus = sum(pm, na.rm = TRUE),
    final_player_score = total_fantasy_pts + (total_plus_minus * 0.1),
    .groups = "drop"
  ) %>%
  filter(games_played >= 10 & final_player_score > 50)

# Extract Top 150 Players per Season
top_150_players <- player_season %>%
  group_by(season) %>%
  slice_max(final_player_score, n = 150, with_ties = FALSE)

# Positional Breakdown
q_pos <- player_season %>%
  mutate(pos_clean = case_when(
    athlete_position_abbreviation %in% c("G", "PG", "SG") ~ "Guards (Top 60)",
    athlete_position_abbreviation %in% c("F", "SF", "PF") ~ "Forwards (Top 60)",
    athlete_position_abbreviation %in% c("C") ~ "Centers (Top 30)",
    TRUE ~ "Flex (Top 150)"
  )) %>%
  group_by(season, pos_clean) %>%
  slice_max(final_player_score, n = 60, with_ties = FALSE)

# Save Player Plots
plot_player_top150 <- ggplot(top_150_players, aes(x = final_player_score, fill = factor(season))) +
  geom_density(alpha = 0.4) +
  facet_wrap(~ season, ncol = 1) +
  theme_minimal() +
  labs(title = "NCAA MBB Top 150 Players Score Distribution (2020-2025)", x = "Total Score", y = "Density") +
  theme(legend.position = "none")

ggsave("NCAAM/Players/top_150_overall_distribution.png", plot = plot_player_top150, width = 9, height = 12, dpi = 300)

plot_player_pos <- ggplot(q_pos, aes(x = final_player_score, fill = pos_clean)) +
  geom_density(alpha = 0.5) +
  facet_wrap(~ pos_clean, scales = "free_y", ncol = 2) +
  theme_minimal() +
  labs(title = "NCAA MBB Positional Distributions (2020-2025)", x = "Total Score", y = "Density", fill = "Position") +
  theme(legend.position = "none")

ggsave("NCAAM/Players/positional_distribution.png", plot = plot_player_pos, width = 11, height = 8, dpi = 300)

# ==============================================================================
# 4. MASTER CSV EXPORT (PLAYERS & TEAMS)
# ==============================================================================

# A. Export Players to Master CSV
master_players_file <- "Master_Data/master_players.csv"
max_ncaam_season_player <- max(player_season$season, na.rm = TRUE)

recent_ncaam_players <- player_season %>%
  filter(season == max_ncaam_season_player) %>%
  transmute(
    Player = athlete_display_name,
    Team = "NCAAM", 
    League = "NCAAM",
    Role = athlete_position_abbreviation,
    Season = season,
    Total_Points = final_player_score
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "NCAAM" & master_players$Season == max_ncaam_season_player)) {
    write.table(recent_ncaam_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_ncaam_players), "NCAAM players to Master CSV."))
  } else {
    message("NCAAM Player data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_ncaam_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added NCAAM players.")
}

# B. Export Teams to Master CSV
master_teams_file <- "Master_Data/master_teams.csv"
max_ncaam_season_team <- max(team_scored$season, na.rm = TRUE)

recent_ncaam_teams <- team_scored %>%
  filter(season == max_ncaam_season_team) %>%
  transmute(
    Team = team,
    League = "NCAAM",
    Season = season,
    Total_Points = total_team_fantasy_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  if (!any(master_teams$League == "NCAAM" & master_teams$Season == max_ncaam_season_team)) {
    write.table(recent_ncaam_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_ncaam_teams), "NCAAM teams to Master CSV."))
  } else {
    message("NCAAM Team data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_ncaam_teams, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added NCAAM teams.")
}

message("NCAAM analysis and Master CSV exports complete!")