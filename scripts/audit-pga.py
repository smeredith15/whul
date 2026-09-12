#!/usr/bin/env python3
"""Show the arithmetic behind every PGA score, so it can be checked by hand.

Deliberately independent of the scoring code. The finish table and the major
multiplier are restated here from PGA.R rather than imported, and every score is
rebuilt from the finishes stored in ``raw_stats`` -- the same labels the site
shows, so a line here can be read straight off a leaderboard. Where this script
and the pipeline disagree, one of them is wrong and the mismatch is printed,
which is the point. Importing ``whul.scoring.golf`` would make the two agree by
construction and check nothing.

    python scripts/audit-pga.py
    python scripts/audit-pga.py --db other.sqlite3
    python scripts/audit-pga.py --player "Morikawa"

Golf is scored over the league year's own window rather than a season, so the
first thing checked is the window: which tournaments fall inside it, and whether
anything on the edge was kept or dropped when it should not have been.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whul.store import open_store  # noqa: E402

# --------------------------------------------------------------------------
# The rules, restated. Change these only to match a rules change -- never to
# make this script agree with the pipeline.
# --------------------------------------------------------------------------

#: Points for finishing 1st through 30th. Nothing below 30th scores at all.
FINISH_POINTS = (
    500, 300, 190, 135, 110, 100, 90, 85, 80, 75,
    70, 65, 60, 57, 54, 51, 48, 45, 42, 39,
    36, 33, 30, 27, 24, 21, 18, 15, 12, 10,
)

#: The four majors and the Players, worth half again as much.
MAJOR_MULTIPLIER = 1.5
MAJORS = re.compile(
    r"masters|pga championship|u\.?s\.? open|open championship|players",
    re.IGNORECASE,
)

#: Tournaments that are majors but that the pattern above would not catch,
#: because a feed may name them more briefly than the pattern expects. Listed
#: separately so a miss shows up as a warning rather than as a quietly
#: single-weighted major -- a 4th place worth 135 instead of 202.50 is a
#: plausible number that nothing else would question.
#: The TOUR Championship is deliberately absent: it is the FedEx Cup finale,
#: not a major, and listing it here made the audit cry wolf on every golfer.
NEARLY_MAJOR = re.compile(
    r"^the open\b|^open$|british open|us open|usopen|the masters|"
    r"^pga champ|players champ",
    re.IGNORECASE,
)

#: A tie takes the position it is tied at: five players tied for 3rd are each
#: paid 3rd-place points rather than splitting four places between them.
TIES_SHARE_THE_PLACE = True

RULE = "-" * 78


def points_for(place: int | None) -> float:
    if place is None or not (1 <= place <= len(FINISH_POINTS)):
        return 0.0
    return float(FINISH_POINTS[place - 1])


def place_from(label: str) -> int | None:
    """The finishing position out of a label like 'BMW Championship 4th'."""
    match = re.search(r"(\d+)(?:st|nd|rd|th)\s*$", str(label).strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def tournament_from(label: str) -> str:
    return re.sub(r"\s*\d+(?:st|nd|rd|th)\s*$", "", str(label).strip(), flags=re.IGNORECASE)


class Audit:
    def __init__(self, db: str, as_of: str | None, only: str | None):
        self.store = open_store(db)
        self.db = db
        self.only = (only or "").lower()
        self.season, self.as_of = self._latest_day(as_of)
        self.version, self.frozen_at = self._frozen_version()
        self.benchmark = self._benchmark()
        self.opens = self._window_opens()
        self.notes: list[tuple[str, str, str]] = []
        self.standings: dict[str, float] = {}
        self.seen_events: dict[str, str] = {}

    def _latest_day(self, as_of):
        rows = self.store.query(
            "SELECT season, MAX(as_of) AS day FROM raw_stats WHERE league = 'PGA'"
            + (" AND as_of = ?" if as_of else "") + " GROUP BY season",
            (as_of,) if as_of else (),
        )
        if rows.empty:
            sys.exit(f"No PGA rows in {self.db}" + (f" for {as_of}" if as_of else ""))
        return str(rows.iloc[-1]["season"]), str(rows.iloc[-1]["day"])

    def _frozen_version(self):
        rows = self.store.query(
            "SELECT version, frozen_at FROM benchmark_versions "
            "WHERE season = ? AND frozen_at IS NOT NULL ORDER BY frozen_at DESC LIMIT 1",
            (self.season,),
        )
        if rows.empty:
            sys.exit(f"No frozen benchmark for {self.season}")
        return str(rows.iloc[0]["version"]), str(rows.iloc[0]["frozen_at"])

    def _benchmark(self):
        rows = self.store.query(
            "SELECT benchmark, pool_size, seasons FROM benchmarks "
            "WHERE version = ? AND asset_type = 'Player' AND norm_key = 'PGA'",
            (self.version,),
        )
        return None if rows.empty else rows.iloc[0]

    def _window_opens(self) -> date:
        from whul.config.league import season_start

        return season_start("PGA")

    def rostered(self):
        """Every golfer in a slot today, whether or not the feed mentioned him.

        The audit walks feed rows, so a rostered golfer the feed never named is
        invisible in it -- and a golfer who did not tee it up and a golfer the
        feed forgot look exactly alike from inside a list of the ones it did
        name. Only the roster says who *should* have been there.
        """
        return self.store.query(
            "SELECT a.asset_id, a.display_name, m.display_name AS manager, "
            "       ss.counts, "
            "       (SELECT COUNT(*) FROM raw_stats r WHERE r.asset_id = a.asset_id "
            "         AND r.as_of = ?) AS rows_today, "
            "       (SELECT COUNT(*) FROM raw_stats r WHERE r.asset_id = a.asset_id) "
            "         AS rows_ever "
            "FROM assets a "
            "JOIN slot_occupancy so USING (asset_id) "
            "JOIN roster_slots rs USING (slot_id) "
            "JOIN managers m USING (manager_id) "
            "LEFT JOIN slot_scores ss ON ss.slot_id = rs.slot_id AND ss.as_of = ? "
            "WHERE a.league = 'PGA' AND so.end_date IS NULL "
            "ORDER BY a.display_name",
            (self.as_of, self.as_of),
        )

    def absent(self):
        """Rostered golfers with nothing in the feed today.

        Split by whether the feed has *ever* named them. A golfer who was being
        scored and stopped has missed a cut or withdrawn, which is ordinary. One
        who has never appeared at all, across every day the league year has run,
        is a different thing: he may simply not have teed it up, or the roster
        may spell him in a way the feed does not, and from inside a list of the
        golfers that did match those two look identical. Only the never-seen
        list makes the second one visible.
        """
        frame = self.rostered()
        if frame.empty:
            return
        missing = frame[frame["rows_today"] == 0]
        print(f"\n{RULE}\nROSTERED BUT ABSENT FROM THE FEED\n{RULE}")
        if missing.empty:
            print(f"  none -- all {len(frame)} rostered golfer(s) have a row today")
            return

        never = missing[missing["rows_ever"] == 0]
        lapsed = missing[missing["rows_ever"] > 0]
        print(f"  {len(missing)} of {len(frame)} rostered golfer(s) have no row on "
              f"{self.as_of}.")

        if not lapsed.empty:
            print(f"\n  Scored before, not today -- a missed cut or a week off:\n")
            for row in lapsed.itertuples():
                print(f"    {str(row.display_name):<24}{str(row.manager):<10}"
                      f"last seen in {int(row.rows_ever)} earlier pull(s)")
        if not never.empty:
            print(f"\n  NEVER seen in this feed, on any day of the league year:\n")
            for row in never.itertuples():
                print(f"    {str(row.display_name):<24}{str(row.manager):<10}"
                      f"has scored 0.00 every day")
            print(f"\n  Either he has not teed it up since {self.opens}, or the "
                  f"roster spells him\n  in a way the feed does not. Those look "
                  f"the same from here and only one\n  is acceptable -- check him "
                  f"against the tournament list above.")

    def rows(self):
        frame = self.store.query(
            "SELECT rs.asset_id, a.display_name, rs.stats FROM raw_stats rs "
            "JOIN assets a USING (asset_id) "
            "WHERE rs.league = 'PGA' AND rs.as_of = ? ORDER BY a.display_name",
            (self.as_of,),
        )
        for row in frame.itertuples():
            if self.only and self.only not in str(row.display_name).lower():
                continue
            yield str(row.asset_id), str(row.display_name), json.loads(row.stats)

    def stored_score(self, asset_id: str):
        rows = self.store.query(
            "SELECT league_points, scaled_score FROM daily_scores "
            "WHERE asset_id = ? AND season = ? AND as_of = ?",
            (asset_id, self.season, self.as_of),
        )
        if rows.empty:
            return None, None
        return float(rows.iloc[0]["league_points"]), float(rows.iloc[0]["scaled_score"])

    def slot(self, asset_id: str):
        rows = self.store.query(
            "SELECT m.display_name AS manager, rs.category, ss.counts FROM slot_scores ss "
            "JOIN roster_slots rs USING (slot_id) JOIN managers m USING (manager_id) "
            "WHERE ss.asset_id = ? AND ss.as_of = ?",
            (asset_id, self.as_of),
        )
        if rows.empty:
            return None, False, "not in any slot on this day"
        r = rows.iloc[0]
        counts = "counts toward the total" if int(r["counts"]) else "held, does NOT count"
        return str(r["manager"]), bool(int(r["counts"])), \
            f"{r['manager']} -- {r['category']} -- {counts}"

    # -- the derivation ------------------------------------------------------

    def golfer(self, asset_id: str, name: str, stats: dict):
        _, counts, where = self.slot(asset_id)
        print(f"\n{'=' * 78}\n{name}\n  {where}")

        finishes = stats.get("finishes") or []
        if not finishes:
            print("\n  no tournaments inside the league year yet")
            return

        print(f"\n  Every finish since the league year opened {self.opens}")
        print(f"    {'date':<12}{'tournament':<32}{'place':>6}{'table':>8}"
              f"{'major':>8}{'= points':>10}")

        total = 0.0
        flags = []
        for finish in sorted(finishes, key=lambda f: str(f.get("date")), reverse=True):
            label = str(finish.get("label", ""))
            when = str(finish.get("date", ""))[:10]
            place = place_from(label)
            event = tournament_from(label)
            self.seen_events.setdefault(event, when)

            base = points_for(place)
            major = bool(MAJORS.search(event))
            points = base * (MAJOR_MULTIPLIER if major else 1.0)
            total += points

            shown_place = f"{place}" if place is not None else "--"
            shown_table = f"{base:,.0f}" if place else "0"
            shown_major = "x1.5" if major else "--"
            print(f"    {when:<12}{event[:31]:<32}{shown_place:>6}{shown_table:>8}"
                  f"{shown_major:>8}{points:>10.2f}")

            flags += self.check_finish(label, event, when, place, major)

        print(f"    {'':<12}{'TOTAL':<32}{'':>6}{'':>8}{'':>8}{total:>10.2f}")
        print(f"\n    {len(finishes)} start(s). Nothing below 30th scores; a tie takes "
              f"the place\n    it is tied at rather than splitting it.")

        stored_total = stats.get("total_points")
        if stored_total is not None and abs(total - float(stored_total)) > 0.01:
            flags.append((
                "the finishes do not sum to the stored total",
                f"{total:,.2f} vs {float(stored_total):,.2f}",
            ))

        scaled = self.normalize(total)
        if scaled is not None:
            self.verdict(asset_id, name, scaled, counts, flags)

    def check_finish(self, label, event, when, place, major) -> list[tuple[str, str]]:
        """The ways a finish can be wrong without looking wrong."""
        flags = []
        if place is None:
            flags.append((
                "a finish with no position in its label, so it scores nothing "
                "and nothing says why",
                label,
            ))
        if not major and NEARLY_MAJOR.search(event):
            flags.append((
                "reads like a major but was scored at single weight. A feed that "
                "names it more briefly than the pattern expects loses the 1.5x "
                "quietly -- a 4th worth 135 rather than 202.50",
                f"{event} ({when})",
            ))
        if when and when < self.opens.isoformat():
            flags.append((
                f"finished before the league year opened on {self.opens} and is "
                f"being counted anyway",
                f"{event} ({when})",
            ))
        return flags

    def normalize(self, points: float) -> float | None:
        if self.benchmark is None:
            print(f"\n    no PGA benchmark in {self.version}")
            return None
        bench = float(self.benchmark["benchmark"])
        scaled = round(points / bench * 100, 2)
        print(f"\n    benchmark  PGA = {bench:,.2f} points")
        print(f"      the 99th percentile of {self.benchmark['pool_size']} "
              f"golfer-years drawn from the league years")
        print(f"      {self.benchmark['seasons']}")
        print(f"    scaled     {points:,.2f} / {bench:,.2f} x 100 = {scaled:.2f}")
        return scaled

    def verdict(self, asset_id, name, scaled, counts, flags):
        _, stored_scaled = self.stored_score(asset_id)
        if stored_scaled is None:
            print(f"\n    CHECK  this script says {scaled:.2f}, but nothing is stored "
                  f"in daily_scores")
            self.notes.append((name, "scored here but absent from daily_scores", ""))
        elif abs(scaled - stored_scaled) > 0.02:
            print(f"\n    CHECK  this script says {scaled:.2f}, the database says "
                  f"{stored_scaled:.2f}   <-- MISMATCH")
            self.notes.append((
                name, "the stored score does not match the current rules",
                f"{stored_scaled:.2f} stored, {scaled:.2f} under the rules as they stand",
            ))
            manager, _, _ = self.slot(asset_id)
            if manager and counts:
                self.standings[manager] = (
                    self.standings.get(manager, 0.0) + scaled - stored_scaled
                )
        else:
            print(f"\n    CHECK  {scaled:.2f} recomputed, {stored_scaled:.2f} stored"
                  f"   -- agrees")
        for kind, detail in flags:
            print(f"    NOTE   {kind}" + (f"  [{detail}]" if detail else ""))
            self.notes.append((name, kind, detail))

    # -- framing -------------------------------------------------------------

    def header(self):
        print(RULE)
        print(f"PGA audit -- {self.db}")
        print(f"  scores as of      {self.as_of}   (season {self.season})")
        print(f"  league year opens {self.opens}   -- golf is scored over the "
              f"year's own window,")
        print(f"                    not a season, so only finishes on or after "
              f"this date count")
        print(f"  benchmark version {self.version}, frozen {self.frozen_at[:19]}")
        print(RULE)

    def tournaments(self):
        """Every event any rostered golfer played, oldest first.

        The failure this catches is an absent tournament. A missing finish is
        invisible on a player's own line -- the total is simply lower, and a
        golfer who missed a cut and a golfer the feed forgot look identical.
        Seeing the week-by-week list together is what makes a hole obvious.
        """
        print(f"\n{RULE}\nTOURNAMENTS SEEN, OLDEST FIRST\n{RULE}")
        if not self.seen_events:
            print("  none")
            return
        for event, when in sorted(self.seen_events.items(), key=lambda kv: kv[1]):
            major = " (major, x1.5)" if MAJORS.search(event) else ""
            print(f"  {when}  {event}{major}")
        print(f"\n  {len(self.seen_events)} event(s) between {self.opens} and "
              f"{self.as_of}. A week missing from this list is a week nobody was "
              f"scored for.")

    def footer(self):
        print(f"\n{RULE}\nWORTH A SECOND LOOK\n{RULE}")
        if not self.notes:
            print("  nothing -- every score recomputed to the stored value")
        else:
            grouped: dict[str, list[tuple[str, str]]] = {}
            for who, kind, detail in self.notes:
                grouped.setdefault(kind, []).append((who, detail))
            for kind, whom in grouped.items():
                print(f"\n  {kind}")
                print(f"    {len(whom)} affected:")
                for who, detail in whom:
                    print(f"      {who:<24}{detail}")

        if self.standings:
            print(f"\n{RULE}\nWHAT RERUNNING THE PIPELINE WOULD MOVE\n{RULE}")
            print("  Counting slots only -- a held asset changes nothing.\n")
            for manager, delta in sorted(self.standings.items(), key=lambda kv: kv[1]):
                print(f"    {manager:<16}{delta:+.2f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/whul.sqlite3")
    ap.add_argument("--as-of", default=None, help="a day in raw_stats; default the latest")
    ap.add_argument("--player", default=None, help="substring of a name, to audit one")
    args = ap.parse_args()

    audit = Audit(args.db, args.as_of, args.player)
    audit.header()
    for asset_id, name, stats in audit.rows():
        audit.golfer(asset_id, name, stats)
    audit.tournaments()
    audit.absent()
    audit.footer()


if __name__ == "__main__":
    main()
