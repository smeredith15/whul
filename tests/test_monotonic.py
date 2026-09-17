"""A count may only rise. Everything else may do as the sport does.

Every case here is taken from a real day in the published database, where a
manager lost points for a day in which nothing happened to them.
"""

import pandas as pd
import pytest

from whul.store import monotonic


# --- the split ---------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "reg_wins", "shutouts", "div_champ", "games", "games_played", "ab", "h",
    "hr", "matches", "starts", "wins", "playoff_wins", "conf_wins",
    "season_settled", "big_wins", "mm_appearance", "receptions",
])
def test_these_can_only_rise(name):
    assert monotonic.is_a_count(name)


@pytest.mark.parametrize("name", [
    # Differentials, by name and by shape.
    "point_diff", "run_diff", "goal_diff", "margin", "xg_diff", "plus_minus",
    # Baseball's advanced terms: runs above a replacement, and they go down.
    "war", "offense", "defense",
    # Rates, and the weights a score is scaled by.
    "advanced_share", "proration_factor", "schedule_factor", "era", "whip",
    "save_pct", "completion_rate",
    # Derived from the rest, so checked through the rest.
    "total_points", "role_points", "pts_run_diff", "scaled_score",
    "league_points",
    # Identity.
    "season", "asset_id", "conference", "game_id",
])
def test_these_may_fall(name):
    assert not monotonic.is_a_count(name)


def test_anything_new_is_a_count_until_it_is_named_otherwise():
    """The safe direction. A measure wrongly called a count raises a false
    alarm, which is loud and cheap; a count wrongly called a measure is a
    silent loss, which is the thing this exists to stop."""
    assert monotonic.is_a_count("something_nobody_has_written_yet")


def test_nothing_said_is_not_a_fall():
    """NaN is the feed declining to answer, not a zero -- and it is truthy,
    which this project has been caught by three times."""
    assert monotonic.what_went_backwards({"wins": 5}, {"wins": float("nan")}) == []
    assert monotonic.what_went_backwards({"wins": float("nan")}, {"wins": 5}) == []
    # A field that has appeared since is new rather than fallen.
    assert monotonic.what_went_backwards({}, {"wins": 5}) == []
    # And one that has gone is the row changing shape, which is another fault.
    assert monotonic.what_went_backwards({"wins": 5}, {}) == []


# --- the real days -----------------------------------------------------------

def test_a_baseball_club_that_lost_a_win_overnight_is_caught():
    """Los Angeles were 19-10 with three shutouts on the sixteenth of
    September and 18-11 with two on the seventeenth, having been credited
    with a game that was still being played when it was counted."""
    fell = monotonic.what_went_backwards(
        {"reg_wins": 19.0, "reg_losses": 10.0, "shutouts": 3.0,
         "run_diff": 35.0, "total_points": 59.38},
        {"reg_wins": 18.0, "reg_losses": 11.0, "shutouts": 2.0,
         "run_diff": 30.0, "total_points": 54.20},
    )
    assert [name for name, _, _ in fell] == ["reg_wins", "shutouts"]


def test_a_division_title_taken_back_is_caught():
    """Baltimore were 1-0 and were awarded the AFC North -- fifteen points --
    and had it taken off them four days later when week two appeared."""
    fell = monotonic.what_went_backwards(
        {"div_champ": 1, "season_settled": True, "reg_wins": 1.0,
         "total_points": 29.8},
        {"div_champ": 0, "season_settled": False, "reg_wins": 1.0,
         "total_points": 14.8},
    )
    assert [name for name, _, _ in fell] == ["div_champ", "season_settled"]


@pytest.mark.parametrize("who,before,after", [
    # A batter who went 0-for-4: four more at-bats at minus one apiece.
    ("Dillon Dingler",
     {"ab": 88.0, "h": 14, "games": 24, "offense": 1.249, "defense": 2.128,
      "role_points": 29.36},
     {"ab": 92.0, "h": 14, "games": 25, "offense": 1.301, "defense": 2.217,
      "role_points": 24.67}),
    # A pitcher hit around: more innings, and his WAR collapsed.
    ("Grant Taylor",
     {"games": 8, "ip": 9.0, "h": 5, "hr": 0, "war": 2.297, "role_points": 115.8},
     {"games": 11, "ip": 13.67, "h": 11, "hr": 3, "war": 0.391, "role_points": 86.0}),
    # A college team that lost: its point differential went from +17 to -16.
    ("Arkansas Razorbacks",
     {"games_played": 1, "losses": 0, "point_diff": 17.0, "total_points": 10.85},
     {"games_played": 2, "losses": 1, "point_diff": -16.0, "total_points": 9.2}),
    # A baseball club that was outscored.
    ("Athletics",
     {"games_played": 26, "reg_losses": 14, "run_diff": -11.0},
     {"games_played": 27, "reg_losses": 15, "run_diff": -29.0}),
])
def test_the_sport_happening_is_not_a_fault(who, before, after):
    """These all lost points and every one of them is the truth. A pipeline
    that refused them would be refusing the sport."""
    assert monotonic.what_went_backwards(before, after) == [], who
