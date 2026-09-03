library(dplyr)
library(purrr)
library(stringr)
library(readr)
library(ggplot2)

# ==============================================================================
# 1. SETUP & DIRECTORIES
# ==============================================================================
if (!dir.exists("Tennis/Rankings")) dir.create("Tennis/Rankings", recursive = TRUE)
if (!dir.exists("Master_Data")) dir.create("Master_Data", recursive = TRUE)

# ==============================================================================
# 2. ATP POINT DISTRIBUTION TABLE (Matches Python backend)
# ==============================================================================
atp_points_map <- list(
  "GS_R128" = 50, "GS_R64" = 50, "GS_R32" = 100, "GS_R16" = 200, "GS_QF" = 400, "GS_SF" = 500, "GS_F" = 700,
  "M1000_128_R128" = 30, "M1000_128_R64" = 20, "M1000_128_R32" = 50, "M1000_128_R16" = 100, "M1000_128_QF" = 200, "M1000_128_SF" = 250, "M1000_128_F" = 350,
  "M1000_64_R64" = 50, "M1000_64_R32" = 50, "M1000_64_R16" = 100, "M1000_64_QF" = 200, "M1000_64_SF" = 250, "M1000_64_F" = 350,
  "A500_32_R32" = 50, "A500_32_R16" = 50, "A500_32_QF" = 100, "A500_32_SF" = 130, "A500_32_F" = 170,
  "A500_64_R64" = 25, "A500_64_R32" = 25, "A500_64_R16" = 50, "A500_64_QF" = 100, "A500_64_SF" = 130, "A500_64_F" = 170,
  "A250_32_R32" = 25, "A250_32_R16" = 25, "A250_32_QF" = 50, "A250_32_SF" = 65, "A250_32_F" = 85,
  "A250_64_R64" = 13, "A250_64_R32" = 12, "A250_64_R16" = 25, "A250_64_QF" = 50, "A250_64_SF" = 65, "A250_64_F" = 85,
  "FINALS_RR" = 200, "FINALS_SF" = 400, "FINALS_F" = 500
)

tier_rounds <- list(
  "GS"        = c("R128", "R64", "R32", "R16", "QF", "SF", "F"),
  "M1000_128" = c("R128", "R64", "R32", "R16", "QF", "SF", "F"),
  "M1000_64"  = c("R64", "R32", "R16", "QF", "SF", "F"),
  "A500_32"   = c("R32", "R16", "QF", "SF", "F"),
  "A500_64"   = c("R64", "R32", "R16", "QF", "SF", "F"),
  "A250_32"   = c("R32", "R16", "QF", "SF", "F"),
  "A250_64"   = c("R64", "R32", "R16", "QF", "SF", "F")
)

# ==============================================================================
# 3. HELPER FUNCTIONS
# ==============================================================================
classify_tier <- function(tourney_name, match_count = 32) {
  name_lower <- tolower(tourney_name)
  
  if (grepl("australian open|roland garros|french open|wimbledon|us open|grand slam", name_lower)) {
    return("GS")
  }
  if (grepl("united cup|davis cup|billie jean king|bjk cup", name_lower)) {
    return("INTERNATIONAL")
  }
  if (grepl("atp finals|wta finals", name_lower)) {
    return("FINALS")
  }
  if (grepl("indian wells|miami|madrid|rome|shanghai|cincinnati|canada|toronto|montreal|paris|monte carlo|1000", name_lower)) {
    return(if (match_count >= 50) "M1000_128" else "M1000_64")
  }
  if (grepl("500|halle|vienna|beijing|tokyo|washington|queen|basel|rotterdam|dubai|acapulco|hamburg|barcelona", name_lower)) {
    return(if (match_count >= 30) "A500_64" else "A500_32")
  }
  return(if (match_count >= 30) "A250_64" else "A250_32")
}

normalize_round <- function(round_str) {
  r <- tolower(coalesce(round_str, ""))
  case_when(
    grepl("1/64|r128|round 1|128", r) ~ "R128",
    grepl("1/32|r64|round 2|64", r)   ~ "R64",
    grepl("1/16|r32|round 3|32", r)   ~ "R32",
    grepl("1/8|r16|round 4|16", r)    ~ "R16",
    grepl("quarter|qf", r)            ~ "QF",
    grepl("semi|sf", r)               ~ "SF",
    grepl("final", r)                 ~ "F",
    grepl("group|round robin|rr", r)  ~ "RR",
    TRUE                              ~ "R32"
  )
}

