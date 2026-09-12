library(baseballr)
library(dplyr)
library(ggplot2)

# ==============================================================================
# 1. SETUP & TARGETING
# ==============================================================================
if (!dir.exists("NCAABaseball/Teams")) dir.create("NCAABaseball/Teams", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

seasons_to_pull <- 2021:2024 

message("Loading NCAA Division 1 Teams...")
all_teams <- tryCatch({
  load_ncaa_baseball_teams() %>% filter(division == 1)
}, error = function(e) {
  stop("Failed to load NCAA teams. Check your connection or baseballr package version.")
})

target_conferences <- c("SEC", "Southeastern", "ACC", "Atlantic Coast", 
                        "Big Ten", "Big 12", "Sun Belt")

target_teams <- all_teams %>%
  filter(grepl(paste(target_conferences, collapse = "|"), conference, ignore.case = TRUE)) %>%
  select(team_id, team_name, conference) %>%
  distinct(team_id, .keep_all = TRUE) 

message(sprintf("Targeting %d teams across major conferences.", nrow(target_teams)))

# Base Point Values
base_reg_win     <- 2.0
pts_series_reg   <- 5  
pts_series_sup   <- 6  
pts_series_cws   <- 8  

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
message("Downloading Bulk Schedules via baseballr Release Assets...")

team_games_raw <- purrr::map_df(seasons_to_pull, function(yr) {
  message("  Fetching Schedule for ", yr)
  tryCatch({
    load_ncaa_baseball_schedule(seasons = yr) %>% janitor::clean_names()
  }, error = function(e) {
    message("    Failed to fetch ", yr)
    data.frame()
  })
})

# ==============================================================================
# 3. NCAA TEAM SCORING
# ==============================================================================
message("Processing Team Scores...")

team_season_summary <- team_games_raw %>%
  filter(!is.na(home_team_score) & !is.na(away_team_score)) %>%
  filter(home_team_id %in% target_teams$team_id | away_team_id %in% target_teams$team_id) %>%
  left_join(target_teams, by = c("home_team_id" = "team_id")) %>%
  rename(target_team_home = team_name) %>%
  left_join(target_teams, by = c("away_team_id" = "team_id")) %>%
  rename(target_team_away = team_name) %>%
  mutate(
    notes_str = get_char_col(., c("contest_name", "notes", "description", "game_info")),
    date_str  = get_char_col(., c("date", "game_date", "start_date")),
    
    target_team = coalesce(target_team_home, target_team_away),
    target_conf = coalesce(conference.x, conference.y),
    
    is_home = !is.na(target_team_home),
    runs_for = ifelse(is_home, as.numeric(home_team_score), as.numeric(away_team_score)),
    runs_against = ifelse(is_home, as.numeric(away_team_score), as.numeric(home_team_score)),
    
    margin = runs_for - runs_against,
    is_win = margin > 0,
    
    is_regional = grepl("Regional", notes_str, ignore.case = TRUE) & !grepl("Super", notes_str, ignore.case = TRUE),
    is_super = grepl("Super", notes_str, ignore.case = TRUE),
    is_cws = grepl("College World Series|CWS", notes_str, ignore.case = TRUE),
    
    is_june = grepl("-06-", date_str) | grepl("^6/", date_str),
    is_postseason = is_regional | is_super | is_cws | is_june
  ) %>%
  group_by(season = year, team = target_team, conference = target_conf) %>%
  summarise(
    games = n(),
    reg_wins = sum(is_win & !is_postseason, na.rm = TRUE),
    run_diff = sum(margin[!is_postseason], na.rm = TRUE),
    
    regional_wins = sum(is_win & is_regional, na.rm = TRUE),
    super_wins = sum(is_win & is_super, na.rm = TRUE),
    cws_wins = sum(is_win & is_cws, na.rm = TRUE),
    june_wins = sum(is_win & is_june, na.rm = TRUE),
    
    # Corrected scalar checks
    series_regional = ifelse(regional_wins >= 3 | june_wins >= 3, 1, 0),
    series_super = ifelse(super_wins >= 2, 1, 0),
    series_cws_champ = ifelse(cws_wins >= 4, 1, 0), 
    .groups = "drop"
  )

team_scored <- team_season_summary %>%
  mutate(
    pts_reg_wins = reg_wins * base_reg_win,
    pts_run_diff = run_diff * 0.05,
    pts_postseason = (series_regional * pts_series_reg) + 
      (series_super * pts_series_sup) + 
      (series_cws_champ * pts_series_cws),
    
    total_team_pts = pts_reg_wins + pts_run_diff + pts_postseason
  ) %>% 
  filter(games >= 10)

plot_ncaa_teams <- ggplot(team_scored, aes(x = total_team_pts)) +
  geom_histogram(aes(y = after_stat(density)), binwidth = 15, fill = "#002D72", color = "white", alpha = 0.7) +
  geom_density(color = "#D50032", linewidth = 1.2) +
  theme_minimal() +
  labs(title = "NCAA Baseball Team Fantasy Points (2021-2023)", 
       subtitle = "Major Conferences (SEC, ACC, B10, B12, Sun Belt)",
       x = "Total Season Points", y = "Density")

ggsave("NCAABaseball/Teams/team_contract_distribution.png", plot = plot_ncaa_teams, width = 9, height = 6, dpi = 300)

# ==============================================================================
# 4. MASTER CSV EXPORT (TEAMS ONLY)
# ==============================================================================
master_teams_file <- "Master_Data/master_teams.csv"
max_ncaa_season_team <- max(team_scored$season, na.rm = TRUE)

recent_ncaa_teams <- team_scored %>%
  filter(season == max_ncaa_season_team) %>%
  transmute(
    Team = team,
    League = "NCAABaseball",
    Season = season,
    Total_Points = total_team_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  if (!any(master_teams$League == "NCAABaseball" & master_teams$Season == max_ncaa_season_team)) {
    write.table(recent_ncaa_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_ncaa_teams), "NCAA Baseball teams to Master CSV."))
  } else {
    message("NCAA Baseball Team data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_ncaa_teams, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added NCAA Baseball teams.")
}

message("NCAA Baseball Team bulk analysis and Master CSV export complete!")