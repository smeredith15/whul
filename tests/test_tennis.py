"""Tennis scoring tests.

The points tables are the ATP ranking-points tables; each tier's column is
checked against the event's face value, which is the arithmetic that says the
table is transcribed correctly.
"""

import pandas as pd

from whul.normalize import assign_norm_key
from whul.scoring.tennis import (
    ATP_WIN_POINTS,
    F,
    GRAND_SLAM,
    INTERNATIONAL,
    INTERNATIONAL_WIN_POINTS,
    MASTERS_1000,
    QF,
    R16,
    R32,
    R64,
    R128,
    RR,
    SF,
    TIER_ROUNDS,
    TOUR_250,
    TOUR_500,
    TOUR_FINALS,
    best_of_for,
    bye_bonus,
    effective_bracket,
    is_complete_set,
    is_straight_sets,
    is_walkover,
    normalize_round,
    parse_sets,
    previous_round,
    round_points,
    score_matches,
    straight_sets_multiplier,
    score_players,
)

FACE_VALUE = {
    "GS": 2000, "M1000_128": 1000, "M1000_64": 1000,
    "A500_32": 500, "A500_64": 500, "A250_32": 250, "A250_64": 250,
}


def match(winner="Jannik Sinner", loser="Carlos Alcaraz", round_name=F,
          tournament="Wimbledon", category=GRAND_SLAM, draw_size=128,
          season=2027, score="6-3 6-4 6-4", tour="ATP"):
    return {"winner": winner, "loser": loser, "round": round_name,
            "tournament": tournament, "category": category,
            "draw_size": draw_size, "season": season, "score": score,
            "tour": tour}


# --- the points tables -----------------------------------------------------

def test_each_tier_pays_out_its_face_value():
    """Winning every round of an event is worth exactly what the event is
    worth: 2000 at a slam, 1000 at a Masters, 500, 250. If a column does not
    sum to its face value a number was transcribed wrong."""
    for tier, face in FACE_VALUE.items():
        total = sum(ATP_WIN_POINTS[(tier, r)] for r in TIER_ROUNDS[tier])
        assert total == face, f"{tier} sums to {total}, not {face}"


def test_a_seed_and_an_unseeded_champion_reach_the_same_total():
    """In a 96-draw the seeds skip the opening round. Their first win pays the
    round plus the bye credit, which puts both paths on 1000."""
    unseeded = sum(ATP_WIN_POINTS[("M1000_128", r)] for r in TIER_ROUNDS["M1000_128"])
    seeded = (
        ATP_WIN_POINTS[("M1000_128", R64)] + ATP_WIN_POINTS[("M1000_128", R128)]
        + sum(ATP_WIN_POINTS[("M1000_128", r)]
              for r in TIER_ROUNDS["M1000_128"] if r not in (R128, R64))
    )
    assert unseeded == seeded == 1000


def test_the_tour_finals_pay_per_round_robin_win():
    assert round_points("FINALS", RR) == 200
    assert round_points("FINALS", SF) == 400
    assert round_points("FINALS", F) == 500


def test_team_events_pay_a_flat_rate_per_win():
    for round_name in (R32, QF, F, RR):
        assert round_points("INTERNATIONAL", round_name) == INTERNATIONAL_WIN_POINTS


def test_a_round_a_tier_does_not_play_scores_nothing():
    assert round_points("A250_32", R128) == 0.0


# --- brackets and tiers ----------------------------------------------------

def test_draws_round_up_to_the_next_power_of_two():
    """Tour draws are 28, 32, 48, 56, 96, 128 -- rarely a power of two. The
    bracket is the next one up and the difference is made up by byes."""
    assert effective_bracket(32) == 32
    assert effective_bracket(48) == 64
    assert effective_bracket(56) == 64
    assert effective_bracket(96) == 128
    assert effective_bracket(128) == 128


def test_an_unknown_draw_falls_to_the_middle_bracket():
    assert effective_bracket(None) == 64
    assert effective_bracket(float("nan")) == 64


