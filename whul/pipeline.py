"""The nightly run, and the backfill that rebuilds it.

Two stages, deliberately separate:

**Scoring** turns a day's raw stats into ``daily_scores`` -- one season-to-date
score per asset, on the 0-100 scale, stamped with the benchmark version it was
measured against. This is per-league, because each league has its own formula.

**Rollup** turns ``daily_scores`` into ``slot_scores`` and
``standings_snapshots``. This is league-agnostic: it reads cumulative scores,
splits them across owner stints, and takes the top K per category. Nothing
about it knows what sport it is looking at.

Keeping them apart is what makes the season reconstructible. Raw stats are
append-only and dated, scores are derived, and the rollup is derived from
those -- so a formula fix is a recompute rather than a re-scrape, and the
progression graph can be rebuilt back to the season's first day rather than
starting whenever the app went live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from whul.bestball import score_slots, standings
from whul.config.league import SEASON
from whul.scoring.base import resolve_num
from whul.store import benchmarks as bm
from whul.store import rosters
from whul.store.db import Store, _as_text, _now


@dataclass
class RunReport:
    """What one day's run did, in the terms a person checking it would ask."""

    as_of: date
    scored_assets: int = 0
    slots: int = 0
    managers: int = 0
    benchmark_version: str = ""
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        head = (
            f"{self.as_of}: {self.scored_assets} assets, {self.slots} slots, "
            f"{self.managers} managers, benchmarks {self.benchmark_version or 'none'}"
        )
        return "\n".join([head, *(f"  ! {w}" for w in self.warnings)])


def write_daily_scores(
    store: Store,
    scored: pd.DataFrame,
    season: str,
    as_of: date | str,
    benchmark_version: str,
) -> int:
    """Record one day's season-to-date scores.

    ``scored`` needs ``asset_id`` and ``scaled_score``; ``total_points`` and
    ``postseason_bonus`` are carried when present. Values are cumulative, which
    is what the rollup differences to split a slot between its occupants.
    """
    if scored is None or scored.empty:
        return 0

    # resolve_num rather than DataFrame.get: get returns the *scalar* default
    # when a column is absent, and multiplying or filling that raises on a
    # float and silently broadcasts a string. A league that reports no
    # postseason bonus simply has no such column.
    rows = pd.DataFrame({
        "asset_id": scored["asset_id"].astype(str),
        "season": season,
        "as_of": _as_text(as_of),
        "league_points": resolve_num(scored, ["total_points", "league_points"]),
        "postseason_bonus": resolve_num(scored, ["postseason_bonus"]),
        "scaled_score": resolve_num(scored, ["scaled_score"], required=True),
        "benchmark_version": benchmark_version,
        "computed_at": _now(),
    })
    return store.insert_frame(
        "daily_scores", rows, keys=("asset_id", "season", "as_of")
    )


def cumulative_scores(store: Store, season: str, through: date | str) -> pd.DataFrame:
    """Every asset's score series up to a day, shaped for the rollup.

    ``whul.bestball`` expects ``asset_id``, ``date`` and ``score``, and reads
    the series as cumulative -- differencing it is how a trade splits a slot.
    """
    frame = store.query(
        "SELECT asset_id, as_of AS date, scaled_score AS score "
        "FROM daily_scores WHERE season = ? AND as_of <= ? "
        "ORDER BY asset_id, as_of",
        (season, _as_text(through)),
    )
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame


