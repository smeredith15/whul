# Load required libraries
library(nflreadr)
library(dplyr)
library(ggplot2)
library(tidyr)

# 1. Ensure directories exist
if (!dir.exists("NFL/Players")) dir.create("NFL/Players", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

# 2. Pull stats for 2020 through 2025
seasons_to_pull <- 2020:2025
raw_stats <- load_player_stats(seasons = seasons_to_pull)

# 3. Aggregate player stats per season & calculate Half-PPR scores
season_stats <- raw_stats %>%
  group_by(season, player_id, player_display_name, position) %>%
  summarise(
    team = paste(unique(team[!is.na(team)]), collapse = "/"),
    games_played = n_distinct(week),
    passing_yards = sum(passing_yards, na.rm = TRUE),
    passing_tds = sum(passing_tds, na.rm = TRUE),
    interceptions = sum(passing_interceptions, na.rm = TRUE),
    rushing_yards = sum(rushing_yards, na.rm = TRUE),
    rushing_tds = sum(rushing_tds, na.rm = TRUE),
    receptions = sum(receptions, na.rm = TRUE),
    receiving_yards = sum(receiving_yards, na.rm = TRUE),
    receiving_tds = sum(receiving_tds, na.rm = TRUE),
    fumbles_lost = sum(sack_fumbles_lost + rushing_fumbles_lost + receiving_fumbles_lost, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter(position %in% c("QB", "RB", "WR", "TE")) %>%
  # Half-PPR scoring formula
  mutate(
    half_ppr_pts = (passing_yards * 0.04) +
      (passing_tds * 4) +
      (interceptions * -2) +
      (rushing_yards * 0.1) +
      (rushing_tds * 6) +
      (receptions * 0.5) +
      (receiving_yards * 0.1) +
      (receiving_tds * 6) +
      (fumbles_lost * -2)
  ) %>%
  filter(half_ppr_pts > 0)

# ==============================================================================
# SET 1: Distribution of Top 100 Overall Performers per Season (2020-2025)
# ==============================================================================

top_100_overall <- season_stats %>%
  group_by(season) %>%
  slice_max(order_by = half_ppr_pts, n = 100, with_ties = FALSE) %>%
  ungroup()

plot_top100 <- ggplot(top_100_overall, aes(x = half_ppr_pts, fill = factor(season))) +
  geom_density(alpha = 0.4) +
  facet_wrap(~ season, ncol = 1) +
  theme_minimal() +
  labs(
    title = "Half-PPR Score Distribution: Top 100 Overall Players (2020-2025)",
    subtitle = "Density profiles of top fantasy contributors across 6 NFL seasons",
    x = "Total Half-PPR Points",
    y = "Density",
    fill = "Season"
  ) +
  theme(
    legend.position = "none",
    plot.title = element_text(face = "bold", size = 14),
    strip.text = element_text(face = "bold", size = 11)
  )

ggsave("NFL/Players/top_100_overall_distribution.png", plot = plot_top100, width = 10, height = 14, dpi = 300)

# ==============================================================================
# SET 2: Positional Score Distributions (QB, RB, WR, TE, FLEX)
# ==============================================================================

qbs  <- season_stats %>% filter(position == "QB") %>% group_by(season) %>% slice_max(half_ppr_pts, n = 20, with_ties = FALSE) %>% mutate(pos_group = "QB (Top 20)")
rbs  <- season_stats %>% filter(position == "RB") %>% group_by(season) %>% slice_max(half_ppr_pts, n = 30, with_ties = FALSE) %>% mutate(pos_group = "RB (Top 30)")
wrs  <- season_stats %>% filter(position == "WR") %>% group_by(season) %>% slice_max(half_ppr_pts, n = 50, with_ties = FALSE) %>% mutate(pos_group = "WR (Top 50)")
tes  <- season_stats %>% filter(position == "TE") %>% group_by(season) %>% slice_max(half_ppr_pts, n = 20, with_ties = FALSE) %>% mutate(pos_group = "TE (Top 20)")
flex <- season_stats %>% filter(position %in% c("RB", "WR", "TE")) %>% group_by(season) %>% slice_max(half_ppr_pts, n = 100, with_ties = FALSE) %>% mutate(pos_group = "FLEX (Top 100)")

positional_data <- bind_rows(qbs, rbs, wrs, tes, flex) %>%
  mutate(pos_group = factor(pos_group, levels = c("QB (Top 20)", "RB (Top 30)", "WR (Top 50)", "TE (Top 20)", "FLEX (Top 100)")))

plot_positional <- ggplot(positional_data, aes(x = half_ppr_pts, fill = pos_group)) +
  geom_density(alpha = 0.5) +
  facet_wrap(~ pos_group, scales = "free_y", ncol = 2) +
  theme_minimal() +
  labs(
    title = "Half-PPR Score Distribution by Positional Tier (2020-2025 Data)",
    subtitle = "Compares distribution shapes and tails for roster relevance pools",
    x = "Total Half-PPR Season Points",
    y = "Density",
    fill = "Position Group"
  ) +
  theme(
    legend.position = "none",
    plot.title = element_text(face = "bold", size = 14),
    strip.text = element_text(face = "bold", size = 11)
  )

ggsave("NFL/Players/positional_distribution.png", plot = plot_positional, width = 12, height = 10, dpi = 300)

# ==============================================================================
# MASTER CSV EXPORT (PLAYERS)
# ==============================================================================
master_players_file <- "Master_Data/master_players.csv"
max_nfl_season <- max(season_stats$season, na.rm = TRUE)

recent_nfl_players <- season_stats %>%
  filter(season == max_nfl_season) %>%
  transmute(
    Player = player_display_name,
    Team = team,
    League = "NFL",
    Role = position,
    Season = season,
    Total_Points = half_ppr_pts
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "NFL" & master_players$Season == max_nfl_season)) {
    write.table(recent_nfl_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_nfl_players), "NFL players to Master CSV."))
  } else {
    message("NFL Player data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_nfl_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added NFL players.")
}

message("NFL Player analysis complete!")