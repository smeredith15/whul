"""Benchmark computation and normalization to the 0-100 scale.

Ports the engine in ``All_Analysis.R``:

1. Map league -> draft pool.
2. Rank the pool by total points and truncate to the buffer pool.
3. Within each normalization group, take the 99th percentile as the benchmark.
4. ``scaled = total / benchmark * 100``.

Benchmarks are frozen before the season and reused unchanged for its duration, so
standings never silently rewrite themselves. ``compute_benchmarks`` is therefore
expected to run once per season, against data no later than the season's
``benchmark_cutoff``.
"""

from __future__ import annotations

import pandas as pd

from whul.config.league import (
    BENCHMARK_MANAGER_COUNT,
    BENCHMARK_QUANTILE,
    PLAYER_BUFFER_MULTIPLIER,
    POOL_MAP_PLAYERS,
    POOL_MAP_TEAMS,
    TEAM_BUFFER_MULTIPLIER,
    ALL_SLOTS,
)


def _rate_lookup(asset_type: str) -> dict[str, int]:
    return {s.category: s.benchmark_rate for s in ALL_SLOTS if s.asset_type == asset_type}


def assign_norm_key(df: pd.DataFrame, asset_type: str) -> pd.Series:
    """Normalization group for each row.

    Players are split by position where the league's positions have materially
    different scoring distributions; everything else normalizes league-wide.
    """
    if asset_type == "Team":
        return df["league"].astype(str)

    league = df["league"].astype(str)
    role = df.get("role", pd.Series("", index=df.index)).astype(str)

    key = league.copy()
    basketball = league.isin(["NBA", "WNBA"])
    guards = role.str.contains(r"\b(?:G|PG|SG|Guards)\b", case=False, regex=True, na=False)
    bigs = role.str.contains(r"\b(?:F|SF|PF|C|Forwards|Centers)\b", case=False, regex=True, na=False)
    key = key.mask(basketball & guards, league + "_Backcourt")
    key = key.mask(basketball & bigs & ~guards, league + "_Frontcourt")

    positional = league.isin(["MLB", "NFL"])
    key = key.mask(positional, league + "_" + role)
    return key


def buffer_pool(
    df: pd.DataFrame,
    asset_type: str,
    managers: int = BENCHMARK_MANAGER_COUNT,
) -> pd.DataFrame:
    """Truncate each draft pool to its fantasy-relevant buffer pool.

    The benchmark is deliberately computed *after* this truncation, so 100 means
    the 99th percentile of draftable assets rather than of every professional.
    """
    rates = _rate_lookup(asset_type)
    mult = TEAM_BUFFER_MULTIPLIER if asset_type == "Team" else PLAYER_BUFFER_MULTIPLIER
    pool_map = POOL_MAP_TEAMS if asset_type == "Team" else POOL_MAP_PLAYERS

    out = df.copy()
    out["draft_pool"] = out["league"].map(pool_map)

    # Unmapped leagues are an error, not a silent drop: a typo'd or newly added
    # league name must not quietly vanish from the standings.
    unmapped = sorted(set(out.loc[out["draft_pool"].isna(), "league"].astype(str)))
    if unmapped:
        raise ValueError(f"No draft pool mapped for {asset_type} leagues: {unmapped}")

    unknown = sorted(set(out["draft_pool"]) - set(rates))
    if unknown:
        raise ValueError(f"No benchmark rate configured for {asset_type} pools: {unknown}")

    if out.empty:
        return out.assign(buffer_n=pd.Series(dtype="int64"), pool_rank=pd.Series(dtype="int64"))

    out["buffer_n"] = out["draft_pool"].map(lambda p: round(rates[p] * managers * mult)).astype(int)
    out = out.sort_values("total_points", ascending=False)
    out["pool_rank"] = out.groupby("draft_pool").cumcount() + 1
    return out[out["pool_rank"] <= out["buffer_n"]].reset_index(drop=True)


def compute_benchmarks(
    df: pd.DataFrame,
    asset_type: str,
    managers: int = BENCHMARK_MANAGER_COUNT,
) -> pd.DataFrame:
    """Frozen 99th-percentile benchmark per normalization group.

    Expects columns ``league``, ``total_points`` and (for players) ``role``.
    """
    pool = buffer_pool(df, asset_type, managers)
    pool["norm_key"] = assign_norm_key(pool, asset_type)
    # pandas' linear interpolation matches R's default quantile type 7.
    bench = (
        pool.groupby("norm_key")["total_points"]
        .quantile(BENCHMARK_QUANTILE)
        .rename("benchmark")
        .reset_index()
    )
    bench["asset_type"] = asset_type
    bench["n_in_pool"] = pool.groupby("norm_key").size().reindex(bench["norm_key"]).to_numpy()
    return bench


def scale(total_points: pd.Series, benchmark: pd.Series) -> pd.Series:
    """Normalized 0-100 score. Values above 100 are expected and meaningful --
    they mark performances beyond the 99th percentile of the draftable pool."""
    return (total_points / benchmark * 100).round(2)


def apply_benchmarks(
    df: pd.DataFrame,
    benchmarks: pd.DataFrame,
    asset_type: str,
) -> pd.DataFrame:
    """Attach ``norm_key``, ``benchmark`` and ``scaled_score`` to scored assets."""
    out = df.copy()
    out["norm_key"] = assign_norm_key(out, asset_type)
    out = out.merge(
        benchmarks.loc[benchmarks["asset_type"] == asset_type, ["norm_key", "benchmark"]],
        on="norm_key",
        how="left",
    )
    out["scaled_score"] = scale(out["total_points"], out["benchmark"])
    return out
