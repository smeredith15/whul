"""Computing, reviewing and freezing the 0-100 scale.

The benchmark decides what 100 means, and getting it wrong produces no error --
just a season of plausible, wrong standings. These tests pin the parts that
would fail silently: which seasons are drawn from, that a thin pool is called
out rather than quietly used, and that a version with holes in it cannot be
frozen by accident.
"""

from datetime import date

import pandas as pd
import pytest

from whul import benchmarks
from whul.benchmark_sources import ORDER, SOURCES, resolve
from whul.store import open_store, rosters
from whul.store import benchmarks as bm


@pytest.fixture
def store():
    return open_store(":memory:")


def fake_league(rows_per_season: int, league: str = "NFL", role: str = "QB"):
    """A load/score pair over synthetic rows -- no network, no sport."""

    def load(seasons):
        return pd.DataFrame([
            {"season": season, "player": f"p{i}", "n": i}
            for season in seasons
            for i in range(rows_per_season)
        ])

    def score(raw):
        return raw.assign(
            league=league,
            role=role,
            total_points=raw["n"].astype(float),
            games_played=10,
        )

    return load, score


# --- which seasons -------------------------------------------------------

def test_five_usable_seasons_are_taken_from_a_league_that_played_them_all():
    seasons, notes = benchmarks.seasons_for("NFL", latest=2025)
    assert seasons == [2021, 2022, 2023, 2024, 2025]
    assert notes == []


def test_a_covid_season_lengthens_the_reach_rather_than_shrinking_the_pool():
    # Excluding 2020 and 2021 must still yield five seasons, by reaching back
    # to 2019 -- otherwise the NBA benchmark would rest on three.
    seasons, notes = benchmarks.seasons_for("NBA", latest=2025)
    assert seasons == [2019, 2022, 2023, 2024, 2025]
    assert len(seasons) == benchmarks.DEFAULT_SEASONS
    assert any("2020 excluded" in n for n in notes)
    assert any("2021 excluded" in n for n in notes)


def test_tennis_stops_at_its_floor_and_says_it_returned_fewer_seasons():
    seasons, notes = benchmarks.seasons_for("Tennis", latest=2025)
    assert seasons == [2023, 2024, 2025]
    assert any("only 3 of 5" in n and "2023" in n for n in notes)


def test_a_calendar_exclusion_does_not_report_zero_of_zero_games():
    # Tennis is excluded by calendar, not game count, so it carries no totals.
    _, notes = benchmarks.seasons_for("Tennis", count=8, latest=2025)
    assert not any("0 of 0 games" in n for n in notes)


def test_the_latest_season_defaults_to_the_last_completed_one():
    seasons, _ = benchmarks.seasons_for("NFL")
    assert seasons[-1] == date.today().year - 1


# --- computing -----------------------------------------------------------

def test_computing_takes_the_percentile_of_the_truncated_pool():
    load, score = fake_league(400)
    run = benchmarks.compute("NFL", load, score, latest=2025, verbose=False)

    assert run.used == [2021, 2022, 2023, 2024, 2025]
    assert list(run.benchmarks["norm_key"]) == ["NFL_QB"]
    # 400 rows a season, truncated to the buffer pool, five seasons pooled.
    assert run.benchmarks.loc[0, "pool_size"] == 340
    assert run.problems == []


def test_a_load_failure_is_reported_rather_than_raised():
    def load(_):
        raise ConnectionError("the feed is down")

    run = benchmarks.compute("NFL", load, lambda raw: raw, latest=2025, verbose=False)
    assert run.benchmarks is None
    assert "the feed is down" in run.problems[0]


def test_a_thin_pool_is_called_out_rather_than_quietly_used():
    load, score = fake_league(1)
    run = benchmarks.compute("NFL", load, score, latest=2025, verbose=False)

    assert run.benchmarks.loc[0, "pool_size"] < benchmarks.THIN_POOL
    assert "99th percentile" in run.problems[0]


def test_regular_season_production_is_what_the_benchmark_is_drawn_from():
    def load(seasons):
        return pd.DataFrame([{"season": s, "n": i} for s in seasons for i in range(50)])

    def score(raw):
        return raw.assign(
            league="NFL", role="QB",
            regular_points=raw["n"] * 1.0,
            # A postseason inflated total must not reach the scale.
            total_points=raw["n"] * 10.0,
        )

    run = benchmarks.compute("NFL", load, score, latest=2025, verbose=False)
    plain = benchmarks.compute("NFL", *fake_league(50), latest=2025, verbose=False)
    assert run.benchmarks.loc[0, "benchmark"] == plain.benchmarks.loc[0, "benchmark"]