# ==============================================================================
# 4. MATCH LEDGER SCORING ENGINE
# ==============================================================================
score_season_ledger <- function(file_path) {
  message("Processing ledger: ", file_path)
  
  df_raw <- read_csv(file_path, show_col_types = FALSE) %>% janitor::clean_names()
  
  # Exclude Unfinished, Qualification, Challenger, ITF, and Exhibition matches
  df_filtered <- df_raw %>%
    filter(
      status == "FINISHED",
      coalesce(status_extra, "") != "CANCELED",
      coalesce(round, "") != "Qualifier",
      !grepl("qualification|qualifier|challenger|chall|itf|exhibition|exhib", tournament, ignore.case = TRUE),
      !grepl("challenger|chall|exhibition|exhib", tour_type_human, ignore.case = TRUE)
    )
  
  if (nrow(df_filtered) == 0) return(data.frame())
  
  tourney_match_counts <- df_filtered %>%
    group_by(tournament) %>%
    summarise(total_matches = n(), .groups = "drop")
  
  scored_matches <- df_filtered %>%
    left_join(tourney_match_counts, by = "tournament") %>%
    mutate(
      tier = map2_chr(tournament, total_matches, classify_tier),
      round_clean = map_chr(round, normalize_round),
      
      winner_name = ifelse(winner_code == 1, home_name, away_name),
      loser_name  = ifelse(winner_code == 1, away_name, home_name),
      
      winner_sets = ifelse(winner_code == 1, home_set_score, away_set_score),
      loser_sets  = ifelse(winner_code == 1, away_set_score, home_set_score),
      
      is_straight_sets = (loser_sets == 0 & winner_sets > 0)
    ) %>%
    group_by(season_year, tournament, winner_name) %>%
    arrange(date_timestamp) %>%
    mutate(
      match_num_for_player = row_number(),
      is_first_win = match_num_for_player == 1
    ) %>%
    ungroup() %>%
    mutate(
      prev_round = map2_chr(tier, round_clean, function(t, r) {
        seq <- tier_rounds[[t]]
        if (is.null(seq) || !(r %in% seq)) return("")
        idx <- match(r, seq)
        if (idx > 1) seq[idx - 1] else ""
      }),
      
      bye_bonus = map2_dbl(tier, prev_round, function(t, pr) {
        if (pr == "") return(0)
        key <- paste0(t, "_", pr)
        coalesce(atp_points_map[[key]], 0)
      }),
      
      lookup_key = paste0(tier, "_", round_clean),
      base_round_pts = map_dbl(lookup_key, ~coalesce(atp_points_map[[.x]], 0)),
      base_round_pts = ifelse(tier == "INTERNATIONAL", 50, base_round_pts),
      
      total_base_win_pts = base_round_pts + ifelse(is_first_win, bye_bonus, 0),
      
      fantasy_pts = ifelse(is_straight_sets, total_base_win_pts * 1.5, total_base_win_pts)
    )
  
  return(scored_matches)
}

# ==============================================================================
# 5. RUN PIPELINE FOR 2024 & 2025 FILES
# ==============================================================================
files_to_process <- c(
  "2024-atp-season.csv", "2025-atp-season.csv",
  "2024-wta-season.csv", "2025-wta-season.csv"
)

existing_files <- files_to_process[file.exists(files_to_process)]

if (length(existing_files) == 0) {
  message("Warning: No tennis season CSV files were found. Skipping match processing.")
  all_scored_matches <- data.frame()
} else {
  all_scored_matches <- map_df(existing_files, score_season_ledger)
}

