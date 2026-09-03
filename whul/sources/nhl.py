"""NHL data source.

The NHL's own stats API, which is what ``fastRhockey`` wraps. Free, no key, and
a whole season arrives in one request per endpoint -- so the nightly job is a
handful of calls regardless of how many games were played.

    skaters: /stats/rest/en/skater/summary?cayenneExp=seasonId=20252026 and gameTypeId=2
    teams:   /stats/rest/en/team/summary?cayenneExp=...

``gameTypeId`` is 2 for the regular season and 3 for the playoffs, so the two
phases are separate requests rather than something to disentangle afterwards --
which is exactly the discrete postseason split the scoring needs.

Season ids are the two calendar years concatenated: the 2025-26 season, which we
label 2026, is ``20252026``.

UNVERIFIED: the host is blocked from the environment this was written in. Run
``python -m whul.cli probe nhl`` from a machine with access.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

BASE = "https://api.nhle.com/stats/rest/en"
CACHE = Path("data/cache/nhl")
REQUEST_PAUSE = 0.4
TIMEOUT = 60

GAME_TYPE_REGULAR = 2
GAME_TYPE_PLAYOFFS = 3

#: The API pages; -1 asks for everything at once.
PAGE_ALL = -1


def season_id(season: int) -> str:
    """Our label (the ending year) to the API's concatenated form: 2026 -> 20252026."""
    return f"{season - 1}{season}"


def _get(path: str, params: dict, cache_key: str | None = None) -> dict:
    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        if cached.exists():
            return json.loads(cached.read_text())

    response = requests.get(
        f"{BASE}{path}",
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


def _summary(endpoint: str, seasons: list[int], game_type: int) -> pd.DataFrame:
    frames = []
    for season in seasons:
        sid = season_id(season)
        payload = _get(
            f"/{endpoint}/summary",
            {
                "isAggregate": "false",
                "isGame": "false",
                "limit": PAGE_ALL,
                "start": 0,
                "cayenneExp": f"seasonId={sid} and gameTypeId={game_type}",
            },
            cache_key=f"{endpoint}/{sid}_{game_type}",
        )
        rows = payload.get("data", [])
        if rows:
            frame = pd.DataFrame(rows)
            frame["season"] = season
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_skaters(seasons: list[int], game_type: int = GAME_TYPE_REGULAR) -> pd.DataFrame:
    return _summary("skater", seasons, game_type)


def load_goalies(seasons: list[int], game_type: int = GAME_TYPE_REGULAR) -> pd.DataFrame:
    return _summary("goalie", seasons, game_type)


def load_teams(seasons: list[int], game_type: int = GAME_TYPE_REGULAR) -> pd.DataFrame:
    return _summary("team", seasons, game_type)


def daily_update_cost(season: int | None = None) -> float:
    """Seconds to refresh one season -- the nightly job. Cache bypassed."""
    from datetime import date

    season = season or (date.today().year + 1 if date.today().month >= 9 else date.today().year)
    sid = season_id(season)
    started = time.monotonic()
    for endpoint in ("skater", "team"):
        _get(
            f"/{endpoint}/summary",
            {
                "isAggregate": "false", "isGame": "false", "limit": PAGE_ALL, "start": 0,
                "cayenneExp": f"seasonId={sid} and gameTypeId={GAME_TYPE_REGULAR}",
            },
        )
    return time.monotonic() - started


def probe(season: int = 2025) -> dict:
    """Check every endpoint the scoring needs, reporting each separately."""
    result: dict[str, object] = {"season": season, "season_id": season_id(season)}

    checks = [
        ("skaters_regular", lambda: load_skaters([season], GAME_TYPE_REGULAR)),
        ("skaters_playoffs", lambda: load_skaters([season], GAME_TYPE_PLAYOFFS)),
        ("teams_regular", lambda: load_teams([season], GAME_TYPE_REGULAR)),
        ("teams_playoffs", lambda: load_teams([season], GAME_TYPE_PLAYOFFS)),
    ]
    frames: dict[str, pd.DataFrame] = {}
    for label, loader in checks:
        try:
            frame = loader()
            frames[label] = frame
            result[label] = f"ok ({len(frame)} rows)" if len(frame) else "EMPTY"
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            result[label] = f"FAILED ({status}): {type(exc).__name__}: {exc}"

    skaters = frames.get("skaters_regular")
    if skaters is not None and not skaters.empty:
        wanted = ["goals", "assists", "shots", "plusMinus", "gamesPlayed", "skaterFullName"]
        result["skater_columns_present"] = [c for c in wanted if c in skaters.columns]
        result["skater_columns_missing"] = [c for c in wanted if c not in skaters.columns]

    teams = frames.get("teams_regular")
    if teams is not None and not teams.empty:
        wanted = ["wins", "otLosses", "goalsFor", "goalsAgainst", "gamesPlayed", "teamFullName"]
        result["team_columns_present"] = [c for c in wanted if c in teams.columns]
        result["team_columns_missing"] = [c for c in wanted if c not in teams.columns]
        if "gamesPlayed" in teams.columns:
            result["games_per_team"] = sorted(teams["gamesPlayed"].dropna().unique().tolist())[-3:]

    try:
        from whul.scoring import nhl as scoring

        scored = scoring.score_skaters(frames.get("skaters_regular", pd.DataFrame()))
        result["scored_skaters"] = len(scored)
        if len(scored):
            top = scored.nlargest(1, "total_points").iloc[0]
            result["top_skater"] = f"{top['player']} {top['total_points']:.1f}"
    except Exception as exc:
        result["scoring"] = f"FAILED: {type(exc).__name__}: {exc}"
    return result
