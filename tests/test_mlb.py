"""MLB scoring tests.

Expected values are computed by hand from MLB_Players_Teams.R.
"""

import pandas as pd
import pytest

from whul.normalize import apply_benchmarks, compute_benchmarks
from whul.scoring.mlb import (
    MULT_YEAR_N,
    MULT_YEAR_N1,
    SHARE_POST_ASB,
    SHARE_PRE_ASB,
    combine_two_way,
    score_batters,
    score_pitchers,
    score_players,
    score_teams,
    summarize_teams,
)


def normalized(batters, pitchers):
    """Score, normalize per role, then fold two-way players together."""
    roles = score_players(batters, pitchers)
    bench = compute_benchmarks(roles, "Player")
    return combine_two_way(apply_benchmarks(roles, bench, "Player"))


def filler(n=70):
    """Enough peers in each role that a benchmark is meaningful."""
    bats = [batter(PlayerName=f"B{i}", H=80 + i, AB=400) for i in range(n)]
    pits = [pitcher(PlayerName=f"P{i}", IP=150, SO=120 + i) for i in range(n)]
    return bats, pits


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


def test_a_bad_stretch_scores_below_zero_rather_than_vanishing():
    """500 at bats and nothing to show for them is a real, terrible line. It
    used to be dropped for being non-positive, which made a wrecked stretch
    free -- and for a two-way player meant his secondary role could only ever
    help him. Over a season nobody finishes in the red, so this only bites on a
    short window, which is where a single start is most of the sample."""
    out = score_batters(pd.DataFrame([batter(AB=500)]))
    assert len(out) == 1
    assert out.loc[0, "role_points"] == pytest.approx(-500.0)


def test_a_player_who_did_not_appear_gets_no_row():
    """No outing is not a bad outing. A row of zeroes means he did not play,
    and carrying it would make a batter two-way on the strength of a game he
    never pitched in."""
    assert score_batters(pd.DataFrame([batter(AB=0, G=0)])).empty
    assert score_pitchers(pd.DataFrame([pitcher(IP=0, G=0)])).empty


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

def test_scoring_emits_one_row_per_player_role():
    """Roles stay separate through scoring so each can meet its own benchmark."""
    out = score_players(
        pd.DataFrame([batter(PlayerName="X", H=100)]),
        pd.DataFrame([pitcher(PlayerName="X", IP=100)]),
    )
    assert len(out) == 2
    assert set(out["role"]) == {"Batter", "Pitcher"}


def test_each_role_is_measured_against_its_own_benchmark():
    """Batting against the batter p99, pitching against the pitcher p99.

    Raw batting and pitching points are on unlike scales, so they can only be
    combined once each has been put on the 0-100 scale.
    """
    from whul.normalize import assign_norm_key

    bats, pits = filler()
    roles = score_players(
        pd.DataFrame(bats + [batter(PlayerName="X", H=200)]),
        pd.DataFrame(pits + [pitcher(PlayerName="X", IP=200, SO=250)]),
    )
    assert set(assign_norm_key(roles, "Player")) == {"MLB_Batter", "MLB_Pitcher"}
    bench = compute_benchmarks(roles, "Player")
    assert set(bench["norm_key"]) == {"MLB_Batter", "MLB_Pitcher"}
    assert "MLB_Two-Way" not in set(bench["norm_key"]), "no separate two-way group"


def test_two_way_combines_normalized_scores_not_raw_points():
    bats, pits = filler()
    out = normalized(
        pd.DataFrame(bats + [batter(PlayerName="X", H=200, HR=40)]),
        pd.DataFrame(pits + [pitcher(PlayerName="X", IP=200, SO=250, WAR=6)]),
    )
    row = out[out["player"] == "X"].iloc[0]
    assert row["is_two_way"]
    assert row["scaled_score"] == pytest.approx(
        row["primary_score"] + row["secondary_score"] * 0.5, abs=0.01
    )


