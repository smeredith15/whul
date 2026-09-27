"""The standings under each best-game proposal: who fills which slot, and how
each sport's best-k is read from the games the published score counts."""

import pandas as pd
import pytest

from whul import proposal_scores as ps
from whul.best_game_calibration import Scale


def player(manager, category, name, season, best, counts, games=0):
    return ps.Player(manager, category, name, name, "", "", season, counts,
                     best=best, games=games)


# --- proposal 2 ---------------------------------------------------------------

def test_a_bench_slot_becomes_a_best_game_slot_outside_soccer():
    roster = [player("A", "NFL", "one", 20, 20, True),
              player("A", "NFL", "two", 15, 15, True),
              player("A", "NFL", "three", 10, 12, False),
              player("A", "NFL", "four", 5, 5, False)]
    pick = ps.proposal_two(roster)["A"]
    # Both season slots stay; the best bench player's games are added.
    assert pick.total == pytest.approx(20 + 15 + 12)
    assert pick.best == ["three"]


def test_in_soccer_a_season_slot_becomes_the_best_game_slot():
    roster = [player("A", "Club Soccer Top 3", n, s, s, c)
              for n, s, c in (("a", 30, True), ("b", 20, True), ("c", 10, True),
                              ("d", 8, True), ("e", 6, False))]
    pick = ps.proposal_two(roster)["A"]
    # Three season slots and one best-game slot: 30 + 20 + 10 + 8.
    assert pick.total == pytest.approx(68)
    assert len(pick.season) == 3 and len(pick.best) == 1


# --- proposal 3 ---------------------------------------------------------------

def test_wildcards_take_the_bench_without_moving_anyone_who_counts():
    roster = [player("A", "NFL", "one", 20, 1, True),
              player("A", "NFL", "two", 15, 1, True),
              player("A", "NFL", "three", 10, 4, False),
              player("A", "MLB", "bat", 12, 3, True),
              player("A", "MLB", "bat2", 11, 3, True),
              player("A", "MLB", "bench", 2, 6, False)]
    pick = ps.proposal_three(roster, wildcards=5)["A"]
    assert pick.total == pytest.approx(20 + 15 + 12 + 11 + 4 + 6)
    assert set(pick.best) == {"three", "bench"}


def test_a_player_worth_more_in_a_wildcard_moves_there():
    """His games are worth more than the gap between his season and the
    bench player who takes his seat."""
    roster = [player("A", "NFL", "star", 20, 30, True),
              player("A", "NFL", "two", 15, 1, True),
              player("A", "NFL", "bench", 14, 2, False)]
    pick = ps.proposal_three(roster, wildcards=1)["A"]
    assert pick.best == ["star"]
    assert pick.total == pytest.approx(15 + 14 + 30)


def test_no_more_wildcards_are_used_than_there_are():
    roster = [player("A", "NFL", f"p{i}", 10 - i, 5, i < 2) for i in range(8)]
    pick = ps.proposal_three(roster, wildcards=5)["A"]
    assert len(pick.best) == 5


def test_the_totals_swap_only_the_six_categories():
    roster = [player("A", "NFL", "one", 20, 20, True),
              player("A", "NFL", "two", 15, 15, True),
              player("A", "NFL", "three", 10, 12, False)]
    report = ps.Report("2026-09-27", {"A": 100.0}, roster, ps.current(roster),
                       {"Proposal 2": ps.proposal_two(roster)})
    # 100 published, 35 of it from the NFL, which is now 47.
    assert report.standings().loc["A", "Proposal 2"] == pytest.approx(112.0)


# --- NFL ------------------------------------------------------------------------

def nflverse(rows):
    return pd.DataFrame([
        {"season": 2026, "season_type": "REG", "week": w, "player_id": pid,
         "player_display_name": pid, "position": "WR", "receiving_yards": yds}
        for pid, w, yds in rows])


def test_nfl_takes_the_weeks_the_published_line_counts():
    """nflverse has his third week; the published line has two. The third is
    not in his season score, so it is not in his best-k either."""
    roster = [ps.Player("A", "NFL", "x", "X", "NFL", "", 5.0, True)]
    lines = {"x": {"player_id": "X", "position": "WR", "regular_games": 2,
                   "regular_points": 15.0}}
    def loader(seasons):
        return nflverse([("X", 1, 100), ("X", 2, 50), ("X", 3, 200)])

    ps.nfl_best(roster, lines, {"NFL_WR": 100.0}, 2026, loader=loader)
    assert roster[0].best == pytest.approx(15.0) and roster[0].games == 2
    assert roster[0].note == ""


