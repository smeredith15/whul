"""NCAA stats API adapter tests.

The live service is unreachable from the environment this was written in, so
these exercise the parsing against a payload shaped like the API's. Its value
over ESPN is that division membership is explicit in the URL.
"""

from datetime import date

import pandas as pd

from whul.sources import ncaa_api
from whul.scoring.ncaa import score_football


PAYLOAD = {
    "games": [
        {
            "game": {
                "gameID": "6301234",
                "gameState": "final",
                "title": "Ohio State vs UCLA",
                "home": {
                    "names": {"full": "Ohio State Buckeyes"},
                    "score": "48",
                    "conferences": [{"conferenceName": "Big Ten"}],
                },
                "away": {
                    "names": {"full": "UCLA Bruins"},
                    "score": "10",
                    "conferences": [{"conferenceName": "Big Ten"}],
                },
            }
        },
        {
            "game": {
                "gameID": "6301235",
                "gameState": "live",
                "home": {"names": {"full": "A"}, "score": "0", "conferences": []},
                "away": {"names": {"full": "B"}, "score": "0", "conferences": []},
            }
        },
    ]
}


def test_division_is_explicit_in_the_url():
    """The whole reason to prefer this over ESPN, whose teams endpoint returns
    all 760 college football programs whatever group filter is passed."""
    assert ncaa_api.SPORT_PATHS["ncaaf"] == ("football", "fbs")
    assert ncaa_api.SPORT_PATHS["ncaam"] == ("basketball-men", "d1")


def test_parse_matches_the_espn_adapter_column_shape():
    """Identical columns mean the same scoring modules work against either source."""
    rows = ncaa_api.parse_scoreboard(PAYLOAD, "ncaaf", date(2025, 11, 15))
    assert len(rows) == 2
    expected = {
        "season", "game_id", "game_date", "season_type", "completed",
        "home_team", "away_team", "home_conference", "away_conference",
        "home_score", "away_score", "notes",
    }
    assert set(rows[0]) == expected


def test_parse_extracts_scores_and_conferences():
    row = ncaa_api.parse_scoreboard(PAYLOAD, "ncaaf", date(2025, 11, 15))[0]
    assert row["home_team"] == "Ohio State Buckeyes"
    assert row["home_score"] == 48.0
    assert row["away_score"] == 10.0
    assert row["home_conference"] == "Big Ten"
    assert row["completed"] is True


def test_unfinished_games_are_marked_incomplete():
    row = ncaa_api.parse_scoreboard(PAYLOAD, "ncaaf", date(2025, 11, 15))[1]
    assert row["completed"] is False


def test_parsed_rows_feed_the_ncaa_scorer():
    """End to end: this source can drive scoring without any translation layer."""
    rows = ncaa_api.parse_scoreboard(PAYLOAD, "ncaaf", date(2025, 11, 15))
    scored = score_football(pd.DataFrame(rows), eligible={"Ohio State Buckeyes", "UCLA Bruins"})
    out = scored.set_index("team")
    assert out.loc["Ohio State Buckeyes", "wins"] == 1
    # +38 in a conference game clears the 13-point bar
    assert out.loc["Ohio State Buckeyes", "big_wins"] == 1
    assert out.loc["Ohio State Buckeyes", "conf_wins"] == 1


def test_empty_payload_is_tolerated():
    assert ncaa_api.parse_scoreboard({}, "ncaaf", date(2025, 11, 15)) == []


# --- name and conference extraction ----------------------------------------

def test_team_name_falls_back_through_the_name_forms():
    """`full` came back blank for football; `short` carried the value."""
    assert ncaa_api._team_name({"names": {"full": "Ohio State Buckeyes"}}) == "Ohio State Buckeyes"
    assert ncaa_api._team_name({"names": {"full": "", "short": "Massachusetts"}}) == "Massachusetts"
    assert ncaa_api._team_name({"names": {"char6": "AKRON"}}) == "AKRON"
    assert ncaa_api._team_name({"names": {}}) == ""


def test_conference_falls_back_through_key_names():
    assert ncaa_api._team_conference({"conferences": [{"conferenceName": "Big Ten"}]}) == "Big Ten"
    assert ncaa_api._team_conference({"conferences": [{"name": "SEC"}]}) == "SEC"
    assert ncaa_api._team_conference({"conference": "ACC"}) == "ACC"
    assert ncaa_api._team_conference({"conferences": []}) == ""


def test_probe_reports_raw_keys_when_extraction_comes_up_empty():
    """A blank name should hand back the payload keys needed to fix it, rather
    than costing another round trip to diagnose."""
    payload = {"games": [{"game": {"gameID": "1", "gameState": "final",
                                   "home": {"names": {"unknownKey": "X"}, "score": "1"},
                                   "away": {"names": {}, "score": "0"}}}]}
    rows = ncaa_api.parse_scoreboard(payload, "ncaaf", date(2025, 11, 15))
    assert rows[0]["home_team"] == ""
    inner = payload["games"][0]["game"]
    assert "names" in inner["home"]


