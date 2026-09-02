# Cross-Sport Fantasy League — Web App Plan

**Status:** v0.2 — decisions locked from the Q&A round. Sections marked **[OPEN]** still need input.
Reference material on the existing R scripts is in §7.

---

## 1. League rules (locked)

### 1.1 Season

| | |
|---|---|
| Normal league year | July → July (anchored on the MLB All-Star Game) |
| **2026–27 season start** | **2026-08-21** |
| **2026–27 season end** | **~2027-07-13** (MLB All-Star Game) |
| First-season length | ~47 weeks, not 52 — see §2.3 |
| Start date | Admin-entered each season |
| Backfill | Season already underway; stats must be retrieved retroactively from 2026-08-21 |

### 1.2 Managers

- **5 active managers** in 2026–27; the app must let this be changed per season.
- **Benchmark manager count is a separate, independently configurable number**, defaulting to **15** —
  deliberately generous, because its job is to define "fantasy relevant," not to match league size.
- Do not conflate the two. `league_manager_count = 5`, `benchmark_manager_count = 15`.

### 1.3 Roster template

65 slots at full strength: **29 team slots** (no bench, all count) + **36 player slots**
(22 starters + 14 bench).

| Category | Team slots | Player slots | Player starters | Player bench |
|---|---:|---:|---:|---:|
| Club Soccer Top 3 (EPL / La Liga / Serie A) | 4 | 5 | 4 | 1 |
| Club Soccer Other (Bundesliga / Ligue 1 / MLS / NWSL) | 4 | 5 | 4 | 1 |
| NFL | 2 | 4 | 2 | 2 |
| NBA | 2 | 4 | 2 | 2 |
| MLB | 2 | 4 | 2 | 2 |
| NHL | 2 | 4 | 2 | 2 |
| WNBA | 1 | 2 | 1 | 1 |
| PGA | — | 3 | 2 | 1 |
| Tennis (ATP + WTA) | — | 3 | 2 | 1 |
| Motorsports (F1 + NASCAR) | — | 2 | 1 | 1 |
| NCAAF | 2 | — | — | — |
| NCAAM | 2 | — | — | — |
| NCAAW | 2 | — | — | — |
| NCAA Baseball | 1 | — | — | — |
| NCAA Softball | 1 | — | — | — |
| Intl Soccer | 2 | — | — | — |
| Olympics | 2 | — | — | — |
| **Total** | **29** | **36** | **22** | **14** |

**2026–27 deactivations** (placeholders retained in the schema, flagged inactive for the season):

- **Olympics** — 2 team slots. No Games before the next draft.
- **WNBA** — 1 team + 2 player slots. Season nearly over at the 8/21 start.

→ **60 active slots, 47 of which count. Maximum theoretical team score = 4,700.**

### 1.4 Scoring: season-long best ball

A team's total is the **sum** of, for each category, the **top-K scores** where K = that category's
starter count. Nothing is set weekly; the best performers are selected continuously.

> NFL example: four rostered players scoring 100, 98, 97, 80 → only 100 + 98 count (K=2).

Every team slot counts (K = cap). Which slots are "counting" changes over time as scores move — the
UI must reflect the live selection (see §4.2).

### 1.5 Transactions

- No waivers, no free-agent pickups, no weekly lineups.
- **Trades only.** On a trade, **points accrued to date stay with the original owner**; the receiving
  manager accrues from the trade date forward.
- Consequence for the data model: an asset's season is a sequence of **owner stints**. Standings sum
  *accrued stint values*, not an asset's overall season score. Best-ball ranks stint values within a
  manager's category.
- Injured / retired / inactive assets simply stop accruing. That is what bench slots are for.

---

## 2. Normalization

### 2.1 The benchmark (frozen)

Unchanged from `All_Analysis.R`, with the pool cutoffs made parametric:

1. Map league → draft pool.
2. `Target_N = per_manager_rate × benchmark_manager_count`; `Buffer_N = Target_N × 1.50` (players)
   or `× 1.33` (teams).
