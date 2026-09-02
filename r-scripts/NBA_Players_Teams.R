library(hoopR)
library(dplyr)
library(ggplot2)
library(tidyr)

# ==============================================================================
# 1. DIRECTORY CREATION
# ==============================================================================
if (!dir.exists("NBA/Players")) dir.create("NBA/Players", recursive = TRUE)
if (!dir.exists("NBA/Teams")) dir.create("NBA/Teams", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

seasons_to_pull <- 2020:2025

# ==============================================================================
# 2. NBA TEAM SCORING & DISTRIBUTION
# ==============================================================================
message("Loading NBA Schedules (2020-2025)...")
schedules <- load_nba_schedule(seasons = seasons_to_pull)

# Standardize Home/Away game records using correct hoopR column names
home_games <- schedules %>%
  filter(!is.na(home_score) & !is.na(away_score)) %>%
  transmute(
    season, game_id, game_date, season_type,
    team = home_abbreviation, opp = away_abbreviation,
    pts_for = home_score, pts_against = away_score,
    notes = if ("notes_headline" %in% names(.)) ifelse(is.na(notes_headline), "", notes_headline) else ""
  )

away_games <- schedules %>%
  filter(!is.na(home_score) & !is.na(away_score)) %>%
  transmute(
    season, game_id, game_date, season_type,
    team = away_abbreviation, opp = home_abbreviation,
    pts_for = away_score, pts_against = home_score,
    notes = if ("notes_headline" %in% names(.)) ifelse(is.na(notes_headline), "", notes_headline) else ""
  )

team_games <- bind_rows(home_games, away_games) %>%
  mutate(
    margin = pts_for - pts_against,
    is_win = margin > 0,
    is_big_win = margin >= 15,
    # Season Type 2 = Regular Season, 3 = Postseason, 5 = Play-In
    is_reg = season_type == 2,
    is_playin = season_type == 5 | grepl("Play-In", notes, ignore.case = TRUE),
    is_playoff = season_type == 3 & !is_playin,
    is_ist = grepl("In-Season Tournament|NBA Cup", notes, ignore.case = TRUE)
  )

# Aggregate Team Performance
team_season_summary <- team_games %>%
  group_by(season, team) %>%
  summarise(
    reg_wins = sum(is_win & is_reg),
    reg_big_wins = sum(is_big_win & is_reg),
    point_diff = sum(margin[is_reg]),
    
    # Play-In tracking
    playin_appearance = ifelse(any(is_playin), 1, 0),
    
    # Playoff tracking
    playoff_appearance = ifelse(any(is_playoff), 1, 0),
    playoff_wins = sum(is_win & is_playoff),
    
    # Playoff Series Wins (4 wins in a series = 1 series win)
    playoff_series_wins = floor(playoff_wins / 4),
    
    # In-Season Tournament (NBA Cup)
    ist_wins = sum(is_win & is_ist),
    ist_champ = ifelse(any(is_win & is_ist & grepl("Championship|Final", notes, ignore.case = TRUE)), 1, 0),
    .groups = "drop"
  )

# Calculate Team Fantasy Points
team_scored <- team_season_summary %>%
  mutate(
    pts_wins = reg_wins * 2,
    pts_big_wins = reg_big_wins * 1,
    pts_playin_app = ifelse(playin_appearance == 1 & playoff_appearance == 0, 3, 0), # Only awarded if NOT advancing
    pts_playoff_app = playoff_appearance * 10, # Uniform points for all 16 playoff teams
    pts_playoff_wins = playoff_wins * 3,
    pts_series_wins = playoff_series_wins * 5,
    pts_ist_wins = ist_wins * 2,
    pts_ist_champ = ist_champ * 8,
    pts_point_diff = point_diff * 0.05,
    
    total_team_fantasy_pts = pts_wins + pts_big_wins + pts_playin_app + 
      pts_playoff_app + pts_playoff_wins + pts_series_wins + 
      pts_ist_wins + pts_ist_champ + pts_point_diff
  )

# Save Team Plots
plot_team_overall <- ggplot(team_scored, aes(x = total_team_fantasy_pts)) +
  geom_histogram(aes(y = after_stat(density)), binwidth = 10, fill = "#17408B", color = "white", alpha = 0.7) +
  geom_density(color = "#C9082A", linewidth = 1.2) +
  theme_minimal() +
  labs(title = "NBA Team Fantasy Points Distribution (2020-2025)", x = "Total Points", y = "Density")

ggsave("NBA/Teams/team_overall_distribution.png", plot = plot_team_overall, width = 9, height = 6, dpi = 300)

plot_team_yearly <- ggplot(team_scored, aes(x = total_team_fantasy_pts, fill = factor(season))) +
  geom_density(alpha = 0.4) +
  facet_wrap(~ season, ncol = 1) +
  theme_minimal() +
  labs(title = "NBA Team Score Distribution by Season", x = "Total Points", y = "Density", fill = "Season") +
  theme(legend.position = "none")

ggsave("NBA/Teams/team_yearly_distribution.png", plot = plot_team_yearly, width = 9, height = 12, dpi = 300)

# ==============================================================================
# 3. NBA PLAYER SCORING & DISTRIBUTION
# ==============================================================================
message("Loading NBA Player Box Scores (2020-2025)...")
player_box <- load_nba_player_box(seasons = seasons_to_pull)

# Standard Fantasy Box Score Scoring
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
    pm  = as.numeric(plus_minus),
    
    # Check Double-Double & Triple-Double
    dd = ifelse((pts >= 10) + (reb >= 10) + (ast >= 10) + (stl >= 10) + (blk >= 10) >= 2, 1, 0),
    td = ifelse((pts >= 10) + (reb >= 10) + (ast >= 10) + (stl >= 10) + (blk >= 10) >= 3, 1, 0),
    
    # Single Game Fantasy Score
    game_fantasy_pts = (pts * 1) + (reb * 1.2) + (ast * 1.5) + (stl * 3) + 
      (blk * 3) + (to * -1) + (fg3 * 0.5) + (dd * 1.5) + (td * 3)
  ) %>%
  group_by(season, athlete_id, athlete_display_name, athlete_position_abbreviation) %>%
  summarise(
    games_played = n(),
    total_fantasy_pts = sum(game_fantasy_pts, na.rm = TRUE),
    total_plus_minus = sum(pm, na.rm = TRUE),
    
    # Combined Player Score (Fantasy Pts + Net Plus-Minus Contribution)
    final_player_score = total_fantasy_pts + (total_plus_minus * 0.1),
    .groups = "drop"
  ) %>%
  filter(games_played >= 15 & final_player_score > 100)

