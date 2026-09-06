from datetime import date

import pytest
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


def test_a_stray_top_level_directory_cannot_be_taken_for_a_package():
    """What actually broke `pip install -e .` was setuptools discovering a
    second top-level package. Pinned discovery is the fix, so a runtime
    directory like data/ -- which the simulator and the caches write into --
    is now harmless. What must stay true is that nothing outside whul/ looks
    importable, since that is what discovery would pick up."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for directory in root.iterdir():
        if not directory.is_dir() or directory.name in ("whul", "tests"):
            continue
        if directory.name.startswith("."):
            continue
        assert not (directory / "__init__.py").exists(), (
            f"{directory.name}/ looks like a package; either move it under "
            f"whul/ or exclude it from discovery in pyproject.toml"
        )


def test_every_package_directory_has_an_init_file():
    """Without one, `setuptools.packages.find` does not see the directory and
    the module is missing from an installed copy -- while still importing fine
    from the repo root, where Python treats it as a namespace package. That is
    how whul.site reached CI and failed there having passed every local test."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "whul"
    for directory in sorted(root.rglob("*")):
        if not directory.is_dir() or directory.name == "__pycache__":
            continue
        if not any(directory.glob("*.py")):
            continue
        assert (directory / "__init__.py").exists(), (
            f"{directory.relative_to(root.parent)} has modules but no "
            f"__init__.py, so it will be missing from an installed copy"
        )


def test_every_subpackage_imports():
    """A smoke test against the same failure from the other direction."""
    import importlib

    for module in (
        "whul.config.league", "whul.normalize", "whul.bestball", "whul.pipeline",
        "whul.simulate", "whul.roster_import", "whul.admin",
        "whul.scoring.nfl", "whul.sources.espn",
        "whul.site.build", "whul.site.charts", "whul.site.theme", "whul.site.images",
        "whul.store.db", "whul.store.benchmarks", "whul.store.rosters",
    ):
        assert importlib.import_module(module) is not None, module


def test_no_source_file_is_excluded_by_gitignore():
    """The whole whul/site/ package was invisible to git for four commits: the
    ignore rule `site/` has no leading slash, so it matched at any depth. Local
    tests all passed against the working tree, and CI -- checking out what was
    actually committed -- was the first thing to notice.

    Skipped where git is unavailable; it is a repository check, not a code one.
    """
    import shutil
    import subprocess
    from pathlib import Path

    if shutil.which("git") is None:
        pytest.skip("git is not available")

    root = Path(__file__).resolve().parent.parent
    sources = [
        str(p.relative_to(root))
        for p in (root / "whul").rglob("*")
        if p.suffix in (".py", ".sql", ".csv") and "__pycache__" not in p.parts
    ]
    if not sources:
        pytest.skip("no sources found")

    ignored = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=root, input="\n".join(sources), capture_output=True, text=True,
    ).stdout.split()
    assert not ignored, (
        f"these source files are excluded by .gitignore and would be missing "
        f"from a clean checkout: {ignored}"
    )


# --- when each league's results start counting ------------------------------

def test_college_football_starts_when_the_league_says_it_does():
    """The 28th, which excludes Week 0 on purpose.

    This read the 22nd first, on the grounds that ESPN labels Week 1 as August
    22 to September 7 and a later start would discard the games played that
    first weekend. It does discard them, and that is the rule: WHUL counts from
    the 28th, so the handful of Week 0 games belong to nobody. A feed's idea of
    when a season opens is not the league's."""
    from whul.config.league import season_start

    assert season_start("NCAAF") == date(2026, 8, 28)


def test_a_league_with_no_start_of_its_own_uses_the_league_years():
    """Not the NHL any more -- every rostered league is dated now. An unknown
    league still falls back, because a score arriving from one should be
    recorded rather than dropped."""
    from whul.config.league import SEASON, season_start

    assert season_start("Some League Nobody Drafted") == SEASON.start


def test_every_league_start_falls_in_this_league_year():
    """A typo in the month here silently drops or admits weeks of results.

    Bounded from both ends, but not symmetrically. A league may open a little
    *before* the year does -- La Liga and the PGA both do -- and never long
    before, which is what a wrong year looks like. Lateness is bounded by the
    year's end instead of by a window around its start, because a league picked
    for a season that opens mid-year is legitimately months late: MLS and the
    NWSL were drafted for 2027 while their 2026 seasons were being played.
    """
    from whul.config.league import LEAGUE_START, SEASON

    for league, day in LEAGUE_START.items():
        assert SEASON.start.year - 1 <= day.year <= SEASON.start.year + 1, league
        assert (SEASON.start - day).days <= 45, (league, day)
        assert day <= SEASON.end, (league, day)


def test_a_league_drafted_for_next_calendar_year_asks_for_nothing_yet():
    """MLS and the NWSL are the case the bound above is loosened for, so the
    loosening is pinned to what it buys: today they fetch no season at all, so
    a club picked for 2027 cannot be paid for a 2026 match."""
    from datetime import date

    from whul.benchmark_sources import SOURCES

    for key in ("mls", "nwsl"):
        assert SOURCES[key].seasons_for(date(2026, 9, 5)) == []
        assert SOURCES[key].seasons_for(date(2027, 3, 15)) == [2027]


def test_a_league_with_no_start_date_answers_neither_yes_nor_no():
    """Not the same as "no". The caller has to decide what to do about not
    knowing, and the one thing it must not do is report the slot as out of
    season."""
    from whul.config.league import in_season

    assert in_season("Men's Intl Soccer", date(2026, 9, 6)) is None
    assert in_season("NFL", date(2026, 9, 6)) is False
    assert in_season("NFL", date(2026, 9, 10)) is True


def test_an_unknown_league_does_not_fall_back_to_the_league_years_start():
    """season_start() does, deliberately -- a score with no start date should
    still be recorded. in_season() must not: the fallback is in the past, so
    every unknown league would read as in season and a dropped NBA row would
    have the NBA playing in September."""
    from whul.config.league import in_season, season_start, SEASON

    assert season_start("Nonexistent League") == SEASON.start
    assert in_season("Nonexistent League", date(2026, 9, 6)) is None


def test_every_rostered_league_that_can_be_dated_is():
    """A league missing from the table is read from its scores instead, which
    reports an injured player as a league that has not started. That fallback
    is for the international squads and nothing else, so this is the list of
    what is allowed to be missing."""
    from whul.config.league import LEAGUE_START

    rostered = {
        "NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAM", "NCAAW",
        "NCAA Baseball", "NCAA Softball", "ATP", "WTA", "Tennis",
        "PGA", "F1", "NASCAR", "Motorsports", "Premier League", "La Liga",
        "Serie A", "Bundesliga", "Ligue 1", "MLS", "NWSL",
    }
    assert not rostered - set(LEAGUE_START)
