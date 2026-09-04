"""Computing and freezing a season's benchmarks.

A benchmark is the number every score in its group is divided by, so it decides
what 100 means. Getting it wrong does not produce an error -- it produces a
season of plausible, wrong standings. So this deliberately separates three
acts that a single command would have run together:

**Compute.** Pull several seasons, score them with the league's own formula,
truncate to the buffer pool per normalization group, take the 99th percentile.
Regular-season production only: postseason samples are small and reach a
minority of players, and including them would distort the distribution the
scale is drawn from.

**Review.** The result is printed with the pool each number came from and, if
a previous version exists, how far every score in the group would move. A
benchmark drawn from four players and one drawn from sixty deserve different
amounts of trust, and only the row can say which this was.

**Freeze.** A separate, explicit step. Until then the version exists but
nothing is measured against it.

Which seasons are usable is not the caller's choice: COVID years and, for
tennis, the years whose calendar was rearranged, are filtered out in
``whul.scoring.schedule`` so a league added later cannot be quietly
benchmarked against one.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from whul.config.league import (
    BENCHMARK_MANAGER_COUNT,
    SEASON,
    competitions_for,
    season_start,
)
from whul.scoring.schedule import (
    EARLIEST_SEASON,
    SCHEDULE_CHANGES,
    describe_exclusions,
    scale_benchmarks,
    usable_seasons,
)
from whul.store import benchmarks as store_benchmarks
from whul.store.db import Store

#: How many seasons to draw from. Enough for a stable percentile without
#: reaching into a materially different competitive era.
DEFAULT_SEASONS = 5


@dataclass
class BenchmarkRun:
    """What one league's computation found."""

    league: str
    asset_type: str
    requested: list[int] = field(default_factory=list)
    used: list[int] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    rows: int = 0
    benchmarks: pd.DataFrame | None = None
    scaled_by: float = 1.0
    #: True when the pool was drawn over league-year windows rather than
    #: calendar seasons, which is how the continuous sports are benchmarked.
    windowed: bool = False
    problems: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        unit = "windows" if self.windowed else "seasons"
        lines = [
            f"{self.league} {self.asset_type.lower()}s: "
            f"{self.rows:,} scored rows over {len(self.used)} {unit} "
            f"({', '.join(str(s) for s in self.used) or 'none'})"
        ]
        # The notes are already sentences ("2020 excluded: ..."), so they
        # are printed as they stand rather than re-labelled.
        lines += [f"  {note}" for note in self.excluded]
        if self.scaled_by != 1.0:
            lines.append(f"  benchmarks lifted x{self.scaled_by:.4f} for a schedule change")
        if self.benchmarks is not None and not self.benchmarks.empty:
            lines.append(f"  {'group':<22}{'benchmark':>12}{'pool':>8}")
            for row in self.benchmarks.itertuples():
                flag = "  <- thin" if row.pool_size < THIN_POOL else ""
                lines.append(
                    f"  {row.norm_key:<22}{row.benchmark:>12,.1f}"
                    f"{row.pool_size:>8}{flag}"
                )
        lines += [f"  ! {p}" for p in self.problems]
        return "\n".join(lines)


#: A pool this small makes the 99th percentile close to the single best season
#: in it, which is a different statistic than the one intended.
THIN_POOL = 10


def seasons_for(league: str, count: int = DEFAULT_SEASONS, latest: int | None = None) -> tuple[list[int], list[str]]:
    """The seasons to draw from, and notes on what was left out.

    Counts back from the most recently completed season and keeps taking until
    it has ``count`` usable ones, so excluding a COVID year lengthens the reach
    rather than shrinking the pool.
    """
    latest = latest or (date.today().year - 1)
    floor = EARLIEST_SEASON.get(league, 0)
    wanted: list[int] = []
    skipped: list[int] = []
    year = latest
    while len(wanted) < count and year >= floor and year > latest - 15:
        (wanted if usable_seasons(league, [year]) else skipped).append(year)
        year -= 1
    wanted.reverse()

    notes = describe_exclusions(league, sorted(skipped))
    if len(wanted) < count:
        # Say so rather than quietly returning a shorter pool: four seasons of
        # tennis is a deliberate consequence of the 2022 floor, and a reader
        # comparing it against five seasons of the NFL should be told which.
        reach = f"nothing usable before {floor}" if floor else "the source reaches no further back"
        notes.append(
            f"only {len(wanted)} of {count} seasons available for {league}: {reach}"
        )
    return wanted, notes


