"""MLB data sources.

Two feeds, because no single free source carries everything the scoring needs:

* **MLB Stats API** (``statsapi.mlb.com``) -- schedules and results. Official,
  free, no key, and returns a whole season in one request.
* **FanGraphs** -- batting and pitching leaderboards. Needed for the Offense,
  Defense and WAR components, which the R script's formulas use and which the
  Stats API does not expose.

Both return **cumulative season-to-date** figures, which is what the scoring
wants: a daily pull of the current season, stored as a daily snapshot, gives both
live standings and the history the progression graph needs. Per-game detail is
only required where accrual has to be split finer than a day.

UNVERIFIED: every host here is blocked from the environment this was written in,
so nothing below has touched a live response. Run ``python -m whul.cli probe mlb``
from a machine with access before trusting it; the probe reports which feed and
which stage fails.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

STATS_API = "https://statsapi.mlb.com/api/v1"
FANGRAPHS_API = "https://www.fangraphs.com/api/leaders/major-league/data"
CACHE = Path("data/cache/mlb")
REQUEST_PAUSE = 0.5
TIMEOUT = 60

#: MLB Stats API game types: regular season plus the four postseason rounds.
GAME_TYPES = ("R", "F", "D", "L", "W")

#: FanGraphs qualifier thresholds, matching the R script's leaderboard calls.
BATTER_QUAL = 100
PITCHER_QUAL = 30

#: FanGraphs sits behind bot protection that answers 403 to a plain request.
#: Warming a session against the HTML leaderboard first collects the cookies the
#: API call is checked against -- the same sequence a browser performs.
FANGRAPHS_HOME = "https://www.fangraphs.com/leaders/major-league"
_SESSION: requests.Session | None = None


def _session() -> requests.Session:
    """A cookie-bearing session, warmed once against the HTML leaderboard."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": FANGRAPHS_HOME,
    })
    try:
        session.get(FANGRAPHS_HOME, timeout=TIMEOUT)
        time.sleep(REQUEST_PAUSE)
    except Exception:
        # A failed warm-up is not fatal; the API call may still succeed.
        pass
    _SESSION = session
    return session


def _cache_path(cache_key: str, params: dict) -> Path:
    """Cache file for a request.

    The parameters are hashed into the name: changing them changes what comes
    back, and a key that ignored them would keep serving the old response. That
    is what kept a 145-row qualified-players-only reply alive after the request
    was fixed to ask for the full pool.
    """
    digest = hashlib.sha1(
        json.dumps(params, sort_keys=True, default=str).encode()
    ).hexdigest()[:10]
    return CACHE / f"{cache_key}.{digest}.json"


def _get(url: str, params: dict, cache_key: str | None = None) -> dict | list:
    """Fetch, caching by key. The pause applies only to real requests.

    FanGraphs requests go through a warmed session carrying its cookies; the MLB
    Stats API needs nothing special.
    """
    if cache_key:
        cached = _cache_path(cache_key, params)
        if cached.exists():
            return json.loads(cached.read_text())

    if url.startswith(FANGRAPHS_API):
        response = _session().get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if cache_key:
            target = _cache_path(cache_key, params)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload))
        time.sleep(REQUEST_PAUSE)
        return payload

    response = requests.get(
        url,
        params=params,
        timeout=TIMEOUT,
        headers={
            # FanGraphs rejects unadorned clients; these are the minimum a
            # browser sends that it appears to check.
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        },
    )
    response.raise_for_status()
    payload = response.json()

    if cache_key:
        cached = _cache_path(cache_key, params)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload))

    time.sleep(REQUEST_PAUSE)
    return payload


