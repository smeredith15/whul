#!/usr/bin/env python3
"""Show the arithmetic behind every ATP and WTA score, so it can be checked.

Deliberately independent of the scoring code. The round-by-round points tables
and the straight-sets bonuses are restated here rather than imported, and each
player's total is rebuilt from the finishes stored against him. Where this
script and the pipeline disagree, one of them is wrong and the mismatch is
printed.

    python scripts/audit-tennis.py
    python scripts/audit-tennis.py --tour WTA
    python scripts/audit-tennis.py --player Gauff

Tennis pays per round *won*, in increments: reaching the fourth round of a slam
is worth the first three rounds added up. So a total cannot be checked against a
single number -- what can be checked is a range. Every finish here is shown with
the floor it must clear (every round won, no bonuses) and the ceiling it cannot
pass (every win in straight sets), and a total outside those is wrong however
plausible it looks.

The check that matters most is the last one. These totals accumulate over a
league year, so within one they can only grow. A total that came back smaller
than the day before is a feed that has stopped reaching as far back as it did,
and the score simply gets smaller with nothing raised.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whul.store import open_store  # noqa: E402

# --------------------------------------------------------------------------
# The rules, restated. Change these only to match a rules change.
# --------------------------------------------------------------------------

#: Points for *winning* each round, as increments. Every column sums to the
#: event's face value for a champion -- 2000 at a slam, 1000 at a Masters --
#: which is the arithmetic check that the table itself is right.
WIN_POINTS: dict[str, dict[str, float]] = {
    "GS":        {"R128": 50, "R64": 50, "R32": 100, "R16": 200, "QF": 400,
                  "SF": 500, "F": 700},
    "M1000_128": {"R128": 30, "R64": 20, "R32": 50, "R16": 100, "QF": 200,
                  "SF": 250, "F": 350},
    "M1000_64":  {"R64": 50, "R32": 50, "R16": 100, "QF": 200, "SF": 250, "F": 350},
    "A500_32":   {"R32": 50, "R16": 50, "QF": 100, "SF": 130, "F": 170},
    "A500_64":   {"R64": 25, "R32": 25, "R16": 50, "QF": 100, "SF": 130, "F": 170},
    "A250_32":   {"R32": 25, "R16": 25, "QF": 50, "SF": 65, "F": 85},
    "A250_64":   {"R64": 13, "R32": 12, "R16": 25, "QF": 50, "SF": 65, "F": 85},
    "FINALS":    {"RR": 200, "SF": 400, "F": 500},
}

ROUND_ORDER = ("RR", "R128", "R64", "R32", "R16", "QF", "SF", "F", "W")

#: Winning in straight sets pays more the more sets it skipped: best-of-five in
#: three avoids two, best-of-three in two avoids one.
BONUS_BEST_OF_FIVE = 1.5
BONUS_BEST_OF_THREE = 1.25

#: Only ATP main-draw matches at a slam are best of five.
def best_of_five(tour: str, tier: str) -> bool:
    return tour == "ATP" and tier == "GS"


#: Which tier a label's category names. The draw size is not in the stored
#: finish, so a Masters is assumed to be the 96-draw kind, as most now are.
TIERS = [
    ("Grand Slam", "GS"),
    ("Masters 1000", "M1000_128"),
    ("Tour Finals", "FINALS"),
    ("500", "A500_32"),
    ("250", "A250_32"),
    ("International", "INTERNATIONAL"),
]
INTERNATIONAL_PER_WIN = 50.0

RULE = "-" * 78


def parse_label(label: str):
    """``ATP US Open Grand Slam R32`` -> tour, tournament, tier key, round."""
    text = str(label).strip()
    tour = text.split(" ", 1)[0] if text[:3] in ("ATP", "WTA") else ""
    rest = text[len(tour):].strip()

    round_name = ""
    for name in sorted(ROUND_ORDER, key=len, reverse=True):
        if re.search(rf"\b{name}$", rest):
            round_name = name
            rest = rest[: -len(name)].strip()
            break

    tier_key, category = "", ""
    for label_text, key in TIERS:
        if rest.endswith(label_text):
            tier_key, category = key, label_text
            rest = rest[: -len(label_text)].strip()
            break
    return tour, rest, tier_key, category, round_name


def bounds(tier: str, tour: str, round_name: str):
    """The floor and ceiling a finish's points must lie between.

    The label says the furthest round contested, which does not say whether
    that round was won: "R64" is both a player who won it and went through and
    one who lost it. So the range spans both readings -- floor is everything
    before it won with no bonus, ceiling is that round won too and every win in
    straight sets. Only "W" is unambiguous, and it means the lot.

    A bye pays the skipped round's points with the next win, so a run including
    one lands above the floor rather than breaking it. That is why this is a
    range and not an equality.
    """
    table = WIN_POINTS.get(tier)
    if table is None:
        return None, None
    sequence = [r for r in ROUND_ORDER if r in table]
    if round_name == "W":
        least = most = sequence
    elif round_name in sequence:
        index = sequence.index(round_name)
        least, most = sequence[:index], sequence[: index + 1]
    else:
        return None, None
    bonus = BONUS_BEST_OF_FIVE if best_of_five(tour, tier) else BONUS_BEST_OF_THREE
    return sum(table[r] for r in least), sum(table[r] for r in most) * bonus


class Audit:
    def __init__(self, db, as_of, tour, only):
        self.store = open_store(db)
        self.db = db
        self.only = (only or "").lower()
        self.tour = (tour or "").upper()
        self.season, self.as_of = self._latest_day(as_of)
        self.previous = self._previous_day()
        self.version, self.frozen_at = self._frozen_version()
        self.benchmarks = self._benchmarks()
        self.notes: list[tuple[str, str, str]] = []
        self.standings: dict[str, float] = {}
        self.events: dict[str, str] = {}

    def _latest_day(self, as_of):
        rows = self.store.query(
            "SELECT season, MAX(as_of) AS day FROM raw_stats WHERE source = 'tennis'"
            + (" AND as_of = ?" if as_of else "") + " GROUP BY season",
            (as_of,) if as_of else (),
        )
        if rows.empty:
            sys.exit(f"No tennis rows in {self.db}")
        return str(rows.iloc[-1]["season"]), str(rows.iloc[-1]["day"])

    def _previous_day(self):
        rows = self.store.query(
            "SELECT MAX(as_of) AS day FROM raw_stats WHERE source = 'tennis' AND as_of < ?",
            (self.as_of,),
        )
        return None if rows.empty or rows.iloc[0]["day"] is None else str(rows.iloc[0]["day"])

    def _frozen_version(self):
        rows = self.store.query(
            "SELECT version, frozen_at FROM benchmark_versions WHERE season = ? "
            "AND frozen_at IS NOT NULL ORDER BY frozen_at DESC LIMIT 1", (self.season,),
        )
        if rows.empty:
            sys.exit(f"No frozen benchmark for {self.season}")
        return str(rows.iloc[0]["version"]), str(rows.iloc[0]["frozen_at"])

    def _benchmarks(self):
        rows = self.store.query(
            "SELECT norm_key, benchmark, pool_size, seasons FROM benchmarks "
            "WHERE version = ? AND asset_type = 'Player' AND norm_key IN ('ATP', 'WTA')",
            (self.version,),
        )
        return {str(r.norm_key): r for r in rows.itertuples()}

    def opens(self, tour):
        from whul.config.league import season_start
        return season_start(tour)

    def rows(self):
        frame = self.store.query(
            "SELECT rs.asset_id, a.display_name, rs.stats, "
            "  (SELECT COUNT(*) FROM slot_occupancy so WHERE so.asset_id = a.asset_id "
            "    AND so.end_date IS NULL) AS in_a_slot "
            "FROM raw_stats rs JOIN assets a USING (asset_id) "
            "WHERE rs.source = 'tennis' AND rs.as_of = ? ORDER BY a.display_name",
            (self.as_of,),
        )
        for row in frame.itertuples():
            if self.only and self.only not in str(row.display_name).lower():
                continue
            stats = json.loads(row.stats)
            if self.tour and str(stats.get("league", "")).upper() != self.tour:
                continue
            yield str(row.asset_id), str(row.display_name), int(row.in_a_slot), stats

    def yesterday(self, asset_id):
        if not self.previous:
            return None
        rows = self.store.query(
            "SELECT stats FROM raw_stats WHERE asset_id = ? AND source = 'tennis' "
            "AND as_of = ?", (asset_id, self.previous),
        )
        if rows.empty:
            return None
        return json.loads(rows.iloc[0]["stats"])

    def stored_score(self, asset_id):
        rows = self.store.query(
            "SELECT scaled_score FROM daily_scores WHERE asset_id = ? AND season = ? "
            "AND as_of = ?", (asset_id, self.season, self.as_of),
        )
        return None if rows.empty else float(rows.iloc[0]["scaled_score"])

    def slot(self, asset_id):
        rows = self.store.query(
            "SELECT m.display_name AS manager, ss.counts FROM slot_scores ss "
            "JOIN roster_slots rs USING (slot_id) JOIN managers m USING (manager_id) "
            "WHERE ss.asset_id = ? AND ss.as_of = ?", (asset_id, self.as_of),
        )
        if rows.empty:
            return None, False
        return str(rows.iloc[0]["manager"]), bool(int(rows.iloc[0]["counts"]))

    # -- the derivation ------------------------------------------------------

    def player(self, asset_id, name, in_a_slot, stats):
        tour = str(stats.get("league", ""))
        manager, counts = self.slot(asset_id)
        where = (f"{manager} -- "
                 + ("counts toward the total" if counts else "held, does NOT count")
                 ) if manager else "in no slot -- scored, but reaching no standing"
        print(f"\n{'=' * 78}\n{name}   [{tour}]   feed name: {stats.get('player')}"
              f"\n  {where}")

        flags = []
        if not in_a_slot:
            flags.append((
                "scored every night but in nobody's roster -- a leftover asset "
                "from before the roster was reimported", "",
            ))

        finishes = stats.get("finishes") or []
        if not finishes:
            print("\n  no tournaments inside the league year yet")
            return

        print(f"\n  Every tournament since the league year opened {self.opens(tour)}")
        print(f"    {'date':<12}{'tournament':<30}{'round':>6}{'floor':>9}"
              f"{'ceiling':>9}{'points':>9}")

        total = 0.0
        for finish in sorted(finishes, key=lambda f: str(f.get("date")), reverse=True):
            label = str(finish.get("label", ""))
            when = str(finish.get("date", ""))[:10]
            points = float(finish.get("points") or 0.0)
            total += points
            tour_of, event, tier, category, round_name = parse_label(label)
            self.events[f"{tour_of} {event} {category}".strip()] = when

            low, high = bounds(tier, tour_of or tour, round_name)
            shown_low = f"{low:,.0f}" if low is not None else "--"
            shown_high = f"{high:,.0f}" if high is not None else "--"
            print(f"    {when:<12}{event[:29]:<30}{round_name:>6}{shown_low:>9}"
                  f"{shown_high:>9}{points:>9.1f}")
            flags += self.check_finish(label, tier, tour_of or tour, round_name, points)

        print(f"    {'':<12}{'TOTAL':<30}{'':>6}{'':>9}{'':>9}{total:>9.1f}")
        print(f"\n    Points are per round won, as increments. The floor is every"
              f"\n    round won with no bonus; the ceiling is every win in straight sets.")

        stored_total = stats.get("total_points")
        if stored_total is not None and abs(total - float(stored_total)) > 0.01:
            flags.append((
                "the finishes do not sum to the stored total",
                f"{total:,.1f} vs {float(stored_total):,.1f}",
            ))

        flags += self.check_shrinkage(asset_id, total)
        scaled = self.normalize(total, tour)
        if scaled is not None:
            self.verdict(asset_id, name, scaled, counts, flags)

    def check_finish(self, label, tier, tour, round_name, points):
        flags = []
        if not round_name:
            flags.append(("a finish whose round could not be read from its label", label))
            return flags
        if tier == "INTERNATIONAL":
            return flags

        low, high = bounds(tier, tour, round_name)
        if low is None:
            flags.append(("a finish whose tier could not be read from its label", label))
            return flags

        if points > high + 0.01:
            flags.append((
                "more points than winning every round of this event in straight "
                "sets could pay",
                f"{label}: {points:,.1f} above a ceiling of {high:,.1f}",
            ))
        elif points < low - 0.01:
            flags.append((
                "fewer points than reaching this round is worth. Normal where the "
                "run began before the league year opened -- only the matches "
                "inside it are paid -- and wrong otherwise",
                f"{label}: {points:,.1f} below a floor of {low:,.1f}",
            ))
        return flags

    def check_shrinkage(self, asset_id, total):
        """A league-year total can only grow. A drop is a feed losing history."""
        before = self.yesterday(asset_id)
        if before is None:
            return []
        was = float(before.get("total_points") or 0.0)
        if total >= was - 0.01:
            return []
        lost = [f["label"] for f in (before.get("finishes") or [])]
        return [(
            f"the total fell since {self.previous}, and a league-year total can "
            f"only grow. This is a feed that has stopped reaching as far back as "
            f"it did",
            f"{was:,.1f} -> {total:,.1f}; had {len(lost)} finish(es): "
            f"{', '.join(lost)}",
        )]

    def normalize(self, points, tour):
        bench = self.benchmarks.get(tour)
        if bench is None:
            print(f"\n    no {tour} benchmark in {self.version}")
            return None
        value = float(bench.benchmark)
        scaled = round(points / value * 100, 2)
        print(f"\n    benchmark  {tour} = {value:,.2f} points")
        print(f"      the 99th percentile of {bench.pool_size} player-years drawn from")
        print(f"      {bench.seasons}")
        print(f"    scaled     {points:,.1f} / {value:,.2f} x 100 = {scaled:.2f}")
        return scaled

    def verdict(self, asset_id, name, scaled, counts, flags):
        stored = self.stored_score(asset_id)
        if stored is None:
            print(f"\n    CHECK  this script says {scaled:.2f}, nothing is stored")
            self.notes.append((name, "scored here but absent from daily_scores", ""))
        elif abs(scaled - stored) > 0.02:
            print(f"\n    CHECK  this script says {scaled:.2f}, the database says "
                  f"{stored:.2f}   <-- MISMATCH")
            self.notes.append((
                name, "the stored score does not match the current rules",
                f"{stored:.2f} stored, {scaled:.2f} recomputed",
            ))
        else:
            print(f"\n    CHECK  {scaled:.2f} recomputed, {stored:.2f} stored   -- agrees")
        for kind, detail in flags:
            print(f"    NOTE   {kind}" + (f"  [{detail}]" if detail else ""))
            self.notes.append((name, kind, detail))

    # -- framing -------------------------------------------------------------

    def header(self):
        print(RULE)
        print(f"Tennis audit -- {self.db}")
        print(f"  scores as of      {self.as_of}   (season {self.season})")
        if self.previous:
            print(f"  compared against  {self.previous}")
        print(f"  benchmark version {self.version}, frozen {self.frozen_at[:19]}")
        for tour in ("ATP", "WTA"):
            bench = self.benchmarks.get(tour)
            got = f"{float(bench.benchmark):,.2f}" if bench is not None else "MISSING"
            print(f"  {tour} opens {self.opens(tour)}   benchmark {got}")
        print(RULE)

    def tournaments(self):
        print(f"\n{RULE}\nTOURNAMENTS SEEN, OLDEST FIRST\n{RULE}")
        if not self.events:
            print("  none")
            return
        for event, when in sorted(self.events.items(), key=lambda kv: kv[1]):
            print(f"  {when}  {event}")
        earliest = min(self.events.values())
        opens = min(self.opens("ATP"), self.opens("WTA")).isoformat()
        print(f"\n  The league year opened {opens} and the earliest result here is "
              f"{earliest}.")
        if earliest > opens:
            print(f"  Everything before {earliest} is missing from every player's "
                  f"total. Tennis is\n  assembled from three vintages and the middle "
                  f"one closes the gap between an\n  archive ending in February and a "
                  f"feed serving seven days -- lose it and the\n  totals quietly "
                  f"become the last week of the season.")

    def doubles(self):
        """The same person scored twice, with two different answers.

        A roster reimport leaves the old assets behind, and both keep being
        scored. That is untidy but harmless while they agree. It stops being
        harmless when they do not: only one of them is in a slot, and if that
        one is the smaller the manager is quietly short the difference, with
        nothing anywhere reading as an error. Two totals for one person on one
        day is the signal, and it is invisible from any single player's line.
        """
        frame = self.store.query(
            "SELECT a.display_name, a.asset_id, a.league, rs.stats, "
            "  (SELECT COUNT(*) FROM slot_occupancy so WHERE so.asset_id = a.asset_id "
            "    AND so.end_date IS NULL) AS in_a_slot "
            "FROM raw_stats rs JOIN assets a USING (asset_id) "
            "WHERE rs.source = 'tennis' AND rs.as_of = ?", (self.as_of,),
        )
        print(f"\n{RULE}\nTHE SAME PLAYER, SCORED TWICE\n{RULE}")
        if frame.empty:
            print("  no rows today")
            return

        seen: dict[str, list[tuple[str, float, int, int]]] = {}
        for row in frame.itertuples():
            stats = json.loads(row.stats)
            seen.setdefault(str(row.display_name), []).append((
                str(row.asset_id), float(stats.get("total_points") or 0.0),
                len(stats.get("finishes") or []), int(row.in_a_slot),
            ))

        doubled = {n: v for n, v in seen.items() if len(v) > 1}
        if not doubled:
            print("  none -- one row per player")
            return

        disagree = {n: v for n, v in doubled.items()
                    if len({round(t, 2) for _, t, _, _ in v}) > 1}
        print(f"  {len(doubled)} player(s) have more than one asset scoring today. "
              f"{len(disagree)} disagree.\n")
        for name, entries in sorted(doubled.items()):
            if name not in disagree:
                continue
            best = max(t for _, t, _, _ in entries)
            print(f"    {name}")
            for asset_id, total, events, slotted in sorted(entries, key=lambda e: -e[1]):
                held = "IN A SLOT" if slotted else "no slot"
                print(f"      {asset_id:<34}{total:>9.1f}  {events} finish(es)  {held}")
            for asset_id, total, _, slotted in entries:
                if slotted and total < best:
                    short = (best - total) / float(
                        self.benchmarks[self._tour_of(asset_id)].benchmark) * 100
                    print(f"      -> the rostered one is the smaller: short "
                          f"{best - total:,.1f} points, {short:.2f} on the scale")
                    self.notes.append((
                        name,
                        "the rostered asset scores less than another asset for the "
                        "same person on the same day, so the manager is short the "
                        "difference",
                        f"{total:,.1f} credited, {best:,.1f} available",
                    ))
        if not disagree:
            print("  they all agree, so nothing is being lost -- but only one of "
                  "each is in a slot.")

    def _tour_of(self, asset_id: str) -> str:
        rows = self.store.query(
            "SELECT stats FROM raw_stats WHERE asset_id = ? AND source = 'tennis' "
            "AND as_of = ?", (asset_id, self.as_of),
        )
        return str(json.loads(rows.iloc[0]["stats"]).get("league", "ATP"))

    def absent(self):
        frame = self.store.query(
            "SELECT a.display_name, a.league, m.display_name AS manager, "
            "  (SELECT COUNT(*) FROM raw_stats r WHERE r.asset_id = a.asset_id "
            "    AND r.as_of = ?) AS rows_today, "
            "  (SELECT COUNT(*) FROM raw_stats r WHERE r.asset_id = a.asset_id) AS rows_ever "
            "FROM assets a JOIN slot_occupancy so USING (asset_id) "
            "JOIN roster_slots rs USING (slot_id) JOIN managers m USING (manager_id) "
            "WHERE a.league IN ('ATP', 'WTA', 'Tennis') AND so.end_date IS NULL "
            "ORDER BY a.display_name", (self.as_of,),
        )
        print(f"\n{RULE}\nROSTERED BUT ABSENT FROM THE FEED\n{RULE}")
        if frame.empty:
            print("  nothing rostered")
            return
        missing = frame[frame["rows_today"] == 0]
        if missing.empty:
            print(f"  none -- all {len(frame)} rostered player(s) have a row today")
            return
        for row in missing.itertuples():
            seen = ("NEVER seen in this feed" if not int(row.rows_ever)
                    else f"seen in {int(row.rows_ever)} earlier pull(s)")
            print(f"    {str(row.display_name):<24}{str(row.league):<8}"
                  f"{str(row.manager):<10}{seen}")

    def footer(self):
        print(f"\n{RULE}\nWORTH A SECOND LOOK\n{RULE}")
        if not self.notes:
            print("  nothing -- every score recomputed to the stored value")
            return
        grouped: dict[str, list[tuple[str, str]]] = {}
        for who, kind, detail in self.notes:
            grouped.setdefault(kind, []).append((who, detail))
        for kind, whom in grouped.items():
            print(f"\n  {kind}")
            print(f"    {len(whom)} affected:")
            for who, detail in whom:
                print(f"      {who:<22}{detail}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/whul.sqlite3")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--tour", default=None, choices=["ATP", "WTA", "atp", "wta"])
    ap.add_argument("--player", default=None)
    args = ap.parse_args()

    audit = Audit(args.db, args.as_of, args.tour, args.player)
    audit.header()
    for asset_id, name, in_a_slot, stats in audit.rows():
        audit.player(asset_id, name, in_a_slot, stats)
    audit.tournaments()
    audit.doubles()
    audit.absent()
    audit.footer()


if __name__ == "__main__":
    main()
