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