# ==============================================================================
# 6. AGGREGATE PLAYER TOTALS
# ==============================================================================
if(nrow(all_scored_matches) > 0) {
  player_season_totals <- all_scored_matches %>%
    group_by(season_year, tour_type_human, player = winner_name) %>%
    summarise(
      matches_won = n(),
      straight_set_wins = sum(is_straight_sets),
      total_fantasy_pts = sum(fantasy_pts, na.rm = TRUE),
      .groups = "drop"
    )
  
  # ==============================================================================
  # 7. TOP 100 SLICING & PLOTTING
  # ==============================================================================
  
  # --- A. Top 100 ATP Players ---
  top100_atp <- player_season_totals %>%
    filter(tour_type_human == "ATP Tour") %>%
    group_by(season_year) %>%
    slice_max(total_fantasy_pts, n = 100, with_ties = FALSE) %>%
    ungroup()
  
  if(nrow(top100_atp) > 0) {
    plot_atp100 <- ggplot(top100_atp, aes(x = total_fantasy_pts, fill = factor(season_year))) +
      geom_density(alpha = 0.5) +
      theme_minimal() +
      scale_fill_manual(values = c("2024" = "#002B49", "2025" = "#008080")) +
      labs(
        title = "Top 100 ATP Players Fantasy Points Distribution",
        subtitle = "ATP Tour (2024 vs 2025)",
        x = "Total Season Points", y = "Density", fill = "Season"
      )
    ggsave("Tennis/Rankings/top100_atp_distribution.png", plot = plot_atp100, width = 9, height = 6, dpi = 300)
  }
  
  # --- B. Top 100 WTA Players ---
  top100_wta <- player_season_totals %>%
    filter(tour_type_human == "WTA Tour") %>%
    group_by(season_year) %>%
    slice_max(total_fantasy_pts, n = 100, with_ties = FALSE) %>%
    ungroup()
  
  if(nrow(top100_wta) > 0) {
    plot_wta100 <- ggplot(top100_wta, aes(x = total_fantasy_pts, fill = factor(season_year))) +
      geom_density(alpha = 0.5) +
      theme_minimal() +
      scale_fill_manual(values = c("2024" = "#7B2CBF", "2025" = "#E0aaff")) +
      labs(
        title = "Top 100 WTA Players Fantasy Points Distribution",
        subtitle = "WTA Tour (2024 vs 2025)",
        x = "Total Season Points", y = "Density", fill = "Season"
      )
    ggsave("Tennis/Rankings/top100_wta_distribution.png", plot = plot_wta100, width = 9, height = 6, dpi = 300)
  }
  
  # --- C. Top 100 Overall Players (ATP & WTA Combined) ---
  top100_overall <- player_season_totals %>%
    group_by(season_year) %>%
    slice_max(total_fantasy_pts, n = 100, with_ties = FALSE) %>%
    ungroup()
  
  if(nrow(top100_overall) > 0) {
    plot_overall100 <- ggplot(top100_overall, aes(x = total_fantasy_pts, fill = tour_type_human)) +
      geom_density(alpha = 0.5) +
      facet_wrap(~ season_year, ncol = 1) +
      scale_fill_manual(values = c("ATP Tour" = "#002B49", "WTA Tour" = "#7B2CBF")) +
      theme_minimal() +
      labs(
        title = "Top 100 Overall Tennis Players Fantasy Points Distribution",
        subtitle = "Combined ATP & WTA Top 100 Performers",
        x = "Total Season Points", y = "Density", fill = "Tour"
      )
    ggsave("Tennis/Rankings/top100_overall_distribution.png", plot = plot_overall100, width = 10, height = 7, dpi = 300)
  }
  
  message("All Top 100 plots created and saved to Tennis/Rankings/")
  
  # ==============================================================================
  # 8. MASTER CSV EXPORT (PLAYERS ONLY)
  # ==============================================================================
  master_players_file <- "Master_Data/master_players.csv"
  max_tennis_season <- max(player_season_totals$season_year, na.rm = TRUE)
  
  recent_tennis_players <- player_season_totals %>%
    filter(season_year == max_tennis_season) %>%
    transmute(
      Player = player,
      Team = ifelse(grepl("ATP", tour_type_human), "ATP", "WTA"),
      League = ifelse(grepl("ATP", tour_type_human), "ATP", "WTA"),
      Role = "Singles",
      Season = season_year,
      Total_Points = total_fantasy_pts
    )
  
  if (file.exists(master_players_file)) {
    master_players <- read.csv(master_players_file, stringsAsFactors = FALSE)
    # Check if ATP/WTA data for this season is already present
    if (!any(master_players$League %in% c("ATP", "WTA") & master_players$Season == max_tennis_season)) {
      write.table(recent_tennis_players, master_players_file, sep = ",", append = TRUE, row.names = FALSE, col.names = FALSE)
      message(paste("Successfully appended", nrow(recent_tennis_players), "Tennis players to Master CSV."))
    } else {
      message("Tennis Player data for the most recent season already exists in Master CSV. Skipping append.")
    }
  } else {
    write.csv(recent_tennis_players, master_players_file, row.names = FALSE)
    message("Created Master_Data/master_players.csv and added Tennis players.")
  }
  
  message("Tennis analysis and Master CSV export complete!")
}