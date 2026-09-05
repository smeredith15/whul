"""The nightly rollup and the backfill that rebuilds it."""

from datetime import date

import pandas as pd
import pytest

from whul import pipeline
from whul.store import benchmarks as bm
from whul.store import open_store, rosters

SEASON = "2026-27"
DAYS = [date(2026, 9, 1), date(2026, 9, 8), date(2026, 9, 15)]
LAST = DAYS[-1]


def history():
    return pd.DataFrame({
        "league": ["NFL"] * 6, "role": ["QB"] * 6,
        "season": [2024, 2024, 2024, 2025, 2025, 2025],
        "total_points": [400, 380, 360, 410, 395, 370],
    })


@pytest.fixture
def league():
    """Two managers, one QB each, three weeks of scores."""
    store = open_store(":memory:")
    for manager in ("alice", "bob"):
        rosters.add_manager(store, manager)
        rosters.create_slots(store, manager, SEASON)

    version = bm.save(store, bm.compute(history(), "Player", SEASON), SEASON, version="v1")
    bm.freeze(store, version)

    store.upsert("assets", [
        {"asset_id": a, "asset_type": "Player", "display_name": a, "league": "NFL",
         "role": "QB", "norm_key": "NFL_QB", "active": 1, "created_at": "2026-08-21"}
        for a in ("qb1", "qb2")
    ], keys=("asset_id",))

    slots = {
        m: [s.slot_id for s in rosters.load_slots(store, SEASON, m) if s.category == "NFL"][0]
        for m in ("alice", "bob")
    }
    rosters.assign(store, slots["alice"], "qb1", date(2026, 8, 21))
    rosters.assign(store, slots["bob"], "qb2", date(2026, 8, 21))

    for i, day in enumerate(DAYS, start=1):
        pipeline.write_daily_scores(store, pd.DataFrame({
            "asset_id": ["qb1", "qb2"],
            "total_points": [100.0 * i, 80.0 * i],
            "scaled_score": [25.0 * i, 20.0 * i],
        }), SEASON, day, version)

    return store, slots, version


def run(store, **kwargs):
    return pipeline.backfill(
        store, SEASON, start=DAYS[0], end=LAST, today=LAST, verbose=False, **kwargs
    )


# --- writing scores --------------------------------------------------------

def test_a_league_without_a_postseason_bonus_column_still_scores():
    """DataFrame.get returns the scalar default when a column is absent, and
    filling that raises on a float -- most leagues have no such column."""
    store = open_store(":memory:")
    store.upsert("assets", [{
        "asset_id": "a", "asset_type": "Player", "display_name": "a", "league": "NFL",
        "role": "QB", "norm_key": "NFL_QB", "active": 1, "created_at": "2026-08-21",
    }], keys=("asset_id",))
    bm.freeze(store, bm.save(store, bm.compute(history(), "Player", SEASON), SEASON, version="v1"))
    written = pipeline.write_daily_scores(
        store, pd.DataFrame({"asset_id": ["a"], "scaled_score": [50.0]}),
        SEASON, DAYS[0], "v1",
    )
    assert written == 1
    assert store.scalar("SELECT postseason_bonus FROM daily_scores") == 0.0


def test_re_running_a_day_replaces_its_scores(league):
    """A formula fix is a recompute, so the same day is written more than once."""
    store, _, version = league
    pipeline.write_daily_scores(store, pd.DataFrame({
        "asset_id": ["qb1"], "total_points": [999.0], "scaled_score": [99.0],
    }), SEASON, DAYS[0], version)
    scores = store.query(
        "SELECT * FROM daily_scores WHERE asset_id = 'qb1' AND as_of = ?", (DAYS[0].isoformat(),)
    )
    assert len(scores) == 1
    assert scores.iloc[0]["scaled_score"] == 99.0


# --- the rollup ------------------------------------------------------------

def test_the_rollup_writes_a_snapshot_per_day(league):
    store, _, _ = league
    reports = run(store)
    assert len(reports) == 15, "Sep 1 to Sep 15 inclusive"
    days = store.query(
        "SELECT DISTINCT as_of FROM standings_snapshots WHERE season = ?", (SEASON,)
    )
    assert len(days) == 15


def test_standings_carry_the_cumulative_score(league):
    store, _, _ = league
    run(store)
    final = pipeline.progression(store, SEASON)
    final = final[final["as_of"] == LAST].set_index("manager_id")
    assert final.loc["alice", "total"] == pytest.approx(75.0)
    assert final.loc["bob", "total"] == pytest.approx(60.0)
    assert final.loc["alice", "rank"] == 1


def test_a_score_carries_forward_on_a_day_with_no_new_data(league):
    """Feeds do not report every day. The season-to-date figure stands until
    the next one arrives; it must not drop to zero in between."""
    store, _, _ = league
    run(store)
    series = pipeline.progression(store, SEASON)
    alice = series[series["manager_id"] == "alice"].set_index("as_of")
    assert alice.loc[date(2026, 9, 3), "total"] == pytest.approx(25.0)
    assert alice.loc[date(2026, 9, 10), "total"] == pytest.approx(50.0)