def test_primary_role_is_decided_on_the_normalized_scale():
    """Raw points would pick the wrong role: the two scales are not comparable."""
    bats, pits = filler()
    roles = score_players(
        pd.DataFrame(bats + [batter(PlayerName="X", H=95, AB=400)]),
        pd.DataFrame(pits + [pitcher(PlayerName="X", IP=210, SO=300, WAR=8)]),
    )
    raw = roles[roles["player"] == "X"].set_index("role")["total_points"]
    scored = apply_benchmarks(roles, compute_benchmarks(roles, "Player"), "Player")
    norm = scored[scored["player"] == "X"].set_index("role")["scaled_score"]
    combined = combine_two_way(scored)
    row = combined[combined["player"] == "X"].iloc[0]
    assert row["role"] == norm.idxmax()
    assert row["primary_score"] == pytest.approx(norm.max())


def test_position_player_mopping_up_an_inning_uses_the_same_rule():
    """A blowout inning is a tiny secondary score, not a special case."""
    bats, pits = filler()
    out = normalized(
        pd.DataFrame(bats + [batter(PlayerName="Utility", H=140)]),
        pd.DataFrame(pits + [pitcher(PlayerName="Utility", IP=1, SO=1)]),
    )
    row = out[out["player"] == "Utility"].iloc[0]
    assert row["is_two_way"]
    assert row["role"] == "Batter"
    assert row["secondary_score"] < 5, "one mop-up inning is worth very little"


def test_single_role_players_are_unaffected_by_the_fold():
    bats, pits = filler()
    out = normalized(pd.DataFrame(bats), pd.DataFrame(pits))
    assert not out["is_two_way"].any()
    assert out["scaled_score"].equals(out["primary_score"])


def test_a_missing_feed_degrades_rather_than_raising():
    """A partial fetch must not crash the run before anything is scored."""
    assert score_batters(pd.DataFrame()).empty
    assert score_pitchers(pd.DataFrame()).empty
    assert score_players(pd.DataFrame(), pd.DataFrame()).empty
    assert combine_two_way(pd.DataFrame()).empty
    assert score_teams(pd.DataFrame()).empty


# --- teams: the contract engine -------------------------------------------

def game(home, away, hs, as_, game_type="R", season=2025):
    return {"season": season, "game_type": game_type, "home_team": home,
            "away_team": away, "home_score": hs, "away_score": as_}


def two_seasons(rows_2025, rows_2026):
    return pd.DataFrame(rows_2025 + rows_2026)


def divisions_of(*teams, season=2025, division="AL East"):
    """A one-division map, for the seasons a test spans."""
    return pd.DataFrame([
        {"season": year, "team": team, "division": division}
        for year in (season, season + 1) for team in teams
    ])


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
    out = score_teams(sched, divisions=divisions_of("NYY", "BOS"))
    nyy = out[out["team"] == "NYY"].iloc[0]
    assert nyy["season"] == 2025
    expected_n = 0.42 * 2.0 * 0.75 + 4 * 0.42 * 0.05 * 0.75
    expected_n1 = 0.58 * 2.0 * MULT_YEAR_N1 + 4 * 0.58 * 0.05 * MULT_YEAR_N1
    # NYY beat BOS, so it leads their shared division and takes the title.
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


# --- the FanGraphs dependency ----------------------------------------------

def test_advanced_metrics_are_included_by_default():
    """Offense, Defense and WAR are part of the scoring as the R script defines it."""
    from whul.scoring.mlb import INCLUDE_ADVANCED_METRICS

    assert INCLUDE_ADVANCED_METRICS is True


def test_batters_can_be_scored_without_the_advanced_terms():
    """Available only if FanGraphs proves permanently unreachable, and only as a
    deliberate rules change -- never as a silent fallback."""
    df = pd.DataFrame([batter(AB=500, H=150, HR=30, Off=60, Def=20)])
    with_adv = score_batters(df, include_advanced=True).iloc[0]["role_points"]
    without = score_batters(df, include_advanced=False).iloc[0]["role_points"]
    # Off 60 * 0.25 + Def 20 * 1.5 = 15 + 30 = 45
    assert with_adv - without == pytest.approx(45.0)


