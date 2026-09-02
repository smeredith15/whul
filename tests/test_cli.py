import pytest

from whul.cli import LEAGUES, main


def test_list_runs(capsys):
    assert main(["list"]) == 0
    assert "nfl" in capsys.readouterr().out


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
