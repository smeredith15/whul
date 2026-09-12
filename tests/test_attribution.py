"""Deciding a player's competition from his club's results.

Bayern's Bundesliga roster returned Harry Kane with his Champions League match
among his appearances, so it was counted as domestic football -- three points
and into the base score -- and held as a European bonus at the same time. The
club's own results never wobbled: Bayern kept that win on both days, because
team results are read from a scoreboard walk where every match is fetched under
the competition it was played in.
"""

import pandas as pd

from whul import attribution


def _events(*rows) -> pd.DataFrame:
    return pd.DataFrame([
        {"event_id": eid, "date": day, "competition": comp, "opponent": "Someone"}
        for eid, day, comp in rows
    ])


def _matches(*rows) -> pd.DataFrame:
    return pd.DataFrame([
        {"event_id": eid, "competition_key": key, "team": "Bayern Munich"}
        for eid, key in rows
    ])


def test_the_club_decides_which_competition_an_appearance_was_in():
    """The whole point. The player's own gamelog names the competition too, but
    the club's results are the ones that have never been wrong."""
    found = attribution.attribute(
        _events(("1", "2026-08-29", "German Bundesliga"),
                ("2", "2026-09-05", "German Bundesliga"),
                ("3", "2026-09-11", "UEFA Champions League")),
        _matches(("1", "bundesliga"), ("2", "bundesliga"), ("3", "ucl")),
    )
    counted = dict(zip(found["competition_key"], found["appearances"]))
    assert counted == {"bundesliga": 2.0, "ucl": 1.0}
    assert found["from_club"].all()


def test_a_figure_counting_a_european_match_as_domestic_is_named():
    """Kane's night, exactly. The stored figure counted three appearances; the
    club played two Bundesliga matches and a European one, and European
    appearances are held rather than counted."""
    attributed = attribution.attribute(
        _events(("1", "2026-08-29", "German Bundesliga"),
                ("2", "2026-09-05", "German Bundesliga"),
                ("3", "2026-09-11", "UEFA Champions League")),
        _matches(("1", "bundesliga"), ("2", "bundesliga"), ("3", "ucl")),
    )
    found = attribution.disagreements(attributed, 3.0, "Harry Kane", "Bundesliga")
    assert len(found) == 1
    assert found[0]["excess"] == 1.0
    assert found[0]["played"] == 2.0 and found[0]["held"] == 1.0
    line = attribution.report(found)[1]
    assert "Harry Kane" in line and "ucl" in line


def test_a_domestic_cup_counts_towards_the_domestic_total():
    """Cups are the base score too, so a cup tie is not an excess."""
    attributed = attribution.attribute(
        _events(("1", "2026-08-29", "German Bundesliga"),
                ("2", "2026-09-02", "DFB Pokal")),
        _matches(("1", "bundesliga"), ("2", "dfbpokal")),
    )
    assert attribution.disagreements(attributed, 2.0, "Harry Kane", "Bundesliga") == []
    domestic, held, _ = attribution.counted_appearances(attributed, "Bundesliga")
    assert (domestic, held) == (2.0, 0.0)


def test_a_figure_below_what_was_found_is_not_a_disagreement():
    """The gamelog does not return every competition, so what it finds is a
    floor and not a total. Reporting a figure under it would bury the one case
    that matters under one that never does."""
    attributed = attribution.attribute(
        _events(("1", "2026-08-29", "German Bundesliga"),
                ("2", "2026-09-05", "German Bundesliga")),
        _matches(("1", "bundesliga"), ("2", "bundesliga")),
    )
    assert attribution.disagreements(attributed, 1.0, "Harry Kane", "Bundesliga") == []


def test_a_missed_game_does_not_hide_a_misattributed_one():
    """The fault in the bound this replaces. A player who sat out a league
    match has slack under his club's total, so a European match counted as
    domestic fits inside it and a count alone can never see it. Naming the
    match can: the club played event 3 in the Champions League whoever
    appeared in it."""
    attributed = attribution.attribute(
        _events(("2", "2026-09-05", "German Bundesliga"),      # played
                ("3", "2026-09-11", "UEFA Champions League")),  # missed event 1
        _matches(("1", "bundesliga"), ("2", "bundesliga"), ("3", "ucl")),
    )
    # Two appearances, two league matches played by the club: a count-only
    # check sees nothing wrong. This does.
    found = attribution.disagreements(attributed, 2.0, "Harry Kane", "Bundesliga")
    assert len(found) == 1 and found[0]["excess"] == 1.0


def test_a_match_the_club_list_does_not_hold_keeps_its_own_name():
    """Honest rather than dropped -- the gamelog names each event truthfully --
    and flagged, because a club match list missing a match the club played is
    its own fault worth seeing."""
    found = attribution.attribute(
        _events(("9", "2026-09-11", "UEFA Champions League")),
        _matches(("1", "bundesliga")),
    )
    assert list(found["competition_key"]) == ["ucl"]
    assert not found["from_club"].all()


def test_nothing_to_go_on_is_not_a_disagreement():
    assert attribution.attribute(pd.DataFrame(), pd.DataFrame()).empty
    assert attribution.disagreements(pd.DataFrame(), 0.0, "Nobody", "Bundesliga") == []
    assert attribution.report([]) == []
