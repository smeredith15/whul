library(httr)
library(jsonlite)
library(rvest)
library(dplyr)
library(purrr)
library(openxlsx)
library(stringr)
library(janitor)

message("Initializing Live 2026/2027 Roster & Team Scraper...")

# ==============================================================================
# 1. LEAGUE CONFIGURATIONS
# ==============================================================================
team_leagues <- list(
  c("football", "nfl", "NFL", TRUE),
  c("basketball", "nba", "NBA", TRUE),
  c("basketball", "wnba", "WNBA", TRUE),
  c("baseball", "mlb", "MLB", TRUE),
  c("hockey", "nhl", "NHL", TRUE),
  c("soccer", "eng.1", "Premier League", TRUE),
  c("soccer", "esp.1", "La Liga", TRUE),
  c("soccer", "ita.1", "Serie A", TRUE),
  c("soccer", "ger.1", "Bundesliga", TRUE),
  c("soccer", "fra.1", "Ligue 1", TRUE),
  c("soccer", "usa.1", "MLS", TRUE),
  c("soccer", "usa.nwsl", "NWSL", TRUE),
  c("football", "college-football", "NCAAF", FALSE),
  c("basketball", "mens-college-basketball", "NCAAM", FALSE),
  c("basketball", "womens-college-basketball", "NCAAW", FALSE),
  c("baseball", "college-baseball", "NCAA Baseball", FALSE),
  c("softball", "college-softball", "NCAA Softball", FALSE)
)

individual_leagues <- list(
  c("golf", "pga", "PGA", "Golfer"),
  c("racing", "nascar-premier", "Motorsports", "Driver")
)

extract_names_from_json <- function(json_text) {
  tryCatch({
    parsed <- fromJSON(json_text, simplifyVector = FALSE)
    flat <- unlist(parsed)
    keys <- names(flat)
    valid_keys <- grep("(athlete|competitor|player|items).*(fullName|displayName)$", keys, ignore.case = TRUE)
    names_found <- unique(as.character(flat[valid_keys]))
    names_found <- names_found[!grepl("Tour|Series|Cup|Championship|Open|NASCAR|PGA|ATP|WTA", names_found, ignore.case = TRUE)]
    return(names_found)
  }, error = function(e) { return(c()) })
}

# ==============================================================================
# 2. FETCH TEAMS VIA ESPN API (WITH NBA FIX & SOFTBALL FALLBACK)
# ==============================================================================
message("Scraping Live Team Directories...")
all_teams <- data.frame()

for (l in team_leagues) {
  sport <- l[1]; espn_league <- l[2]; display_name <- l[3]; needs_players <- as.logical(l[4])
  url <- sprintf("https://site.api.espn.com/apis/site/v2/sports/%s/%s/teams?limit=1000", sport, espn_league)
  
  tryCatch({
    resp <- GET(url)
    if (status_code(resp) == 200) {
      json <- fromJSON(content(resp, "text", encoding = "UTF-8"), flatten = TRUE)
      
      if (length(json$sports) > 0 && "leagues" %in% names(json$sports)) {
        leagues_df <- json$sports$leagues[[1]]
        
        if ("teams" %in% names(leagues_df)) {
          teams_raw <- bind_rows(leagues_df$teams)
          
          if ("team.id" %in% names(teams_raw) && "team.displayName" %in% names(teams_raw)) {
            temp_teams <- data.frame(
              Team_ID = teams_raw$team.id,
              Name = teams_raw$team.displayName,
              League = display_name,
              Needs_Players = needs_players,
              Sport_Path = sport,
              League_Path = espn_league,
              stringsAsFactors = FALSE
            )
            
            # Filter exhibition/international teams from NBA/NHL API responses
            temp_teams <- temp_teams %>%
              filter(!grepl("Lions|Maccabi|Adelaide|All-Stars|Team |Ratiopharm", Name, ignore.case = TRUE))
            
            all_teams <- bind_rows(all_teams, temp_teams)
          }
        }
      }
    }
  }, error = function(e) {})
  Sys.sleep(0.3)
}

# NCAA Softball Failsafe
if (!any(all_teams$League == "NCAA Softball")) {
  message("  [!] NCAA Softball API offline. Injecting Top 25 Failsafe...")
  sb_failsafe <- data.frame(
    Team_ID = NA,
    Name = c("Oklahoma", "Texas", "Florida", "UCLA", "Stanford", "Oklahoma State", "Florida State", 
             "Tennessee", "Duke", "Alabama", "LSU", "Texas A&M", "Georgia", "Arkansas", "Missouri", 
             "Virginia Tech", "Washington", "Arizona", "Clemson", "Mississippi State", "Oregon", 
             "South Carolina", "Baylor", "Kentucky", "Louisiana"),
    League = "NCAA Softball", Needs_Players = FALSE, Sport_Path = "softball", League_Path = "college-softball", 
    stringsAsFactors = FALSE
  )
  all_teams <- bind_rows(all_teams, sb_failsafe)
}

