"""NHL scoring tests.

Expected values are computed by hand from NHL_Teams_Players.R.
"""

import pandas as pd
import pytest

from whul.scoring.nhl import (
    score_goalies,
    score_players,
    score_skaters,
    score_teams,
)
from whul.scoring.nhl import PTS_DIV_CHAMP
from whul.scoring.schedule import (
    SCHEDULE_CHANGES, factor_for, scale_benchmarks, scheduled_games,
)


def skater(**over):
    row = {"season": 2026, "skaterFullName": "Test Skater", "gamesPlayed": 82,
           "goals": 0, "assists": 0, "shots": 0, "plusMinus": 0}
    row.update(over)
    return row


def team(**over):
    row = {"season": 2026, "teamFullName": "Test Team", "gamesPlayed": 82,
           "wins": 0, "otLosses": 0, "goalsFor": 0, "goalsAgainst": 0}
    row.update(over)
    return row


# --- skaters ---------------------------------------------------------------

def test_skater_points_match_hand_calculation():
    """50*3 + 70*2 + 300*0.5 + 25*1 = 150 + 140 + 150 + 25 = 465"""
    df = pd.DataFrame([skater(goals=50, assists=70, shots=300, plusMinus=25)])
    assert score_skaters(df).iloc[0]["total_points"] == pytest.approx(465.0)


def test_plus_minus_can_be_negative():
    df = pd.DataFrame([skater(goals=20, assists=20, shots=100, plusMinus=-30)])
    # 60 + 40 + 50 - 30 = 120
    assert score_skaters(df).iloc[0]["total_points"] == pytest.approx(120.0)


def test_non_positive_skaters_are_dropped():
    assert score_skaters(pd.DataFrame([skater(plusMinus=-5)])).empty


def test_skater_column_names_resolve_from_either_convention():
    """The API returns camelCase; the R package returned snake_case."""
    camel = pd.DataFrame([skater(goals=10)])
    snake = pd.DataFrame([{"season": 2026, "skater_full_name": "Test Skater",
                           "games_played": 82, "goals": 10, "assists": 0,
                           "shots": 0, "plus_minus": 0}])
    assert score_skaters(camel).iloc[0]["total_points"] == pytest.approx(30.0)
    assert score_skaters(snake).iloc[0]["total_points"] == pytest.approx(30.0)


# --- goalies ---------------------------------------------------------------

def test_goalies_are_scored_but_not_rostered():
    """Kept because the R script computes it; the league holds no goalie slots."""
    df = pd.DataFrame([{"season": 2026, "goalieFullName": "Test Goalie",
                        "gamesPlayed": 60, "wins": 35, "shutouts": 5,
                        "saves": 1500, "goalsAgainst": 130}])
    # 35*4 + 5*3 + 1500*0.1 + 130*-1 = 140 + 15 + 150 - 130 = 175
    assert score_goalies(df).iloc[0]["total_points"] == pytest.approx(175.0)


def test_score_players_returns_skaters_only():
    skaters = pd.DataFrame([skater(goals=30)])
    goalies = pd.DataFrame([{"season": 2026, "goalieFullName": "G", "gamesPlayed": 60,
                             "wins": 40, "shutouts": 0, "saves": 0, "goalsAgainst": 0}])
    out = score_players(skaters, goalies)
    assert set(out["role"]) == {"Skater"}
    assert "G" not in set(out["player"])


# --- the 84-game expansion -------------------------------------------------

def test_schedule_factor_lifts_82_games_to_84():
    assert factor_for("NHL") == pytest.approx(84 / 82)
    assert factor_for("NFL") == 1.0, "the NFL has not expanded yet"


def test_benchmarks_scale_to_the_current_schedule():
    """Scaling one number per group, rather than every historical season."""
    bench = pd.DataFrame({"norm_key": ["NHL"], "benchmark": [1000.0]})
    out = scale_benchmarks(bench, "NHL")
    assert out.iloc[0]["benchmark"] == pytest.approx(1000 * 84 / 82)
    assert out.iloc[0]["schedule_factor"] == pytest.approx(84 / 82)


def test_unchanged_leagues_are_left_alone():
    bench = pd.DataFrame({"norm_key": ["NFL_QB"], "benchmark": [400.0]})
    out = scale_benchmarks(bench, "NFL")
    assert out.iloc[0]["benchmark"] == 400.0
    assert "schedule_factor" not in out.columns


# --- teams -----------------------------------------------------------------

