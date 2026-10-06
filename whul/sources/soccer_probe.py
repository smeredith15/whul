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


# --- ESPN commentary, every competition -----------------------------------------

#: Where to look for one finished match of each competition a player can score
#: in: a date the competition played on, searched forward a few days.
COMMENTARY_CHECKS = (
    ("epl", date(2024, 9, 21)), ("mls", date(2024, 9, 21)),
    ("ucl", date(2024, 10, 1)), ("uel", date(2024, 10, 2)),
    ("efl_cup", date(2024, 10, 29)), ("dfbpokal", date(2024, 10, 29)),
    ("coppaitalia", date(2024, 12, 17)), ("facup", date(2025, 1, 11)),
    ("copadelrey", date(2025, 1, 14)), ("coupedefrance", date(2025, 1, 14)),
)
SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
#: How an Opta-style line opens, by what it is about.
LINE_KINDS = {
    "goal": re.compile(r"^Goal!", re.IGNORECASE),
    "attempt": re.compile(r"^Attempt (saved|missed|blocked)", re.IGNORECASE),
    "woodwork": re.compile(r"hits the (bar|post|left post|right post)", re.IGNORECASE),
    "penalty": re.compile(r"^Penalty", re.IGNORECASE),
    "corner": re.compile(r"^Corner", re.IGNORECASE),
    "foul": re.compile(r"^Foul by|wins a free kick", re.IGNORECASE),
}


def _first_finished(path: str, start: date, days: int = 10):
    """The first finished match on or after ``start``, and its summary."""
    for offset in range(days):
        day = start + timedelta(days=offset)
        board = requests.get(f"{SITE}/{path}/scoreboard",
                             params={"dates": day.strftime("%Y%m%d")},
                             timeout=TIMEOUT, headers=AGENT)
        board.raise_for_status()
        done = [e for e in board.json().get("events", [])
                if ((e.get("status") or {}).get("type") or {}).get("completed")]
        if done:
            event = done[0]
            summary = requests.get(f"{SITE}/{path}/summary", params={"event": event["id"]},
                                   timeout=TIMEOUT, headers=AGENT)
            summary.raise_for_status()
            return day, event, summary.json()
    return None, None, None


def _lines(payload: dict) -> list[tuple[int | None, str]]:
    """The match commentary as (minute, text), wherever the summary keeps it."""
    out = []
    for item in payload.get("commentary") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or (item.get("play") or {}).get("text") or "")
        clock_text = str(((item.get("time") or {}).get("displayValue"))
                         or ((item.get("play") or {}).get("clock") or {}).get("displayValue")
                         or "")
        minute = re.match(r"(\d+)", clock_text)
        out.append((int(minute.group(1)) if minute else None, text))
    return out


def commentary_report(payload: dict) -> dict:
    """What a summary's commentary says about shots and who made them."""
    lines = _lines(payload)
    kinds = {k: [t for _, t in lines if rx.search(t)] for k, rx in LINE_KINDS.items()}
    shots = kinds["goal"] + kinds["attempt"]
    assisted = [t for t in shots if "assisted by" in t.lower()]
    return {
        "summary_keys": sorted(payload)[:40],
        "commentary_lines": len(lines),
        "goals_and_attempts": len(shots),
        "of_which_assisted": len(assisted),
        "woodwork": len(kinds["woodwork"]),
        "corners": len(kinds["corner"]),
        "samples": [t[:220] for t in (kinds["goal"][:1] + kinds["attempt"][:3])],
    }


