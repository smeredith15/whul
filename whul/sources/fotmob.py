"""FotMob: every club soccer match, player by player.

The second version of club soccer player scoring (``docs/PROJECT_PLAN.md``
section 2.8) reads, for every player in every match, figures ESPN does not
keep: tackles, interceptions, blocks, clearances, dribbles, chances created,
the xG of each goal, and a match rating. FotMob carries all of them for every
league, cup and European competition the league scores -- back to 2021-22,
when the benchmark begins -- and answers GitHub's runners, which Sofascore and
FBref do not (see ``whul.sources.soccer_probe``). NWSL is the exception: full
player lines, no shot map, so no xG.

Three requests, all unauthenticated JSON:

    /api/data/matches?date=YYYYMMDD          every match on a day, by competition
    /api/data/leagues?id=47&season=2024/2025 a competition's season, every match
    /api/data/matchDetails?matchId=N         one match: lineup, events, each
                                             player's stat line, the shot map

What a match says never changes once it is over, so a finished match is read
once and its player lines kept (``CACHE``): a rerun, a manual run, a second
league asking about the same Champions League night, costs nothing.

**Not an official API.** It could add a token or refuse us, as Sofascore does.
Every caller treats a match it could not read as one not yet read -- held,
never scored as nothing -- and nothing here raises past one match.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import requests

BASE = "https://www.fotmob.com/api/data"
CACHE = Path("data/cache/fotmob")
TIMEOUT = 30
#: Between requests that go out. FotMob publishes no limit; this is a
#: considerate client's pace, and a season of one league is ten minutes at it.
REQUEST_PAUSE = 1.0
#: Waits before each retry of a request that failed for a reason worth
#: retrying (a timeout, a 429, a 5xx). A 4xx other than 429 is not retried.
RETRY_WAITS = (2, 4, 8)
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-GB,en;q=0.9",
}

#: FotMob's competition ids, by the keys the rest of the project uses, with a
#: (country, name) to recognise the competition by in a day's list where the id
#: there is a season's rather than the competition's.
#:
#: The name is matched whole, never as a part of a longer one. Matching on
#: "contains" filed LaLiga2 as La Liga -- every Segunda Division club, and a
#: Segunda captain third in La Liga's benchmark -- and would have done the same
#: with 2. Bundesliga, Serie A Femminile, Premier League 2 and the women's cups.
COMPETITIONS = {
    "epl": (47, "ENG", "premier league"), "laliga": (87, "ESP", "laliga"),
    "seriea": (55, "ITA", "serie a"), "bundesliga": (54, "GER", "bundesliga"),
    "ligue1": (53, "FRA", "ligue 1"), "mls": (130, "USA", "major league soccer"),
    "nwsl": (9134, "USA", "nwsl"),
    "ucl": (42, "INT", "champions league"), "uel": (73, "INT", "europa league"),
    "uecl": (10216, "INT", "conference league"),
    "facup": (132, "ENG", "fa cup"), "efl_cup": (133, "ENG", "efl cup"),
    "dfbpokal": (209, "GER", "dfb pokal"), "coppaitalia": (141, "ITA", "coppa italia"),
    "copadelrey": (138, "ESP", "copa del rey"),
    "coupedefrance": (134, "FRA", "coupe de france"),
    "usopencup": (None, "USA", "us open cup"),
}
#: Other whole names the same competition goes by.
ALSO_CALLED = {
    "laliga": ("la liga", "laliga ea sports"), "mls": ("mls",),
    "ucl": ("uefa champions league",), "uel": ("uefa europa league",),
    "uecl": ("uefa conference league", "uefa europa conference league"),
    "efl_cup": ("carabao cup", "league cup"), "usopencup": ("u s open cup",
                                                           "lamar hunt us open cup"),
}


def _plain(name) -> str:
    """A competition's name with case and punctuation gone: "DFB-Pokal" and
    "DFB Pokal" are one name; "LaLiga2" and "LaLiga" are not."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).split())
