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


#: What each competition pays, and why it differs. The share is not a property
#: of the sport but of how much is *known at the draft*: a league drafted
#: before it starts pays the full share, one drafted mid-season pays less
#: because the standings already hint at the field, and a competition whose
#: entrants were decided last season pays least of all.
EXPECTED_SHARES = {
    "NFL": 0.10, "NBA": 0.10, "NHL": 0.10,
    "MLB": 0.075, "WNBA": 0.075, "NWSL": 0.075,
    # MLS is on the mid-season leagues' calendar and not on their draft: its
    # season opens in February, inside the league year, so a manager drafts an
    # MLS club before a ball is kicked.
    "MLS": 0.10,
    "UCL": 0.05, "Europa League": 0.05, "Europa Conference League": 0.05,
    "CONCACAF Champions Cup": 0.025,
}


def test_every_competition_pays_the_share_the_admin_set():
    assert DEFAULT_BONUS_SHARE == 0.10
    assert set(RULES) == set(EXPECTED_SHARES), "a rule was added without a share"
    for name, want in EXPECTED_SHARES.items():
        assert RULES[name].bonus_share == pytest.approx(want), name


def test_a_scalar_is_its_share_of_its_own_season():
    expected = {"NFL": 1.7, "MLB": 12.15, "NBA": 8.2, "NHL": 8.4,
                "WNBA": 3.3, "NWSL": 1.95, "MLS": 3.4,
                "UCL": 1.9, "Europa League": 1.9,
                "Europa Conference League": 1.9,
                "CONCACAF Champions Cup": 0.85}
    for name, want in expected.items():
        assert RULES[name].scalar == pytest.approx(want), name


def test_a_league_drafted_mid_season_pays_less_than_one_drafted_before_it():
    """The reason the shares differ at all: by July the standings already say
    a great deal about who plays in October."""
    assert RULES["MLB"].bonus_share < RULES["NFL"].bonus_share


def test_a_settled_field_pays_least():
    """European entrants are decided by the season that has just finished, so
    at the draft there is nothing left to find out."""
    assert RULES["UCL"].bonus_share < RULES["MLB"].bonus_share
    assert (RULES["CONCACAF Champions Cup"].bonus_share
            < RULES["UCL"].bonus_share)


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


def test_a_full_run_at_your_regular_rate_is_worth_its_own_share():
    """The defining property, still: a player who performs in the postseason
    exactly as well as they did in the regular season earns their
    competition's share of a season, whatever the length of that season."""
    for name, share in EXPECTED_SHARES.items():
        rule = RULES[name]
        reg_games = rule.regular_games
        rate = 10.0
        out = apply_bonus(
            phase_frame(rate * reg_games, reg_games, rate * 4, 4), rule
        ).iloc[0]
        assert out["postseason_bonus"] / out["regular_points"] == pytest.approx(
            share), name


def test_outperforming_your_regular_rate_earns_more_than_the_share():
    rule = RULES["NFL"]
    out = apply_bonus(phase_frame(170.0, 17, 40.0, 1), rule).iloc[0]  # 40/game vs 10
    assert out["postseason_bonus"] / out["regular_points"] > rule.bonus_share


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


# --- the rate denominator --------------------------------------------------

def test_rate_uses_player_games_not_team_games():
    """A player who appeared in 2 of his team's 4 playoff games is rated on 2.

    Using team games would halve the rate of anyone who missed a game, and would
    reward a player who sat out for being on a team that went deep.
    """
    played_two = apply_bonus(phase_frame(170.0, 17, 60.0, 2), RULES["NFL"]).iloc[0]
    played_four = apply_bonus(phase_frame(170.0, 17, 60.0, 4), RULES["NFL"]).iloc[0]
    assert played_two["postseason_rate"] == pytest.approx(30.0)
    assert played_four["postseason_rate"] == pytest.approx(15.0)
    assert played_two["postseason_bonus"] > played_four["postseason_bonus"]


def test_a_player_who_did_not_appear_earns_nothing_from_a_deep_run():
    """Being rostered on a finalist is not itself worth anything."""
    out = apply_bonus(phase_frame(170.0, 17, 0.0, 0), RULES["NFL"]).iloc[0]
    assert out["postseason_bonus"] == 0.0
