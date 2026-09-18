"""Finishes, as a profile shows them."""

import pandas as pd
import pytest

from whul.scoring import finishes
from whul.scoring.tennis import INTERNATIONAL_WIN_POINTS


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


def test_a_race_is_labelled_by_where_it_finished_not_where_it_stands():
    """``position`` is overloaded: the driver feed carries a championship
    standing under that name, and a frame can reach here with both columns.
    Reading it labelled every one of Ryan Blaney's races "3rd" -- a win and a
    thirty-third alike, beside points of 55 and 4 that said otherwise."""
    events = pd.DataFrame([
        {"player": "R. Blaney", "date": "2026-07-13", "tournament": "Sonoma",
         "finish": 1, "position": 3, "event_points": 55.0, "league": "NASCAR"},
        {"player": "R. Blaney", "date": "2026-08-23", "tournament": "Daytona",
         "finish": 33, "position": 3, "event_points": 4.0, "league": "NASCAR"},
    ])
    out = finishes.summarize(events)["R. Blaney"]
    assert [f["label"] for f in out] == ["Daytona 33rd", "Sonoma 1st"]


# --- tennis by the size of the field ---------------------------------------

def _rows(*played):
    from whul.scoring import tennis

    return tennis.match_events(pd.DataFrame([
        {"tournament": t, "category": cat, "round": rnd, "winner": w,
         "loser": l, "score": score, "date": "2026-09-01", "season": 2026,
         "tour": "ATP", "draw_size": draw}
        for t, cat, rnd, w, l, draw, score in played
    ]), losses=True)


ME = "A. Fils"
#: A season with one of everything in it. The slam is a third-round exit
#: written out in full -- the two wins that got him there and the loss that
#: ended it -- because only wins pay and the wins are what makes it 100 rather
#: than nothing.
SEASON = (
    ("Cincinnati", "Masters 1000", "F", "Other", ME, 64, "6-4 6-4"),
    ("Cincinnati", "Masters 1000", "SF", ME, "X", 64, "6-4 6-4"),
    ("US Open", "Grand Slam", "R128", ME, "Y3", 128, "6-4 6-4 6-4"),
    ("US Open", "Grand Slam", "R64", ME, "Y2", 128, "6-4 6-4 6-4"),
    ("US Open", "Grand Slam", "R32", "Y", ME, 128, "6-4 6-4 6-4"),
    ("Metz", "250", "F", ME, "Z", 32, "6-4 6-4"),
    ("Basel", "250", "SF", "Q", ME, 32, "6-4 6-4"),
    ("Turin", "Tour Finals", "RR", ME, "P", 8, "6-4 6-4"),
    ("Turin", "Tour Finals", "RR", "R", ME, 8, "6-4 6-4"),
    ("Turin", "Tour Finals", "RR", "S", ME, 8, "6-4 6-4"),
    ("Davis Cup", "International", "R32", ME, "T", 16, "6-4 6-4"),
)


def _by_label(events):
    return {e["label"]: e for e in finishes.tier_summary(events)[ME]}


def test_a_season_reads_by_the_size_of_the_field():
    """Fifty tournaments a year and seven matches in a good week make one list
    unreadable, and the thing a reader wants -- how he did against fields of
    each size -- is exactly what a flat list buries: a first-round loss at a
    250 and one at a slam are the same line and not remotely the same result."""
    tiers = _by_label(_rows(*SEASON))

    assert tiers["ATP 250"]["note"] == "W · SF"
    assert tiers["ATP 1000"]["note"] == "F"
    assert tiers["Grand Slam"]["note"] == "R32"
    assert tiers["ATP Finals"]["note"] == "RR 1-2"
    assert tiers["Team Events"]["note"] == "1-0"


def test_a_tier_he_never_entered_is_unknown_rather_than_nothing():
    """Zero is the honest answer for a tournament he lost his opening match at
    -- only wins pay -- and the wrong one for a 500 he did not play."""
    tiers = _by_label(_rows(*SEASON, ("Doha", "500", "R32", "N", ME, 32, "6-4 6-4")))

    assert tiers["ATP 500"]["points"] == 0.0, "entered, and won nothing"
    assert _by_label(_rows(*SEASON))["ATP 500"]["points"] is None


