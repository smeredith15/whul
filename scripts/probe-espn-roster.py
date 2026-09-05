#!/usr/bin/env python3
"""Does ESPN's team roster really answer for the season it is asked for?

The roster endpoint carries seven of the eight fields the player scorer needs
-- everything but minutes, which it does not need, because starts plus
appearances is the fallback the scorer already documents. At twenty requests a
league-season that is four minutes for the whole benchmark, against seventy-six
for the same thing built out of match summaries.

All of which is worthless if the ``season`` parameter is ignored.

That is not a hypothetical. The MLB Stats API takes a date range on its
sabermetrics endpoint, accepts it without complaint, and answers with the whole
season regardless -- so every run looked right and every figure was four months
too generous. The only way that was ever caught was asking for two different
spans and noticing the answers were identical.

So this asks for the same club in two seasons and compares. If the numbers do
not move, the parameter is decoration and the benchmark cannot be built from
this endpoint at all.

    python scripts/probe-espn-roster.py
    python scripts/probe-espn-roster.py --league esp.1 --team 83

It also prints the shape of one athlete record, because an adapter has to be
written against the payload as it is rather than as it ought to be.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
HEADERS = {"User-Agent": "whul-fantasy/0.1"}
TIMEOUT = 30
PAUSE = 0.4
RULE = "=" * 74

#: What the scorer reads, and the names ESPN might use for each.
WANTED = {
    "appearances": ("appearances", "gamesPlayed", "matchesPlayed"),
    "starts": ("starts", "subIns", "substituteAppearances"),
    "goals": ("totalGoals", "goals"),
    "assists": ("goalAssists", "assists"),
    "yellow": ("yellowCards",),
    "red": ("redCards",),
    "position": ("position",),
}


def roster(league: str, team: str, season: int, session) -> dict:
    response = session.get(
        f"{SITE}/{league}/teams/{team}/roster",
        params={"season": season}, headers=HEADERS, timeout=TIMEOUT,
    )
    response.raise_for_status()
    time.sleep(PAUSE)
    return response.json()


def athletes_in(payload: dict) -> list[dict]:
    """Every athlete, however the payload groups them."""
    found = []
    for entry in payload.get("athletes") or []:
        if isinstance(entry, dict) and "items" in entry:
            found += [a for a in entry["items"] if isinstance(a, dict)]
        elif isinstance(entry, dict):
            found.append(entry)
    return found


def stat_map(athlete: dict) -> dict[str, str]:
    """``{stat name: value}`` for one athlete, wherever the stats hide."""
    out: dict[str, str] = {}
    for block in athlete.get("statistics") or []:
        for stat in block.get("stats") or []:
            name = str(stat.get("name") or stat.get("abbreviation") or "")
            if name:
                out[name] = str(stat.get("displayValue", stat.get("value", "")))
    return out


def summarise(label: str, payload: dict) -> dict[str, dict[str, str]]:
    people = athletes_in(payload)
    print(f"\n  {label}: {len(people)} athlete(s)")
    if not people:
        print("    nothing to read")
        return {}
    by_name = {}
    for athlete in people:
        name = str(athlete.get("displayName") or athlete.get("fullName") or "")
        if name:
            by_name[name] = stat_map(athlete)
    with_stats = sum(1 for s in by_name.values() if s)
    print(f"    {with_stats} of them carry a statistics block")
    return by_name


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default="eng.1")
    ap.add_argument("--team", default="359", help="ESPN team id; 359 is Arsenal")
    ap.add_argument("--seasons", type=int, nargs=2, default=[2024, 2025])
    args = ap.parse_args()

    session = requests.Session()
    older, newer = args.seasons

    print(RULE)
    print(f"ESPN roster -- {args.league} team {args.team}, "
          f"seasons {older} and {newer}")
    print(RULE)

    try:
        first = roster(args.league, args.team, older, session)
        second = roster(args.league, args.team, newer, session)
    except Exception as exc:  # noqa: BLE001 -- reporting is the job
        sys.exit(f"\n  could not read the roster: {type(exc).__name__}: {exc}")

    print(f"\n  club: {(first.get('team') or {}).get('displayName', '?')}")
    a = summarise(f"season {older}", first)
    b = summarise(f"season {newer}", second)

    print(f"\n{RULE}\nDOES THE SEASON PARAMETER DO ANYTHING?\n{RULE}")
    if json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True):
        print(f"""
  NO. The two payloads are byte-identical, so `season` is decoration -- ESPN
  accepted it and ignored it. This is the MLB sabermetrics trap exactly: every
  run would look right and every historical season would be this season's
  numbers.

  The benchmark cannot be built from this endpoint. Match summaries (one per
  match, carrying their own date) are the fallback, at about seventy-six
  minutes rather than four.
""")
        return 1

    shared = sorted(set(a) & set(b))
    print(f"\n  The payloads differ. {len(shared)} player(s) appear in both.")
    moved = [n for n in shared if a[n] and b[n] and a[n] != b[n]]
    print(f"  {len(moved)} of them have different numbers in the two seasons.")
    if not moved:
        print("""
  But no player's stats moved, which is its own kind of wrong: the squads
  differ and the numbers do not. Read the sample below before trusting it.
""")
    for name in moved[:3]:
        print(f"\n    {name}")
        keys = sorted(set(a[name]) | set(b[name]))
        for key in keys[:10]:
            was, now = a[name].get(key, "-"), b[name].get(key, "-")
            mark = "  <-- moved" if was != now else ""
            print(f"      {key:<26}{was:>10}{now:>10}{mark}")

    print(f"\n{RULE}\nCAN THE EIGHT FIELDS BE FOUND?\n{RULE}")
    sample = next((n for n in b if b[n]), None)
    if sample is None:
        print("\n  No athlete in the newer season carries a statistics block.")
        return 1
    print(f"\n  Every stat ESPN gives for {sample}:\n")
    for key, value in sorted(b[sample].items()):
        print(f"    {key:<32}{value}")

    print(f"\n  What the scorer needs, and where it is:\n")
    missing = []
    for field, names in WANTED.items():
        found = next((n for n in names if n in b[sample]), None)
        if found:
            print(f"    {field:<14}{found:<26}{b[sample][found]}")
        else:
            missing.append(field)
            print(f"    {field:<14}{'NOT FOUND':<26}tried {', '.join(names)}")
    print()
    if missing:
        print(f"  Missing: {', '.join(missing)}. The stat list above is what an")
        print(f"  adapter has to be written against -- paste it back.")
        return 1
    print("  All present. The roster can serve both the benchmark and the")
    print("  standings, from one source, at four minutes for a full backfill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
