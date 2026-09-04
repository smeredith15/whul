"""Pulling a live league and recording it against the roster."""

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd
import pytest

from whul import benchmarks, ingest
from whul.store import benchmarks as bm
from whul.store import open_store, rosters


@dataclass
class FakeSource:
    """The shape ``ingest`` reads off a benchmark source."""

    key: str = "nfl"
    league: str = "NFL"
    asset_type: str = "Player"
    produces: tuple[str, ...] = ()
    seasons_for: object = None
    roster_scoped: bool = False
    windowed: bool = False
    live: Callable | None = None
    build: Callable = lambda: (lambda seasons: pd.DataFrame(), lambda raw: raw)


@pytest.fixture
def store():
    return open_store(":memory:")


def rostered(store, *names, league="NFL", season="2026-27", asset_type="Player"):
    rosters.add_manager(store, "TG")
    rosters.create_slots(store, "TG", season)
    category = {"NFL": "NFL", "Tennis": "Tennis", "NCAAF": "NCAAF"}.get(
        league, "Club Soccer Top 3" if "Premier" in league else "Tennis"
    )
    slots = store.query(
        "SELECT slot_id FROM roster_slots WHERE season = ? AND asset_type = ? "
        "AND category = ?",
        (season, asset_type, category),
    )
    for index, name in enumerate(names):
        asset_id = f"a{index}"
        store.upsert("assets", [{
            "asset_id": asset_id, "asset_type": asset_type, "display_name": name,
            "league": league, "role": "", "norm_key": league,
            "active": 1, "created_at": "2026-08-21",
        }], keys=("asset_id",))
        rosters.assign(store, slots.loc[index, "slot_id"], asset_id, "2026-08-21")


def source_over(rows, **kwargs):
    frame = pd.DataFrame(rows)
    return FakeSource(build=lambda: (lambda seasons: frame, lambda raw: raw), **kwargs)


def frozen_benchmark(store, season="2026-27", norm_key="NFL_QB", value=100.0):
    version = bm.save(
        store,
        pd.DataFrame([{
            "asset_type": "Player", "norm_key": norm_key,
            "benchmark": value, "pool_size": 300, "seasons": "2021,2025",
        }]),
        season,
    )
    bm.freeze(store, version)
    return version


def test_a_matched_asset_is_scored_against_the_frozen_benchmark(store):
    rostered(store, "Josh Allen")
    frozen_benchmark(store)
    source = source_over([
        {"player": "Josh Allen", "league": "NFL", "role": "QB", "total_points": 200.0},
        {"player": "Nobody", "league": "NFL", "role": "QB", "total_points": 999.0},
    ])

    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)

    assert report.matched == 1 and report.scored == 1
    scores = store.query("SELECT asset_id, scaled_score FROM daily_scores")
    assert scores.loc[0, "asset_id"] == "a0"
    assert scores.loc[0, "scaled_score"] == pytest.approx(200.0)


def test_the_raw_figures_are_recorded_even_with_no_benchmark(store):
    """A benchmark can be computed next week. A rolling feed's earlier weeks
    cannot be fetched back, so the pull is never conditional on the scale."""
    rostered(store, "Josh Allen")
    source = source_over([
        {"player": "Josh Allen", "league": "NFL", "role": "QB", "total_points": 200.0},
    ])

    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)

    assert report.recorded == 1
    assert report.scored == 0
    assert any("not scaled" in p for p in report.problems)
    assert len(store.query("SELECT * FROM raw_stats")) == 1


def test_an_unmatched_asset_is_named_in_the_report(store):
    rostered(store, "Josh Allen", "Jeremiyah Love")
    frozen_benchmark(store)
    source = source_over([
        {"player": "Josh Allen", "league": "NFL", "role": "QB", "total_points": 200.0},
    ])

    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    assert report.resolution.unmatched == [("Jeremiyah Love", "NFL")]


def test_a_group_with_no_benchmark_is_recorded_but_not_scored(store):
    rostered(store, "Josh Allen")
    frozen_benchmark(store, norm_key="NFL_RB")
    source = source_over([
        {"player": "Josh Allen", "league": "NFL", "role": "QB", "total_points": 200.0},
    ])

    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    assert report.recorded == 1 and report.scored == 0
    assert any("no benchmark" in p for p in report.problems)


def test_a_pull_failure_is_reported_rather_than_raised(store):
    rostered(store, "Josh Allen")

    def explode():
        def load(seasons):
            raise ConnectionError("the feed is down")
        return load, (lambda raw: raw)

    report = ingest.ingest(
        store, FakeSource(build=explode), "2026-27", date(2026, 9, 4), verbose=False
    )
    assert "the feed is down" in report.problems[0]


def test_a_league_with_nothing_rostered_is_skipped(store):
    rostered(store, "Josh Allen")
    source = source_over([], key="pga", league="PGA")
    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    assert "nothing rostered" in report.problems[0]
    assert report.pulled == 0


def test_a_category_pick_reaches_the_source_that_scores_it(store):
    # The roster says "Tennis"; the source produces ATP and WTA.
    rostered(store, "Iga Swiatek", league="Tennis")
    source = source_over(
        [{"player": "Iga Swiatek", "league": "WTA", "total_points": 5000.0}],
        key="tennis", league="Tennis", produces=("ATP", "WTA"),
    )
    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    assert report.matched == 1