def _plain_name(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", text.lower())


def understat_against_espn(event: dict, espn_payload: dict, season: int) -> dict:
    """One league match from both sides: Understat's shots with their xG and
    the commentary lines at the same minute, and the two counts of chances."""
    out: dict[str, object] = {}
    session = requests.Session()
    session.headers.update({**AGENT, "X-Requested-With": "XMLHttpRequest"})
    teams = [c.get("team", {}).get("displayName", "")
             for c in (event.get("competitions") or [{}])[0].get("competitors", [])]
    day = str(event.get("date") or "")[:10]
    data = session.get(f"https://understat.com/getLeagueData/EPL/{season}",
                       timeout=TIMEOUT).json()
    wanted = {w for t in teams for w in _plain_name(t).split() if len(w) > 3}
    found = None
    for match in data.get("dates") or []:
        if str(match.get("datetime", ""))[:10] != day:
            continue
        names = _plain_name(f"{match['h']['title']} {match['a']['title']}").split()
        if len(wanted & set(names)) >= 2:
            found = match
            break
    if not found:
        out["understat_match"] = f"not found for {teams} on {day}"
        return out
    out["understat_match"] = f"{found['h']['title']} v {found['a']['title']} ({found['id']})"
    body = session.get(f"https://understat.com/getMatchData/{found['id']}",
                       timeout=TIMEOUT).json()
    shots = [s for side in ("h", "a") for s in (body.get("shots") or {}).get(side) or []]
    rosters = [r for side in ("h", "a")
               for r in ((body.get("rosters") or {}).get(side) or {}).values()]
    out["understat_shots"] = len(shots)
    out["understat_key_passes"] = sum(int(r.get("key_passes") or 0) for r in rosters)
    out["understat_roster_fields"] = sorted(rosters[0]) if rosters else []
    by_minute: dict[int, list[str]] = {}
    for minute, text in _lines(espn_payload):
        if minute is not None and (LINE_KINDS["goal"].search(text)
                                   or LINE_KINDS["attempt"].search(text)):
            by_minute.setdefault(minute, []).append(text)
    paired = []
    for shot in sorted(shots, key=lambda s: int(s.get("minute") or 0))[:10]:
        minute = int(shot.get("minute") or 0)
        espn = next((t for m in (minute, minute + 1, minute - 1)
                     for t in by_minute.get(m, [])
                     if _plain_name(shot.get("player", "")).split()[-1:][0]
                     in _plain_name(t)), "")
        paired.append(f"{minute}' {shot.get('player')} {shot.get('result')} "
                      f"{shot.get('situation')} {shot.get('shotType')} "
                      f"xG={float(shot.get('xG') or 0):.2f} || {espn[:150] or 'NO ESPN LINE'}")
    out["shots_side_by_side"] = paired
    return out


# --- per-shot xG in every competition: FotMob and Sofascore ----------------------

#: A Champions League night: the point is a competition Understat does not cover.
EUROPE_DAY = date(2024, 10, 1)
BROWSER = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-GB,en;q=0.9",
}


def _next_data(html: str) -> dict:
    found = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    return json.loads(found.group(1)) if found else {}


def _find_key(node, key: str, depth: int = 0):
    """The first value under ``key`` anywhere in a nested payload."""
    if depth > 12:
        return None
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            hit = _find_key(value, key, depth + 1)
            if hit is not None:
                return hit
    elif isinstance(node, list):
        for value in node:
            hit = _find_key(value, key, depth + 1)
            if hit is not None:
                return hit
    return None


def probe_fotmob(day: date = EUROPE_DAY) -> dict:
    """FotMob's shot map for one Champions League match: its API, which wants
    a token its own script makes, and failing that the match page, which
    carries the same data in the HTML it serves."""
    out: dict[str, object] = {}
    session = requests.Session()
    session.headers.update(BROWSER)
    match_id = None
    for url in ("https://www.fotmob.com/api/data/matches",
                "https://www.fotmob.com/api/matches"):
        try:
            response = session.get(url, params={"date": day.strftime("%Y%m%d")},
                                   timeout=TIMEOUT)
            out[f"day_list {url.rsplit('/api/', 1)[1]}"] = (
                f"{response.status_code}, {len(response.content):,} bytes")
            if response.ok and not match_id:
                for league in response.json().get("leagues") or []:
                    if "champions league" in str(league.get("name", "")).lower():
                        done = [m for m in league.get("matches") or []
                                if (m.get("status") or {}).get("finished")]
                        if done:
                            match_id = done[0].get("id")
                            out["match"] = (f"{done[0].get('home', {}).get('name')} v "
                                            f"{done[0].get('away', {}).get('name')} ({match_id})")
                            break
        except Exception as exc:  # noqa: BLE001
            out[f"day_list {url}"] = _failed(exc)
    if not match_id:
        try:
            page = session.get("https://www.fotmob.com/leagues/42/matches/champions-league"
                               "?season=2024-2025", timeout=TIMEOUT)
            out["league_page"] = f"{page.status_code}, {len(page.text):,} bytes"
            found = re.search(r'/matches/[^"]+?#(\d+)', page.text)
            match_id = found.group(1) if found else None
            if match_id:
                out["match"] = f"from the league page ({match_id})"
        except Exception as exc:  # noqa: BLE001
            out["league_page"] = _failed(exc)
    if not match_id:
        out["status"] = "no Champions League match id found"
        return out
    try:
        api = session.get("https://www.fotmob.com/api/data/matchDetails",
                          params={"matchId": match_id}, timeout=TIMEOUT)
        out["match_api"] = f"{api.status_code}, {len(api.content):,} bytes"
    except Exception as exc:  # noqa: BLE001
        out["match_api"] = _failed(exc)
    try:
        page = session.get(f"https://www.fotmob.com/match/{match_id}", timeout=TIMEOUT)
        out["match_page"] = f"{page.status_code}, {len(page.text):,} bytes"
        shots = _find_key(_next_data(page.text), "shots") or []
        out["match_page_shots"] = len(shots)
        goals = [s for s in shots if str(s.get("eventType")) == "Goal"]
        if goals:
            g = goals[0]
            out["a_goal_with_its_xg"] = {k: g.get(k) for k in (
                "playerName", "min", "expectedGoals", "situation", "shotType",
                "isOwnGoal")}
        stats = _find_key(_next_data(page.text), "playerStats")
        out["has_player_stats"] = bool(stats)
    except Exception as exc:  # noqa: BLE001
        out["match_page"] = _failed(exc)
    return out


