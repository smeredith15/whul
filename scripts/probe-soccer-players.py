#!/usr/bin/env python3
"""Which source can give us club soccer player stats -- and at what cost?

Twenty-eight rostered players across all five managers have never been scored,
because there is no source for them. The scoring already exists; only the feed
is missing.

    python scripts/probe-soccer-players.py
    python scripts/probe-soccer-players.py --league esp.1 --season 2025

WHAT THE SCORER NEEDS, per player per season:

    appearances, starts (or substitute outings), minutes,
    goals, assists, yellow cards, red cards, position

Position matters more than it looks: a goal is worth 6 to a defender, 5 to a
midfielder and 4 to a forward, so a source without it silently pays every
scorer as a forward.

AND -- the question that actually decides the design -- HOW MANY REQUESTS does
a whole league-season cost? The benchmark needs the top 135 players per league,
and to know who those are you have to rank every player in it, for five
seasons, for six leagues. The difference between one request per league-season
and one per match is the difference between a minute and a night.

    one per league-season   ~30 requests for the whole benchmark
    one per team            ~600
    one per match           ~11,000

So each candidate is judged on three things: can it be reached at all, does it
carry the eight fields, and what does a league-season cost.

A note on where this runs. FBref answers 403 to a datacenter address, which is
why the adapter written against it never worked -- but the nightly pull runs on
GitHub Actions, which is also a datacenter. A source that works from a laptop
and not from CI can serve the benchmark and not the standings, and using two
sources for one number is what put four months of MLB into a three-week window.
So ESPN is tried hardest: it is the one host already known to work from both.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

TIMEOUT = 30
RULE = "=" * 74

#: What the scorer reads. Anything a source cannot give has to come from
#: somewhere else, and a second source for one number is how a window gets
#: filled with a season.
WANTED = ("appearances", "starts", "minutes", "goals", "assists",
          "yellow", "red", "position")

#: Words a payload uses for each of those, so a field can be recognised
#: whatever the source calls it.
SYNONYMS = {
    "appearances": ("appearances", "gamesplayed", "matchesplayed", "mp", "games"),
    "starts": ("starts", "started", "subins", "substitutein", "appearancesasstarter"),
    "minutes": ("minutes", "min", "timeplayed", "minutesplayed"),
    "goals": ("goals", "totalgoals", "gls"),
    "assists": ("assists", "goalassists", "ast"),
    "yellow": ("yellowcards", "yellow", "crdy"),
    "red": ("redcards", "red", "crdr"),
    "position": ("position", "pos", "positionname", "abbreviation"),
}

BROWSER = {
    "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://www.google.com/",
}


def fields_in(payload) -> set[str]:
    """Which of the wanted fields appear anywhere in a payload."""
    text = json.dumps(payload, default=str).lower() if not isinstance(payload, str) \
        else payload.lower()
    found = set()
    for field, words in SYNONYMS.items():
        if any(re.search(rf'["\s>]{re.escape(word)}["\s<:]', text) for word in words):
            found.add(field)
    return found


def report(name: str, cost: str, ok: bool, fields: set[str], note: str = ""):
    missing = [f for f in WANTED if f not in fields]
    print(f"\n  {name}")
    print(f"    reachable   {'yes' if ok else 'NO'}")
    print(f"    cost        {cost}")
    if ok:
        print(f"    has         {', '.join(f for f in WANTED if f in fields) or 'nothing recognisable'}")
        print(f"    missing     {', '.join(missing) if missing else '-- nothing'}")
    if note:
        print(f"    {note}")
    return ok and not missing


def get(url, params=None, headers=None, session=None):
    getter = (session or requests).get
    response = getter(url, params=params or {},
                      headers=headers or {"User-Agent": "whul-fantasy/0.1"},
                      timeout=TIMEOUT)
    response.raise_for_status()
    return response


def try_json(label, url, cost, params=None, session=None, note=""):
    try:
        payload = get(url, params, session=session).json()
    except Exception as exc:  # noqa: BLE001 -- reporting is the job
        report(label, cost, False, set(), f"{type(exc).__name__}: {str(exc)[:120]}")
        return None, False
    size = len(json.dumps(payload))
    whole = report(label, cost, True, fields_in(payload),
                   note or f"payload {size:,} bytes")
    return payload, whole


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default="eng.1", help="ESPN league key")
    ap.add_argument("--season", type=int, default=2025,
                    help="the year the season ended, e.g. 2025 for 2024-25")
    args = ap.parse_args()

    league, season = args.league, args.season
    session = requests.Session()
    site = "https://site.api.espn.com/apis/site/v2/sports/soccer"
    web = "https://site.web.api.espn.com/apis/common/v3/sports/soccer"
    core = "https://sports.core.api.espn.com/v2/sports/soccer/leagues"

    print(RULE)
    print(f"Club soccer player stats -- {league}, season {season}")
    print(RULE)
    print("\nA. ONE REQUEST FOR THE WHOLE LEAGUE-SEASON  (~30 for the benchmark)")

    try_json("ESPN site statistics", f"{site}/{league}/statistics",
             "1 per league-season", {"season": season}, session)
    try_json("ESPN web statistics", f"{web}/{league}/statistics",
             "1 per league-season", {"season": season}, session)
    try_json("ESPN leaders", f"{site}/{league}/leaders",
             "1 per league-season", {"season": season}, session)
    try_json("ESPN core athlete statistics",
             f"{core}/{league}/seasons/{season}/types/1/athletes",
             "1 + 1 per player", {"limit": 1000}, session)

    print("\n\nB. ONE REQUEST PER TEAM  (~600 for the benchmark)")

    teams, _ = try_json("ESPN teams", f"{site}/{league}/teams",
                        "1 per league-season", {"season": season}, session)
    team_id = None
    try:
        entries = teams["sports"][0]["leagues"][0]["teams"]
        team_id = entries[0]["team"]["id"]
        print(f"    -> {len(entries)} team(s); using id {team_id} "
              f"({entries[0]['team'].get('displayName')})")
    except Exception:  # noqa: BLE001
        print("    -> could not read a team id, so the per-team probes are skipped")

    if team_id:
        try_json("ESPN team roster", f"{site}/{league}/teams/{team_id}/roster",
                 "1 per team-season", {"season": season}, session)
        try_json("ESPN web team athletes statistics",
                 f"{web}/{league}/teams/{team_id}/statistics",
                 "1 per team-season", {"season": season}, session)

    print("\n\nC. ONE REQUEST PER MATCH  (~11,000 for the benchmark)")
    print("   Exact per-match minutes, which the scorer prefers to any season")
    print("   aggregate -- but a night's work to backfill five seasons.")

    event_id = None
    try:
        board = get(f"{site}/{league}/scoreboard", {"dates": f"{season - 1}0901"},
                    session=session).json()
        events = board.get("events") or []
        event_id = events[0]["id"] if events else None
        print(f"\n    scoreboard for {season - 1}-09-01: {len(events)} event(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"\n    scoreboard failed: {type(exc).__name__}: {exc}")

    if event_id:
        payload, _ = try_json("ESPN match summary", f"{site}/{league}/summary",
                              "1 per match", {"event": event_id}, session)
        if payload:
            keys = sorted(payload)[:14]
            print(f"    -> top-level keys: {keys}")
            for key in ("rosters", "boxscore", "keyEvents", "commentary"):
                print(f"       {key:<12}{'present' if key in payload else 'absent'}")

    print("\n\nD. FBREF, THE SCHEMA THE SCORER WAS WRITTEN AGAINST")
    print("   Its column names -- MP, Starts, Min, Gls, Ast, CrdY, CrdR -- are")
    print("   already what the scorer reads. It answers 403 to a datacenter")
    print("   address; a laptop may be another matter.")
    url = (f"https://fbref.com/en/comps/Big5/{season - 1}-{season}/stats/players/"
           f"{season - 1}-{season}-Big-5-European-Leagues-Stats")
    try:
        response = get(url, headers=BROWSER, session=session)
        text = response.text
        report("FBref Big 5", "1 per season, all five leagues", True, fields_in(text),
               f"{len(text):,} bytes, {text.lower().count('<table')} table(s)")
    except Exception as exc:  # noqa: BLE001
        report("FBref Big 5", "1 per season, all five leagues", False, set(),
               f"{type(exc).__name__}: {str(exc)[:120]}")

    print(f"\n{RULE}")
    print("WHAT TO DO WITH THIS")
    print(RULE)
    print("""
  Look for the cheapest row that is reachable AND missing nothing. Position and
  starts are the two most often absent, and both matter: without position every
  goal pays as a forward's, and without starts the appearance points fall back
  to an approximation the scorer explicitly calls second-best.

  If FBref answers from your machine but the others do not, say so -- it can
  build the benchmark, but the nightly pull runs on GitHub Actions from a
  datacenter address, so it cannot serve the standings. Two sources for one
  number is what put four months of a season into a three-week window.
""")


if __name__ == "__main__":
    main()
