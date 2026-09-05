#!/usr/bin/env python3
"""Show the arithmetic behind every European club score, so it can be checked.

Deliberately independent of the scoring code: the tier values and the two win
bonuses are restated here from Club_Soccer.R rather than imported, and each
club's total is rebuilt from the figures stored in ``raw_stats``.

    python scripts/audit-soccer-clubs.py
    python scripts/audit-soccer-clubs.py --rules
    python scripts/audit-soccer-clubs.py --league "Premier League"
    python scripts/audit-soccer-clubs.py --team Arsenal

A club scores only for winning, and a win is worth what the competition it
happened in is worth. That is the whole difficulty: two wins can be nine points
or fourteen, and the total alone does not say which. Where the stored row
carries the breakdown this script checks it exactly. Where it does not -- rows
written before the scorer emitted one -- it checks the range instead, and says
that is what it did.
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

#: What a win is worth, by where it happened.
TIER_POINTS = {
    "champions_league": 5,
    "domestic_postseason": 5,
    "europa": 4,
    "conference": 4,
    "domestic_cup": 4,
    "league": 3,
    "qualifying": 0,
}
TIER_NAMES = {
    "champions_league": "Champions League",
    "domestic_postseason": "domestic postseason (MLS/NWSL play-offs)",
    "europa": "Europa League",
    "conference": "Conference League",
    "domestic_cup": "domestic cup",
    "league": "the domestic league",
    "qualifying": "qualifying -- does not count at all",
}

BIG_MARGIN = 2
PTS_BIG_MARGIN = 1
PTS_CLEAN_SHEET = 1

#: The cheapest and dearest a win can be, used to bound a total when the stored
#: row carries no breakdown.
MIN_PER_WIN = TIER_POINTS["league"]
MAX_PER_WIN = max(TIER_POINTS.values()) + PTS_BIG_MARGIN + PTS_CLEAN_SHEET

EUROPEAN = ("Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1")

RULE = "-" * 78


def show_rules():
    print(RULE)
    print("EUROPEAN CLUB SCORING, AS THIS SCRIPT UNDERSTANDS IT")
    print(RULE)
    print("""
  A club scores only for winning. A draw and a loss are both worth nothing, so
  the league table a club is actually in has no bearing on its score.

  A win is worth what the competition it happened in is worth:
""")
    for tier, points in sorted(TIER_POINTS.items(), key=lambda kv: -kv[1]):
        print(f"    {points:>2}   {TIER_NAMES[tier]}")
    print(f"""
  Plus, on a win only:

    +{PTS_BIG_MARGIN}   winning by {BIG_MARGIN} goals or more
    +{PTS_CLEAN_SHEET}   conceding nothing

  So a win is worth between {MIN_PER_WIN} and {MAX_PER_WIN} points, and two wins can be anything
  from {MIN_PER_WIN * 2} to {MAX_PER_WIN * 2}. This is why a total on its own cannot be checked.

  READING EVERY COMPETITION IS WHAT MAKES THE TIERS MEAN ANYTHING
    Restricted to league fixtures every win would be worth three, and the
    Champions League premium would never appear. So a club's European and
    domestic-cup matches are gathered too -- from separate requests, because
    ESPN keys each competition on its own.

  THE SILENT ONE
    A competition the classifier cannot place falls through to the domestic
    league. That is the right default -- it is overwhelmingly the common case --
    but it means a Champions League tie named unexpectedly pays 3 instead of 5,
    with nothing raised. The per-tier breakdown below is what makes that
    visible: a club that played in Europe and shows no European wins is worth
    a second look.

  QUALIFYING
    Dropped outright, neither scored nor allowed to pad a match count. A
    qualifying tie carries its competition's name, so "Champions League
    Qualifying" is tested for first and removed before anything else.

  BYES
    A round skipped by finishing high enough is credited as though the team
    swept it, rather than left looking like an early exit.
