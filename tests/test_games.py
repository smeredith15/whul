"""Every rostered player's games: where each sport's come from, and what a
game is worth.

The feeds are stubbed in the shapes they were found in: nflverse's weekly rows
and schedule as the ledger keeps them, the Stats API's game log, the NHL's
game log, and an ESPN match summary's lineups.
"""

import json
from datetime import date

import pandas as pd
import pytest

from whul import games, pipeline
from whul.scoring import soccer as soccer_scoring
from whul.store import benchmarks as bm
from whul.store import open_store, rosters

SEASON = "2026-27"
DAY = "2026-09-27"


def history():
    rows = []
    for season in (2023, 2024, 2025):
        rows += [
            {"league": "NFL", "role": "QB", "total_points": 400},
            {"league": "MLB", "role": "Batter", "total_points": 1500},
            {"league": "MLB", "role": "Pitcher", "total_points": 1200},
            {"league": "NHL", "role": "Skater", "total_points": 500},
            {"league": "Premier League", "role": "F", "total_points": 200},
        ]
        for row in rows[-5:]:
            row["season"] = season
    return pd.DataFrame(rows)


@pytest.fixture
def store():
    store = open_store(":memory:")
    rosters.add_manager(store, "alice")
    rosters.create_slots(store, "alice", SEASON)
    bm.freeze(store, bm.save(store, bm.compute(history(), "Player", SEASON),
                             SEASON, version="v1"))
    return store


def hold(store, asset_id, category, league, role, line):
    store.upsert("assets", [{
        "asset_id": asset_id, "asset_type": "Player", "display_name": line["player"],
        "league": league, "role": role, "norm_key": league, "active": 1,
        "created_at": "2026-08-21"}], keys=("asset_id",))
    free = next(s for s in rosters.load_slots(store, SEASON, "alice")
                if s.category == category and s.asset_type == "Player"
                and not s.occupancies)
    rosters.assign(store, free.slot_id, asset_id, date(2026, 8, 21))
    store.upsert("raw_stats", [{
        "asset_id": asset_id, "league": league, "season": SEASON, "as_of": DAY,
        "source": league.lower(), "phase": "regular", "stats": json.dumps(line),
        "fetched_at": DAY}], keys=("asset_id", "season", "as_of", "source", "phase"))


def ledger(store, source, rows, key):
    store.upsert("feed_rows", [{
        "source": source, "row_key": key(r), "season": "", "payload": json.dumps(r),
        "first_seen": DAY, "last_seen": DAY} for r in rows], keys=("source", "row_key"))


def recorded(store, asset_id):
    return store.query("SELECT * FROM game_scores WHERE asset_id = ? ORDER BY date, game_key",
                       (asset_id,))


# --- NFL ------------------------------------------------------------------------

def nfl_week(week, game, yards, tds):
    return {"player_id": "00-1", "player_display_name": "Quarterback", "position": "QB",
            "season": 2026, "week": week, "season_type": "REG", "game_id": game,
            "opponent_team": "BUF", "passing_yards": yards, "passing_tds": tds}


def test_nfl_weeks_come_from_the_ledger_dated_by_the_schedule(store):
    hold(store, "qb", "NFL", "NFL", "QB",
         {"player": "Quarterback", "player_id": "00-1", "position": "QB",
          "regular_points": 0.04 * 500 + 4 * 5})
    ledger(store, "nfl", [nfl_week(1, "2026_01_A_B", 300, 3),
                          nfl_week(2, "2026_02_C_A", 200, 2)],
           key=lambda r: f"{r['week']}")
    ledger(store, "nfl-teams", [{"game_id": "2026_01_A_B", "gameday": "2026-09-13"},
                                {"game_id": "2026_02_C_A", "gameday": "2026-09-20"}],
           key=lambda r: r["game_id"])

    report = games.record(store, SEASON, DAY, verbose=False, sports=("NFL",))
    rows = recorded(store, "qb")
    divisor = games.divisors(store, SEASON)["NFL_QB"]

    assert report.problems == []
    assert list(rows["date"]) == ["2026-09-13", "2026-09-20"]
    assert rows["points"].iloc[0] == pytest.approx(0.04 * 300 + 4 * 3)
    assert rows["score"].iloc[0] == pytest.approx(100 * (0.04 * 300 + 12) / divisor)
    assert json.loads(rows["detail"].iloc[0]) == {"passing_yards": 300.0, "passing_tds": 3.0}


