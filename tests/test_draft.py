"""What the auction did, read off the bid log.

Nothing here scores a manager. These check that the five things that separate
one $200 buy from another -- what was paid, what the field would have paid, how
contested it was, when it went, and what the buyer still needed -- come out of
the log correctly.
"""

import pandas as pd

from whul import draft
from whul.store import open_store, rosters

SEASON = "2026-27"


def _store(tmp_path, bids=()):
    store = open_store(str(tmp_path / "d.sqlite3"))
    for manager in {b[4] for b in bids}:
        rosters.add_manager(store, manager)
    rows = []
    for name, league, category, asset_type, manager, bid, status, rnd, asset in bids:
        rows.append({
            "season": SEASON, "round": rnd, "name_key": name.lower(),
            "league": league, "manager_id": manager, "asset_id": asset,
            "name": name, "asset_type": asset_type, "category": category,
            "bid": bid, "status": status, "note": "",
            "recorded_at": "2026-08-21",
        })
    if rows:
        store.upsert("draft_bids", rows,
                     keys=("season", "round", "name_key", "league", "manager_id"))
    store.conn.commit()
    return store


#: Distinguishes "give it the obvious id" from "this one has none", which is
#: the normal state of a losing bid and is what several of these test.
DERIVE = object()


def bid(name, manager, amount, status="won", rnd=1, category="MLB",
        asset_type="Team", league="MLB", asset=DERIVE):
    found = f"team-{name.lower()}" if asset is DERIVE else asset
    return (name, league, category, asset_type, manager, amount, status, rnd,
            found)


# --- what the field said ----------------------------------------------------

def test_an_uncontested_win_names_nobody(tmp_path):
    """Four assets in five drew a single bid, so this is the ordinary case and
    it must not read as a rival bidding zero."""
    store = _store(tmp_path, [bid("Ohtani", "SM", 200)])
    out = draft.market(store, SEASON)
    row = out.iloc[0]
    assert row["field"] == 0 and row["bidders"] == 1 and row["contested"] == 0
    # The asset could have been had for the floor, so the rest was bid against
    # nobody -- not the whole price, and not nothing.
    assert row["premium"] == 199


def test_a_contested_win_keeps_the_best_losing_bid(tmp_path):
    store = _store(tmp_path, [
        bid("Brewers", "JM", 200),
        bid("Brewers", "SS", 15, status="outbid"),
        bid("Brewers", "TG", 9, status="outbid"),
    ])
    row = draft.market(store, SEASON).iloc[0]
    assert row["field"] == 15
    assert row["bidders"] == 3
    assert row["demand"] == 224
    assert row["premium"] == 185
    assert row["winner"] == "JM"


def test_a_win_at_the_floor_paid_no_premium(tmp_path):
    """There was nothing cheaper to bid."""
    store = _store(tmp_path, [bid("Flames", "TG", 1)])
    assert draft.market(store, SEASON).iloc[0]["premium"] == 0


def test_a_rejected_bid_never_competed(tmp_path):
    """The manager's roster was already full when it was read. It counts
    toward what they wanted and not toward what anything cost."""
    store = _store(tmp_path, [
        bid("Ohtani", "SM", 200),
        bid("Ohtani", "JM", 91, status="rejected"),
    ])
    row = draft.market(store, SEASON).iloc[0]
    assert row["bidders"] == 1 and row["contested"] == 0 and row["field"] == 0


def test_a_bid_on_an_undrafted_asset_still_makes_a_row(tmp_path):
    """Most of the market information in the file has no asset id behind it."""
    store = _store(tmp_path, [
        bid("Everton", "JM", 12, status="outbid", asset=""),
        bid("Everton", "SS", 20, status="outbid", asset=""),
    ])
    out = draft.market(store, SEASON)
    assert len(out) == 1
    assert out.iloc[0]["asset_id"] == "" and out.iloc[0]["demand"] == 32
    # Nobody won it, so there is no winner to name.
    assert out.iloc[0]["winner"] == ""


def test_the_same_name_in_two_rounds_is_two_rows(tmp_path):
    store = _store(tmp_path, [
        bid("49ers", "SM", 11, rnd=2, category="NFL", league="NFL"),
        bid("49ers", "TG", 1, rnd=3, category="NFL", league="NFL"),
    ])
    assert sorted(draft.market(store, SEASON)["round"]) == [2, 3]


def test_no_bids_is_an_empty_frame_with_its_columns(tmp_path):
    out = draft.market(_store(tmp_path), SEASON)
    assert out.empty and list(out.columns) == list(draft.MARKET_COLUMNS)


# --- what the buyer still needed --------------------------------------------

def test_pressure_counts_down_as_the_slots_fill(tmp_path):
    """Need is a fact about the moment. The MLB team cap is two, so the first
    buy is made with two open and three rounds to go and the second with one
    open and two."""
    store = _store(tmp_path, [
        bid("Brewers", "JM", 200, rnd=1),
        bid("Dodgers", "JM", 100, rnd=2),
    ])
    out = draft.pressure(store, SEASON, rounds=3).sort_values("round")
    assert list(out["open_before"]) == [2, 1]
    assert list(out["rounds_left"]) == [3, 2]
    assert [round(p, 3) for p in out["pressure"]] == [0.667, 0.5]


