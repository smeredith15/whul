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
"""

from __future__ import annotations

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str
from whul.scoring.schedule import factor_for

# --- teams ----------------------------------------------------------------
PTS_WIN = 2.0
PTS_OTL = 1.0
PTS_GOAL_DIFF = 0.1
PTS_DIV_CHAMP = 10.0
PTS_PLAYOFF_APP = 5.0
PTS_PLAYOFF_WIN = 1.0
PTS_SERIES_WIN = 5.0
WINS_PER_SERIES = 4

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


def score_teams(
    regular: pd.DataFrame,
    playoffs: pd.DataFrame | None = None,
    scale_regular_season: bool | None = None,
) -> pd.DataFrame:
    """Season points per team, blending regular-season and playoff summaries.

    ``scale_regular_season`` lifts wins, overtime losses and goal differential to
    the current schedule length, leaving the playoff and division terms alone --
    those do not scale with games played. Defaults to on whenever the league has
    a schedule change configured.
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
    # The R script leaves division titles and awards as placeholders; a division
    # standing is not derivable from a team summary alone.
    work["is_division_champ"] = 0

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
