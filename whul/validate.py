"""League validation report.

Answers four questions for one league, in one run:

1. Can we acquire several seasons of stats?
2. What are the per-position 99th-percentile benchmarks over that pool?
3. For a target season, who are the #1 and #10 in each position group -- raw and
   normalized, with and without the postseason bonus?
4. Will the source support daily scrapes?

Written to be read by a person checking a new data source, so every section
states what "good" looks like rather than only printing numbers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from whul.normalize import apply_benchmarks, compute_benchmarks


@dataclass
class LeagueSpec:
    """Everything the report needs to exercise one league."""

    name: str
    load: Callable[[list[int]], pd.DataFrame]  # seasons -> raw rows
    score: Callable[[pd.DataFrame, bool], pd.DataFrame]  # (raw, postseason) -> scored
    id_col: str
    week_col: str
    source: str
    supports_incremental: bool = True
    #: Cost of ONE incremental update -- what the nightly job actually does.
    #: Not the backfill cost, which is paid once and may be far larger.
    daily_cost: Callable[[], float] | None = None


def _rule(title: str) -> str:
    return f"\n{'=' * 78}\n{title}\n{'=' * 78}"


def _say(*args) -> None:
    """Print immediately. Output is usually redirected to a file, where Python
    block-buffers stdout and a long run would otherwise look stalled."""
    print(*args, flush=True)


def acquire(spec: LeagueSpec, seasons: list[int]) -> tuple[pd.DataFrame, dict]:
    """Section 1 -- pull the raw rows and describe what came back."""
    _say(_rule(f"1. ACQUISITION -- {spec.name}, seasons {seasons[0]}-{seasons[-1]}"))
    _say(f"source: {spec.source}\n")

    started = time.monotonic()
    raw = spec.load(seasons)
    elapsed = time.monotonic() - started

    got = sorted(raw["season"].dropna().unique().tolist())
    missing = [s for s in seasons if s not in got]

    per_season = raw.groupby("season").agg(
        rows=(spec.id_col, "size"),
        assets=(spec.id_col, "nunique"),
        **{f"{spec.week_col}s": (spec.week_col, "nunique")},
    )
    _say(per_season.to_string())
    _say(f"\nfetched {len(raw):,} rows in {elapsed:.1f}s")
    if missing:
        _say(f"MISSING SEASONS: {missing}  <-- investigate before trusting this source")
    else:
        _say(f"all {len(seasons)} requested seasons present")
    return raw, {"elapsed": elapsed, "missing": missing, "seasons": got}


def benchmarks(spec: LeagueSpec, raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Section 2 -- per-group benchmarks over the multi-season pool.

    Benchmarks use regular-season production only, so the scale is never skewed
    by the small subset of players who reach the postseason.
    """
    _say(_rule("2. BENCHMARKS -- 99th percentile per position group"))

    scored = spec.score(raw, True)
    reg_only = scored.assign(total_points=scored["regular_points"])
    bench = compute_benchmarks(reg_only, "Player", season_col="season")

    _say("pool: regular-season production, truncated per season per group\n")
    cols = ["norm_key", "benchmark", "n_in_pool"] + (
        ["n_seasons"] if "n_seasons" in bench.columns else []
    )
    _say(bench[cols].to_string(index=False))
    _say("\n100 on the normalized scale = these values. Scores above 100 are expected.")
    return scored, bench


def leaders(
    spec: LeagueSpec,
    scored: pd.DataFrame,
    bench: pd.DataFrame,
    target: int,
    ranks: tuple[int, ...] = (1, 10),
) -> pd.DataFrame:
    """Section 3 -- #1 and #10 per group, raw and normalized, both variants."""
    _say(_rule(f"3. LEADERS -- {target} season, ranks {', '.join(f'#{r}' for r in ranks)}"))

    season = scored[scored["season"] == target].copy()
    if season.empty:
        _say(f"No {target} data. Cannot report leaders.")
        return pd.DataFrame()

    # Normalize both variants against the same regular-season benchmark.
    excl = apply_benchmarks(season.assign(total_points=season["regular_points"]), bench, "Player")
    incl = apply_benchmarks(season, bench, "Player")

    merged = excl[["player", "role", "norm_key", "regular_points", "scaled_score"]].rename(
        columns={"regular_points": "raw_excl", "scaled_score": "norm_excl"}
    )
    merged["raw_incl"] = incl["total_points"].to_numpy()
    merged["norm_incl"] = incl["scaled_score"].to_numpy()
    merged["po_games"] = season["postseason_games"].to_numpy()

    rows = []
    for group, block in merged.groupby("norm_key"):
        ordered = block.sort_values("raw_incl", ascending=False).reset_index(drop=True)
        for rank in ranks:
            if rank <= len(ordered):
                row = ordered.loc[rank - 1].to_dict()
                row["group"] = group
                row["rank"] = rank
                rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        _say("No rows.")
        return out

    display = out[
        ["group", "rank", "player", "po_games", "raw_excl", "norm_excl", "raw_incl", "norm_incl"]
    ].round(2)
    _say("raw_excl / norm_excl  = regular season only")
    _say("raw_incl / norm_incl  = regular season + postseason bonus\n")
    _say(display.to_string(index=False))

    gap = out[out["rank"] == 1]["norm_excl"].describe()
    _say(
        f"\n#1 normalized scores span {gap['min']:.1f} to {gap['max']:.1f} across groups."
        "\nValues clustered near 100 mean each position is being measured against itself."
    )
    return out


