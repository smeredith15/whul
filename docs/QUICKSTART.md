# Quickstart — running the NFL and NBA tests in CodeOSS

One pass, start to finish. Part A is setup (once). Part B is NFL, which should
pass cleanly. Part C is NBA, which may not — it is a diagnostic.

Total time: about 10 minutes, most of it waiting on `pip install`.

---

## Part A — Setup (once)

### A1. Open a terminal

In CodeOSS: **Terminal → New Terminal**, or `` Ctrl+` ``.

### A2. Check Python

```bash
python3 --version
```

Must be **3.11 or newer**. If it is older, install a newer Python before going on —
the code uses `X | Y` type syntax that older versions reject.

### A3. Clone the repository

If you have **not** cloned it before:

```bash
cd ~
git clone https://github.com/smeredith15/whul.git
cd whul
git checkout claude/fantasy-league-webapp-dp99e3
```

If you **have** cloned it before, refresh instead — `git clone` will refuse and
`git checkout` alone does not fetch new commits:

```bash
cd ~/whul
git checkout claude/fantasy-league-webapp-dp99e3
git pull
```

Confirm you are current:

```bash
git log --oneline -1
```

### A4. Create the virtual environment

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
```

The last line takes 1-3 minutes (pyarrow is a large wheel). It should end with
`Successfully installed ... whul-0.1.0`.

#### If venv fails with "ensurepip is not available"

Debian and Ubuntu ship the `venv` module without the `ensurepip` bootstrap, so
this is common on a fresh cloud instance:

```
The virtual environment was not created successfully because ensurepip is not
available.  On Debian/Ubuntu systems, you need to install the python3-venv
package ...
```

The `.venv` directory it left behind is broken — **delete it before retrying**.
Then use whichever of these works for your instance.

**Option A — install the missing package (cleanest, needs sudo):**

```bash
rm -rf .venv
sudo apt update && sudo apt install -y python3.12-venv
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
```

Match the version to your Python: `python3 --version` reporting 3.12.x means
`python3.12-venv`.

**Option B — use `uv` (no sudo; it builds venvs without ensurepip):**

```bash
rm -rf .venv
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

Everything afterwards is unchanged — `uv` produces an ordinary venv, so
`.venv/bin/python` works exactly as documented.

**Option C — no virtual environment at all (last resort):**

```bash
rm -rf .venv
pip3 install --user --break-system-packages -e '.[dev]'
```

`--break-system-packages` is needed because Ubuntu 24.04 marks the system Python
as externally managed. It installs into your user site directory, not the system
one, so it is less alarming than it sounds — but it does mean these packages are
visible to every Python project you run as this user.

**With Option C, drop the `.venv/bin/` prefix from every command below** — use
`python3 -m pytest -q` and `python3 -m whul.cli validate nfl` instead.

> Every command below uses `.venv/bin/python` explicitly rather than a bare
> `python`, so nothing depends on the venv being "activated". If you prefer,
> `source .venv/bin/activate` once and then use plain `python`.

### A5. Point CodeOSS at the interpreter (optional)

`Ctrl+Shift+P` → **Python: Select Interpreter** → `./.venv/bin/python`.
Only affects editor autocomplete, not the commands.

### A6. Confirm the install

```bash
.venv/bin/python -m whul.cli list
```

**Expect** a two-row table listing `nfl` and `nba`. If this errors, stop —
something in A4 failed.

---

## Part B — NFL (should pass)

### B1. Offline tests

```bash
.venv/bin/python -m pytest -m "not network" -q
```

**Expect:** `104 passed, 2 deselected`. These need no network. They check the
scoring formulas against values computed by hand from your R scripts.

### B2. All tests, including live data

```bash
.venv/bin/python -m pytest -q
```

**Expect:** `106 passed`. If B1 passed but B2 fails, the problem is network
access, not the code — see Troubleshooting.

### B3. Quick reachability check

```bash
.venv/bin/python -m whul.cli probe nfl
```

**Expect:**

```
nflverse reachable: 19,422 rows for 2025, 150 columns
season types: {'REG': 18540, 'POST': 882}
```

### B4. The full validation report

This is the main event — acquisition, benchmarks, leaders and scrape readiness
in one run:

```bash
.venv/bin/python -m whul.cli validate nfl
```

**Expect** four sections ending in `scrape: READY`:

1. **Acquisition** — 2021-2025, ~19,000 rows and 22 weeks per season, no gaps, a
   few seconds total.
2. **Benchmarks** — four position groups, each pooled from 340 player-seasons
   (68 per season × 5 seasons): QB ~390, RB ~337, TE ~213, WR ~314.
3. **Leaders** — #1 and #10 per position for 2025, raw and normalized, with and
   without the postseason bonus. Josh Allen leads QB (93.07 excluding playoffs,
   104.45 including); McCaffrey leads RB (108.44 / 117.59); McBride leads TE
   (118.81); Nacua leads WR (98.76 / 110.58).
4. **Readiness** — four `[PASS]` lines.

Check the exit code:

```bash
echo $?
```

**Expect `0`.**

### B5. Save the output to send back

```bash
.venv/bin/python -m whul.cli validate nfl > nfl_validation.txt 2>&1
```

### B6. Optional extras

```bash
# week-by-week coverage — proves per-week rows exist for daily scoring
.venv/bin/python -m whul.cli weekly nfl --season 2025