# ==============================================================================
# 3. FETCH PLAYERS (TEAM SPORTS)
# ==============================================================================
message("Scraping Active Player Rosters...")
all_players <- data.frame()
teams_needing_players <- all_teams %>% filter(Needs_Players == TRUE & !is.na(Team_ID))

for (i in 1:nrow(teams_needing_players)) {
  t <- teams_needing_players[i, ]
  url <- sprintf("https://site.api.espn.com/apis/site/v2/sports/%s/%s/teams/%s/roster", t$Sport_Path, t$League_Path, t$Team_ID)
  
  tryCatch({
    resp <- GET(url)
    if (status_code(resp) == 200) {
      extracted_names <- extract_names_from_json(content(resp, "text", encoding = "UTF-8"))
      
      if (length(extracted_names) > 0) {
        temp_players <- data.frame(
          Asset_Type = "Player",
          Name = extracted_names,
          Real_Life_Team = t$Name,
          League = t$League,
          Position = "Player", 
          stringsAsFactors = FALSE
        )
        all_players <- bind_rows(all_players, temp_players)
      }
    }
  }, error = function(e) {})
  
  if (i %% 50 == 0) message(sprintf("  Processed %d / %d teams...", i, nrow(teams_needing_players)))
  Sys.sleep(0.2)
}

all_players <- all_players %>% distinct(League, Name, .keep_all = TRUE)

# ==============================================================================
# 4. SCRAPE TENNIS (TENNIS ABSTRACT TOP 50 ATP & WTA)
# ==============================================================================
message("Scraping Top 50 ATP & WTA Players from Tennis Abstract...")

scrape_tennis_abstract <- function(url, tour_label, top_n = 50) {
  tryCatch({
    page <- read_html(url)
    tables <- page %>% html_nodes("table")
    if (length(tables) == 0) return(data.frame())
    
    rankings_table <- html_table(tables[[length(tables)]], fill = TRUE)
    
    if (nrow(rankings_table) > 0) {
      cleaned <- rankings_table %>%
        clean_names() %>%
        filter(!is.na(player) & player != "") %>%
        head(top_n) %>%
        transmute(
          Asset_Type = "Player",
          Name = player,
          Real_Life_Team = "N/A",
          League = "Tennis",
          Position = "Athlete",
          stringsAsFactors = FALSE
        )
      return(cleaned)
    } else {
      return(data.frame())
    }
  }, error = function(e) {
    message("  [!] Error scraping ", tour_label, ": ", e$message)
    return(data.frame())
  })
}

atp_top50 <- scrape_tennis_abstract("https://tennisabstract.com/reports/atpRankings.html", "ATP", 50)
wta_top50 <- scrape_tennis_abstract("https://tennisabstract.com/reports/wtaRankings.html", "WTA", 50)

tennis_players <- bind_rows(atp_top50, wta_top50) %>% distinct(Name, .keep_all = TRUE)
message("  [✓] Retrieved ", nrow(tennis_players), " Tennis players (ATP & WTA Top 50).")

# ==============================================================================
# 5. FETCH PGA, MOTORSPORTS, F1 & INTL SOCCER
# ==============================================================================
message("Scraping Golf (PGA), Motorsports (NASCAR, F1) & Intl Soccer...")
all_indiv_players <- data.frame()

for (l in individual_leagues) {
  sport <- l[1]; espn_league <- l[2]; display_name <- l[3]; pos_name <- l[4]
  athlete_names <- c()
  
  endpoints <- c(
    sprintf("https://site.api.espn.com/apis/site/v2/sports/%s/%s/standings", sport, espn_league),
    sprintf("https://site.api.espn.com/apis/site/v2/sports/%s/%s/athletes?limit=200", sport, espn_league)
  )
  
  for (url in endpoints) {
    if (length(athlete_names) > 0) break
    tryCatch({
      resp <- GET(url)
      if (status_code(resp) == 200) {
        athlete_names <- extract_names_from_json(content(resp, "text", encoding = "UTF-8"))
      }
    }, error = function(e) {})
  }
  
  # Failsafes for NASCAR and PGA
  if (length(athlete_names) < 10) {
    if (espn_league == "nascar-premier") {
      athlete_names <- c("Kyle Larson", "William Byron", "Denny Hamlin", "Ryan Blaney", "Christopher Bell", 
                         "Tyler Reddick", "Chase Elliott", "Joey Logano", "Chase Briscoe", "Ross Chastain", 
                         "Shane van Gisbergen", "Bubba Wallace", "Alex Bowman", "Austin Cindric", "Josh Berry", 
                         "Ty Gibbs", "Brad Keselowski", "Kyle Busch", "Martin Truex Jr.", "Chris Buescher")
    } else if (espn_league == "pga") {
      athlete_names <- c("Scottie Scheffler", "Xander Schauffele", "Rory McIlroy", "Collin Morikawa", 
                         "Wyndham Clark", "Ludvig Aberg", "Viktor Hovland", "Patrick Cantlay", 
                         "Bryson DeChambeau", "Jon Rahm", "Brooks Koepka", "Hideki Matsuyama", "Tommy Fleetwood")
    }
  }
  
  if (length(athlete_names) > 0) {
    res_df <- data.frame(
      Asset_Type = "Player", Name = athlete_names, Real_Life_Team = "N/A", 
      League = display_name, Position = pos_name, stringsAsFactors = FALSE
    )
    all_indiv_players <- bind_rows(all_indiv_players, res_df)
  }
}

