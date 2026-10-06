"""FotMob match details, read player by player, and scored.

The fixtures are six real matches FotMob served to the probe workflow, cut
down to the sections the reader uses (``tests/data/fotmob``).
"""

import json
from pathlib import Path

import pytest

from whul.scoring import soccer_match as sm
from whul.sources import fotmob

DATA = Path(__file__).parent / "data" / "fotmob"


def lines_of(name: str) -> dict[str, dict]:
    path = next(DATA.glob(f"{name}-*.json"))
    return {line["player"]: line for line in fotmob.match_lines(json.loads(path.read_text()))}


def test_a_league_match_reads_every_player_who_played():
    lines = lines_of("epl-2024-09-21")
    assert len(lines) == 32
    jackson = lines["Nicolas Jackson"]
    assert (jackson["team"], jackson["position"], jackson["minutes"]) == ("Chelsea", "F", 64)
    assert (jackson["goals"], jackson["assists"]) == (2, 1)
    assert jackson["goal_xg"] == [0.2222, 0.4427]
    assert jackson["potm"] and jackson["rating"] == 9.1


def test_goals_conceded_are_counted_only_while_a_player_was_on():
    """West Ham lost 3-0 (4', 18', 47'). Rodriguez went off at 38 and Soucek
    came on for him; the starters who lasted were there for all three."""
    lines = lines_of("epl-2024-09-21")
    assert lines["Guido Rodríguez"]["conceded_on"] == 2
    assert lines["Tomás Souček"]["conceded_on"] == 1
    assert lines["Max Kilman"]["conceded_on"] == 3
    assert lines["Cole Palmer"]["conceded_on"] == 0


def test_a_sending_off_ends_a_players_match_and_counts_once():
    """Dalot's second yellow at 61; Arsenal's goal came after it."""
    dalot = lines_of("facup-2025-01-12")["Diogo Dalot"]
    assert (dalot["yellow"], dalot["red"]) == (1, 1)
    assert dalot["conceded_on"] == 0 and dalot["minutes"] == 61


def test_a_shootout_is_neither_goals_nor_goals_conceded():
    """Arsenal and United drew 1-1 and went to penalties; Guingamp and Sochaux
    drew 2-2. Nobody conceded more than the match's own goals."""
    for name, most in (("facup-2025-01-12", 1), ("coupedefrance-2025-01-14", 2)):
        lines = lines_of(name).values()
        assert max(line["conceded_on"] for line in lines) == most
        assert all(line["penalty_goals"] == 0 for line in lines)


def test_nwsl_has_player_lines_but_no_shot_map():
    lines = lines_of("nwsl-2024-09-21").values()
    assert lines and not any(line["has_shotmap"] for line in lines)
    assert all(line["goal_xg"] == [] for line in lines)
    assert any(line["rating"] for line in lines)


def test_an_own_goal_counts_against_the_scorers_own_side():
    details = {
        "general": {"matchId": "1", "matchTimeUTCDate": "2025-01-01T15:00:00Z",
                    "homeTeam": {"id": 1, "name": "Home"}, "awayTeam": {"id": 2, "name": "Away"}},
        "header": {"status": {"finished": True}},
        "content": {
            "lineup": {"homeTeam": {"id": 1, "name": "Home",
                                    "starters": [{"id": 10}], "subs": []},
                       "awayTeam": {"id": 2, "name": "Away",
                                    "starters": [{"id": 20}], "subs": []}},
            "matchFacts": {"events": {"events": [
                {"type": "Goal", "time": 30, "playerId": 10, "isHome": True, "ownGoal": True}]}},
            "playerStats": {
                "10": {"name": "Unlucky", "teamId": 1, "usualPosition": 1, "stats": [
                    {"stats": {"Minutes played": {"key": "minutes_played", "stat": {"value": 90}}}}]},
                "20": {"name": "Grateful", "teamId": 2, "usualPosition": 1, "stats": [
                    {"stats": {"Minutes played": {"key": "minutes_played", "stat": {"value": 90}}}}]},
            },
        },
    }
    lines = {line["player"]: line for line in fotmob.match_lines(details)}
    assert lines["Unlucky"]["own_goals"] == 1 and lines["Unlucky"]["conceded_on"] == 1
    assert lines["Grateful"]["conceded_on"] == 0


