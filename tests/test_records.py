"""The record book.

The one thing worth testing hard: a record is of a completed thing. A season
still being played is not the best season, and whoever is top of a quarter with
three weeks to run has not won it. Taking the maximum of a running total would
put today's standings under a heading saying "best ever" and restate it
tomorrow.
"""

from datetime import date, timedelta

import pandas as pd

from whul import records
from whul.config.league import SEASON, SeasonWindow, quarters
from whul.store import open_store, rosters

MANAGERS = ["TG", "LS", "SS", "JM", "SM"]


def _store(tmp_path, snapshots):
    """``(season, as_of, manager, total)`` in the standings snapshots."""
    store = open_store(str(tmp_path / "r.sqlite3"))
    for manager in {s[2] for s in snapshots}:
        rosters.add_manager(store, manager)
    rows = []
    for season, when, manager, total in snapshots:
        rows.append({"season": season, "as_of": str(when), "manager_id": manager,
                     "total": total, "rank": 1})
    if rows:
        store.upsert("standings_snapshots", rows,
                     keys=("season", "as_of", "manager_id"))
    store.conn.commit()
    return store


#: A second league year, so "career" has more than one thing to count.
LAST_YEAR = SeasonWindow(
    label="2025-26", start=date(2025, 8, 21), end=date(2026, 7, 14),
    benchmark_cutoff=date(2025, 8, 20),
)
WINDOWS = {LAST_YEAR.label: LAST_YEAR, SEASON.label: SEASON}


# --- what counts as finished ------------------------------------------------

def test_a_season_still_being_played_is_not_a_record(tmp_path):
    """The heart of it. Scott leads by a distance and has won nothing."""
    store = _store(tmp_path, [
        (SEASON.label, date(2026, 9, 18), "SM", 324.7),
        (SEASON.label, date(2026, 9, 18), "JM", 212.9),
    ])
    book = records.book(store, MANAGERS, date(2026, 9, 20), WINDOWS)

    assert book.empty
    assert int(book.titles["titles"].sum()) == 0
    # The figure is still shown -- a page saying only "nothing yet" is useless
    # -- but it is marked as unfinished.
    assert [m.manager for m in book.seasons] == ["SM", "JM"]
    assert not any(m.settled for m in book.seasons)


def test_a_closed_season_is_a_title(tmp_path):
    store = _store(tmp_path, [
        (LAST_YEAR.label, LAST_YEAR.end, "LS", 900.0),
        (LAST_YEAR.label, LAST_YEAR.end, "SM", 850.0),
        (LAST_YEAR.label, LAST_YEAR.end, "TG", 800.0),
    ])
    book = records.book(store, MANAGERS, date(2026, 9, 20), WINDOWS)

    assert not book.empty
    titles = book.titles.set_index("manager_id")
    assert titles.loc["LS", "titles"] == 1
    assert titles.loc["SM", "second"] == 1
    assert titles.loc["TG", "third"] == 1
    assert titles.loc["JM", "seasons"] == 0
    assert all(m.settled for m in book.seasons)


def test_the_league_year_decides_it_and_not_the_data(tmp_path):
    """A season with scores in it is a season that started. Whether it ended
    is a fact about the calendar, which is where it is read from."""
    store = _store(tmp_path, [(SEASON.label, SEASON.end, "SM", 900.0)])

    during = records.book(store, MANAGERS, SEASON.end - timedelta(days=1), WINDOWS)
    after = records.book(store, MANAGERS, SEASON.end + timedelta(days=1), WINDOWS)

    assert during.empty and int(during.titles["titles"].sum()) == 0
    assert int(after.titles["titles"].sum()) == 1


# --- quarters ---------------------------------------------------------------

