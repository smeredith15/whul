"""ESPN site API.

The sportsdataverse packages (hoopR, wehoop) wrap this API; their data
repositories are archived and stop at season 2023, so live scoring has to come
from ESPN directly. One adapter serves NBA, WNBA and the NCAA leagues.

    scoreboard: /apis/site/v2/sports/{sport}/{league}/scoreboard?dates=YYYYMMDD
    boxscore:   /apis/site/v2/sports/{sport}/{league}/summary?event={id}

UNVERIFIED: this module could not be exercised where it was written -- ESPN is
blocked by that environment's egress policy. Run ``probe()`` (or
``python -m whul.cli probe nba``) from a machine with access before trusting it;
the probe reports exactly which stage fails.

Fetching a season means walking its dates, so a backfill is slow (thousands of
requests) while a daily update is cheap (one date). ``load_nba_player_box``
caches per-date responses under ``data/cache`` so a re-run costs nothing.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports"
CACHE = Path("data/cache/espn")
REQUEST_PAUSE = 0.4  # be a considerate client; ESPN publishes no rate limit
TIMEOUT = 30

LEAGUE_PATHS = {
    "nba": ("basketball", "nba"),
    "wnba": ("basketball", "wnba"),
    "ncaam": ("basketball", "mens-college-basketball"),
    "ncaaw": ("basketball", "womens-college-basketball"),
}

# ESPN season_type ids, matching the codes hoopR exposed.
SEASON_TYPE_REGULAR = 2
SEASON_TYPE_POST = 3
SEASON_TYPE_PLAYIN = 5

#: An NBA season labelled 2026 runs Oct 2025 - Jun 2026.
NBA_SEASON_START = (10, 1)
NBA_SEASON_END = (6, 30)


def _get(url: str, params: dict, cache_key: str | None = None) -> dict:
    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        if cached.exists():
            return json.loads(cached.read_text())

    response = requests.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload))
    return payload


def season_dates(season: int) -> list[date]:
    """Every date an NBA season labelled ``season`` could have games on."""
    start = date(season - 1, *NBA_SEASON_START)
    end = min(date(season, *NBA_SEASON_END), date.today())
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def scoreboard(league: str, day: date) -> dict:
    sport, path = LEAGUE_PATHS[league]
    return _get(
        f"{BASE}/{sport}/{path}/scoreboard",
        {"dates": day.strftime("%Y%m%d")},
        cache_key=f"{league}/scoreboard/{day.isoformat()}",
    )


def summary(league: str, event_id: str) -> dict:
    sport, path = LEAGUE_PATHS[league]
    return _get(
        f"{BASE}/{sport}/{path}/summary",
        {"event": event_id},
        cache_key=f"{league}/summary/{event_id}",
    )


def _parse_box(payload: dict, event_id: str, day: date, season: int, season_type: int) -> list[dict]:
    """Flatten one game's boxscore into per-player rows.

    ESPN returns stats as a parallel list of labels and string values, so
    everything is looked up by label rather than by position.
    """
    rows: list[dict] = []
    for team_block in payload.get("boxscore", {}).get("players", []):
        team = team_block.get("team", {}).get("abbreviation", "")
        for stat_block in team_block.get("statistics", []):
            labels = [label.upper() for label in stat_block.get("labels", [])]
            for entry in stat_block.get("athletes", []):
                athlete = entry.get("athlete", {})
                values = entry.get("stats", [])
                if not values:
                    continue  # did not play
                stats = dict(zip(labels, values))
                rows.append(
                    {
                        "season": season,
                        "season_type": season_type,
                        "game_id": event_id,
                        "game_date": day.isoformat(),
                        "team": team,
                        "athlete_id": str(athlete.get("id", "")),
                        "athlete_display_name": athlete.get("displayName", ""),
                        "athlete_position_abbreviation": (
                            entry.get("position", {}).get("abbreviation", "")
                        ),
                        "points": stats.get("PTS"),
                        "rebounds": stats.get("REB"),
                        "assists": stats.get("AST"),
                        "steals": stats.get("STL"),
                        "blocks": stats.get("BLK"),
                        "turnovers": stats.get("TO"),
                        "three_point_field_goals_made": (stats.get("3PT") or "0-0").split("-")[0],
                        "plus_minus": stats.get("+/-", "0"),
                    }
                )
    return rows


def load_nba_player_box(seasons: list[int], verbose: bool = True) -> pd.DataFrame:
    """Per-player, per-game box scores for whole NBA seasons.

    Slow on a cold cache -- a season is ~250 game days and ~1,300 games, so a
    backfill is thousands of requests. Responses are cached per date and per
    game, so this is a one-time cost and a daily update is a single date.
    """
    rows: list[dict] = []
    for season in seasons:
        days = season_dates(season)
        if verbose:
            print(f"  {season}: walking {len(days)} dates ...")
        for index, day in enumerate(days):
            try:
                board = scoreboard("nba", day)
            except Exception:
                continue
            for event in board.get("events", []):
                competition = (event.get("competitions") or [{}])[0]
                if not competition.get("status", {}).get("type", {}).get("completed"):
                    continue
                season_type = int(
                    (event.get("season") or {}).get("type", SEASON_TYPE_REGULAR)
                )
                try:
                    rows.extend(
                        _parse_box(
                            summary("nba", event["id"]),
                            event["id"],
                            day,
                            season,
                            season_type,
                        )
                    )
                except Exception:
                    continue
                time.sleep(REQUEST_PAUSE)
            if verbose and index % 50 == 0 and index:
                print(f"    {index}/{len(days)} dates, {len(rows):,} rows")
            time.sleep(REQUEST_PAUSE)
    return pd.DataFrame(rows)


def probe(league: str = "nba", day: date | None = None) -> dict:
    """Check reachability and schema without pulling a whole season.

    Returns a dict of stage -> outcome so a failure says which stage broke.
    """
    day = day or (date.today() - timedelta(days=1))
    result: dict[str, object] = {"league": league, "date": day.isoformat()}

    try:
        board = scoreboard(league, day)
        result["scoreboard"] = "ok"
        events = board.get("events", [])
        result["events"] = len(events)
    except Exception as exc:
        result["scoreboard"] = f"FAILED: {type(exc).__name__}: {exc}"
        return result

    if not events:
        result["boxscore"] = "skipped (no games that date)"
        return result

    try:
        payload = summary(league, events[0]["id"])
        players = payload.get("boxscore", {}).get("players", [])
        result["boxscore"] = "ok"
        result["teams_in_box"] = len(players)
        if players:
            result["stat_labels"] = players[0].get("statistics", [{}])[0].get("labels", [])
        rows = _parse_box(payload, events[0]["id"], day, day.year, SEASON_TYPE_REGULAR)
        result["parsed_rows"] = len(rows)
        result["sample"] = rows[0] if rows else None
    except Exception as exc:
        result["boxscore"] = f"FAILED: {type(exc).__name__}: {exc}"
    return result
