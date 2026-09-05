#!/usr/bin/env python3
"""Show the arithmetic behind every F1 and NASCAR score, so it can be checked.

Deliberately independent of the scoring code. The NASCAR scale and both Formula
1 points tables are restated here from NASCAR.R and F1.R rather than imported,
and every score is rebuilt from the finishes stored in ``raw_stats`` -- the same
labels the site shows, so a line here reads straight off a results page. Where
this script and the pipeline disagree, one of them is wrong and the mismatch is
printed. Importing ``whul.scoring.motorsport`` would make the two agree by
construction and check nothing.

    python scripts/audit-motorsports.py
    python scripts/audit-motorsports.py --series F1
    python scripts/audit-motorsports.py --driver Verstappen

One pull produces two series and they are benchmarked separately -- a Formula 1
season is about 450 points and a NASCAR one about 1,000 -- so each is scored
against its own scale and never pooled.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whul.store import open_store  # noqa: E402

# --------------------------------------------------------------------------
# The rules, restated. Change these only to match a rules change -- never to
# make this script agree with the pipeline.
# --------------------------------------------------------------------------

#: NASCAR's 2026 Cup scale, applied to every season so eras compare: a win is
#: 55, second is 35, then down by one a place to 36th, and a single point for
#: anything beyond that.
NASCAR_WIN = 55
NASCAR_SECOND = 35
NASCAR_LAST_SCORING = 36
NASCAR_MINIMUM = 1

#: Formula 1 championship points. A sprint is a separate race with its own,
#: much smaller table -- paying a sprint on the Grand Prix table would be about
#: three times too generous, and the finishing position alone cannot tell them
#: apart.
F1_POINTS = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)
F1_SPRINT_POINTS = (8, 7, 6, 5, 4, 3, 2, 1)
F1_FASTEST_LAP = 1
F1_FASTEST_LAP_MAX_POSITION = 10

SPRINT = re.compile(r"\bsprint\b", re.IGNORECASE)

RULE = "-" * 78


def nascar_points(place: int | None) -> float:
    if place is None:
        return 0.0
    if place == 1:
        return float(NASCAR_WIN)
    if 2 <= place <= NASCAR_LAST_SCORING:
        return float(NASCAR_SECOND - (place - 2))
    return float(NASCAR_MINIMUM)


def f1_points(place: int | None, sprint: bool) -> float:
    if place is None:
        return 0.0
    table = F1_SPRINT_POINTS if sprint else F1_POINTS
    return float(table[place - 1]) if 1 <= place <= len(table) else 0.0


def place_from(label: str) -> int | None:
    match = re.search(r"(\d+)(?:st|nd|rd|th)\s*$", str(label).strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def race_from(label: str) -> str:
    return re.sub(r"\s*\d+(?:st|nd|rd|th)\s*$", "", str(label).strip(), flags=re.IGNORECASE)


class Audit:
    def __init__(self, db: str, as_of: str | None, series: str | None, only: str | None):
        self.store = open_store(db)
        self.db = db
        self.only = (only or "").lower()
        self.series = (series or "").upper()
        self.season, self.as_of = self._latest_day(as_of)
        self.version, self.frozen_at = self._frozen_version()
        self.benchmarks = self._benchmarks()
        self.notes: list[tuple[str, str, str]] = []
        self.races: dict[str, dict[str, str]] = defaultdict(dict)

    def _latest_day(self, as_of):
        rows = self.store.query(
            "SELECT season, MAX(as_of) AS day FROM raw_stats WHERE source = 'motorsports'"
            + (" AND as_of = ?" if as_of else "") + " GROUP BY season",
            (as_of,) if as_of else (),
        )
        if rows.empty:
            sys.exit(f"No motorsport rows in {self.db}")
        return str(rows.iloc[-1]["season"]), str(rows.iloc[-1]["day"])

    def _frozen_version(self):
        rows = self.store.query(
            "SELECT version, frozen_at FROM benchmark_versions WHERE season = ? "
            "AND frozen_at IS NOT NULL ORDER BY frozen_at DESC LIMIT 1",
            (self.season,),
        )
        if rows.empty:
            sys.exit(f"No frozen benchmark for {self.season}")
        return str(rows.iloc[0]["version"]), str(rows.iloc[0]["frozen_at"])

    def _benchmarks(self):
        rows = self.store.query(
            "SELECT norm_key, benchmark, pool_size, seasons FROM benchmarks "
            "WHERE version = ? AND asset_type = 'Player' AND norm_key IN ('F1', 'NASCAR')",
            (self.version,),
        )
        return {str(r.norm_key): r for r in rows.itertuples()}

    def opens(self, series: str):
        from whul.config.league import season_start

        return season_start(series)

    def rows(self):
        frame = self.store.query(
            "SELECT rs.asset_id, a.display_name, a.league AS asset_league, rs.stats "
            "FROM raw_stats rs JOIN assets a USING (asset_id) "
            "WHERE rs.source = 'motorsports' AND rs.as_of = ? ORDER BY a.display_name",
            (self.as_of,),
        )
        for row in frame.itertuples():
            if self.only and self.only not in str(row.display_name).lower():
                continue
            stats = json.loads(row.stats)
            if self.series and str(stats.get("league", "")).upper() != self.series:
                continue
            yield str(row.asset_id), str(row.display_name), str(row.asset_league), stats

    def stored_score(self, asset_id: str):
        rows = self.store.query(
            "SELECT scaled_score FROM daily_scores WHERE asset_id = ? AND season = ? "
            "AND as_of = ?", (asset_id, self.season, self.as_of),
        )
        return None if rows.empty else float(rows.iloc[0]["scaled_score"])

    def slot(self, asset_id: str):
        rows = self.store.query(
            "SELECT m.display_name AS manager, rs.category, ss.counts FROM slot_scores ss "
            "JOIN roster_slots rs USING (slot_id) JOIN managers m USING (manager_id) "
            "WHERE ss.asset_id = ? AND ss.as_of = ?", (asset_id, self.as_of),
        )
        if rows.empty:
            return None, "in no slot on this day -- scored, but reaching no standing"
        r = rows.iloc[0]
        counts = "counts toward the total" if int(r["counts"]) else "held, does NOT count"
        return str(r["manager"]), f"{r['manager']} -- {r['category']} -- {counts}"

    # -- the derivation ------------------------------------------------------

    def driver(self, asset_id, name, asset_league, stats):
        series = str(stats.get("league", ""))
        manager, where = self.slot(asset_id)
        print(f"\n{'=' * 78}\n{name}   [{series}]\n  {where}")

        flags = []
        if asset_league not in (series, ""):
            flags.append((
                "the roster files this driver under a different league than the "
                "feed scores him in, so there are two assets for one person",
                f"roster says {asset_league}, results say {series}",
            ))

        finishes = stats.get("finishes") or []
        if not finishes:
            print("\n  no races inside the league year yet")
            return

        opens = self.opens(series)

        # Group by race and day first. A sprint weekend is two races on one
        # date, and the only thing that tells them apart is the label saying so.
        # Where it does not, this script cannot price either of them -- a 4th in
        # a sprint is 5 points and a 4th in the Grand Prix is 12, and the
        # position alone does not say which. Guessing would be worse than
        # saying so, so the stored figure is used and the gap is reported.
        parsed = []
        for finish in sorted(finishes, key=lambda f: str(f.get("date")), reverse=True):
            label = str(finish.get("label", ""))
            race = race_from(label)
            parsed.append({
                "label": label,
                "when": str(finish.get("date", ""))[:10],
                "place": place_from(label),
                "race": race,
                "sprint": bool(SPRINT.search(race)),
                "base": SPRINT.sub("", race).strip(),
                "stored": float(finish.get("points") or 0.0),
            })

        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for entry in parsed:
            groups[(entry["when"], entry["base"])].append(entry)
        for key, group in groups.items():
            sprints = sum(1 for e in group if e["sprint"])
            told_apart = len(group) == 1 or sprints == len(group) - 1
            for entry in group:
                entry["priceable"] = told_apart

        print(f"\n  Every result since the league year opened {opens}")
        print(f"    {'date':<12}{'race':<38}{'place':>6}{'table':>10}{'= points':>10}")

        total = 0.0
        unpriceable = 0
        for entry in parsed:
            self.races[series].setdefault(entry["race"], entry["when"])
            if not entry["priceable"]:
                points, table = entry["stored"], "as stored"
                unpriceable += 1
            elif series == "NASCAR":
                points, table = nascar_points(entry["place"]), "Cup 2026"
            else:
                points = f1_points(entry["place"], entry["sprint"])
                table = "F1 sprint" if entry["sprint"] else "F1 race"
            total += points
            print(f"    {entry['when']:<12}{entry['race'][:37]:<38}"
                  f"{str(entry['place'] or '--'):>6}{table:>10}{points:>10.2f}")
            flags += self.check_finish(entry["label"], entry["place"], series)

        print(f"    {'':<12}{'TOTAL':<38}{'':>6}{'':>10}{total:>10.2f}")

        for (when, race), group in groups.items():
            if len(group) > 1 and not group[0]["priceable"]:
                flags.append((
                    "two results in one race on one day and the label does not say "
                    "which was the sprint. A 4th in a sprint is 5 points and a 4th "
                    "in the Grand Prix is 12; the position alone cannot tell them "
                    "apart, so these are taken as stored rather than checked",
                    f"{race} ({when}) x{len(group)}",
                ))

        if unpriceable:
            print(f"\n    {unpriceable} result(s) taken as stored rather than "
                  f"recomputed -- see the notes.")

        stored_total = stats.get("total_points")
        if stored_total is not None and abs(total - float(stored_total)) > 0.01:
            flags.append((
                "the results do not sum to the stored total",
                f"{total:,.2f} vs {float(stored_total):,.2f}",
            ))

        scaled = self.normalize(total, series)
        if scaled is not None:
            self.verdict(asset_id, name, scaled, flags)

    def check_finish(self, label, place, series) -> list[tuple[str, str]]:
        flags = []
        if place is None:
            flags.append((
                "a result with no finishing position in its label, so it scores "
                "nothing and nothing says why", label,
            ))
        elif series == "F1" and place > len(F1_POINTS) and "Sprint" not in label:
            pass  # outside the points, which is normal and scores zero
        return flags

    def normalize(self, points: float, series: str):
        bench = self.benchmarks.get(series)
        if bench is None:
            print(f"\n    no {series} benchmark in {self.version}")
            return None
        value = float(bench.benchmark)
        scaled = round(points / value * 100, 2)
        print(f"\n    benchmark  {series} = {value:,.2f} points")
        print(f"      the 99th percentile of {bench.pool_size} driver-years drawn from")
        print(f"      {bench.seasons}")
        print(f"    scaled     {points:,.2f} / {value:,.2f} x 100 = {scaled:.2f}")
        return scaled

    def verdict(self, asset_id, name, scaled, flags):
        stored = self.stored_score(asset_id)
        if stored is None:
            print(f"\n    CHECK  this script says {scaled:.2f}, but nothing is stored")
            self.notes.append((name, "scored here but absent from daily_scores", ""))
        elif abs(scaled - stored) > 0.02:
            print(f"\n    CHECK  this script says {scaled:.2f}, the database says "
                  f"{stored:.2f}   <-- MISMATCH")
            self.notes.append((
                name, "the stored score does not match the current rules",
                f"{stored:.2f} stored, {scaled:.2f} under the rules as they stand",
            ))
        else:
            print(f"\n    CHECK  {scaled:.2f} recomputed, {stored:.2f} stored   -- agrees")
        for kind, detail in flags:
            print(f"    NOTE   {kind}" + (f"  [{detail}]" if detail else ""))
            self.notes.append((name, kind, detail))

    # -- framing -------------------------------------------------------------

    def header(self):
        print(RULE)
        print(f"Motorsports audit -- {self.db}")
        print(f"  scores as of      {self.as_of}   (season {self.season})")
        print(f"  benchmark version {self.version}, frozen {self.frozen_at[:19]}")
        for series in ("F1", "NASCAR"):
            bench = self.benchmarks.get(series)
            got = f"{float(bench.benchmark):,.2f}" if bench is not None else "MISSING"
            print(f"  {series:<7} opens {self.opens(series)}   benchmark {got}")
        print(f"  One pull, two series, two scales -- never pooled.")
        print(RULE)

    def calendar(self):
        print(f"\n{RULE}\nRACES SEEN, OLDEST FIRST\n{RULE}")
        for series in sorted(self.races):
            print(f"\n  {series}")
            for race, when in sorted(self.races[series].items(), key=lambda kv: kv[1]):
                print(f"    {when}  {race}")
        if not self.races:
            print("  none")
        print(f"\n  A race missing from this list is a race nobody was scored for, "
              f"and it is\n  invisible on any single driver's line -- a bad "
              f"finish and an absent one both\n  just lower the total.")

    def orphans(self):
        """Assets nothing points at.

        A driver filed under one league by the roster and another by the feed
        becomes two assets. Only one of them can be in a slot, and the other is
        scored every night and reaches nothing. Harmless until somebody fills a
        slot with the wrong one, at which point it stops being harmless quietly.
        """
        frame = self.store.query(
            "SELECT a.asset_id, a.display_name, a.league, "
            "  (SELECT COUNT(*) FROM slot_occupancy so WHERE so.asset_id = a.asset_id) "
            "    AS ever_in_a_slot, "
            "  (SELECT COUNT(*) FROM daily_scores d WHERE d.asset_id = a.asset_id) "
            "    AS scored_days "
            "FROM assets a WHERE a.league IN ('F1', 'NASCAR', 'Motorsports') "
            "ORDER BY a.display_name, a.league"
        )
        loose = frame[(frame["ever_in_a_slot"] == 0) & (frame["scored_days"] > 0)]
        print(f"\n{RULE}\nASSETS IN NO SLOT\n{RULE}")
        if loose.empty:
            print("  none -- every scored driver is in somebody's roster")
            return
        print(f"  {len(loose)} of {len(frame)} motorsport asset(s) are scored every "
              f"night and\n  reach no standing:\n")
        for row in loose.itertuples():
            print(f"    {str(row.display_name):<24}{str(row.league):<14}"
                  f"{int(row.scored_days)} day(s) scored, never in a slot")
        print(f"\n  A duplicate of a rostered driver, filed under a different league. "
              f"It costs\n  nothing today and costs everything the day a slot is "
              f"filled with the wrong one.")

    def absent(self):
        frame = self.store.query(
            "SELECT a.display_name, a.league, m.display_name AS manager, "
            "  (SELECT COUNT(*) FROM raw_stats r WHERE r.asset_id = a.asset_id "
            "    AND r.as_of = ?) AS rows_today, "
            "  (SELECT COUNT(*) FROM raw_stats r WHERE r.asset_id = a.asset_id) AS rows_ever "
            "FROM assets a JOIN slot_occupancy so USING (asset_id) "
            "JOIN roster_slots rs USING (slot_id) JOIN managers m USING (manager_id) "
            "WHERE a.league IN ('F1', 'NASCAR', 'Motorsports') AND so.end_date IS NULL "
            "ORDER BY a.display_name", (self.as_of,),
        )
        print(f"\n{RULE}\nROSTERED BUT ABSENT FROM THE FEED\n{RULE}")
        if frame.empty:
            print("  nothing rostered")
            return
        missing = frame[frame["rows_today"] == 0]
        if missing.empty:
            print(f"  none -- all {len(frame)} rostered driver(s) have a row today")
            return
        for row in missing.itertuples():
            seen = "NEVER seen in this feed" if not int(row.rows_ever) \
                else f"seen in {int(row.rows_ever)} earlier pull(s)"
            print(f"    {str(row.display_name):<24}{str(row.league):<10}"
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
                print(f"      {who:<26}{detail}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/whul.sqlite3")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--series", default=None, choices=["F1", "NASCAR", "f1", "nascar"])
    ap.add_argument("--driver", default=None, help="substring of a name")
    args = ap.parse_args()

    audit = Audit(args.db, args.as_of, args.series, args.driver)
    audit.header()
    seen = set()
    for asset_id, name, asset_league, stats in audit.rows():
        if asset_id in seen:
            continue
        seen.add(asset_id)
        audit.driver(asset_id, name, asset_league, stats)
    audit.calendar()
    audit.orphans()
    audit.absent()
    audit.footer()


if __name__ == "__main__":
    main()
