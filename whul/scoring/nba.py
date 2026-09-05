"""NBA scoring -- port of NBA_Players_Teams.R.

Players are scored per game off the box score, with double-double and
triple-double bonuses, then summed and nudged by net plus-minus. Teams score off
the schedule: regular-season wins and blowouts, the Play-In, the playoffs
(including series wins), the NBA Cup, and point differential.

Both functions take an already-loaded frame so they stay pure and testable.
"""

from __future__ import annotations

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str
from whul.scoring.postseason import (
    EXCLUDED,
    POSTSEASON,
    REGULAR,
    RULES,
    apply_bonus,
    split_phases,
)

# Per-game box score weights, per NBA_Players_Teams.R
BOX_WEIGHTS = {
    "points": 1.0,
    "rebounds": 1.2,
    "assists": 1.5,
    "steals": 3.0,
    "blocks": 3.0,
    "turnovers": -1.0,
    "three_pt_made": 0.5,
}
DOUBLE_DOUBLE_BONUS = 1.5
TRIPLE_DOUBLE_BONUS = 3.0
PLUS_MINUS_WEIGHT = 0.1

# Categories that can reach double figures for a double-double.
DOUBLE_CATEGORIES = ("points", "rebounds", "assists", "steals", "blocks")

MIN_GAMES = 15
MIN_SCORE = 100

TEAM_WEIGHTS = {
    "reg_wins": 2.0,
    "reg_big_wins": 1.0,
    "playin_only": 3.0,  # awarded only if the Play-In did not lead to a berth
    "playoff_appearance": 10.0,
    "playoff_wins": 3.0,
    "playoff_series_wins": 5.0,
    "ist_wins": 2.0,
    "ist_champ": 8.0,
    "point_diff": 0.05,
}

BIG_WIN_MARGIN = 15
WINS_PER_SERIES = 4

# ESPN season_type codes as used by hoopR.
SEASON_TYPE_REGULAR = 2
SEASON_TYPE_POST = 3
SEASON_TYPE_PLAYIN = 5

PLAYIN_PATTERN = r"Play-?In"
IST_PATTERN = r"In-Season Tournament|NBA Cup"
IST_FINAL_PATTERN = r"Championship|Final"

# All-Star and exhibition entries appear as pseudo-teams (e.g. "LEB", "GIA") with
# a single game; they must not pollute the team pool the benchmark is drawn from.
EXHIBITION_PATTERN = r"All-Star|Rising Stars|Celebrity"
MIN_TEAM_GAMES = 10


def _plus_minus(df: pd.DataFrame) -> pd.Series:
    """Plus-minus arrives as a signed string ('+7', '-12', '')."""
    raw = resolve_str(df, ["plus_minus", "pm"])
    return pd.to_numeric(raw.str.replace("+", "", regex=False), errors="coerce").fillna(0.0)


def score_players(box: pd.DataFrame, postseason: bool = True) -> pd.DataFrame:
    """Season totals per player from per-game box scores.

    Playoff rows (``season_type`` 3) are credited as a bonus rather than as raw
    counting stats. Play-In games (5) are dropped entirely -- they are neither
    regular season nor playoffs. Pass ``postseason=False`` for benchmark
    computation.
    """
    work = pd.DataFrame(
        {
            "season": resolve_num(box, ["season"], required=True).astype(int),
            "athlete_id": resolve_str(box, ["athlete_id", "player_id"], required=True),
            "player": resolve_str(
                box, ["athlete_display_name", "athlete_name", "player_name"], required=True
            ),
            "position": resolve_str(
                box, ["athlete_position_abbreviation", "position_abbreviation", "position"]
            ),
            "points": resolve_num(box, ["points", "pts"]),
            "rebounds": resolve_num(box, ["rebounds", "reb", "total_rebounds"]),
            "assists": resolve_num(box, ["assists", "ast"]),
            "steals": resolve_num(box, ["steals", "stl"]),
            "blocks": resolve_num(box, ["blocks", "blk"]),
            "turnovers": resolve_num(box, ["turnovers", "to"]),
            "three_pt_made": resolve_num(box, ["three_point_field_goals_made", "fg3m"]),
            "plus_minus": _plus_minus(box),
        }
    )
    # A row with no recorded points is a DNP, not a zero-point performance.
    work = work[resolve_num(box, ["points", "pts"]).notna().to_numpy()]

    doubles = sum((work[c] >= 10).astype(int) for c in DOUBLE_CATEGORIES)
    work["game_points"] = (
        sum(work[c] * w for c, w in BOX_WEIGHTS.items())
        + (doubles >= 2).astype(float) * DOUBLE_DOUBLE_BONUS
        + (doubles >= 3).astype(float) * TRIPLE_DOUBLE_BONUS
    )

    # Net plus-minus is folded in per game so it rides along with the phase split.
    work["game_points"] = work["game_points"] + work["plus_minus"] * PLUS_MINUS_WEIGHT
    work["game_count"] = 1
    season_type = resolve_num(box, ["season_type"], default=SEASON_TYPE_REGULAR).reindex(
        work.index
    )
    work["phase"] = pd.Series(REGULAR, index=work.index).mask(
        season_type == SEASON_TYPE_POST, POSTSEASON
    ).mask(season_type == SEASON_TYPE_PLAYIN, EXCLUDED)

    keys = ["season", "athlete_id", "player", "position"]
    phases = split_phases(work, keys, "game_points", "game_count", work["phase"])
    agg = apply_bonus(phases, RULES["NBA"] if postseason else None)
    agg["league"] = "NBA"
    agg["role"] = agg["position"]
    keep = (agg["games_played"] >= MIN_GAMES) & (agg["total_points"] > MIN_SCORE)
    return agg[keep].reset_index(drop=True)


