"""Reading the tennis2026 app's ongoing match database.

The static snapshot stops in February 2026 and the Flashscore feed reaches back
seven days, so this is what covers the months between. It is a local file read,
which means it can be tested for real rather than mocked.
"""

import sqlite3
from datetime import date

import pytest
from pathlib import Path

from whul.scoring import tennis
from whul.sources import tennis2026

SCHEMA = """
CREATE TABLE players (id INTEGER PRIMARY KEY, name TEXT, tour TEXT);
CREATE TABLE tournaments (
    id INTEGER PRIMARY KEY, name TEXT, tour TEXT, category TEXT,
    draw_size INTEGER, start_date TEXT
);
CREATE TABLE match_results (
    id INTEGER PRIMARY KEY, tournament_id INTEGER, player_id INTEGER,
    opponent_id INTEGER, round TEXT, won INTEGER, score TEXT,
    walkover INTEGER DEFAULT 0, retired INTEGER DEFAULT 0, match_date TEXT
);
"""


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "tennis2026.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO players (id, name, tour) VALUES (?, ?, ?)",
        [(1, "Carlos Alcaraz", "ATP"), (2, "Jannik Sinner", "ATP"),
         (3, "Iga Swiatek", "WTA"), (4, "Aryna Sabalenka", "WTA")],
    )
    conn.executemany(
        "INSERT INTO tournaments (id, name, tour, category, draw_size, start_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(10, "US Open", "ATP", "Grand Slam", 128, "2026-08-31"),
         (11, "US Open", "WTA", "Grand Slam", 128, "2026-08-31"),
         (12, "Cincinnati", "ATP", "ATP Masters 1000", 96, "2026-08-10")],
    )
    conn.commit()
    return path


def add(database, **kwargs):
    row = {
        "tournament_id": 10, "player_id": 1, "opponent_id": 2, "round": "F",
        "won": 1, "score": "6-3 6-4 6-2", "walkover": 0, "retired": 0,
        "match_date": "2026-09-01",
    }
    row.update(kwargs)
    conn = sqlite3.connect(database)
    conn.execute(
        "INSERT INTO match_results (tournament_id, player_id, opponent_id, round, "
        "won, score, walkover, retired, match_date) VALUES "
        "(:tournament_id, :player_id, :opponent_id, :round, :won, :score, "
        ":walkover, :retired, :match_date)",
        row,
    )
    conn.commit()


def test_a_win_comes_back_in_the_shape_the_scorer_expects(database):
    add(database)
    rows = tennis2026.load_matches(database, verbose=False)

    assert len(rows) == 1
    row = rows.iloc[0]
    assert row["winner"] == "Carlos Alcaraz" and row["loser"] == "Jannik Sinner"
    assert row["category"] == tennis.GRAND_SLAM
    assert row["draw_size"] == 128 and row["round"] == "F" and row["season"] == 2026
    # The whole point is that it can be scored without further translation.
    assert not tennis.score_matches(rows).empty


def test_a_loss_is_not_a_row(database):
    add(database, player_id=2, opponent_id=1, won=0)
    assert tennis2026.load_matches(database, verbose=False).empty


def test_qualifying_never_reaches_the_scorer(database):
    """A missing qualifying result would read as a main-draw bye and pay for a
    round nobody played, so it is excluded in the query rather than later."""
    add(database, round="Q3")
    assert tennis2026.load_matches(database, verbose=False).empty


def test_a_walkover_is_dropped(database):
    add(database, walkover=1)
    assert tennis2026.load_matches(database, verbose=False).empty


def test_since_filters_to_the_league_year(database):
    add(database, tournament_id=12, round="F", match_date="2026-08-22")  # Cincinnati
    add(database, match_date="2026-09-01")                               # US Open
    rows = tennis2026.load_matches(database, since=date(2026, 8, 23), verbose=False)
    assert list(rows["tournament"]) == ["US Open"]


def test_both_tours_come_back_and_stay_apart(database):
    add(database, player_id=3, opponent_id=4, tournament_id=11)
    add(database)
    rows = tennis2026.load_matches(database, verbose=False)
    assert set(rows["tour"]) == {"ATP", "WTA"}
    scored = tennis.score_players(rows)
    assert set(scored["league"]) == {"ATP", "WTA"}


def test_an_enum_written_by_name_still_resolves(database):
    # SQLAlchemy writes an enum by name or by value depending on the column;
    # an unmapped category would be dropped and score nothing.
    conn = sqlite3.connect(database)
    conn.execute("UPDATE tournaments SET category = 'GRAND_SLAM' WHERE id = 10")
    conn.commit()
    add(database)
    rows = tennis2026.load_matches(database, verbose=False)
    assert list(rows["category"]) == [tennis.GRAND_SLAM]


