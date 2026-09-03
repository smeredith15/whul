"""The local admin tool."""

from datetime import date

import pytest

from whul import admin, simulate
from whul.store import open_store, rosters

SEASON = simulate.SIM_SEASON
MANAGER_A, MANAGER_B = "TG", "LS"


@pytest.fixture
def league():
    store = open_store(":memory:")
    simulate.generate(store, seed=3, end=date(2026, 9, 30), verbose=False)
    filled = [s for s in admin.open_slots(store, SEASON) if s["asset_id"]]

    def pick(manager, category="NFL", asset_type="Player"):
        """A filled slot. The draft is not finished, so some are empty."""
        return next(
            s for s in filled
            if s["manager_id"] == manager
            and s["category"] == category
            and s["asset_type"] == asset_type
        )

    return store, pick


# --- reading the roster ----------------------------------------------------

def test_every_slot_is_listed_including_the_undrafted_ones(league):
    """A slot nobody has drafted into is exactly the one an admin needs to see."""
    store, _ = league
    slots = admin.open_slots(store, SEASON)
    assert len(slots) == 300
    assert any(s["asset_id"] is None for s in slots), "the draft is not finished"
    assert any(s["asset_id"] for s in slots)


def test_a_slot_emptied_by_a_release_still_lists(league):
    """It is a slot that needs filling, which is exactly when you want to see
    it -- dropping it from the list would hide the problem."""
    store, pick = league
    slot = pick(MANAGER_A)
    rosters.release(store, slot["slot_id"], date(2026, 9, 15))
    listed = {s["slot_id"]: s for s in admin.open_slots(store, SEASON)}
    assert slot["slot_id"] in listed
    assert listed[slot["slot_id"]]["asset_id"] is None


# --- recording a trade -----------------------------------------------------

def test_a_valid_trade_swaps_both_slots(league):
    store, pick = league
    left, right = pick(MANAGER_A), pick(MANAGER_B)
    result = admin.apply_trade(
        store, SEASON, left["slot_id"], right["slot_id"], "2026-09-15"
    )
    assert result["ok"]
    after = {s["slot_id"]: s for s in admin.open_slots(store, SEASON)}
    assert after[left["slot_id"]]["asset_id"] == right["asset_id"]
    assert after[right["slot_id"]]["asset_id"] == left["asset_id"]


def test_a_trade_leaves_no_overlapping_occupancy(league):
    """Two occupants on one slot would count an asset twice."""
    store, pick = league
    admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"], pick(MANAGER_B)["slot_id"], "2026-09-15"
    )
    assert rosters.overlaps(store, SEASON).empty


def test_the_confirmation_names_both_players_and_the_date(league):
    """A trade recorded against the wrong slot rescores every day after it, so
    the confirmation has to be checkable at a glance."""
    store, pick = league
    result = admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"], pick(MANAGER_B)["slot_id"], "2026-09-15"
    )
    assert MANAGER_A in result["message"] and MANAGER_B in result["message"]
    assert "2026-09-15" in result["message"]


def test_the_confirmation_says_what_to_rebuild(league):
    """The standings do not move until the rollup runs again."""
    store, pick = league
    result = admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"], pick(MANAGER_B)["slot_id"], "2026-09-15"
    )
    assert "rollup" in result["rebuild"] and "site" in result["rebuild"]


# --- what it refuses -------------------------------------------------------

def test_a_slot_cannot_trade_with_itself(league):
    store, pick = league
    slot = pick(MANAGER_A)["slot_id"]
    result = admin.apply_trade(store, SEASON, slot, slot, "2026-09-15")
    assert not result["ok"]
    assert any("same slot" in p for p in result["problems"])


def test_two_slots_of_one_manager_cannot_trade(league):
    store, pick = league
    result = admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"], pick(MANAGER_A, "NBA")["slot_id"],
        "2026-09-15",
    )
    assert any("same manager" in p for p in result["problems"])


def test_categories_cannot_be_crossed(league):
    """A slot only holds its own category, so a cross-category trade would put
    a golfer in a hockey slot and quietly score them there."""
    store, pick = league
    result = admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"], pick(MANAGER_B, "NHL")["slot_id"],
        "2026-09-15",
    )
    assert any("cannot swap" in p for p in result["problems"])


def test_a_player_slot_cannot_trade_with_a_team_slot(league):
    store, pick = league
    result = admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"],
        pick(MANAGER_B, "NFL", "Team")["slot_id"], "2026-09-15",
    )
    assert any("cannot swap" in p for p in result["problems"])


def test_an_unparseable_date_is_refused(league):
    store, pick = league
    result = admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"], pick(MANAGER_B)["slot_id"], "soon"
    )
    assert any("not a date" in p for p in result["problems"])


def test_an_unknown_slot_is_refused(league):
    store, pick = league
    result = admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"], "no-such-slot", "2026-09-15"
    )
    assert any("no slot" in p for p in result["problems"])


def test_an_empty_slot_has_nothing_to_trade(league):
    store, pick = league
    empty = pick(MANAGER_B)
    rosters.release(store, empty["slot_id"], date(2026, 9, 10))
    result = admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"], empty["slot_id"], "2026-09-15"
    )
    assert any("nothing to trade" in p for p in result["problems"])


def test_a_refused_trade_changes_nothing(league):
    """Validated before anything is written: a half-applied trade would leave
    an asset in two slots at once."""
    store, pick = league
    before = {s["slot_id"]: s["asset_id"] for s in admin.open_slots(store, SEASON)}
    admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"], pick(MANAGER_B, "NHL")["slot_id"],
        "2026-09-15",
    )
    after = {s["slot_id"]: s["asset_id"] for s in admin.open_slots(store, SEASON)}
    assert before == after


# --- the page --------------------------------------------------------------

def test_the_page_lists_every_filled_slot(league):
    store, _ = league
    filled = [s for s in admin.open_slots(store, SEASON) if s["asset_id"]]
    html = admin._page(store, SEASON)
    assert html.count("<option") == len(filled) * 2, "both selects"
    assert "Record a trade" in html


def test_the_page_shows_recorded_trades(league):
    store, pick = league
    admin.apply_trade(
        store, SEASON, pick(MANAGER_A)["slot_id"], pick(MANAGER_B)["slot_id"], "2026-09-15"
    )
    assert "2026-09-15" in admin._page(store, SEASON)


def test_a_failure_is_reported_on_the_page(league):
    store, pick = league
    flash = admin.apply_trade(store, SEASON, pick(MANAGER_A)["slot_id"], "nope", "2026-09-15")
    assert "Not recorded" in admin._page(store, SEASON, flash)


def test_the_server_binds_loopback_only():
    """Write access to the league must not be on the network."""
    assert admin.HOST == "127.0.0.1"
