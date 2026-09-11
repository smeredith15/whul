import pytest

from whul.cli import LEAGUES, main


def test_list_runs(capsys):
    assert main(["list"]) == 0
    assert "nfl" in capsys.readouterr().out


def test_list_columns_do_not_run_together(capsys):
    """A long value must not collide with the next column."""
    main(["list"])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("nba")]
    assert lines, "nba row missing"
    for cfg_value in (LEAGUES["nba"]["seasons"], LEAGUES["nba"]["source"]):
        assert f" {cfg_value}" in lines[0] or lines[0].endswith(cfg_value)
    assert "ESPNESPN" not in lines[0]


def test_every_league_declares_its_assets_and_source():
    for name, cfg in LEAGUES.items():
        assert cfg["assets"], name
        assert cfg["source"], name
        assert callable(cfg["fn"]), name


def test_unsupported_asset_type_is_rejected(monkeypatch, capsys):
    monkeypatch.setitem(LEAGUES["nfl"], "assets", ("players",))
    assert main(["score", "nfl", "--season", "2024", "--assets", "teams"]) == 2
    assert "no 'teams'" in capsys.readouterr().err


def test_unknown_league_exits():
    with pytest.raises(SystemExit):
        main(["score", "cricket", "--season", "2024"])


# --- the nightly job's exit code -------------------------------------------

def test_rollup_fails_only_when_it_produced_nothing(tmp_path, capsys):
    """A stale feed or an overlapping slot is worth seeing, but failing the
    nightly build over one would take the site down rather than let it go a
    day stale. A run that produced no standings at all is a real failure."""
    import argparse
    from datetime import date

    from whul import simulate
    from whul.cli import cmd_rollup

    db = tmp_path / "whul.sqlite3"

    def run(season):
        return cmd_rollup(argparse.Namespace(
            db=str(db), season=season, date=None, backfill=False,
        ))

    from whul.store import open_store

    store = open_store(db)
    simulate.generate(store, seed=1, end=date(2026, 9, 30), verbose=False)
    store.conn.commit()

    assert run(simulate.SIM_SEASON) == 0
    assert run("no-such-season") == 1


def test_a_warning_is_printed_once(tmp_path, capsys):
    import argparse

    from whul.cli import cmd_rollup

    cmd_rollup(argparse.Namespace(
        db=str(tmp_path / "empty.sqlite3"), season="nothing",
        date=None, backfill=False,
    ))
    assert capsys.readouterr().out.count("no frozen benchmark") == 1


def test_derive_accepts_frozen_instead_of_a_version_id(tmp_path, capsys):
    """So an unattended run does not have to parse an id out of `versions`.
    A script that derived from the wrong scale would produce a plausible,
    wrong one, which is the failure `derive` exists to make cheap to fix."""
    import argparse

    from whul.cli import cmd_benchmarks_derive
    from whul.store import benchmarks as bm
    from whul.store import open_store

    import pandas as pd

    db = tmp_path / "whul.sqlite3"
    store = open_store(str(db))
    version = bm.save(store, pd.DataFrame([{
        "asset_type": "Team", "norm_key": "NFL", "benchmark": 100.0,
        "pool_size": 40, "seasons": "2021-2025",
    }]), "2026-27", notes="first")
    bm.freeze(store, version)

    args = argparse.Namespace(db=str(db), version="frozen", season=None, notes="")
    assert cmd_benchmarks_derive(args) == 0
    out = capsys.readouterr().out
    assert f"frozen -> {version}" in out
    assert "copied from" in out


def test_derive_from_frozen_says_so_when_there_is_none(tmp_path, capsys):
    import argparse

    from whul.cli import cmd_benchmarks_derive
    from whul.store import open_store

    db = tmp_path / "whul.sqlite3"
    open_store(str(db))
    args = argparse.Namespace(db=str(db), version="frozen", season=None, notes="")
    assert cmd_benchmarks_derive(args) == 1
    assert "No frozen version" in capsys.readouterr().err


# --- pruning assets the spreadsheet no longer names --------------------------

def _stocked(tmp_path, held: list[str], stale: list[str]):
    """A store with some assets on a roster slot and some not."""
    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("managers", [{"manager_id": "TG", "display_name": "TG",
                               "active": 1}], ["manager_id"])
    store.upsert("assets", [
        {"asset_id": a, "asset_type": "Player", "display_name": a,
         "league": "ATP", "norm_key": "ATP", "created_at": "2026-08-21"}
        for a in held + stale
    ], ["asset_id"])
    store.upsert("roster_slots", [
        {"slot_id": f"s{i}", "manager_id": "TG", "season": "2026-27",
         "category": "Tennis", "asset_type": "Player", "slot_index": i}
        for i, _ in enumerate(held)
    ], ["slot_id"])
    store.upsert("slot_occupancy", [
        {"slot_id": f"s{i}", "asset_id": a, "start_date": "2026-08-21",
         "end_date": None, "cost": 0.0, "note": ""}
        for i, a in enumerate(held)
    ], ["slot_id", "start_date"])
    return store