def roll_up(
    store: Store,
    season: str,
    as_of: date,
    check_overlaps: bool = True,
) -> RunReport:
    """Score every slot for one day and write the standings snapshot.

    The snapshot is stored rather than derived on request, so the progression
    graph shows what the standings actually said on each day -- including the
    days a since-corrected score was live.
    """
    report = RunReport(as_of=as_of)

    version = bm.active_version(store, season)
    if version is None:
        report.warnings.append(
            f"no frozen benchmark version for {season}; scores cannot be "
            f"placed on the 0-100 scale until one is frozen"
        )
        return report
    report.benchmark_version = version.version

    slots = rosters.load_slots(store, season)
    if not slots:
        report.warnings.append(f"no roster slots for {season}")
        return report

    if check_overlaps:
        clashes = rosters.overlaps(store, season)
        if not clashes.empty:
            # Two occupants on one slot double-counts an asset, so this is
            # reported rather than quietly summed.
            report.warnings.append(
                f"{len(clashes)} slot(s) with overlapping occupancy: "
                f"{', '.join(clashes['slot_id'].head(5))}"
            )

    cumulative = cumulative_scores(store, season, as_of)
    if cumulative.empty:
        report.warnings.append(f"no scores recorded on or before {as_of}")
        return report
    report.scored_assets = int(cumulative["asset_id"].nunique())

    scored = score_slots(slots, cumulative, as_of)
    if not scored.empty:
        store.insert_frame(
            "slot_scores",
            pd.DataFrame({
                "slot_id": scored["slot_id"],
                "season": season,
                "as_of": _as_text(as_of),
                "asset_id": scored["asset_id"],
                "score": scored["score"].round(4),
                "counts": scored["counts"].astype(int),
            }),
            keys=("slot_id", "as_of"),
        )
        report.slots = len(scored)

    table = standings(slots, cumulative, as_of)
    if not table.empty:
        store.insert_frame(
            "standings_snapshots",
            pd.DataFrame({
                "manager_id": table["manager"],
                "season": season,
                "as_of": _as_text(as_of),
                "total": table["total"],
                "rank": table["rank"],
            }),
            keys=("manager_id", "season", "as_of"),
        )
        report.managers = len(table)

    return report


def season_days(
    start: date | None = None, end: date | None = None, today: date | None = None
) -> list[date]:
    """Every day of the season up to today.

    Never runs past today: a future day has no scores, and writing an empty
    snapshot for it would put a flat line on the progression graph.
    """
    start = start or SEASON.start
    end = min(end or SEASON.end, today or date.today())
    if end < start:
        return []
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def backfill(
    store: Store,
    season: str,
    start: date | None = None,
    end: date | None = None,
    today: date | None = None,
    verbose: bool = True,
) -> list[RunReport]:
    """Rebuild every snapshot from the season's start.

    This is what a formula fix costs: a recompute, not a re-scrape. It is also
    how the progression graph reaches back to the first day rather than to
    whenever the app went live.

    Overlap checking runs once rather than per day -- it is a property of the
    roster, not of any particular day, and repeating it would turn one warning
    into three hundred identical ones.

    ``today`` caps the run, defaulting to the real one. A future day has no
    scores, and writing an empty snapshot for it would put a flat line on the
    progression graph.
    """
    days = season_days(start, end, today)
    if not days:
        return []

    clashes = rosters.overlaps(store, season)
    reports = []
    for day in days:
        report = roll_up(store, season, day, check_overlaps=False)
        if not clashes.empty and day == days[0]:
            report.warnings.append(
                f"{len(clashes)} slot(s) with overlapping occupancy: "
                f"{', '.join(clashes['slot_id'].head(5))}"
            )
        reports.append(report)
        if verbose and (report.warnings or day == days[-1]):
            print(report, flush=True)
    return reports


def progression(store: Store, season: str) -> pd.DataFrame:
    """Each manager's total by day -- the line graph's data."""
    frame = store.query(
        "SELECT as_of, manager_id, total, rank FROM standings_snapshots "
        "WHERE season = ? ORDER BY as_of, rank",
        (season,),
    )
    if not frame.empty:
        frame["as_of"] = pd.to_datetime(frame["as_of"]).dt.date
    return frame


def contributions(store: Store, season: str, as_of: date | str) -> pd.DataFrame:
    """Each manager's counting slots on one day -- the bar chart's data.

    Bench slots are included with ``counts = 0``, so the chart can show what a
    manager is carrying as well as what is scoring.
    """
    frame = store.query(
        "SELECT s.manager_id, s.category, s.asset_type, ss.slot_id, ss.asset_id, "
        "       ss.score, ss.counts "
        "FROM slot_scores ss JOIN roster_slots s ON s.slot_id = ss.slot_id "
        "WHERE ss.season = ? AND ss.as_of = ? "
        "ORDER BY s.manager_id, s.asset_type, s.category, ss.score DESC",
        (season, _as_text(as_of)),
    )
    return frame
