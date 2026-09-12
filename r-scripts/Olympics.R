library(dplyr)
library(readr)
library(purrr)
library(stringr)
library(ggplot2)
library(janitor)

# ==============================================================================
# 1. SETUP & DIRECTORIES
# ==============================================================================
if (!dir.exists("Olympics/Rankings")) dir.create("Olympics/Rankings", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

summer_years <- c(2016, 2020, 2024)
winter_years <- c(2018, 2022, 2026)

clean_str <- function(x) {
  if (is.null(x)) return(rep(NA_character_, 1))
  s <- str_trim(as.character(x))
  ifelse(is.na(s) | s == "" | s == "NA" | s == "None", NA_character_, s)
}

# Country Normalizer (Pulls Russian Independent / Neutral Athletes into Russia)
normalize_country <- function(...) {
  args <- list(...)
  combined <- paste(sapply(args, clean_str), collapse = " ")
  
  if (grepl("oar|roc|ain|russia|neutral|independent|athletes from russia|nor", combined, ignore.case = TRUE) &&
      !grepl("norway", combined, ignore.case = TRUE)) { # Guard against 'NOR' matching neutral
    if (grepl("oar|roc|ain|russia|neutral|independent", combined, ignore.case = TRUE)) {
      return("Russia")
    }
  }
  
  # Pick first valid country string
  for (arg in args) {
    val <- clean_str(arg)
    if (!is.na(val)) return(val)
  }
  return("Unknown")
}

# ==============================================================================
# 2. PARSER A: HISTORICAL ATHLETE/EVENT-LEVEL CSV (2016 - 2024)
# ==============================================================================
parse_historical_olympics <- function(file_path) {
  if (!file.exists(file_path)) return(data.frame())
  
  message("  Parsing historical event-level file: ", file_path)
  df <- read_csv(file_path, show_col_types = FALSE) %>% clean_names()
  
  find_col <- function(candidates) {
    m <- intersect(candidates, names(df))
    if (length(m) > 0) df[[m[1]]] else rep(NA_character_, nrow(df))
  }
  
  raw_df <- data.frame(
    season_year = as.numeric(gsub("[^0-9]", "", as.character(find_col(c("season_year", "year", "game_year", "season"))))),
    country_raw = find_col(c("country", "country_name", "nation")),
    team_raw    = find_col(c("team", "noc_name", "team_name")),
    noc_raw     = find_col(c("noc", "code", "noc_code", "country_code")),
    event_raw   = find_col(c("event", "discipline", "event_name")),
    medal_raw   = find_col(c("medal", "medal_type")),
    stringsAsFactors = FALSE
  )
  
  raw_df %>%
    filter(season_year %in% c(2016, 2018, 2020, 2022, 2024)) %>%
    filter(!is.na(medal_raw) & !grepl("no medal|none", medal_raw, ignore.case = TRUE)) %>%
    mutate(
      country_clean = pmap_chr(list(country_raw, team_raw, noc_raw), normalize_country),
      season_type_clean = ifelse(season_year %in% winter_years, "Winter", "Summer")
    ) %>%
    # Deduplicate team/relay events so 1 team win = 1 medal per country
    distinct(season_year, season_type_clean, event_raw, medal_raw, country_clean, .keep_all = TRUE) %>%
    group_by(season_year, season_type_clean, country = country_clean) %>%
    summarise(
      golds   = sum(grepl("gold", medal_raw, ignore.case = TRUE)),
      silvers = sum(grepl("silver", medal_raw, ignore.case = TRUE)),
      bronzes = sum(grepl("bronze", medal_raw, ignore.case = TRUE)),
      total_medals = n(),
      total_fantasy_pts = (golds * 5) + (silvers * 3) + (bronzes * 1),
      .groups = "drop"
    )
}

# ==============================================================================
# 3. PARSER B: PRE-AGGREGATED COUNTRY-LEVEL CSV (2026)
# ==============================================================================
parse_2026_olympics <- function(file_path) {
  if (!file.exists(file_path)) return(data.frame())
  
  message("  Parsing 2026 country-level file: ", file_path)
  df <- read_csv(file_path, show_col_types = FALSE) %>% clean_names()
  
  df %>%
    mutate(
      season_year = 2026,
      season_type_clean = "Winter",
      country_clean = map2_chr(country, country_code, ~normalize_country(.x, .y)),
      
      golds   = as.numeric(coalesce(gold, 0)),
      silvers = as.numeric(coalesce(silver, 0)),
      bronzes = as.numeric(coalesce(bronze, 0)),
      
      total_medals = golds + silvers + bronzes,
      total_fantasy_pts = (golds * 5) + (silvers * 3) + (bronzes * 1)
    ) %>%
    # Group in case Russia/Independent athlete rows merged during normalization
    group_by(season_year, season_type_clean, country = country_clean) %>%
    summarise(
      golds = sum(golds, na.rm = TRUE),
      silvers = sum(silvers, na.rm = TRUE),
      bronzes = sum(bronzes, na.rm = TRUE),
      total_medals = sum(total_medals, na.rm = TRUE),
      total_fantasy_pts = sum(total_fantasy_pts, na.rm = TRUE),
      .groups = "drop"
    )
}

# ==============================================================================
# 4. RUN COMBINED PIPELINE
# ==============================================================================
message("Ingesting Olympic datasets...")

df_historical <- parse_historical_olympics("olympic_medals.csv")
df_2026       <- parse_2026_olympics("medals_2026.csv")

scored_countries <- bind_rows(df_historical, df_2026)

if (nrow(scored_countries) == 0) {
  stop("CRITICAL ERROR: Failed to parse data from both 'olympic_medals.csv' and 'medals_2026.csv'.")
}

# ==============================================================================
# 5. TOP 30 SLICING & ALIGNED PLOTTING
# ==============================================================================
top_countries <- scored_countries %>%
  group_by(season_year, season_type_clean) %>%
  slice_max(total_fantasy_pts, n = 30, with_ties = FALSE) %>%
  ungroup() %>%
  # Assign sequential Cycle label to align Summer and Winter Games on the same grid
  mutate(
    cycle_label = case_when(
      season_year %in% c(2016, 2018) ~ "Cycle 1 (2016 Summer / 2018 Winter)",
      season_year %in% c(2020, 2022) ~ "Cycle 2 (2020 Summer / 2022 Winter)",
      season_year %in% c(2024, 2026) ~ "Cycle 3 (2024 Summer / 2026 Winter)"
    )
  )

write.csv(top_countries, "Olympics/Rankings/country_olympic_totals_3yr.csv", row.names = FALSE)

# Generate 2x3 Grid Plot (Summer vs. Winter aligned)
plot_olympics <- ggplot(top_countries, aes(x = total_fantasy_pts, fill = season_type_clean)) +
  geom_density(alpha = 0.5) +
  facet_grid(season_type_clean ~ cycle_label, scales = "free_y") +
  scale_fill_manual(values = c("Summer" = "#EE334E", "Winter" = "#0081C8")) +
  theme_minimal() +
  labs(
    title = "Country Olympic Fantasy Points Distribution (Aligned 3-Games Window)",
    subtitle = "5-3-1 System (Top 30 Countries Per Games, Independent/Neutral Athletes -> Russia)",
    x = "Total Country Fantasy Points", y = "Density", fill = "Season"
  ) +
  theme(legend.position = "bottom")

ggsave("Olympics/Rankings/country_distribution_3yr.png", plot = plot_olympics, width = 12, height = 7, dpi = 300)

message("Olympic Country Engine complete! Outputs saved to Olympics/Rankings/")

# ==============================================================================
# 6. MASTER CSV EXPORT (TEAMS ONLY)
# ==============================================================================
master_teams_file <- "Master_Data/master_teams.csv"

recent_olympics <- top_countries %>%
  filter(season_year %in% c(2024, 2026)) %>%
  transmute(
    Team = country,
    League = "Olympics",
    Season = season_year,
    Total_Points = total_fantasy_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  # Ensure we don't append if the most recent cycle is already logged
  if (!any(master_teams$League == "Olympics" & master_teams$Season %in% c(2024, 2026))) {
    write.table(recent_olympics, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_olympics), "Olympic countries (2024 Summer & 2026 Winter) to Master CSV."))
  } else {
    message("Olympics Team data for the most recent cycle already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_olympics, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added Olympic countries.")
}

message("Olympics analysis and Master CSV export complete!")