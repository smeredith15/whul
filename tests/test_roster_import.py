"""Reading the draft spreadsheet."""

from datetime import date

import pandas as pd
import pytest

from whul import roster_import
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


def _sheet(rows):
    return pd.DataFrame(
        rows, columns=["Manager", "Asset_Type", "Name", "League", "Category"]
    )


def test_a_pick_that_moved_stops_scoring_for_its_old_manager():
    """The shape the live roster was actually in. LS held three NBA players and
    now holds two, so slot 3 is not in the sheet at all and nothing overwrote
    it -- Shai Gilgeous-Alexander stayed open there while also being assigned
    to JM, and scored for both managers.

    A same-slot swap corrects itself, because the occupancy is keyed on the
    slot and the season's start. It is the slot that falls off the end of a
    shortened list that keeps its occupant.
    """
    from whul.store import open_store, rosters

    store = open_store(":memory:")
    picks, _ = roster_import.plan(_sheet([
        ["LS", "Player", "Jalen Brunson", "NBA", "NBA"],
        ["LS", "Player", "Luka Doncic", "NBA", "NBA"],
        ["LS", "Player", "Shai Gilgeous-Alexander", "NBA", "NBA"],
    ]))
    roster_import.apply(store, picks, "2026-27")

    picks, _ = roster_import.plan(_sheet([
        ["LS", "Player", "Jalen Brunson", "NBA", "NBA"],
        ["LS", "Player", "Luka Doncic", "NBA", "NBA"],
        ["JM", "Player", "Shai Gilgeous-Alexander", "NBA", "NBA"],
    ]))
    written, dropped = roster_import.apply(store, picks, "2026-27")

    assert written == 3
    assert len(dropped) == 1  # LS's now-orphaned third slot
    assert rosters.double_rostered(store, "2026-27").empty
    where = store.query(
        "SELECT s.manager_id FROM slot_occupancy o "
        "JOIN roster_slots s ON s.slot_id = o.slot_id "
        "WHERE o.asset_id = 'player-nba-shai-gilgeous-alexander' "
        "AND o.end_date IS NULL"
    )
    assert list(where["manager_id"]) == ["JM"]


def test_re_importing_an_unchanged_sheet_takes_nothing_back():
    """The cleanup must fire on a move and never on an ordinary nightly run,
    which re-imports the same sheet every time."""
    from whul.store import open_store

    store = open_store(":memory:")
    picks, _ = roster_import.plan(_sheet([
        ["LS", "Player", "Jalen Brunson", "NBA", "NBA"],
        ["JM", "Player", "Jayson Tatum", "NBA", "NBA"],
    ]))
    roster_import.apply(store, picks, "2026-27")
    assert roster_import.apply(store, picks, "2026-27")[1] == []


def test_a_dated_trade_survives_the_nightly_re_import():
    """The import may take back only what it wrote. A trade entered through the
    admin page carries its own note and effective date, and re-importing a
    sheet that predates it must not quietly undo the correction."""
    from datetime import date

    from whul.store import open_store, rosters

    store = open_store(":memory:")
    picks, _ = roster_import.plan(_sheet([
        ["LS", "Player", "Anthony Edwards", "NBA", "NBA"],
    ]))
    roster_import.apply(store, picks, "2026-27")
    store.upsert("assets", [{
        "asset_id": "player-nba-someone-else", "asset_type": "Player",
        "display_name": "Someone Else", "league": "NBA", "role": "",
        "norm_key": "NBA", "active": 1, "created_at": "2026-08-21",
    }], keys=("asset_id",))
    slot = store.query(
        "SELECT slot_id FROM roster_slots WHERE manager_id = 'LS' "
        "AND category = 'NBA' AND asset_type = 'Player' AND slot_index = 1"
    ).loc[0, "slot_id"]
    rosters.release(store, slot, date(2026, 10, 1))
    rosters.assign(store, slot, "player-nba-someone-else", date(2026, 10, 2),
                   note="trade")

    # The same sheet again: it still names Edwards, who is no longer there.
    roster_import.apply(store, picks, "2026-27")

    kept = store.query(
        "SELECT asset_id, note FROM slot_occupancy WHERE slot_id = ? "
        "AND end_date IS NULL", (slot,))
    assert list(kept["asset_id"]) == ["player-nba-someone-else"]
    assert list(kept["note"]) == ["trade"]


def test_two_teams_sharing_a_name_are_reported_rather_than_merged():
    """Michigan's men's and women's sides were both entered as NCAAM, so both
    became one asset filling two managers' slots and scoring for each. Only the
    sheet knows which was meant, so this is said rather than guessed -- and it
    does not block the other 283 picks."""
    report = roster_import.plan(_sheet([
        ["SS", "Team", "Michigan Wolverines", "NCAAM", "NCAAM"],
        ["JM", "Team", "Michigan Wolverines", "NCAAM", "NCAAW"],
    ]))[1]
    assert report.problems == []
    assert len(report.warnings) == 1
    assert "fills 2 slots at once" in report.warnings[0]
    assert "SS/NCAAM#1" in report.warnings[0]
    assert "JM/NCAAW#1" in report.warnings[0]


def test_a_team_column_is_not_read_as_the_manager():
    """"team" is an alias for the manager column and for the affiliation one.
    Without claim-tracking the same column answers for both, and a sheet with a
    Manager column and a Team column reads the club as the owner of every
    pick."""
    import pandas as pd

    from whul.roster_import import plan

    frame = pd.DataFrame([{
        "Manager": "JM", "Category": "NFL", "Asset_Type": "Player",
        "Name": "Bijan Robinson", "League": "NFL", "Team": "Atlanta Falcons",
    }])
    picks, report = plan(frame)
    assert report.matched_columns["manager"] == "Manager"
    assert report.matched_columns["affiliation"] == "Team"
    assert picks[0]["manager"] == "JM"
    assert picks[0]["affiliation"] == "Atlanta Falcons"


def test_a_nationality_column_serves_the_same_field():
    """One column for both, because it answers one question -- what goes in the
    corner of this asset's picture -- and which kind of answer it is follows
    from the roster category."""
    import pandas as pd

    from whul.roster_import import plan

    frame = pd.DataFrame([{
        "Manager": "JM", "Category": "Tennis", "Asset_Type": "Player",
        "Name": "Carlos Alcaraz", "League": "ATP", "Nationality": "Spain",
    }])
    picks, _ = plan(frame)
    assert picks[0]["affiliation"] == "Spain"


def test_a_sheet_with_no_affiliation_column_still_imports():
    """Every sheet before this one had none, and they must keep working."""
    import pandas as pd

    from whul.roster_import import plan

    frame = pd.DataFrame([{
        "Manager": "JM", "Category": "NFL", "Asset_Type": "Player",
        "Name": "Bijan Robinson", "League": "NFL",
    }])
    picks, report = plan(frame)
    assert "affiliation" in report.missing_columns
    assert picks[0]["affiliation"] == ""