def test_prune_removes_only_what_the_spreadsheet_stopped_naming(tmp_path, capsys):
    """Twelve tennis players sat under `Tennis` after the roster moved them to
    `ATP` and `WTA` -- scoring until the correction landed, then quiet,
    invisible to the site and still in the table."""
    import argparse

    from whul.cli import cmd_prune_assets

    held = [f"held-{i}" for i in range(20)]
    store = _stocked(tmp_path, held, ["gone-1", "gone-2"])
    args = argparse.Namespace(db=str(tmp_path / "whul.sqlite3"),
                              season="2026-27", write=True)
    assert cmd_prune_assets(args) == 0
    left = set(store.query("SELECT asset_id FROM assets")["asset_id"])
    assert left == set(held)


def test_prune_refuses_when_nothing_is_rostered(tmp_path, capsys):
    """Before an import every asset looks unheld, and the difference between
    twenty-four duplicates and everything is the whole point of the guard."""
    import argparse

    from whul.cli import cmd_prune_assets

    store = _stocked(tmp_path, [], ["a", "b"])
    args = argparse.Namespace(db=str(tmp_path / "whul.sqlite3"),
                              season="2026-27", write=True)
    assert cmd_prune_assets(args) == 1
    assert "Nothing is rostered" in capsys.readouterr().err
    assert len(store.query("SELECT asset_id FROM assets")) == 2


def test_prune_refuses_an_implausible_share(tmp_path, capsys):
    """A half-read spreadsheet looks exactly like a tidy-up, until the numbers
    are looked at."""
    import argparse

    from whul.cli import cmd_prune_assets

    store = _stocked(tmp_path, ["held"], [f"gone-{i}" for i in range(9)])
    args = argparse.Namespace(db=str(tmp_path / "whul.sqlite3"),
                              season="2026-27", write=True)
    assert cmd_prune_assets(args) == 1
    assert "Refusing" in capsys.readouterr().err
    assert len(store.query("SELECT asset_id FROM assets")) == 10


def test_prune_without_write_removes_nothing(tmp_path):
    import argparse

    from whul.cli import cmd_prune_assets

    store = _stocked(tmp_path, [f"held-{i}" for i in range(20)], ["gone"])
    args = argparse.Namespace(db=str(tmp_path / "whul.sqlite3"),
                              season="2026-27", write=False)
    assert cmd_prune_assets(args) == 0
    assert len(store.query("SELECT asset_id FROM assets")) == 21


def test_prune_takes_the_history_with_it(tmp_path):
    """An asset removed while its scores stay behind is a foreign-key failure
    on the next write, or a row nothing ever reads again."""
    import json

    from whul.cli import cmd_prune_assets
    import argparse

    store = _stocked(tmp_path, [f"held-{i}" for i in range(20)], ["gone"])
    # daily_scores names a benchmark version, so one has to exist for the
    # foreign key -- which is also why pruning has to clear it.
    store.upsert("benchmark_versions", [{
        "version": "v1", "season": "2026-27", "quantile": 0.99, "managers": 15,
        "computed_at": "2026-09-05T09:00:00Z", "notes": "",
    }], ["version"])
    store.upsert("daily_scores", [{
        "asset_id": "gone", "season": "2026-27", "as_of": "2026-09-05",
        "league_points": 10.0, "postseason_bonus": 0.0, "scaled_score": 5.0,
        "benchmark_version": "v1", "computed_at": "2026-09-05T09:00:00Z",
    }], ["asset_id", "season", "as_of"])
    store.upsert("raw_stats", [{
        "asset_id": "gone", "league": "ATP", "season": "2026-27",
        "as_of": "2026-09-05", "source": "test", "phase": "regular",
        "stats": json.dumps({"events": 1}), "fetched_at": "2026-09-05T09:00:00Z",
    }], ["asset_id", "season", "as_of", "source", "phase"])

    args = argparse.Namespace(db=str(tmp_path / "whul.sqlite3"),
                              season="2026-27", write=True)
    assert cmd_prune_assets(args) == 0
    for table in ("daily_scores", "raw_stats"):
        left = store.query(f"SELECT asset_id FROM {table} WHERE asset_id = 'gone'")
        assert left.empty, f"{table} still names a removed asset"