3. Rank the pool by `Total_Points`, truncate to `Buffer_N`.
4. Within each normalization group, `benchmark = quantile(Total_Points, 0.99)`.
5. `Scaled_Score = Total_Points / benchmark × 100`.

Every `Target_N` in the R script is an exact multiple of 15, so the per-manager rates are clean:

| Pool | Player rate | Team rate |
|---|---:|---:|
| Club Soccer Top 3 | 6 | 4 |
| Club Soccer Other | 6 | 4 |
| NFL / NBA / MLB / NHL | 3 | 2 |
| WNBA | 2 | 1 |
| PGA / Tennis | 3 | — |
| Motorsports | 2 | — |
| NCAAF / NCAAM / NCAAW / Intl Soccer / Olympics | — | 2 |
| NCAA Baseball / NCAA Softball | — | 1 |

Team rates equal roster caps exactly. Player rates do not: NFL/NBA/MLB/NHL are 3 vs. a cap of 4, and
club soccer is 6 vs. a cap of 5. Preserved as-is — see **[OPEN-1]**.

**Normalization groups:** players — NBA/WNBA → Backcourt / Frontcourt; MLB → Batter / Pitcher;
NFL → QB / RB / WR / TE; all others → league. Teams → league. NHL goalies excluded.

**Freezing rules for 2026–27:**

- Benchmarks are computed **once before the season** and frozen for its duration. Recomputed each
  new season.
- **No data after 2026-08-20** may enter a benchmark.
- Use **complete seasons only**, except MLB and WNBA (bisected — see §2.2).
- For individual sports, full-season historical data is acceptable *for benchmark purposes* because a
  season is still a 52-week period.

### 2.2 Draft-bisected leagues

Leagues whose season straddles the draft use the MLB contract-blend pattern from
`MLB_Players_Teams.R`: the post-draft remainder of year N is discounted, and the pre-draft portion of
year N+1 is inflated so the two shares reconcile to a full season.

```
contract_pts = pts_N × share_post × mult_N  +  pts_N1 × share_pre × mult_N1
mult_N1      = (1 − share_post × mult_N) / share_pre
```

MLB (computed): `share_post = 0.42`, `share_pre = 0.58`, `mult_N = 0.75` → `mult_N1 ≈ 1.181`.

**Still to compute: WNBA and NWSL**, using each league's own schedule shares. See **[OPEN-2]** on the
`mult_N` value.

**MLS** is a special case: moving to a fall–spring calendar, with a shortened 2027 transition
("sprint") season. It needs *both* bisection weighting for 2026 *and* short-season proration for 2027.

### 2.3 The first-season window problem — proposed solution

The 2026–27 year runs 2026-08-21 → 2027-07-13, ~47 weeks. A flat 47/52 scalar is **wrong** for
tennis, golf and motorsport, because an August→July window contains a different proportion of
offseason than a July→July one. Scaling by elapsed weeks would systematically misprice them.

**Proposal: don't scale — benchmark on the same calendar window.**

All four individual sports have date-stamped, event-level historical data. So rather than deriving a
correction factor, compute each benchmark over the *actual* window:

> For each of the last N years, sum each athlete's event points over
> `[Aug 21 of year Y, Jul 13 of year Y+1]`, then take the p99 of those window totals.

This dissolves the problem — the offseason proportion is identical in the benchmark and in live
scoring, so no correction factor exists to get wrong. It generalizes for free: every future season
just uses that season's own start/end dates, and a July→July year needs no special handling.

Cost: event-level data with dates, rather than season aggregates. Feasible and free for all four —
F1 via Jolpica per-round results, NASCAR from the race-level CSV, PGA via the ESPN golf scoreboard
by date, tennis from match-level ledgers. Confirm at **[OPEN-3]**.

### 2.4 Season-length proration

