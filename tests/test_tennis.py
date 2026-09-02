"""Tennis scoring tests.

Expected values are read off the ATP points map in Tennis_Players.R.
"""

import pandas as pd

from whul.normalize import assign_norm_key
from whul.scoring.tennis import (
    ATP_POINTS,
    INTERNATIONAL_WIN_POINTS,
    MASTERS_LARGE_DRAW_MATCHES,
    STRAIGHT_SETS_MULTIPLIER,
    TIER_ROUNDS,
    TOUR_LARGE_DRAW_MATCHES,
    bye_bonus,
    classify_tier,
    eligible_matches,
    normalize_round,
    previous_round,
    round_points,
    score_matches,
    score_players,
)


def game(home="Jannik Sinner", away="Carlos Alcaraz", winner_code=1,
         tournament="Wimbledon", round_name="Final", season=2027,
         home_sets=3, away_sets=0, timestamp=1, tour="ATP Tour",
         status="FINISHED", status_extra=""):
    return {
        "home_name": home, "away_name": away, "winner_code": winner_code,
        "tournament": tournament, "round": round_name, "season_year": season,
        "home_set_score": home_sets, "away_set_score": away_sets,
        "date_timestamp": timestamp, "tour_type_human": tour,
        "status": status, "status_extra": status_extra,
    }


def tournament(name, matches, round_name="Round of 64", **target):
    """A tournament padded to ``matches`` rows, so the draw-size cutoffs fire.

    The tier is inferred from how many matches an event played, which means a
    scoring test has to supply a plausible field around the match it cares about.
    """
    filler = [
        game(home=f"Filler {i}A", away=f"Filler {i}B", tournament=name,
             round_name=round_name, timestamp=i, home_sets=2, away_sets=1)
        for i in range(matches - 1)
    ]
    return pd.DataFrame(filler + [game(tournament=name, round_name=round_name, **target)])


# --- tier classification ---------------------------------------------------

def test_the_four_slams_are_recognized():
    for name in ("Australian Open", "Roland Garros", "Wimbledon", "US Open"):
        assert classify_tier(name) == "GS", name


def test_draw_size_decides_which_masters_table_applies():
    """The same round is one win deeper into a larger field, so it pays
    differently -- the tier carries the draw, not just the tournament's status."""
    assert classify_tier("Miami Open", MASTERS_LARGE_DRAW_MATCHES) == "M1000_128"
    assert classify_tier("Miami Open", MASTERS_LARGE_DRAW_MATCHES - 1) == "M1000_64"


def test_draw_size_decides_the_tour_tables_too():
    assert classify_tier("Halle Open", TOUR_LARGE_DRAW_MATCHES) == "A500_64"
    assert classify_tier("Halle Open", TOUR_LARGE_DRAW_MATCHES - 1) == "A500_32"
    assert classify_tier("Brisbane International", TOUR_LARGE_DRAW_MATCHES) == "A250_64"
    assert classify_tier("Brisbane International", TOUR_LARGE_DRAW_MATCHES - 1) == "A250_32"


def test_the_cutoffs_sit_below_the_real_draw_boundaries():
    """Ported from Tennis_Players.R as written. A 56-draw Masters plays 55
    matches and still lands on the 128 table, and a 32-draw 500 plays 31 and
    lands on the 64 table -- so the smaller tables are unreachable on complete
    tour data. Recorded here because it is deliberate, not overlooked: both the
    benchmark and the live season run through this function, so the 0-100 scale
    absorbs it, and moving the cutoffs would restate every historical score."""
    assert classify_tier("Monte Carlo Masters", 55) == "M1000_128"
    assert classify_tier("Dubai Duty Free Championships", 31) == "A500_64"


def test_team_events_and_the_tour_finals_are_their_own_tiers():
    assert classify_tier("United Cup") == "INTERNATIONAL"
    assert classify_tier("Davis Cup Finals") == "INTERNATIONAL"
    assert classify_tier("ATP Finals") == "FINALS"


def test_slams_outrank_a_name_that_also_reads_as_a_masters():
    """'US Open' would match neither Masters nor 500 patterns, but 'Paris'
    appears in both the Masters list and Roland Garros coverage -- the slam
    check runs first so the order is what decides."""
    assert classify_tier("Roland Garros", 127) == "GS"


def test_an_unrecognized_event_falls_to_the_lowest_tier():
    """A new or renamed tournament that matches nothing scores as a 250 rather
    than being dropped."""
    assert classify_tier("Some New Event", 31) == "A250_64"
    assert classify_tier("Some New Event", 27) == "A250_32"


# --- round normalization ---------------------------------------------------

def test_round_labels_from_different_feeds_land_on_one_name():
    assert normalize_round("1/16") == "R32"
    assert normalize_round("Round of 32") == "R32"
    assert normalize_round("R32") == "R32"


