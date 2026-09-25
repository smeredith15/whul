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

from whul.benchmarks import DEFAULT_SEASONS
from whul.normalize import apply_benchmarks, compute_benchmarks
from whul.store.db import missing_database_note


def _nfl(season: int, assets: str) -> pd.DataFrame:
    from whul.scoring import nfl
    from whul.sources import nflverse

    if assets == "players":
        return nfl.score_players(nflverse.load_player_stats([season]))
    return nfl.score_teams(nflverse.load_schedules([season]), nflverse.load_teams([season]))


#: Fantasy category -> ESPN league key, for the results-only NCAA leagues.
#: Probeable but not scored in their own right: a club's cup and European
#: matches are gathered into its league total rather than standing alone.
#: What a bare ``--into`` means: whichever version for this season is still
#: being built. Not a valid version id, so it cannot collide with one.
LATEST_DRAFT = "\0latest"


PROBE_ONLY_COMPETITIONS = (
    "ucl", "uel", "uecl", "facup", "efl_cup",
    "copadelrey", "dfbpokal", "coppaitalia", "coupedefrance",
    # Neither is pulled: the US Open Cup path is unverified and the CONCACAF
    # Champions Cup one has never answered, which is why MLS clubs are paid for
    # qualifying for it and not for playing in it. Probeable so that "has never
    # answered" stays a fact somebody checked rather than one this repeats.
    "usopencup", "concacafchampions",
)

#: Club soccer competitions the app scores directly.
SOCCER_LEAGUES = {
    "epl": "Premier League", "laliga": "La Liga", "seriea": "Serie A",
    "bundesliga": "Bundesliga", "ligue1": "Ligue 1", "mls": "MLS", "nwsl": "NWSL",
}

NCAA_LEAGUES = {
    "ncaaf": "NCAAF",
    "ncaam": "NCAAM",
    "ncaaw": "NCAAW",
    "ncaabaseball": "NCAA Baseball",
    "ncaasoftball": "NCAA Softball",
}


def _soccer(key: str):
    """Club soccer reads every competition its clubs play, not just the league."""

    def load(season: int, assets: str) -> pd.DataFrame:
        from whul.scoring import soccer
        from whul.sources import espn

        matches = espn.load_soccer_matches(key, [season])
        if matches.empty:
            return matches
        matches["league"] = SOCCER_LEAGUES[key]
        return soccer.score_teams(matches)

    return load


def _ncaa(key: str):
    """NCAA leagues read from the NCAA stats API rather than ESPN.

    ESPN cannot express division membership -- its teams endpoint returns all 760
    college football programs whatever group filter is passed -- while the NCAA
    API states the division in the URL. Every team in its results therefore
    belongs to the division by construction.
    """

    def load(season: int, assets: str) -> pd.DataFrame:
        from whul.scoring.ncaa import SCORERS
        from whul.sources import ncaa_api

        results = ncaa_api.load_team_results(key, [season])
        eligible = set(results["home_team"]) | set(results["away_team"]) if not results.empty else None
        return SCORERS[NCAA_LEAGUES[key]](results, eligible)

    return load


def _nhl(season: int, assets: str) -> pd.DataFrame:
    from whul.scoring import nhl
    from whul.sources import nhl as source

    if assets == "players":
        return nhl.score_players(source.load_skaters([season]))
    return nhl.score_teams(
        source.load_teams([season], source.GAME_TYPE_REGULAR),
        source.load_teams([season], source.GAME_TYPE_PLAYOFFS),
        divisions=source.load_divisions([season]),
    )


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


def _pga(season: int, assets: str) -> pd.DataFrame:
    from whul.scoring import golf
    from whul.sources import espn_individual

    return golf.score_players(espn_individual.load_results("pga", [season]))


def _motorsports(season: int, assets: str) -> pd.DataFrame:
    """NASCAR and Formula 1 together -- one roster category, one benchmark."""
    from whul.scoring import motorsport
    from whul.sources import espn_individual, jolpica

    return motorsport.score_players(
        espn_individual.load_results("nascar", [season]),
        jolpica.load_results([season]),
    )


def _tennis(season: int, assets: str) -> pd.DataFrame:
    """Sackmann for a completed season, the live feed for the current one.

    The snapshot is the record -- and the only surviving copy of it, since the
    Sackmann repository was removed. The Flashscore window only reaches back a
    fortnight, so it can answer for the season in progress and nothing else.
    """
    from datetime import date

    from whul.scoring import tennis
    from whul.sources import flashscore, snapshot

    if season < date.today().year:
        # The snapshot resolves categories through the calendar itself.
        return tennis.score_players(snapshot.load_matches([season]))
    return tennis.score_players(flashscore.load_matches())


