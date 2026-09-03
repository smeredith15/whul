"""The scoring store: schema, ingest, staleness, and benchmark versioning."""

from datetime import date

import pandas as pd
import pytest

from whul.store import open_store
from whul.store import benchmarks as bm
from whul.store.db import SCHEMA_VERSION


@pytest.fixture
def store():
    return open_store(":memory:")


def asset(asset_id="nfl-lamar-jackson", league="NFL", role="QB", asset_type="Player"):
    return {
        "asset_id": asset_id, "asset_type": asset_type,
        "display_name": asset_id, "league": league, "role": role,
        "norm_key": f"{league}_{role}" if role else league,
        "active": 1, "created_at": "2026-08-21",
    }


def add_asset(store, **kwargs):
    store.upsert("assets", [asset(**kwargs)], keys=("asset_id",))


# --- schema ----------------------------------------------------------------

def test_the_schema_creates_every_table_the_pipeline_writes():
    s = open_store(":memory:")
    tables = {r["name"] for r in s.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )}
    for expected in (
        "assets", "asset_aliases", "raw_stats", "source_status",
        "benchmark_versions", "benchmarks", "daily_scores",
        "managers", "roster_slots", "slot_occupancy",
        "slot_scores", "standings_snapshots", "admin_overrides",
    ):
        assert expected in tables, expected


def test_applying_the_schema_twice_is_safe(store):
    """It runs on every startup, and is how a new table reaches an existing
    database."""
    from whul.store.db import apply_schema

    assert apply_schema(store.conn) == SCHEMA_VERSION
    assert store.scalar("SELECT COUNT(*) FROM assets") == 0


def test_foreign_keys_are_enforced(store):
    """Off by default in SQLite, and the schema is full of them -- a score for
    an asset that does not exist is a bug worth failing on."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO daily_scores (asset_id, season, as_of, league_points, "
            "scaled_score, benchmark_version, computed_at) "
            "VALUES ('ghost', '2026-27', '2026-09-01', 1, 1, 'nope', 'now')"
        )


def test_an_unknown_asset_type_is_rejected(store):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.upsert("assets", [asset() | {"asset_type": "Coach"}], keys=("asset_id",))


# --- ingest ----------------------------------------------------------------

def test_stats_round_trip_through_the_json_payload(store):
    """A feed adding a stat should need no migration."""
    add_asset(store)
    store.record_stats(
        [{"asset_id": "nfl-lamar-jackson", "passing_yards": 3200, "passing_tds": 28}],
        source="nflverse", season="2026-27", as_of=date(2026, 12, 1), league="NFL",
    )
    back = store.read_stats("2026-27", date(2026, 12, 1))
    assert back.iloc[0]["passing_yards"] == 3200
    assert back.iloc[0]["passing_tds"] == 28


def test_re_running_a_day_replaces_it_rather_than_duplicating(store):
    """The nightly job is re-run after a fix more often than not."""
    add_asset(store)
    for yards in (3200, 3250):
        store.record_stats(
            [{"asset_id": "nfl-lamar-jackson", "passing_yards": yards}],
            source="nflverse", season="2026-27", as_of="2026-12-01", league="NFL",
        )
    back = store.read_stats("2026-27", "2026-12-01")
    assert len(back) == 1
    assert back.iloc[0]["passing_yards"] == 3250


def test_two_sources_for_one_asset_day_are_kept_apart(store):
    """MLB reads two feeds for the same player; neither should overwrite the
    other."""
    add_asset(store, asset_id="mlb-ohtani", league="MLB", role="Batter")
    for source in ("statsapi", "sabermetrics"):
        store.record_stats(
            [{"asset_id": "mlb-ohtani", "value": source}],
            source=source, season="2026-27", as_of="2027-06-01", league="MLB",
        )
    assert len(store.read_stats("2026-27", "2027-06-01")) == 2


def test_a_row_without_an_asset_id_is_skipped(store):
    assert store.record_stats(
        [{"passing_yards": 100}], source="x", season="2026-27",
        as_of="2026-12-01", league="NFL",
    ) == 0


def test_postseason_rows_do_not_overwrite_regular_season_ones(store):
    """They are separate phases of the same asset's season and both score."""
    add_asset(store)
    for phase in ("regular", "postseason"):
        store.record_stats(
            [{"asset_id": "nfl-lamar-jackson", "yards": 10}],
            source="nflverse", season="2026-27", as_of="2027-01-15",
            league="NFL", phase=phase,
        )
    assert len(store.read_stats("2026-27", "2027-01-15")) == 2


# --- staleness -------------------------------------------------------------

def test_a_feed_that_stopped_updating_is_reported(store):
    """The dangerous failure is not a crash: the standings freeze and still
    look plausible."""
    add_asset(store)
    store.record_stats(
        [{"asset_id": "nfl-lamar-jackson", "yards": 1}],
        source="nflverse", season="2026-27", as_of="2026-12-01", league="NFL",
    )
    assert store.stale_sources("2026-12-02").empty
    stale = store.stale_sources("2026-12-10")
    assert list(stale["source"]) == ["nflverse"]


