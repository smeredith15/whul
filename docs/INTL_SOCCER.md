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

**Where inference cannot work, the shape is stated** in
`whul/data/intl_editions.csv` — sixteen rows, all of them Nations Leagues, for
the two reasons that file sets out at length. Those rows carry **League A's**
numbers and every team in the competition is measured against them. That is
deliberate and confirmed by the admin: a League C side plays the same six
league matches and cannot reach the Finals, so measuring it against the
winner's path caps it below a full Nations League ceiling, which is the right
answer for winning League C. Nobody in this league is likely to hold a team
outside League A anyway.

### 5. A season is its best competition plus half of everything else

The two-way rule the MLB scorer already uses for a player who bats and pitches
— the primary scores whole, the secondary contributes half — extended to
however many competitions a year holds. 674 team-seasons in the pool have two
competitions and 74 have three.

Summing them instead put the United States' 2018-19 at **211 on the 0-100
scale**: they won the World Cup and the championship that qualified them for it
inside one league year. The fold and the shallower rung ladder bring that to
**165**.

### 6. A fallow year is scaled up

**Adopted 2026-09-07.**

```
multiplier = top ceiling / the best rung the team actually played that season
```

A Nations League year pays ×2, a federation-cup year ×1.33, a World Cup year
×1. Without it a European men's team's 2026-27 — a Nations League and the first
Euro qualifiers, and nothing else — scores half what its World Cup year does.

It also flattens the top rather than raising it, which is the opposite of what
a flat percentile suggested. Lifting fallow years lifts far more of the pool
than it lifts the best seasons — a World Cup winner is already on the top rung
and gets ×1 — so the benchmark rises and the outlier comes down with it.

**It has one sharp edge, and it took the Nations League table to find it.**
Where a competition's knockout is left out of the denominator, a perfect run in
its league phase is a perfect run in the competition, and the lift then doubles
it. Guatemala winning four CONCACAF League C matches in 2019-20 scored **106**
against Spain's **87** for winning the World Cup. The knockout belongs in the
denominator whether or not it falls in the same league year — the team could
have gone on to play it, and which year it lands in changes where the points
go, not what the run was worth. With that fixed the same season scores 62.

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

> ### Which league year a match scores in
>
> **Decided 2026-09-07.** Two rules, and the difference is the *phase* rather
> than the competition.
>
> **A block goes whole into the year it began in.** A group stage that runs
> directly into a knockout is one event, and splitting it at a date nobody
> playing in it would recognise is worse than letting it finish outside the
> year. The 2027 Women's World Cup runs 24 June - 25 July 2027 and the league
> year closes on 13 July; every match of it belongs to **2026-27**, final
> included, and pays the rosters that held those teams when it kicked off —
> even though the next draft happens mid-tournament. The 2027 Africa Cup of
> Nations (19 June - 17 July) is the same.
>
> **A windowed phase scores where it was played.** Qualifying campaigns and the
> Nations Leagues' league phases run across international windows months apart
> and do not line up with a league year in any reliable way: the 2022-23 UEFA
> Nations League opened in June 2022 and finished in June 2023, so a
> whole-block rule would have to pick one year and be wrong about half the
> fixtures either way.
>
> `whul/data/intl_tournaments.csv` carries the distinction per row as `phase`,
> `block` or `windows`; qualifying is always `windows`.
>
> **The league year normally runs mid-July to mid-July.** 2026-27 is the
> exception — it opens on 21 August because that is when the league was
> drafted — and it closes on 13 July 2027 like any other, so history is
> partitioned from the 14th and every year is contiguous with the next.
>
> That date is what makes the block rule load-bearing rather than decorative.
> Nearly every continental championship and World Cup runs from mid-June to
> mid-July, so a mid-July boundary falls *inside* them: Euro 2024 ran 14 June to
> 14 July, and by match date alone its final would score in a different league
> year from its group stage. Against an August boundary the rule never fired
> once in eleven years; against this one it holds **292 team-matches** together,
> including the finals of Euro 2024, Copa América 2024 and the 2026 World Cup.
>
> It also settles the one case that was genuinely ambiguous. The 2023 Women's
> World Cup ran 20 July to 20 August 2023 — beginning a week after the 2022-23
> year closed — so it belongs to **2023-24**, not to the year it nearly
> straddled. Spain's win moves with it.

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
| **summed, no fold, no lift** | | | | | | |
| Men's | 161.7 | 117.5 | 4 | 0 | 0 | 0 |
| Women's | 149.5 | **207.3** (USA 18) | 4 | 2 | 1 | 1 |
| **folded, no lift** | | | | | | |
| Men's | 136.6 | 120.4 | 4 | 0 | 0 | 0 |
| Women's | 148.7 | **158.1** | 4 | 1 | 1 | 0 |
| **folded and lifted — adopted** | | | | | | |
| Men's | 163.6 | **111.7** (Qatar 18) | 4 | 0 | 0 | 0 |
| Women's | 177.0 | **132.8** (USA 18) | 4 | 1 | 0 | 0 |

Four seasons of 320 clear 100 in every variant, which is what a 99th percentile
means. What changes is the tail: summing gives one season at 207, the fold
brings it to 158, and the fold plus the lift brings it to 133 with nothing at
all above 150 on either side.

The middle of the distribution is nowhere near 100 — the women's pool has a
median of 26 and a 90th percentile of 63; the men's 35 and 64.

### The highest seasons in the pool, all teams

