"""NHL scoring -- port of NHL_Teams_Players.R.

**Skaters only.** Goalies are scored here for completeness but excluded from
normalization, matching ``All_Analysis.R``: goalie and skater distributions are
not comparable and the league abandoned goalie slots.

**84-game season.** The NHL expands from 82 games in 2026-27, so historical
benchmarks describe a shorter season than the one being scored. Regular-season
components are scaled at source for teams, and the benchmark is scaled for
players -- see ``whul.scoring.schedule``.

**No byes.** All sixteen qualifiers play a first round, so the rule that a bye
scores as a swept round does not arise here.

**Division titles need the standings.** A season summary says how a club did,
not who it was competing with, so ``score_teams`` takes a division map and
awards nothing without one. It also awards nothing until every club in the
division has played its schedule: a title is an outcome, not a rate, and one
handed out in November belongs to whoever started well.
"""

from __future__ import annotations

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str
from whul.scoring.schedule import factor_for, scheduled_games

# --- teams ----------------------------------------------------------------
PTS_WIN = 2.0
PTS_OTL = 1.0
PTS_GOAL_DIFF = 0.1
PTS_DIV_CHAMP = 10.0
PTS_PLAYOFF_APP = 5.0
PTS_PLAYOFF_WIN = 1.0
PTS_SERIES_WIN = 5.0
WINS_PER_SERIES = 4

#: Standings points, which is what a division is won on. Computed from wins and
#: overtime losses rather than read from the feed's own ``points`` column: those
#: two are already resolved by name and checked by the probe, and a column that
#: turns out to be absent resolves to zero for every club -- which would not
#: fail, it would make every division a five-way tie.
STANDINGS_WIN = 2
STANDINGS_OTL = 1

#: The NHL's tiebreakers, in the order it applies them, minus the ones a season
#: summary cannot answer. Games played never separates anyone here because a
#: title is only awarded once every club has played its full schedule, and
#: head-to-head points need a schedule this scorer is not given. Regulation wins
#: are used when the feed carries them and skipped when it does not, which is
#: why goal differential is kept as the last real step.
TIEBREAKERS = ("regulation wins", "goal differential")

# --- skaters --------------------------------------------------------------
PTS_GOAL = 3.0
PTS_ASSIST = 2.0
PTS_SHOT = 0.5
PTS_PLUS_MINUS = 1.0

# --- goalies (scored, but not normalized) ---------------------------------
PTS_GOALIE_WIN = 4.0
PTS_SHUTOUT = 3.0
PTS_SAVE = 0.1
PTS_GOAL_AGAINST = -1.0

SKATER_ROLE = "Skater"
GOALIE_ROLE = "Goalie"


def score_skaters(df: pd.DataFrame) -> pd.DataFrame:
    """Season points per skater. All components are counting stats."""
    if df is None or df.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "season": resolve_num(df, ["season", "season_id", "seasonId"], required=True).astype(int),
            "player": resolve_str(
                df, ["player", "skater_full_name", "skaterFullName", "playerName"], required=True
            ),
            "player_id": resolve_str(df, ["player_id", "playerId"]),
            "games_played": resolve_num(df, ["games_played", "gamesPlayed"]),
            "goals": resolve_num(df, ["goals"]),
            "assists": resolve_num(df, ["assists"]),
            "shots": resolve_num(df, ["shots"]),
            "plus_minus": resolve_num(df, ["plus_minus", "plusMinus"]),
        }
    )
    work["total_points"] = (
        work["goals"] * PTS_GOAL
        + work["assists"] * PTS_ASSIST
        + work["shots"] * PTS_SHOT
        + work["plus_minus"] * PTS_PLUS_MINUS
    )
    work["league"] = "NHL"
    work["role"] = SKATER_ROLE
    return work[work["total_points"] > 0].reset_index(drop=True)


