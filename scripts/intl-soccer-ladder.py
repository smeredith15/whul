#!/usr/bin/env python3
"""What the international soccer ladder does to real seasons.

The category is ten rostered national teams and no scorer. This is the
prototype for one: it applies the proposed ladder to the whole martj42 history
and prints what comes out, so the weights can be argued about against seasons
that happened rather than in the abstract. Nothing here writes to the database.

The scheme, in four steps:

  1. **A competition is its qualifying and its finals together**, at one of
     three rungs -- nations league, federation cup, world cup. World Cup
     qualifying is therefore worth more than Euro qualifying, which is right,
     and a team that fails to qualify has still spent its year on the World
     Cup rung, which stops "miss the World Cup, get your other points
     upscaled" from being a strategy.
  2. **A match is worth its result times its stage**: qualifying 1, group 2,
     knockout 3. Multiplied by 3 for a win, 2 for a shootout win, 1 for a draw
     or shootout loss, 0 for a loss -- the R script's own scale, kept.
  3. **A competition pays a ceiling, not a rate.** A team takes the share of
     it their results earned, measured against the champion's whole path:

         team points = ceiling x (its units / path_max units)

     so winning the Gold Cup (six matches) and winning AFCON (seven) are worth
     the same, and the 2026 World Cup's new Round of 32 changes nothing about
     what a World Cup is worth. This is the tennis tier model already in the
     codebase.
  4. **A season is its best competition plus half its second** -- the two-way
     rule the MLB scorer uses for a player who bats and pitches. Summing them
     instead put the 2018-19 United States at 500 against a 99th percentile of
     200: they won the World Cup and the championship that qualified them for
     it, and a benchmark nobody else can reach is a category decided by one
     season.
  5. **A fallow year can be scaled up** so the best rung actually in play that
     season reaches a full ceiling. Without it a European team's Nations League
     year -- which is all 2026-27 holds for England, France and Spain -- scores
     half what the same team's World Cup year does. Both are computed here.

Stage is inferred per edition rather than assumed. The R script took the first
three matches as the group stage, which is right for a four-team group and
wrong for the six-match Nations League phase, the five-team groups of the 2025
Copa América Femenina, and the nine-team CONMEBOL Women's Nations League that
has no knockout stage at all.

    python scripts/intl-soccer-ladder.py --data <dir>

Downloads the two ledgers to --data (default: a cache beside this script) and
reuses them, so re-running costs nothing.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whul.config.league import league_year
from whul.scoring.intl_soccer import RUNG, SCALE, per_team, score_teams
from whul.sources import intl_soccer as source

#: Two full four-year cycles. Five seasons -- what the other leagues use --
#: holds one World Cup and either one continental championship or two, so a
#: five-year pool changes character with the year it starts in.
BENCHMARK_SEASONS = 8

LEAGUES = {"M": "Men's Intl Soccer", "W": "Women's Intl Soccer"}

ROSTERED = {
    "M": ["England", "France", "Spain"],
    "W": ["Brazil", "Canada", "England", "France", "Germany", "Spain", "United States"],
}


def cached(directory: Path, refresh: bool = False) -> None:
    """Read the ledgers from disk after the first run.

    The adapter fetches them every time, which is right for a nightly job and
    wrong for a review script somebody runs twenty times in an afternoon.
    """
    directory.mkdir(parents=True, exist_ok=True)
    real = pd.read_csv

    def read(path, *args, **kwargs):
        if not (isinstance(path, str) and path.startswith("http")):
            return real(path, *args, **kwargs)
        gender = "W" if "womens" in path else "M"
        name = "results" if path.endswith("results.csv") else "shootouts"
        local = directory / f"{gender}-{name}.csv"
        if local.exists() and not refresh:
            return real(local, *args, **kwargs)
        frame = real(path, *args, **kwargs)
        frame.to_csv(local, index=False)
        return frame

    pd.read_csv = read


def normalized(table: pd.DataFrame, window: list[int]) -> pd.DataFrame:
    """Team-seasons on the 0-100 scale, against a benchmark computed as WHUL does.

    The buffer-pool truncation is the whole point of routing through
    `whul.normalize` rather than taking a percentile here: it keeps only the
    top of each season before pooling, so the benchmark is the best of a
    draftable field. A flat percentile over every nation that played a
    competitive match puts the bar around a team that lost in qualifying, and
    every good season then scores three figures.
    """
    from whul.normalize import buffer_pool, compute_benchmarks

    live = table[table["season"].isin(window)].copy()
    bench = compute_benchmarks(live, "Team", season_col="season")
    pooled = buffer_pool(live, "Team", season_col="season")
    keys = set(zip(pooled["league"], pooled["team"], pooled["season"]))

    out = live.merge(
        bench[["norm_key", "benchmark"]].rename(columns={"norm_key": "league"}),
        on="league", how="left",
    )
    out["scaled"] = out["total_points"] / out["benchmark"] * 100.0
    out["in_pool"] = [
        (lg, tm, yr) in keys
        for lg, tm, yr in zip(out["league"], out["team"], out["season"])
    ]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(Path(__file__).parent / ".intl-cache"))
    parser.add_argument("--refresh", action="store_true", help="re-download the ledgers")
    parser.add_argument("--from-year", type=int, default=2015)
    args = parser.parse_args()

    print(__doc__.split("The scheme, in")[0].strip())
    print(f"\n{'=' * 74}\nLoading\n")
    cached(Path(args.data), args.refresh)
    matches = source.load_matches()
    print(f"  {len(matches):,} scoring matches, seasons "
          f"{int(matches['season'].min())} to {int(matches['season'].max())}")

    moved = matches[matches["season"] != matches["date"].map(league_year)]
    recent = moved[moved["season"] >= args.from_year]
    print(f"\n  Block tournaments held whole in the year they began: {len(recent)} "
          f"match(es) since {args.from_year} score in a league year their date "
          f"alone would not have put them in.")
    for key, block in recent.groupby(["gender", "competition", "season"]):
        print(f"    {key[0]}  {key[1]} -> {int(key[2])}  ({len(block)} matches)")

    table = score_teams(matches)
    window = sorted(table["season"].unique())[-BENCHMARK_SEASONS:]
    print(f"\n{'=' * 74}\nThe benchmark, through the real machinery\n")
    print(f"    `whul.normalize.compute_benchmarks`, not a flat percentile: the")
    print(f"    pool is truncated to the top of each season before the 99th")
    print(f"    percentile is taken, so 100 means the best of the draftable")
    print(f"    field rather than of every nation that played a match.")
    print(f"    Window: {window[0]}-{window[-1]}, two full four-year cycles.\n")

    scored = normalized(table, window)
    for league in LEAGUES.values():
        block = scored[scored["league"] == league]
        pool = block[block["in_pool"]]
        if pool.empty:
            continue
        over = [(t, int((pool["scaled"] > t).sum())) for t in (100, 125, 150, 200)]
        print(f"    {league:<22} benchmark {block['benchmark'].iloc[0]:6.1f} from "
              f"{len(pool)} pooled seasons; best raw "
              f"{pool['total_points'].max():6.1f} = {pool['scaled'].max():5.1f}")
        print(f"      seasons over: " + ",  ".join(f"{t} -> {n}" for t, n in over))

    print(f"\n{'=' * 74}\nNormalized scores -- every team, not only the roster\n")
    for league in LEAGUES.values():
        block = scored[(scored["league"] == league) & scored["in_pool"]]
        print(f"  --- {league}: the 12 highest seasons in the pool")
        for row in block.nlargest(12, "scaled").itertuples():
            print(f"      {int(row.season)}  {row.team:<28} "
                  f"{row.total_points:7.1f} raw  {row.scaled:6.1f}  "
                  f"({row.competitions} competition(s), {row.matches} matches)")
        share = block["scaled"]
        print(f"      median {share.median():5.1f}   75th {share.quantile(.75):5.1f}   "
              f"90th {share.quantile(.90):5.1f}   max {share.max():5.1f}\n")

    print(f"{'=' * 74}\nThe rostered teams, normalized\n")
    for gender, names in ROSTERED.items():
        block = scored[(scored["league"] == LEAGUES[gender]) & scored["team"].isin(names)]
        wide = block.pivot_table(index="team", columns="season", values="scaled")
        print(f"  {LEAGUES[gender]}")
        print("    " + wide.round(0).fillna(0).astype(int).to_string().replace(
            "\n", "\n    "))
        print()

    print(f"{'=' * 74}\nHow the fold and the lift land\n")
    live = table[table["season"].isin(window)]
    counts = live["competitions"].value_counts().sort_index()
    for entered, n in counts.items():
        note = "  <- all but the best are halved" if entered >= 2 else ""
        print(f"    {entered} competition(s) in a season: {n:>5} team-seasons{note}")
    lifts = live["lift"].value_counts().sort_index()
    for lift, n in lifts.items():
        print(f"    lifted x{lift:.2f}: {n:>5} team-seasons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
