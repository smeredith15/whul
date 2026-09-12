"""Which of a feed's seasons a league year touches.

Every case here is a wrong answer that would have looked right: a complete,
plausible season, returned without an error, for a league year it does not
belong to.
"""

from datetime import date, timedelta

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
    ("nfl", date(2026, 9, 10), [2026]),
    # Before the NFL opens, nothing -- not season 2026.
    #
    # This read [2026] until the inverted-span fix, and only because of it: the
    # span runs from the day results start counting (10 September) to today, so
    # on the 5th it ran backwards, and a backwards span matched every window
    # instead of none. The feed window opens in August deliberately, to take in
    # the preseason weeks nflverse publishes -- but WHUL counts from the 10th,
    # so those weeks belong to nobody and asking for them got the season's file
    # before it existed. That is what the nightly run reported as "nflverse has
    # no player stats for [2026]" every morning of the first three weeks.
    ("nfl", date(2026, 9, 5), []),
    ("nba-teams", date(2026, 12, 1), [2027]),
    ("epl", date(2027, 3, 15), [2027]),
    # Drafted for 2027, so the 2026 season they are playing now is not asked
    # for at all -- and before 2027 opens they ask for nothing, which is how a
    # club picked for a season nobody has played scores zero rather than being
    # paid for the wrong one.
    ("mls", date(2027, 3, 15), [2027]),
    ("nwsl", date(2027, 3, 15), [2027]),
    ("mls", date(2026, 9, 5), []),
    ("nwsl", date(2026, 9, 5), []),
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


# --- a league that has not opened yet --------------------------------------

def test_a_span_that_ends_before_it_begins_is_empty():
    """The callers ask from the day a league's results start counting to
    today, so every day before a league opens inverts the span. An inverted
    span is not merely empty here -- both comparisons are satisfied by any
    window straddling the two dates, so it matches *more* than a real one."""
    from whul.sources.season_window import overlapping

    window = ((9, 15), (6, 30), "ends")
    assert overlapping(window, date(2026, 9, 29), date(2026, 9, 28)) == []
    # ...and the day it opens is not inverted, so it answers.
    assert overlapping(window, date(2026, 9, 29), date(2026, 9, 29)) == [2027]


#: Leagues whose start date is the day their season actually opens.
#:
#: MLS and the NWSL are excluded, and are not oversights: they were drafted for
#: their 2027 seasons and their start date is the first day that can only
#: belong to 2027, not a kickoff. Their seasons open in February, so answering
#: nothing on 1 January is the right answer.
OPENING_DAYS = [
    ("nfl", "NFL"), ("nfl-teams", "NFL"),
    ("nba", "NBA"), ("nba-teams", "NBA"),
    ("nhl", "NHL"), ("nhl-teams", "NHL"),
    ("ncaaf", "NCAAF"), ("ncaam", "NCAAM"), ("ncaaw", "NCAAW"),
    ("ncaabaseball", "NCAA Baseball"), ("ncaasoftball", "NCAA Softball"),
    ("epl", "Premier League"), ("laliga", "La Liga"), ("seriea", "Serie A"),
    ("bundesliga", "Bundesliga"), ("ligue1", "Ligue 1"),
]


@pytest.mark.parametrize("key,league", OPENING_DAYS)
def test_a_league_is_pulled_from_the_day_it_opens(key, league):
    """A feed window is written per feed and a start date per league, by
    different hands at different times, and nothing made them agree.

    The NHL is why this exists. Its window opened 1 October and its 2026-27
    season opens 29 September, so on opening night the source answered "no NHL
    season has been played inside this league year yet" -- naming the very date
    that had passed -- and did not pull until the 1st. Season totals meant the
    games arrived late rather than never, which is the kind of wrong that is
    only ever noticed by someone looking at the standings on opening weekend.
    """
    from whul.benchmark_sources import SOURCES
    from whul.config.league import season_start

    opens = season_start(league)
    assert SOURCES[key].seasons_for(opens), f"{key} pulls nothing on {opens}"
    assert not SOURCES[key].seasons_for(opens - timedelta(days=1)), (
        f"{key} pulls a season the day before {league} opens"
    )
