# Cross-Sport Fantasy League — Web App Plan

**Status:** v0.3 — all opening questions resolved. Build underway: league config, normalization,
best-ball rollup and the NFL module are implemented and tested. Reference material on the R scripts
is in §8.

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
- **Trades only, and necessarily reciprocal** — without free agency, a trade is a swap of slot
  occupants, not an addition.
- **The scoring unit is the slot, not the asset.** A slot is a persistent container; a trade changes
  who occupies it. The slot's score is the sum of what each occupant accrued while it sat there:

  ```
  slot_score = (outgoing player's start-to-trade score) + (incoming player's trade-to-now score)
  ```

  That single number is what competes for a starter slot. Points earned before a trade stay with the
  manager who earned them, which is exactly what the slot model gives for free.
- **Pairing is explicit.** When multiple players from the same league are traded together, the admin
  designates which outgoing player pairs with which incoming one — it cannot be inferred.
- Injured / retired / inactive assets simply stop accruing and sink below the best-ball cut on their
  own. That is what bench slots are for; no transaction is needed.

---

## 2. Normalization

### 2.1 The benchmark (frozen)

Unchanged from `All_Analysis.R`, with the pool cutoffs made parametric:

1. Map league → draft pool, and assign a normalization group.
2. `Target_N = per_manager_rate × benchmark_manager_count`; `Buffer_N = Target_N × 1.50` (players)
   or `× 1.33` (teams).
3. Rank **within each normalization group** by `Total_Points`, truncate to `Buffer_N`.
4. `benchmark = quantile(Total_Points, 0.99)` for that group.
5. `Scaled_Score = Total_Points / benchmark × 100`.

**Truncation is per normalization group, not per draft pool.** Each position is measured against its
own historical distribution — a tight end against tight ends, a pitcher against pitchers, a backcourt
player against the backcourt. `All_Analysis.R` ranked within `Draft_Pool` and only *then* grouped by
`Norm_Key`, which meant a position's benchmark could be computed from whatever few of its members
happened to crack the combined pool. For NFL 2024 at 15 managers that gave the tight end benchmark a
sample of **4 players**; per-group truncation gives it 68. Benchmarks shift by up to ~11% (WR) as a
result, and every position's leader now lands near 100, which is the intended meaning of the scale.

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

MLB (computed, confirmed): `share_post = 0.42`, `share_pre = 0.58`, **`mult_N = 0.75`** → `mult_N1 ≈ 1.181`.

**All three computed.** The `mult_N1` inflation differs per league because each captures a different
proportion of its season on either side of the draft:

| League | Season split at the draft | `mult_N` | `mult_N1` | Basis |
|---|---|---:|---:|---|
| MLB | 58% pre / 42% post | 0.75 | 1.1810 | ~58% of 162 games before the All-Star break |
| WNBA | 47% pre / 53% post | 0.80 | 1.2255 | 66 season days before Jul 13, 73 after |
| NWSL | 13.2% pre / 86.8% post | 0.95 | 1.3294 | 17 season days before Jul 13, 112 after |

`mult_N1 = (1 - share_post × mult_N) / share_pre`, which is what makes both weighted stretches
reconcile to one full season — without it, discounting the known half would quietly shrink a
bisected league's whole contribution against the leagues that are not bisected. The lighter the
draft-time knowledge, the lighter the discount: MLB drafts with 42% of the season left and takes
0.75, NWSL drafts with 87% left and takes 0.95.

The WNBA day counts (66/73) are 47.5%/52.5%, which would give 1.2212; the stated 47/53 is used so
the multiplier matches the figure the rule was set with. The 0.35% difference is well inside the
uncertainty in `mult_N` itself.

**MLS** is a special case: moving to a fall–spring calendar, with a shortened 2027 transition
("sprint") season. It needs **only** short-season proration — the league drafts for 2027, which is
not bisected, so no `mult_N` applies.

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

**Approved.** Cost is event-level data with dates rather than season aggregates — free for all four:
F1 via Jolpica per-round results, NASCAR from race-level data, PGA via the ESPN golf scoreboard by
date, tennis from match-level ledgers. Each source is verified independently before its league ships.

### 2.4 Season-length proration

For genuinely shortened seasons (MLS 2027, possibly MLB), scale up by games played. Prorate **only**:

- **Players:** counting stats.
- **Teams:** regular-season achievements (wins, big wins, shutouts, point/run differential).

Do **not** prorate playoff milestones, division titles, championships, or any one-off achievement.