def score_goalies(df: pd.DataFrame) -> pd.DataFrame:
    """Season points per goalie.

    Retained because the R script computes it, but goalies hold no roster slots
    and are excluded from normalization, so this feeds nothing downstream.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "season": resolve_num(df, ["season", "season_id", "seasonId"], required=True).astype(int),
            "player": resolve_str(
                df, ["player", "goalie_full_name", "goalieFullName", "playerName"], required=True
            ),
            "games_played": resolve_num(df, ["games_played", "gamesPlayed"]),
            "wins": resolve_num(df, ["wins"]),
            "shutouts": resolve_num(df, ["shutouts"]),
            "saves": resolve_num(df, ["saves"]),
            "goals_against": resolve_num(df, ["goals_against", "goalsAgainst"]),
        }
    )
    work["total_points"] = (
        work["wins"] * PTS_GOALIE_WIN
        + work["shutouts"] * PTS_SHUTOUT
        + work["saves"] * PTS_SAVE
        + work["goals_against"] * PTS_GOAL_AGAINST
    )
    work["league"] = "NHL"
    work["role"] = GOALIE_ROLE
    return work[work["total_points"] > 0].reset_index(drop=True)


def score_players(skaters: pd.DataFrame, goalies: pd.DataFrame | None = None) -> pd.DataFrame:
    """Skaters only -- the league does not roster goalies."""
    return score_skaters(skaters)


def _division_champs(
    work: pd.DataFrame, divisions: pd.DataFrame | None
) -> pd.Series:
    """The club atop each division, per season, once the season has finished.

    Most standings points in the division, ties broken on regulation wins and
    then goal differential -- the NHL's own order, minus the steps a season
    summary cannot answer. Clubs level after both share the title: they were
    not separated by anything that happened on the ice, and picking one would
    be inventing a result rather than reading one.

    Two things return zeroes rather than a guess, and both matter more than the
    title does:

    * **No divisions.** A club's own totals never say who it was racing.
    * **An unfinished season.** A standing is only a final standing once every
      club in the division has played its schedule. Awarding on games in hand
      would pay ten points in November to whoever started well and take them
      back in March, which is the bug this scorer had in the other direction
      for a year -- and the same one ``whul.scoring.mlb`` fixed by refusing to
      award a title mid-season.
    """
    zero = pd.Series(0, index=work.index, dtype=int)
    if divisions is None or divisions.empty:
        return zero
    if not {"season", "team", "division"} <= set(divisions.columns):
        return zero

    lookup = divisions.assign(
        season=pd.to_numeric(divisions["season"], errors="coerce"),
        team=divisions["team"].astype(str),
        division=divisions["division"].astype(str),
    ).dropna(subset=["season"])
    lookup["season"] = lookup["season"].astype(int)

    placed = work[["season", "team"]].copy()
    placed["team"] = placed["team"].astype(str)
    placed = placed.merge(
        lookup[["season", "team", "division"]].drop_duplicates(["season", "team"]),
        on=["season", "team"],
        how="left",
    )
    for column in ("standings_points", "regulation_wins", "goal_diff",
                   "games_played"):
        placed[column] = pd.to_numeric(work[column], errors="coerce").to_numpy()
    placed.index = work.index

    champ = zero.copy()
    for (season, division), block in placed.dropna(subset=["division"]).groupby(
        ["season", "division"]
    ):
        full = scheduled_games("NHL", int(season))
        if full is None or (block["games_played"] < full).any():
            continue
        leaders = block.index
        for column in ("standings_points", "regulation_wins", "goal_diff"):
            best = block.loc[leaders, column].max()
            leaders = leaders[block.loc[leaders, column] == best]
            if len(leaders) == 1:
                break
        champ.loc[leaders] = 1
    return champ


def score_teams(
    regular: pd.DataFrame,
    playoffs: pd.DataFrame | None = None,
    scale_regular_season: bool | None = None,
    divisions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Season points per team, blending regular-season and playoff summaries.

    ``scale_regular_season`` lifts wins, overtime losses and goal differential to
    the current schedule length, leaving the playoff and division terms alone --
    those do not scale with games played. Defaults to on whenever the league has
    a schedule change configured.

    ``divisions`` maps a team to its division for a season -- columns
    ``season``, ``team``, ``division``. Without it no division title is awarded,
    which is the honest answer: a season summary says how a club did, not who
    it was competing with.
    """
    if regular is None or regular.empty:
        return pd.DataFrame()

    factor = factor_for("NHL") if scale_regular_season in (None, True) else 1.0

    work = pd.DataFrame(
        {
            "season": resolve_num(regular, ["season", "season_id", "seasonId"], required=True).astype(int),
            "team": resolve_str(regular, ["team", "team_full_name", "teamFullName"], required=True),
            "team_id": resolve_str(regular, ["team_id", "teamId"]),
            "games_played": resolve_num(regular, ["games_played", "gamesPlayed"]),
            "reg_wins": resolve_num(regular, ["wins"]),
            "reg_otl": resolve_num(regular, ["ot_losses", "otLosses"]),
            # A tiebreaker, and the feed may not carry it. Absent, it resolves
            # to zero for every club and simply does not separate anyone, which
            # is why goal differential follows it.
            "regulation_wins": resolve_num(
                regular, ["regulation_wins", "regulationWins"]
            ),
            "goals_for": resolve_num(regular, ["goals_for", "goalsFor"]),
            "goals_against": resolve_num(regular, ["goals_against", "goalsAgainst"]),
        }
    )
    work["goal_diff"] = work["goals_for"] - work["goals_against"]

    if playoffs is not None and not playoffs.empty:
        post = pd.DataFrame(
            {
                "season": resolve_num(playoffs, ["season", "season_id", "seasonId"], required=True).astype(int),
                "team": resolve_str(playoffs, ["team", "team_full_name", "teamFullName"], required=True),
                "playoff_games": resolve_num(playoffs, ["games_played", "gamesPlayed"]),
                "playoff_wins": resolve_num(playoffs, ["wins"]),
            }
        )
        work = work.merge(post, on=["season", "team"], how="left")
    else:
        work["playoff_games"] = 0.0
        work["playoff_wins"] = 0.0

    work[["playoff_games", "playoff_wins"]] = work[["playoff_games", "playoff_wins"]].fillna(0.0)
    work["made_playoffs"] = (work["playoff_games"] > 0).astype(int)
    work["series_wins"] = (work["playoff_wins"] // WINS_PER_SERIES).astype(int)
    work["standings_points"] = (
        work["reg_wins"] * STANDINGS_WIN + work["reg_otl"] * STANDINGS_OTL
    )
    work["is_division_champ"] = _division_champs(work, divisions)

    work["total_points"] = (
        work["reg_wins"] * PTS_WIN * factor
        + work["reg_otl"] * PTS_OTL * factor
        + work["goal_diff"] * PTS_GOAL_DIFF * factor
        + work["made_playoffs"] * PTS_PLAYOFF_APP
        + work["playoff_wins"] * PTS_PLAYOFF_WIN
        + work["series_wins"] * PTS_SERIES_WIN
        + work["is_division_champ"] * PTS_DIV_CHAMP
    )
    work["league"] = "NHL"
    work["schedule_factor"] = factor
    return work.sort_values(["season", "total_points"], ascending=[True, False]).reset_index(
        drop=True
    )