""")


class Audit:
    def __init__(self, db, as_of, league, team):
        self.store = open_store(db)
        self.db = db
        self.only_league = (league or "").lower()
        self.only_team = (team or "").lower()
        self.season, self.as_of = self._latest_day(as_of)
        self.version, self.frozen_at = self._frozen_version()
        self.benchmarks = self._benchmarks()
        self.notes: list[tuple[str, str, str]] = []

    def _latest_day(self, as_of):
        rows = self.store.query(
            "SELECT season, MAX(as_of) AS day FROM raw_stats "
            "WHERE league IN ({}) {} GROUP BY season".format(
                ",".join("?" for _ in EUROPEAN),
                "AND as_of = ?" if as_of else "",
            ),
            (*EUROPEAN, as_of) if as_of else EUROPEAN,
        )
        if rows.empty:
            sys.exit(f"No European club rows in {self.db}")
        return str(rows.iloc[-1]["season"]), str(rows.iloc[-1]["day"])

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
            "WHERE version = ? AND asset_type = 'Team'", (self.version,),
        )
        return {str(r.norm_key): r for r in rows.itertuples()}

    def rows(self):
        frame = self.store.query(
            "SELECT rs.asset_id, a.display_name, rs.league, rs.stats "
            "FROM raw_stats rs JOIN assets a USING (asset_id) "
            "WHERE rs.as_of = ? AND rs.league IN ({}) "
            "ORDER BY rs.league, a.display_name".format(
                ",".join("?" for _ in EUROPEAN)),
            (self.as_of, *EUROPEAN),
        )
        for row in frame.itertuples():
            if self.only_league and self.only_league not in str(row.league).lower():
                continue
            if self.only_team and self.only_team not in str(row.display_name).lower():
                continue
            yield (str(row.asset_id), str(row.display_name), str(row.league),
                   json.loads(row.stats))

    def stored_score(self, asset_id):
        rows = self.store.query(
            "SELECT scaled_score FROM daily_scores WHERE asset_id = ? AND season = ? "
            "AND as_of = ?", (asset_id, self.season, self.as_of),
        )
        return None if rows.empty else float(rows.iloc[0]["scaled_score"])

    def slot(self, asset_id):
        rows = self.store.query(
            "SELECT m.display_name AS manager, rs.category, ss.counts FROM slot_scores ss "
            "JOIN roster_slots rs USING (slot_id) JOIN managers m USING (manager_id) "
            "WHERE ss.asset_id = ? AND ss.as_of = ?", (asset_id, self.as_of),
        )
        if rows.empty:
            return "in no slot on this day"
        r = rows.iloc[0]
        counts = "counts toward the total" if int(r["counts"]) else "held, does NOT count"
        return f"{r['manager']} -- {r['category']} -- {counts}"

    # -- the derivation ------------------------------------------------------

    def club(self, asset_id, name, league, stats):
        print(f"\n{'=' * 78}\n{name}   [{league}]\n  {self.slot(asset_id)}")
        flags = []

        played = float(stats.get("matches_played") or 0)
        wins = float(stats.get("wins") or 0)
        stored_total = float(stats.get("total_points") or 0)
        byes = float(stats.get("bye_points") or 0)

        tiers = {t: float(stats.get(f"wins_{t}") or 0) for t in TIER_POINTS
                 if f"wins_{t}" in stats}
        detailed = bool(tiers) and "pts_wins" in stats

        print(f"\n  {played:.0f} match(es) that count, {wins:.0f} won. "
              f"A draw and a loss are worth nothing.")

        if detailed:
            print(f"\n    {'where the win happened':<34}{'wins':>6}{'x each':>9}"
                  f"{'= points':>11}")
            total = 0.0
            for tier, points in sorted(TIER_POINTS.items(), key=lambda kv: -kv[1]):
                won = tiers.get(tier, 0.0)
                if not won:
                    continue
                earned = won * points
                total += earned
                print(f"    {TIER_NAMES[tier][:33]:<34}{won:>6.0f}{points:>9}"
                      f"{earned:>11.2f}")
            big = float(stats.get("big_margins") or 0)
            clean = float(stats.get("clean_sheets") or 0)
            total += big * PTS_BIG_MARGIN + clean * PTS_CLEAN_SHEET
            print(f"    {'won by ' + str(BIG_MARGIN) + '+ goals':<34}{big:>6.0f}"
                  f"{PTS_BIG_MARGIN:>9}{big * PTS_BIG_MARGIN:>11.2f}")
            print(f"    {'conceded nothing':<34}{clean:>6.0f}"
                  f"{PTS_CLEAN_SHEET:>9}{clean * PTS_CLEAN_SHEET:>11.2f}")
            if byes:
                total += byes
                print(f"    {'byes, credited as a sweep':<34}{'':>6}{'':>9}{byes:>11.2f}")
            print(f"    {'TOTAL':<34}{'':>6}{'':>9}{total:>11.2f}")

            if abs(total - stored_total) > 0.01:
                flags.append((
                    "the components do not sum to the stored total",
                    f"{total:,.2f} vs {stored_total:,.2f}",
                ))
            european = sum(tiers.get(t, 0) for t in
                           ("champions_league", "europa", "conference"))
            if league in EUROPEAN and not european and played > wins:
                flags.append((
                    "no European win recorded. Normal before the group stages "
                    "start; worth a look once they have, because a competition "
                    "the classifier cannot place falls through to the league and "
                    "pays 3 rather than 5",
                    f"{wins:.0f} win(s), all domestic",
                ))
        else:
            low, high = wins * MIN_PER_WIN, wins * MAX_PER_WIN
            total = stored_total
            print(f"\n    This row carries no breakdown, so the total cannot be")
            print(f"    rebuilt -- only bounded. {wins:.0f} win(s) is worth between")
            print(f"    {low:,.0f} and {high:,.0f} points.")
            print(f"    {'stored total':<34}{'':>6}{'':>9}{stored_total:>11.2f}")
            # A league win is worth at most 5 with both bonuses, so a total above
            # that many is proof that some win happened somewhere dearer -- a cup
            # tie or a European one. Not an error; it is the one thing the total
            # alone can still be made to say.
            league_only_max = wins * (TIER_POINTS["league"] + PTS_BIG_MARGIN
                                      + PTS_CLEAN_SHEET)
            if wins and stored_total > league_only_max + 0.01:
                print(f"    {'':<34}{'':>6}{'':>9}")
                print(f"    Above the {league_only_max:,.0f} that {wins:.0f} league "
                      f"win(s) could pay, so at least")
                print(f"    one win came in a cup or in Europe.")

            if not (low - 0.01 <= stored_total <= high + 0.01):
                flags.append((
                    "the total is outside what this many wins can be worth",
                    f"{stored_total:,.2f} against a range of {low:,.0f}-{high:,.0f}",
                ))
            else:
                flags.append((
                    "no per-competition breakdown stored, so the total was bounded "
                    "rather than checked. Rerunning the pipeline records one",
                    f"{wins:.0f} win(s) -> {low:,.0f}-{high:,.0f}, stored "
                    f"{stored_total:,.2f}"
                    + (" (some outside the league)"
                       if wins and stored_total > league_only_max + 0.01 else ""),
                ))

        scaled = self.normalize(total, league)
        if scaled is not None:
            self.verdict(asset_id, name, scaled, flags)

    def normalize(self, points, league):
        bench = self.benchmarks.get(league)
        if bench is None:
            print(f"\n    no {league} benchmark in {self.version}")
            return None
        value = float(bench.benchmark)
        scaled = round(points / value * 100, 2)
        print(f"\n    benchmark  {league} = {value:,.2f} points")
        print(f"      the 99th percentile of {bench.pool_size} club-seasons drawn from")
        print(f"      {bench.seasons}")
        print(f"    scaled     {points:,.2f} / {value:,.2f} x 100 = {scaled:.2f}")
        return scaled

    def verdict(self, asset_id, name, scaled, flags):
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

    def header(self):
        print(RULE)
        print(f"European club audit -- {self.db}")
        print(f"  scores as of      {self.as_of}   (season {self.season})")
        print(f"  benchmark version {self.version}, frozen {self.frozen_at[:19]}")
        from whul.config.league import season_start
        for league in EUROPEAN:
            bench = self.benchmarks.get(league)
            got = f"{float(bench.benchmark):,.2f}" if bench is not None else "MISSING"
            print(f"  {league:<16} opens {season_start(league)}   benchmark {got}")
        print(RULE)

    def absent(self):
        """Rostered clubs the feed did not mention -- and the player slots.

        The same league name covers both, so the player picks fall out of this
        query too. They are separated rather than mixed: a club with no row is a
        possible fault, while a player with no row is the known one -- there is
        no club-soccer player source at all, FBref having refused the datacenter
        address it was built against. Listing them together would bury the first
        in the second.
        """
        marks = ",".join("?" for _ in EUROPEAN)
        frame = self.store.query(
            f"SELECT a.display_name, a.league, a.asset_type, m.display_name AS manager, "
            f"  (SELECT COUNT(*) FROM raw_stats r WHERE r.asset_id = a.asset_id "
            f"    AND r.as_of = ?) AS rows_today, "
            f"  (SELECT COUNT(*) FROM raw_stats r WHERE r.asset_id = a.asset_id) AS rows_ever "
            f"FROM assets a JOIN slot_occupancy so USING (asset_id) "
            f"JOIN roster_slots rs USING (slot_id) JOIN managers m USING (manager_id) "
            f"WHERE a.league IN ({marks}) AND so.end_date IS NULL "
            f"ORDER BY a.league, a.display_name", (self.as_of, *EUROPEAN),
        )
        print(f"\n{RULE}\nROSTERED BUT ABSENT FROM THE FEED\n{RULE}")
        if frame.empty:
            print("  nothing rostered")
            return

        clubs = frame[frame["asset_type"] == "Team"]
        missing = clubs[clubs["rows_today"] == 0]
        if missing.empty:
            print(f"  Clubs: none -- all {len(clubs)} rostered club(s) have a row "
                  f"today.")
        else:
            print(f"  Clubs: {len(missing)} of {len(clubs)} have no row on "
                  f"{self.as_of}.\n")
            for row in missing.itertuples():
                seen = ("NEVER seen in this feed" if not int(row.rows_ever)
                        else f"seen in {int(row.rows_ever)} earlier pull(s)")
                print(f"    {str(row.display_name):<26}{str(row.league):<17}"
                      f"{str(row.manager):<10}{seen}")

        players = frame[frame["asset_type"] == "Player"]
        if players.empty:
            return
        never = players[players["rows_ever"] == 0]
        print(f"\n  Players: {len(never)} of {len(players)} rostered club-soccer "
              f"player(s) have never\n  been scored, because there is no source "
              f"for them at all -- FBref refuses the\n  datacenter address the "
              f"adapter was built against. Every one is on 0.00.\n")
        by_manager: dict[str, int] = {}
        by_league: dict[str, int] = {}
        for row in never.itertuples():
            by_manager[str(row.manager)] = by_manager.get(str(row.manager), 0) + 1
            by_league[str(row.league)] = by_league.get(str(row.league), 0) + 1
        print(f"    {'per manager':<18}" + "  ".join(
            f"{m} {n}" for m, n in sorted(by_manager.items())))
        print(f"    {'per league':<18}" + "  ".join(
            f"{lg} {n}" for lg, n in sorted(by_league.items())))

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
    ap.add_argument("--league", default=None)
    ap.add_argument("--team", default=None)
    ap.add_argument("--rules", action="store_true")
    args = ap.parse_args()

    if args.rules:
        show_rules()
        return

    audit = Audit(args.db, args.as_of, args.league, args.team)
    audit.header()
    for asset_id, name, league, stats in audit.rows():
        audit.club(asset_id, name, league, stats)
    audit.absent()
    audit.footer()


if __name__ == "__main__":
    main()
