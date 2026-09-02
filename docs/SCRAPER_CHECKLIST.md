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

## 3. MLB

Built, unverified — both hosts are blocked from my sandbox.

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

## 4. NHL

Not started. Skaters only — goalies are excluded from normalization, matching
`All_Analysis.R` and your earlier decision.

- **[C-5] answered: scale the p99 itself to an 84-game pace**, rather than scaling
  every historical score and re-deriving. Better call than mine — the benchmark
  already excludes playoffs, so it is a single multiplication on one number per
  group, and a manager checking the arithmetic has one fewer normalization step
  to follow. Same mechanism will serve NFL when it expands.

## 5. NCAA (all five) — BUILT; four of five confirmed reachable

Scoring ported for all five and covered by 23 tests. Probes confirm NCAAM
(49 games, 49/49 conference coverage) and NCAAW (82 games, 82/82); NCAAF and NCAA
Baseball answer correctly but were out of season on the probe date. Division
filtering works — the men's probe found 3 opponents outside the 362 listed teams
(Lindenwood, Queens, Southern Indiana), exactly the reclassifying schools the old
games floor was meant to exclude.

**NCAA Softball rejects every parameter** (`groups` *and* `limit`), so the
scoreboard now tries request shapes progressively and reports which one the
endpoint accepted. Results only — no player slots exist, so no box scores. That is one
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
