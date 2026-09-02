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

- **[C-1] Schedule expansion.** When does the 18th game land, and should historical
  benchmarks be rescaled to an 18-game basis? My proposal: scale historical
  *counting* stats by `18/17` for benchmark purposes only — never live scoring —
  and leave achievement-style team points (division title, playoff appearance)
  untouched, matching the proration rule already agreed for shortened seasons.
  Team scoring needs the same treatment, since wins and point differential both
  scale with games.

## 2. NBA

Done: ESPN adapter verified end to end, per-date rows, playoffs distinguished by
`season_type`, Play-In excluded from both phases, Backcourt/Frontcourt benchmarks
over the pool. 2026 measured at 273 dates / 30,853 rows / 705 players.

Nothing outstanding. The five-season backfill is the remaining step and is running.

## 3. MLB

Built, unverified — both hosts are blocked from my sandbox.

- **[C-2] Season end date.** You wrote **7/17/27** here but **~7/13/27** earlier.
  Which is the fencepost? It sets the season window and every proration derived
  from it.
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

- **[C-5] 84-game expansion.** This happens *this* season, so unlike NFL it is not
  hypothetical. Scale five years of historical benchmarks by `84/82` (≈2.4%), or
  leave them on an 82-game basis and accept scores running slightly high? I lean
  toward scaling, for the same reason as [C-1].

## 5. NCAA (all five)

Not started. Results only — no player slots exist, so no box scores. That is one
scoreboard request per date, and the ESPN adapter already proven for NBA covers it.

- **[C-6] NCAAF needs more than results.** `NCAAF_Players_Teams.R` scores
  `conf_wins`, `reg_season_champ` (split `6 / tie_count` among co-champions) and
  `conf_title_win` — all of which need conference affiliation and a
  conference-game flag, not just scores. ESPN exposes both, but confirm you want
  the shared-title split kept as the R script has it.
- NCAA Baseball and Softball are simpler: `reg_wins`, `run_diff × 0.05`, and flat
  series milestones (regional 5, super 6, CWS champion 8).
- **[C-7]** NCAAM/NCAAW: confirm tournament rounds are scored off results alone, or
  tell me if seeding matters.

## 6. Motorsports, golf, tennis

Not started, and **this is the largest data gap in the project**. In the R scripts
NASCAR, PGA and tennis all ran off hand-built local CSVs; only F1 had a live
source (Jolpica), and that host is blocked from my sandbox too.

- **[C-8]** Do you still have those CSVs, and do you know their provenance? Even
  knowing they came from a specific Kaggle set or an FBref export would save a lot
  of guessing.
- Bespoke window p99 is agreed for these three, and is tractable because all four
  competitions are event-based with dates — but only once each has a real source.

## 7. Soccer except MLS

Not started. League-specific p99 confirmed — each of EPL, La Liga, Serie A,
Bundesliga, Ligue 1 and NWSL normalizes against itself.

- **[C-9] Competition stages.** Qualifying rounds are excluded and competition
  proper counts. I need the boundary stated per competition: for the current UCL
  format, does the league phase count as proper (I assume yes)? And is the
  play-off round between league phase and Round of 16 in or out?
- **[C-10] Domestic cups.** `Club_Soccer.R` awards 4 points for a cup win (FA Cup,
  Copa del Rey, DFB-Pokal, Coppa Italia). Are those still in, and do they carry a
  postseason bonus or only their win value?
- **[C-11] International soccer.** Men's and Women's national teams are separate
  team slots. Is that inside this item or its own workstream? The Women's World
  Cup rule (counting entirely to 2026-27 even if it runs past the draft) is
  already recorded.

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

**10. Asset identity across feeds.** Not on your list and it will bite. The same
person is "Shohei Ohtani", "Ohtani, Shohei" and "S. Ohtani" depending on the feed,
and a mismatch silently drops a rostered player to zero. Needs a canonical id per
asset, an alias table, and an admin queue for unresolved names. Worth building
before six more feeds arrive rather than after.

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
