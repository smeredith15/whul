"""Benchmarks drawn over the league year's own calendar window.

Golf, tennis and motorsport have no season in the sense the team sports do.
They run more or less continuously, so a league year that starts in August and
ends the following July contains a different proportion of offseason than a
July-to-July one does. Scaling a season total by elapsed weeks would misprice
them systematically -- 47/52 assumes the missing five weeks were as productive
as the rest, and for these sports they are not.

The fix is not a correction factor but a matching denominator: compute the
benchmark over the *same* calendar window the season uses. For each of the last
N years, sum each athlete's event points over the window shifted back that many
years, then take the percentile of those window totals. The offseason
proportion is then identical in the benchmark and in live scoring, so there is
no factor left to get wrong -- and a future season with different dates needs
no special handling, because it uses its own.

This needs event-level data with dates rather than season aggregates, which all
three sports have.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from whul.config.league import SEASON

#: Leagues benchmarked this way. The team sports keep season aggregates, whose
#: seasons already align year to year.
WINDOW_LEAGUES = ("PGA", "Tennis", "Motorsports", "ATP", "WTA", "NASCAR", "F1")

#: How many prior windows to draw the pool from. Enough to be stable without
#: reaching back into a materially different competitive era.
DEFAULT_YEARS = 5


@dataclass(frozen=True)
class Window:
    """One league year, as an inclusive date range."""

    label: str
    start: date
    end: date

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


def _shift_years(day: date, years: int) -> date:
    """The same calendar day, ``years`` earlier.

    February 29 has no counterpart in a common year, so it moves to the 28th --
    the alternative is March 1, which would put the day in the wrong month and
    could shift it across a window boundary.
    """
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def season_windows(
    years: int = DEFAULT_YEARS,
    start: date | None = None,
    end: date | None = None,
) -> list[Window]:
    """The current league year and the ``years`` before it.

    Each is the season's own window shifted back whole years, so every one
    spans the same months and the same share of offseason.
    """
    start = start or SEASON.start
    end = end or SEASON.end
    windows = []
    for back in range(years, -1, -1):
        window_start = _shift_years(start, back)
        window_end = _shift_years(end, back)
        windows.append(
            Window(label=f"{window_start.year}-{str(window_end.year)[-2:]}",
                   start=window_start, end=window_end)
        )
    return windows


def assign_windows(
    events: pd.DataFrame,
    windows: list[Window],
    date_col: str = "date",
) -> pd.DataFrame:
    """Label each event with the window it falls in, dropping those outside.

    Windows do not overlap, so an event belongs to at most one. Events between
    two windows -- in the offseason gap, or before the earliest window -- are
    dropped rather than pulled into the nearest, which would inflate whichever
    window absorbed them.
    """
    if events is None or events.empty:
        return pd.DataFrame()

    work = events.copy()
    days = pd.to_datetime(work[date_col], errors="coerce")
    work["_day"] = days.dt.date
    work["window"] = None
    for window in windows:
        inside = work["_day"].map(
            lambda d, w=window: d is not None and not pd.isna(d) and w.contains(d)
        )
        work.loc[inside & work["window"].isna(), "window"] = window.label
    return work[work["window"].notna()].drop(columns=["_day"]).reset_index(drop=True)


def window_totals(
    events: pd.DataFrame,
    windows: list[Window] | None = None,
    id_col: str = "player",
    points_col: str = "event_points",
    date_col: str = "date",
    league_col: str = "league",
    role_col: str = "role",
) -> pd.DataFrame:
    """Per-athlete totals per window, shaped for ``compute_benchmarks``.

    The output's ``season`` column is the window label, which is what lets the
    benchmark machinery truncate each window to its own buffer pool before
    pooling the survivors -- the same treatment a team sport's season gets.
    """
    windows = windows or season_windows()
    labelled = assign_windows(events, windows, date_col=date_col)
    if labelled.empty:
        return pd.DataFrame(
            columns=["season", id_col, "league", "role", "total_points", "events"]
        )

    group = ["window", id_col]
    for column in (league_col, role_col):
        if column in labelled.columns:
            group.append(column)

    totals = labelled.groupby(group, as_index=False).agg(
        total_points=(points_col, "sum"),
        events=(points_col, "size"),
    )
    totals = totals.rename(columns={"window": "season"})
    if league_col not in totals.columns:
        totals[league_col] = ""
    if role_col not in totals.columns:
        totals[role_col] = ""
    return totals.sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True)


def describe(windows: list[Window]) -> list[str]:
    """One line per window, for a report that has to be checked by a person."""
    return [f"{w.label}: {w.start.isoformat()} -> {w.end.isoformat()}" for w in windows]
