"""Postseason bonus.

Benchmarks (the 99th-percentile scale) are built from **regular-season data only**:
postseason samples are small and reach only a minority of players, so including
them would distort the distribution the scale is drawn from.

Postseason production still counts, but not as raw counting stats -- a player who
appears in the postseason is credited as though they played a fixed number of
*extra games at their own rate*::

    per_game_rate = (regular_points + postseason_points) / games actually played
    bonus         = per_game_rate * scalar
    total         = regular_points + bonus

The scalar is ``regular_games / max_postseason_games`` for the competition. Note
that the bonus is earned by *appearing* -- a player who plays one postseason game
earns the same bonus as one who plays a full run -- but its size scales with how
well they played across the whole season.

The resulting bonus, expressed as a share of a full regular season, varies by
competition (NFL 25.0%, MLB 4.5%, NBA 3.6%, NHL 3.6%, UCL 5.9%). ``scalar`` is
overridable per league so this can be tuned from the admin dashboard without
touching the formula.

This applies to **players only**. Team scoring already prices the postseason
explicitly and boundedly (playoff appearance, wins and series bonuses), and those
terms sit in both the benchmark and live scoring consistently.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class PostseasonRule:
    """How much a postseason appearance is worth in a given competition."""

    competition: str
    regular_games: int
    max_postseason_games: int
    scalar_override: float | None = None

    @property
    def scalar(self) -> float:
        """Extra games' worth of production credited for an appearance."""
        if self.scalar_override is not None:
            return self.scalar_override
        return self.regular_games / self.max_postseason_games

    @property
    def bonus_share(self) -> float:
        """The bonus as a fraction of a full regular season."""
        return self.scalar / self.regular_games


RULES: dict[str, PostseasonRule] = {
    "NFL": PostseasonRule("NFL", 17, 4),
    "MLB": PostseasonRule("MLB", 162, 22),
    "NBA": PostseasonRule("NBA", 82, 28),
    # The NHL regular season expands to 84 games in 2026-27.
    "NHL": PostseasonRule("NHL", 84, 28),
    # Club soccer has no playoffs; European competition plays the same role.
    # Referenced to a 38-game domestic league.
    "UCL": PostseasonRule("UCL", 38, 17),
    "Europa League": PostseasonRule("Europa League", 38, 17),
    "Europa Conference League": PostseasonRule("Europa Conference League", 38, 21),
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
    is_postseason: pd.Series,
) -> pd.DataFrame:
    """Aggregate per-game rows into regular and postseason totals per asset."""
    work = rows.copy()
    work["_phase"] = is_postseason.to_numpy()
    grouped = (
        work.groupby(keys + ["_phase"], as_index=False)
        .agg(points=(points_col, "sum"), games=(games_col, "sum"))
    )
    reg = grouped[~grouped["_phase"]].drop(columns="_phase").rename(
        columns={"points": "regular_points", "games": "regular_games"}
    )
    post = grouped[grouped["_phase"]].drop(columns="_phase").rename(
        columns={"points": "postseason_points", "games": "postseason_games"}
    )
    out = reg.merge(post, on=keys, how="outer")
    for col in PHASE_COLUMNS:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = out[col].fillna(0.0)
    return out


def apply_bonus(agg: pd.DataFrame, rule: PostseasonRule | None) -> pd.DataFrame:
    """Add ``postseason_bonus`` and ``total_points`` to a phase-split frame.

    With no rule, or where a player made no postseason appearance, the bonus is
    zero and the total is simply their regular-season production.
    """
    out = agg.copy()
    for col in PHASE_COLUMNS:
        if col not in out.columns:
            raise KeyError(f"expected phase column {col!r}; have {sorted(out.columns)}")

    games = out["regular_games"] + out["postseason_games"]
    all_points = out["regular_points"] + out["postseason_points"]
    rate = all_points.divide(games).where(games > 0, 0.0)

    scalar = rule.scalar if rule else 0.0
    appeared = out["postseason_games"] > 0
    out["per_game_rate"] = rate.round(4)
    out["postseason_bonus"] = (rate * scalar).where(appeared, 0.0)
    out["games_played"] = games.astype(int)
    out["total_points"] = out["regular_points"] + out["postseason_bonus"]
    return out
