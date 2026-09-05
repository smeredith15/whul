#!/usr/bin/env python3
"""Can Wikipedia tell us who qualified for each UEFA competition?

Nothing is scored off this yet. The point is to see the shape of the answer
before an adapter is written against a guess -- which is how an FBref adapter
got written and then met a 403.

    python scripts/probe-uefa-wikipedia.py                  # a finished season
    python scripts/probe-uefa-wikipedia.py --season 2027-28
    python scripts/probe-uefa-wikipedia.py --dump "Champions League"

The default is a season already played, deliberately: its tables are complete
and you can check the output against what you remember. A future season's page
carries rows naming a slot rather than a club -- "Qualifying round winner" --
and telling those apart is one of the things this is here to test.

It asks four questions, in the order they fail:

    1. does the article have a section naming the teams?
    2. does that section hold a table pandas can read?
    3. do clubs come out of it, with how each qualified?
    4. do the three competitions hold 36 clubs each, and no club twice?

Everything found is printed before it is judged, so a parse that fails is still
worth reading: the section headings and table columns printed here are what an
adapter has to be written against.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from whul.sources import wikipedia  # noqa: E402

RULE = "-" * 78


def probe_one(competition: str, season: str, dump: str, session) -> dict[str, str]:
    title = wikipedia.title_for(competition, season)
    print(f"\n{RULE}\n{competition}  --  {title}\n{RULE}")

    try:
        found = wikipedia.sections(title, session)
    except Exception as exc:  # noqa: BLE001 -- reporting is the whole job
        print(f"  could not read the article: {type(exc).__name__}: {exc}")
        return {}

    wanted = wikipedia.matching_sections(found)
    print(f"  {len(found)} section(s); {len(wanted)} might name the teams:")
    for section in wanted:
        print(f"    [{section.get('index')}] {section.get('line')}")
    if not wanted:
        print("    None matched. Every heading, so a pattern can be added:")
        for section in found[:40]:
            print(f"      [{section.get('index')}] {section.get('line')}")
        return {}

    clubs: dict[str, str] = {}
    placeholders: list[str] = []
    for section in wanted:
        index, line = str(section.get("index")), str(section.get("line"))
        try:
            tables = wikipedia.section_tables(title, index, session)
        except Exception as exc:  # noqa: BLE001
            print(f"\n  section [{index}] {line}: FAILED "
                  f"{type(exc).__name__}: {exc}")
            continue
        print(f"\n  section [{index}] {line}: {len(tables)} table(s)")
        for number, frame in enumerate(tables):
            print(f"    table {number}: {frame.shape[0]} rows x {frame.shape[1]} cols")
            print(f"      columns: {[str(c) for c in frame.columns]}")
            entries = wikipedia.clubs_from(frame)
            real = [(n, h) for n, h in entries if not wikipedia.is_placeholder(n)]
            holes = [n for n, _ in entries if wikipedia.is_placeholder(n)]
            placeholders += holes
            print(f"      -> {len(real)} club(s), {len(holes)} placeholder(s)")
            if dump and dump.lower() in competition.lower():
                print(frame.head(8).to_string())
            for name, how in real[:4]:
                print(f"         {name:<28}{how[:44]}")
            if len(real) > 4:
                print(f"         ... and {len(real) - 4} more")
            for name, how in real:
                clubs.setdefault(name, f"{line}: {how}" if how else line)

    print(f"\n  {len(clubs)} distinct club(s) across every table read.")
    if placeholders:
        print(f"  {len(placeholders)} placeholder row(s) skipped, e.g. "
              f"{placeholders[0]!r}")
    return clubs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", default="2025-26",
                    help="the UEFA season, e.g. 2027-28. Default is a finished "
                         "one, whose tables are complete")
    ap.add_argument("--dump", default="",
                    help="print the head of every table for one competition")
    args = ap.parse_args()

    if importlib.util.find_spec("lxml") is None:
        sys.exit("pandas needs lxml to read a table out of HTML: pip install lxml")
    try:
        wikipedia.title_for("Champions League", args.season)
    except (ValueError, KeyError):
        sys.exit(f"--season should look like 2027-28, not {args.season!r}")

    print(RULE)
    print(f"UEFA qualification probe -- {args.season}")
    print(f"  en.wikipedia.org through the MediaWiki API, "
          f"{wikipedia.PAUSE:.0f}s between requests")
    print(RULE)

    session = requests.Session()
    entrants = {
        competition: probe_one(competition, args.season, args.dump, session)
        for competition in wikipedia.COMPETITION_TITLES
    }

    print(f"\n{RULE}\nDOES IT HOLD TOGETHER?\n{RULE}")
    for competition, clubs in entrants.items():
        mark = "ok" if len(clubs) >= wikipedia.LEAGUE_PHASE_SIZE else "SHORT"
        print(f"  {competition:<20}{len(clubs):>4} club(s)   {mark}")

    total = sum(len(c) for c in entrants.values())
    if not total:
        print("\n  Nothing was read, so there is nothing to cross-check. The "
              "section\n  headings above are the useful part.")
        return 1

    problems = wikipedia.check(entrants)
    if problems:
        print()
        for problem in problems[:12]:
            print(f"  - {problem}")
        print("\n  Something is off. That is the useful outcome: the headings and")
        print("  columns above are what an adapter has to be written against.")
        return 1

    print(f"\n  {total} club(s) in total, none in two competitions.")
    print("  The shape holds. Paste this back and the adapter can be written")
    print("  against a structure that has actually been seen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
