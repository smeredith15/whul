# WHUL — Cross-Sport Fantasy League

Scoring engine and web app for a 5-manager cross-sport fantasy league. Each roster
slot holds a player or a team from one of ~20 leagues, and is scored on a 0-100
scale where 100 is the 99th percentile of a frozen, draft-relevant benchmark pool.

See [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for league rules, the scoring
model, and the build plan. The original R analyses are preserved in `r-scripts/`.

## Quickstart

`docs/QUICKSTART.md` walks from a fresh terminal through running both league test
suites, with expected output at each step.

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

Two commands exist for checking a data source:

```bash
.venv/bin/python -m whul.cli probe nba                    # is it reachable, does it parse
.venv/bin/python -m whul.cli validate nfl                 # acquisition, benchmarks, leaders, readiness
.venv/bin/python -m whul.cli validate nfl --seasons 2020-2024 --target 2024
```

## Freezing the benchmarks

[`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) is the step-by-step procedure, in the
order that fails cheapest — including which sources need a machine with open
outbound HTTPS. [`docs/MAC_SETUP.md`](docs/MAC_SETUP.md) is the terminal setup
that comes before it. In short:

The benchmark is the number every score in its group is divided by, so it
decides what 100 means. Getting it wrong produces no error — just a season of
plausible, wrong standings. Computing, reviewing and adopting one are therefore
three separate steps:

```bash
.venv/bin/python -m whul.cli benchmarks list                    # what can be computed
.venv/bin/python -m whul.cli benchmarks compute nfl tennis      # pull, score, print
.venv/bin/python -m whul.cli benchmarks compute --save          # every league, stored unfrozen
.venv/bin/python -m whul.cli benchmarks coverage <version>      # what the roster needs
.venv/bin/python -m whul.cli benchmarks compare <old> <new>     # how far scores would move
.venv/bin/python -m whul.cli benchmarks freeze <version>        # adopt it
```

Five seasons by default, counting back from the last completed one. COVID
seasons are excluded — and reaching past one lengthens the reach rather than
shrinking the pool, so the NBA draws on 2019 and 2022-25.

Tennis, golf and motorsport are pooled differently: they run continuously, so
their benchmark is drawn over the league year's own August-to-July window
shifted back whole years, not over calendar seasons (§2.3). A window is judged
by the year it *ends* in, and one the source cannot cover to its end date is
dropped and replaced by reaching one further back — a half-covered window looks
like a full one with quiet athletes in it. Tennis starts at 2022-23; golf and
motorsport skip 2019-20 and 2020-21, which lost the shutdown months and then
absorbed what was pushed into them (two Masters fall in the same golf league
year).

`freeze` refuses while a rostered asset has no benchmark, since that manager
would otherwise score nothing without an error; `--force` overrides it. Nothing
is measured against a version until it is frozen, and a frozen version is never
edited — superseding it means a new version, which leaves both on the record.

Tennis history comes from `model_data_snapshot.rds` in a `tennis2026` checkout.
Set `WHUL_TENNIS2026` if it is not a sibling of this repository.

## Layout

| Path | Purpose |
|---|---|
| `whul/config/league.py` | Roster template, slot caps, season window, pool rates |
| `whul/normalize.py` | Buffer pool → frozen 99th-percentile benchmark → 0-100 scale |
| `whul/benchmarks.py` | Compute, review and freeze a season's scale |
| `docs/BENCHMARKS.md` | How to build and freeze one, step by step |
| `docs/MAC_SETUP.md` | Terminal setup on macOS, from zero to a working database |
| `whul/benchmark_sources.py` | Which loader and scorer each league's history comes from |
| `whul/bestball.py` | Slot occupancy, trade accrual, top-K rollup, standings |
| `whul/scoring/` | Per-league scoring formulas, ported from `r-scripts/` |
| `whul/sources/` | Data adapters (free sources only) |
| `whul/cli.py` | Per-league terminal harness |

## Data source status

| League | Source | Status |
|---|---|---|
| NFL | nflverse `stats_player` release | verified through 2025 |
| NBA | ESPN site API | **unverified** — blocked where written; run `probe nba` |
| NBA (historical) | hoopR-data | archived 2026-08-07, stops at season 2023 |

Per-league verification guides live in `docs/TESTING_<LEAGUE>.md`. Start with:

```bash
.venv/bin/python -m whul.cli probe nfl        # cheap reachability check
.venv/bin/python -m whul.cli validate nfl     # full acquisition report
```
