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

### 2. Two ladders of 1-2-3

| Stage of a match | | | Rung of a competition | |
|---|--:|---|---|--:|
| qualifying | 1 | | nations league | 1 |
| group | 2 | | federation cup | 2 |
| knockout | 3 | | world cup | 3 |

A result is the R script's own scale, kept: **3** for a win, **2** for a
shootout win, **1** for a draw or shootout loss, **0** for a loss.

**Friendlies do not count**, and neither do the invitational cups that are
friendlies by another name — the SheBelieves Cup, the Algarve Cup, the Tournoi
de France, the Arnold Clark Cup, FIFA Series. That is 476 friendlies and ~150
invitational matches for the rostered teams since 2015.

### 3. A competition pays a purse, divided by the champion's own path

```
team points = purse x (its units / path_max)
path_max    = 3 x (its own qualifiers x 1 + group x 2 + champion's knockouts x 3)
```

So winning the Gold Cup in six matches and AFCON in seven are worth the same,
and the 2026 World Cup's new Round of 32 changes nothing about what a World Cup
is worth. Qualifying length is the team's own, because a CONMEBOL campaign is
eighteen matches and a CAF one is six and both are the same achievement.

**Stage is inferred per edition, not assumed.** `G` is the fewest matches any
team played — a side eliminated in the group stage plays exactly the group —
and `K` is what the longest run adds. The script prints every edition's
inferred shape for eyeballing against the format table above, because a
withdrawal would drag the minimum down and nothing else would say so.

### 4. A fallow year is scaled up

```
multiplier = top purse / the best rung the team actually played that season
```

A Nations League year pays x3, a federation-cup year x1.5, a World Cup year
x1. Without it a European men's team's 2026-27 — which is a Nations League and
the first Euro qualifiers, and nothing else — scores a third of what its World
Cup year does, and the category goes quiet two years in three.

### 5. Then the ordinary machinery

The purses are league points, not scores. The 0-100 scale comes from dividing
by the pool's 99th percentile as everywhere else, **so a perfect run lands well
above 100** — which is the admin's stated requirement and the reason the purse
is not itself the scale.

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

Run `scripts/intl-soccer-ladder.py`. Every figure here is out of it.

It reproduces results anyone in the league can check. England's women won Euro
2022 with six wins from six and take the **full 200** federation purse, plus 86
from World Cup qualifying the same year. Spain's men won the 2026 World Cup
dropping only a group draw to Cape Verde and score **278 of 300**. The United
States' women won both the 2019 World Cup and the 2018 CONCACAF Championship
that qualified them for it, and score **500** — two purses, because a season is
the sum of the competitions in it and taking two of them whole is meant to beat
taking one.

The rostered teams, raw purse shares by league year:

```
   M          2017 2018 2019 2020 2021 2022 2023 2024 2025
   England     139   63   38  200   71  122  108   77  224
   France      214   55   38  148   80  210   90   62  226
   Spain        89   57   33  155  109  123  185   72  278

   W          2017 2018 2019 2020 2021 2022 2023 2024 2025
   Brazil      200   67    0    0  200   44    0  167    0
   Canada        0  217    0    0  150   44    0    0    0
   England      62  162    0    0  286  171   85  160   62
   France         0  150   17   46  152  114  116  167  101
   Germany      58  127   35   35  179   50  113  148  103
   Spain        69   54   20   43  104  208  133  210  142
   United S      0  500    0    0  200   72    0    0    0
```

**The benchmark, and the requirement it has to meet:**

| | pool | p99 | a perfect World Cup run scores |
|---|--:|--:|--:|
| raw purse shares | 3,023 team-seasons | 199.9 | **150.0** |
| with fallow-year upscaling | 3,023 | 249.0 | **120.5** |

Both clear 100, which is the requirement. Upscaling costs a third of the
headroom, because lifting every fallow year lifts the 99th percentile with it.
Worth knowing before the multiplier is fixed, and an argument for a gentler
lift than the full x3 if the headroom matters more than the flat years.

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
qualifying. A World Cup host plays no competitive football for a year. It also
makes their World Cup cheaper to max out than a team that had to qualify —
their `path_max` carries no qualifying term at all.

**The Olympics are the one real exclusion question.** The rostered women's
teams have played 58 Olympic matches since 2016 and 20 more in Olympic
qualifying. Women's Olympic football is a senior tournament, unlike the men's
U-23 competition, so it is not a friendly. It is left out here only because
WHUL plans a separate Olympics category and double-counting would be worse than
the gap.

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

Settled 2026-09-07: the league-year exception, the 1-2-3 stage and rung
ladders, purse-by-path-max, no friendlies, and normalizing to history so a
perfect run clears 100.

What is left:

1. **The strength of the fallow-year lift.** The full x3 works and costs a
   third of the headroom above 100 (150.0 falls to 120.5). A gentler lift keeps
   more of it. Both are computed; the choice is which trade is wanted.
2. **The Olympics.** 58 matches for the rostered women's teams since 2016.
   Excluded here on the assumption that the planned Olympics category will
   carry them, which is worth confirming rather than assuming.
3. **CONCACAF's missing 2024 W Gold Cup**, and more generally whether the
   CONCACAF women's calendar is well enough covered by this source for Canada
   and the USA to be scored fairly. Four blank years in a row is the symptom.
4. **The Nations Leagues' internal divisions.** The ledger does not record
   whether a match is League A, B or C, so an edition's inferred `G` is the
   smallest league's and its `K` the largest's — the UEFA men's 2024-25 edition
   comes out `G=4 K=6` where League A is `G=6 K=4`. It is an ~8% error on one
   competition and it needs a small per-edition override table. No rostered
   team is in CONCACAF's men's Nations League; England, France and Spain are in
   UEFA's, so this one does bite.
5. **Whether a shootout win stays worth 2.** Kept from the R script; never
   challenged, only inherited.
