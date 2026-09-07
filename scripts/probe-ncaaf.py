#!/usr/bin/env python3
"""Where does ESPN keep a college football team's conference?

Ten rostered NCAAF teams have scored nothing all season, and the ingest says
why every night: completed games arrive with no conference on them, so
conference wins and the regular-season title cannot be scored and the guard
refuses rather than scoring them low.

**The first version of this probe read the wrong endpoint.** It asked the
scoreboard, which carries `team.conferenceId` on both sides of every game --
25 of 25 -- and concluded the adapter's reading was fine. The nightly ingest
does not use the scoreboard. It uses `load_rostered_schedules`, which walks
each rostered team's own schedule, precisely because the scoreboard caps at
twenty-five events and returns the featured slate rather than all of it. So a
clean bill of health was collected from a payload nothing in the pipeline
reads.

This asks the endpoint that is actually used, and two others that could supply
what it lacks:

  1. `/teams/{id}/schedule` -- what the nightly run reads. Every key on a
     competition, a competitor and its team, so a conference filed under a
     name nobody checks is visible rather than absent. `conferenceCompetition`
     is on the *scoreboard's* competition object; if it is here too, whether a
     game is a conference game needs no conference at all.
  2. `/teams/{id}` -- one request per rostered team. Conference membership is a
     property of a team for a season, so a map built here cannot go missing on
     the night of a particular slate.
  3. the scoreboard, for contrast, and to print each rostered team's conference
     id against its name -- which is how the ACC's id gets confirmed from data
     rather than from memory.

    python scripts/probe-ncaaf.py --db data/whul.sqlite3
    python scripts/probe-ncaaf.py --db data/whul.sqlite3 --date 2026-09-05

RUN IT WHERE ESPN IS REACHABLE. The sandbox this is developed in answers 403 to
every outbound CONNECT, so every request fails identically there and the report
reads as "ESPN has no conferences", which is the one answer it must not invent.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Read straight off the roster rather than typed in, so the probe follows the
#: league rather than a snapshot of it.
FALLBACK_TEAMS = ("Notre Dame Fighting Irish", "Miami Hurricanes",
                  "Ohio State Buckeyes", "Georgia Bulldogs")


def last_saturday(today: date | None = None) -> date:
    """College football is played on Saturdays; a Tuesday probe sees nothing."""
    today = today or date.today()
    return today - timedelta(days=(today.weekday() - 5) % 7 or 7)


def rostered(db_path: str, season: str) -> list[str]:
    if not Path(db_path).exists():
        return list(FALLBACK_TEAMS)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT DISTINCT a.display_name FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE r.season = ? AND a.league = 'NCAAF' ORDER BY 1", (season,),
    ).fetchall()
    return [str(r[0]) for r in rows] or list(FALLBACK_TEAMS)


def conference_ish(*nodes) -> dict:
    """Every key across the given objects whose name suggests a conference."""
    out: dict = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if any(w in key.lower() for w in ("conf", "group", "division")):
                out[key] = value
    return out


def show_team_schedule(names: list[str], season: int) -> dict[str, str]:
    """The endpoint the nightly run reads. Returns {team: conference it gave}."""
    from whul.sources.espn import (
        BASE, LEAGUE_PATHS, _competitor, _conference, _get, _match_key, team_index,
    )

    sport, path = LEAGUE_PATHS["ncaaf"]
    lookup = {_match_key(n): i for n, i in team_index("ncaaf").items()}
    found: dict[str, str] = {}
    shown = False

    for name in names:
        team_id = lookup.get(_match_key(name))
        if not team_id:
            print(f"    {name:<32} no ESPN team id -- it scores nothing either way")
            continue
        try:
            payload = _get(f"{BASE}/{sport}/{path}/teams/{team_id}/schedule",
                           {"season": season}, cache_key=None)
        except Exception as exc:
            print(f"    {name:<32} FAILED {type(exc).__name__}")
            continue

        events = payload.get("events") or []
        done = [e for e in events
                if ((e.get("competitions") or [{}])[0].get("status") or {})
                .get("type", {}).get("completed")]
        covered = 0
        mine = ""
        for event in done:
            inner = (event.get("competitions") or [{}])[0]
            home, away = _competitor(inner, "home"), _competitor(inner, "away")
            if _conference(home) and _conference(away):
                covered += 1
            for side in (home, away):
                if (side.get("team") or {}).get("displayName") == name:
                    mine = mine or _conference(side)
        found[name] = mine
        print(f"    {name:<32} {len(done):>2} completed, conference on both "
              f"sides in {covered}, own conference {mine!r}")

        if not shown and done:
            shown = True
            inner = (done[0].get("competitions") or [{}])[0]
            home = _competitor(inner, "home")
            team = home.get("team") or {}
            print(f"\n      --- one game in full, since this is the payload that matters")
            print(f"      competition keys: {sorted(inner)}")
            print(f"      conferenceCompetition: {inner.get('conferenceCompetition')!r}")
            print(f"      competitor keys : {sorted(home)}")
            print(f"      team keys       : {sorted(team)}")
            print(f"      conference-ish  : "
                  f"{json.dumps(conference_ish(home, team), default=str)[:400]}\n")
    return found


def show_team_detail(names: list[str]) -> None:
    """One request a team. A season-long map cannot go missing on one night."""
    from whul.sources.espn import BASE, LEAGUE_PATHS, _get, _match_key, team_index

    sport, path = LEAGUE_PATHS["ncaaf"]
    lookup = {_match_key(n): i for n, i in team_index("ncaaf").items()}
    shown = False
    for name in names:
        team_id = lookup.get(_match_key(name))
        if not team_id:
            continue
        try:
            payload = _get(f"{BASE}/{sport}/{path}/teams/{team_id}", {},
                           cache_key=f"probe-ncaaf/team/{team_id}")
        except Exception as exc:
            print(f"    {name:<32} FAILED {type(exc).__name__}")
            continue
        team = (payload.get("team") or payload)
        found = conference_ish(team)
        print(f"    {name:<32} {json.dumps(found, default=str)[:220] or '(nothing)'}")
        if not shown:
            shown = True
            print(f"      team keys: {sorted(team)}\n")


def show_scoreboard(day: date, names: list[str]) -> None:
    """Known to carry it. Printed to confirm which id belongs to which league."""
    from whul.sources.espn import (
        BASE, LEAGUE_PATHS, _competitor, _conference, _get, scoreboard_variants,
    )

    sport, path = LEAGUE_PATHS["ncaaf"]
    board = None
    for params in scoreboard_variants("ncaaf", day):
        try:
            board = _get(f"{BASE}/{sport}/{path}/scoreboard", params,
                         cache_key=f"probe-ncaaf/board/{day.isoformat()}")
            break
        except Exception:
            continue
    if board is None:
        print("    every request shape was rejected.")
        return
    seen: dict[str, str] = {}
    for event in board.get("events") or []:
        inner = (event.get("competitions") or [{}])[0]
        for side in ("home", "away"):
            entry = _competitor(inner, side)
            team = (entry.get("team") or {}).get("displayName", "")
            if team:
                seen.setdefault(team, _conference(entry))
    print(f"    {len(seen)} team(s) on {day}. Rostered ones:")
    for name in names:
        if name in seen:
            print(f"      {name:<32} conference {seen[name]!r}")
    others = [(t, c) for t, c in sorted(seen.items()) if t not in names][:6]
    if others:
        print("    and a few others, for the id-to-league mapping:")
        for team, conf in others:
            print(f"      {team:<32} conference {conf!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/whul.sqlite3")
    parser.add_argument("--season", default="2026-27")
    parser.add_argument("--date", help="YYYY-MM-DD; default the last Saturday")
    parser.add_argument("--feed-season", type=int, default=2026,
                        help="ESPN's own season label for the schedule request")
    args = parser.parse_args()
    day = date.fromisoformat(args.date) if args.date else last_saturday()
    names = rostered(args.db, args.season)

    print(__doc__.split("This asks the endpoint")[0].strip())
    print(f"\nRostered NCAAF teams: {len(names)}")

    for title, run in (
        ("1. /teams/{id}/schedule -- WHAT THE NIGHTLY RUN ACTUALLY READS",
         lambda: show_team_schedule(names, args.feed_season)),
        ("2. /teams/{id} -- a season-long conference map, one request a team",
         lambda: show_team_detail(names)),
        (f"3. the scoreboard ({day}) -- known to carry it; which id is which",
         lambda: show_scoreboard(day, names)),
    ):
        print(f"\n{'=' * 70}\n{title}\n")
        try:
            run()
        except Exception as exc:  # noqa: BLE001 -- one source must not stop the rest
            print(f"    FAILED: {type(exc).__name__}: {exc}")

    print(f"\n{'=' * 70}")
    print("Section 1 is the one that matters: it is the payload the pipeline")
    print("reads. If `conferenceCompetition` is on its competition object, a")
    print("conference game needs no conference id at all. If section 2 carries")
    print("a conference, that is the map to join on. Section 3 only confirms")
    print("which id belongs to which league -- Miami's is the ACC's, which is")
    print("what Notre Dame is scored as.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
