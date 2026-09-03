"""Benchmark computation and normalization to the 0-100 scale.

Ports the engine in ``All_Analysis.R``:

1. Map league -> draft pool, and assign a normalization group.
2. Rank within each *normalization group* and truncate to the buffer pool.
3. Take the 99th percentile of each group as its benchmark.
4. ``scaled = total / benchmark * 100``.

Truncation is per normalization group, not per draft pool. Each position is
measured against its own historical distribution: a tight end against tight ends,
a pitcher against pitchers, a backcourt player against the backcourt.
``All_Analysis.R`` ranked within ``Draft_Pool`` before grouping by ``Norm_Key``,
which let a low-scoring position be squeezed out of the pool entirely and left
unscoreable -- at 5 benchmark managers the top-22 NFL pool contains no tight end.

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

    A ``norm_league`` column overrides the league for grouping purposes, which is
    how sports that share a benchmark across tours or series -- ATP with WTA,
    NASCAR with Formula 1 -- get one distribution instead of two pools each
    sized for the whole category.
    """
    if "norm_league" in df.columns:
        basis = df["norm_league"].astype("string").fillna(df["league"].astype("string"))
        basis = basis.mask(basis.str.strip() == "", df["league"].astype("string"))
    else:
        basis = df["league"].astype("string")

    if asset_type == "Team":
        return basis.astype(str)

    league = basis.astype(str)
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
    season_col: str | None = None,
) -> pd.DataFrame:
    """Truncate each normalization group to its fantasy-relevant buffer pool.

    The benchmark is deliberately computed *after* this truncation, so 100 means
    the 99th percentile of draftable assets rather than of every professional.

    With ``season_col``, truncation happens within each season separately and the
    survivors are pooled. Over five seasons that yields five times the sample at
    the same relevance cutoff -- a far more stable percentile than one season
    gives, without letting one exceptional year crowd out the others.
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
        return out.assign(
            norm_key=pd.Series(dtype="object"),
            buffer_n=pd.Series(dtype="int64"),
            pool_rank=pd.Series(dtype="int64"),
        )

    out["norm_key"] = assign_norm_key(out, asset_type)
    out["buffer_n"] = out["draft_pool"].map(lambda p: round(rates[p] * managers * mult)).astype(int)
    out = out.sort_values("total_points", ascending=False)
    # Rank within the normalization group so every position keeps its own pool.
    rank_by = ([season_col] if season_col else []) + ["norm_key"]
    out["pool_rank"] = out.groupby(rank_by).cumcount() + 1
    return out[out["pool_rank"] <= out["buffer_n"]].reset_index(drop=True)


def compute_benchmarks(
    df: pd.DataFrame,
    asset_type: str,
    managers: int = BENCHMARK_MANAGER_COUNT,
    season_col: str | None = None,
) -> pd.DataFrame:
    """Frozen 99th-percentile benchmark per normalization group.

    Expects columns ``league``, ``total_points`` and (for players) ``role``. Pass
    ``season_col`` when ``df`` spans several seasons, so each season is truncated
    to its own top-N before the percentile is taken across the pooled result.
    """
    pool = buffer_pool(df, asset_type, managers, season_col=season_col)
    # pandas' linear interpolation matches R's default quantile type 7.
    bench = (
        pool.groupby("norm_key")["total_points"]
        .quantile(BENCHMARK_QUANTILE)
        .rename("benchmark")
        .reset_index()
    )
    bench["asset_type"] = asset_type
    bench["n_in_pool"] = pool.groupby("norm_key").size().reindex(bench["norm_key"]).to_numpy()
    if season_col:
        bench["n_seasons"] = (
            pool.groupby("norm_key")[season_col].nunique().reindex(bench["norm_key"]).to_numpy()
        )
    return bench


def scale(total_points: pd.Series, benchmark: pd.Series) -> pd.Series:
    """Normalized 0-100 score. Values above 100 are expected and meaningful --
    they mark performances beyond the 99th percentile of the draftable pool."""
    return (total_points / benchmark * 100).round(2)


def apply_benchmarks(
    df: pd.DataFrame,
    benchmarks: pd.DataFrame,
    asset_type: str,
    strict: bool = True,
) -> pd.DataFrame:
    """Attach ``norm_key``, ``benchmark`` and ``scaled_score`` to scored assets.

    Raises if any normalization group has no benchmark. Since truncation is now
    per normalization group, this cannot happen for a group with any members at
    all, so it signals a genuine configuration problem.

    Pass ``strict=False`` to allow NaN scores instead, for exploratory use only.
    """
    out = df.copy()
    out["norm_key"] = assign_norm_key(out, asset_type)
    out = out.merge(
        benchmarks.loc[benchmarks["asset_type"] == asset_type, ["norm_key", "benchmark"]],
        on="norm_key",
        how="left",
    )
    missing = out.loc[out["benchmark"].isna(), "norm_key"]
    if strict and not missing.empty:
        groups = sorted(set(missing.astype(str)))
        raise ValueError(
            f"No benchmark for {len(missing)} {asset_type.lower()}s in groups {groups}. "
            "The buffer pool excluded these groups entirely -- raise the benchmark "
            "manager count or widen the pool."
        )
    out["scaled_score"] = scale(out["total_points"], out["benchmark"])
    return out
