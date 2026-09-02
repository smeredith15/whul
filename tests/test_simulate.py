"""The placeholder league."""

from datetime import date

import pytest

from whul import pipeline, simulate
from whul.config.league import ALL_SLOTS, SEASON, active_slots
from whul.store import open_store

END = date(2026, 11, 30)


@pytest.fixture(scope="module")
def simulated():
    store = open_store(":memory:")
    summary = simulate.generate(store, seed=2026, end=END, verbose=False)
    return store, summary


# --- kept apart from the real season ---------------------------------------

def test_the_simulated_season_is_not_the_real_one():
    """They must never merge, and purging must never need untangling."""
    assert simulate.SIM_SEASON != SEASON.label
    assert simulate.SIM_SEASON.startswith(SEASON.label)


def test_every_invented_asset_is_marked_as_one(simulated):
    store, _ = simulated
    ids = store.query("SELECT asset_id FROM assets")["asset_id"]
    assert (ids.str.startswith(simulate.SIM_PREFIX)).all()


def test_the_season_records_that_it_is_simulated(simulated):
    """So a view that cares can say so rather than presenting it as real."""
    store, _ = simulated
    note = store.scalar(
        "SELECT value FROM admin_overrides WHERE scope = 'simulation' AND season = ?",
        (simulate.SIM_SEASON,),
    )
    assert note == "whul.simulate"


def test_purging_removes_everything_it_created(simulated):
    store = open_store(":memory:")
    simulate.generate(store, seed=1, end=date(2026, 9, 30), verbose=False)
    simulate.purge(store)
    for table in ("standings_snapshots", "slot_scores", "daily_scores", "roster_slots"):
        assert store.scalar(f"SELECT COUNT(*) FROM {table}") == 0
    assert store.scalar("SELECT COUNT(*) FROM assets") == 0


def test_purging_leaves_a_real_season_alone():
    """The whole point of the separate label."""
    from whul.store import rosters

    store = open_store(":memory:")
    simulate.generate(store, seed=1, end=date(2026, 9, 30), verbose=False)
    rosters.add_manager(store, "real")
    rosters.create_slots(store, "real", SEASON.label)
    simulate.purge(store)
    assert store.scalar(
        "SELECT COUNT(*) FROM roster_slots WHERE season = ?", (SEASON.label,)
    ) > 0


# --- the shape is real even though the players are not ---------------------

def test_the_roster_matches_the_league_template(simulated):
    store, summary = simulated
    expected = sum(g.cap for g in active_slots(ALL_SLOTS)) * len(simulate.MANAGERS)
    assert summary["slots"] == expected == 300


def test_the_counting_slots_are_the_starter_counts(simulated):
    """47 per manager, which is what caps the season at 4,700."""
    store, _ = simulated
    bars = pipeline.contributions(store, simulate.SIM_SEASON, END)
    counting = sum(g.starters for g in active_slots(ALL_SLOTS)) * len(simulate.MANAGERS)
    assert int(bars["counts"].sum()) == counting == 235


def test_no_asset_is_drafted_twice(simulated):
    """The rollup would count it for two managers at once."""
    store, _ = simulated
    occupants = store.query(
        "SELECT o.asset_id FROM slot_occupancy o JOIN roster_slots s "
        "ON s.slot_id = o.slot_id WHERE s.season = ? AND o.start_date = ?",
        (simulate.SIM_SEASON, SEASON.start.isoformat()),
    )
    assert occupants["asset_id"].is_unique


def test_every_manager_is_filled(simulated):
    store, _ = simulated
    filled = store.query(
        "SELECT s.manager_id, COUNT(*) AS n FROM roster_slots s "
        "JOIN slot_occupancy o ON o.slot_id = s.slot_id "
        "WHERE s.season = ? GROUP BY s.manager_id",
        (simulate.SIM_SEASON,),
    )
    assert set(filled["manager_id"]) == set(simulate.MANAGERS)
    assert (filled["n"] >= 60).all()


def test_trades_happen_and_leave_no_overlap(simulated):
    """Accrual splitting needs something to split, and an overlap would
    double-count."""
    from whul.store import rosters

    store, summary = simulated
    assert summary["trades"] > 0
    assert rosters.overlaps(store, simulate.SIM_SEASON).empty


def test_a_trade_only_ever_swaps_matching_slots(simulated):
    """A team slot cannot hold a player."""
    store, _ = simulated
    mismatched = store.query(
        "SELECT COUNT(*) AS n FROM slot_occupancy o "
        "JOIN roster_slots s ON s.slot_id = o.slot_id "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE s.season = ? AND (a.asset_type != s.asset_type OR a.league != s.category)",
        (simulate.SIM_SEASON,),
    )
    assert mismatched.iloc[0]["n"] == 0


# --- what the app needs to draw --------------------------------------------

def test_the_progression_has_a_point_per_manager_per_day(simulated):
    store, summary = simulated
    series = pipeline.progression(store, simulate.SIM_SEASON)
    assert series["as_of"].nunique() == summary["days"]
    assert len(series) == summary["days"] * len(simulate.MANAGERS)


def test_scores_never_go_backwards(simulated):
    """Season-to-date totals only accumulate. A dip would mean the accrual is
    losing points, not that a player had a bad week."""
    store, _ = simulated
    series = pipeline.progression(store, simulate.SIM_SEASON)
    for _, run in series.groupby("manager_id"):
        totals = run.sort_values("as_of")["total"].tolist()
        assert totals == sorted(totals), "a manager's total fell"


def test_the_race_is_close_enough_to_be_worth_drawing(simulated):
    """Five identical managers would make every chart a straight line; five
    wildly different ones would make the scale useless."""
    store, _ = simulated
    series = pipeline.progression(store, simulate.SIM_SEASON)
    final = series[series["as_of"] == series["as_of"].max()]["total"]
    spread = (final.max() - final.min()) / final.mean()
    assert 0.005 < spread < 0.5, f"spread was {spread:.3f}"


def test_the_contribution_bars_break_down_by_category(simulated):
    store, _ = simulated
    bars = pipeline.contributions(store, simulate.SIM_SEASON, END)
    assert set(bars["manager_id"]) == set(simulate.MANAGERS)
    assert len(set(bars["category"])) > 10


def test_the_run_is_reproducible():
    """A screenshot taken today should still match the data tomorrow."""
    results = []
    for _ in range(2):
        store = open_store(":memory:")
        simulate.generate(store, seed=7, end=date(2026, 9, 30), verbose=False)
        series = pipeline.progression(store, simulate.SIM_SEASON)
        results.append(series[series["as_of"] == series["as_of"].max()]["total"].tolist())
    assert results[0] == results[1]


def test_a_different_seed_gives_a_different_league():
    stores = []
    for seed in (1, 2):
        store = open_store(":memory:")
        simulate.generate(store, seed=seed, end=date(2026, 9, 30), verbose=False)
        series = pipeline.progression(store, simulate.SIM_SEASON)
        stores.append(series[series["as_of"] == series["as_of"].max()]["total"].tolist())
    assert stores[0] != stores[1]