def test_a_schedule_change_lifts_the_benchmark_without_leaking_a_column():
    load, score = fake_league(400, league="NHL", role="")
    plain = benchmarks.compute("NHL", load, score, latest=2025, verbose=False)
    lifted = benchmarks.compute(
        "NHL", load, score, latest=2025, scale_for="NHL", verbose=False
    )

    assert lifted.scaled_by > 1.0
    assert lifted.benchmarks.loc[0, "benchmark"] > plain.benchmarks.loc[0, "benchmark"]
    # The stored table has no schedule_factor column; leaving it on the frame
    # would fail the insert rather than the computation.
    assert "schedule_factor" not in lifted.benchmarks.columns


# --- saving --------------------------------------------------------------

def test_saving_writes_one_unfrozen_version_across_every_league(store):
    runs = [
        benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False),
        benchmarks.compute(
            "NHL", *fake_league(400, league="NHL", role=""),
            latest=2025, verbose=False,
        ),
    ]
    version = benchmarks.save(store, runs, "2026-27")

    stored = bm.load(store, version)
    assert set(stored["norm_key"]) == {"NFL_QB", "NHL"}
    assert bm.get_version(store, version).is_frozen is False
    assert bm.active_version(store, "2026-27") is None


def test_saving_nothing_returns_nothing_rather_than_an_empty_version(store):
    failed = benchmarks.compute("NFL", lambda _: pd.DataFrame(), lambda r: r, verbose=False)
    assert benchmarks.save(store, [failed], "2026-27") is None


def test_the_notes_name_the_leagues_and_the_span_they_were_drawn_from(store):
    run = benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)
    version = benchmarks.save(store, [run], "2026-27")
    notes = bm.get_version(store, version).notes
    assert "NFL players" in notes and "2021-2025" in notes


# --- coverage ------------------------------------------------------------

def rostered(store, league, asset_type, season="2026-27", manager="TG"):
    rosters.add_manager(store, manager)
    rosters.create_slots(store, manager, season)
    store.upsert("assets", [{
        "asset_id": f"a-{league}-{asset_type}", "asset_type": asset_type,
        "display_name": "Someone", "league": league, "role": "",
        "norm_key": league, "active": 1, "created_at": "2026-08-21",
    }], keys=("asset_id",))
    slot = store.query(
        "SELECT slot_id FROM roster_slots WHERE season = ? AND asset_type = ? LIMIT 1",
        (season, asset_type),
    ).loc[0, "slot_id"]
    rosters.assign(store, slot, f"a-{league}-{asset_type}", "2026-08-21")


def test_coverage_matches_a_positional_league_on_any_of_its_groups(store):
    # A roster records the league, never the position: the position comes from
    # the feed at scoring time. NFL_QB must therefore cover a rostered NFL
    # player whose role is still blank.
    rostered(store, "NFL", "Player")
    run = benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)
    version = benchmarks.save(store, [run], "2026-27")

    rows = benchmarks.coverage(store, version, "2026-27")
    nfl = rows[rows["league"] == "NFL"].iloc[0]
    assert nfl["covered"] is True or bool(nfl["covered"])
    assert nfl["groups"] == "NFL_QB"


def test_a_category_open_to_two_tours_needs_both(store):
    # Twelve picks are recorded as "Tennis" because that is the category they
    # were drafted into. Nothing on the roster says which tour each plays, so
    # an ATP benchmark alone leaves every WTA pick among them unscored.
    rostered(store, "Tennis", "Player")
    atp = benchmarks.compute(
        "ATP", *fake_league(400, league="ATP", role=""), latest=2025, verbose=False
    )
    version = benchmarks.save(store, [atp], "2026-27")

    half = benchmarks.coverage(store, version, "2026-27")
    assert not half[half["league"] == "Tennis"].iloc[0]["covered"]

    wta = benchmarks.compute(
        "WTA", *fake_league(400, league="WTA", role=""), latest=2025, verbose=False
    )
    benchmarks.extend(store, [wta], version)

    whole = benchmarks.coverage(store, version, "2026-27").set_index("league")
    assert bool(whole.loc["Tennis", "covered"])
    assert whole.loc["Tennis", "groups"] == "ATP, WTA"


