#!/usr/bin/env python3
"""Show the arithmetic behind every NCAAF score -- and, when there are none,
say precisely why.

Deliberately independent of the scoring code: the weights are restated here
from the NCAA R scripts rather than imported, and every score is rebuilt from
the figures stored in ``raw_stats``.

    python scripts/audit-ncaaf.py
    python scripts/audit-ncaaf.py --rules      # the scoring system, in full
    python scripts/audit-ncaaf.py --probe      # ask ESPN what it has (needs network)

On the current database there is nothing to recompute: eight teams are rostered,
all eight score 0.00, and no NCAAF row has ever been written. That is the whole
finding, and it is worth more than a clean recomputation would be -- a league
that scores nothing looks identical to a league that has not kicked off, and
eight teams on zero in September is not that.

``--probe`` is what tells them apart, and it needs a machine that can reach
ESPN. It asks three questions in order, because they fail differently:

    1. does ESPN's team index know the names on the roster?
    2. does the schedule endpoint return events for them?
    3. are any of those events finished?

A "no" at 1 is a naming problem and costs everything. A "no" at 2 is a broken
request. A "no" at 3 is simply a season that has not been played yet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whul.store import open_store  # noqa: E402

# --------------------------------------------------------------------------
# The rules, restated. Change these only to match a rules change.
# --------------------------------------------------------------------------

WEIGHTS = [
    ("wins", 10.0, "every win"),
    ("big_wins", 2.0, "won by 13+ in conference, 20+ out of it"),
    ("conf_wins", 2.0, "wins against conference opponents"),
    ("conf_title_win", 6.0, "winning the conference championship game"),
    ("playoff_app", 10.0, "reaching the College Football Playoff"),
    ("playoff_wins", 15.0, "every playoff win"),
    ("point_diff", 0.05, "points scored minus points allowed"),
]
BIG_WIN_CONF = 13
BIG_WIN_NONCONF = 20
REG_CHAMP_POOL = 6.0

RULE = "-" * 78


def show_rules():
    print(RULE)
    print("NCAAF SCORING, AS THIS SCRIPT UNDERSTANDS IT")
    print(RULE)
    print("""
  Team slots only -- there are no NCAA player slots, so no box scores are
  needed. Game results plus conference affiliation are enough.
""")
    print(f"    {'term':<18}{'each':>8}   what it counts")
    for name, weight, what in WEIGHTS:
        print(f"    {name:<18}{weight:>8.2f}   {what}")
    print(f"""
    {'reg_champ':<18}{REG_CHAMP_POOL:>8.2f}   split evenly among co-champions, so a
    {'':<18}{'':>8}   three-way title is {REG_CHAMP_POOL / 3:.2f} each

  A blowout is worth the same whoever it is against, but the bar moves: {BIG_WIN_CONF}
  points in conference, {BIG_WIN_NONCONF} out of it. A conference field is stronger, so
  the same margin is harder to reach.

  CONFERENCE AFFILIATION IS LOAD-BEARING
    Conference wins are scored directly and the regular-season title is split
    among co-champions, so a feed that returns games without conference data
    cannot score this league at all. It does not fail loudly either: every
    conf_wins is zero, nobody is a champion, and the totals are simply lower.

  THE OPPONENT POOL
    A scoreboard request returns games *involving* a listed team, so the
    opponent may be from a lower division. Those teams would enter the
    benchmark pool with one or two games apiece and drag it down, so the
    division's members are named explicitly rather than approximated with a
    minimum-games filter.