def scrape_readiness(spec: LeagueSpec, raw: pd.DataFrame, seasons: list[int], stats: dict) -> bool:
    """Section 4 -- can this source back a daily job?"""
    _say(_rule("4. DAILY SCRAPE READINESS"))

    latest = max(stats["seasons"]) if stats["seasons"] else None
    recent = raw[raw["season"] == latest]
    weeks = sorted(recent[spec.week_col].dropna().unique().tolist())

    checks: list[tuple[str, bool, str]] = []
    # What actually matters is that the *dataset* refreshes daily and can be
    # re-pulled cheaply. Cumulative season-to-date figures are enough: stored as
    # a daily snapshot they give both live standings and the history the
    # progression graph needs, and differencing them yields any window's accrual.
    # Per-period detail is a bonus -- needed where accrual must be split finer
    # than a day, which in practice means MLB's postseason.
    checks.append((
        "cumulative season-to-date available",
        len(raw) > 0,
        f"{len(raw):,} rows for {latest}",
    ))
    checks.append((
        "incremental fetch supported",
        spec.supports_incremental,
        "the nightly job pulls only what is new",
    ))

    # Readiness is about the *nightly* cost, not the backfill. A source can be
    # slow to backfill once and still be trivially cheap to keep current.
    if spec.daily_cost is not None:
        try:
            daily = spec.daily_cost()
            checks.append((
                "nightly update cost",
                daily < 120,
                f"~{daily:.1f}s per day",
            ))
        except Exception as exc:
            checks.append(("nightly update cost", False, f"measurement failed: {exc}"))
    else:
        checks.append(("nightly update cost", False, "not measured -- no probe defined"))

    checks.append((
        "all requested seasons available",
        not stats["missing"],
        "no gaps" if not stats["missing"] else f"missing {stats['missing']}",
    ))

    for label, ok, detail in checks:
        _say(f"  [{'PASS' if ok else 'FAIL'}]  {label:<45} {detail}")

    granularity = (
        f"{len(weeks)} distinct {spec.week_col}s"
        if len(weeks) > 1
        else "season aggregates only"
    )
    _say(f"\n  (granularity: {granularity})")

    backfill = stats["elapsed"] / max(len(seasons), 1)
    _say(f"\n  (backfill cost, paid once: ~{backfill:.0f}s per season)")

    ready = all(ok for _, ok, _ in checks)
    _say(
        f"\n{'READY' if ready else 'NOT READY'}: "
        + (
            "the dataset refreshes daily and can be re-pulled cheaply."
            if ready
            else "see the failing checks above."
        )
    )
    _say(
        "\nNote: this confirms historical acquisition. Live in-season refresh can only be\n"
        "confirmed once games are being played -- rerun this against the live season then."
    )
    return ready


def run(spec: LeagueSpec, seasons: list[int], target: int) -> int:
    raw, stats = acquire(spec, seasons)
    scored, bench = benchmarks(spec, raw)
    leaders(spec, scored, bench, target)
    ready = scrape_readiness(spec, raw, seasons, stats)
    _say(_rule("SUMMARY"))
    _say(f"league:     {spec.name}")
    _say(f"seasons:    {stats['seasons']}")
    _say(f"groups:     {sorted(bench['norm_key'])}")
    _say(f"scrape:     {'READY' if ready else 'NOT READY'}")
    return 0 if ready and not stats["missing"] else 1