def test_nfl_weeks_that_do_not_add_up_to_the_season_are_named(store):
    hold(store, "qb", "NFL", "NFL", "QB",
         {"player": "Quarterback", "player_id": "00-1", "position": "QB",
          "regular_points": 99.0})
    ledger(store, "nfl", [nfl_week(1, "g1", 300, 3)], key=lambda r: "1")
    ledger(store, "nfl-teams", [{"game_id": "g1", "gameday": "2026-09-13"}],
           key=lambda r: r["game_id"])
    report = games.record(store, SEASON, DAY, verbose=False, sports=("NFL",))
    assert any("Quarterback" in p and "99.00" in p for p in report.problems)


# --- MLB ------------------------------------------------------------------------

def lines(season, group):
    return pd.DataFrame([{"player": "José Ramírez", "player_id": 7},
                         {"player": "Two Way", "player_id": 9}])


BAT = {"atBats": 4, "hits": 1, "doubles": 0, "triples": 0, "homeRuns": 1,
       "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 0, "caughtStealing": 0}


def log(rows):
    return pd.DataFrame([{"game_pk": pk, "date": d, "game_number": 1, "player": "x", "season": 2026,
                          "opponent": "Rivals", **stat} for pk, d, stat in rows])


def test_a_doubleheader_is_two_games_and_april_is_not_in_the_year(store):
    hold(store, "jr", "MLB", "MLB", "Batter",
         {"player": "Jose Ramirez", "role": "Batter", "games": 2,
          "season_lines": [{"ab": 8}]})

    def logs(pid, season, group):
        assert group == "hitting"
        if season != 2026:
            return log([])
        return log([(1, "2026-04-02", BAT), (2, "2026-09-05", BAT),
                    (3, "2026-09-05", BAT)])

    report = games.record(store, SEASON, DAY, verbose=False, sports=("MLB",),
                          loaders={"mlb_lines": lines, "mlb_log": logs})
    rows = recorded(store, "jr")
    assert report.problems == []
    assert list(rows["game_key"]) == ["2", "3"]
    assert set(rows["role"]) == {"bat"}
    assert json.loads(rows["detail"].iloc[0])["batting"]["homeRuns"] == 1.0


def test_a_two_way_game_is_split_by_the_role_that_led_it(store):
    hold(store, "tw", "MLB", "MLB", "Batter",
         {"player": "Two Way", "role": "Batter", "games": 1,
          "season_lines": [{"ab": 4, "ip": 6.0}]})
    arm = {"inningsPitched": "6.0", "strikeOuts": 9, "hits": 0, "baseOnBalls": 0,
           "hitByPitch": 0, "homeRuns": 0, "saves": 0, "holds": 0, "gamesStarted": 1}

    def logs(pid, season, group):
        if season != 2026:
            return log([])
        return log([(5, "2026-09-05", BAT if group == "hitting" else arm)])

    games.record(store, SEASON, DAY, verbose=False, sports=("MLB",),
                 loaders={"mlb_lines": lines, "mlb_log": logs})
    row = recorded(store, "tw").iloc[0]
    divisor = games.divisors(store, SEASON)
    from whul.sources import mlb
    batting = 100 * mlb.game_points(log([(5, "d", BAT)]), "hitting")["points"][0] \
        / divisor["MLB_Batter"]
    pitching = 100 * mlb.game_points(log([(5, "d", arm)]), "pitching")["points"][0] \
        / divisor["MLB_Pitcher"]
    assert row["role"] == ("start" if pitching > batting else "bat")
    assert row["score"] == pytest.approx(max(batting, pitching) + 0.5 * min(batting, pitching),
                                         abs=1e-4)


def test_an_mlb_player_the_stats_api_cannot_find_is_named(store):
    hold(store, "zz", "MLB", "MLB", "Batter", {"player": "Nobody Atall", "games": 3})
    report = games.record(store, SEASON, DAY, verbose=False, sports=("MLB",),
                          loaders={"mlb_lines": lines, "mlb_log": lambda *a: log([])})
    assert any("Nobody Atall" in p for p in report.problems)


# --- NHL ------------------------------------------------------------------------

def test_nhl_games_come_from_the_skaters_log_and_goalies_are_skipped(store):
    hold(store, "sk", "NHL", "NHL", "Skater",
         {"player": "Skater", "player_id": "84", "role": "Skater",
          "total_points": 3 + 2 * 2 + 0.5 * 4 + 1})
    hold(store, "gk", "NHL", "NHL", "Goalie",
         {"player": "Keeper", "player_id": "30", "role": "Goalie"})
    asked = []

    def logs(pid, season_id):
        asked.append((pid, season_id))
        return {"gameLog": [{"gameId": 1, "gameDate": "2026-10-08", "goals": 1,
                             "assists": 2, "shots": 4, "plusMinus": 1,
                             "opponentAbbrev": "BOS"}]}

    report = games.record(store, SEASON, "2026-10-10", verbose=False, sports=("NHL",),
                          loaders={"nhl": logs})
    assert asked == [("84", "20262027")]
    assert report.problems == []
    assert recorded(store, "sk")["points"].iloc[0] == pytest.approx(10.0)


# --- club soccer ----------------------------------------------------------------

def match(event, day, competition="epl", goals=2.0):
    return {"team": "Arsenal", "opponent": "Chelsea", "event_id": event, "date": day,
            "competition_key": competition, "goals_for": goals, "goals_against": 0.0}


def summary(started=True, came_on=False, goals=1, name="Striker"):
    entry = {"athlete": {"id": "1", "displayName": name}, "starter": started,
             "subbedIn": came_on, "active": True,
             "stats": [{"name": "totalGoals", "value": goals},
                       {"name": "goalAssists", "value": 0},
                       {"name": "yellowCards", "value": 1},
                       {"name": "redCards", "value": 0}]}
    bench = {"athlete": {"id": "2", "displayName": "Unused"}, "starter": False,
             "subbedIn": False, "active": True, "stats": []}
    return {"rosters": [{"team": {"displayName": "Arsenal"}, "roster": [entry, bench]}]}


def soccer_player(store, matches=2):
    hold(store, "st", "Club Soccer Top 3", "Premier League", "F",
          {"player": "Striker", "league": "Premier League", "team": "Arsenal",
           "position": "F", "matches": matches})


def test_a_match_is_scored_from_its_own_summary(store):
    soccer_player(store)
    ledger(store, "epl", [match("e1", "2026-09-13"), match("e2", "2026-09-20", "efl_cup"),
                          match("e3", "2026-09-17", "ucl")],
           key=lambda r: r["event_id"])
    summaries = {"e1": summary(goals=2), "e2": summary(started=False, came_on=True, goals=0),
                 "e3": summary(goals=1)}

    report = games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                          loaders={"soccer": lambda comp, event: summaries[event]})
    rows = recorded(store, "st")
    per_goal = soccer_scoring.goal_points_for("F")

    # The Champions League tie counts too, labelled as the European game it
    # is; the season line's two domestic matches still add up.
    assert report.problems == []
    assert list(rows["game_key"]) == ["e1", "e3", "e2"]
    assert rows["points"].iloc[0] == pytest.approx(2 + 2 * per_goal - 1)
    assert rows["points"].iloc[2] == pytest.approx(1 - 1)
    assert list(rows["phase"]) == ["regular", "europe", "regular"]
    assert json.loads(rows["detail"].iloc[1])["competition"] == "Champions League"
    assert json.loads(rows["detail"].iloc[2])["competition"] == "EFL Cup"