def test_a_tour_rostered_by_name_needs_only_itself(store):
    # One pick is recorded as "NASCAR" outright, and on that one an F1
    # benchmark has no bearing.
    rostered(store, "NASCAR", "Player")
    run = benchmarks.compute(
        "NASCAR", *fake_league(400, league="NASCAR", role=""), latest=2025, verbose=False
    )
    version = benchmarks.save(store, [run], "2026-27")

    rows = benchmarks.coverage(store, version, "2026-27").set_index("league")
    assert bool(rows.loc["NASCAR", "covered"])
    assert rows.loc["NASCAR", "groups"] == "NASCAR"


def test_coverage_reports_a_league_nobody_computed(store):
    rostered(store, "NFL", "Player")
    rostered(store, "PGA", "Player")
    run = benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)
    version = benchmarks.save(store, [run], "2026-27")

    rows = benchmarks.coverage(store, version, "2026-27")
    pga = rows[rows["league"] == "PGA"].iloc[0]
    assert not pga["covered"]
    assert pga["groups"] == ""


# --- the registry --------------------------------------------------------

def test_every_registered_source_is_in_the_run_order():
    assert set(SOURCES) == set(ORDER)


def test_every_source_builds_without_touching_the_network():
    # Each entry is lazy on purpose: importing the registry must not import
    # twenty source modules, and building one must not fetch anything.
    for source in SOURCES.values():
        load, score = source.build()
        assert callable(load) and callable(score)


def test_resolving_nothing_gives_the_cheapest_verified_sources_first():
    keys = [s.key for s in resolve(None)]
    assert keys[0] == "nfl"
    assert keys.index("tennis") < keys.index("nba")


def test_an_unknown_league_key_names_the_ones_that_exist():
    with pytest.raises(KeyError) as caught:
        resolve(["hockeyball"])
    assert "hockeyball" in caught.value.args[0]
    assert "nfl" in caught.value.args[0]


def test_the_shared_pools_are_registered_under_the_league_they_normalize_across():
    assert SOURCES["tennis"].league == "Tennis"
    assert SOURCES["motorsports"].league == "Motorsports"


# --- the command ---------------------------------------------------------

def run_cli(*argv) -> int:
    from whul.cli import main

    return main(["benchmarks", *argv])


def patched_source(monkeypatch, rows=400):
    """Point the nfl source at synthetic rows so the command touches nothing."""
    from dataclasses import replace

    from whul import benchmark_sources

    fake = replace(benchmark_sources.SOURCES["nfl"], build=lambda: fake_league(rows))
    monkeypatch.setitem(benchmark_sources.SOURCES, "nfl", fake)


def test_list_names_every_source_and_what_it_scores(capsys):
    assert run_cli("list") == 0
    out = capsys.readouterr().out
    for key in ("nfl", "tennis", "motorsports", "ncaaf"):
        assert key in out


def test_compute_writes_nothing_without_save(tmp_path, monkeypatch, capsys):
    patched_source(monkeypatch)
    db = tmp_path / "w.sqlite3"
    assert run_cli("compute", "nfl", "--latest", "2025", "--db", str(db)) == 0

    assert "Nothing written" in capsys.readouterr().out
    assert open_store(str(db)).query("SELECT * FROM benchmark_versions").empty


def test_compute_with_save_leaves_the_version_unfrozen(tmp_path, monkeypatch, capsys):
    patched_source(monkeypatch)
    db = tmp_path / "w.sqlite3"
    assert run_cli(
        "compute", "nfl", "--latest", "2025", "--save", "--db", str(db)
    ) == 0

    store = open_store(str(db))
    versions = store.query("SELECT * FROM benchmark_versions")
    assert len(versions) == 1
    assert versions.loc[0, "frozen_at"] is None
    assert "freeze" in capsys.readouterr().out


