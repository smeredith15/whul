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

#: One game: a goal on the power play that won it, two assists, four shots,
#: plus one, three hits and two blocked shots.
NHL_GAME_POINTS = 3 + 2 * 2 + 0.5 * 4 + 1 + 0.5 + 1.0 + 0.25 * 3 + 0.5 * 2


def test_nhl_games_come_from_the_skaters_log_and_goalies_are_skipped(store):
    hold(store, "sk", "NHL", "NHL", "Skater",
         {"player": "Skater", "player_id": "84", "role": "Skater",
          "total_points": NHL_GAME_POINTS})
    hold(store, "gk", "NHL", "NHL", "Goalie",
         {"player": "Keeper", "player_id": "30", "role": "Goalie"})
    asked, realtime = [], []

    def logs(pid, season_id):
        asked.append((pid, season_id))
        return {"gameLog": [{"gameId": 1, "gameDate": "2026-10-08", "goals": 1,
                             "assists": 2, "shots": 4, "plusMinus": 1,
                             "powerPlayPoints": 1, "gameWinningGoals": 1,
                             "opponentAbbrev": "BOS"}]}

    def hits(pid, season, game_type):
        realtime.append((pid, season, game_type))
        return {"1": {"gameId": 1, "hits": 3, "blockedShots": 2}}

    report = games.record(store, SEASON, "2026-10-10", verbose=False, sports=("NHL",),
                          loaders={"nhl": logs, "nhl_realtime": hits})
    assert asked == [("84", "20262027")]
    assert realtime == [("84", 2027, 2)]
    assert report.problems == []
    row = recorded(store, "sk")
    assert row["points"].iloc[0] == pytest.approx(NHL_GAME_POINTS)
    detail = json.loads(row["detail"].iloc[0])
    assert detail["hits"] == 3 and detail["blocks"] == 2 and detail["pp_points"] == 1


def test_an_nhl_game_the_realtime_report_lacks_is_not_written_short(store):
    """A game the box score has not reached yet would be a game worth less
    than it was; it waits a night instead."""
    hold(store, "sk", "NHL", "NHL", "Skater",
         {"player": "Skater", "player_id": "84", "role": "Skater", "total_points": 5})

    def logs(pid, season_id):
        return {"gameLog": [{"gameId": 1, "gameDate": "2026-10-08", "goals": 1,
                             "assists": 1, "opponentAbbrev": "BOS"}]}

    report = games.record(store, SEASON, "2026-10-10", verbose=False, sports=("NHL",),
                          loaders={"nhl": logs, "nhl_realtime": lambda *a: {}})
    assert recorded(store, "sk").empty
    assert any("realtime" in p for p in report.problems)


# --- club soccer ----------------------------------------------------------------

def match(event, day, competition="epl", goals=2.0, player="Striker", minutes=90,
          started=True, player_id="9", team="Arsenal"):
    """One FotMob line, as ``whul.sources.fotmob.match_lines`` writes it."""
    return {"match_id": event, "date": day, "competition_key": competition,
            "player": player, "player_id": player_id, "team": team,
            "opponent": "Chelsea", "position": "F", "started": started,
            "minutes": minutes, "goals": goals, "assists": 0, "yellow": 1, "red": 0,
            "conceded_on": 0, "goal_xg": [], "rating": 7.0, "potm": False}


def walker_of(lines):
    def walk(start, end, keys):
        return [line for line in lines if line["competition_key"] in keys]

    return walk


def soccer_player(store, matches=2, line=None):
    hold(store, "st", "Club Soccer Top 3", "Premier League", "F",
         {"player": "Striker", "league": "Premier League", "team": "Arsenal",
          "position": "F", "matches": matches, **(line or {})})


def test_a_match_is_priced_from_its_fotmob_line(store):
    from whul.scoring import soccer_match

    soccer_player(store)
    lines = [match("e1", "2026-09-13"), match("e2", "2026-09-20", "efl_cup", goals=0,
                                              minutes=20, started=False),
             match("e3", "2026-09-17", "ucl", goals=1)]
    report = games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                          loaders={"soccer_lines": walker_of(lines)})
    rows = recorded(store, "st")

    # The Champions League tie counts too, labelled as the European game it
    # is; the season line's two domestic matches still add up.
    assert report.problems == []
    assert list(rows["game_key"]) == ["fotmob-e1", "fotmob-e3", "fotmob-e2"]
    assert rows["points"].iloc[0] == pytest.approx(soccer_match.match_points(lines[0]))
    assert rows["points"].iloc[0] == pytest.approx(2 + 2 * 4 - 1)
    assert list(rows["phase"]) == ["regular", "europe", "regular"]
    assert json.loads(rows["detail"].iloc[1])["competition"] == "Champions League"
    assert json.loads(rows["detail"].iloc[2])["competition"] == "EFL Cup"


def test_a_match_is_priced_at_the_position_the_league_holds_for_him(store):
    """FotMob files him a forward; the league has him as a defender, and a
    defender's goal is worth six."""
    soccer_player(store, matches=1, line={"position": "D"})
    games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                 loaders={"soccer_lines": walker_of([match("e1", "2026-09-13", goals=1)])})
    # 2 for the appearance, 6 for the goal, 2 for the clean sheet, -1 the card.
    assert recorded(store, "st")["points"].iloc[0] == pytest.approx(9.0)


