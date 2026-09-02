# Cross-Sport Fantasy League — Web App Plan

**Status:** Draft v0.1 — written after reading the 21 R scripts in `r-scripts/`.
Sections marked **[Q#]** are open questions blocking design decisions. Nothing here is
committed to until those are answered.

---

## 1. What the R scripts currently do

### 1.1 Per-league valuation scripts (18 of them)

Each script pulls ~2020–2025 historical data, applies a bespoke scoring formula, and appends
the most recent season's results to one of two master files:

- `Master_Data/master_players.csv` — columns `Player, Team, League, Role, Season, Total_Points`
- `Master_Data/master_teams.csv` — columns `Team, League, Season, Total_Points`

| Script | Assets | Data source | Notes |
|---|---|---|---|
| `NFL_Players.R` | Players | `nflreadr::load_player_stats` | Half-PPR; QB/RB/WR/TE only |
| `NFL_Teams.R` | Teams | `nflreadr::load_schedules` + `load_teams` | Wins/big wins/shutouts/div/playoffs/pt diff |
| `NBA_Players_Teams.R` | Both | `hoopR` | Box-score fantasy + 0.1×plus-minus; teams incl. NBA Cup |
| `WNBA_Players_Teams.R` | Both | `wehoop` | Same shape as NBA; Commissioner's Cup instead of NBA Cup |
| `MLB_Players_Teams.R` | Both | `baseballr` (MLB Stats API + FanGraphs) | **Rolling 12-month "contract" engine** — see §1.3 |
| `NHL_Teams_Players.R` | Both | `fastRhockey` (NHL API) | Skaters + goalies scored separately |
| `NCAAF_Players_Teams.R` | Both | `cfbfastR` | |
| `NCAAM_Players_Teams.R` | Both | `hoopR` (MBB) | |
| `NCAAW_Players_Teams.R` | Both | `wehoop` (WBB) | |
| `NCAA_Baseball_Teams_Players.R` | Both | `baseballr` NCAA fns | |
| `NCAA_Softball_Teams.R` | Teams | `softballR` | API is flaky; script has a Top-25 failsafe |
| `Club_Soccer.R` | Both | **Local CSVs** (`football_matches.csv`, `mls_results.csv`, `shooting.csv`, `fifa_fbref_merged.csv`, `nwsl_stats.csv`) | Win pts scale by competition (UCL 5 / Europa-Cup 4 / league 3), +1 for 2-goal margin, +1 clean sheet. Players: minutes/goals-by-position/assists/cards |
| `Intl_Soccer.R` | Teams | GitHub CSVs (`martj42/international_results`, `womens-international-results`) | |
| `Olympics.R` | Teams (countries) | **Local CSVs** (`olympic_medals.csv`, `medals_2026.csv`) | Gold 5 / Silver 3 / Bronze 1 |
| `PGA.R` | Players | `golfastr::load_leaderboard` | Top-30 finish points table, 1.5× majors + Players |
| `Tennis_Players.R` | Players | **Local CSVs** (`2024-atp-season.csv`, `2025-atp-season.csv`, `2024-wta-season.csv`, `2025-wta-season.csv`) | ATP-points-by-round map, 1.5× straight-sets bonus, bye bonus |
| `F1.R` | Players (drivers) | Jolpica/Ergast API | Uses official championship points |
| `NASCAR.R` | Players (drivers) | **Local CSV** (`NASCAR 2017-2024 Full Race Points Data - Cup.csv`) | Retro-applies 2026 points scale (1st=55, 2nd=35 descending) |

**Five leagues currently depend on hand-assembled local CSVs** (Club Soccer, Olympics, Tennis,
NASCAR, and the FBref-derived soccer player data). These are the hard part of "daily scrapes" —
everything else has a queryable API or package behind it. See **[Q9]**.

### 1.2 `All_Analysis.R` — the normalization engine

This is the piece that produces the 0–100 scale. The logic:

1. Load `master_players.csv` / `master_teams.csv`.
2. Map each `League` to a **Draft Pool** (e.g. Premier League / La Liga / Serie A → "Club Soccer Top 3";
   ATP + WTA → "Tennis"; F1 + NASCAR → "Motorsports").
3. Rank all assets within a Draft Pool by `Total_Points` and **truncate to a buffer pool**:
   `Buffer_N = Target_N × 1.50` for players, `× 1.33` for teams.
4. Assign a **normalization group** (`Norm_Key`):
   - Players: NBA/WNBA → `Backcourt` / `Frontcourt`; MLB → `Role` (Batter/Pitcher); NFL → `Role`
     (QB/RB/WR/TE); everything else → `League`. NHL goalies are **excluded entirely**.
   - Teams: `League`.
5. Within each `Norm_Key`, compute `pool_benchmark_99th = quantile(Total_Points, 0.99)`.
6. `Scaled_Score = round(Total_Points / pool_benchmark_99th × 100, 2)`.

So 100 = the 99th percentile of the *fantasy-relevant* pool, not of all pros. Scores can exceed 100.

> `All_Analysis.R` is written for a **15-manager league** (`RENORMALIZED_*_15_Managers.csv`), but you
> said the league has **5 teams**. See **[Q1]**.

### 1.3 The MLB "contract" engine (the only cross-season one)

`MLB_Players_Teams.R` doesn't score a calendar season — it scores a **rolling 12 months anchored on
the July All-Star break**, which strongly implies the league year runs July→July:

```
share_post_asb = 0.42   # ~68 games remaining in draft year N
share_pre_asb  = 0.58   # ~94 games in year N+1 before the break
mult_year_n    = 0.75   # 25% discount on the known/certain remainder of year N
mult_year_n1   = (1 - share_post_asb * mult_year_n) / share_pre_asb   # ≈ 1.181
contract_pts   = pts_N × 0.42 × 0.75  +  pts_N+1 × 0.58 × 1.181
```

Plus an "Ohtani rule": a two-way player scores `primary + 0.5 × secondary`.

`Club_Soccer.R` also rolls its season at month > 7 (except MLS/NWSL). No other script does this — they
all score discrete calendar seasons. See **[Q3]**.

### 1.4 `Board.R` — draft board generator

Scrapes live rosters/team directories from the ESPN site API, Tennis Abstract (ATP/WTA top 50),
Jolpica (F1 drivers), plus hardcoded lists for international soccer teams, and emits an Excel big
board with `My_Projection / Max_Bid_$ / Draft_Target? / Notes` columns for each manager to fill in.

### 1.5 `Auction Resolution.R` — the draft engine

A **blind daily auction**, run one round per day:

- Each manager starts with `BASE_DAILY_BUDGET = 1000` per round.
- Managers submit bids on their copy of the big board; the script reads all workbooks from
  `draft/Round N/`.
- **Resolution order:** highest bid → tiebreaker queue rank → self-tie priority → submission row.
- **Roster caps** are enforced per (Asset_Type × Category), rejecting bids into full categories.
  Rejections are distinguished: `Roster Full Pre-Round` vs. `Roster Filled During Round`.
- **Tiebreaker queue** is a rotating list: winning a tied bid sends you to the back.
- **Interactive self-tie resolution:** if a manager's own tied bids exceed remaining slots, the
  script prompts at the console for a priority ranking.
- **Budget ledger:**
  - `Forfeited = max(0, Start − Submitted) + (Submitted − Valid_Submitted)`
    (i.e. you forfeit any budget you didn't bid, *and* any budget bid into a full category)
  - `Refunded = Start − Spent − Forfeited`
  - `Next_Day_Starting = 1000 + Refunded`

  Net effect: unspent-but-bid money rolls over; unbid or invalidly-bid money is burned.

### 1.6 Roster caps as coded

| Category | Team slots | Player slots |
|---|---|---|
| Club Soccer Top 3 (EPL/La Liga/Serie A) | 4 | 5 |
| Club Soccer Other (Bundesliga/Ligue 1/MLS/NWSL) | 4 | 5 |
| NFL | 2 | 4 |
| NBA | 2 | 4 |
| MLB | 2 | 4 |
| NHL | 2 | 4 |
| WNBA | 1 | 2 |
| NCAAF | 2 | — |
| NCAAM | 2 | — |
| NCAAW | 2 | — |
| NCAA Baseball | 1 | — |
| NCAA Softball | 1 | — |
| Intl Soccer | 2 | — |
| Olympics | 2 | — |
| PGA | — | 3 |
| Tennis (ATP+WTA) | — | 3 |
| Motorsports (F1+NASCAR) | — | 2 |
| **Total** | **29** | **36** |

**29 + 36 = 65 slots, but you said 62.** See **[Q2]**.

---

## 2. The central gap: historical valuation vs. live scoring

Every one of these scripts scores a **completed season, retrospectively, to price assets for a
draft**. The web app needs something different: a **score-to-date that grows daily** through the
season, for 5 × 62 = 310 roster slots.

That transformation is the single biggest design decision in the project, and most of the questions
below flow from it. See **[Q3]–[Q6]**.

---

## 3. Open questions

### League structure
- **[Q1]** `All_Analysis.R` is parameterized for 15 managers; you said 5 teams. Which is live for
  2026–27? Do the pool cutoffs (`Target_N`) need rescaling for 5, or were they always sized for a
  larger pool on purpose?
- **[Q2]** Roster caps in `Auction Resolution.R` sum to 65, not 62. Which three slots are gone, or is
  62 the count of *starters* with something else on top? The comment in that script says
  "starters + 14 strict bench spots" — what's the actual starter/bench split, and **do bench slots
  score**?
- **[Q3]** Is the league year July→July (as the MLB contract engine and the club-soccer season roll
  imply)? What are the exact start and end dates of the 2026–27 scoring window?

### Scoring
- **[Q4]** During the season, how does a roster slot accumulate? My assumption unless told otherwise:
  apply the same per-league formula to season-to-date stats, then divide by the **frozen** draft-time
  99th-percentile benchmark → a score that climbs from 0 toward ~100 as the season completes. Correct?
- **[Q5]** Is the benchmark denominator **frozen at draft time**, or recomputed as new data arrives?
  (Frozen is the only way standings are stable day-to-day; recomputing means yesterday's standings
  silently change.)
- **[Q6]** How does the MLB July-anchored contract blend translate to live scoring? Once the season is
  underway, are the 0.42/0.58/0.75/1.181 multipliers still applied, or do they only exist to price
  assets pre-draft and live scoring just counts actual points from the contract start date?
- **[Q7]** A fantasy team's total = **sum** of all 62 slot scores? Or average, or best-N? If sum, a
  team is at ~6,200 when everyone hits 100 — is that the intended headline number, or do you want it
  divided back down to a 0–100 team score?
- **[Q8]** Assets whose seasons don't overlap the league year (an NFL player in June, Olympics outside
  a Games year) — do they sit at 0, carry last season's score, or get hidden from the display?

### Data & scraping
- **[Q9]** Five leagues currently run off hand-built local CSVs (Club Soccer via FBref, Olympics,
  Tennis, NASCAR, and the FBref soccer player file). For daily automated scraping I need a real
  source for each. Do you have the provenance of those CSVs — were they FBref/Kaggle exports? For
  Tennis, is Tennis Abstract's match ledger the source (`Board.R` already scrapes their rankings)?
- **[Q10]** **Do we keep R in the pipeline, or port the scoring to one language?** Two viable paths:
  - *(a)* Keep the R scripts as the scoring engine and have a scheduler run `Rscript` nightly, writing
    to a database. Zero risk of scoring drift, but two runtimes to deploy and R packages
    (`nflreadr`, `hoopR`, `wehoop`, `baseballr`, `fastRhockey`, `cfbfastR`, `softballR`, `golfastr`)
    become production dependencies.
  - *(b)* Port every formula to the app's language (Python or TypeScript) and hit the underlying APIs
    directly. One runtime, much easier to test and to compute *daily deltas* rather than full-season
    aggregates — but it's a real porting effort across 18 leagues and I'd want to validate each
    against the R output.

    My recommendation is **(b)**, because live daily scoring wants incremental per-game rows, which
    those season-aggregate scripts aren't shaped for — but it's your call and (a) gets you running sooner.
- **[Q11]** What's the acceptable staleness? "Daily" — a single overnight run, or multiple refreshes a
  day for in-progress games?

### Rosters & transactions
- **[Q12]** Do rosters change in-season — waivers, free-agent pickups, trades, IR? This heavily
  affects the data model: if slots can change hands, I need to score *points earned while on your
  roster*, which means storing per-day per-asset deltas rather than just season totals.
- **[Q13]** What happens when a rostered player is injured, retires, is traded between leagues, or a
  driver leaves the grid?

### Season-length normalization (admin)
- **[Q14]** For a shortened MLS 2027 / MLB season, what's the intended correction — prorate counting
  stats to a full-season game count (`score × expected_games / actual_games`)? Applied to which
  components? Playoff milestones, division titles, and championship bonuses shouldn't prorate the way
  wins and point-differential do.
- **[Q15]** Should the admin set an **expected games count per league per season**, and the app derive
  the multiplier — or do you want to enter a raw multiplier directly?
- **[Q16]** What else belongs in the admin dashboard? My starting list: season-length overrides,
  manual score corrections, benchmark freeze/refresh, roster cap edits, scrape re-runs and failure
  log, asset name-matching fixes (this *will* be a recurring chore — the same person is
  "Shohei Ohtani" in one feed and "S. Ohtani" in another).

### Product & platform
- **[Q17]** Who can see what? Public read-only, or login for all 5 managers? Does a manager need to
  edit anything, or is it read-only for everyone but the admin?
- **[Q18]** Standings line graph "progression through time" needs a **daily snapshot table** written
  from day one — no way to reconstruct it later. Confirm you want that from launch.
- **[Q19]** The bar graph "breakdown by roster slot type" — what are the buckets? The 17 draft
  categories above, Player vs. Team, or by sport (Soccer / Basketball / Baseball / …)?
- **[Q20]** On the My Team tab, "raw stats" — which stats per league? I'd default to showing the exact
  inputs the scoring formula consumes (so the score is auditable), plus games played. Enough?
- **[Q21]** Where should this be hosted, and do you have a preference for the stack? Default proposal
  below in §4 unless you say otherwise.
- **[Q22]** Any budget constraints on hosting/paid data APIs? Some of these sources (especially soccer
  and tennis) are much easier with a paid feed.

### Draft (next season)
- **[Q23]** For the 2027 in-app draft: keep the same blind daily-auction format exactly as
  `Auction Resolution.R` implements it, or is this a chance to change the rules?
- **[Q24]** If it stays: does it become live/synchronous (managers bid in the app, round resolves on a
  timer) or does it keep the current async once-a-day cadence?

---

## 4. Proposed architecture (pending [Q10], [Q21])

```
┌─────────────────────┐
│  Scheduled job      │  nightly, per league
│  (cron / Actions)   │
└──────────┬──────────┘
           │  fetch → normalize → score
           ▼
┌─────────────────────┐     ┌──────────────────────────────┐
│  Ingest + scoring   │────▶│  Postgres                    │
│  workers            │     │  · assets, leagues           │
└─────────────────────┘     │  · raw_stats (per asset/day) │
                            │  · scores (per asset/day)    │
                            │  · benchmarks (frozen p99)   │
                            │  · rosters, roster_history   │
                            │  · standings_snapshots       │
                            │  · admin_overrides           │
                            └──────────┬───────────────────┘
                                       ▼
                            ┌──────────────────────────────┐
                            │  Web app (Next.js)           │
                            │  Standings · My Team · Admin  │
                            └──────────────────────────────┘
```

Key data-model decisions this bakes in:

- **`raw_stats` is append-only, per asset per day.** Everything else is derived, so a formula fix is a
  recompute rather than a re-scrape.
- **`benchmarks` is a versioned table**, so a frozen draft-time p99 is a row you can point at, and
  re-normalizing later is explicit rather than accidental.
- **`standings_snapshots` is written nightly** so the progression graph exists from day one ([Q18]).
- **`roster_history` with valid-from/valid-to** so mid-season transactions ([Q12]) are representable
  even if you don't use them in year one.

---

## 5. To-do list

### Phase 0 — Decisions
- [ ] Answer [Q1]–[Q24]; lock league config (managers, roster template, league-year dates)
- [ ] Decide R-in-pipeline vs. port ([Q10])
- [ ] Decide stack + hosting ([Q21])

### Phase 1 — Foundation
- [ ] Repo scaffold, CI, lint/test
- [ ] Database schema + migrations
- [ ] Seed leagues, draft pools, normalization groups, roster caps from the R config
- [ ] Import the frozen draft-time benchmarks and the drafted rosters (`Master_Drafted_Assets.xlsx`)

### Phase 2 — Scoring engine
- [ ] Port/wrap each league's formula, one module per league, each with a golden-file test asserting
      it reproduces the R output for a known season
- [ ] Normalization layer (buffer pool → p99 benchmark → 0–100 scale)
- [ ] Season-length adjustment layer ([Q14]/[Q15])
- [ ] Nightly recompute + standings snapshot

### Phase 3 — Ingestion
- [ ] One adapter per source (ESPN API, MLB Stats API, FanGraphs, nflverse, NHL API, Jolpica, …)
- [ ] Solve the five CSV-backed leagues ([Q9])
- [ ] Asset identity/name-matching layer with an admin resolution queue
- [ ] Scheduling, retries, failure alerting, per-source health dashboard

### Phase 4 — Web app
- [ ] Standings: table (default) → bar breakdown by slot type → line progression
- [ ] My Team + all-teams browser: per-slot raw stats and normalized score
- [ ] Asset detail view with score history
- [ ] Auth for 5 managers + admin ([Q17])

### Phase 5 — Admin
- [ ] Season-length overrides
- [ ] Manual corrections + audit log
- [ ] Scrape control (re-run, backfill, view failures)
- [ ] Roster/transaction management ([Q12])

### Phase 6 — Draft (2027)
- [ ] Big-board generation in-app (port `Board.R`)
- [ ] Bid submission UI
- [ ] Auction resolution engine (port `Auction Resolution.R`, incl. tiebreaker queue, self-tie
      resolution, budget ledger) with tests reproducing the 2026 draft results exactly
- [ ] Live draft room ([Q24])
