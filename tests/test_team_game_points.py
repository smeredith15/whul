"""A club's games, each priced on its own, add up to its season.

The results list prices every game at the scorer's own weights and leaves the
season's items -- titles, berths, series, byes -- to the panel. These pin that
the two agree: a game's points are the season total's game-level terms, so the
games plus the season's items are the total, in every sport.
"""

import pandas as pd
import pytest

from whul.scoring import mlb, nba, ncaa, nfl, nhl, soccer


def test_nfl_games_add_up_to_the_season_less_its_titles():
    from tests.test_nfl import DIVISIONS, game

    sched = pd.DataFrame([
        game("BUF", "MIA", 31, 0, div=1, gameday="2026-09-13"),
        game("MIA", "BUF", 20, 27, div=1, gameday="2026-10-04"),
        game("BUF", "NYJ", 17, 17, gameday="2026-10-11"),
        game("BUF", "KC", 24, 21, game_type="WC", gameday="2027-01-10"),
    ])
    divisions = pd.concat([DIVISIONS, pd.DataFrame([
        {"season": 2026, "team_abbr": "NYJ", "team_division": "AFC East", "div_rank": 3},
        {"season": 2026, "team_abbr": "KC", "team_division": "AFC West", "div_rank": 1},
    ])], ignore_index=True)
    total = nfl.score_teams(sched, divisions).set_index("team").loc["BUF"]
    games = nfl._team_games(sched)
    mine = games[games["team"] == "BUF"]
    season = (total["div_champ"] * nfl.TEAM_WEIGHTS["div_champ"]
              + total["playoff_appearance"] * nfl.TEAM_WEIGHTS["playoff_appearance"]
              + total["bye_wins"] * nfl.TEAM_WEIGHTS["playoff_wins"])
    assert nfl.team_game_points(mine).sum() + season == pytest.approx(total["total_points"])
    assert set(mine["date"]) >= {"2026-09-13", "2027-01-10"}
    assert set(mine["opponent"]) == {"MIA", "NYJ", "KC"}


def test_nba_games_add_up_to_the_season_less_its_berths():
    from tests.test_nba import game, many

    sched = many(
        game("BOS", "NYK", 120, 100),
        game("NYK", "BOS", 99, 101, notes="NBA Cup - Group Play"),
        game("BOS", "MIA", 110, 104, season_type=3, notes="East First Round - Game 1"),
    )
    total = nba.score_teams(sched).set_index("team").loc["BOS"]
    games = nba._team_games(sched)
    season = (total["playin_only"] * nba.TEAM_WEIGHTS["playin_only"]
              + total["playoff_appearance"] * nba.TEAM_WEIGHTS["playoff_appearance"]
              + total["playoff_series_wins"] * nba.TEAM_WEIGHTS["playoff_series_wins"]
              + total["ist_champ"] * nba.TEAM_WEIGHTS["ist_champ"])
    mine = games[games["team"] == "BOS"]
    assert nba.team_game_points(mine).sum() + season == pytest.approx(total["total_points"])


def test_mlb_games_add_up_to_the_game_level_terms():
    from tests.test_mlb import game

    sched = pd.DataFrame([
        game("NYY", "BOS", 7, 0), game("BOS", "NYY", 2, 1), game("NYY", "BOS", 9, 3),
        game("NYY", "BOS", 4, 2, game_type="D"),
    ])
    counts = mlb.summarize_teams(sched).set_index("team").loc["NYY"]
    games = mlb._team_games(sched)
    mine = games[games["team"] == "NYY"]
    expected = (counts["reg_wins"] * mlb.BASE_REG_WIN
                + counts["reg_big_wins"] * mlb.PTS_BIG_WIN
                + counts["shutouts"] * mlb.PTS_SHUTOUT
                + counts["run_diff"] * mlb.PTS_RUN_DIFF
                + counts["playoff_game_wins"] * mlb.BASE_PLAYOFF_WIN)
    assert mlb.team_game_points(mine).sum() == pytest.approx(expected)
    # The contract year's weight on everything; the lift on the season only.
    weighted = mlb.team_game_points(mine, weight=0.75, lift=1.2).sum()
    october = counts["playoff_game_wins"] * mlb.BASE_PLAYOFF_WIN
    assert weighted == pytest.approx(((expected - october) * 1.2 + october) * 0.75)