# one player's weekly line
.venv/bin/python -m whul.cli weekly nfl --season 2025 --player "Josh Allen"

# a different window
.venv/bin/python -m whul.cli validate nfl --seasons 2020-2024 --target 2024

# export every scored player
.venv/bin/python -m whul.cli score nfl --season 2025 --csv nfl_2025.csv
```

---

## Part C — NBA (a diagnostic, not a pass/fail test)

**The NBA adapter is unverified.** ESPN is blocked from the environment it was
written in, so not one line of it has touched the live API. The parsing logic is
tested against a synthetic ESPN-shaped payload, but whether the real payload has
that shape is exactly what C2 and C3 determine.

Background: the R script used `hoopR`, whose data repository was archived on
2026-08-07 and stops at season **2023**. It cannot supply 2025-26 and never will.
ESPN's API is what `hoopR` wrapped, so going direct is the natural replacement —
and the same adapter will later serve WNBA and the four NCAA leagues.

### C1. Confirm ESPN is reachable at all

```bash
curl -sS -o /dev/null -w "%{http_code}\n" \
  "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20260115"
```

**Expect `200`.** If you get `403`, `000`, or a proxy error, your instance cannot
reach ESPN — skip to C5 and tell me, because nothing below will work.

### C2. Probe the adapter — **the step that matters**

```bash
.venv/bin/python -m whul.cli probe nba
```

Defaults to 15 January of the most recent season, so it lands on a date with
games. Override with `--date 2026-03-01` if you like.

**Expect:**

```
ESPN probe -- nba on 2026-01-15

  scoreboard       ok
  events           8
  boxscore         ok
  teams_in_box     2
  stat_labels      ['MIN', 'FG', '3PT', 'FT', 'OREB', ..., 'PTS', '+/-']
  parsed_rows      26
  sample           {'season': 2026, ..., 'points': '30', ...}

ESPN reachable and the boxscore schema parses.
```

Reading a failure:

| Line | What it means |
|---|---|
| `scoreboard FAILED` | Cannot reach ESPN — network or egress policy |
| `events 0` | No games that date; try `--date 2026-03-01` |
| `boxscore FAILED` | The summary endpoint moved or changed shape |
| `stat_labels` differ from above | ESPN renamed stats — I need the new labels |
| `parsed_rows 0` | Labels parsed but no athletes extracted — send me `sample` |

**If anything says FAILED, stop and send me the output.** Exit code is `1` on
failure, `0` on success.

### C3. Pull one day and score it

Only if C2 passed. One date, a handful of games — cheap:

```bash
.venv/bin/python - <<'PY'
from datetime import date
import pandas as pd
from whul.sources import espn