#: Competitions named for one calendar year rather than two.
CALENDAR_YEAR = {"mls", "nwsl"}

#: FotMob's usual-position codes.
POSITIONS = {0: "G", 1: "D", 2: "M", 3: "F"}

#: Stat keys on a FotMob player line, by the names a match line uses. Where a
#: stat is a fraction ("14 of 17") the first number is the one kept.
STAT_KEYS = {
    "minutes": "minutes_played", "goals": "goals", "assists": "assists",
    "rating": "rating_title", "xg": "expected_goals", "xa": "expected_assists",
    "shots": "total_shots", "shots_on_target": "ShotsOnTarget",
    "chances_created": "chances_created",
    "tackles": "matchstats.headers.tackles", "interceptions": "interceptions",
    "shot_blocks": "shot_blocks", "clearances": "clearances",
    "recoveries": "recoveries", "dribbles": "dribbles_succeeded",
    "dispossessed": "dispossessed", "fouls": "fouls", "was_fouled": "was_fouled",
    "aerials_won": "aerials_won", "duels_won": "duel_won", "touches": "touches",
    "accurate_passes": "accurate_passes",
}

#: A minute late enough that nobody is still on the pitch.
FINAL_WHISTLE = 1000.0


# --- requests -----------------------------------------------------------------

@dataclass
class Client:
    """One session's requests: paced, retried, counted, and stopped after too
    many failures in a row, so a FotMob outage is a few seconds of a run
    rather than an hour of timeouts."""

    pause: float = REQUEST_PAUSE
    waits: tuple = RETRY_WAITS
    give_up_after: int = 8
    session: requests.Session = field(default_factory=requests.Session)
    sent: int = 0
    failed_in_a_row: int = 0
    last: float = 0.0

    def __post_init__(self):
        self.session.headers.update(HEADERS)

    @property
    def stopped(self) -> bool:
        return self.failed_in_a_row >= self.give_up_after

    def get(self, path: str, **params) -> dict | None:
        """The JSON at ``path``, or None once retries are spent."""
        if self.stopped:
            return None
        for attempt, wait in enumerate((0, *self.waits)):
            if wait:
                time.sleep(wait)
            gap = self.pause - (time.monotonic() - self.last)
            if gap > 0:
                time.sleep(gap)
            self.last = time.monotonic()
            self.sent += 1
            try:
                response = self.session.get(f"{BASE}{path}", params=params,
                                            timeout=TIMEOUT)
            except requests.RequestException:
                continue
            if response.status_code == 429 or response.status_code >= 500:
                continue
            if not response.ok:
                break
            try:
                body = response.json()
            except ValueError:
                break
            self.failed_in_a_row = 0
            return body
        self.failed_in_a_row += 1
        return None


# --- which matches ------------------------------------------------------------

def season_label(key: str, season: int) -> str:
    """FotMob's name for a season: "2024/2025" for a European one starting in
    ``season``, "2024" for MLS and NWSL."""
    return str(season) if key in CALENDAR_YEAR else f"{season}/{season + 1}"


def _is(league: dict, key: str) -> bool:
    wanted, country, name = COMPETITIONS[key]
    ids = {league.get("id"), league.get("primaryId"), league.get("parentLeagueId")}
    if wanted is not None and wanted in ids:
        return True
    names = {_plain(name), *(_plain(n) for n in ALSO_CALLED.get(key, ()))}
    return (str(league.get("ccode", "")).upper() == country
            and _plain(league.get("name")) in names)


def _finished(match: dict) -> bool:
    status = match.get("status") or {}
    return bool(status.get("finished")) and not status.get("cancelled")


def _listed(match: dict, key: str) -> dict:
    status = match.get("status") or {}
    return {"match_id": str(match.get("id")), "competition": key,
            "date": str(status.get("utcTime") or match.get("time") or "")[:10],
            "home": (match.get("home") or {}).get("name"),
            "away": (match.get("away") or {}).get("name"),
            "finished": _finished(match)}


