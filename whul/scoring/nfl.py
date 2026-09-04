"""NFL scoring -- port of NFL_Players.R and NFL_Teams.R.

Players use half-PPR. Teams score off the schedule: wins, blowouts, shutouts,
division record and title, playoff appearance and wins, and point differential.

Both functions take an already-loaded frame so they stay pure and testable; the
network lives in ``whul.sources.nflverse``.
"""

from __future__ import annotations

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str
from whul.scoring.postseason import POSTSEASON, REGULAR, RULES, apply_bonus, split_phases

SCORING_POSITIONS = ("QB", "RB", "WR", "TE")

# Half-PPR, per NFL_Players.R
PLAYER_WEIGHTS = {
    "passing_yards": 0.04,
    "passing_tds": 4.0,
    "interceptions": -2.0,
    "rushing_yards": 0.1,
    "rushing_tds": 6.0,
    "receptions": 0.5,
    "receiving_yards": 0.1,
    "receiving_tds": 6.0,
    "fumbles_lost": -2.0,
}

# Per NFL_Teams.R
TEAM_WEIGHTS = {
    "reg_wins": 10.0,
    "reg_big_wins": 3.0,
    "reg_shutouts": 5.0,
    "div_wins": 2.0,
    "div_champ": 15.0,
    "playoff_appearance": 10.0,
    "playoff_wins": 15.0,
    "point_diff": 0.1,
}

BIG_WIN_MARGIN = 9  # a "big win" is a two-possession game or better


def score_players(stats: pd.DataFrame, postseason: bool = True) -> pd.DataFrame:
    """Season half-PPR totals per player.

    The nflverse release carries both REG and POST rows. Regular-season points
    are summed directly; postseason production is credited as a bonus worth a
    fixed number of extra games at the player's own rate (see
    ``whul.scoring.postseason``). Pass ``postseason=False`` to score the regular
    season alone -- which is what benchmark computation uses.
    """
    df = stats
    work = pd.DataFrame(
        {
            "season": resolve_num(df, ["season"], required=True).astype(int),
            "player_id": resolve_str(df, ["player_id", "gsis_id"], required=True),
            "player": resolve_str(
                df, ["player_display_name", "player_name", "display_name"], required=True
            ),
            "position": resolve_str(df, ["position", "position_group"], required=True),
            "team": resolve_str(df, ["team", "recent_team", "team_abbr"]),
            "week": resolve_num(df, ["week"], required=True),
            "passing_yards": resolve_num(df, ["passing_yards"]),
            "passing_tds": resolve_num(df, ["passing_tds"]),
            "interceptions": resolve_num(df, ["passing_interceptions", "interceptions"]),
            "rushing_yards": resolve_num(df, ["rushing_yards"]),
            "rushing_tds": resolve_num(df, ["rushing_tds"]),
            "receptions": resolve_num(df, ["receptions"]),
            "receiving_yards": resolve_num(df, ["receiving_yards"]),
            "receiving_tds": resolve_num(df, ["receiving_tds"]),
            "sack_fumbles_lost": resolve_num(df, ["sack_fumbles_lost"]),
            "rushing_fumbles_lost": resolve_num(df, ["rushing_fumbles_lost"]),
            "receiving_fumbles_lost": resolve_num(df, ["receiving_fumbles_lost"]),
        }
    )
    work["fumbles_lost"] = (
        work["sack_fumbles_lost"] + work["rushing_fumbles_lost"] + work["receiving_fumbles_lost"]
    )
    work = work[work["position"].isin(SCORING_POSITIONS)].copy()
    work["game_points"] = sum(work[c] * w for c, w in PLAYER_WEIGHTS.items())
    work["game_count"] = 1
    phase = resolve_str(stats, ["season_type"], default="REG").reindex(work.index)
    work["phase"] = phase.map({"REG": REGULAR, "POST": POSTSEASON}).fillna(REGULAR)

    keys = ["season", "player_id", "player", "position"]
    phases = split_phases(work, keys, "game_points", "game_count", work["phase"])
    teams = (
        work.groupby(keys, as_index=False)["team"]
        .agg(lambda s: "/".join(sorted(set(x for x in s if x))))
    )
    agg = apply_bonus(phases.merge(teams, on=keys, how="left"), RULES["NFL"] if postseason else None)
    agg["league"] = "NFL"
    agg["role"] = agg["position"]
    return agg[agg["total_points"] > 0].reset_index(drop=True)


