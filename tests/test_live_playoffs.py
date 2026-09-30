"""The live player sources pay the playoffs; the benchmark builders do not.

The NFL, NBA and NHL live sources were the benchmark's own builders, which
score the regular season alone -- so no player in those leagues was ever paid
for a playoff run, although the Scoring page promised them 10% of a season.
"""

import datetime as _dt

import pandas as pd
import pytest

from whul import benchmark_sources


def _nfl_week(week, kind="REG", player="Test QB", pid="00-1", yards=250):
    return {"season": 2026, "season_type": kind, "player_id": pid,
            "player_display_name": player, "position": "QB",
            "recent_team": "BUF", "week": week, "passing_yards": yards,
            "passing_tds": 0, "interceptions": 0, "rushing_yards": 0,
            "rushing_tds": 0, "receptions": 0, "receiving_yards": 0,
            "receiving_tds": 0, "sack_fumbles_lost": 0,
            "rushing_fumbles_lost": 0, "receiving_fumbles_lost": 0}


def test_every_team_sport_has_a_live_source_that_pays_the_playoffs():
    for key in ("nfl", "nba", "nhl", "mlb"):
        assert benchmark_sources.SOURCES[key].live is not None, key


def test_nfl_live_pays_january_and_the_benchmark_does_not(monkeypatch):
    from whul.sources import nflverse

    weekly = pd.DataFrame([_nfl_week(w) for w in range(1, 18)]
                          + [_nfl_week(19, "POST", yards=400)])
    monkeypatch.setattr(nflverse, "load_player_stats", lambda seasons: weekly)

    load, score = benchmark_sources._nfl_players_live()
    live = score(load([2026])).iloc[0]
    load, score = benchmark_sources._nfl_players()
    bench = score(load([2026])).iloc[0]

    assert live["postseason_games"] == 1
    # 400 yards is 16 points, at 1.7 of a club game, held to February.
    assert live["postseason_pending"] == pytest.approx(16.0 * 1.7)
    assert bench["postseason_pending"] == 0.0
    assert live["regular_points"] == pytest.approx(bench["regular_points"])


def _nba_game(game, kind=2, points=20, team="BOS", pid="1", name="Test Player"):
    return {"season": 2027, "athlete_id": pid, "athlete_display_name": name,
            "athlete_position_abbreviation": "PG", "season_type": kind,
            "points": points, "rebounds": 0, "assists": 0, "steals": 0,
            "blocks": 0, "turnovers": 0, "three_point_field_goals_made": 0,
            "plus_minus": "+0", "team_abbreviation": team, "game_id": game}


def test_nba_live_pays_the_playoffs_and_the_benchmark_does_not(monkeypatch):
    from whul.sources import espn

    box = pd.DataFrame([_nba_game(f"r{n}") for n in range(20)]
                       + [_nba_game("p1", kind=3, points=30)])
    monkeypatch.setattr(espn, "load_nba_player_box", lambda seasons: box)

    load, score = benchmark_sources._nba_players_live()
    live = score(load([2027])).iloc[0]
    load, score = benchmark_sources._nba_players()
    bench = score(load([2027])).iloc[0]

    assert live["postseason_games"] == 1
    assert live["postseason_pending"] == pytest.approx(30.0 * 8.2)
    assert bench["postseason_pending"] == 0.0


def _skater(name, team, games, goals, **over):
    row = {"season": 2027, "skaterFullName": name, "teamAbbrevs": team,
           "playerId": name.lower(), "gamesPlayed": games, "goals": goals,
           "assists": 0, "shots": 0, "plusMinus": 0}
    row.update(over)
    return row


def _nhl(monkeypatch, today):
    from whul.sources import nhl as source

    asked = []

    def skaters(seasons, game_type=source.GAME_TYPE_REGULAR):
        asked.append(game_type)
        if game_type == source.GAME_TYPE_PLAYOFFS:
            return pd.DataFrame([
                _skater("Depth", "EDM", 2, 1),
                _skater("Captain", "EDM", 8, 4),
            ])
        return pd.DataFrame([
            _skater("Depth", "EDM", 80, 10),
            _skater("Captain", "EDM", 84, 40),
        ])

    class _Day(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(*today)

    monkeypatch.setattr(source, "load_skaters", skaters)
    monkeypatch.setattr(source, "load_divisions", lambda seasons: pd.DataFrame())
    monkeypatch.setattr(benchmark_sources, "date", _Day)
    load, score = benchmark_sources._nhl_players_live()
    return load, score, asked


def test_nhl_live_asks_for_the_playoffs_and_pays_them(monkeypatch):
    from whul.sources import nhl as source

    load, score, asked = _nhl(monkeypatch, today=(2027, 5, 10))
    out = score(load([2027])).set_index("player")

    assert source.GAME_TYPE_PLAYOFFS in asked
    depth = out.loc["Depth"]
    assert depth["postseason_games"] == 2
    # One goal is 3 points, over the eight games Edmonton played -- the most
    # any of its skaters did -- at 8.4 club games.
    assert depth["bonus_detail"][0]["team_games"] == 8.0
    assert depth["postseason_pending"] == pytest.approx(3.0 / 8 * 8.4)
    assert depth["regular_games"] == 80
    assert depth["team"] == "EDM", "his club survives for the heading"


def test_nhl_live_does_not_ask_for_playoffs_in_october(monkeypatch):
    from whul.sources import nhl as source

    load, score, asked = _nhl(monkeypatch, today=(2026, 10, 15))
    out = score(load([2027]))

    assert source.GAME_TYPE_PLAYOFFS not in asked
    assert (out["postseason_pending"] == 0).all()
    assert set(out["player"]) == {"Depth", "Captain"}
