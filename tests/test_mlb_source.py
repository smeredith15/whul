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


# --- FanGraphs bot protection ----------------------------------------------

def test_a_warmed_session_is_used_for_fangraphs(monkeypatch):
    """A plain request answers 403; a browser collects cookies from the HTML
    leaderboard first, so the session does the same."""
    mlb._SESSION = None
    warmed = []

    class FakeSession:
        headers: dict = {}

        def get(self, url, params=None, timeout=None):
            warmed.append(url)

            class R:
                status_code = 200

                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {"data": [{"PlayerName": "X", "AB": 1}]}

            return R()

    monkeypatch.setattr(mlb.requests, "Session", lambda: FakeSession())
    monkeypatch.setattr(mlb.time, "sleep", lambda _: None)

    session = mlb._session()
    assert warmed == [mlb.FANGRAPHS_HOME], "the HTML page is fetched first"
    assert mlb._session() is session, "the session is reused, not re-warmed"
    mlb._SESSION = None


def test_warm_up_failure_does_not_prevent_the_request(monkeypatch):
    """The API call may still succeed, so a failed warm-up must not be fatal."""
    mlb._SESSION = None

    class FakeSession:
        headers: dict = {}

        def get(self, url, params=None, timeout=None):
            raise RuntimeError("blocked")

    monkeypatch.setattr(mlb.requests, "Session", lambda: FakeSession())
    monkeypatch.setattr(mlb.time, "sleep", lambda _: None)
    assert mlb._session() is not None
    mlb._SESSION = None


# --- MLB's own advanced metrics ---------------------------------------------

