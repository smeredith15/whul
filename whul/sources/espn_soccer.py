"""Club soccer player stats, from ESPN's team rosters.

Twenty-eight rostered players had never been scored, because there was no
source. FBref -- whose column names the scorer was written against -- answers
403 to a datacenter address *and* to a laptop, so it is out rather than
pending, and the nightly pull runs on GitHub Actions anyway.

ESPN is the one host already known to answer from both. Three of its shapes
were probed:

    league statistics   1 request a league-season, missing cards and starts
    team roster        20 requests, missing only minutes
    match summary     380 requests, missing nothing

The roster wins. Minutes are wanted rather than needed: the scorer takes
per-match minutes where it has them and otherwise reads starts and
appearances, which the roster carries -- as ``appearances`` and ``subIns``,
so a start is the difference between them.

Twenty requests a league-season is about four minutes for a five-season,
six-league benchmark, against seventy-six for the same thing out of match
summaries. Match summaries remain the upgrade if the appearance approximation
ever looks wrong; the scorer reads exact minutes without being asked twice.

The payload shape, which was found rather than assumed::

    athlete.position.abbreviation                        "D"
    athlete.statistics.splits.categories[N].name         "general" | "offensive"
    athlete.statistics.splits.categories[N].stats[M].name   "appearances"
    athlete.statistics.splits.categories[N].stats[M].value   30.0
"""

from __future__ import annotations

import time

import pandas as pd
import requests

from whul.sources.espn import (
    BASE, LEAGUE_PATHS, REQUEST_PAUSE, SEASON_WINDOWS, TIMEOUT,
)

#: Where each figure the scorer needs lives, as ``category.stat``. Read by the
#: stat's own name rather than its position: the order of both the categories
#: and the stats within them varies by player -- a goalkeeper has a goalKeeping
#: category that an outfielder does not -- so an index would read the wrong
#: number for half a squad.
STAT_PATHS = {
    "appearances": ("general", "appearances"),
    "sub_ins": ("general", "subIns"),
    "goals": ("offensive", "totalGoals"),
    "assists": ("offensive", "goalAssists"),
    "yellow": ("general", "yellowCards"),
    "red": ("general", "redCards"),
}

#: ESPN's own goals stat is in the same category as the real ones and is named
#: similarly enough to be picked up by a looser match. Conceding one must never
#: be paid as scoring one.
NOT_A_GOAL = "ownGoals"


def roster_season(league: str, season: int) -> int:
    """Our season label, in ESPN's numbering.

    ESPN names a soccer season for the year it starts; we name it for the year
    it ends. A live run confirmed it: asked for 2021, the feed answered
    "2021-22 English Premier League". So every European league is one year
    apart from us, and MLS -- which runs inside a calendar year and answered
    "2021 MLS" -- is not.

    That distinction is already in ``SEASON_WINDOWS`` as "ends" against
    "within", so it is derived rather than listed and a league added later is
    right without anyone remembering this.

    The cost of getting it wrong is not the benchmark, where five consecutive
    seasons are five consecutive seasons. It is the live pull: our 2026-27 is
    2027, and asking ESPN for 2027 returns 2027-28, a season nobody has played.
    Every rostered player would score zero and the run would look like it had
    worked.
    """
    numbering = SEASON_WINDOWS.get(league, ((), (), "within"))[2]
    return season - 1 if numbering == "ends" else season


def season_matches(league: str, season: int, said: str) -> bool:
    """Does the label the feed returned describe the season we meant?

    Deliberately strict about which year it must start with. The first version
    of this check accepted a label beginning with either the year asked for or
    the year before, which is to say it accepted both conventions and so
    detected neither -- it passed on the very shift it was written to find.
    """
    if not said:
        return True
    return said.strip().startswith(str(roster_season(league, season)))


def _get(url: str, params: dict, session=None) -> dict:
    getter = session.get if session is not None else requests.get
    response = getter(url, params=params, timeout=TIMEOUT,
                      headers={"User-Agent": "whul-fantasy/0.1"})
    response.raise_for_status()
    payload = response.json()
    time.sleep(REQUEST_PAUSE)
    return payload


def _athletes(payload: dict) -> list[dict]:
    """Every athlete, however the payload groups them.

    ESPN groups a soccer roster by position, so the athletes are one level
    further in than for the sports that do not.
    """
    found = []
    for entry in payload.get("athletes") or []:
        if isinstance(entry, dict) and isinstance(entry.get("items"), list):
            found += [a for a in entry["items"] if isinstance(a, dict)]
        elif isinstance(entry, dict):
            found.append(entry)
    return found


