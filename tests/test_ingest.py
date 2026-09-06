"""Pulling a live league and recording it against the roster."""

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd
import pytest

from whul import benchmarks, ingest
from whul.config.league import season_start
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
    category = {
        "NFL": "NFL", "Tennis": "Tennis", "NCAAF": "NCAAF", "NBA": "NBA",
        "MLS": "Club Soccer Other", "NWSL": "Club Soccer Other",
        "Men's Intl Soccer": "Intl Soccer", "Women's Intl Soccer": "Intl Soccer",
    }.get(league, "Club Soccer Top 3" if "Premier" in league else "Tennis")
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


def test_a_two_way_player_is_folded_rather_than_dropped(store):
    """MLB scores a player once as a batter and once as a pitcher, because the
    two are normalized against different benchmarks and only comparable
    afterwards. Two rows there are the design, not a collision -- and treated as
    one, Ohtani is held back as ambiguous and scores nothing at all, which is
    the single outcome the two-way rule exists to prevent."""
    rostered(store, "Shohei Ohtani")
    version = bm.save(
        store,
        pd.DataFrame([
            {"asset_type": "Player", "norm_key": "NFL_QB", "benchmark": 100.0,
             "pool_size": 300, "seasons": "2021,2025"},
        ]),
        "2026-27",
    )
    bm.freeze(store, version)

    folds = []

    def fold(placed):
        folds.append(len(placed))
        best = placed.sort_values("scaled_score", ascending=False).head(1).copy()
        best["scaled_score"] = placed["scaled_score"].max() + placed["scaled_score"].min() / 2
        return best

    source = source_over(
        [{"player": "Shohei Ohtani", "league": "NFL", "role": "QB",
          "total_points": 200.0},
         {"player": "Shohei Ohtani", "league": "NFL", "role": "QB",
          "total_points": 60.0}],
    )
    source.post_normalize = fold

    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)

    assert report.matched == 1, "one player, not an ambiguity"
    assert not report.resolution.ambiguous
    assert folds == [2], "both role rows reach the fold, already scaled"
    assert report.scored == 1, "and leave it as one row in one slot"
    scores = store.query("SELECT scaled_score FROM daily_scores")
    assert scores.loc[0, "scaled_score"] == pytest.approx(230.0)


def test_a_source_with_no_fold_still_refuses_to_guess(store):
    """The permission is granted by the fold, not assumed. A feed with two
    genuinely different people of one name must still be held back."""
    rostered(store, "Josh Allen")
    frozen_benchmark(store)
    source = source_over([
        {"player": "Josh Allen", "league": "NFL", "role": "QB", "total_points": 200.0},
        {"player": "Josh Allen", "league": "NFL", "role": "QB", "total_points": 90.0},
    ])

    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    assert report.resolution.ambiguous == [("Josh Allen", "NFL", 2)]
    assert report.scored == 0


def test_the_mlb_source_declares_the_fold():
    from whul.benchmark_sources import SOURCES

    assert SOURCES["mlb"].post_normalize is not None
    assert SOURCES["nfl"].post_normalize is None


# --- why nothing scored -------------------------------------------------------

def test_a_schedule_nobody_has_played_says_so(store):
    """Three things look identical from the outside: a feed with nothing in it,
    a feed whose rows all predate the league year, and a full fixture list
    nobody has played. Only the last is normal, and it reads most like a broken
    adapter -- which is what sent someone hunting for a bug in a college
    football season that had not kicked off."""
    rostered(store, "Ohio State Buckeyes", league="NCAAF", asset_type="Team")
    fixtures = pd.DataFrame([
        {"team": "Ohio State Buckeyes", "league": "NCAAF",
         "game_date": "2026-09-05", "completed": False},
        {"team": "Ohio State Buckeyes", "league": "NCAAF",
         "game_date": "2026-09-12", "completed": False},
    ])
    source = FakeSource(
        key="ncaaf", league="NCAAF", asset_type="Team",
        # The scorer keeps completed games only, so an unplayed slate scores
        # nothing -- which is exactly what a real one does.
        build=lambda: (lambda seasons: fixtures, lambda raw: pd.DataFrame()),
    )

    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    assert "2 fixture(s) scheduled, none played yet" in report.problems[0]
    assert "2026-09-05" in report.problems[0]
    assert "not a feed that is broken" in report.problems[0]


def test_rows_that_all_predate_the_league_year_say_that_instead(store):
    from whul.ingest import _why_nothing_scored

    raw = pd.DataFrame([{"game_date": "2026-07-01", "completed": True}] * 5)
    note = _why_nothing_scored(raw, raw.iloc[0:0])
    assert "5 row(s)" in note
    assert "before this league's results start counting" in note


