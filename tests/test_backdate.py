"""The days before a player's first stored score, rebuilt from his games."""

import json

import pandas as pd
import pytest

from whul import backdate, pipeline
from whul.store import benchmarks as bm
from whul.store import open_store

SEASON = "2026-27"


@pytest.fixture
def store():
    store = open_store(":memory:")
    history = pd.DataFrame([{"league": "MLB", "role": "Batter", "season": s,
                             "total_points": 1500} for s in (2023, 2024, 2025)])
    bm.freeze(store, bm.save(store, bm.compute(history, "Player", SEASON), SEASON,
                             version="v1"))
    store.upsert("assets", [{
        "asset_id": "bat", "asset_type": "Player", "display_name": "Bat",
        "league": "MLB", "role": "Batter", "norm_key": "MLB_Batter", "active": 1,
        "created_at": "2026-08-21"}], keys=("asset_id",))
    return store


def games(store, rows):
    store.upsert("game_scores", [{
        "season": SEASON, "asset_id": "bat", "game_key": str(i), "date": d,
        "role": "bat", "points": p, "score": p / 10, "opponent": "", "detail": "{}",
        "source": "MLB", "recorded_at": "x"} for i, (d, p) in enumerate(rows)],
        keys=("season", "asset_id", "game_key"))


def stored(store, day, score, advanced=0.0):
    pipeline.write_daily_scores(store, pd.DataFrame({
        "asset_id": ["bat"], "total_points": [score * 10], "scaled_score": [score]}),
        SEASON, day, "v1")
    store.upsert("raw_stats", [{
        "asset_id": "bat", "league": "MLB", "season": SEASON, "as_of": day,
        "source": "mlb", "phase": "regular",
        "stats": json.dumps({"advanced_share": advanced}), "fetched_at": day}],
        keys=("asset_id", "season", "as_of", "source", "phase"))


def day_scores(store):
    frame = store.query("SELECT as_of, scaled_score FROM daily_scores ORDER BY as_of")
    return dict(zip(frame["as_of"], frame["scaled_score"]))


def test_the_days_before_the_first_are_scaled_by_what_had_been_earned(store):
    """Twenty points by the 4th, stored as 40 on the 5th. On the 22nd he had
    the 21st's game behind him -- a quarter of his points."""
    games(store, [("2026-08-21", 5), ("2026-08-25", 15), ("2026-09-04", 0)])
    stored(store, "2026-09-05", 40.0)

    report = backdate.rebuild(store, SEASON, write=True)
    scores = day_scores(store)

    assert report.players == 1
    # The 21st's pull sees nothing yet: that night's game is not in it.
    assert scores["2026-08-21"] == pytest.approx(0.0)
    assert scores["2026-08-22"] == pytest.approx(10.0)
    assert scores["2026-08-26"] == pytest.approx(40.0)
    assert scores["2026-09-05"] == 40.0, "a stored day was rewritten"


def test_mlb_run_values_are_shared_by_games_not_points(store):
    """A fifth of his season is Offense and Defense, which was shared across
    his games -- so after one of two games he has half of that fifth."""
    games(store, [("2026-08-21", 10), ("2026-08-25", 30)])
    stored(store, "2026-09-05", 40.0, advanced=0.2)
    backdate.rebuild(store, SEASON, write=True)
    assert day_scores(store)["2026-08-22"] == pytest.approx(40 * (0.8 * 0.25 + 0.2 * 0.5))


def test_a_dry_run_writes_nothing(store):
    games(store, [("2026-08-21", 5)])
    stored(store, "2026-09-05", 40.0)
    report = backdate.rebuild(store, SEASON, write=False)
    assert report.written > 0
    assert list(day_scores(store)) == ["2026-09-05"]


def test_a_player_with_no_games_before_his_first_day_is_left_alone(store):
    games(store, [("2026-09-06", 5)])
    stored(store, "2026-09-05", 0.0)
    assert backdate.rebuild(store, SEASON, write=True).written == 0


def test_stored_days_are_checked_against_the_games(store):
    games(store, [("2026-09-05", 10), ("2026-09-07", 10)])
    stored(store, "2026-09-06", 5.0)
    stored(store, "2026-09-08", 10.0)
    report = backdate.rebuild(store, SEASON)
    # The 6th, rebuilt from the 8th: one of two games behind it, half of 10.
    assert report.checked == 1 and report.agreed == 1


def test_a_restated_league_has_its_stored_days_rebuilt_from_the_latest(store):
    """The stored MLB days came from totals that ran days behind: the 6th says
    5 when one of the two games was already played and the latest day says 40."""
    games(store, [("2026-09-05", 10), ("2026-09-07", 10)])
    stored(store, "2026-09-06", 5.0)
    stored(store, "2026-09-10", 40.0)
    store.conn.execute("UPDATE daily_scores SET held_score = 3.0 WHERE as_of = '2026-09-06'")
    store.conn.commit()

    backdate.rebuild(store, SEASON, write=True, restate=("MLB",))
    scores = day_scores(store)
    assert scores["2026-09-06"] == pytest.approx(20.0)
    assert scores["2026-09-08"] == pytest.approx(40.0)
    assert scores["2026-09-10"] == 40.0, "the anchor itself was rewritten"
    held = store.scalar("SELECT held_score FROM daily_scores WHERE as_of = '2026-09-06'")
    assert held == 3.0, "a held bonus on a stored day was wiped"


def test_an_unrestated_league_keeps_its_stored_days(store):
    games(store, [("2026-09-05", 10), ("2026-09-07", 10)])
    stored(store, "2026-09-06", 5.0)
    stored(store, "2026-09-10", 40.0)
    backdate.rebuild(store, SEASON, write=True)
    assert day_scores(store)["2026-09-06"] == 5.0


def test_a_restated_day_is_on_the_anchors_version_so_a_rescore_leaves_it(store):
    """A restated day is built from the latest one, on the latest one's scale.
    Left naming its old version, a rescore in the same run would move it a
    second time by the ratio of two benchmarks it is no longer on."""
    games(store, [("2026-09-05", 10), ("2026-09-07", 10)])
    stored(store, "2026-09-06", 5.0)
    stored(store, "2026-09-10", 40.0)
    history = pd.DataFrame([{"league": "MLB", "role": "Batter", "season": 2025,
                             "total_points": 1000}])
    bm.save(store, bm.compute(history, "Player", SEASON), SEASON, version="v0")
    store.conn.execute("UPDATE daily_scores SET benchmark_version = 'v0' "
                       "WHERE as_of = '2026-09-06'")
    store.conn.commit()
    backdate.rebuild(store, SEASON, write=True, restate=("MLB",))
    version = store.scalar("SELECT benchmark_version FROM daily_scores "
                           "WHERE as_of = '2026-09-06'")
    assert version == "v1"
