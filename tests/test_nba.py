"""NBA scoring tests.

Expected values are computed by hand from NBA_Players_Teams.R.
"""

import pandas as pd
import pytest

from whul.scoring.nba import score_players, score_teams


def box(**over):
    row = {
        "season": 2026, "athlete_id": "1", "athlete_display_name": "Test Player",
        "athlete_position_abbreviation": "PG", "season_type": 2, "points": 0.0, "rebounds": 0.0,
        "assists": 0.0, "steals": 0.0, "blocks": 0.0, "turnovers": 0.0,
        "three_point_field_goals_made": 0.0, "plus_minus": "+0",
    }
    row.update(over)
    return row


def season_of(rows, n=20):
    """Repeat a game line enough times to clear the 15-game minimum."""
    return pd.DataFrame([dict(r) for r in rows for _ in range(n)])


# --- player scoring --------------------------------------------------------

def test_game_score_matches_hand_calculation():
    """20*1 + 5*1.2 + 5*1.5 + 2*3 + 1*3 + 3*-1 + 2*0.5 = 40.5 per game."""
    games = season_of([box(points=20, rebounds=5, assists=5, steals=2, blocks=1,
                           turnovers=3, three_point_field_goals_made=2)], n=20)
    out = score_players(games).iloc[0]
    assert out["games_played"] == 20
    assert out["regular_points"] == pytest.approx(40.5 * 20)


def test_double_double_bonus():
    """10 pts + 10 reb = 10 + 12 + 1.5 = 23.5"""
    games = season_of([box(points=10, rebounds=10)], n=20)
    assert score_players(games).iloc[0]["regular_points"] == pytest.approx(23.5 * 20)


def test_triple_double_earns_both_bonuses():
    """A triple-double is also a double-double: 10 + 12 + 15 + 1.5 + 3 = 41.5"""
    games = season_of([box(points=10, rebounds=10, assists=10)], n=20)
    assert score_players(games).iloc[0]["regular_points"] == pytest.approx(41.5 * 20)


def test_steals_and_blocks_count_toward_doubles():
    """Per the R script, all five categories can trigger the bonus."""
    games = season_of([box(steals=10, blocks=10)], n=20)
    # 30 + 30 + 1.5 double-double
    assert score_players(games).iloc[0]["regular_points"] == pytest.approx(61.5 * 20)


def test_plus_minus_is_parsed_from_signed_strings():
    games = pd.DataFrame(
        [box(points=30, plus_minus="+10")] * 10 + [box(points=30, plus_minus="-4")] * 10
    )
    out = score_players(games).iloc[0]
    # 30 pts/game * 20 games = 600, plus 0.1 * (10*10 - 4*10) = 6.0
    assert out["regular_points"] == pytest.approx(606.0)
    assert out["total_points"] == pytest.approx(606.0)


def test_minimum_games_filter():
    """R filters games_played >= 15."""
    assert score_players(season_of([box(points=50)], n=14)).empty
    assert len(score_players(season_of([box(points=50)], n=15))) == 1


def test_minimum_score_filter():
    """R filters final_player_score > 100."""
    assert score_players(season_of([box(points=1)], n=20)).empty


def test_playin_games_are_dropped_from_player_scoring():
    """Play-In is neither regular season nor playoffs, so it counts for neither."""
    games = pd.DataFrame(
        [box(points=30, season_type=2)] * 20 + [box(points=999, season_type=5)]
    )
    out = score_players(games).iloc[0]
    assert out["regular_games"] == 20, "Play-In must not pad the regular season"
    assert out["postseason_games"] == 0, "Play-In must not earn the bonus"
    assert out["postseason_bonus"] == 0.0


def test_playoff_games_earn_the_bonus_at_the_nba_scalar():
    """Playoff rate 50/game * NBA scalar 8.2 = 410 bonus."""
    games = pd.DataFrame(
        [box(points=30, season_type=2)] * 20 + [box(points=50, season_type=3)] * 2
    )
    out = score_players(games).iloc[0]
    assert out["postseason_games"] == 2
    assert out["postseason_rate"] == pytest.approx(50.0)
    assert out["postseason_bonus"] == pytest.approx(410.0)


# --- team scoring ----------------------------------------------------------

def game(home, away, hs, as_, season_type=2, notes="", completed=True, season=2026):
    return {
        "season": season, "season_type": season_type, "notes_headline": notes,
        "home_abbreviation": home, "away_abbreviation": away,
        "home_score": hs, "away_score": as_, "status_type_completed": completed,
    }