def test_completed_rows_that_score_nothing_are_the_scorers_to_explain():
    """Not the feed's. The distinction is what says where to look."""
    from whul.ingest import _why_nothing_scored

    kept = pd.DataFrame([{"game_date": "2026-09-01", "completed": True}] * 3)
    note = _why_nothing_scored(kept, kept)
    assert "the scorer's to explain" in note


def test_a_cumulative_source_is_differenced_and_summed(store):
    """The whole design in one pass: a feed that reports season to date, a
    league year that spans two of them, and a score that is what the player
    earned inside it."""
    from datetime import timedelta

    from whul.config.league import season_start
    from whul.store import baselines as baseline_store

    rostered(store, "Pete Crow-Armstrong")
    frozen_benchmark(store)

    rows = [{"player": "Pete Crow-Armstrong", "league": "NFL", "role": "QB",
             "season": 2026, "total_points": 400.0}]
    source = source_over(rows)
    source.cumulative = True

    opens = season_start("NFL")
    # Day one: the baseline is taken, so nothing has been earned yet.
    ingest.ingest(store, source, "2026-27", opens, verbose=False)
    held = baseline_store.load(store, "2026-27", "nfl", 2026)
    assert held["a0"]["total_points"] == 400.0

    # `captured_at` is wall-clock, and this test's league year opened months
    # ago. Stamp it as though the run really had happened on the day, which is
    # what a season starting under this code would do.
    store.conn.execute(
        "UPDATE stat_baselines SET captured_at = ?", (f"{opens.isoformat()}T09:00:00",))
    store.conn.commit()

    # Later, on a bigger season-to-date figure, the contribution is the growth.
    rows[0]["total_points"] = 520.0
    later = source_over(rows)
    later.cumulative = True
    ingest.ingest(store, later, "2026-27", opens + timedelta(days=20), verbose=False)

    stats = store.query("SELECT stats FROM raw_stats ORDER BY as_of DESC")
    assert "120" in stats.loc[0, "stats"], "520 season-to-date minus a 400 baseline"


def test_a_late_baseline_is_reported_rather_than_used(store):
    """Subtracting one taken weeks in would credit a manager with none of what
    their player did in between, and the standings would just look low."""
    rostered(store, "Pete Crow-Armstrong")
    frozen_benchmark(store)
    source = source_over([
        {"player": "Pete Crow-Armstrong", "league": "NFL", "role": "QB",
         "season": 2026, "total_points": 400.0}])
    source.cumulative = True

    # Dated three weeks after the NFL opened, rather than left to the clock.
    #
    # `record` stamps captured_at with today and INSERT OR IGNOREs, so a
    # baseline written here stands and its date is the test's to choose. Leaving
    # it to the clock made this assert a late baseline only while today happened
    # to be late enough -- true for a year, and false the day the NFL got a
    # start date of its own, which moved the cutoff past today and turned a late
    # baseline into a usable one.
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO stat_baselines (asset_id, season, source, feed_season, "
            "captured_at, captured_for, stats) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("a0", "2026-27", source.key, 2026, "2026-10-01",
             str(season_start("NFL")), '{"total_points": 400.0}'),
        )

    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 20), verbose=False)
    assert any("recorded but not subtracted" in p for p in report.problems)


def test_a_baseline_taken_on_the_opening_day_is_used(store):
    """The other side of the same rule, and the reason the one above is not
    simply "any baseline is late"."""
    rostered(store, "Pete Crow-Armstrong")
    frozen_benchmark(store)
    source = source_over([
        {"player": "Pete Crow-Armstrong", "league": "NFL", "role": "QB",
         "season": 2026, "total_points": 520.0}])
    source.cumulative = True

    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO stat_baselines (asset_id, season, source, feed_season, "
            "captured_at, captured_for, stats) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("a0", "2026-27", source.key, 2026, str(season_start("NFL")),
             str(season_start("NFL")), '{"total_points": 400.0}'),
        )

    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 20), verbose=False)
    assert not any("recorded but not subtracted" in p for p in report.problems)
    stats = store.query("SELECT stats FROM raw_stats ORDER BY as_of DESC")
    assert "120" in stats.loc[0, "stats"], "520 season-to-date minus a 400 baseline"


def test_two_feed_seasons_for_one_slot_are_summed_not_split():
    """A league year can hold a club through the tail of one calendar-year
    season and the front of the next, and the feed files those halves
    separately. Left split they are two feed rows for one roster slot, which
    the resolver holds back as ambiguous -- the club would score nothing at
    all.

    Tested on the function rather than through a league, because no league
    currently spans two feed seasons: MLS did, and its results now start in
    2027, which leaves it one season. The machinery is still right and still
    has to work the next time a league year straddles one.
    """
    both = pd.DataFrame([
        {"team": "Inter Miami", "league": "MLS", "season": 2026,
         "matches_played": 8, "total_points": 30.0},
        {"team": "Inter Miami", "league": "MLS", "season": 2027,
         "matches_played": 5, "total_points": 18.0},
    ])
    summed = ingest._across_feed_seasons(both)
    assert len(summed) == 1
    assert summed.iloc[0]["total_points"] == 48.0
    assert summed.iloc[0]["matches_played"] == 13


