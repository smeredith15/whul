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
from datetime import date, timedelta
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


def usable_events(events: list) -> list[dict]:
    """The entries in a season list that are actually events.

    ESPN pads a year's golf scoreboard with empty objects -- the 2022 response
    carries 51 entries of which 25 have no keys at all. They are not events that
    failed to finish; they are not events. Counting them made half a season look
    unfinished and hid that the other half was missing outright.
    """
    return [
        e for e in events
        if isinstance(e, dict) and (e.get("id") or e.get("name") or e.get("date"))
    ]


def season_events(league: str, season: int) -> list[dict]:
    """Every event ESPN lists for a season.

    Each request shape is tried and the one returning the most usable events
    wins. Taking the first non-empty answer is not enough: ESPN answers some
    season requests with a half-populated list padded out with empty objects,
    which reads as a successful response and silently halves the season.
    """
    sport, path = LEAGUE_PATHS[league]
    url = f"{BASE}/{sport}/{path}/scoreboard"
    cached = CACHE / f"{league}/season/{season}.json"
    if cached.exists():
        return usable_events(json.loads(cached.read_text()).get("events", []))

    best: dict | None = None
    best_count = -1
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
        count = len(usable_events(payload.get("events") or []))
        if count > best_count:
            best, best_count = payload, count

    if best is None:
        raise last if last else RuntimeError(f"no season shape succeeded for {league}")

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(best))
    return usable_events(best.get("events", []))


class EventUnavailable(RuntimeError):
    """No endpoint served this event's field.

    Its own class so a caller can tell "ESPN has nothing for this one event"
    from a network fault or a schema change, and skip the event rather than
    abandoning the season.
    """


#: Endpoints that serve one event's field, most specific first. ``summary`` is
#: what the team sports use, but golf answers 404 to it and serves the field at
#: ``leaderboard`` instead; racing has answered 502 to ``summary`` for a
#: completed race. Trying them in turn costs nothing once one works, since only
#: the successful response is cached.
EVENT_ENDPOINTS = {
    "pga": ("leaderboard", "summary"),
    "nascar": ("summary", "leaderboard", "scoreboard"),
    "f1": ("summary", "leaderboard", "scoreboard"),
}
DEFAULT_EVENT_ENDPOINTS = ("summary", "leaderboard")


def event_summary(league: str, event_id: str) -> dict:
    """One event's full result, cached by id.

    A finished event never changes, so this is cached permanently; an event
    still in progress is cached too, and the nightly job clears the entry for
    anything not yet final.

    An endpoint that answers 404, 502 or a payload with no field is treated as
    the wrong one and the next is tried. A 502 is included deliberately: ESPN
    returns it for endpoints that exist for other sports but not this one, so
    treating it as a transient server fault would abandon a league that a
    different path serves perfectly.
    """
    sport, path = LEAGUE_PATHS[league]
    cached = CACHE / f"{league}/event/{event_id}.json"
    if cached.exists():
        return json.loads(cached.read_text())

    best: dict | None = None
    last: Exception | None = None
    for endpoint in EVENT_ENDPOINTS.get(league, DEFAULT_EVENT_ENDPOINTS):
        url = f"{BASE}/{sport}/{path}/{endpoint}"
        try:
            payload = _get(url, {"event": event_id})
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in (400, 404, 500, 502, 503):
                raise
            last = exc
            continue
        if _competitors(payload):
            best = payload
            break
        if best is None:
            best = payload

    if best is None:
        tried = ", ".join(EVENT_ENDPOINTS.get(league, DEFAULT_EVENT_ENDPOINTS))
        raise EventUnavailable(
            f"no endpoint served {league} event {event_id} (tried {tried})"
        ) from last

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(best))
    return best


#: Status names ESPN uses for an event that has finished. It reports completion
#: inconsistently across sports and across seasons -- sometimes a ``completed``
#: boolean, sometimes only a state or a name -- and reading one of them alone
#: dropped half of the 2022 PGA season.
FINAL_STATUS_NAMES = (
    "STATUS_FINAL", "STATUS_COMPLETE", "STATUS_PLAY_COMPLETE", "STATUS_FINAL_OT",
)

#: How long after an event ESPN is given to mark it finished before its date is
#: taken as the answer. A tournament runs four days and a race meeting three, so
#: a week clears the event itself with room to spare.
SETTLED_AFTER_DAYS = 7


