"""MLB source adapter tests.

Both feeds are unreachable from the environment this was written in, so these
exercise the request-shape search and response parsing. Only
`python -m whul.cli probe mlb` proves the endpoints themselves.
"""

import json

import pytest

from whul.sources import mlb


# --- leaderboard row extraction --------------------------------------------

def test_rows_found_under_any_of_the_known_keys():
    """The leaderboard has returned its rows under several keys across versions."""
    for key in mlb.ROW_KEYS:
        assert mlb._rows_from({key: [{"AB": 1}]}) == [{"AB": 1}]


def test_rows_found_when_nested_one_level_deeper():
    assert mlb._rows_from({"payload": {"data": [{"AB": 1}]}}) == [{"AB": 1}]


def test_a_bare_list_is_accepted():
    assert mlb._rows_from([{"AB": 1}]) == [{"AB": 1}]


def test_an_empty_response_yields_no_rows():
    assert mlb._rows_from({"data": []}) == []
    assert mlb._rows_from({}) == []
    assert mlb._rows_from(None) == []


# --- parameter-shape search -------------------------------------------------

def test_variants_are_ordered_most_specific_first():
    variants = mlb.fangraphs_variants(2025, "bat", 100)
    assert len(variants) > 1
    assert len(variants[0]) > len(variants[-1])
    assert all(v["stats"] == "bat" and v["qual"] == 100 for v in variants)


def test_a_shape_returning_no_rows_is_treated_as_suspect(monkeypatch, tmp_path):
    """A 200 with nothing looks identical to a season with no qualifiers, which
    is how a season silently comes back empty."""
    seen = []

    def fake_get(url, params, cache_key=None):
        seen.append(params)
        # Only the last, simplest shape returns anything.
        if "age" in params or "pageitems" in params or "startseason" in params:
            return {"data": []}
        return {"data": [{"PlayerName": "Test", "AB": 400, "H": 120}]}

    monkeypatch.setattr(mlb, "_get", fake_get)
    monkeypatch.setattr(mlb, "CACHE", tmp_path)

    frame = mlb._fangraphs(2025, "bat", 100)
    assert len(frame) == 1
    assert frame.iloc[0]["PlayerName"] == "Test"
    assert len(seen) == 4, "every shape should have been tried"


def test_the_successful_payload_is_cached(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, params, cache_key=None):
        calls.append(params)
        return {"data": [{"PlayerName": "Test", "AB": 400}]}

    monkeypatch.setattr(mlb, "_get", fake_get)
    monkeypatch.setattr(mlb, "CACHE", tmp_path)

    mlb._fangraphs(2025, "bat", 100)
    assert len(calls) == 1
    mlb._fangraphs(2025, "bat", 100)
    assert len(calls) == 1, "the second call reads the cache"


def test_season_is_added_when_the_feed_omits_it(monkeypatch, tmp_path):
    monkeypatch.setattr(mlb, "_get", lambda *a, **k: {"data": [{"PlayerName": "X", "AB": 1}]})
    monkeypatch.setattr(mlb, "CACHE", tmp_path)
    assert mlb._fangraphs(2025, "bat", 100).iloc[0]["Season"] == 2025


def test_every_shape_failing_raises(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("blocked")

    monkeypatch.setattr(mlb, "_get", boom)
    monkeypatch.setattr(mlb, "CACHE", tmp_path)
    with pytest.raises(RuntimeError):
        mlb._fangraphs(2025, "bat", 100)


# --- the Stats API fallback -------------------------------------------------

def test_stats_api_flattens_splits_into_player_rows(monkeypatch, tmp_path):
    payload = {
        "stats": [
            {
                "splits": [
                    {
                        "player": {"fullName": "Aaron Judge", "id": 592450},
                        "stat": {"atBats": 550, "hits": 180, "homeRuns": 50},
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(mlb, "_get", lambda *a, **k: payload)
    frame = mlb.load_stats_api_players(2025, "hitting")
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["player"] == "Aaron Judge"
    assert row["homeRuns"] == 50
    assert row["season"] == 2025


def test_stats_api_cannot_replace_fangraphs():
    """It carries counting stats but not Off, Def or WAR, so substituting it
    would drop scoring components rather than merely change source."""
    import inspect

    doc = inspect.getdoc(mlb.load_stats_api_players)
    assert "fallback" in doc.lower()
    assert "WAR" in doc


def test_season_id_helpers_are_absent_here():
    """MLB seasons are plain years, unlike the NHL's concatenated form."""
    assert not hasattr(mlb, "season_id")