def test_the_two_masters_shapes_land_on_different_tables():
    """This is what the old match-count heuristic got wrong: a 96-draw and a
    56-draw are both Masters 1000s and pay differently in the early rounds."""
    from whul.scoring.tennis import scoring_tier

    assert scoring_tier(MASTERS_1000, 96) == "M1000_128"
    assert scoring_tier(MASTERS_1000, 56) == "M1000_64"
    assert round_points("M1000_128", R64) == 20
    assert round_points("M1000_64", R64) == 50


def test_the_two_tour_shapes_land_on_different_tables():
    from whul.scoring.tennis import scoring_tier

    assert scoring_tier(TOUR_500, 32) == "A500_32"
    assert scoring_tier(TOUR_500, 48) == "A500_64"
    assert scoring_tier(TOUR_250, 32) == "A250_32"
    assert scoring_tier(TOUR_250, 48) == "A250_64"


def test_slams_and_finals_ignore_the_draw_size():
    from whul.scoring.tennis import scoring_tier

    assert scoring_tier(GRAND_SLAM, 128) == "GS"
    assert scoring_tier(GRAND_SLAM, None) == "GS"
    assert scoring_tier(TOUR_FINALS, 8) == "FINALS"
    assert scoring_tier(INTERNATIONAL, None) == "INTERNATIONAL"


def test_an_unknown_masters_draw_assumes_the_common_shape():
    """Most 1000s have been 96-draw since the 2024 reform."""
    from whul.scoring.tennis import scoring_tier

    assert scoring_tier(MASTERS_1000, None) == "M1000_128"


# --- rounds ----------------------------------------------------------------

def test_the_winner_label_is_the_final():
    """Some feeds label the title match W. It pays the same as F and folds
    into it, so a champion is never credited twice for one match."""
    assert normalize_round("W") == F
    assert normalize_round("F") == F


def test_an_unreadable_round_scores_nothing_rather_than_guessing():
    """The R script defaulted to R32. A round that cannot be traced to a
    bracket position should not be paid at all."""
    assert normalize_round("Consolation Bracket") == ""
    assert normalize_round("") == ""
    assert normalize_round(None) == ""


def test_qualifying_rounds_are_recognized_so_they_can_be_dropped():
    for label in ("Q1", "Q2", "Q3"):
        assert normalize_round(label) == label


# --- byes ------------------------------------------------------------------

def test_a_bye_is_the_absence_of_a_result_in_the_preceding_round():
    """The ATP rule: no result in round N means the player was not in it, so
    round N's points come with their round N+1 win."""
    assert bye_bonus("M1000_128", R64, rounds_played={R64}) == 30
    assert bye_bonus("M1000_128", R64, rounds_played={R128, R64}) == 0.0


def test_the_opening_round_never_carries_a_bye():
    assert previous_round("GS", R128) == ""
    assert bye_bonus("GS", R128, rounds_played={R128}) == 0.0


def test_a_bye_is_credited_once_not_at_every_later_round():
    """Only the round immediately after the bye is affected; a player who won
    R64 has a result there, so their R32 win carries no credit."""
    assert bye_bonus("M1000_128", R32, rounds_played={R64, R32}) == 0.0


def test_tiers_without_a_bracket_never_pay_a_bye():
    assert bye_bonus("INTERNATIONAL", QF, rounds_played=set()) == 0.0
    assert bye_bonus("FINALS", RR, rounds_played=set()) == 0.0


def test_a_structural_bye_pays_from_the_match_data(): 
    """A seed entering a 96-draw at R64 has no R128 row, which is the whole
    signal -- nothing has to mark them as seeded."""
    matches = pd.DataFrame([
        match(winner="Seed", round_name=R64, tournament="Miami",
              category=MASTERS_1000, draw_size=96, score="6-4 6-4"),
    ])
    row = score_matches(matches).iloc[0]
    assert row["tier"] == "M1000_128"
    assert row["base_points"] == 20
    assert row["bye_points"] == 30
    assert row["win_points"] == 50


