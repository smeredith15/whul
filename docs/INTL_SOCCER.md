# International soccer: what a tournament is worth

To revisit once NCAAF data acquisition is settled. Recorded 2026-09-04 from
the league admin; nothing below is implemented.

Intl Soccer is a two-slot team category with six rostered assets — four
women's, two men's — and no adapter yet. Whatever gets written should be
written against this design rather than the club soccer one, because the
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
match's worth depends on the tier of the event. Club soccer already grades
competitions this way (`whul.scoring.soccer` classifies a match by its
competition and assigns a tier); this is the same idea with a different ladder.

---

## What must come out equal

**Equivalent tournaments across federations are equivalent.** The Asian Cup,
the Copa América, AFCON and the Euros are one rung, and a team winning its
continental championship should score the same whichever continent it is in —
even though the formats differ in length, group size and knockout depth.

That last clause is the hard part. If a rung is worth a fixed sum per match,
a federation whose tournament runs seven matches pays more than one that runs
five, and the rung stops being a rung. So the design likely needs one of:

* per-match values set **per tournament** so each tournament's full run totals
  the same, the way the tennis tables are built so every tier's column sums to
  its face value; or
* a per-team scaling by the points available to them — the admin raised this
  explicitly: *"this might also entail weighting countries/teams by the number
  of points available to them."*

The same equivalence holds between the men's and women's game: the Women's
World Cup is the World Cup rung.

---

## What must not happen

**One benchmark for the whole category, not one per federation.** Splitting
UEFA from CONMEBOL from CAF would leave each pool far too small to draw a 99th
percentile from — the same reason F1's 20-car grid makes its benchmark close to
the single best season. Intl Soccer stays one normalization group.

---

## The pool is an open question too

Recorded 2026-09-04: *"This will definitely involve re-benchmarking
international soccer, the question is how. In terms of what tournaments get
what points, and in terms of what is our pool."*

Not splitting by federation is settled — those pools would be far too small.
What is not settled is what the pool *is*. The buffer-pool machinery assumes a
league of comparable competitors playing comparable seasons, and international
soccer has neither: a team's opportunities depend on which year of the cycle it
is, and on whether it qualified at all. Whether the pool is every national
team, or the teams that entered a tournament that year, or something scaled by
opportunity, is part of the same decision as the points table and should be
made with it.

## Still to decide

Each federation's ladder, one at a time, with the admin. The rungs and the
tournaments on them are the substance of this; the machinery to apply them
already exists in the club soccer tier model.

Also unresolved, and part of the same job:

* **Where the data comes from.** No international fixture source is wired up.
* **Friendlies and qualifiers** — whether they score at all, and if so how far
  below the tournament rungs they sit.
