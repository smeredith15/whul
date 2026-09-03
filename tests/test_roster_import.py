"""Reading the draft spreadsheet."""

from datetime import date

import pandas as pd
import pytest

from whul.roster_import import apply, plan, run
from whul.store import open_store, rosters

SEASON = "2026-27"


def sheet(rows):
    return pd.DataFrame(rows)


def draft_rows():
    return [
        {"Manager": "TG", "Category": "NFL", "Type": "Player", "Player": "Lamar Jackson"},
        {"Manager": "TG", "Category": "NFL", "Type": "Player", "Player": "Bijan Robinson"},
        {"Manager": "TG", "Category": "NFL", "Type": "Team", "Player": "Baltimore Ravens"},
        {"Manager": "LS", "Category": "PGA", "Type": "Player", "Player": "Scottie Scheffler"},
    ]


# --- finding the columns ---------------------------------------------------

def test_columns_are_matched_however_the_sheet_words_them():
    """A spreadsheet's headers are whatever felt natural on the day."""
    _, report = plan(sheet(draft_rows()))
    assert report.matched_columns["manager"] == "Manager"
    assert report.matched_columns["asset"] == "Player"


def test_a_longer_header_still_matches():
    rows = [{"Manager Name": "TG", "Roster Category": "NFL", "Selection": "L. Jackson"}]
    _, report = plan(sheet(rows))
    assert report.matched_columns["manager"] == "Manager Name"
    assert report.matched_columns["asset"] == "Selection"


def test_a_sheet_with_no_manager_column_is_refused_with_the_names_it_tried():
    _, report = plan(sheet([{"Who": "TG", "Category": "NFL", "Player": "X"}]))
    assert any("manager" in p for p in report.problems)
    assert any("owner" in p for p in report.problems), "says what it looked for"


def test_a_sheet_that_never_names_the_asset_is_refused():
    _, report = plan(sheet([{"Manager": "TG", "Category": "NFL"}]))
    assert any("names the drafted asset" in p for p in report.problems)


# --- a draft in progress ---------------------------------------------------

def test_a_blank_pick_is_an_open_slot_not_an_error():
    """The normal state between rounds."""
    rows = draft_rows() + [
        {"Manager": "TG", "Category": "NBA", "Type": "Player", "Player": ""},
    ]
    picks, report = plan(sheet(rows))
    assert report.problems == []
    assert report.empty > 0
    assert len(picks) == 4


def test_the_placeholders_a_spreadsheet_uses_all_read_as_blank():
    for blank in ("", "-", "TBD", "n/a", "None", "  "):
        rows = [{"Manager": "TG", "Category": "NFL", "Type": "Player", "Player": blank}]
        picks, _ = plan(sheet(rows))
        assert picks == [], f"{blank!r} should be an open slot"


def test_a_blank_row_still_consumes_its_slot():
    """The sheet lists slots in order, so a blank means that slot is unfilled --
    the pick after it belongs in the next slot, not the blank one."""
    rows = [
        {"Manager": "TG", "Category": "NFL", "Type": "Player", "Player": ""},
        {"Manager": "TG", "Category": "NFL", "Type": "Player", "Player": "Bijan Robinson"},
    ]
    picks, _ = plan(sheet(rows))
    assert picks[0]["slot_index"] == 2


def test_a_partly_drafted_sheet_reports_what_is_left():
    _, report = plan(sheet(draft_rows()))
    assert report.filled == 4
    assert report.empty > 0
    assert report.managers == {"TG": 3, "LS": 1}


# --- what it refuses -------------------------------------------------------

def test_a_category_the_league_does_not_have_is_reported_by_row():
    rows = [{"Manager": "TG", "Category": "Cricket", "Type": "Player", "Player": "X"}]
    _, report = plan(sheet(rows))
    assert any("row 2" in p and "Cricket" in p for p in report.problems)


def test_more_picks_than_the_roster_allows_is_reported():
    """Two NFL teams is the cap; a third is a mistake worth naming."""
    rows = [
        {"Manager": "TG", "Category": "NFL", "Type": "Team", "Player": f"Team {i}"}
        for i in range(3)
    ]
    _, report = plan(sheet(rows))
    assert any("roster allows 2" in p for p in report.problems)


