"""Club soccer scoring tests.

Expected values are computed by hand from Club_Soccer.R.
"""

import pandas as pd
import pytest

from whul.scoring import soccer
from whul.scoring.competition import Tier, bye_credit, classify
from whul.scoring.soccer import (
    goal_points_for,
    score_players,
    score_team_matches,
    score_teams,
    season_for,
)


def match(team="Arsenal", gf=0, ga=0, comp="Premier League",
          date="2026-09-15", league="Premier League"):
    return {"team": team, "league": league, "date": date,
            "competition": comp, "goals_for": gf, "goals_against": ga}


# --- competition classification --------------------------------------------

def test_win_value_depends_on_the_competition():
    assert classify("UEFA Champions League").win_points == 5
    assert classify("UEFA Europa League").win_points == 4
    assert classify("UEFA Conference League").win_points == 4
    assert classify("FA Cup").win_points == 4
    assert classify("Premier League").win_points == 3


def test_qualifying_does_not_count():
    """Competition proper only."""
    for name in ("Champions League Qualifying", "Third Qualifying Round",
                 "Europa League Preliminary Round"):
        assert classify(name).counts is False, name


def test_qualifying_is_tested_before_the_competition_name():
    """A qualifier carries the parent competition's name, so checking tiers
    first would score it as the competition proper."""
    assert classify("Champions League Qualifying").tier is Tier.QUALIFYING
    assert classify("UEFA Champions League").tier is Tier.CHAMPIONS_LEAGUE


def test_the_knockout_playoff_is_inside_the_competition():
    """It reads like a qualifying round but sits between the league phase and
    the round of 16."""
    out = classify("Champions League Knockout Phase Play-off")
    assert out.counts is True
    assert out.tier is Tier.CHAMPIONS_LEAGUE
    assert out.win_points == 5


def test_domestic_cups_count_at_their_ordinary_value():
    """Every club enters, so there is no qualification premium."""
    for name in ("FA Cup", "DFB-Pokal", "Coppa Italia", "Copa del Rey", "Carabao Cup"):
        assert classify(name).win_points == 4, name


def test_unknown_competitions_fall_through_to_the_league():
    assert classify("Some New Trophy 2027").counts is True
    assert classify("").tier is Tier.LEAGUE
    assert classify(None).tier is Tier.LEAGUE


def test_a_bye_scores_as_a_swept_tie():
    """Without this a bye is indistinguishable from an early exit, which would
    punish the performance that earned it."""
    assert bye_credit(Tier.CHAMPIONS_LEAGUE) == 10
    assert bye_credit(Tier.EUROPA) == 8
    assert bye_credit(Tier.CHAMPIONS_LEAGUE, legs=1) == 5


# --- team scoring ----------------------------------------------------------

def test_only_wins_score():
    rows = pd.DataFrame([match(gf=1, ga=1), match(gf=0, ga=2), match(gf=1, ga=0)])
    out = score_team_matches(rows)
    assert list(out["match_points"]) == [0, 0, 3 + 1]  # win, clean sheet, margin 1


def test_margin_and_clean_sheet_bonuses():
    """3 for a league win, +1 for a two-goal margin, +1 for a clean sheet."""
    rows = pd.DataFrame([
        match(gf=3, ga=0),   # 3 + 1 + 1 = 5
        match(gf=3, ga=1),   # 3 + 1     = 4
        match(gf=1, ga=0),   # 3     + 1 = 4
    ])
    assert list(score_team_matches(rows)["match_points"]) == [5, 4, 4]


def test_a_champions_league_win_is_worth_more_than_a_league_win():
    rows = pd.DataFrame([
        match(gf=2, ga=0, comp="UEFA Champions League"),
        match(gf=2, ga=0, comp="Premier League"),
    ])
    points = list(score_team_matches(rows)["match_points"])
    assert points == [5 + 1 + 1, 3 + 1 + 1]


def test_qualifying_matches_are_dropped_entirely():
    """Neither scored nor counted towards matches played."""
    rows = pd.DataFrame([
        match(gf=5, ga=0, comp="Champions League Qualifying"),
        match(gf=1, ga=0, comp="Premier League"),
    ])
    out = score_teams(rows)
    assert out.iloc[0]["matches_played"] == 1
    assert out.iloc[0]["total_points"] == 4


