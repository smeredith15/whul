"""Club soccer scoring tests.

Expected values are computed by hand from Club_Soccer.R.
"""

import pandas as pd
import pytest

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


def test_appearance_points_are_per_appearance_not_per_season():
    """The R script tests season-total minutes against 60, which awards 2 points
    for an entire year and makes the term meaningless. Per appearance it is
    worth roughly as much as a dozen goals to a regular starter.
    """
    df = pd.DataFrame([player(MP=34, Starts=30, Min=2800, Gls=0)])
    per_appearance = score_players(df, per_appearance=True).iloc[0]["total_points"]
    literal = score_players(df, per_appearance=False).iloc[0]["total_points"]
    assert per_appearance == pytest.approx(64.0)
    assert literal == pytest.approx(2.0)


def test_substitute_appearances_score_less_than_starts():
    starter = pd.DataFrame([player(MP=10, Starts=10)])
    sub = pd.DataFrame([player(MP=10, Starts=0)])
    assert score_players(starter).iloc[0]["total_points"] == 20
    assert score_players(sub).iloc[0]["total_points"] == 10


def test_empty_inputs_return_empty():
    assert score_players(pd.DataFrame()).empty
    assert score_teams(pd.DataFrame()).empty
    assert score_team_matches(pd.DataFrame()).empty
