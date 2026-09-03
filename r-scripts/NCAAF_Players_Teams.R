# Load required libraries
library(cfbfastR)
library(dplyr)
library(ggplot2)
library(tidyr)

# ==============================================================================
# 1. SETUP & DIRECTORIES
# ==============================================================================

if (!dir.exists("NCAAF/Players")) dir.create("NCAAF/Players", recursive = TRUE)
if (!dir.exists("NCAAF/Teams")) dir.create("NCAAF/Teams", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

seasons_to_pull <- 2020:2025 

# ==============================================================================
# 2. NCAAF TEAM SCORING & DISTRIBUTION
# ==============================================================================
message("Loading NCAAF Schedules (2020-2025)...")
schedules <- load_cfb_schedules(seasons = seasons_to_pull)

# Filter strictly to completed games involving at least one FBS team
fbs_games <- schedules %>%
  filter(completed == TRUE | (!is.na(home_points) & !is.na(away_points) & (home_points + away_points > 0))) %>%
  filter(home_division == "fbs" | away_division == "fbs")

# Reshape into a team-level dataframe
home_teams <- fbs_games %>%
  transmute(
    season, team = home_team, conference = home_conference, opp = away_team,
    points_for = home_points, points_against = away_points,
    conf_game = ifelse(home_conference == away_conference & !is.na(home_conference), TRUE, FALSE),
    season_type, notes
  )

away_teams <- fbs_games %>%
  transmute(
    season, team = away_team, conference = away_conference, opp = home_team,
    points_for = away_points, points_against = home_points,
    conf_game = ifelse(away_conference == home_conference & !is.na(away_conference), TRUE, FALSE),
    season_type, notes
  )

team_games <- bind_rows(home_teams, away_teams) %>%
  filter(!is.na(conference)) %>%
  mutate(
    margin = points_for - points_against,
    is_win = margin > 0,
    is_big_win = margin >= 9
  )

# Aggregate Team Stats & Filter Out Low-Game Entries (< 6 FBS games)
team_season_summary <- team_games %>%
  group_by(season, team, conference) %>%
  summarise(
    games_played = n(),
    wins = sum(is_win),
    losses = sum(!is_win),
    big_wins = sum(is_big_win),
    conf_wins = sum(is_win & conf_game),
    point_diff = sum(margin),
    conf_title_win = sum(is_win & season_type == "postseason" & grepl("Championship", notes, ignore.case = TRUE)),
    playoff_app = ifelse(any(season_type == "postseason" & grepl("Playoff|CFP|Rose|Sugar|Orange|Cotton|Fiesta|Peach", notes, ignore.case = TRUE)), 1, 0),
    playoff_wins = sum(is_win & season_type == "postseason" & grepl("Playoff|CFP|Rose|Sugar|Orange|Cotton|Fiesta|Peach", notes, ignore.case = TRUE)),
    .groups = "drop"
  ) %>%
  filter(games_played >= 6)

# Determine Regular Season Conference Champions (Splitting ties evenly)
team_season_summary <- team_season_summary %>%
  group_by(season, conference) %>%
  mutate(
    is_reg_season_champ = ifelse(conf_wins == max(conf_wins) & conf_wins > 0, 1, 0),
    champ_tie_count = sum(is_reg_season_champ)
  ) %>%
  ungroup()

# Apply Down-weighted CFB Team Scoring Model
team_scored <- team_season_summary %>%
  mutate(
    pts_wins = wins * 10,
    pts_big_wins = big_wins * 2,         
    pts_conf_wins = conf_wins * 2,
    pts_reg_champ = ifelse(champ_tie_count > 0, is_reg_season_champ * (6 / champ_tie_count), 0), 
    pts_title_game = conf_title_win * 6,
    pts_playoff_app = playoff_app * 10,
    pts_playoff_wins = playoff_wins * 15,
    pts_point_diff = point_diff * 0.05,  
    
    total_team_fantasy_pts = pts_wins + pts_big_wins + pts_conf_wins + pts_reg_champ + 
      pts_title_game + pts_playoff_app + pts_playoff_wins + pts_point_diff
  )

# Plot Team Distributions
plot_cfb_teams <- ggplot(team_scored, aes(x = total_team_fantasy_pts)) +
  geom_histogram(aes(y = after_stat(density)), binwidth = 15, fill = "#c5050c", color = "white", alpha = 0.7) +
  geom_density(color = "#9b0000", linewidth = 1.2) +
  theme_minimal() +
  labs(title = "NCAAF Team Fantasy Points Distribution (2020-2025)", x = "Total Points", y = "Density")

ggsave("NCAAF/Teams/team_overall_distribution.png", plot = plot_cfb_teams, width = 9, height = 6, dpi = 300)

# ==============================================================================
# 3. NCAAF PLAYER SCORING & DISTRIBUTION (150% Scale of NFL)
# ==============================================================================
message("Loading NCAAF Play-by-Play Data (This will take a few minutes)...")
pbp <- load_cfb_pbp(seasons = seasons_to_pull)

# Aggregate PBP into Season Player Stats using yards_gained
player_season_stats <- pbp %>%
  filter(!is.na(passer_player_name) | !is.na(rusher_player_name) | !is.na(receiver_player_name))

# Passing
passing <- player_season_stats %>%
  filter(!is.na(passer_player_name)) %>%
  group_by(season, player_name = passer_player_name) %>%
  summarise(
    pass_yds = sum(yards_gained, na.rm = TRUE),
    pass_tds = sum(pass_td, na.rm = TRUE),
    pass_ints = sum(int, na.rm = TRUE),
    .groups = "drop"
  )

# Rushing
rushing <- player_season_stats %>%
  filter(!is.na(rusher_player_name)) %>%
  group_by(season, player_name = rusher_player_name) %>%
  summarise(
    rush_yds = sum(yards_gained, na.rm = TRUE),
    rush_tds = sum(rush_td, na.rm = TRUE),
    .groups = "drop"
  )

# Receiving
receiving <- player_season_stats %>%
  filter(!is.na(receiver_player_name)) %>%
  group_by(season, player_name = receiver_player_name) %>%
  summarise(
    receptions = sum(completion, na.rm = TRUE),
    rec_yds = sum(yards_gained, na.rm = TRUE),
    rec_tds = sum(pass_td, na.rm = TRUE),
    .groups = "drop"
  )

# Merge and Score (Half-PPR)
cfb_players <- passing %>%
  full_join(rushing, by = c("season", "player_name")) %>%
  full_join(receiving, by = c("season", "player_name")) %>%
  replace(is.na(.), 0) %>%
  mutate(
    half_ppr_pts = (pass_yds * 0.04) + (pass_tds * 4) + (pass_ints * -2) +
      (rush_yds * 0.1) + (rush_tds * 6) +
      (receptions * 0.5) + (rec_yds * 0.1) + (rec_tds * 6)
  ) %>%
  filter(half_ppr_pts > 25)

# Infer Positions
cfb_players <- cfb_players %>%
  mutate(
    position = case_when(
      pass_yds > rush_yds & pass_yds > rec_yds ~ "QB",
      rush_yds > pass_yds & rush_yds > rec_yds ~ "RB",
      rec_yds > pass_yds & rec_yds > rush_yds ~ "WR/TE",
      TRUE ~ "FLEX"
    )
  )

# Extract 150% scaled tiers per season
qbs   <- cfb_players %>% filter(position == "QB") %>% group_by(season) %>% slice_max(half_ppr_pts, n = 30, with_ties = FALSE) %>% mutate(pos_group = "QB (Top 30)")
rbs   <- cfb_players %>% filter(position == "RB") %>% group_by(season) %>% slice_max(half_ppr_pts, n = 45, with_ties = FALSE) %>% mutate(pos_group = "RB (Top 45)")
wr_te <- cfb_players %>% filter(position == "WR/TE") %>% group_by(season) %>% slice_max(half_ppr_pts, n = 105, with_ties = FALSE) %>% mutate(pos_group = "WR/TE (Top 105)")
flex  <- bind_rows(rbs, wr_te) %>% group_by(season) %>% slice_max(half_ppr_pts, n = 150, with_ties = FALSE) %>% mutate(pos_group = "FLEX (Top 150)")

# Plot Positional Distributions
positional_data <- bind_rows(qbs, rbs, wr_te, flex) %>%
  mutate(pos_group = factor(pos_group, levels = c("QB (Top 30)", "RB (Top 45)", "WR/TE (Top 105)", "FLEX (Top 150)")))

plot_cfb_positional <- ggplot(positional_data, aes(x = half_ppr_pts, fill = pos_group)) +
  geom_density(alpha = 0.5) +
  facet_wrap(~ pos_group, scales = "free_y", ncol = 2) +
  theme_minimal() +
  labs(title = "NCAAF Player Distributions by Position (2020-2025)", x = "Total Half-PPR Points", y = "Density", fill = "Position Group") +
  theme(legend.position = "none")

ggsave("NCAAF/Players/positional_distribution.png", plot = plot_cfb_positional, width = 12, height = 10, dpi = 300)

# ==============================================================================
# 4. MASTER CSV EXPORT (PLAYERS & TEAMS)
# ==============================================================================

# A. Export Players to Master CSV
master_players_file <- "Master_Data/master_players.csv"
max_cfb_season_player <- max(cfb_players$season, na.rm = TRUE)

recent_cfb_players <- cfb_players %>%
  filter(season == max_cfb_season_player) %>%
  transmute(
    Player = player_name,
    Team = "NCAAF",
    League = "NCAAF",
    Role = position,
    Season = season,
    Total_Points = half_ppr_pts
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "NCAAF" & master_players$Season == max_cfb_season_player)) {
    write.table(recent_cfb_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_cfb_players), "NCAAF players to Master CSV."))
  } else {
    message("NCAAF Player data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_cfb_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added NCAAF players.")
}

# B. Export Teams to Master CSV
master_teams_file <- "Master_Data/master_teams.csv"
max_cfb_season_team <- max(team_scored$season, na.rm = TRUE)

recent_cfb_teams <- team_scored %>%
  filter(season == max_cfb_season_team) %>%
  transmute(
    Team = team,
    League = "NCAAF",
    Season = season,
    Total_Points = total_team_fantasy_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  if (!any(master_teams$League == "NCAAF" & master_teams$Season == max_cfb_season_team)) {
    write.table(recent_cfb_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_cfb_teams), "NCAAF teams to Master CSV."))
  } else {
    message("NCAAF Team data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_cfb_teams, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added NCAAF teams.")
}

message("NCAAF analysis and Master CSV exports complete!")