def test_team_regular_season_scoring():
    """50 wins, 10 OTL, +60 diff, unscaled: 50*2 + 10*1 + 60*0.1 = 116"""
    df = pd.DataFrame([team(wins=50, otLosses=10, goalsFor=280, goalsAgainst=220)])
    out = score_teams(df, scale_regular_season=False).iloc[0]
    assert out["goal_diff"] == 60
    assert out["total_points"] == pytest.approx(116.0)


def test_only_regular_season_components_scale():
    """A playoff run does not get longer because the regular season did.

    16 playoff wins is a title: 5 appearance + 16 wins + 4 series * 5 = 89 flat,
    on top of the scaled regular-season terms.
    """
    regular = pd.DataFrame([team(wins=50, otLosses=10, goalsFor=280, goalsAgainst=220)])
    playoffs = pd.DataFrame([{"season": 2026, "teamFullName": "Test Team",
                              "gamesPlayed": 22, "wins": 16}])
    scaled = score_teams(regular, playoffs, scale_regular_season=True).iloc[0]
    unscaled = score_teams(regular, playoffs, scale_regular_season=False).iloc[0]

    postseason = 5 + 16 * 1 + 4 * 5
    assert unscaled["total_points"] == pytest.approx(116.0 + postseason)
    assert scaled["total_points"] == pytest.approx(116.0 * (84 / 82) + postseason)
    assert scaled["series_wins"] == 4


def test_scaling_is_on_by_default_for_nhl():
    df = pd.DataFrame([team(wins=50, otLosses=10, goalsFor=280, goalsAgainst=220)])
    assert score_teams(df).iloc[0]["schedule_factor"] == pytest.approx(84 / 82)


def test_missing_the_playoffs_earns_no_postseason_points():
    df = pd.DataFrame([team(wins=30, otLosses=8, goalsFor=200, goalsAgainst=260)])
    out = score_teams(df, pd.DataFrame(), scale_regular_season=False).iloc[0]
    assert out["made_playoffs"] == 0
    assert out["series_wins"] == 0
    assert out["total_points"] == pytest.approx(30 * 2 + 8 - 6.0)


def test_series_wins_need_four_victories_each():
    regular = pd.DataFrame([team(wins=40, otLosses=5, goalsFor=250, goalsAgainst=240)])
    for wins, expected in ((3, 0), (4, 1), (7, 1), (8, 2)):
        playoffs = pd.DataFrame([{"season": 2026, "teamFullName": "Test Team",
                                  "gamesPlayed": wins + 2, "wins": wins}])
        out = score_teams(regular, playoffs, scale_regular_season=False).iloc[0]
        assert out["series_wins"] == expected, wins


def test_empty_inputs_return_empty():
    assert score_skaters(pd.DataFrame()).empty
    assert score_goalies(pd.DataFrame()).empty
    assert score_teams(pd.DataFrame()).empty


def test_schedule_change_is_documented_with_its_season():
    change = SCHEDULE_CHANGES["NHL"]
    assert change.historical_games == 82
    assert change.current_games == 84
    assert change.effective_season == "2026-27"


# --- irregular seasons ------------------------------------------------------

def test_covid_seasons_are_excluded_from_benchmarks():
    """A 56-game NHL season would set a bar every full season clears."""
    from whul.scoring.schedule import irregular_seasons

    assert 2021 in irregular_seasons("NHL")
    assert 2022 not in irregular_seasons("NHL")
    assert 2020 in irregular_seasons("MLB")


def test_exclusions_explain_themselves():
    from whul.scoring.schedule import describe_exclusions

    notes = describe_exclusions("NHL", [2021, 2022, 2023])
    assert len(notes) == 1
    assert "56 of 82 games" in notes[0]
    assert "COVID" in notes[0]


def test_a_league_with_no_entry_excludes_nothing():
    # Named leagues keep acquiring exclusions as their COVID years get worked
    # out, so this pins the mechanism rather than whichever league is currently
    # clean: an unknown league filters nothing and explains nothing.
    from whul.scoring.schedule import describe_exclusions, irregular_seasons, usable_seasons

    assert irregular_seasons("Kabaddi") == set()
    assert describe_exclusions("Kabaddi", [2020, 2021]) == []
    assert usable_seasons("Kabaddi", [2020, 2021]) == [2020, 2021]