def test_a_match_is_asked_about_once(store):
    soccer_player(store, matches=1)
    ledger(store, "epl", [match("e1", "2026-09-13")], key=lambda r: r["event_id"])
    asked = []

    def loader(comp, event):
        asked.append(event)
        return summary()

    for _ in range(2):
        games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                     loaders={"soccer": loader})
    assert asked == ["e1"]


def test_a_match_he_sat_out_is_not_his(store):
    soccer_player(store, matches=0)
    ledger(store, "epl", [match("e1", "2026-09-13")], key=lambda r: r["event_id"])
    games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                 loaders={"soccer": lambda c, e: summary(name="Someone Else")})
    assert recorded(store, "st").empty


def test_matches_that_do_not_add_up_to_his_season_are_named(store):
    soccer_player(store, matches=3)
    ledger(store, "epl", [match("e1", "2026-09-13")], key=lambda r: r["event_id"])
    report = games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                          loaders={"soccer": lambda c, e: summary()})
    assert any("Striker" in p and "1 domestic match" in p for p in report.problems)


# --- into the rollup ------------------------------------------------------------

def test_recorded_games_fill_the_best_slot(store):
    hold(store, "qb", "NFL", "NFL", "QB",
         {"player": "Quarterback", "player_id": "00-1", "position": "QB",
          "regular_points": 0.04 * 300 + 12})
    ledger(store, "nfl", [nfl_week(1, "g1", 300, 3)], key=lambda r: "1")
    ledger(store, "nfl-teams", [{"game_id": "g1", "gameday": "2026-09-13"}],
           key=lambda r: r["game_id"])
    games.record(store, SEASON, DAY, verbose=False, sports=("NFL",))
    index = pipeline.game_records(store, SEASON, DAY)
    assert len(index) == 1 and index["date"].iloc[0] == date(2026, 9, 13)


