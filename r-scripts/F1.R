library(httr)
library(jsonlite)
library(dplyr)
library(purrr)
library(ggplot2)

# ==============================================================================
# 1. SETUP & DIRECTORIES
# ==============================================================================
if (!dir.exists("F1/Rankings")) dir.create("F1/Rankings", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

# Full 5-Year Window (2020–2024)
seasons <- 2020:2024

# ==============================================================================
# 2. JOLPICA F1 API SCRAPER
# ==============================================================================
message("Downloading 5-Year Formula 1 Driver Standings via Jolpica API...")

f1_standings <- map_df(seasons, function(yr) {
  message("  Fetching ", yr, " season totals...")
  
  url <- sprintf("http://api.jolpi.ca/ergast/f1/%d/driverStandings.json", yr)
  
  tryCatch({
    resp <- GET(url)
    if (status_code(resp) != 200) {
      message("    [!] HTTP Error ", status_code(resp))
      return(data.frame())
    }
    
    data <- fromJSON(content(resp, "text", encoding = "UTF-8"))
    standings_list <- data$MRData$StandingsTable$StandingsLists
    
    if (length(standings_list) == 0 || nrow(standings_list) == 0) {
      message("    [!] No standings data found for ", yr)
      return(data.frame())
    }
    
    drivers <- standings_list$DriverStandings[[1]]
    
    df <- data.frame(
      season_year = yr,
      position = as.numeric(drivers$position),
      points = as.numeric(drivers$points),
      driver_name = paste(drivers$Driver$givenName, drivers$Driver$familyName),
      constructor = sapply(drivers$Constructors, function(x) if(is.data.frame(x)) x$name[1] else NA),
      stringsAsFactors = FALSE
    )
    
    return(df)
  }, error = function(e) {
    message("    [!] Failed to parse data for ", yr, ": ", e$message)
    return(data.frame())
  })
})

if (nrow(f1_standings) == 0) {
  stop("CRITICAL ERROR: Failed to retrieve any data from the Jolpica API.")
}

# ==============================================================================
# 3. TOP 20 SLICING & PLOTTING
# ==============================================================================
# Isolate Top 20 active grid drivers per season
f1_scored <- f1_standings %>%
  group_by(season_year) %>%
  slice_max(points, n = 20, with_ties = FALSE) %>%
  ungroup()

# Save raw dataset
write.csv(f1_scored, "F1/Rankings/f1_driver_totals_2020_2024.csv", row.names = FALSE)

plot_f1 <- ggplot(f1_scored, aes(x = points, fill = factor(season_year))) +
  geom_density(alpha = 0.4) +
  theme_minimal() +
  scale_fill_brewer(palette = "Set1") +
  labs(
    title = "Formula 1 Driver Fantasy Points Distribution (5-Year Window)",
    subtitle = "Top 20 Drivers Per Season (2020–2024, Official FIA Points)",
    x = "Total Season Points", y = "Density", fill = "Season"
  )

ggsave("F1/Rankings/driver_distribution_5yr.png", plot = plot_f1, width = 10, height = 6, dpi = 300)

message("F1 5-Year Engine complete! Outputs and plots saved to F1/Rankings/")

# ==============================================================================
# 4. MASTER CSV EXPORT (PLAYERS ONLY)
# ==============================================================================

master_players_file <- "Master_Data/master_players.csv"
max_f1_season_player <- max(f1_scored$season_year, na.rm = TRUE)

recent_f1_players <- f1_scored %>%
  filter(season_year == max_f1_season_player) %>%
  transmute(
    Player = driver_name,
    Team = constructor,
    League = "F1",
    Role = "Driver",
    Season = season_year,
    Total_Points = points
  )

if (file.exists(master_players_file)) {
  master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
  if (!any(master_players$League == "F1" & master_players$Season == max_f1_season_player)) {
    write.table(recent_f1_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_f1_players), "F1 drivers to Master CSV."))
  } else {
    message("F1 Driver data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_f1_players, master_players_file, row.names = FALSE)
  message("Created Master_Data/master_players.csv and added F1 drivers.")
}

message("F1 analysis and Master CSV export complete!")