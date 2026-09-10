"""The nightly rollup and the backfill that rebuilds it."""

from datetime import date

import pandas as pd
import pytest

from whul import pipeline
from whul.store import benchmarks as bm
from whul.store import open_store, rosters

SEASON = "2026-27"
#: Three weeks inside the NFL season, which opens on September 10.
#:
#: These were the 1st, 8th and 15th, from before the NFL had a start date. The
#: rollup drops scores dated before their league's season opens, so two thirds
#: of this fixture's scores stopped existing and every total here read low. The
#: gaps between the days are what the carry-forward and trade tests turn on,
#: and those are unchanged.
DAYS = [date(2026, 9, 15), date(2026, 9, 22), date(2026, 9, 29)]
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
    assert len(reports) == 15, "Sep 15 to Sep 29 inclusive"
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
    assert alice.loc[date(2026, 9, 17), "total"] == pytest.approx(25.0)
    assert alice.loc[date(2026, 9, 24), "total"] == pytest.approx(50.0)


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
    rosters.trade(store, slots["alice"], slots["bob"], "qb1", "qb2", date(2026, 9, 23))
    run(store)

    final = pipeline.progression(store, SEASON)
    final = final[final["as_of"] == LAST].set_index("manager_id")
    # alice: qb1 through Sep 22 = 50.0, then qb2 from Sep 23 = 60.0 - 40.0 = 20.0
    assert final.loc["alice", "total"] == pytest.approx(70.0)
    # bob: qb2 through Sep 22 = 40.0, then qb1 from Sep 23 = 75.0 - 50.0 = 25.0
    assert final.loc["bob", "total"] == pytest.approx(65.0)


def test_a_trade_conserves_the_total_between_the_two_managers(league):
    """Nothing is created or lost by moving an asset -- no day is counted
    twice and none goes missing."""
    store, slots, _ = league
    run(store)
    before = pipeline.progression(store, SEASON)
    before_total = before[before["as_of"] == LAST]["total"].sum()

    rosters.trade(store, slots["alice"], slots["bob"], "qb1", "qb2", date(2026, 9, 23))
    run(store)
    after = pipeline.progression(store, SEASON)
    assert after[after["as_of"] == LAST]["total"].sum() == pytest.approx(before_total)


def test_a_trade_leaves_no_overlapping_occupancy(league):
    """Two occupants on one slot would count an asset twice."""
    store, slots, _ = league
    rosters.trade(store, slots["alice"], slots["bob"], "qb1", "qb2", date(2026, 9, 23))
    assert rosters.overlaps(store, SEASON).empty


def test_an_overlap_is_reported_rather_than_silently_summed(league):
    store, slots, _ = league
    rosters.assign(store, slots["alice"], "qb2", date(2026, 9, 19))
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


def test_a_score_from_before_its_league_started_never_reaches_a_standing():
    """Ingest refuses to record one, so this only catches rows written under an
    older rule -- which is the case that matters. ``backfill`` rebuilds the
    standings from ``daily_scores`` rather than from ``raw_stats``, so nothing
    recomputes a row already written: MLS clubs drafted for 2027 kept their
    2026 points after the start date was corrected, the ingest stopped
    recording them, a backfill ran clean, and Vancouver still read ten."""
    import pandas as pd

    from whul import pipeline

    frame = pd.DataFrame([
        # Recorded when MLS had no start date, from the 2026 season.
        {"asset_id": "mls1", "date": date(2026, 9, 5), "score": 10.08,
         "league": "MLS"},
        {"asset_id": "mls1", "date": date(2027, 3, 8), "score": 4.0,
         "league": "MLS"},
        {"asset_id": "epl1", "date": date(2026, 9, 5), "score": 42.0,
         "league": "Premier League"},
    ])
    kept = pipeline._from_league_start(frame)
    assert list(zip(kept["asset_id"], kept["date"])) == [
        ("mls1", date(2027, 3, 8)),
        ("epl1", date(2026, 9, 5)),
    ]
    assert "league" not in kept.columns