def test_mls_scores_only_the_season_it_was_drafted_for(store):
    """The clubs were picked for 2027 and the 2026 season is being played now.
    Both halves used to be summed into one total, which paid a manager for a
    season nobody drafted -- Vancouver ten points, Nashville eight."""
    source = source_over(
        [{"team": "Inter Miami", "league": "MLS", "season": 2026,
          "date": "2026-09-01", "matches_played": 8, "total_points": 30.0},
         {"team": "Inter Miami", "league": "MLS", "season": 2027,
          "date": "2027-03-08", "matches_played": 5, "total_points": 18.0}],
        key="mls", league="MLS", asset_type="Team",
        seasons_for=lambda day: [2026, 2027],
    )
    rostered(store, "Inter Miami", league="MLS", asset_type="Team")
    report = ingest.ingest(store, source, "2026-27", date(2027, 3, 15), verbose=False)

    assert report.matched == 1
    assert not report.resolution.ambiguous
    import json
    figures = json.loads(store.query(
        "SELECT stats FROM raw_stats WHERE league = 'MLS'").loc[0, "stats"])
    assert figures["total_points"] == 18.0
    assert figures["matches_played"] == 5


def test_mls_scores_nothing_before_its_own_season_opens(store):
    """Which is the state today: the 2026 season is in progress, the 2027 one
    has not started, and the right number for every MLS club is zero."""
    source = source_over(
        [{"team": "Inter Miami", "league": "MLS", "season": 2026,
          "date": "2026-09-01", "matches_played": 8, "total_points": 30.0}],
        key="mls", league="MLS", asset_type="Team",
        seasons_for=lambda day: [2026],
    )
    rostered(store, "Inter Miami", league="MLS", asset_type="Team")
    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 5), verbose=False)
    assert report.scored == 0
    assert store.query("SELECT * FROM raw_stats WHERE league = 'MLS'").empty


def test_one_feed_season_is_left_exactly_as_it_came(store):
    """The summing must not fire on the ordinary case. A single season's rows
    pass through untouched, including a club that legitimately has one row."""
    source = source_over(
        [{"team": "Arsenal", "league": "Premier League", "season": 2027,
          "date": "2026-08-22", "total_points": 40.0}],
        key="epl", league="Premier League", asset_type="Team",
        seasons_for=lambda day: [2027],
    )
    rostered(store, "Arsenal", league="Premier League", asset_type="Team")
    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 4), verbose=False)
    assert report.pulled == 1


def test_a_league_that_has_not_reached_the_year_is_not_fetched(store):
    """The NBA has not tipped off when the league year opens in August. An
    empty season list is the true answer, and pulling anyway would return an
    empty frame that reads exactly like a broken adapter."""
    asked = []

    def build():
        def load(seasons):
            asked.append(list(seasons))
            return pd.DataFrame()
        return load, (lambda raw: raw)

    source = FakeSource(
        key="nba-teams", league="NBA", asset_type="Team",
        build=build, seasons_for=lambda day: [],
    )
    rostered(store, "Boston Celtics", league="NBA", asset_type="Team")
    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 5), verbose=False)

    assert asked == []
    assert any("has been played inside this league year yet" in p for p in report.problems)


def test_a_shrinking_season_total_is_reported(store):
    """A season-to-date total accumulates over a league year, so within one it
    can only grow. A drop is the feed no longer reaching as far back as it did
    -- tennis assembled from three vintages, losing the middle one -- and the
    score simply gets smaller with nothing raised."""
    rostered(store, "Coco Gauff", league="WTA", asset_type="Player")

    def source_at(points):
        return source_over(
            [{"player": "Coco Gauff", "league": "WTA", "total_points": points}],
            key="tennis", league="WTA", asset_type="Player",
        )

    ingest.ingest(store, source_at(537.5), "2026-27", date(2026, 9, 4), verbose=False)
    report = ingest.ingest(store, source_at(100.0), "2026-27", date(2026, 9, 5),
                           verbose=False)

    shrunk = [p for p in report.problems if "smaller season-to-date total" in p]
    assert shrunk, report.problems
    assert "Coco Gauff 537.5 -> 100.0" in shrunk[0]


