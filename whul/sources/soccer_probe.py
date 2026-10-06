"""Which richer soccer sources this project can actually reach, and what they hold.

The player scoring reads appearances, goals, assists and cards, and a defender
or goalkeeper earns almost nothing past turning up. Four places could carry
more, and each was found blocked from the sandbox this was written in, so this
asks them from where the nightly runs -- GitHub Actions -- and prints what
came back:

    espn          the match summaries we already read for game records: every
                  stat name on a player line, and whether a substitute's minute
                  can be read, which a 60-minute clean sheet needs
    api-football  api-sports.io, a paid API ($19 a month for 7,500 requests a
                  day): tackles, interceptions, blocks, key passes, duels,
                  dribbles, a rating. Needs API_FOOTBALL_KEY in the
                  environment; never printed. Its /status call costs nothing
                  against the quota
    understat     xG for the five big leagues and nothing else, shot by shot,
                  which is the only free source tying an xG to the shot that
                  scored
    mls           MLS's own stats API, Opta-fed, for MLS alone

Reads and writes nothing, and every check stands on its own: one host refusing
says nothing about the next.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta

import requests

from whul import clock

TIMEOUT = 30
AGENT = {"User-Agent": "Mozilla/5.0 (whul-fantasy probe)"}

#: A finished Premier League Saturday in each of the two seasons asked about:
#: the history a benchmark is built from, and the season being played.
OLD_DAY = date(2024, 9, 21)
API_FOOTBALL = "https://v3.football.api-sports.io"
#: api-sports' own ids: the Premier League, and MLS.
AF_LEAGUES = {"Premier League": 39, "MLS": 253}


def _failed(exc: Exception) -> str:
    status = getattr(getattr(exc, "response", None), "status_code", "?")
    return f"FAILED ({status}): {type(exc).__name__}: {str(exc)[:200]}"


def _recent_saturday(today: date | None = None) -> date:
    day = (today or clock.today()) - timedelta(days=2)
    return day - timedelta(days=(day.weekday() - 5) % 7)


# --- ESPN ---------------------------------------------------------------------

def probe_espn(day: date) -> dict:
    """One finished Premier League match's summary: the stat names on a player
    line, and how a substitution is dated."""
    out: dict[str, object] = {}
    base = "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1"
    try:
        board = requests.get(f"{base}/scoreboard", params={"dates": day.strftime("%Y%m%d")},
                             timeout=TIMEOUT, headers=AGENT)
        board.raise_for_status()
        events = [e for e in board.json().get("events", [])
                  if ((e.get("status") or {}).get("type") or {}).get("completed")]
        out["finished_matches"] = len(events)
        if not events:
            return out
        event = events[0]
        out["match"] = f"{event.get('name')} ({event.get('id')})"
        summary = requests.get(f"{base}/summary", params={"event": event["id"]},
                               timeout=TIMEOUT, headers=AGENT)
        summary.raise_for_status()
        payload = summary.json()
    except Exception as exc:  # noqa: BLE001
        out["status"] = _failed(exc)
        return out

    names: dict[str, set] = {"outfield": set(), "goalkeeper": set()}
    sub_shape = None
    for block in payload.get("rosters") or []:
        for entry in block.get("roster") or []:
            if not isinstance(entry, dict):
                continue
            position = ((entry.get("position") or {}).get("abbreviation")
                        or ((entry.get("athlete") or {}).get("position") or {})
                        .get("abbreviation") or "")
            kind = "goalkeeper" if str(position).upper() in ("G", "GK") else "outfield"
            names[kind] |= {str(s.get("name")) for s in entry.get("stats") or []
                            if isinstance(s, dict)}
            came_on = entry.get("subbedIn")
            if sub_shape is None and isinstance(came_on, dict) and came_on.get("didSub"):
                sub_shape = json.dumps(came_on)[:300]
    out["outfield_stat_names"] = sorted(names["outfield"])
    out["goalkeeper_stat_names"] = sorted(names["goalkeeper"])
    out["substitute_entry"] = sub_shape or "no subbedIn object with a minute"
    # The other place a minute can be read: the match's key events.
    subs = [e for e in payload.get("keyEvents") or []
            if "substitution" in str((e.get("type") or {}).get("text", "")).lower()]
    out["key_event_substitutions"] = len(subs)
    if subs:
        first = subs[0]
        out["key_event_sample"] = {
            "clock": (first.get("clock") or {}).get("displayValue"),
            "participants": len(first.get("participants") or []),
            "text": str(first.get("text") or "")[:120],
        }
    return out


# --- API-Football -------------------------------------------------------------

def probe_api_football(seasons: tuple[int, ...]) -> dict:
    """The plan and its quota, then one fixture's player statistics a season.

    Three requests a season asked about plus the free status call. A free key
    is enough to see the fields; whether the free plan reaches the season
    being played is one of the things this says.
    """
    key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not key:
        return {"status": "NO KEY -- add an API_FOOTBALL_KEY repository secret "
                          "(a free key from dashboard.api-football.com is enough "
                          "to probe)"}
    session = requests.Session()
    session.headers.update({"x-apisports-key": key, **AGENT})
    out: dict[str, object] = {}

    def get(path: str, **params):
        response = session.get(f"{API_FOOTBALL}{path}", params=params, timeout=TIMEOUT)
        response.raise_for_status()
        out["requests_left_today"] = response.headers.get(
            "x-ratelimit-requests-remaining", "?")
        body = response.json()
        # The API answers 200 with its refusals in `errors`.
        if body.get("errors"):
            raise RuntimeError(json.dumps(body["errors"])[:300])
        return body

    try:
        status = get("/status").get("response") or {}
        sub = status.get("subscription") or {}
        req = status.get("requests") or {}
        out["plan"] = f"{sub.get('plan')} (active={sub.get('active')}, ends {sub.get('end')})"
        out["quota"] = f"{req.get('current')} of {req.get('limit_day')} used today"
    except Exception as exc:  # noqa: BLE001
        out["status"] = _failed(exc)
        return out

    for season in seasons:
        label = f"season_{season}"
        try:
            # Its seasons are named for the year they start in, as ours are not.
            fixtures = get("/fixtures", league=AF_LEAGUES["Premier League"],
                           season=season, status="FT").get("response") or []
            out[f"{label}_finished_fixtures"] = len(fixtures)
            if not fixtures:
                continue
            fixture = fixtures[-1]["fixture"]["id"]
            teams = get("/fixtures/players", fixture=fixture).get("response") or []
            players = [p for t in teams for p in t.get("players") or []]
            out[f"{label}_players_in_fixture"] = len(players)
            if players:
                stats = (players[0].get("statistics") or [{}])[0]
                out[f"{label}_stat_groups"] = {
                    group: sorted(values) if isinstance(values, dict) else values
                    for group, values in stats.items()
                }
        except Exception as exc:  # noqa: BLE001
            out[label] = _failed(exc)
    try:
        mls = get("/leagues", id=AF_LEAGUES["MLS"]).get("response") or []
        coverage = ((mls[0].get("seasons") or [{}])[-1].get("coverage") or {}) if mls else {}
        out["mls_latest_season_coverage"] = {
            k: v for k, v in (coverage.get("fixtures") or {}).items()
        } or "none listed"
    except Exception as exc:  # noqa: BLE001
        out["mls"] = _failed(exc)
    return out


# --- Understat ----------------------------------------------------------------

def probe_understat(season: int) -> dict:
    """The league page and one match's shots, read the two ways the site has
    served them: inside the page, and from its own JSON endpoints."""
    out: dict[str, object] = {}
    session = requests.Session()
    session.headers.update(AGENT)
    match_id = None
    try:
        page = session.get(f"https://understat.com/league/EPL/{season}", timeout=TIMEOUT)
        out["league_page"] = f"{page.status_code}, {len(page.text):,} bytes"
        embedded = re.findall(r"var (\w+)\s*=\s*JSON\.parse", page.text)
        out["embedded_json"] = embedded or "none"
        found = re.search(r"/match/(\d+)", page.text)
        match_id = found.group(1) if found else None
    except Exception as exc:  # noqa: BLE001
        out["league_page"] = _failed(exc)
    try:
        data = session.get(f"https://understat.com/getLeagueData/EPL/{season}",
                           timeout=TIMEOUT,
                           headers={"X-Requested-With": "XMLHttpRequest"})
        out["league_endpoint"] = f"{data.status_code}"
        if data.ok:
            body = data.json()
            out["league_endpoint_keys"] = sorted(body)[:10]
            dates = body.get("dates") or []
            done = [m for m in dates if m.get("isResult")]
            if done and not match_id:
                match_id = done[-1].get("id")
    except Exception as exc:  # noqa: BLE001
        out["league_endpoint"] = _failed(exc)
    if not match_id:
        return out
    out["match_id"] = match_id
    try:
        shots = session.get(f"https://understat.com/getMatchData/{match_id}",
                            timeout=TIMEOUT,
                            headers={"X-Requested-With": "XMLHttpRequest"})
        if shots.ok:
            body = shots.json()
            listed = (body.get("shots") or {})
            every = [s for side in ("h", "a") for s in listed.get(side) or []]
            goals = [s for s in every if s.get("result") == "Goal"]
            out["match_endpoint"] = f"{len(every)} shots, {len(goals)} goals"
            if goals:
                g = goals[0]
                out["a_goal_with_its_xg"] = {k: g.get(k) for k in
                                             ("player", "minute", "xG", "situation",
                                              "shotType")}
        else:
            page = session.get(f"https://understat.com/match/{match_id}", timeout=TIMEOUT)
            out["match_endpoint"] = f"{shots.status_code}; page {page.status_code}"
            out["match_embedded_json"] = re.findall(
                r"var (\w+)\s*=\s*JSON\.parse", page.text) or "none"
    except Exception as exc:  # noqa: BLE001
        out["match_endpoint"] = _failed(exc)
    return out


# --- MLS ----------------------------------------------------------------------

def probe_mls(season: int) -> dict:
    """MLS's stats API: whether it answers at all, and what a player's match
    line carries. Its paths are documented only by people who found them."""
    out: dict[str, object] = {}
    base = "https://stats-api.mlssoccer.com/v1"
    session = requests.Session()
    session.headers.update(AGENT)
    try:
        matches = session.get(f"{base}/matches", timeout=TIMEOUT, params={
            "competition_opta_id": 98, "season_opta_id": season, "page_size": 5,
            "order_by": "-match_date"})
        out["matches"] = f"{matches.status_code}, {len(matches.content):,} bytes"
        rows = matches.json() if matches.ok else []
        rows = rows if isinstance(rows, list) else rows.get("data") or []
        game = next((m.get("match_game_id") or m.get("opta_id") for m in rows
                     if isinstance(m, dict)), None)
        out["match_keys"] = sorted(rows[0])[:25] if rows else "none"
    except Exception as exc:  # noqa: BLE001
        out["matches"] = _failed(exc)
        return out
    if not game:
        return out
    try:
        lines = session.get(f"{base}/players/matches", timeout=TIMEOUT, params={
            "match_game_id": game, "include": ["statistics", "player"],
            "page_size": 3})
        out["player_lines"] = f"{lines.status_code}"
        body = lines.json() if lines.ok else []
        body = body if isinstance(body, list) else body.get("data") or []
        if body:
            stats = body[0].get("statistics") or body[0]
            out["player_stat_names"] = sorted(stats)[:80] if isinstance(stats, dict) else str(stats)[:300]
    except Exception as exc:  # noqa: BLE001
        out["player_lines"] = _failed(exc)
    return out


def probe(season: int | None = None) -> dict[str, dict]:
    """Every source, each on its own."""
    current = season or clock.today().year
    return {
        "espn (history)": probe_espn(OLD_DAY),
        "espn (this season)": probe_espn(_recent_saturday()),
        "api-football": probe_api_football((OLD_DAY.year, current)),
        "understat": probe_understat(current),
        "mls stats api": probe_mls(current),
    }