def test_sabermetrics_flattens_like_the_season_stats(monkeypatch, tmp_path):
    payload = {
        "stats": [
            {
                "splits": [
                    {
                        "player": {"fullName": "Aaron Judge", "id": 592450},
                        "stat": {"war": 9.1, "wRaa": 75.2, "battingRuns": 70.0},
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(mlb, "_get", lambda *a, **k: payload)
    frame = mlb.load_sabermetrics(2025, "hitting")
    assert frame.iloc[0]["war"] == 9.1
    assert frame.iloc[0]["wRaa"] == 75.2


def test_advanced_equivalents_name_what_each_field_contributes():
    """The mapping is explicit rather than assumed, so a field renamed upstream
    surfaces as a missing component instead of a silent zero."""
    assert mlb.ADVANCED_EQUIVALENTS["war"].startswith("WAR")
    assert "Off" in mlb.ADVANCED_EQUIVALENTS["batting"]
    assert "Def" in mlb.ADVANCED_EQUIVALENTS["fielding"]
    assert mlb.OFFENSE_COMPONENTS == ("batting", "baseRunning")
    assert mlb.DEFENSE_COMPONENTS == ("fielding", "positional")


def test_season_stats_request_all_players_not_just_qualifiers(monkeypatch):
    """The default pool returned 145 rows; the R thresholds admit several hundred."""
    captured = {}

    def fake_get(url, params, cache_key=None):
        captured.update(params)
        return {"stats": []}

    monkeypatch.setattr(mlb, "_get", fake_get)
    mlb.load_stats_api_players(2025, "hitting")
    assert captured["playerPool"] == "All"


# --- rebuilding Offense and Defense -----------------------------------------

def test_offense_and_defense_are_reconstructed_not_approximated():
    """FanGraphs defines Off as batting + base running and Def as fielding +
    positional, and the Stats API returns all four components."""
    import pandas as pd

    saber = pd.DataFrame([{"player_id": 1, "batting": 45.0, "baseRunning": 5.0,
                           "fielding": 8.0, "positional": -3.0, "war": 6.0}])
    out = mlb.derive_offense_defense(saber).iloc[0]
    assert out["Off"] == pytest.approx(50.0)
    assert out["Def"] == pytest.approx(5.0)


def test_missing_components_raise_rather_than_scoring_zero():
    import pandas as pd

    with pytest.raises(KeyError, match="Def"):
        mlb.derive_offense_defense(pd.DataFrame([{"batting": 10.0, "baseRunning": 1.0}]))


def test_absent_component_values_are_treated_as_zero():
    import pandas as pd

    saber = pd.DataFrame([{"batting": 40.0, "baseRunning": None,
                           "fielding": None, "positional": 2.0}])
    out = mlb.derive_offense_defense(saber).iloc[0]
    assert out["Off"] == pytest.approx(40.0)
    assert out["Def"] == pytest.approx(2.0)


# --- innings notation -------------------------------------------------------

def test_innings_are_thirds_not_decimals():
    """200.1 means 200 and one third. Read as a decimal it understates by a
    factor of three, and at 7.4 points per inning that is not rounding."""
    assert mlb.innings_to_float("200.1") == pytest.approx(200 + 1 / 3)
    assert mlb.innings_to_float("200.2") == pytest.approx(200 + 2 / 3)
    assert mlb.innings_to_float("200.0") == 200.0
    assert mlb.innings_to_float("200") == 200.0


def test_innings_tolerate_missing_values():
    assert mlb.innings_to_float(None) == 0.0
    assert mlb.innings_to_float("") == 0.0


def test_a_third_of_an_inning_is_worth_more_than_two_points():
    from whul.scoring.mlb import PITCHER_WEIGHTS

    naive = 200.1
    correct = mlb.innings_to_float("200.1")
    assert (correct - naive) * PITCHER_WEIGHTS["ip"] > 1.7


# --- cache invalidation -----------------------------------------------------

def test_cache_key_reflects_the_parameters(tmp_path, monkeypatch):
    """Changing a request must not keep serving the old response -- that is what
    kept a qualified-players-only reply alive after the fix."""
    monkeypatch.setattr(mlb, "CACHE", tmp_path)
    a = mlb._cache_path("statsapi/hitting_2025", {"playerPool": "Qualified"})
    b = mlb._cache_path("statsapi/hitting_2025", {"playerPool": "All"})
    assert a != b


# --- the probe reflects the two-way pipeline --------------------------------

def test_probe_folds_two_way_players_after_normalizing(monkeypatch):
    """score_players emits one row per player-role; is_two_way only exists after
    combine_two_way, which runs post-normalization. The probe must follow that
    order rather than expecting the column earlier."""
    import pandas as pd

    from whul.normalize import apply_benchmarks, compute_benchmarks
    from whul.scoring import mlb as scoring

    bats = pd.DataFrame(
        [{"PlayerName": f"B{i}", "season": 2025, "AB": 400, "H": 100 + i, "2B": 20,
          "3B": 1, "HR": 20, "BB": 50, "HBP": 3, "SB": 5, "CS": 2,
          "Off": 10.0, "Def": 5.0, "G": 150} for i in range(70)]
        + [{"PlayerName": "TwoWay", "season": 2025, "AB": 400, "H": 170, "2B": 30,
            "3B": 5, "HR": 45, "BB": 80, "HBP": 5, "SB": 20, "CS": 3,
            "Off": 60.0, "Def": -5.0, "G": 155}]
    )
    pits = pd.DataFrame(
        [{"PlayerName": f"P{i}", "season": 2025, "IP": 150.0, "SO": 150 + i, "H": 130,
          "BB": 40, "HBP": 4, "HR": 15, "SV": 0, "HLD": 0, "WAR": 3.0, "G": 30}
         for i in range(70)]
        + [{"PlayerName": "TwoWay", "season": 2025, "IP": 120.0, "SO": 160, "H": 90,
            "BB": 35, "HBP": 3, "HR": 10, "SV": 0, "HLD": 0, "WAR": 4.0, "G": 22}]
    )

    roles = scoring.score_players(bats, pits)
    assert "is_two_way" not in roles.columns, "not available before the fold"

    combined = scoring.combine_two_way(
        apply_benchmarks(roles, compute_benchmarks(roles, "Player"), "Player")
    )
    assert "is_two_way" in combined.columns
    assert int(combined["is_two_way"].sum()) == 1
    assert len(combined) == len(roles) - 1, "the two-way player's rows collapse to one"


# --- counting only what the league year covers -------------------------------

def test_mlb_starts_when_the_earliest_other_league_does():
    """Baseball is mid-season when the league year opens and its feed reports
    season totals, so without a start date a manager is credited with a player's
    April. There is no baseball event to anchor on the way there is a matchday
    or a green flag, so it takes the earliest date any other league starts."""
    from whul.config.league import LEAGUE_START, season_start

    others = [d for league, d in LEAGUE_START.items() if league != "MLB"]
    assert season_start("MLB") == min(others)


def test_a_date_range_asks_the_api_for_one():
    from datetime import date
    from unittest import mock

    from whul.sources import mlb

    seen = {}

    def capture(url, params, cache_key=None):
        seen.update(params)
        return {"stats": []}

    with mock.patch.object(mlb, "_get", capture):
        mlb.load_stats_api_players(2026, "hitting", since=date(2026, 8, 15),
                                   until=date(2026, 9, 4))
    assert seen["stats"] == "byDateRange"
    assert seen["startDate"] == "2026-08-15"
    assert seen["endDate"] == "2026-09-04"


def test_without_a_range_the_whole_season_is_asked_for():
    from unittest import mock

    from whul.sources import mlb

    seen = {}
    with mock.patch.object(mlb, "_get",
                           lambda u, p, cache_key=None: seen.update(p) or {"stats": []}):
        mlb.load_stats_api_players(2026, "hitting")
    assert seen["stats"] == "season"
    assert "startDate" not in seen


def test_a_range_the_api_ignored_is_refused():
    """The Stats API ignores parameters it does not recognise rather than
    rejecting them, so an unsupported range comes back as a full season of
    perfectly valid-looking numbers -- the player exists, the lines parse, the
    totals are real, they are simply four months too generous. Nothing
    downstream could tell."""
    from datetime import date

    import pandas as pd
    import pytest

    from whul.sources import mlb

    whole = pd.DataFrame({"season": [2026] * 3, "gamesPlayed": [140, 150, 130]})
    with pytest.raises(RuntimeError, match="byDateRange is not being applied"):
        mlb._check_range_applied(whole.copy(), whole, "hitting", date(2026, 8, 15))


def test_a_range_that_was_applied_passes():
    from datetime import date

    import pandas as pd

    from whul.sources import mlb

    whole = pd.DataFrame({"season": [2026] * 3, "gamesPlayed": [140, 150, 130]})
    ranged = pd.DataFrame({"season": [2026] * 3, "gamesPlayed": [18, 19, 17]})
    mlb._check_range_applied(ranged, whole, "hitting", date(2026, 8, 15))


def test_the_advanced_figures_cover_the_same_span(monkeypatch):
    """WAR is itself a season total. A whole season of it added to six weeks of
    hits would weight one player's WAR as heavily as another's whole summer."""
    from datetime import date

    import pandas as pd

    from whul.sources import mlb

    asked = []
    monkeypatch.setattr(mlb, "load_players_since",
                        lambda s, g, since, until=None: pd.DataFrame(
                            {"player_id": [1], "season": [s], "gamesPlayed": [20]}))

    def saber(season, group="hitting", since=None, until=None):
        asked.append(since)
        return pd.DataFrame()

    monkeypatch.setattr(mlb, "load_sabermetrics", saber)
    mlb._merge_counting_and_advanced(2026, "hitting", date(2026, 8, 15))
    assert asked == [date(2026, 8, 15)]


def test_the_mlb_source_has_a_live_builder_distinct_from_its_history():
    from whul.benchmark_sources import SOURCES

    assert SOURCES["mlb"].live is not None
