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

## 3. MLB — CONFIRMED, single source

**MLB Stats API works**: 2,481 games for 2025 with the postseason correctly typed
(R 2434, D 18, F 11, L 11, W 7) and all 30 teams, plus 765 hitting and 873
pitching advanced lines. Team scoring, the contract engine and player scoring are
all served by this one host.

**FanGraphs is unavailable and no longer needed.** It answers 403 to every
parameter shape, cookie warming did not clear it, and it blocks the target
machine as well as the sandbox — so datacenter IPs generally, meaning a
production scraper was never going to work.

**The MLB Stats API supplies everything instead.** Its `sabermetrics` group
returns the full fWAR decomposition — `batting`, `baseRunning`, `fielding`,
`positional`, `replacement`, `war` — across 765 hitting and 873 pitching rows.
FanGraphs defines `Off` as batting plus base running and `Def` as fielding plus
the positional adjustment, so **those two columns are reconstructed exactly, not
approximated**. Pitcher WAR arrives directly.

Scoring is therefore whole: `INCLUDE_ADVANCED_METRICS` stays on, no components
are dropped, and one host serves the schedule, the counting stats and the
advanced metrics.

Confirmed live: 765 hitting and 873 pitching lines, every scoring column present
on both sides, 1,391 scored role rows.

Three traps caught on the way:

- **Innings are thirds, not decimals.** `200.1` means 200 and one third. Read as
  a decimal it understates by a factor of three, and at 7.4 points per inning
  that is worth about 1.7 points per pitcher — small individually, systematic
  across the league.
- **Cache keys ignored request parameters.** That is why `stats_api_hitting`
  still reported 145 rows after the fix to request the full player pool: the
  qualified-only response was still cached. Keys now hash the parameters, so
  changing a request changes what is cached. Confirmed fixed — the same call now
  returns 765.
- **The probe expected `is_two_way` too early.** My own regression from the
  two-way refactor: `score_players` emits one row per player-*role*, and the
  column only exists after `combine_two_way`, which runs post-normalization. The
  probe now follows that order and reports the fold, so it exercises the
  per-role normalization rather than assuming it.

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

## 7. Soccer except MLS — SCORING BUILT, source next

League-specific p99 confirmed — each of EPL, La Liga, Serie A, Bundesliga,
Ligue 1 and NWSL normalizes against itself. 21 tests.

`whul/scoring/competition.py` places every match before scoring it. Qualifying is
tested **before** the tier patterns, because a qualifying tie carries its parent
competition's name — checking tiers first would score "Champions League
Qualifying" as the Champions League. The knockout play-off is exempted, since it
reads like a qualifying round but sits inside the competition, between the league
phase and the round of 16.

**[C-12] answered: appearance points are per game.** The R script's
`pts_minutes = ifelse(Min >= 60, 2, 1)` tested *season-total* minutes, awarding
2 points for an entire year — a per-game rule applied to aggregate data, the same
class of bug as the NCAAF blowout threshold. Now 2 for a 60-minute appearance,
1 for a shorter one: 64 points across a 30-start season against 2 under the
literal reading, roughly the value of a dozen goals.

Where per-match minutes are available they are applied exactly. Season feeds
carry only totals, so starts and substitute outings approximate the rule there —
a starter withdrawn at 50 minutes scores 2 rather than 1. The imprecision is
documented rather than hidden, and disappears once per-match data is in use.

**Source built and probed.** ESPN keys each competition separately, so the loader
gathers a league's own fixtures *plus* its domestic cups and the three European
competitions. That is what makes the tiers mean anything: restricted to league
matches every win is worth three points and the Champions League premium never
appears.

**The probe caught a real scoring bug.** `competition_labels` came back as
`['epl']` — the bare key — because ESPN returns the competition's display name at
the top of the scoreboard response, not on each event, and the code read it
per-event. Harmless for the Premier League, which falls through to the league
tier correctly, but **five of the six domestic cup keys match no name pattern and
would have scored 4-point cup wins as 3**: `facup`, `efl_cup`, `copadelrey`,
`coppaitalia`, `coupedefrance`. The European keys survived only because their
abbreviations happen to appear in the patterns — luck, not design.

Classification now works from the **competition key**, which the app chooses when
making the request and so cannot arrive absent or worded unexpectedly; the round
name is used only to spot qualifying ties. The display name is also extracted
from the right place now, so labels are informative again.

**Two further bugs the corrected labels exposed:**

- **Bare ordinal rounds were treated as qualifying.** The pattern matched
  "1st round|2nd round|3rd round" to catch UEFA qualifiers — but those are the
  FA Cup's *proper* round names, so a legitimate cup tie would have been dropped
  entirely. ESPN happened to write "third round" in the probe; nothing
  guaranteed it. UEFA always says "qualifying" or "play-off round", so matching
  only those is both sufficient and far safer.
- **MLS and NWSL postseasons scored as regular-season wins.** The R script's
  `case_when` groups `Play-off|Playoff` with the Champions League at 5 points,
  ahead of the cup line. "MLS Cup Playoffs" contains "Cup", so testing cups
  first scored a postseason tie at 4. A `DOMESTIC_POSTSEASON` tier now sits in
  the same position the R script puts it.

All four probes classify correctly: UCL `champions_league (5)`, FA Cup
`domestic_cup (4)`, EPL and MLS regular season `league (3)`, MLS Cup Playoffs
`domestic_postseason (5)`.

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