For genuinely shortened seasons (MLS 2027, possibly MLB), scale up by games played. Prorate **only**:

- **Players:** counting stats.
- **Teams:** regular-season achievements (wins, big wins, shutouts, point/run differential).

Do **not** prorate playoff milestones, division titles, championships, or any one-off achievement.

The app computes the multiplier from an admin-entered expected-games count; the admin verifies it.

### 2.5 Assets whose events cross a season boundary

Events ending shortly after a draft — Women's World Cup, a Summer Olympics — count **entirely for the
previous league year**. Women's international soccer teams drafted in 2026 accrue all their World Cup
points to 2026–27 even if the tournament runs past the 2027 draft. Scoring windows therefore attach
to the *asset's event*, not strictly to the season end date.

### 2.6 Soccer transfers across pool boundaries

A player transferring between a Top-3 league and an Other league gets the manager a courtesy fractional
slot: **3.5 in the origin category, 4.5 in the destination** (the 0.5 representing each partial stint).
Scores need no weighting beyond normal normalization. Needs a concrete algorithm — **[OPEN-4]**.

---

## 3. Architecture

**Python** backend (the R work is reference, not runtime), Postgres, daily batch. Dev on a cloud
CodeOSS instance; later a dedicated permanent server with a free domain. All data sources free.

```
┌──────────────────────┐
│ Nightly scheduler    │
└──────────┬───────────┘
           ▼
┌──────────────────────┐   per-league adapters (one module each)
│ Ingest               │   ESPN · MLB Stats · FanGraphs · nflverse
└──────────┬───────────┘   NHL API · Jolpica · CFBD · …
           ▼
┌──────────────────────┐
│ raw_stats            │  append-only, per asset per day — the source of truth
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ Scoring              │  per-league formula → league_points
│ Weighting            │  bisection blend · proration
│ Normalization        │  ÷ frozen benchmark × 100
│ Accrual              │  split by owner stint
│ Best-ball rollup     │  top-K per category → team total
└──────────┬───────────┘
           ▼
┌──────────────────────┐      ┌────────────────────────┐
│ Postgres             │─────▶│ FastAPI + React        │
│ · assets, leagues    │      │ Standings · My Team    │
│ · benchmarks (frozen)│      │ Admin                  │
│ · rosters, stints    │      └────────────────────────┘
│ · daily_scores       │
│ · standings_snapshots│
│ · admin_overrides    │
└──────────────────────┘
```

Design commitments:

- **`raw_stats` is append-only and dated.** Everything downstream is derived, so a formula fix is a
  recompute, not a re-scrape. This also means the daily progression graph can be **reconstructed back
  to 2026-08-21** rather than starting at app launch.
- **`benchmarks` is versioned** — a frozen season benchmark is a row you point at. Re-normalizing is
  explicit, never accidental.
- **`roster_stints`** carry valid-from / valid-to so trades split accrual correctly.
- **Best-ball selection is recomputed and stored per day**, so the UI can show which slots counted on
  any given date.

---

## 4. Web app

### 4.1 Standings (default view)

Table of teams and points. Two toggles:

### 4.2 Bar graph — contribution breakdown

Stacked horizontal bar per manager:

- **One hue per league** (NFL one color, NBA another, …).
- **Teams and players within a league are different shades** of that hue; all team slots share a
  shade and all player slots share a shade.
- **Each individual slot is a separate segment with a border**, so per-slot contribution is visible.
- Reflects **live best-ball selection** — non-counting slots are excluded or visually demoted, and
  this changes as scores move.

### 4.3 Line graph — progression over time

From `standings_snapshots`, backfilled to season start.

### 4.4 My Team (and all other teams)

Per slot: raw stats (exactly the inputs the scoring formula consumes, so the score is auditable),
games played, league points, and normalized score. Counting vs. bench state shown per slot.

### 4.5 Access

Managers get read-only access. Admin edits. Manager write access arrives with the draft feature.

### 4.6 Admin dashboard