def test_ncaa_football_games_add_up_to_the_season_less_its_titles():
    from tests.test_ncaa import game

    sched = pd.DataFrame([
        game("Duke", "UNC", 35, 10), game("Duke", "Ohio", 30, 7, ac="MAC"),
        game("Duke", "Clemson", 20, 24),
    ])
    total = ncaa.score_football(sched).set_index("team").loc["Duke"]
    games = ncaa.football_games(sched)
    season = (total["conf_title_win"] * ncaa.FB_WEIGHTS["conf_title_win"]
              + total["playoff_app"] * ncaa.FB_WEIGHTS["playoff_app"]
              + total["pts_reg_champ"]
              + total["playoff_byes"] * ncaa.FB_WEIGHTS["playoff_wins"])
    mine = games[games["team"] == "Duke"]
    assert ncaa.football_game_points(mine).sum() + season == pytest.approx(
        total["total_points"])


def test_ncaa_basketball_games_add_up_to_the_season_less_its_titles():
    from tests.test_ncaa import game

    sched = pd.DataFrame([
        game("Duke", "UNC", 90, 60, game_date="2026-01-10", season=2026),
        game("Duke", "Kansas", 70, 72, ac="B12", game_date="2026-01-17", season=2026),
        game("Duke", "Yale", 80, 50, ac="Ivy", season_type=3,
             notes="Men's Basketball Championship - East Region - 1st Round",
             game_date="2026-03-20", season=2026),
    ])
    total = ncaa.score_basketball(sched).set_index("team").loc["Duke"]
    games = ncaa.basketball_games(sched)
    season = (total["conf_tourney_champ"] * ncaa.BB_WEIGHTS["conf_tourney_champ"]
              + total["mm_appearance"] * ncaa.BB_WEIGHTS["mm_appearance"]
              + total["pts_reg_champ"]
              + total["conf_tourney_byes"] * ncaa.BB_WEIGHTS["conf_tourney_wins"])
    mine = games[games["team"] == "Duke"]
    assert ncaa.basketball_game_points(mine).sum() + season == pytest.approx(
        total["total_points"])


def test_ncaa_diamond_games_add_up_to_the_season_less_its_series():
    from tests.test_ncaa import game

    sched = pd.DataFrame([
        game("LSU", "Bama", 8, 2, hc="SEC", ac="SEC", game_date="2026-04-10", season=2026),
        game("LSU", "Tulane", 3, 4, hc="SEC", ac="AAC", game_date="2026-04-12", season=2026),
        game("LSU", "Rice", 6, 1, hc="SEC", ac="AAC", season_type=3,
             notes="Baton Rouge Regional", game_date="2026-05-30", season=2026),
    ])
    total = ncaa.score_diamond(sched, "NCAA Baseball").set_index("team").loc["LSU"]
    games = ncaa.diamond_games(sched)
    season = (total["series_regional"] * ncaa.PTS_SERIES_REGIONAL
              + total["series_super"] * ncaa.PTS_SERIES_SUPER
              + total["series_cws_champ"] * ncaa.PTS_SERIES_CWS
              + total["conf_tourney_byes"] * ncaa.DIAMOND_REG_WIN)
    mine = games[games["team"] == "LSU"]
    assert ncaa.diamond_game_points(mine).sum() + season == pytest.approx(
        total["total_points"])


def test_an_nhl_club_game_prices_the_overtime_loss_the_score_cannot_show():
    games = pd.DataFrame([
        {"wins": 1, "otLosses": 0, "goalsFor": 4, "goalsAgainst": 2, "game_type": 2},
        {"wins": 0, "otLosses": 1, "goalsFor": 2, "goalsAgainst": 3, "game_type": 2},
        {"wins": 0, "otLosses": 0, "goalsFor": 1, "goalsAgainst": 3, "game_type": 2},
        {"wins": 1, "otLosses": 0, "goalsFor": 3, "goalsAgainst": 2, "game_type": 3},
    ])
    got = list(nhl.team_game_points(games))
    assert got == pytest.approx([2 + 0.2, 1 - 0.1, -0.2, nhl.PTS_PLAYOFF_WIN])


def test_soccer_matches_add_up_to_the_season_less_its_title():
    matches = pd.DataFrame([
        {"team": "Arsenal", "opponent": "Spurs", "league": "Premier League",
         "date": "2026-09-01", "competition": "Premier League",
         "goals_for": 3, "goals_against": 0},
        {"team": "Arsenal", "opponent": "Chelsea", "league": "Premier League",
         "date": "2026-09-08", "competition": "Premier League",
         "goals_for": 1, "goals_against": 1},
    ])
    per = soccer.score_team_matches(matches)
    total = soccer.score_teams(matches).set_index("team").loc["Arsenal"]
    assert per["match_points"].sum() + total["pts_league_title"] + total["bye_points"] \
        == pytest.approx(total["total_points"])
    assert list(per["opponent"]) == ["Spurs", "Chelsea"]
