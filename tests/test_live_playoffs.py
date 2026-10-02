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


# --- the benchmark pool's thresholds are not for live scoring ----------------

def test_a_skater_netting_zero_is_still_scored_live():
    """Auston Matthews: +0.5, then -0.5. Dropped for a season of 0.0, his slot
    went on counting the 0.1 his first game made and his page went blank."""
    from whul.scoring import nhl

    raw = pd.DataFrame([
        _skater("Matthews", "TOR", 2, 0, shots=4, plusMinus=-2),
        _skater("Minus", "TOR", 2, 0, plusMinus=-3),
    ]).assign(_phase="reg")
    live = nhl.score_skater_phases(raw, postseason=True, pool=False)
    pool = nhl.score_skater_phases(raw, postseason=False)

    assert set(live["player"]) == {"Matthews", "Minus"}
    assert live.set_index("player").loc["Minus", "total_points"] < 0
    assert pool.empty, "the benchmark pool still keeps positive seasons only"


def test_an_nba_player_is_scored_from_his_first_game_live(monkeypatch):
    """The pool wants fifteen games and a hundred points; live, those kept
    every rostered player off the standings until mid-November."""
    from whul.sources import espn

    box = pd.DataFrame([_nba_game("r1", points=12)])
    monkeypatch.setattr(espn, "load_nba_player_box", lambda seasons: box)

    load, score = benchmark_sources._nba_players_live()
    live = score(load([2027]))
    load, score = benchmark_sources._nba_players()
    bench = score(load([2027]))

    assert list(live["player"]) == ["Test Player"]
    assert bench.empty


def test_an_nfl_player_below_zero_is_scored_live(monkeypatch):
    from whul.sources import nflverse

    weekly = pd.DataFrame([{**_nfl_week(1, yards=0), "interceptions": 2}])
    monkeypatch.setattr(nflverse, "load_player_stats", lambda seasons: weekly)

    load, score = benchmark_sources._nfl_players_live()
    live = score(load([2026]))

    assert len(live) == 1
    assert live.iloc[0]["total_points"] < 0


def test_nhl_clubs_are_scored_live_when_the_standings_cannot_be_read(monkeypatch):
    """A title is not awarded until April, so a night without standings costs
    nothing live -- and refusing cost every rostered NHL club its score. The
    benchmark still refuses: there a missing title sets the bar low."""
    from whul.sources import nhl as source

    teams = pd.DataFrame([{"season": 2027, "teamFullName": "Toronto Maple Leafs",
                           "gamesPlayed": 3, "wins": 2, "otLosses": 0,
                           "goalsFor": 10, "goalsAgainst": 6}])
    monkeypatch.setattr(source, "load_teams", lambda seasons, kind=None: (
        teams if kind != source.GAME_TYPE_PLAYOFFS else pd.DataFrame()))
    monkeypatch.setattr(source, "load_divisions", lambda seasons: pd.DataFrame())

    load, score = benchmark_sources.SOURCES["nhl-teams"].live()
    live = score(load([2027]))
    assert list(live["team"]) == ["Toronto Maple Leafs"]
    assert live.iloc[0]["total_points"] > 0

    load, score = benchmark_sources._nhl_teams()
    with pytest.raises(RuntimeError, match="divisions could not be read"):
        score(load([2027]))