def windows_for(league: str, count: int = DEFAULT_SEASONS) -> tuple[list, list[str]]:
    """The league-year windows to draw from, and notes on what was left out.

    Golf, tennis and motorsport run continuously, so their benchmark is drawn
    over the season's own August-to-July window shifted back whole years rather
    than over calendar seasons (see PROJECT_PLAN 2.3). This picks which of those
    shifted windows may be used.

    The window is the league's own, so a sport that starts later than the
    league year opens is benchmarked over the span it will actually play. The
    offseason share then matches between the benchmark and live scoring, which
    is the whole point of drawing it this way.

    A window is judged by the calendar year it *ends* in, which is the year the
    sport itself calls that season: the 2020-21 window holds the February 2021
    Australian Open and the July 2021 Olympics, so it is the one the tennis
    rearrangement disqualifies, while the 2021-22 window holds only the
    September-2021-onward tour and is usable.
    """
    from whul.scoring import window

    # The league's own start, not the league year's: a benchmark drawn over a
    # window the live season will not fill would price the sport against
    # results it can no longer earn.
    live_from = season_start(league)
    # Ask for more than needed: excluded years must lengthen the reach rather
    # than shorten the pool, exactly as they do for a calendar season.
    candidates = window.season_windows(count + 8, start=live_from)

    usable, skipped = [], []
    for candidate in reversed(candidates):
        if len(usable) == count:
            break
        # The live season cannot benchmark itself: no data at or after the
        # season start may enter the scale it is measured against.
        if candidate.end >= live_from:
            continue
        if usable_seasons(league, [candidate.end.year]):
            usable.append(candidate)
        else:
            skipped.append(candidate.end.year)
    usable.reverse()

    notes = describe_exclusions(league, sorted(skipped))
    if len(usable) < count:
        floor = EARLIEST_SEASON.get(league, 0)
        reach = f"nothing usable before {floor}" if floor else "the calendar reaches no further back"
        notes.append(
            f"only {len(usable)} of {count} windows available for {league}: {reach}"
        )
    return usable, notes


#: How much further back to generate a league's own windows when re-deriving
#: them by label, so a label the run kept is still in reach after exclusions.
WINDOW_LOOKBACK = 4


def _totals_per_league(
    scored: pd.DataFrame, windows: list, produces: tuple[str, ...], count: int
) -> pd.DataFrame:
    """Window totals, each league summed over the window *it* plays.

    One pull can serve two leagues that start on different days -- NASCAR opens
    two days after Formula 1 does -- and a shared window would price one of them
    against results it cannot earn. The window labels stay the source's, so the
    pool is still truncated one window at a time.
    """
    from whul.scoring import window

    if len(produces) < 2 or "league" not in scored.columns:
        return window.window_totals(scored, windows)

    # Match on the label, never by position: the run has already dropped any
    # window the source cannot cover, and re-deriving by slicing would quietly
    # put an incomplete one back.
    chosen = {w.label for w in windows}
    frames = []
    for name in produces:
        rows = scored[scored["league"].astype(str) == name]
        if rows.empty:
            continue
        own, _ = windows_for(name, len(windows) + WINDOW_LOOKBACK)
        own = [w for w in own if w.label in chosen] or windows
        frames.append(window.window_totals(rows, own))
    if not frames:
        return window.window_totals(scored, windows)
    return pd.concat(frames, ignore_index=True)


def _window_years(windows) -> list[int]:
    """Every calendar year a set of windows touches -- the unit sources load in."""
    return sorted({y for w in windows for y in (w.start.year, w.end.year)})