# --- NBA ------------------------------------------------------------------------

def test_nba_games_come_from_the_box_scores_the_pull_keeps(store):
    hold(store, "g", "NBA", "NBA", "G",
         {"player": "Guard", "athlete_id": "11", "position": "G",
          "regular_points": 0})
    box = {"season": 2027, "season_type": 2, "game_id": "401", "game_date": "2026-10-21",
           "team": "OKC", "athlete_id": "11", "athlete_display_name": "Guard",
           "athlete_position_abbreviation": "G", "points": 30, "rebounds": 5,
           "assists": 10, "steals": 1, "blocks": 0, "turnovers": 3,
           "three_point_field_goals_made": 4, "plus_minus": "+7"}
    ledger(store, "nba", [box, {**box, "game_id": "400", "season_type": 1}],
           key=lambda r: r["game_id"])
    history_row = pd.DataFrame([{"league": "NBA", "role": "G", "season": s,
                                 "total_points": 4000} for s in (2023, 2024, 2025)])
    bm.freeze(store, bm.save(store, bm.compute(pd.concat([history(), history_row]),
                                               "Player", SEASON), SEASON, version="v2"))
    games.record(store, SEASON, "2026-10-22", verbose=False, sports=("NBA",))
    rows = recorded(store, "g")
    # The preseason game is not a game here; the regular-season one is, with
    # its double-double bonus and plus-minus.
    assert list(rows["game_key"]) == ["401"]
    expected = 30 + 5 * 1.2 + 10 * 1.5 + 3 - 3 + 4 * 0.5 + 1.5 + 0.7
    assert rows["points"].iloc[0] == pytest.approx(expected)


def test_a_match_still_being_played_is_not_kept(store):
    soccer_player(store, matches=1)
    ledger(store, "epl", [match("e1", "2026-09-13")], key=lambda r: r["event_id"])
    live = {**summary(), "header": {"competitions": [{"status": {"type": {"completed": False}}}]}}
    report = games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                          loaders={"soccer": lambda c, e: live})
    assert recorded(store, "st").empty
    assert any("could not read match e1" in p for p in report.problems)
    assert store.scalar("SELECT COUNT(*) FROM match_lineups") == 0


def test_a_stored_lineup_is_not_read_as_a_fixture(store):
    """The head-to-head table reads every ledger row as one fixture; a lineup
    kept among them took the site down."""
    from whul import headtohead

    soccer_player(store, matches=1)
    ledger(store, "epl", [match("e1", "2026-09-13")], key=lambda r: r["event_id"])
    games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                 loaders={"soccer": lambda c, e: summary()})
    assert store.scalar("SELECT COUNT(*) FROM match_lineups") == 1
    headtohead.meetings(store, SEASON)



# --- the postseason -----------------------------------------------------------

