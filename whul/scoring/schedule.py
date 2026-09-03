"""Schedule-length changes.

When a league changes how many games it plays, historical benchmarks stop
describing the same thing. The NHL expands from 82 to 84 games in 2026-27, so a
benchmark drawn from 82-game seasons sets a bar every skater would clear about
2.4% too easily.

The correction applies to the **benchmark**, not to live scores: scaling one
number per normalization group leaves a manager checking the arithmetic with one
fewer step to follow than rescaling every historical season and re-deriving.

It applies only to **counting stats**. Goals, assists, shots, wins and goal
differential all scale with games played; a division title or a playoff berth
does not. Player scoring in these leagues is entirely counting stats, so a
player benchmark scales whole. Team scoring mixes the two, so a team's
regular-season components are scaled at source and its achievement components
left alone -- which is why ``score_teams`` takes the factor rather than having it
applied afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduleChange:
    """A league's game count moving between seasons."""

    league: str
    historical_games: int
    current_games: int
    effective_season: str

    @property
    def factor(self) -> float:
        """Multiplier taking a historical pace to the current one."""
        return self.current_games / self.historical_games


#: Live schedule changes. The NFL's expansion to 18 games is expected but not
#: scheduled, so it is absent until a date is known.
SCHEDULE_CHANGES: dict[str, ScheduleChange] = {
    "NHL": ScheduleChange("NHL", historical_games=82, current_games=84,
                          effective_season="2026-27"),
}


@dataclass(frozen=True)
class IrregularSeason:
    """A season whose length makes it unusable as benchmark evidence."""

    league: str
    season: int
    games: int
    standard_games: int
    reason: str


#: Seasons excluded from benchmark pools. Scaling a 56-game season up to 84 is a
#: 1.5x extrapolation across a year that also had no crowds, condensed travel and
#: division-only schedules -- the distortion is not just one of length, so the
#: honest move is to drop it rather than model it. `MLB_Players_Teams.R` set the
#: precedent by filtering 2020 "to eradicate COVID season distortion".
IRREGULAR_SEASONS: tuple[IrregularSeason, ...] = (
    IrregularSeason("NHL", 2021, 56, 82, "COVID: 56 games, division-only schedule"),
    IrregularSeason("NHL", 2020, 71, 82, "COVID: season halted in March"),
    IrregularSeason("NBA", 2021, 72, 82, "COVID: 72-game season"),
    IrregularSeason("NBA", 2020, 67, 82, "COVID: season suspended, bubble restart"),
    IrregularSeason("MLB", 2020, 60, 162, "COVID: 60-game season"),
    IrregularSeason("WNBA", 2020, 22, 34, "COVID: 22-game bubble season"),
)


def irregular_seasons(league: str) -> set[int]:
    """Seasons to keep out of a league's benchmark pool."""
    return {s.season for s in IRREGULAR_SEASONS if s.league == league}


def describe_exclusions(league: str, seasons: list[int]) -> list[str]:
    """Human-readable notes for whichever excluded seasons were requested."""
    excluded = {s.season: s for s in IRREGULAR_SEASONS if s.league == league}
    return [
        f"{s} excluded: {excluded[s].games} of {excluded[s].standard_games} games "
        f"({excluded[s].reason})"
        for s in seasons
        if s in excluded
    ]


def factor_for(league: str) -> float:
    """Scaling factor for a league, 1.0 when its schedule is unchanged."""
    change = SCHEDULE_CHANGES.get(league)
    return change.factor if change else 1.0


def scale_benchmarks(benchmarks, league: str, column: str = "benchmark"):
    """Scale a league's benchmarks to the current schedule length.

    For leagues whose scoring is entirely counting stats -- which is every player
    benchmark we compute -- the whole benchmark scales.
    """
    factor = factor_for(league)
    if factor == 1.0:
        return benchmarks
    out = benchmarks.copy()
    out[column] = (out[column] * factor).round(4)
    out["schedule_factor"] = factor
    return out