# --- the database that was never there -------------------------------------

def test_a_missing_database_says_so_rather_than_reading_as_empty(tmp_path, capsys):
    """The one failure this project cares about most, in the CLI itself.

    ``open_store`` creates the file when it is not there -- which is right for
    ``simulate`` and for a first run in CI, and wrong for every read: the new
    database answers every question with "nothing", and "Nothing rostered in
    2026-27" is exactly what a real empty roster looks like. The database is
    gitignored and lives on the `data` branch, so a fresh clone hits this on
    the first command it runs.
    """
    import argparse

    from whul.cli import cmd_images_needed

    missing = tmp_path / "not-here.sqlite3"
    args = argparse.Namespace(db=str(missing), season="2026-27", images=None)
    assert cmd_images_needed(args) == 1
    out = capsys.readouterr().out
    assert "Nothing rostered" in out
    assert "There is no database" in out
    assert "git show origin/data:data/whul.sqlite3" in out


def test_a_database_that_is_there_and_empty_does_not_blame_the_clone(tmp_path, capsys):
    """An empty database somebody built is a different problem, and saying
    "fetch the data branch" to a league that simply has not imported its draft
    would send them to overwrite the one they have."""
    import argparse

    from whul.cli import cmd_images_needed
    from whul.store import open_store

    present = tmp_path / "empty.sqlite3"
    open_store(present)  # creates it; the next open finds it there
    args = argparse.Namespace(db=str(present), season="2026-27", images=None)
    assert cmd_images_needed(args) == 1
    out = capsys.readouterr().out
    assert "Nothing rostered" in out
    assert "There is no database" not in out


def test_the_store_records_whether_it_found_a_database_or_made_one(tmp_path):
    from whul.store import open_store
    from whul.store.db import missing_database_note

    path = tmp_path / "whul.sqlite3"
    made = open_store(path)
    assert made.existed is False
    assert missing_database_note(made)

    found = open_store(path)
    assert found.existed is True
    assert missing_database_note(found) == ""


def test_an_in_memory_store_is_never_a_missing_one():
    """Every test opens one of these, and none of them is a broken checkout."""
    from whul.store import open_store
    from whul.store.db import missing_database_note

    assert missing_database_note(open_store(":memory:")) == ""


def _titled(tmp_path, days: dict, league: str = "NFL", norm_key: str = "NFL"):
    """A store holding one team's stored figures across several days.

    ``days`` maps a date to the payload stored for it. Everything else -- the
    asset, a frozen benchmark, and a scored row per day -- is the minimum the
    retraction reads.
    """
    import json

    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("assets", [
        {"asset_id": "team-x", "asset_type": "Team", "display_name": "X",
         "league": league, "norm_key": norm_key, "created_at": "2026-08-21"},
    ], ["asset_id"])
    store.upsert("benchmark_versions", [
        {"version": "v1", "season": "2026-27", "quantile": 0.99, "managers": 5,
         "computed_at": "2026-09-01", "frozen_at": "2026-09-01", "notes": ""},
    ], ["version"])
    store.upsert("benchmarks", [
        {"version": "v1", "asset_type": "Team", "norm_key": norm_key,
         "benchmark": 100.0, "pool_size": 50, "seasons": "2025"},
    ], ["version", "asset_type", "norm_key"])
    store.upsert("raw_stats", [
        {"season": "2026-27", "as_of": day, "asset_id": "team-x",
         "source": "test", "phase": "regular", "league": league,
         "stats": json.dumps(payload), "fetched_at": day}
        for day, payload in days.items()
    ], ["asset_id", "season", "as_of", "source", "phase"])
    store.upsert("daily_scores", [
        {"season": "2026-27", "as_of": day, "asset_id": "team-x",
         "scaled_score": float(payload["total_points"]),
         "league_points": float(payload["total_points"]),
         "benchmark_version": "v1", "computed_at": day}
        for day, payload in days.items()
    ], ["asset_id", "season", "as_of"])
    store.conn.commit()
    return store


def _retract(tmp_path, **kw):
    import argparse

    from whul import cli

    args = argparse.Namespace(db=str(tmp_path / "whul.sqlite3"),
                              season="2026-27", all=False, dry_run=False)
    for k, v in kw.items():
        setattr(args, k, v)
    return cli.cmd_retract_titles(args)


