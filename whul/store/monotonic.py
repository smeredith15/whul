"""What a figure is allowed to do from one day to the next.

A score that falls is not by itself wrong. A club that loses by three has a
worse point differential than it had yesterday; a batter who goes 0-for-4 has
four more at-bats and the same hits, and at-bats are priced at minus one; a
pitcher who is hit around loses WAR. Those are the sport happening, and a
pipeline that refused them would be refusing the truth.

What is always wrong is a *count* going down. A win cannot be un-won, a match
cannot be un-played, a shutout cannot be un-thrown, and a division title cannot
be un-awarded. When one of those falls it is never the sport: it is the feed
forgetting something it used to say, or the pipeline having credited something
before it was final and then taking it back. Both show up in the standings
ledger -- which differences consecutive days -- as a manager losing points for
a day in which nothing bad happened to them.

So this splits every figure into two kinds and checks only the one that can be
checked:

    a COUNT      may only rise within a league year
    a MEASURE    may do anything, because it measures rather than counts

and the split is by exception rather than by enumeration. Anything not named
below is a count. That direction is deliberate: a measure wrongly treated as a
count raises a false alarm, which is loud and cheap; a count wrongly treated as
a measure is a silent loss, which is the whole thing this file exists to stop.
"""

from __future__ import annotations

import math
import re

#: Figures that measure rather than count, and so may fall honestly.
#:
#: Differentials first -- a margin is signed by construction and every team
#: sport here scores one. Then the advanced baseball terms, which are runs
#: above a replacement level and go down when a player plays badly. Then
#: hockey's plus-minus, which is a differential wearing another name.
MEASURES: frozenset[str] = frozenset({
    "point_diff", "run_diff", "goal_diff", "goals_diff", "margin",
    "plus_minus", "war", "offense", "defense", "advanced_share",
    "proration_factor", "schedule_factor", "lift", "top_rung",
    # A rate is a ratio of two counts and falls whenever the denominator wins.
    "era", "whip", "avg", "obp", "slg", "ops", "save_pct", "gaa",
})

#: Suffixes that make a figure a measure whatever it is called. A scorer that
#: adds `xg_diff` tomorrow is covered without anybody remembering to come here.
MEASURE_SUFFIXES: tuple[str, ...] = ("_diff", "_differential", "_pct",
                                     "_rate", "_average", "_share", "_factor")

#: Figures derived from the rest rather than counted themselves. They fall
#: exactly when their inputs say they should, so checking them would flag every
#: honest fall the inputs are allowed to make -- the check is on the inputs.
DERIVED = re.compile(r"^(pts_|points_|total_points$|role_points$|scaled_score$"
                     r"|league_points$|folded$|gross$|counted$)")

#: Fields that identify the row rather than describe it.
IDENTITY: frozenset[str] = frozenset({
    "season", "as_of", "asset_id", "league", "source", "phase", "fetched_at",
    "team_id", "player_id", "athlete_id", "game_id", "contract_year",
    "conference", "opp_conference", "div_rank", "position",
})

#: How much of a fall is rounding rather than a fall. Figures arrive through
#: JSON and through a float, and a count that reads 25.999999 yesterday and 26
#: today has not moved.
TOLERANCE = 1e-6


def is_a_count(name: str) -> bool:
    """Whether this figure may only ever rise inside a league year."""
    key = str(name).strip().lower()
    if not key or key in IDENTITY or key in MEASURES:
        return False
    if DERIVED.match(key):
        return False
    return not key.endswith(MEASURE_SUFFIXES)


def _number(value):
    """The figure as a float, or None where it is not one.

    NaN is not a number here and is not zero either: it is the feed declining
    to say, and comparing it would make every such field look like a fall the
    first time it was filled in. (NaN is also truthy, which this project has
    been caught by three times.)
    """
    if isinstance(value, bool):
        return float(value)
    if not isinstance(value, (int, float)):
        return None
    got = float(value)
    return None if math.isnan(got) or math.isinf(got) else got


def what_went_backwards(before: dict, after: dict) -> list[tuple[str, float, float]]:
    """Every count that is smaller than it was, worst first.

    Only fields both days carry. A field that has appeared since is new rather
    than fallen, and one that has gone is the shape of the row changing --
    which is a different fault, and one that reads as every count falling at
    once if it is mixed in with this.
    """
    fell = []
    for name, value in after.items():
        if not is_a_count(name):
            continue
        was, now = _number(before.get(name)), _number(value)
        if was is None or now is None:
            continue
        if now < was - TOLERANCE:
            fell.append((str(name), was, now))
    return sorted(fell, key=lambda item: item[2] - item[1])


def explain(fell: list[tuple[str, float, float]], shown: int = 3) -> str:
    """The fall, in the words a person reading a report needs."""
    said = ", ".join(f"{name} {was:,.4g} -> {now:,.4g}"
                     for name, was, now in fell[:shown])
    return said + (f" (and {len(fell) - shown} more)" if len(fell) > shown else "")