def test_a_match_he_sat_out_is_not_his(store):
    soccer_player(store, matches=0)
    games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                 loaders={"soccer_lines": walker_of(
                     [match("e1", "2026-09-13", player="Someone Else")])})
    assert recorded(store, "st").empty


def test_two_players_of_one_name_are_told_apart_by_club(store):
    soccer_player(store, matches=1)
    lines = [match("e1", "2026-09-13"),
             match("e9", "2026-09-13", player_id="77", team="Elsewhere FC", goals=3)]
    games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                 loaders={"soccer_lines": walker_of(lines)})
    assert list(recorded(store, "st")["game_key"]) == ["fotmob-e1"]


def test_matches_that_do_not_add_up_to_his_season_are_named(store):
    soccer_player(store, matches=3)
    report = games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                          loaders={"soccer_lines": walker_of([match("e1", "2026-09-13")])})
    assert any("Striker" in p and "1 domestic match" in p for p in report.problems)


def test_fotmob_unreadable_leaves_the_record_as_it_was(store):
    soccer_player(store, matches=1)
    games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                 loaders={"soccer_lines": walker_of([match("e1", "2026-09-13")])})

    def refuse(start, end, keys):
        raise RuntimeError("FotMob stopped answering")

    report = games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                          loaders={"soccer_lines": refuse})
    assert list(recorded(store, "st")["game_key"]) == ["fotmob-e1"]
    assert any("could not be read" in p for p in report.problems)


def test_the_espn_record_of_a_match_goes_when_fotmobs_arrives(store):
    """The same match under ESPN's id would count twice in a best-performances
    slot; it is dropped once FotMob has recorded the player's matches."""
    soccer_player(store, matches=1)
    store.upsert("game_scores", [{
        "season": SEASON, "asset_id": "st", "game_key": "704328", "date": "2026-09-13",
        "role": "", "phase": "regular", "points": 5.0, "score": 2.0, "opponent": "",
        "detail": "{}", "source": "Club Soccer", "recorded_at": "x"}],
        keys=("season", "asset_id", "game_key"))
    games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                 loaders={"soccer_lines": walker_of([match("e1", "2026-09-13")])})
    assert list(recorded(store, "st")["game_key"]) == ["fotmob-e1"]


def test_a_season_line_priced_the_same_way_must_add_up(store):
    from whul.scoring import soccer_match

    line = match("e1", "2026-09-13")
    soccer_player(store, matches=1, line={
        "pts_appearance": 2.0, "regular_points": soccer_match.match_points(line) + 3})
    report = games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                          loaders={"soccer_lines": walker_of([line])})
    assert any("come to" in p for p in report.problems)


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

    def realtime(pid, season, game_type):
        return {str(game_type): {"hits": 1, "blockedShots": 0}}

    loaders = {"nhl": logs, "nhl_realtime": realtime}
    games.record(store, SEASON, "2026-10-10", verbose=False, sports=("NHL",),
                 loaders=loaders)
    assert asked == [2]
    asked.clear()
    games.record(store, SEASON, "2027-04-30", verbose=False, sports=("NHL",),
                 loaders=loaders)
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



def test_a_cup_tie_before_the_league_opens_is_his(store):
    """Frankfurt's DFB-Pokal tie on the 21st, a week before the Bundesliga's
    first matchday: inside the league year, and in his season line."""
    history_row = pd.DataFrame([{"league": "Bundesliga", "role": "F", "season": y,
                                 "total_points": 190} for y in (2023, 2024, 2025)])
    bm.freeze(store, bm.save(store, bm.compute(pd.concat([history(), history_row]),
                                               "Player", SEASON), SEASON, version="v3"))
    hold(store, "yo", "Club Soccer Other", "Bundesliga", "F",
         {"player": "Striker", "league": "Bundesliga", "team": "Arsenal",
          "position": "F", "matches": 2})
    lines = [match("p1", "2026-08-21", "dfbpokal"), match("b1", "2026-08-29", "bundesliga"),
             match("x0", "2026-08-15", "dfbpokal")]
    report = games.record(store, SEASON, DAY, verbose=False, sports=("Club Soccer",),
                          loaders={"soccer_lines": walker_of(lines)})
    assert list(recorded(store, "yo")["game_key"]) == ["fotmob-p1", "fotmob-b1"]
    assert report.problems == []


def test_the_rollup_keeps_a_cup_tie_before_the_league_opened(store):
    store.upsert("assets", [{
        "asset_id": "yo", "asset_type": "Player", "display_name": "Striker",
        "league": "Bundesliga", "role": "F", "norm_key": "Bundesliga", "active": 1,
        "created_at": "2026-08-21"}], keys=("asset_id",))
    store.upsert("game_scores", [{
        "season": SEASON, "asset_id": "yo", "game_key": k, "date": d, "role": "",
        "phase": "regular", "points": 5.0, "score": 2.0, "opponent": "", "detail": "{}",
        "source": "Club Soccer", "recorded_at": "x"}
        for k, d in (("p1", "2026-08-21"), ("b1", "2026-08-29"), ("x0", "2026-08-15"))],
        keys=("season", "asset_id", "game_key"))
    kept = pipeline.game_records(store, SEASON, DAY)
    assert sorted(kept["game_key"]) == ["b1", "p1"]
