"""NCAA stats API (henrygd/ncaa-api).

A thin wrapper over the NCAA's own stats site, suggested as an alternative to
ESPN for the college leagues. Its advantage is decisive for our purposes:
**division membership is explicit in the URL** (``football/fbs``, ``basketball-men/d1``),
where ESPN's teams endpoint ignores the `groups` filter and returns all 760
college football programs regardless.

    scoreboard: /scoreboard/{sport}/{division}/{year}/{mm}/{dd}/all-conf

A public instance runs at ``ncaa-api.henrygd.me``; it is rate limited and may
require an ``x-ncaa-key`` header. For daily production use the project is
self-hostable, which removes the dependency on someone else's uptime.

UNVERIFIED: unreachable from the environment this was written in. Run
``python -m whul.cli probe-ncaa-api`` from a machine with access.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE = "https://ncaa-api.henrygd.me"
CACHE = Path("data/cache/ncaa_api")
REQUEST_PAUSE = 0.5
TIMEOUT = 30

#: Our league key -> (sport path, division path).
SPORT_PATHS = {
    "ncaaf": ("football", "fbs"),
    "ncaam": ("basketball-men", "d1"),
    "ncaaw": ("basketball-women", "d1"),
    "ncaabaseball": ("baseball", "d1"),
    "ncaasoftball": ("softball", "d1"),
}


def _get(path: str, cache_key: str | None = None) -> dict:
    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        if cached.exists():
            return json.loads(cached.read_text())

    response = requests.get(
        f"{BASE}{path}", timeout=TIMEOUT, headers={"User-Agent": "whul-fantasy/0.1"}
    )
    response.raise_for_status()
    payload = response.json()

    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload))

    time.sleep(REQUEST_PAUSE)
    return payload


def scoreboard(league: str, day: date) -> dict:
    sport, division = SPORT_PATHS[league]
    path = f"/scoreboard/{sport}/{division}/{day.year}/{day.month:02d}/{day.day:02d}/all-conf"
    return _get(path, cache_key=f"{league}/{day.isoformat()}")


#: The API exposes several name forms and does not populate all of them; the
#: first non-empty one wins. `full` came back blank for football, where `short`
#: carries the value.
NAME_KEYS = ("full", "short", "seo", "char6", "sixCharacter", "nameShort")
CONFERENCE_KEYS = ("conferenceName", "name", "conferenceSeo")


def _team_name(side: dict) -> str:
    names = side.get("names") or {}
    for key in NAME_KEYS:
        value = names.get(key)
        if value:
            return str(value)
    for key in ("nameShort", "name", "shortName"):
        if side.get(key):
            return str(side[key])
    return ""


def _team_conference(side: dict) -> str:
    conferences = side.get("conferences") or []
    if conferences and isinstance(conferences[0], dict):
        for key in CONFERENCE_KEYS:
            if conferences[0].get(key):
                return str(conferences[0][key])
    for key in ("conference", "conferenceName"):
        if side.get(key):
            return str(side[key])
    return ""


def parse_scoreboard(payload: dict, league: str, day: date) -> list[dict]:
    """Flatten one date's games into rows matching the ESPN adapter's shape.

    Keeping the column names identical means the same scoring modules work
    against either source.
    """
    rows: list[dict] = []
    for game in payload.get("games", []):
        inner = game.get("game", game)
        home = inner.get("home", {}) or {}
        away = inner.get("away", {}) or {}

        def score(side: dict) -> float | None:
            try:
                return float(side.get("score"))
            except (TypeError, ValueError):
                return None

        rows.append(
            {
                "season": day.year,
                "game_id": inner.get("gameID") or inner.get("url", ""),
                "game_date": day.isoformat(),
                "season_type": 2,
                "completed": str(inner.get("gameState", "")).lower() == "final",
                "home_team": _team_name(home),
                "away_team": _team_name(away),
                "home_conference": _team_conference(home),
                "away_conference": _team_conference(away),
                "home_score": score(home),
                "away_score": score(away),
                "notes": inner.get("title", "") or inner.get("bracketRound", ""),
            }
        )
    return rows


def load_team_results(league: str, days: list[date], verbose: bool = True) -> pd.DataFrame:
    rows: list[dict] = []
    for index, day in enumerate(days):
        try:
            rows.extend(parse_scoreboard(scoreboard(league, day), league, day))
        except Exception:
            continue
        if verbose and index and index % 50 == 0:
            print(f"    {index}/{len(days)} dates, {len(rows):,} games", flush=True)
    return pd.DataFrame(rows)


def probe(league: str = "ncaaf", day: date | None = None) -> dict:
    """Reachability and shape check, reporting what ESPN could not give us."""
    day = day or date(2025, 11, 15)
    result: dict[str, object] = {"league": league, "date": day.isoformat(), "base": BASE}
    try:
        payload = scoreboard(league, day)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "?")
        result["scoreboard"] = f"FAILED ({status}): {type(exc).__name__}: {exc}"
        return result

    result["scoreboard"] = "ok"
    rows = parse_scoreboard(payload, league, day)
    result["games"] = len(rows)
    if rows:
        with_conf = sum(1 for r in rows if r["home_conference"] and r["away_conference"])
        result["conference_coverage"] = f"{with_conf}/{len(rows)}"
        names = {r["home_team"] for r in rows} | {r["away_team"] for r in rows}
        result["teams_seen"] = len(names - {""})
        result["sample"] = rows[0]

    # When extraction comes up empty the payload keys are what is needed to fix
    # it, so report them rather than requiring another round trip.
    raw_games = payload.get("games") or []
    if raw_games and (not rows or not rows[0]["home_team"] or not rows[0]["home_conference"]):
        inner = raw_games[0].get("game", raw_games[0])
        result["raw_game_keys"] = sorted(inner)
        home = inner.get("home") or {}
        result["raw_home_keys"] = sorted(home)
        result["raw_home_names"] = home.get("names")
        result["raw_home_conferences"] = home.get("conferences")
    return result