def test_every_round_label_is_recognized():
    assert normalize_round("Round of 128") == "R128"
    assert normalize_round("Round of 64") == "R64"
    assert normalize_round("Round of 16") == "R16"
    assert normalize_round("Quarterfinal") == "QF"
    assert normalize_round("Semifinal") == "SF"
    assert normalize_round("Final") == "F"
    assert normalize_round("Group Stage") == "RR"


def test_an_unreadable_round_defaults_to_the_middle_of_the_draw():
    assert normalize_round("") == "R32"
    assert normalize_round(None) == "R32"


# --- the points table ------------------------------------------------------

def test_slam_points_climb_to_seven_hundred_for_the_title():
    assert round_points("GS", "R128") == 50
    assert round_points("GS", "QF") == 400
    assert round_points("GS", "F") == 700


def test_the_smaller_masters_draw_pays_more_for_its_opening_round():
    """In a 64-draw Masters the R64 winner has beaten a seeded field; in a
    128-draw the R64 winner is only two wins in."""
    assert round_points("M1000_64", "R64") == 50
    assert round_points("M1000_128", "R64") == 20


def test_team_events_pay_a_flat_rate_per_win():
    for round_clean in ("R32", "QF", "F", "RR"):
        assert round_points("INTERNATIONAL", round_clean) == INTERNATIONAL_WIN_POINTS


def test_a_round_a_tier_does_not_play_scores_nothing():
    assert round_points("A250_32", "R128") == 0.0


def test_every_tier_sequence_has_points_for_each_of_its_rounds():
    for tier, rounds in TIER_ROUNDS.items():
        for round_clean in rounds:
            assert f"{tier}_{round_clean}" in ATP_POINTS, f"{tier}_{round_clean}"


# --- byes ------------------------------------------------------------------

def test_the_opening_round_has_no_earlier_round_to_credit():
    assert previous_round("GS", "R128") == ""
    assert bye_bonus("GS", "R128") == 0.0


def test_a_bye_pays_the_round_it_covered():
    assert previous_round("M1000_128", "R64") == "R128"
    assert bye_bonus("M1000_128", "R64") == 30


def test_tiers_without_a_knockout_sequence_never_pay_a_bye():
    """Team events and the round-robin Tour Finals have no draw to receive a
    bye in, so the credit must not fire there."""
    assert bye_bonus("INTERNATIONAL", "QF") == 0.0
    assert bye_bonus("FINALS", "SF") == 0.0


def test_the_bye_is_paid_on_the_first_win_not_on_the_bye_itself():
    """League convention: a seed who receives a first-round bye earns that
    round's points only by winning their opening match."""
    matches = tournament("Miami Open", MASTERS_LARGE_DRAW_MATCHES,
                         round_name="Round of 64", home="Seed", away="Qualifier",
                         home_sets=2, away_sets=1, timestamp=99)
    row = score_matches(matches).set_index("winner").loc["Seed"]
    assert row["tier"] == "M1000_128"
    assert row["base_points"] == 20
    assert row["bye_points"] == 30
    assert row["win_points"] == 50


def test_a_player_who_entered_in_the_first_round_gets_no_bye_credit():
    """Their first win is in R128, which has no earlier round -- so the same
    rule that pays the seed pays them nothing extra."""
    matches = tournament("Miami Open", MASTERS_LARGE_DRAW_MATCHES,
                         round_name="Round of 128", home="Unseeded", away="Wildcard",
                         home_sets=2, away_sets=1, timestamp=99)
    row = score_matches(matches).set_index("winner").loc["Unseeded"]
    assert row["bye_points"] == 0.0
    assert row["win_points"] == 30


def test_a_second_win_in_the_same_event_carries_no_bye_credit():
    """Only the first win can have been preceded by a bye; crediting later
    rounds would pay the previous round twice."""
    matches = pd.concat([
        tournament("Miami Open", MASTERS_LARGE_DRAW_MATCHES, round_name="Round of 128",
                   home="Unseeded", away="Wildcard", home_sets=2, away_sets=1, timestamp=98),
        pd.DataFrame([game(home="Unseeded", away="Seed", tournament="Miami Open",
                           round_name="Round of 64", home_sets=2, away_sets=1, timestamp=99)]),
    ], ignore_index=True)
    scored = score_matches(matches)
    scored = scored[scored["winner"] == "Unseeded"].sort_values("timestamp")
    assert list(scored["bye_points"]) == [0.0, 0.0]
    assert list(scored["win_points"]) == [30.0, 20.0]