def _stats(athlete: dict) -> dict[str, float]:
    """``{category.stat: value}`` for one athlete.

    Empty where the athlete has no statistics block at all, which is ordinary:
    a squad player who has not appeared has nothing to report, and reading that
    as zeroes rather than as absence is the same answer here.
    """
    stats = athlete.get("statistics")
    if isinstance(stats, list):
        stats = stats[0] if stats else None
    if not isinstance(stats, dict):
        return {}
    splits = stats.get("splits")
    if isinstance(splits, list):
        splits = splits[0] if splits else None
    if not isinstance(splits, dict):
        return {}

    out: dict[str, float] = {}
    for category in splits.get("categories") or []:
        if not isinstance(category, dict):
            continue
        group = str(category.get("name") or "")
        for stat in category.get("stats") or []:
            if not isinstance(stat, dict):
                continue
            name = str(stat.get("name") or "")
            if not name:
                continue
            try:
                out[f"{group}.{name}"] = float(stat.get("value"))
            except (TypeError, ValueError):
                continue
    return out


def season_label(payload: dict) -> str:
    """What season the feed says it answered with, if it says.

    Asked for rather than deduced. ESPN's own numbering could name a season for
    the year it starts where ours names it for the year it ends, and a
    one-year shift would fill every benchmark season with the wrong year's
    football -- every figure still a real footballer's real season, and nothing
    anywhere reading as wrong.
    """
    for holder in (payload, payload.get("team") or {}):
        block = holder.get("season")
        if isinstance(block, dict):
            for key in ("displayName", "name", "year", "id"):
                if block.get(key):
                    return str(block[key])
        if isinstance(block, (str, int)):
            return str(block)
    return ""


def load_squad(
    league: str, team_id: str, season: int, session=None
) -> pd.DataFrame:
    """One club's players for one season, in the shape the scorer reads.

    ``season`` is our label throughout -- 2027 is 2026-27 -- and the
    translation into ESPN's numbering happens at the request. The rows come
    back tagged with ours, so nothing downstream has to know the difference.
    """
    sport, path = LEAGUE_PATHS[league]
    payload = _get(
        f"{BASE}/{sport}/{path}/teams/{team_id}/roster",
        {"season": roster_season(league, season)}, session,
    )
    club = str((payload.get("team") or {}).get("displayName", ""))
    said = season_label(payload)

    rows = []
    for athlete in _athletes(payload):
        stats = _stats(athlete)
        if not stats:
            continue
        appearances = stats.get(".".join(STAT_PATHS["appearances"]), 0.0)
        sub_ins = stats.get(".".join(STAT_PATHS["sub_ins"]), 0.0)
        rows.append({
            "player": str(athlete.get("displayName") or athlete.get("fullName") or ""),
            "player_id": str(athlete.get("id") or ""),
            "team": club,
            "season": int(season),
            "season_said": said,
            "position": str((athlete.get("position") or {}).get("abbreviation") or ""),
            "matches": appearances,
            # A start is an appearance that did not begin on the bench. ESPN
            # gives the substitute count, never the starts, so this is the
            # subtraction the scorer's season path wants.
            "starts": max(appearances - sub_ins, 0.0),
            "goals": stats.get(".".join(STAT_PATHS["goals"]), 0.0),
            "assists": stats.get(".".join(STAT_PATHS["assists"]), 0.0),
            "yellow": stats.get(".".join(STAT_PATHS["yellow"]), 0.0),
            "red": stats.get(".".join(STAT_PATHS["red"]), 0.0),
        })
    return pd.DataFrame(rows)


def team_ids(league: str, season: int, session=None) -> dict[str, str]:
    """``{club: espn id}`` for one league and season."""
    sport, path = LEAGUE_PATHS[league]
    payload = _get(f"{BASE}/{sport}/{path}/teams",
                   {"season": roster_season(league, season)}, session)
    out = {}
    try:
        entries = payload["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError, TypeError):
        return out
    for entry in entries:
        team = (entry or {}).get("team") or {}
        if team.get("id") and team.get("displayName"):
            out[str(team["displayName"])] = str(team["id"])
    return out


def load_players(
    league: str, seasons: list[int], verbose: bool = True, session=None
) -> pd.DataFrame:
    """Every player in a league, for each season given.

    One request for the club list and one per club, so a league-season is
    twenty-one requests and a five-season benchmark about a hundred.

    A club that fails is reported and skipped rather than taking the league
    down with it: nineteen clubs' players are worth more than none, and the
    league that lost one says so.
    """
    session = session or requests.Session()
    frames = []
    for season in seasons:
        clubs = team_ids(league, season, session)
        if not clubs:
            if verbose:
                print(f"  {league} {season}: no clubs listed, so no players",
                      flush=True)
            continue
        if verbose:
            print(f"  {league} {season}: {len(clubs)} club(s) ...", flush=True)
        for club, team_id in clubs.items():
            try:
                frames.append(load_squad(league, team_id, season, session))
            except Exception as exc:  # noqa: BLE001 -- one club, not the league
                if verbose:
                    print(f"    {club} failed: {type(exc).__name__}", flush=True)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
