# WHUL — Cross-Sport Fantasy League

Scoring engine and web app for a 5-manager cross-sport fantasy league. Each roster
slot holds a player or a team from one of ~20 leagues, and is scored on a 0-100
scale where 100 is the 99th percentile of a frozen, draft-relevant benchmark pool.

See [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for league rules, the scoring
model, and the build plan. The original R analyses are preserved in `r-scripts/`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Tests

```bash
.venv/bin/python -m pytest              # everything
.venv/bin/python -m pytest -m "not network"   # offline only
```

Tests marked `network` fetch live data from nflverse and assert against known
2024 results.

## Running a league from the terminal

```bash
.venv/bin/python -m whul.cli list
.venv/bin/python -m whul.cli score nfl --season 2024
.venv/bin/python -m whul.cli score nba --season 2023 --assets teams --normalize
.venv/bin/python -m whul.cli score nfl --season 2024 --csv nfl_2024.csv
```

`--normalize` applies the 0-100 scale and prints the benchmark it used.
`--managers` overrides the benchmark manager count (default 15).

## Layout

| Path | Purpose |
|---|---|
| `whul/config/league.py` | Roster template, slot caps, season window, pool rates |
| `whul/normalize.py` | Buffer pool → frozen 99th-percentile benchmark → 0-100 scale |
| `whul/bestball.py` | Slot occupancy, trade accrual, top-K rollup, standings |
| `whul/scoring/` | Per-league scoring formulas, ported from `r-scripts/` |
| `whul/sources/` | Data adapters (free sources only) |
| `whul/cli.py` | Per-league terminal harness |

## Data source status

| League | Source | Live? |
|---|---|---|
| NFL | nflverse release assets | yes |
| NBA | hoopR-data | **no** — archived 2026-08-07, stops at season 2023 |

hoopR-data being archived means NBA (and likely WNBA / NCAA, which use the sibling
`wehoop` feeds) need a live source before the season can be scored daily. See
`docs/PROJECT_PLAN.md` §7.
