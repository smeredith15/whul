"""Source adapters for the individual sports.

The feeds themselves cannot be reached from here, so these exercise the parsing
against recorded response shapes -- which is where the adapters have actually
broken before (a value nested one level deeper than the code looked).
"""

import pandas as pd
import pytest

from whul.sources import espn_individual as espn_ind
from whul.sources import jolpica, tennis_ledger


# --- ESPN golf and racing --------------------------------------------------

def test_the_field_is_found_under_competitions():
    payload = {"competitions": [{"competitors": [{"athlete": {"displayName": "Rory McIlroy"}}]}]}
    assert len(espn_ind._competitors(payload)) == 1


def test_the_field_is_found_under_a_top_level_leaderboard():
    """Golf summaries have shipped the field under 'leaderboard' as well as
    'competitions'; reading only one location loses the other outright."""
    payload = {"leaderboard": [{"competitors": [{"athlete": {"displayName": "Rory McIlroy"}}]}]}
    assert len(espn_ind._competitors(payload)) == 1


def test_the_field_is_found_under_the_header():
    payload = {"header": {"competitions": [{"competitors": [{"athlete": {"name": "Kyle Larson"}}]}]}}
    assert len(espn_ind._competitors(payload)) == 1


def test_an_empty_response_yields_no_field_rather_than_raising():
    assert espn_ind._competitors({}) == []
    assert espn_ind._competitors({"competitions": [{}]}) == []


def test_the_athlete_name_is_read_from_whichever_key_carries_it():
    assert espn_ind._athlete_name({"athlete": {"displayName": "A"}}) == "A"
    assert espn_ind._athlete_name({"athlete": {"fullName": "B"}}) == "B"
    assert espn_ind._athlete_name({"displayName": "C"}) == "C"
    assert espn_ind._athlete_name({}) == ""


def test_a_tie_keeps_its_t_prefix_out_of_the_adapter():
    """Scoring decides what a tie is worth; collapsing 'T12' to 12 here would
    lose the distinction before anything can use it."""
    entry = {"status": {"position": {"displayName": "T12"}}}
    assert espn_ind._position(entry) == "T12"


def test_a_missed_cut_comes_through_as_a_status_not_a_place():
    entry = {"status": {"type": {"shortDetail": "CUT"}}}
    assert espn_ind._position(entry) == "CUT"


def test_the_position_falls_back_to_finishing_order():
    assert espn_ind._position({"order": 3}) == "3"


def test_only_completed_events_are_scored():
    """A Thursday leaderboard would credit the first-round leader with a win."""
    assert espn_ind._is_final({"status": {"type": {"completed": True}}}) is True
    assert espn_ind._is_final({"status": {"type": {"completed": False}}}) is False
    assert espn_ind._is_final({}) is False


def test_season_shapes_are_tried_most_specific_first():
    variants = espn_ind.scoreboard_variants(2025)
    assert variants[0] == {"dates": "2025", "limit": 200}
    assert {"dates": "2025"} in variants
    assert any("-" in v["dates"] for v in variants)


def test_every_registered_league_has_a_path():
    for league in ("pga", "nascar", "f1"):
        sport, path = espn_ind.LEAGUE_PATHS[league]
        assert sport and path


def test_golf_results_flow_into_scoring(monkeypatch):
    """End to end on the parsing side: a recorded ESPN shape has to reach the
    scorer as the columns it expects."""
    from whul.scoring import golf

    event = {"id": "401", "name": "Masters Tournament", "date": "2026-04-12T18:00Z",
             "status": {"type": {"completed": True}}}
    summary = {"competitions": [{"competitors": [
        {"athlete": {"displayName": "Winner"}, "status": {"position": {"displayName": "1"}}},
        {"athlete": {"displayName": "Runner Up"}, "status": {"position": {"displayName": "T2"}}},
        {"athlete": {"displayName": "Missed"}, "status": {"type": {"shortDetail": "CUT"}}},
    ]}]}
    monkeypatch.setattr(espn_ind, "season_events", lambda league, season: [event])
    monkeypatch.setattr(espn_ind, "event_summary", lambda league, event_id: summary)

    raw = espn_ind.load_results("pga", [2026], verbose=False)
    assert list(raw["player"]) == ["Winner", "Runner Up", "Missed"]

    scored = golf.score_players(raw, min_events=1)
    assert scored.set_index("player").loc["Winner", "total_points"] == 500 * golf.MAJOR_MULTIPLIER
    # The missed cut has no position, so it never reaches the totals.
    assert "Missed" not in set(scored["player"])


# --- Jolpica / Ergast ------------------------------------------------------

def test_races_and_totals_are_read_from_the_ergast_envelope():
    payload = {"MRData": {"total": "480", "RaceTable": {"Races": [{"raceName": "Bahrain"}]}}}
    assert jolpica._total(payload) == 480
    assert len(jolpica._races(payload)) == 1


def test_a_missing_envelope_is_empty_rather_than_an_error():
    assert jolpica._races({}) == []
    assert jolpica._total({}) == 0


def test_driver_names_are_joined_from_the_two_name_fields():
    races = [{"round": "1", "raceName": "Bahrain", "date": "2026-03-08", "Results": [
        {"position": "1", "points": "25", "status": "Finished",
         "Driver": {"givenName": "Max", "familyName": "Verstappen", "driverId": "max_verstappen"}},
    ]}]
    row = jolpica._result_rows(races, 2026, "results")[0]
    assert row["driver_name"] == "Max Verstappen"
    assert row["points"] == 25
    assert row["is_sprint"] is False


