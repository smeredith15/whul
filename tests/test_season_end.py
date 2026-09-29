"""When a season is over, and when a score can no longer change."""

from datetime import date

from whul import season_end as se


def test_every_team_sport_has_a_last_day_and_no_individual_sport_does():
    for league in ("NFL", "NBA", "NHL", "MLB", "Premier League", "MLS", "NWSL",
                   "NCAAF", "NCAAM", "NCAAW", "NCAA Baseball", "NCAA Softball",
                   "Men's Intl Soccer", "Women's Intl Soccer"):
        assert se.finished_by(league) is not None, league
    for league in ("PGA", "ATP", "WTA", "Tennis", "F1", "NASCAR", "Motorsports"):
        assert se.finished_by(league) is None, league


def test_a_european_club_is_not_done_until_europe_is():
    """The Premier League ends in May; the Champions League final is later."""
    assert se.finished_by("Premier League") >= date(2027, 6, 1)


def test_the_nfl_season_is_named_for_the_year_it_starts():
    assert se.finished_by("NFL").year == 2027


def test_a_club_that_is_done_is_over_before_its_league_is():
    day = date(2026, 10, 1)
    assert not se.season_over("MLB", day)
    assert se.season_over("MLB", day, club_done=True)


def test_mlb_is_never_final_because_its_league_year_has_a_second_half():
    assert se.season_over("MLB", date(2026, 12, 1))
    assert not se.score_final("MLB", date(2026, 12, 1))


def test_a_score_is_final_only_once_its_league_cannot_move():
    assert not se.score_final("NFL", date(2027, 2, 10))
    assert se.score_final("NFL", date(2027, 3, 1))


def test_the_individual_sports_are_never_marked():
    assert not se.season_over("PGA", date(2027, 7, 1), club_done=True)
    assert not se.score_final("ATP", date(2027, 12, 1))
