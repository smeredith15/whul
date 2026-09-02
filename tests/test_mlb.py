"""MLB scoring tests.

Expected values are computed by hand from MLB_Players_Teams.R.
"""

import pandas as pd
import pytest

from whul.scoring.mlb import (
    MULT_YEAR_N,
    MULT_YEAR_N1,
    SHARE_POST_ASB,
    SHARE_PRE_ASB,
    combine_roles,
    score_batters,
    score_pitchers,
    score_players,
    score_teams,
    summarize_teams,
)


def batter(**over):
    row = {"season": 2025, "PlayerName": "Test Batter", "AB": 0, "H": 0, "2B": 0,
           "3B": 0, "HR": 0, "BB": 0, "HBP": 0, "SB": 0, "CS": 0, "Off": 0, "Def": 0, "G": 150}
    row.update(over)
    return row


def pitcher(**over):
    row = {"season": 2025, "PlayerName": "Test Pitcher", "IP": 0, "SO": 0, "H": 0,
           "BB": 0, "HBP": 0, "HR": 0, "SV": 0, "HLD": 0, "WAR": 0, "G": 30}
    row.update(over)
    return row


# --- batters ---------------------------------------------------------------

def test_batter_points_match_hand_calculation():
    """500*-1 + 150*5.6 + 30*2.9 + 3*5.7 + 35*9.4 + 60*3 + 5*3 + 10*1.9 + 3*-2.8
    = -500 + 840 + 87 + 17.1 + 329 + 180 + 15 + 19 - 8.4 = 978.7"""
    df = pd.DataFrame([batter(AB=500, H=150, **{"2B": 30, "3B": 3}, HR=35, BB=60,
                              HBP=5, SB=10, CS=3)])
    assert score_batters(df).iloc[0]["role_points"] == pytest.approx(978.7)


def test_offense_and_defense_components_are_applied():
    """Off 40 * 0.25 = 10; Def 12 * 1.5 = 18, on top of the counting stats."""
    df = pd.DataFrame([batter(H=100, Off=40, Def=12)])
    assert score_batters(df).iloc[0]["role_points"] == pytest.approx(100 * 5.6 + 10 + 18)


def test_at_bats_carry_a_penalty():
    """An out costs; only what a batter does with the plate appearance pays."""
    high_ab = score_batters(pd.DataFrame([batter(AB=600, H=150)])).iloc[0]["role_points"]
    low_ab = score_batters(pd.DataFrame([batter(AB=400, H=150)])).iloc[0]["role_points"]
    assert low_ab - high_ab == pytest.approx(200.0)


def test_non_positive_batters_are_dropped():
    assert score_batters(pd.DataFrame([batter(AB=500)])).empty


# --- pitchers --------------------------------------------------------------

def test_pitcher_points_match_hand_calculation():
    """200*7.4 + 220*2 + 160*-2.6 + 50*-3 + 5*-3 + 20*-12.3 + 0 + 0 + 5*0.5*10
    = 1480 + 440 - 416 - 150 - 15 - 246 + 25 = 1118.0"""
    df = pd.DataFrame([pitcher(IP=200, SO=220, H=160, BB=50, HBP=5, HR=20, WAR=5)])
    assert score_pitchers(df).iloc[0]["role_points"] == pytest.approx(1118.0)


def test_saves_and_holds_reward_relievers():
    df = pd.DataFrame([pitcher(IP=70, SO=90, SV=40, HLD=5)])
    # 70*7.4 + 90*2 + 40*5 + 5*4 = 518 + 180 + 200 + 20 = 918
    assert score_pitchers(df).iloc[0]["role_points"] == pytest.approx(918.0)


def test_home_runs_allowed_are_the_heaviest_penalty():
    base = score_pitchers(pd.DataFrame([pitcher(IP=200, SO=200)])).iloc[0]["role_points"]
    with_hr = score_pitchers(pd.DataFrame([pitcher(IP=200, SO=200, HR=10)])).iloc[0]["role_points"]
    assert base - with_hr == pytest.approx(123.0)


# --- the two-way rule ------------------------------------------------------

def test_single_role_player_scores_that_role_in_full():
    out = score_players(pd.DataFrame([batter(H=100)]), pd.DataFrame([]))
    assert len(out) == 1
    assert out.iloc[0]["total_points"] == pytest.approx(560.0)
    assert not out.iloc[0]["is_two_way"]


def test_two_way_player_scores_primary_plus_half_secondary():
    """Not an Ohtani special case -- the same arithmetic for anyone who does both."""
    both = "Two Way Guy"
    out = score_players(
        pd.DataFrame([batter(PlayerName=both, H=100)]),      # 560
        pd.DataFrame([pitcher(PlayerName=both, IP=100)]),    # 740
    ).iloc[0]
    assert out["is_two_way"]
    assert out["role"] == "Pitcher", "the higher-scoring role is primary"
    assert out["total_points"] == pytest.approx(740 + 560 * 0.5)


def test_position_player_mopping_up_an_inning_uses_the_same_rule():
    """A blowout inning is a tiny secondary score, not a special case."""
    name = "Utility Guy"
    out = score_players(
        pd.DataFrame([batter(PlayerName=name, H=120)]),          # 672
        pd.DataFrame([pitcher(PlayerName=name, IP=1, SO=1)]),    # 9.4
    ).iloc[0]
    assert out["is_two_way"]
    assert out["role"] == "Batter"
    assert out["total_points"] == pytest.approx(672 + 9.4 * 0.5)