def test_byes_are_credited_as_swept_ties():
    rows = pd.DataFrame([match(gf=1, ga=0, comp="UEFA Champions League")])
    byes = pd.DataFrame([{"team": "Arsenal", "season": 2027,
                          "tier": "champions_league", "legs": 2}])
    without = score_teams(rows).iloc[0]["total_points"]
    with_bye = score_teams(rows, byes).iloc[0]["total_points"]
    assert with_bye - without == 10


# --- season boundaries ------------------------------------------------------

def test_european_seasons_roll_in_august():
    dates = pd.Series(["2026-09-15", "2027-03-10", "2026-05-20"])
    leagues = pd.Series(["Premier League"] * 3)
    assert list(season_for(dates, leagues)) == [2027, 2027, 2026]


def test_calendar_year_leagues_do_not_roll():
    dates = pd.Series(["2026-09-15", "2026-03-10"])
    leagues = pd.Series(["MLS", "NWSL"])
    assert list(season_for(dates, leagues)) == [2026, 2026]


# --- player scoring ---------------------------------------------------------

def player(**over):
    row = {"Player": "Test", "Comp_clean": "Premier League", "season": 2027,
           "Pos": "FW", "MP": 0, "Starts": 0, "Min": 0,
           "Gls": 0, "Ast": 0, "CrdY": 0, "CrdR": 0}
    row.update(over)
    return row


def test_goals_are_worth_more_the_further_back_you_play():
    assert goal_points_for("DF") == 6
    assert goal_points_for("GK") == 6
    assert goal_points_for("MF") == 5
    assert goal_points_for("FW") == 4
    assert goal_points_for(None) == 4, "unknown positions default to forward"


def test_player_points_match_hand_calculation():
    """30 starts + 4 sub appearances, 20 goals as a forward, 10 assists, 5 yellows.

    30*2 + 4*1 + 20*4 + 10*3 + 5*-1 = 60 + 4 + 80 + 30 - 5 = 169
    """
    df = pd.DataFrame([player(MP=34, Starts=30, Gls=20, Ast=10, CrdY=5)])
    assert score_players(df).iloc[0]["total_points"] == pytest.approx(169.0)


def test_a_red_card_costs_three():
    df = pd.DataFrame([player(MP=1, Starts=1, CrdR=1)])
    assert score_players(df).iloc[0]["total_points"] == pytest.approx(2 - 3)


def test_defender_goals_outscore_forward_goals():
    defender = pd.DataFrame([player(Pos="DF", MP=30, Starts=30, Gls=10)])
    forward = pd.DataFrame([player(Pos="FW", MP=30, Starts=30, Gls=10)])
    gap = (score_players(defender).iloc[0]["total_points"]
           - score_players(forward).iloc[0]["total_points"])
    assert gap == pytest.approx(20.0)  # 10 goals * (6 - 4)


def test_appearance_points_are_per_game_not_per_season():
    """The R script tested season-total minutes against 60, awarding 2 points for
    an entire year. Per game the term is worth roughly a dozen goals to a
    regular starter."""
    df = pd.DataFrame([player(MP=34, Starts=30, Min=2800, Gls=0)])
    assert score_players(df).iloc[0]["total_points"] == pytest.approx(64.0)


def test_per_match_minutes_are_used_exactly_when_available():
    """60 minutes or more is a full appearance; less is a short one."""
    from whul.scoring.soccer import appearance_points_from_matches

    minutes = pd.Series([90, 60, 59, 12, 0])
    assert list(appearance_points_from_matches(minutes)) == [2, 2, 1, 1, 0]


def test_season_aggregates_approximate_the_same_rule():
    from whul.scoring.soccer import appearance_points_from_season

    points = appearance_points_from_season(pd.Series([30]), pd.Series([34]))
    assert points.iloc[0] == pytest.approx(64.0)


def test_the_season_approximation_is_documented_as_inexact():
    """A starter withdrawn at 50 minutes scores 2 by this route and 1 by the
    rule, so the imprecision is stated rather than hidden."""
    import inspect

    from whul.scoring.soccer import appearance_points_from_season

    doc = inspect.getdoc(appearance_points_from_season)
    assert "not exact" in doc


def test_substitute_appearances_score_less_than_starts():
    starter = pd.DataFrame([player(MP=10, Starts=10)])
    sub = pd.DataFrame([player(MP=10, Starts=0)])
    assert score_players(starter).iloc[0]["total_points"] == 20
    assert score_players(sub).iloc[0]["total_points"] == 10


