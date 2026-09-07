library(dplyr)
library(readr)
library(ggplot2)
library(stringr)

# ==============================================================================
# 1. SETUP & DEFINITIONS (15-MANAGER LEAGUE WITH DRAFT BUFFERS)
# ==============================================================================
if (!dir.exists("Master_Data")) stop("Master_Data directory not found.")
if (!dir.exists("Master_Data/Visualizations")) dir.create("Master_Data/Visualizations", recursive = TRUE)

message("Loading Master Datasets...")
players_raw <- read_csv("Master_Data/master_players.csv", show_col_types = FALSE) %>% filter(!is.na(Total_Points))
teams_raw   <- read_csv("Master_Data/master_teams.csv", show_col_types = FALSE) %>% filter(!is.na(Total_Points))

# --- PLAYER POOL CONFIGURATION ---
# Target N = Exact Rostered Need
# Buffer N = Target N * 1.50 (50% expansion to capture fantasy-relevant reach/bench pool)
player_pool_map <- tibble(
  League = c("NFL", "NBA", "WNBA", "MLB", "NHL", "ATP", "WTA", "F1", "NASCAR", "PGA",
             "Premier League", "La Liga", "Serie A", "MLS", "NWSL", "Ligue 1", "Bundesliga"),
  Draft_Pool = c("NFL", "NBA", "WNBA", "MLB", "NHL", "Tennis", "Tennis", "Motorsports", "Motorsports", "PGA",
                 "Club Soccer Top 3", "Club Soccer Top 3", "Club Soccer Top 3", "Club Soccer Other", "Club Soccer Other", "Club Soccer Other", "Club Soccer Other")
)

player_cutoffs <- tibble(
  Draft_Pool = c("NFL", "NBA", "WNBA", "MLB", "NHL", "Tennis", "Motorsports", "PGA", "Club Soccer Top 3", "Club Soccer Other"),
  Target_N   = c(45, 45, 30, 45, 45, 45, 30, 45, 90, 90),
  Buffer_N   = round(c(45, 45, 30, 45, 45, 45, 30, 45, 90, 90) * 1.50) # +50% Buffer Pool
)

# --- TEAM POOL CONFIGURATION ---
# Target N = Exact Rostered Need
# Buffer N = Target N * 1.33 (33% expansion to capture fantasy-relevant reach/bench pool)
team_pool_map <- tibble(
  League = c("NFL", "NBA", "WNBA", "MLB", "NHL", "NCAAF", "NCAAM", "NCAAW", "NCAABaseball", "NCAASoftball", "Olympics",
             "Men's Soccer", "Women's Soccer", "Premier League", "La Liga", "Serie A", "MLS", "NWSL", "Ligue 1", "Bundesliga"),
  Draft_Pool = c("NFL", "NBA", "WNBA", "MLB", "NHL", "NCAAF", "NCAAM", "NCAAW", "NCAA Baseball", "NCAA Softball", "Olympics",
                 "Intl Soccer", "Intl Soccer", "Club Soccer Top 3", "Club Soccer Top 3", "Club Soccer Top 3", "Club Soccer Other", "Club Soccer Other", "Club Soccer Other", "Club Soccer Other")
)

team_cutoffs <- tibble(
  Draft_Pool = c("NFL", "NBA", "WNBA", "MLB", "NHL", "NCAAF", "NCAAM", "NCAAW", "NCAA Baseball", "NCAA Softball", "Olympics", "Intl Soccer", "Club Soccer Top 3", "Club Soccer Other"),
  Target_N   = c(30, 30, 15, 30, 30, 30, 30, 30, 15, 15, 30, 30, 60, 60),
  Buffer_N   = round(c(30, 30, 15, 30, 30, 30, 30, 30, 15, 15, 30, 30, 60, 60) * 1.33) # +33% Buffer Pool
)

# ==============================================================================
# 2. PLAYER NORMALIZATION (BENCHMARK SET ON FANTASY-RELEVANT POOL)
# ==============================================================================
message("Filtering Player Pool & Benchmarking 99th Percentile on Fantasy-Relevant Field...")

# Step 1: Assign Positional Groups
players_prepped <- players_raw %>%
  inner_join(player_pool_map, by = "League") %>%
  filter(!(League == "NHL" & Role == "Goalie")) %>%
  mutate(
    Norm_Group = case_when(
      League %in% c("NBA", "WNBA") & grepl("G|PG|SG|Guards", Role, ignore.case = TRUE) ~ "Backcourt",
      League %in% c("NBA", "WNBA") & grepl("F|SF|PF|C|Forwards|Centers", Role, ignore.case = TRUE) ~ "Frontcourt",
      League == "MLB" ~ Role,
      League == "NFL" ~ Role,
      TRUE ~ League
    ),
    Norm_Key = paste(League, Norm_Group, sep = "_")
  )

