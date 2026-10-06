"""FotMob match details, read player by player, and scored.

The fixtures are six real matches FotMob served to the probe workflow, cut
down to the sections the reader uses (``tests/data/fotmob``).
"""

import json
from pathlib import Path

import pandas as pd
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


def test_a_match_still_being_played_is_neither_read_nor_kept(tmp_path):
    """Kept only once it is over: a lineup read at half time would be stored
    as the match and never asked about again."""
    path = next(DATA.glob("epl-*.json"))
    live = json.loads(path.read_text())
    live["header"]["status"]["finished"] = False
    live["general"]["finished"] = False

    class Live(fotmob.Client):
        def get(self, path, **params):
            return live

    assert fotmob.cached_lines("4506312", Live(pause=0, waits=()), cache=tmp_path) is None
    assert not list(tmp_path.rglob("*.json"))


def test_a_walk_reads_each_finished_match_once_and_tags_its_competition(tmp_path):
    details = json.loads(next(DATA.glob("epl-*.json")).read_text())

    class Day(fotmob.Client):
        def get(self, path, **params):
            self.sent += 1
            if path == "/matches":
                return {"leagues": [{"id": 47, "ccode": "ENG", "name": "Premier League",
                                     "matches": [{"id": "4506312",
                                                  "status": {"finished": True}},
                                                 {"id": "999", "status": {"finished": False}}]}]}
            return details

    from datetime import date

    client = Day(pause=0, waits=())
    found = fotmob.walk(client, date(2024, 9, 21), date(2024, 9, 22), ("epl",),
                        today=date(2024, 10, 1), cache=tmp_path)
    assert found.matches == 1 and len(found.lines) == 32
    assert {line["competition_key"] for line in found.lines} == {"epl"}
    sent = client.sent
    again = fotmob.walk(client, date(2024, 9, 21), date(2024, 9, 22), ("epl",),
                        today=date(2024, 10, 1), cache=tmp_path)
    # Both days settled and kept, the match kept: nothing asked twice.
    assert client.sent == sent and again.matches == 1


# --- the soccer-players source ---------------------------------------------------

def _fake_days(monkeypatch, tmp_path, days, fail=False):
    """FotMob as a day list and match details from the fixtures."""
    details = {p.stem.rsplit("-", 1)[1]: json.loads(p.read_text()) for p in DATA.glob("*.json")}

    class Fake(fotmob.Client):
        def get(self, path, **params):
            self.sent += 1
            if fail:
                self.failed_in_a_row = self.give_up_after
                return None
            if path == "/matches":
                leagues = []
                for key, mid in days.get(params["date"], []):
                    wanted, country, name = fotmob.COMPETITIONS[key]
                    leagues.append({"id": wanted, "ccode": country, "name": name,
                                    "matches": [{"id": mid, "status": {"finished": True}}]})
                return {"leagues": leagues}
            return details.get(str(params.get("matchId")))

    monkeypatch.setattr(fotmob, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(fotmob, "Client", lambda: Fake(pause=0, waits=()))


def test_the_benchmark_pull_files_clubs_under_their_league_and_season(monkeypatch, tmp_path):
    from whul import benchmark_sources as bs

    _fake_days(monkeypatch, tmp_path, {"20240921": [("epl", "4506312"), ("mls", "4386994")]})
    load, score = bs._fotmob_players()
    raw = load([2025])
    # The MLS match is 2024's, not the 2024-25 season asked for.
    assert set(raw["league"]) == {"Premier League"} and set(raw["season"]) == {2025}
    scored = score(raw)
    best = scored.sort_values("total_points", ascending=False).iloc[0]
    assert best["player"] == "Nicolas Jackson"
    # Domestic football only: the benchmark is drawn from it.
    assert scored["postseason_bonus"].eq(0).all()


def test_a_walk_fotmob_stops_answering_raises_rather_than_scoring_short(monkeypatch, tmp_path):
    from whul import benchmark_sources as bs

    _fake_days(monkeypatch, tmp_path, {}, fail=True)
    load, _ = bs._fotmob_players()
    with pytest.raises(RuntimeError, match="stopped answering"):
        load([2025])


def test_a_rostered_player_is_scored_at_the_position_his_line_holds(monkeypatch, tmp_path):
    """FotMob files Jackson a forward; were the league to hold him a
    defender, his two goals would be worth six each."""
    from whul import benchmark_sources as bs
    from whul.store import open_store

    store = open_store(":memory:")
    store.upsert("assets", [{"asset_id": "nj", "asset_type": "Player",
                             "display_name": "Nicolas Jackson", "league": "Premier League",
                             "role": "", "norm_key": "Premier League", "active": 1,
                             "created_at": "2024-08-01"}], keys=("asset_id",))
    store.upsert("raw_stats", [{"asset_id": "nj", "league": "Premier League",
                                "season": "2024-25", "as_of": "2024-09-20",
                                "source": "soccer-players", "phase": "regular",
                                "stats": json.dumps({"position": "D"}),
                                "fetched_at": "x"}],
                 keys=("asset_id", "season", "as_of", "source", "phase"))
    frame = pd.DataFrame([{"league": "Premier League", "player": "Nicolas Jackson"},
                          {"league": "Premier League", "player": "Cole Palmer"}])
    assert bs._roster_positions(store, frame) == ["D", ""]
