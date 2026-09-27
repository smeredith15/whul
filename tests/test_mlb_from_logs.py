"""Rostered MLB lines, summed from the games in their logs.

The date-range totals were found days behind, so a rostered player's line is
rebuilt from his game log: every game from the league's start to the day
before the run.
"""

from datetime import date

import pandas as pd
import pytest

from whul.sources import mlb

SINCE, UNTIL = date(2026, 8, 15), date(2026, 9, 27)


def ranged():
    return pd.DataFrame([
        {"player": "José Ramírez", "player_id": 7, "season": 2026, "gamesPlayed": 2,
         "atBats": 8, "hits": 2, "homeRuns": 0, "avg": ".250"},
        {"player": "Nobody Rostered", "player_id": 9, "season": 2026, "gamesPlayed": 30,
         "atBats": 110, "hits": 30, "homeRuns": 5, "avg": ".273"},
    ])


def whole():
    return pd.DataFrame([
        {"player": "José Ramírez", "player_id": 7, "season": 2026, "gamesPlayed": 100},
        {"player": "Nobody Rostered", "player_id": 9, "season": 2026, "gamesPlayed": 140},
    ])


def game(pk, day, ab=4, h=1, hr=0):
    return {"game_pk": pk, "date": day, "game_number": 1, "season": 2026,
            "atBats": ab, "hits": h, "homeRuns": hr, "avg": ".250"}


def logs(pid, season, group):
    assert str(pid) == "7", "a player nobody holds had his log fetched"
    return pd.DataFrame([
        game(1, "2026-08-01", hr=3),          # before the league year
        game(2, "2026-09-05", h=2, hr=1),     # a doubleheader...
        game(3, "2026-09-05", h=1),           # ...is two games
        game(4, "2026-09-20", h=3, hr=1),
        game(5, "2026-09-27", h=4, hr=2),     # today: may not be over
    ])


def test_a_rostered_line_is_the_sum_of_his_games_in_the_window():
    rows, full = mlb.from_game_logs(ranged(), whole(), 2026, "hitting",
                                    ["Jose Ramirez"], SINCE, UNTIL, log_loader=logs)
    mine = rows.set_index("player_id").loc[7]
    assert mine["gamesPlayed"] == 3
    assert mine["atBats"] == 12 and mine["hits"] == 6 and mine["homeRuns"] == 2
    # Rates are not counts and are left as they were.
    assert mine["avg"] == ".250"
    # The season behind the run-value share is the log's too: four games
    # before today, whatever the cached whole-season line said.
    assert full.set_index("player_id").loc[7, "gamesPlayed"] == 4


def test_a_player_nobody_holds_keeps_the_date_range_line():
    rows, full = mlb.from_game_logs(ranged(), whole(), 2026, "hitting",
                                    ["Jose Ramirez"], SINCE, UNTIL, log_loader=logs)
    other = rows.set_index("player_id").loc[9]
    assert other["gamesPlayed"] == 30 and other["homeRuns"] == 5
    assert full.set_index("player_id").loc[9, "gamesPlayed"] == 140


def test_a_log_that_cannot_be_read_keeps_the_date_range_line():
    def broken(pid, season, group):
        raise RuntimeError("503")

    rows, _ = mlb.from_game_logs(ranged(), whole(), 2026, "hitting",
                                 ["Jose Ramirez"], SINCE, UNTIL, log_loader=broken)
    assert rows.set_index("player_id").loc[7, "gamesPlayed"] == 2


def test_innings_add_in_thirds():
    log = pd.DataFrame([{"game_pk": 1, "date": "2026-09-01", "inningsPitched": "6.2",
                         "strikeOuts": 7},
                        {"game_pk": 2, "date": "2026-09-06", "inningsPitched": "5.1",
                         "strikeOuts": 5}])
    line = mlb.season_from_log(log, "pitching")
    assert line["inningsPitched"] == "12.0"
    assert line["strikeOuts"] == 12 and line["gamesPlayed"] == 2
    assert mlb.innings_to_float(line["inningsPitched"]) == pytest.approx(12.0)


def test_the_live_loader_passes_the_roster_through(monkeypatch):
    from whul import benchmark_sources

    seen = {}

    def fake(years, since=None, names=(), **kw):
        seen.setdefault("names", names)
        return pd.DataFrame()

    monkeypatch.setattr(mlb, "load_batters", fake)
    monkeypatch.setattr(mlb, "load_pitchers", fake)
    monkeypatch.setattr(benchmark_sources, "_october_is_possible", lambda y: False)
    load, _ = benchmark_sources._mlb_players_live()
    load([2026], ["Jose Ramirez"])
    assert seen["names"] == ["Jose Ramirez"]
    assert benchmark_sources.SOURCES["mlb"].roster_scoped
