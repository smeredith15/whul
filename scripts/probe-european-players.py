#!/usr/bin/env python3
"""Every rostered player's European line, as the feed reports it right now.

Pulled live rather than read from the database, so it shows what the next run
would score rather than what the last one did. The two differ while ESPN is
still filling a competition in -- which it does club by club, hours or days
after the matches: PSG's whole squad came back with no statistics block at all
while Manchester City's already carried Haaland's goal.

    python scripts/probe-european-players.py
    python scripts/probe-european-players.py --competition ucl
    python scripts/probe-european-players.py --all
    python scripts/probe-european-players.py --db data/whul.sqlite3

Three states, and telling them apart is the point:

  reported   the feed has a statistics block for this player in this
             competition -- these are the figures that will be scored
  waiting    his club is entered and its roster carries no statistics for
             anybody, so the feed has not published the competition yet
  unused     his club's roster does carry statistics for other players, and
             his say he did not appear. That is the feed asserting he sat out

A player in `waiting` whose club has played is not missing data in the sense of
anything being wrong: it arrives on a later run, and the pull re-reads the whole
season every night rather than yesterday's matches, so nothing has to be caught
in the moment. `unused` for a player who did appear would be a different matter
entirely, and is what this exists to make visible.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from whul.scoring import soccer  # noqa: E402
from whul.scoring.postseason import rule_for  # noqa: E402
from whul.scoring.competition import classify_key  # noqa: E402
from whul.sources import espn_soccer  # noqa: E402
from whul.sources.espn import EUROPEAN_COMPETITIONS  # noqa: E402
from whul.benchmark_sources import competition_label  # noqa: E402

RULE = "-" * 96


def rostered(db: str, season: str) -> dict[str, str]:
    """``{player name: manager}`` for the club-soccer players on a roster."""
    from whul.store import open_store

    store = open_store(db)
    frame = store.query(
        "SELECT a.display_name, m.display_name AS manager "
        "FROM assets a JOIN slot_occupancy so USING (asset_id) "
        "JOIN roster_slots rs USING (slot_id) JOIN managers m USING (manager_id) "
        "WHERE a.asset_type = 'Player' AND so.end_date IS NULL AND rs.season = ?",
        (season,),
    )
    return {str(r.display_name): str(r.manager) for r in frame.itertuples()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/whul.sqlite3")
    parser.add_argument("--season", default="2026-27")
    parser.add_argument("--feed-season", type=int, default=2027,
                        help="our season label; 2027 is 2026-27")
    parser.add_argument("--competition", nargs="+", default=list(EUROPEAN_COMPETITIONS))
    parser.add_argument("--all", action="store_true",
                        help="every player in the squads, not only rostered ones")
    args = parser.parse_args()

    names: dict[str, str] = {}
    if not args.all:
        try:
            names = rostered(args.db, args.season)
        except Exception as exc:  # noqa: BLE001
            print(f"Could not read the roster from {args.db} "
                  f"({type(exc).__name__}: {exc}). Showing every player instead.")
            args.all = True
    if names:
        print(f"\n{len(names)} club-soccer player(s) rostered in {args.season}")

    session = requests.Session()
    reported, waiting, unused = [], [], []

    for competition in args.competition:
        label = competition_label(competition)
        rule = rule_for(classify_key(competition, label).tier.value)
        share = f"{rule.bonus_share * 100:g}%" if rule else "not a bonus"
        print(f"\n{RULE}\n{label}  --  paid at {share} of a season\n{RULE}")

        clubs = espn_soccer.team_ids(competition, args.feed_season, session)
        if not clubs:
            print("  no clubs listed; the competition has not been drawn yet")
            continue

        for club, team_id in sorted(clubs.items()):
            try:
                squad = espn_soccer.load_squad(competition, team_id,
                                               args.feed_season, session)
            except Exception as exc:  # noqa: BLE001
                print(f"  {club}: could not read ({type(exc).__name__})")
                continue
            if squad.empty:
                continue
            wanted = squad if args.all else squad[squad["player"].isin(names)]
            if wanted.empty:
                continue
            # A club whose whole squad has nothing is a club the feed has not
            # published, not twenty-four players who all sat out.
            club_has_stats = bool((squad["matches"] > 0).any())

            print(f"\n  {club}")
            for row in wanted.itertuples():
                who = f"{row.player}"
                if names.get(row.player):
                    who += f"  ({names[row.player]})"
                if row.matches > 0:
                    points = soccer.score_players(
                        squad[squad["player"] == row.player].assign(
                            league="x", season=args.feed_season,
                            competition_key=competition, competition=label),
                        postseason=True,
                    )
                    adds = float(points["postseason_bonus"].iloc[0])
                    print(f"      {who:<44} reported  "
                          f"{row.matches:>2.0f} app  {row.goals:>2.0f} g  "
                          f"{row.assists:>2.0f} a  ->  +{adds:.1f}")
                    reported.append(row.player)
                elif club_has_stats:
                    print(f"      {who:<44} unused    the feed says he did not "
                          f"appear")
                    unused.append(row.player)
                else:
                    print(f"      {who:<44} waiting   this club has no statistics "
                          f"for anybody yet")
                    waiting.append(row.player)

    print(f"\n{RULE}\nSummary\n{RULE}")
    print(f"  reported  {len(reported):>3}   figures the next run will score")
    print(f"  unused    {len(unused):>3}   the feed says these players did not appear")
    print(f"  waiting   {len(waiting):>3}   their club is entered and unpublished")
    if waiting:
        print("\n  `waiting` resolves on its own. The pull re-reads the whole "
              "season every night rather than\n  yesterday's matches, and the "
              "feed is never cached, so a figure published late is picked up\n"
              "  on the next run without anything being re-run by hand.")
    if unused:
        print("\n  Check `unused` against what actually happened. A player who "
              "did appear sitting there is\n  the feed asserting otherwise, "
              "which no amount of waiting fixes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