The app computes the multiplier from an admin-entered expected-games count; the admin verifies it.

### 2.5 Postseason handling

**Benchmarks (p99) are computed from regular-season data only.** Postseason samples are small and
reach only a minority of players, so including them would distort the distribution the scale is drawn
from.

**Postseason production counts as a bonus worth a flat 10% of a regular season**, the same share in
every competition, paid at the player's postseason rate:

```
scalar = bonus_share × regular_games        # bonus_share = 0.10
bonus  = (postseason_points / postseason_games) × scalar
total  = regular_points + bonus
```

| Competition | Reg games | Scalar |
|---|---:|---:|
| NFL | 17 | 1.7× |
| MLB | 162 | 16.2× |
| NBA | 82 | 8.2× |
| NHL | 84 | 8.4× |
| Champions / Europa / Conference League | 38 | 3.8× |

An NFL player with one playoff game has those points multiplied by 1.7; with two, their combined
points by 1.7/2. A player who performs in the postseason at their regular-season rate earns exactly
10% of that season — identically in every league. Outperforming that rate earns more, underperforming
less. Raw postseason stats never enter the total directly; they only set the rate.

`bonus_share` is global and `scalar` is overridable per competition, both tunable from the admin
dashboard without touching the formula.

**Excluded entirely** — counting for neither phase, so they neither pad the regular season nor earn a
bonus: the **NBA Play-In**, and **European qualifying rounds**. Only the playoffs and European
competition proper carry the bonus.

**Not applied to teams.** Team scoring already prices the postseason explicitly and boundedly (NFL:
playoff appearance 10, playoff wins 15; NBA: appearance 10, wins 3, series 5), those terms sit in both
the benchmark and live scoring consistently, and with ~30 teams per league there is no small-sample
distortion to correct.

### 2.6 Assets whose events cross a season boundary

Events ending shortly after a draft — Women's World Cup, a Summer Olympics — count **entirely for the
previous league year**. Women's international soccer teams drafted in 2026 accrue all their World Cup
points to 2026–27 even if the tournament runs past the 2027 draft. Scoring windows therefore attach
to the *asset's event*, not strictly to the season end date.

### 2.7 Soccer transfers across pool boundaries

When a drafted player transfers between a Top-3 league (EPL / Serie A / La Liga) and an Other league
(Bundesliga / Ligue 1 / MLS):

- The **original entry keeps every stat earned before the transfer** and stays in its origin category.
- A **duplicate entry is created in the destination league**, accruing the player's stats from the
  transfer forward.
- **Each entry is normalized against the league it was acquired in** — the original against the origin
  pool's benchmark, the duplicate against the destination pool's.

This is the concrete form of the "3.5 / 4.5 courtesy slot": the manager ends up with a partial entry
on each side rather than losing the player's pre-transfer production. No extra weighting is applied
beyond normal normalization.

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

### 3.1 Storage — SQLite now, Postgres later

The schema in `whul/store/schema.sql` is portable SQL: no `SERIAL`, no `AUTOINCREMENT`, no JSON
operators, dates as ISO-8601 `TEXT`, payloads as JSON in `TEXT`. SQLite runs it today — stdlib, no
server, works in the test suite and on the dev box — and a five-manager season is on the order of
660,000 score rows, which it handles without effort. Postgres stays the deployment target; the
dialect-specific parts (connection, parameter style, upsert) are confined to `whul/store/db.py`.

`raw_stats` holds one row per asset per day per source, with the feed's figures as a JSON payload,
so a feed adding a stat needs no migration. It stores **cumulative** season-to-date values rather
than daily deltas: that is what most feeds serve, differencing consecutive snapshots recovers the
deltas exactly, and rebuilding a cumulative total from deltas would compound any day the scraper
missed.

### 3.2 The app is a static site

The league updates once a day and is read by five people. There is no login, no user input, and
nothing interactive but the admin tools — so there is nothing for a server to do. The nightly job
runs the pipeline, writes a folder of HTML, and GitHub Pages serves it.

That makes hosting free and permanent, removes the class of failure where the site is down because
a process died, and means nothing has to stay awake. `.github/workflows/publish.yml` runs the
pipeline on a cron, rebuilds the site and deploys it. The database lives on a `data` branch rather
than in `main`, so a growing binary does not put an unreadable diff into every pull request.

The tradeoff is that standings refresh nightly rather than on demand. For a season-long best-ball
league scored from daily cumulative feeds, the underlying data only moves daily anyway.