def test_freezing_a_version_with_holes_needs_force(tmp_path, monkeypatch, capsys):
    patched_source(monkeypatch)
    db = tmp_path / "w.sqlite3"
    store = open_store(str(db))
    rostered(store, "NFL", "Player")
    rostered(store, "PGA", "Player")
    store.conn.commit()

    run_cli("compute", "nfl", "--latest", "2025", "--save", "--db", str(db))
    version = open_store(str(db)).query(
        "SELECT version FROM benchmark_versions"
    ).loc[0, "version"]

    assert run_cli("freeze", version, "--db", str(db)) == 1
    assert "PGA players" in capsys.readouterr().err
    assert bm.get_version(open_store(str(db)), version).is_frozen is False

    assert run_cli("freeze", version, "--force", "--db", str(db)) == 0
    assert bm.get_version(open_store(str(db)), version).is_frozen is True


def test_coverage_exits_non_zero_while_a_rostered_league_is_unscoreable(
    tmp_path, monkeypatch, capsys
):
    patched_source(monkeypatch)
    db = tmp_path / "w.sqlite3"
    store = open_store(str(db))
    rostered(store, "NFL", "Player")
    rostered(store, "PGA", "Player")
    store.conn.commit()

    run_cli("compute", "nfl", "--latest", "2025", "--save", "--db", str(db))
    version = open_store(str(db)).query(
        "SELECT version FROM benchmark_versions"
    ).loc[0, "version"]

    assert run_cli("coverage", version, "--db", str(db)) == 1
    out = capsys.readouterr().out
    # The fix differs by cause, so the report must distinguish them.
    assert "benchmarks compute pga" in out


def test_an_unknown_league_is_a_usage_error_not_a_crash(tmp_path, capsys):
    assert run_cli("compute", "hockeyball", "--db", str(tmp_path / "w.sqlite3")) == 2
    assert "hockeyball" in capsys.readouterr().err


# --- the simulated season ------------------------------------------------

def test_a_simulated_season_falls_back_to_the_real_season_scale(store):
    run = benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)
    version = benchmarks.save(store, [run], "2026-27")
    bm.freeze(store, version)

    # The simulator exists to show what the standings will look like, which it
    # can only do on the scale the season itself will use.
    assert bm.active_version(store, "2026-27-SIM").version == version


def test_a_simulated_season_prefers_its_own_scale_when_it_has_one(store):
    real = benchmarks.save(
        store,
        [benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)],
        "2026-27",
    )
    bm.freeze(store, real)
    sim = benchmarks.save(
        store,
        [benchmarks.compute("NFL", *fake_league(200), latest=2025, verbose=False)],
        "2026-27-SIM",
    )
    bm.freeze(store, sim)

    assert bm.active_version(store, "2026-27-SIM").version == sim


# --- the continuously running sports (PROJECT_PLAN 2.3) ------------------

def fake_events(per_year: int, points=lambda i: float(i), through: str = "2026-12-31"):
    """Dated event rows for a sport with no offseason, one event a week."""

    def load(years):
        rows = []
        for year in years:
            for i in range(per_year):
                day = pd.Timestamp(year=year, month=1, day=1) + pd.Timedelta(weeks=i % 52)
                if day > pd.Timestamp(through):
                    continue
                rows.append({"year": year, "who": f"p{i}", "when": day.date().isoformat()})
        return pd.DataFrame(rows)

    def events(raw):
        return raw.assign(
            player=raw["who"],
            date=raw["when"],
            event_points=[points(int(w[1:])) for w in raw["who"]],
            league="Tennis",
            role="Singles",
        )

    return load, events


def test_windows_are_judged_by_the_year_they_end_in():
    # Golf's 2020-21 league year holds the November 2020 Masters and the April
    # 2021 one, so it is the window the exclusion has to catch -- naming the
    # calendar year 2021 must drop that window, not the 2021-22 one, which is
    # played on a normal schedule and is kept.
    labels = [w.label for w in benchmarks.windows_for("PGA")[0]]
    assert "2020-21" not in labels
    assert labels[0] == "2021-22"


def test_the_live_season_cannot_benchmark_itself():
    from whul.config.league import SEASON

    windows, _ = benchmarks.windows_for("PGA", count=8)
    assert all(w.end < SEASON.start for w in windows)


def test_a_covid_window_is_never_returned_to_make_the_count_up():
    # Tennis can reach only four windows before its floor. Asking for more must
    # report the shortfall rather than quietly handing back a rearranged year.
    windows, notes = benchmarks.windows_for("Tennis", count=7)
    labels = [w.label for w in windows]
    assert "2020-21" not in labels and "2021-22" not in labels
    assert any("only 4 of 7" in n for n in notes)