#: Sports read one event at a time rather than one date at a time. Their probes
#: return a nested report keyed by stage, so they render differently.
INDIVIDUAL_LEAGUES = ("pga", "nascar", "f1", "tennis", "snapshot", "schedule")


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
            "source": "NCAA stats API, results only (division-filtered)",
        }
        for key in NCAA_LEAGUES
    },
    "nhl": {
        "fn": _nhl,
        "assets": ("players", "teams"),
        "seasons": "2009-present",
        "source": "NHL stats API (UNVERIFIED); 84 games from 2026-27",
    },
    **{
        key: {
            "fn": _soccer(key),
            "assets": ("teams",),
            "seasons": "2001-present",
            "source": "ESPN scoreboard, league + cups + Europe (UNVERIFIED)",
        }
        for key in SOCCER_LEAGUES
    },
    "pga": {
        "fn": _pga,
        "assets": ("players",),
        "seasons": "2015-present",
        "source": "ESPN golf leaderboard (UNVERIFIED)",
    },
    "motorsports": {
        "fn": _motorsports,
        "assets": ("players",),
        "seasons": "2015-present",
        "source": "ESPN racing (UNVERIFIED) + Jolpica/Ergast for F1 (UNVERIFIED)",
    },
    "tennis": {
        "fn": _tennis,
        "assets": ("players",),
        "seasons": "1990-present",
        "source": "Phase7B snapshot for history + Flashscore feed live",
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
        from whul.sources import ncaa_api

        category = NCAA_LEAGUES[league]

        def _eligible(raw):
            if raw is None or raw.empty:
                return None
            return set(raw["home_team"]) | set(raw["away_team"])

        return LeagueSpec(
            name=category,
            load=lambda seasons: ncaa_api.load_team_results(league, seasons),
            # Teams only -- there is no postseason player bonus to apply.
            score=lambda raw, post: SCORERS[category](raw, _eligible(raw)).assign(
                regular_points=lambda d: d["total_points"],
                regular_games=lambda d: d["games_played"],
                postseason_points=0.0,
                postseason_games=0.0,
                postseason_bonus=0.0,
                player=lambda d: d["team"],
            ),
            id_col="game_id",
            week_col="game_date",
            source=f"NCAA stats API, results only ({league})",
            daily_cost=lambda: ncaa_api.daily_update_cost(league),
        )
    if league == "nhl":
        from whul.scoring import nhl
        from whul.sources import nhl as source

        # The standings, for how many games each skater's club has played. The
        # benchmark path has always fetched them; this one never did, so the
        # figure was blank and "50 played" could not say whether the rest were
        # missed or not yet played -- which is the only reason it is a heading.
        held: dict[str, pd.DataFrame] = {}

        def load(seasons):
            regular = source.load_skaters(seasons, source.GAME_TYPE_REGULAR)
            playoffs = source.load_skaters(seasons, source.GAME_TYPE_PLAYOFFS)
            regular["_phase"] = "reg"
            if not playoffs.empty:
                playoffs["_phase"] = "post"
            try:
                held["standings"] = source.load_divisions(seasons)
            except Exception:  # noqa: BLE001 -- a heading must not stop scoring
                held["standings"] = pd.DataFrame()
            return pd.concat([regular, playoffs], ignore_index=True)

        #: What a skater is scored on, and so what his playoffs must also show.
        #: Games are deliberately absent: `split_phases` already reports them
        #: per phase, and a second column counting the same thing would be one
        #: more figure to keep in step.
        counted = ["goals", "assists", "shots", "plus_minus"]

        def score(raw, postseason):
            from whul.scoring.postseason import (
                POSTSEASON, REGULAR, RULES, apply_bonus, phase_totals,
                regular_totals, split_phases,
            )

            scored = nhl.score_skaters(raw, held.get("standings"))
            # From the scored frame, not reindexed off `raw`: the scorer drops
            # skaters who earned nothing and renumbers, so matching by position
            # afterwards mislabelled the phase of every row after the first
            # such skater.
            phase = scored["_phase"] if "_phase" in scored.columns else None
            scored["phase"] = (
                phase.map({"reg": REGULAR, "post": POSTSEASON}).fillna(REGULAR)
                if phase is not None
                else REGULAR
            )
            keys = ["season", "player"]
            phases = split_phases(
                scored, keys, "total_points", "games_played", scored["phase"]
            )
            # April's figures, kept apart and labelled as such. The playoff
            # request has always been made -- `gameTypeId=3`, its own call --
            # and the rows were reduced to points and games one line later,
            # leaving the profile's playoff boxes with nothing to hold.
            counting = regular_totals(scored, keys, counted, scored["phase"])
            post_counting = phase_totals(
                scored, keys, counted, scored["phase"], POSTSEASON,
                prefix="post_")
            # Carried through the groupby rather than left behind by it. It
            # is a fact about his club, identical on all his rows, so the
            # largest is the same as any of them -- but a column the aggregate
            # never mentions is a column the page reads as unknown.
            clubs = (
                scored.groupby(keys, as_index=False)["team_games"].max()
                if "team_games" in scored.columns else None
            )
            out = phases.merge(counting, on=keys, how="left").merge(
                post_counting, on=keys, how="left")
            if clubs is not None:
                out = out.merge(clubs, on=keys, how="left")
            out = apply_bonus(out, RULES["NHL"] if postseason else None)
            for column in counted + [f"post_{c}" for c in counted]:
                if column in out.columns:
                    out[column] = out[column].fillna(0)
            out["league"] = "NHL"
            out["role"] = nhl.SKATER_ROLE
            return out

        return LeagueSpec(
            name="NHL",
            load=load,
            score=score,
            id_col="player",
            week_col="season",
            source="NHL stats API (skater summaries, regular and playoffs)",
            daily_cost=source.daily_update_cost,
            scale_benchmarks_for="NHL",
        )
    if league == "mlb":
        from whul.scoring import mlb
        from whul.sources import mlb as source

        def load(seasons):
            batters = source.load_batters(seasons)
            pitchers = source.load_pitchers(seasons)
            batters["_phase"] = "bat"
            pitchers["_phase"] = "pit"
            frames = [batters, pitchers]
            # October, asked for separately and checked before it is used --
            # the endpoint ignores a gameType it does not understand and
            # answers with the whole year. See `_check_postseason_applied`.
            post_batters = source.load_batters(seasons, postseason=True)
            post_pitchers = source.load_pitchers(seasons, postseason=True)
            for frame, role in ((post_batters, "bat"), (post_pitchers, "pit")):
                if frame is not None and not frame.empty:
                    frame["_phase"] = role
                    frame["_season_phase"] = "post"
                    frames.append(frame)
            return pd.concat(frames, ignore_index=True)

        def score(raw, postseason):
            from whul.scoring.postseason import RULES, apply_bonus

            phase = (raw["_season_phase"] if "_season_phase" in raw.columns
                     else pd.Series("reg", index=raw.index)).fillna("reg")

            def scored_for(want):
                part = raw[phase == want]
                if part.empty:
                    return None
                return mlb.score_players(part[part["_phase"] == "bat"],
                                         part[part["_phase"] == "pit"])

            scored = scored_for("reg")
            if scored is None or scored.empty:
                scored = mlb.score_players(raw.iloc[0:0], raw.iloc[0:0])
            scored = mlb.with_october(scored, scored_for("post"))
            return apply_bonus(scored, RULES["MLB"] if postseason else None)

        return LeagueSpec(
            name="MLB",
            load=load,
            score=score,
            id_col="player",
            week_col="season",
            source="MLB Stats API (schedule, season and postseason lines)",
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
    # 2021 was a 56-game COVID season; the window starts after it.
    "nhl": ((2022, 2026), 2026),
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


def _print_stages(title: str, report: dict) -> int:
    """Render a staged probe report.

    The individual sports probe in stages -- reach the season, read one event,
    parse the field -- and report where they stopped, so a failure names the
    stage and what it saw rather than only an exception.
    """
    print(f"\n{title}\n")
    for key, value in report.items():
        if key == "stages":
            continue
        print(f"  {key:<12} {value}")

    stages = report.get("stages", {})
    failed = False
    for name, detail in stages.items():
        ok = detail.get("ok")
        mark = "ok  " if ok else "FAIL"
        failed = failed or not ok
        print(f"\n  [{mark}] {name}")
        for key, value in detail.items():
            if key == "ok":
                continue
            # A list one item per line. These carry the samples a probe exists
            # to show -- the headers it dropped, the records under them -- and
            # a dozen dicts printed on one line is a report nobody can read.
            if isinstance(value, (list, tuple)):
                print(f"        {key}:")
                for item in value:
                    print(f"            {item}")
                continue
            print(f"        {key:<18} {value}")

    if failed or not stages:
        print("\nThe adapter stopped at the stage marked FAIL. Send me this output.")
        return 1
    print("\nAll stages passed.")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    """Build a placeholder league to develop the app against."""
    from datetime import date as _date

    from whul import simulate
    from whul.store import open_store

    store = open_store(args.db)
    if args.purge:
        removed = simulate.purge(store)
        print(f"\nPurged {simulate.SIM_SEASON}\n")
        for table, count in removed.items():
            if count:
                print(f"  {table:<22} {count:,} rows")
        return 0

    end = _date.fromisoformat(args.end) if args.end else None
    summary = simulate.generate(
        store, seed=args.seed, end=end, verbose=False,
        from_season=getattr(args, "from_season", None),
    )
    print(f"\nSimulated league -- season {summary['season']}\n")
    for key in ("managers", "slots", "assets", "days", "trades"):
        print(f"  {key:<12} {summary[key]:,}")
    print(f"  {'benchmarks':<12} {summary['benchmark_version']}")

    from whul import pipeline

    table = pipeline.progression(store, summary["season"])
    if not table.empty:
        latest = table[table["as_of"] == table["as_of"].max()]
        print(f"\n  standings on {latest.iloc[0]['as_of']}\n")
        from whul.config.league import manager_name

        for row in latest.itertuples():
            label = f"{manager_name(row.manager_id)} ({row.manager_id})"
            print(f"    {row.rank}. {label:<18} {row.total:>10,.2f}")
    for warning in summary["warnings"]:
        print(f"  ! {warning}")
    print(f"\nWritten to {args.db}. Remove it with `simulate --purge`.")
    return 0


#: The most of the asset table one run may remove. A spreadsheet that half
#: parsed, or a season nobody has imported yet, would leave almost every asset
#: looking unrostered -- and the difference between "twenty-four duplicates"
#: and "everything" is exactly the difference between a tidy-up and a disaster.
PRUNE_CEILING = 0.25


def cmd_prune_assets(args: argparse.Namespace) -> int:
    """Remove assets the spreadsheet no longer holds, and their history.

    An asset reaches the database from the spreadsheet and stays there after
    the spreadsheet stops naming it -- a name corrected, a category moved, a
    player traded away. Twelve tennis players sat under `Tennis` after the
    roster moved them to `ATP` and `WTA`; they scored until the correction
    landed and then went quiet, invisible to the site and still in the table.

    The occupancy *is* the spreadsheet: `import-rosters` writes a slot for
    every row it reads, so an asset occupying no current slot is one the sheet
    does not name. That is why this runs after the import and refuses to run
    before one.
    """
    from whul.store import open_store

    store = open_store(args.db)
    held = set(store.query(
        "SELECT DISTINCT o.asset_id FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "WHERE r.season = ?", (args.season,),
    )["asset_id"])
    if not held:
        print(f"\nNothing is rostered in {args.season}, so every asset would "
              f"look unheld. Run `import-rosters --write` first.",
              file=sys.stderr)
        print(missing_database_note(store), file=sys.stderr)
        return 1

    assets = store.query("SELECT asset_id, display_name, league, asset_type FROM assets")
    stale = assets[~assets["asset_id"].isin(held)]
    if stale.empty:
        print(f"\n  Every asset in the table is on a {args.season} roster. "
              f"Nothing to prune.\n")
        return 0

    share = len(stale) / max(len(assets), 1)
    print(f"\n  {len(stale)} of {len(assets)} asset(s) are on no {args.season} "
          f"roster slot ({share:.0%}):\n")
    for row in stale.sort_values(["league", "display_name"]).itertuples():
        counts = store.query(
            "SELECT (SELECT COUNT(*) FROM daily_scores WHERE asset_id = ?) AS scores, "
            "(SELECT COUNT(*) FROM raw_stats WHERE asset_id = ?) AS stats",
            (row.asset_id, row.asset_id),
        ).iloc[0]
        trail = f"{int(counts.scores)} scored day(s), {int(counts.stats)} stat row(s)"
        print(f"    {row.league:<22} {row.display_name:<30} {trail}")

    if share > PRUNE_CEILING:
        print(f"\n  Refusing: that is more than {PRUNE_CEILING:.0%} of the table, "
              f"which is what a half-read spreadsheet looks like rather than a "
              f"tidy-up. Check the import before forcing it.\n", file=sys.stderr)
        return 1
    if not args.write:
        print(f"\n  Nothing removed. Re-run with --write to remove them and "
              f"every score, stat and alias that names them.\n")
        return 0

    removed = store.prune_assets(list(stale["asset_id"]))
    print(f"\n  Removed {len(stale)} asset(s): "
          + ", ".join(f"{n} from {table}" for table, n in sorted(removed.items()))
          + "\n")
    return 0


def cmd_import_rosters(args: argparse.Namespace) -> int:
    """Read the draft spreadsheet. Reports the column mapping before writing."""
    from pathlib import Path as _Path

    from whul.roster_import import run
    from whul.store import open_store

    store = open_store(args.db)
    try:
        report = run(
            store, args.season,
            path=_Path(args.path) if args.path else None,
            sheet=args.sheet, dry_run=not args.write,
        )
    except (FileNotFoundError, ImportError) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    print(f"\n{report}\n")
    if report.problems:
        print("Nothing was written. Fix the rows above, or rename the columns "
              "so they are recognised.\n", file=sys.stderr)
        return 1
    if not args.write:
        print("Looks right? Re-run with --write.\n")
    else:
        print("Next: freeze benchmarks, then "
              "`python -m whul.cli rollup --backfill && python -m whul.cli site`\n")
    return 0


def cmd_import_bids(args: argparse.Namespace) -> int:
    """Read the auction's bid logs -- the losing bids as well as the winning.

    One file a round, with the round in the filename. The roster already knows
    what everything cost; this is the only record of what anybody else was
    willing to pay, and without it a price cannot be read.
    """
    from whul.draft_bids import run
    from whul.store import open_store

    store = open_store(args.db)
    try:
        report = run(store, args.season, args.paths, dry_run=not args.write)
    except (FileNotFoundError, ImportError) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    print(f"\n{report}\n")
    if report.problems:
        print("Nothing was written.\n", file=sys.stderr)
        return 1
    if not args.write:
        print("Looks right? Re-run with --write.\n")
    else:
        print("Next: `python -m whul.cli site`\n")
    return 0


def cmd_admin(args: argparse.Namespace) -> int:
    """Serve the local admin page.

    Local because the published site is files: files cannot accept a trade, and
    the controls that change the league should not be on the same public page
    the league reads.
    """
    from whul.admin import serve
    from whul.store import open_store

    store = open_store(args.db)
    serve(store, args.season, port=args.port, open_browser=not args.no_browser)
    return 0


#: How many falls to name before the count stands in for the rest.
FALLS_SHOWN = 30


def _falls(store, season: str, only: str = "") -> tuple[list, list]:
    """Every day a score fell, split into what the sport did and what did not.

    ``only`` narrows it to falls landing on that one day, which is what a
    restatement needs: the day it starts on is the one that has an older day
    in front of it, and that join is where a correction leaves a cliff.
    """
    import json
    from collections import defaultdict

    from whul.store import monotonic

    scores = store.query(
        "SELECT d.asset_id, d.as_of, d.league_points, a.display_name, a.league "
        "FROM daily_scores d JOIN assets a ON a.asset_id = d.asset_id "
        "WHERE d.season = ? ORDER BY d.asset_id, d.as_of",
        (season,),
    )
    if scores.empty:
        return [], []
    figures: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in store.query(
            "SELECT asset_id, as_of, stats FROM raw_stats WHERE season = ?",
            (season,)).itertuples():
        try:
            figures[str(row.asset_id)][str(row.as_of)] = json.loads(row.stats)
        except (TypeError, ValueError):
            continue

    days: dict[str, list] = defaultdict(list)
    for row in scores.itertuples():
        days[str(row.asset_id)].append(row)

    unexplained, explained = [], []
    for asset_id, ordered in days.items():
        for before, now in zip(ordered, ordered[1:]):
            if only and str(now.as_of) != only:
                continue
            fall = float(before.league_points or 0) - float(now.league_points or 0)
            if fall <= monotonic.TOLERANCE:
                continue
            fell = monotonic.what_went_backwards(
                figures[asset_id].get(str(before.as_of), {}),
                figures[asset_id].get(str(now.as_of), {}),
            )
            entry = (str(now.as_of), str(now.league), str(now.display_name),
                     fall, fell)
            (unexplained if fell else explained).append(entry)
    return explained, unexplained


def _earliest_stored_day(store, season: str) -> str:
    got = store.query(
        "SELECT MIN(as_of) AS first FROM daily_scores WHERE season = ?", (season,))
    return "" if got.empty else str(got["first"].iloc[0] or "")


def cmd_check_falls(args: argparse.Namespace) -> int:
    """Every day an asset's score went down, and whether anything explains it.

    A score that falls is not by itself wrong: a club that loses by three has a
    worse point differential, a batter who goes 0-for-4 has four more at-bats
    priced at minus one apiece. What is always wrong is a count falling -- a
    win un-won, a match un-played, a title un-awarded -- and that is what this
    separates out.

    It reads the stored days rather than re-pulling anything, so it can be run
    against any database at any time, and it exits non-zero when anything is
    unexplained. That is the check the promise rests on: not that nobody has
    noticed a negative lately, but that the ledger can be asked.
    """
    from whul.store import monotonic, open_store

    store = open_store(args.db)
    explained, unexplained = _falls(store, args.season)
    if not explained and not unexplained and not _earliest_stored_day(
            store, args.season):
        print(f"\nNothing stored for {args.season}.\n")
        return 0

    print(f"\n{args.season}:")
    print(f"  {len(explained) + len(unexplained)} day(s) where a score fell.")
    print(f"  {len(explained)} explained by a measure -- a differential, a "
          f"rate, a WAR: the sport happening.")
    print(f"  {len(unexplained)} where a count went backwards, which cannot "
          f"happen.\n")

    if args.show_all and explained:
        print("  Explained:")
        for day, league, who, fall, _ in sorted(explained, key=lambda e: -e[3]):
            print(f"    {day}  {league:10s} {who[:30]:30s} -{fall:8,.2f}")
        print()
    if not unexplained:
        print("  Nothing unexplained.\n")
        return 0
    print("  Unexplained -- each of these is a bug:")
    for day, league, who, fall, fell in sorted(unexplained,
                                               key=lambda e: -e[3])[:FALLS_SHOWN]:
        print(f"    {day}  {league:10s} {who[:26]:26s} -{fall:8,.2f}   "
              f"{monotonic.explain(fell)}")
    if len(unexplained) > FALLS_SHOWN:
        print(f"    ... and {len(unexplained) - FALLS_SHOWN} more")
    _say_how_to_clear_them(store, args.season, unexplained)
    return 1


def _say_how_to_clear_them(store, season: str, unexplained: list) -> None:
    """The command that removes them, with the date worked out.

    A fall between two days means the earlier one holds a figure the later one
    says was never true, so it is the earlier one that is wrong -- and
    restating only from the day the fall shows up moves the cliff back a day
    rather than removing it. That was done once, with `--since` set to the day
    of the drop, and the drop simply reappeared on the day before.

    So the date is the first day stored, not the first day that looks wrong.
    """
    first = _earliest_stored_day(store, season)
    if not first:
        return
    earliest = min(day for day, *_ in unexplained)
    print(f"\n  The earliest of these lands on {earliest}, which means the day "
          f"before it\n  holds a figure that day says was never true -- so it is "
          f"the earlier day\n  that is wrong, and restating from {earliest} "
          f"would move the cliff rather\n  than remove it. Restate the whole "
          f"stored history:\n")
    print(f"      python -m whul.cli ingest <leagues> --season {season} "
          f"--since {first}\n")


def _refuse_a_restatement_that_left_a_cliff(store, season: str, since) -> int:
    """Check the join a restatement necessarily leaves behind.

    ``--since`` rewrites a range and leaves everything before it alone, so the
    first day of the range now sits against a day that was scored from worse
    information. Where the correction went downwards -- which is most
    corrections, since a figure that was too high is what a premature credit
    or a double count leaves -- that join is a fall, and it is the whole
    correction expressed as a loss on one morning.

    Nothing here can fix it, because fixing it means restating further back,
    which is the caller's decision and another twenty minutes. So it is said
    as loudly as a run can say anything, with the date to use.
    """
    _, unexplained = _falls(store, season, only=since.isoformat())
    if not unexplained:
        return 0
    print(f"\n  THE RESTATEMENT LEFT A CLIFF on {since}.\n")
    print(f"  {len(unexplained)} asset(s) now sit against a day before the range "
          f"that holds\n  figures this restatement says were never true. The "
          f"correction is right and\n  it is landing as a one-day loss, which "
          f"is the thing being restated away:\n")
    for day, league, who, fall, fell in sorted(
            unexplained, key=lambda e: -e[3])[:FALLS_SHOWN]:
        from whul.store import monotonic
        print(f"    {day}  {league:14s} {who[:24]:24s} -{fall:8,.2f}   "
              f"{monotonic.explain(fell)}")
    if len(unexplained) > FALLS_SHOWN:
        print(f"    ... and {len(unexplained) - FALLS_SHOWN} more")
    _say_how_to_clear_them(store, season, unexplained)
    return 1


def cmd_rescore(args: argparse.Namespace) -> int:
    """Restate every stored day against one benchmark version.

    Adopting a new scale changes what 100 means, and only the days scored after
    it was frozen know that. The days before keep the old divisor, so the ledger
    -- which differences consecutive days -- shows every asset in a group
    dropping on the changeover, by the group's percentage, having done nothing.
    Three club soccer players went down about a point on the day the September
    scale was adopted, with identical raw totals on both days.

    No feed is touched. The raw figures are already in ``raw_stats``; this
    re-runs the normalization over them, which is the only step the new scale
    changes. That is also why it can be trusted: rescoring the most recent day
    must reproduce what is already stored for it, and the command checks that
    before writing anything.
    """
    from whul import pipeline
    from whul.normalize import apply_benchmarks
    from whul.store import benchmarks as store_benchmarks
    from whul.store import open_store

    store = open_store(args.db)
    version = args.version
    if version in (None, "", "frozen"):
        active = store_benchmarks.active_version(store, args.season)
        if active is None:
            print(f"\nNo frozen benchmark for {args.season}, so there is nothing "
                  f"to restate against.\n", file=sys.stderr)
            return 1
        version = active.version
    bench = store_benchmarks.load(store, version)
    if bench.empty:
        print(f"\n{version} holds no benchmarks.\n", file=sys.stderr)
        return 1

    days = [str(d) for d in store.query(
        "SELECT DISTINCT as_of FROM daily_scores WHERE season = ? ORDER BY as_of",
        (args.season,),
    )["as_of"]]
    if not days:
        print(f"\nNo scored days for {args.season}.\n", file=sys.stderr)
        return 1

    assets = store.query("SELECT asset_id, asset_type, league FROM assets")
    kinds = (
        dict(zip(assets["asset_id"], assets["asset_type"])),
        dict(zip(assets["asset_id"], assets["league"])),
    )
    print(f"\nRestating {len(days)} day(s) of {args.season} against {version}.\n")

    changed, moved, written, wrong = 0, 0.0, 0, []
    plan = []
    for day in days:
        rows_for_day = store.query(
            "SELECT asset_id, scaled_score, benchmark_version FROM daily_scores "
            "WHERE season = ? AND as_of = ?", (args.season, day),
        )
        already = set(rows_for_day["benchmark_version"]) == {version}
        stored = rows_for_day.set_index("asset_id")["scaled_score"].to_dict()

        rows = _rescore_day(store, args.season, day, bench, kinds, apply_benchmarks)
        if rows is None or rows.empty:
            print(f"  {day}: no raw figures stored, so it is left as it is")
            continue

        day_changed, day_moved = 0, 0.0
        for row in rows.itertuples():
            was = stored.get(row.asset_id)
            if was is None:
                continue
            gap = abs(float(row.scaled_score) - float(was))
            if gap > 0.05:
                day_changed += 1
                day_moved = max(day_moved, gap)
        changed += day_changed
        moved = max(moved, day_moved)
        # A day already scored against this version must come back unchanged.
        # It is the only check available that the restatement reproduces what
        # the scorer itself does, and it costs nothing to print.
        if already and day_changed:
            # This day was already scored against this very scale, so restating
            # it must reproduce what is there. That it does not means the
            # restatement is not doing what the scorer does, and every earlier
            # day it rewrites would be wrong in the same way and silently.
            wrong.append((day, day_changed, day_moved))
        note = ("unchanged, as it should be -- already scored against this scale"
                if already and not day_changed else
                f"{day_changed} asset(s) move, largest {day_moved:.1f}"
                if day_changed else "nothing moves")
        print(f"  {day}: {len(rows):>3} asset(s) restated; {note}")
        plan.append((day, rows))

    if wrong:
        print(f"\n  REFUSED. {len(wrong)} day(s) already scored against {version} "
              f"do not come back the same:", file=sys.stderr)
        for day, count, gap in wrong:
            print(f"    {day}: {count} asset(s) differ, largest {gap:.1f}",
                  file=sys.stderr)
        print("\n  Restating the other days would rewrite them the same wrong "
              "way, and\n  nothing afterwards would say so. Nothing was "
              "written.\n", file=sys.stderr)
        return 1

    for day, rows in plan:
        if args.dry_run:
            continue
        written += pipeline.write_daily_scores(
            store, rows.assign(total_points=rows["league_points"]),
            args.season, day, version,
        )

    print(f"\n  {changed} asset-day(s) move by more than a tenth; "
          f"largest move {moved:.1f}")
    if args.dry_run:
        print("\n  --dry-run, so nothing was written.\n")
        return 0
    store.conn.commit()
    print(f"  {written} row(s) restated.")
    print(f"\n  Now run `rollup --backfill` to rebuild the standings from "
          f"them.\n")
    return 0


def _rescore_day(store, season: str, day: str, bench, kinds, apply_benchmarks):
    """One day's stored figures, scaled against the given benchmarks.

    Grouped by asset type, because a normalization group is a property of one:
    a Player's is his position within his league and a Team's is the league
    itself, and scaling them together would look up the wrong row.
    """
    import json

    import pandas as pd

    # Read the payload rather than going through `read_stats`, which drops any
    # payload column the table already has -- and `league` is one of them.
    # The two are different things. The table records the league the *source*
    # answers for and the payload the group the row is scored in: "Club Soccer"
    # against "La Liga", "Motorsports" against "F1". Neither is redundant and
    # neither is right for both. The scorer normalizes on the payload's, so
    # this does too. Taking the table's dropped sixty-one of a hundred and
    # forty-five rows for having no benchmark -- among them every row that was
    # going to move, so the restatement called the days already correct.
    stored = store.query(
        "SELECT asset_id, source, league AS feed_league, stats FROM raw_stats "
        "WHERE season = ? AND as_of = ?", (season, day),
    )
    if stored.empty:
        return None
    payload = pd.json_normalize(stored["stats"].map(json.loads))
    payload.index = stored.index
    frame = payload.assign(
        asset_id=stored["asset_id"], source=stored["source"],
        asset_type=stored["asset_id"].map(kinds[0]),
    )
    if "league" not in frame.columns:
        frame["league"] = stored["feed_league"]
    else:
        frame["league"] = frame["league"].fillna(stored["feed_league"])
    if "total_points" not in frame.columns:
        return None

    out = []
    for asset_type, rows in frame.groupby("asset_type"):
        if not isinstance(asset_type, str) or not asset_type:
            continue
        placed = apply_benchmarks(rows, bench, asset_type, strict=False)
        placed = placed[placed["scaled_score"].notna()]
        if placed.empty:
            continue
        # The one fold that happens after scaling: a two-way player's batting
        # and pitching are not comparable until both are on the 0-100 scale.
        # It is keyed on the source rather than the league so that a row from
        # any other MLB feed is left alone.
        if (placed["source"] == "mlb").any():
            from whul.scoring import mlb as mlb_scoring

            mine = placed[placed["source"] == "mlb"]
            rest = placed[placed["source"] != "mlb"]
            placed = pd.concat([mlb_scoring.combine_two_way(mine), rest],
                               ignore_index=True)
        out.append(placed)
    if not out:
        return None
    joined = pd.concat(out, ignore_index=True)
    return joined.assign(league_points=joined["total_points"])


#: Title fields a stored payload can carry. One whose name begins ``pts_``
#: carries its own points and is authoritative; a bare flag is paid at the
#: scorer's own weight, looked up rather than copied here so that the two
#: cannot drift apart.
TITLE_FIELDS = ("pts_div_champ", "pts_reg_champ", "pts_league_title", "div_champ")


def _title_weight(field: str) -> float:
    if field == "div_champ":
        from whul.scoring import nfl as nfl_scoring

        return float(nfl_scoring.TEAM_WEIGHTS["div_champ"])
    return 1.0


def _titles_held(payload: dict) -> dict:
    """Title fields this payload was paid for, and what each was worth."""
    held = {}
    for field in TITLE_FIELDS:
        if field not in payload:
            continue
        try:
            value = float(payload[field])
        except (TypeError, ValueError):
            continue
        if value:
            held[field] = value * _title_weight(field)
    return held


def cmd_retract_titles(args: argparse.Namespace) -> int:
    """Take back a title that was awarded before its season was settled.

    A title is a season outcome and nobody holds one in September, but the
    scorer used to hand one to whoever led the table. New England and Seattle
    each carried an NFL division title after a single game -- one of which they
    lost -- Miami six points for an ACC title on a 1-0 conference record, and
    three clubs an MLB division five. ``settled_seasons`` stopped it happening
    again; it cannot reach the days it already happened on.

    Nor can anything else here. ``rescore`` re-divides a stored total by a new
    benchmark and never re-runs a scorer, and ``backfill`` rebuilds the
    standings from the scores rather than from the feeds. So yesterday keeps
    the title, today does not, and the ledger -- which differences consecutive
    days -- reads the correction as a fifteen-point collapse by a team that did
    nothing.

    This edits the stored figures, which nothing else here does. Two things
    keep that honest. It only takes back a title the asset no longer holds on
    its newest stored day, so a division won in January is left alone and only
    one the rules have since withdrawn is touched. And having rescored a day it
    checks that every asset it did *not* edit came back exactly as stored,
    refusing the lot if any moved -- the same guard ``rescore`` uses, and the
    only available evidence that the rescoring does what the scorer does.

    ``--all`` widens it to every title in the season, for the case the first
    rule cannot see: an asset whose feed stopped answering has no newer day for
    the title to be gone on. Miami's sat on its last four stored days and there
    was no fifth. That is only correct while the season is still being played,
    when no title can have been won yet, so it is a flag that has to be typed
    rather than a judgement made quietly.

    Written for one job, on one season, once. Run it with --dry-run first.
    """
    import json

    from whul import pipeline
    from whul.normalize import apply_benchmarks
    from whul.store import benchmarks as store_benchmarks
    from whul.store import open_store

    store = open_store(args.db)
    active = store_benchmarks.active_version(store, args.season)
    if active is None:
        print(f"\nNo frozen benchmark for {args.season}, so the days it touches "
              f"could not be rescored.\n", file=sys.stderr)
        return 1
    bench = store_benchmarks.load(store, active.version)

    # Source and phase are part of the key, not decoration: an asset can have a
    # regular row and a postseason one on the same day, and an update matching
    # only the date would write one payload over both.
    raw = store.query(
        "SELECT as_of, asset_id, source, phase, stats FROM raw_stats "
        "WHERE season = ? ORDER BY asset_id, as_of", (args.season,),
    )
    if raw.empty:
        print(f"\nNo stored figures for {args.season}.\n", file=sys.stderr)
        return 1

    series: dict = {}
    for row in raw.itertuples():
        series.setdefault((row.asset_id, row.source, row.phase), []).append(
            (str(row.as_of), json.loads(row.stats)))

    edits = []
    still_held = []
    for (asset_id, source, phase), days in series.items():
        held = [(day, payload, _titles_held(payload)) for day, payload in days]
        if held[-1][2] and not args.all:
            # Still holding it on the newest day, so it may have been won: a
            # title the rules withdrew is one that is gone by now. Except that
            # an asset whose feed stopped answering has no newer day to be
            # gone on -- Miami's ACC title sat on its last four stored days and
            # there was no fifth -- which is what --all is for, and why it says
            # what it assumes rather than quietly assuming it.
            still_held.append((asset_id, held[-1][0], sorted(held[-1][2])))
            continue
        edits += [(day, asset_id, source, phase, payload, titles)
                  for day, payload, titles in held if titles]

    if not edits:
        print(f"\nNo title to take back in {args.season}.\n")
        for asset_id, day, fields in sorted(still_held):
            print(f"  {asset_id} still holds {', '.join(fields)} on {day}, "
                  f"so it is left alone")
        return 0

    print(f"\nTaking back {len(edits)} title(s) awarded in {args.season}, "
          f"then rescoring against {active.version}.\n")
    for day, asset_id, _, _, payload, titles in sorted(edits):
        what = ", ".join(f"{f} {p:g}" for f, p in sorted(titles.items()))
        was = float(payload.get("total_points") or 0.0)
        print(f"  {day}  {asset_id:<38}{was:>9.2f} -> "
              f"{was - sum(titles.values()):>8.2f}   ({what})")
    for asset_id, day, fields in sorted(still_held):
        print(f"\n  {asset_id} still holds {', '.join(fields)} on {day}, "
              f"so it is left alone")

    if args.dry_run:
        print("\n  --dry-run, so nothing was written.\n")
        return 0

    touched: dict = {}
    with store.transaction() as conn:
        for day, asset_id, source, phase, payload, titles in edits:
            for field in titles:
                payload[field] = 0
            for total in ("total_points", "regular_points", "league_points"):
                if total in payload:
                    try:
                        payload[total] = float(payload[total]) - sum(titles.values())
                    except (TypeError, ValueError):
                        pass
            conn.execute(
                "UPDATE raw_stats SET stats = ? WHERE season = ? AND as_of = ? "
                "AND asset_id = ? AND source = ? AND phase = ?",
                (json.dumps(payload), args.season, day, asset_id, source, phase),
            )
            touched.setdefault(day, set()).add(asset_id)

    assets = store.query("SELECT asset_id, asset_type, league FROM assets")
    kinds = (dict(zip(assets["asset_id"], assets["asset_type"])),
             dict(zip(assets["asset_id"], assets["league"])))

    plan, wrong = [], []
    for day in sorted(touched):
        stored = store.query(
            "SELECT asset_id, scaled_score FROM daily_scores WHERE season = ? "
            "AND as_of = ?", (args.season, day),
        ).set_index("asset_id")["scaled_score"].to_dict()
        rows = _rescore_day(store, args.season, day, bench, kinds, apply_benchmarks)
        if rows is None or rows.empty:
            continue
        for row in rows.itertuples():
            was = stored.get(row.asset_id)
            if was is None or row.asset_id in touched[day]:
                continue
            if abs(float(row.scaled_score) - float(was)) > 0.05:
                wrong.append((day, row.asset_id, float(was), float(row.scaled_score)))
        plan.append((day, rows))

    if wrong:
        print(f"\n  REFUSED. {len(wrong)} asset(s) this did not edit came back "
              f"changed anyway:", file=sys.stderr)
        for day, asset_id, was, now in wrong[:10]:
            print(f"    {day} {asset_id}: {was:.2f} -> {now:.2f}", file=sys.stderr)
        print("\n  That means the rescoring is not reproducing what the scorer "
              "does, so\n  the edited rows cannot be trusted either. The "
              "figures were rolled back.\n", file=sys.stderr)
        store.conn.rollback()
        return 1

    written = 0
    for day, rows in plan:
        written += pipeline.write_daily_scores(
            store, rows.assign(total_points=rows["league_points"]),
            args.season, day, active.version,
        )
    store.conn.commit()
    print(f"\n  {len(edits)} title(s) taken back; {written} row(s) rescored "
          f"across {len(plan)} day(s).")
    print(f"\n  Now run `rollup --backfill` to rebuild the standings from "
          f"them.\n")
    return 0


#: How closely a rescored day must reproduce what is already stored for it
#: before this will write anything -- the tenth of a point ``rescore`` and
#: ``retract-titles`` use.
REPRODUCES_WITHIN = 0.05

#: How many of the largest moves to print. Enough to recognise the shape of
#: the restatement without printing three hundred rows.
MOVES_SHOWN = 10


def cmd_reweight_mlb(args: argparse.Namespace) -> int:
    """Put stored MLB figures on the weights their benchmark was built with.

    The benchmark prices a contract year as 0.42 of a season at 0.75 and 0.58
    of one at 1.181: year N is worth less per game than year N+1 because only
    its last two months fall inside the league year. The live scorer applied
    neither multiplier. It wrote every 2026 figure at face value and then
    lifted the lot by a proration factor, so a September win was priced above
    what the frozen scale says a September win is worth. ``contract_weight``
    fixed the scorer, which fixes tomorrow. The days already stored keep the
    old arithmetic, and the ledger -- which differences consecutive days --
    would read the correction as every MLB asset losing a quarter of its
    season overnight.

    Nothing is fetched. The MLB player feed reports a season to date and
    cannot be asked what it said on the 4th of September, so a re-ingest could
    not restate those days even in principle. It does not need to: every input
    the scorer used is stored beside its output, so the corrected figure is
    arithmetic on the row. ``mlb.reweight_stored`` does that arithmetic, and
    rebuilds each component under the *old* rule first so that a row whose
    stored points it cannot reproduce is refused rather than rewritten on a
    guess.

    Three guards, because this rewrites stored figures, which almost nothing
    here does:

    * one bad row refuses the whole run. A restatement that skipped the rows
      it could not explain would leave the ledger half on each rule, which is
      worse than leaving it wholly on the old one.
    * every affected day is rescored *before* anything is written, and every
      MLB asset on it must come back within a tenth of its stored score. That
      is the only evidence available that the rescoring reproduces what the
      scorer does, and it is checked while nothing has been touched.
    * a row already carrying the current lift is left alone, so running this
      twice is running it once.

    Written for one job, on one season, once. Run it with --dry-run first.
    """
    import json

    from whul import pipeline
    from whul.normalize import apply_benchmarks
    from whul.scoring import mlb as mlb_scoring
    from whul.scoring import proration
    from whul.store import benchmarks as store_benchmarks
    from whul.store import open_store

    store = open_store(args.db)
    rule = (proration.load_rule(store, "MLB", args.season)
            or proration.built_in_rule("MLB", args.season))
    if rule is None:
        print(f"\nNo proration rule for MLB in {args.season}, so there is no "
              f"lift to restate onto.\n", file=sys.stderr)
        return 1
    lift = rule.factor

    active = store_benchmarks.active_version(store, args.season)
    if active is None:
        print(f"\nNo frozen benchmark for {args.season}, so the days this "
              f"touches could not be rescored.\n", file=sys.stderr)
        return 1
    bench = store_benchmarks.load(store, active.version)

    # Source and phase are part of the key, not decoration: an asset can have
    # a regular row and a postseason one on the same day, and an update
    # matching only the date would write one payload over both.
    raw = store.query(
        "SELECT as_of, asset_id, source, phase, stats FROM raw_stats "
        "WHERE season = ? AND league = 'MLB' ORDER BY as_of, asset_id",
        (args.season,),
    )
    if raw.empty:
        print(f"\nNo stored MLB figures for {args.season}.\n", file=sys.stderr)
        return 1

    edits, refused, already = [], [], 0
    for row in raw.itertuples():
        figures = json.loads(row.stats)
        rebuilt, problem = mlb_scoring.reweight_stored(figures, lift)
        if problem == mlb_scoring.ALREADY_CURRENT:
            already += 1
        elif problem:
            refused.append((str(row.as_of), str(row.asset_id), problem))
        else:
            edits.append((str(row.as_of), str(row.asset_id), row.source,
                          row.phase, figures, rebuilt))

    if refused:
        print(f"\n  REFUSED. {len(refused)} stored row(s) could not be rebuilt "
              f"from what is on them:", file=sys.stderr)
        for day, asset_id, problem in refused[:MOVES_SHOWN]:
            print(f"    {day} {asset_id}: {problem}", file=sys.stderr)
        print("\n  A row this cannot explain is a row it was never meant to "
              "touch, and\n  restating the rest would leave the ledger half on "
              "each rule. Nothing\n  was written.\n", file=sys.stderr)
        return 1

    if not edits:
        print(f"\nEvery stored MLB row in {args.season} already carries the "
              f"{lift:.6f} lift ({already} row(s)). Nothing to do.\n")
        return 0

    print(f"\nRestating {len(edits)} stored MLB row(s) in {args.season} onto "
          f"the contract weights, then rescoring against {active.version}.\n")
    if already:
        print(f"  {already} row(s) already carry the {lift:.6f} lift and are "
              f"left alone.\n")

    by_day: dict = {}
    for day, asset_id, _, _, was, now in edits:
        by_day.setdefault(day, []).append(
            (asset_id, _points(was), _points(now)))
    for day in sorted(by_day):
        rows = by_day[day]
        before = sum(b for _, b, _ in rows)
        after = sum(a for _, _, a in rows)
        print(f"  {day}: {len(rows):>3} row(s), {before:>9.1f} -> "
              f"{after:>9.1f} points")

    newest = sorted(by_day)[-1]
    print(f"\n  Largest moves on {newest}:")
    for asset_id, before, after in sorted(
        by_day[newest], key=lambda r: r[1] - r[2], reverse=True
    )[:MOVES_SHOWN]:
        print(f"    {asset_id:<38}{before:>9.2f} -> {after:>8.2f}")

    assets = store.query("SELECT asset_id, asset_type, league FROM assets")
    kinds = (dict(zip(assets["asset_id"], assets["asset_type"])),
             dict(zip(assets["asset_id"], assets["league"])))
    # Only MLB rows are edited and only MLB rows are written back, so the
    # check covers exactly what this writes. Holding the other leagues to it
    # would refuse the run over something it neither reads nor touches.
    mine = {a for a, league in kinds[1].items() if league == "MLB"}

    days = sorted(by_day)
    wrong = []
    for day in days:
        stored = store.query(
            "SELECT asset_id, scaled_score, benchmark_version FROM daily_scores "
            "WHERE season = ? AND as_of = ?", (args.season, day),
        )
        # A day scored against an older scale is *meant* to come back
        # different, so it has nothing to say about whether the rescoring
        # works and is left out of the check rather than failing it.
        stored = stored[stored["benchmark_version"] == active.version]
        was = dict(zip(stored["asset_id"], stored["scaled_score"]))
        rows = _rescore_day(store, args.season, day, bench, kinds,
                            apply_benchmarks)
        if rows is None or rows.empty:
            continue
        for row in rows.itertuples():
            before = was.get(row.asset_id)
            if before is None or row.asset_id not in mine:
                continue
            if abs(float(row.scaled_score) - float(before)) > REPRODUCES_WITHIN:
                wrong.append((day, str(row.asset_id), float(before),
                              float(row.scaled_score)))

    if wrong:
        print(f"\n  REFUSED. {len(wrong)} MLB asset-day(s) do not come back as "
              f"stored even before the restatement:", file=sys.stderr)
        for day, asset_id, before, now in wrong[:MOVES_SHOWN]:
            print(f"    {day} {asset_id}: {before:.2f} -> {now:.2f}",
                  file=sys.stderr)
        print("\n  That means the rescoring is not reproducing what the scorer "
              "does, so\n  the restated rows could not be trusted either. "
              "Nothing was written.\n", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n  Every affected day rescores to what is stored for it.")
        print("\n  --dry-run, so nothing was written.\n")
        return 0

    for day, asset_id, source, phase, _, rebuilt in edits:
        store.conn.execute(
            "UPDATE raw_stats SET stats = ? WHERE season = ? AND as_of = ? "
            "AND asset_id = ? AND source = ? AND phase = ?",
            (json.dumps(rebuilt), args.season, day, asset_id, source, phase),
        )

    written = 0
    for day in days:
        rows = _rescore_day(store, args.season, day, bench, kinds,
                            apply_benchmarks)
        if rows is None or rows.empty:
            continue
        rows = rows[rows["asset_id"].isin(mine)]
        if rows.empty:
            continue
        written += pipeline.write_daily_scores(
            store, rows.assign(total_points=rows["league_points"]),
            args.season, day, active.version,
        )
    store.conn.commit()

    print(f"\n  {len(edits)} stored row(s) restated; {written} score(s) "
          f"rewritten across {len(days)} day(s).")
    print(f"\n  Now run `rollup --backfill` to rebuild the standings from "
          f"them.\n")
    return 0


def _points(figures: dict) -> float:
    """A stored row's total, as a number whatever the payload put there."""
    try:
        total = float(figures.get("total_points"))
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if total != total else total


def _seed_from_typed_results(args, path, source, keys) -> int:
    """Results typed by hand, checked before anything is written.

    They arrive from the player's point of view, so a match appears twice --
    once as a win and once as a loss -- and the two copies are collapsed. What
    is *not* collapsed is a doubt: a row missing its opponent is left out and
    named, because importing it blank would key differently from the same match
    arriving named later, and the pair would be paid twice.
    """
    from pathlib import Path

    from whul.sources import tennis_text
    from whul.store import feed_ledger

    from datetime import date as _date

    dates = {}
    for entry in args.date or []:
        if "=" not in entry:
            print(f"\n--date wants TOURNAMENT[:ROUND[:TOUR]]=YYYY-MM-DD, not "
                  f"{entry!r}\n", file=sys.stderr)
            return 1
        name, when = entry.split("=", 1)
        dates[name.strip().casefold()] = when.strip()

    rows, problems = tennis_text.parse(path.read_text(), dates)
    named = {r["winner"] for r in rows} | {r["loser"] for r in rows}
    players = {w for w in named if sum(
        1 for r in rows if w in (r["winner"], r["loser"])) > 1}
    incomplete = tennis_text.gaps(rows, players, as_of=_date.today())

    print(f"\n  {len(rows)} distinct match(es) from {path}")
    if problems:
        print(f"\n  {len(problems)} row(s) not imported:")
        for line in problems:
            print(f"      {line}")
    if incomplete:
        print(f"\n  {len(incomplete)} run(s) that do not hold together:")
        for line in incomplete:
            print(f"      {line}")

    undated = sorted({r["tournament"] for r in rows if not r.get("date")})
    if undated:
        print(f"\n  REFUSED. No date for: {', '.join(undated)}.")
        print("  A tennis total is summed over the league year, so a match with")
        print("  no date is a match that scores nothing -- silently. Supply one")
        print("  per tournament:")
        for name in undated:
            print(f"      --date \"{name}=YYYY-MM-DD\"")
        print()
        return 1

    total = feed_ledger.write_seed(source.key, pd.DataFrame(rows), keys)
    seed = feed_ledger.seed_path(source.key)
    print(f"\n  {total:,} match(es) now in {seed}\n")
    print(f"  Commit it. Every pull loads it before the feed's window.\n")
    return 0


def cmd_feed_seed(args: argparse.Namespace) -> int:
    """Put a source's history where the ledger can always find it again.

    A feed that serves a rolling window cannot be asked about last month, so
    everything before the ledger started has to come from somewhere else: the
    tennis2026 app's own database, an export, a list typed by hand. Whatever it
    comes from, it lands in one place -- ``data/seed/<source>.jsonl``, in the
    repository -- and every pull loads it before it loads the window.

    In the repository and not in the database on purpose. Three workflows
    rebuild the database and force-push it to the data branch, and a history
    that had to be restored by somebody remembering to restore it is one that
    eventually is not.

    Idempotent. Run it again with a fresher export and the new matches are
    added, the ones already held are left as they were, and the file stays
    sorted so the diff is readable.
    """
    from pathlib import Path

    from whul.benchmark_sources import resolve
    from whul.store import feed_ledger

    source = next((s for s in resolve(None) if s.key == args.source), None)
    if source is None:
        print(f"\nNo source {args.source!r}.\n", file=sys.stderr)
        return 1
    keys = getattr(source, "accumulates", ())
    if not keys:
        print(f"\n{args.source} reads a feed that answers for whole seasons, so "
              f"it has no history to keep.\n", file=sys.stderr)
        return 1

    if getattr(args, "from_ledger", False):
        return _seed_from_the_ledger(args, source, keys)
    if not args.source_file:
        print(f"\nGive a file to read, or --from-ledger to write down what "
              f"{args.source} has already gathered.\n", file=sys.stderr)
        return 1

    path = Path(args.source_file)
    if path.suffix == ".txt":
        return _seed_from_typed_results(args, path, source, keys)
    if not path.exists():
        print(f"\nNo such file: {path}\n", file=sys.stderr)
        return 1
    try:
        rows = _read_feed_history(path, args.source)
    except Exception as exc:  # noqa: BLE001 -- the path is the whole message
        print(f"\nCould not read {path}: {type(exc).__name__}: {exc}\n",
              file=sys.stderr)
        return 1
    if rows is None or rows.empty:
        print(f"\n{path} holds no matches.\n", file=sys.stderr)
        return 1

    missing = [k for k in keys if k not in rows.columns]
    if missing:
        print(f"\n{path} has no {', '.join(missing)}. {args.source} identifies a "
              f"row by {', '.join(keys)}, and a history keyed on anything else "
              f"would be paid for twice alongside the feed's own copy.\n",
              file=sys.stderr)
        return 1

    before = len(feed_ledger.read_seed(args.source))
    total = feed_ledger.write_seed(args.source, rows, keys)
    seed = feed_ledger.seed_path(args.source)
    print(f"\n  {len(rows):,} row(s) read from {path}")
    print(f"  {total - before:,} new; {total:,} now in {seed}")
    if "date" in rows.columns and not rows["date"].isna().all():
        held = feed_ledger.read_seed(args.source)
        print(f"  covering {held['date'].min()} to {held['date'].max()}")
    print(f"\n  Commit {seed}. Every pull loads it before the feed's window, "
          f"so\n  the history survives the database being rebuilt.\n")
    return 0


def _seed_from_the_ledger(args: argparse.Namespace, source, keys: tuple) -> int:
    """Commit what a source has gathered a night at a time.

    A club soccer season is not exported from anywhere -- it is accumulated,
    one pull at a time, as the guard against a feed forgetting what it showed
    yesterday. That record lives in a database three workflows rebuild and
    force-push, so a season held only there is a season one rebuild from being
    the feed's opinion again.
    """
    from whul.store import feed_ledger
    from whul.store import open_store

    held = feed_ledger.load(open_store(args.db), source.key)
    if held is None or held.empty:
        print(f"\n{source.key} has gathered nothing yet.\n", file=sys.stderr)
        return 1

    before = len(feed_ledger.read_seed(source.key))
    total = feed_ledger.write_seed(source.key, held, keys)
    path = feed_ledger.seed_path(source.key)
    print(f"\n  {len(held):,} row(s) in {source.key}'s ledger")
    print(f"  {total - before:,} new; {total:,} now in {path}")
    if "date" in held.columns and not held["date"].isna().all():
        print(f"  covering {held['date'].min()} to {held['date'].max()}")
    print(f"\n  Commit {path}. Every pull loads it before the feed's own "
          f"answer, so\n  the history survives the database being rebuilt.\n")
    return 0


def _read_feed_history(path: Path, source: str):
    """A history export, whatever shape it arrived in."""
    import json

    import pandas as pd

    if path.suffix in (".db", ".sqlite", ".sqlite3"):
        if source != "tennis":
            raise ValueError(f"no database reader for {source}")
        from whul.sources import tennis2026

        return tennis2026.load_matches(path=path, verbose=True)
    if path.suffix == ".csv":
        return pd.read_csv(path)
    if path.suffix in (".json", ".jsonl"):
        text = path.read_text()
        if path.suffix == ".jsonl":
            return pd.DataFrame([json.loads(l) for l in text.splitlines() if l.strip()])
        return pd.DataFrame(json.loads(text))
    raise ValueError(
        f"{path.suffix or 'that'} is not a shape this reads: a tennis2026 "
        f"database (.db), or a .csv/.json/.jsonl carrying one row per match"
    )


def _overview_lines(report: dict) -> list[str]:
    """The overview's statistics blocks, which may already be per competition."""
    blocks = (report or {}).get("blocks") or []
    if not blocks:
        return []
    lines = ["", "      --- statistics blocks in the overview ---"]
    for block in blocks[:14]:
        lines.append(f"      {block.get('title')!r}  at {block.get('at')}")
        if block.get("labels"):
            lines.append(f"          labels: {', '.join(block['labels'])}")
        if block.get("values"):
            lines.append(f"          values: {', '.join(block['values'])}")
    titles = [str(b.get("title")) for b in blocks]
    lines.append(f"      {len(blocks)} block(s); distinct titles: "
                 f"{len(set(titles))}")
    if len(set(titles)) > 1:
        lines.append("      ^ more than one title, so this may already be split by "
                     "competition")
    return lines


def _compared_lines(report: dict) -> list[str]:
    """The roster and the overview, on the same player and competition."""
    lines = ["", "  --- roster against overview, same player, same competition ---",
             f"      asked ESPN for season {report.get('asked')}"]
    roster = report.get("roster") or {}
    over = report.get("overview") or {}
    if report.get("roster_error"):
        lines.append(f"      roster: {report['roster_error']}")
    else:
        lines.append(f"      roster  {roster.get('player', '?')}: "
                     f"matches={roster.get('matches')} starts={roster.get('starts')} "
                     f"goals={roster.get('goals')}")
    if report.get("overview_error"):
        lines.append(f"      overview: {report['overview_error']}")
    else:
        lines.append(f"      overview {over.get('label', '?')!r}")
        for name, value in over.items():
            if name != "label":
                lines.append(f"          {name} = {value}")
    lines += [
        "",
        "      If the overview says fewer appearances than the roster, it is the",
        "      better-scoped endpoint and the fix. If the two agree, both carry",
        "      the same European match and no endpoint here rescues attribution.",
    ]
    return lines


def _by_path_lines(report: dict) -> list[str]:
    """The competition in the path rather than in a parameter."""
    lines = ["", "  --- gamelog with the competition in the PATH ---",
             "      (the `league` parameter was shown not to filter: eight values,",
             "       one identical Champions League match each)"]
    for entry in report.get("tried") or []:
        head = f"      {entry.get('path'):<18}{entry.get('shape'):<14}"
        if entry.get("error"):
            lines.append(head + f"refused: {entry['error']}")
            continue
        named = ", ".join(entry.get("named") or []) or "-"
        lines.append(head + f"{entry.get('matches', 0)} match(es): {named}")
    lines += [
        "",
        "      If a path returns its own competition's matches and nobody else's,",
        "      that is the request shape. If every path returns the same match",
        "      again, the gamelog cannot be asked for a competition at all and the",
        "      overview's blocks are the remaining hope.",
    ]
    return lines


def _by_competition_lines(report: dict) -> list[str]:
    """One competition at a time, which is how a loader would ask."""
    from whul.sources.espn_soccer import NEEDED_FROM_A_MATCH

    lines = ["", "  --- the gamelog, one competition at a time ---",
             f"      {report.get('url', '')}"]
    for key, entry in (report.get("asked") or {}).items():
        ours = entry.get("our_key") or "-"
        lines.append(f"      {key:<20} -> {ours:<14}{entry.get('scored', '')}")
        if entry.get("error"):
            lines.append(f"          refused: {entry['error']}")
            continue
        lines.append(f"          {entry.get('matches', 0)} match(es); season types: "
                     f"{', '.join(entry.get('seasonTypes') or []) or '-'}")
        named = [n for n in entry.get("named") or [] if n]
        lines.append(f"          events name: {', '.join(named) or '-'}"
                     + ("   <- the filter did NOT filter" if len(named) > 1 else ""))
        if entry.get("stat_row_keys"):
            lines.append(f"          a stat row carries: "
                         f"{', '.join(entry['stat_row_keys'])}")
        have = [n for n in entry.get("names") or []
                if any(w.lower() in n.lower() for w in NEEDED_FROM_A_MATCH)]
        lines.append(f"          appearance/start fields: {', '.join(have) or 'NONE'}")
    lines += [
        "",
        "      Two questions. Does `league=` actually filter -- if the events name",
        "      more than one competition, it does not. And is a *start* anywhere in",
        "      a row? Appearance points are 2 for a start and 1 off the bench, and",
        "      no label so far carries it.",
    ]
    return lines


def _gamelog_lines(report: dict) -> list[str]:
    """The gamelog in the terms a loader would need, or nothing."""
    import json

    if not report:
        return []
    lines = ["", "      --- gamelog in detail ---"]
    for entry in report.get("filters") or []:
        options = ", ".join(
            f"{o['value']}={o['label']}" for o in entry.get("options") or []
        )
        lines.append(f"      filter {entry.get('name')} = {entry.get('value')}"
                     + (f"  [{options}]" if options else ""))
    for key in ("labels", "names", "displayNames"):
        if report.get(key):
            lines.append(f"      {key}: {', '.join(report[key])}")
    for entry in report.get("seasonTypes") or []:
        lines.append(f"      seasonType {entry.get('displayName')!r} "
                     f"({entry.get('matches')} match(es)) keys={entry.get('keys')}")
    if report.get("events_shape"):
        lines.append(f"      events: {report['events_shape']}")
    if report.get("event_keys"):
        lines.append(f"      an event carries: {', '.join(report['event_keys'])}")
    for event in (report.get("events") or [])[:3]:
        lines.append("      " + json.dumps(event, default=str)[:400])
    lines += [
        "",
        "      The question: does an EVENT name its own competition? If it does,",
        "      a match can be attributed without inheriting the request's league.",
    ]
    return lines


def _cup_appearances(league, athlete, counted, matches, season, lineups):
    """His club's ties in competitions his gamelog never named, that he played.

    Deliberately narrow. Where the gamelog returned a competition it is the
    authority on which of its matches he was in, and second-guessing it would
    be a request per match for an answer already given. This asks only about
    the competitions it is silent on, which is the whole of the gap.
    """
    from whul.sources import espn_soccer

    if matches is None or getattr(matches, "empty", True):
        return []
    if "competition_key" not in matches.columns:
        return []
    seen = ({str(r.competition_key) for r in counted.itertuples()}
            if counted is not None and not counted.empty else set())
    keys = {str(k) for k in matches["competition_key"]}
    missing = keys - seen
    if not missing:
        return []
    ties = matches[matches["competition_key"].astype(str).isin(missing)]
    return espn_soccer.matches_he_played(
        league, athlete, ties, season, seen=lineups)


def cmd_probe_squad(args: argparse.Namespace) -> int:
    """What a competition's squad pull actually returns, in three numbers.

    The domestic cups reach a player's total through this request and nothing
    else, and they are contributing nothing: Cole Palmer's breakdown holds the
    Premier League and not the two League Cup ties Chelsea played. Three things
    could be true and they look identical from the outside -- the competition
    lists no clubs, the clubs answer with no squads, or the squads come back
    with every appearance at zero -- and only the first two are the feed
    refusing. The third is the feed answering honestly about a competition it
    does not keep statistics for, and no amount of retrying fixes it.

    Prints the count at each stage, so the next step is decided by which one
    is zero rather than by guessing.
    """
    from whul.sources import espn_soccer

    league = args.competition
    season = int(args.season)
    asked = espn_soccer.roster_season(league, season)
    print(f"\n  {league}, our {season} -- ESPN's {asked}\n")

    why: list[str] = []
    clubs = espn_soccer.team_ids(league, season, note=why)
    for line in why:
        print(f"      {line}")
    print(f"  clubs listed: {len(clubs)}")
    if not clubs:
        print("\n  Nothing else can be pulled. This competition contributes "
              "nothing to any player,\n  and will go on doing so until the "
              "club list answers.\n")
        return 1
    shown = ", ".join(sorted(clubs)[:6])
    print(f"      {shown}{', ...' if len(clubs) > 6 else ''}")

    frame = espn_soccer.load_players(league, [season], verbose=True)
    rows = 0 if frame is None or frame.empty else len(frame)
    print(f"  player rows: {rows}")
    if not rows:
        print("\n  The clubs are listed and their squads are not. The request "
              "shape is what to look at:\n  `espn_soccer.load_squad`.\n")
        return 1

    played = pd.to_numeric(frame.get("matches"), errors="coerce").fillna(0)
    appeared = int((played > 0).sum())
    print(f"  rows recording an appearance: {appeared}")
    if not appeared:
        print("\n  The feed answered with the squads and no appearances in "
              "them. That is the feed\n  saying it keeps no statistics for "
              "this competition, and the appearances have to\n  come from the "
              "ties themselves -- see `probe-cup`, which reads a match's own "
              "lineup.\n")
        return 1

    top = frame.assign(_m=played).sort_values("_m", ascending=False).head(5)
    print("\n  The most-played, as the feed reports them:")
    for row in top.to_dict("records"):
        print(f"      {str(row.get('team'))[:22]:<24}"
              f"{str(row.get('player'))[:24]:<26}{row['_m']:g}")
    print("\n  This competition is answering. If it is missing from a "
          "player's breakdown,\n  the loss is after the pull -- "
          "`_soccer_players` attributes a cup row by the club's\n  league, and "
          "a club it cannot place is dropped.\n")
    return 0


def cmd_probe_cup(args: argparse.Namespace) -> int:
    """Whether a cup tie's own record says who played in it.

    The gamelog does not return the domestic cups. The cups are scored anyway
    -- the roster aggregate carries them -- so what is missing is only the
    attribution, and the club's match list already names the ties. This asks
    whether their summaries name the players.
    """
    from whul.sources import espn_soccer

    found = espn_soccer.probe_cup_lineups(
        args.league, args.club, int(args.season))
    lines = [
        f"ESPN cup-lineup probe -- {found['league']}, {found['club']}, "
        f"season {found['season']}",
        "",
        f"  domestic cups for this league   {', '.join(found['cups']) or '(none)'}",
        f"  matches found for the club      {found.get('club_matches', 0)}",
        f"  by competition                  {found.get('by_competition', {})}",
        f"  of those, cup ties              {found.get('cup_ties', 0)}",
    ]
    if found.get("problem"):
        lines += ["", f"  {found['problem']}"]
    for tie in found.get("ties", []):
        lines += ["", f"  event {tie['event_id']} ({tie['competition']})"]
        if tie.get("refused"):
            lines.append(f"      refused: {tie['refused']}")
            continue
        lines.append(f"      top-level keys: {', '.join(tie['top_level_keys'])}")
        lines.append(f"      athletes named: {tie['athletes_named']}")
        for who in tie.get("sample", []):
            lines.append(f"          {who}")
    lines += [
        "",
        "  What to look for: a tie naming twenty-odd athletes is one whose",
        "  lineup can be read, and attribution has its route -- a couple of",
        "  requests a club rather than a season of them. A tie naming none",
        "  means the summary carries no lineup and the next question is the",
        "  match's own roster endpoint.",
        "",
    ]
    for tie in found.get("ties", []):
        for line in tie.get("shape", []):
            lines.append(f"  {line}")
    report = "\n".join(lines)
    print(report)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(report + "\n")
        print(f"  written to {args.out}\n")
    return 0


def cmd_probe_athlete(args: argparse.Namespace) -> int:
    """Ask whether an athlete's own record names the competition.

    The roster gives a player one statistics block for a season and does not
    say which competition it covers, so a figure inherits whichever request
    fetched it. That is how Harry Kane's Champions League match came to be
    counted as Bundesliga football on one night and not the next.

    This does not fix anything. It asks ESPN the same question five ways and
    prints what came back, so the fix can be built against what the feed
    actually serves rather than against what it ought to.
    """
    from whul.sources import espn_soccer

    season = int(args.season) if args.season else None
    athlete = args.athlete
    club = args.club
    if args.player and not athlete:
        # By name, because picking the first athlete on the first club returned
        # Bayern's goalkeeper -- whose figures are saves and clean sheets, and
        # read as a feed with no appearances in it rather than as a probe that
        # had chosen a keeper.
        athlete, club = espn_soccer.athlete_named(args.league, args.player, season)
        print(f"\n  {args.player} -> athlete {athlete or '(not found)'} at "
              f"{club or '-'}")
    found = espn_soccer.probe_athlete(
        args.league, athlete_id=athlete, season=season, club=club,
        dump_dir=args.dump)
    if athlete and season:
        found["compared"] = espn_soccer.compare_roster_and_overview(
            args.league, athlete, season)

    lines = [
        f"ESPN soccer athlete probe -- {found['league']} "
        f"({found['path']})",
        "",
        f"  athlete   {found.get('athlete_id') or '(none found)'}",
        f"  club      {found.get('club') or '-'}",
        f"  season    {found.get('season') or '(not asked for)'}",
    ]
    if found.get("problem"):
        lines += ["", f"  {found['problem']}"]
    for label, entry in found.get("shapes", {}).items():
        lines += ["", f"  {label}", f"      {entry['url']}"]
        if entry.get("params"):
            lines.append(f"      params {entry['params']}")
        if entry.get("error"):
            lines.append(f"      refused: {entry['error']}")
            continue
        lines.append(f"      top-level keys: {', '.join(entry.get('keys') or []) or '-'}")
        for shape in entry.get("splits_by") or []:
            lines.append(f"      {shape}")
        lines += _overview_lines(entry.get("overview") or {})
        comps = entry.get("competitions") or []
        lines.append(f"      competitions named ({len(comps)}): "
                     f"{', '.join(comps) or 'none'}")
        if len(comps) > 1:
            lines.append("      ^ more than one, so this shape can tell them apart")
        lines += _gamelog_lines(entry.get("gamelog") or {})
    if found.get("by_path"):
        lines += _by_path_lines(found["by_path"])
    if found.get("compared"):
        lines += _compared_lines(found["compared"])
    lines += ["", "  What to look for: a shape naming more than one competition is one",
              "  that knows which match a figure came from. That is the one to read",
              "  a player's season from, instead of inheriting the request's league.",
              ""]
    text = "\n".join(lines)
    print(text)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(text + "\n")
        print(f"  written to {args.out}\n")
    return 0


def cmd_check_attribution(args: argparse.Namespace) -> int:
    """Check each rostered club-soccer player's competitions against his club's.

    A player reaches this project as a season aggregate per competition, and the
    aggregate carries whatever ESPN scoped the request to. That scoping wobbles:
    Bayern's Bundesliga roster returned Harry Kane with his Champions League
    match among his appearances, so it was counted as domestic football and held
    as a European bonus on the same night, and was gone again the next.

    The club's results never wobbled, because they are read from a scoreboard
    walk where every match is fetched under the competition it was played in.
    So the club is the authority and the player's gamelog is the link: both
    carry ESPN's own match id, which makes the join exact rather than a
    date-and-name guess.

    One request per rostered player. Reports and changes nothing -- what to do
    about a disagreement is a scoring decision, and it should be made against a
    week of these rather than the first one.
    """
    from whul import attribution
    from whul import resolve as resolver
    from whul.benchmark_sources import PLAYER_LEAGUES
    from whul.sources import espn, espn_soccer
    from whul.store import open_store

    store = open_store(args.db)
    rostered = resolver.rostered_assets(store, args.season, "Player")
    mine = rostered[rostered["league"].isin(PLAYER_LEAGUES)]
    if args.league:
        mine = mine[mine["league"] == args.league]
    if mine.empty:
        print(f"\nNo club-soccer players rostered in {args.season}.\n")
        return 0

    season = int(args.feed_season) if args.feed_season else None
    print(f"\nChecking {len(mine)} rostered club-soccer player(s) against their "
          f"clubs' results.\n")

    # Read before the loop, because an empty gamelog means different things
    # depending on what the figures claim.
    stated = _rostered_player_figures(store, args.season, mine)
    club_results: dict[str, object] = {}
    attributed: dict[str, object] = {}
    club_matches: dict[str, object] = {}
    unreadable = 0
    nothing_claimed = 0
    unopened: dict[str, bool] = {}
    # One parsed lineup per tie, shared across every rostered player at that
    # club. Chelsea's two ties are two requests whether one player is rostered
    # there or three.
    lineups: dict[str, object] = {}
    for row in mine.itertuples():
        key = PLAYER_LEAGUES[str(row.league)]
        # A league whose season has not opened has no squads to search, and
        # ESPN says so with a 404 on every club. Thirty of those per player is
        # three hundred and thirty requests to be told nothing has happened,
        # and it reads as a broken feed rather than an empty calendar. The
        # ingest already declines to ask; so does this now.
        if key not in unopened:
            unopened[key] = not espn_soccer.season_has_begun(key, season)
            if unopened[key]:
                print(f"  {key}: no season inside this league year has opened "
                      f"yet, so there are no squads to search; its "
                      f"players are neither confirmed nor faulted")
        if unopened[key]:
            unreadable += 1
            continue
        if key not in club_results:
            # The clubs' results for the whole league, once, however many of
            # its players are rostered.
            try:
                club_results[key] = espn.load_soccer_matches(
                    key, [season], verbose=False)
            except Exception as exc:  # noqa: BLE001 -- one league, not the run
                print(f"  {key}: could not read the clubs' results "
                      f"({type(exc).__name__}); its players are skipped")
                club_results[key] = None
        saw: list = []
        failed: list = []
        athlete, club = espn_soccer.athlete_named(
            key, str(row.display_name), season, saw=saw, failed=failed)
        if not athlete:
            unreadable += 1
            # The squad names sharing his surname, so a spelling can be told
            # from an absence without another run. Named, never chosen.
            near = ("; nearest in the squads: "
                    + ", ".join(f"{n!r} ({c})" for n, c in saw[:4])
                    if saw else "")
            # And whether the search was even able to look everywhere.
            broke = (f"; {len(failed)} club squad(s) could not be read ("
                     + ", ".join(f"{c}: {e}" for c, e in failed[:3]) + ")"
                     if failed else "")
            print(f"  {row.display_name}: no athlete id in {key}{near}{broke}")
            continue
        matches = club_results[key]
        if matches is not None and not matches.empty and club:
            matches = matches[matches["team"].astype(str) == str(club)]
        events = espn_soccer.load_gamelog(key, athlete, season)
        if events.empty:
            # An empty gamelog and a figure of nothing agree. Balogun has not
            # played this season and his figures say none, so there is no
            # disagreement available to find and calling him unreadable
            # overstated what was actually unknown.
            claims = float(stated.get(str(row.display_name)) or 0.0)
            if claims <= attribution.TOLERANCE:
                nothing_claimed += 1
                print(f"  {row.display_name}: no matches this season, and the "
                      f"figures claim none either")
                continue
            # A figure claiming appearances against an empty gamelog is not
            # unanswerable, only unanswerable *from him*. Gozo's own ESPN
            # record is an EFL Trophy record with no appearances in it and no
            # Premier League gamelog at all -- but his club's matches happened,
            # and their lineups say whether he was in them. Every match, since
            # there is no gamelog to be missing competitions from.
            found = (espn_soccer.matches_he_played(
                        key, athlete, matches, season, seen=lineups)
                     if matches is not None and not getattr(matches, "empty", True)
                     else [])
            if not found:
                unreadable += 1
                print(f"  {row.display_name}: no gamelog for this season and "
                      f"his club's lineups do not name him, so the "
                      f"{claims:g} his figures claim cannot be confirmed")
                continue
            events = pd.DataFrame(found)
            print(f"      + {len(found)} appearance(s) read from his club's "
                  f"matches, which his own record does not carry")
        counted = attribution.attribute(events, matches)
        # The gamelog does not return the domestic cups, so a club that plays
        # one leaves its players unjudgeable -- the figure counts the cup tie
        # and the gamelog cannot say whether he was in it. The tie itself can.
        # Asked only for competitions the gamelog never mentioned, and once per
        # tie however many rostered players share the club.
        extra = _cup_appearances(key, athlete, counted, matches, season, lineups)
        if extra:
            events = pd.concat([events, pd.DataFrame(extra)], ignore_index=True)
            counted = attribution.attribute(events, matches)
            print(f"      + {len(extra)} cup appearance(s) read from the ties "
                  f"themselves, which the gamelog does not return")
        attributed[str(row.display_name)] = counted
        # His club's own matches, kept for the judgement below: they are the
        # only hard ceiling, and they say which competitions his gamelog was
        # silent about.
        club_matches[str(row.display_name)] = matches
        print(f"  {row.display_name} ({club or '?'}): "
              + ", ".join(f"{r.competition_key} {r.appearances:g}"
                          for r in counted.itertuples()))

    # What the stored figures counted, for the same players. Read from
    # `raw_stats` rather than re-pulled: the question is whether what was
    # recorded agrees with what the clubs played, and re-fetching would ask a
    # different night's answer.
    leagues = {str(r.display_name): str(r.league) for r in mine.itertuples()}
    found: list[dict] = []
    for player, counted in attributed.items():
        if player in stated:
            found += attribution.disagreements(
                counted, stated[player], player, leagues.get(player, ""),
                club_matches.get(player))

    lines = attribution.report(found)
    print()
    for line in lines or ["  Nothing claims more than its club played."]:
        print(line)
    if unreadable:
        print(f"\n  {unreadable} player(s) claim appearances that could not be "
              f"read, so they were neither confirmed nor faulted.")
    if nothing_claimed:
        print(f"  {nothing_claimed} player(s) have played nothing this season "
              f"and claim nothing, which agrees.")
    print()
    return 0


def _rostered_player_figures(store, season: str, rostered) -> dict:
    """``{player: counted appearances}`` from the most recent stored day.

    One number, not a breakdown, because that is what a season aggregate folded
    down to: `matches` is domestic football alone -- European appearances are
    held rather than counted -- which is exactly the figure the club's own
    matches can be checked against.
    """
    import json

    names = {str(r.asset_id): str(r.display_name) for r in rostered.itertuples()}
    if not names:
        return {}
    latest = store.query(
        "SELECT asset_id, stats FROM raw_stats WHERE season = ? AND as_of = "
        "(SELECT MAX(as_of) FROM raw_stats WHERE season = ?)", (season, season))
    out: dict = {}
    for row in latest.itertuples():
        who = names.get(str(row.asset_id))
        if who:
            out[who] = float(json.loads(row.stats).get("matches") or 0)
    return out


def cmd_rollup(args: argparse.Namespace) -> int:
    """Score every slot and write the standings snapshot -- the nightly job."""
    from datetime import date as _date

    from whul import pipeline
    from whul.store import open_store

    store = open_store(args.db)
    if args.backfill:
        reports = pipeline.backfill(store, args.season, verbose=False)
        if not reports:
            print("\nNothing to roll up: the season has not started.\n")
            return 0
        print(f"\nRebuilt {len(reports)} days\n  {reports[-1]}")
        warnings = {w for r in reports for w in r.warnings}
        produced = any(r.managers for r in reports)
    else:
        day = _date.fromisoformat(args.date) if args.date else _date.today()
        report = pipeline.roll_up(store, args.season, day)
        print(f"\n{report}")
        warnings = set(report.warnings)
        produced = bool(report.managers)

    # A backfill prints only its last day, so warnings raised on any other day
    # reached nobody -- they were gathered into a set and dropped on the floor.
    # An asset in two slots, scoring for two managers, is raised on the first.
    # The single-day path needs none of this: its own report is printed whole.
    if args.backfill and warnings:
        print("\n  Worth a look:")
        for warning in sorted(warnings):
            print(f"    ! {warning}")

    stale = store.stale_sources(_date.today())
    if not stale.empty:
        print("\n  Sources that have stopped updating:")
        for row in stale.itertuples():
            print(f"    {row.source}/{row.league}: last data {row.last_data_date}")
    print()
    # Only a run that produced nothing is a failure. A warning about an
    # overlapping slot or a stale feed is worth seeing, but publishing
    # yesterday's standings beats publishing none: failing the nightly build
    # over it would take the site down rather than let it go one day stale.
    return 0 if produced else 1


def cmd_fixtures(args: argparse.Namespace) -> int:
    """Which leagues have upcoming fixtures, and which rostered assets do not.

    The column on the roster page is blank wherever nothing is known, which is
    right and is also indistinguishable from a bug. This is where a reader
    finds out which it is.
    """
    from datetime import date as _date

    from whul import fixtures
    from whul.store import open_store

    store = open_store(args.db)
    as_of = _date.fromisoformat(args.date) if args.date else _date.today()

    if args.discover:
        from whul.sources import flashscore_fixtures as feed

        print("\nWhat each Flashscore sport id serves.\n")
        print("  Golf and motorsport are not fixtures -- a golfer's next event "
              "is a field and a\n  driver's is an entry list -- so the parser "
              "for those is written from this,\n  not guessed. Send me the "
              "output.\n")
        for row in feed.discover():
            print(f"  {row['sport']:>3}  {str(row['guess']):<20} {row['result']}")
            for header in row.get("headers", []) or []:
                print(f"           header: {header}")
            if row.get("fields"):
                print(f"           fields: {', '.join(row['fields'])}")
        return 0

    if args.probe:
        from whul.sources import flashscore_fixtures as feed

        def report(found: dict) -> None:
            for key, value in found.items():
                if isinstance(value, list):
                    print(f"  {key}:")
                    for item in value:
                        print(f"      {item}")
                else:
                    print(f"  {key:<22} {value}")

        for name, sport in (("soccer", feed.SPORT_SOCCER),
                            ("basketball", feed.SPORT_BASKETBALL),
                            ("baseball", feed.SPORT_BASEBALL),
                            ("hockey", feed.SPORT_HOCKEY),
                            ("tennis", feed.SPORT_TENNIS)):
            if args.sport and args.sport != name:
                continue
            print(f"\nFlashscore fixtures probe -- {name}\n")
            report(feed.probe(sport))

        # And the season pages, which fail separately: a different host, a
        # different response shape, and a schedule rendered in chunks. This is
        # where a league that opens in six weeks gets its fixtures, so a day
        # feed working perfectly says nothing about whether these do.
        for league, (sport, *_) in sorted(feed.SEASON_PAGES.items()):
            if args.sport and feed.SPORTS.get(league) != {
                "soccer": feed.SPORT_SOCCER, "basketball": feed.SPORT_BASKETBALL,
                "baseball": feed.SPORT_BASEBALL, "hockey": feed.SPORT_HOCKEY,
                "tennis": feed.SPORT_TENNIS,
            }.get(args.sport):
                continue
            print(f"\nFlashscore season page -- {league}\n")
            report(feed.probe_season(league))
        return 0

    if args.fetch:
        print("\n  Fetching upcoming fixtures from Flashscore ...\n")
        got = fixtures.from_flashscore(store, args.season, as_of)
        print(f"\n  {sum(got.values())} fixture row(s) recorded across "
              f"{len(got)} feed(s).\n")

    cover = fixtures.coverage(store, args.season)
    print(f"\n  Fixtures held for {args.season}, as of {as_of}:\n")
    if cover.empty:
        print("    none. No league has been pulled since fixtures were added,")
        print("    or every league's schedule feed carries results only.")
        print(missing_database_note(store))
        return 0
    print(f"    {'League':<20}{'Teams':>6}{'Fixtures':>10}  {'First':<12}{'Last':<12}")
    for row in cover.itertuples():
        print(f"    {row.league:<20}{row.teams:>6}{row.fixtures:>10}  "
              f"{str(row.first):<12}{str(row.last):<12}")

    # Which leagues this command can fetch, and which get theirs on the way
    # past during a pull. Separated because the failure looks identical from
    # the outside -- a blank column -- and the fix is a different command.
    from whul.sources import flashscore_fixtures as _feed

    held = set(cover["league"])
    rostered_leagues = {
        lg for lg in store.query(
            "SELECT DISTINCT a.league FROM roster_slots r "
            "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
            "JOIN assets a ON a.asset_id = o.asset_id WHERE r.season = ?",
            (args.season,),
        )["league"].astype(str) if lg
    }
    # A league's fixtures are recorded under whichever feed supplies them, so
    # "has this league got any" is asked of the feeds it may read rather than
    # of its own name: an F1 driver's next race is filed under Motorsports.
    def covered(lg: str) -> bool:
        return bool(fixtures.feeds_for(lg) & held)

    rides_along = set(fixtures.HARVESTED) | set(fixtures.TOUR)
    waiting = sorted(
        lg for lg in rostered_leagues & rides_along if not covered(lg)
    )
    uncovered = sorted(
        lg for lg in rostered_leagues
        if lg not in rides_along and lg not in _feed.SPORTS
    )
    if waiting:
        print(f"\n  Fixtures ride along with these leagues' own results pull, "
              f"and none have arrived:\n    {', '.join(waiting)}")
        print("\n    Run `python -m whul.cli ingest` -- --fetch reads Flashscore "
              "only, and\n    a schedule is harvested while it is being read "
              "for scoring.")
    if uncovered:
        print(f"\n  Nothing covers these yet, so their cells stay blank:"
              f"\n    {', '.join(uncovered)}")

    matched = fixtures.by_asset(store, args.season, as_of)
    rostered = store.query(
        "SELECT DISTINCT a.asset_id, a.display_name, r.category, a.asset_type "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id WHERE r.season = ? "
        "ORDER BY r.category, a.display_name",
        (args.season,),
    )
    blank = rostered[~rostered["asset_id"].isin(matched)]
    print(f"\n  {len(matched)} of {len(rostered)} rostered asset(s) have a next "
          f"fixture.")
    if not blank.empty:
        by_category = blank.groupby("category").size().sort_values(ascending=False)
        print("\n  No fixture, by category:\n")
        for category, count in by_category.items():
            print(f"    {category:<24}{count:>4}")
        print("\n    A blank is not always a gap: a league between seasons has "
              "nothing to\n    play next, and a tour names its field only once "
              "the event opens.")
    return 0


def cmd_images_needed(args: argparse.Namespace) -> int:
    """Every image file the site would use, and whether it is there yet.

    The naming rule is short enough to state in a sentence and long enough to
    get wrong eighty times running, so this states it once per file instead:
    the exact directory, the exact name, and what belongs in it. Fill what you
    can and leave the rest; a missing file renders as a monogram, which is a
    finished answer rather than a hole.

    Grouped by directory and listing only what is absent, because the question
    being asked is "what do I do this evening", not "what does the site hold".
    """
    from pathlib import Path

    from whul.site import images
    from whul.site.build import INDIVIDUAL_CATEGORIES, _slug, badge_names
    from whul.store import open_store

    store = open_store(args.db)
    rows = store.query(
        "SELECT DISTINCT a.asset_id, a.asset_type, a.display_name, a.league, "
        "       a.affiliation, r.category "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE r.season = ? ORDER BY r.category, a.display_name",
        (args.season,),
    )
    if rows.empty:
        print(f"\nNothing rostered in {args.season}.")
        print(missing_database_note(store))
        return 1

    # What each asset is badged with, the feed's spelling winning over the
    # sheet's. Named here rather than read off `affiliation` directly so the
    # filename this asks for is the one the fetch writes -- they were two
    # readings of the same thing, and the crest a sheet called "Los Angeles
    # Clippers" and ESPN calls "LA Clippers" fell down the gap.
    badges = badge_names(store, args.season)

    # Keyed, so the club twenty players share is one file rather than twenty.
    # That collapse is most of the difference between a long evening and a
    # short one, and it is invisible in a list of players.
    wanted: dict[tuple[str, str], str] = {}
    for row in rows.itertuples():
        name = str(row.display_name)
        wanted[("asset", str(row.asset_id))] = (
            f"{name} -- {'headshot' if row.asset_type == 'Player' else 'team logo'}"
        )
        affiliation = badges.get(str(row.asset_id), "")
        if row.asset_type == "Team":
            # A national side takes its confederation's shield, not a league.
            if "Intl" not in str(row.category):
                wanted[("badge", _slug(str(row.league)))] = f"{row.league} -- league logo"
        elif str(row.category) in INDIVIDUAL_CATEGORIES:
            if affiliation:
                wanted[("flag", _slug(affiliation))] = f"{affiliation} -- flag"
        elif affiliation:
            wanted[("club", _slug(affiliation))] = f"{affiliation} -- club crest"

    for confederation in ("uefa", "conmebol", "concacaf", "caf", "afc", "ofc"):
        wanted[("shield", confederation)] = f"{confederation.upper()} -- shield"

    # Where it looked, said out loud, always.
    #
    # `assets/img` is a relative path, so run from anywhere but the repository
    # root it resolves to nothing and every single file reads as missing --
    # which is indistinguishable from having fetched none of them, and was
    # read that way. A stale clone does the same thing. Printing the directory
    # and whether it exists turns a wrong answer into an obvious one.
    source = Path(args.images) if args.images else images.SOURCE_DIR
    missing = [(kind, key, what) for (kind, key), what in sorted(wanted.items())
               if images.find(kind, key, source=source) is None]

    print(f"\n  Looking in {source.resolve()}")
    if not source.is_dir():
        print("  ...which does not exist, so everything below reads as missing.")
        print("  Run this from the repository root, or pass --images.")
    print(f"\n  {len(wanted) - len(missing)} of {len(wanted)} image(s) present; "
          f"{len(missing)} to add.")
    print("  Any of .png .jpg .jpeg .webp .svg. A missing one is a monogram,")
    print("  so there is no wrong order and no need to finish.\n")

    kind = None
    for this_kind, key, what in missing:
        if this_kind != kind:
            kind = this_kind
            print(f"  --- {images.SOURCE_DIR}/{kind}/")
        # The accent-folded spelling, because that is the one a person can
        # type. `find` accepts either.
        print(f"    {images.plain(key) + '.png':<50}{what}")

    # A file that is present and unreadable is worse than one that is absent:
    # both render as a monogram, and only the absent one is reported anywhere.
    # An SVG named .png is served as image/png and refused outright; a WebP
    # named .png survives on browser sniffing, which is luck rather than
    # design. Both are one `git mv` from correct, so both are named here.
    wrong = images.mislabelled(source)
    if wrong:
        print(f"\n  {len(wrong)} file(s) are not the format their name claims. "
              f"These are\n  present, so nothing above lists them, and they may "
              f"render as nothing at\n  all -- an SVG named .png is refused by "
              f"the browser outright. Rename:\n")
        for path, claimed, actual in wrong:
            print(f"    {str(path):<50} .{claimed} -> .{actual}")
        print(f"\n    (`git mv` each one; the site accepts any of "
              f"{' '.join(images.EXTENSIONS)}.)")

    blank = sorted(
        str(r.display_name) for r in rows.itertuples()
        if r.asset_type == "Player" and not badges.get(str(r.asset_id), "")
    )
    if blank:
        print(f"\n  {len(blank)} player(s) have no club or country in the "
              f"spreadsheet,")
        print("  so this cannot name a corner badge for them:")
        for name in blank[:15]:
            print(f"    {name}")
        if len(blank) > 15:
            print(f"    ... and {len(blank) - 15} more")
    print()
    return 0


def cmd_site(args: argparse.Namespace) -> int:
    """Generate the static site from whatever the store holds."""
    from pathlib import Path as _Path

    from whul.site.build import build
    from whul.store import open_store

    store = open_store(args.db)
    try:
        result = build(store, args.season, _Path(args.out))
    except ValueError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    print(f"\nBuilt {result['pages']} pages in {result['out']}/\n")
    for key in ("season", "as_of", "managers", "days", "profiles"):
        print(f"  {key:<10} {result[key]}")
    photos = result.get("photos", {})
    supplied = ", ".join(f"{k} {v}" for k, v in photos.items() if v)
    print(f"  {'images':<10} {supplied or 'none yet — monograms in use'}")
    if result["simulated"]:
        what = {"scores_only": "scores only; rosters are real",
                "everything": "rosters and scores"}.get(
            result["simulated"], str(result["simulated"]))
        print(f"\n  Simulated: {what} -- every page says so.")
    print(f"\nOpen {result['out']}/index.html, or serve it with:")
    print(f"  python -m http.server -d {result['out']} 8000")
    return 0


def _benchmark_store(args: argparse.Namespace):
    from whul.store import open_store

    return open_store(args.db)


def cmd_feed_names(args: argparse.Namespace) -> int:
    """What a live feed is calling things right now.

    A rostered asset that matches nothing is either absent from the feed or
    spelled differently in it, and those need opposite fixes. Only the feed can
    say which.
    """
    from datetime import date as _date

    from whul import ingest as ingest_module
    from whul import resolve as resolver
    from whul.benchmark_sources import resolve as resolve_sources
    from whul.store import open_store

    try:
        source = resolve_sources([args.league])[0]
    except KeyError as exc:
        print(f"\n{exc.args[0]}\n", file=sys.stderr)
        return 2

    as_of = _date.fromisoformat(args.date) if args.date else _date.today()
    try:
        scored = ingest_module._pull(source, as_of, verbose=True)
    except Exception as exc:  # noqa: BLE001 -- a diagnostic reports, it does not raise
        print(f"\n  could not pull: {type(exc).__name__}: {exc}\n", file=sys.stderr)
        return 1
    if scored is None or scored.empty:
        print(
            f"\n  {source.league}: the feed returned nothing at all for this "
            f"season, so no name could match. That is a source problem, not a "
            f"spelling one.\n"
        )
        return 1

    column, _ = resolver._name_columns(scored, source.asset_type)
    names = sorted({str(n) for n in scored[column] if str(n).strip()})
    print(f"\n  {source.league}: {len(scored)} rows, {len(names)} distinct names\n")

    store = open_store(args.db)
    rostered = resolver.rostered_assets(store, args.season, source.asset_type)
    wanted = set(ingest_module._leagues_of(source))
    mine = rostered[rostered["league"].isin(wanted)]

    shape = resolver.normalize_team if source.asset_type == "Team" else resolver.normalize_name
    keyed = {shape(n): n for n in names}
    for asset in mine.itertuples():
        key = shape(asset.display_name)
        mark = "ok  " if key in keyed else "MISS"
        near = [n for n in names if key.split()[-1] in shape(n).split()] if mark == "MISS" else []
        note = f"   feed has: {', '.join(near[:3])}" if near else ""
        print(f"  {mark}  {asset.display_name}{note}")

    if args.all:
        print("\n  every name the feed carries:")
        for name in names:
            print(f"    {name}")
    else:
        print(f"\n  {len(names)} names in the feed; --all to list them.")
    print()
    return 0


def cmd_alias(args: argparse.Namespace) -> int:
    """Link a feed's name to a rostered asset, by hand.

    The resolver refuses to guess -- two people with one name, or a suffix that
    disagrees, are reported rather than linked. This is how a person settles
    one, and the link is permanent: it wins over re-deriving the match on every
    run after.
    """
    from whul import resolve as resolver
    from whul.store import open_store
    from whul.store.db import _now

    store = open_store(args.db)
    assets = resolver.rostered_assets(store, args.season)
    if assets.empty:
        print(f"\nNothing rostered in {args.season}.", file=sys.stderr)
        print(missing_database_note(store), file=sys.stderr)
        return 1

    wanted = resolver.normalize_name(args.asset)
    hits = assets[
        assets["display_name"].map(resolver.normalize_name) == wanted
    ] if not args.asset_id else assets[assets["asset_id"] == args.asset_id]

    if hits.empty:
        near = [
            n for n in assets["display_name"]
            if args.asset.lower() in str(n).lower()
        ][:8]
        print(f"\nNo rostered asset called {args.asset!r}.", file=sys.stderr)
        if near:
            print(f"  Did you mean: {', '.join(near)}", file=sys.stderr)
        print(file=sys.stderr)
        return 1
    if len(hits) > 1:
        print(f"\n{args.asset!r} matches {len(hits)} rostered assets; "
              f"pass --asset-id to say which:", file=sys.stderr)
        for row in hits.itertuples():
            print(f"  {row.asset_id}  {row.display_name} ({row.league})", file=sys.stderr)
        print(file=sys.stderr)
        return 1

    asset = hits.iloc[0]
    store.upsert("asset_aliases", [{
        "source": args.source, "source_key": args.feed_name,
        "asset_id": asset["asset_id"], "match_kind": "manual",
        "needs_review": 0, "created_at": _now(),
    }], keys=("source", "source_key"))
    print(
        f"\n  {args.source}: {args.feed_name!r} -> {asset['display_name']} "
        f"({asset['league']})"
    )
    print("  It will be used from the next ingest on.\n")
    return 0


def _ingest_one_day(ingest_module, store, sources, season, as_of,
                    spent: dict | None = None, hold: bool = True,
                    today=None) -> list:
    """One day's pull across every source asked for."""
    from whul import meter

    print(f"\nIngesting {season} as of {as_of}.\n")
    reports = []
    for source in sources:
        with meter.measure() as spend:
            report = ingest_module.ingest(store, source, season, as_of,
                                          hold=hold, today=today)
        if spent is not None:
            spent[source.key] = spent.get(source.key, meter.Spend()) + spend
        _record_cost(store, season, as_of, source.key, spend)
        reports.append(report)
        # A skipped league is noise when every league is being tried; a league
        # that actually did something, or failed at something, is not.
        if report.pulled or report.problems != [
                "nothing rostered in this league; skipped"]:
            print(f"{report}  [{_cost(spend)}]\n")
    return reports


def _cost(spend) -> str:
    """One source's spend, short enough to sit at the end of its line."""
    if not spend.requests:
        return f"{spend.seconds:,.1f}s, no request seen"
    return (f"{spend.seconds:,.1f}s: {spend.waiting:,.1f}s waiting on "
            f"{spend.requests} request(s), {spend.pausing:,.1f}s pausing, "
            f"{spend.other:,.1f}s other")


def _record_cost(store, season: str, as_of, key: str, spend) -> None:
    """Keep the reading, so the question can be about the trend.

    Never fatal. A pull that worked and could not write down what it cost is a
    pull that worked, and taking the run down over an instrument would be the
    instrument costing more than it measures.
    """
    try:
        store.upsert("ingest_timings", [{
            "season": season,
            "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
            "source": key,
            "seconds": round(spend.seconds, 3),
            "requests": spend.requests,
            "waiting": round(spend.waiting, 3),
            "pausing": round(spend.pausing, 3),
        }], keys=("season", "as_of", "source"))
        store.conn.commit()
    except Exception as exc:  # noqa: BLE001 -- an instrument must not stop a run
        print(f"  (could not record what {key} cost: "
              f"{type(exc).__name__}: {exc})", flush=True)


#: How many sources to name in the timing line. Enough to see the shape and
#: few enough that it stays one glance.
SLOWEST_SHOWN = 8


def cmd_pull_costs(args: argparse.Namespace) -> int:
    """What the pull has cost per source, and which way it is heading.

    One night's timings name the expensive feeds. Two months of them answer
    the question actually worth asking, which is whether a feed that costs
    forty seconds in September costs four minutes in March -- and whether it
    got there by waiting longer or by working harder, because those have
    different fixes.
    """
    from whul.store import open_store

    store = open_store(args.db)
    rows = store.query(
        "SELECT as_of, source, seconds, requests, waiting, pausing "
        "FROM ingest_timings WHERE season = ? ORDER BY as_of, source",
        (args.season,)
    )
    if rows.empty:
        print(f"\nNothing recorded for {args.season}. The timings are written "
              f"by `ingest`,\nso the first reading arrives with the next pull.\n")
        return 0

    days = sorted(rows["as_of"].unique())
    if args.source:
        one = rows[rows["source"] == args.source]
        if one.empty:
            known = ", ".join(sorted(rows["source"].unique()))
            print(f"\nNo readings for {args.source!r}. Recorded: {known}\n",
                  file=sys.stderr)
            return 2
        print(f"\n{args.source} in {args.season}, day by day:\n")
        print(f"  {'day':12s} {'total':>8s} {'waiting':>9s} {'pausing':>9s} "
              f"{'work':>8s} {'reqs':>5s}")
        for row in one.itertuples():
            other = max(0.0, float(row.seconds) - float(row.waiting)
                        - float(row.pausing))
            print(f"  {row.as_of:12s} {row.seconds:7,.1f}s {row.waiting:8,.1f}s "
                  f"{float(row.pausing):8,.1f}s {other:7,.1f}s "
                  f"{int(row.requests):5d}")
        print()
        return 0

    # The newest day against one far enough back to show a trend rather than
    # a Tuesday. Fewer days recorded than asked for is not a problem: the
    # earliest there is is the earliest there is, and the header says which.
    latest = days[-1]
    earliest = days[max(0, len(days) - args.days)]
    now = rows[rows["as_of"] == latest].set_index("source")
    then = rows[rows["as_of"] == earliest].set_index("source")

    print(f"\n  {args.season}: {latest}, against {earliest} "
          f"({len(days)} day(s) recorded).\n")
    print(f"  {'source':16s} {'total':>8s} {'waiting':>9s} {'pausing':>9s} "
          f"{'work':>8s} {'reqs':>5s}   {'since ' + earliest[5:]:>10s}")
    order = now["seconds"].sort_values(ascending=False)
    for source in order.index:
        row = now.loc[source]
        other = max(0.0, float(row["seconds"]) - float(row["waiting"])
                    - float(row["pausing"]))
        if source in then.index:
            was = float(then.loc[source, "seconds"])
            moved = f"{float(row['seconds']) - was:+,.1f}s" if was else "new"
        else:
            moved = "new"
        print(f"  {source:16s} {float(row['seconds']):7,.1f}s "
              f"{float(row['waiting']):8,.1f}s {float(row['pausing']):8,.1f}s "
              f"{other:7,.1f}s {int(row['requests']):5d}   {moved:>10s}")
    total = float(now["seconds"].sum())
    before = float(then["seconds"].sum())
    print(f"\n  {total:,.0f}s in total, against {before:,.0f}s on {earliest} "
          f"({total - before:+,.0f}s).")
    if _unmeasured_pauses(then):
        # The pausing column arrived after the first readings did. Those days
        # put their politeness pauses in `work`, so the first comparison
        # across the changeover shows work collapsing and pausing appearing,
        # having done neither. Only the total is comparable there.
        print(f"  {earliest} was recorded before pausing was measured, so its "
              f"pauses are\n  inside its `work`. Compare the totals across "
              f"that day, not the columns.")
    print()
    return 0


def _unmeasured_pauses(day) -> bool:
    """Whether a day's readings predate the pausing column.

    A source that made no request genuinely paused for nothing, so the tell is
    a day that fetched and still reports no pause anywhere -- which no real
    pull does, every feed here serving itself one.
    """
    if day.empty:
        return False
    return bool(day["requests"].sum() > 0 and float(day["pausing"].sum()) == 0.0)


def _say_where_the_time_went(spent: dict) -> None:
    """Which sources the pull spent itself on, and on what.

    The nightly job takes ten minutes and a backfill takes forty, and until
    this nothing said which of the twenty-four feeds that was. A run that
    cannot say where its time goes cannot be made faster except by guessing,
    and the guesses have been wrong before -- the fixture pull looks expensive
    and is forty seconds of the eighteen minutes.

    The split matters as much as the total, because the three columns want
    different things. Waiting on a slow host falls when it is asked alongside
    a different one. Pausing -- the politeness a source serves itself between
    requests -- falls when there are fewer requests to pay for, or when two
    hosts pay at once. What is left is work, and work falls only when it
    stops being done twice. A total says which source to look at; the split
    says what to do when you get there.
    """
    if not spent:
        return
    whole = sum(s.seconds for s in spent.values())
    ranked = sorted(spent.items(), key=lambda kv: kv[1].seconds, reverse=True)
    print(f"\n  {whole:,.0f}s in the feeds. Slowest:")
    print(f"    {'source':16s} {'total':>8s} {'waiting':>9s} {'pausing':>9s} "
          f"{'work':>8s} {'reqs':>5s}   share")
    for key, spend in ranked[:SLOWEST_SHOWN]:
        share = 100.0 * spend.seconds / whole if whole else 0.0
        flag = "  (no request seen)" if spend.blind else ""
        print(f"    {key:16s} {spend.seconds:7,.1f}s {spend.waiting:8,.1f}s "
              f"{spend.pausing:8,.1f}s {spend.other:7,.1f}s "
              f"{spend.requests:5d}  {share:4.1f}%{flag}")
    rest = [spend for _, spend in ranked[SLOWEST_SHOWN:]]
    if rest:
        total = sum(s.seconds for s in rest)
        print(f"    {'the rest':16s} {total:7,.1f}s "
              f"{sum(s.waiting for s in rest):8,.1f}s "
              f"{sum(s.pausing for s in rest):8,.1f}s "
              f"{sum(s.other for s in rest):7,.1f}s "
              f"{sum(s.requests for s in rest):5d}  "
              f"{100.0 * total / whole if whole else 0:4.1f}%")
    if any(spend.blind for spend in spent.values()):
        print("    A source with no request seen either replayed a cache or "
              "reached the\n    network by a route the meter does not wrap. "
              "Its cost is in `other`\n    either way, where it reads as "
              "parsing and may not be.")


def cmd_ingest(args: argparse.Namespace) -> int:
    """Pull today's results for the live leagues and record them."""
    from datetime import date as _date, timedelta

    from whul import ingest as ingest_module
    from whul.benchmark_sources import resolve
    from whul.store import open_store

    try:
        sources = resolve(args.leagues)
    except KeyError as exc:
        print(f"\n{exc.args[0]}\n", file=sys.stderr)
        return 2

    store = open_store(args.db)
    as_of = _date.fromisoformat(args.date) if args.date else _date.today()
    days = [as_of]
    if getattr(args, "since", None):
        # Every stored day from `since`, rewritten from what is known now.
        # A correction to the history -- a duplicate found in the ledger, a
        # name two feeds spelled differently -- fixes today and leaves every
        # earlier day holding the figure it was given at the time, and the
        # standings ledger differences consecutive days, so the correction
        # reads as a loss on the day it was made. Restating does not touch
        # this: it rescores what is stored against the frozen scale, and a
        # day stored wrong stays wrong at whatever scale it is rescored
        # against.
        first = _date.fromisoformat(args.since)
        if first > as_of:
            print(f"\n--since {first} is after {as_of}.\n", file=sys.stderr)
            return 2
        days = [first + timedelta(days=n) for n in range((as_of - first).days + 1)]

    # A figure that has gone backwards is normally held at the last whole one,
    # because a win cannot be un-won. A restatement is the exception: the day
    # before the range is one of the days being corrected, so holding against
    # it would pin the figure the restatement exists to remove.
    hold = not getattr(args, "since", None)
    if not hold:
        print("\n  Restating, so a figure that falls is written rather than "
              "held: the days before this range are the ones being corrected.")

    reports = []
    spent: dict = {}   # source key -> meter.Spend
    for day in days:
        if len(days) > 1:
            print(f"\n--- {day} ---", flush=True)
        reports.extend(_ingest_one_day(
            ingest_module, store, sources, args.season, day, spent, hold=hold,
            today=as_of))

    scored = sum(r.scored for r in reports)
    recorded = sum(r.recorded for r in reports)
    unmatched = [
        (name, league) for r in reports if r.resolution
        for name, league in r.resolution.unmatched
    ]
    inferred = [
        pair for r in reports if r.resolution
        for pair in (*r.resolution.extra_names, *r.resolution.reordered)
    ]
    print(f"{recorded} raw rows recorded, {scored} scored.")
    left = [r for r in reports if getattr(r, "skipped", False)]
    if left:
        # Named, because "nothing recorded" and "nothing needed recording" look
        # the same in a row count and are opposite answers.
        leagues = sorted({r.league for r in left})
        print(f"\n  {len(left)} pull(s) left the stored day as it was, in "
              f"{', '.join(leagues)}:")
        print("  a feed that reports a season to date cannot say what was true "
              "on a day\n  that has gone, and the day already has the answer it "
              "was given at the time.")
    _say_where_the_time_went(spent)
    if inferred:
        # Inferred, not read: worth a glance the first time each one appears.
        print(f"\n  {len(inferred)} matched on a partial name:")
        for name, feed_name in inferred:
            print(f"    {name} -> {feed_name}")
    if unmatched:
        print(
            f"\n  {len(unmatched)} rostered asset(s) matched no feed row and will "
            f"score nothing until they do:"
        )
        for name, league in unmatched[:20]:
            print(f"    {name} ({league})")
        if len(unmatched) > 20:
            print(f"    ... and {len(unmatched) - 20} more")
        print("\n  A name the feed spells differently is fixable in the alias table;")
        print("  a player who has not appeared yet will match itself once they do.")

    # The failure a league at a time cannot see: a league nobody asked for.
    # Every source can succeed and every asset match, and a whole league still
    # score nothing, because it was never in the list.
    missed = ingest_module.uncovered(store, args.season, sources)
    left_out = missed[missed["source"] != ""] if not missed.empty else missed
    unsourced = missed[missed["source"] == ""] if not missed.empty else missed
    if not left_out.empty:
        total = int(left_out["assets"].sum())
        print(
            f"\n  {total} rostered asset(s) scored nothing because their league "
            f"was not in this pull:"
        )
        for row in left_out.itertuples():
            print(f"    {row.league} {row.asset_type.lower()}s ({row.assets}) "
                  f"-- add `{row.source}`: {row.names}")
        print("\n  Out of season is a fair reason to leave one out. Nothing here")
        print("  failed, so nothing else will mention them.")
    if not unsourced.empty:
        total = int(unsourced["assets"].sum())
        print(f"\n  {total} rostered asset(s) have no source at all yet:")
        for row in unsourced.itertuples():
            print(f"    {row.league} {row.asset_type.lower()}s "
                  f"({row.assets}): {row.names}")
    print()
    # A restatement rewrites a range and leaves everything before it alone, so
    # the first day of the range now sits against a day scored from worse
    # information. Where the correction went downwards -- which is what a
    # premature credit or a double count leaves behind -- that join is the
    # whole correction expressed as a loss on one morning, and the run must
    # not report success having created it.
    if not hold and _refuse_a_restatement_that_left_a_cliff(
            store, args.season, days[0]):
        return 1
    return 0 if scored or recorded else 1


def cmd_benchmarks_list(_: argparse.Namespace) -> int:
    """What can be benchmarked, in the order a full run would do it."""
    from whul.benchmark_sources import resolve

    print(f"\n  {'key':<14}{'league':<20}{'assets':<9}{'pooled':<8}{'status':<12}notes")
    for source in resolve(None):
        print(
            f"  {source.key:<14}{source.league:<20}{source.asset_type.lower() + 's':<9}"
            f"{'window' if source.windowed else 'season':<8}"
            f"{source.reliability:<12}{source.note}"
        )
    from whul.benchmark_sources import ORDER, SOURCES

    # Subsets of a source above, so a full run must not include them: it would
    # compute the same group twice. Named here because they are what a person
    # dispatching a recompute of one league actually wants.
    subsets = [SOURCES[key] for key in sorted(SOURCES) if key not in ORDER]
    if subsets:
        print("\n  Recomputing one group only -- name these instead, never "
              "alongside the source they come from:")
        for source in subsets:
            print(
                f"  {source.key:<20}{source.league:<20}"
                f"{source.asset_type.lower() + 's':<9}{source.note}"
            )

    print("\n  Cheap and verified first, so a failure late in the list still")
    print("  leaves a reviewable set of the leagues that did work.")
    print("  `window` means the pool is drawn over the season's own Aug-Jul")
    print("  window rather than calendar seasons -- see PROJECT_PLAN 2.3.\n")
    return 0


def cmd_benchmarks_compute(args: argparse.Namespace) -> int:
    """Pull, score and take the percentile. Writes nothing unless --save."""
    from whul import benchmarks
    from whul.benchmark_sources import resolve
    from whul.store import benchmarks as store_benchmarks

    try:
        sources = resolve(args.leagues)
    except KeyError as exc:
        print(f"\n{exc.args[0]}\n", file=sys.stderr)
        return 2

    if args.seasons != DEFAULT_SEASONS:
        print(f"\nComputing benchmarks from {args.seasons} seasons per league.\n")
    else:
        print("\nComputing benchmarks from each league's own pool depth.\n")
    runs = []
    for source in sources:
        load, score = source.build()
        # Each league's own pool depth, unless the caller named one. Eight
        # seasons is right for international football and wrong for club
        # soccer, where it reaches back past the pandemic; asking a person to
        # remember which is asking for the wrong number.
        seasons = (
            args.seasons if args.seasons != DEFAULT_SEASONS
            else (source.benchmark_seasons or DEFAULT_SEASONS)
        )
        if source.windowed:
            # A continuously running sport is benchmarked over the league
            # year's own window, so --latest (a calendar year) does not apply.
            run = benchmarks.compute_windowed(
                source.league, load, score,
                produces=source.produces, seasons=seasons,
            )
        else:
            run = benchmarks.compute(
                source.league, load, score,
                asset_type=source.asset_type,
                seasons=seasons,
                latest=args.latest,
                scale_for=source.scale_for,
            )
        runs.append(run)
        print(f"\n{run}")

    ok = [r for r in runs if r.benchmarks is not None and not r.benchmarks.empty]
    failed = [r for r in runs if r not in ok]
    print(f"\n{len(ok)} of {len(runs)} computed.")
    for run in failed:
        print(f"  FAILED {run.league} {run.asset_type.lower()}s: {'; '.join(run.problems)}")

    if args.csv and ok:
        frame = pd.concat(
            [r.benchmarks.assign(league=r.league, seasons_used=len(r.used)) for r in ok],
            ignore_index=True,
        )
        frame.to_csv(args.csv, index=False)
        print(f"\n  wrote {args.csv}")

    if not args.save:
        print("\nNothing written. Re-run with --save to store this as a version,")
        print("then `benchmarks freeze <version>` to score against it.\n")
        return 0 if ok else 1

    store = _benchmark_store(args)
    if args.into:
        target = args.into
        if target == LATEST_DRAFT:
            draft = store_benchmarks.latest_draft(store, args.season)
            if draft is None:
                print(
                    f"\nNo unfrozen version for {args.season} to add to. Drop --into "
                    f"to start one.\n",
                    file=sys.stderr,
                )
                return 1
            target = draft.version
            print(f"\n  adding to the version still being built: {target}")
        try:
            version = benchmarks.extend(store, runs, target, notes=args.notes)
        except (ValueError, store_benchmarks.FrozenBenchmarkError) as exc:
            print(f"\n{exc}\n", file=sys.stderr)
            return 1
        if version is None:
            print("\nNothing to add.\n", file=sys.stderr)
            return 1
        groups = len(store_benchmarks.load(store, version))
        print(f"\n  added to version {version}, now {groups} groups (still unfrozen)")
    else:
        version = benchmarks.save(store, runs, args.season, notes=args.notes)
        if version is None:
            print("\nNothing to save.\n", file=sys.stderr)
            return 1
        print(f"\n  saved version {version} (unfrozen)")

    previous = args.compare
    if previous:
        diff = store_benchmarks.compare(store, previous, version)
        print(f"\n  against {previous}:")
        _print_benchmark_diff(diff)

    print(f"\n  Check what it still needs: benchmarks coverage {version}")
    print(f"  Add another league to it:  benchmarks compute <league> --save --into {version}")
    print(f"  Adopt it:                  benchmarks freeze {version}\n")
    # A league that failed leaves its group inherited from whatever was copied
    # into this draft, which is a number that looks computed and is not. Saving
    # the leagues that worked is right; exiting 0 on top of it is not, because
    # a green run is how a person decides the draft is ready to freeze.
    if failed:
        print(f"  {len(failed)} league(s) above did not compute. Their groups still",
              file=sys.stderr)
        print("  hold whatever this draft was derived from -- fix and re-run "
              "before freezing.\n", file=sys.stderr)
        return 1
    return 0


def _print_benchmark_diff(diff) -> None:
    """What adopting a draft would do, group by group.

    The kind is a column of its own because a group name does not identify a
    group: club soccer normalizes players and clubs against the same six league
    names, so a comparison listing "Premier League" twice with different
    numbers and nothing between them is one a reader has to guess at. A review
    that cannot be read is a review that gets skipped.
    """
    if diff.empty:
        print("    (nothing in common)")
        return
    kinds = {"Team": "teams", "Player": "players"}
    print(f"    {'group':<24}{'kind':<9}{'before':>11}{'after':>11}{'change':>9}")
    for row in diff.itertuples():
        before = f"{row.before:,.1f}" if pd.notna(row.before) else "--"
        after = f"{row.after:,.1f}" if pd.notna(row.after) else "--"
        change = f"{row.change_pct:+.1f}%" if pd.notna(row.change_pct) else "new"
        kind = kinds.get(str(row.asset_type), str(row.asset_type).lower())
        print(f"    {row.norm_key:<24}{kind:<9}{before:>11}{after:>11}{change:>9}")
    moved = diff["change_pct"].abs()
    if moved.notna().any():
        print(
            f"\n    Every score in a group moves by that group's percentage. "
            f"Largest: {moved.max():.1f}%."
        )


def cmd_benchmarks_versions(args: argparse.Namespace) -> int:
    store = _benchmark_store(args)
    # The count is the column that was missing. A version holding no benchmarks
    # reads exactly like a full one here, and everything downstream treats it as
    # a scale that covers nothing -- so `deploy.sh` reported an empty version as
    # one that "would score fewer leagues than the version already in force",
    # which sends you to look at the benchmark rather than at the version.
    rows = store.query(
        "SELECT v.version, v.season, v.quantile, v.managers, v.computed_at, "
        "       v.frozen_at, v.notes, COUNT(b.version) AS groups "
        "FROM benchmark_versions v "
        "LEFT JOIN benchmarks b ON b.version = v.version "
        "GROUP BY v.version ORDER BY v.computed_at DESC"
    )
    if rows.empty:
        print("\nNo benchmark versions yet. Start with `benchmarks compute`.\n")
        return 0
    print(f"\n  {'version':<26}{'season':<12}{'state':<10}{'groups':>7}  notes")
    for row in rows.itertuples():
        state = "FROZEN" if row.frozen_at else "draft"
        empty = "  <- holds nothing" if not row.groups else ""
        print(f"  {row.version:<26}{row.season:<12}{state:<10}{row.groups:>7}  "
              f"{row.notes or ''}{empty}")
    print()
    return 0


def cmd_benchmarks_compare(args: argparse.Namespace) -> int:
    from whul.store import benchmarks as store_benchmarks

    store = _benchmark_store(args)
    for version in (args.left, args.right):
        if store_benchmarks.get_version(store, version) is None:
            print(f"\nNo benchmark version {version!r}.\n", file=sys.stderr)
            return 1
    print(f"\n  {args.left} -> {args.right}\n")
    _print_benchmark_diff(store_benchmarks.compare(store, args.left, args.right))
    print()
    return 0


def cmd_benchmarks_coverage(args: argparse.Namespace) -> int:
    """Which rostered assets a version can score -- checked before freezing."""
    from whul import benchmarks

    store = _benchmark_store(args)
    rows = benchmarks.coverage(store, args.version, args.season)
    if rows.empty:
        print(f"\nNothing rostered in {args.season}, so nothing to cover.")
        print(missing_database_note(store))
        return 0
    from whul.benchmark_sources import SOURCES

    # A missing benchmark has two very different causes, and the fix differs:
    # a league nobody has computed yet is one command away, while a league with
    # no registered source at all needs a scraper written first.
    registered: dict[tuple[str, str], str] = {}
    for src in SOURCES.values():
        for group in src.produces or (src.league,):
            registered[(group, src.asset_type)] = src.key

    def source_for(row) -> str | None:
        keys = {
            registered[(c, row.asset_type)]
            for c in row.needs.split(", ")
            if (c, row.asset_type) in registered
        }
        return ", ".join(sorted(keys)) if keys else None

    missing = rows[~rows["covered"]]
    print(f"\n  {'league':<22}{'type':<8}{'assets':>7}  {'benchmark groups'}")
    for row in rows.itertuples():
        if row.covered:
            state = row.groups
        elif source_for(row):
            state = f"MISSING -- run `benchmarks compute {source_for(row)}`"
        else:
            state = "MISSING -- no source registered for it yet"
        print(f"  {row.league:<22}{row.asset_type.lower():<8}{row.assets:>7}  {state}")

    if missing.empty:
        print(f"\n  Every rostered asset in {args.season} has a benchmark.\n")
        return 0

    unsourced = [r for r in missing.itertuples() if not source_for(r)]
    print(
        f"\n  {int(missing['assets'].sum())} rostered asset(s) across "
        f"{len(missing)} league/type pair(s) would score nothing."
    )
    if unsourced:
        pairs = ", ".join(f"{r.league} {r.asset_type.lower()}s" for r in unsourced)
        print(f"  Of those, no data source exists yet for: {pairs}.")
    print()
    return 1


def cmd_benchmarks_adopt(args: argparse.Namespace) -> int:
    """Bring a scale computed elsewhere into this database."""
    from whul.store import benchmarks as store_benchmarks

    store = _benchmark_store(args)
    try:
        season, rows = store_benchmarks.adopt(store, args.source, args.version)
    except ValueError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    print(f"\n  {args.version} copied in: {rows} benchmark(s) for {season}.")
    print("  Nothing is frozen. Check it covers what the old one did, then adopt it:\n")
    print(f"    python -m whul.cli benchmarks coverage {args.version} --season {season}")
    print(f"    python -m whul.cli benchmarks freeze {args.version}\n")
    return 0


def cmd_benchmarks_derive(args: argparse.Namespace) -> int:
    """Start a draft from an existing scale, so one league can be corrected."""
    from whul.config.league import SEASON
    from whul.store import benchmarks as store_benchmarks

    store = _benchmark_store(args)
    source = args.version
    if source == "frozen":
        # So a script does not have to parse a version id out of `versions`.
        # An unattended run that derived from the wrong scale would produce a
        # plausible, wrong one, which is the failure this whole command exists
        # to make cheap to correct.
        active = store_benchmarks.active_version(store, args.season or SEASON.label)
        if active is None:
            print(f"\nNo frozen version for {args.season or SEASON.label} to "
                  f"derive from.\n", file=sys.stderr)
            return 1
        source = active.version
        print(f"\n  frozen -> {source}")
    try:
        version = store_benchmarks.derive(
            store, source, season=args.season, notes=args.notes
        )
    except ValueError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    rows = store.query(
        "SELECT COUNT(*) AS n FROM benchmarks WHERE version = ?", (version,)
    )
    print(f"\n  {version} holds {int(rows.loc[0, 'n'])} benchmark(s), copied from "
          f"{source}.")
    print(f"  Nothing is frozen. Recompute the group(s) that changed into it:\n")
    print(f"    python -m whul.cli benchmarks compute <league> --into {version} --save")
    print(f"    python -m whul.cli benchmarks compare {source} {version}")
    print(f"    python -m whul.cli benchmarks freeze {version}\n")
    return 0


def cmd_benchmarks_freeze(args: argparse.Namespace) -> int:
    """Adopt a version. After this, changing it means a new version."""
    from whul import benchmarks
    from whul.store import benchmarks as store_benchmarks

    store = _benchmark_store(args)
    version = store_benchmarks.get_version(store, args.version)
    if version is None:
        print(f"\nNo benchmark version {args.version!r}.\n", file=sys.stderr)
        return 1
    if version.is_frozen:
        print(f"\n{args.version} was already frozen at {version.frozen_at}.\n")
        return 0

    holes = benchmarks.coverage(store, args.version, version.season)
    if not holes.empty:
        missing = holes[~holes["covered"]]
        if not missing.empty and not args.force:
            print(
                f"\n{int(missing['assets'].sum())} rostered asset(s) have no benchmark "
                f"in this version:\n", file=sys.stderr,
            )
            for row in missing.itertuples():
                print(
                    f"  {row.league} {row.asset_type.lower()}s ({row.assets})",
                    file=sys.stderr,
                )
            print(
                "\nFreezing anyway would let those managers score nothing without "
                "an error. Compute the missing leagues, or pass --force.\n",
                file=sys.stderr,
            )
            return 1

    frozen = store_benchmarks.freeze(store, args.version, notes=args.notes)
    print(f"\n  froze {frozen.version} for {frozen.season} at {frozen.frozen_at}")
    print("  Scores are now measured against it. Run `rollup --backfill` to restate.\n")
    return 0


def cmd_benchmarks_discard(args: argparse.Namespace) -> int:
    """Delete drafts nobody adopted, so they stop warning every later run."""
    from whul.store import benchmarks as store_benchmarks

    store = _benchmark_store(args)
    if args.drafts:
        targets = store_benchmarks.spent_drafts(store, args.season)
        if not targets:
            print(f"\n  No spent drafts for {args.season}.\n")
            return 0
    else:
        if not args.version:
            print("\nName a version, or pass --drafts to sweep the spent ones.\n",
                  file=sys.stderr)
            return 2
        found = store_benchmarks.get_version(store, args.version)
        if found is None:
            print(f"\nNo benchmark version {args.version!r}.\n", file=sys.stderr)
            return 1
        targets = [found]

    # Asked before anything is described, so that the dry run cannot promise a
    # deletion the real run would refuse. A frozen version named by mistake is
    # the likely case -- the ids are fifteen digits and differ by a minute, and
    # the first id typed into this workflow was a superseded frozen scale.
    refusals = [
        note for note in
        (store_benchmarks.refusal_for(store, v.version) for v in targets)
        if note
    ]
    if refusals:
        for note in refusals:
            print(f"\n{note}", file=sys.stderr)
        print(file=sys.stderr)
        return 1

    # Naming what is about to go, because a version id is fifteen digits and
    # two of them can differ by a minute -- the abandoned draft and the scale
    # in use were computed three hours apart on the same day.
    print(f"\n  {len(targets)} version(s) to discard:\n")
    for version in targets:
        groups = len(store_benchmarks.load(store, version.version))
        print(f"    {version.version}   {groups} group(s), computed "
              f"{version.computed_at}")
    if args.drafts:
        newest = store_benchmarks.latest_draft(store, args.season)
        if newest is not None:
            print(f"\n  Keeping {newest.version}, the newest draft -- it is what "
                  f"a resumed\n  recompute continues into. Name it to delete it.")
    if not args.yes:
        print("\n  Nothing frozen, and no stored score names any of them.")
        print("  Pass --yes to delete.\n")
        return 0

    gone = 0
    for version in targets:
        gone += store_benchmarks.discard(store, version.version)
    print(f"\n  discarded {len(targets)} version(s) and {gone} benchmark "
          f"group(s)\n")
    return 0


def cmd_probe_rounds(args: argparse.Namespace) -> int:
    """What the feed's round text actually says, so a phase split can be real.

    The profile groups a European campaign into a league phase and a knockout
    phase, and the only thing that can tell them apart is the competition label
    ESPN builds out of its league name, its note headline and its season-type
    slug. Nothing in the agent sandbox can reach that feed, so the patterns in
    `whul.scoring.competition` are a guess until this has run somewhere that
    can. Every distinct label is printed with how many matches carry it and
    which phase the patterns place it in -- `(unplaced)` being the answer that
    matters, since those are the matches a section would have to leave
    undivided.
    """
    from whul.scoring.competition import european_phase
    from whul.sources import espn

    from whul.scoring.competition import CONTINENTAL_TIERS, classify_key

    seasons = [int(s) for s in (args.seasons or "").split()] or [args.season]
    print(f"\nRound labels for {args.competition} {seasons}\n")
    # Cups included, which is what carries the European competitions: the
    # first version of this asked for the league alone and then reported every
    # league row as "unplaced", which is both alarming and meaningless -- a
    # league has no rounds and the phase split never looks at one.
    matches = espn.load_soccer_matches(
        args.competition, seasons, include_cups=True, verbose=True)
    if matches.empty:
        print("  the feed returned nothing\n", file=sys.stderr)
        return 1

    continental = {tier.value for tier, _ in CONTINENTAL_TIERS}
    seen: dict[tuple[str, str], int] = {}
    for key, label in zip(matches["competition_key"], matches["competition"]):
        tier = classify_key(str(key), str(label)).tier.value
        seen[(tier, str(label))] = seen.get((tier, str(label)), 0) + 1

    europe = {k: v for k, v in seen.items() if k[0] in continental}
    if not europe:
        print("\n  No European matches in this pull, so nothing here can "
              "answer the\n  phase question. Try a league whose clubs were in "
              "Europe that season.\n", file=sys.stderr)
        return 1

    unplaced = 0
    print("\n  European rows, which are the only ones the split reads:\n")
    for (tier, label), count in sorted(europe.items(), key=lambda kv: -kv[1]):
        phase = european_phase(label) or "(unplaced)"
        if phase == "(unplaced)":
            unplaced += count
        print(f"  {count:>5}  {phase:<13}  [{tier}] {label}")
    total = sum(europe.values())
    print(f"\n  {len(europe)} distinct European label(s); {unplaced:,} of "
          f"{total:,} row(s) unplaced.")
    print("  Unplaced rows are shown undivided rather than guessed at, so a "
          "non-zero\n  number here is the patterns needing a word this feed "
          "uses and they do not.\n")

    other = sorted(k for k in seen if k[0] not in continental)
    if other:
        print("  Everything else, for context only -- these are never split:\n")
        for tier, label in other[:12]:
            print(f"  {seen[(tier, label)]:>5}  [{tier}] {label}")
        print()
    return 0


#: How many of a list the terminal shows. The file gets all of them: "eleven
#: matches missing" is a fact and *which* eleven is the diagnosis, and a
#: diagnosis that scrolls off the top of a terminal is one nobody sends on.
TERMINAL_SAMPLE = 6


def _range_probe_lines(found: dict, limit: int | None) -> list[str]:
    """The report, as lines. ``limit`` truncates every list; None keeps all."""
    def listed(values: list) -> list[str]:
        shown = values if limit is None else values[:limit]
        out = [f"          {line}" for line in shown]
        if limit is not None and len(values) > limit:
            out.append(f"          ... and {len(values) - limit} more "
                       f"(all of them are in the file)")
        return out

    lines = [
        f"ESPN soccer date-range probe -- {found.get('league')} "
        f"{found.get('span')}",
        "",
    ]
    for key, value in found.items():
        if isinstance(value, (dict, list)):
            continue
        lines.append(f"  {key:<26} {value}")
    if found.get("unread_dates"):
        lines += ["", "  unread_dates:"] + listed(found["unread_dates"])

    for shape, report in found.items():
        if not shape.startswith("dates="):
            continue
        lines += ["", f"  {shape}"]
        if isinstance(report, str):
            lines.append(f"      {report}")
            continue
        for key, value in report.items():
            if isinstance(value, list):
                if value:
                    lines += [f"      {key}:"] + listed(value)
            else:
                lines.append(f"      {key:<22} {value}")
    return lines


def _print_range_probe(found: dict, out: str | None) -> int:
    """The range probe, to the terminal and to a file that can be sent on.

    Both, rather than either. The terminal is where a verdict is read and the
    file is what gets attached to a message -- and the file carries every
    missing match rather than the first handful, because the whole point of
    writing one is that it does not have to fit on a screen.
    """
    from pathlib import Path

    worked = any(
        str(report.get("verdict", "")).startswith("IDENTICAL")
        for shape, report in found.items()
        if shape.startswith("dates=") and isinstance(report, dict)
    )

    target = Path(out) if out else Path(
        f"probe-{found.get('league', 'soccer')}-range-"
        f"{str(found.get('span', '')).replace(' to ', '-').replace(' ', '')}.txt"
    )
    written = ""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(_range_probe_lines(found, None)) + "\n")
        written = str(target)
    except OSError as exc:
        # A report that cannot be written is not a reason to lose the run: the
        # terminal copy is still the answer, and this says why there is no file
        # rather than leaving somebody looking for one.
        print(f"\n  Could not write {target}: {exc}\n", file=sys.stderr)

    print()
    for line in _range_probe_lines(found, TERMINAL_SAMPLE):
        print(line)
    print()
    if written:
        print(f"  Written to {written} -- send me that file.\n")
    if worked:
        print("  A range that returns exactly what the walk does is safe to "
              "adopt.\n")
        return 0
    print("  No shape matched the day-by-day walk, so the walk stays. That is "
          "the\n  right outcome to report: a range that returns fewer matches "
          "would\n  lower the benchmark and raise every score above it.\n",
          file=sys.stderr)
    return 1


def cmd_probe(args: argparse.Namespace) -> int:
    """Cheap reachability + schema check, before committing to a full pull."""
    if args.events:
        from whul.sources import espn_individual

        if args.league not in ("pga", "nascar", "f1"):
            print("\n--events applies to pga, nascar and f1.\n", file=sys.stderr)
            return 2
        season = int(args.season) if args.season else _date.today().year - 1
        report = espn_individual.diagnose_season(args.league, season)
        print(f"\n  {report['league']} {report['season']}: {report['events']} events, "
              f"{report['finished']} read as finished, {report['with_date']} carry a date\n")
        print("  status shapes seen:")
        for shape, count in sorted(
            report["status_shapes"].items(), key=lambda kv: -kv[1]
        ):
            print(f"    {count:>4}  {shape}")
        if report["unfinished"]:
            print("\n  not reading as finished (first 12):")
            for row in report["unfinished"]:
                print(f"    {row['date']:<12}{row['name']:<46}{row['status']}")
            print(f"\n  keys on one of them: {report['unfinished'][0]['keys']}")
        print()
        return 0

    if args.league == "tennis2026":
        from whul.sources import tennis2026

        report = tennis2026.probe(args.path)
        print(f"\n  database  {report['path']}")
        if not report.get("exists") or "error" in report:
            print(f"  ERROR     {report.get('error', 'unreadable')}", file=sys.stderr)
            for candidate in report.get("looked_in", []):
                print(f"    looked in {candidate}", file=sys.stderr)
            print(file=sys.stderr)
            return 1
        print(f"  matches   {report['matches']:,}")
        if report["matches"]:
            print(f"  span      {report['first']} -> {report['last']}")
            print(f"  tours     {', '.join(report['tours'])}")
            for season, count in sorted(report["by_season"].items()):
                print(f"  {season}      {count:,} wins")
        print()
        return 0
    if args.league in ("fbref", "soccer-players"):
        from whul.sources import fbref

        seasons = [int(args.season)] if args.season else None
        return fbref.probe(seasons)

    if args.league == "tennis":
        from whul.sources import flashscore

        report = flashscore.probe()
        return _print_stages(
            f"Flashscore probe -- days {report['days']}", report
        )

    if args.league == "snapshot":
        from whul.sources import snapshot

        report = snapshot.probe(int(args.season) if args.season else None)
        return _print_stages(
            f"Historical snapshot probe -- season {report['season']}", report
        )

    if args.league == "schedule":
        # Three sources, each probed on its own: the tours are authoritative
        # but defended, and tennistonic is the fallback when they refuse.
        from whul.sources import tour_schedule

        season = int(args.season) if args.season else None
        sources = [args.tour] if args.tour else list(tour_schedule.SOURCES)
        status = 0
        for source in sources:
            report = tour_schedule.probe(source, season)
            status = _print_stages(
                f"Tour schedule probe -- {source} {report['season']} "
                f"({report['url']})",
                report,
            ) or status
        return status

    if args.league == "f1":
        from whul.sources import jolpica

        report = jolpica.probe(int(args.season) if args.season else None)
        return _print_stages(f"Jolpica F1 probe -- season {report['season']}", report)

    if args.league in ("pga", "nascar"):
        from whul.sources import espn_individual

        report = espn_individual.probe(args.league, int(args.season) if args.season else None)
        return _print_stages(
            f"ESPN {report['league']} probe -- season {report['season']}", report
        )

    if args.league == "motorsports":
        # The category is two series from two different feeds, so both are
        # probed: either one failing leaves the category half-scored.
        from whul.sources import espn_individual, jolpica

        season = int(args.season) if args.season else None
        nascar = espn_individual.probe("nascar", season)
        status = _print_stages(
            f"ESPN nascar probe -- season {nascar['season']}", nascar
        )
        f1 = jolpica.probe(season)
        return _print_stages(f"Jolpica F1 probe -- season {f1['season']}", f1) or status

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

    if args.league in SOCCER_LEAGUES or args.league in PROBE_ONLY_COMPETITIONS:
        from datetime import date as _d

        from whul.sources import espn

        if getattr(args, "span", None):
            start, _, end = args.span.partition(":")
            if not end:
                print("\n  --range takes START:END, e.g. "
                      "2025-08-15:2025-08-31\n", file=sys.stderr)
                return 2
            try:
                first, last = _d.fromisoformat(start), _d.fromisoformat(end)
            except ValueError as exc:
                print(f"\n  --range: {exc}\n", file=sys.stderr)
                return 2
            if last < first:
                print("\n  --range: the end is before the start.\n",
                      file=sys.stderr)
                return 2
            found = espn.probe_soccer_range(args.league, first, last)
            return _print_range_probe(found, getattr(args, "out", None))

        day = _d.fromisoformat(args.date) if args.date else None
        result = espn.probe_soccer(args.league, day)
        print(f"\nESPN soccer probe -- {result['league']} on {result['date']}\n")
        for key, value in result.items():
            if key in ("league", "date"):
                continue
            print(f"  {key:<22} {value}")
        if any(isinstance(v, str) and v.startswith("FAILED") for v in result.values()):
            print("\nCould not reach or parse ESPN. Send me this output.", file=sys.stderr)
            return 1
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

    if args.league == "nhl":
        from whul.sources import nhl as source

        result = source.probe()
        print(f"\nNHL probe -- season {result['season']} (id {result['season_id']})\n")
        for key, value in result.items():
            if key in ("season", "season_id"):
                continue
            print(f"  {key:<24} {value}")
        if any(isinstance(v, str) and v.startswith("FAILED") for v in result.values()):
            print("\nAn endpoint could not be reached. Send me this output.", file=sys.stderr)
            return 1
        print("\nNHL stats API reachable and parsing.")
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


def cmd_backwards(args: argparse.Namespace) -> int:
    """Days where an asset's season-to-date figure fell.

    The standings ledger differences consecutive days, so a figure that goes
    down is read as a loss and shown as a negative adjustment. Restating does
    not touch it: restating rescores what is stored, and a day stored wrong
    stays wrong at whatever scale it is rescored against.

    Some falls are real -- a run value can genuinely drop, and a feed whose
    window has aged out will report less than it did. What this separates is
    one bad day from many honest ones: if a single date accounts for most of
    the fall across most of the roster, that date is a broken run and can be
    pulled again.
    """
    from whul.store import open_store

    store = open_store(args.db)
    rows = store.query(
        "SELECT d.asset_id, d.as_of, d.league_points, a.display_name, a.league "
        "FROM daily_scores d LEFT JOIN assets a ON a.asset_id = d.asset_id "
        "WHERE d.season = ? ORDER BY d.asset_id, d.as_of", (args.season,))
    if rows.empty:
        print(f"\nNo scored days in {args.season}.\n")
        return 0

    limit = float(args.tolerance)
    falls, by_day = [], {}
    for asset_id, block in rows.groupby("asset_id", sort=False):
        block = block.reset_index(drop=True)
        for i in range(1, len(block)):
            before = float(block.loc[i - 1, "league_points"] or 0.0)
            now = float(block.loc[i, "league_points"] or 0.0)
            if before - now <= limit:
                continue
            day = str(block.loc[i, "as_of"])
            falls.append({
                "asset": str(block.loc[i, "display_name"] or asset_id),
                "league": str(block.loc[i, "league"] or ""),
                "day": day, "from": before, "to": now,
                "fell": round(before - now, 1),
            })
            entry = by_day.setdefault(day, {"assets": 0, "fell": 0.0, "to_zero": 0})
            entry["assets"] += 1
            entry["fell"] += before - now
            entry["to_zero"] += 1 if now == 0 else 0

    scored = rows["asset_id"].nunique()
    print(f"\nDays a season-to-date figure fell by more than {limit:g}, "
          f"across {scored} asset(s) in {args.season}.\n")
    if not falls:
        print("  None. Every asset's figure only ever rose.\n")
        return 0

    for day in sorted(by_day):
        e = by_day[day]
        # A day where most of the roster falls, and falls to nothing, is a run
        # that failed rather than a league that had a bad Tuesday.
        flag = ("  <-- most of the roster, and mostly to zero: a broken run"
                if e["assets"] >= scored * 0.5 and e["to_zero"] >= e["assets"] * 0.5
                else "")
        print(f"  {day}: {e['assets']} asset(s) fell, {e['fell']:,.1f} in all, "
              f"{e['to_zero']} to zero{flag}")

    print(f"\n  The largest, worst first:")
    for f in sorted(falls, key=lambda f: -f["fell"])[:15]:
        print(f"      {f['day']}  {f['asset'][:28]:<30} "
              f"{f['from']:>9,.1f} -> {f['to']:>9,.1f}  ({-f['fell']:+,.1f})")
    print(f"\n  A day flagged above is re-pulled with "
          f"`ingest --date <day>`, then `rescore` and `rollup --backfill`.\n")
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

    sim = sub.add_parser(
        "simulate", help="build a placeholder league to develop the app against"
    )
    sim.add_argument("--db", default="data/whul.sqlite3", help="database path")
    sim.add_argument("--seed", type=int, default=2026, help="so runs are reproducible")
    sim.add_argument("--end", help="YYYY-MM-DD to simulate through (default: today)")
    sim.add_argument("--purge", action="store_true", help="delete the simulated league")
    sim.add_argument(
        "--from-season",
        help="mirror this season's real rosters and invent only the scores",
    )
    sim.set_defaults(func=cmd_simulate)

    imp = sub.add_parser("import-rosters", help="read the draft spreadsheet")
    imp.add_argument("--db", default="data/whul.sqlite3", help="database path")
    imp.add_argument("--season", default="2026-27", help="season to import into")
    imp.add_argument("--path", help="spreadsheet (default: Master_Drafted_Assets.xlsx)")
    imp.add_argument("--sheet", default=0, help="sheet name or index")
    imp.add_argument(
        "--write", action="store_true",
        help="actually write; without it the run only reports what it found",
    )
    imp.set_defaults(func=cmd_import_rosters)

    prune = sub.add_parser(
        "prune-assets",
        help="remove assets the spreadsheet no longer holds, and their history",
    )
    prune.add_argument("--db", default="data/whul.sqlite3", help="database path")
    prune.add_argument("--season", default="2026-27", help="season whose roster decides")
    prune.add_argument(
        "--write", action="store_true",
        help="actually remove them; without it the run only reports",
    )
    prune.set_defaults(func=cmd_prune_assets)

    bids = sub.add_parser(
        "import-bids",
        help="read the auction bid logs, losing bids included",
    )
    bids.add_argument("paths", nargs="+", metavar="file",
                      help="one log a round; the round comes from the filename")
    bids.add_argument("--db", default="data/whul.sqlite3", help="database path")
    bids.add_argument("--season", default="2026-27", help="season to import into")
    bids.add_argument(
        "--write", action="store_true",
        help="actually write; without it the run only reports what it found",
    )
    bids.set_defaults(func=cmd_import_bids)

    admin = sub.add_parser("admin", help="local page for trades and corrections")
    admin.add_argument("--db", default="data/whul.sqlite3", help="database path")
    admin.add_argument("--season", default="2026-27-SIM", help="season to administer")
    admin.add_argument("--port", type=int, default=8787)
    admin.add_argument("--no-browser", action="store_true", help="do not open a browser")
    admin.set_defaults(func=cmd_admin)

    rollup = sub.add_parser("rollup", help="score slots and snapshot the standings")
    rollup.add_argument("--db", default="data/whul.sqlite3", help="database path")
    rollup.add_argument("--season", default="2026-27-SIM", help="season to roll up")
    rollup.add_argument("--date", help="YYYY-MM-DD (default: today)")
    rollup.add_argument(
        "--backfill", action="store_true",
        help="rebuild every day from the season start, after a formula change",
    )
    rollup.set_defaults(func=cmd_rollup)

    costs = sub.add_parser(
        "pull-costs",
        help="what each feed has cost per day, and whether it is growing",
    )
    costs.add_argument("--db", default="data/whul.sqlite3", help="database path")
    costs.add_argument("--season", default="2026-27", help="season to report on")
    costs.add_argument(
        "--source", help="one source key, day by day, instead of the summary")
    costs.add_argument(
        "--days", type=int, default=14,
        help="how many recorded days to compare the earliest against")
    costs.set_defaults(func=cmd_pull_costs)

    falls = sub.add_parser(
        "check-falls",
        help="every day a score went down, and whether a count explains it",
    )
    falls.add_argument("--db", default="data/whul.sqlite3", help="database path")
    falls.add_argument("--season", default="2026-27")
    falls.add_argument(
        "--show-all", action="store_true",
        help="list the explained falls too, not only the unexplained ones",
    )
    falls.set_defaults(func=cmd_check_falls)

    rescore = sub.add_parser(
        "rescore",
        help="restate every stored day against the frozen benchmark",
    )
    rescore.add_argument("--db", default="data/whul.sqlite3", help="database path")
    rescore.add_argument("--season", default="2026-27-SIM")
    rescore.add_argument(
        "--version", default="frozen",
        help="benchmark version to restate against (default: the frozen one)",
    )
    rescore.add_argument(
        "--dry-run", action="store_true",
        help="say what would move without writing anything",
    )
    rescore.set_defaults(func=cmd_rescore)

    reweight = sub.add_parser(
        "reweight-mlb",
        help="restate stored MLB figures onto the contract-year weights",
    )
    reweight.add_argument("--db", default="data/whul.sqlite3",
                          help="database path")
    reweight.add_argument("--season", default="2026-27")
    reweight.add_argument(
        "--dry-run", action="store_true",
        help="say what would move without writing anything",
    )
    reweight.set_defaults(func=cmd_reweight_mlb)

    retract = sub.add_parser(
        "retract-titles",
        help="take back a title awarded before its season was settled",
    )
    retract.add_argument("--db", default="data/whul.sqlite3", help="database path")
    retract.add_argument("--season", default="2026-27-SIM")
    retract.add_argument(
        "--all", action="store_true",
        help="take back every title in the season, not only those already "
             "withdrawn. Correct only while the season is still being played, "
             "when no title can have been won yet",
    )
    retract.add_argument(
        "--dry-run", action="store_true",
        help="say what would be taken back without writing anything",
    )
    retract.set_defaults(func=cmd_retract_titles)

    seed = sub.add_parser(
        "feed-seed",
        help="keep a windowed feed's history where every pull can find it",
    )
    seed.add_argument("--source", default="tennis",
                      help="the source key the history belongs to")
    seed.add_argument(
        "--date", action="append", metavar="TOURNAMENT=DATE",
        help="when a round was played, for typed results that carry no dates. "
             "TOURNAMENT, TOURNAMENT:ROUND or TOURNAMENT:ROUND:TOUR -- the most "
             "specific given wins, because a round is not always one day for "
             "both tours. Repeatable")
    seed.add_argument("--db", default="data/whul.sqlite3",
                      help="database path, for --from-ledger")
    seed.add_argument(
        "--from-ledger", action="store_true",
        help="write what this source's ledger already holds, instead of "
             "reading a file. The ledger lives in a database three workflows "
             "rebuild and force-push; this is how a season gathered a night "
             "at a time survives that")
    seed.add_argument("source_file", metavar="FILE", nargs="?",
                      help="a tennis2026 database (.db), or a .csv/.json/.jsonl "
                           "with one row per match, or a .txt of typed results")
    seed.set_defaults(func=cmd_feed_seed)

    athlete = sub.add_parser(
        "probe-athlete",
        help="ask whether a player's own record names the competition",
    )
    athlete.add_argument("--league", default="bundesliga",
                         help="ESPN league key the player is asked for under")
    athlete.add_argument("--athlete", help="ESPN athlete id, if you have one")
    athlete.add_argument("--club", help="pick the athlete from this club")
    athlete.add_argument("--player", help="pick the athlete by name, e.g. 'Harry Kane'")
    athlete.add_argument("--season", help="our season label, e.g. 2027")
    athlete.add_argument("--out", help="write the report to this file too")
    athlete.add_argument(
        "--dump", metavar="DIR",
        help="write each payload to this directory as JSON. Four rounds of "
             "inferring a shape through a summary is three more than reading "
             "the shape itself")
    athlete.set_defaults(func=cmd_probe_athlete)

    cup = sub.add_parser(
        "probe-cup",
        help="ask whether a cup tie's own record says who played in it",
    )
    cup.add_argument("--league", default="epl",
                     help="ESPN league key, e.g. epl")
    cup.add_argument("--club", required=True,
                     help="the club as its results name it, e.g. Chelsea")
    cup.add_argument("--season", default="2027",
                     help="our season label, e.g. 2027")
    cup.add_argument("--out", help="write the report to this file too")
    cup.set_defaults(func=cmd_probe_cup)

    squad = sub.add_parser(
        "probe-squad",
        help="what a competition's squad pull returns, stage by stage",
    )
    squad.add_argument("--competition", default="efl_cup",
                       help="ESPN competition key, e.g. efl_cup, dfbpokal")
    squad.add_argument("--season", default="2027",
                       help="our season label, e.g. 2027")
    squad.set_defaults(func=cmd_probe_squad)

    check = sub.add_parser(
        "check-attribution",
        help="check rostered players' competitions against their clubs' results",
    )
    check.add_argument("--db", default="data/whul.sqlite3", help="database path")
    check.add_argument("--season", default="2026-27")
    check.add_argument("--league", help="one scored league, e.g. Bundesliga")
    check.add_argument("--feed-season", default="2027",
                       help="our season label for the feed, e.g. 2027")
    check.set_defaults(func=cmd_check_attribution)

    back = sub.add_parser(
        "backwards",
        help="days a season-to-date figure fell, which a ledger reads as a loss",
    )
    back.add_argument("--db", default="data/whul.sqlite3", help="database path")
    back.add_argument("--season", default="2026-27")
    back.add_argument("--tolerance", default="0.05",
                      help="ignore falls smaller than this (default 0.05)")
    back.set_defaults(func=cmd_backwards)

    site = sub.add_parser("site", help="generate the static site")
    site.add_argument("--db", default="data/whul.sqlite3", help="database path")
    site.add_argument("--season", default="2026-27-SIM", help="season to publish")
    site.add_argument("--out", default="site", help="output directory")
    site.set_defaults(func=cmd_site)

    fixt = sub.add_parser(
        "fixtures", help="which leagues have upcoming fixtures, and which assets do not"
    )
    fixt.add_argument("--db", default="data/whul.sqlite3", help="database path")
    fixt.add_argument("--season", default="2026-27", help="season to report on")
    fixt.add_argument("--date", help="YYYY-MM-DD to count from (default: today)")
    fixt.add_argument("--fetch", action="store_true",
                      help="pull upcoming matches from Flashscore before "
                           "reporting -- the next week of every sport, plus "
                           "the NHL's and NBA's own season pages")
    fixt.add_argument("--probe", action="store_true",
                      help="report what the Flashscore feed returns, without "
                           "touching the database")
    fixt.add_argument("--sport",
                      choices=("soccer", "basketball", "baseball", "hockey",
                               "tennis"),
                      help="probe one sport rather than all of them")
    fixt.add_argument("--discover", action="store_true",
                      help="ask every Flashscore sport id what it serves -- how "
                           "golf and motorsport would be added")
    fixt.set_defaults(func=cmd_fixtures)

    needed = sub.add_parser(
        "images-needed", help="every image file the site wants, and what is missing"
    )
    needed.add_argument("--db", default="data/whul.sqlite3", help="database path")
    needed.add_argument("--season", default="2026-27", help="season whose roster to list")
    needed.add_argument("--images", help="where the image directories are "
                                         "(default: assets/img, relative to here)")
    needed.set_defaults(func=cmd_images_needed)

    names = sub.add_parser(
        "feed-names", help="what a live feed calls things, against your roster"
    )
    names.add_argument("league", help="a key from `benchmarks list`")
    names.add_argument("--db", default="data/whul.sqlite3", help="database path")
    names.add_argument("--season", default="2026-27", help="season whose roster to check")
    names.add_argument("--date", help="YYYY-MM-DD to pull as (default: today)")
    names.add_argument("--all", action="store_true", help="list every name the feed carries")
    names.set_defaults(func=cmd_feed_names)

    alias = sub.add_parser(
        "alias", help="link a feed's name to a rostered asset the resolver would not guess"
    )
    alias.add_argument("source", help="source key, as `benchmarks list` names it")
    alias.add_argument("feed_name", help="the name exactly as the feed spells it")
    alias.add_argument("asset", help="the rostered asset's name")
    alias.add_argument("--asset-id", help="disambiguate when the name is not unique")
    alias.add_argument("--db", default="data/whul.sqlite3", help="database path")
    alias.add_argument("--season", default="2026-27", help="season whose roster to search")
    alias.set_defaults(func=cmd_alias)

    ingest = sub.add_parser("ingest", help="pull today's results and record them")
    ingest.add_argument(
        "leagues", nargs="*", metavar="league",
        help="keys from `benchmarks list` (default: all of them)",
    )
    ingest.add_argument("--db", default="data/whul.sqlite3", help="database path")
    ingest.add_argument("--season", default="2026-27", help="season to record into")
    ingest.add_argument("--date", help="YYYY-MM-DD to record as (default: today)")
    ingest.add_argument(
        "--since", metavar="YYYY-MM-DD",
        help="also rewrite every stored day from this one forward, each from "
             "what is known now cut back to that day. For a correction to the "
             "history -- a duplicate found in the ledger, a name two feeds "
             "spelled differently -- which otherwise fixes today and leaves "
             "the days before it holding the figure they were given, so the "
             "correction reads as a loss. Restating cannot do this: it "
             "rescores what is stored, and a day stored wrong stays wrong")
    ingest.set_defaults(func=cmd_ingest)

    bench = sub.add_parser("benchmarks", help="compute, review and freeze the 0-100 scale")
    bench_sub = bench.add_subparsers(dest="benchmarks_command", required=True)

    def _with_db(parser):
        parser.add_argument("--db", default="data/whul.sqlite3", help="database path")
        return parser

    bench_sub.add_parser("list", help="what can be benchmarked").set_defaults(
        func=cmd_benchmarks_list
    )

    bench_compute = _with_db(bench_sub.add_parser("compute", help="pull, score, take the percentile"))
    bench_compute.add_argument(
        "leagues", nargs="*", metavar="league",
        help="keys from `benchmarks list` (default: all of them)",
    )
    bench_compute.add_argument(
        "--seasons", type=int, default=DEFAULT_SEASONS,
        help=f"how many usable seasons to draw from (default: {DEFAULT_SEASONS})",
    )
    bench_compute.add_argument(
        "--latest", type=int,
        help="most recent season to include (default: last completed); ignored "
             "for the window-pooled sports, whose windows come from the season dates",
    )
    bench_compute.add_argument("--season", default="2026-27", help="season the version is for")
    bench_compute.add_argument(
        "--save", action="store_true", help="store the result as an unfrozen version",
    )
    bench_compute.add_argument(
        "--into", metavar="VERSION", nargs="?", const=LATEST_DRAFT,
        help="add to this unfrozen version instead of starting a new one, so a "
             "run split across sittings builds one scale rather than several "
             "with different holes. Bare --into means the season's newest "
             "unfrozen version, so the id does not have to be carried between "
             "commands",
    )
    bench_compute.add_argument("--compare", help="version to diff the new one against")
    bench_compute.add_argument("--csv", help="also write the benchmarks to this file")
    bench_compute.add_argument("--notes", default="", help="why this version exists")
    bench_compute.set_defaults(func=cmd_benchmarks_compute)

    _with_db(bench_sub.add_parser("versions", help="every version and its state")).set_defaults(
        func=cmd_benchmarks_versions
    )

    bench_compare = _with_db(bench_sub.add_parser("compare", help="what adopting one would change"))
    bench_compare.add_argument("left")
    bench_compare.add_argument("right")
    bench_compare.set_defaults(func=cmd_benchmarks_compare)

    bench_cover = _with_db(bench_sub.add_parser("coverage", help="rostered assets a version can score"))
    bench_cover.add_argument("version")
    bench_cover.add_argument("--season", default="2026-27", help="season whose roster to check")
    bench_cover.set_defaults(func=cmd_benchmarks_coverage)

    bench_derive = _with_db(bench_sub.add_parser(
        "derive",
        help="start a draft holding everything an existing version holds, so one "
             "league can be recomputed without redoing the other nineteen",
    ))
    bench_derive.add_argument(
        "version",
        help="the version to copy from; `frozen` means the season's current "
             "scale, so a script needs no version id",
    )
    bench_derive.add_argument(
        "--season", help="season the new version is for (default: the source's)",
    )
    bench_derive.add_argument("--notes", default="", help="why this version exists")
    bench_derive.set_defaults(func=cmd_benchmarks_derive)

    bench_adopt = _with_db(bench_sub.add_parser(
        "adopt",
        help="copy a version out of another database, so a scale computed on a "
             "laptop can be published from the database the nightly job owns",
    ))
    bench_adopt.add_argument("version", help="the version to copy")
    # `from` is a keyword, so the flag reads as one and the attribute does not.
    bench_adopt.add_argument(
        "--from", dest="source", required=True, metavar="DB",
        help="database to copy it out of, opened read-only",
    )
    bench_adopt.set_defaults(func=cmd_benchmarks_adopt)

    bench_freeze = _with_db(bench_sub.add_parser("freeze", help="adopt a version as the scale"))
    bench_freeze.add_argument("version")
    bench_freeze.add_argument("--notes", default="", help="why this version was adopted")
    bench_freeze.add_argument(
        "--force", action="store_true", help="freeze despite rostered assets with no benchmark",
    )
    bench_freeze.set_defaults(func=cmd_benchmarks_freeze)

    bench_discard = _with_db(bench_sub.add_parser(
        "discard", help="delete a draft nobody adopted"))
    bench_discard.add_argument("version", nargs="?")
    bench_discard.add_argument(
        "--drafts", action="store_true",
        help="every spent draft for the season, keeping the newest one",
    )
    bench_discard.add_argument("--season", default="2026-27",
                               help="season to sweep with --drafts")
    bench_discard.add_argument(
        "--yes", action="store_true",
        help="actually delete; without this the versions are only described",
    )
    bench_discard.set_defaults(func=cmd_benchmarks_discard)

    rounds = sub.add_parser(
        "probe-rounds", help="what a competition's round labels say, for the phase split")
    rounds.add_argument("competition", help="a league key, e.g. epl")
    rounds.add_argument("--season", type=int, default=2025, help="season to read")
    rounds.add_argument("--seasons", default="", help="several, space separated")
    rounds.set_defaults(func=cmd_probe_rounds)

    probe = sub.add_parser("probe", help="check a source is reachable and its schema intact")
    # Cups and European competitions are probeable even though they are not
    # scored as leagues in their own right.
    probe.add_argument(
        "league",
        choices=sorted(
            set(LEAGUES) | set(PROBE_ONLY_COMPETITIONS) | set(INDIVIDUAL_LEAGUES)
            | {"tennis2026", "fbref"}
        ),
        metavar="league",
    )
    probe.add_argument("--date", help="YYYY-MM-DD to probe (default: yesterday)")
    probe.add_argument("--path", help="file to probe (tennis2026: the app's database)")
    probe.add_argument(
        "--events", action="store_true",
        help="explain why a season's events do or do not read as finished "
             "(pga, nascar, f1; reads the cached season list, costs nothing)",
    )
    # The individual sports probe a whole season rather than a date: a golf
    # tournament or a race meeting spans days, so a single date says nothing.
    probe.add_argument("--season", help="season to probe (individual sports; default: last year)")
    probe.add_argument(
        "--tour", choices=("atp", "wta", "tennistonic"),
        help="which schedule source to probe (default: all three)",
    )
    probe.add_argument(
        "--out", metavar="FILE",
        help="where to write the range probe's report (default: a "
             "probe-<league>-range-<span>.txt beside you)",
    )
    probe.add_argument(
        "--range", dest="span", metavar="START:END",
        help="soccer only: walk this span a day at a time, then ask for it in "
             "one request, and compare the two match by match "
             "(e.g. 2025-08-15:2025-08-31)",
    )
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
