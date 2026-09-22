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


# --- assets, picks, margins, days at the top, head to head ------------------

def _with_slots(tmp_path, snapshots, slots):
    """``slots`` is ``(season, as_of, asset, name, type, category, manager,
    score, cost)``."""
    store = _store(tmp_path, snapshots)
    for _, _, _, _, _, _, manager, _, _ in slots:
        rosters.add_manager(store, manager)
    store.upsert("assets", [
        {"asset_id": a, "asset_type": t, "display_name": n, "league": cat,
         "role": "", "norm_key": cat, "affiliation": "", "active": 1,
         "created_at": "2026-08-21"}
        for _, _, a, n, t, cat, _, _, _ in slots
    ], keys=("asset_id",))
    seats: dict[tuple, int] = {}
    for season, when, asset, _, kind, cat, manager, score, cost in slots:
        seats[(manager, cat)] = seats.get((manager, cat), 0) + 1
        slot_id = f"slot-{asset}"
        store.upsert("roster_slots", [{
            "slot_id": slot_id, "season": season, "manager_id": manager,
            "category": cat, "asset_type": kind,
            "slot_index": seats[(manager, cat)]}], keys=("slot_id",))
        store.upsert("slot_occupancy", [{
            "slot_id": slot_id, "asset_id": asset, "start_date": "2026-08-21",
            "end_date": None, "cost": cost, "note": "draft"}],
            keys=("slot_id", "start_date"))
        store.upsert("slot_scores", [{
            "slot_id": slot_id, "season": season, "as_of": str(when),
            "asset_id": asset, "score": score, "counts": 1}],
            keys=("slot_id", "as_of"))
    store.conn.commit()
    return store


def test_the_best_and_worst_assets_are_the_ends_of_one_list(tmp_path):
    q1 = quarters()[0]
    store = _with_slots(tmp_path, [(SEASON.label, q1.end, "SM", 40.0)], [
        (SEASON.label, q1.end, "a1", "Raphinha", "Player", "MLB", "SM", 32.5, 25.0),
        (SEASON.label, q1.end, "a2", "Broncos", "Team", "NFL", "SM", -0.8, 141.0),
        (SEASON.label, q1.end, "a3", "Messi", "Player", "MLS", "SM", 0.0, 30.0),
    ])
    book = records.book(store, MANAGERS, q1.end, WINDOWS)

    assert [m.name for m in book.best_assets] == ["Raphinha", "Messi", "Broncos"]
    assert [m.name for m in book.worst_assets] == ["Broncos", "Messi", "Raphinha"]
    # The type rides along, because the page defaults the worst list to teams.
    assert book.worst_assets[0].asset_type == "Team"


def test_the_best_pick_is_points_for_the_money(tmp_path):
    q1 = quarters()[0]
    store = _with_slots(tmp_path, [(SEASON.label, q1.end, "SM", 40.0)], [
        (SEASON.label, q1.end, "a1", "Cheap", "Player", "MLB", "SM", 14.6, 1.0),
        (SEASON.label, q1.end, "a2", "Dear", "Player", "MLB", "SM", 32.5, 100.0),
    ])
    book = records.book(store, MANAGERS, q1.end, WINDOWS)

    # The dear one scored more and the cheap one was the better buy.
    assert [m.name for m in book.best_picks] == ["Cheap", "Dear"]
    assert round(book.best_picks[0].per_dollar, 2) == 14.6


def test_a_bigger_overpay_on_a_negative_score_is_the_worse_pick(tmp_path):
    """The bug this guards. Dividing a negative score by a larger price makes
    the bigger overpay look better: -0.80/141 sits above -0.77/125. Clamping a
    negative to zero before dividing leaves price to break the tie."""
    q1 = quarters()[0]
    store = _with_slots(tmp_path, [(SEASON.label, q1.end, "SM", 40.0)], [
        (SEASON.label, q1.end, "a1", "Broncos", "Team", "NFL", "SM", -0.80, 141.0),
        (SEASON.label, q1.end, "a2", "Rams", "Team", "NFL", "SM", -0.77, 125.0),
    ])
    book = records.book(store, MANAGERS, q1.end, WINDOWS)

    assert [m.name for m in book.worst_picks] == ["Broncos", "Rams"]


def test_the_dearest_scoreless_pick_is_the_worst_one(tmp_path):
    """No price floor either way. Every scoreless pick ties at nought per
    dollar, so price breaks the tie and a cheap dud sorts to the bottom of
    the list on its own -- a floor was tried and was borrowed reasoning."""
    q1 = quarters()[0]
    store = _with_slots(tmp_path, [(SEASON.label, q1.end, "SM", 40.0)], [
        (SEASON.label, q1.end, "a1", "Dollar", "Player", "MLB", "SM", 0.0, 1.0),
        (SEASON.label, q1.end, "a2", "Expensive", "Team", "NFL", "SM", 0.0, 200.0),
        (SEASON.label, q1.end, "a3", "Middling", "Team", "NFL", "SM", 0.0, 50.0),
    ])
    book = records.book(store, MANAGERS, q1.end, WINDOWS)

    assert [m.name for m in book.worst_picks] == ["Expensive", "Middling", "Dollar"]