# --- scoring --------------------------------------------------------------------

def test_a_forwards_brace_is_priced_term_by_term():
    jackson = lines_of("epl-2024-09-21")["Nicolas Jackson"]
    parts = sm.components(jackson)
    assert parts["appearance"] == 2.0
    assert parts["goals"] == 8.0
    assert parts["highlight"] == pytest.approx(2 * ((1 - 0.2222) + (1 - 0.4427)), abs=1e-3)
    assert parts["assists"] == 3.0
    assert parts["chances_created"] == 0.5        # two chances, one the assist
    assert parts["clean_sheet"] == 0.0             # forwards get none
    assert parts["rating"] == 1.0 and parts["player_of_match"] == 1.0
    assert sm.match_points(jackson) == pytest.approx(sum(parts.values()))


def test_defenders_pay_for_goals_conceded_after_the_first():
    kilman = lines_of("epl-2024-09-21")["Max Kilman"]
    assert sm.components(kilman)["conceded"] == -1.0     # three conceded: two after the first
    assert sm.components(kilman)["clean_sheet"] == 0.0


def test_a_clean_sheet_needs_sixty_minutes():
    line = {"position": "D", "minutes": 59, "conceded_on": 0}
    assert sm.components(line)["clean_sheet"] == 0.0
    assert sm.components({**line, "minutes": 60})["clean_sheet"] == 2.0
    assert sm.components({**line, "minutes": 60, "position": "M"})["clean_sheet"] == 1.0


def test_goalkeepers_and_unused_substitutes_score_nothing():
    assert sm.components({"position": "G", "minutes": 90}) == {}
    assert sm.components({"position": "D", "minutes": 0}) == {}


def test_a_figure_fotmob_left_off_counts_as_none():
    line = {"position": "M", "minutes": 70, "conceded_on": 1, "tackles": None}
    assert sm.components(line)["tackles"] == 0.0


# --- the season study ----------------------------------------------------------

class FakeClient(fotmob.Client):
    """Serves a season page listing the fixture matches, and their details."""

    def __init__(self, matches: dict[str, dict]):
        super().__init__(pause=0, waits=())
        self.matches = matches

    def get(self, path, **params):
        self.sent += 1
        if path == "/leagues":
            return {"fixtures": {"allMatches": [
                {"id": mid, "status": {"finished": True, "utcTime": "2024-09-21T14:00:00Z"},
                 "home": {"name": "A"}, "away": {"name": "B"}} for mid in self.matches]}}
        if path == "/matchDetails":
            return self.matches.get(str(params["matchId"]))
        return None


def test_a_study_writes_each_competitions_lines_and_leaders(tmp_path, monkeypatch):
    from whul import soccer_study

    monkeypatch.setattr(fotmob, "CACHE", tmp_path / "cache")
    details = {p.stem.rsplit("-", 1)[1]: json.loads(p.read_text())
               for p in DATA.glob("epl-*.json")}
    client = FakeClient(details)
    assert soccer_study.run(["epl"], 2024, tmp_path / "out", client=client) == 0
    import pandas as pd

    frame = pd.read_csv(tmp_path / "out" / "lines-epl-2024.csv.gz")
    assert len(frame) == 32 and "points" in frame and "pts_highlight" in frame
    report = (tmp_path / "out" / "study.txt").read_text()
    assert "1 of 1 finished matches read" in report and "Nicolas Jackson" in report
    # Read once: a second run is answered from the cache.
    sent = client.sent
    soccer_study.run(["epl"], 2024, tmp_path / "again", client=client)
    assert client.sent == sent + 1          # the season's list, and nothing else


def test_a_client_stops_after_failures_in_a_row():
    client = fotmob.Client(pause=0, waits=(), give_up_after=3)

    def refuse(*args, **kwargs):
        import requests
        raise requests.ConnectionError("refused")

    client.session.get = refuse
    for _ in range(5):
        assert client.get("/matches", date="20240921") is None
    assert client.stopped and client.sent == 3