def test_the_snapshot_is_stored_not_derived(league):
    """The progression graph should show what the standings said at the time,
    including the days a since-corrected score was live."""
    store, _, version = league
    run(store)
    before = pipeline.progression(store, SEASON)
    assert len(before) == 30

    # A correction to the latest day must not disturb the earlier snapshots
    # until they are recomputed.
    pipeline.write_daily_scores(store, pd.DataFrame({
        "asset_id": ["qb1"], "total_points": [1.0], "scaled_score": [1.0],
    }), SEASON, LAST, version)
    unchanged = pipeline.progression(store, SEASON)
    early = unchanged[unchanged["as_of"] == DAYS[0]].set_index("manager_id")
    assert early.loc["alice", "total"] == pytest.approx(25.0)


# --- trades ----------------------------------------------------------------

def test_a_trade_splits_a_slot_between_its_owners(league):
    """Points earned before the trade stay with the manager who earned them."""
    store, slots, _ = league
    run(store)
    rosters.trade(store, slots["alice"], slots["bob"], "qb1", "qb2", date(2026, 9, 9))
    run(store)

    final = pipeline.progression(store, SEASON)
    final = final[final["as_of"] == LAST].set_index("manager_id")
    # alice: qb1 through Sep 8 = 50.0, then qb2 from Sep 9 = 60.0 - 40.0 = 20.0
    assert final.loc["alice", "total"] == pytest.approx(70.0)
    # bob: qb2 through Sep 8 = 40.0, then qb1 from Sep 9 = 75.0 - 50.0 = 25.0
    assert final.loc["bob", "total"] == pytest.approx(65.0)


def test_a_trade_conserves_the_total_between_the_two_managers(league):
    """Nothing is created or lost by moving an asset -- no day is counted
    twice and none goes missing."""
    store, slots, _ = league
    run(store)
    before = pipeline.progression(store, SEASON)
    before_total = before[before["as_of"] == LAST]["total"].sum()

    rosters.trade(store, slots["alice"], slots["bob"], "qb1", "qb2", date(2026, 9, 9))
    run(store)
    after = pipeline.progression(store, SEASON)
    assert after[after["as_of"] == LAST]["total"].sum() == pytest.approx(before_total)


def test_a_trade_leaves_no_overlapping_occupancy(league):
    """Two occupants on one slot would count an asset twice."""
    store, slots, _ = league
    rosters.trade(store, slots["alice"], slots["bob"], "qb1", "qb2", date(2026, 9, 9))
    assert rosters.overlaps(store, SEASON).empty


def test_an_overlap_is_reported_rather_than_silently_summed(league):
    store, slots, _ = league
    rosters.assign(store, slots["alice"], "qb2", date(2026, 9, 5))
    report = pipeline.roll_up(store, SEASON, LAST)
    assert any("overlapping" in w for w in report.warnings)


# --- guards ----------------------------------------------------------------

def test_no_frozen_benchmark_means_no_standings():
    """Scores cannot be placed on the 0-100 scale until a scale is adopted."""
    store = open_store(":memory:")
    rosters.add_manager(store, "alice")
    rosters.create_slots(store, "alice", SEASON)
    report = pipeline.roll_up(store, SEASON, LAST)
    assert any("no frozen benchmark" in w for w in report.warnings)
    assert report.managers == 0


def test_an_unfrozen_version_does_not_count_as_adopted():
    store = open_store(":memory:")
    rosters.add_manager(store, "alice")
    rosters.create_slots(store, "alice", SEASON)
    bm.save(store, bm.compute(history(), "Player", SEASON), SEASON, version="draft")
    assert any("no frozen benchmark" in w for w in pipeline.roll_up(store, SEASON, LAST).warnings)


def test_an_empty_roster_is_reported(league):
    """A season with a scale but nobody in it. The benchmark check runs first,
    so this needs one frozen for that season to reach the roster check at all."""
    store, _, _ = league
    bm.freeze(store, bm.save(
        store, bm.compute(history(), "Player", "1999-00"), "1999-00", version="old"
    ))
    assert any("no roster slots" in w for w in pipeline.roll_up(store, "1999-00", LAST).warnings)


def test_the_run_never_reaches_past_today():
    """A future day has no scores, and an empty snapshot would put a flat line
    on the progression graph."""
    days = pipeline.season_days(
        date(2026, 9, 1), date(2027, 7, 13), today=date(2026, 9, 3)
    )
    assert days[-1] == date(2026, 9, 3)


def test_a_season_that_has_not_started_yields_no_days():
    assert pipeline.season_days(
        date(2026, 9, 1), date(2027, 7, 13), today=date(2026, 8, 1)
    ) == []


# --- the app's two views ---------------------------------------------------

def test_contributions_mark_which_slots_are_scoring(league):
    """The bar chart shows what is counting and what is being carried."""
    store, _, _ = league
    run(store)
    bars = pipeline.contributions(store, SEASON, LAST)
    scoring = bars[bars["counts"] == 1]
    assert set(scoring["manager_id"]) == {"alice", "bob"}
    assert (bars["counts"] == 0).any(), "bench slots are included"


def test_contributions_carry_the_category_the_chart_breaks_down_by(league):
    store, _, _ = league
    run(store)
    bars = pipeline.contributions(store, SEASON, LAST)
    assert "category" in bars.columns
    assert "NFL" in set(bars["category"])


def test_the_progression_is_ordered_for_plotting(league):
    store, _, _ = league
    run(store)
    series = pipeline.progression(store, SEASON)
    assert list(series["as_of"]) == sorted(series["as_of"])
