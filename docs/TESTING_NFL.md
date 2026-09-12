# Testing NFL data acquisition

Step-by-step verification that the NFL feed works from a machine you control.
Run these in a cloud CodeOSS instance (or any Linux/macOS shell with Python 3.11+).

The 2026-27 season has not started, so this confirms **historical weekly data**
acquisition and scoring. Live week-by-week refresh gets retested once games begin;
step 6 is the check that will matter then.

---

## 1. Open a terminal

In CodeOSS: **Terminal → New Terminal** (`` Ctrl+` ``).

Confirm you have Python 3.11 or newer:

```bash
python3 --version
```

If it is older, install 3.11+ before continuing — the code uses `X | Y` type syntax.

## 2. Clone the repository

```bash
git clone https://github.com/smeredith15/whul.git
cd whul
git checkout claude/fantasy-league-webapp-dp99e3
```

## 3. Create the environment

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
```

Installs pandas, pyarrow, requests and pytest. Takes a minute or two — pyarrow is
a large wheel.

> Optional: point CodeOSS at the interpreter with **Ctrl+Shift+P → Python: Select
> Interpreter → ./.venv/bin/python**, so the editor resolves imports.

## 4. Run the test suite

Offline tests first — these need no network and should pass instantly:

```bash
.venv/bin/python -m pytest -m "not network" -q
```

**Expect:** `103 passed, 2 deselected` (roughly — the count grows as leagues are added).

Now the whole suite, including tests that fetch real nflverse data:

```bash
.venv/bin/python -m pytest -q
```

**Expect:** `105 passed`. If the offline tests pass but these fail, the problem is
network access, not the code — jump to Troubleshooting.

## 5. Run the full validation report

This is the one command that answers everything at once — acquisition, benchmarks,
leaders and scrape readiness:

```bash
.venv/bin/python -m whul.cli validate nfl
```

**Expect** four sections and a `SUMMARY` ending in `scrape: READY`:

- **Acquisition** — seasons 2021-2025, ~19,000 rows and 22 weeks each, no gaps.
- **Benchmarks** — four position groups, each pooled from 340 player-seasons
  (68 per season × 5): QB ~390, RB ~337, TE ~213, WR ~314.
- **Leaders** — #1 and #10 per position for 2025, raw and normalized, with and
  without the postseason bonus. Josh Allen leads QB at 93.1 normalized excluding
  playoffs and 104.5 including; McCaffrey leads RB at 108.4 / 117.6.
- **Readiness** — four PASS lines.

Exit code is 0 when everything passes: `echo $?` after the run.

To change the window:

```bash
.venv/bin/python -m whul.cli validate nfl --seasons 2020-2024 --target 2024
```

## 6. Confirm the season aggregate

```bash
.venv/bin/python -m whul.cli score nfl --season 2024
```

**Expect** Lamar Jackson first with `regular_points` **428.38**, then Josh Allen,
Baker Mayfield, Jayden Daniels. That 428.38 is the assertion the integration test
checks, so it is the single best signal the feed is intact.

> Note: nflverse retired the `player_stats` release and weekly stats now live
> under `stats_player`. The adapter reads the new location and falls back to the
> old one, which is how 2025 became available at all.

Team scoring:

```bash
.venv/bin/python -m whul.cli score nfl --season 2024 --assets teams
```

**Expect:** 32 teams, Philadelphia first (they won that Super Bowl, 4 playoff wins).

## 7. Confirm week-by-week granularity

This is the important one for daily scoring — it proves the feed carries
per-week rows rather than only season totals.

```bash
.venv/bin/python -m whul.cli weekly nfl --season 2024
```

**Expect:** 22 rows — regular-season weeks 1-18, plus POST weeks 19-22 (wild card
through Super Bowl). Roughly 250-320 players per regular-season week, and the
playoff rounds halving each time: 117 → 76 → 40 → 20.

One player's line:

```bash
.venv/bin/python -m whul.cli weekly nfl --season 2024 --player "Josh Allen"
```

**Expect:** weeks 1-17 with **week 12 absent** (Buffalo's bye) and week 18 absent
(he was rested), then POST weeks 19-21, and a regular-season total of **370.34** —
matching what step 5 reported for him.

A single week:

```bash
.venv/bin/python -m whul.cli weekly nfl --season 2024 --week 5 --top 5
```

**Expect:** Ja'Marr Chase 36.30, Kirk Cousins 34.36, Joe Burrow 33.78.

## 8. Confirm normalization

```bash
.venv/bin/python -m whul.cli score nfl --season 2024 --normalize --top 20
```

**Expect:** a benchmark table per position group — `NFL_QB` ~391, `NFL_RB` ~330,
`NFL_WR` ~290, `NFL_TE` ~201, each drawn from a pool of 68 — then `scaled_score`
values where ~100 marks the 99th percentile *of that position*. Scores above 100
are correct and expected.

Sorting by `scaled_score` rather than raw points should put roughly one player
from each position at the top (Chase ~117 WR, Jackson ~110 QB, Bowers ~103 TE,
Barkley ~101 RB). That spread across positions is the signal that per-position
normalization is working: a tight end is being measured against tight ends, not
against quarterbacks.

Try a different benchmark manager count to see the pools resize:

```bash
.venv/bin/python -m whul.cli score nfl --season 2024 --normalize --managers 5
```

**Expect:** the same four position groups, each with `n_in_pool` of 22 instead of
68, and slightly higher benchmarks — a shallower pool means a stiffer 99th
percentile. Every position still gets a benchmark, because truncation is applied
per position group rather than across the NFL pool as a whole.

## 9. Export for inspection

```bash
.venv/bin/python -m whul.cli score nfl --season 2024 --csv nfl_2024.csv
```

Open `nfl_2024.csv` in CodeOSS and check `regular_points`, `postseason_games`,
`postseason_rate`, `postseason_bonus` and `total_points` per player. Players who
missed the playoffs should show a zero bonus; Joe Burrow is a good example.

## 10. Spot-check several seasons

```bash
for y in 2021 2022 2023 2024; do
  .venv/bin/python -m whul.cli score nfl --season $y --top 3
done
```

**Expect:** each season returns 500+ scored players with a plausible leader. This
is the check that the source is stable across years, not just for 2024.

---

## What "passing" means

| Check | Signal |
|---|---|
| Offline tests | Formulas match the R scripts |
| Network tests | The feed is reachable and its schema hasn't drifted |
| Step 5 | Everything, in one command |
| Step 6 (428.38) | Season aggregation is exact |
| Step 7 (22 weeks) | Per-week rows exist — daily scoring is possible |
| Step 8 | The 0-100 scale is being computed |
| Step 10 | The source is stable across seasons |

---

## Troubleshooting

**`403 Forbidden` or `CONNECT tunnel failed`** — your instance is behind an egress
proxy that blocks the host. The NFL feed needs
`github.com`, `objects.githubusercontent.com` and `raw.githubusercontent.com`.
Allowlist those, or set `HTTPS_PROXY` to a proxy that permits them.

**`ModuleNotFoundError: whul`** — you are not in the repo root, or not using the
venv. Use the full `.venv/bin/python` path as shown rather than a bare `python`.

**`pyarrow` fails to build** — your pip is old or the platform lacks a wheel.
`.venv/bin/pip install --upgrade pip` first; if it still builds from source, the
instance is likely on an unusual architecture.

**A network test fails but the CLI works** — the schema drifted upstream. The
candidate-column resolver in `whul/scoring/base.py` handles renames; send me the
error and I'll add the new name.

**Numbers differ slightly from this document** — nflverse restates prior seasons
occasionally. Small changes are normal; large ones are worth reporting.