def test_a_growing_total_is_not_reported(store):
    """The ordinary case must stay quiet, or the report is noise."""
    rostered(store, "Coco Gauff", league="WTA", asset_type="Player")

    def source_at(points):
        return source_over(
            [{"player": "Coco Gauff", "league": "WTA", "total_points": points}],
            key="tennis", league="WTA", asset_type="Player",
        )

    ingest.ingest(store, source_at(100.0), "2026-27", date(2026, 9, 4), verbose=False)
    report = ingest.ingest(store, source_at(537.5), "2026-27", date(2026, 9, 5),
                           verbose=False)
    assert not [p for p in report.problems if "smaller season-to-date" in p]


def test_a_source_that_found_nothing_still_leaves_a_trace(store):
    """source_status exists because the dangerous scraper failure is not a
    crash but a feed that quietly stops updating. It was written only on a
    successful record, so the one case it was built for left no row at all --
    and eight NCAAF teams sat on zero for a fortnight with nothing in the
    database to say the league had even been tried."""
    source = source_over([], key="ncaaf", league="NCAAF", asset_type="Team")
    rostered(store, "Ohio State Buckeyes", league="NCAAF", asset_type="Team")

    ingest.ingest(store, source, "2026-27", date(2026, 9, 5), verbose=False)

    status = store.query("SELECT * FROM source_status WHERE source = 'ncaaf'")
    assert len(status) == 1
    assert int(status.loc[0, "last_ok"]) == 0
    assert int(status.loc[0, "rows_last_run"]) == 0
    assert status.loc[0, "message"]


def test_a_source_that_raised_leaves_a_trace_too(store):
    def build():
        def load(seasons):
            raise LookupError("none of the rostered teams match ESPN's index")
        return load, (lambda raw: raw)

    source = FakeSource(key="ncaaf", league="NCAAF", asset_type="Team", build=build)
    rostered(store, "Ohio State Buckeyes", league="NCAAF", asset_type="Team")

    report = ingest.ingest(store, source, "2026-27", date(2026, 9, 5), verbose=False)

    status = store.query("SELECT * FROM source_status WHERE source = 'ncaaf'")
    assert len(status) == 1 and int(status.loc[0, "last_ok"]) == 0
    assert "none of the rostered teams" in status.loc[0, "message"]
    assert any("LookupError" in p for p in report.problems)


def test_uncovered_names_a_league_nobody_asked_for():
    """The failure a league at a time cannot see.

    Every source in the run can succeed and every asset it covers can match,
    and a whole league still scores nothing -- because it was never in the
    list. That is how thirty-two club soccer players sat at zero through a
    nightly run that reported no problems at all.
    """
    store = open_store(":memory:")
    rostered(store, "Josh Allen")
    rostered(store, "Bukayo Saka", league="Premier League")

    from whul.benchmark_sources import resolve

    missed = ingest.uncovered(store, "2026-27", resolve(["nfl"]))
    assert list(missed["league"]) == ["Premier League"]
    assert int(missed["assets"].iloc[0]) == 1
    # Named, so the fix is one word rather than a hunt.
    assert missed["source"].iloc[0] == "soccer-players"

    covered = ingest.uncovered(store, "2026-27", resolve(["nfl", "soccer-players"]))
    assert covered.empty


def test_uncovered_tells_a_gap_from_an_omission():
    """A league left out of tonight's pull is a one-word fix. A league no
    source can score is a known gap. Reporting them the same way makes the
    first invisible among the second."""
    store = open_store(":memory:")
    rostered(store, "Spain", league="Men's Intl Soccer", asset_type="Team")

    from whul.benchmark_sources import resolve

    missed = ingest.uncovered(store, "2026-27", resolve(["nfl"]))
    assert list(missed["league"]) == ["Men's Intl Soccer"]
    assert missed["source"].iloc[0] == ""


def test_mls_results_from_the_season_nobody_drafted_do_not_count():
    """MLS and NWSL were drafted for 2027. Their 2026 seasons are running now,
    and without a start date every 2026 match counted -- Vancouver was credited
    with ten points and Nashville eight for a season nobody picked."""
    from whul.config.league import season_start

    assert season_start("MLS").year == 2027
    assert season_start("NWSL").year == 2027

    raw = pd.DataFrame([
        {"team": "Vancouver Whitecaps", "date": "2026-09-05", "points": 10},
        {"team": "Vancouver Whitecaps", "date": "2027-03-06", "points": 3},
    ])
    kept = ingest._from_season_start(raw, "MLS")
    assert list(kept["date"]) == ["2027-03-06"]


def test_the_european_leagues_still_start_in_august():
    """The 2027 start is MLS and NWSL's alone -- a league drafted for the
    season now being played must not be pushed into next year with them."""
    raw = pd.DataFrame([
        {"team": "Arsenal", "date": "2026-08-14"},   # before the opener
        {"team": "Arsenal", "date": "2026-09-05"},   # this season
    ])
    kept = ingest._from_season_start(raw, "Premier League")
    assert list(kept["date"]) == ["2026-09-05"]
