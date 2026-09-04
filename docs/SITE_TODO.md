# Site changes to make

*Scoring changes live elsewhere: [`INTL_SOCCER.md`](INTL_SOCCER.md) holds the
international soccer tournament ladder.*

Collected 2026-09-04, to be worked through once the benchmarks are frozen and
the standings are real. Nothing here changes a number; it is all how the
numbers are presented.

---

## 1. Use the league's full name in the header

WHUL is the **Wolf Hill Uber League**. The header says "WHUL", which nobody
outside the league can read.

Full name in the page header, initials wherever there is no room for it — the
same rule the managers already follow (Tyler in the standings, TG on a badge).
`whul/site/build.py` writes the header; the `<title>` wants it too.

---

## 2. Collapsible league sections

The bar plots and tables are grouped by league and every group is always open,
so the page is long before it is informative. Each league heading should
collapse and expand.

`<details>`/`<summary>` does this with no script and keeps working with
JavaScript off, which suits a static site. Remembering which sections a reader
left open (localStorage) is worth adding at the same time — a reader who
collapses fifteen leagues does not want to do it again tomorrow.

---

## 3. Sort same-slot assets next to each other in the bar chart

A manager's NFL Team 1 and NFL Team 2 should be adjacent bars in that manager's
colour, then the next manager's two, and so on. At the moment the ordering
breaks the pair up, so a category cannot be read as a block.

The grouping key is (category, manager, slot index) sorted in that order.
`whul/site/build.py::_slot_rows` already ranks slots within a category — this
is a change to how the result is laid out, not to what it computes.

---

## 4. Filter by manager, from the chart itself

Clicking a manager's name above a plot should hide and show that manager's
series — on the bar chart, the tables, and the progression line together, since
a reader filtering one means all three.

The line chart already carries a per-series legend and a hover crosshair, so
the mechanism is half there: `whul/site/charts.py` needs the legend entries to
toggle a class rather than only label. Keep the axis fixed when a series is
hidden — a rescaling y-axis makes the remaining lines appear to move.

---

## 5. Show every scoring category, not four of them

A team profile currently lists `total_points`, `team`, `matches_played`,
`wins`, `bye_points`. That is what the *aggregate* carries; the categories that
made it up are computed and then dropped.

They are recoverable. `whul/scoring/soccer.py::score_team_matches` produces a
row per match with the competition, its tier, goals for and against, and
whether it counted; the NFL team scorer carries `reg_wins`, `reg_big_wins`,
`reg_shutouts`, `div_wins`, `point_diff`, `playoff_appearance`, `playoff_wins`,
`div_champ`. Nothing new needs fetching — the ingest stores the scored row, so
this is a question of what the scorers keep and how the profile labels it.

Labels want writing too: the window shows raw column names (`games_played`),
which read as debug output.

---

## 6. List an athlete's actual finishes

"Daytona 500 4th · Indian Wells QF · Masters 2nd" says more than a points
total, and it is the natural thing to want from a profile.

The data is already there and already dated: `tennis.match_events`,
`golf.score_events` and `motorsport.race_events` each return one row per event
with the tournament, the date and the finish or round. Tennis carries losses
too, as rows worth nothing, so a first-round exit shows as "US Open R128"
rather than as an absence — which is what distinguishes a player who lost from
one who is injured and did not enter. They were written for
the window benchmarks and the live ingest sums them — so the finishes exist and
are thrown away at aggregation. Keeping the event rows for rostered assets is
the work; the profile then reads them newest first.

---

## 7. Strike the score, not the name

On a team page a benched slot is struck through entirely. Only the score should
be — the player is not crossed out, their contribution is.

`whul/site/build.py::_asset_button` applies the strike; move it to the score
cell.