def test_a_score_whose_asset_is_missing_a_league_is_kept():
    """The join is a LEFT one, so a score with no matching asset row arrives
    with a null league. That is a different fault, and dropping the score
    silently would hide it rather than report it."""
    import pandas as pd

    from whul import pipeline

    frame = pd.DataFrame([
        {"asset_id": "ghost", "date": date(2026, 9, 5), "score": 7.0,
         "league": None},
    ])
    assert list(pipeline._from_league_start(frame)["asset_id"]) == ["ghost"]


def test_the_rollup_reports_an_asset_in_two_slots():
    """The mirror of the overlap check, and the one that actually happened:
    that asks whether a slot has two occupants, this whether an occupant has
    two slots. Four assets were in this state before anything looked, each
    scoring for two managers at once."""
    from whul import pipeline
    from whul.store import open_store, rosters

    store = open_store(":memory:")
    for manager in ("LS", "JM"):
        rosters.add_manager(store, manager)
        rosters.create_slots(store, manager, "2026-27")
    store.upsert("assets", [{
        "asset_id": "p1", "asset_type": "Player", "display_name": "Traded Away",
        "league": "NBA", "role": "", "norm_key": "NBA", "active": 1,
        "created_at": "2026-08-21",
    }], keys=("asset_id",))
    slots = store.query(
        "SELECT slot_id, manager_id FROM roster_slots WHERE season = '2026-27' "
        "AND category = 'NBA' AND asset_type = 'Player' AND slot_index = 1")
    for row in slots.itertuples():
        rosters.assign(store, row.slot_id, "p1", date(2026, 8, 21), note="draft")

    warnings = pipeline._double_rostered_warnings(store, "2026-27")
    assert len(warnings) == 1
    assert "Traded Away is in 2 slots at once" in warnings[0]
    assert "JM/NBA#1" in warnings[0] and "LS/NBA#1" in warnings[0]


def test_a_clean_roster_reports_nothing():
    from whul import pipeline
    from whul.store import open_store, rosters

    store = open_store(":memory:")
    rosters.add_manager(store, "LS")
    rosters.create_slots(store, "LS", "2026-27")
    assert pipeline._double_rostered_warnings(store, "2026-27") == []


# --- restating stored days against a new scale -------------------------------


def rescore_store(tmp_path, versions, benchmarks):
    """One asset, three days, scored against the versions given."""
    import json

    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("benchmark_versions", [
        {"version": v, "season": "2026-27", "quantile": 0.99, "managers": 5,
         "computed_at": "2026-09-01T00:00:00Z",
         "frozen_at": "2026-09-01T00:00:00Z" if v == "new" else None}
        for v in ("old", "new")
    ], ["version"])
    store.upsert("benchmarks", [
        {"version": v, "asset_type": "Player", "norm_key": "La Liga",
         "benchmark": bench, "pool_size": 675, "seasons": "2021,2022"}
        for v, bench in benchmarks.items()
    ], ["version", "asset_type", "norm_key"])
    store.upsert("assets", [
        {"asset_id": "a1", "asset_type": "Player", "display_name": "Yamal",
         # The asset's league is the category. The payload below carries the
         # scoring group, and for club soccer they differ.
         "league": "La Liga", "norm_key": "La Liga", "created_at": "2026-08-21"},
    ], ["asset_id"])
    for day, version in versions.items():
        store.upsert("raw_stats", [{
            "asset_id": "a1", "league": "Club Soccer", "season": "2026-27",
            "as_of": day, "source": "soccer-players", "phase": "regular",
            "stats": json.dumps({"league": "La Liga", "role": "",
                                 "total_points": 24.0}),
            "fetched_at": f"{day}T09:00:00Z",
        }], ["asset_id", "season", "as_of", "source", "phase"])
        store.upsert("daily_scores", [{
            "asset_id": "a1", "season": "2026-27", "as_of": day,
            "league_points": 24.0,
            "scaled_score": round(24.0 / benchmarks[version] * 100, 2),
            "benchmark_version": version, "computed_at": f"{day}T09:00:00Z",
        }], ["asset_id", "season", "as_of"])
    store.conn.commit()
    return store