def test_the_last_round_leaves_one(tmp_path):
    """A slot open in the final round has to be filled now or not at all, so
    pressure there is the count itself."""
    store = _store(tmp_path, [bid("Brewers", "JM", 200, rnd=3)])
    row = draft.pressure(store, SEASON, rounds=3).iloc[0]
    assert row["rounds_left"] == 1 and row["pressure"] == 2


def test_a_losing_bid_carries_no_pressure(tmp_path):
    """It filled nothing, so it moved nothing."""
    store = _store(tmp_path, [bid("Brewers", "JM", 200, status="outbid")])
    assert draft.pressure(store, SEASON).empty


# --- what a slot was worth for nothing --------------------------------------

def _slots(rows):
    """``(category, cost, score)`` as the page's slot table shapes it."""
    return pd.DataFrame(
        [{"category": c, "cost": k, "score": s, "asset_id": f"a{i}",
          "manager_id": "JM"} for i, (c, k, s) in enumerate(rows)])


def test_a_free_pick_is_the_measured_replacement_level(tmp_path):
    """The snake round is the experiment nobody designed: a slot filled at no
    cost, in the same category as everything else."""
    floor = draft.replacement(_slots([
        ("MLB", 100, 12.0), ("MLB", 50, 8.0), ("MLB", 0, 6.0),
    ]))
    assert floor["MLB"] == 6.0


def test_two_free_picks_are_averaged(tmp_path):
    floor = draft.replacement(_slots([
        ("NFL", 80, 9.0), ("NFL", 0, 4.0), ("NFL", 0, 6.0),
    ]))
    assert floor["NFL"] == 5.0


def test_a_category_with_no_free_pick_falls_back_to_its_worst(tmp_path):
    """Which understates replacement, because the first undrafted asset sits
    below the last drafted one and not above it."""
    floor = draft.replacement(_slots([("NHL", 60, 5.0), ("NHL", 20, 2.0)]))
    assert floor["NHL"] == 2.0


def test_a_category_whose_money_bought_nothing_reads_negative(tmp_path):
    """The finding this exists for: where the paid slots do not beat the free
    ones, the spend bought less than the snake did."""
    out = draft.categories(_slots([
        ("Club Soccer Other", 200, 3.0),
        ("Club Soccer Other", 100, 5.0),
        ("Club Soccer Other", 0, 7.0),
    ])).iloc[0]
    assert out["replacement"] == 7.0
    assert out["scored"] == 4.0
    assert out["over_free"] == -3.0
    assert out["spend"] == 300
    # A free pick is not a slot the money was spread over.
    assert out["slots"] == 2 and out["per_slot"] == 150


# --- the whole thing together -----------------------------------------------

def test_a_priced_slot_picks_up_the_market_and_the_pressure(tmp_path):
    store = _store(tmp_path, [
        bid("Brewers", "JM", 200, rnd=1, asset="a0"),
        bid("Brewers", "SS", 15, status="outbid", rnd=1, asset="a0"),
    ])
    priced = _slots([("MLB", 200, 12.0)])
    out = draft.value(store, SEASON, priced)
    row = out.iloc[0]
    assert row["round"] == 1 and row["field"] == 15 and row["contested"] == 1
    assert row["premium"] == 185
    assert row["open_before"] == 2

def test_a_slot_nobody_bid_for_keeps_its_score_and_gets_blanks(tmp_path):
    """The snake picks were not bid for, and saying so is better than a zero
    that reads like a rival offer."""
    store = _store(tmp_path, [bid("Brewers", "JM", 200, asset="a0")])
    out = draft.value(store, SEASON, _slots([("MLB", 200, 12.0), ("MLB", 50, 4.0)]))
    quiet = out[out["asset_id"] == "a1"].iloc[0]
    assert pd.isna(quiet["field"]) and pd.isna(quiet["round"])


def test_replacement_is_read_off_the_whole_roster_not_the_priced_part(tmp_path):
    """`value` is handed the paid slots, which is exactly the frame the free
    picks have been filtered out of -- so the floor has to come from the other
    one or every category falls back to its worst paid asset."""
    store = _store(tmp_path)
    everything = _slots([("MLB", 200, 12.0), ("MLB", 50, 4.0), ("MLB", 0, 9.0)])
    priced = everything[everything["cost"] > 0]
    out = draft.value(store, SEASON, priced, everything=everything)
    assert list(out["replacement"]) == [9.0, 9.0]
    assert list(out["over_free"]) == [3.0, -5.0]


def test_the_rounds_do_not_look_alike(tmp_path):
    store = _store(tmp_path, [
        bid("Brewers", "JM", 200, rnd=1, asset="a0"),
        bid("Dodgers", "JM", 20, rnd=2, asset="a1"),
    ])
    out = draft.by_round(draft.value(store, SEASON, _slots([
        ("MLB", 200, 10.0), ("MLB", 20, 10.0)])))
    assert list(out["round"]) == [1, 2]
    assert list(out["spend"]) == [200, 20]
    assert [round(v, 1) for v in out["per_hundred"]] == [5.0, 50.0]
