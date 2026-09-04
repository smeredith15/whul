"""Season-length proration."""

import pandas as pd
import pytest

from whul.scoring.proration import (
    MAX_FACTOR,
    ProrationRule,
    load_rule,
    load_rules,
    prorate,
    save_rule,
)
from whul.store import open_store


@pytest.fixture
def store():
    return open_store(":memory:")


def team_frame():
    return pd.DataFrame({
        "team": ["A"],
        "reg_wins": [10.0],
        "big_wins": [4.0],
        "title_points": [25.0],
        "total_points": [39.0],
    })


SHORT_SEASON = ProrationRule("MLS", "2027", actual_games=20, expected_games=34)


# --- the factor ------------------------------------------------------------

def test_the_factor_is_expected_over_actual():
    assert SHORT_SEASON.factor == pytest.approx(1.7)


def test_a_full_season_needs_no_adjustment():
    assert ProrationRule("MLS", "2026", 34, 34).factor == 1.0


def test_a_factor_below_one_is_a_data_entry_error():
    """A league does not play more games than expected; the counts were swapped."""
    rule = ProrationRule("MLS", "2027", actual_games=34, expected_games=20)
    assert any("below 1" in p for p in rule.validate())


def test_a_season_under_half_length_is_refused():
    """At that point the sample is too thin, and the admin should be deciding
    what to do rather than discovering a 3x multiplier in the standings."""
    rule = ProrationRule("MLS", "2027", actual_games=10, expected_games=34)
    assert any(str(MAX_FACTOR) in p for p in rule.validate())


def test_zero_games_played_is_refused_rather_than_dividing():
    rule = ProrationRule("MLS", "2027", actual_games=0, expected_games=34)
    assert rule.validate()
    with pytest.raises(ValueError, match="positive"):
        _ = rule.factor


# --- what scales and what does not -----------------------------------------

def test_counting_production_scales():
    out = prorate(team_frame(), SHORT_SEASON, ["reg_wins", "big_wins"])
    assert out.iloc[0]["reg_wins"] == pytest.approx(17.0)
    assert out.iloc[0]["big_wins"] == pytest.approx(6.8)


def test_one_off_achievements_do_not_scale():
    """A title was won once. Doubling it because the season was half length
    would be absurd."""
    out = prorate(team_frame(), SHORT_SEASON, ["reg_wins", "big_wins"])
    assert out.iloc[0]["title_points"] == 25.0


def test_the_total_is_rebuilt_so_the_untouched_part_survives():
    """Scaling the total instead would carry the title along with the wins."""
    out = prorate(team_frame(), SHORT_SEASON, ["reg_wins", "big_wins"])
    assert out.iloc[0]["total_points"] == pytest.approx(25.0 + 17.0 + 6.8)


def test_the_factor_is_recorded_on_every_row():
    out = prorate(team_frame(), SHORT_SEASON, ["reg_wins"])
    assert out.iloc[0]["proration_factor"] == pytest.approx(1.7)


def test_columns_the_frame_lacks_are_ignored():
    """A league's scorer emits the components it has; a caller naming a
    superset should not fail on the ones that do not apply."""
    out = prorate(team_frame(), SHORT_SEASON, ["reg_wins", "shutouts", "goals"])
    assert out.iloc[0]["reg_wins"] == pytest.approx(17.0)


def test_naming_no_column_that_exists_is_an_error():
    """Nothing would be prorated and the caller thinks otherwise."""
    with pytest.raises(ValueError, match="nothing would be prorated"):
        prorate(team_frame(), SHORT_SEASON, ["not_a_column"])


def test_an_invalid_rule_refuses_to_score():
    bad = ProrationRule("MLS", "2027", actual_games=10, expected_games=34)
    with pytest.raises(ValueError, match="exceeds"):
        prorate(team_frame(), bad, ["reg_wins"])


def test_empty_input_is_empty_output():
    assert prorate(pd.DataFrame(), SHORT_SEASON, ["reg_wins"]).empty


