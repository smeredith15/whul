"""Placing a soccer match before it can be priced.

The classifier decides two things at once now: what a win in this competition
is worth to a club, and whether a player's appearance in it belongs in the
benchmark. One reading, not two.
"""

from whul.scoring.competition import WIN_POINTS, Tier, classify, classify_key

# --- MLS plays two competitions the European leagues do not ----------------

def test_the_concacaf_competition_is_not_uefas():
    """It was called the Champions *League* until 2024 and feeds still say so.
    Read as UEFA's, a Seattle continental night would pay the Champions
    League's premium."""
    for name in ("CONCACAF Champions Cup", "Concacaf Champions League",
                 "CONCACAF Champions League"):
        found = classify(name)
        assert found.tier is Tier.CONTINENTAL_CUP, name
        assert found.win_points == WIN_POINTS[Tier.CONTINENTAL_CUP]


def test_uefas_champions_league_is_still_uefas():
    for name in ("UEFA Champions League", "Champions League"):
        assert classify(name).tier is Tier.CHAMPIONS_LEAGUE, name


def test_the_continental_cup_is_keyed_as_well_as_named():
    assert classify_key("concacafchampions").tier is Tier.CONTINENTAL_CUP


def test_an_mls_playoff_tie_outranks_its_own_regular_season():
    """The key says league and the round says playoffs; the round wins."""
    found = classify_key("mls", "MLS Cup Playoffs - Round One")
    assert found.tier is Tier.DOMESTIC_POSTSEASON
    assert found.win_points == WIN_POINTS[Tier.DOMESTIC_POSTSEASON]


def test_the_us_open_cup_is_a_domestic_cup():
    assert classify_key("usopencup").tier is Tier.DOMESTIC_CUP
