"""Postseason bonus tests.

The bonus is a flat share of a regular season -- identical across leagues -- paid
at the player's postseason rate::

    bonus = (postseason_points / postseason_games) * (share * regular_games)
"""

import pandas as pd
import pytest

from whul.scoring.postseason import (
    DEFAULT_BONUS_SHARE,
    EXCLUDED,
    POSTSEASON,
    REGULAR,
    RULES,
    PostseasonRule,
    apply_bonus,
    split_phases,
)


def phase_frame(reg_pts, reg_games, post_pts, post_games):
    return pd.DataFrame([{
        "regular_points": reg_pts, "regular_games": reg_games,
        "postseason_points": post_pts, "postseason_games": post_games,
    }])


def test_share_is_ten_percent_everywhere():
    assert DEFAULT_BONUS_SHARE == 0.10
    for name, rule in RULES.items():
        assert rule.bonus_share == pytest.approx(0.10), name


def test_scalars_are_ten_percent_of_each_regular_season():
    expected = {"NFL": 1.7, "MLB": 16.2, "NBA": 8.2, "NHL": 8.4,
                "UCL": 3.8, "Europa League": 3.8, "Europa Conference League": 3.8}
    for name, want in expected.items():
        assert RULES[name].scalar == pytest.approx(want), name


def test_nhl_uses_the_expanded_84_game_season():
    assert RULES["NHL"].regular_games == 84


def test_one_playoff_game_multiplies_those_points_by_the_scalar():
    """NFL: one playoff game worth 20 -> 20 * 1.7 = 34."""
    out = apply_bonus(phase_frame(300.0, 17, 20.0, 1), RULES["NFL"]).iloc[0]
    assert out["postseason_bonus"] == pytest.approx(34.0)
    assert out["total_points"] == pytest.approx(334.0)


def test_two_playoff_games_use_the_combined_points_over_two():
    """40 points across two games -> 40 * 1.7/2 = 34, same as 20 in one game."""
    out = apply_bonus(phase_frame(300.0, 17, 40.0, 2), RULES["NFL"]).iloc[0]
    assert out["postseason_bonus"] == pytest.approx(34.0)


def test_a_full_run_at_your_regular_rate_is_worth_ten_percent_of_the_season():
    """The defining property: same proportional reward in every league."""
    for name in ("NFL", "MLB", "NBA", "NHL"):
        rule = RULES[name]
        reg_games = rule.regular_games
        rate = 10.0
        out = apply_bonus(
            phase_frame(rate * reg_games, reg_games, rate * 4, 4), rule
        ).iloc[0]
        assert out["postseason_bonus"] / out["regular_points"] == pytest.approx(0.10), name


def test_outperforming_your_regular_rate_earns_more_than_the_share():
    rule = RULES["NFL"]
    out = apply_bonus(phase_frame(170.0, 17, 40.0, 1), rule).iloc[0]  # 40/game vs 10
    assert out["postseason_bonus"] / out["regular_points"] > 0.10


def test_raw_postseason_points_never_enter_the_total_directly():
    out = apply_bonus(phase_frame(100.0, 10, 900.0, 1), RULES["NFL"]).iloc[0]
    assert out["total_points"] == pytest.approx(100.0 + 900.0 * 1.7)


def test_no_appearance_earns_no_bonus():
    out = apply_bonus(phase_frame(300.0, 17, 0.0, 0), RULES["NFL"]).iloc[0]
    assert out["postseason_bonus"] == 0.0
    assert out["total_points"] == pytest.approx(300.0)


def test_no_rule_means_no_bonus():
    out = apply_bonus(phase_frame(170.0, 17, 40.0, 4), None).iloc[0]
    assert out["total_points"] == pytest.approx(170.0)


def test_scalar_override_lets_the_admin_tune_a_single_league():
    rule = PostseasonRule("NFL", 17, scalar_override=5.0)
    out = apply_bonus(phase_frame(170.0, 17, 20.0, 2), rule).iloc[0]
    assert out["postseason_bonus"] == pytest.approx(10.0 * 5.0)


def test_share_can_be_retuned_globally():
    rule = PostseasonRule("NFL", 17, bonus_share=0.20)
    assert rule.scalar == pytest.approx(3.4)


def test_zero_games_does_not_divide_by_zero():
    out = apply_bonus(phase_frame(0.0, 0, 0.0, 0), RULES["NFL"]).iloc[0]
    assert out["total_points"] == 0.0


# --- phase splitting -------------------------------------------------------

def test_split_phases_separates_regular_and_postseason():
    rows = pd.DataFrame({"player": ["a"] * 3, "pts": [10.0, 20.0, 50.0], "g": [1, 1, 1]})
    phase = pd.Series([REGULAR, REGULAR, POSTSEASON])
    out = split_phases(rows, ["player"], "pts", "g", phase).iloc[0]
    assert out["regular_points"] == 30.0 and out["regular_games"] == 2
    assert out["postseason_points"] == 50.0 and out["postseason_games"] == 1


def test_excluded_rows_count_for_neither_phase():
    """Play-In and European qualifying must not pad the regular season either."""
    rows = pd.DataFrame({"player": ["a"] * 3, "pts": [10.0, 999.0, 50.0], "g": [1, 1, 1]})
    phase = pd.Series([REGULAR, EXCLUDED, POSTSEASON])
    out = split_phases(rows, ["player"], "pts", "g", phase).iloc[0]
    assert out["regular_points"] == 10.0 and out["regular_games"] == 1
    assert out["postseason_points"] == 50.0 and out["postseason_games"] == 1


def test_split_phases_fills_missing_phase_with_zero():
    rows = pd.DataFrame({"player": ["a"], "pts": [10.0], "g": [1]})
    out = split_phases(rows, ["player"], "pts", "g", pd.Series([REGULAR])).iloc[0]
    assert out["postseason_points"] == 0.0 and out["postseason_games"] == 0.0