def compute_windowed(
    league: str,
    load,
    events,
    produces: tuple[str, ...] = (),
    seasons: int = DEFAULT_SEASONS,
    managers: int = BENCHMARK_MANAGER_COUNT,
    verbose: bool = True,
) -> BenchmarkRun:
    """Pull, score and take the percentile over league-year windows.

    ``events`` returns one dated, scored row per event rather than a season
    total. Each window is truncated to its own buffer pool and the survivors
    pooled -- the same treatment a team sport's season gets, over a denominator
    that matches the season being scored instead of a calendar year that does
    not.

    A window the source cannot cover to its end is dropped, and one more window
    is fetched to replace it. This is the failure that would otherwise go
    unnoticed: a half-covered window looks like a full one with quiet athletes
    in it, and pools a year of half-sized totals into a percentile that then
    reads as the whole field having got worse.
    """
    from whul.scoring import window

    windows, excluded = windows_for(league, seasons)
    run = BenchmarkRun(
        league=league, asset_type="Player",
        requested=[w.label for w in windows], excluded=excluded, windowed=True,
    )
    if not windows:
        run.problems.append(f"no usable league-year windows for {league}")
        return run

    def pull(years: list[int]):
        if verbose:
            print(f"  pulling {league} {years[0]}-{years[-1]}...", flush=True)
        raw = load(years)
        if raw is None or raw.empty:
            return None
        scored = events(raw)
        return None if scored is None or scored.empty else scored

    def problem(exc: Exception, doing: str) -> None:
        run.problems.append(f"could not {doing}: {type(exc).__name__}: {exc}")

    fetched = _window_years(windows)
    try:
        scored = pull(fetched)
    except Exception as exc:  # noqa: BLE001 -- reported, so one league cannot stop the rest
        problem(exc, "load")
        return run
    if scored is None:
        run.problems.append("the source returned no scoreable rows")
        return run

    covered = pd.to_datetime(scored["date"], errors="coerce").max()
    if pd.isna(covered):
        run.problems.append("no event carried a readable date")
        return run
    covered = covered.date()

    short = [w for w in windows if w.end > covered]
    if short:
        # Reach one window further back per incomplete one, and pull only the
        # years that adds. Fetching those up front would cost every run several
        # extra years of a per-date feed to cover a case that usually does not
        # arise.
        windows, excluded = windows_for(league, seasons + len(short))
        # The second selection's notes supersede the first's rather than
        # joining them: both count a shortfall against a different target, and
        # two contradictory counts is worse than either alone.
        run.excluded = excluded + [
            f"{dropped.label} dropped: the source covers only to {covered}, "
            f"short of the window's {dropped.end}"
            for dropped in short
        ]
        extra = [y for y in _window_years(windows) if y not in fetched]
        if extra:
            try:
                more = pull(extra)
            except Exception as exc:  # noqa: BLE001
                more = None
                problem(exc, f"load {extra[0]}-{extra[-1]}")
            if more is not None:
                scored = pd.concat([scored, more], ignore_index=True)

    windows = [w for w in windows if w.end <= covered][-seasons:]
    if not windows:
        run.problems.append(
            f"the source covers only to {covered}; no window ends before that"
        )
        return run
    if len(windows) < seasons:
        run.problems.append(
            f"{len(windows)} complete windows, not {seasons}; the pool is smaller "
            f"than intended and the percentile less stable"
        )

    totals = _totals_per_league(scored, windows, produces, len(windows))
    if totals.empty:
        run.problems.append("no events fell inside any window")
        return run
    run.used = [w.label for w in windows]
    run.rows = len(totals)
    bench = store_benchmarks.compute(
        totals, "Player", season="", season_col="season", managers=managers
    )
    run.benchmarks = bench.sort_values("norm_key").reset_index(drop=True)

    thin = run.benchmarks[run.benchmarks["pool_size"] < THIN_POOL]
    for row in thin.itertuples():
        run.problems.append(
            f"{row.norm_key} drew from {row.pool_size} rows; at that size the "
            f"99th percentile is close to the single best window in the pool"
        )
    return run


