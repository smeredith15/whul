"""Bisection weighting for leagues whose season straddles the draft."""

import pytest

from whul.scoring.bisection import (
    MLB,
    NOT_BISECTED,
    NWSL,
    RULES,
    WNBA,
    BisectionRule,
    describe,
    from_days,
    rule_for,
    weights,
)


# --- the reconciling formula -----------------------------------------------

def test_the_two_weighted_stretches_reconcile_to_one_season():
    """That is what mult_n1 is for. Without it, discounting the known half
    would quietly shrink a bisected league's whole contribution against the
    leagues that are not bisected."""
    for rule in RULES.values():
        weighted = rule.share_post * rule.mult_n + rule.share_pre * rule.mult_n1
        assert weighted == pytest.approx(1.0), rule.league


def test_mlb_reproduces_the_figure_already_in_the_scripts():
    assert MLB.mult_n == 0.75
    assert MLB.mult_n1 == pytest.approx(1.181, abs=0.001)


def test_wnba_matches_the_stated_arithmetic():
    """0.80 x 0.53 = 0.424, then (1 - 0.424) / 0.47."""
    assert WNBA.mult_n == 0.80
    assert WNBA.mult_n1 == pytest.approx(1.2255, abs=0.0005)


def test_nwsl_follows_from_its_day_counts():
    """17 season days before the draft against 112 after, discounted 5%:
    (129 - 0.95 x 112) / 17."""
    assert NWSL.mult_n == 0.95
    assert NWSL.share_pre == pytest.approx(17 / 129)
    assert NWSL.mult_n1 == pytest.approx(22.6 / 17, abs=0.0001)


def test_a_lighter_discount_follows_from_less_being_known():
    """The more of a season remains after the draft, the less a manager knows
    when they draft into it, and the lighter the discount on it. MLB drafts
    with 42% left and takes 0.75; NWSL drafts with 87% left and takes 0.95."""
    assert MLB.share_post < WNBA.share_post < NWSL.share_post
    assert MLB.mult_n < WNBA.mult_n < NWSL.mult_n


def test_a_small_pre_draft_share_makes_the_inflation_large():
    """A 5% discount on 87% of NWSL's season has to be recovered across the
    remaining 13%."""
    assert NWSL.mult_n1 > WNBA.mult_n1 > MLB.mult_n1


def test_an_undiscounted_season_needs_no_inflation():
    rule = BisectionRule("Test", share_pre=0.5, share_post=0.5, mult_n=1.0)
    assert rule.mult_n1 == pytest.approx(1.0)


# --- construction and guards -----------------------------------------------

def test_a_rule_can_be_built_from_day_counts():
    rule = from_days("Test", pre_days=30, post_days=70, mult_n=0.9)
    assert rule.share_pre == pytest.approx(0.30)
    assert rule.share_post == pytest.approx(0.70)
    assert "30 season days" in rule.basis


def test_a_season_with_no_days_is_refused():
    with pytest.raises(ValueError, match="not a season"):
        from_days("Test", 0, 0, 0.9)


def test_shares_that_do_not_partition_a_season_are_flagged():
    """They split one season, so anything but 1 means one was measured wrong."""
    rule = BisectionRule("Test", share_pre=0.5, share_post=0.4, mult_n=0.8)
    assert any("sum to" in p for p in rule.validate())


def test_a_multiplier_outside_its_range_is_flagged():
    assert any("mult_n" in p for p in BisectionRule("T", 0.5, 0.5, 1.4).validate())
    assert any("mult_n" in p for p in BisectionRule("T", 0.5, 0.5, 0.0).validate())


def test_a_zero_pre_draft_share_is_flagged_before_it_divides():
    rule = BisectionRule("Test", share_pre=0.0, share_post=1.0, mult_n=0.9)
    assert any("divides" in p for p in rule.validate())


def test_every_shipped_rule_is_valid():
    for rule in RULES.values():
        assert rule.validate() == [], rule.league


# --- lookup ----------------------------------------------------------------

def test_an_unbisected_league_weights_both_stretches_equally():
    assert rule_for("NFL") is None
    assert weights("NFL") == (1.0, 1.0)


def test_mls_is_recorded_as_deliberately_unbisected():
    """A missing rule should read as a decision, not an oversight -- the 2027
    season it is drafted for is not bisected."""
    assert rule_for("MLS") is None
    assert "MLS" in NOT_BISECTED
    assert "2027" in NOT_BISECTED["MLS"]


def test_weights_come_back_as_a_pair():
    mult_n, mult_n1 = weights("MLB")
    assert mult_n == 0.75
    assert mult_n1 == pytest.approx(1.181, abs=0.001)


def test_the_rules_describe_themselves_for_a_report():
    lines = "\n".join(describe())
    for league in ("MLB", "WNBA", "NWSL", "MLS"):
        assert league in lines


def test_mlb_scoring_reads_the_shared_rule():
    """One source of truth: the scorer should not carry its own copy."""
    from whul.scoring import mlb

    assert mlb.MULT_YEAR_N == MLB.mult_n
    assert mlb.MULT_YEAR_N1 == MLB.mult_n1
    assert mlb.SHARE_PRE_ASB == MLB.share_pre