# Fetch F1 via Jolpica API
tryCatch({
  f1_req <- GET("http://api.jolpi.ca/ergast/f1/current/drivers.json")
  if (status_code(f1_req) == 200) {
    f1_json <- fromJSON(content(f1_req, "text", encoding = "UTF-8"))
    drivers <- f1_json$MRData$DriverTable$Drivers
    if (nrow(drivers) > 0) {
      f1_df <- data.frame(
        Asset_Type = "Player", Name = paste(drivers$givenName, drivers$familyName), 
        Real_Life_Team = "F1", League = "Motorsports", Position = "Driver", stringsAsFactors = FALSE
      )
      all_indiv_players <- bind_rows(all_indiv_players, f1_df)
    }
  }
}, error = function(e) {})

# Top 30 International Soccer Teams
intl_mens <- data.frame(
  Asset_Type = "Team", 
  Name = c("Argentina", "France", "Spain", "England", "Brazil", "Belgium", "Netherlands", "Portugal", "Colombia", "Italy", "Uruguay", "Croatia", "Germany", "Morocco", "Switzerland", "USA", "Mexico", "Japan", "Senegal", "Iran", "Denmark", "Austria", "South Korea", "Australia", "Ukraine", "Turkey", "Ecuador", "Poland", "Sweden", "Wales"), 
  Real_Life_Team = "N/A", League = "Men's Intl Soccer", Position = "Team Defense"
)

intl_womens <- data.frame(
  Asset_Type = "Team", 
  Name = c("Spain", "France", "England", "Germany", "USA", "Sweden", "Japan", "Canada", "Brazil", "North Korea", "Netherlands", "Australia", "Iceland", "Italy", "Denmark", "Norway", "Austria", "Belgium", "China", "South Korea", "Portugal", "Colombia", "Ireland", "Switzerland", "Finland", "Russia", "New Zealand", "Czechia", "Argentina", "Mexico"), 
  Real_Life_Team = "N/A", League = "Women's Intl Soccer", Position = "Team Defense"
)

# Combine all players & teams
final_players <- bind_rows(all_players, tennis_players, all_indiv_players) %>% distinct(League, Name, .keep_all = TRUE)

final_teams <- bind_rows(
  all_teams %>% filter(is.na(Team_ID) | Team_ID != "") %>% transmute(Asset_Type = "Team", Name = Name, Real_Life_Team = "N/A", League = League, Position = "Team Defense"), 
  intl_mens, intl_womens
) %>% distinct(League, Name, .keep_all = TRUE)

# ==============================================================================
# 6. ASSEMBLE AND EXPORT
# ==============================================================================
message("Assembling Excel Draft Board...")

add_manager_columns <- function(df) {
  df %>% arrange(League, Name) %>% 
    mutate(`My_Projection` = "", `Max_Bid_$` = "", `Draft_Target?` = "", `Notes` = "")
}

final_players <- add_manager_columns(final_players)
final_teams <- add_manager_columns(final_teams)
big_board <- bind_rows(final_players, final_teams) %>% arrange(Asset_Type, League, Name)

wb <- createWorkbook()
addWorksheet(wb, "Overall Big Board")
addWorksheet(wb, "Players Only")
addWorksheet(wb, "Teams Only")

writeData(wb, "Overall Big Board", big_board)
writeData(wb, "Players Only", final_players)
writeData(wb, "Teams Only", final_teams)

header_style <- createStyle(textDecoration = "bold", fgFill = "#D9D9D9", border = "TopBottomLeftRight")
for (sheet in c("Overall Big Board", "Players Only", "Teams Only")) {
  addStyle(wb, sheet, style = header_style, rows = 1, cols = 1:ncol(big_board), gridExpand = TRUE)
  freezePane(wb, sheet, firstRow = TRUE)
  setColWidths(wb, sheet, cols = 1:ncol(big_board), widths = "auto")
}

output_file <- "Official_2026_Live_Draft_Board.xlsx"
saveWorkbook(wb, output_file, overwrite = TRUE)
message("Success! Live Draft Board saved to: ", output_file)