def test_empty_inputs_return_empty():
    assert score_players(pd.DataFrame()).empty
    assert score_teams(pd.DataFrame()).empty
    assert score_team_matches(pd.DataFrame()).empty


# --- classifying by the feed's key ------------------------------------------

def test_the_competition_key_decides_the_tier():
    """ESPN returns the league name at the top of the response, not on each
    event, so reading it per-event yields the bare key. Classifying by the key
    we requested avoids depending on a name arriving at all."""
    from whul.scoring.competition import classify_key

    assert classify_key("ucl", "ucl").win_points == 5
    assert classify_key("uel", "uel").win_points == 4
    assert classify_key("facup", "facup").win_points == 4
    assert classify_key("epl", "epl").win_points == 3


def test_bare_cup_keys_would_otherwise_score_as_league_fixtures():
    """The failure this prevents, measured.

    When the display name is absent the label is the bare key. Five of the six
    domestic cup keys then match no name pattern and fall through to the league
    tier, scoring 4-point cup wins as 3. The European keys survive only because
    their abbreviations happen to appear in the patterns -- luck, not design.
    """
    from whul.scoring.competition import classify

    mis_scored = [
        key for key in ("facup", "efl_cup", "copadelrey", "coppaitalia", "coupedefrance")
        if classify(key).win_points != classify_key_points(key)
    ]
    assert mis_scored == ["facup", "efl_cup", "copadelrey", "coppaitalia", "coupedefrance"]
    assert all(classify_key_points(k) == 4 for k in mis_scored)
    assert all(classify(k).win_points == 3 for k in mis_scored)


def classify_key_points(key, label=None):
    from whul.scoring.competition import classify_key

    return classify_key(key, label).win_points


def test_the_round_still_decides_qualifying():
    assert classify_key_points("ucl", "Champions League Qualifying") == 0
    assert classify_key_points("ucl", "Knockout Phase Play-off") == 5


def test_an_unknown_key_falls_back_to_the_name():
    assert classify_key_points("mystery_cup", "UEFA Champions League") == 5


def test_scoring_prefers_the_key_over_the_label():
    rows = pd.DataFrame([{
        "team": "Arsenal", "league": "Premier League", "date": "2026-10-22",
        "competition": "ucl", "competition_key": "ucl",
        "goals_for": 3, "goals_against": 1,
    }])
    # Champions League win with a two-goal margin: 5 + 1
    assert score_team_matches(rows).iloc[0]["match_points"] == 6


# --- round names, as the feeds actually write them --------------------------

def test_ordinal_cup_rounds_are_the_competition_proper():
    """The FA Cup's own rounds are called first, second and third round.

    An earlier qualifying pattern matched bare ordinals to catch UEFA
    qualifiers, and would have silently dropped legitimate cup ties. ESPN
    happened to write "third round" in the probe, but nothing guarantees that.
    """
    for label in ("English FA Cup third round", "English FA Cup 3rd round",
                  "English FA Cup 1st round", "English FA Cup 2nd round"):
        result = classify_key_points_full("facup", label)
        assert result.counts is True, label
        assert result.win_points == 4, label


def classify_key_points_full(key, label):
    from whul.scoring.competition import classify_key

    return classify_key(key, label)


def test_uefa_qualifying_is_still_excluded():
    """UEFA always names these "qualifying" or "play-off round", which is why
    those two forms are sufficient and bare ordinals are not needed."""
    for label in ("UEFA Champions League Third Qualifying Round",
                  "UEFA Champions League Play-off Round",
                  "FA Cup First Qualifying Round"):
        assert classify_key_points_full("ucl", label).counts is False, label


def test_the_knockout_playoff_survives_the_qualifying_filter():
    result = classify_key_points_full("ucl", "UEFA Champions League Knockout Phase Play-off")
    assert result.counts is True
    assert result.win_points == 5


def test_a_domestic_postseason_outranks_its_regular_season():
    """The R script groups Play-off competitions with the Champions League at 5.
    For MLS and NWSL that is the postseason."""
    from whul.scoring.competition import Tier

    postseason = classify_key_points_full("mls", "MLS Cup Playoffs")
    regular = classify_key_points_full("mls", "MLS regular season")
    assert postseason.tier is Tier.DOMESTIC_POSTSEASON
    assert postseason.win_points == 5
    assert regular.win_points == 3
    assert classify_key_points_full("nwsl", "NWSL Playoffs").win_points == 5


