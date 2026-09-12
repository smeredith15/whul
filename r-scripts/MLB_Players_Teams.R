library(baseballr)
library(dplyr)
library(ggplot2)
library(purrr)
library(tidyr)
library(janitor)

# ==============================================================================
# 1. SETUP & DIRECTORIES
# ==============================================================================
if (!dir.exists("MLB/Players")) dir.create("MLB/Players", recursive = TRUE)
if (!dir.exists("MLB/Teams")) dir.create("MLB/Teams", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

seasons_to_pull <- 2020:2025

# ==============================================================================
# 2. MID-SEASON (JULY) CONTRACT SCORING ENGINE PARAMETERS
# ==============================================================================
# Schedule Shares (~162 Total Games)
share_post_asb <- 0.42  # ~68 games in Year N (Post-July)
share_pre_asb  <- 0.58  # ~94 games in Year N+1 (Pre-July)

# Discount / Inflation Multipliers
mult_year_n  <- 0.75    # 25% penalty on Year N due to high certainty
mult_year_n1 <- (1 - (share_post_asb * mult_year_n)) / share_pre_asb  # ~1.181x

# Base Point Values
base_reg_win     <- 2.0
base_playoff_win <- 3.0

# Fixed Escalating Series Milestones (Flat values, no further deflation)
pts_series_wc  <- 5
pts_series_lds <- 6
pts_series_lcs <- 7
pts_series_ws  <- 8

# ==============================================================================
# 3. MLB TEAM SCORING (MLB STATS API)
# ==============================================================================
message("Loading MLB Schedules via MLB Stats API (2020-2025)...")

schedules_raw <- map_df(seasons_to_pull, function(yr) {
  Sys.sleep(1) 
  df <- mlb_schedule(season = yr, level_ids = "1")
  # Strip duplicate columns immediately to prevent dplyr crashes
  df <- df[, !duplicated(colnames(df))] 
  return(janitor::clean_names(df))
})

# Filter out spring training and exhibitions
valid_games <- schedules_raw %>%
  filter(game_type %in% c("R", "F", "D", "L", "W")) %>% 
  filter(!is.na(teams_home_score) & !is.na(teams_away_score))

# Reshape into a team-centric dataframe
home_teams <- valid_games %>%
  transmute(
    season, game_date = official_date, game_type,
    team = teams_home_team_name, opp = teams_away_team_name,
    runs_for = teams_home_score, runs_against = teams_away_score,
    is_win = teams_home_is_winner
  )

away_teams <- valid_games %>%
  transmute(
    season, game_date = official_date, game_type,
    team = teams_away_team_name, opp = teams_home_team_name,
    runs_for = teams_away_score, runs_against = teams_home_score,
    is_win = teams_away_is_winner
  )

team_games <- bind_rows(home_teams, away_teams) %>%
  mutate(
    margin = runs_for - runs_against,
    is_big_win = is_win & margin >= 5,
    is_shutout = is_win & runs_against == 0,
    is_reg = game_type == "R"
  )

# Aggregate Team Performance
team_season_summary <- team_games %>%
  group_by(season, team) %>%
  summarise(
    reg_wins = sum(is_win & is_reg, na.rm = TRUE),
    reg_big_wins = sum(is_big_win & is_reg, na.rm = TRUE),
    shutouts = sum(is_shutout & is_reg, na.rm = TRUE),
    run_diff = sum(margin[is_reg], na.rm = TRUE),
    
    wc_wins = sum(is_win & game_type == "F", na.rm = TRUE),
    lds_wins = sum(is_win & game_type == "D", na.rm = TRUE),
    lcs_wins = sum(is_win & game_type == "L", na.rm = TRUE),
    ws_wins = sum(is_win & game_type == "W", na.rm = TRUE),
    
    # Playoff Series Milestones (Accounts for byes)
    series_wc_or_bye = ifelse(lds_wins > 0 | wc_wins >= 2, 1, 0), 
    series_lds       = ifelse(lds_wins >= 3, 1, 0),
    series_lcs       = ifelse(lcs_wins >= 4, 1, 0),
    series_ws        = ifelse(ws_wins == 4, 1, 0),
    .groups = "drop"
  ) %>%
  group_by(season) %>%
  mutate(
    is_division_champ = ifelse(percent_rank(reg_wins) >= 0.80, 1, 0)
  ) %>%
  ungroup()

# ==============================================================================
# TRUE ROLLING 12-MONTH CONTRACT ENGINE (TEAMS)
# ==============================================================================

# 1. Prepare Year N (Draft Year)
team_year_n <- team_season_summary %>%
  transmute(
    contract_year = as.numeric(season), team,
    yr_n_reg_wins   = (reg_wins * share_post_asb) * base_reg_win * mult_year_n,
    yr_n_big_wins   = (reg_big_wins * share_post_asb) * 1.0 * mult_year_n,
    yr_n_shutouts   = (shutouts * share_post_asb) * 2.0 * mult_year_n,
    yr_n_run_diff   = (run_diff * share_post_asb) * 0.05 * mult_year_n,
    yr_n_div_champ  = is_division_champ * 5.0 * mult_year_n,
    yr_n_playoff_game_wins = (wc_wins + lds_wins + lcs_wins + ws_wins) * base_playoff_win * mult_year_n,
    yr_n_playoff_series = (series_wc_or_bye * pts_series_wc) +
      (series_lds * pts_series_lds) +
      (series_lcs * pts_series_lcs) +
      (series_ws * pts_series_ws),
    total_year_n = yr_n_reg_wins + yr_n_big_wins + yr_n_shutouts + yr_n_run_diff + 
      yr_n_div_champ + yr_n_playoff_game_wins + yr_n_playoff_series
  )

# 2. Prepare Year N+1 (Next Year)
team_year_n1 <- team_season_summary %>%
  transmute(
    contract_year = as.numeric(season) - 1, # Shift to align with Draft Year
    team,
    yr_n1_reg_wins = (reg_wins * share_pre_asb) * base_reg_win * mult_year_n1,
    yr_n1_big_wins = (reg_big_wins * share_pre_asb) * 1.0 * mult_year_n1,
    yr_n1_shutouts = (shutouts * share_pre_asb) * 2.0 * mult_year_n1,
    yr_n1_run_diff = (run_diff * share_pre_asb) * 0.05 * mult_year_n1,
    total_year_n1 = yr_n1_reg_wins + yr_n1_big_wins + yr_n1_shutouts + yr_n1_run_diff
  )

# 3. Join cross-season contracts
team_contracts <- inner_join(team_year_n, team_year_n1, by = c("contract_year", "team")) %>%
  mutate(simulated_contract_pts = total_year_n + total_year_n1) %>%
  filter(contract_year >= 2021) # Eradicate 2020 COVID season distortion

plot_mlb_teams <- ggplot(team_contracts, aes(x = simulated_contract_pts)) +
  geom_histogram(aes(y = after_stat(density)), binwidth = 15, fill = "#002D72", color = "white", alpha = 0.7) +
  geom_density(color = "#D50032", linewidth = 1.2) +
  theme_minimal() +
  labs(title = "MLB Team July Contract Points (True Rolling Seasons 2021-2024)", x = "Combined 12-Month Score", y = "Density")

ggsave("MLB/Teams/team_contract_distribution.png", plot = plot_mlb_teams, width = 9, height = 6, dpi = 300)

# ==============================================================================
# 4. MLB PLAYER SCORING (FANGRAPHS)
# ==============================================================================
message("Loading MLB Player Leaderboards via FanGraphs (2020-2025)...")

get_num_col <- function(df, candidate_cols, default = 0) {
  found <- intersect(candidate_cols, names(df))
  if (length(found) == 0) return(rep(default, nrow(df)))
  cols <- lapply(found, function(c) suppressWarnings(as.numeric(df[[c]])))
  res <- do.call(dplyr::coalesce, cols)
  dplyr::coalesce(res, default)
}

get_char_col <- function(df, candidate_cols, default = "Unknown Player") {
  found <- intersect(candidate_cols, names(df))
  if (length(found) == 0) return(rep(default, nrow(df)))
  cols <- lapply(found, function(c) as.character(df[[c]]))
  res <- do.call(dplyr::coalesce, cols)
  dplyr::coalesce(res, default)
}

# Pull Batters
batters <- map_df(seasons_to_pull, function(yr) {
  message("  Fetching Batters: ", yr)
  Sys.sleep(4) 
  tryCatch({
    df <- fg_batter_leaders(startseason = yr, endseason = yr, qual = 100)
    df <- df[, !duplicated(colnames(df))] 
    return(janitor::clean_names(df))
  }, error = function(e) {
    message("    -> Failed to fetch batters for ", yr, ". Skipping.")
    return(data.frame())
  })
}) 

# Pull Pitchers
pitchers <- map_df(seasons_to_pull, function(yr) {
  message("  Fetching Pitchers: ", yr)
  Sys.sleep(4) 
  tryCatch({
    df <- fg_pitcher_leaders(startseason = yr, endseason = yr, qual = 30)
    df <- df[, !duplicated(colnames(df))]
    return(janitor::clean_names(df))
  }, error = function(e) {
    message("    -> Failed to fetch pitchers for ", yr, ". Skipping.")
    return(data.frame())
  })
}) 

# Score Batters
batters_scored <- batters %>%
  mutate(
    player_name_clean = get_char_col(., c("playername", "player_name", "name")),
    ab_n  = get_num_col(., c("ab", "at_bats")),
    h_n   = get_num_col(., c("h", "hits")),
    x2b_n = get_num_col(., c("2b", "x2b", "X2B", "doubles")),
    x3b_n = get_num_col(., c("3b", "x3b", "X3B", "triples")),
    hr_n  = get_num_col(., c("hr", "home_runs")),
    bb_n  = get_num_col(., c("bb", "base_on_balls")),
    hbp_n = get_num_col(., c("hbp", "hit_by_pitch")),
    sb_n  = get_num_col(., c("sb", "stolen_bases")),
    cs_n  = get_num_col(., c("cs", "caught_stealing")),
    off_n = get_num_col(., c("offense", "off")),
    def_n = get_num_col(., c("defense", "def")),
    
    fg_pts_hit = (ab_n * -1.0) + (h_n * 5.6) + (x2b_n * 2.9) + (x3b_n * 5.7) + 
      (hr_n * 9.4) + (bb_n * 3.0) + (hbp_n * 3.0) + (sb_n * 1.9) + (cs_n * -2.8),
    
    owar_adj = (off_n / 10) * 0.25, 
    dwar_adj = (def_n / 10) * 1.5,
    
    total_season_pts = fg_pts_hit + (owar_adj * 10) + (dwar_adj * 10)
  ) %>%
  filter(total_season_pts > 0) %>%
  mutate(role = "Batter")

# Score Pitchers
pitchers_scored <- pitchers %>%
  mutate(
    player_name_clean = get_char_col(., c("playername", "player_name", "name")),
    ip_n  = get_num_col(., c("ip", "innings_pitched")),
    so_n  = get_num_col(., c("so", "k", "strikeouts")),
    h_n   = get_num_col(., c("h", "hits")),
    bb_n  = get_num_col(., c("bb", "base_on_balls")),
    hbp_n = get_num_col(., c("hbp", "hit_by_pitch")),
    hr_n  = get_num_col(., c("hr", "home_runs")),
    sv_n  = get_num_col(., c("sv", "saves")),
    hld_n = get_num_col(., c("hld", "holds")),
    war_n = get_num_col(., c("war", "fwar")),
    
    fg_pts_pitch = (ip_n * 7.4) + (so_n * 2.0) + (h_n * -2.6) + (bb_n * -3.0) + 
      (hbp_n * -3.0) + (hr_n * -12.3) + (sv_n * 5.0) + (hld_n * 4.0),
    
    pwar_adj = war_n * 0.5,
    
    total_season_pts = fg_pts_pitch + (pwar_adj * 10)
  ) %>%
  filter(total_season_pts > 0) %>%
  mutate(role = "Pitcher")

# Combine datasets
all_players <- bind_rows(
  batters_scored %>% select(season, playername = player_name_clean, role, total_season_pts),
  pitchers_scored %>% select(season, playername = player_name_clean, role, total_season_pts)
)

# Prepare Year N & N+1
player_year_n <- all_players %>%
  select(contract_year = season, playername, role, pts_year_n = total_season_pts) %>%
  mutate(contract_year = as.numeric(contract_year))

player_year_n1 <- all_players %>%
  mutate(contract_year = as.numeric(season) - 1) %>%
  select(contract_year, playername, role, pts_year_n1 = total_season_pts)

true_player_contracts <- inner_join(player_year_n, player_year_n1, by = c("contract_year", "playername", "role")) %>%
  mutate(
    simulated_contract_pts = (pts_year_n * share_post_asb * mult_year_n) + 
      (pts_year_n1 * share_pre_asb * mult_year_n1)
  ) %>%
  filter(contract_year >= 2021) # Eradicate 2020 COVID season distortion

# Slicing for Plotting
top_batters <- true_player_contracts %>%
  filter(role == "Batter") %>%
  group_by(contract_year) %>%
  slice_max(simulated_contract_pts, n = 150, with_ties = FALSE) %>%
  ungroup()

top_pitchers <- true_player_contracts %>%
  filter(role == "Pitcher") %>%
  group_by(contract_year) %>%
  slice_max(simulated_contract_pts, n = 100, with_ties = FALSE) %>%
  ungroup()

top_players <- bind_rows(top_batters, top_pitchers)

plot_mlb_players <- ggplot(top_players, aes(x = simulated_contract_pts, fill = role)) +
  geom_density(alpha = 0.6) +
  facet_wrap(~ role, scales = "free_y", ncol = 1) +
  scale_fill_manual(values = c("Batter" = "#005A9C", "Pitcher" = "#EF3340")) +
  theme_minimal() +
  labs(title = "MLB Player July Contract Points (True Rolling Seasons 2021-2024)", x = "Combined 12-Month Score", y = "Density") +
  theme(legend.position = "none")

ggsave("MLB/Players/player_role_distribution.png", plot = plot_mlb_players, width = 10, height = 8, dpi = 300)

# ==============================================================================
# 5. MASTER CSV EXPORT (PLAYERS & TEAMS)
# ==============================================================================

# A. Export Players to Master CSV with 50% Secondary Tax (Shohei Ohtani Rule)
master_players_file <- "Master_Data/master_players.csv"
max_mlb_season_player <- max(true_player_contracts$contract_year, na.rm = TRUE)

recent_mlb_players <- true_player_contracts %>%
  filter(contract_year == max_mlb_season_player) %>%
  group_by(playername, contract_year) %>%
  summarise(
    role_count = n(),
    primary_pts = max(simulated_contract_pts),
    secondary_pts = ifelse(role_count > 1, min(simulated_contract_pts), 0),
    # 50% Tax on secondary position (Ohtani Rule)
    total_pts = primary_pts + (secondary_pts * 0.5),
    role_clean = ifelse(role_count > 1, "Two-Way", role[which.max(simulated_contract_pts)]),
    .groups = "drop"
  ) %>%
  transmute(
    Player = playername,
    Team = "MLB",
    League = "MLB",
    Role = role_clean,
    Season = contract_year,
    Total_Points = total_pts
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "MLB" & master_players$Season == max_mlb_season_player)) {
    write.table(recent_mlb_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_mlb_players), "MLB players to Master CSV."))
  } else {
    message("MLB Player data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_mlb_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added MLB players.")
}

# B. Export Teams to Master CSV
master_teams_file <- "Master_Data/master_teams.csv"
max_mlb_season_team <- max(team_contracts$contract_year, na.rm = TRUE)

recent_mlb_teams <- team_contracts %>%
  filter(contract_year == max_mlb_season_team) %>%
  transmute(
    Team = team,
    League = "MLB",
    Season = contract_year,
    Total_Points = simulated_contract_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  if (!any(master_teams$League == "MLB" & master_teams$Season == max_mlb_season_team)) {
    write.table(recent_mlb_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_mlb_teams), "MLB teams to Master CSV."))
  } else {
    message("MLB Team data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_mlb_teams, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added MLB teams.")
}

message("\nMLB analysis and Master CSV exports complete!")