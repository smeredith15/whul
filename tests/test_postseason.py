"""Postseason bonus tests.

A player who appears in the postseason is credited as though they played
``scalar`` extra games at their own per-game rate, computed across every game
they actually played.
"""

import pandas as pd
import pytest

from whul.scoring.postseason import RULES, PostseasonRule, apply_bonus, split_phases


def phase_frame(reg_pts, reg_games, post_pts, post_games):
    return pd.DataFrame([{
        "regular_points": reg_pts, "regular_games": reg_games,
        "postseason_points": post_pts, "postseason_games": post_games,
    }])


def test_scalars_match_the_agreed_values():
    expected = {"NFL": 4.25, "MLB": 162 / 22, "NBA": 82 / 28, "NHL": 3.0,
                "UCL": 38 / 17, "Europa Conference League": 38 / 21}
    for name, want in expected.items():
        assert RULES[name].scalar == pytest.approx(want, abs=0.01), name


def test_nhl_uses_the_expanded_84_game_season():
    assert RULES["NHL"].regular_games == 84
    assert RULES["NHL"].scalar == pytest.approx(3.0)


def test_bonus_is_rate_times_scalar():
    """160 pts over 16 reg + 40 over 4 playoff = 200/20 = 10/game.
    NFL scalar 4.25 -> bonus 42.5, total 160 + 42.5."""
    out = apply_bonus(phase_frame(160.0, 16, 40.0, 4), RULES["NFL"]).iloc[0]
    assert out["per_game_rate"] == pytest.approx(10.0)
    assert out["postseason_bonus"] == pytest.approx(42.5)
    assert out["total_points"] == pytest.approx(202.5)


def test_raw_postseason_points_never_enter_the_total_directly():
    """Only the bonus carries postseason value; the raw stats are not summed in."""
    out = apply_bonus(phase_frame(100.0, 10, 900.0, 1), RULES["NFL"]).iloc[0]
    assert out["total_points"] != pytest.approx(1000.0)
    assert out["total_points"] == pytest.approx(100.0 + (1000.0 / 11) * 4.25)


def test_no_appearance_earns_no_bonus():
    out = apply_bonus(phase_frame(300.0, 17, 0.0, 0), RULES["NFL"]).iloc[0]
    assert out["postseason_bonus"] == 0.0
    assert out["total_points"] == pytest.approx(300.0)


def test_one_appearance_earns_the_full_bonus():
    """The bonus is for appearing; a longer run does not enlarge it directly.

    Two players at the same rate get the same bonus regardless of how many
    postseason games they played -- only their rate scales it.
    """
    short = apply_bonus(phase_frame(170.0, 17, 10.0, 1), RULES["NFL"]).iloc[0]
    long = apply_bonus(phase_frame(170.0, 17, 40.0, 4), RULES["NFL"]).iloc[0]
    assert short["per_game_rate"] == pytest.approx(10.0)
    assert long["per_game_rate"] == pytest.approx(10.0)
    assert short["postseason_bonus"] == pytest.approx(long["postseason_bonus"])


def test_a_better_run_earns_a_bigger_bonus():
    weak = apply_bonus(phase_frame(170.0, 17, 10.0, 2), RULES["NFL"]).iloc[0]
    strong = apply_bonus(phase_frame(170.0, 17, 90.0, 2), RULES["NFL"]).iloc[0]
    assert strong["postseason_bonus"] > weak["postseason_bonus"]


def test_no_rule_means_no_bonus():
    out = apply_bonus(phase_frame(170.0, 17, 40.0, 4), None).iloc[0]
    assert out["postseason_bonus"] == 0.0
    assert out["total_points"] == pytest.approx(170.0)


def test_scalar_override_lets_the_admin_tune_without_touching_the_formula():
    rule = PostseasonRule("NFL", 17, 4, scalar_override=2.0)
    assert rule.scalar == 2.0
    out = apply_bonus(phase_frame(170.0, 17, 30.0, 3), rule).iloc[0]
    assert out["postseason_bonus"] == pytest.approx(10.0 * 2.0)


def test_bonus_share_reports_the_cross_league_spread():
    assert RULES["NFL"].bonus_share == pytest.approx(0.25, abs=0.005)
    assert RULES["NBA"].bonus_share == pytest.approx(0.036, abs=0.005)


def test_zero_games_does_not_divide_by_zero():
    out = apply_bonus(phase_frame(0.0, 0, 0.0, 0), RULES["NFL"]).iloc[0]
    assert out["per_game_rate"] == 0.0
    assert out["total_points"] == 0.0


def test_split_phases_separates_regular_and_postseason():
    rows = pd.DataFrame({
        "player": ["a", "a", "a"],
        "pts": [10.0, 20.0, 50.0],
        "g": [1, 1, 1],
    })
    is_post = pd.Series([False, False, True])
    out = split_phases(rows, ["player"], "pts", "g", is_post).iloc[0]
    assert out["regular_points"] == 30.0 and out["regular_games"] == 2
    assert out["postseason_points"] == 50.0 and out["postseason_games"] == 1


def test_split_phases_fills_missing_phase_with_zero():
    rows = pd.DataFrame({"player": ["a"], "pts": [10.0], "g": [1]})
    out = split_phases(rows, ["player"], "pts", "g", pd.Series([False])).iloc[0]
    assert out["postseason_points"] == 0.0 and out["postseason_games"] == 0.0
