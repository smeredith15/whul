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
