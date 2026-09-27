"""The best-game slot's rules, as agreed, pinned before anything scores by them.

A proposal rather than a rule of the league: nothing in the standings reads
these. They are here so what was agreed is what the calibration measures.
"""

import pytest

from whul.scoring.best_game import (
    Candidate, best_configuration, best_k, pitcher_best, two_way_game,
)


# --- best k -------------------------------------------------------------------

def test_best_k_is_the_sum_of_the_k_best_games():
    assert best_k([3.0, 9.0, 1.0, 7.0], 2) == 16.0


def test_a_player_with_fewer_games_than_k_has_what_he_played():
    """Hurt in week two: his two weeks, and no more."""
    assert best_k([5.0, 2.0], 3) == 7.0
    assert best_k([], 3) == 0.0


# --- two-way: 1x / 0.5x, decided within the game ------------------------------

def test_the_role_that_led_the_game_counts_in_full_and_the_other_at_half():
    assert two_way_game(batting=2.0, pitching=4.0) == pytest.approx(4.0 + 0.5 * 2.0)
    assert two_way_game(batting=4.0, pitching=2.0) == pytest.approx(4.0 + 0.5 * 2.0)


def test_the_split_is_decided_by_the_game_not_the_season():
    """A two-way player whose season is mostly batting still leads with the
    pitching on the night he threw eight shutout innings and went 0-for-3."""
    assert two_way_game(batting=-0.2, pitching=5.0) == pytest.approx(5.0 - 0.1)


def test_a_role_he_did_not_play_is_absent_not_zero():
    """A zero would be a role he played and did nothing in. On a day he never
    pitched, an 0-for-4 is an 0-for-4, not half of one."""
    assert two_way_game(batting=-0.3) == pytest.approx(-0.3)
    assert two_way_game(pitching=3.0) == pytest.approx(3.0)
    assert two_way_game(batting=-0.3, pitching=0.0) == pytest.approx(0.0 - 0.15)


# --- starts and relief appearances --------------------------------------------

STARTS = [5.0, 4.0, 1.0]
RELIEFS = [2.0, 1.9, 1.8, 1.7, 0.5]


def test_starts_and_relief_are_never_mixed_under_the_agreed_rule():
    # best 2 starts = 9.0; best 4 reliefs = 7.4; the larger, not a blend.
    assert pitcher_best(STARTS, RELIEFS, n=2, m=4) == pytest.approx(9.0)
    assert pitcher_best([2.0], RELIEFS, n=2, m=4) == pytest.approx(7.4)


def test_the_mix_is_an_exchange_rate_and_never_scores_below_the_rule():
    """n and m are calibrated to be worth the same, so one start is m/n relief
    appearances. With n=2 and m=4 the choices are both starts (9.0), one start
    and two appearances (5.0 + 2.0 + 1.9 = 8.9), or four appearances (7.4).
    The agreed rule's answer is one of them, so the mix is never lower."""
    mixed = pitcher_best(STARTS, RELIEFS, n=2, m=4, mix=True)
    assert mixed >= pitcher_best(STARTS, RELIEFS, n=2, m=4)
    assert mixed == pytest.approx(9.0)


def test_the_mix_credits_a_swingman_whose_best_outings_are_split():
    """One big start and a run of strong relief: neither half alone shows it."""
    starts, reliefs = [6.0], [2.5, 2.4, 2.3, 0.1]
    segregated = pitcher_best(starts, reliefs, n=2, m=4)       # max(6.0, 7.3)
    mixed = pitcher_best(starts, reliefs, n=2, m=4, mix=True)  # 6.0 + 2.5 + 2.4
    assert segregated == pytest.approx(7.3)
    assert mixed == pytest.approx(10.9)


# --- the highest-scoring configuration ----------------------------------------

def test_the_example_as_given_finds_its_own_best_configuration():
    """The example put on record, with its numbers as given. Moving NFL 2 to
    the best-game slot (55) frees a season slot, and the best season left is
    NFL 4's 60 -- so the highest-scoring configuration is 195, one step past
    the 190 the example stopped at. NFL 3 is benched."""
    roster = [Candidate("NFL 1", 80, 40), Candidate("NFL 2", 60, 55),
              Candidate("NFL 3", 55, 45), Candidate("NFL 4", 60, 15)]
    found = best_configuration(roster, season_slots=2)

    assert found.total == 195
    assert found.best == "NFL 2"
    assert set(found.season) == {"NFL 1", "NFL 4"}


def test_a_lower_season_is_seated_when_that_scores_more():
    """The case the example was put on record for: plain best-ball seats the
    two best seasons (80 + 60) and hands the slot to 45, for 185; seating the
    55 instead frees the 60-point season's 55-point games, for 190."""
    roster = [Candidate("NFL 1", 80, 40), Candidate("NFL 2", 60, 55),
              Candidate("NFL 3", 55, 45), Candidate("NFL 4", 50, 15)]
    found = best_configuration(roster, season_slots=2)

    assert found.total == 190
    assert found.best == "NFL 2"
    assert set(found.season) == {"NFL 1", "NFL 3"}


def test_plain_best_ball_wins_when_it_is_already_the_best():
    roster = [Candidate("A", 80, 10), Candidate("B", 70, 10), Candidate("C", 20, 30)]
    found = best_configuration(roster, season_slots=2)
    assert (set(found.season), found.best, found.total) == ({"A", "B"}, "C", 180)


def test_a_best_game_below_zero_is_never_forced_into_the_total():
    roster = [Candidate("A", 80, 10), Candidate("B", 70, 10), Candidate("C", 20, -2)]
    found = best_configuration(roster, season_slots=2)
    # C's -2 would cost points and A or B moving over would cost more; the
    # slot can stay empty.
    assert found.total == 150 and found.best is None


def test_a_tie_goes_to_what_plain_best_ball_would_have_chosen():
    """Equal totals, so the configuration with more in its season slots --
    the one nobody has to have explained to them."""
    roster = [Candidate("A", 80, 0), Candidate("B", 60, 50), Candidate("C", 40, 30)]
    # Seat A+C and use B's games: 120 + 50 = 170. Seat A+B and use C's: 140 +
    # 30 = 170. The second keeps the two best seasons in the season slots.
    found = best_configuration(roster, season_slots=2)
    assert found.total == 170
    assert set(found.season) == {"A", "B"} and found.best == "C"
