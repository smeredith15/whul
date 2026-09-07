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

## The proposal

Three mechanisms, each aimed at one of the three problems. The mechanisms are
mine to build; **every number in them is the admin's to set** — the tables
below carry placeholder values, chosen to be legible rather than right.

### 1. A tournament is worth a purse, not a rate

Each competition gets a **purse**: what a champion takes for winning every
match of it. Not points per match.

| Rung | Purse | Competitions |
|---|--:|---|
| World | 1000 | World Cup, Women's World Cup |
| Continental | 600 | Euro, Copa América, AFCON, Asian Cup, Gold Cup, and every women's equivalent |
| Nations League | 300 | UEFA, CONCACAF, CONMEBOL Women's |
| Qualifying | 200 | World Cup and continental qualifying |

This is the tennis idea already in the codebase: the tables are built so every
tier's column sums to its face value. Winning the Gold Cup and winning AFCON
are worth the same, and the fact that AFCON takes an extra match to win stops
mattering.

### 2. Within a tournament, the purse is divided by the champion's own path

Keep the R script's match scoring — 3 / 2 / 1 / 0, times a stage multiplier —
but treat it as **shares, not points**. For each edition:

```
champion_max = the raw total for winning every match on the champion's path
scale        = purse / champion_max
team points  = its raw total x scale
```

AFCON's champion path is 3 group + 4 knockout: `3x3x1.5 + 4x3x2.0 = 37.5`, so
its scale is `600/37.5 = 16.0`. The Gold Cup's is 3 + 3: `31.5`, scale
`600/31.5 = 19.05`. Same title, same purse, and the shorter tournament simply
pays more per match — which is what "the rung is a rung" means.

It also absorbs format changes without anyone re-tuning a table. The 2026
World Cup added a Round of 32; `champion_max` grows, the scale shrinks, and the
World Cup is still worth a World Cup.

**Stage must be read from a table, not from match order.** The martj42 data
names the tournament and not the stage, so this needs a small per-edition file
— tournament, year, group matches per team, knockout rounds — in the shape of
the `whul/data/tennis_calendar.csv` that already exists for exactly this
reason. Sixteen competitions is a page of CSV, and it is checkable by eye
against the table above.

### 3. Opportunity: divide by what was available

This is the admin's own suggestion — *"weighting countries/teams by the number
of points available to them"* — and it is the only mechanism that fixes both
asymmetries at once, the two-versus-three competitions **and** the fallow year
in the middle of a cycle.

```
available(team, year) = the purses of every competition the team was eligible
                        to enter that year
score                 = REFERENCE x earned / available
```

A World Cup a team failed to qualify for stays in its denominator. Qualifying
is its own competition with its own purse; missing out means earning little of
that purse and none of the finals purse, which is the right answer rather than
a technicality.

**The one dial, and it is a real choice.** Divide fully and a team that wins a
Nations League in a fallow year scores exactly what a World Cup winner scores,
because both took everything available to them. Do not divide at all and a
European team out-earns an African one for reasons of geography. A dampening
exponent sits between them:

```
score = REFERENCE x earned / available^a        a = 1 full share, a = 0 raw points
```

I would not pick `a` from an armchair. The pipeline can compute the 2017-2024
history under two or three candidate values and show what each does to real
seasons — which is how the benchmarks were settled, and the same
compute-review-freeze the rest of the project uses.

---

## What this league year actually holds

The 2026-27 league year runs **21 August 2026 to 13 July 2027**. Verified
against the match ledgers:

**The men's World Cup was played 15 June - 19 July 2026** — over five weeks
before the league year opened. England, France and Spain took nothing from it,
and cannot: the whole tournament falls outside the window.

That leaves the three men's teams with the **UEFA Nations League** (league
phase 24 September - 17 November 2026, quarter-finals March 2027, Finals 9-13
June 2027) and the start of **Euro 2028 qualifying** from 25 March 2027.
Nothing else.

The women's 2027 World Cup qualifying ran March-June 2026, also before the
window. What the seven women's teams have inside it is the play-off phase, the
2026 CONCACAF W Championship (27 November - 5 December 2026, for Canada and the
USA) and then:

> ### The 2027 Women's World Cup does not fit inside the league year
>
> It runs **24 June - 25 July 2027**. The league year ends **13 July 2027**.
> Group stage and round of 16 land inside it; **the quarter-finals, semi-finals
> and final do not** — the semi-final is on 20 July and the final on the 25th.
>
> Seven of the ten slots in this category, the biggest tournament of their
> cycle, and the last three rounds fall outside the year. The 2027 Africa Cup
> of Nations (19 June - 17 July 2027) is cut the same way.
>
> This is a league-year decision, not a scoring one, and it is the admin's:
> extend the year for this category, score the tournament into the following
> year, or accept the truncation. It wants deciding before the ladder does,
> because a truncated World Cup changes what the rungs are worth relative to
> each other.

---

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
  Cup. The ladder should be an allow-list.

---

## Still to decide — the admin's, not mine

1. **The league year against the 2027 Women's World Cup.** The boxed section
   above. This one first.
2. **The four purses**, or a different set of rungs.
3. **`a`, the opportunity dampener** — to be chosen against real history rather
   than in advance.
4. **Do qualifiers score, and do friendlies?** The men's file holds 2,564
   friendlies since 2017 and the women's 1,098, plus invitational cups — the
   SheBelieves Cup, the Algarve Cup, the Arab Cup. The rostered women's teams
   played five SheBelieves matches and five FIFA Series matches in 2026 alone,
   so this is not a rounding decision.
5. **Whether a shootout win stays worth 2.** Kept from the R script above; it
   has never been challenged, only inherited.
