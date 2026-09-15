"""Placing a soccer match before it can be priced.

The classifier decides two things at once now: what a win in this competition
is worth to a club, and whether a player's appearance in it belongs in the
benchmark. One reading, not two.
"""

import pytest

from whul.scoring.competition import (
    WIN_POINTS, Tier, classify, classify_key, european_phase,
)

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


# --- UEFA's phases, from the labels the feed actually returns ---------------

#: Verbatim from `probe-rounds epl --seasons 2025`, which read 1,062 European
#: rows across the three UEFA competitions. Kept as the feed wrote them,
#: aggregate scorelines and all, because the wording is the whole question: the
#: patterns were a guess until this ran, and every one of these is the shape a
#: guess has to survive.
REAL_LABELS = [
    ("League phase", "UEFA Champions League league phase"),
    ("League phase", "UEFA Europa League league phase"),
    ("League phase", "UEFA Conference League league phase"),
    ("Knockout", "UEFA Champions League 1st Leg round of 16"),
    ("Knockout", "UEFA Champions League 1st Leg quarterfinals"),
    ("Knockout", "UEFA Champions League 1st Leg semifinals"),
    ("Knockout", "UEFA Champions League final"),
    # The one the first pattern missed, ninety-six ties a season: the feed says
    # "knockout round playoffs" and the copy that was meant to catch it had
    # dropped that alternative.
    ("Knockout", "UEFA Champions League 1st Leg knockout round playoffs"),
    ("Knockout",
     "UEFA Europa League 2nd Leg - AS Roma advance 4-3 on aggregate "
     "knockout round playoffs"),
    ("Knockout",
     "UEFA Conference League 2nd Leg - Tied on aggregate - Molde advance "
     "5-4 on penalties knockout round playoffs"),
    ("Knockout",
     "UEFA Champions League 2nd Leg - Arsenal advance 9-3 on aggregate "
     "round of 16"),
    ("Knockout",
     "UEFA Europa League 2nd Leg - Tottenham Hotspur advance 2-1 on "
     "aggregate quarterfinals"),
    ("Knockout",
     "UEFA Champions League 2nd Leg - Internazionale advance 7-6 on "
     "aggregate semifinals"),
]


@pytest.mark.parametrize("expected,label", REAL_LABELS)
def test_a_real_uefa_label_lands_in_the_phase_it_belongs_to(expected, label):
    assert european_phase(label) == expected


def test_a_label_with_no_round_in_it_is_left_unplaced():
    """The safe answer, and a real one: it collapses the competition to a
    single block rather than inventing a division the feed did not describe."""
    assert european_phase("UEFA Champions League") == ""
    assert european_phase("") == ""


def test_the_league_phase_is_not_matched_by_the_competitions_own_name():
    """"Champions League" contains the word and is not a phase."""
    assert european_phase("UEFA Champions League 2024 25") == ""