def test_sprints_are_read_from_their_own_key_and_marked():
    races = [{"round": "1", "raceName": "Bahrain", "date": "2026-03-08", "SprintResults": [
        {"position": "1", "points": "8", "Driver": {"givenName": "Max", "familyName": "Verstappen"}},
    ]}]
    rows = jolpica._result_rows(races, 2026, "sprint")
    assert rows[0]["is_sprint"] is True
    assert rows[0]["points"] == 8


def test_a_retirement_is_kept_as_a_result_worth_nothing():
    """Dropping it would understate the driver's starts, which is what the
    minimum-races floor and any per-race rate depend on."""
    races = [{"round": "1", "raceName": "Bahrain", "date": "2026-03-08", "Results": [
        {"position": "20", "points": "0", "status": "Engine",
         "Driver": {"givenName": "A", "familyName": "B"}},
    ]}]
    rows = jolpica._result_rows(races, 2026, "results")
    assert len(rows) == 1
    assert rows[0]["status"] == "Engine"


def test_the_feeds_points_reach_the_scorer():
    from whul.scoring import motorsport

    races = [{"round": "1", "raceName": "Bahrain", "date": "2026-03-08", "Results": [
        {"position": "1", "points": "26", "Driver": {"givenName": "Max", "familyName": "Verstappen"}},
    ]}]
    raw = pd.DataFrame(jolpica._result_rows(races, 2026, "results"))
    assert motorsport.score_f1(raw).iloc[0]["total_points"] == 26


# --- tennis ledgers --------------------------------------------------------

def test_headers_are_cleaned_the_way_the_r_script_cleans_them():
    assert tennis_ledger.clean_names(
        ["Winner Code", "home.set.score", "Season  Year", "TOUR_TYPE_HUMAN"]
    ) == ["winner_code", "home_set_score", "season_year", "tour_type_human"]


def test_ledger_names_follow_the_scrapers_convention():
    assert tennis_ledger.ledger_path(2025, "ATP").name == "2025-atp-season.csv"


def test_season_and_tour_come_from_the_filename_when_the_file_omits_them(tmp_path):
    """A ledger without a season column would pool every year into one, and the
    filename already carries both facts."""
    path = tmp_path / "2025-atp-season.csv"
    path.write_text("Tournament,Round\nWimbledon,Final\n")
    frame = tennis_ledger.read_ledger(path, tour="atp", season=2025)
    assert list(frame["season_year"]) == [2025]
    assert list(frame["tour_type_human"]) == ["ATP Tour"]


def test_a_file_that_carries_its_own_season_is_left_alone(tmp_path):
    path = tmp_path / "2025-atp-season.csv"
    path.write_text("tournament,season_year\nWimbledon,2024\n")
    frame = tennis_ledger.read_ledger(path, tour="atp", season=2025)
    assert list(frame["season_year"]) == [2024]


def test_a_missing_ledger_is_reported_not_raised(tmp_path, capsys):
    """A season part-way through has no completed ledger yet, and the WTA file
    often lands after the ATP one."""
    (tmp_path / "2025-atp-season.csv").write_text("tournament\nWimbledon\n")
    frame = tennis_ledger.load_matches([2025], directory=tmp_path)
    assert len(frame) == 1
    assert "missing ledgers" in capsys.readouterr().out


def test_no_ledgers_at_all_is_an_empty_frame(tmp_path):
    assert tennis_ledger.load_matches([2025], directory=tmp_path).empty


def test_required_columns_are_named_when_absent():
    frame = pd.DataFrame({"tournament": ["Wimbledon"]})
    gaps = tennis_ledger.missing_columns(frame)
    assert "winner_code" in gaps
    assert "tournament" not in gaps


def test_the_probe_says_which_ledgers_are_absent(tmp_path):
    report = tennis_ledger.probe([2025], tmp_path)
    assert report["stages"]["files"]["ok"] is False
    assert set(report["stages"]["files"]["absent"]) == {"atp-2025", "wta-2025"}


def test_the_probe_scores_a_ledger_it_can_read(tmp_path):
    (tmp_path / "2025-atp-season.csv").write_text(
        "tournament,round,home_name,away_name,winner_code,home_set_score,"
        "away_set_score,status,date_timestamp\n"
        "Wimbledon,Round of 128,Sinner,Alcaraz,1,3,0,FINISHED,1\n"
    )
    report = tennis_ledger.probe([2025], tmp_path)
    assert report["stages"]["read"]["ok"] is True
    assert report["stages"]["score"]["ok"] is True
    assert report["stages"]["score"]["top"][0]["player"] == "Sinner"


def test_the_probe_names_the_columns_that_did_arrive(tmp_path):
    """A scraper rename must say what it sent, not only that something is
    missing -- that is what makes the output actionable."""
    (tmp_path / "2025-atp-season.csv").write_text("event,winner\nWimbledon,Sinner\n")
    report = tennis_ledger.probe([2025], tmp_path)
    assert report["stages"]["read"]["ok"] is False
    assert "winner_code" in report["stages"]["read"]["missing_required"]
    assert "event" in report["stages"]["read"]["columns"]
