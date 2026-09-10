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

from whul.scoring import completion

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
#:
#: Unused for players: ESPN has no roster for the competition in any of the
#: five seasons tried, so no player line can be read from it, and a share that
#: can only ever pay zero is worse than no share at all -- it reads as though
#: every MLS player had a quiet Champions Cup. Qualifying for it is still paid
#: on the team side, where it is read from the participant list rather than
#: from match data. See CONTINENTAL_CUPS in whul.sources.espn.
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
    # MLS is on the mid-season leagues' calendar and not on their draft. Its
    # season opens in February, inside the league year, so a manager drafts an
    # MLS club before a ball is kicked and knows no more about who will reach
    # the playoffs than they do for the NFL. The full share is what that is
    # worth; 7.5% was this rule reading its calendar rather than its draft.
    "MLS": PostseasonRule("MLS", 34),

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


def regular_totals(
    rows: pd.DataFrame, keys: list[str], columns, phase: pd.Series
) -> pd.DataFrame:
    """The counting stats behind a player's regular season, summed.

    ``split_phases`` reduces a season to points and games, which is all the
    bonus arithmetic needs and is why the raw figures were never carried past
    it. On the page that left an NFL profile reading "Total points 22.2, Games
    played 1.0" and nothing else -- no yards, no touchdowns, nothing a manager
    could check against a box score.

    Regular-phase rows only, so the totals line up with ``regular_points``
    beside them. What happened in the postseason is carried separately, by the
    rule that prices it.
    """
    work = rows.copy()
    work["_phase"] = phase.to_numpy()
    wanted = [c for c in columns if c in work.columns]
    if not wanted:
        return work[keys].drop_duplicates()
    return (
        work[work["_phase"] == REGULAR]
        .groupby(keys, as_index=False)[wanted]
        .sum()
    )


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


#: The column carrying the per-competition breakdown, as a list of dicts. A
#: list survives into ``raw_stats`` as JSON and is skipped by the season-totals
#: table, the same way ``finishes`` already is.
DETAIL_COLUMN = "bonus_detail"


def detail_for(
    competition: str, games: float, points: float, rule: PostseasonRule | None,
    season: int | None = None, as_of=None,
) -> dict:
    """One competition's postseason line, for the profile window.

    The share is carried alongside the figures rather than looked up again by
    whatever renders them: a page that had to map a competition back to its
    percentage would be a second copy of ``RULES``, and the two would drift the
    first time a share moved -- which is exactly what happened to the Scoring
    page when they split.
    """
    games, points = float(games or 0.0), float(points or 0.0)
    adds = bonus_for(points, games, rule)
    # Asked of the *rule*, not of the label. "UEFA Champions League" is what a
    # reader is shown and "UCL" is what the calendar is keyed by; looking the
    # first one up finds nothing, and finding nothing means never crediting.
    named = rule.competition if rule else competition
    done = (
        completion.is_complete(named, season, as_of)
        if season is not None else False
    )
    return {
        "competition": competition,
        "games": games,
        "points": points,
        "share": rule.bonus_share if rule else 0.0,
        "scalar": rule.scalar if rule else 0.0,
        "adds": adds,
        # Credited only once the competition is over. Until then the figure is
        # a rate off a small sample: one Champions League goal in one game
        # projects to nearly half a league season, and playing a second match
        # without scoring lowers it. See whul.scoring.completion.
        "credited": done,
        "finishes": str(completion.finishes(named, season) or "")
                    if season is not None else "",
    }


def credited_bonus(detail: list) -> float:
    """The part of a breakdown that counts towards the score today."""
    return float(sum(entry.get("adds", 0.0) for entry in detail or []
                     if entry.get("credited")))


def pending_bonus(detail: list) -> float:
    """The part still waiting on a competition to finish."""
    return float(sum(entry.get("adds", 0.0) for entry in detail or []
                     if not entry.get("credited")))


def apply_bonus(
    agg: pd.DataFrame, rule: PostseasonRule | None, competition: str = "",
    season: int | None = None, as_of=None,
) -> pd.DataFrame:
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
    out["games_played"] = (out["regular_games"] + out["postseason_games"]).astype(int)
    # One competition here -- the league's own playoffs -- where soccer has
    # several. Same shape either way, so the page renders one thing.
    label = competition or (rule.competition if rule else "")
    seasons = (out["season"] if season is None and "season" in out.columns
               else [season] * len(out))
    out[DETAIL_COLUMN] = [
        [detail_for(label, games, points, rule,
                    season=int(row_season) if row_season is not None else None,
                    as_of=as_of)]
        if games else []
        for games, points, row_season in zip(
            out["postseason_games"], out["postseason_points"], seasons)
    ]
    # Held until the competition is over: a rate off one playoff game projects
    # a whole share of a season, and a second game without production lowers
    # it. Scores in these sports otherwise only rise.
    out["postseason_bonus"] = [credited_bonus(d) for d in out[DETAIL_COLUMN]]
    out["postseason_pending"] = [pending_bonus(d) for d in out[DETAIL_COLUMN]]
    out["total_points"] = out["regular_points"] + out["postseason_bonus"]
    return out
