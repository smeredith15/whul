"""Command-line harness for exercising a league end to end.

Fetches a season, scores it, optionally normalizes it, and prints the top
results -- the quickest way to sanity-check a league's data source and formula::

    python -m whul.cli list
    python -m whul.cli score nfl --season 2024
    python -m whul.cli score nba --season 2023 --assets teams
    python -m whul.cli score nfl --season 2024 --normalize --top 25
    python -m whul.cli score nfl --season 2024 --csv out.csv
    python -m whul.cli weekly nfl --season 2024
    python -m whul.cli weekly nfl --season 2024 --week 5 --player "Josh Allen"
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from whul.normalize import apply_benchmarks, compute_benchmarks


def _nfl(season: int, assets: str) -> pd.DataFrame:
    from whul.scoring import nfl
    from whul.sources import nflverse

    if assets == "players":
        return nfl.score_players(nflverse.load_player_stats([season]))
    return nfl.score_teams(nflverse.load_schedules([season]), nflverse.load_teams([season]))


def _nba(season: int, assets: str) -> pd.DataFrame:
    from whul.scoring import nba
    from whul.sources import hoopr

    if assets == "players":
        return nba.score_players(hoopr.load_player_box([season]))
    return nba.score_teams(hoopr.load_schedule([season]))


LEAGUES = {
    "nfl": {
        "fn": _nfl,
        "assets": ("players", "teams"),
        "seasons": "1999-present",
        "source": "nflverse (live)",
    },
    "nba": {
        "fn": _nba,
        "assets": ("players", "teams"),
        "seasons": "2002-2023",
        "source": "hoopR-data (ARCHIVED, historical only)",
    },
}

DISPLAY = {
    "players": ["player", "role", "games_played", "total_points", "scaled_score"],
    "teams": ["team", "reg_wins", "total_points", "scaled_score"],
}


def _nfl_weekly(season: int) -> pd.DataFrame:
    """Per-player, per-week half-PPR points -- the granularity daily scoring needs."""
    from whul.scoring.nfl import PLAYER_WEIGHTS, SCORING_POSITIONS
    from whul.sources import nflverse

    raw = nflverse.load_player_stats([season])
    cols = {
        "passing_yards": "passing_yards", "passing_tds": "passing_tds",
        "interceptions": "passing_interceptions", "rushing_yards": "rushing_yards",
        "rushing_tds": "rushing_tds", "receptions": "receptions",
        "receiving_yards": "receiving_yards", "receiving_tds": "receiving_tds",
    }
    out = pd.DataFrame({
        "season": raw["season"], "week": raw["week"],
        "season_type": raw.get("season_type", "REG"),
        "player": raw["player_display_name"], "position": raw["position"],
        "team": raw.get("recent_team", raw.get("team")),
    })
    pts = 0.0
    for stat, weight in PLAYER_WEIGHTS.items():
        if stat == "fumbles_lost":
            col = sum(
                pd.to_numeric(raw[c], errors="coerce").fillna(0)
                for c in ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost")
                if c in raw.columns
            )
        else:
            src = cols.get(stat, stat)
            src = src if src in raw.columns else stat
            col = pd.to_numeric(raw.get(src, 0), errors="coerce").fillna(0)
        pts = pts + col * weight
    out["points"] = pts.round(2)
    return out[out["position"].isin(SCORING_POSITIONS)].reset_index(drop=True)


WEEKLY = {"nfl": _nfl_weekly}


def cmd_weekly(args: argparse.Namespace) -> int:
    """Show week-by-week scoring, proving the feed supports incremental updates."""
    if args.league not in WEEKLY:
        print(f"no weekly view for {args.league} yet", file=sys.stderr)
        return 2

    print(f"Fetching {args.league} weekly data for {args.season} ...", file=sys.stderr)
    df = WEEKLY[args.league](args.season)
    if df.empty:
        print("No rows returned.", file=sys.stderr)
        return 1

    if args.player:
        sel = df[df["player"].str.contains(args.player, case=False, na=False)]
        if sel.empty:
            print(f"No player matching {args.player!r}", file=sys.stderr)
            return 1
        sel = sel.sort_values(["season_type", "week"], ascending=[False, True])
        print(f"\nWeek-by-week for {sel.iloc[0]['player']} ({args.season}):\n")
        print(sel[["week", "season_type", "team", "points"]].to_string(index=False))
        print(f"\nregular-season total: {sel.loc[sel.season_type == 'REG', 'points'].sum():.2f}")
        return 0

    if args.week:
        sel = df[df["week"] == args.week].nlargest(args.top, "points")
        print(f"\nTop {len(sel)} scorers, {args.season} week {args.week}:\n")
        print(sel[["player", "position", "team", "points"]].to_string(index=False))
        return 0

    per_week = df.groupby(["season_type", "week"], as_index=False).agg(
        players=("player", "nunique"), total_points=("points", "sum")
    )
    print(f"\nCoverage by week for {args.season}:\n")
    print(per_week.to_string(index=False))
    print(f"\n{len(per_week)} distinct weeks, {df['player'].nunique()} players.")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    print(f"{'league':<8}{'assets':<18}{'seasons':<16}source")
    print("-" * 70)
    for name, cfg in LEAGUES.items():
        print(f"{name:<8}{', '.join(cfg['assets']):<18}{cfg['seasons']:<16}{cfg['source']}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    cfg = LEAGUES[args.league]
    if args.assets not in cfg["assets"]:
        print(f"{args.league} has no '{args.assets}'; try: {', '.join(cfg['assets'])}", file=sys.stderr)
        return 2

    print(f"Fetching {args.league} {args.assets} for {args.season} ...", file=sys.stderr)
    scored = cfg["fn"](args.season, args.assets)
    if scored.empty:
        print("No rows scored.", file=sys.stderr)
        return 1

    asset_type = "Player" if args.assets == "players" else "Team"
    if args.normalize:
        benchmarks = compute_benchmarks(scored, asset_type, managers=args.managers)
        try:
            scored = apply_benchmarks(scored, benchmarks, asset_type)
        except ValueError as exc:
            print(f"\nCannot normalize: {exc}", file=sys.stderr)
            return 1
        print(f"\nBenchmarks (99th percentile, {args.managers} benchmark managers):", file=sys.stderr)
        print(benchmarks[["norm_key", "benchmark", "n_in_pool"]].to_string(index=False), file=sys.stderr)

    cols = [c for c in DISPLAY[args.assets] if c in scored.columns]
    top = scored.nlargest(args.top, "total_points")
    print(f"\nTop {min(args.top, len(top))} of {len(scored)} scored {args.assets}:\n")
    print(top[cols].to_string(index=False))

    if args.csv:
        scored.to_csv(args.csv, index=False)
        print(f"\nWrote {len(scored)} rows to {args.csv}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whul", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show supported leagues").set_defaults(func=cmd_list)

    score = sub.add_parser("score", help="score one league season")
    score.add_argument("league", choices=sorted(LEAGUES))
    score.add_argument("--season", type=int, required=True)
    score.add_argument("--assets", choices=["players", "teams"], default="players")
    score.add_argument("--top", type=int, default=15)
    score.add_argument("--normalize", action="store_true", help="apply the 0-100 scale")
    score.add_argument("--managers", type=int, default=15, help="benchmark manager count")
    score.add_argument("--csv", help="write all scored rows here")
    score.set_defaults(func=cmd_score)

    weekly = sub.add_parser("weekly", help="week-by-week view (incremental-update check)")
    weekly.add_argument("league", choices=sorted(LEAGUES))
    weekly.add_argument("--season", type=int, required=True)
    weekly.add_argument("--week", type=int, help="show top scorers for one week")
    weekly.add_argument("--player", help="show one player's week-by-week line")
    weekly.add_argument("--top", type=int, default=15)
    weekly.set_defaults(func=cmd_weekly)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