```
   Men's                                      Women's
   2018  Qatar          182.7 raw   111.7     2018  United States  235.0  132.8
   2020  United States  173.3       105.9     2021  Brazil         191.4  108.2
   2024  Mexico         165.8       101.4     2025  Japan          184.0  104.0
   2025  Spain          164.4       100.5     2021  United States  183.3  103.6
   2018  Brazil         160.0        97.8     2018  New Zealand    150.0   84.7
   2023  New Zealand    153.8        94.0     2021  England        149.9   84.7
   2022  Mexico         144.7        88.5     2021  South Africa   149.5   84.4
```

Qatar's 2019 Asian Cup — seven matches, seven wins, one goal conceded before
the final — is the top men's season on the board, which is the right answer.
Spain's men winning the 2026 World Cup is fourth at 100.5. England's women won
Euro 2022 six from six, four of them to nil including an 8-0, and take 122 of
the 150 a federation cup can pay, with no qualifying term at all because they
were hosts.

### The rostered teams, normalized

```
   Men's           2018 2019 2020 2021 2022 2023 2024 2025
   England           33   23   59   26   40   46   30   73
   France            25   19   38   23   62   46   67   82
   Spain             26   17   39   41   42   86   61  101

   Women's         2018 2019 2020 2021 2022 2023 2024 2025
   Brazil            23    0    0  108    0   69   81    0
   Canada            72    0    0   81    0   57    0    0
   England           56    0    0   85    8   58   57   21
   France            41   10   27   39    7   56   59   19
   Germany           44   20   19   53    8   47   55   29
   Spain             18   11   25   32    9   83   81   32
   United States    133    0    0  104    0   74    0    0
```

The women's 2022-23 column is thin because the 2023 World Cup moved out of it:
it began 20 July 2023, a week after that league year closed, so it and Spain's
win belong to 2023-24. What is left in 2022-23 is qualifying alone.

### What the numbers expose

**Canada and the United States are down to two blank years, not four.** The
2024 CONCACAF W Gold Cup was supplied by the admin and now scores: Canada
2023-24 goes from 0 to 68, the USA from 0 to 80, Brazil from 0 to 80. What
remains blank is 2024-25 and 2025-26, and the cause is the same one — CONCACAF
women play a biennial championship and little else that is not a friendly or
the Olympics.

**Brazil's women score nothing in 2025-26 either**, and that one is nobody's
bug: Brazil host the 2027 World Cup, qualify automatically, and are the one
CONMEBOL nation absent from the nine-team Nations League that *is* the
qualifying. A World Cup host plays no competitive football for a year.

**The men's 2018-19 and 2019-20 are still the flattest years on the board** for
the rostered teams, 17 to 33, even after the lift. Those are Euro qualifying
years for teams that go deep in tournaments and rarely lose a qualifier, which
is a season worth little by construction.

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
| `whul/data/intl_supplement.csv` | 25 | the 2024 W Gold Cup, by hand |

`date, home_team, away_team, home_score, away_score, tournament, city,
country, neutral`, plus a separate `shootouts.csv` for the penalty rule. Served
from raw.githubusercontent, the same host nflverse uses.

Four cautions, all of them the silent kind:

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
  pattern does not match. The women's Nations League was renamed from `UEFA
  Nations League` to `UEFA Women's Nations League` in October 2025, the same
  way.

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
* **The 2024 CONCACAF W Gold Cup was not in the ledger at all** — its
  qualification was, 87 matches of it, and not one match of the tournament,
  though the United States won it and Brazil were runners-up. It is now in
  `whul/data/intl_supplement.csv`, supplied by the admin. The loader reports
  any supplement row that later turns up in the ledger too, so a block kept
  after an upstream fix cannot double-count in silence. **Nobody knows why it
  is missing, so the next W Gold Cup has to be checked for rather than assumed
  — that is a diary entry, not a code change.**

## Built

`whul/sources/intl_soccer.py` loads and classifies; `whul/scoring/intl_soccer.py`
applies the ladder; `whul/data/` carries the three tables — the ladder, the
stated Nations League shapes, and the supplement. Registered as `intl-soccer`,
in the nightly run's league list, and covered by eighteen tests.

Three faults the build turned up, none guessable from the design:

**A pull for one league year had no tournament shape to read.** The shape comes
from the edition that was played, so a single-season pull left the 2026 Women's
Africa Cup of Nations with no group stage and no knockout: its qualifiers
became the whole competition, and Ghana topped the women's board on **two won
matches at a full ceiling**. The loader now returns the whole history with the
asked-for years marked and the scorer drops the rest once it has the shapes.

**A feed that returned nothing said nothing.** `_pull` returned early on an
empty frame without reaching any of the explanations, so international
soccer's first pull of 2026-27 — correctly empty, the next international window
being weeks away — reported a league on zero with no word about why. Fixed for
every source, not just this one.

**The date filter undid the whole design, silently.** `_pull` trims every
source's rows to those on or after the league year opened -- correct for a feed
that reports by date, and wrong for one that has already decided which year
each match belongs to. It stripped the history the tournament shapes are read
from, putting the Ghana bug straight back, and it would have cut the 2027
Women's World Cup off at 13 July, which is the one thing the block rule exists
to prevent. Sources now declare `dated_by_source` when they have done the
dating themselves.

Today's pull is empty and right to be: the only counting matches since 14 July
2026 are the last four of the World Cup, which the block rule holds in 2025-26
where the tournament began. The first real scores arrive with the September
international window.

## Still open

1. **Whether the 2023-24 CONCACAF W Gold Cup is the only hole.** Nobody knows
   why it is missing, which means nobody knows whether anything else is — and
   the next W Gold Cup is not until 2029, so there is nothing to probe in the
   meantime. `intl_supplement.csv` carries the 2024 edition by hand and the
   loader reports any row of it that later appears upstream.
2. **The benchmark has not been computed or frozen.** `whul benchmarks` has
   not been run for this league; until it is, the source scores raw points and
   nothing scales them.
