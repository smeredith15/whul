# Testing NBA data acquisition

**Status:** ESPN was confirmed reachable on 2026-01-15 — the scoreboard and
boxscore endpoints both respond and parse. One gap surfaced and is now handled:
**the boxscore returns an empty position for every player**, so positions are
pulled from team rosters instead (30 extra requests, cached). Re-run step 3 after
pulling to confirm the fix resolves them.

The adapter still has not been exercised over a full season, so treat this as a
diagnostic rather than a settled source.

Setup is identical to `TESTING_NFL.md` steps 1-3 — clone, venv, `pip install -e '.[dev]'`.

---

## Why ESPN and not hoopR

The R script used `hoopR`, which reads the `sportsdataverse/hoopR-data`
repository. That repository was **archived on 2026-08-07 and its NBA files stop at
season 2023**. It cannot supply 2025-26, and it will never update again. Its
schedule files also contain regular-season games only, so any postseason terms
computed from them come out zero.

ESPN's site API is what `hoopR` wrapped in the first place, so going direct is the
natural replacement. It is free and needs no key. The same adapter will serve
WNBA and the NCAA leagues.

The trade-off is shape: nflverse ships one file per season, while ESPN is queried
per date. A backfill walks ~250 dates and ~1,300 games per season, so the first
pull is slow. Responses are cached under `data/cache/espn`, so it is a one-time
cost and a daily update is a single date.

## 1. Offline tests

```bash
.venv/bin/python -m pytest -m "not network" -q
```

**Expect:** `103 passed`. These include the NBA scoring formulas and the ESPN
boxscore parser, exercised against a synthetic payload shaped like ESPN's. They
prove the parsing and scoring are right; they cannot prove the endpoint works.

## 2. Check what is reachable

```bash
curl -sS -o /dev/null -w "%{http_code}\n" \
  "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20260115"
```

**Expect `200`.** Anything else — `403`, `000`, a proxy error — means your instance
cannot reach ESPN, and nothing below will work until that is fixed.

## 3. Probe the adapter *(the critical step)*

```bash
.venv/bin/python -m whul.cli probe nba --date 2026-01-15
```

**Expect** something like:

```
ESPN probe -- nba on 2026-01-15

  scoreboard       ok
  events           8
  boxscore         ok
  teams_in_box     2
  stat_labels      ['MIN', 'FG', '3PT', 'FT', ..., 'PTS', '+/-']
  parsed_rows      26
  sample           {'season': 2026, ... 'points': '30', ...}

ESPN reachable and the boxscore schema parses.
```

What each line tells us:

| Line | Meaning if wrong |
|---|---|
| `scoreboard FAILED` | Cannot reach ESPN at all — network or egress |
| `events 0` | Pick a date with games; try a mid-January weeknight |
| `boxscore FAILED` | The summary endpoint moved or changed shape |
| `stat_labels` differ from the table above | ESPN renamed stats; I need the new labels |
| `parsed_rows 0` | Labels parsed but no athletes extracted — send me `sample` |

**If any line says FAILED, stop here and send me the whole output.** Everything
downstream depends on this working, and the failure mode tells me what to fix.

## 4. Pull one day and score it

Cheap — one date, a handful of games:

```bash
.venv/bin/python - <<'PY'
from datetime import date
import pandas as pd
from whul.sources import espn
from whul.scoring import nba

rows = []
day = date(2026, 1, 15)
board = espn.scoreboard("nba", day)
for ev in board.get("events", []):
    comp = (ev.get("competitions") or [{}])[0]
    if not comp.get("status", {}).get("type", {}).get("completed"):
        continue
    st = int((ev.get("season") or {}).get("type", 2))
    rows += espn._parse_box(espn.summary("nba", ev["id"]), ev["id"], day, 2026, st)

box = pd.DataFrame(rows)
print(f"{len(box)} player rows from {box.game_id.nunique()} games")
print(box[["athlete_display_name", "team", "points", "rebounds", "assists", "plus_minus"]].head(10))
PY
```

**Expect:** roughly 25-30 player rows per game, with plausible box score lines.
Compare a couple against the real box score for that date — this is the check that
the adapter maps the right stat to the right column.

## 5. Full validation report

Only attempt this once step 3 passes. **It is slow on a cold cache** — a full
five-season backfill is tens of thousands of requests and will take hours. Start
with one season:

```bash
.venv/bin/python -m whul.cli validate nba --seasons 2026-2026 --target 2026
```

**Expect** the same four sections as the NFL report:

1. **Acquisition** — one season, ~1,300 games, ~30,000 player rows.
2. **Benchmarks** — `NBA_Backcourt` and `NBA_Frontcourt`, each with its own pool
   and 99th percentile.
3. **Leaders** — #1 and #10 in each group for 2025-26, raw and normalized, with
   and without the postseason bonus.
4. **Readiness** — the per-period check should pass (rows are per game date).

Then widen once you are satisfied, letting the cache do the work:

```bash
.venv/bin/python -m whul.cli validate nba --seasons 2022-2026 --target 2026
```

Cached responses live in `data/cache/espn` — delete that directory to force a
refetch.

## 6. Confirm daily-scrape shape

A daily job pulls a single date, which should take seconds:

```bash
time .venv/bin/python -m whul.cli probe nba --date 2026-03-01
```

**Expect:** a couple of seconds on a cold cache, instant on a warm one. That is
the real cost of the nightly job for this league.

---

## What I need back from you

Whatever happens, the useful reply is the output of **step 3**, plus step 4 if it
got that far. Specifically:

- the `stat_labels` list, so I can confirm the stat mapping
- one `sample` row, so I can confirm types and naming
- the timing from step 6, so I can size the nightly job

If ESPN turns out to be blocked on your side too, tell me and I will look at
alternatives — but every free NBA source I could identify was unreachable from
here, so I could not pre-test one.