Season start/end dates · manager counts (league + benchmark) · slot activation (Olympics, WNBA) ·
expected-games overrides and derived proration multipliers · benchmark freeze/recompute · manual score
corrections with an audit log · scrape control (re-run, backfill, failure log) · asset name-matching
resolution queue · trade entry.

---

## 5. Draft (2027)

Identical rules to `Auction Resolution.R` — blind daily auction, $1000/round, category caps, rotating
tiebreaker queue, budget ledger where bid-but-unspent money rolls over and unbid money is forfeited.
Async once-a-day cadence retained.

**One change:** self-ties are flagged at **submission** time and the manager must set a priority order
before sending the offer sheet — replacing the R script's interactive console prompt during resolution.

---

## 6. To-do

### Phase 1 — Foundation
- [ ] Python project scaffold, CI, lint/format/test
- [ ] Postgres schema + migrations (assets, leagues, pools, benchmarks, raw_stats, daily_scores,
      roster_stints, standings_snapshots, admin_overrides)
- [ ] Seed league/pool/normalization-group/roster-cap config from the R scripts
- [ ] Import drafted rosters from `Master_Drafted_Assets.xlsx`
- [ ] Asset identity layer (canonical IDs + alias table) — name matching across feeds will be a
      recurring chore and needs to be designed in, not bolted on

### Phase 2 — Per-league increments *(one league at a time, per your preference)*
For each league, in order: **NFL → NBA → MLB → NHL → Club Soccer → NCAA (F/M/W/Baseball/Softball) →
Intl Soccer → PGA → Tennis → Motorsports → (Olympics, WNBA deferred)**

Each increment ships end-to-end:
- [ ] Free Python data source identified and adapter written
- [ ] Scoring formula ported, with a **golden-file test asserting it reproduces the R output** for a
      known season
- [ ] Historical pull for benchmarking (respecting the 2026-08-20 cutoff)
- [ ] Backfill from 2026-08-21
- [ ] Wired into nightly ingest

### Phase 3 — Scoring pipeline
- [ ] Benchmark computation + freeze, parameterized by `benchmark_manager_count`
- [ ] Window-based benchmarking for individual sports (§2.3)
- [ ] Bisection weighting: MLB (known), WNBA, NWSL, MLS
- [ ] Proration engine (§2.4)
- [ ] Owner-stint accrual + best-ball rollup
- [ ] Nightly standings snapshot + retroactive backfill to season start

### Phase 4 — Web app
- [ ] Standings table
- [ ] Contribution bar graph (§4.2)
- [ ] Progression line graph
- [ ] My Team / all-teams browser
- [ ] Asset detail with score history
- [ ] Read-only manager auth

### Phase 5 — Admin
- [ ] Full dashboard per §4.6

### Phase 6 — Deployment
- [ ] CodeOSS dev environment
- [ ] Permanent server + free domain
- [ ] Nightly job monitoring and failure alerting

### Phase 7 — Draft (2027)
- [ ] Big-board generation (port `Board.R`)
- [ ] Offer-sheet submission UI with submission-time self-tie ordering
- [ ] Auction resolution engine, with tests reproducing the 2026 draft results exactly

---

## 7. Open items

- **[OPEN-1]** Benchmark pool rates: team rates equal roster caps exactly, but player rates don't —
  NFL/NBA/MLB/NHL are 3 per manager against a cap of 4, and club soccer is 6 against a cap of 5.
  Intentional, or drift? Preserved as-is for now.
- **[OPEN-2]** `MLB_Players_Teams.R` uses `mult_year_n = 0.75` (a 25% discount), but you described the
  post-draft portion as weighted **80%**. Which is correct for MLB, and which should WNBA/NWSL use?
- **[OPEN-3]** Confirm the §2.3 window-benchmarking approach for individual sports, which replaces the
  undevised scalar correction.