# --- admin-entered rules ---------------------------------------------------

def test_a_rule_round_trips_through_the_store(store):
    save_rule(store, SHORT_SEASON)
    back = load_rule(store, "MLS", "2027")
    assert back.actual_games == 20
    assert back.expected_games == 34
    assert back.factor == pytest.approx(1.7)


def test_a_league_without_a_rule_returns_nothing(store):
    """A full-length season needs no entry at all."""
    assert load_rule(store, "NFL", "2026-27") is None


def test_an_invalid_rule_is_refused_at_save_time(store):
    """Better than discovering it when the standings move."""
    with pytest.raises(ValueError):
        save_rule(store, ProrationRule("MLS", "2027", 10, 34))


def test_updating_a_rule_replaces_it(store):
    """The expected-game count is an estimate until the season is over."""
    save_rule(store, SHORT_SEASON)
    save_rule(store, ProrationRule("MLS", "2027", 22, 34, note="revised"))
    back = load_rule(store, "MLS", "2027")
    assert back.actual_games == 22
    assert back.note == "revised"


def test_every_rule_for_a_season_can_be_read_at_once(store):
    save_rule(store, SHORT_SEASON)
    save_rule(store, ProrationRule("MLB", "2027", 140, 162))
    rules = load_rules(store, "2027")
    assert set(rules) == {"MLS", "MLB"}
    assert rules["MLB"].factor == pytest.approx(162 / 140)


def test_rules_are_scoped_to_their_season(store):
    save_rule(store, SHORT_SEASON)
    assert load_rules(store, "2026-27") == {}


# --- the shortened 2026-27 baseball window -----------------------------------

def test_mlb_2026_27_is_prorated_and_mlb_teams_are_not():
    """Teams are bisected at the All-Star break and bisection already inflates
    the second stretch so the two reconcile to a full season. Prorating them as
    well would apply the same correction twice."""
    from whul.scoring.proration import BUILT_IN_RULES, built_in_rule

    rule = built_in_rule("MLB", "2026-27")
    assert rule is not None
    assert rule.expected_games == 162
    assert 1.15 < rule.factor < 1.30
    assert rule.validate() == []
    assert [r.league for r in BUILT_IN_RULES] == ["MLB"]


def test_the_rule_is_for_this_season_only():
    """A full league year needs no correction; the window is what is short."""
    from whul.scoring.proration import built_in_rule

    assert built_in_rule("MLB", "2027-28") is None
    assert built_in_rule("NFL", "2026-27") is None


def test_a_players_whole_total_scales():
    """Player scoring is counting production end to end -- no title or playoff
    run in it to hold still -- so nothing is left behind by scaling the lot."""
    import pandas as pd

    from whul.scoring.proration import built_in_rule, prorate

    rule = built_in_rule("MLB", "2026-27")
    scored = pd.DataFrame({
        "player": ["Judge", "Skenes"], "role": ["Batter", "Pitcher"],
        "role_points": [400.0, 300.0], "total_points": [400.0, 300.0],
    })
    out = prorate(scored, rule, columns=["role_points"])
    assert out["total_points"].tolist() == pytest.approx(
        [400 * rule.factor, 300 * rule.factor])
    assert out["role_points"].tolist() == out["total_points"].tolist()


def test_the_live_mlb_source_prorates_and_the_historical_one_does_not():
    """The benchmark must be drawn from unprorated whole seasons: prorating the
    pool as well would lift the bar by exactly as much as it lifts the score,
    and the correction would cancel itself out."""
    import pandas as pd

    from whul.benchmark_sources import _prorated

    scored = pd.DataFrame({
        "player": ["Judge"], "role": ["Batter"],
        "role_points": [400.0], "total_points": [400.0],
    })
    lifted = _prorated(scored.copy(), "MLB")
    assert lifted.loc[0, "total_points"] > 400.0

    # A league with no rule passes through untouched.
    assert _prorated(scored.copy(), "NFL").loc[0, "total_points"] == 400.0