def many(*rows, pad=10):
    """Pad with filler games so both teams clear MIN_TEAM_GAMES."""
    filler = [game("BOS", "MIL", 100, 100) for _ in range(pad)]
    return pd.DataFrame(list(rows) + filler)


def test_regular_season_points():
    """2 wins, 1 big win (>=15), +40 diff: 2*2 + 1*1 + 40*0.05 = 7.0 (plus filler)."""
    sched = pd.DataFrame(
        [game("BOS", "MIL", 120, 90), game("MIL", "BOS", 100, 110)]
        + [game("BOS", "MIL", 100, 100) for _ in range(10)]
    )
    out = score_teams(sched).set_index("team")
    assert out.loc["BOS", "reg_wins"] == 2
    assert out.loc["BOS", "reg_big_wins"] == 1
    assert out.loc["BOS", "point_diff"] == 40
    assert out.loc["BOS", "total_points"] == pytest.approx(7.0)


def test_big_win_threshold_is_fifteen():
    sched = many(game("BOS", "MIL", 115, 100), game("BOS", "MIL", 114, 100))
    assert score_teams(sched).set_index("team").loc["BOS", "reg_big_wins"] == 1


def test_unplayed_games_excluded_even_though_scored_zero_zero():
    """Live feeds carry future fixtures as completed=False, 0-0.

    Counting them would treat every unplayed game as a played tie -- which is the
    difference between a correct and a badly wrong live standings table.
    """
    sched = many(game("BOS", "MIL", 0, 0, completed=False))
    out = score_teams(sched).set_index("team")
    # Only the 10 filler ties remain, so no team has any margin recorded.
    assert out.loc["BOS", "point_diff"] == 0
    assert out.loc["BOS", "reg_wins"] == 0


def test_all_star_games_are_excluded():
    sched = many(game("LEB", "GIA", 184, 175, notes="NBA All-Star Game"))
    assert "LEB" not in set(score_teams(sched)["team"])


def test_playoff_series_wins_and_appearance():
    """8 playoff wins = 2 series: 10 + 8*3 + 2*5 = 44 on top of regular season."""
    playoffs = [game("BOS", "MIL", 110, 100, season_type=3) for _ in range(8)]
    sched = pd.DataFrame(playoffs + [game("BOS", "MIL", 100, 100) for _ in range(10)])
    out = score_teams(sched).set_index("team")
    assert out.loc["BOS", "playoff_appearance"] == 1
    assert out.loc["BOS", "playoff_wins"] == 8
    assert out.loc["BOS", "playoff_series_wins"] == 2
    assert out.loc["BOS", "total_points"] == pytest.approx(44.0)


def test_playin_bonus_only_when_it_does_not_lead_to_a_berth():
    made_it = pd.DataFrame(
        [game("BOS", "MIL", 110, 100, season_type=5),
         game("BOS", "MIL", 110, 100, season_type=3)]
        + [game("BOS", "MIL", 100, 100) for _ in range(10)]
    )
    missed = many(game("BOS", "MIL", 90, 100, season_type=5))
    assert score_teams(made_it).set_index("team").loc["BOS", "playin_only"] == 0
    assert score_teams(missed).set_index("team").loc["BOS", "playin_only"] == 1


def test_playoff_margins_excluded_from_point_diff():
    sched = many(game("BOS", "MIL", 150, 100, season_type=3))
    assert score_teams(sched).set_index("team").loc["BOS", "point_diff"] == 0


def test_nba_cup_wins_and_title():
    sched = many(
        game("BOS", "MIL", 110, 100, notes="NBA Cup Quarterfinal"),
        game("BOS", "MIL", 110, 100, notes="NBA Cup Championship"),
    )
    out = score_teams(sched).set_index("team")
    assert out.loc["BOS", "ist_wins"] == 2
    assert out.loc["BOS", "ist_champ"] == 1
    # 2*2 + 8 = 12, plus 2 reg wins counted too (Cup games are regular season)
    assert out.loc["BOS", "total_points"] == pytest.approx(2 * 2 + 12 + 20 * 0.05)


# --- integration -----------------------------------------------------------

@pytest.mark.network
def test_real_2023_season_sanity():
    from whul.sources import hoopr

    players = score_players(hoopr.load_player_box([2023]))
    assert players.nlargest(1, "total_points").iloc[0]["player"] == "Nikola Jokic"

    teams = score_teams(hoopr.load_schedule([2023]))
    assert len(teams) == 30, "exhibition squads must not enter the team pool"
    assert teams.iloc[0]["team"] == "BOS"