def compute(
    league: str,
    load,
    score,
    asset_type: str = "Player",
    seasons: int = DEFAULT_SEASONS,
    latest: int | None = None,
    scale_for: str | None = None,
    managers: int = BENCHMARK_MANAGER_COUNT,
    verbose: bool = True,
) -> BenchmarkRun:
    """Pull, score and take the percentile for one league.

    ``load`` takes a list of seasons and returns raw rows; ``score`` takes those
    and returns scored ones. Both come from the league's own modules -- this
    does not know how any sport works, only how a benchmark is arrived at.

    For a sport whose season already aligns year to year. The ones that run
    continuously go through ``compute_windowed`` instead.
    """
    wanted, excluded = seasons_for(league, seasons, latest)
    run = BenchmarkRun(
        league=league, asset_type=asset_type, requested=wanted, excluded=excluded
    )
    if not wanted:
        run.problems.append(f"no usable seasons for {league}")
        return run

    if verbose:
        print(f"  pulling {league} {wanted}...", flush=True)
    try:
        raw = load(wanted)
    except Exception as exc:  # noqa: BLE001 -- reported, so one league cannot stop the rest
        run.problems.append(f"could not load: {type(exc).__name__}: {exc}")
        return run

    if raw is None or raw.empty:
        run.problems.append("the source returned no rows")
        return run

    try:
        scored = score(raw)
    except Exception as exc:  # noqa: BLE001 -- a bad row must not lose the pull
        run.problems.append(f"could not score: {type(exc).__name__}: {exc}")
        return run
    if scored is None or scored.empty:
        run.problems.append("scoring produced no rows")
        return run

    # Regular-season production only. Where a scorer separates the two, use its
    # split; where it does not, its total already is the regular season.
    if "regular_points" in scored.columns:
        scored = scored.assign(total_points=scored["regular_points"])

    run.used = sorted(int(s) for s in scored["season"].dropna().unique())
    run.rows = len(scored)

    bench = store_benchmarks.compute(
        scored, asset_type, season="", season_col="season", managers=managers
    )
    if scale_for and scale_for in SCHEDULE_CHANGES:
        # ``scale_benchmarks`` records the factor it used in an extra column;
        # the stored table has no room for it, and the version's notes already
        # say a schedule change was applied.
        bench = scale_benchmarks(bench, scale_for).drop(
            columns=["schedule_factor"], errors="ignore"
        )
        run.scaled_by = SCHEDULE_CHANGES[scale_for].factor
    run.benchmarks = bench.sort_values("norm_key").reset_index(drop=True)

    thin = run.benchmarks[run.benchmarks["pool_size"] < THIN_POOL]
    for row in thin.itertuples():
        run.problems.append(
            f"{row.norm_key} drew from {row.pool_size} rows; at that size the "
            f"99th percentile is close to the single best season in the pool"
        )
    return run


def combine(runs: list[BenchmarkRun]) -> pd.DataFrame | None:
    """Every successful run's benchmarks as one frame, or None if there are none."""
    frames = [
        r.benchmarks for r in runs
        if r.benchmarks is not None and not r.benchmarks.empty
    ]
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["asset_type", "norm_key"], keep="last"
    )


def describe(runs: list[BenchmarkRun]) -> str:
    """A one-line summary of what a set of runs covered, for a version's notes.

    Each run keeps its own span rather than merging into one range: a windowed
    sport's labels are league years ("2022-23") and a team sport's are calendar
    seasons, and a single merged range would read as neither.
    """
    kept = [r for r in runs if r.benchmarks is not None and not r.benchmarks.empty]
    parts = []
    for run in sorted(kept, key=lambda r: (r.league, r.asset_type)):
        span = [str(s) for s in run.used]
        if not span:
            covered = "?"
        elif len(span) == 1:
            covered = span[0]
        else:
            # A hyphen already separates the halves of a league-year label, so
            # joining two of them with another one reads as a single range.
            joiner = " to " if any("-" in label for label in span) else "-"
            covered = f"{span[0]}{joiner}{span[-1]}"
        parts.append(f"{run.league} {run.asset_type.lower()}s {covered}")
    return ", ".join(parts)


