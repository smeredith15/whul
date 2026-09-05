"""Turning a season-to-date feed into a league-year one by subtraction."""

import pandas as pd
import pytest

from whul.store import baselines, open_store


@pytest.fixture
def store():
    s = open_store(":memory:")
    s.upsert("assets", [{
        "asset_id": "a1", "asset_type": "Player", "display_name": "P. Crow-Armstrong",
        "league": "MLB", "role": "", "norm_key": "MLB_Batter", "active": 1,
        "created_at": "2026-08-15",
    }], keys=("asset_id",))
    return s


def test_a_baseline_is_written_once_and_never_moves(store):
    """One that moved would silently rewrite every score derived from it, and
    being the fixed point is the whole of its job."""
    first = baselines.record(store, "a1", "2026-27", "mlb", 2026,
                             {"Off": 43.1, "hits": 120}, "2026-08-15")
    again = baselines.record(store, "a1", "2026-27", "mlb", 2026,
                             {"Off": 49.5, "hits": 150}, "2026-09-05")

    assert first is True, "the first call writes"
    assert again is False, "and every one after leaves it alone"
    held = baselines.load(store, "2026-27", "mlb", 2026)
    assert held["a1"]["Off"] == 43.1


def test_the_contribution_is_what_came_after_the_baseline(store):
    """Which is the whole point: the feed will not serve a date range, so the
    share of a season belonging to this league year is subtracted rather than
    asked for."""
    baselines.record(store, "a1", "2026-27", "mlb", 2026,
                     {"Off": 43.1, "Def": 19.6, "hits": 120}, "2026-08-15")
    now = pd.DataFrame([{"asset_id": "a1", "Off": 49.5, "Def": 22.5, "hits": 150}])

    out = baselines.subtract(now, baselines.load(store, "2026-27", "mlb", 2026))
    assert out.loc[0, "Off"] == pytest.approx(6.4)
    assert out.loc[0, "Def"] == pytest.approx(2.9)
    assert out.loc[0, "hits"] == pytest.approx(30)


def test_a_run_value_that_fell_stays_negative(store):
    """A player above average in July and below it since has earned negative
    value in the window. Clamping at zero would pay him for a bad month."""
    baselines.record(store, "a1", "2026-27", "mlb", 2026, {"Off": 20.0}, "2026-08-15")
    now = pd.DataFrame([{"asset_id": "a1", "Off": 14.0}])
    out = baselines.subtract(now, baselines.load(store, "2026-27", "mlb", 2026))
    assert out.loc[0, "Off"] == pytest.approx(-6.0)


def test_identifying_columns_are_never_differenced(store):
    """Subtracting a season number or a player id is meaningless, and doing it
    silently would be worse than failing."""
    baselines.record(store, "a1", "2026-27", "mlb", 2026,
                     {"season": 2026, "player_id": 12345, "hits": 100}, "2026-08-15")
    now = pd.DataFrame([{"asset_id": "a1", "season": 2026,
                         "player_id": 12345, "hits": 150}])
    out = baselines.subtract(now, baselines.load(store, "2026-27", "mlb", 2026))
    assert out.loc[0, "season"] == 2026
    assert out.loc[0, "player_id"] == 12345
    assert out.loc[0, "hits"] == pytest.approx(50)


def test_a_figure_the_baseline_never_saw_is_left_whole(store):
    """It means the feed started reporting it after the baseline was taken, so
    all of it was earned inside the league year."""
    baselines.record(store, "a1", "2026-27", "mlb", 2026, {"hits": 100}, "2026-08-15")
    now = pd.DataFrame([{"asset_id": "a1", "hits": 150, "war": 3.2}])
    out = baselines.subtract(now, baselines.load(store, "2026-27", "mlb", 2026))
    assert out.loc[0, "war"] == pytest.approx(3.2)


def test_an_asset_with_no_baseline_is_untouched(store):
    """A player rostered mid-year has no baseline for the year's start, and
    guessing one would be worse than leaving the figures alone."""
    now = pd.DataFrame([{"asset_id": "a2", "hits": 150}])
    out = baselines.subtract(now, baselines.load(store, "2026-27", "mlb", 2026))
    assert out.loc[0, "hits"] == pytest.approx(150)


# --- summing the two calendar seasons a league year spans ---------------------