def _stored(tmp_path, day: str) -> dict:
    import json

    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    row = store.query(
        "SELECT stats FROM raw_stats WHERE as_of = ? AND asset_id = 'team-x'",
        (day,))
    return json.loads(row["stats"].iloc[0])


def test_a_title_the_rules_withdrew_is_taken_off_the_total(tmp_path, capsys):
    """New England carried an NFL division title after one game, which they
    lost. `settled_seasons` stopped it happening again and cannot reach the day
    it happened on: `rescore` re-divides a stored total and never re-runs a
    scorer, so yesterday keeps the title, today does not, and the ledger reads
    the correction as a fifteen-point collapse by a team that did nothing."""
    _titled(tmp_path, {
        "2026-09-10": {"league": "NFL", "div_champ": 1, "total_points": 14.7},
        "2026-09-11": {"league": "NFL", "div_champ": 0, "total_points": -0.3},
    })
    assert _retract(tmp_path) == 0

    was = _stored(tmp_path, "2026-09-10")
    assert was["div_champ"] == 0
    # Fifteen, the scorer's own weight for it -- not one, which a flag read as
    # its own points would have cost.
    assert was["total_points"] == pytest.approx(-0.3)
    # And the day after is what it always was, so the ledger differences to zero.
    assert _stored(tmp_path, "2026-09-11")["total_points"] == pytest.approx(-0.3)


def test_a_title_carrying_its_own_points_is_taken_back_at_those(tmp_path):
    """MLB and the NCAA store the points beside the flag, and those are
    authoritative: a conference title is a pool split between however many
    teams tied for it, so its value is not a constant to look up."""
    _titled(tmp_path, {
        "2026-09-07": {"league": "NCAAF", "pts_reg_champ": 6.0, "total_points": 21.95},
        "2026-09-08": {"league": "NCAAF", "pts_reg_champ": 0.0, "total_points": 15.95},
    }, league="NCAAF", norm_key="NCAAF")
    assert _retract(tmp_path) == 0
    assert _stored(tmp_path, "2026-09-07")["total_points"] == pytest.approx(15.95)


def test_a_title_still_held_on_the_newest_day_is_left_alone(tmp_path, capsys):
    """A division won in January is a division won. Only a title that is gone
    by the newest stored day is one the rules withdrew."""
    _titled(tmp_path, {
        "2027-01-04": {"league": "NFL", "div_champ": 1, "total_points": 120.0},
        "2027-01-05": {"league": "NFL", "div_champ": 1, "total_points": 122.0},
    })
    assert _retract(tmp_path) == 0
    assert _stored(tmp_path, "2027-01-04")["total_points"] == pytest.approx(120.0)
    assert "left alone" in capsys.readouterr().out


def test_all_reaches_a_title_that_has_no_later_day_to_be_gone_on(tmp_path):
    """Miami's ACC title sat on its last four stored days and there was no
    fifth, because its feed stopped answering. Nothing later says the rules
    withdrew it, and it was still six points for a 1-0 conference record."""
    _titled(tmp_path, {
        "2026-09-09": {"league": "NCAAF", "pts_reg_champ": 6.0, "total_points": 21.95},
        "2026-09-10": {"league": "NCAAF", "pts_reg_champ": 6.0, "total_points": 21.95},
    }, league="NCAAF", norm_key="NCAAF")
    assert _retract(tmp_path) == 0, "the careful rule sees nothing to do"
    assert _stored(tmp_path, "2026-09-10")["total_points"] == pytest.approx(21.95)

    assert _retract(tmp_path, all=True) == 0
    for day in ("2026-09-09", "2026-09-10"):
        assert _stored(tmp_path, day)["total_points"] == pytest.approx(15.95)
        assert _stored(tmp_path, day)["pts_reg_champ"] == 0


def test_a_dry_run_writes_nothing(tmp_path, capsys):
    _titled(tmp_path, {
        "2026-09-10": {"league": "NFL", "div_champ": 1, "total_points": 14.7},
        "2026-09-11": {"league": "NFL", "div_champ": 0, "total_points": -0.3},
    })
    assert _retract(tmp_path, dry_run=True) == 0
    assert _stored(tmp_path, "2026-09-10")["total_points"] == pytest.approx(14.7)
    assert "nothing was written" in capsys.readouterr().out


def test_a_season_with_no_title_to_take_back_says_so(tmp_path, capsys):
    _titled(tmp_path, {
        "2026-09-10": {"league": "NFL", "div_champ": 0, "total_points": 14.7},
    })
    assert _retract(tmp_path) == 0
    assert "No title to take back" in capsys.readouterr().out