rows, day = [], date(2026, 1, 15)
for ev in espn.scoreboard("nba", day).get("events", []):
    comp = (ev.get("competitions") or [{}])[0]
    if not comp.get("status", {}).get("type", {}).get("completed"):
        continue
    st = int((ev.get("season") or {}).get("type", 2))
    rows += espn._parse_box(espn.summary("nba", ev["id"]), ev["id"], day, 2026, st)

box = pd.DataFrame(rows)
print(f"{len(box)} player rows from {box.game_id.nunique()} games")
print(box[["athlete_display_name","team","points","rebounds","assists","plus_minus"]].head(10))
PY
```

**Expect** ~25-30 rows per game with plausible lines. Spot-check two players
against the real box score for that date — this is what confirms the adapter maps
the right stat to the right column.

### C4. Validation report — **one season first**

ESPN is queried per date, so a five-season backfill is tens of thousands of
requests and will take hours. Start with one:

```bash
.venv/bin/python -m whul.cli validate nba --seasons 2026-2026 --target 2026
```

**Expect** the same four sections as NFL, with two position groups
(`NBA_Backcourt`, `NBA_Frontcourt`) instead of four. Even one season is ~250 dates
and ~1,300 games, so expect this to run for a while on a cold cache.

Responses cache under `data/cache/espn`, so re-runs are instant. Once you are
satisfied, widen:

```bash
.venv/bin/python -m whul.cli validate nba --seasons 2022-2026 --target 2026
```

Delete `data/cache/espn` to force a refetch.

### C5. Save the output to send back

```bash
.venv/bin/python -m whul.cli probe nba > nba_probe.txt 2>&1
```

---

## What to send me

**NFL:** `nfl_validation.txt` from B5, or just confirmation that B4 ended in
`scrape: READY` with exit code 0.

**NBA:** `nba_probe.txt` from C5 — whatever it says. If it succeeded, I especially
want the `stat_labels` list and the `sample` row, so I can confirm the stat
mapping before building on it. If it failed, the error text tells me which stage
broke.

Also useful: roughly how long C4 took, so I can size the nightly job.

---

## Troubleshooting

**`403 Forbidden` / `CONNECT tunnel failed` / `000`**
Your instance is behind an egress proxy. NFL needs `github.com`,
`objects.githubusercontent.com` and `raw.githubusercontent.com`. NBA needs
`site.api.espn.com`. Allowlist them, or set `HTTPS_PROXY` to a proxy that permits
them.

**`ModuleNotFoundError: No module named 'whul'`**
You are not in the repo root, or not using the venv. Use the full
`.venv/bin/python` path, and check you are in `~/whul`.

**`pyarrow` fails to build**
Run `.venv/bin/pip install --upgrade pip` first, then retry. If it still builds
from source, the instance is on an unusual architecture — tell me.

**A network test fails but offline tests pass**
The upstream schema drifted. The candidate-column resolver in
`whul/scoring/base.py` handles renames — send me the error and I will add the new
name.

**Numbers differ slightly from this document**
nflverse restates prior seasons occasionally. Small differences are normal; large
ones are worth reporting.

**Command not found: `python3`**
Try `python --version`. If that is 3.11+, substitute `python` for `python3` in A4.

**`ensurepip is not available` when creating the venv**
See the three options under A4. Delete the broken `.venv` first.

**`error: externally-managed-environment`**
Ubuntu 24.04 protects the system Python. Use a venv (A4 Option A or B), or add
`--break-system-packages` as in Option C.

**`ModuleNotFoundError: No module named 'pandas'`**
The install in A4 did not complete, or you are running the system `python3`
instead of `.venv/bin/python`. Re-run A4 and watch for
`Successfully installed ... whul-0.1.0`.