def test_a_windowed_pool_totals_over_the_window_not_the_calendar_year():
    load, events = fake_events(52)
    run = benchmarks.compute_windowed("Tennis", load, events, verbose=False)

    assert run.windowed is True
    assert run.used == ["2022-23", "2023-24", "2024-25", "2025-26"]
    assert list(run.benchmarks["norm_key"]) == ["Tennis"]


def test_a_window_the_source_cannot_cover_is_dropped_and_said_so():
    # A half-covered window looks like a full one with quiet athletes in it,
    # and would pool a year of half-sized totals into the percentile.
    load, events = fake_events(52, through="2026-02-23")
    run = benchmarks.compute_windowed("Tennis", load, events, verbose=False)

    assert "2025-26" not in run.used
    assert any("2025-26 dropped" in note and "covers only to" in note for note in run.excluded)
    assert any("not 5" in p for p in run.problems)


def test_a_source_covering_no_complete_window_fails_rather_than_guesses():
    load, events = fake_events(52, through="2022-09-30")
    run = benchmarks.compute_windowed("Tennis", load, events, verbose=False)

    assert run.benchmarks is None
    assert "covers only to" in run.problems[0]


def test_the_window_sports_are_registered_as_windowed():
    assert {k for k, s in SOURCES.items() if s.windowed} == {"tennis", "pga", "motorsports"}


def test_the_team_sports_are_not_benchmarked_by_window():
    # Their seasons already align year to year, so a window would only split
    # one season across two pools.
    assert SOURCES["nfl"].windowed is False
    assert SOURCES["nba"].windowed is False


def test_an_incomplete_window_is_replaced_by_reaching_one_further_back():
    # PGA has no floor, so a window the source cannot cover is made up rather
    # than shrinking the pool -- and the extra years are pulled only then.
    pulls = []
    base_load, events = fake_events(52, through="2026-02-23")

    def load(years):
        pulls.append(years)
        return base_load(years)

    run = benchmarks.compute_windowed("PGA", load, events, verbose=False)

    assert len(run.used) == benchmarks.DEFAULT_SEASONS
    assert "2025-26" not in run.used
    assert len(pulls) == 2 and pulls[1] and max(pulls[1]) < min(pulls[0])
    assert not any("complete windows" in p for p in run.problems)


def test_a_complete_source_is_pulled_once():
    pulls = []
    base_load, events = fake_events(52)

    def load(years):
        pulls.append(years)
        return base_load(years)

    benchmarks.compute_windowed("PGA", load, events, verbose=False)
    assert len(pulls) == 1


def test_golf_and_motorsport_exclude_their_covid_windows():
    """Both were shut down in 2020 and distorted by what got pushed into the
    league year after it -- golf played two Masters inside one Aug-Jul window."""
    for league in ("PGA", "Motorsports"):
        labels = [w.label for w in benchmarks.windows_for(league, count=7)[0]]
        assert "2019-20" not in labels
        assert "2020-21" not in labels


# --- building one version across several sittings ------------------------

def test_extending_grows_one_version_rather_than_making_a_second(store):
    first = benchmarks.save(
        store,
        [benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)],
        "2026-27",
    )
    benchmarks.extend(
        store,
        [benchmarks.compute(
            "NHL", *fake_league(400, league="NHL", role=""), latest=2025, verbose=False
        )],
        first,
    )

    assert len(store.query("SELECT * FROM benchmark_versions")) == 1
    assert set(bm.load(store, first)["norm_key"]) == {"NFL_QB", "NHL"}


def test_extending_records_what_each_sitting_added(store):
    version = benchmarks.save(
        store,
        [benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)],
        "2026-27",
    )
    benchmarks.extend(
        store,
        [benchmarks.compute(
            "NHL", *fake_league(400, league="NHL", role=""), latest=2025, verbose=False
        )],
        version,
    )
    notes = bm.get_version(store, version).notes
    assert "NFL players" in notes and "NHL players" in notes


def test_a_frozen_version_refuses_to_be_extended(store):
    version = benchmarks.save(
        store,
        [benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)],
        "2026-27",
    )
    bm.freeze(store, version)

    # Adding a group would restate scores already published against this scale.
    with pytest.raises(bm.FrozenBenchmarkError):
        benchmarks.extend(
            store,
            [benchmarks.compute(
                "NHL", *fake_league(400, league="NHL", role=""), latest=2025, verbose=False
            )],
            version,
        )