def test_a_missing_database_says_where_it_looked(tmp_path):
    with pytest.raises(FileNotFoundError) as caught:
        tennis2026.load_matches(tmp_path / "nope.db")
    message = str(caught.value)
    assert "nope.db" in message, "the path it tried has to be in the message"
    assert "WHUL_TENNIS2026_DB" in message


def test_every_candidate_path_is_reported_not_just_the_first(monkeypatch):
    monkeypatch.delenv("WHUL_TENNIS2026_DB", raising=False)
    monkeypatch.delenv("WHUL_TENNIS2026", raising=False)
    candidates = tennis2026.candidate_paths()
    assert len(candidates) > 1
    assert tennis2026.probe()["looked_in"] == [str(c) for c in candidates]


def test_an_explicit_path_overrides_the_search(tmp_path, monkeypatch):
    monkeypatch.setenv("WHUL_TENNIS2026_DB", str(tmp_path / "env.db"))
    assert tennis2026.candidate_paths(tmp_path / "given.db") == [tmp_path / "given.db"]


def test_the_probe_reports_the_span_it_holds(database):
    add(database, match_date="2026-08-25")
    add(database, round="SF", match_date="2026-09-03")
    report = tennis2026.probe(database)

    assert report["matches"] == 2
    assert report["first"] == "2026-08-25" and report["last"] == "2026-09-03"
    assert report["seasons"] == [2026]


def test_the_probe_reports_a_missing_file_rather_than_raising(tmp_path):
    report = tennis2026.probe(tmp_path / "nope.db")
    assert report["exists"] is False and "error" in report


# --- the app's two spellings of one category ---------------------------------

def test_both_spellings_of_a_category_resolve():
    """SQLAlchemy's Enum persists the member *name*, so the app's database holds
    ATP_MASTERS_1000 where its own Python reads "ATP Masters 1000", and a row
    written another way can hold either."""
    from whul.sources.tennis2026 import CATEGORY_LOOKUP, _category_key

    for spelling in ("ATP_MASTERS_1000", "ATP Masters 1000", "WTA_1000",
                     "TournamentCategory.ATP_MASTERS_1000"):
        from whul.scoring.tennis import MASTERS_1000
        assert CATEGORY_LOOKUP[_category_key(spelling)] == MASTERS_1000, spelling


def test_every_category_the_app_defines_is_mapped():
    """The app's enum is the whole vocabulary. One member unmapped is a tier of
    tournaments dropped -- reported, but dropped."""
    from whul.sources.tennis2026 import CATEGORY_LOOKUP, _category_key

    app_members = (
        "GRAND_SLAM", "ATP_MASTERS_1000", "ATP_500", "ATP_250", "ATP_FINALS",
        "WTA_1000", "WTA_500", "WTA_250", "WTA_FINALS", "INTERNATIONAL",
    )
    unmapped = [m for m in app_members if _category_key(m) not in CATEGORY_LOOKUP]
    assert not unmapped, f"these tiers would be dropped entirely: {unmapped}"


def test_title_casing_a_member_name_is_not_enough():
    """The reason the previous fallback failed: .title() lowercases ATP, so
    every ATP and WTA event was dropped and only the Grand Slams and the
    Internationals came through -- most of the tour, silently absent from a
    benchmark that would then be drawn from four majors a year."""
    from whul.sources.tennis2026 import CATEGORIES

    assert "ATP_MASTERS_1000".replace("_", " ").title() not in CATEGORIES
    assert "Atp Masters 1000" not in CATEGORIES


def test_a_copy_in_the_project_data_directory_is_found(tmp_path, monkeypatch):
    """The app's checkout is not on every machine that scores tennis, so a copy
    carried over and dropped in data/ is the ordinary case. Requiring an
    environment variable for it means the nightly run works only when someone
    remembered to export one."""
    from whul.sources import tennis2026

    monkeypatch.delenv("WHUL_TENNIS2026_DB", raising=False)
    monkeypatch.delenv("WHUL_TENNIS2026", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    copy = tmp_path / "data" / "tennis2026-whul.db"
    copy.touch()

    assert tennis2026.default_path() == Path("data/tennis2026-whul.db")


def test_an_explicit_path_still_wins(tmp_path, monkeypatch):
    from whul.sources import tennis2026

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tennis2026-whul.db").touch()
    monkeypatch.setenv("WHUL_TENNIS2026_DB", "/somewhere/else.db")

    assert tennis2026.default_path() == Path("/somewhere/else.db")
