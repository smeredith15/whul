#!/usr/bin/env python3
"""Show the arithmetic behind every MLB score, so it can be checked by hand.

Deliberately independent of the scoring code. The weights and factors are
re-declared here from MLB_Players_Teams.R rather than imported, and every score
is recomputed from the figures actually stored in ``raw_stats``. Where this
script and the pipeline disagree, one of them is wrong and the mismatch is
printed -- which is the point. Importing ``whul.scoring.mlb`` would make the
two agree by construction and check nothing.

    python scripts/audit-mlb.py                  # the live database
    python scripts/audit-mlb.py --db other.sqlite3
    python scripts/audit-mlb.py --player "Bobby Witt"
    python scripts/audit-mlb.py --as-of 2026-09-04

Read it as four steps per player:

    1. what the feed reported, for the window the league year opened on
    2. every scoring term, stat by stat, adding to a raw total
    3. proration, lifting a short window to a full season's worth
    4. the benchmark, turning points into the 0-100 scale

The check line at the end of each player compares this script's answer to the
one in the database.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whul.store import open_store  # noqa: E402

# --------------------------------------------------------------------------
# The rules, restated. Change these only to match a rules change -- never to
# make this script agree with the pipeline.
# --------------------------------------------------------------------------

BATTER_WEIGHTS = [
    ("ab", -1.0, "at bats"),
    ("h", 5.6, "hits"),
    ("doubles", 2.9, "doubles"),
    ("triples", 5.7, "triples"),
    ("hr", 9.4, "home runs"),
    ("bb", 3.0, "walks"),
    ("hbp", 3.0, "hit by pitch"),
    ("sb", 1.9, "stolen bases"),
    ("cs", -2.8, "caught stealing"),
]
OFFENSE_FACTOR = 0.25
DEFENSE_FACTOR = 1.5

PITCHER_WEIGHTS = [
    ("ip", 7.4, "innings pitched"),
    ("so", 2.0, "strikeouts"),
    ("h", -2.6, "hits allowed"),
    ("bb", -3.0, "walks allowed"),
    ("hbp", -3.0, "hit batters"),
    ("hr", -12.3, "home runs allowed"),
    ("sv", 5.0, "saves"),
    ("hld", 4.0, "holds"),
]
WAR_FACTOR = 0.5 * 10  # WAR * 0.5, then the same x10 rescale the R script uses

SECONDARY_ROLE_WEIGHT = 0.5

# --------------------------------------------------------------------------
# Plausibility. Offense, Defense and WAR are a *share* of a whole season, cut
# down by the games a player played inside the window -- the feed will not
# serve them for a date range. A share that was never applied looks exactly
# like a measured figure: a real number, for a real player, that parses. The
# only thing that gives it away is its rate, so that is what is checked.
#
# These are deliberately loose. They are the rate an all-time season runs at,
# not a good one, so anything tripping them is not a hot streak.
# --------------------------------------------------------------------------

#: ~6 WAR over ~180 innings is a Cy Young season.
ELITE_WAR_PER_INNING = 6.0 / 180
#: ~50 offensive runs over ~150 games is an MVP bat.
ELITE_OFFENSE_PER_GAME = 50.0 / 150
#: How many times the elite rate is worth stopping on.
IMPLAUSIBLE_MULTIPLE = 2.5

#: ``(stored column, label, points each, prorated?)``. The database stores each
#: component already prorated, so the count is recovered by dividing back out --
#: which is what makes the figure checkable against a box score.
TEAM_COMPONENTS = [
    ("pts_reg_wins", "regular season wins", 2.0, True),
    ("pts_big_wins", "wins by 5+ runs", 1.0, True),
    ("pts_shutouts", "shutouts", 2.0, True),
    ("pts_run_diff", "run differential (runs)", 0.05, True),
    ("pts_div_champ", "division title", 5.0, False),
    ("pts_playoff", "playoff wins and series", None, False),
]

#: A division is won by the team with the most regular-season wins in it, and
#: only once the season is over. Any division points at all in a season still
#: being played mean the old league-wide-win-rank rule is still in force.
DIV_CHAMP_ONLY_WHEN_COMPLETE = True

RULE = "-" * 78


def num(value) -> float:
    """A feed's blank, null or NaN is a zero here, as it is in the scoring."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(out) else out


def present(value) -> bool:
    """True where the feed actually reported something."""
    try:
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return value not in (None, "")