def test_european_competitions_are_not_relabelled_as_postseason():
    """Only a league tier can be promoted; a UCL knockout tie stays UCL."""
    from whul.scoring.competition import Tier

    result = classify_key_points_full("ucl", "UEFA Champions League Knockout Phase Play-off")
    assert result.tier is Tier.CHAMPIONS_LEAGUE


def test_name_only_classification_also_finds_the_postseason():
    """For sources that supply no key, such as the historical FBref exports."""
    assert classify("MLS Cup Playoffs").win_points == 5


# --- which clubs belong in a league's pool ---------------------------------

def test_a_leagues_pool_is_its_own_clubs_not_everyone_they_played(monkeypatch):
    """A competition's scoreboard returns every match in it. Unfiltered, the
    Premier League pool was 213 clubs a season instead of 20, with Real Madrid
    and every lower-division cup opponent labelled Premier League."""
    import pandas as pd

    from whul.benchmark_sources import SOURCES
    from whul.sources import espn

    matches = pd.DataFrame([
        {"team": "Arsenal", "opponent": "Real Madrid", "season": 2026},
        {"team": "Real Madrid", "opponent": "Arsenal", "season": 2026},
        {"team": "Barnsley", "opponent": "Arsenal", "season": 2026},
    ])
    monkeypatch.setattr(espn, "load_soccer_matches", lambda key, seasons: matches)
    monkeypatch.setattr(espn, "load_eligible_teams", lambda key: {"Arsenal"})

    load, _ = SOURCES["epl"].build()
    kept = load([2026])
    assert list(kept["team"]) == ["Arsenal"]
    assert list(kept["league"]) == ["Premier League"]


def test_an_unreadable_team_list_is_announced_not_silently_ignored(monkeypatch, capsys):
    import pandas as pd

    from whul.benchmark_sources import SOURCES
    from whul.sources import espn

    matches = pd.DataFrame([{"team": "Arsenal", "season": 2026}])
    monkeypatch.setattr(espn, "load_soccer_matches", lambda key, seasons: matches)
    monkeypatch.setattr(espn, "load_eligible_teams", lambda key: set())

    load, _ = SOURCES["epl"].build()
    assert len(load([2026])) == 1
    assert "every opponent it met" in capsys.readouterr().out


# --- the components behind a club total ------------------------------------

def test_a_club_total_carries_the_wins_that_made_it():
    """Two wins can be nine points or fourteen, and the total alone does not
    say which. A club on eleven from two wins has not won twice in its league
    -- the league pays three a win and five at most with both bonuses -- and
    without the breakdown nobody reading the profile can reach that number."""
    matches = pd.DataFrame([
        {"team": "Borussia Dortmund", "league": "Bundesliga", "date": "2026-08-16",
         "competition": "DFB-Pokal", "goals_for": 4, "goals_against": 0},
        {"team": "Borussia Dortmund", "league": "Bundesliga", "date": "2026-08-23",
         "competition": "Bundesliga", "goals_for": 3, "goals_against": 0},
    ])
    row = soccer.score_teams(matches).iloc[0]

    assert row["wins"] == 2
    assert row["wins_domestic_cup"] == 1
    assert row["wins_league"] == 1
    assert row["pts_wins"] == 7          # 4 for the cup tie, 3 for the league one
    assert row["big_margins"] == 2
    assert row["clean_sheets"] == 2
    assert row["total_points"] == 11
    # And the parts must reconstruct the whole, which is the point of storing them.
    assert (row["pts_wins"] + row["pts_big_margin"] + row["pts_clean_sheet"]
            + row["bye_points"]) == row["total_points"]


def test_a_win_in_europe_is_told_from_a_win_at_home():
    """The tier premium is what makes gathering every competition worthwhile:
    restricted to league fixtures every win would be worth three."""
    matches = pd.DataFrame([
        {"team": "Arsenal", "league": "Premier League", "date": "2026-09-16",
         "competition": "UEFA Champions League", "goals_for": 1, "goals_against": 0},
        {"team": "Arsenal", "league": "Premier League", "date": "2026-09-20",
         "competition": "Premier League", "goals_for": 1, "goals_against": 0},
    ])
    row = soccer.score_teams(matches).iloc[0]
    assert row["wins_champions_league"] == 1
    assert row["wins_league"] == 1
    assert row["pts_wins"] == 8          # 5 + 3
    assert row["total_points"] == 10     # plus two clean sheets, no big margins