Charts are hand-written inline SVG — no framework, no CDN, works offline and prints. The palette is
the validated categorical set; five managers were checked with the colourblind validator in both
modes before anything was drawn (worst adjacent CVD ΔE 9.1 light / 8.4 dark). Light mode raises a
contrast warning on three of the five hues, so every series is directly labelled and each chart
ships a table view — the standings table is the page's default, not an alternative to it.

Design commitments:

- **`raw_stats` is append-only and dated.** Everything downstream is derived, so a formula fix is a
  recompute, not a re-scrape. This also means the daily progression graph can be **reconstructed back
  to 2026-08-21** rather than starting at app launch.
- **`benchmarks` is versioned** — a frozen season benchmark is a row you point at. Re-normalizing is
  explicit, never accidental.
- **`slot_occupancy`** carries valid-from / valid-to per slot, so a trade splits accrual correctly and
  the slot — not the asset — is what best-ball ranks.
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
- [x] Python project scaffold and test suite
- [x] League/pool/roster config seeded from the R scripts
- [x] Normalization engine (buffer pool → frozen benchmark → 0-100)
- [x] Best-ball rollup with slot occupancy and trade accrual
- [x] Schema + migrations (assets, aliases, benchmarks, raw_stats, daily_scores, slot_occupancy,
      slot_scores, standings_snapshots, source_status, admin_overrides) — see §3.1
- [ ] Import drafted rosters from `Master_Drafted_Assets.xlsx` — **waiting on the draft**; until
      then `python -m whul.cli simulate` builds a placeholder league under season `2026-27-SIM`
      (invented assets, real roster shape, a full season of scores and six trades)
- [ ] Asset identity layer (canonical IDs + alias table) — name matching across feeds will be a
      recurring chore and needs to be designed in, not bolted on

### Phase 2 — Per-league increments *(one league at a time, per your preference)*
For each league, in order: **NFL ✅ → NBA ✅ → MLB → NHL → Club Soccer → NCAA (F/M/W/Baseball/Softball) →
Intl Soccer → PGA → Tennis → Motorsports → (Olympics, WNBA deferred)**

Each increment ships end-to-end:
- [ ] Free Python data source identified and adapter written
- [ ] Scoring formula ported, with a **golden-file test asserting it reproduces the R output** for a
      known season
- [ ] Historical pull for benchmarking (respecting the 2026-08-20 cutoff)
- [ ] Backfill from 2026-08-21
- [ ] Wired into nightly ingest

### Phase 3 — Scoring pipeline
- [x] Store: schema, ingest, staleness detection (SQLite now, portable SQL for Postgres later)
- [x] Benchmark computation + freeze, parameterized by `benchmark_manager_count`
- [x] Window-based benchmarking for individual sports (§2.3)
- [x] Bisection weighting — MLB 0.75/1.181, WNBA 0.80/1.2255, NWSL 0.95/1.3294; MLS deliberately
      unbisected (drafting for 2027). See `whul/scoring/bisection.py`
- [x] Proration engine (§2.4) — admin-entered expected games, counting stats only
- [x] Owner-stint accrual + best-ball rollup — wired to the store, trades reciprocal
- [x] Nightly standings snapshot + retroactive backfill to season start

### Phase 4 — Web app *(static site, see §3.2)*
- [x] Standings table — the default view, not a tab
- [x] Contribution bar graph (§4.2) — grouped horizontal bars by roster category
- [x] Progression line graph — hover crosshair, direct end labels, table view
- [x] My Team / all-teams browser — raw and normalized per slot, bench marked
- [ ] Asset detail with score history
- [ ] Read-only manager auth — **not needed on a static site**; the pages are
      public or they are not published. Revisit only if the league wants privacy.

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

## 7. Status and open items

### Built and tested (48 tests passing)

| Module | Covers |
|---|---|
| `whul/config/league.py` | Roster template, season window, per-manager pool rates |
| `whul/normalize.py` | Buffer pool → frozen p99 benchmark → 0-100 scale |
| `whul/bestball.py` | Slot occupancy, trade accrual, top-K rollup, standings |
| `whul/scoring/nfl.py` | Half-PPR players + team scoring |
| `whul/scoring/nba.py` | Box-score players + team scoring |
| `whul/scoring/postseason.py` | Appearance bonus and regular/postseason phase split |
| `whul/sources/nflverse.py` | nflverse release assets (free, no R dependency) |
| `whul/sources/hoopr.py` | hoopR-data (historical only — see below) |
| `whul/cli.py` | Per-league terminal harness (`score`, `weekly`, `list`) |

### Resolved