# Extract Top Tiers per Season
top_100_players <- player_season %>%
  group_by(season) %>%
  slice_max(final_player_score, n = 100, with_ties = FALSE)

# Positional Groupings (PG, SG, SF, PF, C)
q_pos <- player_season %>%
  mutate(pos_clean = case_when(
    athlete_position_abbreviation %in% c("PG", "SG", "G") ~ "Guards (Top 40)",
    athlete_position_abbreviation %in% c("SF", "PF", "F") ~ "Forwards (Top 40)",
    athlete_position_abbreviation %in% c("C") ~ "Centers (Top 20)",
    TRUE ~ "Flex (Top 100)"
  )) %>%
  group_by(season, pos_clean) %>%
  slice_max(final_player_score, n = 40, with_ties = FALSE)

# Save Player Plots
plot_player_top100 <- ggplot(top_100_players, aes(x = final_player_score, fill = factor(season))) +
  geom_density(alpha = 0.4) +
  facet_wrap(~ season, ncol = 1) +
  theme_minimal() +
  labs(title = "NBA Top 100 Players Score Distribution (2020-2025)", x = "Total Score (Fantasy Pts + +/-)", y = "Density") +
  theme(legend.position = "none")

ggsave("NBA/Players/top_100_overall_distribution.png", plot = plot_player_top100, width = 9, height = 12, dpi = 300)

plot_player_pos <- ggplot(q_pos, aes(x = final_player_score, fill = pos_clean)) +
  geom_density(alpha = 0.5) +
  facet_wrap(~ pos_clean, scales = "free_y", ncol = 2) +
  theme_minimal() +
  labs(title = "NBA Positional Distributions (2020-2025)", x = "Total Score", y = "Density", fill = "Position") +
  theme(legend.position = "none")

ggsave("NBA/Players/positional_distribution.png", plot = plot_player_pos, width = 11, height = 8, dpi = 300)

# ==============================================================================
# 4. MASTER CSV EXPORT (PLAYERS & TEAMS)
# ==============================================================================

# A. Export Players to Master CSV
master_players_file <- "Master_Data/master_players.csv"
max_nba_season_player <- max(player_season$season, na.rm = TRUE)

recent_nba_players <- player_season %>%
  filter(season == max_nba_season_player) %>%
  transmute(
    Player = athlete_display_name,
    Team = "NBA",
    League = "NBA",
    Role = athlete_position_abbreviation,
    Season = season,
    Total_Points = final_player_score
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "NBA" & master_players$Season == max_nba_season_player)) {
    write.table(recent_nba_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_nba_players), "NBA players to Master CSV."))
  } else {
    message("NBA Player data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_nba_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added NBA players.")
}

# B. Export Teams to Master CSV
master_teams_file <- "Master_Data/master_teams.csv"
max_nba_season_team <- max(team_scored$season, na.rm = TRUE)

recent_nba_teams <- team_scored %>%
  filter(season == max_nba_season_team) %>%
  transmute(
    Team = team,
    League = "NBA",
    Season = season,
    Total_Points = total_team_fantasy_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  if (!any(master_teams$League == "NBA" & master_teams$Season == max_nba_season_team)) {
    write.table(recent_nba_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_nba_teams), "NBA teams to Master CSV."))
  } else {
    message("NBA Team data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_nba_teams, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added NBA teams.")
}

message("NBA analysis and Master CSV exports complete!")