def save(
    store: Store,
    runs: list[BenchmarkRun],
    season: str,
    version: str | None = None,
    notes: str = "",
) -> str | None:
    """Store every successful run as one unfrozen version.

    One version across all leagues, not one per league: the scale is a single
    artifact that the standings point at, and a half-league version would be
    a scale with holes in it. Where twenty leagues cannot be pulled in one
    sitting, ``extend`` adds to a version already started rather than making a
    second one with a different set of holes.
    """
    combined = combine(runs)
    if combined is None:
        return None
    return store_benchmarks.save(
        store, combined, season, version=version, notes=notes or describe(runs),
    )


def _merge_notes(existing: str, added: str) -> str:
    """The version's contents after a sitting, not a log of the sittings.

    Recomputing a league supersedes it, so listing both spans reads as two
    pools when there is one. The newest mention of each league wins.
    """
    entries: dict[str, str] = {}
    for part in [p.strip() for p in f"{existing}; {added}".replace(";", ",").split(",")]:
        if not part:
            continue
        # "PGA players 2021-22 to 2025-26" -> keyed on "PGA players". The span
        # is however many words it takes to write, so the key is the words
        # before it rather than all but the last.
        words = part.split()
        named = list(itertools.takewhile(lambda w: not w[:1].isdigit(), words))
        entries[" ".join(named) or part] = part
    return ", ".join(entries[k] for k in sorted(entries))


def extend(
    store: Store, runs: list[BenchmarkRun], version: str, notes: str = ""
) -> str | None:
    """Add every successful run's groups to an existing unfrozen version."""
    combined = combine(runs)
    if combined is None:
        return None
    existing = store_benchmarks.get_version(store, version)
    grew = _merge_notes(existing.notes if existing else "", describe(runs))
    store_benchmarks.extend(store, version, combined, notes=notes or grew)
    return version


def coverage(store: Store, version: str, season: str) -> pd.DataFrame:
    """Which rostered leagues a version can score, and which it cannot.

    Matched by league rather than by exact normalization group, because that is
    the level a roster records. An asset carries the league it plays in; its
    position -- and so its group, for the leagues that split by one -- comes
    from the feed when it is scored, and is not known at draft time. A
    positional league therefore counts as covered when any of its groups is
    present, which is the failure this is actually for: a league nobody
    computed, whose managers would quietly score nothing.

    A roster category open to several competitions needs all of them. Twelve
    picks are recorded as "Tennis" because that is the category they were
    drafted into, and nothing on the roster says which tour each plays; an ATP
    benchmark alone would leave every WTA pick among them unscored.
    """
    bench = store_benchmarks.load(store, version)
    have = {(row.asset_type, row.norm_key) for row in bench.itertuples()}

    rostered = store.query(
        "SELECT a.asset_type, a.league, COUNT(*) AS assets "
        "FROM slot_occupancy o "
        "JOIN roster_slots s ON s.slot_id = o.slot_id "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE s.season = ? AND o.end_date IS NULL "
        "GROUP BY a.asset_type, a.league",
        (season,),
    )
    if rostered.empty:
        return rostered.assign(needs=None, covered=None, groups=None)

    def groups_for(asset_type: str, competition: str) -> list[str]:
        return sorted(
            key for kind, key in have
            if kind == asset_type
            and (key == competition or key.startswith(f"{competition}_"))
        )

    needs, groups, covered = [], [], []
    for row in rostered.itertuples():
        wanted = competitions_for(row.league)
        found = {c: groups_for(row.asset_type, c) for c in wanted}
        needs.append(", ".join(wanted))
        groups.append(", ".join(g for c in wanted for g in found[c]))
        covered.append(all(found.values()))
    rostered["needs"] = needs
    rostered["groups"] = groups
    rostered["covered"] = covered
    return rostered.sort_values(
        ["covered", "assets", "league"], ascending=[True, False, True]
    ).reset_index(drop=True)