def test_a_source_that_failed_is_reported_even_when_recent(store):
    store.record_source_status("espn", "NBA", ok=False, message="403")
    stale = store.stale_sources(date.today())
    assert list(stale["source"]) == ["espn"]


# --- benchmark versioning --------------------------------------------------

def scored_history():
    return pd.DataFrame({
        "league": ["NFL"] * 8,
        "role": ["QB"] * 4 + ["TE"] * 4,
        "season": [2024, 2024, 2025, 2025] * 2,
        "total_points": [400, 380, 410, 395, 220, 200, 230, 210],
    })


def test_a_benchmark_carries_the_pool_it_was_drawn_from():
    """Four players and sixty players deserve different amounts of trust, and
    only the row can say which this was."""
    bench = bm.compute(scored_history(), "Player", "2026-27")
    assert set(bench["norm_key"]) == {"NFL_QB", "NFL_TE"}
    assert (bench["pool_size"] > 0).all()
    assert bench.iloc[0]["seasons"] == "2024,2025"


def test_a_saved_version_is_not_yet_the_live_one(store):
    """Computing a set and adopting it are separate acts, so a set can be
    compared against the live one before anything scores against it."""
    version = bm.save(store, bm.compute(scored_history(), "Player", "2026-27"), "2026-27")
    assert bm.active_version(store, "2026-27") is None
    assert bm.get_version(store, version).is_frozen is False


def test_freezing_makes_a_version_live(store):
    version = bm.save(store, bm.compute(scored_history(), "Player", "2026-27"), "2026-27")
    bm.freeze(store, version)
    assert bm.active_version(store, "2026-27").version == version


def test_freezing_twice_does_not_restamp_the_adoption_date(store):
    """The nightly job calls this every run."""
    version = bm.save(store, bm.compute(scored_history(), "Player", "2026-27"), "2026-27")
    first = bm.freeze(store, version).frozen_at
    assert bm.freeze(store, version).frozen_at == first


def test_the_most_recently_frozen_version_wins(store):
    """A mid-season correction is legitimate; the older scores stay explainable
    because each names the version it used."""
    bench = bm.compute(scored_history(), "Player", "2026-27")
    first = bm.save(store, bench, "2026-27", version="v1")
    bm.freeze(store, first)
    second = bm.save(store, bench, "2026-27", version="v2")
    bm.freeze(store, second)
    assert bm.active_version(store, "2026-27").version == "v2"


def test_another_seasons_frozen_version_is_not_picked_up(store):
    bench = bm.compute(scored_history(), "Player", "2025-26")
    bm.freeze(store, bm.save(store, bench, "2025-26", version="old"))
    assert bm.active_version(store, "2026-27") is None


def test_an_empty_benchmark_set_is_refused(store):
    with pytest.raises(ValueError, match="empty"):
        bm.save(store, pd.DataFrame(), "2026-27")


def test_reusing_a_version_id_is_refused(store):
    """Silently replacing one would rewrite the scale under scores that already
    name it."""
    bench = bm.compute(scored_history(), "Player", "2026-27")
    bm.save(store, bench, "2026-27", version="v1")
    with pytest.raises(ValueError, match="already exists"):
        bm.save(store, bench, "2026-27", version="v1")


def test_freezing_an_unknown_version_is_refused(store):
    with pytest.raises(ValueError, match="no benchmark version"):
        bm.freeze(store, "does-not-exist")


def test_comparing_versions_says_how_far_every_score_would_move(store):
    """The point of versioning is answering 'what would adopting this do' before
    adopting it -- a benchmark that moves 4% moves every score in its group."""
    bench = bm.compute(scored_history(), "Player", "2026-27")
    bm.save(store, bench, "2026-27", version="v1")
    lifted = bench.copy()
    lifted.loc[lifted["norm_key"] == "NFL_QB", "benchmark"] = 425.0
    bm.save(store, lifted, "2026-27", version="v2")

    diff = bm.compare(store, "v1", "v2").set_index("norm_key")
    assert diff.loc["NFL_QB", "change_pct"] == pytest.approx(3.73, abs=0.01)
    assert diff.loc["NFL_TE", "change_pct"] == 0.0


def test_the_comparison_puts_the_biggest_movers_first(store):
    bench = bm.compute(scored_history(), "Player", "2026-27")
    bm.save(store, bench, "2026-27", version="v1")
    lifted = bench.copy()
    lifted.loc[lifted["norm_key"] == "NFL_TE", "benchmark"] = 300.0
    bm.save(store, lifted, "2026-27", version="v2")
    assert bm.compare(store, "v1", "v2").iloc[0]["norm_key"] == "NFL_TE"


def test_a_loaded_version_is_shaped_for_apply_benchmarks(store):
    from whul.normalize import apply_benchmarks

    version = bm.save(store, bm.compute(scored_history(), "Player", "2026-27"), "2026-27")
    bench = bm.load(store, version)
    scored = pd.DataFrame({
        "league": ["NFL"], "role": ["QB"], "player": ["Someone"],
        "total_points": [409.7],
    })
    out = apply_benchmarks(scored, bench, "Player")
    assert out.iloc[0]["scaled_score"] == pytest.approx(100.0)