#: A day's list is kept once every match on it is old enough to have been
#: finished and corrected: a list read on the evening of a match day would
#: otherwise be kept with the late kick-offs still to play.
SETTLED_AFTER_DAYS = 3


def day_matches(client: Client, day: date, keys: tuple[str, ...],
                cache: Path | None = None, today: date | None = None) -> list[dict] | None:
    """Every match on ``day`` in the competitions named; None if the day could
    not be read, which is not the same as a day with nothing on."""
    path = (cache / "days" / f"{day:%Y%m%d}.json") if cache else None
    if path is not None and path.exists():
        body = json.loads(path.read_text())
    else:
        body = client.get("/matches", date=day.strftime("%Y%m%d"))
        if body is None:
            return None
        settled = today is not None and (today - day).days >= SETTLED_AFTER_DAYS
        if path is not None and settled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body))
    out = []
    for league in (body or {}).get("leagues") or []:
        for key in keys:
            if _is(league, key):
                MATCHED.setdefault(key, set()).add(
                    f"{league.get('name')} [{league.get('ccode')}, "
                    f"{league.get('primaryId') or league.get('id')}]")
                out += [_listed(m, key) for m in league.get("matches") or []]
    return out


#: Every competition in FotMob's day lists that a key has been taken to mean,
#: as "name [country, id]". A run prints it, so a competition filed under the
#: wrong key is seen in the log rather than found in a benchmark.
MATCHED: dict[str, set[str]] = {}


def season_matches(client: Client, key: str, season: int,
                   start: date | None = None, end: date | None = None) -> list[dict]:
    """Every match of one competition's season.

    The competition's own page first, which is one request. Failing that, a
    walk through the days between ``start`` and ``end``, one request a day --
    slower, but built only on the day list, which every probe has read.
    """
    body = client.get("/leagues", id=COMPETITIONS[key][0], season=season_label(key, season))
    found = _all_matches(body)
    if found:
        return [_listed(m, key) for m in found]
    if start is None or end is None:
        return []
    out, day = [], start
    while day <= end and not client.stopped:
        out += day_matches(client, day, (key,)) or []
        day += timedelta(days=1)
    return out


@dataclass
class Walk:
    """What a walk through a span of days found: every finished match's
    player lines, and what could not be read."""

    lines: list[dict] = field(default_factory=list)
    matches: int = 0
    unread_days: list[str] = field(default_factory=list)
    unread_matches: list[str] = field(default_factory=list)
    stopped: bool = False


def walk(client: Client, start: date, end: date, keys: tuple[str, ...],
         today: date, cache: Path | None = None) -> Walk:
    """Every finished match in ``keys`` between two days, player by player.

    One request a day for the day's list -- none for a day already settled and
    kept -- and one a match for its details, none for a match already read.
    Each line carries the competition key it was found under.
    """
    cache = CACHE if cache is None else cache
    found = Walk()
    day = start
    seen: set[str] = set()
    while day <= end:
        if client.stopped:
            found.stopped = True
            break
        listed = day_matches(client, day, keys, cache=cache, today=today)
        if listed is None:
            found.unread_days.append(day.isoformat())
        for match in listed or []:
            if not match["finished"] or match["match_id"] in seen:
                continue
            seen.add(match["match_id"])
            lines = cached_lines(match["match_id"], client, cache=cache)
            if lines is None:
                found.unread_matches.append(match["match_id"])
                continue
            found.matches += 1
            found.lines += [{**line, "competition_key": match["competition"]}
                            for line in lines]
        day += timedelta(days=1)
    found.stopped = found.stopped or client.stopped
    return found


def _all_matches(body) -> list[dict]:
    """The match list on a competition's page, wherever this version keeps it."""
    if not isinstance(body, dict):
        return []
    for section in ("fixtures", "matches"):
        listed = (body.get(section) or {}).get("allMatches")
        if isinstance(listed, list) and listed:
            return listed
    return []