def test_a_player_who_played_the_opening_round_gets_no_credit():
    matches = pd.DataFrame([
        match(winner="Unseeded", round_name=R128, tournament="Miami",
              category=MASTERS_1000, draw_size=96, score="6-4 6-4"),
        match(winner="Unseeded", round_name=R64, tournament="Miami",
              category=MASTERS_1000, draw_size=96, score="6-4 6-4"),
    ])
    scored = score_matches(matches).set_index("round")
    assert scored.loc[R128, "win_points"] == 30
    assert scored.loc[R64, "win_points"] == 20


def test_the_bye_test_does_not_depend_on_row_order():
    """Unlike a 'first win' rule, this reads the whole tournament at once."""
    matches = pd.DataFrame([
        match(winner="Unseeded", round_name=R64, tournament="Miami",
              category=MASTERS_1000, draw_size=96, score="6-4 6-4"),
        match(winner="Unseeded", round_name=R128, tournament="Miami",
              category=MASTERS_1000, draw_size=96, score="6-4 6-4"),
    ])
    scored = score_matches(matches).set_index("round")
    assert scored.loc[R64, "bye_points"] == 0.0


# --- straight sets ---------------------------------------------------------

def test_set_scores_parse_through_tiebreak_notation():
    assert parse_sets("6-3 7-6(4)") == [(6, 3), (7, 6)]
    assert parse_sets("") == []
    assert parse_sets(None) == []


def test_a_straight_sets_win_at_a_slam_is_worth_half_again_as_much():
    """An ATP slam is best-of-five, so straight sets skipped two."""
    matches = pd.DataFrame([match(round_name=R128, score="6-3 7-6(4) 6-4")])
    assert score_matches(matches).iloc[0]["match_points"] == 50 * 1.5


def test_a_straight_sets_win_at_best_of_three_is_worth_a_quarter_more():
    """One set skipped rather than two, so it pays less."""
    matches = pd.DataFrame([match(
        round_name=R32, tournament="Basel", category=TOUR_500, draw_size=32,
        score="6-3 6-4",
    )])
    assert score_matches(matches).iloc[0]["match_points"] == 50 * 1.25


def test_the_format_comes_from_the_tier_when_the_feed_omits_it():
    """Only an ATP main-draw slam match plays five; the WTA plays three
    everywhere, including at slams."""
    assert best_of_for(GRAND_SLAM, "ATP") == 5
    assert best_of_for(GRAND_SLAM, "WTA Tour") == 3
    assert best_of_for(TOUR_500, "ATP") == 3
    assert best_of_for(TOUR_FINALS, "ATP") == 3


def test_a_feed_that_states_the_format_is_believed():
    """The historical snapshot carries best_of per match."""
    rows = pd.DataFrame([match(round_name=R128, score="6-3 6-4") | {"best_of": 3}])
    assert score_matches(rows).iloc[0]["match_points"] == 50 * 1.25


def test_an_unknown_format_falls_back_to_best_of_three():
    """The commoner format, and the smaller bonus -- guessing high would pay
    for sets that were never scheduled."""
    assert straight_sets_multiplier(None) == 1.25
    assert straight_sets_multiplier(float("nan")) == 1.25


def test_a_retirement_during_the_first_set_earns_no_bonus():
    """Nothing was really won: the loser stopped before a set was completed."""
    assert is_straight_sets("3-1 RET") is False
    assert is_straight_sets("2-0 RET") is False


def test_a_retirement_after_a_completed_set_still_earns_it():
    """'6-3 RET' finished a set before the loser stopped; '6-3 3-1 RET'
    finished one and led the next."""
    assert is_straight_sets("6-3 RET") is True
    assert is_straight_sets("6-3 3-1 RET") is True


def test_a_retirement_after_dropping_a_set_earns_nothing():
    assert is_straight_sets("2-6 1-0 RET") is False