# Step 2: Slice to the Fantasy-Relevant Buffer Pool FIRST, THEN compute 99th percentile benchmark
draftable_players <- players_prepped %>%
  left_join(player_cutoffs, by = "Draft_Pool") %>%
  arrange(desc(Total_Points)) %>%
  group_by(Draft_Pool) %>%
  mutate(Pool_Rank = row_number()) %>%
  filter(Pool_Rank <= Buffer_N) %>%
  ungroup() %>%
  # Benchmark computed strictly across the fantasy-relevant pool
  group_by(Norm_Key) %>%
  mutate(
    pool_benchmark_99th = quantile(Total_Points, probs = 0.99, na.rm = TRUE),
    Scaled_Score = round((Total_Points / pool_benchmark_99th) * 100, 2)
  ) %>%
  ungroup() %>%
  # Annotate count displayed per Draft Pool for facet titles
  group_by(Draft_Pool) %>%
  mutate(
    Display_N = n(),
    Facet_Label = paste0(Draft_Pool, " (N = ", Display_N, ")")
  ) %>%
  ungroup() %>%
  mutate(
    Plot_Category = case_when(
      Draft_Pool %in% c("NFL", "MLB", "NBA", "WNBA") ~ Norm_Group,
      Draft_Pool == "NHL" ~ "Skater",
      TRUE ~ League
    )
  )

write.csv(draftable_players, "Master_Data/RENORMALIZED_Players_15_Managers.csv", row.names = FALSE)

# ==============================================================================
# 3. TEAM NORMALIZATION (BENCHMARK SET ON FANTASY-RELEVANT POOL)
# ==============================================================================
message("Filtering Team Pool & Benchmarking 99th Percentile on Fantasy-Relevant Field...")

teams_prepped <- teams_raw %>%
  inner_join(team_pool_map, by = "League")

# Step 1: Slice to the Fantasy-Relevant Buffer Pool FIRST, THEN compute 99th percentile benchmark
draftable_teams <- teams_prepped %>%
  left_join(team_cutoffs, by = "Draft_Pool") %>%
  arrange(desc(Total_Points)) %>%
  group_by(Draft_Pool) %>%
  mutate(Pool_Rank = row_number()) %>%
  filter(Pool_Rank <= Buffer_N) %>%
  ungroup() %>%
  # Benchmark computed strictly across the fantasy-relevant pool
  group_by(League) %>%
  mutate(
    pool_benchmark_99th = quantile(Total_Points, probs = 0.99, na.rm = TRUE),
    Scaled_Score = round((Total_Points / pool_benchmark_99th) * 100, 2)
  ) %>%
  ungroup() %>%
  # Annotate count displayed per Draft Pool for facet titles
  group_by(Draft_Pool) %>%
  mutate(
    Display_N = n(),
    Facet_Label = paste0(Draft_Pool, " (N = ", Display_N, ")")
  ) %>%
  ungroup() %>%
  mutate(Plot_Category = League)

write.csv(draftable_teams, "Master_Data/RENORMALIZED_Teams_15_Managers.csv", row.names = FALSE)

# ==============================================================================
# 4. VISUALIZE RE-NORMALIZED DISTRIBUTIONS
# ==============================================================================
message("Generating Distribution Plots...")

# A. Player Distribution Plot
plot_players_renorm <- ggplot(draftable_players, aes(x = Scaled_Score, fill = Plot_Category)) +
  geom_density(alpha = 0.7, color = "black", linewidth = 0.3) +
  geom_vline(xintercept = 100, linetype = "dashed", color = "black", linewidth = 0.8) +
  facet_wrap(~ Facet_Label, scales = "free_y", ncol = 3) +
  theme_minimal(base_size = 12) +
  scale_fill_viridis_d(option = "turbo") +
  labs(
    title = "Draftable Player Distributions (100 = Fantasy-Relevant 99th Percentile)",
    subtitle = "Scaled strictly relative to the draft-relevant pool (+50% reach buffer included). Facet labels display total assets plotted.",
    x = "Normalized Fantasy Score (100 = Pool Benchmark)", 
    y = "Density", fill = "Position / League"
  ) +
  theme(
    legend.position = "bottom",
    legend.title = element_text(face = "bold"),
    strip.text = element_text(face = "bold", size = 11),
    panel.grid.minor = element_blank()
  )

ggsave("Master_Data/Visualizations/renormalized_players_distribution.png", plot = plot_players_renorm, width = 14, height = 10, dpi = 300)

# B. Team Distribution Plot
plot_teams_renorm <- ggplot(draftable_teams, aes(x = Scaled_Score, fill = Plot_Category)) +
  geom_density(alpha = 0.7, color = "black", linewidth = 0.3) +
  geom_vline(xintercept = 100, linetype = "dashed", color = "black", linewidth = 0.8) +
  facet_wrap(~ Facet_Label, scales = "free_y", ncol = 3) +
  theme_minimal(base_size = 12) +
  scale_fill_viridis_d(option = "mako") +
  labs(
    title = "Draftable Team Distributions (100 = Fantasy-Relevant 99th Percentile)",
    subtitle = "Scaled strictly relative to the draft-relevant pool (+33% reach buffer included). Facet labels display total assets plotted.",
    x = "Normalized Fantasy Score (100 = Pool Benchmark)", 
    y = "Density", fill = "League"
  ) +
  theme(
    legend.position = "bottom",
    legend.title = element_text(face = "bold"),
    strip.text = element_text(face = "bold", size = 11),
    panel.grid.minor = element_blank()
  )

ggsave("Master_Data/Visualizations/renormalized_teams_distribution.png", plot = plot_teams_renorm, width = 14, height = 10, dpi = 300)

message("Success! Normalization engine complete using fantasy-relevant pool benchmarks.")
