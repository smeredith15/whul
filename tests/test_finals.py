"""A championship seen decided releases the bonuses held for it."""

from datetime import date

import pandas as pd
import pytest

from whul import finals
from whul.scoring import completion
from whul.scoring.postseason import RULES, detail_for
from whul.store.db import open_store


@pytest.fixture(autouse=True)
def nothing_decided():
    completion.DECIDED.clear()
    yield
    completion.DECIDED.clear()


def test_a_sighting_brings_the_release_forward_and_never_back():
    assert completion.finishes("MLB", 2026) == date(2026, 11, 20)
    assert not completion.is_complete("MLB", 2026, date(2026, 11, 2))

    completion.decide("MLB", 2026, date(2026, 11, 2))
    assert completion.finishes("MLB", 2026) == date(2026, 11, 2)
    assert completion.is_complete("MLB", 2026, date(2026, 11, 2))
    assert not completion.is_complete("MLB", 2026, date(2026, 11, 1))
    # The declared date is what it always was, and another season is untouched.
    assert completion.declared("MLB", 2026) == date(2026, 11, 20)
    assert completion.finishes("MLB", 2027) == date(2027, 11, 20)

    # A later sighting does not move it back, and nor does one past the
    # declared date: that date is still the ceiling.
    completion.decide("MLB", 2026, date(2026, 11, 9))
    assert completion.finishes("MLB", 2026) == date(2026, 11, 2)
    completion.decide("NBA", 2027, date(2027, 8, 1))
    assert completion.finishes("NBA", 2027) == date(2027, 7, 15)


def test_a_held_bonus_is_credited_from_the_day_it_was_seen_decided():
    rule = RULES["MLB"]
    before = detail_for("MLB", 4, 20.0, rule, season=2026, as_of=date(2026, 11, 3))
    assert not before["credited"]

    completion.decide("MLB", 2026, date(2026, 11, 3))
    after = detail_for("MLB", 4, 20.0, rule, season=2026, as_of=date(2026, 11, 3))
    assert after["credited"] and after["finishes"] == "2026-11-03"
    # A day before the sighting is still held.
    earlier = detail_for("MLB", 4, 20.0, rule, season=2026, as_of=date(2026, 11, 2))
    assert not earlier["credited"]


def _mlb_game(day, home, away, home_score, away_score, game_type="W"):
    return {"season": 2026, "game_type": game_type, "home_team": home,
            "away_team": away, "home_score": home_score,
            "away_score": away_score, "game_date": day}


def test_four_world_series_wins_decide_it_and_three_do_not():
    three = pd.DataFrame([
        _mlb_game("2026-10-23", "Dodgers", "Yankees", 4, 1),
        _mlb_game("2026-10-24", "Dodgers", "Yankees", 2, 1),
        _mlb_game("2026-10-26", "Yankees", "Dodgers", 1, 3),
        _mlb_game("2026-10-27", "Yankees", "Dodgers", 5, 2),
        # An LCS sweep is four wins too, and is not the World Series.
        _mlb_game("2026-10-15", "Dodgers", "Mets", 3, 0, "L"),
        _mlb_game("2026-10-16", "Dodgers", "Mets", 3, 0, "L"),
        _mlb_game("2026-10-17", "Mets", "Dodgers", 0, 3, "L"),
        _mlb_game("2026-10-18", "Mets", "Dodgers", 0, 3, "L"),
    ])
    assert finals.seen("mlb-teams", raw=three) == []

    four = pd.concat([three, pd.DataFrame(
        [_mlb_game("2026-10-28", "Yankees", "Dodgers", 0, 6)])], ignore_index=True)
    assert finals.seen("mlb-teams", raw=four) == [("MLB", 2026)]


