"""The best-game calibration: who is measured, and how each game is scored.

The Stats API answers from GitHub Actions and not from here, so the season
lines and game logs are stubbed in the shape `probe mlb-gamelog` confirmed.
"""

import math

import pandas as pd
import pytest

from whul import best_game_calibration as calibration
from whul.scoring import mlb as scoring

SCALE = calibration.Scale(
    divisor={"MLB_Batter": 100.0, "MLB_Pitcher": 100.0,
             "NFL_QB": 100.0, "NFL_RB": 100.0, "NFL_WR": 100.0, "NFL_TE": 100.0},
    per_season={"MLB_Batter": 2, "MLB_Pitcher": 2,
                "NFL_QB": 2, "NFL_RB": 2, "NFL_WR": 2, "NFL_TE": 2},
)


def hitter(pid, name, games, pa, ab, h, hr):
    return {"player_id": pid, "player": name, "season": 2025,
            "gamesPlayed": games, "plateAppearances": pa, "atBats": ab,
            "hits": h, "doubles": 0, "triples": 0, "homeRuns": hr,
            "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 0,
            "caughtStealing": 0}


def arm(pid, name, games, starts, ip, so, sv=0):
    return {"player_id": pid, "player": name, "season": 2025,
            "gamesPitched": games, "gamesStarted": starts,
            "inningsPitched": ip, "strikeOuts": so, "hits": 0,
            "baseOnBalls": 0, "hitByPitch": 0, "homeRuns": 0,
            "saves": sv, "holds": 0}


HITTING = pd.DataFrame([
    hitter(1, "Slugger", 150, 600, 550, 180, 40),
    hitter(2, "Regular", 150, 600, 550, 150, 10),
    hitter(3, "Cameo", 10, 30, 28, 20, 5),          # under the games floor
    hitter(9, "Two-Way", 140, 550, 500, 140, 30),
])
PITCHING = pd.DataFrame([
    arm(4, "Ace", 32, 32, "200.0", 220),
    arm(5, "Swing", 30, 16, "110.1", 100),          # half his games started
    arm(6, "Closer", 65, 0, "65.0", 80, sv=40),
    arm(7, "Setup", 70, 0, "70.0", 60),
    arm(9, "Two-Way", 20, 20, "120.0", 150),
])


def lines(season, group="hitting"):
    return HITTING if group == "hitting" else PITCHING


def test_starters_and_relievers_are_ranked_each_against_their_own_kind():
    """Ranked together by points per game, a starter's six innings would push
    every closer out of the population."""
    subjects = calibration.mlb_population(2025, SCALE, loader=lines)
    by_group = {s.name: s.group for s in subjects}

    assert by_group["Ace"] == "Starter" and by_group["Swing"] == "Starter"
    assert by_group["Closer"] == "Reliever" and by_group["Setup"] == "Reliever"


def test_a_player_under_the_games_floor_is_not_in_the_population():
    names = {s.name for s in calibration.mlb_population(2025, SCALE, loader=lines)}
    assert "Cameo" not in names


def test_a_two_way_player_is_calibrated_once_in_his_majority_role():
    """Counted in two groups, his combined games would be in both."""
    subjects = calibration.mlb_population(2025, SCALE, loader=lines)
    his = [s for s in subjects if s.name == "Two-Way"]

    assert len(his) == 1
    assert his[0].group == "Batter" and his[0].two_way


def log_row(pk, date, **stat):
    return {"game_pk": pk, "date": date, "game_number": 1,
            "player": "x", "season": 2025, **stat}