def test_two_way_players_normalize_against_their_primary_role():
    """A separate Two-Way group would be meaningless: position players who pitch
    an inning would swamp it, and a genuine two-way star would have no peers."""
    from whul.normalize import assign_norm_key

    out = score_players(
        pd.DataFrame([batter(PlayerName="X", H=200)]),
        pd.DataFrame([pitcher(PlayerName="X", IP=10)]),
    )
    assert out.iloc[0]["role"] == "Batter"
    assert assign_norm_key(out, "Player").iloc[0] == "MLB_Batter"


def test_players_are_matched_across_roles_by_name_and_season():
    out = score_players(
        pd.DataFrame([batter(PlayerName="A", H=100), batter(PlayerName="B", H=90)]),
        pd.DataFrame([pitcher(PlayerName="A", IP=50)]),
    )
    assert len(out) == 2
    assert out.set_index("player").loc["A", "is_two_way"]
    assert not out.set_index("player").loc["B", "is_two_way"]


def test_a_missing_feed_degrades_rather_than_raising():
    """A partial fetch must not crash the run before anything is scored."""
    assert score_batters(pd.DataFrame()).empty
    assert score_pitchers(pd.DataFrame()).empty
    assert combine_roles(pd.DataFrame(), pd.DataFrame()).empty
    assert score_players(pd.DataFrame(), pd.DataFrame()).empty
    assert score_teams(pd.DataFrame()).empty


# --- teams: the contract engine -------------------------------------------

def game(home, away, hs, as_, game_type="R", season=2025):
    return {"season": season, "game_type": game_type, "home_team": home,
            "away_team": away, "home_score": hs, "away_score": as_}


def two_seasons(rows_2025, rows_2026):
    return pd.DataFrame(rows_2025 + rows_2026)


def test_contract_multipliers_reconcile_to_a_full_season():
    """The year N+1 inflation exists so the weighted shares sum to 1."""
    assert SHARE_POST_ASB * MULT_YEAR_N + SHARE_PRE_ASB * MULT_YEAR_N1 == pytest.approx(1.0)
    assert MULT_YEAR_N1 == pytest.approx(1.181, abs=0.001)


def test_team_summary_counts_the_right_things():
    sched = pd.DataFrame([
        game("NYY", "BOS", 8, 0),   # win by 8: big win and a shutout
        game("BOS", "NYY", 3, 1),   # NYY loses
        game("NYY", "BOS", 5, 4),   # narrow win
    ])
    out = summarize_teams(sched).set_index("team")
    assert out.loc["NYY", "reg_wins"] == 2
    assert out.loc["NYY", "reg_big_wins"] == 1
    assert out.loc["NYY", "shutouts"] == 1
    assert out.loc["NYY", "run_diff"] == (8 - 0) + (1 - 3) + (5 - 4)


def test_shutout_requires_a_win():
    out = summarize_teams(pd.DataFrame([game("NYY", "BOS", 0, 0)])).set_index("team")
    assert out.loc["NYY", "shutouts"] == 0


def test_series_milestones_allow_for_a_bye():
    """Reaching the LDS counts as clearing the wild card round, bye or not."""
    sched = pd.DataFrame(
        [game("NYY", "BOS", 5, 1, game_type="D")] * 3 + [game("NYY", "BOS", 5, 1)]
    )
    out = summarize_teams(sched).set_index("team")
    assert out.loc["NYY", "series_wc_or_bye"] == 1
    assert out.loc["NYY", "series_lds"] == 1


def test_world_series_milestone_needs_four_wins():
    three = summarize_teams(
        pd.DataFrame([game("NYY", "BOS", 5, 1, game_type="W")] * 3)
    ).set_index("team")
    four = summarize_teams(
        pd.DataFrame([game("NYY", "BOS", 5, 1, game_type="W")] * 4)
    ).set_index("team")
    assert three.loc["NYY", "series_ws"] == 0
    assert four.loc["NYY", "series_ws"] == 1


def test_contract_needs_both_seasons_present():
    """A contract year pairs season N with N+1, so a lone season scores nothing."""
    assert score_teams(pd.DataFrame([game("NYY", "BOS", 5, 1)])).empty


def test_contract_blends_the_two_seasons():
    """One regular-season win in each of 2025 and 2026.

    year N:   1 * 0.42 * 2.0 * 0.75            = 0.63
    run diff: 4 * 0.42 * 0.05 * 0.75           = 0.063
    year N+1: 1 * 0.58 * 2.0 * 1.181           = 1.370
    run diff: 4 * 0.58 * 0.05 * 1.181          = 0.137
    """
    sched = two_seasons(
        [game("NYY", "BOS", 5, 1, season=2025)],
        [game("NYY", "BOS", 5, 1, season=2026)],
    )
    out = score_teams(sched)
    nyy = out[out["team"] == "NYY"].iloc[0]
    assert nyy["season"] == 2025
    expected_n = 0.42 * 2.0 * 0.75 + 4 * 0.42 * 0.05 * 0.75
    expected_n1 = 0.58 * 2.0 * MULT_YEAR_N1 + 4 * 0.58 * 0.05 * MULT_YEAR_N1
    # Both teams top their season on wins, so both count as division champions.
    assert nyy["year_n1_points"] == pytest.approx(expected_n1)
    assert nyy["year_n_points"] == pytest.approx(expected_n + 5.0 * 0.75)


def test_series_milestones_are_not_deflated():
    """Flat values, unlike everything else in year N."""
    sched = two_seasons(
        [game("NYY", "BOS", 5, 1, game_type="D", season=2025)] * 3
        + [game("NYY", "BOS", 5, 1, season=2025)],
        [game("NYY", "BOS", 5, 1, season=2026)],
    )
    nyy = score_teams(sched)
    nyy = nyy[nyy["team"] == "NYY"].iloc[0]
    # wc_or_bye 5 + lds 6 = 11 flat, plus the discounted regular-season terms
    assert nyy["year_n_points"] > 11.0