def match_details(client: Client, match_id: str) -> dict | None:
    return client.get("/matchDetails", matchId=match_id)


def cached_lines(match_id: str, client: Client | None = None,
                 cache: Path | None = None) -> list[dict] | None:
    """A finished match's player lines: from the cache, or read and kept.

    None for a match that could not be read or is not over -- a caller holds
    on that rather than scoring the match as nobody having played. ``cache``
    defaults to ``CACHE`` as it stands when called, so a test that points it
    elsewhere is obeyed.
    """
    cache = CACHE if cache is None else cache
    path = (cache / "lines" / f"{match_id}.json") if cache else None
    if path is not None and path.exists():
        return json.loads(path.read_text())
    if client is None:
        return None
    details = match_details(client, match_id)
    if not details or not finished(details):
        return None
    lines = match_lines(details)
    if path is not None and lines:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(lines))
    return lines


# --- one match, player by player ----------------------------------------------

def finished(details: dict) -> bool:
    status = (details.get("header") or {}).get("status") or {}
    general = details.get("general") or {}
    return bool(status.get("finished") or general.get("finished")) and not status.get(
        "cancelled")


def _minute(event: dict) -> float:
    """An event's place in the match: 90+3 sorts after 90 and before 91."""
    added = event.get("overloadTime") or 0
    try:
        return float(event.get("time") or 0) + float(added) / 100.0
    except (TypeError, ValueError):
        return float(event.get("time") or 0)


def _stats(entry: dict) -> dict:
    """A player line's figures by FotMob key, the first number of a fraction."""
    out = {}
    for group in entry.get("stats") or []:
        for title, item in (group.get("stats") or {}).items():
            if not isinstance(item, dict):
                continue
            stat = item.get("stat") or {}
            out[str(item.get("key") or title)] = stat.get("value")
    return out


def _number(value) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(out) else out