- **NWSL** — a placeholder only; managers may not draft NWSL assets, so no bisection weighting is
  needed for it.

- **Benchmark pool rates** — my earlier question was about `Target_N`, the size of the *historical
  pool* the 99th percentile is drawn from, which is a separate knob from roster caps and starter
  counts. The R values are preserved exactly and stored as per-manager rates, so they scale with the
  benchmark manager count. Worth noting the pool sizes don't track roster shape uniformly: NFL and
  friends draw from 3/manager against a cap of 4 and 2 starters, while club soccer draws from
  6/manager against a cap of 5. Preserved as-is; revisit only if the scale looks off in practice.
- **MLB post-draft weight** — `mult_N = 0.75`, per the R script.
- **Window benchmarking** for individual sports — approved, subject to data availability per league.
- **Cross-pool soccer transfers** — see §2.7.
- **Trades** — slot-based, reciprocal, explicit pairing. See §1.5.
- **Data sources** — each verified independently as its league is built.
- **Postseason weighting** — regular-season-only benchmarks plus a bonus worth a flat 10% of a
  regular season, equalized across leagues. See §2.5.
- **Tennis 500 vs 250 — resolved.** `whul/data/tennis_calendar.csv` carries all 121 ATP and WTA 2026
  events with their category and draw size, taken from the season schedule in
  `smeredith15/tennis2026`: 33 correctly designated 500s. For seasons after 2026,
  `whul/sources/tour_schedule.py` scrapes the designation from the tours themselves.
