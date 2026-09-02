"""ESPN site API for the individual sports -- golf and NASCAR.

These leagues do not fit the date-scoreboard shape the team sports use. A golf
tournament runs Thursday to Sunday and a race meeting spans a weekend, so a
season is fetched as a list of events and each event's final standings are read
once, rather than walking every calendar date:

    season events: /apis/site/v2/sports/{sport}/{league}/scoreboard?dates=YYYY
    one event:     /apis/site/v2/sports/{sport}/{league}/summary?event={id}

That makes both cheap to keep current -- a nightly update re-reads the events
that are in progress or newly final, on the order of one or two requests, not
one per athlete.

UNVERIFIED: written where ESPN is blocked by egress policy. Run
``python -m whul.cli probe pga`` (or ``nascar``) from a machine with access
before trusting it; the probe reports which stage fails and what the response
actually contained.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from whul.sources.espn import BASE, TIMEOUT, _get

CACHE = Path("data/cache/espn")

LEAGUE_PATHS = {
    "pga": ("golf", "pga"),
    "nascar": ("racing", "nascar-premier"),
    "f1": ("racing", "f1"),
}

#: Golf and racing seasons run within a calendar year, so the season label is
#: the year -- unlike the team sports, none of these straddles a new year.
SEASON_IS_CALENDAR_YEAR = True

#: A field entry that did not finish. ESPN reports these as a status rather
#: than a position, and they must not be read as a finishing place.
NON_FINISH_STATUSES = ("cut", "wd", "withdrawn", "dnf", "dq", "disqualified", "mdf")


def scoreboard_variants(season: int) -> list[dict]:
    """Request shapes to try for a season's event list, most specific first.

    ESPN has answered season requests to different leagues with ``dates=YYYY``
    and with an explicit range; trying both costs nothing when the first works,
    since only the successful response is cached.
    """
    return [
        {"dates": str(season), "limit": 200},
        {"dates": str(season)},
        {"dates": f"{season}0101-{season}1231", "limit": 200},
    ]


def season_events(league: str, season: int) -> list[dict]:
    """Every event ESPN lists for a season.

    A shape that returns no events is treated as suspect and the next one is
    tried -- a 200 with an empty list is how ESPN reports a parameter it accepts
    but does not honor, and accepting it would yield a silently empty season.
    """
    sport, path = LEAGUE_PATHS[league]
    url = f"{BASE}/{sport}/{path}/scoreboard"
    cached = CACHE / f"{league}/season/{season}.json"
    if cached.exists():
        return json.loads(cached.read_text()).get("events", [])

    best: dict | None = None
    last: Exception | None = None
    for params in scoreboard_variants(season):
        try:
            payload = _get(url, params)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in (400, 404):
                raise
            last = exc
            continue
        if payload.get("events"):
            best = payload
            break
        if best is None:
            best = payload

    if best is None:
        raise last if last else RuntimeError(f"no season shape succeeded for {league}")

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(best))
    return best.get("events", [])


def event_summary(league: str, event_id: str) -> dict:
    """One event's full result, cached by id.

    A finished event never changes, so this is cached permanently; an event
    still in progress is cached too, and the nightly job clears the entry for
    anything not yet final.
    """
    sport, path = LEAGUE_PATHS[league]
    url = f"{BASE}/{sport}/{path}/summary"
    return _get(url, {"event": event_id}, cache_key=f"{league}/event/{event_id}")


def _is_final(event: dict) -> bool:
    status = event.get("status") or {}
    return bool((status.get("type") or {}).get("completed"))


def _competitors(payload: dict) -> list[dict]:
    """The field, wherever this response shape happens to keep it.

    Golf summaries have carried the leaderboard under ``competitions`` and under
    a top-level ``leaderboard``; racing keeps it under ``competitions``. Reading
    every plausible location beats guessing one and returning an empty field.
    """
    for competition in payload.get("competitions") or []:
        if competition.get("competitors"):
            return competition["competitors"]
    for block in payload.get("leaderboard") or []:
        if isinstance(block, dict) and block.get("competitors"):
            return block["competitors"]
    header = payload.get("header") or {}
    for competition in header.get("competitions") or []:
        if competition.get("competitors"):
            return competition["competitors"]
    return []


def _athlete_name(entry: dict) -> str:
    athlete = entry.get("athlete") or {}
    for key in ("displayName", "fullName", "shortName", "name"):
        if athlete.get(key):
            return str(athlete[key])
    for key in ("displayName", "name"):
        if entry.get(key):
            return str(entry[key])
    return ""


def _position(entry: dict) -> str:
    """Finishing position as the feed states it, including 'T12' and 'CUT'.

    Left as a string on purpose: the scoring modules decide what a tie or a
    missed cut is worth, and collapsing 'T12' to 12 here would throw away the
    distinction between a tie and a solo finish before anyone can use it.
    """
    status = entry.get("status") or {}
    position = status.get("position") or entry.get("position") or {}
    if isinstance(position, dict):
        for key in ("displayName", "displayValue", "abbreviation", "id"):
            if position.get(key):
                return str(position[key])
    elif position not in (None, ""):
        return str(position)
    for key in ("order", "place", "rank"):
        if entry.get(key) not in (None, ""):
            return str(entry[key])
    type_name = (status.get("type") or {}).get("shortDetail") or status.get("displayValue")
    return str(type_name) if type_name else ""


def _event_date(event: dict) -> str:
    raw = event.get("date") or ""
    return str(raw)[:10]


def _event_name(event: dict) -> str:
    for key in ("name", "shortName"):
        if event.get(key):
            return str(event[key])
    return ""


def load_results(league: str, seasons: list[int], verbose: bool = True) -> pd.DataFrame:
    """One row per athlete per event, across the requested seasons.

    Only completed events are read: an event in progress has positions that will
    change, and scoring a live leaderboard would credit a Thursday leader with a
    win.
    """
    rows: list[dict] = []
    for season in seasons:
        events = season_events(league, season)
        finished = [e for e in events if _is_final(e)]
        if verbose:
            print(
                f"{league} {season}: {len(finished)} completed of {len(events)} events",
                flush=True,
            )
        for event in finished:
            event_id = str(event.get("id") or "")
            if not event_id:
                continue
            payload = event_summary(league, event_id)
            for entry in _competitors(payload):
                name = _athlete_name(entry)
                if not name:
                    continue
                rows.append(
                    {
                        "season": season,
                        "event_id": event_id,
                        "tournament": _event_name(event),
                        "date": _event_date(event),
                        "player": name,
                        "driver": name,
                        "position": _position(entry),
                    }
                )
    return pd.DataFrame(rows)


def daily_update_cost(league: str, season: int | None = None) -> float:
    """Seconds for one incremental update: the season list plus live events.

    This is what the nightly job pays, and it is the number that decides whether
    a source is usable -- the backfill is paid once.
    """
    import time

    season = season or date.today().year
    started = time.monotonic()
    events = season_events(league, season)
    live = [e for e in events if not _is_final(e)]
    for event in live[:2]:
        event_summary(league, str(event.get("id") or ""))
    return time.monotonic() - started


def probe(league: str = "pga", season: int | None = None) -> dict:
    """Check the source end to end and report which stage fails.

    Every stage records what it actually got, so a failure names the response
    rather than only the exception.
    """
    season = season or (date.today().year - 1)
    report: dict = {"league": league, "season": season, "stages": {}}

    try:
        events = season_events(league, season)
    except Exception as exc:  # noqa: BLE001 -- the probe reports, it does not raise
        report["stages"]["season_events"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return report

    finished = [e for e in events if _is_final(e)]
    report["stages"]["season_events"] = {
        "ok": bool(events),
        "events": len(events),
        "completed": len(finished),
        "sample": [_event_name(e) for e in events[:5]],
    }
    if not finished:
        report["stages"]["season_events"]["note"] = (
            "no completed events -- season may not have started, or the date "
            "parameter was accepted without being honored"
        )
        return report

    event = finished[0]
    event_id = str(event.get("id") or "")
    try:
        payload = event_summary(league, event_id)
    except Exception as exc:  # noqa: BLE001
        report["stages"]["event_summary"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return report

    field = _competitors(payload)
    report["stages"]["event_summary"] = {
        "ok": bool(field),
        "event": _event_name(event),
        "event_id": event_id,
        "field_size": len(field),
        "top_keys": sorted(payload.keys())[:15],
    }
    if not field:
        report["stages"]["event_summary"]["note"] = (
            "no field found -- the leaderboard lives somewhere _competitors() "
            "does not look; the top-level keys above say where to add it"
        )
        return report

    named = [e for e in field if _athlete_name(e)]
    placed = [e for e in field if _position(e)]
    report["stages"]["parse"] = {
        "ok": bool(named) and bool(placed),
        "named": f"{len(named)}/{len(field)}",
        "with_position": f"{len(placed)}/{len(field)}",
        "sample": [
            {"player": _athlete_name(e), "position": _position(e)} for e in field[:5]
        ],
    }
    return report
