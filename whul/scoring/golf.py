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
    _count_finishes(work)
    # Named here rather than only on the season totals, so the window-based
    # benchmark reads the same league and role the season view does.
    work["league"] = "PGA"
    work["role"] = "Golfer"
    return work.reset_index(drop=True)


#: What a profile counts, and what each column is called. The same names the
#: motorsport scorer writes, so one window sums both sports' marks.
FINISH_COUNTS = {1: "wins", 5: "top_fives", 10: "top_tens"}

#: Every per-event mark this scorer writes, for a window to add up. A start is
#: one of them and is not the row count: a season total is grouped by calendar
#: year and a league year is not, so the figure a profile shows has to be the
#: window's own -- summing per-event marks gives the right one for whatever
#: stretch is being asked about.
EVENT_COUNTS = ("starts",) + tuple(FINISH_COUNTS.values()) + ("made_cut",)


def _count_finishes(work: pd.DataFrame) -> None:
    """Mark each tournament so a window can count the good ones."""
    work["starts"] = 1.0
    for place, column in FINISH_COUNTS.items():
        work[column] = ((work["position"] <= place)
                        & (work["position"] > 0)).astype(float)


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