def probe_sofascore(day: date = EUROPE_DAY) -> dict:
    """Sofascore's shot map for one Champions League match. Known to refuse
    datacenter addresses; this says whether it refuses these ones."""
    out: dict[str, object] = {}
    session = requests.Session()
    session.headers.update({**BROWSER, "Referer": "https://www.sofascore.com/"})
    base = "https://api.sofascore.com/api/v1"
    try:
        day_list = session.get(f"{base}/sport/football/scheduled-events/{day.isoformat()}",
                               timeout=TIMEOUT)
        out["day_list"] = f"{day_list.status_code}, {len(day_list.content):,} bytes"
        if not day_list.ok:
            return out
        events = [e for e in day_list.json().get("events") or []
                  if "champions league" in str((e.get("tournament") or {})
                                               .get("name", "")).lower()
                  and (e.get("status") or {}).get("type") == "finished"]
        if not events:
            out["status"] = "no finished Champions League match listed"
            return out
        event = events[0]
        out["match"] = (f"{event['homeTeam']['name']} v {event['awayTeam']['name']} "
                        f"({event['id']})")
        shots = session.get(f"{base}/event/{event['id']}/shotmap", timeout=TIMEOUT)
        out["shotmap"] = f"{shots.status_code}"
        if shots.ok:
            listed = shots.json().get("shotmap") or []
            goals = [s for s in listed if s.get("shotType") == "goal"]
            out["shots"] = f"{len(listed)}, {len(goals)} goals"
            if goals:
                g = goals[0]
                out["a_goal_with_its_xg"] = {
                    "player": (g.get("player") or {}).get("name"),
                    "minute": g.get("time"), "xg": g.get("xg"),
                    "situation": g.get("situation"), "bodyPart": g.get("bodyPart")}
    except Exception as exc:  # noqa: BLE001
        out["status"] = _failed(exc)
    return out


def probe_commentary() -> dict[str, dict]:
    """Whether every competition's ESPN summary carries the commentary, and
    whether its shots line up with Understat's where both exist."""
    report: dict[str, dict] = {}
    from whul.sources.espn import LEAGUE_PATHS

    for key, start in COMMENTARY_CHECKS:
        try:
            day, event, payload = _first_finished(LEAGUE_PATHS[key][1], start)
            if payload is None:
                report[key] = {"status": f"no finished match within 10 days of {start}"}
                continue
            found = {"match": f"{event.get('name')} on {day} ({event.get('id')})",
                     **commentary_report(payload)}
            if key == "epl":
                try:
                    found.update(understat_against_espn(event, payload, start.year))
                except Exception as exc:  # noqa: BLE001
                    found["understat"] = _failed(exc)
            report[key] = found
        except Exception as exc:  # noqa: BLE001
            report[key] = {"status": _failed(exc)}
    report["fotmob (Champions League shot xG)"] = probe_fotmob()
    report["sofascore (Champions League shot xG)"] = probe_sofascore()
    return report


# --- FotMob across every competition and season --------------------------------

#: FotMob's own competition ids, with a (country, name) to fall back on if an
#: id is wrong: the day list names both.
FOTMOB_COMPETITIONS = {
    "epl": (47, "ENG", "premier league"), "laliga": (87, "ESP", "laliga"),
    "seriea": (55, "ITA", "serie a"), "bundesliga": (54, "GER", "bundesliga"),
    "ligue1": (53, "FRA", "ligue 1"), "mls": (130, "USA", "major league soccer"),
    "ucl": (42, "INT", "champions league"), "uel": (73, "INT", "europa league"),
    "efl_cup": (133, "ENG", "efl cup"), "facup": (132, "ENG", "fa cup"),
    "dfbpokal": (209, "GER", "dfb pokal"), "coppaitalia": (141, "ITA", "coppa italia"),
    "copadelrey": (138, "ESP", "copa del rey"),
    "coupedefrance": (134, "FRA", "coupe de france"),
}
#: A day each played on, this season's opponents' history included: the
#: benchmark reaches back to 2021-22, so the oldest is asked about too.
FOTMOB_CHECKS = (
    ("epl", date(2021, 9, 18)), ("laliga", date(2021, 9, 19)),
    ("bundesliga", date(2021, 9, 18)), ("seriea", date(2022, 9, 18)),
    ("ligue1", date(2023, 9, 17)), ("epl", date(2024, 9, 21)),
    ("mls", date(2022, 7, 9)), ("mls", date(2024, 9, 21)),
    ("ucl", date(2021, 9, 14)), ("ucl", date(2024, 10, 1)),
    ("uel", date(2024, 10, 3)), ("efl_cup", date(2024, 10, 29)),
    ("dfbpokal", date(2024, 10, 29)), ("coppaitalia", date(2024, 12, 17)),
    ("facup", date(2025, 1, 11)), ("facup", date(2025, 1, 12)),
    ("copadelrey", date(2025, 1, 14)), ("coupedefrance", date(2025, 1, 14)),
)
#: The figures a richer scoring would read, by FotMob's own stat keys where
#: they are known and its titles otherwise; reported present or absent.
FOTMOB_WANTED = ("minutes_played", "expected_goals", "expected_assists", "chances_created",
                 "shots_on_target", "tackles", "tackles_won", "interceptions", "clearances",
                 "blocks", "shot_blocks", "recoveries", "duel_won", "aerials_won",
                 "accurate_passes", "dribbles_succeeded", "fouls", "was_fouled",
                 "touches", "dispossessed", "rating")


