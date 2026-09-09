"""Postseason bonus.

Benchmarks (the 99th-percentile scale) are built from **regular-season data only**:
postseason samples are small and reach only a minority of players, so including
them would distort the distribution the scale is drawn from.

Postseason production still counts, but as a bonus rather than as raw counting
stats. A player's postseason *rate* is credited as though they played a number of
extra games equal to a fixed share of the regular season -- the same share in
every competition, so a title run is worth proportionally the same everywhere::

    scalar   = bonus_share * regular_games          # 10% of a season by default
    po_rate  = postseason_points / postseason_games
    bonus    = po_rate * scalar
    total    = regular_points + bonus

So an NFL player who plays one playoff game has those points multiplied by 1.7;
two playoff games, their combined points by 1.7/2; and so on. A player who
performs in the postseason exactly at their regular-season rate earns a bonus
worth 10% of their regular season, in every league.

Some games count as neither phase and are dropped entirely: the NBA Play-In, and
European qualifying rounds. Only the playoffs and European competition proper
carry the bonus.

This applies to **players only**. Team scoring already prices the postseason
explicitly and boundedly (playoff appearance, wins and series bonuses), and those
terms sit in both the benchmark and live scoring consistently.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

#: Share of a regular season a postseason run is worth, where nothing is known
#: about the field at draft time.
DEFAULT_BONUS_SHARE = 0.10

#: For the leagues drafted mid-season. By July a manager can see who is heading
#: for the playoffs, so a postseason run is less of a discovery and is paid
#: accordingly. The league admin set these; they are not derived from anything.
MID_SEASON_BONUS_SHARE = 0.075

#: For European competition, where the field is not merely likely but settled:
#: a club's place in next season's Champions League is known before this one
#: ends, so there is nothing left to find out at the draft.
SETTLED_BONUS_SHARE = 0.05

#: For the CONCACAF Champions Cup, which is both settled *and* a small part of
#: an MLS club's year -- a handful of ties against a field of very uneven
#: strength.
CONTINENTAL_CUP_BONUS_SHARE = 0.025

REGULAR = "regular"
POSTSEASON = "postseason"
EXCLUDED = "excluded"


@dataclass(frozen=True)
class PostseasonRule:
    """How much a postseason appearance is worth in a given competition."""

    competition: str
    regular_games: int
    bonus_share: float = DEFAULT_BONUS_SHARE
    scalar_override: float | None = None

    @property
    def scalar(self) -> float:
        """Extra games' worth of postseason-rate production credited."""
        if self.scalar_override is not None:
            return self.scalar_override
        return self.bonus_share * self.regular_games


#: Byes score as though the team swept the round it skipped -- MLB's top seeds
#: skipping the wild-card round, a conference tournament's top seeds, a European
#: competition's league-phase leaders. Tennis is the documented exception: a bye
#: earns first-round credit only if the player wins their second-round match.
BYE_COUNTS_AS_SWEEP = True

RULES: dict[str, PostseasonRule] = {
    # Drafted before a ball is bowled, so the field is genuinely unknown.
    "NFL": PostseasonRule("NFL", 17),
    "NBA": PostseasonRule("NBA", 82),
    # The NHL regular season expands to 84 games in 2026-27. All sixteen
    # qualifiers play a first round, so no bye credit arises here.
    "NHL": PostseasonRule("NHL", 84),

    # Drafted mid-season. The standings in July already say a great deal about
    # who plays in October, so the run is worth less as a surprise.
    "MLB": PostseasonRule("MLB", 162, MID_SEASON_BONUS_SHARE),
    "WNBA": PostseasonRule("WNBA", 44, MID_SEASON_BONUS_SHARE),
    "NWSL": PostseasonRule("NWSL", 26, MID_SEASON_BONUS_SHARE),
    "MLS": PostseasonRule("MLS", 34, MID_SEASON_BONUS_SHARE),

    # European competition, refereced to a 38-game domestic league. The field
    # is settled before the draft: qualification is decided by the season that
    # has just finished, so nobody is guessing.
    "UCL": PostseasonRule("UCL", 38, SETTLED_BONUS_SHARE),
    "Europa League": PostseasonRule("Europa League", 38, SETTLED_BONUS_SHARE),
    "Europa Conference League": PostseasonRule(
        "Europa Conference League", 38, SETTLED_BONUS_SHARE),

    # Settled the same way, and a smaller part of the year besides.
    "CONCACAF Champions Cup": PostseasonRule(
        "CONCACAF Champions Cup", 34, CONTINENTAL_CUP_BONUS_SHARE),
}

