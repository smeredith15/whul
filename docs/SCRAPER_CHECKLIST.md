# Scraper and scoring checklist

Your nine items, annotated with what is done, what I need from you, and what I
would add. Questions are numbered **[C-n]** so they are easy to answer piecemeal.

---

## Corrections applied

**Two-way normalization.** Fixed — batting is scored against the batter p99 and
pitching against the pitcher p99, and only the normalized scores are combined.
This overturns what I built earlier today (I had combined raw points and
normalized against the primary role). Scoring now emits one row per player-role,
and `combine_two_way` runs *after* `apply_benchmarks`. The primary role is the one
with the higher **normalized** score, since raw batting and pitching points are on
unlike scales. Worked example: a season scoring 123.3 as a batter and 81.7 as a
pitcher combines to `123.3 + 0.5 × 81.7 = 164.1`.

Your approach also removes the problem I was working around: there is no
`MLB_Two-Way` normalization group at all, so position players who pitch an inning
cannot pollute one.

**Per-player-game rates.** Already correct — the postseason denominator has always
been the player's own appearances, not his team's games. Now pinned by tests in
NFL, NBA and the postseason module, including the case of a player who missed a
playoff game his team played.

**Discrete playoff stats.** Understood: cumulative is fine provided **games played
is included**, since the rate needs its own denominator. That is now an explicit
requirement on every adapter.

---

## 1. NFL

Done: daily-updating source (nflverse `stats_player`), per-week rows, playoff rows
present and usable (`season_type` REG/POST), position-specific benchmarks over five
seasons.

- **[C-1] answered:** the NFL is **not** expanding yet — expected at some future
  point. When it does, scale regular-season counting stats only. The mechanism is
  the same one NHL needs now (see [C-5]), so building it there covers NFL later.

## 2. NBA

Done: ESPN adapter verified end to end, per-date rows, playoffs distinguished by
`season_type`, Play-In excluded from both phases, Backcourt/Frontcourt benchmarks
over the pool. 2026 measured at 273 dates / 30,853 rows / 705 players.

Nothing outstanding. The five-season backfill is the remaining step and is running.

## 3. MLB — BUILT, unverified; next to confirm

Both hosts are blocked from my sandbox, so nothing here has touched a live
response. The diagnostics that NBA and NCAA taught me to build up front are in
place from the start this time:

- **Two feeds, reported separately.** MLB Stats API for schedules (a whole season
  in one request), FanGraphs for leaderboards. A partial failure names which.
- **Four FanGraphs parameter shapes**, tried most specific first, with a shape
  that answers 200 and no rows treated as suspect rather than accepted — the same
  trap college softball sprang, where an empty season looks exactly like a real
  one with no error.
- **A browser-like header set**, since FanGraphs rejects unadorned clients.
- **Explicit scoring-column reporting**: the probe names any of `Off`, `Def`,
  `WAR` and the counting stats that failed to arrive, rather than leaving them to
  silently resolve as zero.
- **A fallback availability check.** MLB Stats API carries every counting stat
  but **not** Off, Def or WAR. If FanGraphs proves unreachable, substituting it
  is a scoring decision — dropping three components — not a source swap, so the
  probe reports whether the fallback exists without ever taking it automatically.

- **[C-2] Season end date — answered: 7/13/27**, tracking the MLB All-Star Game
  and shifting slightly each season. The drift is too small to re-base benchmarks
  for, which is noted in the config.
- **[C-3] Benchmark basis.** You offered two options and I have a recommendation:
  **use full MLB seasons.** A July→July league year contains almost exactly one
  full MLB season's worth of games, just split across two calendar seasons, so a
  full-season p99 is measuring the right quantity. The July fencepost is then only
  needed for the contract weighting at draft time, not for the scale.
- **[C-4] Year-one truncation.** For MLB I recommend **proration by games played**,
  not a bespoke window p99. The distinction is principled: proration is right when
  the sport plays *continuously* through the window, so a shorter window is simply
  fewer games at the same rate. It is wrong for golf, tennis and motorsport,
  where an August→July window contains a different *proportion of offseason* than
  a July→July one — which is why those get a bespoke window p99 instead. MLB is
  firmly in the first category.
  Rough figure: 8/21/26 captures the last ~20% of the 2026 season plus ~60% of
  2027 through mid-July, so ~80% of a season, inflating by ~1.25. Playoffs
  untouched, as you said.

## 4. NHL — BUILT and CONFIRMED

Skaters only. Goalies are still scored (the R script computes them) but hold no
roster slots and are excluded from normalization, so nothing downstream reads
them. 20 tests.

