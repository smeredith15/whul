"""Which of a feed's seasons a league year touches.

Every case here is a wrong answer that would have looked right: a complete,
plausible season, returned without an error, for a league year it does not
belong to.
"""

from datetime import date

import pytest

from whul.benchmark_sources import SOURCES
from whul.sources import season_window

YEAR_OPENS = date(2026, 8, 21)
YEAR_CLOSES = date(2027, 7, 13)


def test_a_season_named_for_the_year_it_ends_is_found_by_its_play():
    """The NHL's 2026-27 season is labelled 2027 and is played from October
    2026. Asking for the calendar year gets 2026 -- the season that finished in
    June, whose summaries are aggregates with no date to filter on, so every
    player would be credited with a whole previous season."""
    nhl = ((10, 1), (6, 30), "ends")
    assert season_window.overlapping(nhl, YEAR_OPENS, date(2026, 11, 15)) == [2027]
    assert season_window.overlapping(nhl, YEAR_OPENS, date(2027, 6, 1)) == [2027]


def test_a_season_named_for_the_year_it_starts_keeps_its_label_into_january():
    """The NFL season played in January and February 2027 is the 2026 season.
    The calendar year asks for 2027, which has not been played."""
    nfl = ((8, 1), (2, 20), "starts")
    assert season_window.overlapping(nfl, YEAR_OPENS, date(2027, 2, 10)) == [2026]


def test_a_calendar_year_league_spans_two_of_them():
    """MLS runs February to December, so a league year opening in August holds
    a club through the tail of one season and the front of the next."""
    mls = ((2, 20), (12, 15), "within")
    assert season_window.overlapping(mls, YEAR_OPENS, date(2026, 9, 5)) == [2026]
    assert season_window.overlapping(mls, YEAR_OPENS, date(2027, 3, 15)) == [2026, 2027]


def test_a_league_that_has_not_started_returns_nothing():
    """Not an error and not an empty feed: the NBA tips off in October, and in
    September there is no season of it inside the league year at all."""
    nba = ((10, 1), (6, 30), "ends")
    assert season_window.overlapping(nba, YEAR_OPENS, date(2026, 9, 5)) == []


def test_a_span_never_reaches_past_its_own_edges():
    """The season after the league year is not part of it, however close."""
    nba = ((10, 1), (6, 30), "ends")
    assert 2028 not in season_window.overlapping(nba, YEAR_OPENS, YEAR_CLOSES)


@pytest.mark.parametrize("key,day,expected", [
    # The two that were being asked for the wrong season entirely.
    ("nhl", date(2026, 11, 15), [2027]),
    ("nhl-teams", date(2026, 11, 15), [2027]),
    ("nba", date(2026, 11, 15), [2027]),
    ("nfl", date(2027, 1, 20), [2026]),
    ("nfl-teams", date(2027, 1, 20), [2026]),
    # And the ones that already were right, held in place.
    ("nfl", date(2026, 9, 5), [2026]),
    ("nba-teams", date(2026, 12, 1), [2027]),
    ("epl", date(2027, 3, 15), [2027]),
    ("mls", date(2027, 3, 15), [2026, 2027]),
    ("nwsl", date(2027, 3, 15), [2026, 2027]),
])
def test_each_source_asks_its_feed_for_the_season_being_played(key, day, expected):
    assert SOURCES[key].seasons_for(day) == expected


def test_no_source_is_left_asking_for_the_calendar_year():
    """A source with no ``seasons_for`` falls back to ``[as_of.year]``, which
    is the right season only for a feed that numbers within a calendar year --
    and none of these do. The windowed sources are the exception by design:
    they fetch both calendar years and sum by date over the league year, so the
    season label never decides anything for them.

    This is the guard that stops a new league being added with the default and
    quietly scoring the wrong season for four months."""
    unwindowed = {
        key for key, source in SOURCES.items()
        if source.seasons_for is None and not source.windowed
    }
    assert unwindowed == set(), (
        f"these sources still ask for the calendar year: {sorted(unwindowed)}"
    )
