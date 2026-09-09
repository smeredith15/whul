#!/usr/bin/env python3
"""Which clubs entered the CONCACAF Champions Cup, and can we recognise them?

MLS clubs earn a place in the Champions Cup with their league season, and that
place is worth the same as a Europa League place. Like UEFA entry it cannot be
scraped from fixtures: it depends on where clubs finished, on the Leagues Cup
and the two national cups, and on a berth allocation that moves.

Unlike the UEFA articles, this one's Teams section is NOT organised as "Entry
round" against four "Teams" columns -- it is organised by association and
qualification method. So the reader takes club-shaped cells wherever they sit
rather than trusting column names, and is generous on purpose: a qualification
method read as a club matches nothing on the roster and is dropped there, while
a column heading that moved would cost every club its place.

This probe is what checks that. It was written from a sandbox that answers 403
to Wikipedia, so until it has been run the reader is UNVERIFIED.

    python scripts/probe-concacaf-wikipedia.py                 # a played season
    python scripts/probe-concacaf-wikipedia.py --season 2022 2023 2024 2025 2026
    python scripts/probe-concacaf-wikipedia.py --dump          # the raw tables

The default covers every edition the five benchmark seasons earn a place in:
MLS 2021-2025 play the 2022-2026 Champions Cups. Checking one season proves one
season -- a club renamed between editions goes missing in the others in silence.

What to look for: every MLS entrant you expect, named as ESPN names it. The
"unmatched" list at the end is the one that matters -- an MLS club sitting in
it is eight points going missing in silence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from whul.sources import wikipedia  # noqa: E402

RULE = "-" * 78
COMPETITION = "CONCACAF Champions Cup"


def probe(season: str, dump: bool, session) -> dict[str, str]:
    print(f"\n{RULE}\n{COMPETITION}  --  {season}\n{RULE}")
    for title in wikipedia.titles_for(COMPETITION, season):
        try:
            found = wikipedia.sections(title, session)
        except Exception as exc:  # noqa: BLE001
            print(f"  {title}: {type(exc).__name__} {exc}")
            continue
        section = wikipedia.teams_section(found)
        if section is None:
            headings = ", ".join(str(s.get("line")) for s in found[:12])
            print(f"  {title}: no section headed 'Teams'. It has: {headings}")
            continue
        print(f"  {title}: Teams is section {section.get('index')}")
        tables = wikipedia.section_tables(title, str(section.get("index")), session)
        print(f"  {len(tables)} table(s) in it")
        for n, frame in enumerate(tables):
            print(f"    table {n}: {list(frame.columns)}")
            if dump:
                print(frame.to_string()[:2000])
        entrants: dict[str, str] = {}
        for frame in tables:
            entrants.update(wikipedia.entrants_anywhere(frame))
        print(f"\n  {len(entrants)} club-shaped cell(s) read:")
        for name in sorted(entrants):
            print(f"      {name}")
        return entrants
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # A season already played, whose table is complete and checkable against
    # memory. A forthcoming one carries slots rather than clubs.
    parser.add_argument("--season", nargs="+",
                        default=["2022", "2023", "2024", "2025", "2026"])
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--league", default="mls",
                        help="whose clubs the entrants are matched against")
    args = parser.parse_args()

    session = requests.Session()

    # The half that matters: do the names reach our clubs?
    from whul.scoring.soccer import _compare_key, _find_club
    from whul.sources import espn

    try:
        ours = {_compare_key(club): club for club in espn.load_eligible_teams(args.league)}
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read {args.league}'s clubs ({type(exc).__name__}); "
              f"the match half of this probe is unchecked.")
        return 1

    empty, missing = [], []
    for season in args.season:
        entrants = probe(season, args.dump, session)
        if not entrants:
            print("\n  Nothing read. That is the failure this probe exists to catch.")
            empty.append(season)
            continue

        matched, unmatched = [], []
        for name in sorted(entrants):
            club = _find_club(name, ours)
            (matched if club else unmatched).append((name, club))

        print(f"\n  Matched to {args.league} clubs ({len(matched)}):")
        for name, club in matched:
            print(f"      {name}  ->  {club}")
        print(f"\n  Not matched ({len(unmatched)}) -- expected for every club "
              f"outside {args.league}, and eight silent points for any inside it:")
        for name, _ in unmatched:
            print(f"      {name}")
        if not matched:
            missing.append(season)

    print(f"\n{RULE}\nSummary\n{RULE}")
    if empty:
        print(f"  Read nothing at all for: {', '.join(empty)}")
    if missing:
        print(f"  Read clubs but matched no {args.league} club for: "
              f"{', '.join(missing)}")
    if not empty and not missing:
        print(f"  Every season read clubs and matched at least one {args.league} "
              f"club. Read the unmatched lists above anyway -- a club that is "
              f"missing from one is worth eight points and raises nothing.")
    return 1 if empty or missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
