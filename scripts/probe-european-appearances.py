#!/usr/bin/env python3
"""Does ESPN report a European appearance for a player who did not score?

The question came from the site. Paris Saint-Germain won a Champions League tie
and the club's row scored it; four PSG players on the same roster showed nothing
at all. Their Champions League rows were there and correctly attributed -- the
feed had simply returned every one of them with zero appearances.

Across the whole published day, six players had a Champions League appearance
and twenty-eight had a row saying zero. The six are exactly the six who scored.
That is either a lag, which fixes itself, or it is the shape of the endpoint,
which means European player production cannot be read from a roster at all and
has to come from match summaries instead. The two look identical from here and
lead to completely different work.

    python scripts/probe-european-appearances.py
    python scripts/probe-european-appearances.py --club "Real Madrid" --league laliga
    python scripts/probe-european-appearances.py --competition uel

What to look for, in order:

1. The comparison table. A player with league appearances and zero European
   ones, whose club has played in Europe, is the case in question.
2. "appearances reported for N of M". If N is the number who scored, the
   endpoint only reports a player once he has a goal contribution.
3. The raw block for one non-scorer, at the end. Whether `general.appearances`
   is *absent* or *present and zero* is the whole question: absent is a feed
   that has not got there yet, present-and-zero is a feed asserting he did not
   play, which for a player who did is a different problem entirely.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from whul.sources import espn_soccer  # noqa: E402
from whul.sources.espn_soccer import BASE, LEAGUE_PATHS, roster_season  # noqa: E402

RULE = "-" * 78


def squad(competition: str, club: str, season: int, session):
    """One club's players in one competition, through the real adapter."""
    clubs = espn_soccer.team_ids(competition, season, session)
    if not clubs:
        print(f"  {competition}: no clubs listed for {season}")
        return None, None
    match = next((name for name in clubs if name.lower() == club.lower()), None)
    if match is None:
        match = next((name for name in clubs if club.lower() in name.lower()), None)
    if match is None:
        print(f"  {competition}: no club matching {club!r}. It lists: "
              f"{', '.join(sorted(clubs)[:8])} ...")
        return None, None
    return match, espn_soccer.load_squad(competition, clubs[match], season, session)


def raw_athlete(competition: str, club_id: str, season: int, name: str, session):
    """The payload for one athlete, as it actually arrives."""
    sport, path = LEAGUE_PATHS[competition]
    payload = espn_soccer._get(
        f"{BASE}/{sport}/{path}/teams/{club_id}/roster",
        {"season": roster_season(competition, season)}, session,
    )
    for athlete in espn_soccer._athletes(payload):
        if str(athlete.get("displayName") or "") == name:
            return athlete
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--club", default="Paris Saint-Germain")
    parser.add_argument("--league", default="ligue1",
                        help="the club's domestic league key")
    parser.add_argument("--competition", default="ucl")
    parser.add_argument("--season", type=int, default=2027,
                        help="our season label; 2027 is 2026-27")
    args = parser.parse_args()

    session = requests.Session()
    print(f"\n{RULE}\n{args.club} -- {args.league} against {args.competition}, "
          f"our season {args.season}\n{RULE}")

    name, home = squad(args.league, args.club, args.season, session)
    _, euro = squad(args.competition, args.club, args.season, session)
    if home is None or euro is None or home.empty or euro.empty:
        print("\nOne of the two returned nothing, so there is nothing to compare.")
        return 1

    left = home.set_index("player")
    right = euro.set_index("player")
    print(f"\n  {'player':<28}{'league app':>11}{'league g':>10}"
          f"{'euro app':>10}{'euro g':>8}")
    for player in right.index:
        lg = left.loc[player] if player in left.index else None
        eu = right.loc[player]
        print(f"  {str(player)[:27]:<28}"
              f"{(float(lg['matches']) if lg is not None else 0):>11.0f}"
              f"{(float(lg['goals']) if lg is not None else 0):>10.0f}"
              f"{float(eu['matches']):>10.0f}{float(eu['goals']):>8.0f}")

    played = euro[euro["matches"] > 0]
    scored = euro[euro["goals"] > 0]
    print(f"\n  appearances reported for {len(played)} of {len(euro)} in the squad")
    print(f"  goals reported for       {len(scored)}")
    if len(played) and set(played.index) == set(scored.index):
        print("  -> every player with an appearance also has a goal. The endpoint "
              "is reporting scorers, not appearances.")
    elif len(played) > len(scored):
        print("  -> appearances are reported for players who did not score, so "
              "the roster is a usable source and this is a lag.")

    # The decisive detail: absent block, or block asserting zero?
    quiet = [p for p in right.index
             if float(right.loc[p, "matches"]) == 0
             and p in left.index and float(left.loc[p, "matches"]) > 0]
    if not quiet:
        print("\n  Nobody played at home and not in Europe, so there is no case "
              "to inspect.")
        return 0
    who = quiet[0]
    clubs = espn_soccer.team_ids(args.competition, args.season, session)
    athlete = raw_athlete(args.competition, clubs[name], args.season, who, session)
    print(f"\n{RULE}\n{who}: the raw record from the {args.competition} roster\n{RULE}")
    if athlete is None:
        print("  not in the payload at all")
        return 0
    stats = athlete.get("statistics")
    if not stats:
        print("  no `statistics` key at all -- the feed has nothing for him yet, "
              "which is a lag rather than an assertion that he did not play")
        return 0
    print(json.dumps(stats, indent=2)[:3000])
    flat = espn_soccer._stats(athlete)
    print(f"\n  general.appearances present: {'general.appearances' in flat}"
          f"  value: {flat.get('general.appearances')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
