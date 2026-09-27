"""The site's side of the best-performances slot: the standings split, the
two charts, the roster marks and the profile's games."""

import pandas as pd

from whul.site import build


def bars():
    return pd.DataFrame([
        {"manager_id": "SS", "category": "NFL", "asset_type": "Player", "slot_id": "a",
         "asset_id": "allen", "score": 19.6, "best_score": 19.6, "counts": 1,
         "scored_as": "best"},
        {"manager_id": "SS", "category": "NFL", "asset_type": "Player", "slot_id": "b",
         "asset_id": "jsn", "score": 19.1, "best_score": 19.1, "counts": 1,
         "scored_as": "season"},
        {"manager_id": "SS", "category": "NFL", "asset_type": "Player", "slot_id": "c",
         "asset_id": "mahomes", "score": 13.0, "best_score": 13.0, "counts": 0,
         "scored_as": ""},
        {"manager_id": "SS", "category": "NFL", "asset_type": "Team", "slot_id": "d",
         "asset_id": "bills", "score": 9.0, "best_score": 0.0, "counts": 1,
         "scored_as": "season"},
    ])


def test_the_total_splits_into_full_seasons_and_best_performances():
    assert build._split_by_manager(bars()) == {"SS": (28.1, 19.6)}


def test_a_row_written_before_the_slot_existed_still_counts_its_season():
    old = bars().drop(columns=["best_score", "scored_as"])
    added = build._with_added(old)["added"].tolist()
    assert added == [19.6, 19.1, 0.0, 9.0]


def test_the_standings_carry_both_parts_in_their_own_columns():
    table = pd.DataFrame([{"rank": 1, "manager_id": "SS", "total": 47.7}])
    html = build._standings_table(table, {}, ["SS"], split={"SS": (28.1, 19.6)})
    assert "Full season" in html and "Best perf." in html
    assert "28.1" in html and "19.6" in html


def test_the_full_season_chart_lists_every_slot_and_marks_what_counts():
    _, values, depth = build._slot_rows(bars(), ["SS"], "season")
    counted = {v[1]: v[4] for v in values.values()}
    # Allen counts for his best games, so his season is listed and not counting.
    assert counted == {"allen": False, "jsn": True, "mahomes": False, "bills": True}
    assert depth["NFL"] == 6


def test_the_best_performances_chart_is_players_only():
    rows, values, depth = build._slot_rows(bars(), ["SS"], "best")
    assert {v[1] for v in values.values()} == {"allen", "jsn", "mahomes"}
    assert {v[1]: v[4] for v in values.values()}["allen"] is True
    assert "PGA" not in {r[0] for r in rows} and depth["NFL"] == 4


def test_a_games_figures_read_as_a_box_score():
    assert build._game_figures("NFL", {"passing_yards": 334.0, "passing_tds": 2.0}) == [
        ["334", "pass yds"], ["2", "pass TD"]]
    mlb = build._game_figures("MLB", {"batting": {"hits": 2, "atBats": 4, "homeRuns": 1}})
    assert mlb == [["2-4", "H-AB"], ["1", "HR"]]
    assert build._game_figures("NBA", {"plus_minus": 7.0})[0] == ["+7", "+/-"]
    assert build._game_figures("Club Soccer", {"started": True, "goals": 2.0}) == [
        ["", "started"], ["2", "goals"]]
