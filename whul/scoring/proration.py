"""Season-length proration.

A genuinely shortened season -- MLS moving to a fall-spring calendar in 2027,
and possibly MLB -- would otherwise punish everyone in it: fewer games means
fewer counting stats, measured against a benchmark drawn from full seasons.
Scaling the shortened figures up to a full season's length removes that.

**Only counting production is prorated.** A player's yards and goals scale; a
team's wins, big wins, shutouts and point differential scale. A championship, a
division title, a playoff run and any other one-off do not -- they were won
once, and doubling a title because the season was half length would be absurd.
So the caller names which columns scale, and everything else passes through
untouched.

The multiplier comes from an admin-entered expected-game count, not from the
data. The pipeline cannot tell a shortened season from a season still in
progress -- both look like a team with fewer games played -- and getting that
wrong silently would double every score in a league.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from whul.store.db import Store, _now

#: A shortened season scales up, never down. A factor below 1 means the
#: expected count was entered below the actual one, which is a data-entry
#: error rather than a league playing more games than expected.
MIN_FACTOR = 1.0
#: A season under half its normal length is not a season worth prorating into
#: comparability -- at that point the sample is too thin and the admin should
#: be deciding what to do, not discovering a 3x multiplier in the standings.
MAX_FACTOR = 2.0

OVERRIDE_SCOPE = "proration"


@dataclass(frozen=True)
class ProrationRule:
    """How much a shortened season's counting production scales up."""

    league: str
    season: str
    actual_games: int
    expected_games: int
    note: str = ""

    @property
    def factor(self) -> float:
        if self.actual_games <= 0:
            raise ValueError(
                f"{self.league} {self.season}: actual_games must be positive, "
                f"got {self.actual_games}"
            )
        return self.expected_games / self.actual_games

    def validate(self) -> list[str]:
        """Problems worth refusing to score on."""
        problems = []
        if self.actual_games <= 0:
            problems.append(f"actual_games is {self.actual_games}")
            return problems
        factor = self.factor
        if factor < MIN_FACTOR:
            problems.append(
                f"factor {factor:.3f} is below 1 -- expected_games "
                f"({self.expected_games}) is under actual_games "
                f"({self.actual_games}), which is a data-entry error rather "
                f"than a shortened season"
            )
        if factor > MAX_FACTOR:
            problems.append(
                f"factor {factor:.3f} exceeds {MAX_FACTOR} -- a season under "
                f"half its normal length is too thin to prorate into "
                f"comparability; decide what to do with it explicitly"
            )
        return problems


#: Rules that are part of the league's design rather than an admin's judgement
#: call, so they live in code where they can be read and tested. An admin rule
#: saved for the same league and season overrides one of these.
#:
#: MLB 2026-27 is the shortened-window case the module was written for. The
#: league year opens on 15 August and closes at the 2027 All-Star Game, which
#: is about 133 games of a 162-game season -- so a player measured against a
#: benchmark drawn from whole seasons would finish around 80 of a possible 100
#: however well he played, and every baseball pick would sit below every other
#: league's for a structural reason nobody could see in the standings.
#:
#: Teams are deliberately absent. Their contract year is bisected at the
#: All-Star break and ``whul.scoring.bisection`` already inflates the second
#: stretch so the two reconcile to one full season; prorating them as well
#: would apply the same correction twice.
BUILT_IN_RULES: tuple[ProrationRule, ...] = (
    ProrationRule(
        league="MLB", season="2026-27", actual_games=133, expected_games=162,
        note="15 Aug-27 Sep 2026 (43 days) plus 25 Mar-13 Jul 2027 (110 days), "
             "at 162 games over 186 season days",
    ),
)


def built_in_rule(league: str, season: str) -> ProrationRule | None:
    """The design's own rule for a league and season, if it has one."""
    return next(
        (r for r in BUILT_IN_RULES if r.league == league and r.season == season),
        None,
    )


