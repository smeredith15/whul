"""What an athlete actually did, for the profile window.

A points total says how much; it does not say what happened. "Daytona 500 4th ·
US Open R16 · Masters 2nd" is the natural thing to want from a profile, and the
rows behind it already exist -- ``tennis.match_events``, ``golf.score_events``
and ``motorsport.race_events`` each return one dated, scored row per event.
They were written for the window benchmarks, which sum them and throw the
detail away.

**Tennis is summarized per tournament, not per match.** Its event rows are one
per match, so a run to a final is seven of them; a reader wants one line saying
how far the player got and what the week was worth. Losing the final reads

    ATP Winston Salem 250 · F · 150

-- the furthest round reached, and every point earned there, straight-sets
bonuses included. Golf and motorsport already have one row per event, so their
finish *is* the row.

A loss is kept. A player who went out in the first round has played, and a
profile that omits him reads exactly like one for a player who is injured and
did not enter.
"""

from __future__ import annotations

import pandas as pd

#: Rounds from earliest to latest, so "furthest reached" is a max rather than a
#: string comparison. Qualifying sits below the main draw, and W is the title.
ROUND_ORDER = (
    "Q1", "Q2", "Q3", "RR", "R128", "R64", "R32", "R16", "QF", "SF", "F", "W",
)
_ROUND_RANK = {name: index for index, name in enumerate(ROUND_ORDER)}

#: How many finishes a profile carries. A season of tennis is fifty-odd
#: tournaments and the window is scrolled, not read end to end.
MAX_FINISHES = 40


def _round_rank(value) -> int:
    return _ROUND_RANK.get(str(value or "").strip().upper(), -1)


def _tier(category) -> str:
    """The tournament's tier, minus the tour that is already in the league."""
    text = str(category or "").strip()
    for prefix in ("ATP ", "WTA "):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def tennis_finishes(events: pd.DataFrame) -> pd.DataFrame:
    """One row per player per tournament: how far they got, and what it paid.

    The furthest round is taken across every match at that tournament, won or
    lost, because losing in the final is still reaching the final -- and the
    losing row is the only one that records having got there.
    """
    if events is None or events.empty:
        return pd.DataFrame()

    work = events.copy()
    for column in ("tournament", "category", "round", "league"):
        if column not in work.columns:
            work[column] = ""
    work["_rank"] = work["round"].map(_round_rank)

    grouped = work.groupby(
        ["player", "tournament", "category", "league"], as_index=False
    ).agg(
        points=("event_points", "sum"),
        date=("date", "max"),
        _rank=("_rank", "max"),
        matches=("event_points", "size"),
    )
    grouped["round"] = grouped["_rank"].map(
        lambda r: ROUND_ORDER[r] if 0 <= r < len(ROUND_ORDER) else ""
    )
    grouped["label"] = [
        " ".join(part for part in (
            str(row.league or ""), str(row.tournament or ""),
            _tier(row.category), str(row.round or ""),
        ) if part).strip()
        for row in grouped.itertuples()
    ]
    return grouped.drop(columns=["_rank"])


#: How a finishing position reads: 1st, 2nd, 3rd, 4th.
def ordinal(position) -> str:
    try:
        number = int(float(position))
    except (TypeError, ValueError):
        return ""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def event_finishes(events: pd.DataFrame, position_col: str) -> pd.DataFrame:
    """Golf and motorsport: the row already is the finish."""
    if events is None or events.empty:
        return pd.DataFrame()

    work = events.copy()
    for column in ("tournament", "league"):
        if column not in work.columns:
            work[column] = ""
    where = work[position_col] if position_col in work.columns else pd.Series("", index=work.index)
    work["round"] = [ordinal(v) for v in where]
    work["points"] = pd.to_numeric(work["event_points"], errors="coerce").fillna(0.0)
    work["label"] = [
        " ".join(part for part in (
            str(row.tournament or "").strip() or str(row.league or ""),
            str(row.round or ""),
        ) if part).strip()
        for row in work.itertuples()
    ]
    return work[["player", "label", "points", "date"]]


def as_records(finishes: pd.DataFrame, id_col: str = "player") -> dict[str, list[dict]]:
    """``{athlete: [{label, points, date}, ...]}``, newest first.

    Newest first because a profile is opened to see what just happened; the
    Masters in April is context, last weekend is the question.
    """
    if finishes is None or finishes.empty:
        return {}

    out: dict[str, list[dict]] = {}
    ordered = finishes.sort_values("date", ascending=False, kind="mergesort")
    for athlete, block in ordered.groupby(id_col, sort=False):
        out[str(athlete)] = [
            {
                "label": str(row.label),
                "points": round(float(row.points), 1),
                "date": str(row.date)[:10],
            }
            for row in block.head(MAX_FINISHES).itertuples()
        ]
    return out


def summarize(events: pd.DataFrame) -> dict[str, list[dict]]:
    """Finishes for whichever individual sport these rows came from.

    Dispatched on the columns rather than on a league name: the three scorers
    have different vocabularies -- a round, a position, a finish -- and the
    column that is present is what says which sport wrote the frame. A league
    name would have to be kept in step by hand.
    """
    if events is None or events.empty:
        return {}
    if "round" in events.columns:
        return as_records(tennis_finishes(events))
    for column in ("position", "finish"):
        if column in events.columns:
            return as_records(event_finishes(events, column))
    return {}
