library(fastRhockey)
library(dplyr)
library(ggplot2)

# ==============================================================================
# 1. SETUP & CONSTANTS
# ==============================================================================
if (!dir.exists("NHL/Players")) dir.create("NHL/Players", recursive = TRUE)
if (!dir.exists("NHL/Teams")) dir.create("NHL/Teams", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

# The NHL API uses the ending year of the season (e.g., 2024 = the 2023-24 season)
start_yr <- 2021 
end_yr <- 2024   

# Base Point Values (Teams)
pts_win         <- 2.0
pts_otl         <- 1.0
pts_goal_diff   <- 0.1
pts_div_champ   <- 10.0
pts_playoff_app <- 5.0
pts_playoff_win <- 1.0
pts_series_win  <- 5.0

# Base Point Values (Skaters)
pts_goal        <- 3.0
pts_assist      <- 2.0
pts_sog         <- 0.5
pts_plus_minus  <- 1.0

# Base Point Values (Goalies)
pts_goalie_win  <- 4.0
pts_goalie_sho  <- 3.0
pts_save        <- 0.1
pts_ga          <- -1.0

# ==============================================================================
# 2. NHL TEAM SCORING
# ==============================================================================
message("Downloading NHL Team Data (Regular Season & Playoffs)...")

# Pull Regular Season (game_type = 2) and Playoffs (game_type = 3)
team_reg <- nhl_team_summary_range(start_season = start_yr, end_season = end_yr, game_type = 2)
team_playoffs <- nhl_team_summary_range(start_season = start_yr, end_season = end_yr, game_type = 3)

team_scored <- team_reg %>%
  rename(reg_wins = wins, reg_otl = ot_losses, reg_gf = goals_for, reg_ga = goals_against) %>%
  # Join playoff stats to the regular season dataframe
  left_join(
    team_playoffs %>% select(team_id, season_id, playoff_games = games_played, playoff_wins = wins),
    by = c("team_id", "season_id")
  ) %>%
  mutate(
    # Handle teams that missed the playoffs
    playoff_games = coalesce(playoff_games, 0),
    playoff_wins = coalesce(playoff_wins, 0),
    
    # Calculate derived stats
    made_playoffs = ifelse(playoff_games > 0, 1, 0),
    series_wins = floor(playoff_wins / 4), # 4 wins required per playoff series
    goal_diff = reg_gf - reg_ga,
    
    # Placeholders for Awards and Division Titles
    is_division_champ = 0,
    awards_pts = 0,
    
    total_team_pts = (reg_wins * pts_win) + 
      (reg_otl * pts_otl) + 
      (goal_diff * pts_goal_diff) + 
      (made_playoffs * pts_playoff_app) + 
      (playoff_wins * pts_playoff_win) + 
      (series_wins * pts_series_win) + 
      (is_division_champ * pts_div_champ) + 
      awards_pts
  )

plot_nhl_teams <- ggplot(team_scored, aes(x = total_team_pts)) +
  geom_histogram(aes(y = after_stat(density)), binwidth = 5, fill = "#111111", color = "white", alpha = 0.8) +
  geom_density(color = "#FCB514", linewidth = 1.2) + # NHL Shield Colors
  theme_minimal() +
  labs(title = "NHL Team Fantasy Points (2021-2024)", x = "Total Season Points", y = "Density")

ggsave("NHL/Teams/team_distribution.png", plot = plot_nhl_teams, width = 9, height = 6, dpi = 300)

message("NHL Team scoring complete!")

# ==============================================================================
# 3. NHL PLAYER SCORING
# ==============================================================================
message("Downloading NHL Player Data (Skaters & Goalies)...")

# --- SKATERS ---
skaters_raw <- nhl_skater_summary_range(start_season = start_yr, end_season = end_yr, game_type = 2)

skaters_scored <- skaters_raw %>%
  mutate(
    goals = coalesce(goals, 0),
    assists = coalesce(assists, 0),
    shots = coalesce(shots, 0),
    plus_minus = coalesce(plus_minus, 0),
    awards_pts = 0,
    
    total_pts = (goals * pts_goal) + 
      (assists * pts_assist) + 
      (shots * pts_sog) + 
      (plus_minus * pts_plus_minus) + 
      awards_pts,
    role = "Skater"
  ) %>%
  filter(total_pts > 0)

# --- GOALIES ---
goalies_raw <- nhl_goalie_summary_range(start_season = start_yr, end_season = end_yr, game_type = 2)

goalies_scored <- goalies_raw %>%
  mutate(
    wins = coalesce(wins, 0),
    shutouts = coalesce(shutouts, 0),
    saves = coalesce(saves, 0),
    goals_against = coalesce(goals_against, 0),
    awards_pts = 0,
    
    total_pts = (wins * pts_goalie_win) + 
      (shutouts * pts_goalie_sho) + 
      (saves * pts_save) + 
      (goals_against * pts_ga) + 
      awards_pts,
    role = "Goalie"
  ) %>%
  filter(total_pts > 0)

# --- COMBINE & PLOT ---
all_players <- bind_rows(
  skaters_scored %>% select(season = season_id, player = skater_full_name, role, total_pts),
  goalies_scored %>% select(season = season_id, player = goalie_full_name, role, total_pts)
)

top_skaters <- all_players %>%
  filter(role == "Skater") %>%
  group_by(season) %>%
  slice_max(total_pts, n = 150, with_ties = FALSE) %>%
  ungroup()

top_goalies <- all_players %>%
  filter(role == "Goalie") %>%
  group_by(season) %>%
  slice_max(total_pts, n = 50, with_ties = FALSE) %>%
  ungroup()

top_players <- bind_rows(top_skaters, top_goalies)

plot_nhl_players <- ggplot(top_players, aes(x = total_pts, fill = role)) +
  geom_density(alpha = 0.6) +
  facet_wrap(~ role, scales = "free_y", ncol = 1) +
  scale_fill_manual(values = c("Skater" = "#005A9C", "Goalie" = "#EF3340")) +
  theme_minimal() +
  labs(title = "NHL Player Fantasy Points (2021-2024)", x = "Total Points", y = "Density") +
  theme(legend.position = "none")

ggsave("NHL/Players/player_role_distribution.png", plot = plot_nhl_players, width = 10, height = 8, dpi = 300)

# ==============================================================================
# 4. MASTER CSV EXPORT (PLAYERS & TEAMS)
# ==============================================================================

# A. Export Players to Master CSV
master_players_file <- "Master_Data/master_players.csv"
max_nhl_season_player <- max(all_players$season, na.rm = TRUE)

recent_nhl_players <- all_players %>%
  filter(season == max_nhl_season_player) %>%
  transmute(
    Player = player,
    Team = "NHL",
    League = "NHL",
    Role = role,
    Season = season,
    Total_Points = total_pts
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "NHL" & master_players$Season == max_nhl_season_player)) {
    write.table(recent_nhl_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_nhl_players), "NHL players to Master CSV."))
  } else {
    message("NHL Player data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_nhl_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added NHL players.")
}

# B. Export Teams to Master CSV
master_teams_file <- "Master_Data/master_teams.csv"
max_nhl_season_team <- max(team_scored$season_id, na.rm = TRUE)

# Determine team name column dynamically
team_col_name <- intersect(c("team_full_name", "team_name", "tri_code", "team_id"), names(team_scored))[1]

recent_nhl_teams <- team_scored %>%
  filter(season_id == max_nhl_season_team) %>%
  transmute(
    Team = as.character(.data[[team_col_name]]),
    League = "NHL",
    Season = season_id,
    Total_Points = total_team_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  if (!any(master_teams$League == "NHL" & master_teams$Season == max_nhl_season_team)) {
    write.table(recent_nhl_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_nhl_teams), "NHL teams to Master CSV."))
  } else {
    message("NHL Team data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_nhl_teams, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added NHL teams.")
}

message("NHL analysis and Master CSV exports complete!")