def test_extending_replaces_a_group_rather_than_duplicating_it(store):
    version = benchmarks.save(
        store,
        [benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)],
        "2026-27",
    )
    redone = benchmarks.compute(
        "NFL", *fake_league(400, role="QB"), latest=2024, seasons=3, verbose=False
    )
    benchmarks.extend(store, [redone], version)

    rows = bm.load(store, version)
    assert list(rows["norm_key"]) == ["NFL_QB"]
    assert rows.loc[0, "seasons"] == "2022,2023,2024"


def test_the_notes_keep_each_span_in_its_own_units():
    # A windowed sport's labels are league years and a team sport's are calendar
    # seasons; one merged range would read as neither.
    frame = pd.DataFrame({"norm_key": ["x"], "benchmark": [1.0], "pool_size": [99]})
    line = benchmarks.describe([
        benchmarks.BenchmarkRun("NFL", "Player", used=[2021, 2025], benchmarks=frame),
        benchmarks.BenchmarkRun(
            "Tennis", "Player", used=["2022-23", "2024-25"],
            benchmarks=frame, windowed=True,
        ),
    ])
    assert "NFL players 2021-2025" in line
    assert "Tennis players 2022-23 to 2024-25" in line


def test_compute_can_add_to_an_existing_version(tmp_path, monkeypatch, capsys):
    patched_source(monkeypatch)
    db = tmp_path / "w.sqlite3"
    run_cli("compute", "nfl", "--latest", "2025", "--save", "--db", str(db))
    version = open_store(str(db)).query(
        "SELECT version FROM benchmark_versions"
    ).loc[0, "version"]
    capsys.readouterr()

    assert run_cli(
        "compute", "nfl", "--latest", "2024", "--save", "--into", version, "--db", str(db)
    ) == 0
    assert "added to version" in capsys.readouterr().out
    assert len(open_store(str(db)).query("SELECT * FROM benchmark_versions")) == 1


def test_adding_to_a_frozen_version_is_refused_by_the_command(tmp_path, monkeypatch, capsys):
    patched_source(monkeypatch)
    db = tmp_path / "w.sqlite3"
    run_cli("compute", "nfl", "--latest", "2025", "--save", "--db", str(db))
    version = open_store(str(db)).query(
        "SELECT version FROM benchmark_versions"
    ).loc[0, "version"]
    run_cli("freeze", version, "--force", "--db", str(db))
    capsys.readouterr()

    assert run_cli(
        "compute", "nfl", "--latest", "2024", "--save", "--into", version, "--db", str(db)
    ) == 1
    assert "frozen" in capsys.readouterr().err


# --- the tennis floor ----------------------------------------------------

def test_tennis_draws_from_the_windows_the_league_signed_off_on():
    windows, _ = benchmarks.windows_for("Tennis", count=5)
    assert [w.label for w in windows] == ["2022-23", "2023-24", "2024-25", "2025-26"]


def test_a_bare_into_adds_to_the_version_still_being_built(tmp_path, monkeypatch, capsys):
    # Carrying the version id between commands is the part that goes wrong: an
    # unset shell variable turns `--into $V` into an argument error, and every
    # league in that batch is lost.
    patched_source(monkeypatch)
    db = tmp_path / "w.sqlite3"
    run_cli("compute", "nfl", "--latest", "2025", "--save", "--db", str(db))
    capsys.readouterr()

    assert run_cli(
        "compute", "nfl", "--latest", "2024", "--save", "--into", "--db", str(db)
    ) == 0
    assert "still being built" in capsys.readouterr().out
    assert len(open_store(str(db)).query("SELECT * FROM benchmark_versions")) == 1


def test_a_bare_into_with_nothing_to_add_to_says_so(tmp_path, monkeypatch, capsys):
    patched_source(monkeypatch)
    db = tmp_path / "w.sqlite3"

    assert run_cli(
        "compute", "nfl", "--latest", "2025", "--save", "--into", "--db", str(db)
    ) == 1
    assert "Drop --into to start one" in capsys.readouterr().err