def test_nfl_weeks_that_do_not_add_up_are_reported():
    roster = [ps.Player("A", "NFL", "x", "X", "NFL", "", 5.0, True)]
    lines = {"x": {"player_id": "X", "position": "WR", "regular_games": 1,
                   "regular_points": 99.0}}
    def loader(seasons):
        return nflverse([("X", 1, 100)])

    ps.nfl_best(roster, lines, {"NFL_WR": 100.0}, 2026, loader=loader)
    assert "published" in roster[0].note


# --- soccer ---------------------------------------------------------------------

def test_a_match_stored_on_its_own_day_is_known():
    points = [("09-05", 20.0), ("09-06", 20.0), ("09-13", 26.0), ("09-21", 27.0)]
    matches = {"09-05": 4, "09-06": 4, "09-13": 5, "09-21": 6}
    assert ps.single_matches(points, matches) == [6.0, 1.0]


def test_a_day_that_added_two_matches_says_nothing_about_either():
    points = [("09-05", 20.0), ("09-13", 30.0)]
    assert ps.single_matches(points, {"09-05": 4, "09-13": 6}) == []


def soccer(matches, starts, points):
    roster = [ps.Player("A", "Club Soccer Top 3", "x", "X", "La Liga", "",
                        100 * points / 200.0, True)]
    lines = {"x": {"league": "La Liga", "matches": matches, "starts": starts,
                   "regular_points": points}}
    return roster, lines


def test_six_matches_or_fewer_is_the_season_score():
    roster, lines = soccer(6, 6, 40.0)
    ps.soccer_best(None, roster, lines, {"La Liga": 200.0}, "d", steps=lambda a: [])
    assert roster[0].best == roster[0].season and roster[0].note == ""


def test_a_known_cheap_match_is_the_one_dropped():
    roster, lines = soccer(7, 7, 40.0)
    ps.soccer_best(None, roster, lines, {"La Liga": 200.0}, "d", steps=lambda a: [1.0])
    assert roster[0].best == pytest.approx(100 * 39.0 / 200.0)
    assert roster[0].note == ""


def test_otherwise_the_dropped_match_is_a_scoreless_start_and_says_so():
    roster, lines = soccer(7, 7, 40.0)
    ps.soccer_best(None, roster, lines, {"La Liga": 200.0}, "d", steps=lambda a: [14.0])
    assert roster[0].best == pytest.approx(100 * 38.0 / 200.0)
    assert "estimated" in roster[0].note


# --- MLB ------------------------------------------------------------------------

SCALE = Scale(divisor={"MLB_Batter": 100.0, "MLB_Pitcher": 100.0},
              per_season={"MLB_Batter": 1, "MLB_Pitcher": 1})


def lines_loader(season, group):
    return pd.DataFrame([{"player": "José Ramírez", "player_id": 7}])


def hitting_log(pid, season, group):
    assert group == "hitting", "a batter's pitching log was fetched"
    bat = {"atBats": 4, "hits": 1, "doubles": 0, "triples": 0, "baseOnBalls": 0,
           "hitByPitch": 0, "stolenBases": 0, "caughtStealing": 0}
    days = [("2026-08-01", 3), ("2026-08-16", 1), ("2026-08-17", 0),
            ("2026-08-18", 2), ("2026-09-30", 4)]
    return pd.DataFrame([{"game_pk": i, "date": d, "game_number": 1, "player": "x",
                          "season": 2026, "homeRuns": hr, **bat}
                         for i, (d, hr) in enumerate(days)])


def test_mlb_reads_the_games_since_the_league_opened_and_no_later():
    """Before the 15th of August is last league year's; after the day is not
    in the published line yet."""
    roster = [ps.Player("A", "MLB", "jr", "Jose Ramirez", "MLB", "Batter", 9.0, True)]
    lines = {"jr": {"games": 3, "season_lines": [{"ab": 12, "ip": None}]}}
    problems = ps.mlb_best(roster, lines, SCALE, "2026-09-27", 2026,
                           line_loader=lines_loader, log_loader=hitting_log)
    assert problems == []
    assert roster[0].games == 3 and roster[0].note == ""
    from whul.sources import mlb
    points = mlb.game_points(hitting_log(7, 2026, "hitting"), "hitting")["points"]
    # Games 1-3; the divisor is 100, so face value is the points themselves.
    assert roster[0].best == pytest.approx(points.iloc[1:4].sum())


def test_an_mlb_player_the_stats_api_does_not_know_is_a_problem_not_a_zero():
    roster = [ps.Player("A", "MLB", "zz", "Nobody Atall", "MLB", "Batter", 9.0, True)]
    problems = ps.mlb_best(roster, {"zz": {"games": 3}}, SCALE, "2026-09-27", 2026,
                           line_loader=lines_loader, log_loader=hitting_log)
    assert problems and "Nobody Atall" in problems[0]