def run_rescore(tmp_path, **kw):
    from whul.cli import main

    args = ["rescore", "--season", "2026-27",
            "--db", str(tmp_path / "whul.sqlite3"), "--version", "new"]
    return main(args + list(kw.get("extra", [])))


def test_a_day_on_the_old_scale_is_restated(tmp_path, capsys):
    """Adopting a new scale changes what 100 means, and only the days scored
    after it know. Differenced against the day before, that reads as every
    asset in a group having a bad day."""
    from whul.store import open_store

    rescore_store(tmp_path, {"2026-09-09": "old", "2026-09-10": "new"},
                  {"old": 163.26, "new": 175.52})
    assert run_rescore(tmp_path) == 0

    scores = open_store(str(tmp_path / "whul.sqlite3")).query(
        "SELECT as_of, scaled_score, benchmark_version FROM daily_scores "
        "ORDER BY as_of")
    assert list(scores["benchmark_version"]) == ["new", "new"]
    # Both days now sit on the same divisor, so the ledger shows no move.
    assert scores["scaled_score"].nunique() == 1
    assert round(float(scores["scaled_score"].iloc[0]), 2) == 13.67


def test_the_league_comes_from_the_payload_not_the_table(tmp_path, capsys):
    """`read_stats` drops any payload column the table already has, and league
    is one. The table records the league the *source* answers for -- "Club
    Soccer" -- and the payload the group the row is scored in. Reading the
    table's, every club soccer row found no benchmark and was dropped, which
    made the restatement report the days as already correct."""
    rescore_store(tmp_path, {"2026-09-09": "old", "2026-09-10": "new"},
                  {"old": 163.26, "new": 175.52})
    run_rescore(tmp_path)
    said = capsys.readouterr().out
    assert "1 asset(s) restated" in said, "the row was found and scaled"


def test_it_refuses_when_a_day_on_the_new_scale_does_not_reproduce(
    tmp_path, capsys
):
    """A day already scored against the target version must come back the same.
    That it does not means the restatement is not doing what the scorer does,
    and every earlier day it rewrote would be wrong the same way and silently."""
    from whul.store import open_store

    store = rescore_store(tmp_path, {"2026-09-09": "old", "2026-09-10": "new"},
                          {"old": 163.26, "new": 175.52})
    # A day claiming the new version while holding a figure from the old one.
    store.conn.execute(
        "UPDATE daily_scores SET scaled_score = 99.0 WHERE as_of = '2026-09-10'")
    store.conn.commit()

    assert run_rescore(tmp_path) == 1
    assert "REFUSED" in capsys.readouterr().err
    after = open_store(str(tmp_path / "whul.sqlite3")).query(
        "SELECT scaled_score FROM daily_scores WHERE as_of = '2026-09-09'")
    assert round(float(after["scaled_score"].iloc[0]), 2) == 14.70, \
        "the earlier day was left alone"


def test_a_dry_run_writes_nothing(tmp_path, capsys):
    from whul.store import open_store

    rescore_store(tmp_path, {"2026-09-09": "old", "2026-09-10": "new"},
                  {"old": 163.26, "new": 175.52})
    assert run_rescore(tmp_path, extra=["--dry-run"]) == 0
    after = open_store(str(tmp_path / "whul.sqlite3")).query(
        "SELECT scaled_score FROM daily_scores WHERE as_of = '2026-09-09'")
    assert round(float(after["scaled_score"].iloc[0]), 2) == 14.70


# --- what is not in the total yet -------------------------------------------