def test_a_quarter_is_won_only_once_its_last_day_has_passed(tmp_path):
    q1, q2 = quarters()[0], quarters()[1]
    store = _store(tmp_path, [
        (SEASON.label, q1.end, "SM", 300.0),
        (SEASON.label, q1.end, "JM", 200.0),
    ])
    # Data through the last day of Q1, so Q1 has run and Q2 has not started.
    book = records.book(store, MANAGERS, q1.end, WINDOWS)
    wins = book.quarter_wins.set_index("manager_id")

    assert wins.loc["SM", "won"] == 1
    assert book.quarter_wins.attrs["played"] == 1
    assert [m.label for m in book.best_quarters] == [f"{SEASON.label} Q1"] * 2
    assert all(m.settled for m in book.best_quarters)
    # Q2 has no snapshot inside it, so it is absent rather than a row of zeroes.
    assert not any(m.label.endswith("Q2") for m in book.best_quarters)
    assert q2.start > q1.end


def test_a_quarter_still_running_counts_for_nobody(tmp_path):
    q1 = quarters()[0]
    store = _store(tmp_path, [
        (SEASON.label, q1.start + timedelta(days=3), "SM", 300.0),
        (SEASON.label, q1.start + timedelta(days=3), "JM", 200.0),
    ])
    book = records.book(store, MANAGERS, q1.start + timedelta(days=3), WINDOWS)

    assert int(book.quarter_wins["won"].sum()) == 0
    assert book.quarter_wins.attrs["played"] == 0
    assert not any(m.settled for m in book.best_quarters)


def test_a_quarter_is_measured_from_where_it_opened(tmp_path):
    """The same arithmetic the standings page shows: the gain inside the
    quarter, not the running total it ended on."""
    q1, q2 = quarters()[0], quarters()[1]
    store = _store(tmp_path, [
        (SEASON.label, q1.end, "SM", 300.0),
        (SEASON.label, q2.end, "SM", 500.0),
    ])
    frame = records.quarter_totals(store, SEASON.label)
    gains = dict(zip(frame["quarter"], frame["gained"]))

    assert gains["Q1"] == 300.0
    assert gains["Q2"] == 200.0


def test_the_worst_quarter_is_the_same_list_the_other_way(tmp_path):
    q1 = quarters()[0]
    store = _store(tmp_path, [
        (SEASON.label, q1.end, "SM", 300.0),
        (SEASON.label, q1.end, "JM", 200.0),
        (SEASON.label, q1.end, "TG", 250.0),
    ])
    book = records.book(store, MANAGERS, q1.end, WINDOWS)

    assert [m.manager for m in book.best_quarters] == ["SM", "TG", "JM"]
    assert [m.manager for m in book.worst_quarters] == ["JM", "TG", "SM"]


# --- the empty case ---------------------------------------------------------

def test_a_store_with_nothing_in_it_is_a_book_with_nothing_in_it(tmp_path):
    book = records.book(_store(tmp_path, []), MANAGERS, date(2026, 9, 20), WINDOWS)

    assert book.empty
    assert book.seasons == [] and book.best_quarters == []
    # Every manager still gets a row, so the career table is a table rather
    # than a sentence about there being no table.
    assert sorted(book.titles["manager_id"]) == sorted(MANAGERS)
    assert len(book.quarter_wins) == 5


def test_two_seasons_accumulate(tmp_path):
    store = _store(tmp_path, [
        (LAST_YEAR.label, LAST_YEAR.end, "SM", 900.0),
        (LAST_YEAR.label, LAST_YEAR.end, "LS", 800.0),
        (SEASON.label, SEASON.end, "SM", 950.0),
        (SEASON.label, SEASON.end, "LS", 940.0),
    ])
    book = records.book(store, MANAGERS, SEASON.end + timedelta(days=1), WINDOWS)
    titles = book.titles.set_index("manager_id")

    assert titles.loc["SM", "titles"] == 2
    assert titles.loc["LS", "second"] == 2
    # And the best season is the best of the two, not the most recent.
    assert book.seasons[0].value == 950.0
    assert book.seasons[0].season == SEASON.label
