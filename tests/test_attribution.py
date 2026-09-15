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


def _club(*keys):
    """A club's match list: one row an event, as the scoreboard walk returns."""
    return pd.DataFrame([{"event_id": str(i), "competition_key": k}
                         for i, k in enumerate(keys)])


def _gamelog(**counts):
    return pd.DataFrame([{"competition_key": k, "appearances": float(n),
                          "from_club": True} for k, n in counts.items()])


def test_a_gamelog_that_never_fetched_the_cup_cannot_convict():
    """Every player the check flagged on its first run was flagged by matches
    the gamelog had not returned: Chelsea's two League Cup ties, Bayern's
    Pokal tie. Reported as unjudged, because a check that cries wolf cannot be
    promoted to one that blocks."""
    out, = attribution.disagreements(
        _gamelog(epl=4), 6.0, "Cole Palmer", "Premier League",
        _club("epl", "epl", "epl", "epl", "efl-cup", "efl-cup"))

    assert out["verdict"] == "unjudged"
    assert out["missing"] == ["efl-cup"]


def test_more_domestic_football_than_the_club_played_is_always_a_fault():
    """Nobody appears in more of his club's matches than it played, whatever
    the gamelog did or did not return."""
    out, = attribution.disagreements(
        _gamelog(epl=4), 7.0, "Someone", "Premier League",
        _club("epl", "epl", "epl", "epl", "efl-cup", "efl-cup"))

    assert out["verdict"] == "impossible"


def test_a_european_match_wearing_a_domestic_figure_is_still_caught():
    """The case the module was written for: Bayern played three league matches
    and Kane's domestic figure said four, the fourth being his Champions
    League tie -- counted into the base score and held as a bonus at once."""
    out, = attribution.disagreements(
        _gamelog(bundesliga=3, ucl=1), 4.0, "Harry Kane", "Bundesliga",
        _club("bundesliga", "bundesliga", "bundesliga", "ucl"))

    assert out["verdict"] == "impossible"


def test_a_complete_gamelog_short_of_the_claim_is_an_excess():
    """Every competition the club played is in the gamelog, so the excess is a
    match he did not play rather than one nobody fetched."""
    out, = attribution.disagreements(
        _gamelog(epl=3), 4.0, "Someone", "Premier League",
        _club("epl", "epl", "epl", "epl"))

    assert out["verdict"] == "excess"
    assert out["missing"] == []


def test_a_claim_at_or_under_the_gamelog_is_never_reported():
    four = _club("epl", "epl", "epl", "epl")
    assert attribution.disagreements(
        _gamelog(epl=4), 4.0, "Fine", "Premier League", four) == []
    assert attribution.disagreements(
        _gamelog(epl=4), 2.0, "Missed some", "Premier League", four) == []


def test_a_gamelog_longer_than_its_clubs_season_is_not_this_season(monkeypatch):
    """Monaco had played four league matches and Balogun's gamelog came back
    with thirty-one, under a name the club's own results never use -- so none
    of them joined to a match and all of them counted as domestic football.

    He passed every check there was. The comparison reports a figure larger
    than its gamelog, and his figure was smaller than a gamelog holding most
    of a season nobody had asked about."""
    out, = attribution.disagreements(
        _gamelog(**{"French Ligue 1": 31}), 4.0, "Folarin Balogun", "Ligue 1",
        _club("ligue1", "ligue1", "ligue1", "ligue1"))

    assert out["verdict"] == "stale"
    assert out["played"] == 31.0
    assert out["club"] == 4.0
    assert out["saw_in"] == ["French Ligue 1"]


def test_a_stale_gamelog_is_reported_before_anything_is_judged_against_it():
    """It is the instrument, so a fault found with it is not a fault found
    with the player."""
    found = attribution.disagreements(
        _gamelog(**{"French Ligue 1": 31}), 4.0, "Folarin Balogun", "Ligue 1",
        _club("ligue1", "ligue1", "ligue1", "ligue1"))
    text = "\n".join(attribution.report(found))

    assert "more football than their club has played" in text
    assert "his gamelog shows 31 domestic, the club has played 4" in text
    assert "French Ligue 1" in text


def test_the_clubs_own_football_is_counted_once_an_event():
    """A match list holds a row per side, so counting rows would double every
    club's season."""
    matches = pd.DataFrame([
        {"event_id": "1", "competition_key": "epl"},
        {"event_id": "1", "competition_key": "epl"},
        {"event_id": "2", "competition_key": "ucl"},
    ])
    domestic, held, keys = attribution.club_football(matches, "Premier League")

    assert (domestic, held) == (1.0, 1.0)
    assert keys == {"epl", "ucl"}


def test_the_report_separates_a_fault_from_a_silence():
    found = (
        attribution.disagreements(
            _gamelog(epl=4), 6.0, "Unjudgeable", "Premier League",
            _club("epl", "epl", "epl", "epl", "efl-cup", "efl-cup"))
        + attribution.disagreements(
            _gamelog(epl=4), 9.0, "Faulty", "Premier League",
            _club("epl", "epl", "epl", "epl", "efl-cup", "efl-cup"))
    )
    text = "\n".join(attribution.report(found))

    assert "1 player(s) counted more domestic football than their club played" in text
    assert "1 player(s) could not be judged" in text
    # The gap is named where it is the reason and nowhere else.
    assert text.index("Faulty") < text.index("Unjudgeable")
    faulty_line = next(l for l in text.splitlines() if "Faulty" in l)
    assert "never returned" not in faulty_line
