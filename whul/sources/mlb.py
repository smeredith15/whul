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

import json
import time
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


def _get(url: str, params: dict, cache_key: str | None = None) -> dict | list:
    """Fetch, caching by key. The pause applies only to real requests."""
    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        if cached.exists():
            return json.loads(cached.read_text())

    response = requests.get(
        url,
        params=params,
        timeout=TIMEOUT,
        headers={"User-Agent": "whul-fantasy/0.1"},
    )
    response.raise_for_status()
    payload = response.json()

    if cache_key:
        cached = CACHE / f"{cache_key}.json"
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


def _fangraphs(season: int, stats: str, qual: int) -> pd.DataFrame:
    """One FanGraphs leaderboard as a frame.

    The endpoint has returned its rows under several different keys over time, so
    the shape is probed rather than assumed.
    """
    payload = _get(
        FANGRAPHS_API,
        {
            "age": "", "pos": "all", "stats": stats, "lg": "all",
            "season": season, "season1": season, "startdate": "", "enddate": "",
            "qual": qual, "type": 8, "month": 0, "ind": 0,
            "pageitems": 2000, "pagenum": 1,
        },
        cache_key=f"fangraphs/{stats}_{season}",
    )
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("results") or []
    else:
        rows = []

    frame = pd.DataFrame(rows)
    if not frame.empty and "Season" not in frame.columns:
        frame["Season"] = season
    return frame


def load_batters(seasons: list[int]) -> pd.DataFrame:
    return pd.concat(
        [_fangraphs(y, "bat", BATTER_QUAL) for y in seasons], ignore_index=True
    )


def load_pitchers(seasons: list[int]) -> pd.DataFrame:
    return pd.concat(
        [_fangraphs(y, "pit", PITCHER_QUAL) for y in seasons], ignore_index=True
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
    for label, loader in (("batters", load_batters), ("pitchers", load_pitchers)):
        try:
            frame = loader([season])
            result[f"fangraphs_{label}"] = "ok" if not frame.empty else "EMPTY"
            result[f"{label}_rows"] = len(frame)
            if not frame.empty:
                result[f"{label}_columns"] = sorted(frame.columns)[:40]
        except Exception as exc:
            result[f"fangraphs_{label}"] = f"FAILED: {type(exc).__name__}: {exc}"

    # --- do the scoring inputs actually resolve? ---
    try:
        from whul.scoring import mlb as scoring

        batters = load_batters([season])
        pitchers = load_pitchers([season])
        players = scoring.score_players(batters, pitchers)
        result["scored_players"] = len(players)
        result["two_way_players"] = int(players["is_two_way"].sum()) if len(players) else 0
        if len(players):
            top = players.nlargest(1, "total_points").iloc[0]
            result["top_player"] = f"{top['player']} ({top['role']}) {top['total_points']:.1f}"
    except Exception as exc:
        result["scoring"] = f"FAILED: {type(exc).__name__}: {exc}"

    return result