def test_held_points_are_what_the_total_would_gain(league):
    """Not the sum over the counting slots. Best ball picks the counting set by
    score, so the honest figure is the difference between the standings as they
    stand and the standings with every held bonus credited."""
    store, _, version = league
    store.conn.execute(
        "UPDATE daily_scores SET held_score = 10.0 "
        "WHERE asset_id = 'qb1' AND as_of = ?", (str(LAST),))
    store.conn.commit()
    assert pipeline.held_by_manager(store, SEASON, LAST) == {"alice": 10.0, "bob": 0.0}


def spare_qbs(store, version, scores: dict[str, float]) -> None:
    """Fill alice's other NFL player slots. The category caps at four and
    starts two, so a third occupant is what puts somebody on the bench."""
    store.upsert("assets", [{
        "asset_id": a, "asset_type": "Player", "display_name": a,
        "league": "NFL", "role": "QB", "norm_key": "NFL_QB", "active": 1,
        "created_at": "2026-08-21",
    } for a in scores], keys=("asset_id",))
    slots = [s.slot_id for s in rosters.load_slots(store, SEASON, "alice")
             if s.category == "NFL" and s.asset_type == "Player"]
    for slot, asset in zip(slots[1:], scores):
        rosters.assign(store, slot, asset, date(2026, 8, 21))
    for day in DAYS:
        pipeline.write_daily_scores(store, pd.DataFrame({
            "asset_id": list(scores),
            "total_points": list(scores.values()),
            "scaled_score": list(scores.values()),
        }), SEASON, day, version)


def test_a_benched_asset_holding_points_is_still_counted(league):
    """The case the recompute exists for. Summing over today's counting set
    would report nothing for it and then let it land as a surprise in June,
    which is the whole thing this figure is meant to prevent."""
    store, _, version = league
    # alice counts qb1 (75) and qb4 (2); qb3 (1) is benched and is the one
    # sitting on a run.
    spare_qbs(store, version, {"qb3": 1.0, "qb4": 2.0})
    store.conn.execute(
        "UPDATE daily_scores SET held_score = 500.0 "
        "WHERE asset_id = 'qb3' AND as_of = ?", (str(LAST),))
    store.conn.commit()

    held = pipeline.held_by_manager(store, SEASON, LAST)
    # 75 + 2 now; 501 + 75 once it settles, because qb3 displaces qb4.
    assert held["alice"] == pytest.approx(499.0)


def test_nothing_held_is_no_figure_at_all(league):
    store, _, _ = league
    assert pipeline.held_by_manager(store, SEASON, LAST) == {}


def test_the_bench_is_what_best_ball_is_not_counting(league):
    store, _, version = league
    spare_qbs(store, version, {"qb3": 4.0, "qb4": 1.0})
    run(store)
    # qb1 and qb3 count; qb4 does not.
    assert pipeline.bench_by_manager(store, SEASON, LAST)["alice"] == pytest.approx(1.0)


def test_a_held_bonus_is_scaled_by_the_same_benchmark_as_the_score():
    """Two divisions of the same figure by the same number, written in two
    places, is how the two come to disagree."""
    from whul.normalize import apply_benchmarks

    benchmarks = pd.DataFrame({
        "asset_type": ["Player"], "norm_key": ["NFL_QB"], "benchmark": [400.0],
    })
    got = apply_benchmarks(pd.DataFrame({
        "asset_id": ["qb1"], "league": ["NFL"], "role": ["QB"],
        "total_points": [200.0], "postseason_pending": [40.0],
    }), benchmarks, "Player")
    assert got["scaled_score"].iloc[0] == pytest.approx(50.0)
    assert got["held_score"].iloc[0] == pytest.approx(10.0)


def test_a_league_that_holds_nothing_still_scores():
    from whul.normalize import apply_benchmarks

    benchmarks = pd.DataFrame({
        "asset_type": ["Player"], "norm_key": ["NFL_QB"], "benchmark": [400.0],
    })
    got = apply_benchmarks(pd.DataFrame({
        "asset_id": ["qb1"], "league": ["NFL"], "role": ["QB"],
        "total_points": [200.0],
    }), benchmarks, "Player")
    assert got["held_score"].iloc[0] == 0.0
