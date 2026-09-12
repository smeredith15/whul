library(dplyr)
library(purrr)
library(readxl)
library(openxlsx)
library(stringr)

# ==============================================================================
# 1. DRAFT CONFIGURATION (EDIT BEFORE RUNNING EACH ROUND)
# ==============================================================================
DRAFT_ROUND <- 1
BASE_DAILY_BUDGET <- 1000 

draft_folder <- paste0("draft/Round ", DRAFT_ROUND)
master_drafted_file <- "Master_Drafted_Assets.xlsx"
tiebreaker_file <- "Tiebreaker_Queue.csv"
budget_ledger_in <- paste0("Budget_Ledger_Round_", DRAFT_ROUND - 1, ".csv")
budget_ledger_out <- paste0("Budget_Ledger_Round_", DRAFT_ROUND, ".csv")
all_bids_log_out <- paste0("All_Bids_Log_Round_", DRAFT_ROUND, ".xlsx")

if (!dir.exists(draft_folder)) stop("Draft folder not found: ", draft_folder)

# ==============================================================================
# 2. ROSTER LIMIT MAPPINGS (STARTERS + 14 STRICT BENCH SPOTS)
# ==============================================================================
get_roster_category <- function(league) {
  top_3_soccer <- c("Premier League", "La Liga", "Serie A")
  other_soccer <- c("Bundesliga", "Ligue 1", "MLS", "NWSL")
  intl_soccer  <- c("Men's Intl Soccer", "Women's Intl Soccer", "Intl Soccer")
  tennis       <- c("ATP", "WTA", "Tennis")
  motorsports  <- c("F1", "NASCAR", "Motorsports")
  
  case_when(
    league %in% top_3_soccer ~ "Club Soccer Top 3",
    league %in% other_soccer ~ "Club Soccer Other",
    league %in% intl_soccer ~ "Intl Soccer",
    league %in% tennis ~ "Tennis",
    league %in% motorsports ~ "Motorsports",
    TRUE ~ league
  )
}

# Team Limits
team_limits <- c(
  "Club Soccer Top 3" = 4, "Club Soccer Other" = 4, "NFL" = 2, "NBA" = 2, 
  "MLB" = 2, "NHL" = 2, "WNBA" = 1, "NCAAF" = 2, "NCAAM" = 2, "NCAAW" = 2, 
  "NCAA Baseball" = 1, "NCAA Softball" = 1, "Intl Soccer" = 2, "Olympics" = 2
)

# Player Limits
player_limits <- c(
  "Club Soccer Top 3" = 5, "Club Soccer Other" = 5, "NFL" = 4, "NBA" = 4, 
  "MLB" = 4, "NHL" = 4, "WNBA" = 2, "PGA" = 3, "Tennis" = 3, "Motorsports" = 2
)

# ==============================================================================
# 3. LOAD STATE (PREVIOUS ASSETS, TIEBREAKERS, & BUDGETS)
# ==============================================================================
if (file.exists(master_drafted_file)) {
  drafted_df <- read.xlsx(master_drafted_file)
} else {
  drafted_df <- data.frame(Manager = character(), Asset_Type = character(), 
                           Name = character(), League = character(), 
                           Category = character(), Winning_Bid = numeric())
}

files <- list.files(draft_folder, pattern = "\\.xlsx$", full.names = TRUE)
managers <- str_extract(basename(files), "(?<=_)[A-Z0-9]+(?=\\.xlsx)")

if (file.exists(tiebreaker_file)) {
  tb_queue <- read.csv(tiebreaker_file)$Manager
} else {
  set.seed(Sys.time()) 
  tb_queue <- sample(managers)
}

budgets <- list()
for (m in managers) {
  if (DRAFT_ROUND == 1) {
    budgets[[m]] <- BASE_DAILY_BUDGET
  } else if (file.exists(budget_ledger_in)) {
    prev_ledger <- read.csv(budget_ledger_in)
    budgets[[m]] <- prev_ledger$Next_Day_Starting[prev_ledger$Manager == m]
  } else {
    budgets[[m]] <- BASE_DAILY_BUDGET
  }
}

initial_open_slots <- list()
for (m in managers) {
  initial_open_slots[[m]] <- list(Team = list(), Player = list())
  for (cat in names(team_limits)) {
    curr <- sum(drafted_df$Manager == m & drafted_df$Asset_Type == "Team" & drafted_df$Category == cat)
    initial_open_slots[[m]][["Team"]][[cat]] <- max(0, team_limits[[cat]] - curr)
  }
  for (cat in names(player_limits)) {
    curr <- sum(drafted_df$Manager == m & drafted_df$Asset_Type == "Player" & drafted_df$Category == cat)
    initial_open_slots[[m]][["Player"]][[cat]] <- max(0, player_limits[[cat]] - curr)
  }
}

# ==============================================================================
# 4. EXTRACT AND VALIDATE BIDS
# ==============================================================================
message("Extracting bids from ", length(files), " boards...")
all_bids <- data.frame()
submitted_totals <- list()