def test_pitchers_can_be_scored_without_war():
    df = pd.DataFrame([pitcher(IP=200, SO=220, H=160, BB=50, WAR=6)])
    with_adv = score_pitchers(df, include_advanced=True).iloc[0]["role_points"]
    without = score_pitchers(df, include_advanced=False).iloc[0]["role_points"]
    assert with_adv - without == pytest.approx(6 * 0.5 * 10)


def test_dropping_advanced_terms_hurts_defenders_most():
    """The reason this is a rules change rather than a source swap: the terms are
    not uniform, so removing them compresses glove-first against bat-first."""
    slugger = pd.DataFrame([batter(AB=550, H=180, HR=50, BB=120, Off=70, Def=-5)])
    glove = pd.DataFrame([batter(AB=500, H=125, HR=10, BB=40, Off=-10, Def=20)])

    def share(df):
        full = score_batters(df, include_advanced=True).iloc[0]["role_points"]
        bare = score_batters(df, include_advanced=False).iloc[0]["role_points"]
        return (full - bare) / full

    assert share(glove) > share(slugger)
    assert share(slugger) < 0.02


def test_a_live_window_is_scored_at_face_value():
    """The historical path splits a whole season into post- and pre-break
    shares. A live pull is not a whole season to split -- the start date has
    already cut it to the league year's window -- so multiplying by
    SHARE_POST_ASB on top of that shortens it twice: once by the calendar, once
    by arithmetic that no longer describes it."""
    from whul.scoring.mlb import BASE_REG_WIN, score_teams

    schedule = pd.DataFrame([game("NYY", "BOS", 5, 1, season=2026)] * 20)
    assert score_teams(schedule).empty, "a benchmark needs both halves"

    live = score_teams(schedule, partial=True).set_index("team")
    assert set(live.index) == {"NYY", "BOS"}
    assert live.loc["NYY", "pts_reg_wins"] == pytest.approx(20 * BASE_REG_WIN)
    assert live.loc["NYY", "total_points"] >= live.loc["NYY", "pts_reg_wins"]


def test_the_window_separates_what_grows_from_what_happens_once():
    """Wins, big wins, shutouts and run differential grow with games played; a
    division title and a playoff run happen once however long the window is.
    Proration has to be able to tell them apart."""
    from whul.scoring.mlb import WINDOW_COUNTING, score_teams

    live = score_teams(pd.DataFrame([game("NYY", "BOS", 5, 1, season=2026)] * 20),
                       partial=True)
    for column in WINDOW_COUNTING:
        assert column in live.columns, column
    assert "pts_div_champ" in live.columns
    assert "pts_playoff" in live.columns
    assert "pts_div_champ" not in WINDOW_COUNTING


# --- division titles --------------------------------------------------------

def test_a_division_title_goes_to_the_most_wins_in_that_division():
    """Not the most wins in the league. A club can win 100 games and finish
    second, and a club can win 84 and win its division -- the old rule, which
    took the top fifth of the league by wins, got both of those backwards."""
    sched = pd.DataFrame(
        [game("NYY", "BOS", 5, 1)] * 3          # NYY 3 wins, BOS 0
        + [game("LAD", "SFG", 5, 1)] * 2        # LAD 2 wins, SFG 0
        + [game("SFG", "LAD", 5, 1)]            # SFG 1 win
    )
    divisions = pd.DataFrame([
        {"season": 2025, "team": "NYY", "division": "AL East"},
        {"season": 2025, "team": "BOS", "division": "AL East"},
        {"season": 2025, "team": "LAD", "division": "NL West"},
        {"season": 2025, "team": "SFG", "division": "NL West"},
    ])
    out = summarize_teams(sched, divisions).set_index("team")
    assert out.loc["NYY", "is_division_champ"] == 1
    assert out.loc["LAD", "is_division_champ"] == 1   # fewer wins than NYY
    assert out.loc["BOS", "is_division_champ"] == 0
    assert out.loc["SFG", "is_division_champ"] == 0


