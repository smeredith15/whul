"""ESPN adapter tests.

The live API is unreachable from the environment this was written in, so these
exercise the parsing against a payload shaped like ESPN's. They cannot prove the
endpoint works -- `python -m whul.cli probe nba` does that from a machine with
access.
"""

from datetime import date

import pytest

from whul.sources import espn


BOX = {
    "boxscore": {
        "players": [
            {
                "team": {"abbreviation": "BOS"},
                "statistics": [
                    {
                        "labels": ["MIN", "FG", "3PT", "FT", "REB", "AST", "STL", "BLK", "TO", "PTS", "+/-"],
                        "athletes": [
                            {
                                "athlete": {"id": "1966", "displayName": "Jayson Tatum"},
                                "position": {"abbreviation": "SF"},
                                "stats": ["38", "10-20", "4-9", "6-6", "11", "5", "2", "1", "3", "30", "+12"],
                            },
                            {
                                "athlete": {"id": "4065648", "displayName": "Did Not Play"},
                                "position": {"abbreviation": "PG"},
                                "stats": [],
                            },
                        ],
                    }
                ],
            }
        ]
    }
}


def test_parse_box_extracts_scoring_inputs():
    rows = espn._parse_box(BOX, "401", date(2026, 1, 15), 2026, espn.SEASON_TYPE_REGULAR)
    assert len(rows) == 1, "players who did not play are skipped"
    row = rows[0]
    assert row["athlete_display_name"] == "Jayson Tatum"
    assert row["points"] == "30"
    assert row["rebounds"] == "11"
    assert row["assists"] == "5"
    assert row["turnovers"] == "3"
    assert row["plus_minus"] == "+12"


def test_three_pointers_are_taken_from_the_made_side_of_the_split():
    """ESPN reports '4-9' for made-attempted; only the made count scores."""
    rows = espn._parse_box(BOX, "401", date(2026, 1, 15), 2026, espn.SEASON_TYPE_REGULAR)
    assert rows[0]["three_point_field_goals_made"] == "4"


def test_parse_box_carries_season_context():
    rows = espn._parse_box(BOX, "401", date(2026, 1, 15), 2026, espn.SEASON_TYPE_POST)
    assert rows[0]["season"] == 2026
    assert rows[0]["season_type"] == espn.SEASON_TYPE_POST
    assert rows[0]["game_date"] == "2026-01-15"
    assert rows[0]["game_id"] == "401"


def test_parse_box_tolerates_an_empty_payload():
    assert espn._parse_box({}, "1", date(2026, 1, 1), 2026, 2) == []


def test_parsed_rows_feed_the_nba_scorer():
    """The adapter's column names must match what scoring/nba.py resolves."""
    import pandas as pd

    from whul.scoring.nba import score_players

    rows = espn._parse_box(BOX, "401", date(2026, 1, 15), 2026, espn.SEASON_TYPE_REGULAR)
    box = pd.DataFrame(rows * 20)  # clear the 15-game minimum
    scored = score_players(box)
    assert len(scored) == 1
    # 30 + 11*1.2 + 5*1.5 + 2*3 + 1*3 + 3*-1 + 4*0.5 = 58.7
    # + 1.5 double-double (points and rebounds) + 0.1*12 plus-minus = 61.4 per game
    assert scored.iloc[0]["regular_points"] == pytest.approx(61.4 * 20)


def test_season_dates_spans_october_to_june():
    days = espn.season_dates(2024)
    assert days[0] == date(2023, 10, 1)
    assert days[-1] == date(2024, 6, 30)
    assert len(days) > 250


def test_season_dates_never_runs_past_today():
    """A live season stops at today; one that has not tipped off yields nothing."""
    for season in (2024, 2025, 2026, 2027, 2028):
        days = espn.season_dates(season)
        assert all(d <= date.today() for d in days), season


def test_default_probe_date_lands_in_season():
    """Yesterday is a poor default -- for much of the year it is the offseason."""
    assert espn.default_probe_date(date(2026, 9, 2)) == date(2026, 1, 15)
    assert espn.default_probe_date(date(2026, 1, 3)) == date(2025, 1, 15)
    assert espn.default_probe_date(date(2026, 1, 15)) == date(2026, 1, 15)