def _team_games(schedules: pd.DataFrame) -> pd.DataFrame:
    """One row per team per game, from a home/away schedule frame."""
    played = schedules[schedules["home_score"].notna() & schedules["away_score"].notna()]
    if played.empty:
        # Before week one every game is scheduled and none is played. Building
        # the per-team frame anyway reads columns off an empty selection and
        # raises on whichever is missing, which reads as a broken scorer rather
        # than as a season that has not started.
        return pd.DataFrame()
    sides = []
    for side, other in (("home", "away"), ("away", "home")):
        sides.append(
            pd.DataFrame(
                {
                    "season": played["season"].astype(int),
                    "game_type": played["game_type"].astype(str),
                    "team": played[f"{side}_team"].astype(str),
                    "points_for": pd.to_numeric(played[f"{side}_score"]),
                    "points_against": pd.to_numeric(played[f"{other}_score"]),
                    "div_game": played["div_game"].fillna(0).astype(int) == 1,
                }
            )
        )
    games = pd.concat(sides, ignore_index=True)
    games["margin"] = games["points_for"] - games["points_against"]
    games["is_win"] = games["margin"] > 0
    games["is_big_win"] = games["margin"] >= BIG_WIN_MARGIN
    games["is_shutout"] = (games["points_against"] == 0) & games["is_win"]
    games["is_reg"] = games["game_type"] == "REG"
    games["is_playoff"] = games["game_type"].isin(["WC", "DIV", "CON", "SB"])
    return games


def score_teams(schedules: pd.DataFrame, teams_meta: pd.DataFrame) -> pd.DataFrame:
    """Season fantasy totals per NFL team.

    ``teams_meta`` supplies ``team_abbr`` -> ``team_division``, which is needed to
    award the division title (best record in the division, point differential
    breaking ties).
    """
    games = _team_games(schedules)
    if games.empty:
        # Nothing has been played. groupby.apply over an empty frame returns a
        # frame with no columns at all, so every reference after this raises a
        # KeyError naming whichever column is read first -- which reads as a
        # broken scorer rather than as a season that has not started.
        return pd.DataFrame()
    summary = games.groupby(["season", "team"], as_index=False).apply(
        lambda g: pd.Series(
            {
                "reg_wins": int((g["is_win"] & g["is_reg"]).sum()),
                "reg_big_wins": int((g["is_big_win"] & g["is_reg"]).sum()),
                "reg_shutouts": int((g["is_shutout"] & g["is_reg"]).sum()),
                "div_wins": int((g["is_win"] & g["is_reg"] & g["div_game"]).sum()),
                "point_diff": float(g.loc[g["is_reg"], "margin"].sum()),
                "playoff_appearance": int(g["is_playoff"].any()),
                "playoff_wins": int((g["is_win"] & g["is_playoff"]).sum()),
            }
        ),
        include_groups=False,
    )

    meta = teams_meta.rename(columns={"team_abbr": "team"})
    columns = ["team", "team_division"] + (
        ["team_name"] if "team_name" in meta.columns else []
    )
    summary = summary.merge(meta[columns].drop_duplicates("team"), on="team", how="left")
    summary = summary.sort_values(
        ["reg_wins", "point_diff"], ascending=False, kind="mergesort"
    )
    summary["div_champ"] = (
        summary.groupby(["season", "team_division"]).cumcount() == 0
    ).astype(int)

    summary["total_points"] = sum(summary[c] * w for c, w in TEAM_WEIGHTS.items())
    summary["league"] = "NFL"
    # The abbreviation stays the key; the full name is what a roster calls it.
    if "team_name" not in summary.columns:
        summary["team_name"] = summary["team"]
    summary["team_name"] = summary["team_name"].fillna(summary["team"])
    return summary.sort_values(["season", "total_points"], ascending=[True, False]).reset_index(
        drop=True
    )