for (i in seq_along(files)) {
  m <- managers[i]
  df <- suppressMessages(read_excel(files[i], sheet = "Overall Big Board", col_names = FALSE))
  
  bids_subset <- df[5:nrow(df), c(1,2,4,5,6)] 
  colnames(bids_subset) <- c("Asset_Type", "Name", "League", "Position", "Bid")
  
  bids_subset <- bids_subset %>%
    mutate(Bid = as.numeric(Bid)) %>%
    filter(!is.na(Bid) & Bid > 0) %>%
    mutate(Manager = m, Category = get_roster_category(League), Submission_Row = row_number())
  
  submitted_totals[[m]] <- sum(bids_subset$Bid, na.rm = TRUE)
  all_bids <- bind_rows(all_bids, bids_subset)
}

all_bids <- all_bids %>% filter(!Name %in% drafted_df$Name)

check_initial_open <- function(m, atype, cat) {
  res <- initial_open_slots[[m]][[atype]][[cat]]
  if (is.null(res)) return(0) else return(res)
}

all_bids$Valid_Pre_Full <- mapply(check_initial_open, all_bids$Manager, all_bids$Asset_Type, all_bids$Category) > 0
all_bids$Self_Tie_Priority <- 0
all_bids$Bid_Status <- "Pending"

# ==============================================================================
# 5. CONDITIONAL INTERACTIVE SELF-TIE RESOLUTION (FIXED CAPACITY LOGIC)
# ==============================================================================
self_ties <- all_bids %>%
  group_by(Manager, Asset_Type, Category, Bid) %>%
  filter(n() > 1) %>%
  ungroup()

if (nrow(self_ties) > 0) {
  tie_groups <- self_ties %>% distinct(Manager, Asset_Type, Category, Bid)
  
  for (i in 1:nrow(tie_groups)) {
    tg <- tie_groups[i, ]
    assets <- self_ties %>% filter(Manager == tg$Manager, Asset_Type == tg$Asset_Type, 
                                   Category == tg$Category, Bid == tg$Bid)
    
    open_slots <- check_initial_open(tg$Manager, tg$Asset_Type, tg$Category)
    
    # Calculate how many higher bids this manager has in this specific category
    higher_bids_count <- all_bids %>%
      filter(Manager == tg$Manager, Asset_Type == tg$Asset_Type, Category == tg$Category, Bid > tg$Bid) %>%
      nrow()
    
    # Trigger prompt if the tied bids PLUS higher bids exceed the available slots
    if ((nrow(assets) + higher_bids_count) > open_slots) {
      cat(sprintf("\n============================================================\n"))
      cat(sprintf("SELF-TIE REQUIRING SETTLEMENT\n"))
      cat(sprintf("Manager: %s | Asset Type: %s | Category: %s | Bid: $%s\n", 
                  tg$Manager, tg$Asset_Type, tg$Category, tg$Bid))
      cat(sprintf("Available Roster Slots: %d | Higher Bids Pending: %d | Tied Bids: %d\n", 
                  open_slots, higher_bids_count, nrow(assets)))
      cat("------------------------------------------------------------\n")
      cat("TIED ASSETS TO RANK:\n")
      
      for (a in 1:nrow(assets)) {
        cat(sprintf("  [%d] %s (%s - %s)\n", a, assets$Name[a], assets$League[a], assets$Position[a]))
      }
      cat("------------------------------------------------------------\n")
      cat("Please assign priority rankings to these assets (1 = Highest Preference):\n")
      
      assigned_ranks <- numeric(nrow(assets))
      for (a in 1:nrow(assets)) {
        valid_input <- FALSE
        while (!valid_input) {
          prompt_msg <- sprintf("  Rank for [%d] %s: ", a, assets$Name[a])
          resp <- readline(prompt = prompt_msg)
          resp_num <- suppressWarnings(as.integer(resp))
          
          if (!is.na(resp_num) && resp_num >= 1 && resp_num <= nrow(assets)) {
            if (resp_num %in% assigned_ranks) {
              cat("  [!] Rank already assigned to another asset. Please enter a unique rank.\n")
            } else {
              assigned_ranks[a] <- resp_num
              valid_input <- TRUE
            }
          } else {
            cat(sprintf("  [!] Invalid input. Please enter an integer from 1 to %d.\n", nrow(assets)))
          }
        }
      }
      
      for (a in 1:nrow(assets)) {
        all_bids$Self_Tie_Priority[all_bids$Manager == tg$Manager & all_bids$Asset_Type == tg$Asset_Type & 
                                     all_bids$Category == tg$Category & all_bids$Bid == tg$Bid & 
                                     all_bids$Name == assets$Name[a]] <- assigned_ranks[a]
      }
    }
  }
}

all_bids <- all_bids %>%
  mutate(TB_Rank = match(Manager, tb_queue)) %>%
  arrange(desc(Bid), TB_Rank, Self_Tie_Priority, Submission_Row)