def test_a_category_with_one_asset_type_needs_no_type_column():
    rows = [{"Manager": "LS", "Category": "PGA", "Player": "Scottie Scheffler"}]
    picks, report = plan(sheet(rows))
    assert report.problems == []
    assert picks[0]["asset_type"] == "Player"


# --- ids -------------------------------------------------------------------

def test_a_feed_id_is_used_when_the_sheet_has_one():
    """Ids over names: a name is how the league talks about a player and an id
    is how a feed does, and the two disagree constantly."""
    rows = [{"Manager": "TG", "Category": "NFL", "Type": "Player",
             "Player": "Lamar Jackson", "ESPN ID": "3916387"}]
    picks, _ = plan(sheet(rows))
    assert picks[0]["asset_id"] == "3916387"
    assert picks[0]["display_name"] == "Lamar Jackson"


def test_an_id_derived_from_a_name_is_stable():
    """Re-importing the same sheet must not create a second copy of everyone."""
    first, _ = plan(sheet(draft_rows()))
    second, _ = plan(sheet(draft_rows()))
    assert [p["asset_id"] for p in first] == [p["asset_id"] for p in second]
    assert first[0]["asset_id"] == "player-nfl-lamar-jackson"


# --- writing ---------------------------------------------------------------

def test_a_dry_run_writes_nothing(tmp_path):
    path = tmp_path / "draft.csv"
    sheet(draft_rows()).to_csv(path, index=False)
    store = open_store(":memory:")
    report = run(store, SEASON, path=path, dry_run=True)
    assert not report.written
    assert store.scalar("SELECT COUNT(*) FROM roster_slots") == 0


def test_writing_creates_managers_slots_assets_and_occupancies(tmp_path):
    path = tmp_path / "draft.csv"
    sheet(draft_rows()).to_csv(path, index=False)
    store = open_store(":memory:")
    report = run(store, SEASON, path=path, dry_run=False)
    assert report.written
    assert set(store.query("SELECT manager_id FROM managers")["manager_id"]) == {"TG", "LS"}
    assert store.scalar("SELECT COUNT(*) FROM assets") == 4
    assert store.scalar("SELECT COUNT(*) FROM slot_occupancy") == 4


def test_managers_get_their_display_name(tmp_path):
    path = tmp_path / "draft.csv"
    sheet(draft_rows()).to_csv(path, index=False)
    store = open_store(":memory:")
    run(store, SEASON, path=path, dry_run=False)
    names = dict(store.query("SELECT manager_id, display_name FROM managers").values)
    assert names["TG"] == "Tyler"


def test_every_manager_gets_a_full_set_of_slots(tmp_path):
    """Including the ones the draft has not reached: a slot has to exist before
    anyone can be put in it."""
    path = tmp_path / "draft.csv"
    sheet(draft_rows()).to_csv(path, index=False)
    store = open_store(":memory:")
    run(store, SEASON, path=path, dry_run=False)
    assert store.scalar("SELECT COUNT(*) FROM roster_slots") == 120  # 60 x 2 managers


def test_re_importing_does_not_duplicate_anything(tmp_path):
    """The sheet is updated between rounds and re-read each time."""
    path = tmp_path / "draft.csv"
    sheet(draft_rows()).to_csv(path, index=False)
    store = open_store(":memory:")
    run(store, SEASON, path=path, dry_run=False)
    run(store, SEASON, path=path, dry_run=False)
    assert store.scalar("SELECT COUNT(*) FROM slot_occupancy") == 4
    assert store.scalar("SELECT COUNT(*) FROM assets") == 4


def test_an_imported_roster_loads_for_the_rollup(tmp_path):
    path = tmp_path / "draft.csv"
    sheet(draft_rows()).to_csv(path, index=False)
    store = open_store(":memory:")
    run(store, SEASON, path=path, dry_run=False)
    slots = rosters.load_slots(store, SEASON)
    assert len(slots) == 120
    assert sum(1 for s in slots if s.occupancies) == 4


def test_a_missing_file_says_where_to_put_one():
    from pathlib import Path

    store = open_store(":memory:")
    with pytest.raises(FileNotFoundError, match="--path"):
        run(store, SEASON, path=Path("/nonexistent/draft.xlsx"))