def _fotmob_matches(session, key: str, day: date) -> list[dict]:
    wanted_id, country, name = FOTMOB_COMPETITIONS[key]
    response = session.get("https://www.fotmob.com/api/data/matches",
                           params={"date": day.strftime("%Y%m%d")}, timeout=TIMEOUT)
    response.raise_for_status()
    for league in response.json().get("leagues") or []:
        ids = {league.get("id"), league.get("primaryId")}
        named = (str(league.get("ccode", "")).upper() == country
                 and name in str(league.get("name", "")).lower())
        if wanted_id in ids or named:
            return [m for m in league.get("matches") or []
                    if (m.get("status") or {}).get("finished")]
    return []


def _fotmob_details(session, match_id) -> dict:
    """The match's details from the API, or from the page's embedded data."""
    api = session.get("https://www.fotmob.com/api/data/matchDetails",
                      params={"matchId": match_id}, timeout=TIMEOUT)
    if api.ok:
        try:
            return {"via": "api", **api.json()}
        except ValueError:
            pass
    page = session.get(f"https://www.fotmob.com/match/{match_id}", timeout=TIMEOUT)
    page.raise_for_status()
    found = _find_key(_next_data(page.text), "content")
    return {"via": "page", "content": found or {}}


def _stat_keys(player: dict) -> dict[str, object]:
    """Every stat on a FotMob player line, keyed as FotMob keys it."""
    out: dict[str, object] = {}
    for group in player.get("stats") or []:
        for title, entry in (group.get("stats") or {}).items():
            if isinstance(entry, dict):
                out[str(entry.get("key") or title)] = (entry.get("stat") or {}).get("value")
    return out


def probe_fotmob_coverage() -> dict[str, dict]:
    """FotMob, competition by competition and back to 2021-22: shots with
    their xG, and the figures on each player's line."""
    session = requests.Session()
    session.headers.update(BROWSER)
    report: dict[str, dict] = {}
    for key, day in FOTMOB_CHECKS:
        label = f"{key} {day}"
        try:
            matches = _fotmob_matches(session, key, day)
            if not matches:
                report[label] = {"status": "no finished match listed that day"}
                continue
            found: dict[str, object] = {"finished_matches": len(matches)}
            for match in matches[:3]:
                details = _fotmob_details(session, match["id"])
                content = details.get("content") or {}
                shots = ((content.get("shotmap") or {}).get("shots")) or []
                with_xg = [s for s in shots if s.get("expectedGoals") is not None]
                goals = [s for s in shots if s.get("eventType") == "Goal"
                         and not s.get("isOwnGoal")]
                players = content.get("playerStats") or {}
                name = (f"{(match.get('home') or {}).get('name')} v "
                        f"{(match.get('away') or {}).get('name')}")
                found[name] = (f"via {details.get('via')}: {len(shots)} shots, "
                               f"{len(with_xg)} with xG, {len(goals)} goals "
                               f"({sum(g.get('expectedGoals') is not None for g in goals)} "
                               f"with xG), {len(players)} player lines")
                if players and "stat_keys" not in found:
                    outfield = [p for p in players.values()
                                if isinstance(p, dict) and not p.get("isGoalkeeper")]
                    sample = max(outfield or players.values(),
                                 key=lambda p: len(_stat_keys(p)))
                    keys = _stat_keys(sample)
                    found["stat_keys"] = sorted(keys)
                    found["wanted_missing"] = [w for w in FOTMOB_WANTED if w not in keys]
                    found["sample_player"] = {
                        "name": sample.get("name"),
                        **{w: keys.get(w) for w in FOTMOB_WANTED if w in keys}}
            report[label] = found
        except Exception as exc:  # noqa: BLE001
            report[label] = {"status": _failed(exc)}
    return report


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