Probe is fully green: all four endpoints respond, every scoring column is
present, and 868 skaters score from 920 rows with MacKinnon top at 449.0.
Playoffs arrive as separate requests — 332 skater rows and 16 team rows — so the
discrete postseason split needs no disentangling.

Source is the NHL's own stats API, which `fastRhockey` wraps: one request per
season per endpoint, and `gameTypeId` separates regular season from playoffs, so
the discrete postseason split falls out of the request rather than needing to be
disentangled afterwards.

All sixteen qualifiers play a first round, so the bye rule from [C-9] does not
arise here.

- **[C-5] answered and built: scale the p99 to an 84-game pace.** Better call than
  mine — one multiplication per group, and one fewer step for anyone checking the
  arithmetic. `whul/scoring/schedule.py` holds the change (82 → 84, effective
  2026-27, factor 1.0244) and the NFL is registered as unchanged, so its
  expansion is a one-line addition when a date is known.
  **One wrinkle worth stating:** this works cleanly for players because their
  scoring is entirely counting stats. Team scoring is not — wins, overtime losses
  and goal differential scale with games, but a playoff berth, series wins and a
  division title do not. So team regular-season components are scaled at source
  and the achievement terms left alone, which is why `score_teams` takes the
  factor rather than having it applied to the finished benchmark.
- **COVID seasons are excluded from benchmark pools.** The probe reported
  `games_per_team [82]` for 2025, which prompted checking the rest of the window:
  NHL 2021 was **56 games**. Scaling that to 84 is a 1.5x extrapolation across a
  year that also had no crowds and division-only schedules, so the distortion is
  not only one of length — it is dropped rather than modelled, following the
  precedent `MLB_Players_Teams.R` set by filtering 2020. The NHL default window
  is now 2022-2026, and `IRREGULAR_SEASONS` records the same for NBA 2020-21,
  MLB 2020 and WNBA 2020, with the report printing any exclusion it applies.

## 5. NCAA (all five) — BUILT; four of five confirmed reachable

Scoring ported for all five and covered by 23 tests. Probes confirm NCAAM
(49 games, 49/49 conference coverage) and NCAAW (82 games, 82/82); NCAAF and NCAA
Baseball answer correctly but were out of season on the probe date. Division
filtering works — the men's probe found 3 opponents outside the 362 listed teams
(Lindenwood, Queens, Southern Indiana), exactly the reclassifying schools the old
games floor was meant to exclude.

Three findings from the probes:

- **NCAA Softball — path solved, filter removed.** It lives under the **baseball**
  sport path: `baseball/college-softball` returns 442 teams, while every
  `softball/...` variant answers 404. Its `groups=29` filter then returned zero
  events on a date a bare request shows 52 games on, so the filter excluded
  everything rather than narrowing a division; softball now sends no group
  filter, and relies on the eligible-teams list instead.
- **NCAAF's division filter cannot be fixed through ESPN.** The teams endpoint
  returns **760** for `groups` 80, 81, 90 *and* none — the parameter is simply
  ignored, so there is no value that yields the ~134 FBS programs. FCS opponents
  would therefore enter the benchmark pool. **This is a live correctness problem.**
  The scoreboard does respond to `groups` (25 events against 53 bare), but 25 is
  too few for a mid-November Saturday, so that filter is not FBS either.
- **Baseball's blank conference is harmless.** `conference_coverage 0/66` looked
  alarming but diamond scoring never reads conference — wins, run differential
  and series milestones only. The probe now says so explicitly rather than
  printing a fraction that implies a fault.

**The NCAA stats API is the better source for these leagues**, and is now
implemented alongside ESPN. Its decisive advantage is that division membership is
stated in the URL — `football/fbs`, `basketball-men/d1` — which is exactly what
ESPN refuses to express. Its parsed rows use the same column names as the ESPN
adapter, so the scoring modules work against either without a translation layer.
**Confirmed and now primary for all five NCAA leagues.** Football returned 54
games with **54/54 conference coverage and 108 distinct teams**; men's basketball
50 games, 50/50, 100 teams. Since the URL already restricts to the division,
every team in its results belongs there by construction — no separate
eligible-teams call is needed, and the football correctness problem is closed.

Conference arrives as a slug (`mac`, `cusa`) rather than an ESPN numeric id,
which is more legible and equally usable for the conference-wins and shared-title
terms.

Rate limits and third-party uptime remain the open risk; the project is
self-hostable and that is the right move before the season starts.

Results only — no player slots exist, so no box scores. That is one
scoreboard request per date, and the ESPN adapter already proven for NBA covers it.

- **[C-6] answered: keep the R scoring as written**, including the shared-title
  split. Built and tested. Conference affiliation is load-bearing — without it
  `score_football` returns nothing rather than silently scoring zero.