- **[OPEN-4]** Concrete rule for the 3.5 / 4.5 courtesy slot on cross-pool soccer transfers (§2.6).
- **[OPEN-5]** Trades split an asset across two managers. Confirm each stint competes independently
  for a best-ball starter slot in its owner's category.
- **[OPEN-6]** Per-league data source decisions, resolved as each league is built (Phase 2). The five
  leagues previously fed by hand-built CSVs — Club Soccer, Olympics, Tennis, NASCAR — need free
  replacements; PGA needs one regardless, since `golfastr` is R-only.

---

## 8. Reference: the existing R scripts

### 8.1 Per-league valuation scripts

Each pulls ~2020–2025 data, applies a bespoke formula, and appends the latest season to
`Master_Data/master_players.csv` (`Player, Team, League, Role, Season, Total_Points`) or
`master_teams.csv` (`Team, League, Season, Total_Points`).

| Script | Assets | Source | Notes |
|---|---|---|---|
| `NFL_Players.R` | Players | `nflreadr` | Half-PPR; QB/RB/WR/TE |
| `NFL_Teams.R` | Teams | `nflreadr` | Wins ×10, big wins ×3, shutouts ×5, div wins ×2, div champ ×15, playoff app ×10, playoff wins ×15, pt diff ×0.1 |
| `NBA_Players_Teams.R` | Both | `hoopR` | Players: pts + 1.2 reb + 1.5 ast + 3 stl + 3 blk − 1 TO + 0.5 3PM + DD/TD bonuses + 0.1×plus-minus. Teams incl. NBA Cup |
| `WNBA_Players_Teams.R` | Both | `wehoop` | As NBA; Commissioner's Cup |
| `MLB_Players_Teams.R` | Both | `baseballr` | Rolling 12-month contract engine (§2.2); Ohtani rule: primary + 0.5 × secondary |
| `NHL_Teams_Players.R` | Both | `fastRhockey` | Skaters and goalies scored separately; goalies excluded from normalization |
| `NCAAF_*` / `NCAAM_*` / `NCAAW_*` | Both | `cfbfastR`, `hoopR`, `wehoop` | |
| `NCAA_Baseball_*` / `NCAA_Softball_*` | Both / Teams | `baseballr`, `softballR` | Softball API flaky; Top-25 failsafe |
| `Club_Soccer.R` | Both | **local CSVs** | Win pts by competition (UCL 5 / Europa-Cup 4 / league 3), +1 for 2-goal margin, +1 clean sheet. Players: minutes, goals by position (DF/GK 6, MF 5, FW 4), assists ×3, cards −1/−3 |
| `Intl_Soccer.R` | Teams | GitHub CSVs (`martj42`) | |
| `Olympics.R` | Teams | **local CSVs** | Gold 5 / Silver 3 / Bronze 1 |
| `PGA.R` | Players | `golfastr` | Top-30 finish table, 1.5× majors + Players |
| `Tennis_Players.R` | Players | **local CSVs** | ATP round-points map, 1.5× straight sets, bye bonus |
| `F1.R` | Players | Jolpica/Ergast | Championship points |
| `NASCAR.R` | Players | **local CSV** | Retro 2026 scale: 1st = 55, 2nd = 35 descending |

### 8.2 `All_Analysis.R`
The normalization engine — see §2.1.

### 8.3 `Board.R`
Draft-board generator. Scrapes ESPN site API (team directories + rosters), Tennis Abstract (ATP/WTA
top 50), Jolpica (F1 drivers); hardcoded international soccer lists. Emits an Excel big board with
`My_Projection / Max_Bid_$ / Draft_Target? / Notes` per manager.

### 8.4 `Auction Resolution.R`
The draft engine — see §5. Resolution order: highest bid → tiebreaker queue rank → self-tie priority
→ submission row. Ledger:
`Forfeited = max(0, Start − Submitted) + (Submitted − Valid)`; `Refunded = Start − Spent − Forfeited`;
`Next_Day_Starting = 1000 + Refunded`.
