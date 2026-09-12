#!/usr/bin/env python3
"""Who entered each UEFA competition, and at which round?

Entry is worth points and cannot be scraped from fixtures -- it depends on
final league positions, both cup winners, the cascade when a cup winner has
already qualified, and a coefficient allocation that moves every year. The
participant list is the outcome rather than the working, so it is read instead
of derived.

Only entry. Where a club got to once it was in is the daily scraper's business.

    python scripts/probe-uefa-wikipedia.py                  # a finished season
    python scripts/probe-uefa-wikipedia.py --season 2027-28
    python scripts/probe-uefa-wikipedia.py --dump           # the raw table too

The default is a season already played, whose table is complete and which you
can check against what you remember. A future season's page carries rows naming
a slot rather than a club, and telling those apart is part of what is tested.
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


def probe_one(competition: str, season: str, dump: bool, session) -> dict[str, str]:
    title = wikipedia.title_for(competition, season)
    print(f"\n{RULE}\n{competition}  --  {title}\n{RULE}")

    try:
        found = wikipedia.sections(title, session)
    except Exception as exc:  # noqa: BLE001 -- reporting is the whole job
        print(f"  could not read the article: {type(exc).__name__}: {exc}")
        return {}

    section = wikipedia.teams_section(found)
    if section is None:
        print(f"  no section headed exactly 'Teams'. Every heading, so the")
        print(f"  pattern can be corrected rather than guessed at:")
        for other in found[:40]:
            print(f"    [{other.get('index')}] {other.get('line')}")
        return {}
    print(f"  section [{section.get('index')}] Teams, of {len(found)} in the "
          f"article")

    tables = wikipedia.section_tables(title, str(section.get("index")), session)
    entrants: dict[str, str] = {}
    for number, frame in enumerate(tables):
        print(f"    table {number}: {frame.shape[0]} rows x {frame.shape[1]} cols")
        print(f"      columns: {[str(c) for c in frame.columns]}")
        if dump:
            print(frame.head(6).to_string())
        entrants.update(wikipedia.entrants_from(frame))

    by_round: dict[str, list[str]] = {}
    for club, entry in entrants.items():
        by_round.setdefault(entry or "(none)", []).append(club)
    print(f"\n  {len(entrants)} club(s), by where they came in:")
    for entry in (*wikipedia.ENTRY_ROUNDS, "(none)"):
        clubs = sorted(by_round.get(entry, []))
        if not clubs:
            continue
        mark = "  <- direct entry" if entry == wikipedia.DIRECT_ENTRY else ""
        print(f"\n    {entry} -- {len(clubs)}{mark}")
        for index in range(0, len(clubs), 3):
            print("      " + "".join(f"{c[:24]:<26}" for c in clubs[index:index + 3]))
    return entrants


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", default="2025-26",
                    help="the UEFA season, e.g. 2027-28. Default is a finished "
                         "one, whose table is complete")
    ap.add_argument("--dump", action="store_true",
                    help="print the head of each table as well")
    args = ap.parse_args()

    if importlib.util.find_spec("lxml") is None:
        sys.exit("pandas needs lxml to read a table out of HTML: pip install lxml")
    try:
        wikipedia.title_for("Champions League", args.season)
    except (ValueError, KeyError):
        sys.exit(f"--season should look like 2027-28, not {args.season!r}")

    print(RULE)
    print(f"UEFA entry probe -- {args.season}")
    print(f"  en.wikipedia.org through the MediaWiki API, "
          f"{wikipedia.PAUSE:.0f}s between requests")
    print(f"  reading the 'Teams' section only -- entry, never results")
    print(RULE)

    session = requests.Session()
    entrants = {
        competition: probe_one(competition, args.season, args.dump, session)
        for competition in wikipedia.COMPETITION_TITLES
    }

    print(f"\n{RULE}\nDOES IT HOLD TOGETHER?\n{RULE}")
    for competition, clubs in entrants.items():
        direct = sum(1 for r in clubs.values() if r == wikipedia.DIRECT_ENTRY)
        print(f"  {competition:<20}{len(clubs):>4} club(s), {direct:>3} straight "
              f"into the league phase")

    if not any(entrants.values()):
        print("\n  Nothing was read. The headings above are the useful part.")
        return 1

    problems = wikipedia.check(entrants)
    if problems:
        print()
        for problem in problems[:12]:
            print(f"  - {problem}")
        return 1

    print(f"\n  Every entry round is one the scorer understands, and no league")
    print(f"  phase admits more clubs directly than it holds.")
    print(f"\n  A club in two competitions is not checked and must not be: one")
    print(f"  knocked out of Champions League qualifying transfers into the")
    print(f"  Europa League and belongs in both articles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