def test_a_two_way_game_is_split_by_the_role_that_led_it(monkeypatch):
    """He homered and threw six scoreless: both lines, one game, the larger
    in full and the other at half."""
    subject = calibration.Subject("9", "Two-Way", 2025, "Batter", two_way=True)
    bat = {"atBats": 4, "hits": 1, "doubles": 0, "triples": 0, "homeRuns": 1,
           "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 0, "caughtStealing": 0}
    pitch = {"inningsPitched": "6.0", "strikeOuts": 9, "hits": 0,
             "baseOnBalls": 0, "hitByPitch": 0, "homeRuns": 0, "saves": 0,
             "holds": 0, "gamesStarted": 1}

    def logs(pid, season, group):
        if group == "hitting":
            return pd.DataFrame([log_row(1, "2025-06-01", **bat),
                                 log_row(2, "2025-06-02", **bat)])
        return pd.DataFrame([log_row(1, "2025-06-01", **pitch)])

    games = calibration.mlb_games(subject, SCALE, log_loader=logs).set_index("game_pk")

    batting = (4 * scoring.BATTER_WEIGHTS["ab"] + scoring.BATTER_WEIGHTS["h"]
               + scoring.BATTER_WEIGHTS["hr"])
    pitching = 6 * scoring.PITCHER_WEIGHTS["ip"] + 9 * scoring.PITCHER_WEIGHTS["so"]
    assert games.loc[1, "role"] == "start"
    assert games.loc[1, "score"] == pytest.approx(pitching + 0.5 * batting)
    # The day he only batted is batting alone, not half of it.
    assert games.loc[2, "role"] == "bat"
    assert games.loc[2, "score"] == pytest.approx(batting)


def test_a_pitchers_game_says_whether_it_was_a_start():
    subject = calibration.Subject("5", "Swing", 2025, "Starter")
    base = {"inningsPitched": "1.0", "strikeOuts": 1, "hits": 0, "baseOnBalls": 0,
            "hitByPitch": 0, "homeRuns": 0, "saves": 0, "holds": 0}

    def logs(pid, season, group):
        assert group == "pitching", "a pitcher's hitting log was fetched"
        return pd.DataFrame([log_row(1, "2025-06-01", gamesStarted=1, **base),
                             log_row(2, "2025-06-07", gamesStarted=0, **base)])

    games = calibration.mlb_games(subject, SCALE, log_loader=logs).set_index("game_pk")
    assert games.loc[1, "role"] == "start" and games.loc[2, "role"] == "relief"


def test_equalising_k_interpolates_between_the_ks_measured():
    means = {1: 2.0, 2: 4.0, 4: 8.0}
    assert calibration.equalising_k(means, 6.0) == pytest.approx(3.0)
    assert calibration.equalising_k(means, 1.0) == pytest.approx(0.5)
    assert math.isnan(calibration.equalising_k(means, 9.0))


def _run(monkeypatch, log_loader):

    monkeypatch.setattr(calibration, "frozen_scale", lambda store, label: SCALE)
    nfl = pd.DataFrame([
        {"season": 2025, "season_type": "REG", "player_id": p, "player_display_name": p,
         "position": "WR", "week": w, "receiving_yards": yds, "receptions": 5}
        for p, weeks in (("A", (100, 50, 20)), ("B", (80, 60, 10)))
        for w, yds in enumerate(weeks, start=1)
    ])
    return calibration.calibrate(
        store=None, seasons=(2025,), verbose=False,
        nfl_loader=lambda seasons: nfl, line_loader=lines, log_loader=log_loader)


def _logs(pid, season, group):
    bat = {"atBats": 4, "hits": 2, "doubles": 0, "triples": 0, "homeRuns": 1,
           "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 0, "caughtStealing": 0}
    pitch = {"inningsPitched": "5.0", "strikeOuts": 6, "hits": 2, "baseOnBalls": 1,
             "hitByPitch": 0, "homeRuns": 0, "saves": 0, "holds": 0}
    if group == "hitting":
        return pd.DataFrame([log_row(10 * int(pid) + i, f"2025-06-0{i + 1}", **bat)
                             for i in range(3)])
    started = {"4": [1, 1], "5": [1, 0], "6": [0, 0], "7": [0, 0], "9": [1, 1]}[str(pid)]
    return pd.DataFrame([log_row(10 * int(pid) + i, f"2025-07-0{i + 1}",
                                 gamesStarted=s, **pitch)
                         for i, s in enumerate(started)])


def test_the_run_measures_every_group_and_reports_the_swingmen(monkeypatch):
    report = _run(monkeypatch, _logs)

    assert set(report.mlb["group"]) == {"Batter", "Starter", "Reliever"}
    assert len(report.nfl) == 2
    assert list(report.swing["name"]) == ["Swing"], "the swingman was not found"
    text = calibration.render(report)
    assert "Mean best-k" in text and "k at which each matches the NFL" in text
    assert "Swingmen" in text and "Two-Way 2025" in text


def test_a_player_whose_log_cannot_be_read_is_reported_not_fatal(monkeypatch):
    def flaky(pid, season, group):
        if str(pid) == "6":
            raise RuntimeError("404")
        return _logs(pid, season, group)

    report = _run(monkeypatch, flaky)
    assert any("Closer" in line and "404" in line for line in report.failures)
    assert "Setup" in set(report.mlb["name"])
