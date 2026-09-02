"""PGA scoring tests.

Expected values are read off the points table in PGA.R.
"""

import pandas as pd

from whul.scoring.golf import (
    FINISH_POINTS,
    MAJOR_MULTIPLIER,
    finish_points,
    parse_position,
    score_events,
    score_players,
)


def result(player="Scottie Scheffler", position="1", tournament="Genesis Invitational",
           season=2026, date="2026-02-15"):
    return {"player": player, "position": position, "tournament": tournament,
            "season": season, "date": date}


# --- the points table ------------------------------------------------------

def test_finish_points_match_the_table():
    assert finish_points(1) == 500
    assert finish_points(2) == 300
    assert finish_points(3) == 190
    assert finish_points(10) == 75
    assert finish_points(30) == 10


def test_nothing_scores_below_thirtieth():
    assert finish_points(31) == 0.0
    assert finish_points(70) == 0.0


def test_the_table_covers_exactly_thirty_places():
    assert len(FINISH_POINTS) == 30
    assert list(FINISH_POINTS) == sorted(FINISH_POINTS, reverse=True)


def test_missing_position_scores_nothing():
    assert finish_points(None) == 0.0
    assert finish_points(float("nan")) == 0.0


# --- position parsing ------------------------------------------------------

def test_ties_take_the_position_they_are_tied_at():
    """A five-way tie for third pays each player third-place points; the R
    script does not split them."""
    assert parse_position("T3") == 3.0
    assert parse_position("3") == 3.0


def test_unplaced_entries_parse_to_nothing():
    for value in (None, "CUT", "WD", "MDF", ""):
        assert parse_position(value) is None, value


# --- event scoring ---------------------------------------------------------

def test_majors_are_worth_half_again_as_much():
    events = score_events(pd.DataFrame([
        result(position="1", tournament="Masters Tournament"),
        result(position="1", tournament="Genesis Invitational"),
    ]))
    major = events[events["is_major"]].iloc[0]
    regular = events[~events["is_major"]].iloc[0]
    assert major["event_points"] == 500 * MAJOR_MULTIPLIER
    assert regular["event_points"] == 500


def test_the_players_counts_as_a_major():
    events = score_events(pd.DataFrame([result(tournament="THE PLAYERS Championship")]))
    assert bool(events.iloc[0]["is_major"]) is True


def test_all_four_majors_are_recognized():
    names = ["Masters Tournament", "PGA Championship", "U.S. Open", "The Open Championship"]
    events = score_events(pd.DataFrame([result(tournament=n) for n in names]))
    assert events["is_major"].all()


def test_missed_cuts_are_dropped_not_zeroed():
    """A missed cut has no position to score, so it produces no row -- it must
    not land as a zero that drags an average down."""
    events = score_events(pd.DataFrame([result(position="CUT"), result(position="5")]))
    assert len(events) == 1
    assert events.iloc[0]["position"] == 5


# --- season totals ---------------------------------------------------------

def test_season_total_sums_events():
    results = pd.DataFrame([
        result(position="1"),
        result(position="2", tournament="Arnold Palmer Invitational"),
        result(position="1", tournament="Masters Tournament"),
    ])
    totals = score_players(results, min_events=1)
    row = totals.iloc[0]
    assert row["total_points"] == 500 + 300 + 500 * MAJOR_MULTIPLIER
    assert row["events_played"] == 3
    assert row["wins"] == 2
    assert row["top_tens"] == 3


def test_short_seasons_are_kept_out_of_the_pool():
    """Eight starts is the R script's floor: fewer, and a single good week
    dominates a season that was never really played."""
    results = pd.DataFrame([
        result(player="Regular", tournament=f"Event {i}", position="5")
        for i in range(8)
    ] + [result(player="One Off", position="1")])
    totals = score_players(results)
    assert list(totals["player"]) == ["Regular"]


def test_empty_input_is_empty_output():
    assert score_players(pd.DataFrame()).empty
    assert score_events(pd.DataFrame()).empty
