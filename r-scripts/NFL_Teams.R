# Load required libraries
library(nflreadr)
library(dplyr)
library(ggplot2)
library(tidyr)

# 1. Ensure directories exist
if (!dir.exists("NFL/Teams")) dir.create("NFL/Teams", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

# 2. Load schedule and team metadata (2020-2025)
seasons_to_pull <- 2020:2025
schedules <- load_schedules(seasons = seasons_to_pull)
teams_meta <- load_teams() %>% select(team_abbr, team_division)

# 3. Reshape schedule into a team-game level dataframe (Home & Away sides)
home_games <- schedules %>%
  filter(!is.na(home_score) & !is.na(away_score)) %>%
  transmute(
    season,
    game_type,
    team = home_team,
    opp = away_team,
    points_for = home_score,
    points_against = away_score,
    div_game = ifelse(div_game == 1, TRUE, FALSE)
  )

away_games <- schedules %>%
  filter(!is.na(home_score) & !is.na(away_score)) %>%
  transmute(
    season,
    game_type,
    team = away_team,
    opp = home_team,
    points_for = away_score,
    points_against = home_score,
    div_game = ifelse(div_game == 1, TRUE, FALSE)
  )

team_games <- bind_rows(home_games, away_games) %>%
  mutate(
    margin = points_for - points_against,
    is_win = margin > 0,
    is_big_win = margin >= 9,
    is_shutout = (points_against == 0) & is_win
  )

# 4. Aggregate regular season & playoff stats per team per season
team_season_summary <- team_games %>%
  group_by(season, team) %>%
  summarise(
    reg_wins = sum(is_win & game_type == "REG"),
    reg_big_wins = sum(is_big_win & game_type == "REG"),
    reg_shutouts = sum(is_shutout & game_type == "REG"),
    div_wins = sum(is_win & game_type == "REG" & div_game),
    point_diff = sum(margin[game_type == "REG"]),
    
    playoff_appearance = ifelse(any(game_type %in% c("WC", "DIV", "CON", "SB")), 1, 0),
    playoff_wins = sum(is_win & game_type %in% c("WC", "DIV", "CON", "SB")),
    .groups = "drop"
  ) %>%
  left_join(teams_meta, by = c("team" = "team_abbr"))

# 5. Determine Division Champions
team_season_summary <- team_season_summary %>%
  group_by(season, team_division) %>%
  arrange(desc(reg_wins), desc(point_diff)) %>%
  mutate(div_champ = ifelse(row_number() == 1, 1, 0)) %>%
  ungroup()

# 6. Apply Proposed Fantasy Scoring Model
team_scored <- team_season_summary %>%
  mutate(
    pts_wins = reg_wins * 10,
    pts_big_wins = reg_big_wins * 3,
    pts_shutouts = reg_shutouts * 5,
    pts_div_wins = div_wins * 2,
    pts_div_champ = div_champ * 15,
    pts_playoff_app = playoff_appearance * 10,
    pts_playoff_wins = playoff_wins * 15,
    pts_point_diff = point_diff * 0.1,
    
    total_team_fantasy_pts = pts_wins + pts_big_wins + pts_shutouts + 
      pts_div_wins + pts_div_champ + pts_playoff_app + 
      pts_playoff_wins + pts_point_diff
  )

# 7. Plot 1: Overall NFL Team Score Distribution (2020-2025 Aggregate)
plot_team_overall <- ggplot(team_scored, aes(x = total_team_fantasy_pts)) +
  geom_histogram(aes(y = after_stat(density)), binwidth = 15, fill = "#1d70b8", color = "white", alpha = 0.7) +
  geom_density(color = "#002244", linewidth = 1.2) +
  theme_minimal() +
  labs(
    title = "NFL Team Fantasy Points Distribution (2020-2025)",
    subtitle = "Based on Wins, Margins, Shutouts, Division Titles, Playoffs & Point Differential",
    x = "Total Season Points (Team)",
    y = "Density"
  ) +
  theme(plot.title = element_text(face = "bold", size = 14))

ggsave("NFL/Teams/team_overall_distribution.png", plot = plot_team_overall, width = 9, height = 6, dpi = 300)

# 8. Plot 2: Year-over-Year NFL Team Score Distribution (2020-2025)
plot_team_yearly <- ggplot(team_scored, aes(x = total_team_fantasy_pts, fill = factor(season))) +
  geom_density(alpha = 0.4) +
  facet_wrap(~ season, ncol = 1) +
  theme_minimal() +
  labs(
    title = "NFL Team Score Distribution by Season (2020-2025)",
    subtitle = "Year-over-year consistency across all 32 NFL franchises",
    x = "Total Season Points (Team)",
    y = "Density",
    fill = "Season"
  ) +
  theme(
    legend.position = "none",
    plot.title = element_text(face = "bold", size = 14),
    strip.text = element_text(face = "bold", size = 11)
  )

ggsave("NFL/Teams/team_yearly_distribution.png", plot = plot_team_yearly, width = 9, height = 12, dpi = 300)

# ==============================================================================
# MASTER CSV EXPORT (TEAMS)
# ==============================================================================
master_teams_file <- "Master_Data/master_teams.csv"
max_nfl_season_team <- max(team_scored$season, na.rm = TRUE)

recent_nfl_teams <- team_scored %>%
  filter(season == max_nfl_season_team) %>%
  transmute(
    Team = team,
    League = "NFL",
    Season = season,
    Total_Points = total_team_fantasy_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  if (!any(master_teams$League == "NFL" & master_teams$Season == max_nfl_season_team)) {
    write.table(recent_nfl_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_nfl_teams), "NFL teams to Master CSV."))
  } else {
    message("NFL Team data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_nfl_teams, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added NFL teams.")
}

message("NFL Team analysis complete!")