def test_a_division_tie_is_broken_on_head_to_head():
    """MLB's first tiebreaker, and the schedule already says who won those
    games. Both clubs finish on 3 wins; NYY took two of the three they played
    against each other."""
    sched = pd.DataFrame(
        [game("NYY", "BOS", 5, 1)] * 2       # NYY 2-1 head to head
        + [game("BOS", "NYY", 5, 1)]
        + [game("NYY", "TOR", 5, 1)]         # each pads to 3 wins elsewhere
        + [game("BOS", "TOR", 5, 1)] * 2
    )
    out = summarize_teams(sched, divisions_of("NYY", "BOS", "TOR")).set_index("team")
    assert out.loc["NYY", "reg_wins"] == out.loc["BOS", "reg_wins"] == 3
    assert out.loc["NYY", "is_division_champ"] == 1
    assert out.loc["BOS", "is_division_champ"] == 0


def test_the_second_tiebreaker_is_a_rate_not_a_count():
    """Level on wins and level head to head, so it falls to record inside the
    division -- where an unbalanced schedule gives the two clubs a different
    number of chances. BOS has *more* division wins (4 to 3) and a worse record
    (.571 to .750), and counting wins alone would hand it to the club that
    simply played more."""
    sched = pd.DataFrame(
        [game("NYY", "BOS", 5, 1), game("BOS", "NYY", 5, 1)]   # split 1-1
        + [game("NYY", "TOR", 5, 1)] * 2                       # NYY 3-1 in division
        + [game("BOS", "TOR", 5, 1)] * 2                       # BOS 4-3 in division
        + [game("TOR", "BOS", 5, 1)]
        + [game("BOS", "BAL", 5, 1), game("BAL", "BOS", 5, 1)]
        + [game("NYY", "HOU", 5, 1)]                           # HOU is outside it
    )
    out = summarize_teams(
        sched, divisions_of("NYY", "BOS", "TOR", "BAL")
    ).set_index("team")
    assert out.loc["NYY", "reg_wins"] == out.loc["BOS", "reg_wins"] == 4
    assert out.loc["NYY", "is_division_champ"] == 1
    assert out.loc["BOS", "is_division_champ"] == 0


def test_clubs_level_after_every_tiebreaker_take_it_together():
    """Two clubs that split their season series and finished level in the
    division have not been separated by anything that happened on a field.
    Picking one would be inventing a result rather than reading one."""
    sched = pd.DataFrame([game("NYY", "BOS", 5, 1), game("BOS", "NYY", 5, 1)])
    out = summarize_teams(sched, divisions_of("NYY", "BOS")).set_index("team")
    assert out.loc["NYY", "is_division_champ"] == 1
    assert out.loc["BOS", "is_division_champ"] == 1


def test_no_divisions_means_no_title_rather_than_a_guess():
    """A schedule does not say who a team was competing with for a division,
    so without that there is nothing to award."""
    sched = pd.DataFrame([game("NYY", "BOS", 5, 1)] * 3)
    out = summarize_teams(sched).set_index("team")
    assert set(out["is_division_champ"]) == {0}


def test_a_team_outside_the_division_map_wins_nothing_and_costs_nobody():
    """An expansion club or a renamed one is unknown to the map. It takes no
    title, and it does not become the yardstick for anyone else's."""
    sched = pd.DataFrame(
        [game("NYY", "BOS", 5, 1)] * 2 + [game("XXX", "BOS", 5, 1)] * 9
    )
    out = summarize_teams(sched, divisions_of("NYY", "BOS")).set_index("team")
    assert out.loc["XXX", "is_division_champ"] == 0
    assert out.loc["NYY", "is_division_champ"] == 1


def test_a_season_in_progress_awards_no_division_title():
    """Four clubs were being paid five points each for titles in the first
    three weeks of the league year. Nobody has won a division in September."""
    from whul.scoring.mlb import score_teams

    sched = pd.DataFrame([game("NYY", "BOS", 9, 0, season=2026)] * 15)
    live = score_teams(sched, partial=True,
                       divisions=divisions_of("NYY", "BOS", season=2026))
    assert set(live["pts_div_champ"]) == {0.0}