def match_lines(details: dict) -> list[dict]:
    """Every player who played, one line each: what FotMob counted, and what
    the scoring needs worked out from the events -- when he was on the pitch,
    the goals his side conceded while he was, his cards and own goals, and the
    xG of each goal he scored other than a penalty.

    A shootout is left out of everything: its kicks are not goals and the
    match it decides was drawn.
    """
    general = details.get("general") or {}
    content = details.get("content") or {}
    lineup = content.get("lineup") or {}
    facts = content.get("matchFacts") or {}
    events = [e for e in ((facts.get("events") or {}).get("events") or [])
              if isinstance(e, dict)]
    stats = content.get("playerStats") or {}
    shots = [s for s in ((content.get("shotmap") or {}).get("shots") or [])
             if isinstance(s, dict)]

    sides = {}
    for side, home in (("homeTeam", True), ("awayTeam", False)):
        team = lineup.get(side) or {}
        team_id = team.get("id") or (general.get(side) or {}).get("id")
        sides[home] = {"id": team_id,
                       "name": team.get("name") or (general.get(side) or {}).get("name")}
    team_of: dict[str, bool] = {}
    started: dict[str, bool] = {}
    usual: dict[str, int] = {}
    for side, home in (("homeTeam", True), ("awayTeam", False)):
        team = lineup.get(side) or {}
        for listed, starter in (("starters", True), ("subs", False)):
            for person in team.get(listed) or []:
                pid = str(person.get("id"))
                team_of[pid] = home
                started[pid] = starter
                if person.get("usualPlayingPositionId") is not None:
                    usual[pid] = person["usualPlayingPositionId"]
    for pid, entry in stats.items():
        if str(pid) not in team_of and entry.get("teamId") is not None:
            team_of[str(pid)] = entry.get("teamId") == sides[True]["id"]

    # When each player came on and went off, from the events: they carry the
    # added time the lineup's own substitution marks do not.
    on: dict[str, float] = {pid: 0.0 for pid, s in started.items() if s}
    off: dict[str, float] = {}
    for event in events:
        kind = event.get("type")
        if kind == "Substitution":
            swap = [s for s in event.get("swap") or [] if isinstance(s, dict)]
            if len(swap) >= 2:
                on[str(swap[0].get("id"))] = _minute(event)
                off[str(swap[1].get("id"))] = _minute(event)
        elif kind == "Card" and event.get("card") in ("Red", "YellowRed"):
            off[str(event.get("playerId"))] = _minute(event)

    yellow: dict[str, int] = {}
    red: dict[str, int] = {}
    own_goals: dict[str, int] = {}
    against: dict[bool, list[float]] = {True: [], False: []}
    for event in events:
        kind = event.get("type")
        pid = str(event.get("playerId"))
        if kind == "Card":
            if event.get("card") == "Yellow":
                yellow[pid] = yellow.get(pid, 0) + 1
            elif event.get("card") in ("Red", "YellowRed"):
                red[pid] = red.get(pid, 0) + 1
        elif kind == "Goal" and not event.get("isPenaltyShootoutEvent"):
            scorer_home = team_of.get(pid, bool(event.get("isHome")))
            if event.get("ownGoal"):
                own_goals[pid] = own_goals.get(pid, 0) + 1
                against[scorer_home].append(_minute(event))
            else:
                against[not scorer_home].append(_minute(event))

    penalties: dict[str, int] = {}
    goal_xg: dict[str, list[float]] = {}
    for shot in shots:
        if shot.get("eventType") != "Goal" or shot.get("isOwnGoal"):
            continue
        pid = str(shot.get("playerId"))
        if shot.get("situation") == "Penalty" or shot.get("period") == "PenaltyShootout":
            if shot.get("period") != "PenaltyShootout":
                penalties[pid] = penalties.get(pid, 0) + 1
            continue
        if shot.get("expectedGoals") is not None:
            goal_xg.setdefault(pid, []).append(round(_number(shot["expectedGoals"]), 4))

    potm = str((facts.get("playerOfTheMatch") or {}).get("id") or "")
    when = str(general.get("matchTimeUTCDate") or "")[:10]
    lines = []
    for pid, entry in stats.items():
        pid = str(pid)
        figures = _stats(entry)
        minutes = _number(figures.get(STAT_KEYS["minutes"]))
        if minutes <= 0:
            continue
        home = team_of.get(pid)
        if home is None:
            continue
        came_on = on.get(pid, 0.0 if started.get(pid) else None)
        if came_on is None:
            # On the pitch but in no substitution FotMob listed: count him as
            # on for the minutes it says he played, from the end.
            came_on = max(0.0, 90.0 - minutes)
        went_off = off.get(pid, FINAL_WHISTLE)
        conceded = sum(1 for m in against[home] if came_on < m <= went_off)
        position = POSITIONS.get(entry.get("usualPosition"),
                                 POSITIONS.get(usual.get(pid), ""))
        line = {
            "match_id": str(general.get("matchId") or ""),
            "date": when,
            "competition": str(general.get("leagueName") or ""),
            "competition_id": general.get("parentLeagueId") or general.get("leagueId"),
            "player_id": pid,
            "player": str(entry.get("name") or ""),
            "team_id": sides[home]["id"],
            "team": sides[home]["name"],
            "opponent": sides[not home]["name"],
            "home": home,
            "position": "G" if entry.get("isGoalkeeper") else position,
            "started": bool(started.get(pid)),
            "conceded_on": conceded,
            "team_conceded": len(against[home]),
            "own_goals": own_goals.get(pid, 0),
            "yellow": yellow.get(pid, 0),
            "red": red.get(pid, 0),
            "penalty_goals": penalties.get(pid, 0),
            "goal_xg": goal_xg.get(pid, []),
            "has_shotmap": bool(shots),
            "potm": bool(entry.get("isPotm")) or pid == potm,
        }
        for ours, theirs in STAT_KEYS.items():
            value = figures.get(theirs)
            line[ours] = None if value is None else _number(value)
        lines.append(line)
    return lines
