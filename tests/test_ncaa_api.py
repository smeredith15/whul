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
