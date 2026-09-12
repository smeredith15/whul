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
#: The club-facing API, which is where divisions live. The stats API above
#: reports a team's season totals and never says who it was competing with.
WEB = "https://api-web.nhle.com/v1"
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


def _web(path: str, cache_key: str | None = None):
    """A GET against the club-facing API, cached the same way as the rest."""
    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        if cached.exists():
            return json.loads(cached.read_text())

    response = requests.get(
        f"{WEB}{path}", timeout=TIMEOUT, headers={"User-Agent": "whul-fantasy/0.1"}
    )
    response.raise_for_status()
    payload = response.json()

    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload))

    time.sleep(REQUEST_PAUSE)
    return payload


def _standings_dates() -> dict[str, str]:
    """The last day of each season's standings, by season id.

    Asked for rather than guessed. The standings endpoint is addressed by date,
    and a date picked by hand -- "some day in April" -- is wrong for the two
    COVID seasons, wrong for a season that ran late, and silently wrong rather
    than loudly: a date outside a season returns the neighbouring season's
    table, so the divisions would look fine and belong to the wrong year.
    """
    payload = _web("/standings-season", cache_key="standings_season")
    seasons = payload.get("seasons", []) if isinstance(payload, dict) else []
    dates = {}
    for entry in seasons:
        sid = entry.get("id")
        end = entry.get("standingsEnd")
        if sid and end:
            dates[str(sid)] = str(end)
    return dates


def load_divisions(seasons: list[int]) -> pd.DataFrame:
    """Which division each club played in, per season.

    A division title is the one team scoring term that cannot be read off a
    season summary: a summary says how a club did, not who it was competing
    with. Fetched per season rather than hardcoded because the alignment moves
    -- the current four divisions date from 2021-22, the 2020-21 season had a
    temporary set of its own including an all-Canadian North Division, and
    Utah replaced Arizona in the Central in 2024-25. A map written today would
    score an older season against an alignment that never existed.

    Returns ``season``, ``team``, ``division`` -- and an empty frame where the
    feed gives nothing, which the scoring reads as "no title to award" rather
    than guessing one.

    UNVERIFIED, like the rest of this module: the host is blocked from the
    environment this was written in. ``python -m whul.cli probe nhl`` checks it.
    """
    try:
        ends = _standings_dates()
    except Exception:
        # No dates, no addressable standings. An empty frame is the honest
        # answer; the benchmark path turns it into a loud failure.
        return pd.DataFrame(columns=["season", "team", "division"])

    rows: list[dict] = []
    for season in seasons:
        sid = season_id(season)
        end = ends.get(sid)
        if not end:
            # A season the API does not list is a season that has not started.
            continue
        try:
            payload = _web(f"/standings/{end}", cache_key=f"standings/{sid}")
        except Exception:
            continue
        for row in payload.get("standings", []) if isinstance(payload, dict) else []:
            name = (row.get("teamName") or {}).get("default")
            division = row.get("divisionName")
            if not name or not division:
                # Both are needed and neither can be inferred from the other.
                continue
            rows.append({
                "season": season,
                "team": str(name),
                "division": str(division),
            })
    if not rows:
        return pd.DataFrame(columns=["season", "team", "division"])
    return pd.DataFrame(rows).drop_duplicates(subset=["season", "team"])


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
        ("divisions", lambda: load_divisions([season])),
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

    divisions = frames.get("divisions")
    if divisions is not None and not divisions.empty:
        # The shape matters as much as the row count: four divisions of eight
        # is what a modern season looks like, and anything else means the
        # standings came back for the wrong date or in a shape that changed.
        counts = divisions.groupby("division").size().sort_index()
        result["divisions_found"] = {str(k): int(v) for k, v in counts.items()}
        names = load_teams([season], GAME_TYPE_REGULAR)
        if not names.empty and "teamFullName" in names.columns:
            unplaced = sorted(set(names["teamFullName"]) - set(divisions["team"]))
            # The join is on the club's full name, and the two endpoints are
            # free to spell it differently. A club that fails to join is a club
            # that cannot win its division, which is invisible in a total.
            result["teams_without_a_division"] = unplaced

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