def test_the_margin_belongs_to_the_season_and_names_the_winner(tmp_path):
    store = _store(tmp_path, [
        (LAST_YEAR.label, LAST_YEAR.end, "LS", 900.0),
        (LAST_YEAR.label, LAST_YEAR.end, "SM", 850.0),
        (LAST_YEAR.label, LAST_YEAR.end, "TG", 100.0),
    ])
    book = records.book(store, MANAGERS, date(2026, 9, 20), WINDOWS)

    assert len(book.margins) == 1
    assert book.margins[0].manager == "LS"
    # Over second, not over last.
    assert book.margins[0].value == 50.0
    assert book.margins[0].settled


def test_days_at_the_top_count_days_that_have_happened(tmp_path):
    """Unlike the rest of the book there is nothing to wait for: a title is
    awarded when a season ends, a day at the top happened on the day."""
    store = open_store(str(tmp_path / "d.sqlite3"))
    for m in ("SM", "LS"):
        rosters.add_manager(store, m)
    rows = []
    for day in range(1, 4):
        when = f"2026-09-0{day}"
        rows.append({"season": SEASON.label, "as_of": when, "manager_id": "SM",
                     "total": 100.0 * day, "rank": 1 if day < 3 else 2})
        rows.append({"season": SEASON.label, "as_of": when, "manager_id": "LS",
                     "total": 90.0 * day, "rank": 2 if day < 3 else 1})
    store.upsert("standings_snapshots", rows,
                 keys=("season", "as_of", "manager_id"))
    store.conn.commit()

    out = records.days_at_top(store, MANAGERS).set_index("manager_id")
    assert out.loc["SM", "days"] == 2
    assert out.loc["LS", "days"] == 1
    assert out.attrs["days"] == 3


def _snapshot(store, season, day, leader, others=()):
    """One recorded day, with `leader` on top."""
    rows = [{"season": season, "as_of": day, "manager_id": leader,
             "total": 100.0, "rank": 1}]
    rows += [{"season": season, "as_of": day, "manager_id": m,
              "total": 50.0, "rank": 2} for m in others]
    store.upsert("standings_snapshots", rows,
                 keys=("season", "as_of", "manager_id"))
    store.conn.commit()


def test_the_longest_run_is_counted_in_days_the_site_actually_recorded(tmp_path):
    """A day nobody recorded is a day with no evidence the lead changed hands,
    so it must not break a run the evidence says held. The site has not
    published every day of its life -- a failed build, a season that had not
    started -- and a run that reset on each of those would understate every
    leader who held the top through one."""
    store = open_store(str(tmp_path / "run.sqlite3"))
    for m in ("SM", "LS"):
        rosters.add_manager(store, m)
    # Four recorded days with a fortnight missing in the middle of them.
    for day in ("2026-09-01", "2026-09-02", "2026-09-16", "2026-09-17"):
        _snapshot(store, SEASON.label, day, "SM", others=["LS"])

    out = records.days_at_top(store, MANAGERS).set_index("manager_id")
    assert out.loc["SM", "days"] == 4
    assert out.loc["SM", "streak"] == 4, "a gap in recording is not a gap at the top"


def test_a_run_ends_where_somebody_else_takes_the_top(tmp_path):
    store = open_store(str(tmp_path / "lost.sqlite3"))
    for m in ("SM", "LS"):
        rosters.add_manager(store, m)
    #        SM  SM  LS  SM  SM  SM
    for day, leader in zip(
        ("2026-09-01", "2026-09-02", "2026-09-03",
         "2026-09-04", "2026-09-05", "2026-09-06"),
        ("SM", "SM", "LS", "SM", "SM", "SM"),
    ):
        _snapshot(store, SEASON.label, day, leader,
                  others=[m for m in ("SM", "LS") if m != leader])

    out = records.days_at_top(store, MANAGERS).set_index("manager_id")
    assert out.loc["SM", "days"] == 5
    assert out.loc["SM", "streak"] == 3, "the longest run, not the total"
    assert out.loc["LS", "streak"] == 1


def test_a_new_season_starts_the_run_over(tmp_path):
    """Top in May and top again in September is two runs. Calling it one would
    be a claim about a summer in which the league was not being played."""
    store = open_store(str(tmp_path / "seasons.sqlite3"))
    for m in ("SM", "LS"):
        rosters.add_manager(store, m)
    for day in ("2027-05-14", "2027-05-15"):
        _snapshot(store, "2026-27", day, "SM", others=["LS"])
    for day in ("2027-09-01", "2027-09-02", "2027-09-03"):
        _snapshot(store, "2027-28", day, "SM", others=["LS"])

    out = records.days_at_top(store, MANAGERS).set_index("manager_id")
    assert out.loc["SM", "days"] == 5
    assert out.loc["SM", "streak"] == 3


def test_a_manager_who_has_never_led_has_no_run(tmp_path):
    store = open_store(str(tmp_path / "never.sqlite3"))
    for m in ("SM", "LS"):
        rosters.add_manager(store, m)
    _snapshot(store, SEASON.label, "2026-09-01", "SM", others=["LS"])

    out = records.days_at_top(store, MANAGERS).set_index("manager_id")
    assert out.loc["LS", "days"] == 0
    assert out.loc["LS", "streak"] == 0


def test_a_pair_that_never_met_is_absent_from_the_career_grid(tmp_path):
    store = _store(tmp_path, [(SEASON.label, date(2026, 9, 18), "SM", 40.0)])
    frame = records.career_head_to_head(store, MANAGERS)
    assert frame.empty or "played" in frame.columns