def test_a_bare_into_ignores_a_frozen_version(tmp_path, monkeypatch, capsys):
    # A frozen version cannot be added to, so resolving "latest" to one would
    # only produce a refusal a step later.
    patched_source(monkeypatch)
    db = tmp_path / "w.sqlite3"
    run_cli("compute", "nfl", "--latest", "2025", "--save", "--db", str(db))
    frozen = open_store(str(db)).query(
        "SELECT version FROM benchmark_versions"
    ).loc[0, "version"]
    run_cli("freeze", frozen, "--force", "--db", str(db))
    capsys.readouterr()

    assert run_cli(
        "compute", "nfl", "--latest", "2024", "--save", "--into", "--db", str(db)
    ) == 1
    assert "Drop --into to start one" in capsys.readouterr().err


def test_recomputing_a_league_supersedes_it_in_the_notes():
    """Listing both spans reads as two pools when there is one."""
    merged = benchmarks._merge_notes(
        "NFL players 2021-2025; PGA players 2020-21 to 2024-25",
        "PGA players 2021-22 to 2025-26",
    )
    assert merged.count("PGA players") == 1
    assert "2021-22 to 2025-26" in merged
    assert "NFL players 2021-2025" in merged


# --- correcting one league in a scale already in force ----------------------

def test_deriving_copies_a_frozen_scale_into_a_draft(store):
    """A frozen scale is never edited, and a new one has to cover the whole
    roster or the freeze is refused. Without this, correcting one league means
    recomputing twenty -- some of them date-walked and hours long -- so in
    practice the correction does not get made."""
    frozen = benchmarks.save(
        store,
        [benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False),
         benchmarks.compute(
             "NHL", *fake_league(400, league="NHL", role=""), latest=2025, verbose=False
         )],
        "2026-27",
    )
    bm.freeze(store, frozen)

    draft = bm.derive(store, frozen, notes="NHL rule corrected")
    assert draft != frozen
    assert bm.get_version(store, draft).frozen_at is None
    before, after = bm.load(store, frozen), bm.load(store, draft)
    assert set(after["norm_key"]) == set(before["norm_key"]) == {"NFL_QB", "NHL"}
    assert list(after.sort_values("norm_key")["benchmark"]) == \
        list(before.sort_values("norm_key")["benchmark"])


def test_a_derived_version_says_where_it_came_from(store):
    """Most of the scale was inherited unchanged, and someone reading it later
    should be able to see that rather than take it on trust."""
    frozen = benchmarks.save(
        store, [benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)],
        "2026-27",
    )
    draft = bm.derive(store, frozen, notes="MLB division rule corrected")
    notes = bm.get_version(store, draft).notes
    assert frozen in notes
    assert "MLB division rule corrected" in notes


def test_one_league_recomputed_into_a_derived_version_leaves_the_rest_alone(store):
    """The whole point: the corrected group moves and nothing else does."""
    frozen = benchmarks.save(
        store,
        [benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False),
         benchmarks.compute(
             "NHL", *fake_league(400, league="NHL", role=""), latest=2025, verbose=False
         )],
        "2026-27",
    )
    bm.freeze(store, frozen)
    draft = bm.derive(store, frozen)
    benchmarks.extend(
        store,
        [benchmarks.compute(
            "NHL", *fake_league(150, league="NHL", role=""),
            latest=2025, verbose=False,
        )],
        draft,
    )

    before = bm.load(store, frozen).set_index("norm_key")["benchmark"]
    after = bm.load(store, draft).set_index("norm_key")["benchmark"]
    assert after["NFL_QB"] == before["NFL_QB"]
    assert after["NHL"] != before["NHL"]


def test_deriving_from_a_version_that_does_not_exist_says_so(store):
    with pytest.raises(ValueError, match="no benchmark version"):
        bm.derive(store, "2026-27-nonexistent")


def test_a_derived_version_never_overwrites_the_one_it_copies(store):
    """Version ids carry a timestamp to the second, so a derive in the same
    second as its source would land on the source's own id -- and the upsert
    would clear its frozen_at, quietly unfreezing the scale the standings are
    measured against."""
    frozen = benchmarks.save(
        store, [benchmarks.compute("NFL", *fake_league(400), latest=2025, verbose=False)],
        "2026-27",
    )
    bm.freeze(store, frozen)

    drafts = [bm.derive(store, frozen) for _ in range(3)]
    assert len(set(drafts)) == 3
    assert frozen not in drafts
    assert bm.get_version(store, frozen).is_frozen
