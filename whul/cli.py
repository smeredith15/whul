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
    python -m whul.cli validate nfl
    python -m whul.cli validate nba --seasons 2022-2026 --target 2026
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


#: Fantasy category -> ESPN league key, for the results-only NCAA leagues.
NCAA_LEAGUES = {
    "ncaaf": "NCAAF",
    "ncaam": "NCAAM",
    "ncaaw": "NCAAW",
    "ncaabaseball": "NCAA Baseball",
    "ncaasoftball": "NCAA Softball",
}


def _ncaa(key: str):
    def load(season: int, assets: str) -> pd.DataFrame:
        from whul.scoring.ncaa import SCORERS
        from whul.sources import espn

        results = espn.load_team_results(key, [season])
        eligible = espn.load_eligible_teams(key)
        return SCORERS[NCAA_LEAGUES[key]](results, eligible)

    return load


def _mlb(season: int, assets: str) -> pd.DataFrame:
    from whul.scoring import mlb
    from whul.sources import mlb as source

    if assets == "players":
        # Per-role rows; combine_two_way runs after normalization.
        return mlb.score_players(source.load_batters([season]), source.load_pitchers([season]))
    # The contract engine pairs consecutive seasons, so a team score needs both.
    return mlb.score_teams(source.load_schedule([season, season + 1]))


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
        "source": "nflverse `stats_player` release (live)",
    },
    "mlb": {
        "fn": _mlb,
        "assets": ("players", "teams"),
        "seasons": "2000-present",
        "source": "MLB Stats API + FanGraphs (UNVERIFIED)",
    },
    **{
        key: {
            "fn": _ncaa(key),
            "assets": ("teams",),
            "seasons": "2003-present",
            "source": "ESPN scoreboard, results only (UNVERIFIED)",
        }
        for key in NCAA_LEAGUES
    },
    "nba": {
        "fn": _nba,
        "assets": ("players", "teams"),
        "seasons": "2002-2023 via hoopR; 2024+ needs ESPN",
        "source": "ESPN site API (UNVERIFIED) / hoopR-data (archived at 2023)",
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


def _timed(fn) -> float:
    """Seconds one call takes, for the nightly-cost check."""
    import time

    started = time.monotonic()
    fn()
    return time.monotonic() - started


def _spec(league: str):
    from whul.validate import LeagueSpec

    if league == "nfl":
        from whul.scoring import nfl
        from whul.sources import nflverse

        return LeagueSpec(
            name="NFL",
            load=lambda seasons: nflverse.load_player_stats(seasons),
            score=lambda raw, post: nfl.score_players(raw, postseason=post),
            id_col="player_id",
            week_col="week",
            source="nflverse-data release `stats_player` (parquet per season)",
            # The nightly job re-reads the current season's file; there is no
            # smaller unit, so one season IS the incremental update.
            daily_cost=lambda: _timed(lambda: nflverse.load_player_stats([2025])),
        )
    if league in NCAA_LEAGUES:
        from whul.scoring.ncaa import SCORERS
        from whul.sources import espn

        category = NCAA_LEAGUES[league]
        return LeagueSpec(
            name=category,
            load=lambda seasons: espn.load_team_results(league, seasons),
            # Teams only -- there is no postseason player bonus to apply.
            score=lambda raw, post: SCORERS[category](
                raw, espn.load_eligible_teams(league)
            ).assign(
                regular_points=lambda d: d["total_points"],
                regular_games=lambda d: d["games_played"],
                postseason_points=0.0,
                postseason_games=0.0,
                postseason_bonus=0.0,
                player=lambda d: d["team"],
            ),
            id_col="game_id",
            week_col="game_date",
            source=f"ESPN scoreboard, results only ({league})",
            daily_cost=lambda: espn.daily_results_cost(league),
        )
    if league == "mlb":
        from whul.scoring import mlb
        from whul.sources import mlb as source

        def load(seasons):
            batters = source.load_batters(seasons)
            pitchers = source.load_pitchers(seasons)
            batters["_phase"] = "bat"
            pitchers["_phase"] = "pit"
            return pd.concat([batters, pitchers], ignore_index=True)

        def score(raw, postseason):
            batters = raw[raw["_phase"] == "bat"]
            pitchers = raw[raw["_phase"] == "pit"]
            scored = mlb.score_players(batters, pitchers)
            # MLB leaderboards are season aggregates, so there is no separate
            # postseason phase to bonus here -- the contract weighting is what
            # handles the split.
            scored["regular_points"] = scored["total_points"]
            scored["regular_games"] = scored.get("games", 0)
            scored["postseason_points"] = 0.0
            scored["postseason_games"] = 0.0
            scored["postseason_bonus"] = 0.0
            return scored

        return LeagueSpec(
            name="MLB",
            load=load,
            score=score,
            id_col="player",
            week_col="season",
            source="MLB Stats API (schedule) + FanGraphs (leaderboards)",
            daily_cost=source.daily_update_cost,
            post_normalize=mlb.combine_two_way,
        )
    if league == "nba":
        from whul.scoring import nba
        from whul.sources import espn

        return LeagueSpec(
            name="NBA",
            load=lambda seasons: espn.load_nba_player_box(seasons),
            score=lambda raw, post: nba.score_players(raw, postseason=post),
            id_col="athlete_id",
            week_col="game_date",
            source="ESPN site API (scoreboard + boxscore, per date)",
            # ESPN is queried per date, so the nightly job is one date -- a tiny
            # fraction of the backfill cost.
            daily_cost=lambda: espn.daily_update_cost("nba"),
        )
    raise KeyError(league)


DEFAULT_VALIDATE = {
    "mlb": ((2021, 2025), 2025),
    **{key: ((2021, 2025), 2025) for key in NCAA_LEAGUES},
    "nfl": ((2021, 2025), 2025),
    "nba": ((2022, 2026), 2026),
}


def cmd_probe_ncaa_api(args: argparse.Namespace) -> int:
    """The NCAA stats API states division in the URL, which ESPN will not do."""
    from datetime import date as _d

    from whul.sources import ncaa_api

    day = _d.fromisoformat(args.date) if args.date else None
    result = ncaa_api.probe(args.league, day)
    print(f"\nNCAA API probe -- {result['league']} on {result['date']}\n")
    for key, value in result.items():
        if key in ("league", "date"):
            continue
        print(f"  {key:<20} {value}")
    if any(isinstance(v, str) and v.startswith("FAILED") for v in result.values()):
        print("\nCould not reach the NCAA API. Send me this output.", file=sys.stderr)
        return 1
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Ask the API what works, instead of guessing which path or group id is right."""
    from datetime import date as _d

    from whul.sources import espn

    if args.league not in NCAA_LEAGUES and args.league != "nba":
        print(f"discover is for ESPN-backed leagues; {args.league} uses another source",
              file=sys.stderr)
        return 2

    day = _d.fromisoformat(args.date) if args.date else None
    result = espn.discover(args.league, day)
    print(f"\nESPN discovery -- {result['league']} on {result['date']}\n")
    print("  candidate sport/league paths:")
    for line in result.get("paths", []):
        print(f"    {line}")
    print("\n  candidate division group ids:")
    for line in result.get("group_ids", []):
        print(f"    {line}")
    print("\n  scoreboard events by parameter combination:")
    for line in result.get("scoreboard_by_params", []):
        print(f"    {line}")
    if result.get("sample_teams"):
        print(f"\n  sample teams: {', '.join(result['sample_teams'])}")
    if result.get("conference_ids"):
        print("\n  conference ids seen (id: appearances):")
        print("    " + ", ".join(f"{cid}:{n}" for cid, n in result["conference_ids"]))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Cheap reachability + schema check, before committing to a full pull."""
    if args.league == "nfl":
        from whul.sources import nflverse

        try:
            df = nflverse.load_player_stats([2025])
        except Exception as exc:
            print(f"FAILED to reach nflverse: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"nflverse reachable: {len(df):,} rows for 2025, {len(df.columns)} columns")
        print(f"season types: {df['season_type'].value_counts().to_dict()}")
        return 0

    if args.league in NCAA_LEAGUES:
        from datetime import date as _d

        from whul.sources import espn

        day = _d.fromisoformat(args.date) if args.date else None
        result = espn.probe_results(args.league, day)
        print(f"\nESPN probe -- {result['league']} on {result['date']}\n")
        for key, value in result.items():
            if key in ("league", "date"):
                continue
            print(f"  {key:<22} {value}")
        if any(isinstance(v, str) and v.startswith("FAILED") for v in result.values()):
            print("\nCould not reach or parse ESPN. Send me this output.", file=sys.stderr)
            return 1
        print("\nESPN reachable and the scoreboard schema parses.")
        return 0

    if args.league == "mlb":
        from whul.sources import mlb as source

        result = source.probe()
        print(f"\nMLB probe -- season {result['season']}\n")
        for key, value in result.items():
            if key == "season":
                continue
            print(f"  {key:<20} {value}")
        failed = any(isinstance(v, str) and v.startswith("FAILED") for v in result.values())
        if failed:
            print("\nA feed could not be reached or parsed. Send me this output.", file=sys.stderr)
            return 1
        print("\nBoth MLB feeds reachable and parsing.")
        return 0

    from datetime import date as _date

    from whul.sources import espn

    day = _date.fromisoformat(args.date) if args.date else None
    result = espn.probe(args.league, day)
    print(f"\nESPN probe -- {result['league']} on {result['date']}\n")
    for key, value in result.items():
        if key in ("league", "date"):
            continue
        print(f"  {key:<16} {value}")
    failed = any(isinstance(v, str) and v.startswith("FAILED") for v in result.values())
    if failed:
        print("\nThe adapter could not reach or parse ESPN. Send me this output.", file=sys.stderr)
        return 1
    print("\nESPN reachable and the boxscore schema parses.")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Full acquisition + benchmark + leaders + scrape-readiness report."""
    from whul.validate import run

    default_span, default_target = DEFAULT_VALIDATE[args.league]
    if args.seasons:
        first, _, last = args.seasons.partition("-")
        span = (int(first), int(last or first))
    else:
        span = default_span
    seasons = list(range(span[0], span[1] + 1))
    target = args.target or default_target

    try:
        spec = _spec(args.league)
    except KeyError:
        print(f"no validation spec for {args.league}", file=sys.stderr)
        return 2

    try:
        return run(spec, seasons, target)
    except Exception as exc:  # surfaced deliberately: this command exists to diagnose
        print(
            f"\nVALIDATION FAILED: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        print(
            "\nIf this is a network error, the host may be blocked by your egress "
            "policy. See the troubleshooting section of the league's testing guide "
            "in docs/.",
            file=sys.stderr,
        )
        return 1


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
    rows = [
        (name, ", ".join(cfg["assets"]), cfg["seasons"], cfg["source"])
        for name, cfg in LEAGUES.items()
    ]
    headers = ("league", "assets", "seasons", "source")
    # Size each column to its content so a long value cannot run into the next.
    widths = [max(len(r[i]) for r in (*rows, headers)) for i in range(len(headers))]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("-" * len(line))
    for row in rows:
        print("  ".join(value.ljust(w) for value, w in zip(row, widths)))
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

    discover = sub.add_parser(
        "discover", help="report what candidate paths and group ids actually return"
    )
    discover.add_argument("league", choices=sorted(LEAGUES))
    discover.add_argument("--date", help="YYYY-MM-DD, in season for that sport")
    discover.set_defaults(func=cmd_discover)

    ncaa_api = sub.add_parser(
        "probe-ncaa-api", help="check the NCAA stats API as an alternative to ESPN"
    )
    ncaa_api.add_argument("league", choices=sorted(NCAA_LEAGUES))
    ncaa_api.add_argument("--date", help="YYYY-MM-DD, in season for that sport")
    ncaa_api.set_defaults(func=cmd_probe_ncaa_api)

    probe = sub.add_parser("probe", help="check a source is reachable and its schema intact")
    probe.add_argument("league", choices=sorted(LEAGUES))
    probe.add_argument("--date", help="YYYY-MM-DD to probe (default: yesterday)")
    probe.set_defaults(func=cmd_probe)

    validate = sub.add_parser("validate", help="full data-source validation report")
    validate.add_argument("league", choices=sorted(LEAGUES))
    validate.add_argument("--seasons", help="range like 2021-2025 (default: last 5)")
    validate.add_argument("--target", type=int, help="season to report leaders for")
    validate.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