""")


class Audit:
    def __init__(self, db, as_of=None):
        self.store = open_store(db)
        self.db = db
        self.season, self.as_of = self._latest_day(as_of)
        self.version, self.frozen_at = self._frozen_version()
        self.benchmark = self._benchmark()

    def _latest_day(self, as_of):
        rows = self.store.query(
            "SELECT season, MAX(as_of) AS day FROM slot_scores GROUP BY season"
        )
        if rows.empty:
            sys.exit(f"No scored days in {self.db}")
        return str(rows.iloc[-1]["season"]), str(as_of or rows.iloc[-1]["day"])

    def _frozen_version(self):
        rows = self.store.query(
            "SELECT version, frozen_at FROM benchmark_versions WHERE season = ? "
            "AND frozen_at IS NOT NULL ORDER BY frozen_at DESC LIMIT 1", (self.season,),
        )
        if rows.empty:
            sys.exit(f"No frozen benchmark for {self.season}")
        return str(rows.iloc[0]["version"]), str(rows.iloc[0]["frozen_at"])

    def _benchmark(self):
        rows = self.store.query(
            "SELECT benchmark, pool_size, seasons FROM benchmarks WHERE version = ? "
            "AND asset_type = 'Team' AND norm_key = 'NCAAF'", (self.version,),
        )
        return None if rows.empty else rows.iloc[0]

    def opens(self):
        from whul.config.league import season_start
        return season_start("NCAAF")

    def roster(self):
        return self.store.query(
            "SELECT a.asset_id, a.display_name, m.display_name AS manager, "
            "  ss.score, ss.counts, "
            "  (SELECT COUNT(*) FROM raw_stats r WHERE r.asset_id = a.asset_id) AS rows_ever "
            "FROM assets a JOIN slot_occupancy so USING (asset_id) "
            "JOIN roster_slots rs USING (slot_id) JOIN managers m USING (manager_id) "
            "LEFT JOIN slot_scores ss ON ss.slot_id = rs.slot_id AND ss.as_of = ? "
            "WHERE a.league = 'NCAAF' AND so.end_date IS NULL ORDER BY a.display_name",
            (self.as_of,),
        )

    def header(self):
        print(RULE)
        print(f"NCAAF audit -- {self.db}")
        print(f"  scores as of      {self.as_of}   (season {self.season})")
        print(f"  league year opens {self.opens()}   -- ESPN calls the opening "
              f"weekend Week 1 from")
        print(f"                    August 22, so the season is {(_today() - self.opens()).days} "
              f"days old as of today")
        bench = self.benchmark
        got = f"{float(bench['benchmark']):,.2f}" if bench is not None else "MISSING"
        print(f"  benchmark version {self.version}   NCAAF = {got}")
        print(RULE)

    def scores(self):
        roster = self.roster()
        print(f"\n{RULE}\nWHAT THE ROSTER IS SCORING\n{RULE}")
        if roster.empty:
            print("  nothing rostered in NCAAF")
            return roster
        print(f"    {'team':<28}{'manager':<10}{'score':>8}{'counts':>8}"
              f"{'feed rows ever':>16}")
        for row in roster.itertuples():
            score = "--" if row.score is None else f"{float(row.score):.2f}"
            counts = "yes" if row.counts else "no"
            print(f"    {str(row.display_name):<28}{str(row.manager):<10}{score:>8}"
                  f"{counts:>8}{int(row.rows_ever):>16}")
        return roster

    def totals(self):
        """Recompute from stored figures, where any exist."""
        rows = self.store.query(
            "SELECT rs.asset_id, a.display_name, rs.stats FROM raw_stats rs "
            "JOIN assets a USING (asset_id) WHERE rs.league = 'NCAAF' AND rs.as_of = ?",
            (self.as_of,),
        )
        print(f"\n{RULE}\nTHE ARITHMETIC\n{RULE}")
        if rows.empty:
            print(f"  No NCAAF row on {self.as_of}, so there is no arithmetic to "
                  f"check.\n")
            return
        for row in rows.itertuples():
            stats = json.loads(row.stats)
            print(f"\n  {row.display_name}")
            print(f"    {'term':<18}{'count':>8}{'x each':>9}{'= points':>10}")
            total = 0.0
            for name, weight, _ in WEIGHTS:
                value = float(stats.get(name) or 0.0)
                points = value * weight
                total += points
                print(f"    {name:<18}{value:>8.2f}{weight:>9.2f}{points:>10.2f}")
            champ = float(stats.get("reg_champ_points") or 0.0)
            if champ:
                total += champ
                print(f"    {'reg_champ':<18}{'':>8}{'':>9}{champ:>10.2f}")
            print(f"    {'TOTAL':<18}{'':>8}{'':>9}{total:>10.2f}")
            stored = stats.get("total_points")
            if stored is not None:
                agree = abs(total - float(stored)) < 0.01
                print(f"    {'stored':<18}{'':>8}{'':>9}{float(stored):>10.2f}"
                      f"   {'agrees' if agree else '<-- MISMATCH'}")

    def diagnosis(self, roster):
        print(f"\n{RULE}\nWHY THERE IS NOTHING\n{RULE}")
        if roster.empty:
            return
        never = roster[roster["rows_ever"] == 0]
        if never.empty:
            print("  Nothing to explain -- every rostered team has been in the feed.")
            return
        days = (_today() - self.opens()).days
        print(f"  {len(never)} of {len(roster)} rostered team(s) have never had a "
              f"feed row, {days} days")
        print(f"  into a season that opened {self.opens()}. Every one of them "
              f"scores 0.00 and counts.\n")
        print("  Three things look identical from here and only one is acceptable:\n")
        print("    1. ESPN's team index does not know these names, so no schedule")
        print("       was ever requested for them. Costs the whole season.")
        print("    2. The schedule request returned no events. A broken request.")
        print("    3. Events came back and none is finished. A season that has not")
        print("       been played -- which after a fortnight of it, it has.")
        print(f"\n  Run --probe from a machine that can reach ESPN to tell them "
              f"apart.")


def _today():
    from datetime import date
    return date.today()


def probe(db):
    """Ask ESPN the three questions, in order."""
    from whul.sources import espn

    audit = Audit(db)
    roster = audit.roster()
    names = [str(n) for n in roster["display_name"]]
    print(RULE)
    print(f"NCAAF probe -- {len(names)} rostered team(s), season "
          f"{audit.opens().year}")
    print(RULE)

    print("\n1. Does ESPN's team index know these names?\n")
    try:
        index = espn.team_index("ncaaf")
    except Exception as exc:  # noqa: BLE001 -- the point is to report it
        print(f"   FAILED to load the index: {type(exc).__name__}: {exc}")
        return 1
    lookup = {espn._match_key(name): team_id for name, team_id in index.items()}
    print(f"   the index holds {len(index)} team(s)")
    found = {}
    for name in names:
        team_id = lookup.get(espn._match_key(name))
        print(f"     {name:<30}{'id ' + str(team_id) if team_id else 'NOT FOUND'}")
        if team_id:
            found[name] = team_id
    if not found:
        print("\n   Not one rostered name resolves. This is the whole problem:")
        print("   no schedule is ever requested, so nothing can score.")
        return 1

    print(f"\n2. and 3. What does each team's schedule return?\n")
    print(f"   {'team':<30}{'events':>8}{'finished':>10}{'first':>12}{'last':>12}")
    total_finished = 0
    for name, team_id in found.items():
        try:
            frame = espn.load_team_schedule("ncaaf", team_id, audit.opens().year)
        except Exception as exc:  # noqa: BLE001
            print(f"   {name:<30}FAILED {type(exc).__name__}: {exc}")
            continue
        if frame.empty:
            print(f"   {name:<30}{0:>8}{0:>10}{'--':>12}{'--':>12}")
            continue
        done = frame[frame["completed"].fillna(False).astype(bool)]
        total_finished += len(done)
        print(f"   {name:<30}{len(frame):>8}{len(done):>10}"
              f"{str(frame['game_date'].min()):>12}{str(frame['game_date'].max()):>12}")

    print()
    if total_finished == 0:
        print("   Events came back but none is finished. Either the season really")
        print("   has not been played, or `completed` is not being read from the")
        print("   status the way this adapter expects.")
    else:
        print(f"   {total_finished} finished game(s) are available. They are not")
        print(f"   reaching the database, so the failure is after the fetch --")
        print(f"   the league-start filter, the scorer, or the name resolution.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/whul.sqlite3")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--rules", action="store_true", help="print the scoring system")
    ap.add_argument("--probe", action="store_true", help="ask ESPN what it has")
    args = ap.parse_args()

    if args.rules:
        show_rules()
        return 0
    if args.probe:
        return probe(args.db)

    audit = Audit(args.db, args.as_of)
    audit.header()
    roster = audit.scores()
    audit.totals()
    audit.diagnosis(roster)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
