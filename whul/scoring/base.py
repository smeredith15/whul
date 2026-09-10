"""Shared helpers for the per-league scoring modules.

Upstream feeds rename columns between releases -- nflverse has shipped both
``passing_interceptions`` and ``interceptions``, and both ``team`` and
``recent_team``. The R scripts handled this with ``get_num_col`` / ``get_char_col``
candidate lists; these are the Python equivalents, so a rename upstream degrades
into a clear error rather than a silently zeroed stat.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def resolve_num(
    df: pd.DataFrame,
    candidates: list[str],
    default: float = 0.0,
    required: bool = False,
) -> pd.Series:
    """First present candidate column, coerced to numeric."""
    for col in candidates:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").fillna(default)
    if required:
        raise KeyError(f"None of {candidates} present; have {sorted(df.columns)[:25]}")
    return pd.Series(default, index=df.index, dtype="float64")


def resolve_str(
    df: pd.DataFrame,
    candidates: list[str],
    default: str = "",
    required: bool = False,
) -> pd.Series:
    """First present candidate column, coerced to string."""
    for col in candidates:
        if col in df.columns:
            return df[col].astype("string").fillna(default)
    if required:
        raise KeyError(f"None of {candidates} present; have {sorted(df.columns)[:25]}")
    return pd.Series(default, index=df.index, dtype="string")


#: Where a schedule keeps the day a game is played, in the order they are tried.
#: The feeds disagree, which is what this module exists for.
DATE_COLUMNS = ("gameday", "game_date", "date", "start_date")

#: Both halves of a completed game, per feed.
SCORE_COLUMNS = (("home_score", "away_score"), ("points_for", "points_against"))


def settled_seasons(schedule: pd.DataFrame, today: date | None = None) -> set[int]:
    """Seasons whose outcome is decided: every game played, or in the past.

    A season *title* is not a running total. Until the last game is played
    nobody has won a division or a conference, and awarding one to whoever
    leads what has been played so far pays fifteen points for a standing that
    does not exist yet -- in week one, to a team that lost.

    Read off the schedule rather than the calendar, because the fixture list is
    already in hand and says exactly when the question becomes answerable.

    "Or in the past" is what keeps a completed season completed. A game with no
    result and a date that has gone is a result the feed is never going to
    supply -- a cancellation, a postponement nobody rescheduled -- and treating
    one of those as an open question would leave a finished season unsettled
    for ever, which would quietly change every benchmark drawn from it.

    A frame with no dates at all cannot answer this, and says so by settling
    nothing: withholding a title that was won is a visible undercount, and
    awarding one that was not is the invisible kind.
    """
    if schedule is None or schedule.empty or "season" not in schedule.columns:
        return set()
    seasons = pd.to_numeric(schedule["season"], errors="coerce")
    everything = {int(s) for s in seasons.dropna().unique()}

    played = _has_a_result(schedule)
    if played is None:
        return set()
    column = next((c for c in DATE_COLUMNS if c in schedule.columns), None)
    if column is None:
        return set()
    days = pd.to_datetime(schedule[column], errors="coerce").dt.date
    now = today or date.today()
    # An unplayed game still to come is the only thing that can change a title.
    open_still = ~played & days.notna() & days.map(lambda d: d >= now)
    return everything - {int(s) for s in seasons[open_still].dropna().unique()}


def _has_a_result(schedule: pd.DataFrame):
    """Which rows carry a result, or None where the frame cannot say."""
    if "completed" in schedule.columns:
        return schedule["completed"].fillna(False).astype(bool)
    for home, away in SCORE_COLUMNS:
        if home in schedule.columns and away in schedule.columns:
            return (pd.to_numeric(schedule[home], errors="coerce").notna()
                    & pd.to_numeric(schedule[away], errors="coerce").notna())
    return None