def test_a_completed_set_is_told_from_one_in_progress():
    """This is what separates a first-set retirement from a later one."""
    assert is_complete_set((6, 3)) is True
    assert is_complete_set((7, 5)) is True
    assert is_complete_set((7, 6)) is True
    assert is_complete_set((3, 1)) is False
    assert is_complete_set((5, 4)) is False


def test_the_bonus_is_recorded_on_the_match(): 
    """So a score can be explained without recomputing it."""
    matches = pd.DataFrame([match(round_name=R128, score="6-3 6-4 6-2")])
    assert score_matches(matches).iloc[0]["straight_sets_bonus"] == 1.5


def test_dropping_a_set_pays_the_base_rate():
    matches = pd.DataFrame([match(round_name=R128, score="6-3 4-6 6-2 6-4")])
    assert score_matches(matches).iloc[0]["match_points"] == 50


def test_a_walkover_is_never_a_straight_sets_win():
    """There is nothing to be straight about -- no set was played."""
    assert is_walkover("W/O") is True
    assert is_straight_sets("W/O") is False


def test_a_retirement_counts_only_if_no_set_had_been_dropped():
    """'6-3 3-1 RET' is a straight-sets win in progress; '6-3 4-6 1-0 RET'
    is not, because a set was already gone."""
    assert is_straight_sets("6-3 3-1 RET") is True
    assert is_straight_sets("6-3 4-6 1-0 RET") is False


def test_a_missing_score_is_not_treated_as_straight_sets():
    assert is_straight_sets("") is False
    assert is_straight_sets(None) is False


# --- match and season scoring ----------------------------------------------

def test_qualifying_matches_never_score():
    matches = pd.DataFrame([match(round_name="Q3", score="6-1 6-1")])
    assert score_matches(matches).empty


def test_an_unreadable_round_is_dropped_rather_than_scored():
    matches = pd.DataFrame([match(round_name="Consolation", score="6-1 6-1")])
    assert score_matches(matches).empty


def test_a_dropped_qualifying_result_cannot_be_mistaken_for_a_bye():
    """Qualifying is removed before the bye test runs. If a Q3 win stayed in
    the player's round set it could suppress a legitimate credit, and if it
    were treated as a main-draw round it could create a false one."""
    matches = pd.DataFrame([
        match(winner="Qualifier", round_name="Q3", tournament="Miami",
              category=MASTERS_1000, draw_size=96, score="6-1 6-1"),
        match(winner="Qualifier", round_name=R128, tournament="Miami",
              category=MASTERS_1000, draw_size=96, score="6-4 6-4"),
    ])
    scored = score_matches(matches)
    assert len(scored) == 1
    assert scored.iloc[0]["bye_points"] == 0.0


def test_season_total_sums_wins():
    matches = pd.DataFrame([
        match(winner="Champion", round_name=QF, score="6-3 4-6 6-2"),
        match(winner="Champion", round_name=SF, score="6-3 4-6 6-2"),
        match(winner="Champion", round_name=F, score="6-3 4-6 6-2"),
    ])
    row = score_players(matches).iloc[0]
    # The quarterfinal is the earliest round they appear in, so it carries the
    # round-of-16 credit: 400 + 200, then 500 and 700.
    assert row["total_points"] == (400 + 200) + 500 + 700
    assert row["matches_won"] == 3
    assert row["titles"] == 1


def test_each_tour_is_measured_against_its_own_history():
    """The tours pay the identical table by round, so the raw scores are
    comparable -- but the fields are not, and each is normalized against
    itself."""
    matches = pd.DataFrame([
        match(winner="Man", tour="ATP", round_name=R128),
        match(winner="Woman", tour="WTA Tour", tournament="US Open", round_name=R128),
    ])
    totals = score_players(matches)
    assert set(totals["league"]) == {"ATP", "WTA"}
    assert set(assign_norm_key(totals, "Player")) == {"ATP", "WTA"}


def test_empty_input_is_empty_output():
    assert score_players(pd.DataFrame()).empty
    assert score_matches(pd.DataFrame()).empty