def test_the_live_feed_is_preferred_where_one_is_declared(store):
    rostered(store, "Iga Swiatek", league="Tennis")
    historical = pd.DataFrame([
        {"player": "Iga Swiatek", "league": "WTA", "total_points": 1.0}])
    live = pd.DataFrame([
        {"player": "Iga Swiatek", "league": "WTA", "total_points": 5000.0}])
    source = FakeSource(
        key="tennis", league="Tennis", produces=("ATP", "WTA"),
        build=lambda: (lambda s: historical, lambda raw: raw),
        live=lambda: (lambda s: live, lambda raw: raw),
    )

    ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    stats = store.query("SELECT stats FROM raw_stats")
    assert "5000" in stats.loc[0, "stats"]


def test_running_twice_on_one_day_replaces_rather_than_doubles(store):
    rostered(store, "Josh Allen")
    frozen_benchmark(store)
    source = source_over([
        {"player": "Josh Allen", "league": "NFL", "role": "QB", "total_points": 200.0},
    ])
    for _ in range(2):
        ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)

    assert len(store.query("SELECT * FROM daily_scores")) == 1
    assert len(store.query("SELECT * FROM raw_stats")) == 1


def test_a_feed_that_numbers_a_season_by_its_end_year_is_asked_for_the_right_one(store):
    """European football labels 2026-27 as season 2027. Asking for the calendar
    year returns the season that finished in May -- a full set of results, from
    last year, which is the kind of wrong answer that looks right."""
    asked = []

    def build():
        def load(seasons):
            asked.append(list(seasons))
            return pd.DataFrame([
                {"team": "Arsenal", "league": "Premier League",
                 "date": "2026-08-22", "total_points": 40.0},
            ])
        return load, (lambda raw: raw)

    source = FakeSource(
        key="epl", league="Premier League", asset_type="Team",
        build=build, seasons_for=lambda day: [day.year + 1],
    )
    rostered(store, "Arsenal", league="Premier League", asset_type="Team")
    ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    assert asked == [[2027]]


def test_results_before_a_leagues_start_date_are_dropped(store):
    """La Liga was three matchdays old when the league year opened; the
    Premier League's first fixtures fall the week before its 8/21 start."""
    source = source_over(
        [{"team": "Arsenal", "league": "Premier League", "date": "2026-08-15",
          "total_points": 40.0},
         {"team": "Arsenal", "league": "Premier League", "date": "2026-08-22",
          "total_points": 40.0}],
        key="epl", league="Premier League", asset_type="Team",
    )
    rostered(store, "Arsenal", league="Premier League", asset_type="Team")
    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    assert report.pulled == 1


def test_the_feed_name_report_separates_absent_from_misspelled(tmp_path, capsys):
    """A rostered asset that matches nothing is either absent from the feed or
    spelled differently in it, and those need opposite fixes."""
    from unittest import mock

    from whul import ingest as ingest_module
    from whul.cli import main
    from whul.store import open_store

    db = tmp_path / "w.sqlite3"
    store = open_store(str(db))
    rostered(store, "Carlos Alcaraz", "Iga Swiatek", league="Tennis")
    store.conn.commit()

    feed = pd.DataFrame([
        {"player": "Alcaraz C.", "league": "ATP", "total_points": 900.0},
        {"player": "Iga Swiatek", "league": "WTA", "total_points": 800.0},
    ])
    with mock.patch.object(ingest_module, "_pull", return_value=feed):
        assert main(["feed-names", "tennis", "--db", str(db), "--season", "2026-27"]) == 0

    out = capsys.readouterr().out
    assert "ok    Iga Swiatek" in out
    assert "MISS  Carlos Alcaraz" in out and "Alcaraz C." in out


def test_an_empty_feed_is_called_a_source_problem(tmp_path, capsys):
    from unittest import mock

    from whul import ingest as ingest_module
    from whul.cli import main
    from whul.store import open_store

    db = tmp_path / "w.sqlite3"
    store = open_store(str(db))
    rostered(store, "Carlos Alcaraz", league="Tennis")
    store.conn.commit()
    with mock.patch.object(ingest_module, "_pull", return_value=pd.DataFrame()):
        assert main(["feed-names", "tennis", "--db", str(db), "--season", "2026-27"]) == 1
    assert "not a spelling one" in capsys.readouterr().out


def test_a_roster_scoped_source_is_asked_only_for_what_is_rostered(store):
    """A team league pulled team by team is eight requests rather than a season
    of dates, and a team's own schedule cannot be short of its own games."""
    asked = []

    def build():
        def load(seasons, names):
            asked.append(sorted(names))
            return pd.DataFrame([
                {"team": n, "league": "NCAAF", "game_date": "2026-08-30",
                 "total_points": 40.0} for n in names
            ])
        return load, (lambda raw: raw)

    rostered(store, "Ohio State Buckeyes", "Texas Longhorns",
             league="NCAAF", asset_type="Team")
    source = FakeSource(
        key="ncaaf", league="NCAAF", asset_type="Team",
        build=lambda: (lambda s: pd.DataFrame(), lambda raw: raw),
        live=build, roster_scoped=True,
    )
    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)

    assert asked == [["Ohio State Buckeyes", "Texas Longhorns"]]
    assert report.matched == 2


def test_a_source_that_is_not_roster_scoped_is_called_the_old_way(store):
    calls = []

    def build():
        def load(seasons):
            calls.append(seasons)
            return pd.DataFrame([
                {"player": "Josh Allen", "league": "NFL", "role": "QB",
                 "total_points": 200.0}])
        return load, (lambda raw: raw)

    rostered(store, "Josh Allen")
    source = FakeSource(build=build, live=build)
    ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    assert calls == [[2026]]
