from whul.config.league import (
    ALL_SLOTS,
    BENCHMARK_MANAGER_COUNT,
    PLAYER_SLOTS,
    SEASON,
    TEAM_SLOTS,
    active_slots,
    buffer_n,
    target_n,
)


def test_full_roster_is_65_slots():
    assert sum(s.cap for s in ALL_SLOTS) == 65
    assert sum(s.cap for s in TEAM_SLOTS) == 29
    assert sum(s.cap for s in PLAYER_SLOTS) == 36


def test_starter_bench_split_matches_auction_resolution_comment():
    """Auction Resolution.R describes 'starters + 14 strict bench spots'."""
    assert sum(s.bench for s in PLAYER_SLOTS) == 14
    assert sum(s.starters for s in PLAYER_SLOTS) == 22
    assert all(s.bench == 0 for s in TEAM_SLOTS), "team slots carry no bench"


def test_counting_slots():
    assert sum(s.starters for s in ALL_SLOTS) == 51


def test_bench_depth_by_category():
    bench = {s.category: s.bench for s in PLAYER_SLOTS}
    assert all(bench[c] == 2 for c in ("NFL", "NBA", "MLB", "NHL"))
    assert all(v == 1 for k, v in bench.items() if k not in ("NFL", "NBA", "MLB", "NHL"))


def test_2026_27_deactivations():
    """Olympics (no Games) and WNBA (season nearly over) sit out, as placeholders."""
    inactive = {(s.asset_type, s.category) for s in ALL_SLOTS if not s.active}
    assert inactive == {("Team", "Olympics"), ("Team", "WNBA"), ("Player", "WNBA")}
    live = active_slots()
    assert sum(s.cap for s in live) == 60
    assert sum(s.starters for s in live) == 47


def test_max_theoretical_score():
    assert sum(s.starters for s in active_slots()) * 100 == 4700


def test_target_n_reproduces_all_analysis_values_at_15_managers():
    """Every Target_N in All_Analysis.R is an exact multiple of 15."""
    expected_players = {
        "NFL": 45, "NBA": 45, "WNBA": 30, "MLB": 45, "NHL": 45,
        "Tennis": 45, "Motorsports": 30, "PGA": 45,
        "Club Soccer Top 3": 90, "Club Soccer Other": 90,
    }
    expected_teams = {
        "NFL": 30, "NBA": 30, "WNBA": 15, "MLB": 30, "NHL": 30, "NCAAF": 30,
        "NCAAM": 30, "NCAAW": 30, "NCAA Baseball": 15, "NCAA Softball": 15,
        "Olympics": 30, "Intl Soccer": 30,
        "Club Soccer Top 3": 60, "Club Soccer Other": 60,
    }
    for s in PLAYER_SLOTS:
        assert target_n(s) == expected_players[s.category], s.category
    for s in TEAM_SLOTS:
        assert target_n(s) == expected_teams[s.category], s.category


def test_buffer_multipliers():
    nfl_p = next(s for s in PLAYER_SLOTS if s.category == "NFL")
    nfl_t = next(s for s in TEAM_SLOTS if s.category == "NFL")
    assert buffer_n(nfl_p) == round(45 * 1.50) == 68  # +50% reach pool
    assert buffer_n(nfl_t) == round(30 * 1.33) == 40  # +33% reach pool


def test_pools_scale_with_benchmark_manager_count():
    nfl_p = next(s for s in PLAYER_SLOTS if s.category == "NFL")
    assert target_n(nfl_p, managers=5) == 15
    assert target_n(nfl_p, managers=20) == 60


def test_season_window():
    assert SEASON.start.isoformat() == "2026-08-21"
    assert SEASON.end.isoformat() == "2027-07-13"
    assert SEASON.benchmark_cutoff < SEASON.start, "benchmarks must not see live results"
    assert BENCHMARK_MANAGER_COUNT == 15


# --- packaging -------------------------------------------------------------

def test_the_calendar_is_found_regardless_of_working_directory():
    """It used to be a path relative to the repository root, which only
    resolved when the process happened to start there -- not from an installed
    copy, and not from the nightly job."""
    from whul.sources.tennis_calendar import CALENDAR_PATH, load

    assert CALENDAR_PATH.is_absolute()
    assert CALENDAR_PATH.exists(), f"{CALENDAR_PATH} is missing from the package"
    assert len(load()) > 100


def test_package_discovery_is_pinned():
    """A stray top-level directory made setuptools refuse to build at all --
    'Multiple top-level packages discovered in a flat-layout'. Discovery is
    pinned to whul* so adding one cannot break the install again."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    config = tomllib.loads((root / "pyproject.toml").read_text())
    assert config["tool"]["setuptools"]["packages"]["find"]["include"] == ["whul*"]
    assert "data/*.csv" in config["tool"]["setuptools"]["package-data"]["whul"]


def test_no_data_directory_sits_beside_the_package():
    """The thing that broke the build. Package data belongs under whul/."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert not (root / "data").is_dir(), (
        "a top-level data/ directory breaks `pip install -e .`; "
        "put package data under whul/data/ instead"
    )
