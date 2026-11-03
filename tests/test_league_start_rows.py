"""A row recorded before its league starts counting is read by nothing.

MLS clubs drafted for 2027 kept two days of September 2026 after their start
moved to January. The standings ignored them; each asset's "latest row" did
not, so the Philadelphia Union showed points on its manager's page.
"""

import json
from datetime import date

from whul.store import open_store

SEASON = "2026-27"


def _asset(store, asset_id, league):
    store.upsert("assets", [{
        "asset_id": asset_id, "asset_type": "Team", "display_name": asset_id,
        "league": league, "role": "", "norm_key": league, "active": 1,
        "created_at": "2026-08-01", "affiliation": "",
    }], keys=("asset_id",))


def _day(store, asset_id, league, as_of, matches, score):
    store.upsert("raw_stats", [{
        "asset_id": asset_id, "league": league, "season": SEASON,
        "as_of": as_of, "source": league.lower(), "phase": "regular",
        "stats": json.dumps({"team": asset_id, "matches_played": matches}),
        "fetched_at": as_of,
    }], keys=("asset_id", "season", "as_of", "source", "phase"))
    store.upsert("daily_scores", [{
        "asset_id": asset_id, "season": SEASON, "as_of": as_of,
        "league_points": float(matches), "postseason_bonus": 0.0,
        "scaled_score": score, "benchmark_version": "v", "computed_at": as_of,
        "held_score": 0.0,
    }], keys=("asset_id", "season", "as_of"))


def _store():
    store = open_store(":memory:")
    store.upsert("benchmark_versions", [{
        "version": "v", "season": SEASON, "quantile": 0.99, "managers": 8,
        "computed_at": "2026-08-01",
    }], keys=("version",))
    _asset(store, "union", "MLS")      # counts from 2027-01-01
    _asset(store, "celtics", "NBA")    # counts from 2026-10-20
    _day(store, "union", "MLS", "2026-09-05", 2, 3.05)
    _day(store, "celtics", "NBA", "2026-10-25", 3, 4.0)
    return store


def test_the_latest_readers_skip_a_row_from_before_the_league_starts():
    store = _store()
    assert list(store.latest_scores(SEASON, "2026-11-03")["asset_id"]) == ["celtics"]
    assert list(store.read_latest_stats(SEASON, "2026-11-03")["asset_id"]) == ["celtics"]

    # Once the league has started, its own rows are read as ever.
    _day(store, "union", "MLS", "2027-03-01", 1, 1.5)
    latest = store.latest_scores(SEASON, "2027-03-02").set_index("asset_id")
    assert latest.loc["union", "scaled_score"] == 1.5
    assert "_asset_league" not in latest.columns


def test_the_first_match_of_a_season_is_not_held_against_a_stale_row():
    from whul import ingest

    class Source:
        key, league = "mls", "MLS"

    store = _store()
    assert ingest._figures_the_day_before(store, Source(), SEASON,
                                          date(2027, 3, 1)) == {}

    _day(store, "union", "MLS", "2027-03-01", 1, 1.5)
    before = ingest._figures_the_day_before(store, Source(), SEASON, date(2027, 3, 8))
    assert before["union"]["matches_played"] == 1