def _statuses(event: dict) -> list[dict]:
    """Every place this payload might keep the event's status."""
    found = [event.get("status") or {}]
    for competition in event.get("competitions") or []:
        if isinstance(competition, dict) and competition.get("status"):
            found.append(competition["status"])
    return found


def _is_final(event: dict, today: date | None = None) -> bool:
    """Whether an event has finished, by any of the ways ESPN says so.

    Falls back to the calendar. An event whose date is more than a week past is
    over whatever the payload says, and treating it as unfinished is the
    expensive mistake: the event is dropped, every athlete's season total falls
    by what they earned in it, and nothing anywhere reports a gap -- the numbers
    are simply smaller. A cancelled event survives this only to be dropped by
    the scorer, which finds no finishing positions in it.
    """
    for status in _statuses(event):
        kind = status.get("type") or {}
        if kind.get("completed"):
            return True
        if str(kind.get("state", "")).lower() == "post":
            return True
        if str(kind.get("name", "")).upper() in FINAL_STATUS_NAMES:
            return True

    stamp = _event_date(event)
    if not stamp:
        return False
    try:
        played = date.fromisoformat(stamp)
    except ValueError:
        return False
    return (today or date.today()) - played > timedelta(days=SETTLED_AFTER_DAYS)


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
    listed: dict[int, int] = {}
    for season in seasons:
        events = season_events(league, season)
        finished = [e for e in events if _is_final(e)]
        listed[season] = len(events)
        if verbose:
            print(
                f"{league} {season}: {len(finished)} completed of {len(events)} events",
                flush=True,
            )
        _check_season_ran_out(league, season, events, finished)
        unserved: list[str] = []
        for event in finished:
            event_id = str(event.get("id") or "")
            if not event_id:
                continue
            # The season scoreboard sometimes carries the whole field already.
            # Where it does, the per-event request is pure cost -- one request
            # for the season instead of fifty.
            field = _competitors(event)
            if not field:
                try:
                    field = _competitors(event_summary(league, event_id))
                except EventUnavailable:
                    # ESPN serves no result for a few old events -- an
                    # abandoned tournament, a renumbered id. Losing one of a
                    # season's fifty is a rounding error; letting it end the
                    # pull loses five seasons, which is what it used to do.
                    unserved.append(f"{_event_name(event)} ({event_id})")
                    continue
            for entry in field:
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
        _report_unserved(league, season, unserved, len(finished), verbose)

    _check_seasons_agree(league, listed, verbose)
    return pd.DataFrame(rows)


#: How far below its neighbours a season may fall before it is not the same
#: season they are. A tour plays roughly the same number of events every year,
#: so a year at half the usual count is missing data, not playing less.
SHORT_SEASON_LIMIT = 0.70


