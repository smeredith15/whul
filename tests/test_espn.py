"""ESPN adapter tests.

The fixture below mirrors a real payload returned by the live API on 2026-01-15:
the label order is ESPN's actual order, and position sits at
``entry["athlete"]["position"]`` while ``entry["position"]`` is an empty dict --
reading the latter is what once made positions look absent.

These prove the parsing and the stat mapping. Only `python -m whul.cli probe nba`
proves the endpoint itself.
"""

from datetime import date

import pandas as pd
import pytest

from whul.sources import espn

LABELS = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK",
          "OREB", "DREB", "PF", "+/-"]

# Jaren Jackson Jr., MEM, 2026-01-15: 30 pts, 3 reb, 1 ast, 4 to, 2 stl, 2 blk,
# 3 threes, -21. Position comes back empty, exactly as the live API returns it.
JJJ = ["38", "30", "10-20", "3-9", "7-8", "3", "1", "4", "2", "2", "0", "3", "3", "-21"]

BOX = {
    "boxscore": {
        "players": [
            {
                "team": {"abbreviation": "MEM"},
                "statistics": [
                    {
                        "labels": LABELS,
                        "athletes": [
                            {
                                # ESPN populates the athlete-level position and
                                # leaves the entry-level one empty.
                                "athlete": {
                                    "id": "4277961",
                                    "displayName": "Jaren Jackson Jr.",
                                    "position": {"abbreviation": "F"},
                                },
                                "position": {},
                                "stats": JJJ,
                            },
                            {
                                "athlete": {"id": "9999", "displayName": "Did Not Play"},
                                "stats": [],
                            },
                        ],
                    }
                ],
            }
        ]
    }
}


def parse(positions=None, season_type=espn.SEASON_TYPE_REGULAR):
    return espn._parse_box(BOX, "401810433", date(2026, 1, 15), 2026, season_type, positions)


# --- stat mapping ----------------------------------------------------------

def test_stats_are_looked_up_by_label_not_position():
    """ESPN's label order is not the order the scorer needs, so lookup is by name."""
    row = parse()[0]
    assert row["athlete_display_name"] == "Jaren Jackson Jr."
    assert row["points"] == "30"
    assert row["rebounds"] == "3"
    assert row["assists"] == "1"
    assert row["turnovers"] == "4"
    assert row["steals"] == "2"
    assert row["blocks"] == "2"
    assert row["plus_minus"] == "-21"


def test_three_pointers_come_from_the_made_side_of_the_split():
    """ESPN reports '3-9' made-attempted; only the made count scores."""
    assert parse()[0]["three_point_field_goals_made"] == "3"


def test_players_who_did_not_play_are_skipped():
    assert len(parse()) == 1


def test_season_context_is_carried():
    row = parse(season_type=espn.SEASON_TYPE_POST)[0]
    assert row["season"] == 2026
    assert row["season_type"] == espn.SEASON_TYPE_POST
    assert row["game_date"] == "2026-01-15"
    assert row["game_id"] == "401810433"
    assert row["team"] == "MEM"


def test_parse_box_tolerates_an_empty_payload():
    assert espn._parse_box({}, "1", date(2026, 1, 1), 2026, 2) == []


# --- position resolution ---------------------------------------------------

def test_position_is_read_from_the_athlete_not_the_entry():
    """entry["position"] is an empty dict; the real value is one level in."""
    assert parse()[0]["athlete_position_abbreviation"] == "F"


def test_roster_map_only_fills_what_the_boxscore_omits():
    entry = {"athlete": {"id": "4277961"}}
    assert espn._position(entry, {"4277961": "PF"}) == "PF"


def test_inline_position_wins_when_present():
    entry = {"athlete": {"id": "1"}, "position": {"abbreviation": "SG"}}
    assert espn._position(entry, {"1": "PF"}) == "SG"


def test_position_found_under_the_athlete_too():
    entry = {"athlete": {"id": "1", "position": {"abbreviation": "C"}}}
    assert espn._position(entry) == "C"


def test_position_is_empty_when_nothing_supplies_it():
    assert espn._position({"athlete": {"id": "1"}}) == ""
    assert espn._position({"athlete": {"id": "1"}}, {"2": "PG"}) == ""


def test_espn_position_vocabulary_maps_to_the_normalization_groups():
    """ESPN returns generic G/F/C and hyphenated forms, not PG/SG/SF/PF."""
    from whul.normalize import assign_norm_key

    df = pd.DataFrame({"league": ["NBA"] * 5, "role": ["G", "F", "C", "G-F", "F-C"]})
    assert list(assign_norm_key(df, "Player")) == [
        "NBA_Backcourt", "NBA_Frontcourt", "NBA_Frontcourt",
        "NBA_Backcourt", "NBA_Frontcourt",
    ]


def test_an_unresolved_position_collapses_the_group():
    """The failure mode to guard against: no position means no Backcourt /
    Frontcourt split, silently, with no error anywhere."""
    from whul.normalize import assign_norm_key

    df = pd.DataFrame({"league": ["NBA"], "role": [""]})
    assert assign_norm_key(df, "Player").iloc[0] == "NBA", "the split is lost"


# --- integration with the scorer -------------------------------------------

def test_parsed_rows_feed_the_nba_scorer():
    from whul.scoring.nba import score_players

    box = pd.DataFrame(parse() * 20)  # clear the 15-game minimum
    scored = score_players(box)
    assert len(scored) == 1
    # 30 + 3*1.2 + 1*1.5 + 2*3 + 2*3 + 4*-1 + 3*0.5 = 44.6, no double-double,
    # plus 0.1 * -21 = -2.1 -> 42.5 per game
    assert scored.iloc[0]["regular_points"] == pytest.approx(42.5 * 20)
    assert scored.iloc[0]["role"] == "F"


# --- season windows --------------------------------------------------------

def test_season_dates_spans_october_to_june():
    days = espn.season_dates(2024)
    assert days[0] == date(2023, 10, 1)
    assert days[-1] == date(2024, 6, 30)
    assert len(days) > 250


def test_season_dates_never_runs_past_today():
    for season in (2024, 2025, 2026, 2027, 2028):
        assert all(d <= date.today() for d in espn.season_dates(season)), season


def test_default_probe_date_lands_in_season():
    """Yesterday is a poor default -- for much of the year it is the offseason."""
    assert espn.default_probe_date(date(2026, 9, 2)) == date(2026, 1, 15)
    assert espn.default_probe_date(date(2026, 1, 3)) == date(2025, 1, 15)
    assert espn.default_probe_date(date(2026, 1, 15)) == date(2026, 1, 15)
