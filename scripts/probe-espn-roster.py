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


def walk(node, path="", depth=0):
    """Every leaf in a payload, as ``path -> value``.

    Written blind on purpose. The first version of this probe assumed
    ``statistics`` was a list of blocks holding a list of stats, and died on an
    AttributeError when it turned out to hold something else -- which taught me
    nothing except that I had guessed. A probe exists to show a shape, so it
    must not have opinions about the shape.
    """
    if depth > 8:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}" if path else str(key), depth + 1)
    elif isinstance(node, list):
        for index, value in enumerate(node[:40]):
            yield from walk(value, f"{path}[{index}]", depth + 1)
    else:
        yield path, node


def stat_map(athlete: dict) -> dict[str, str]:
    """``{leaf path: value}`` for one athlete, whatever shape the stats take.

    Keyed by path rather than by stat name, because the name may itself be a
    value one level down -- ``{"name": "goals", "value": 12}`` -- and reading
    it as a key would find nothing while looking like it had looked.
    """
    return {path: value for path, value in walk(athlete)
            if not isinstance(value, (dict, list))}


def where(athlete: dict, words: tuple[str, ...]) -> list[str]:
    """Paths whose key or value mentions any of these words."""
    hits = []
    for path, value in walk(athlete):
        haystack = f"{path} {value}".lower()
        if any(word.lower() in haystack for word in words):
            hits.append(f"{path} = {str(value)[:40]}")
    return hits


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
    numeric = sum(1 for s in by_name.values()
                  if any(k.startswith("statistics") for k in s))
    print(f"    {numeric} of them carry anything under 'statistics'")
    return by_name


def show_one(payload: dict) -> None:
    """The shape of a single athlete record, before anything is assumed."""
    people = athletes_in(payload)
    if not people:
        return
    athlete = max(people, key=lambda a: len(json.dumps(a, default=str)))
    name = athlete.get("displayName", "?")
    print(f"\n{RULE}\nONE ATHLETE, AS THE PAYLOAD ACTUALLY HAS IT\n{RULE}")
    print(f"\n  {name} -- top-level keys:")
    for key, value in sorted(athlete.items()):
        kind = type(value).__name__
        size = f" ({len(value)})" if isinstance(value, (list, dict, str)) else ""
        print(f"    {key:<24}{kind}{size}")

    leaves = stat_map(athlete)
    print(f"\n  {len(leaves)} leaf value(s). Those under 'statistics':\n")
    stats = {p: v for p, v in leaves.items() if p.startswith("statistics")}
    for path, value in list(stats.items())[:60]:
        print(f"    {path:<52}{str(value)[:20]}")
    if not stats:
        print("    none -- the stats are not under that key, if they are here at all")
    if len(stats) > 60:
        print(f"    ... and {len(stats) - 60} more")

    print(f"\n  Where each field the scorer needs might be:\n")
    for field, words in WANTED.items():
        hits = where(athlete, words)
        if hits:
            print(f"    {field}")
            for hit in hits[:4]:
                print(f"      {hit}")
        else:
            print(f"    {field:<14}NOT FOUND  (tried {', '.join(words)})")


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
    show_one(second)

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

    print(f"\n{RULE}\nWHAT TO DO WITH THIS\n{RULE}")
    print("""
  Paste it back. The athlete record above is what an adapter has to be written
  against, and the two probe runs before this one each proved a guess wrong --
  clubs running four across in a Wikipedia table, and a competition that had
  been renamed. Guessing at a third shape would be a choice.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
