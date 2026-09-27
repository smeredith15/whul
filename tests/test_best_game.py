"""The best-game slot's rules, as agreed, pinned before anything scores by them.

A proposal rather than a rule of the league: nothing in the standings reads
these. They are here so what was agreed is what the calibration measures.
"""

import pytest

from whul.scoring.best_game import (
    Appearance, Candidate, best_configuration, best_k, exchange, pitcher_best,
    primary_role, two_way_best, two_way_game,
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


# --- starts and relief appearances: traded ----------------------------------

STARTS = [5.0, 4.0, 1.0]
RELIEFS = [2.0, 1.9, 1.8, 1.7, 0.5]


def test_starts_and_appearances_trade_at_the_ratio_of_their_ks():
    """n=2 and m=4, so one start is two appearances. The choices are both
    starts (9.0), one start and two appearances (5.0 + 2.0 + 1.9 = 8.9), or
    four appearances (7.4)."""
    assert pitcher_best(STARTS, RELIEFS, n=2, m=4) == pytest.approx(9.0)


def test_a_swingman_whose_best_outings_are_split_is_credited_for_both():
    """One big start and a run of strong relief: neither half alone shows it."""
    starts, reliefs = [6.0], [2.5, 2.4, 2.3, 0.1]
    assert pitcher_best(starts, reliefs, n=2, m=4) == pytest.approx(6.0 + 2.5 + 2.4)


def test_trading_never_scores_below_the_rule_first_agreed():
    """Kept apart, a pitcher takes the better of the two alone. That is one of
    the combinations the trade considers, so it can only add."""
    for starts, reliefs in ((STARTS, RELIEFS), ([6.0], [2.5, 2.4, 2.3, 0.1]),
                            ([], RELIEFS), (STARTS, [])):
        apart = pitcher_best(starts, reliefs, n=2, m=4, mix=False)
        assert pitcher_best(starts, reliefs, n=2, m=4) >= apart - 1e-12


def test_the_room_left_is_counted_exactly():
    """Three starts of four spend 3/4 of the slot, which at m=13 leaves room
    for exactly 3.25 appearances -- three, not two because a float came to
    3.2499999."""
    starts = [10.0, 10.0, 10.0, -5.0]
    reliefs = [1.0] * 13
    # 3 starts + 3 appearances = 33.0, the best there is here.
    assert pitcher_best(starts, reliefs, n=4, m=13) == pytest.approx(33.0)


def test_three_roles_share_one_slot():
    """A two-way player can have batting games, starts and relief outings in
    one season, each role with its own k."""
    games = {"bat": [3.0, 2.0, 1.0, 1.0], "start": [6.0, 1.0], "relief": [2.0, 2.0]}
    k = {"bat": 4, "start": 2, "relief": 4}
    # One start (1/2) + two batting games (1/2): 6.0 + 3.0 + 2.0 = 11.0 beats
    # any one role alone (7.0, 7.0, 4.0) and the other mixes.
    assert exchange(games, k) == pytest.approx(11.0)


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


# --- a two-way player's slot -------------------------------------------------

K = {"bat": 10, "start": 4, "relief": 13}


def test_an_appearance_counts_as_the_role_that_led_it():
    """The night he threw six innings and homered twice counts as a batting
    game if the homers were worth more -- that role in full, the pitching at
    half, and the batting game's share of the slot."""
    assert primary_role(batting=6.0, pitching=4.0, pitched="start") == "bat"
    assert primary_role(batting=1.0, pitching=4.0, pitched="start") == "start"
    assert primary_role(batting=None, pitching=4.0, pitched="relief") == "relief"
    assert primary_role(batting=1.0, pitching=None) == "bat"


def test_a_tie_counts_as_the_role_that_costs_less_of_the_slot():
    """Either way it scores the same, so it is called the role with the larger
    k, which can never score less."""
    assert primary_role(2.0, 2.0, "start", K) == "bat"      # 10 > 4
    assert primary_role(2.0, 2.0, "relief", K) == "relief"  # 13 > 10


def test_a_two_way_slot_trades_across_every_role_he_played():
    """Two gems that led their games, and ten batting nights. One pitching
    game is 2.5 batting games at these k's."""
    games = ([Appearance(batting=0.5, pitching=9.0, started=True)] * 2
             + [Appearance(batting=2.0)] * 10)
    pitching_game = two_way_game(batting=0.5, pitching=9.0)   # 9.25
    # Both starts (1/2) and five batting games (1/2): 18.5 + 10.0 = 28.5.
    assert two_way_best(games, K) == pytest.approx(2 * pitching_game + 5 * 2.0)


def test_a_pitching_night_his_bat_won_is_a_batting_game():
    """Three homers and a rough start: the batting led, so the whole game --
    batting in full, the start at half -- is spent as a batting game, and the
    four starts' worth of room stays free for real starts."""
    games = [Appearance(batting=5.0, pitching=1.0, started=True)]
    assert two_way_best(games, K) == pytest.approx(5.0 + 0.5 * 1.0)
    role = primary_role(5.0, 1.0, "start", K)
    assert role == "bat"


def test_a_season_he_never_pitched_is_a_batters_slot():
    games = [Appearance(batting=b) for b in (3.0, 2.0, 1.0)]
    assert two_way_best(games, {"bat": 2, "start": 4, "relief": 13}) == pytest.approx(5.0)
