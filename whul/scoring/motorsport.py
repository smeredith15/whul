"""Motorsport scoring -- ports of NASCAR.R and F1.R.

NASCAR and Formula 1 share one roster category and one benchmark, so both are
scored here and pooled downstream. Their native points systems differ, and the
scripts keep them: NASCAR is rescored onto the 2026 scale, while Formula 1 uses
its own championship points, which are already comparable across eras.
"""

from __future__ import annotations

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str

# --- NASCAR ---------------------------------------------------------------
#: The 2026 Cup scale, applied retroactively so historical seasons are
#: comparable with the one being scored: a win is 55, then 35 descending by one
#: to 36th place, and anything beyond that is a single point.
NASCAR_WIN_POINTS = 55
NASCAR_SECOND_POINTS = 35
NASCAR_LAST_SCORING_POSITION = 36
NASCAR_MINIMUM_POINTS = 1
#: Minimum starts for a season to enter the benchmark pool, which keeps
#: part-time entries and one-off substitutes out.
NASCAR_MIN_RACES = 10

# --- Formula 1 ------------------------------------------------------------
#: Championship points by finishing position, plus the sprint allocation.
F1_POINTS = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)
F1_SPRINT_POINTS = (8, 7, 6, 5, 4, 3, 2, 1)
F1_FASTEST_LAP_POINT = 1
F1_FASTEST_LAP_MAX_POSITION = 10


def nascar_points(finish: float | None) -> float:
    """Points for a NASCAR finishing position on the 2026 scale."""
    if finish is None or pd.isna(finish):
        return 0.0
    place = int(finish)
    if place == 1:
        return float(NASCAR_WIN_POINTS)
    if 2 <= place <= NASCAR_LAST_SCORING_POSITION:
        return float(NASCAR_SECOND_POINTS - (place - 2))
    return float(NASCAR_MINIMUM_POINTS)


def f1_points(finish: float | None, sprint: bool = False, fastest_lap: bool = False) -> float:
    """Championship points for a Formula 1 result."""
    if finish is None or pd.isna(finish):
        return 0.0
    place = int(finish)
    table = F1_SPRINT_POINTS if sprint else F1_POINTS
    points = float(table[place - 1]) if 1 <= place <= len(table) else 0.0
    if fastest_lap and not sprint and place <= F1_FASTEST_LAP_MAX_POSITION:
        points += F1_FASTEST_LAP_POINT
    return points


def score_nascar(results: pd.DataFrame, min_races: int = NASCAR_MIN_RACES) -> pd.DataFrame:
    """Season totals per NASCAR driver."""
    if results is None or results.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "driver": resolve_str(results, ["driver", "driver_name", "racer"], required=True),
            "season": resolve_num(results, ["season", "year", "season_year"], required=True).astype(int),
            "finish": resolve_num(results, ["finish", "fin", "position", "pos"]),
            "date": resolve_str(results, ["date", "race_date"]),
        }
    )
    work = work[work["finish"] > 0].copy()
    if work.empty:
        return pd.DataFrame()

    work["race_points"] = work["finish"].map(nascar_points)
    totals = work.groupby(["season", "driver"], as_index=False).agg(
        races_started=("race_points", "size"),
        wins=("finish", lambda s: int((s == 1).sum())),
        top_tens=("finish", lambda s: int((s <= 10).sum())),
        total_points=("race_points", "sum"),
    )
    totals = totals[totals["races_started"] >= min_races]
    totals["league"] = "NASCAR"
    totals["role"] = "Driver"
    return totals.rename(columns={"driver": "player"}).sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True)


def score_f1(results: pd.DataFrame) -> pd.DataFrame:
    """Season totals per Formula 1 driver.

    Uses the feed's own points where present -- the standings endpoint reports
    them directly, and they already account for regulation changes -- and falls
    back to computing from finishing position otherwise.
    """
    if results is None or results.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "player": resolve_str(results, ["driver_name", "driver", "player"], required=True),
            "season": resolve_num(results, ["season", "season_year", "year"], required=True).astype(int),
            "finish": resolve_num(results, ["position", "finish", "pos"]),
            "date": resolve_str(results, ["date", "race_date"]),
        }
    )
    reported = resolve_num(results, ["points"], default=float("nan")).reindex(work.index)
    computed = work["finish"].map(lambda f: f1_points(f))
    work["race_points"] = reported.fillna(computed)

    totals = work.groupby(["season", "player"], as_index=False).agg(
        races_started=("race_points", "size"),
        wins=("finish", lambda s: int((s == 1).sum())),
        total_points=("race_points", "sum"),
    )
    totals["league"] = "F1"
    totals["role"] = "Driver"
    return totals.sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True)


def score_players(nascar: pd.DataFrame, f1: pd.DataFrame) -> pd.DataFrame:
    """Both series together -- they share a roster category and a benchmark."""
    frames = [f for f in (score_nascar(nascar), score_f1(f1)) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    # The normalization group is the shared pool, not the individual series.
    out["norm_league"] = "Motorsports"
    return out
