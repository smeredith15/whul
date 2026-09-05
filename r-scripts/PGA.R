library(golfastr)
library(dplyr)
library(purrr)
library(ggplot2)
library(janitor)

# ==============================================================================
# 1. SETUP & DIRECTORIES
# ==============================================================================
if (!dir.exists("PGA/Rankings")) dir.create("PGA/Rankings", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

# Standard PGA Tour finishing points for Top 30
pga_standard_points <- c(
  500, 300, 190, 135, 110, 100, 90, 85, 80, 75,
  70, 65, 60, 57, 54, 51, 48, 45, 42, 39,
  36, 33, 30, 27, 24, 21, 18, 15, 12, 10
)

target_seasons <- 2020:2024

message("Downloading 5-Year PGA Tour Leaderboards (2020-2024) via golfastr...")

# Pull hosted season leaderboards directly (no manual CSV required)
pga_raw <- map_df(target_seasons, function(yr) {
  message("  Fetching ", yr, " season data...")
  tryCatch({
    load_leaderboard(year = yr) %>%
      clean_names() %>%
      mutate(season_year = yr)
  }, error = function(e) {
    message("    [!] Error loading ", yr, ": ", e$message)
    data.frame()
  })
})

# Process finishing positions and gross points
pga_scored <- pga_raw %>%
  rename_with(~"player_display", any_of(c("player_name", "athlete_display_name", "player"))) %>%
  rename_with(~"pos_str", any_of(c("position", "pos", "place"))) %>%
  rename_with(~"tourney_str", any_of(c("tournament_name", "event_name", "tournament"))) %>%
  filter(!is.na(pos_str) & !is.na(player_display)) %>%
  mutate(
    # Extract numeric rank from strings like "T12" or "12"
    finish_pos = as.numeric(gsub("[^0-9]", "", as.character(pos_str))),
    
    # Award base points to Top 30 finishers
    base_pts = case_when(
      !is.na(finish_pos) & finish_pos >= 1 & finish_pos <= 30 ~ pga_standard_points[finish_pos],
      TRUE ~ 0
    ),
    
    # Major Championship / Players Multiplier (1.5x)
    is_major = grepl("masters|pga championship|u.s. open|open championship|players", 
                     tourney_str, ignore.case = TRUE),
    gross_fantasy_pts = ifelse(is_major, base_pts * 1.5, base_pts)
  ) %>%
  group_by(season_year, player = player_display) %>%
  summarise(
    events_played = n(),
    cuts_made = sum(!is.na(finish_pos) & finish_pos <= 70),
    wins = sum(!is.na(finish_pos) & finish_pos == 1),
    top_10s = sum(!is.na(finish_pos) & finish_pos <= 10),
    total_gross_points = sum(gross_fantasy_pts, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter(events_played >= 8)

# Isolate Top 100 golfers per season
top100_pga <- pga_scored %>%
  group_by(season_year) %>%
  slice_max(total_gross_points, n = 100, with_ties = FALSE) %>%
  ungroup()

write.csv(top100_pga, "PGA/Rankings/pga_player_totals_2020_2024.csv", row.names = FALSE)

# Generate density distribution
plot_pga <- ggplot(top100_pga, aes(x = total_gross_points, fill = factor(season_year))) +
  geom_density(alpha = 0.4) +
  theme_minimal() +
  scale_fill_brewer(palette = "Greens") +
  labs(
    title = "PGA Tour Golfer Fantasy Points Distribution (5-Year Window)",
    subtitle = "Top 100 Golfers Per Season (Gross Points, Playoff Reset Removed)",
    x = "Total Season Fantasy Points", y = "Density", fill = "Season"
  )

ggsave("PGA/Rankings/golfer_distribution_5yr.png", plot = plot_pga, width = 10, height = 6, dpi = 300)

message("PGA 5-Year Engine complete! Data and plot saved to PGA/Rankings/")

# ==============================================================================
# 4. MASTER CSV EXPORT (PLAYERS ONLY)
# ==============================================================================
master_players_file <- "Master_Data/master_players.csv"
max_pga_season_player <- max(top100_pga$season_year, na.rm = TRUE)

recent_pga_players <- top100_pga %>%
  filter(season_year == max_pga_season_player) %>%
  transmute(
    Player = player,
    Team = "PGA",
    League = "PGA",
    Role = "Golfer",
    Season = season_year,
    Total_Points = total_gross_points
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "PGA" & master_players$Season == max_pga_season_player)) {
    write.table(recent_pga_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_pga_players), "PGA golfers to Master CSV."))
  } else {
    message("PGA Golfer data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_pga_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added PGA golfers.")
}

message("PGA analysis and Master CSV export complete!")