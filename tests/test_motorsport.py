"""Motorsport scoring tests.

Expected values follow NASCAR.R and the Formula 1 championship table.
"""

import pandas as pd
import pytest

from whul.scoring import motorsport
from whul.normalize import assign_norm_key
from whul.scoring.motorsport import (
    NASCAR_MIN_RACES,
    f1_points,
    nascar_points,
    score_f1,
    score_nascar,
    score_players,
)


def race(driver="Kyle Larson", finish=1, season=2026, date="2026-02-15"):
    return {"driver": driver, "finish": finish, "season": season, "date": date}


def grand_prix(driver="Max Verstappen", position=1, season=2026, date="2026-03-08", **extra):
    row = {"driver_name": driver, "position": position, "season": season, "date": date}
    row.update(extra)
    return row


# --- NASCAR ----------------------------------------------------------------

def test_a_win_is_worth_more_than_the_gap_to_second_suggests():
    """The 2026 scale jumps from 35 to 55 for a win -- the largest single step
    on the board, and the reason a win-heavy season outruns a consistent one."""
    assert nascar_points(1) == 55
    assert nascar_points(2) == 35


def test_positions_below_second_descend_by_one():
    assert nascar_points(3) == 34
    assert nascar_points(10) == 27
    assert nascar_points(36) == 1


def test_beyond_the_scoring_field_everyone_gets_a_point():
    assert nascar_points(37) == 1
    assert nascar_points(40) == 1


def test_a_missing_finish_scores_nothing():
    assert nascar_points(None) == 0.0
    assert nascar_points(float("nan")) == 0.0


def test_nascar_season_total_sums_races():
    results = pd.DataFrame([race(finish=1), race(finish=3), race(finish=40)])
    totals = score_nascar(results, min_races=1)
    row = totals.iloc[0]
    assert row["total_points"] == 55 + 34 + 1
    assert row["races_started"] == 3
    assert row["wins"] == 1
    assert row["league"] == "NASCAR"


def test_part_time_entries_stay_out_of_the_pool():
    full = [race(driver="Full Time", finish=5) for _ in range(NASCAR_MIN_RACES)]
    partial = [race(driver="Substitute", finish=1)]
    totals = score_nascar(pd.DataFrame(full + partial))
    assert list(totals["player"]) == ["Full Time"]


# --- Formula 1 -------------------------------------------------------------

def test_f1_uses_the_championship_table():
    assert f1_points(1) == 25
    assert f1_points(2) == 18
    assert f1_points(3) == 15
    assert f1_points(10) == 1


def test_f1_scores_only_the_top_ten():
    assert f1_points(11) == 0.0
    assert f1_points(20) == 0.0


def test_sprints_pay_their_own_shorter_table():
    assert f1_points(1, sprint=True) == 8
    assert f1_points(8, sprint=True) == 1
    assert f1_points(9, sprint=True) == 0.0


def test_the_fastest_lap_point_needs_a_top_ten_finish():
    assert f1_points(5, fastest_lap=True) == 10 + 1
    assert f1_points(11, fastest_lap=True) == 0.0


def test_the_feeds_own_points_win_over_the_computed_table():
    """Standings feeds report points directly and already account for
    regulation changes, so a reported value is preferred where present."""
    results = pd.DataFrame([grand_prix(position=1, points=26.0)])
    assert score_f1(results).iloc[0]["total_points"] == 26.0


def test_points_are_computed_when_the_feed_omits_them():
    results = pd.DataFrame([grand_prix(position=1), grand_prix(position=2, driver="Lando Norris")])
    totals = score_f1(results).set_index("player")
    assert totals.loc["Max Verstappen", "total_points"] == 25
    assert totals.loc["Lando Norris", "total_points"] == 18


# --- one series, one distribution ------------------------------------------

def test_each_series_is_measured_against_its_own_history():
    """They fill the same roster slots, but a twenty-car grid and a forty-car
    field are not one distribution, so each is normalized against itself."""
    nascar = pd.DataFrame([race(finish=1) for _ in range(NASCAR_MIN_RACES)])
    f1 = pd.DataFrame([grand_prix(position=1)])
    both = score_players(nascar, f1)
    assert set(both["league"]) == {"NASCAR", "F1"}
    assert set(assign_norm_key(both, "Player")) == {"NASCAR", "F1"}


def test_empty_input_is_empty_output():
    assert score_players(pd.DataFrame(), pd.DataFrame()).empty
    assert score_nascar(pd.DataFrame()).empty
    assert score_f1(pd.DataFrame()).empty


# --- a sprint is its own race ----------------------------------------------

def test_a_sprint_is_labelled_as_one():
    """The feed files a sprint as a separate row and says which it is. Dropping
    that made a sprint weekend read as one driver finishing 2nd and 4th in the
    same Grand Prix -- a flat contradiction on the profile page, and the kind of
    thing that makes a reader doubt everything else on it."""
    races = pd.DataFrame([
        {"driver_name": "Antonelli", "season": 2026, "position": 2,
         "date": "2026-08-23", "race": "Dutch Grand Prix", "points": 18.0,
         "is_sprint": False},
        {"driver_name": "Antonelli", "season": 2026, "position": 4,
         "date": "2026-08-23", "race": "Dutch Grand Prix", "points": 5.0,
         "is_sprint": True},
    ])
    out = motorsport.f1_events(races)
    assert list(out["tournament"]) == ["Dutch Grand Prix", "Dutch Grand Prix Sprint"]
    assert out["event_points"].sum() == pytest.approx(23.0)


def test_an_unpriced_sprint_falls_back_to_the_sprint_table():
    """The feed's own points are used where it reports them. Where it does not,
    the fallback used to reach for the Grand Prix table whatever the race was --
    paying a sprint 4th 12 points instead of 5, about three times over."""
    races = pd.DataFrame([
        {"driver_name": "Antonelli", "season": 2026, "position": 4,
         "date": "2026-08-23", "race": "Dutch Grand Prix", "points": None,
         "is_sprint": True},
        {"driver_name": "Verstappen", "season": 2026, "position": 4,
         "date": "2026-08-23", "race": "Dutch Grand Prix", "points": None,
         "is_sprint": False},
    ])
    out = motorsport.f1_events(races).set_index("player")
    assert out.loc["Antonelli", "event_points"] == pytest.approx(5.0)
    assert out.loc["Verstappen", "event_points"] == pytest.approx(12.0)


def test_a_feed_without_a_sprint_column_still_scores():
    """Older seasons have no sprints at all, and the column may be absent."""
    races = pd.DataFrame([
        {"driver_name": "Hamilton", "season": 2019, "position": 1,
         "date": "2019-07-14", "race": "British Grand Prix", "points": 25.0},
    ])
    out = motorsport.f1_events(races)
    assert out.loc[0, "tournament"] == "British Grand Prix"
    assert not out.loc[0, "is_sprint"]