def _check_seasons_agree(league: str, listed: dict[int, int], verbose: bool) -> None:
    """Refuse a season the others show to be incomplete.

    The other checks judge a season against itself, which cannot see this: every
    event ESPN returned was finished and served, there were simply half as many
    as the sport plays. Only its neighbours reveal that, and the cost of missing
    it is a window whose totals are quietly halved.
    """
    real = {s: n for s, n in listed.items() if n}
    if len(real) < 3:
        return
    counts = sorted(real.values())
    median = counts[len(counts) // 2]
    short = {
        season: n for season, n in real.items() if n < median * SHORT_SEASON_LIMIT
    }
    if not short:
        return
    detail = ", ".join(f"{s}: {n}" for s, n in sorted(short.items()))
    raise RuntimeError(
        f"{league}: {detail} events, against {median} in a typical season here. "
        f"A tour does not play half a year, so those seasons are missing events "
        f"rather than short of them -- and a benchmark drawn from them would "
        f"understate every athlete in that window. Clear "
        f"data/cache/espn/{league}/season/ and re-run to refetch; if the count "
        f"holds, ESPN does not have those seasons."
    )


#: How much of an elapsed season may be missing before it is not worth having.
#: Well below this and the season understates every athlete in it, invisibly:
#: the totals are simply lower, with nothing to say a tournament is absent.
FINISHED_LIMIT = 0.85


def _check_season_ran_out(
    league: str, season: int, events: list[dict], finished: list[dict],
    today: date | None = None,
) -> None:
    """Refuse an elapsed season that lost too many of its events.

    A season still being played is legitimately part-finished, so only a season
    whose last listed event is well past is judged. This is the failure the
    unserved-event check does not catch: those events ESPN refuses to serve,
    while these it simply never marks as done.
    """
    if not events:
        return
    today = today or date.today()
    last = max((_event_date(e) for e in events), default="")
    try:
        ended = date.fromisoformat(last) if last else None
    except ValueError:
        ended = None
    if ended is None or today - ended <= timedelta(days=SETTLED_AFTER_DAYS):
        return
    if len(finished) >= len(events) * FINISHED_LIMIT:
        return
    raise RuntimeError(
        f"{league} {season}: only {len(finished)} of {len(events)} listed events "
        f"came back finished, and the season ended on {last}. Below "
        f"{FINISHED_LIMIT:.0%} the totals understate every athlete in it rather "
        f"than miss an event, so the season is refused instead of scored."
    )


#: How much of a season may go unserved before the season is not worth having.
#: A benchmark drawn from a season missing a tenth of its events understates
#: every athlete in it, and does so invisibly -- the totals are simply lower.
UNSERVED_LIMIT = 0.10


def _report_unserved(
    league: str, season: int, unserved: list[str], finished: int, verbose: bool
) -> None:
    """Say what was skipped, and refuse a season with too little of it left."""
    if not unserved:
        return
    listed = ", ".join(unserved[:5]) + (" ..." if len(unserved) > 5 else "")
    if finished and len(unserved) > finished * UNSERVED_LIMIT:
        raise RuntimeError(
            f"{league} {season}: ESPN served no result for {len(unserved)} of "
            f"{finished} completed events ({listed}). That is more than "
            f"{UNSERVED_LIMIT:.0%} of the season, so the totals would understate "
            f"every athlete in it rather than miss an event."
        )
    if verbose:
        print(
            f"{league} {season}: skipped {len(unserved)} event(s) ESPN served no "
            f"result for: {listed}",
            flush=True,
        )


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


def diagnose_season(league: str, season: int) -> dict:
    """Why a season's events do or do not read as finished.

    The completeness guard can only say a season came back short. This says
    what the short events look like -- whether they carry a date, what status
    shape they have, what they are called -- which is what a fix has to be
    written against. The season list is cached, so this costs nothing to run.
    """
    events = season_events(league, season)
    report: dict = {
        "league": league, "season": season,
        "events": len(events),
        "finished": sum(1 for e in events if _is_final(e)),
        "with_date": sum(1 for e in events if _event_date(e)),
        "status_shapes": {},
        "unfinished": [],
    }

    for event in events:
        kinds = []
        for status in _statuses(event):
            kind = status.get("type") or {}
            kinds.append("|".join(
                f"{k}={kind[k]}" for k in ("completed", "state", "name") if k in kind
            ) or "type empty")
        # "no status" and "a status saying nothing" are different problems and
        # want different fixes, so they are not collapsed into one label.
        shape = ";".join(kinds) if any(_statuses(event)) else "no status at all"
        report["status_shapes"][shape] = report["status_shapes"].get(shape, 0) + 1

        if not _is_final(event):
            report["unfinished"].append({
                "id": str(event.get("id") or ""),
                "name": _event_name(event)[:44],
                "date": _event_date(event) or "(none)",
                "keys": ",".join(sorted(event.keys()))[:90],
                "status": shape[:60],
            })
    report["unfinished"] = report["unfinished"][:12]
    return report


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

    # Whether the season list already carries the field decides whether a
    # per-event request is needed at all, so it is reported before one is made.
    inline = _competitors(event)
    report["stages"]["inline_field"] = {
        "ok": True,
        "field_in_season_list": len(inline),
        "event_keys": sorted(event.keys()),
        "competition_keys": sorted(
            (event.get("competitions") or [{}])[0].keys()
        ) if event.get("competitions") else [],
        "note": "the season list already carries the field; no per-event "
                "request needed" if inline else
                "the season list has no field, so an event endpoint must serve it",
    }

    try:
        payload = event if inline else event_summary(league, event_id)
    except Exception as exc:  # noqa: BLE001
        report["stages"]["event_summary"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "endpoints_tried": list(EVENT_ENDPOINTS.get(league, DEFAULT_EVENT_ENDPOINTS)),
            "note": "every endpoint refused -- the competition_keys above say "
                    "what the season list holds instead",
        }
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