- NCAA Baseball and Softball are simpler: `reg_wins`, `run_diff × 0.05`, and flat
  series milestones (regional 5, super 6, CWS champion 8).
- **[C-7] answered:** results alone; seeding does not matter. No change needed.
- **Minimum-games filters removed.** They existed in the R scripts to drop
  non-Division-I opponents, which appear in the ledger with one or two games from
  the non-conference schedule. Filtering by actual division membership (from
  ESPN's teams endpoint) is exact, and no longer discards a genuinely short
  season.
- **Big-win threshold corrected.** The R script's `margin >= 9` was recycled from
  NFL. NCAAF now uses **13 against a conference opponent or in the postseason,
  20 out of conference** — a lower bar where the field is stronger.

## 6. Motorsports, golf, tennis

Not started, and **this is the largest data gap in the project**. In the R scripts
NASCAR, PGA and tennis all ran off hand-built local CSVs; only F1 had a live
source (Jolpica), and that host is blocked from my sandbox too.

- **[C-8] answered:** the historical CSVs are recoverable, and you have
  flashscore scrapers for tennis, but there is no live source for golf or
  motorsport. **ESPN may already solve this** — `Board.R` used its `golf/pga` and
  `racing/nascar-premier` endpoints, and the same scoreboard adapter now proven
  for NBA and NCAA should reach both, plus F1. Worth probing before building
  anything bespoke; if it works, historical CSVs are needed only for the
  five-year benchmark, not for live results.
- Bespoke window p99 is agreed for these three, and is tractable because all four
  competitions are event-based with dates — but only once each has a real source.

## 7. Soccer except MLS

Not started. League-specific p99 confirmed — each of EPL, La Liga, Serie A,
Bundesliga, Ligue 1 and NWSL normalizes against itself.

- **[C-9] answered:** league phase counts as competition proper; qualifying does
  not. **Byes score as though the team won the skipped round in a sweep** — this
  applies to every playoff competition, not just soccer, so it belongs in shared
  logic rather than each league's module.
  *Exception: tennis*, where a bye earns first-round points only if the player
  wins their second-round match. `Tennis_Players.R` already encodes this as
  `bye_bonus` gated on `is_first_win`.
- **[C-10] answered:** domestic cups stay in at their normal win value (4), with
  no postseason bonus, since every team qualifies.
- **[C-11] answered:** the two Intl Soccer slots may be filled by any combination
  of men's and women's national teams. The current config already matches this —
  one `Intl Soccer` category of 2, with Men's and Women's normalizing separately
  against their own benchmarks. Picking teams likely to play competitive matches
  during the league year is the manager's problem, not the app's.

## 8. MLS

Not started. 2027 sprint season scaled up to historical point values by games
played — the same proration machinery as [C-4], driven by an admin-entered
expected-games count.

## 9. WNBA and NWSL

Not started, not used this year. Needs the schedule shares to compute each
league's own `mult_year_n1`, since each captures a different proportion of its
season either side of the draft. Deferred until the 2027 draft.

---

## Additions I would make

**10. Asset identity across feeds — agreed, canonical IDs.** The hard part is not
the aliasing but the near-collisions: distinct people with identical or similar
names. Feed-native ids (ESPN athlete id, FanGraphs playerid, nflverse gsis id)
are the reliable anchor where available; names are only a fallback, and any
name-only match between two feeds should land in an admin review queue rather
than auto-merging. Same problem exists for teams, where "Miami" is two different
schools.

**11. Daily snapshot storage.** This is what makes cumulative scraping sufficient
for everything except MLB's postseason: store each day's cumulative figures, and
the progression graph and trade accrual both fall out of differencing snapshots.
It has to be running from day one — it cannot be reconstructed later.

**12. Staleness detection.** The dangerous scraper failure is not a crash, it is a
feed that quietly stops updating: standings freeze and look plausible. Each source
should record the date of its most recent data, with an alert when that falls
behind during an active season.

**13. Benchmark versioning.** Frozen benchmarks need to be a stored, versioned
artifact rather than something recomputed on the fly, so a mid-season formula fix
can be replayed without silently moving the scale under the standings.

**14. Olympics.** No slots this year, but 2028 will need one. Not urgent; worth a
placeholder so it is not a surprise.

---

## Suggested order

1. **NCAA (all five)** — one shared adapter on proven ESPN plumbing, results only,
   five leagues at once. Best value per hour of work.
2. **NHL** — the last major team-and-player league; unblocks [C-5].
3. **Soccer** — six leagues, one shape, but needs [C-9] and [C-10] answered.
4. **Motorsports / golf / tennis** — blocked on [C-8] until sources exist.
5. **Identity layer (10)** — before the feed count grows further.

Answering [C-2], [C-6] and [C-8] unblocks the most work.