def prorate(
    scored: pd.DataFrame,
    rule: ProrationRule,
    columns: list[str],
    total_col: str = "total_points",
    strict: bool = True,
) -> pd.DataFrame:
    """Scale the named counting columns, then rebuild the total from them.

    Columns the frame does not have are ignored: a league's scorer emits the
    components it has, and a caller naming a superset should not fail on the
    ones that do not apply. Naming *no* column that exists is an error, though
    -- that means nothing was prorated and the caller thinks otherwise.
    """
    if scored is None or scored.empty:
        return scored if scored is not None else pd.DataFrame()

    problems = rule.validate()
    if problems and strict:
        raise ValueError(f"{rule.league} {rule.season}: " + "; ".join(problems))

    present = [c for c in columns if c in scored.columns]
    if not present:
        raise ValueError(
            f"none of {columns} is in the frame; nothing would be prorated. "
            f"Available: {sorted(scored.columns)[:20]}"
        )

    out = scored.copy()
    factor = rule.factor
    for column in present:
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0.0) * factor

    # The total is rebuilt rather than scaled, because it also carries the
    # one-off achievements that must not move.
    untouched = _untouched_total(scored, present, total_col)
    if untouched is not None:
        out[total_col] = untouched + out[present].sum(axis=1)

    out["proration_factor"] = factor
    return out


def _untouched_total(
    scored: pd.DataFrame, prorated: list[str], total_col: str
) -> pd.Series | None:
    """The part of the total that does not scale.

    Returns None when the frame has no total to rebuild, which is the case for
    a component frame the caller will total itself.
    """
    if total_col not in scored.columns:
        return None
    total = pd.to_numeric(scored[total_col], errors="coerce").fillna(0.0)
    components = scored[prorated].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    return total - components.sum(axis=1)


# --- admin-entered rules ---------------------------------------------------

def save_rule(store: Store, rule: ProrationRule, set_by: str = "admin") -> None:
    """Record a rule as an admin override, dated and attributable.

    Stored as a row rather than configuration so a mid-season change to the
    expected-game count is visible in the record instead of appearing as an
    unexplained shift in the standings.
    """
    problems = rule.validate()
    if problems:
        raise ValueError(f"{rule.league} {rule.season}: " + "; ".join(problems))
    store.upsert(
        "admin_overrides",
        [{
            "scope": OVERRIDE_SCOPE,
            "key": rule.league,
            "value": f"{rule.actual_games}/{rule.expected_games}",
            "season": rule.season,
            "set_by": set_by,
            "set_at": _now(),
            "note": rule.note,
        }],
        keys=("scope", "key", "season"),
    )


def load_rule(store: Store, league: str, season: str) -> ProrationRule | None:
    """The rule for a league-season, or None when the season is full length."""
    row = store.conn.execute(
        "SELECT value, note FROM admin_overrides "
        "WHERE scope = ? AND key = ? AND season = ?",
        (OVERRIDE_SCOPE, league, season),
    ).fetchone()
    if row is None:
        return None
    actual, _, expected = str(row["value"]).partition("/")
    return ProrationRule(
        league=league, season=season,
        actual_games=int(actual), expected_games=int(expected),
        note=row["note"],
    )


def load_rules(store: Store, season: str) -> dict[str, ProrationRule]:
    """Every league with a proration rule this season."""
    rows = store.query(
        "SELECT key, value, note FROM admin_overrides "
        "WHERE scope = ? AND season = ?",
        (OVERRIDE_SCOPE, season),
    )
    rules = {}
    for row in rows.itertuples():
        actual, _, expected = str(row.value).partition("/")
        rules[row.key] = ProrationRule(
            league=row.key, season=season,
            actual_games=int(actual), expected_games=int(expected), note=row.note,
        )
    return rules


#: Which columns scale, per asset type. Counting production only -- a caller
#: may narrow this but should not widen it without deciding that the added
#: column really is a counting stat.
PLAYER_COLUMNS = [
    "regular_points", "goal_points", "appearance_points",
    "assists", "goals", "total_points",
]
TEAM_COLUMNS = ["reg_wins", "big_wins", "shutouts", "point_diff", "regular_points"]
