library(dplyr)
library(readr)
library(ggplot2)
library(janitor)

# ==============================================================================
# 1. SETUP & DATA INGESTION
# ==============================================================================
if (!dir.exists("NASCAR/Rankings")) dir.create("NASCAR/Rankings", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

message("Loading local 5-year NASCAR Cup Series CSV...")

# Using the local file provided
file_path <- "NASCAR 2017-2024 Full Race  Points Data - Cup.csv"

if (!file.exists(file_path)) {
  stop("CRITICAL ERROR: Local CSV not found. Please verify the filename in your RStudio directory.")
}

cup_data <- read_csv(file_path, show_col_types = FALSE) %>% clean_names()

# 5-Year Window (2020–2024)
target_seasons <- 2020:2024

# ==============================================================================
# 2. GROSS POINT NORMALIZATION (2026 SYSTEM)
# ==============================================================================
cup_scored <- cup_data %>%
  # Catching potential column header variations
  rename_with(~"finish", any_of(c("fin", "finish_position", "pos", "position", "pos_fin"))) %>%
  rename_with(~"driver", any_of(c("driver_name", "racer", "driver"))) %>%
  rename_with(~"season", any_of(c("year", "season_year", "season"))) %>%
  # Clean columns for numeric math
  mutate(
    finish = as.numeric(gsub("[^0-9]", "", as.character(finish))),
    season = as.numeric(season)
  ) %>%
  filter(season %in% target_seasons & !is.na(finish)) %>%
  mutate(
    # Retroactively apply 2026 points scale: 1st = 55, 2nd = 35, 3rd = 34...
    base_points = case_when(
      finish == 1 ~ 55,
      finish >= 2 & finish <= 36 ~ 35 - (finish - 2),
      TRUE ~ 1
    )
  ) %>%
  group_by(season, driver) %>%
  summarise(
    races_started = n(),
    wins = sum(finish == 1, na.rm = TRUE),
    top_10s = sum(finish <= 10, na.rm = TRUE),
    total_gross_points = sum(base_points, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  # Exclude part-time drivers/ringers
  filter(races_started >= 10)

# ==============================================================================
# 3. TOP 30 SLICING & PLOTTING
# ==============================================================================
top_drivers <- cup_scored %>%
  group_by(season) %>%
  slice_max(total_gross_points, n = 30, with_ties = FALSE) %>%
  ungroup()

# Save raw dataset for future 1-100 scale normalization
write.csv(top_drivers, "NASCAR/Rankings/cup_driver_totals_2020_2024.csv", row.names = FALSE)

plot_nascar <- ggplot(top_drivers, aes(x = total_gross_points, fill = factor(season))) +
  geom_density(alpha = 0.4) +
  theme_minimal() +
  scale_fill_brewer(palette = "Set1") + 
  labs(
    title = "NASCAR Cup Series Fantasy Points Distribution (5-Year Window)",
    subtitle = "Top 30 Drivers Per Season (Normalized to 2026 Point System, Gross Points)",
    x = "Total Season Points", y = "Density", fill = "Season"
  )

ggsave("NASCAR/Rankings/driver_distribution_5yr.png", plot = plot_nascar, width = 10, height = 6, dpi = 300)

message("NASCAR 5-Year Engine complete! Outputs saved to NASCAR/Rankings/")

# ==============================================================================
# 4. MASTER CSV EXPORT (PLAYERS ONLY)
# ==============================================================================
master_players_file <- "Master_Data/master_players.csv"
max_nascar_season_player <- max(top_drivers$season, na.rm = TRUE)

recent_nascar_players <- top_drivers %>%
  filter(season == max_nascar_season_player) %>%
  transmute(
    Player = driver,
    Team = "NASCAR",
    League = "NASCAR",
    Role = "Driver",
    Season = season,
    Total_Points = total_gross_points
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "NASCAR" & master_players$Season == max_nascar_season_player)) {
    write.table(recent_nascar_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_nascar_players), "NASCAR drivers to Master CSV."))
  } else {
    message("NASCAR Driver data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_nascar_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added NASCAR drivers.")
}

message("NASCAR analysis and Master CSV export complete!")