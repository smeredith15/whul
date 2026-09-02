"""PGA scoring -- port of PGA.R.

Points come from finishing position on a fixed table for the top 30, with majors
and the Players Championship worth half again as much.

Golf is one of the three individual sports whose benchmark is drawn over the
league year's actual calendar window rather than a season, because an
August-to-July window contains a different proportion of offseason than a
July-to-July one -- see the project plan's normalization section.
"""

from __future__ import annotations

import re

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str

#: Points by finishing position, 1st through 30th. Nothing below 30th scores.
FINISH_POINTS = (
    500, 300, 190, 135, 110, 100, 90, 85, 80, 75,
    70, 65, 60, 57, 54, 51, 48, 45, 42, 39,
    36, 33, 30, 27, 24, 21, 18, 15, 12, 10,
)
SCORING_POSITIONS = len(FINISH_POINTS)

MAJOR_MULTIPLIER = 1.5
MAJOR_PATTERN = re.compile(
    r"masters|pga championship|u\.?s\.? open|open championship|players", re.IGNORECASE
)

#: A made cut, for reporting rather than scoring.
CUT_POSITION = 70
#: Minimum starts for a season to enter the benchmark pool.
MIN_EVENTS = 8


def finish_points(position: float | None) -> float:
    """Points for a finishing position, zero outside the top 30."""
    if position is None or pd.isna(position):
        return 0.0
    place = int(position)
    if 1 <= place <= SCORING_POSITIONS:
        return float(FINISH_POINTS[place - 1])
    return 0.0


def parse_position(value) -> float | None:
    """Finishing position from strings like '12', 'T12', '1'.

    Ties share the position they are tied at, as the R script does -- a five-way
    tie for 3rd pays each player 3rd-place points rather than splitting them.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return float(digits) if digits else None


def score_events(results: pd.DataFrame) -> pd.DataFrame:
    """Points per player per tournament.

    Expects ``player``, ``tournament``, ``position`` and ``date``.
    """
    if results is None or results.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "player": resolve_str(results, ["player", "player_display", "athlete"], required=True),
            "tournament": resolve_str(results, ["tournament", "tourney_str", "event"]),
            "date": resolve_str(results, ["date", "event_date"]),
            "season": resolve_num(results, ["season", "season_year"], default=0).astype(int),
            "position_raw": resolve_str(results, ["position", "pos_str", "place"]),
        }
    )
    work["position"] = work["position_raw"].map(parse_position)
    work = work[work["position"].notna()].copy()
    if work.empty:
        return pd.DataFrame()

    work["base_points"] = work["position"].map(finish_points)
    work["is_major"] = work["tournament"].fillna("").str.contains(MAJOR_PATTERN)
    work["event_points"] = work["base_points"] * work["is_major"].map(
        {True: MAJOR_MULTIPLIER, False: 1.0}
    )
    work["made_cut"] = work["position"] <= CUT_POSITION
    return work.reset_index(drop=True)


def score_players(results: pd.DataFrame, min_events: int = MIN_EVENTS) -> pd.DataFrame:
    """Season totals per golfer."""
    events = score_events(results)
    if events.empty:
        return pd.DataFrame()

    totals = events.groupby(["season", "player"], as_index=False).agg(
        events_played=("event_points", "size"),
        cuts_made=("made_cut", "sum"),
        wins=("position", lambda s: int((s == 1).sum())),
        top_tens=("position", lambda s: int((s <= 10).sum())),
        total_points=("event_points", "sum"),
    )
    totals = totals[totals["events_played"] >= min_events]
    totals["league"] = "PGA"
    totals["role"] = "Golfer"
    return totals.sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True)
