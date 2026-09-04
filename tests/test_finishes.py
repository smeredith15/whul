"""Finishes, as a profile shows them."""

import pandas as pd
import pytest

from whul.scoring import finishes


def match(round_, points, tournament="Winston Salem", category="ATP 250",
          date="2026-08-20", player="A. Fils"):
    return {"player": player, "date": date, "tournament": tournament,
            "category": category, "round": round_, "event_points": points,
            "league": "ATP"}


def test_a_run_to_a_final_is_one_line_not_seven():
    """Tennis event rows are one per match, so a run to a final is seven of
    them. A reader wants one line saying how far the player got and what the
    week was worth."""
    events = pd.DataFrame([
        match("R32", 20.0, date="2026-08-18"),
        match("R16", 30.0, date="2026-08-19"),
        match("QF", 40.0, date="2026-08-20"),
        match("SF", 60.0, date="2026-08-21"),
        match("F", 0.0, date="2026-08-22"),
    ])
    out = finishes.as_records(finishes.tennis_finishes(events))["A. Fils"]
    assert len(out) == 1
    assert out[0]["label"] == "ATP Winston Salem 250 F"
    assert out[0]["points"] == 150.0


def test_losing_the_final_still_reaches_the_final():
    """The losing row is the only one that records having got there -- the
    player has no win at that round to be found by."""
    events = pd.DataFrame([match("SF", 60.0), match("F", 0.0)])
    out = finishes.tennis_finishes(events)
    assert out.loc[0, "round"] == "F"


def test_a_first_round_exit_is_a_line_not_an_absence():
    """Which is what distinguishes a player who lost from one who is injured
    and did not enter."""
    events = pd.DataFrame([match("R128", 0.0, tournament="US Open",
                                 category="Grand Slam")])
    out = finishes.as_records(finishes.tennis_finishes(events))["A. Fils"]
    assert out[0]["label"] == "ATP US Open Grand Slam R128"
    assert out[0]["points"] == 0.0


def test_the_tour_is_not_repeated_in_the_tier():
    """"ATP ... ATP 250" says it twice."""
    events = pd.DataFrame([match("W", 250.0)])
    assert "ATP Winston Salem 250" in finishes.tennis_finishes(events).loc[0, "label"]


def test_finishes_are_newest_first():
    """A profile is opened to see what just happened."""
    events = pd.DataFrame([
        match("W", 250.0, tournament="Winston Salem", date="2026-08-22"),
        match("R16", 30.0, tournament="US Open", date="2026-09-01"),
    ])
    out = finishes.as_records(finishes.tennis_finishes(events))["A. Fils"]
    assert [f["date"] for f in out] == ["2026-09-01", "2026-08-22"]


def test_a_race_finish_reads_as_a_placing():
    events = pd.DataFrame([
        {"player": "W. Byron", "date": "2026-02-15", "tournament": "Daytona 500",
         "finish": 4, "event_points": 42.0, "league": "NASCAR"},
    ])
    out = finishes.as_records(finishes.event_finishes(events, "finish"))["W. Byron"]
    assert out[0]["label"] == "Daytona 500 4th"
    assert out[0]["points"] == 42.0


def test_a_golf_finish_reads_as_a_placing():
    events = pd.DataFrame([
        {"player": "S. Scheffler", "date": "2026-04-12", "tournament": "Masters",
         "position": 2, "event_points": 68.0, "league": "PGA"},
    ])
    out = finishes.as_records(finishes.event_finishes(events, "position"))["S. Scheffler"]
    assert out[0]["label"] == "Masters 2nd"


def test_ordinals_read_correctly():
    assert [finishes.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd"]


def test_the_sport_is_read_off_the_columns():
    """The three scorers have different vocabularies -- a round, a position, a
    finish -- and the column present is what says which sport wrote the frame.
    A league name would have to be kept in step by hand."""
    tennis = pd.DataFrame([match("QF", 40.0)])
    assert finishes.summarize(tennis)["A. Fils"][0]["label"].endswith("QF")

    race = pd.DataFrame([{"player": "W. Byron", "date": "2026-02-15",
                          "tournament": "Daytona 500", "finish": 4,
                          "event_points": 42.0, "league": "NASCAR"}])
    assert finishes.summarize(race)["W. Byron"][0]["label"] == "Daytona 500 4th"

    assert finishes.summarize(pd.DataFrame()) == {}


def test_a_profile_does_not_carry_a_whole_season_of_matches():
    """A season of tennis is fifty-odd tournaments and the window is scrolled,
    not read end to end."""
    events = pd.DataFrame([
        match("W", 10.0, tournament=f"Event {i}", date=f"2026-01-{i % 28 + 1:02d}")
        for i in range(80)
    ])
    out = finishes.as_records(finishes.tennis_finishes(events))["A. Fils"]
    assert len(out) == finishes.MAX_FINISHES