def test_the_nfls_only_exclusion_is_a_length_change_not_covid():
    """The NFL played every 2020 game. 2020 is out because it was the last
    16-game season, not because it was disrupted -- and the note must say so,
    since a reader who sees "COVID" will look for a distortion that isn't there."""
    from whul.scoring.schedule import describe_exclusions, irregular_seasons

    assert irregular_seasons("NFL") == {2020}
    note = describe_exclusions("NFL", [2020])[0]
    assert "16 of 17 games" in note
    assert "COVID" not in note


def test_shortened_seasons_are_dropped_not_scaled():
    """Scaling 56 games to 84 is a 1.5x extrapolation across a year that also had
    no crowds and division-only schedules -- the distortion is not only length."""
    from whul.scoring.schedule import IRREGULAR_SEASONS

    nhl_2021 = next(s for s in IRREGULAR_SEASONS if s.league == "NHL" and s.season == 2021)
    assert nhl_2021.games == 56
    assert nhl_2021.standard_games == 82


def test_college_covid_seasons_are_excluded_under_their_own_numbering():
    """Each league's exclusions are named the way ``season_label`` names its
    seasons -- football by the year it starts, basketball by the year it ends --
    so both point at the same disrupted winter of 2020-21. Name football's by
    its end year and the exclusion lands on 2021, a season played in full, while
    the shortened one walks into the pool."""
    from whul.scoring.schedule import irregular_seasons, usable_seasons

    assert irregular_seasons("NCAAF") == {2020}
    assert 2021 not in irregular_seasons("NCAAF")
    assert irregular_seasons("NCAAM") == {2020, 2021}
    assert irregular_seasons("NCAAW") == {2020, 2021}
    assert usable_seasons("NCAAF", [2019, 2020, 2021, 2022]) == [2019, 2021, 2022]


def test_college_spring_sports_lost_2020_outright():
    from whul.scoring.schedule import irregular_seasons

    assert irregular_seasons("NCAA Baseball") == {2020}
    assert irregular_seasons("NCAA Softball") == {2020}


def test_a_calendar_exclusion_does_not_claim_a_game_count():
    """These are excluded for what happened to the calendar, not for a number of
    games we can stand behind -- so the note must not print "0 of 0 games"."""
    from whul.scoring.schedule import describe_exclusions

    note = describe_exclusions("NCAAM", [2021])[0]
    assert "games" not in note
    assert "COVID" in note


# --- division titles -------------------------------------------------------

METRO = "Metropolitan"


def divisions(*teams, season=2026, division=METRO):
    return pd.DataFrame(
        [{"season": season, "team": name, "division": division} for name in teams]
    )


def finished(name, wins, otl=0, diff=0, season=2026, **over):
    """A club that has played its whole schedule."""
    row = team(season=season, teamFullName=name, wins=wins, otLosses=otl,
               goalsFor=200 + diff, goalsAgainst=200,
               gamesPlayed=scheduled_games("NHL", season))
    row.update(over)
    return row


def test_no_division_map_means_no_title():
    """A club's own totals never say who it was racing."""
    frame = pd.DataFrame([finished("Alpha", 55), finished("Beta", 30)])
    out = score_teams(frame, scale_regular_season=False)
    assert set(out["is_division_champ"]) == {0}


def test_the_best_record_in_the_division_wins_it():
    frame = pd.DataFrame([finished("Alpha", 55), finished("Beta", 30)])
    out = score_teams(
        frame, scale_regular_season=False, divisions=divisions("Alpha", "Beta")
    ).set_index("team")
    assert out.loc["Alpha", "is_division_champ"] == 1
    assert out.loc["Beta", "is_division_champ"] == 0
    assert out.loc["Alpha", "total_points"] == pytest.approx(
        55 * 2 + 0 + 0 * 0.1 + PTS_DIV_CHAMP
    )


def test_the_title_is_won_on_points_not_wins():
    """Beta wins fewer games and collects more points, which is the standings."""
    frame = pd.DataFrame([finished("Alpha", 44, otl=2), finished("Beta", 43, otl=6)])
    out = score_teams(
        frame, scale_regular_season=False, divisions=divisions("Alpha", "Beta")
    ).set_index("team")
    assert out.loc["Beta", "standings_points"] == 92
    assert out.loc["Alpha", "standings_points"] == 90
    assert out.loc["Beta", "is_division_champ"] == 1


