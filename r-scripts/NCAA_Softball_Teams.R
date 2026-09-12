library(softballR)
library(dplyr)
library(purrr)
library(tidyr)
library(janitor)
library(ggplot2)

# ==============================================================================
# 1. SETUP 
# ==============================================================================
if (!dir.exists("NCAASoftball/Teams")) dir.create("NCAASoftball/Teams", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

seasons_to_pull <- 2021:2025 

# Base Point Values
base_reg_win     <- 2.0
pts_series_reg   <- 5  
pts_series_sup   <- 6  
pts_series_cws   <- 8  

get_num_col <- function(df, candidate_cols, default = -1) {
  found <- intersect(candidate_cols, names(df))
  if (length(found) == 0) return(rep(default, nrow(df)))
  cols <- lapply(found, function(c) suppressWarnings(as.numeric(df[[c]])))
  res <- do.call(dplyr::coalesce, cols)
  dplyr::coalesce(res, default)
}

get_char_col <- function(df, candidate_cols, default = "") {
  found <- intersect(candidate_cols, names(df))
  if (length(found) == 0) return(rep(default, nrow(df)))
  cols <- lapply(found, function(c) as.character(df[[c]]))
  res <- do.call(dplyr::coalesce, cols)
  dplyr::coalesce(res, default)
}

# ==============================================================================
# 2. BULK SCHEDULE DOWNLOAD
# ==============================================================================
message("Downloading Bulk Softball CSVs...")

team_games_raw <- map_df(seasons_to_pull, function(yr) {
  message("  Fetching Schedule for ", yr)
  tryCatch({
    df <- load_ncaa_softball_scoreboard(season = yr) 
    if(nrow(df) > 0) {
      df <- df %>% janitor::clean_names() %>% mutate(year = yr)
      return(df)
    } else {
      return(data.frame())
    }
  }, error = function(e) {
    message("    Failed to fetch schedule for ", yr)
    data.frame()
  })
})

# ==============================================================================
# 3. NCAA SOFTBALL TEAM SCORING
# ==============================================================================
message("Processing Team Scores natively from softballR...")

# Extract core columns and squash API duplication glitches
games_clean <- team_games_raw %>%
  mutate(
    home_team = get_char_col(., c("home_team", "home", "home_team_name")),
    away_team = get_char_col(., c("away_team", "away", "away_team_name")),
    home_score = get_num_col(., c("home_team_runs", "home_team_score", "home_score", "home_runs"), default = -1),
    away_score = get_num_col(., c("away_team_runs", "away_team_score", "away_score", "away_runs"), default = -1),
    notes_str = get_char_col(., c("game_info", "notes", "contest_name", "description")),
    date_str  = get_char_col(., c("game_date", "date", "start_date"))
  ) %>%
  filter(home_team != "" & away_team != "") %>%
  filter(home_score >= 0 & away_score >= 0) %>%
  # Deduplicate to prevent API Cartesian explosion
  distinct(year, date_str, home_team, away_team, .keep_all = TRUE) %>%
  mutate(
    is_regional = grepl("Regional", notes_str, ignore.case = TRUE) & !grepl("Super", notes_str, ignore.case = TRUE),
    is_super = grepl("Super", notes_str, ignore.case = TRUE),
    is_cws = grepl("WCWS|Women's College World Series", notes_str, ignore.case = TRUE),
    is_june = grepl("-06-", date_str) | grepl("^6/", date_str),
    is_postseason = is_regional | is_super | is_cws | is_june
  )

# Reshape Home and Away 
home_games <- games_clean %>%
  transmute(
    season = year, team = home_team, 
    runs_for = home_score, runs_against = away_score, 
    is_regional, is_super, is_cws, is_june, is_postseason
  )

away_games <- games_clean %>%
  transmute(
    season = year, team = away_team, 
    runs_for = away_score, runs_against = home_score, 
    is_regional, is_super, is_cws, is_june, is_postseason
  )

# Combine, score, and aggregate
team_scored <- bind_rows(home_games, away_games) %>%
  mutate(
    margin = runs_for - runs_against,
    is_win = margin > 0
  ) %>%
  group_by(season, team) %>%
  summarise(
    games = n(),
    reg_wins = sum(is_win & !is_postseason, na.rm = TRUE),
    run_diff = sum(margin[!is_postseason], na.rm = TRUE),
    regional_wins = sum(is_win & is_regional, na.rm = TRUE),
    super_wins = sum(is_win & is_super, na.rm = TRUE),
    cws_wins = sum(is_win & is_cws, na.rm = TRUE),
    june_wins = sum(is_win & is_june, na.rm = TRUE),
    
    series_regional = ifelse(regional_wins >= 3 | june_wins >= 3, 1, 0),
    series_super = ifelse(super_wins >= 2, 1, 0),
    series_cws_champ = ifelse(cws_wins >= 5, 1, 0),
    .groups = "drop"
  ) %>%
  mutate(
    pts_reg_wins = reg_wins * base_reg_win,
    pts_run_diff = run_diff * 0.05,
    pts_postseason = (series_regional * pts_series_reg) + 
      (series_super * pts_series_sup) + 
      (series_cws_champ * pts_series_cws),
    total_team_pts = pts_reg_wins + pts_run_diff + pts_postseason
  ) %>% 
  # Set a 20-game minimum to filter out extreme low-level/partial schedules
  filter(games >= 20)

if(nrow(team_scored) > 0) {
  plot_ncaa_teams <- ggplot(team_scored, aes(x = total_team_pts)) +
    geom_histogram(aes(y = after_stat(density)), binwidth = 15, fill = "#002D72", color = "white", alpha = 0.7) +
    geom_density(color = "#D50032", linewidth = 1.2) +
    theme_minimal() +
    labs(title = "NCAA Softball Team Fantasy Points (2021-2025)", 
         subtitle = "All Evaluated Teams (Min. 20 Games Played)",
         x = "Total Season Points", y = "Density")
  
  ggsave("NCAASoftball/Teams/team_distribution.png", plot = plot_ncaa_teams, width = 9, height = 6, dpi = 300)
} 

# ==============================================================================
# 4. MASTER CSV EXPORT (TEAMS ONLY)
# ==============================================================================
if(nrow(team_scored) > 0) {
  master_teams_file <- "Master_Data/master_teams.csv"
  max_ncaasb_season_team <- max(team_scored$season, na.rm = TRUE)
  
  recent_ncaasb_teams <- team_scored %>%
    filter(season == max_ncaasb_season_team) %>%
    transmute(Team = team, League = "NCAASoftball", Season = season, Total_Points = total_team_pts)
  
  if (file.exists(master_teams_file)) {
    # Purge any old/corrupted NCAASoftball data for this season before appending
    master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
    master_teams <- master_teams %>% filter(League != "NCAASoftball" | Season != max_ncaasb_season_team)
    write.csv(master_teams, master_teams_file, row.names = FALSE)
    
    write.table(recent_ncaasb_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully replaced and appended", nrow(recent_ncaasb_teams), "clean NCAA Softball teams to Master CSV."))
  } else {
    write.csv(recent_ncaasb_teams, master_teams_file, row.names = FALSE)
    message("Created Master_Data/master_teams.csv and added NCAA Softball teams.")
  }
}

message("NCAA Softball Team-Only pipeline complete!")