def test_the_two_halves_of_a_league_year_are_added_before_scoring():
    """A league year opening in August covers the tail of one season and the
    front of the next. Scored one season at a time a player appears twice, each
    half measured against a benchmark drawn from whole years."""
    frame = pd.DataFrame([
        {"asset_id": "a1", "player": "PCA", "role": "Batter",
         "season": 2026, "hits": 30, "hr": 4, "Off": 6.4},
        {"asset_id": "a1", "player": "PCA", "role": "Batter",
         "season": 2027, "hits": 95, "hr": 15, "Off": 18.2},
    ])
    out = baselines.combine_seasons(frame, ["asset_id", "role"])

    assert len(out) == 1
    assert out.loc[0, "hits"] == 125
    assert out.loc[0, "hr"] == 19
    assert out.loc[0, "Off"] == pytest.approx(24.6)


def test_the_summed_row_names_no_season():
    """It spans two of them, and naming one would be a lie."""
    frame = pd.DataFrame([
        {"asset_id": "a1", "role": "Batter", "season": 2026, "hits": 30},
        {"asset_id": "a1", "role": "Batter", "season": 2027, "hits": 95},
    ])
    out = baselines.combine_seasons(frame, ["asset_id", "role"])
    assert out.loc[0, "season"] == 2026, "the first is carried, not summed"
    assert out.loc[0, "hits"] == 125


def test_two_roles_stay_apart_when_the_seasons_are_added():
    """Batting is normalized against the batter benchmark and pitching against
    the pitcher one, so folding the two would score both against neither."""
    frame = pd.DataFrame([
        {"asset_id": "a1", "role": "Batter", "season": 2026, "role_points": 100.0},
        {"asset_id": "a1", "role": "Batter", "season": 2027, "role_points": 300.0},
        {"asset_id": "a1", "role": "Pitcher", "season": 2026, "role_points": 50.0},
        {"asset_id": "a1", "role": "Pitcher", "season": 2027, "role_points": 150.0},
    ])
    out = baselines.combine_seasons(frame, ["asset_id", "role"]).set_index("role")
    assert out.loc["Batter", "role_points"] == pytest.approx(400.0)
    assert out.loc["Pitcher", "role_points"] == pytest.approx(200.0)


def test_a_name_survives_the_summation():
    frame = pd.DataFrame([
        {"asset_id": "a1", "player": "PCA", "role": "Batter", "season": 2026, "hits": 30},
        {"asset_id": "a1", "player": "PCA", "role": "Batter", "season": 2027, "hits": 95},
    ])
    out = baselines.combine_seasons(frame, ["asset_id", "role"])
    assert out.loc[0, "player"] == "PCA"


# --- a baseline is only a baseline if it was taken at the start ---------------

def test_a_baseline_taken_late_is_kept_but_not_used(store):
    """One recorded three weeks in describes a player who has already been
    accumulating. Subtracting it credits a manager with none of what their
    player did in between -- an error that looks like a quiet slump."""
    from datetime import date

    baselines.record(store, "a1", "2026-27", "mlb", 2026, {"hits": 120},
                     "2026-08-15")
    store.conn.execute(
        "UPDATE stat_baselines SET captured_at = '2026-09-05T12:00:00'")
    store.conn.commit()

    assert baselines.load(store, "2026-27", "mlb", 2026), "still recorded"
    assert baselines.usable(store, "2026-27", "mlb", 2026,
                            date(2026, 8, 15)) == {}, "but not subtracted"


def test_a_baseline_taken_on_the_day_is_used(store):
    from datetime import date

    baselines.record(store, "a1", "2026-27", "mlb", 2026, {"hits": 0}, "2026-08-15")
    store.conn.execute(
        "UPDATE stat_baselines SET captured_at = '2026-08-15T09:00:00'")
    store.conn.commit()
    assert baselines.usable(store, "2026-27", "mlb", 2026, date(2026, 8, 15))


def test_a_night_of_lag_is_forgiven(store):
    """A cron that ran after midnight is not a mid-season retrofit."""
    from datetime import date

    baselines.record(store, "a1", "2026-27", "mlb", 2026, {"hits": 0}, "2026-08-15")
    store.conn.execute(
        "UPDATE stat_baselines SET captured_at = '2026-08-16T09:00:00'")
    store.conn.commit()
    assert baselines.usable(store, "2026-27", "mlb", 2026, date(2026, 8, 15))
