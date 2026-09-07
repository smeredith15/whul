# International soccer: what a tournament is worth

Two-slot team category, **ten rostered assets** — three men's (England, France,
Spain) and seven women's (Brazil, Canada, England, France, Germany, Spain,
USA) — and no adapter yet. Every one of them reads zero today, correctly: see
[the calendar](#what-this-league-year-actually-holds).

Design recorded 2026-09-04 from the league admin; formats and data verified
2026-09-07. Nothing below is implemented.

Written against this design rather than the club soccer one, because the
problem is different: a club plays thirty-eight league matches a season, and a
national team plays a handful of tournaments across a four-year cycle.

---

## The problem

The scoring year decides which tournament a team can even enter, and the
tournaments are not worth the same. Take a European men's team:

| Year | What it could win |
|---|---|
| 2026 | the World Cup |
| 2027 | the Nations League Finals |
| 2028 | the Euros |

In each case it might win every match by several goals — an identical playing
record. Those three seasons must **not** be worth the same:

> **World Cup > Euros > Nations League**

So a match's worth depends on the tournament it is played in, the way a tennis
match's worth depends on the tier of the event.

---

## What the previous attempt did, and where it breaks

`r-scripts/Intl_Soccer.R` scored every match 3 for a win, 2 for a shootout win,
1 for a draw or shootout loss, 0 for a loss, then multiplied by a stage
factor — qualifier 1.0, group 1.5, knockout 2.0 — and summed by team and
calendar year over 2017-2024.

It is the right skeleton. Three things in it do not survive contact with the
formats:

**1. It has no prestige ladder at all.** A Nations League win and a World Cup
win are both 6 points. That is the thing this document exists to fix.

**2. Stage is inferred from match order** — `match_seq <= 3` is the group
stage, everything after is a knockout. That is right for a four-team group and
wrong wherever the group is not four teams:

* the UEFA Nations League league phase is **six** matches, so matchdays 4-6 are
  scored as knockout football at 2.0x;
* the 2025 Copa América Femenina had two groups of five, so **four** group
  matches, and the fourth is scored as a knockout;
* the 2025-26 CONMEBOL Women's Nations League is a nine-team single round
  robin — **eight** matches and **no knockout stage at all** — so five of every
  team's eight league matches are scored as knockouts.

**3. Longer tournaments pay more for the same achievement.** Per-match scoring
means a 24-team continental championship (seven matches to win it) pays a
champion more than a 16-team one (six). The rung stops being a rung, which is
the one thing the admin said must not happen.

Everything else in it is worth keeping, including the shootout rule.

---

## The formats, as verified

Champion's matches is the whole run: group stage plus every knockout round.

| Competition | Confederation | Field | Group | Knockouts | Champion plays |
|---|---|--:|--:|---|--:|
| FIFA World Cup (2026-) | FIFA | 48 | 3 | R32, R16, QF, SF, F | **8** |
| FIFA Women's World Cup | FIFA | 32 | 3 | R16, QF, SF, F | 7 |
| UEFA Euro | UEFA | 24 | 3 | R16, QF, SF, F | 7 |
| UEFA Women's Euro | UEFA | 16 | 3 | QF, SF, F | 6 |
| Copa América | CONMEBOL | 16 | 3 | QF, SF, F | 6 |
| Copa América Femenina (2025) | CONMEBOL | 10 | **4** | SF, F | 6 |
| Africa Cup of Nations | CAF | 24 | 3 | R16, QF, SF, F | 7 |
| Women's Africa Cup of Nations | CAF | 12 | 3 | QF, SF, F | 6 |
| AFC Asian Cup | AFC | 24 | 3 | R16, QF, SF, F | 7 |
| AFC Women's Asian Cup | AFC | 12 | 3 | QF, SF, F | 6 |
| CONCACAF Gold Cup | CONCACAF | 16 | 3 | QF, SF, F | 6 |
| CONCACAF W Gold Cup | CONCACAF | 12 | 3 | QF, SF, F | 6 |
| CONCACAF W Championship (2026) | CONCACAF | 8 | 3 | QF, SF, F | ~6 |
| UEFA Nations League A (2026-27) | UEFA | 16 | **6** | QF over two legs, SF, F | **10** |
| CONCACAF Nations League A (2026-27) | CONCACAF | 16 | **4** or 0 | QF over two legs, SF, F | 4-9 |
| CONMEBOL Women's Nations League | CONMEBOL | 9 | **8** | none | 8 |

**Six to ten matches to win a trophy**, and one competition with no knockout
stage whatsoever. Any scheme that pays per match pays these unequally for the
same achievement.

Two structural notes that matter more than they look:

* **The Nations Leagues and the qualifiers are merging.** The CONMEBOL Women's
  Nations League *is* CONMEBOL's 2027 World Cup qualifying, and UEFA's women's
  World Cup qualifying is played in the Women's Nations League format. Any rule
  that treats "a Nations League" and "qualifying" as separate rungs has to say
  which one those are.
* **The asymmetry the admin named is real.** UEFA and CONCACAF run a Nations
  League; CAF and AFC do not, so their teams have nothing but qualifiers
  between continental championships. Over a four-year cycle a European team can
  enter three competitions and an African team two.

---

## The scheme, as settled

Decided by the league admin 2026-09-07; the machinery below is built to it.
`scripts/intl-soccer-ladder.py` implements it against the whole history and
prints what comes out.

### 1. A competition is its qualifying and its finals together

Not two events. **World Cup qualifying is on the World Cup rung**, so it is
worth more than Euro qualifying, which is right — and a team that fails to
qualify has still spent its year on the World Cup rung, which is what stops
"miss the World Cup, get your other points upscaled" from being a strategy.

### 2. A match is scored exactly as a club match is

The club soccer scale, unchanged, so a national team's 2-0 and a club's 2-0 are
worth the same before the tournament ladder touches them:

| | |
|---|--:|
| win | 3 |
| shootout win | 2 |
| draw | 1 |
| shootout loss | 1 |
| loss | 0 |
| **+ won by two or more** | +1 |
| **+ conceded nothing** | +1 |

A shootout win is deliberately not a win for the margin bonus — the match
itself was drawn, so there is no margin to be big. The clean sheet is not gated
on the result, because a side that conceded nothing cannot have lost in normal
time, so it reaches exactly wins to nil and goalless draws.

**Friendlies do not count**, nor the invitational cups that are friendlies
under another name — SheBelieves, the Algarve Cup, the Tournoi de France, the
Arnold Clark Cup, the Pinatar Cup, FIFA Series. **Nor the Olympics**, which the
planned Olympics category will carry: 58 Olympic matches and 20 in Olympic
qualifying for the rostered women's teams since 2016, deliberately left here.

### 3. Two multipliers, and the rung ladder is shallow

| Stage of a match | | | Rung of a competition | |
|---|--:|---|---|--:|
| qualifying | ×1 | | nations league | ×1 |
| group | ×2 | | federation cup | ×1.5 |
| knockout | ×3 | | world cup | ×2 |

The rung ladder is 2 : 1.5 : 1 rather than 3 : 2 : 1 on purpose. See §5.

### 4. A competition pays a ceiling, divided by the champion's own path

```
team points = ceiling x (its units / path_max)
path_max    = 5 x (its own qualifiers x 1 + group x 2 + champion's knockouts x 3)
```

Five, not three, because the ceiling has to be a real ceiling: a *perfect* run
now means winning every match by two or more to nil, and nothing can exceed its
own competition.

Winning the Gold Cup in six matches and AFCON in seven are therefore worth the
same, and the 2026 World Cup's new Round of 32 changes nothing about what a
World Cup is worth. Qualifying length is the team's own, because a CONMEBOL
campaign is eighteen matches and a CAF one is six and both are the same
achievement.

**Stage is inferred per edition, not assumed.** `G` is the fewest matches any
team played — a side eliminated in the group stage plays exactly the group —
and `K` is what the longest run adds. The script prints every edition's
inferred shape for eyeballing against the format table above, because a
withdrawal would drag the minimum down and nothing else would say so.

### 5. A season is its best competition plus half of everything else

The two-way rule the MLB scorer already uses for a player who bats and pitches
— the primary scores whole, the secondary contributes half — extended to
however many competitions a year holds. 674 team-seasons in the pool have two
competitions and 74 have three.

Summing them instead put the United States' 2018-19 at **211 on the 0-100
scale**: they won the World Cup and the championship that qualified them for it
inside one league year. The fold and the shallower rung ladder bring that to
**165**.

### 6. Whether to scale up a fallow year

```
multiplier = top ceiling / the best rung the team actually played that season
```

A Nations League year pays ×2, a federation-cup year ×1.33, a World Cup year
×1. Without it a European men's team's 2026-27 — a Nations League and the first
Euro qualifiers, and nothing else — scores half what its World Cup year does.

**It also flattens the top, which is the opposite of what a flat percentile
suggested.** Lifting every fallow year lifts far more of the pool than it lifts
the best seasons — a World Cup winner is already on the top rung and gets ×1 —
so the benchmark rises and the outlier comes down with it. The United States'
2018-19 goes from 165 to **133**, and nothing in sixteen pool-years clears 150.

### 7. Then the ordinary machinery

These are league points, not scores. The 0-100 scale comes from dividing by the
pool's 99th percentile as everywhere else, **so a perfect run lands well above
100** — which is the admin's stated requirement and the reason the ceiling is
not itself the scale.

## What this league year actually holds

The 2026-27 league year runs **21 August 2026 to 13 July 2027**. Verified
against the match ledgers:

**The men's World Cup was played 15 June - 19 July 2026** — over five weeks
before the league year opened. England, France and Spain took nothing from it,
and cannot. That leaves them the **UEFA Nations League** (league phase 24
September - 17 November 2026, quarter-finals March 2027, Finals 9-13 June 2027)
and the start of **Euro 2028 qualifying** from 25 March 2027. Nothing else — so
their multiplier this year is x1.5, not x3.

The women's 2027 World Cup qualifying ran March-June 2026, also before the
window. Inside it are the play-off phase, the 2026 CONCACAF W Championship (27
November - 5 December 2026, for Canada and the USA), and the World Cup itself.

> ### International tournaments are the exception to the end of the league year
>
> **Decided 2026-09-07.** The 2027 Women's World Cup runs 24 June - 25 July
> 2027 and the league year ends 13 July, so its quarter-finals, semi-finals and
> final fall outside. The 2027 Africa Cup of Nations (19 June - 17 July) is cut
> the same way.
>
> The rule is that **a tournament is scored whole into the league year it began
> in**, however long after the year's end it finishes. Even where the next
> draft has happened before the final, those points go to the 2026-27 rosters.
>
> So the edition key is the league year of a tournament's first match — not the
> calendar year, and not the match date. That is also what holds a tournament
> together internally: keyed by calendar year, the 2025 Africa Cup of Nations
> split across new year into two half-tournaments, one of which had its group
> stage inferred as a single match, and both Nations Leagues broke into a
> league phase in one year and a four-team finals in the next.

---

## What the scheme does to real seasons

Run `scripts/intl-soccer-ladder.py`. Every figure here is out of it, and the
benchmark comes from `whul.normalize.compute_benchmarks` rather than a
percentile taken here — **the pool is truncated to the top 40 of each season
before the 99th percentile is taken**, so 100 means the best of a draftable
field rather than of every nation that played a competitive match. Two full
four-year cycles, 2018-19 to 2025-26, 320 pooled seasons per gender.

That truncation is not a detail. Against a flat percentile over all 3,023
seasons the numbers look about 40% larger and the conclusions invert.

### Does anything score far above 100?

| | benchmark | best season | >100 | >125 | >150 | >200 |
|---|--:|--:|--:|--:|--:|--:|
| **summed, no fold** | | | | | | |
| Men's | 164.4 | 115.6 (Spain 25) | 4 | 0 | 0 | 0 |
| Women's | 146.8 | **211.2** (USA 18) | 4 | 1 | 1 | 1 |
| **best + half the rest** | | | | | | |
| Men's | 136.6 | 120.4 (Spain 25) | 4 | 0 | 0 | 0 |
| Women's | 142.5 | **164.9** (USA 18) | 4 | 1 | 1 | 0 |
| **...and fallow years upscaled** | | | | | | |
| Men's | 163.6 | 126.3 | 4 | 1 | 0 | 0 |
| Women's | 177.0 | **132.8** | 4 | 1 | 0 | 0 |

Four seasons of 320 clear 100 in every variant, which is what a 99th percentile
means. What changes is the tail: summing gives one season at 211, the fold
brings it to 165, and the fold plus the lift brings it to 133 with nothing at
all above 150.

The middle of the distribution is nowhere near 100 — the women's pool has a
median of 26 and a 90th percentile of 63; the men's 40 and 68.

### The highest seasons in the pool, all teams

```
   Men's                                      Women's
   2025  Spain           164.4 raw   120.4    2018  United States  235.0   164.9
   2020  United States   155.0       113.5    2018  New Zealand    150.0   105.3
   2022  Mexico          149.0       109.1    2021  England        149.9   105.2
   2018  Qatar           137.0       100.3    2021  Brazil         143.6   100.7
   2025  France          134.8        98.7    2025  Japan          138.0    96.8
   2023  Argentina       133.3        97.6    2021  United States  137.5    96.5
   2022  United States   130.7        95.7    2018  Canada         127.5    89.5
   2024  Mexico          130.6        95.6    2021  South Africa   112.1    78.7
```

Spain's men won the 2026 World Cup dropping only a group draw to Cape Verde,
which is the top men's season on the board at 120. England's women won Euro
2022 six from six, four of them to nil including an 8-0, and take 105 — 122 of
the 150 a federation cup can pay, with no qualifying term at all because they
were hosts.

### The rostered teams, normalized

```
   Men's           2018 2019 2020 2021 2022 2023 2024 2025
   England           30   20   76   32   48   41   40   87
   France            22   17   64   27   74   41   41   99
   Spain             23   16   59   46   52   77   37  120

   Women's         2018 2019 2020 2021 2022 2023 2024 2025
   Brazil            28    0    0  101   22    0   75    0
   Canada            89    0    0   75   16    0    0    0
   England           69    0    0  105   64   34   57   26
   France            51    9   25   48   48   52   77   30
   Germany           55   18   17   65   22   55   69   42
   Spain             23   10   23   40   78   63   78   68
   United States    165    0    0   96   34    0    0    0
```

### What the numbers expose

**Two rostered teams have four blank years in a row.** Canada and the United
States score nothing from 2022 to 2025. CONCACAF's women play a biennial
championship and almost nothing else that is not a friendly or the Olympics —
and the 2024 W Gold Cup they did play is missing from the ledger altogether.
Whatever the ladder says, half the CONCACAF women's calendar is in the excluded
pile.

**Brazil's women score nothing in 2025-26 either**, and that one is nobody's
bug: Brazil host the 2027 World Cup, qualify automatically, and are the one
CONMEBOL nation absent from the nine-team Nations League that *is* the
qualifying. A World Cup host plays no competitive football for a year.

**The men's 2018-19 and 2019-20 are the flattest years on the board**, 16 to 30
for teams that reach World Cup finals. That is the fallow-year problem in its
natural habitat, and the row to look at when deciding whether to lift.

## What must come out equal

**Equivalent tournaments across federations are equivalent.** The Asian Cup,
the Copa América, AFCON and the Euros are one rung, and a team winning its
continental championship should score the same whichever continent it is in —
even though the formats differ in length, group size and knockout depth. The
purse mechanism is the answer to that clause.

The same equivalence holds between the men's and women's game: the Women's
World Cup is the World Cup rung.

**One benchmark for the whole category, not one per federation.** Splitting
UEFA from CONMEBOL from CAF would leave each pool far too small to draw a 99th
percentile from — the same reason F1's 20-car grid makes its benchmark close to
the single best season. Intl Soccer stays one normalization group.

---

## The pool is an open question too

Recorded 2026-09-04: *"This will definitely involve re-benchmarking
international soccer, the question is how. In terms of what tournaments get
what points, and in terms of what is our pool."*

Not splitting by federation is settled. What the pool *is* is not. The
buffer-pool machinery assumes a league of comparable competitors playing
comparable seasons, and international soccer has neither: a team's
opportunities depend on which year of the cycle it is, and on whether it
qualified at all.

The opportunity division above is what makes a pool possible: once every team's
figure is a share of what was available to it, seasons from different cycle
years are commensurable and the pool can be every national team that played a
competitive match. Without it the pool is a mixture of World Cup years and
fallow years and its 99th percentile means nothing in particular. So §3 and the
pool are one decision, not two.

---

## Where the data comes from

**Settled, and reachable.** The R script's sources are alive and answer from
this sandbox, which nothing else in this project does:

| | rows | through |
|---|--:|---|
| `martj42/international_results` | 49,547 | 2026-08-26 |
| `martj42/womens-international-results` | 11,650 | 2026-06-10 |

`date, home_team, away_team, home_score, away_score, tournament, city,
country, neutral`, plus a separate `shootouts.csv` for the penalty rule. Served
from raw.githubusercontent, the same host nflverse uses.

Three cautions, all of them the silent kind:

* **The women's file lags.** It stops at 10 June 2026 against the men's 26
  August. Their seasons genuinely differ, but a stale file and a quiet season
  look identical, and seven of ten slots here are women's. The adapter has to
  report the ledger's own last date rather than infer a quiet week.
* **The tournament name is the only key, it is not consistent, and it
  changes.** Men file the Gold Cup as `Gold Cup`; women file it as `CONCACAF
  Gold Cup`. Worse, the women's African championship appears under four names
  across its history — `African Championship` and `African Championship
  qualification` to 2014, `African Cup of Nations` and `African Cup of Nations
  qualification` from 2016, and then **`Africa Cup of Nations qualification`
  from 2025**, the current spelling and the one the R script's `African Cup`
  pattern does not match.

  That is the project's own failure mode in miniature: a pattern written
  against history keeps matching history, returns a full-looking answer, and
  drops the season being played. Every competition needs an explicit list of
  the strings that mean it, and any name that matches nothing needs reporting
  rather than dropping.
* **82 distinct tournament names in the men's file, 89 in the women's.** A
  permissive regex sweeps in the Island Games and the CONIFA World Football
  Cup. The ladder is therefore `whul/data/intl_tournaments.csv`, an allow-list
  of 39 exact strings, and the script prints every name it did not match so a
  competition that should score cannot go missing quietly.
* **The 2024 CONCACAF W Gold Cup is not in the ledger at all.** Its
  qualification is there, 87 matches of it; the tournament itself is not — not
  one match, though the United States won it and Brazil were runners-up. Three
  of the ten rostered teams played in it. The `CONCACAF Gold Cup` name in the
  women's file holds only the old 2000-2010 competition. This is a hole in the
  source, not in the ladder, and it needs either a second source for CONCACAF
  or an upstream fix before those slots can be trusted.

---

## Still to decide — the admin's, not mine

Settled 2026-09-07: the league-year exception; the club soccer match scale with
its clean-sheet and margin bonuses; stage ×1/×2/×3 and rung ×1/×1.5/×2; the
ceiling divided by the champion's path; the best competition whole plus half of
everything else; no friendlies, no invitational cups, no Olympics; and
normalizing to history so a good season clears 100 without the scale breaking.

What is left:

1. **Whether to upscale fallow years.** It fixes the flat years *and* pulls the
   top season from 165 to 133, which is not the trade it looked like against a
   flat percentile. The cost is that a Nations League year and a World Cup year
   become closer than the rung ladder says they are.
2. **The eight-year benchmark window.** Every other league uses five seasons.
   Five here holds one World Cup and either one continental championship or
   two, so the pool changes character with its start year. Two full cycles is
   the natural unit and is what the R script used.
3. **CONCACAF's missing 2024 W Gold Cup**, and whether this source covers the
   CONCACAF women's calendar well enough for Canada and the USA to be scored
   fairly. Four blank years in a row is the symptom, and the admin has no lead
   on where the data might be either. Options, none free: a second source for
   CONCACAF, an upstream contribution to the ledger, or entering those
   tournaments by hand.
4. **The Nations Leagues' internal divisions.** The ledger does not record
   whether a match is League A, B or C, so an edition's inferred `G` is the
   smallest league's and its `K` the largest's — UEFA men's 2024-25 comes out
   `G=4 K=6` where League A is `G=6 K=4`. An ~8% error on one competition, and
   England, France and Spain are all in it, so it needs a small per-edition
   override table before this goes live.