def load_schedule(seasons: list[int]) -> pd.DataFrame:
    """Every completed game for the given seasons, one row per game.

    A whole season arrives in a single request, so this is cheap enough to re-pull
    nightly during the season.
    """
    rows: list[dict] = []
    for season in seasons:
        payload = _get(
            f"{STATS_API}/schedule",
            {
                "sportId": 1,
                "season": season,
                "gameTypes": ",".join(GAME_TYPES),
                "fields": (
                    "dates,date,games,gamePk,gameType,season,officialDate,status,"
                    "detailedState,teams,home,away,score,team,name,isWinner"
                ),
            },
            cache_key=f"schedule/{season}",
        )
        for day in payload.get("dates", []):
            for game in day.get("games", []):
                home = (game.get("teams") or {}).get("home", {})
                away = (game.get("teams") or {}).get("away", {})
                if home.get("score") is None or away.get("score") is None:
                    continue
                rows.append(
                    {
                        "season": int(game.get("season", season)),
                        "game_id": game.get("gamePk"),
                        "game_date": game.get("officialDate") or day.get("date"),
                        "game_type": game.get("gameType"),
                        "home_team": (home.get("team") or {}).get("name", ""),
                        "away_team": (away.get("team") or {}).get("name", ""),
                        "home_score": home.get("score"),
                        "away_score": away.get("score"),
                    }
                )
    return pd.DataFrame(rows)


#: Keys the leaderboard has returned its rows under across versions.
ROW_KEYS = ("data", "results", "leaders", "rows")


def fangraphs_variants(season: int, stats: str, qual: int) -> list[dict]:
    """Parameter shapes to try, most specific first.

    The leaderboard's query parameters have changed shape more than once, and a
    rejected or empty response looks the same as a season with no qualifiers, so
    guessing one shape and trusting it is how a season silently comes back empty.
    """
    common = {"pos": "all", "stats": stats, "lg": "all", "qual": qual, "type": 8}
    return [
        {**common, "age": "", "season": season, "season1": season,
         "startdate": "", "enddate": "", "month": 0, "ind": 0,
         "pageitems": 2000, "pagenum": 1},
        {**common, "season": season, "season1": season, "month": 0, "ind": 0,
         "pageitems": 2000, "pagenum": 1},
        {**common, "startseason": season, "endseason": season, "month": 0, "ind": 0},
        {**common, "season": season, "season1": season},
    ]