class Audit:
    def __init__(self, db: str, as_of: str | None, only: str | None):
        self.store = open_store(db)
        self.db = db
        self.only = (only or "").lower()
        self.season, self.as_of = self._latest_day(as_of)
        self.version, self.frozen_at = self._frozen_version()
        self.benchmarks = self._benchmarks()
        self.notes: list[tuple[str, str, str]] = []

    # -- what we are auditing ------------------------------------------------

    def _latest_day(self, as_of):
        rows = self.store.query(
            "SELECT season, MAX(as_of) AS day FROM raw_stats WHERE league = 'MLB'"
            + (" AND as_of = ?" if as_of else "") + " GROUP BY season",
            (as_of,) if as_of else (),
        )
        if rows.empty:
            sys.exit(f"No MLB rows in {self.db}"
                     + (f" for {as_of}" if as_of else ""))
        return str(rows.iloc[-1]["season"]), str(rows.iloc[-1]["day"])

    def _frozen_version(self):
        rows = self.store.query(
            "SELECT version, frozen_at FROM benchmark_versions "
            "WHERE season = ? AND frozen_at IS NOT NULL "
            "ORDER BY frozen_at DESC LIMIT 1",
            (self.season,),
        )
        if rows.empty:
            sys.exit(f"No frozen benchmark for {self.season}")
        return str(rows.iloc[0]["version"]), str(rows.iloc[0]["frozen_at"])

    def _benchmarks(self):
        rows = self.store.query(
            "SELECT asset_type, norm_key, benchmark, pool_size, seasons "
            "FROM benchmarks WHERE version = ?",
            (self.version,),
        )
        return {
            (str(r.asset_type), str(r.norm_key)): r for r in rows.itertuples()
        }

    def rows(self, source: str):
        frame = self.store.query(
            "SELECT rs.asset_id, a.display_name, a.asset_type, rs.stats "
            "FROM raw_stats rs JOIN assets a USING (asset_id) "
            "WHERE rs.league = 'MLB' AND rs.source = ? AND rs.as_of = ? "
            "ORDER BY a.display_name",
            (source, self.as_of),
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

    def roster(self, asset_id: str) -> str:
        rows = self.store.query(
            "SELECT m.display_name AS manager, rs.category, ss.counts "
            "FROM slot_scores ss "
            "JOIN roster_slots rs USING (slot_id) "
            "JOIN managers m USING (manager_id) "
            "WHERE ss.asset_id = ? AND ss.as_of = ?",
            (asset_id, self.as_of),
        )
        if rows.empty:
            return "not in any slot on this day"
        r = rows.iloc[0]
        counts = "counts toward the total" if int(r["counts"]) else "held, does NOT count"
        return f"{r['manager']} -- {r['category']} -- {counts}"

    # -- the derivations -----------------------------------------------------

    def player(self, name: str, stats: dict) -> tuple[float, float, list[str]]:
        """Raw points and prorated points for one player-role, printing the work."""
        role = str(stats.get("role", ""))
        weights = BATTER_WEIGHTS if role == "Batter" else PITCHER_WEIGHTS
        flags = []

        print(f"\n  {role} line, for play since the league year opened")
        print(f"    {'stat':<22}{'value':>10}{'x weight':>12}{'= points':>12}")
        subtotal = 0.0
        for key, weight, label in weights:
            value = num(stats.get(key))
            points = value * weight + 0.0  # keeps a zero from printing as -0.00
            subtotal += points
            shown = "--" if not present(stats.get(key)) else f"{value:,.2f}".rstrip("0").rstrip(".")
            print(f"    {label:<22}{shown:>10}{weight:>12.2f}{points:>12.2f}")
        print(f"    {'counting subtotal':<22}{'':>10}{'':>12}{subtotal:>12.2f}")

        advanced = 0.0
        if role == "Batter":
            for key, factor, label in (("offense", OFFENSE_FACTOR, "Offense runs"),
                                       ("defense", DEFENSE_FACTOR, "Defense runs")):
                value = num(stats.get(key))
                points = value * factor
                advanced += points
                print(f"    {label:<22}{value:>10.3f}{factor:>12.2f}{points:>12.2f}")
        else:
            value = num(stats.get("war"))
            points = value * WAR_FACTOR
            advanced += points
            print(f"    {'WAR':<22}{value:>10.3f}{WAR_FACTOR:>12.2f}{points:>12.2f}")

        flags += self.check_advanced(stats, role)
        raw = subtotal + advanced
        print(f"    {'RAW TOTAL':<22}{'':>10}{'':>12}{raw:>12.2f}")

        factor = num(stats.get("proration_factor")) or 1.0
        prorated = raw * factor
        print(f"\n    proration  {raw:,.2f} x {factor:.6f} = {prorated:,.2f}")
        print(f"      (the league year covers a short window; counting production "
              f"is lifted to a full season)")

        stored_points = stats.get("role_points", stats.get("total_points"))
        if stored_points is not None and abs(prorated - float(stored_points)) > 0.01:
            flags.append((
                "recomputed role points disagree with the stored ones",
                f"{prorated:,.2f} vs {float(stored_points):,.2f}",
            ))
        return raw, prorated, flags

    def check_advanced(self, stats: dict, role: str) -> list[str]:
        """Is the run value a share of the window, or a whole season's?

        The feed serves counting stats for a date range and refuses to serve
        the run values for one, so those are apportioned by the games played
        inside the window. When that step does not run the figure arrives whole
        -- four months of it earned before anybody drafted the player -- and
        nothing about the number itself says so. Its rate does.
        """
        flags = []
        share = stats.get("advanced_share")
        games, innings = num(stats.get("games")), num(stats.get("ip"))

        if share is None or not present(share):
            flags.append((
                "no advanced_share recorded, so nothing says the "
                "Offense/Defense/WAR figures were cut down to this window",
                "",
            ))
        else:
            print(f"    {'advanced share':<22}{float(share):>10.4f}"
                  f"{'':>12}{'(of the whole season)':>12}")

        if role == "Pitcher" and innings > 0:
            rate = num(stats.get("war")) / innings
            if rate > ELITE_WAR_PER_INNING * IMPLAUSIBLE_MULTIPLE:
                flags.append((
                    "a whole season's WAR, never cut down to the window",
                    f"{num(stats.get('war')):.2f} WAR in {innings:.0f} IP = "
                    f"{rate:.3f}/IP, {rate / ELITE_WAR_PER_INNING:.0f}x a Cy Young rate",
                ))
        if role == "Batter" and games > 0:
            rate = num(stats.get("offense")) / games
            if rate > ELITE_OFFENSE_PER_GAME * IMPLAUSIBLE_MULTIPLE:
                flags.append((
                    "a whole season's Offense, never cut down to the window",
                    f"{num(stats.get('offense')):.2f} runs in {games:.0f} G = "
                    f"{rate:.2f}/G, {rate / ELITE_OFFENSE_PER_GAME:.0f}x an MVP rate",
                ))
        return flags

    def normalize(self, points: float, asset_type: str, norm_key: str) -> float | None:
        bench = self.benchmarks.get((asset_type, norm_key))
        if bench is None:
            print(f"\n    no benchmark for {asset_type}/{norm_key} in {self.version}")
            return None
        scaled = round(points / float(bench.benchmark) * 100, 2)
        print(f"\n    benchmark  {norm_key} = {float(bench.benchmark):,.2f} points"
              f"   (99th pct of {bench.pool_size} seasons: {bench.seasons})")
        print(f"    scaled     {points:,.2f} / {float(bench.benchmark):,.2f} x 100"
              f" = {scaled:.2f}")
        return scaled

    # -- the two passes ------------------------------------------------------

    def players(self):
        print(f"\n{RULE}\nMLB PLAYERS\n{RULE}")
        by_player: dict[str, list] = {}
        for asset_id, name, stats in self.rows("mlb"):
            by_player.setdefault((asset_id, name), []).append(stats)

        if not by_player:
            print("  no MLB player rows on this day")
            return

        for (asset_id, name), lines in by_player.items():
            print(f"\n{'=' * 78}\n{name}\n  {self.roster(asset_id)}")
            scored = []
            all_flags = []
            for stats in sorted(lines, key=lambda s: str(s.get("role"))):
                raw, prorated, flags = self.player(name, stats)
                role = str(stats.get("role", ""))
                scaled = self.normalize(prorated, "Player", f"MLB_{role}")
                if scaled is not None:
                    scored.append((role, scaled, prorated))
                all_flags += flags

            if not scored:
                continue
            scored.sort(key=lambda s: s[1], reverse=True)
            if len(scored) > 1:
                primary, secondary = scored[0], scored[1]
                total = round(primary[1] + secondary[1] * SECONDARY_ROLE_WEIGHT, 2)
                print(f"\n    two-way: {primary[0]} {primary[1]:.2f} is the higher "
                      f"score and counts in full;")
                print(f"             {secondary[0]} {secondary[1]:.2f} x "
                      f"{SECONDARY_ROLE_WEIGHT} = {secondary[1] * SECONDARY_ROLE_WEIGHT:.2f}")
                print(f"             {primary[1]:.2f} + "
                      f"{secondary[1] * SECONDARY_ROLE_WEIGHT:.2f} = {total:.2f}")
            else:
                total = scored[0][1]

            self.verdict(asset_id, name, scored[0][2], total, all_flags)

    def teams(self):
        print(f"\n{RULE}\nMLB TEAMS\n{RULE}")
        found = False
        for asset_id, name, stats in self.rows("mlb-teams"):
            found = True
            print(f"\n{'=' * 78}\n{name}\n  {self.roster(asset_id)}")
            factor = num(stats.get("proration_factor")) or 1.0
            print(f"\n  Points by component, for play since the league year opened")
            print(f"    {'component':<26}{'count':>8}{'x each':>9}{'= base':>10}"
                  f"{'proration':>12}{'= points':>11}")

            total = 0.0
            flags = []
            for key, label, each, counting in TEAM_COMPONENTS:
                if key not in stats:
                    continue
                points = num(stats.get(key))
                total += points
                # The count the feed saw, recovered from the stored points so
                # it can be checked against a box score.
                if each:
                    base = points / factor if counting else points
                    count = base / each
                    shown_count = f"{count:,.0f}" if abs(count - round(count)) < 0.01 \
                        else f"{count:,.2f}"
                    shown_each = f"{each:.2f}"
                    shown_base = f"{base:,.2f}"
                else:
                    shown_count, shown_each, shown_base = "--", "--", f"{points:,.2f}"
                shown_pro = f"x {factor:.4f}" if counting else "held"
                print(f"    {label:<26}{shown_count:>8}{shown_each:>9}{shown_base:>10}"
                      f"{shown_pro:>12}{points:>11.2f}")

            print(f"    {'TOTAL':<26}{'':>8}{'':>9}{'':>10}{'':>12}{total:>11.2f}")
            print(f"\n    counting production is lifted to a full season; a division")
            print(f"    title and a playoff run happen once and are held as they are")

            reported = num(stats.get("reg_wins"))
            implied = num(stats.get("pts_reg_wins")) / factor / 2.0
            if abs(reported - implied) > 0.01:
                flags.append((
                    "the stored win count disagrees with the stored points",
                    f"reg_wins {reported:,.0f} vs {implied:,.1f} implied",
                ))
            if num(stats.get("pts_div_champ")) > 0:
                flags.append((
                    f"scored as a division winner, though no division has been "
                    f"won on {self.as_of}. A title is an outcome, not a rate: it "
                    f"does not exist until the season it belongs to has finished",
                    "+5.00 points",
                ))

            stored_total = stats.get("total_points")
            if stored_total is not None and abs(total - float(stored_total)) > 0.01:
                flags.append((
                    "the components do not sum to the stored total",
                    f"{total:,.2f} vs {float(stored_total):,.2f}",
                ))
            scaled = self.normalize(total, "Team", "MLB")
            if scaled is not None:
                self.verdict(asset_id, name, total, scaled, flags)

        if not found:
            what = f" matching '{self.only}'" if self.only else ""
            print(f"  no MLB team rows{what} on this day")

    def verdict(self, asset_id, name, points, scaled, flags):
        stored_points, stored_scaled = self.stored_score(asset_id)
        if stored_scaled is None:
            print(f"\n    CHECK  this script says {scaled:.2f}, but nothing is "
                  f"stored in daily_scores for {name}")
            self.notes.append((name, "scored here but absent from daily_scores", ""))
        elif abs(scaled - stored_scaled) > 0.02:
            print(f"\n    CHECK  this script says {scaled:.2f}, the database says "
                  f"{stored_scaled:.2f}   <-- MISMATCH")
            self.notes.append((
                name, "recomputed and stored scores disagree",
                f"{scaled:.2f} vs {stored_scaled:.2f}",
            ))
        else:
            print(f"\n    CHECK  {scaled:.2f} recomputed, {stored_scaled:.2f} stored"
                  f"   -- agrees")
        for kind, detail in flags:
            print(f"    NOTE   {kind}" + (f"  [{detail}]" if detail else ""))
            self.notes.append((name, kind, detail))

    def header(self):
        print(f"{RULE}")
        print(f"MLB audit -- {self.db}")
        print(f"  scores as of      {self.as_of}   (season {self.season})")
        print(f"  benchmark version {self.version}, frozen {self.frozen_at[:19]}")
        print(RULE)

    def footer(self):
        print(f"\n{RULE}\nWORTH A SECOND LOOK\n{RULE}")
        if not self.notes:
            print("  nothing -- every score recomputed to the stored value")
            return
        # Grouped by what is wrong rather than by who: a fault in the pipeline
        # hits everyone it touches, and thirteen copies of one line reads like
        # thirteen problems.
        grouped: dict[str, list[tuple[str, str]]] = {}
        for who, kind, detail in self.notes:
            grouped.setdefault(kind, []).append((who, detail))
        for kind, whom in grouped.items():
            print(f"\n  {kind}")
            print(f"    {len(whom)} affected:")
            for who, detail in whom:
                print(f"      {who:<24}{detail}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/whul.sqlite3")
    ap.add_argument("--as-of", default=None, help="a day in raw_stats; default the latest")
    ap.add_argument("--player", default=None, help="substring of a name, to audit one")
    ap.add_argument("--teams-only", action="store_true")
    ap.add_argument("--players-only", action="store_true")
    args = ap.parse_args()

    audit = Audit(args.db, args.as_of, args.player)
    audit.header()
    if not args.teams_only:
        audit.players()
    if not args.players_only:
        audit.teams()
    audit.footer()


if __name__ == "__main__":
    main()
