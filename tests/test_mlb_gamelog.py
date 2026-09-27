"""MLB game logs: can a best game be scored for baseball at all?

The season-to-date feed differences into days, and a day is not a game: a
doubleheader is two, a snapshot the run did not take is a day nobody can get
back, and the first weeks of a season were never snapshotted at all. A game
log is keyed by the game. These pin how it is read and what the probe checks
-- against the documented payload shape, because the host answers from GitHub
Actions and not from the sandbox this was written in.
"""

import pandas as pd
import pytest

from whul.scoring import mlb as scoring
from whul.sources import mlb


def split(date, pk, stat, number=1, player=(660271, "Shohei Ohtani")):
    return {
        "season": "2025", "date": date, "isHome": True,
        "game": {"gamePk": pk, "gameNumber": number},
        "team": {"name": "Los Angeles Dodgers"},
        "opponent": {"name": "Chicago Cubs"},
        "player": {"id": player[0], "fullName": player[1]},
        "stat": stat,
    }


def payload(*splits):
    return {"stats": [{"type": {"displayName": "gameLog"}, "splits": list(splits)}]}


HOMER = {"atBats": 4, "hits": 1, "doubles": 0, "triples": 0, "homeRuns": 1,
         "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 0, "caughtStealing": 0}
WALKS = {"atBats": 2, "hits": 0, "doubles": 0, "triples": 0, "homeRuns": 0,
         "baseOnBalls": 2, "hitByPitch": 0, "stolenBases": 1, "caughtStealing": 0}
START = {"inningsPitched": "6.1", "strikeOuts": 8, "hits": 4, "baseOnBalls": 1,
         "hitByPitch": 0, "homeRuns": 0, "saves": 0, "holds": 0, "gamesStarted": 1}
RELIEF = {"inningsPitched": "0.2", "strikeOuts": 1, "hits": 0, "baseOnBalls": 0,
          "hitByPitch": 0, "homeRuns": 0, "saves": 1, "holds": 0, "gamesStarted": 0}


def test_a_game_log_is_one_row_a_game_keyed_by_the_game(monkeypatch):
    monkeypatch.setattr(mlb, "_get", lambda url, params, cache_key=None: payload(
        split("2025-06-01", 101, HOMER), split("2025-06-02", 102, WALKS)))

    log = mlb.load_game_log(660271, 2025, "hitting")

    assert list(log["game_pk"]) == [101, 102]
    assert list(log["date"]) == ["2025-06-01", "2025-06-02"]
    assert log.loc[0, "homeRuns"] == 1 and log.loc[1, "baseOnBalls"] == 2


def test_the_game_log_is_asked_for_by_player_and_season(monkeypatch):
    asked = {}

    def note(url, params, cache_key=None):
        asked.update(url=url, params=params, cache_key=cache_key)
        return payload()

    monkeypatch.setattr(mlb, "_get", note)
    mlb.load_game_log(660271, 2025, "pitching")

    assert asked["url"].endswith("/people/660271/stats")
    assert asked["params"]["stats"] == "gameLog"
    assert asked["params"]["group"] == "pitching"
    # A season in progress changes after every game, so a cache keyed on the
    # season would freeze it at the first pull.
    assert asked["cache_key"] is None


def test_a_batters_game_is_scored_by_the_leagues_own_weights():
    """Four at bats and a home run: the home run is also a hit."""
    log = pd.DataFrame([dict(HOMER, game_pk=101, date="2025-06-01",
                             player="Shohei Ohtani", season=2025)])
    points = mlb.game_points(log, "hitting")

    expected = (4 * scoring.BATTER_WEIGHTS["ab"] + 1 * scoring.BATTER_WEIGHTS["h"]
                + 1 * scoring.BATTER_WEIGHTS["hr"])
    assert points.loc[0, "points"] == pytest.approx(expected)


def test_a_pitchers_innings_are_counted_in_thirds():
    """6.1 is six and a third innings, not six point one."""
    log = pd.DataFrame([dict(START, game_pk=201, date="2025-06-01",
                             player="Starter", season=2025)])
    points = mlb.game_points(log, "pitching")

    w = scoring.PITCHER_WEIGHTS
    expected = (19 / 3) * w["ip"] + 8 * w["so"] + 4 * w["h"] + 1 * w["bb"]
    assert points.loc[0, "points"] == pytest.approx(expected)


def test_a_game_is_scored_on_counting_stats_alone():
    """WAR is a season's run value with no share in any one game. A game row
    that happened to carry it must not score it."""
    plain = pd.DataFrame([dict(START, game_pk=201, date="2025-06-01",
                               player="Starter", season=2025)])
    with_war = plain.assign(war=4.0)

    assert (mlb.game_points(with_war, "pitching")["points"].iloc[0]
            == pytest.approx(mlb.game_points(plain, "pitching")["points"].iloc[0]))


def test_games_that_add_up_to_the_season_reconcile():
    log = pd.DataFrame([START, RELIEF])
    line = pd.Series({"inningsPitched": "7.0", "strikeOuts": 9, "hits": 4,
                      "baseOnBalls": 1, "hitByPitch": 0, "homeRuns": 0,
                      "saves": 1, "holds": 0})
    assert mlb._reconcile(log, line, "pitching") == []


def test_a_game_missing_from_the_log_is_named():
    """A best game read out of an incomplete record is a best game that may
    not be the best one."""
    log = pd.DataFrame([HOMER])
    line = pd.Series(dict(HOMER, atBats=6, baseOnBalls=2, stolenBases=1))

    found = mlb._reconcile(log, line, "hitting")
    assert any(m.startswith("atBats") for m in found)
    assert any(m.startswith("baseOnBalls") for m in found)


# --- the probe ----------------------------------------------------------------

def _season_lines():
    hitting = pd.DataFrame([
        {"player": "Busy Batter", "player_id": 1, "plateAppearances": 700,
         "gamesPlayed": 2, **{k: v + w for (k, v), w in
                               zip(HOMER.items(), WALKS.values())}},
        {"player": "Bench Bat", "player_id": 2, "plateAppearances": 50,
         "gamesPlayed": 20, **HOMER},
    ])
    pitching = pd.DataFrame([
        {"player": "Ace", "player_id": 3, "gamesStarted": 1, "gamesPitched": 1,
         **{k: v for k, v in START.items() if k != "gamesStarted"}},
        {"player": "Closer", "player_id": 4, "gamesStarted": 0, "gamesPitched": 1,
         **{k: v for k, v in RELIEF.items() if k != "gamesStarted"}},
    ])
    return hitting, pitching


def _stub(monkeypatch, logs):
    hitting, pitching = _season_lines()
    monkeypatch.setattr(mlb, "load_stats_api_players",
                        lambda season, group="hitting", **k:
                        hitting if group == "hitting" else pitching)
    monkeypatch.setattr(mlb, "load_game_log",
                        lambda pid, season, group="hitting", **k:
                        pd.DataFrame(logs[pid]))


def _row(stat, pk, date, number=1):
    return dict(stat, game_pk=pk, date=date, game_number=number,
                player="x", season=2025)


def test_the_probe_reads_the_start_flag_and_a_doubleheader(monkeypatch):
    _stub(monkeypatch, {
        1: [_row(HOMER, 11, "2025-07-04", 1), _row(WALKS, 12, "2025-07-04", 2)],
        3: [_row(START, 31, "2025-07-04")],
        4: [_row(RELIEF, 41, "2025-07-05")],
    })
    report = mlb.probe_game_logs(2025)
    stages = report["stages"]

    assert all(stage["ok"] for stage in stages.values()), stages
    assert "two games" in stages["batter"]["doubleheaders"]
    assert stages["starter"]["start flag"].startswith("yes")
    assert "values [1]" in stages["starter"]["start flag"]
    assert "values [0]" in stages["reliever"]["start flag"]
    assert stages["batter"]["sums to season"] == "yes, every field"


def test_the_probe_fails_a_log_that_does_not_add_up(monkeypatch):
    """The batter's season line has two games; his log has one."""
    _stub(monkeypatch, {
        1: [_row(HOMER, 11, "2025-07-04")],
        3: [_row(START, 31, "2025-07-04")],
        4: [_row(RELIEF, 41, "2025-07-05")],
    })
    stages = mlb.probe_game_logs(2025)["stages"]

    assert not stages["batter"]["ok"]
    assert stages["batter"]["sums to season"] == "NO"


def test_the_probe_says_when_a_game_does_not_say_who_started(monkeypatch):
    """Separate k for starters and relievers needs the game itself to say so.
    If it does not, that is the finding, not a detail."""
    no_flag = {k: v for k, v in START.items() if k != "gamesStarted"}
    _stub(monkeypatch, {
        1: [_row(HOMER, 11, "2025-07-04", 1), _row(WALKS, 12, "2025-07-04", 2)],
        3: [_row(no_flag, 31, "2025-07-04")],
        4: [_row(RELIEF, 41, "2025-07-05")],
    })
    stages = mlb.probe_game_logs(2025)["stages"]

    assert stages["starter"]["start flag"].startswith("NO")


def test_the_probe_reports_a_refused_log_rather_than_raising(monkeypatch):
    hitting, pitching = _season_lines()
    monkeypatch.setattr(mlb, "load_stats_api_players",
                        lambda season, group="hitting", **k:
                        hitting if group == "hitting" else pitching)

    def refuse(*a, **k):
        raise RuntimeError("403")

    monkeypatch.setattr(mlb, "load_game_log", refuse)
    stages = mlb.probe_game_logs(2025)["stages"]

    assert not stages["batter"]["ok"] and "403" in stages["batter"]["error"]