def test_the_bye_credit_follows_time_order_not_row_order():
    later = pd.DataFrame([game(home="Unseeded", away="Seed", tournament="Miami Open",
                               round_name="Round of 64", home_sets=2, away_sets=1, timestamp=99)])
    earlier = tournament("Miami Open", MASTERS_LARGE_DRAW_MATCHES, round_name="Round of 128",
                         home="Unseeded", away="Wildcard", home_sets=2, away_sets=1, timestamp=98)
    scored = score_matches(pd.concat([later, earlier], ignore_index=True))
    scored = scored[scored["winner"] == "Unseeded"].set_index("round_clean")
    assert scored.loc["R128", "bye_points"] == 0.0
    assert scored.loc["R64", "bye_points"] == 0.0


# --- straight sets ---------------------------------------------------------

def test_a_straight_sets_win_is_worth_half_again_as_much():
    matches = pd.DataFrame([game(round_name="Round of 128", home_sets=3, away_sets=0)])
    assert score_matches(matches).iloc[0]["match_points"] == 50 * STRAIGHT_SETS_MULTIPLIER


def test_a_win_after_dropping_a_set_pays_the_base_rate():
    matches = pd.DataFrame([game(round_name="Round of 128", home_sets=3, away_sets=1)])
    assert score_matches(matches).iloc[0]["match_points"] == 50


def test_a_walkover_with_no_sets_played_is_not_a_straight_sets_win():
    """0-0 in sets means nothing was played; treating it as straight sets
    would pay a 1.5x bonus for a match that never happened."""
    matches = pd.DataFrame([game(round_name="Round of 128", home_sets=0, away_sets=0)])
    assert bool(score_matches(matches).iloc[0]["is_straight_sets"]) is False


# --- the winner ------------------------------------------------------------

def test_the_away_player_wins_when_the_code_says_so():
    matches = pd.DataFrame([
        game(home="Loser", away="Winner", winner_code=2, home_sets=1, away_sets=3),
    ])
    row = score_matches(matches).iloc[0]
    assert row["winner"] == "Winner"
    assert row["loser"] == "Loser"
    assert row["winner_sets"] == 3


def test_only_wins_score():
    """One row per match produces one scoring row -- the loser earns nothing."""
    matches = pd.DataFrame([game()])
    assert len(score_matches(matches)) == 1


# --- exclusions ------------------------------------------------------------

def test_below_tour_events_are_excluded():
    for name in ("Challenger Phoenix", "ITF W75 Tunis", "Exhibition Match",
                 "Australian Open Qualification"):
        assert eligible_matches(pd.DataFrame([game(tournament=name)])).empty, name


def test_qualifying_rounds_are_excluded():
    assert eligible_matches(pd.DataFrame([game(round_name="Qualifier")])).empty


def test_unfinished_and_cancelled_matches_are_excluded():
    assert eligible_matches(pd.DataFrame([game(status="INPROGRESS")])).empty
    assert eligible_matches(pd.DataFrame([game(status_extra="CANCELED")])).empty


def test_a_challenger_tour_label_is_excluded_even_when_the_name_is_clean():
    assert eligible_matches(pd.DataFrame([game(tour="ATP Challenger Tour")])).empty


def test_main_tour_matches_survive():
    assert len(eligible_matches(pd.DataFrame([game()]))) == 1


# --- season totals ---------------------------------------------------------

def test_season_total_sums_wins():
    matches = pd.DataFrame([
        game(home="Winner", round_name="Quarterfinal", home_sets=3, away_sets=1, timestamp=1),
        game(home="Winner", round_name="Semifinal", home_sets=3, away_sets=1, timestamp=2),
        game(home="Winner", round_name="Final", home_sets=3, away_sets=1, timestamp=3),
    ])
    row = score_players(matches).iloc[0]
    # The quarterfinal is this player's first win in the event, so it also
    # carries the round-of-16 credit -- 200 on the slam table.
    assert row["total_points"] == (400 + 200) + 500 + 700
    assert row["matches_won"] == 3
    assert row["titles"] == 1


def test_both_tours_normalize_against_one_distribution():
    """ATP and WTA fill the same roster slots and pay the identical table by
    round, so the pooled distribution is the comparable one."""
    matches = pd.DataFrame([
        game(home="Man", tour="ATP Tour", timestamp=1),
        game(home="Woman", tour="WTA Tour", tournament="US Open", timestamp=2),
    ])
    totals = score_players(matches)
    assert set(totals["league"]) == {"ATP", "WTA"}
    assert set(totals["norm_league"]) == {"Tennis"}
    assert set(assign_norm_key(totals, "Player")) == {"Tennis"}


def test_empty_input_is_empty_output():
    assert score_players(pd.DataFrame()).empty
    assert score_matches(pd.DataFrame()).empty
    assert eligible_matches(pd.DataFrame()).empty
