"""When a competition is over, which is when its bonus may be credited.

The bonus is a rate. After one game it is at its noisiest -- a single Champions
League goal projected to 11.4 points, nearly half a league season -- and a
second game without production *lowers* it. Holding it until the competition
finishes is what stops a score falling because a player took the pitch.
"""

from datetime import date

import pytest

from whul.scoring import completion
from whul.scoring.postseason import RULES


def test_every_competition_that_pays_a_bonus_has_a_finishing_date():
    """One without it is never complete, so its bonus is never credited and
    nothing says why. A rule added later must not go quiet."""
    missing = [name for name in RULES if completion.finishes(name, 2027) is None]
    assert not missing, f"no completion date for {missing}"


@pytest.mark.parametrize("competition,season,ends", [
    # Named for the year it starts in: the 2026 season is settled at a Super
    # Bowl in February 2027.
    ("NFL", 2026, 2027),
    # Named for the year they finish in, crossing the new year.
    ("NBA", 2026, 2026),
    ("NHL", 2026, 2026),
    ("UCL", 2027, 2027),
    # Named for the calendar year they are played inside.
    ("MLB", 2026, 2026),
    ("WNBA", 2026, 2026),
    ("MLS", 2026, 2026),
    ("NWSL", 2026, 2026),
])
def test_a_season_finishes_in_the_year_its_final_is_played(competition, season, ends):
    """Two conventions arriving at the same rule from opposite directions: a
    season crossing the new year is named for its second half, and one played
    inside a calendar year is named for that year. Only the NFL is named for
    the year it starts."""
    assert completion.finishes(competition, season).year == ends


def test_a_competition_still_being_played_is_not_complete():
    assert not completion.is_complete("UCL", 2027, date(2026, 9, 10))


def test_a_competition_past_its_date_is_complete():
    assert completion.is_complete("UCL", 2027, date(2027, 7, 1))


def test_an_unknown_competition_is_never_complete():
    """False rather than True, deliberately. Holding a bonus that was earned is
    a figure arriving late; crediting one that can still fall is a score going
    down, and only one of those is worth risking."""
    assert completion.finishes("Some New Cup", 2027) is None
    assert not completion.is_complete("Some New Cup", 2027, date(2099, 1, 1))