# --- one row per game -----------------------------------------------------

def test_a_game_returned_on_several_dates_counts_once(monkeypatch, capsys):
    """The scoreboard is week-based for some sports, so walking every date
    returns the same game repeatedly. Summed, that multiplies a team's wins and
    point differential by however many days its week spans -- which looks like
    a season in which everyone played eighty games, and raises nowhere."""
    from datetime import date

    from whul.sources import ncaa_api

    game = {
        "season": 2024, "game_id": "555", "game_date": "2024-09-07",
        "season_type": 2, "completed": True,
        "home_team": "Alabama", "away_team": "Georgia",
        "home_conference": "SEC", "away_conference": "SEC",
        "home_score": 38.0, "away_score": 14.0, "notes": "",
    }
    monkeypatch.setattr(ncaa_api, "season_days", lambda l, s: [date(2024, 9, d) for d in range(1, 8)])
    monkeypatch.setattr(ncaa_api, "scoreboard", lambda l, d: {})
    monkeypatch.setattr(ncaa_api, "parse_scoreboard", lambda b, l, d: [dict(game)])

    frame = ncaa_api.load_team_results("ncaaf", [2024], verbose=True)
    assert len(frame) == 1
    assert "6 duplicate game rows dropped" in capsys.readouterr().out


def test_two_different_games_both_survive(monkeypatch):
    from datetime import date

    from whul.sources import ncaa_api

    def rows(board, league, day):
        return [{
            "season": 2024, "game_id": f"g{day.day}", "game_date": day.isoformat(),
            "season_type": 2, "completed": True,
            "home_team": "Alabama", "away_team": "Georgia",
            "home_conference": "SEC", "away_conference": "SEC",
            "home_score": 38.0, "away_score": 14.0, "notes": "",
        }]

    monkeypatch.setattr(ncaa_api, "season_days", lambda l, s: [date(2024, 9, 7), date(2024, 9, 14)])
    monkeypatch.setattr(ncaa_api, "scoreboard", lambda l, d: {})
    monkeypatch.setattr(ncaa_api, "parse_scoreboard", rows)
    assert len(ncaa_api.load_team_results("ncaaf", [2024], verbose=False)) == 2


def test_a_game_with_no_id_falls_back_to_what_identifies_it(monkeypatch):
    from datetime import date

    from whul.sources import ncaa_api

    game = {
        "season": 2024, "game_id": "", "game_date": "2024-09-07",
        "season_type": 2, "completed": True,
        "home_team": "Alabama", "away_team": "Georgia",
        "home_conference": "SEC", "away_conference": "SEC",
        "home_score": 38.0, "away_score": 14.0, "notes": "",
    }
    monkeypatch.setattr(ncaa_api, "season_days", lambda l, s: [date(2024, 9, d) for d in (7, 8)])
    monkeypatch.setattr(ncaa_api, "scoreboard", lambda l, d: {})
    monkeypatch.setattr(ncaa_api, "parse_scoreboard", lambda b, l, d: [dict(game)])
    assert len(ncaa_api.load_team_results("ncaaf", [2024], verbose=False)) == 1


def test_a_season_that_crosses_new_year_is_not_cut_in_half():
    """College football runs August to January. Labelled by the calendar year,
    a thirteen-win season becomes eleven wins in one season and two bowl games
    in the next, and the benchmark is drawn from half-seasons nobody played."""
    from datetime import date

    from whul.sources import ncaa_api

    payload = {"games": [{"game": {
        "gameID": "1",
        "home": {"names": {"short": "Alabama"}, "score": "38", "conferences": []},
        "away": {"names": {"short": "Georgia"}, "score": "14", "conferences": []},
        "gameState": "final",
    }}]}

    autumn = ncaa_api.parse_scoreboard(payload, "ncaaf", date(2024, 11, 9))
    bowls = ncaa_api.parse_scoreboard(payload, "ncaaf", date(2025, 1, 9))
    assert autumn[0]["season"] == bowls[0]["season"] == 2025


def test_a_season_inside_one_calendar_year_is_unchanged():
    from datetime import date

    from whul.sources import ncaa_api

    payload = {"games": [{"game": {
        "gameID": "1",
        "home": {"names": {"short": "LSU"}, "score": "7", "conferences": []},
        "away": {"names": {"short": "Texas"}, "score": "3", "conferences": []},
        "gameState": "final",
    }}]}
    rows = ncaa_api.parse_scoreboard(payload, "ncaabaseball", date(2025, 4, 12))
    assert rows[0]["season"] == 2025