def test_nfl_playoff_weeks_are_games_and_not_in_the_season_check(store):
    hold(store, "qb", "NFL", "NFL", "QB",
         {"player": "Quarterback", "player_id": "00-1", "position": "QB",
          "regular_points": 0.04 * 300 + 4 * 3})
    post = {**nfl_week(19, "2026_19_A_B", 400, 4), "season_type": "POST"}
    ledger(store, "nfl", [nfl_week(1, "2026_01_A_B", 300, 3), post],
           key=lambda r: f"{r['week']}")
    ledger(store, "nfl-teams", [{"game_id": "2026_01_A_B", "gameday": "2026-09-13"},
                                {"game_id": "2026_19_A_B", "gameday": "2027-01-10"}],
           key=lambda r: r["game_id"])
    report = games.record(store, SEASON, "2027-01-12", verbose=False, sports=("NFL",))
    rows = recorded(store, "qb")
    assert report.problems == []
    assert list(rows["phase"]) == ["regular", "playoffs"]


def test_the_nba_play_in_and_playoffs_are_games(store):
    hold(store, "g", "NBA", "NBA", "G",
         {"player": "Guard", "athlete_id": "11", "position": "G", "regular_points": 0})
    box = {"season": 2027, "game_date": "2027-04-15", "team": "OKC", "athlete_id": "11",
           "athlete_display_name": "Guard", "athlete_position_abbreviation": "G",
           "points": 20, "rebounds": 5, "assists": 5, "steals": 0, "blocks": 0,
           "turnovers": 2, "three_point_field_goals_made": 2, "plus_minus": "+3"}
    ledger(store, "nba", [{**box, "game_id": "1", "season_type": 2},
                          {**box, "game_id": "2", "season_type": 5},
                          {**box, "game_id": "3", "season_type": 3},
                          {**box, "game_id": "4", "season_type": 1}],
           key=lambda r: r["game_id"])
    history_row = pd.DataFrame([{"league": "NBA", "role": "G", "season": s,
                                 "total_points": 4000} for s in (2023, 2024, 2025)])
    bm.freeze(store, bm.save(store, bm.compute(pd.concat([history(), history_row]),
                                               "Player", SEASON), SEASON, version="v2"))
    games.record(store, SEASON, "2027-05-01", verbose=False, sports=("NBA",))
    rows = recorded(store, "g").set_index("game_key")
    assert rows["phase"].to_dict() == {"1": "regular", "2": "play-in", "3": "playoffs"}


def test_nhl_playoff_logs_are_asked_for_only_once_they_can_have_started(store):
    hold(store, "sk", "NHL", "NHL", "Skater",
         {"player": "Skater", "player_id": "84", "role": "Skater", "total_points": 0})
    asked = []

    def logs(pid, season_id, game_type=2):
        asked.append(game_type)
        day = "2027-04-25" if game_type == 3 else "2026-10-08"
        return {"gameLog": [{"gameId": game_type, "gameDate": day, "goals": 1,
                             "assists": 0, "shots": 2, "plusMinus": 0}]}

    games.record(store, SEASON, "2026-10-10", verbose=False, sports=("NHL",),
                 loaders={"nhl": logs})
    assert asked == [2]
    asked.clear()
    games.record(store, SEASON, "2027-04-30", verbose=False, sports=("NHL",),
                 loaders={"nhl": logs})
    assert asked == [2, 3]
    assert set(recorded(store, "sk")["phase"]) == {"regular", "playoffs"}


def test_mlb_october_rounds_are_read_once_october_can_have_started(store):
    hold(store, "jr", "MLB", "MLB", "Batter",
         {"player": "Jose Ramirez", "role": "Batter", "games": 1,
          "season_lines": [{"ab": 4}]})
    asked = []

    def logs(pid, season, group, game_type="R"):
        asked.append(game_type)
        if season != 2026:
            return log([])
        day = "2026-09-05" if game_type == "R" else "2026-10-03"
        return log([(f"{game_type}1", day, BAT)])

    games.record(store, SEASON, "2026-09-27", verbose=False, sports=("MLB",),
                 loaders={"mlb_lines": lines, "mlb_log": logs})
    assert "F" not in asked
    asked.clear()
    report = games.record(store, SEASON, "2026-10-06", verbose=False, sports=("MLB",),
                          loaders={"mlb_lines": lines, "mlb_log": logs})
    assert {"R", "F", "D", "L", "W"} <= set(asked)
    rows = recorded(store, "jr")
    assert set(rows["phase"]) == {"regular", "playoffs"}
    # October is not in the season line, so it is not held against it.
    assert report.problems == []
