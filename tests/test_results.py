"""Every result an asset has had, for its profile's Results tab."""

import json
import re
from datetime import date

import pandas as pd
import pytest

from whul import simulate
from whul.site import results
from whul.site.build import build
from whul.store import open_store

END = date(2026, 10, 31)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    store = open_store(":memory:")
    simulate.generate(store, seed=2026, end=END, verbose=False)
    # The simulation scores seasons, not games: two games for one rostered
    # player, so there is a list to write.
    held = store.query(
        "SELECT o.asset_id FROM slot_occupancy o JOIN roster_slots r "
        "ON r.slot_id = o.slot_id WHERE r.season = ? AND r.asset_type = 'Player' "
        "LIMIT 1", (simulate.SIM_SEASON,))
    store.upsert("game_scores", [
        {"season": simulate.SIM_SEASON, "asset_id": held["asset_id"][0],
         "game_key": f"g{i}", "date": f"2026-10-0{i}", "role": "", "points": 3.0 * i,
         "score": 1.0 * i, "opponent": "BOS", "detail": "{}", "source": "test",
         "recorded_at": "2026-10-10", "phase": "regular"}
        for i in (1, 2)], ["season", "asset_id", "game_key"])
    out = tmp_path_factory.mktemp("site")
    build(store, simulate.SIM_SEASON, out)
    return out


# --- grouping ------------------------------------------------------------------

def test_the_current_month_is_open_and_the_last_one_through_the_seventh():
    assert results.month_open((2026, 10), date(2026, 10, 20))
    assert results.month_open((2026, 9), date(2026, 10, 7))
    assert not results.month_open((2026, 9), date(2026, 10, 8))
    assert not results.month_open((2026, 8), date(2026, 10, 2))
    # January's month before is December of the year before.
    assert results.month_open((2026, 12), date(2027, 1, 3))


def _row(day, group="", **extra):
    return {"_day": day, "_group": group, "date": f"{day:%b} {day.day}",
            "points": "1.0", **extra}


def test_a_month_list_keeps_the_playoffs_apart_and_newest_first():
    rows = [_row(date(2026, 8, 30)), _row(date(2026, 9, 27)),
            _row(date(2026, 10, 1), "Playoffs"), _row(date(2026, 9, 2))]
    groups = results.group_rows(rows, "month", date(2026, 10, 4))
    assert [g["label"] for g in groups] == ["Playoffs", "September 2026", "August 2026"]
    assert [g["open"] for g in groups] == [True, True, False]
    assert [r["date"] for r in groups[1]["rows"]] == ["Sep 27", "Sep 2"]


def test_a_named_list_opens_where_the_newest_result_is():
    rows = [_row(date(2026, 9, 10), "Champions League"),
            _row(date(2026, 9, 20), "Premier League"),
            _row(date(2026, 9, 13), "Premier League")]
    groups = results.group_rows(rows, "named", date(2026, 10, 4))
    assert [g["label"] for g in groups] == ["Premier League", "Champions League"]
    assert [g["open"] for g in groups] == [True, False]


def test_a_flat_list_is_one_open_group_with_no_heading():
    groups = results.group_rows([_row(date(2026, 9, 7)), _row(date(2026, 9, 14))],
                                "flat", date(2026, 10, 4))
    assert len(groups) == 1 and groups[0]["label"] == "" and groups[0]["open"]
    assert [r["date"] for r in groups[0]["rows"]] == ["Sep 14", "Sep 7"]


def test_a_row_leaves_off_what_it_does_not_have():
    clean = results._clean({"_day": None, "date": "Sep 7", "points": "0.0",
                            "phase": "", "figs": [], "best": False, "res": "W 1-0"})
    assert clean == {"date": "Sep 7", "points": "0.0", "res": "W 1-0"}


# --- what a row says -----------------------------------------------------------

@pytest.mark.parametrize("row, text", [
    ({"for": 5, "against": 3}, "W 5–3"),
    ({"for": 1, "against": 1}, "D 1–1"),
    ({"for": 2, "against": 3, "_otl": True}, "L 2–3 OT"),
    ({"for": 2, "against": 3, "_otl": False}, "L 2–3"),
    ({"for": 1, "against": 1, "_so": "shootout_win"}, "W 1–1 (pens)"),
    ({"for": float("nan"), "against": 1}, ""),
])
def test_a_result_reads_as_a_box_score_does(row, text):
    assert results._result_text(row) == text


