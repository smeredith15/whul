# Setting up the benchmarks

The benchmark is the number every score in its group is divided by, so it
decides what 100 means. Getting it wrong raises nothing — it produces a season
of plausible, wrong standings. This is the procedure for building one, in the
order that fails cheapest.

Everything below is one command with different arguments:

```
python -m whul.cli benchmarks <list|compute|coverage|compare|versions|freeze>
```

---

## Before you start

**Run this where the feeds reach.** Most sources are refused by the cloud
sandbox's network policy (a 403 on CONNECT), so NFL and tennis are the only two
that can be computed there. Everything else has to run on your Mac or wherever
the machine has open outbound HTTPS.

```bash
git clone https://github.com/smeredith15/whul && cd whul
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q          # expect all green before trusting a number
```

**Tennis needs the snapshot.** `model_data_snapshot.rds` is the only surviving
copy of the match history. Clone `tennis2026` next to this repo, or point at it:

```bash
git clone https://github.com/smeredith15/tennis2026 ../tennis2026
# or, anywhere else:
export WHUL_TENNIS2026=/path/to/tennis2026
```

**Have the draft database.** The benchmark set is checked against the roster
before it can be frozen, so `data/whul.sqlite3` needs the imported draft in it.

---

## Step 1 — see what there is to do

```bash
.venv/bin/python -m whul.cli benchmarks list
```

23 sources. The `pooled` column says how each is drawn:

* **season** — the sport's own season, five back. The team sports.
* **window** — the league year's own Aug-to-Jul window shifted back whole
  years. Tennis, golf and motorsport, which run continuously and have no
  season to align.

---

## Step 2 — check a source is reachable before spending an hour on it

`probe` is seconds; `compute` can be an hour. Probe first.

```bash
.venv/bin/python -m whul.cli probe nhl
.venv/bin/python -m whul.cli probe pga --season 2025
.venv/bin/python -m whul.cli probe epl
```

A probe that fails on the network is a source to fix or skip; a probe that
fails on the schema is a feed that changed shape and needs the adapter looked
at. Either way, find out now.

---

## Step 3 — compute the cheap ones and start a version

Start with the sources that pull whole files rather than walking dates:

```bash
.venv/bin/python -m whul.cli benchmarks compute nfl nfl-teams tennis --save
```

That prints each league's groups with the pool each number came from, then
saves an **unfrozen** version and echoes its id. Nothing is measured against it
yet.

Read the output before moving on. Three things are worth stopping for:

* `<- thin` on a row — the pool was under 10, so the 99th percentile is close
  to the single best season in it. That is a different statistic than the one
  intended.
* `! N complete windows, not 5` — the source could not cover a window to its
  end date and there was no usable one further back. The pool is smaller than
  intended.
* `FAILED` — that league contributed nothing. The others still saved.

---

## Step 4 — add the rest to the *same* version

Use `--into <version>` so a run split across sittings builds one scale rather
than several with different holes in them.

```bash
V=2026-27-20260903-185127     # whatever step 3 printed

.venv/bin/python -m whul.cli benchmarks compute pga motorsports --save --into $V
.venv/bin/python -m whul.cli benchmarks compute nhl nhl-teams --save --into $V
.venv/bin/python -m whul.cli benchmarks compute mlb mlb-teams --save --into $V
.venv/bin/python -m whul.cli benchmarks compute epl laliga seriea \
    bundesliga ligue1 mls nwsl --save --into $V
.venv/bin/python -m whul.cli benchmarks compute ncaaf ncaam ncaaw \
    ncaabaseball ncaasoftball --save --into $V
.venv/bin/python -m whul.cli benchmarks compute nba nba-teams --save --into $V
```

NBA last: ESPN is queried one date at a time, so five seasons of box scores is
by far the longest pull here. Everything else is already banked by the time it
starts.

Re-running a league you already added replaces its groups rather than
duplicating them, so a league you want to redo is just the same command again.

---

## Step 5 — check the version against the roster

```bash
.venv/bin/python -m whul.cli benchmarks coverage $V
```

Every rostered league, and whether this version can score it. A missing one is
labelled by cause, because the fix differs:

* `MISSING -- run 'benchmarks compute <key>'` — a league nobody has computed
  yet. One command.
* `MISSING -- no source registered for it yet` — no adapter exists. That is a
  scraper to write, not a command to run. See **Known gaps** below.

Exit status is non-zero while anything is uncovered, so this is safe to put in
a script.

---

## Step 6 — freeze

```bash
.venv/bin/python -m whul.cli benchmarks freeze $V
```

This is the step that makes the version the scale. It **refuses** while a
rostered asset has no benchmark, since that manager would otherwise score
nothing without an error anywhere. `--force` overrides it, which is the right
call only when the uncovered leagues are the ones with no source at all and you
want the rest of the standings live in the meantime.

After freezing:

```bash
.venv/bin/python -m whul.cli rollup --backfill --season 2026-27
.venv/bin/python -m whul.cli site --season 2026-27
```

A frozen version is never edited. Superseding it means computing a new one,
which leaves both on the record and makes the change explainable rather than
invisible:

```bash
.venv/bin/python -m whul.cli benchmarks compare $OLD $NEW
```

Every score in a group moves by that group's percentage, which is what that
diff is for: it answers "what would adopting this do to the standings" before
you adopt it.

---

## Which seasons get used, and why

Five back from the last completed one. COVID years are excluded, and excluding
one **lengthens the reach** rather than shrinking the pool — so the NBA draws
on 2019 plus 2022-25 rather than on three seasons.

The window sports are judged by the calendar year their league year *ends* in,
which is the year the sport itself calls that season:

| League | Draws from | Why not earlier |
|---|---|---|
| Tennis | 2022-23 → | 2021-22 opens weeks after the displaced Tokyo Olympics |
| PGA | 2021-22 → | 2020-21 holds two Masters; 2019-20 lost the shutdown |
| Motorsports | 2021-22 → | both windows hold parts of the compressed 2020 seasons |

A window the source cannot cover to its end date is dropped and replaced by
reaching one further back. This is not hypothetical: the tennis snapshot stops
at **2026-02-23**, so the 2025-26 window is half-covered, and pooling it would
read as the whole field getting quieter. Refreshing the snapshot through July
2026 restores that window.

---

## Known gaps

Two rostered categories have **no data source at all** — computing them is not
possible yet, and `coverage` says so rather than pretending:

* **Club soccer players** (Premier League, La Liga, Serie A, Bundesliga,
  Ligue 1, MLS). `whul.scoring.soccer.score_players` exists and is tested, but
  nothing loads per-player rows into it. It wants an FBref-shaped frame:
  player, league, season, position, matches, starts, minutes, goals, assists,
  cards.
* **Intl Soccer teams** (men's and women's). No fixture source is wired up.

Until those exist, freezing needs `--force` and those slots score nothing.