def _rows_from(payload) -> list:
    """Rows out of a leaderboard response, whatever key they arrived under."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ROW_KEYS:
            value = payload.get(key)
            if isinstance(value, list) and value:
                return value
        # Some versions nest one level deeper.
        for value in payload.values():
            if isinstance(value, dict):
                for key in ROW_KEYS:
                    nested = value.get(key)
                    if isinstance(nested, list) and nested:
                        return nested
    return []


def _fangraphs(season: int, stats: str, qual: int) -> pd.DataFrame:
    """One FanGraphs leaderboard as a frame.

    Tries each parameter shape until one returns rows. A shape that answers 200
    with nothing is treated as suspect rather than accepted, for the same reason
    it is in the ESPN adapter: an empty season with no error is the worst
    scraper failure, because the standings simply stay at zero.
    """
    cached = CACHE / f"fangraphs/{stats}_{season}.json"
    if cached.exists():
        frame = pd.DataFrame(_rows_from(json.loads(cached.read_text())))
        if not frame.empty and "Season" not in frame.columns:
            frame["Season"] = season
        return frame

    best: object = None
    last: Exception | None = None
    for params in fangraphs_variants(season, stats, qual):
        try:
            payload = _get(FANGRAPHS_API, params)
        except Exception as exc:
            last = exc
            continue
        if _rows_from(payload):
            best = payload
            break
        if best is None:
            best = payload

    if best is None:
        raise last if last else RuntimeError(f"no FanGraphs shape returned for {stats} {season}")

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(best))

    frame = pd.DataFrame(_rows_from(best))
    if not frame.empty and "Season" not in frame.columns:
        frame["Season"] = season
    return frame


def _merge_counting_and_advanced(
    season: int, group: str, since: date | None = None
) -> pd.DataFrame:
    """Counting stats joined to the fWAR components, from one host.

    Both come from the MLB Stats API, which removes the FanGraphs dependency
    entirely: FanGraphs blocks datacenter IPs, so it was never going to serve a
    production scraper.
    """
    counting = (
        load_players_since(season, group, since) if since
        else load_stats_api_players(season, group)
    )
    # The advanced figures have to cover the same span as the counting ones. WAR
    # is itself a season total, and a whole season of it added to six weeks of
    # hits would weight one player's WAR as heavily as another's whole summer.
    saber = load_sabermetrics(season, group, since=since)
    if counting.empty:
        return counting

    if not saber.empty:
        if group == "hitting":
            saber = derive_offense_defense(saber)
            keep = ["player_id", "Off", "Def"]
        else:
            keep = ["player_id", "war"]
        keep = [c for c in keep if c in saber.columns]
        counting = counting.merge(saber[keep], on="player_id", how="left")

    for column in ("Off", "Def", "war"):
        if column in counting.columns:
            counting[column] = pd.to_numeric(counting[column], errors="coerce").fillna(0.0)
    return counting


def load_batters(
    seasons: list[int], use_fangraphs: bool = False, since: date | None = None
) -> pd.DataFrame:
    """Batting lines with Offense and Defense attached.

    ``use_fangraphs`` is retained for the case where its leaderboard becomes
    reachable again; the default path needs only the Stats API.
    """
    if use_fangraphs:
        return pd.concat([_fangraphs(y, "bat", BATTER_QUAL) for y in seasons], ignore_index=True)

    frames = [_merge_counting_and_advanced(y, "hitting", since) for y in seasons]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    return out.rename(columns={
        "atBats": "AB", "hits": "H", "doubles": "2B", "triples": "3B",
        "homeRuns": "HR", "baseOnBalls": "BB", "hitByPitch": "HBP",
        "stolenBases": "SB", "caughtStealing": "CS", "gamesPlayed": "G",
        "player": "PlayerName",
    })


def load_pitchers(
    seasons: list[int], use_fangraphs: bool = False, since: date | None = None
) -> pd.DataFrame:
    """Pitching lines with WAR attached, and innings converted from outs notation."""
    if use_fangraphs:
        return pd.concat([_fangraphs(y, "pit", PITCHER_QUAL) for y in seasons], ignore_index=True)

    frames = [_merge_counting_and_advanced(y, "pitching", since) for y in seasons]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    if "inningsPitched" in out.columns:
        out["IP"] = out["inningsPitched"].map(innings_to_float)
    return out.rename(columns={
        "strikeOuts": "SO", "hits": "H", "baseOnBalls": "BB", "hitByPitch": "HBP",
        "homeRuns": "HR", "saves": "SV", "holds": "HLD", "war": "WAR",
        "gamesPlayed": "G", "player": "PlayerName",
    })


#: Fields the scoring needs from an advanced-metrics feed, and the FanGraphs
#: column each would stand in for.
#: The Stats API returns the full fWAR decomposition, so Offense and Defense are
#: not approximated but *reconstructed*: FanGraphs defines Off as batting plus
#: base running, and Def as fielding plus the positional adjustment.
OFFENSE_COMPONENTS = ("batting", "baseRunning")
DEFENSE_COMPONENTS = ("fielding", "positional")

ADVANCED_EQUIVALENTS = {
    "batting": "Off component",
    "baseRunning": "Off component",
    "fielding": "Def component",
    "positional": "Def component",
    "war": "WAR (pitchers)",
    "wRaa": "Off (offensive runs above average)",
    "wRc": "Off (runs created)",
}


def derive_offense_defense(saber: pd.DataFrame) -> pd.DataFrame:
    """Rebuild FanGraphs' Offense and Defense from their components.

    Not a substitute metric -- the same quantity, summed from the same parts.
    """
    out = saber.copy()
    for name, components in (("Off", OFFENSE_COMPONENTS), ("Def", DEFENSE_COMPONENTS)):
        present = [c for c in components if c in out.columns]
        if not present:
            raise KeyError(
                f"cannot rebuild {name}: none of {components} present; "
                f"have {sorted(out.columns)[:20]}"
            )
        out[name] = sum(pd.to_numeric(out[c], errors="coerce").fillna(0.0) for c in present)
    return out


def innings_to_float(value) -> float:
    """Convert baseball's innings notation: 200.1 means 200 and one third.

    Read as a decimal it understates a third of an inning by a factor of three,
    which at 7.4 points per inning is not a rounding error.
    """
    try:
        text = str(value)
        whole, _, outs = text.partition(".")
        base = float(whole or 0)
        return base + (float(outs[0]) / 3 if outs else 0.0)
    except (TypeError, ValueError):
        return 0.0


def load_sabermetrics(
    season: int, group: str = "hitting", since: date | None = None,
    until: date | None = None,
) -> pd.DataFrame:
    """Advanced metrics from the MLB Stats API.

    Worth trying before conceding the FanGraphs terms: this is the same host that
    already serves the schedule successfully, and its ``sabermetrics`` group
    carries run-value and WAR figures that may stand in for Offense, Defense and
    WAR. They will not be numerically identical to FanGraphs' -- different
    models -- so adopting them is a scoring decision, but a far smaller one than
    dropping the components entirely.
    """
    common = {
        "group": group, "season": season,
        "sportId": 1, "limit": 2000, "gameType": "R", "playerPool": "All",
    }
    if since is None:
        params = {"stats": "sabermetrics", **common}
        cache_key = f"statsapi/saber_{group}_{season}"
    else:
        end = until or date.today()
        params = {
            "stats": "sabermetrics", **common,
            "startDate": since.isoformat(), "endDate": end.isoformat(),
        }
        cache_key = (f"statsapi/saber_{group}_{season}_"
                     f"{since.isoformat()}_{end.isoformat()}")
    payload = _get(f"{STATS_API}/stats", params, cache_key=cache_key)
    rows: list[dict] = []
    for split_group in payload.get("stats", []):
        for split in split_group.get("splits", []):
            player = split.get("player") or {}
            stat = split.get("stat") or {}
            rows.append({"player": player.get("fullName", ""),
                         "player_id": player.get("id", ""),
                         "season": season, **stat})
    return pd.DataFrame(rows)


def load_stats_api_players(
    season: int, group: str = "hitting",
    since: date | None = None, until: date | None = None,
) -> pd.DataFrame:
    """Counting stats from the MLB Stats API, for a season or a span of it.

    A fallback, not a replacement: it carries every counting stat the formulas
    use but **not** FanGraphs' Offense, Defense or WAR, which contribute a
    meaningful share of a player's score. If FanGraphs becomes unavailable this
    is what remains, and dropping those three components is a scoring decision
    rather than something to do silently.

    ``since`` switches to the byDateRange stat type. A league year that opens in
    August cannot count a player's April, and the season totals this returns by
    default are the whole year -- four months of which were earned before anyone
    drafted him.
    """
    common = {
        # playerPool=All matters: the default returns qualified players only
        # (~145), where the R script's thresholds admit several hundred.
        "group": group, "season": season,
        "sportId": 1, "limit": 2000, "gameType": "R", "playerPool": "All",
    }
    if since is None:
        params = {"stats": "season", **common}
        cache_key = f"statsapi/{group}_{season}"
    else:
        end = until or date.today()
        params = {
            "stats": "byDateRange", **common,
            "startDate": since.isoformat(), "endDate": end.isoformat(),
        }
        cache_key = f"statsapi/{group}_{season}_{since.isoformat()}_{end.isoformat()}"
    payload = _get(f"{STATS_API}/stats", params, cache_key=cache_key)
    rows: list[dict] = []
    for split_group in payload.get("stats", []):
        for split in split_group.get("splits", []):
            player = split.get("player") or {}
            stat = split.get("stat") or {}
            rows.append({"player": player.get("fullName", ""),
                         "player_id": player.get("id", ""),
                         "season": season, **stat})
    return pd.DataFrame(rows)


#: Below this the two pulls are the same numbers and the range did nothing.
RANGE_DIFFERENCE = 0.02


def load_players_since(
    season: int, group: str, since: date, until: date | None = None
) -> pd.DataFrame:
    """One span of a season, having checked the span was actually applied.

    The Stats API ignores parameters it does not recognise rather than
    rejecting them, so an unsupported date range comes back as a full season of
    perfectly valid-looking numbers. Nothing downstream could tell the
    difference: the player exists, the stat lines parse, the totals are real --
    they are simply four months too generous. So the whole season is fetched as
    well and the two are compared, and near-identical totals fail rather than
    pass.
    """
    ranged = load_stats_api_players(season, group, since=since, until=until)
    whole = load_stats_api_players(season, group)
    _check_range_applied(ranged, whole, group, since)
    return ranged


def _check_range_applied(
    ranged: pd.DataFrame, whole: pd.DataFrame, group: str, since: date
) -> None:
    column = "gamesPlayed"
    if ranged.empty or whole.empty or column not in ranged or column not in whole:
        return
    part = pd.to_numeric(ranged[column], errors="coerce").sum()
    full = pd.to_numeric(whole[column], errors="coerce").sum()
    if not full:
        return
    if part >= full * (1 - RANGE_DIFFERENCE):
        raise RuntimeError(
            f"the Stats API returned the same {group} totals for "
            f"{since}-onwards as for the whole {ranged['season'].iloc[0]} season "
            f"({part:,.0f} vs {full:,.0f} games). It ignores parameters it does "
            f"not recognise, so byDateRange is not being applied and every "
            f"player would be scored on months earned before the league year "
            f"opened."
        )


def daily_update_cost(season: int | None = None) -> float:
    """Seconds to refresh one season's cumulative figures -- the nightly job.

    Bypasses the cache so the number reflects real network cost.
    """
    from datetime import date

    season = season or date.today().year
    started = time.monotonic()
    _get(f"{STATS_API}/schedule", {"sportId": 1, "season": season, "gameTypes": "R"})
    _get(
        FANGRAPHS_API,
        {"age": "", "pos": "all", "stats": "bat", "lg": "all", "season": season,
         "season1": season, "qual": BATTER_QUAL, "type": 8, "month": 0, "ind": 0,
         "pageitems": 2000, "pagenum": 1},
    )
    return time.monotonic() - started


def probe(season: int = 2025) -> dict:
    """Check both feeds and report the shape of each, without a full pull."""
    result: dict[str, object] = {"season": season}

    # --- MLB Stats API ---
    try:
        schedule = load_schedule([season])
        result["stats_api"] = "ok"
        result["schedule_games"] = len(schedule)
        if not schedule.empty:
            result["game_types"] = schedule["game_type"].value_counts().to_dict()
            result["teams"] = schedule["home_team"].nunique()
            result["schedule_sample"] = schedule.iloc[0].to_dict()
    except Exception as exc:
        result["stats_api"] = f"FAILED: {type(exc).__name__}: {exc}"

    # --- FanGraphs ---
    # Off, Def and WAR live here and nowhere free; report explicitly whether the
    # scoring inputs arrived, since a leaderboard can answer 200 with nothing.
    needed = {
        "batters": ["AB", "H", "2B", "3B", "HR", "BB", "HBP", "SB", "CS", "Off", "Def"],
        "pitchers": ["IP", "SO", "H", "BB", "HBP", "HR", "SV", "HLD", "WAR"],
    }
    for label, loader in (("batters", load_batters), ("pitchers", load_pitchers)):
        try:
            frame = loader([season])
            result[f"fangraphs_{label}"] = "ok" if not frame.empty else "EMPTY"
            result[f"{label}_rows"] = len(frame)
            if not frame.empty:
                columns = set(frame.columns)
                lowered = {c.lower() for c in columns}
                missing = [c for c in needed[label] if c not in columns and c.lower() not in lowered]
                result[f"{label}_scoring_columns_missing"] = missing or "none"
                result[f"{label}_columns"] = sorted(columns)[:40]
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            result[f"fangraphs_{label}"] = f"FAILED ({status}): {type(exc).__name__}: {exc}"

    # --- do the scoring inputs actually resolve? ---
    # Can the working host supply the advanced metrics FanGraphs is withholding?
    for group in ("hitting", "pitching"):
        try:
            saber = load_sabermetrics(season, group)
            result[f"sabermetrics_{group}"] = f"ok ({len(saber)} rows)" if len(saber) else "EMPTY"
            if len(saber):
                fields = [c for c in saber.columns if c not in ("player", "player_id", "season")]
                result[f"sabermetrics_{group}_fields"] = sorted(fields)
                useful = [f for f in fields if f in ADVANCED_EQUIVALENTS]
                result[f"sabermetrics_{group}_usable"] = (
                    {f: ADVANCED_EQUIVALENTS[f] for f in useful} if useful else "none recognised"
                )
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            result[f"sabermetrics_{group}"] = f"FAILED ({status}): {type(exc).__name__}"

    # Is there a usable fallback if FanGraphs stays out of reach?
    try:
        hitting = load_stats_api_players(season, "hitting")
        result["stats_api_hitting"] = f"ok ({len(hitting)} rows)" if len(hitting) else "EMPTY"
        if len(hitting):
            counting = ["atBats", "hits", "doubles", "triples", "homeRuns",
                        "baseOnBalls", "hitByPitch", "stolenBases", "caughtStealing"]
            result["stats_api_counting_present"] = [c for c in counting if c in hitting.columns]
            result["stats_api_note"] = (
                "counting stats only -- no Off/Def/WAR, so using this would drop "
                "those scoring components"
            )
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "?")
        result["stats_api_hitting"] = f"FAILED ({status}): {type(exc).__name__}"

    try:
        from whul.normalize import apply_benchmarks, compute_benchmarks
        from whul.scoring import mlb as scoring

        batters = load_batters([season])
        pitchers = load_pitchers([season])

        # score_players emits one row per player-*role*; two-way players are
        # folded together only after each role has met its own benchmark.
        roles = scoring.score_players(batters, pitchers)
        result["scored_role_rows"] = len(roles)
        if len(roles):
            result["rows_by_role"] = roles["role"].value_counts().to_dict()
            counts = roles.groupby(["season", "player"]).size()
            result["two_way_players"] = int((counts > 1).sum())

            bench = compute_benchmarks(roles, "Player")
            result["benchmarks"] = {
                row["norm_key"]: round(float(row["benchmark"]), 1)
                for _, row in bench.iterrows()
            }
            combined = scoring.combine_two_way(apply_benchmarks(roles, bench, "Player"))
            result["scored_players"] = len(combined)
            top = combined.nlargest(1, "scaled_score").iloc[0]
            result["top_player"] = (
                f"{top['player']} ({top['role']}) "
                f"{top['total_points']:.1f} raw, {top['scaled_score']:.1f} normalized"
            )
            two_way = combined[combined["is_two_way"]]
            if len(two_way):
                best = two_way.nlargest(1, "scaled_score").iloc[0]
                result["top_two_way"] = (
                    f"{best['player']}: {best['primary_score']:.1f} {best['role']} "
                    f"+ 0.5 x {best['secondary_score']:.1f} {best['secondary_role']} "
                    f"= {best['scaled_score']:.1f}"
                )
    except Exception as exc:
        result["scoring"] = f"FAILED: {type(exc).__name__}: {exc}"

    return result