PHASE_COLUMNS = (
    "regular_points",
    "regular_games",
    "postseason_points",
    "postseason_games",
)


def split_phases(
    rows: pd.DataFrame,
    keys: list[str],
    points_col: str,
    games_col: str,
    phase: pd.Series,
) -> pd.DataFrame:
    """Aggregate per-game rows into regular and postseason totals per asset.

    ``phase`` labels each row ``REGULAR``, ``POSTSEASON`` or ``EXCLUDED``.
    Excluded rows (NBA Play-In, European qualifying) are dropped outright -- they
    contribute to neither phase, so they neither pad the regular season nor earn
    a bonus.
    """
    work = rows.copy()
    work["_phase"] = phase.to_numpy()
    work = work[work["_phase"] != EXCLUDED]

    grouped = (
        work.groupby(keys + ["_phase"], as_index=False)
        .agg(points=(points_col, "sum"), games=(games_col, "sum"))
    )
    reg = grouped[grouped["_phase"] == REGULAR].drop(columns="_phase").rename(
        columns={"points": "regular_points", "games": "regular_games"}
    )
    post = grouped[grouped["_phase"] == POSTSEASON].drop(columns="_phase").rename(
        columns={"points": "postseason_points", "games": "postseason_games"}
    )
    out = reg.merge(post, on=keys, how="outer")
    for col in PHASE_COLUMNS:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = out[col].fillna(0.0)
    return out


#: Which rule pays for a club soccer competition that is *not* part of the
#: benchmark. A tier absent here is ordinary football: it counts in the season
#: total and in the pool the benchmark is drawn from.
#:
#: The domestic postseason is deliberately keyed by league rather than by tier,
#: because MLS and the NWSL play one and the European leagues do not -- and a
#: rule that guessed would quietly pay a Premier League club for a "playoff"
#: that was a promotion play-off in a competition nobody drafted.
BONUS_TIERS: dict[str, str] = {
    "champions_league": "UCL",
    "europa": "Europa League",
    "conference": "Europa Conference League",
    "continental_cup": "CONCACAF Champions Cup",
}

POSTSEASON_LEAGUES: dict[str, str] = {"MLS": "MLS", "NWSL": "NWSL"}


def rule_for(tier: str, league: str = "") -> PostseasonRule | None:
    """The bonus rule for a competition, or ``None`` if it counts in full.

    ``tier`` is ``whul.scoring.competition.Tier``'s value, so the same
    classifier that prices a club's win decides whether a player's appearance
    in it belongs in the benchmark. One reading of "what competition is this",
    not two.
    """
    if tier == "domestic_postseason":
        named = POSTSEASON_LEAGUES.get(str(league))
        return RULES.get(named) if named else None
    return RULES.get(BONUS_TIERS.get(str(tier), ""))


def bonus_for(points: float, games: float, rule: PostseasonRule | None) -> float:
    """What a run at this rate adds, for one competition.

    Rate, not tally: the points are divided by the games that produced them and
    credited as though the player had played ``rule.scalar`` more of them. A
    competition nobody appeared in adds nothing rather than dividing by zero.
    """
    if rule is None or not games:
        return 0.0
    return float(points) / float(games) * rule.scalar


def apply_bonus(agg: pd.DataFrame, rule: PostseasonRule | None) -> pd.DataFrame:
    """Add ``postseason_bonus`` and ``total_points`` to a phase-split frame.

    With no rule, or where a player made no postseason appearance, the bonus is
    zero and the total is simply their regular-season production.
    """
    out = agg.copy()
    for col in PHASE_COLUMNS:
        if col not in out.columns:
            raise KeyError(f"expected phase column {col!r}; have {sorted(out.columns)}")

    appeared = out["postseason_games"] > 0
    po_rate = out["postseason_points"].divide(out["postseason_games"]).where(appeared, 0.0)

    scalar = rule.scalar if rule else 0.0
    out["postseason_rate"] = po_rate.round(4)
    out["postseason_bonus"] = po_rate * scalar
    out["games_played"] = (out["regular_games"] + out["postseason_games"]).astype(int)
    out["total_points"] = out["regular_points"] + out["postseason_bonus"]
    return out