def test_a_super_bowl_with_a_score_decides_the_nfl():
    schedule = pd.DataFrame({
        "season": [2026, 2026], "game_type": ["CON", "SB"],
        "home_team": ["KC", "KC"], "away_team": ["BUF", "PHI"],
        "home_score": [27, None], "away_score": [20, None],
    })
    assert finals.seen("nfl-teams", raw=schedule) == []
    schedule.loc[1, ["home_score", "away_score"]] = [31, 24]
    assert finals.seen("nfl-teams", raw=schedule) == [("NFL", 2026)]


def test_four_series_won_decide_the_nba_and_the_nhl():
    nba = pd.DataFrame({"season": [2027, 2027], "team": ["Celtics", "Thunder"],
                        "playoff_series_wins": [3, 3]})
    assert finals.seen("nba-teams", scored=nba) == []
    nba.loc[1, "playoff_series_wins"] = 4
    assert finals.seen("nba-teams", scored=nba) == [("NBA", 2027)]

    nhl = pd.DataFrame({"season": [2027], "team": ["Panthers"], "series_wins": [4]})
    assert finals.seen("nhl-teams", scored=nhl) == [("NHL", 2027)]
    # Each reader answers for its own league only.
    assert finals.seen("nhl", scored=nhl) == []


def _line(round_name, key="ucl", competition="UEFA Champions League",
          league="Premier League", season=2027):
    return {"round": round_name, "competition_key": key,
            "competition": competition, "league": league, "season": season,
            "player": "x"}


def test_a_european_final_with_a_line_decides_its_competition():
    lines = pd.DataFrame([_line("Semi-final"), _line("Quarter-final"),
                          _line("Final", "uel", "UEFA Europa League")])
    assert finals.seen("soccer-players", raw=lines) == [("Europa League", 2027)]

    # A league's last round is not a final, and a cup final releases nothing:
    # a domestic cup is counted in full, so nothing is held for it.
    domestic = pd.DataFrame([_line("Round 38", "epl", "Premier League"),
                             _line("Final", "facup", "FA Cup")])
    assert finals.seen("soccer-players", raw=domestic) == []

    # Lines read before the round was carried have none, and say nothing.
    assert finals.seen("soccer-players",
                       raw=pd.DataFrame([{"competition_key": "ucl"}])) == []


def test_a_reader_that_cannot_read_a_frame_leaves_the_date_to_it():
    assert finals.seen("mlb-teams", raw=pd.DataFrame({"x": [1]})) == []
    assert finals.seen("nba-teams", scored=None) == []


def test_the_store_keeps_the_earliest_sighting():
    store = open_store(":memory:")
    assert store.record_final("MLB", 2026, date(2026, 11, 3), "mlb-teams")
    assert not store.record_final("MLB", 2026, date(2026, 11, 4), "mlb-teams")
    assert store.read_finals() == {("MLB", 2026): date(2026, 11, 3)}
    # A replay of an earlier day that already held it moves the day back.
    assert store.record_final("MLB", 2026, date(2026, 11, 2), "mlb-teams")
    assert store.read_finals() == {("MLB", 2026): date(2026, 11, 2)}


def test_a_pull_that_sees_a_final_records_it_for_the_players_after_it():
    from whul import ingest

    class Source:
        key, league = "nba-teams", "NBA"

    store = open_store(":memory:")
    scored = pd.DataFrame({"season": [2027], "team": ["Thunder"],
                           "playoff_series_wins": [4]})
    ingest._see_finals(Source(), date(2027, 6, 18), store, False, scored=scored)
    assert store.read_finals() == {("NBA", 2027): date(2027, 6, 18)}
    assert completion.is_complete("NBA", 2027, date(2027, 6, 18))

    # A later process -- the site build, the next night's pull -- reads it back.
    completion.DECIDED.clear()
    completion.remember(store.read_finals())
    assert completion.finishes("NBA", 2027) == date(2027, 6, 18)


def test_each_team_pull_runs_ahead_of_its_players():
    from whul.benchmark_sources import ORDER

    for league in ("nfl", "nhl", "mlb", "nba"):
        assert ORDER.index(f"{league}-teams") < ORDER.index(league)