- **The tennis history is a single file with no upstream — back it up.** `JeffSackmann/tennis_atp`
  has been removed from GitHub, so `model_data_snapshot.rds` in `smeredith15/tennis2026` is the
  only surviving copy of 215,386 matches back to 2014. It cannot be re-downloaded. It is not
  vendored into this repo (7.9 MB of someone else's dataset), so `whul/sources/snapshot.py` reads
  it from a sibling checkout by default and `root=` points elsewhere.

### Data acquisition, as measured

| League | Source | Backfill | Nightly | Status |
|---|---|---:|---:|---|
| NFL | nflverse `stats_player` (GitHub) | ~1s / season | ~0.6s | verified through 2025 |
| NBA | ESPN site API (per date) | ~690s / season | ~5s | verified on 2026 |
| MLB | MLB Stats API + FanGraphs | not yet measured | not yet measured | **unverified** |
| PGA | ESPN golf scoreboard (season list) | 1 request / season | 1 request | **verified** on 2025: 49/49 events, 59-player field, all placed |
| NASCAR | ESPN racing scoreboard (season list) | 1 request / season | 1 request | **verified** on 2025: 41/41 events, 23-car field, all placed |
| F1 | Jolpica (Ergast successor) | ~5 requests / season | 1 request | **verified** on 2025: 479 results, 5 sprints |
| Tennis (live) | Flashscore feed + tournament pages | n/a | ~1 request pair / tournament | **verified**: 56 matches, rounds and scoring correct |
| Tennis (history) | Phase7B snapshot, local file | one read | n/a | **verified**: 172,400 matches 2014-2026, all 3,116 players named |
| Tennis calendar | api.wtatennis.com (WTA); ATP and tennistonic 403 | a few API calls | once a season | WTA API **confirmed**, recognition incomplete; 2026 seeded, parked until 2027 |

The individual sports are fetched per event rather than per date: a golf tournament runs Thursday to
Sunday and a race meeting spans a weekend, so walking the calendar would re-read the same event four
times. That also makes the nightly job cheap — it re-reads only what is in progress or newly final.

Tennis reads from two sources because no single one covers both jobs. History comes from
`model_data_snapshot.rds` — 215,386 matches back to 2014, with round, score, surface and best-of.
It began as a Sackmann export; the upstream repository has since been removed, so this file is the
only copy and has no replacement. The Flashscore feed (`global.flashscore.ninja`, ported from
`smeredith15/tennis2026`) serves a rolling ±7-day window, so it answers for the season in progress
and nothing else — run it nightly and it accumulates.

The daily feed carries no per-match round; a match inherits its tournament header, and a slam's
header ends with the country. The round comes from the per-tournament page instead, as `ER÷` next
to each match id — one request pair per tournament, only for tournaments that need it. A match
still without a round is dropped rather than scored: there is no bracket position to pay for.

### §2.9 How a tennis win is priced

Two facts decide which ATP points table a win pays from, and neither can be read off a
tournament's name:

- **Category** — slam, 1000, 500, 250, Tour Finals, or a team event.
- **Draw size** — a 96-draw Masters and a 56-draw Masters pay differently in the early rounds,
  because the same round is one win deeper into the larger field.

Both live in `whul/data/tennis_calendar.csv`, versioned in the repo, and
`tennis_calendar.unresolved` names any tournament a feed references that the calendar does not
know — so a renamed or new event surfaces as a gap rather than as quietly wrong points.

The **straight-sets bonus** depends on the format: 1.5× at best-of-five, where straight sets skipped
two, and 1.25× at best-of-three, where it skipped one. Only ATP main-draw slam matches are
best-of-five; the WTA plays best-of-three everywhere. A retirement earns the bonus only if a set was
actually completed first — `6-3 RET` and `6-3 3-1 RET` qualify, `3-1 RET` does not, because the
loser stopped during the opening set and nothing was really won.

A **bye** is the absence of a result in the preceding round, per ATP rules: a player with no R128
row in a 96-draw was not in that round, so its points come with their R64 win. This covers
structural byes, where the seeds skip the opening round without a bye ever being recorded.

This replaces the heuristic ported from `Tennis_Players.R`, which inferred the tier from a keyword
list of host cities and a count of matches played. Its cutoffs sat below the real draw boundaries,
so a 56-draw Masters and a 96-draw Masters landed on the same table and a 32-draw 500 landed on
the 64-draw one — the smaller tables were unreachable on complete tour data. The approach is taken
from `smeredith15/tennis2026`, which is already running against these feeds.

Each tier's column sums to the event's face value for a champion — 2000 at a slam, 1000 at a
Masters, 500, 250 — which is the arithmetic check that the tables are transcribed right, and it is
asserted in the tests.

NBA 2026: 273 dates, 30,853 rows, 705 players, benchmarks 3800.7 Backcourt /
4086.5 Frontcourt. Warm re-run 7.4s. Responses cache under `data/cache/espn`.

**What a scraper actually has to provide is a dataset that updates daily** — not
necessarily per-game rows. For most leagues, cumulative season-to-date figures
pulled once a day are sufficient: stored as a daily snapshot they give both live
standings and the history the progression line graph needs, and differencing two
snapshots yields any window's accrual, which is what trades and owner stints
require. Per-game detail is only needed where accrual must be split finer than a
day — in practice MLB's postseason. This markedly simplifies most adapters: a
season leaderboard endpoint is enough.

**NCAA needs game results only, not box scores.** All five NCAA categories are
team slots — there are no NCAA player slots at all — so those leagues need the
scoreboard endpoint alone, one request per date, with no per-game boxscore call.
That is roughly one request per date instead of one-plus-N-games, which removes
the scaling problem their game volume would otherwise create. The same is true of
Intl Soccer and Olympics.

**Source reachability from the development sandbox.** Only GitHub-hosted data is
reachable; every live sports API is blocked by egress policy — ESPN,
`statsapi.mlb.com`, FanGraphs, and Jolpica among them. So NFL could be verified
in place, while NBA had to be written blind and proved on the target machine.
MLB, NHL, F1 and the soccer leagues are in the same position: build with a
`probe` command first, confirm the payload shape from the target terminal, then
write scoring against a known-good shape. That sequence caught a real bug in the
NBA adapter (position read from the wrong key) before any scoring depended on it.

### Verification

`docs/TESTING_NFL.md` is a step-by-step guide for verifying a league's data
acquisition from your own machine, with expected values at each step. One guide per
league as each ships.

### Open

- **[OPEN-B] Validating the ports against R.** R is not installed in this environment, so I cannot
  diff Python output against R output directly. Current tests assert against values computed by hand
  from the R formulas plus real-season sanity checks, which catches formula errors but not subtle
  data-frame semantics. The stronger check is for you to run each R script once and commit its
  `Master_Data` CSV as a golden file. Worth doing for at least MLB and Club Soccer, where the R
  logic is most intricate.
- **[OPEN-F] Live sources for the sportsdataverse leagues.** `hoopR-data` was archived on
  2026-08-07 and its NBA files stop at season 2023, so it can seed benchmarks but cannot drive daily
  scoring. The same very likely applies to `wehoop` (WNBA, NCAAW) and the other sibling feeds. The
  natural replacement is the ESPN API, which is what these packages wrap — but it is blocked by this
  environment's egress policy, so the adapter has to be verified from your machine.

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
| `Tennis_Players.R` | Players | **local CSVs** | ATP round-points map, straight-sets bonus, bye bonus |
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