@pytest.mark.parametrize("entry, expected", [
    ({"label": "TOUR Championship 1st"}, ("TOUR Championship", "1st")),
    ({"label": "Azerbaijan Grand Prix 19th"}, ("Azerbaijan Grand Prix", "19th")),
    ({"label": "The Open T12"}, ("The Open", "T12")),
    ({"label": "Memorial Tournament MC"}, ("Memorial Tournament", "MC")),
    ({"label": "Something unplaced"}, ("Something unplaced", "")),
    ({"name": "Dutch Grand Prix", "finish": "1st", "label": "x"}, ("Dutch Grand Prix", "1st")),
])
def test_a_finish_is_split_from_an_older_line(entry, expected):
    assert results._name_and_finish(entry) == expected


def test_a_figure_is_never_negative_zero():
    assert results._points(-0.04) == "0.0"
    assert results._points(-0.06) == "-0.1"


def test_a_club_match_is_grouped_by_its_competition_not_its_round():
    assert results._competition("efl_cup", "English Carabao Cup third round",
                                "Premier League") == "EFL Cup"
    assert results._competition("epl", "English Premier League 2026 27",
                                "Premier League") == "Premier League"
    assert results._competition("ucl", "UEFA Champions League league phase",
                                "Premier League") == "Champions League"
    assert results._competition("mls", "MLS Cup Playoffs Round One", "MLS") == "Playoffs"


def test_an_nfl_club_list_sums_to_what_its_games_earned():
    from whul.scoring import nfl

    schedule = pd.DataFrame({
        "season": [2026, 2026], "game_type": ["REG", "REG"],
        "gameday": ["2026-09-13", "2026-09-20"],
        "home_team": ["SEA", "ARI"], "away_team": ["NE", "SEA"],
        "home_score": [13, 7], "away_score": [10, 31], "div_game": [0, 1],
    })
    priced = results._priced_games("NFL", schedule, {})
    mine = priced[priced["team"] == "SEA"]
    assert list(mine["opponent"]) == ["NE", "ARI"]
    assert list(mine["home"]) == [True, False]
    total = nfl.team_game_points(nfl._team_games(schedule))
    assert mine["points"].sum() == pytest.approx(
        total[nfl._team_games(schedule)["team"] == "SEA"].sum())


# --- the files -----------------------------------------------------------------

def test_a_file_name_reads_the_same_as_a_path_and_a_url():
    name = results.file_name("team-la-liga-atlético-madrid")
    assert re.fullmatch(r"[A-Za-z0-9_.~-]+\.json", name)
    assert name != results.file_name("team-la-liga-atletico-madrid")


def test_every_string_is_escaped_for_the_window():
    payload = results.escaped({"rows": [{"vs": "v Brighton & Hove Albion",
                                         "figs": [["1", "<b>"]]}], "count": 1})
    assert payload["rows"][0]["vs"] == "v Brighton &amp; Hove Albion"
    assert payload["rows"][0]["figs"][0][1] == "&lt;b&gt;"
    assert payload["count"] == 1


def test_every_profile_with_results_points_at_a_file_that_is_there(site):
    pages = {"index.html": "", **{f"team/{p.name}": "../"
                                  for p in (site / "team").glob("*.html")}}
    linked = 0
    for page, up in pages.items():
        html = (site / page).read_text()
        found = re.search(r'id="assetdata">(.*?)</script>', html, re.DOTALL)
        if not found:
            continue
        for profile in json.loads(found.group(1)).values():
            held = profile.get("results")
            if not held:
                continue
            assert held["src"].startswith(up + results.RESULTS_DIR + "/")
            payload = json.loads((site / held["src"][len(up):]).read_text())
            assert payload["count"] == held["count"] > 0
            linked += 1
    assert linked


def test_the_results_tab_is_drawn_and_fetched_lazily():
    from whul.site import charts

    assert "data-pane=\"results\"" in charts.SCRIPT
    assert "loadResults(pane)" in charts.SCRIPT
