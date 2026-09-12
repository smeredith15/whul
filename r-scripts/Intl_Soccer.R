library(dplyr)
library(readr)
library(ggplot2)
library(purrr)

# ==============================================================================
# 1. SETUP & DIRECTORIES
# ==============================================================================
if (!dir.exists("Soccer/Rankings")) dir.create("Soccer/Rankings", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

# 8-Year Window (Two Complete 4-Year Cycles)
target_years <- 2017:2024

# ==============================================================================
# 2. DATA INGESTION
# ==============================================================================
message("Downloading 8-Year International Soccer Match Ledgers (2017-2024)...")

url_men_results     <- "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
url_men_shootouts   <- "https://raw.githubusercontent.com/martj42/international_results/master/shootouts.csv"
url_women_results   <- "https://raw.githubusercontent.com/martj42/womens-international-results/master/results.csv"
url_women_shootouts <- "https://raw.githubusercontent.com/martj42/womens-international-results/master/shootouts.csv"

men_results   <- read_csv(url_men_results, show_col_types = FALSE) %>% mutate(gender = "Men")
men_shootouts <- read_csv(url_men_shootouts, show_col_types = FALSE) %>% mutate(gender = "Men")

women_results <- read_csv(url_women_results, show_col_types = FALSE) %>% mutate(gender = "Women")

women_shootouts <- tryCatch({
  read_csv(url_women_shootouts, show_col_types = FALSE) %>% mutate(gender = "Women")
}, error = function(e) {
  data.frame(date = as.Date(character()), home_team = character(), 
             away_team = character(), winner = character(), gender = character())
})

all_results <- bind_rows(men_results, women_results) %>%
  mutate(
    date_parsed = as.Date(date),
    season_year = as.numeric(format(date_parsed, "%Y"))
  ) %>%
  filter(season_year %in% target_years)

all_shootouts <- bind_rows(men_shootouts, women_shootouts) %>%
  mutate(date_parsed = as.Date(date))

# ==============================================================================
# 3. TOURNAMENT FILTERING & CONSOLE SUMMARY
# ==============================================================================
valid_tournaments <- all_results %>%
  filter(
    grepl("World Cup|Euro|Copa Am|Gold Cup|African Cup|Asian Cup|Oceania|CONCACAF|Nations League", tournament, ignore.case = TRUE),
    !grepl("Friendly", tournament, ignore.case = TRUE)
  )

tournament_summary <- valid_tournaments %>%
  group_by(gender, tournament) %>%
  summarise(match_count = n(), .groups = "drop") %>%
  arrange(gender, desc(match_count))

cat("\n===================================================\n")
cat(" MATCH COUNTS PER TOURNAMENT OF INTEREST (2017-2024)\n")
cat("===================================================\n")
print(as.data.frame(tournament_summary), row.names = FALSE)
cat("===================================================\n\n")

# ==============================================================================
# 4. SCORING, STAGE DETERMINATION & MULTIPLIERS
# ==============================================================================
message("Scoring Matches (Qualifiers 1.0x, Group Stage 1.5x, Knockouts 2.0x)...")

scored_matches <- valid_tournaments %>%
  left_join(
    all_shootouts %>% select(date_parsed, home_team, away_team, gender, winner), 
    by = c("date_parsed", "home_team", "away_team", "gender")
  ) %>%
  mutate(
    home_result = case_when(
      home_score > away_score ~ "Win",
      home_score < away_score ~ "Loss",
      TRUE ~ "Draw"
    ),
    away_result = case_when(
      away_score > home_score ~ "Win",
      away_score < home_score ~ "Loss",
      TRUE ~ "Draw"
    ),
    
    # Base Points (3 Win, 2 PK Win, 1 Draw/PK Loss, 0 Loss)
    home_base_pts = case_when(
      home_result == "Win" ~ 3,
      home_result == "Draw" & !is.na(winner) & winner == home_team ~ 2,
      home_result == "Draw" & !is.na(winner) & winner != home_team ~ 1,
      home_result == "Draw" & is.na(winner) ~ 1,
      TRUE ~ 0
    ),
    
    away_base_pts = case_when(
      away_result == "Win" ~ 3,
      away_result == "Draw" & !is.na(winner) & winner == away_team ~ 2,
      away_result == "Draw" & !is.na(winner) & winner != away_team ~ 1,
      away_result == "Draw" & is.na(winner) ~ 1,
      TRUE ~ 0
    )
  )

# Pivot to long format per team per match
home_teams <- scored_matches %>%
  select(season_year, gender, date_parsed, tournament, team = home_team, base_pts = home_base_pts)

away_teams <- scored_matches %>%
  select(season_year, gender, date_parsed, tournament, team = away_team, base_pts = away_base_pts)

team_matches_long <- bind_rows(home_teams, away_teams) %>%
  mutate(
    is_qualifier = grepl("qualification|qualifying", tournament, ignore.case = TRUE)
  ) %>%
  # Determine match sequence number for main tournament matches per team
  group_by(season_year, gender, tournament, team, is_qualifier) %>%
  arrange(date_parsed) %>%
  mutate(
    match_seq = row_number(),
    stage = case_when(
      is_qualifier ~ "Qualifier",
      match_seq <= 3 ~ "Group Stage",
      TRUE ~ "Elimination Round"
    ),
    multiplier = case_when(
      stage == "Qualifier" ~ 1.0,
      stage == "Group Stage" ~ 1.5,
      stage == "Elimination Round" ~ 2.0
    ),
    fantasy_pts = base_pts * multiplier
  ) %>%
  ungroup()

# Aggregate team season totals
team_season_totals <- team_matches_long %>%
  group_by(season_year, gender, team) %>%
  summarise(
    matches_played    = n(),
    qualifier_matches = sum(stage == "Qualifier"),
    group_matches     = sum(stage == "Group Stage"),
    knockout_matches  = sum(stage == "Elimination Round"),
    total_base_pts    = sum(base_pts, na.rm = TRUE),
    total_fantasy_pts = sum(fantasy_pts, na.rm = TRUE),
    .groups = "drop"
  ) %>%
  filter(matches_played >= 3)

# ==============================================================================
# 5. TOP 50 SLICING & PLOTTING (2x4 Grid)
# ==============================================================================
top_teams <- team_season_totals %>%
  group_by(season_year, gender) %>%
  slice_max(total_fantasy_pts, n = 50, with_ties = FALSE) %>%
  ungroup()

write.csv(top_teams, "Soccer/Rankings/soccer_team_totals_8yr.csv", row.names = FALSE)

# Density plots formatted across an 8-year facet grid
plot_soccer <- ggplot(top_teams, aes(x = total_fantasy_pts, fill = gender)) +
  geom_density(alpha = 0.6) +
  facet_wrap(~ season_year, ncol = 4, scales = "free_y") +
  scale_fill_manual(values = c("Men" = "#005C53", "Women" = "#9B2335")) +
  theme_minimal() +
  labs(
    title = "International Soccer Fantasy Points Distribution (8-Year Window: 2017–2024)",
    subtitle = "Qualifiers (1x: 3/2/1), Group Stage (1.5x: 4.5/3/1.5), Elimination Rounds (2x: 6/4/2)",
    x = "Total Country Fantasy Points", y = "Density", fill = "Team"
  ) +
  theme(legend.position = "bottom")

ggsave("Soccer/Rankings/country_distribution_8yr.png", plot = plot_soccer, width = 14, height = 8, dpi = 300)

message("8-Year Soccer Engine complete! Data and plots saved to Soccer/Rankings/")

# ==============================================================================
# 6. MASTER CSV EXPORT (TEAMS ONLY)
# ==============================================================================
master_teams_file <- "Master_Data/master_teams.csv"
max_soccer_season_team <- max(top_teams$season_year, na.rm = TRUE)

recent_soccer_teams <- top_teams %>%
  filter(season_year == max_soccer_season_team) %>%
  transmute(
    Team = team,
    League = ifelse(gender == "Men", "Men's Soccer", "Women's Soccer"),
    Season = season_year,
    Total_Points = total_fantasy_pts
  )

if (file.exists(master_teams_file)) {
  master_teams <- read.csv(master_teams_file, stringsAsFactors = FALSE)
  # Check if Men's/Women's Soccer data for this season is already present
  if (!any(master_teams$League %in% c("Men's Soccer", "Women's Soccer") & master_teams$Season == max_soccer_season_team)) {
    write.table(recent_soccer_teams, master_teams_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
    message(paste("Successfully appended", nrow(recent_soccer_teams), "Soccer national teams to Master CSV."))
  } else {
    message("Soccer Team data for the most recent season already exists in Master CSV. Skipping append.")
  }
} else {
  write.csv(recent_soccer_teams, master_teams_file, row.names = FALSE)
  message("Created Master_Data/master_teams.csv and added Soccer national teams.")
}

message("Soccer analysis and Master CSV export complete!")