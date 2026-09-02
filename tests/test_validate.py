"""Validation report tests, driven by synthetic data (no network)."""

import pandas as pd
import pytest

from whul.validate import LeagueSpec, acquire, benchmarks, leaders, scrape_readiness


def fake_raw(seasons, players_per_season=80, weeks=18):
    rows = []
    for season in seasons:
        for p in range(players_per_season):
            for w in range(1, weeks + 1):
                rows.append({
                    "season": season, "week": w, "player_id": f"p{p}",
                    "points": 100 - p,
                })
    return pd.DataFrame(rows)


def fake_score(raw, postseason):
    agg = raw.groupby(["season", "player_id"], as_index=False).agg(
        regular_points=("points", "sum"), regular_games=("week", "nunique")
    )
    agg["postseason_points"] = 0.0
    agg["postseason_games"] = 0.0
    agg["postseason_bonus"] = 0.0
    agg["total_points"] = agg["regular_points"]
    agg["games_played"] = agg["regular_games"]
    agg["player"] = agg["player_id"]
    agg["league"] = "NFL"
    agg["role"] = ["QB" if int(p[1:]) % 2 else "TE" for p in agg["player_id"]]
    return agg


SPEC = LeagueSpec(
    name="Fake", load=lambda s: fake_raw(s), score=fake_score,
    id_col="player_id", week_col="week", source="synthetic",
    daily_cost=lambda: 0.5,
)


def test_acquire_reports_every_season(capsys):
    raw, stats = acquire(SPEC, [2021, 2022])
    out = capsys.readouterr().out
    assert stats["missing"] == []
    assert "all 2 requested seasons present" in out
    assert len(raw) > 0


def test_acquire_flags_missing_seasons(capsys):
    spec = LeagueSpec(
        name="Gappy", load=lambda s: fake_raw([2021]), score=fake_score,
        id_col="player_id", week_col="week", source="synthetic",
    )
    _, stats = acquire(spec, [2021, 2022])
    assert stats["missing"] == [2022]
    assert "MISSING SEASONS" in capsys.readouterr().out


def test_benchmarks_pool_across_seasons(capsys):
    raw, _ = acquire(SPEC, [2021, 2022, 2023])
    scored, bench = benchmarks(SPEC, raw)
    assert set(bench["norm_key"]) == {"NFL_QB", "NFL_TE"}
    assert (bench["n_seasons"] == 3).all(), "every season should contribute"


def test_leaders_reports_requested_ranks(capsys):
    raw, _ = acquire(SPEC, [2021, 2022])
    scored, bench = benchmarks(SPEC, raw)
    out = leaders(SPEC, scored, bench, 2022)
    assert set(out["rank"]) == {1, 10}
    assert set(out["group"]) == {"NFL_QB", "NFL_TE"}
    for col in ("raw_excl", "norm_excl", "raw_incl", "norm_incl"):
        assert col in out.columns


def test_leaders_handles_a_missing_target_season(capsys):
    raw, _ = acquire(SPEC, [2021])
    scored, bench = benchmarks(SPEC, raw)
    assert leaders(SPEC, scored, bench, 2099).empty
    assert "Cannot report leaders" in capsys.readouterr().out


def test_scrape_readiness_passes_on_weekly_data(capsys):
    raw, stats = acquire(SPEC, [2021])
    assert scrape_readiness(SPEC, raw, [2021], stats) is True
    assert "READY" in capsys.readouterr().out


def test_scrape_readiness_fails_without_per_period_rows(capsys):
    """A source giving only season totals cannot back a daily job."""
    raw, stats = acquire(SPEC, [2021])
    raw = raw.assign(week=1)
    assert scrape_readiness(SPEC, raw, [2021], stats) is False
    assert "NOT READY" in capsys.readouterr().out


def test_readiness_judges_nightly_cost_not_backfill_cost(capsys):
    """A slow one-time backfill must not fail a source that stays current cheaply.

    ESPN takes ~27 minutes to backfill an NBA season but seconds to fetch one
    day, which is all the nightly job does.
    """
    slow_backfill = LeagueSpec(
        name="SlowBackfill", load=lambda s: fake_raw(s), score=fake_score,
        id_col="player_id", week_col="week", source="synthetic",
        daily_cost=lambda: 3.0,
    )
    raw, stats = acquire(slow_backfill, [2021])
    stats["elapsed"] = 1613.2  # as measured against the real NBA season
    assert scrape_readiness(slow_backfill, raw, [2021], stats) is True
    out = capsys.readouterr().out
    assert "~3.0s per day" in out
    assert "backfill cost, paid once" in out


def test_expensive_nightly_update_fails(capsys):
    costly = LeagueSpec(
        name="Costly", load=lambda s: fake_raw(s), score=fake_score,
        id_col="player_id", week_col="week", source="synthetic",
        daily_cost=lambda: 600.0,
    )
    raw, stats = acquire(costly, [2021])
    assert scrape_readiness(costly, raw, [2021], stats) is False


def test_unmeasurable_nightly_cost_fails_loudly(capsys):
    def boom():
        raise RuntimeError("endpoint down")

    spec = LeagueSpec(
        name="Broken", load=lambda s: fake_raw(s), score=fake_score,
        id_col="player_id", week_col="week", source="synthetic",
        daily_cost=boom,
    )
    raw, stats = acquire(spec, [2021])
    assert scrape_readiness(spec, raw, [2021], stats) is False
    assert "measurement failed" in capsys.readouterr().out