# ==============================================================================
# 6. RESOLVE AUCTION & ENFORCE ROSTER CAPS
# ==============================================================================
message("\nResolving bids globally...")
spent_totals <- setNames(rep(0, length(managers)), managers)
won_assets <- data.frame()
awarded_names <- c()

current_roster_counts <- list()
for (m in managers) {
  current_roster_counts[[m]] <- list(Team = setNames(rep(0, length(team_limits)), names(team_limits)),
                                     Player = setNames(rep(0, length(player_limits)), names(player_limits)))
  m_drafted <- drafted_df %>% filter(Manager == m)
  for (r in seq_len(nrow(m_drafted))) {
    cat <- m_drafted$Category[r]; type <- m_drafted$Asset_Type[r]
    if (cat %in% names(current_roster_counts[[m]][[type]])) {
      current_roster_counts[[m]][[type]][cat] <- current_roster_counts[[m]][[type]][cat] + 1
    }
  }
}

for (i in 1:nrow(all_bids)) {
  bid <- all_bids[i, ]
  
  unique_asset_id <- paste(bid$Name, bid$League, sep = "_")
  if (unique_asset_id %in% awarded_names) {
    all_bids$Bid_Status[i] <- "Outbid"
    next 
  }
  
  m <- bid$Manager; cat <- bid$Category; type <- bid$Asset_Type; price <- bid$Bid
  
  if (budgets[[m]] - spent_totals[[m]] < price) {
    all_bids$Bid_Status[i] <- "Rejected (Insufficient Budget)"
    next 
  }
  
  limit <- if (type == "Team") team_limits[cat] else player_limits[cat]
  current <- current_roster_counts[[m]][[type]][cat]
  
  if (!is.na(limit) && current < limit) {
    awarded_names <- c(awarded_names, unique_asset_id)
    current_roster_counts[[m]][[type]][cat] <- current + 1
    spent_totals[[m]] <- spent_totals[[m]] + price
    all_bids$Bid_Status[i] <- "Won"
    
    won_assets <- bind_rows(won_assets, data.frame(
      Manager = m, Asset_Type = type, Name = bid$Name, League = bid$League, 
      Category = cat, Winning_Bid = price
    ))
    
    tied_bids <- all_bids %>% filter(Name == bid$Name, Bid == price, Manager != m)
    if (nrow(tied_bids) > 0) {
      tb_queue <- c(tb_queue[tb_queue != m], m) 
    }
  } else {
    if (bid$Valid_Pre_Full == FALSE) {
      all_bids$Bid_Status[i] <- "Rejected (Roster Full Pre-Round)"
    } else {
      all_bids$Bid_Status[i] <- "Rejected (Roster Filled During Round)"
    }
  }
}

# ==============================================================================
# 7. CALCULATE STRICT BUDGET LEDGER & EXPORT LOGS
# ==============================================================================
valid_submitted <- all_bids %>%
  filter(Valid_Pre_Full == TRUE) %>%
  group_by(Manager) %>%
  summarise(Valid_Total = sum(Bid, na.rm = TRUE), .groups = "drop")

ledger <- data.frame(Manager = managers) %>%
  left_join(valid_submitted, by = "Manager") %>%
  mutate(
    Valid_Total = coalesce(Valid_Total, 0),
    Starting_Budget = sapply(Manager, function(x) budgets[[x]]),
    Submitted_Bids_Total = sapply(Manager, function(x) coalesce(submitted_totals[[x]], 0)),
    Spent_Cash = sapply(Manager, function(x) spent_totals[[x]]),
    Forfeited_Cash = pmax(0, Starting_Budget - Submitted_Bids_Total) + (Submitted_Bids_Total - Valid_Total),
    Refunded_Cash = Starting_Budget - Spent_Cash - Forfeited_Cash,
    Next_Day_Starting = BASE_DAILY_BUDGET + Refunded_Cash
  ) %>% select(-Valid_Total)

write.csv(ledger, budget_ledger_out, row.names = FALSE)
write.csv(data.frame(Priority_Rank = 1:length(tb_queue), Manager = tb_queue), tiebreaker_file, row.names = FALSE)

final_drafted <- bind_rows(drafted_df, won_assets) %>% arrange(Manager, Asset_Type, Category)
write.xlsx(final_drafted, master_drafted_file, overwrite = TRUE)

# Export Master Bid Log
bids_log <- all_bids %>%
  select(Name, League, Position, Asset_Type, Manager, Bid, Bid_Status) %>%
  arrange(Asset_Type, League, Name, desc(Bid))

write.xlsx(bids_log, all_bids_log_out, overwrite = TRUE)

message("\nRound ", DRAFT_ROUND, " Complete!")
message("Assets Awarded: ", nrow(won_assets))
message("Budget Ledger saved to: ", budget_ledger_out)
message("Updated Master Draft List saved to: ", master_drafted_file)
message("League Bid Transparency Log saved to: ", all_bids_log_out)