def _team_games(schedule: pd.DataFrame) -> pd.DataFrame:
    """One row per team per game."""
    notes = resolve_str(schedule, ["notes_headline", "notes"]).fillna("")
    season_type = resolve_num(schedule, ["season_type"], default=SEASON_TYPE_REGULAR)
    base = pd.DataFrame(
        {
            "season": resolve_num(schedule, ["season"], required=True).astype(int),
            "season_type": season_type,
            "notes": notes,
            "home_team": resolve_str(schedule, ["home_abbreviation", "home_team"], required=True),
            "away_team": resolve_str(schedule, ["away_abbreviation", "away_team"], required=True),
            "home_score": resolve_num(schedule, ["home_score"], default=float("nan")),
            "away_score": resolve_num(schedule, ["away_score"], default=float("nan")),
        }
    )
    # Only completed games count. Unplayed games are not NA in this feed -- they
    # carry a 0-0 score with status_type_completed False, so filtering on NA (as
    # the R script does) silently counts every future fixture as a played tie.
    # Harmless on a finished season, badly wrong during a live one.
    # hoopR spells it status_type_completed and ESPN spells it completed; both
    # mean the same thing and both feeds are used, hoopR for the seasons it
    # archived and ESPN for everything after.
    flag = next(
        (c for c in ("status_type_completed", "completed") if c in schedule.columns),
        None,
    )
    if flag is not None:
        completed = schedule[flag].fillna(False).astype(bool).to_numpy()
        base = base[completed[: len(base)] if len(completed) == len(base) else completed]
    else:
        base = base[(base["home_score"] > 0) | (base["away_score"] > 0)]
    base = base[base["home_score"].notna() & base["away_score"].notna()]
    base = base[~base["notes"].str.contains(EXHIBITION_PATTERN, case=False, regex=True, na=False)]

    sides = []
    for side, other in (("home", "away"), ("away", "home")):
        sides.append(
            pd.DataFrame(
                {
                    "season": base["season"],
                    "season_type": base["season_type"],
                    "notes": base["notes"],
                    "team": base[f"{side}_team"],
                    "points_for": base[f"{side}_score"],
                    "points_against": base[f"{other}_score"],
                }
            )
        )
    games = pd.concat(sides, ignore_index=True)
    games["margin"] = games["points_for"] - games["points_against"]
    games["is_win"] = games["margin"] > 0
    games["is_big_win"] = games["margin"] >= BIG_WIN_MARGIN
    games["is_reg"] = games["season_type"] == SEASON_TYPE_REGULAR
    games["is_playin"] = (games["season_type"] == SEASON_TYPE_PLAYIN) | games[
        "notes"
    ].str.contains(PLAYIN_PATTERN, case=False, regex=True, na=False)
    games["is_playoff"] = (games["season_type"] == SEASON_TYPE_POST) & ~games["is_playin"]
    games["is_ist"] = games["notes"].str.contains(IST_PATTERN, case=False, regex=True, na=False)
    games["is_ist_final"] = games["is_ist"] & games["notes"].str.contains(
        IST_FINAL_PATTERN, case=False, regex=True, na=False
    )
    return games


def score_teams(schedule: pd.DataFrame) -> pd.DataFrame:
    """Season fantasy totals per NBA team."""
    games = _team_games(schedule)
    # Drop pseudo-teams that survive the exhibition filter (All-Star squads play
    # a single game and would otherwise enter the benchmark pool as real teams).
    counts = games.groupby("team").size()
    games = games[games["team"].isin(counts[counts >= MIN_TEAM_GAMES].index)]
    summary = games.groupby(["season", "team"], as_index=False).apply(
        lambda g: pd.Series(
            {
                "reg_wins": int((g["is_win"] & g["is_reg"]).sum()),
                "reg_big_wins": int((g["is_big_win"] & g["is_reg"]).sum()),
                "point_diff": float(g.loc[g["is_reg"], "margin"].sum()),
                "playin_appearance": int(g["is_playin"].any()),
                "playoff_appearance": int(g["is_playoff"].any()),
                "playoff_wins": int((g["is_win"] & g["is_playoff"]).sum()),
                "ist_wins": int((g["is_win"] & g["is_ist"]).sum()),
                "ist_champ": int((g["is_win"] & g["is_ist_final"]).any()),
            }
        ),
        include_groups=False,
    )
    summary["playoff_series_wins"] = summary["playoff_wins"] // WINS_PER_SERIES
    # The Play-In bonus is consolation: it is dropped once a berth is secured.
    summary["playin_only"] = (
        (summary["playin_appearance"] == 1) & (summary["playoff_appearance"] == 0)
    ).astype(int)

    summary["total_points"] = sum(summary[c] * w for c, w in TEAM_WEIGHTS.items())
    summary["league"] = "NBA"
    return summary.sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True)
