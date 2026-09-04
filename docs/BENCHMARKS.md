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

### Where to run it

**Any machine with open outbound HTTPS and a real Python.** Two work:

* **The CodeOSS cloud instance.** The obvious choice — it is where every source
  probe in this project was run and came back green, and it is reachable from a
  browser on anything, phone included. Open a terminal in it
  (**Terminal → New Terminal**, or `` Ctrl+` ``) and follow
  [`QUICKSTART.md`](QUICKSTART.md) part A if the checkout is not set up.
* **A Mac.** [`MAC_SETUP.md`](MAC_SETUP.md) walks through it from opening
  Terminal to a working database.

**Not Android.** `pandas`, `pyarrow` and `pyreadr` are compiled extensions and
none of them ships an Android build — `pyreadr`, which reads the tennis
history, publishes only macOS, Windows and manylinux wheels, and manylinux is
not what Termux provides. A phone is a fine way to *drive* the cloud instance
through a browser, but not to run this directly.

**Not the Claude Code development sandbox.** Its network policy answers `403`
to almost every sports host, so only NFL (a GitHub release) and tennis (a local
file) can be computed there. That is a property of that sandbox alone, not of
cloud machines generally.

### What you need in place

Whichever machine you use:

* the repo on branch `claude/fantasy-league-webapp-dp99e3`, with `.venv` built
  and `pytest -q` green — do not compute against a broken checkout. If pytest
  stops saying dependencies are missing, the checkout is fine and the
  environment is stale; run the reinstall command it prints;
* `tennis2026` cloned beside it, for `model_data_snapshot.rds` (the only
  surviving copy of the match history), or `WHUL_TENNIS2026` pointing at it;
* `data/whul.sqlite3` built with `import-rosters --write`, since the benchmark
  set is checked against the roster before it can be frozen.

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

Use `--into` so a run split across sittings builds one scale rather than several
with different holes in them. Bare, it means the season's newest unfrozen
version — the one step 3 started — so there is no id to carry between commands:

```bash
.venv/bin/python -m whul.cli benchmarks compute pga motorsports --save --into
.venv/bin/python -m whul.cli benchmarks compute nhl nhl-teams --save --into
.venv/bin/python -m whul.cli benchmarks compute mlb mlb-teams --save --into
.venv/bin/python -m whul.cli benchmarks compute epl laliga seriea \
    bundesliga ligue1 mls nwsl --save --into
.venv/bin/python -m whul.cli benchmarks compute ncaaf ncaam ncaaw \
    ncaabaseball ncaasoftball --save --into
```

Each prints which version it added to. Pass the id explicitly
(`--into 2026-27-20260903-203037`) when more than one draft version exists and
you mean a particular one.

**The NBA is the long one.** ESPN is queried a date at a time, so five seasons of
box scores is thousands of requests — an hour or more, and a dropped connection
would lose it. Run it detached:

```bash
nohup .venv/bin/python -m whul.cli benchmarks compute nba nba-teams \
    --save --into > ~/nba-benchmarks.log 2>&1 &
tail -f ~/nba-benchmarks.log
```

NBA last so everything else is already banked by the time it starts.

Re-running a league you already added replaces its groups rather than
duplicating them, so a league you want to redo is just the same command again.

---

## Step 5 — check the version against the roster

```bash
.venv/bin/python -m whul.cli benchmarks versions        # the id, if you lost it
.venv/bin/python -m whul.cli benchmarks coverage <version>
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
.venv/bin/python -m whul.cli benchmarks freeze <version>
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
.venv/bin/python -m whul.cli benchmarks compare <old> <new>
```

Every score in a group moves by that group's percentage, which is what that
diff is for: it answers "what would adopting this do to the standings" before
you adopt it.

---

## Which seasons get used, and why

Five back from the last completed one. COVID years are excluded, and excluding
one **lengthens the reach** rather than shrinking the pool — so the NBA draws
on 2019 plus 2022-25 rather than on three seasons.

### One league, one distribution

Every league is measured against its own history and nothing pools two
together. ATP is normalized against ATP and WTA against WTA; F1 against F1 and
NASCAR against NASCAR; each club soccer league against itself. A tennis pull
scores both tours in one pass and produces two benchmarks from it, which is why
`benchmarks list` shows one source named `tennis` and `coverage` reports groups
called `ATP` and `WTA`.

A roster category can still be open to more than one of them. The draft sheet
records twelve picks as "Tennis" and three as "Motorsports", because that is
the category they were drafted into, and nothing on the roster says which tour
or series each plays. Those categories therefore count as covered only when
*every* competition they admit has a benchmark — an ATP benchmark alone would
leave every WTA pick among the twelve unscored.

The buffer pool is still sized from the roster category, so each tour draws the
depth the whole category allows rather than a share of it: tennis has three
slots per manager, and ATP and WTA each draw 68 per window rather than roughly
34 apiece. **That is deliberate.** The pool answers "who could plausibly be
drafted", not "how many were", and any of the top ~45 ATP players could
plausibly fill a tennis slot — the slots are not rationed by tour. `Target_N`
is the exact rostered need and the ×1.5 buffer is precisely the reach-and-bench
expansion on top of it, so splitting the rate between the tours would count the
same slot twice.

The cost of that choice is small and known: measured over the 2022-25 windows,
a per-tour pool sits about 1.5% below a half-sized one on ATP and 3.4% on WTA,
which is how much tennis scores run high against a sport whose category is a
single league.

### Which seasons

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
