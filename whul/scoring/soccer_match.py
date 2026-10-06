"""Club soccer player scoring, second version: one match at a time.

The values are the league's (``docs/PROJECT_PLAN.md`` section 2.8); the
figures are FotMob's, as ``whul.sources.fotmob.match_lines`` reads them. A
season is the sum of its matches, so a match that has not been read yet holds
a player's total rather than lowering it.

Goalkeepers are not rostered and score nothing here.
"""

from __future__ import annotations

import math

#: Values that differ by position: defender, midfielder, forward.
BY_POSITION = {
    "goal":               {"D": 6.0, "M": 5.0, "F": 4.0},
    # Paid per non-penalty goal as k x (1 - that shot's xG): a 0.05 strike is
    # worth nearly k on top of the goal, a tap-in little.
    "highlight":          {"D": 4.0, "M": 3.0, "F": 2.0},
    "assist":             {"D": 5.0, "M": 3.0, "F": 3.0},
    "clean_sheet":        {"D": 2.0, "M": 1.0, "F": 0.0},
    # Each goal conceded while on the pitch after the first.
    "conceded_after_one": {"D": -0.5, "M": -0.25, "F": 0.0},
}

#: Values the same for everyone, per one of each.
FLAT = {
    "chance_created": 0.5,   # a pass leading to a shot, other than an assist
    "shot_on_target": 0.5,
    "tackle": 0.5,
    "interception": 0.5,
    "shot_block": 0.5,
    "clearance": 0.25,
    "dribble": 0.5,
    "dispossessed": -0.4,
    "own_goal": -2.0,
    "yellow": -1.0,
    "red": -3.0,
    "rating_8": 1.0,         # a FotMob rating of 8.0 or higher
    "player_of_match": 1.0,
}

PTS_FULL_APPEARANCE = 2.0
PTS_SHORT_APPEARANCE = 1.0
FULL_APPEARANCE_MINUTES = 60
#: Minutes on the pitch a clean sheet needs.
CLEAN_SHEET_MINUTES = 60
RATING_BONUS_FROM = 8.0

SCORED_POSITIONS = ("D", "M", "F")


def _n(line: dict, key: str) -> float:
    value = line.get(key)
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(out) else out


def components(line: dict) -> dict[str, float]:
    """What each part of one match line earned. Empty for a goalkeeper, a line
    with no position, or a player who did not play."""
    position = str(line.get("position") or "")
    minutes = _n(line, "minutes")
    if position not in SCORED_POSITIONS or minutes <= 0:
        return {}
    goals, assists = _n(line, "goals"), _n(line, "assists")
    conceded = _n(line, "conceded_on")
    rating = line.get("rating")
    out = {
        "appearance": (PTS_FULL_APPEARANCE if minutes >= FULL_APPEARANCE_MINUTES
                       else PTS_SHORT_APPEARANCE),
        "goals": goals * BY_POSITION["goal"][position],
        "highlight": sum(1.0 - min(max(float(x), 0.0), 1.0)
                         for x in line.get("goal_xg") or []) * BY_POSITION["highlight"][position],
        "assists": assists * BY_POSITION["assist"][position],
        "chances_created": max(_n(line, "chances_created") - assists, 0.0)
                           * FLAT["chance_created"],
        "shots_on_target": _n(line, "shots_on_target") * FLAT["shot_on_target"],
        "clean_sheet": (BY_POSITION["clean_sheet"][position]
                        if minutes >= CLEAN_SHEET_MINUTES and conceded == 0 else 0.0),
        "conceded": max(conceded - 1.0, 0.0) * BY_POSITION["conceded_after_one"][position],
        "tackles": _n(line, "tackles") * FLAT["tackle"],
        "interceptions": _n(line, "interceptions") * FLAT["interception"],
        "shot_blocks": _n(line, "shot_blocks") * FLAT["shot_block"],
        "clearances": _n(line, "clearances") * FLAT["clearance"],
        "dribbles": _n(line, "dribbles") * FLAT["dribble"],
        "dispossessed": _n(line, "dispossessed") * FLAT["dispossessed"],
        "own_goals": _n(line, "own_goals") * FLAT["own_goal"],
        "cards": _n(line, "yellow") * FLAT["yellow"] + _n(line, "red") * FLAT["red"],
        "rating": (FLAT["rating_8"] if rating is not None
                   and float(rating) >= RATING_BONUS_FROM else 0.0),
        "player_of_match": FLAT["player_of_match"] if line.get("potm") else 0.0,
    }
    return {k: round(v, 4) for k, v in out.items()}


def match_points(line: dict) -> float:
    """One match line's points."""
    return round(sum(components(line).values()), 4)