def test_the_figure_is_the_one_the_tour_publishes():
    """550 at a Masters is a number that appears on the tour's own list; 687.5
    is a number that appears nowhere. What winning quickly added rides above
    it instead, and losing in the third round of a slam is the two wins that
    got him there rather than nothing."""
    from whul.scoring.tennis import ATP_WIN_POINTS

    tiers = _by_label(_rows(*SEASON))
    table = ATP_WIN_POINTS

    assert tiers["Grand Slam"]["points"] == (
        table[("GS", "R128")] + table[("GS", "R64")]) == 100
    assert tiers["Grand Slam"]["points"] == int(tiers["Grand Slam"]["points"])
    assert tiers["Grand Slam"]["straight"] > 0, "won in straight sets as well"


def test_the_tiers_add_up_to_what_the_season_scored():
    """Each figure and the superscript above it, over six tiers, is the score
    said twelve ways -- so a reader can check it, which is the point of
    splitting them."""
    events = _rows(*SEASON)
    tiers = finishes.tier_summary(events)[ME]

    shown = sum((entry["points"] or 0.0) + entry["straight"] for entry in tiers)
    scored = float(events[events["player"] == ME]["event_points"].sum())
    assert round(shown, 2) == round(scored, 2)


def test_the_straight_sets_bonus_is_kept_apart_from_what_the_round_paid():
    """It is the difference between beating the draw and beating it quickly,
    and a single figure cannot say which of the two a player did."""
    events = _rows(*SEASON)
    tiers = _by_label(events)

    assert tiers["ATP 1000"]["straight"] > 0
    assert tiers["ATP 1000"]["straight"] < tiers["ATP 1000"]["points"]


def test_a_tier_entered_more_than_once_is_counted_not_listed():
    tiers = _by_label(_rows(
        ("Metz", "250", "F", ME, "Z", 32, "6-4 6-4"),
        ("Basel", "250", "F", ME, "Q", 32, "6-4 6-4"),
        ("Doha", "250", "SF", "R", ME, 32, "6-4 6-4"),
    ))
    assert tiers["ATP 250"]["note"] == "W ×2 · SF"


def test_a_tie_reads_as_a_record_rather_than_a_round():
    """Ben Shelton lost to Jiri Lehecka in the World Group and nothing about
    his score could say so: a loss pays nothing, and the line that would have
    said he played said "International" and a blank round. A tie has no draw
    position, so the record is the result."""
    out = finishes.summarize(_rows(
        ("Davis Cup - World Group", "International", "", "J. Lehecka", ME,
         None, "6-4 6-4"),
    ))[ME]

    assert [f["label"] for f in out] == ["ATP Davis Cup - World Group 0-1"]
    assert out[0]["points"] == 0.0


def test_a_tie_a_player_lost_still_fills_his_team_events_box():
    """Zero is the honest answer for a tie he lost -- only wins pay -- and a
    dash is the wrong one: one says he earned nothing there, the other that
    there is nothing to say."""
    box = _by_label(_rows(
        ("Davis Cup - World Group", "International", "", "J. Lehecka", ME,
         None, "6-4 6-4"),
    ))["Team Events"]

    assert box["points"] == 0.0
    assert box["note"] == "0-1"
    assert box["entered"] == 1


def test_a_record_counts_both_sides_of_a_tie():
    """Two rubbers in a weekend is the shape a tie actually comes in."""
    box = _by_label(_rows(
        ("Davis Cup - World Group", "International", "", ME, "A", None, "6-4 6-4"),
        ("Davis Cup - World Group", "International", "", "B", ME, None, "6-4 6-4"),
    ))["Team Events"]

    assert box["note"] == "1-1"
    assert box["points"] == INTERNATIONAL_WIN_POINTS