def test_each_division_gets_its_own_champion():
    frame = pd.DataFrame([
        finished("Alpha", 55), finished("Beta", 30),
        finished("Gamma", 40), finished("Delta", 20),
    ])
    placed = pd.concat([
        divisions("Alpha", "Beta", division="Metropolitan"),
        divisions("Gamma", "Delta", division="Atlantic"),
    ])
    out = score_teams(frame, scale_regular_season=False, divisions=placed)
    champs = set(out.loc[out["is_division_champ"] == 1, "team"])
    assert champs == {"Alpha", "Gamma"}


def test_a_tie_on_points_is_broken_on_regulation_wins():
    frame = pd.DataFrame([
        finished("Alpha", 45, otl=2, regulationWins=30),
        finished("Beta", 45, otl=2, regulationWins=38),
    ])
    out = score_teams(
        frame, scale_regular_season=False, divisions=divisions("Alpha", "Beta")
    ).set_index("team")
    assert out.loc["Beta", "is_division_champ"] == 1
    assert out.loc["Alpha", "is_division_champ"] == 0


def test_a_tie_the_feed_cannot_break_falls_through_to_goal_difference():
    """Without regulation wins the tiebreak still has somewhere to go."""
    frame = pd.DataFrame([
        finished("Alpha", 45, otl=2, diff=10),
        finished("Beta", 45, otl=2, diff=40),
    ])
    out = score_teams(
        frame, scale_regular_season=False, divisions=divisions("Alpha", "Beta")
    ).set_index("team")
    assert out.loc["Beta", "is_division_champ"] == 1


def test_clubs_level_on_everything_share_the_title():
    """Nothing on the ice separated them, so nothing here invents a winner."""
    frame = pd.DataFrame([finished("Alpha", 45, otl=2), finished("Beta", 45, otl=2)])
    out = score_teams(
        frame, scale_regular_season=False, divisions=divisions("Alpha", "Beta")
    )
    assert list(out["is_division_champ"]) == [1, 1]


def test_no_title_is_awarded_while_the_season_is_running():
    """The bug this replaces, in the other direction: ten points in November to
    whoever started well, taken back in March."""
    frame = pd.DataFrame([
        dict(finished("Alpha", 20), gamesPlayed=30),
        dict(finished("Beta", 10), gamesPlayed=30),
    ])
    out = score_teams(
        frame, scale_regular_season=False, divisions=divisions("Alpha", "Beta")
    )
    assert set(out["is_division_champ"]) == {0}


def test_a_division_with_one_club_short_of_the_finish_awards_nothing():
    """Not "most clubs have finished" -- a club with games in hand can still win."""
    frame = pd.DataFrame([
        finished("Alpha", 44),
        dict(finished("Beta", 43), gamesPlayed=79),
    ])
    out = score_teams(
        frame, scale_regular_season=False, divisions=divisions("Alpha", "Beta")
    )
    assert set(out["is_division_champ"]) == {0}


def test_a_club_missing_from_the_division_map_wins_nothing():
    """An unjoined club is invisible, which must not make it a champion."""
    frame = pd.DataFrame([finished("Alpha", 55), finished("Unknown Club", 60)])
    out = score_teams(
        frame, scale_regular_season=False, divisions=divisions("Alpha")
    ).set_index("team")
    assert out.loc["Alpha", "is_division_champ"] == 1
    assert out.loc["Unknown Club", "is_division_champ"] == 0


def test_the_title_does_not_scale_with_the_schedule():
    """A title is an outcome, not a rate: 84 games do not make it worth more."""
    frame = pd.DataFrame([finished("Alpha", 50, season=2027), finished("Beta", 30, season=2027)])
    placed = divisions("Alpha", "Beta", season=2027)
    scaled = score_teams(frame, scale_regular_season=True, divisions=placed)
    unscaled = score_teams(frame, scale_regular_season=False, divisions=placed)
    champ = scaled.set_index("team").loc["Alpha"]
    plain = unscaled.set_index("team").loc["Alpha"]
    assert champ["total_points"] - plain["total_points"] == pytest.approx(
        (50 * 2) * (84 / 82) - (50 * 2)
    )


def test_a_covid_season_is_judged_complete_at_its_own_length():
    """56 games was the whole 2020-21 season, not two thirds of an unfinished one."""
    frame = pd.DataFrame([
        finished("Alpha", 35, season=2021), finished("Beta", 20, season=2021),
    ])
    assert set(frame["gamesPlayed"]) == {56}
    out = score_teams(
        frame, scale_regular_season=False,
        divisions=divisions("Alpha", "Beta", season=2021),
    ).set_index("team")
    assert out.loc["Alpha", "is_division_champ"] == 1
