"""MLB scoring -- port of MLB_Players_Teams.R.

Two things make MLB unlike the other leagues:

**The contract engine.** MLB's season straddles the July draft, so an asset is
valued across a rolling twelve months anchored on the All-Star break: the
remainder of year N is discounted for being largely known, and the pre-break
portion of year N+1 is inflated so the two shares reconcile to a full season.

**The two-way rule.** A player who both bats and pitches scores their primary
role in full plus half their secondary. This is not an Ohtani special case -- it
applies to every player who accumulates in both, including a position player who
mops up an inning in a blowout.

Crucially the two halves are normalized **separately**: batting production is
measured against the batter benchmark and pitching against the pitcher benchmark,
and only then are the normalized scores combined. Raw batting and pitching points
are on different scales, so combining before normalizing would compare unlike
quantities -- and it is the normalized scores that decide which role is primary.
Scoring therefore emits one row per player-role, and ``combine_two_way`` is
applied after ``apply_benchmarks``.
"""

from __future__ import annotations

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str
from whul.scoring.bisection import MLB as _MLB_RULE

# --- contract engine ------------------------------------------------------
# The weights live in whul.scoring.bisection with the other bisected leagues,
# so the shares and the reconciling inflation are defined once rather than
# per league. Re-exported here because the scoring below reads them by name.
SHARE_POST_ASB = _MLB_RULE.share_post   #: after the All-Star break in year N
SHARE_PRE_ASB = _MLB_RULE.share_pre     #: before it in year N+1
MULT_YEAR_N = _MLB_RULE.mult_n          #: year N, discounted as partly known
MULT_YEAR_N1 = _MLB_RULE.mult_n1        #: year N+1, inflated to reconcile (~1.181)

SECONDARY_ROLE_WEIGHT = 0.5

# --- batter scoring -------------------------------------------------------
BATTER_WEIGHTS = {
    "ab": -1.0, "h": 5.6, "doubles": 2.9, "triples": 5.7, "hr": 9.4,
    "bb": 3.0, "hbp": 3.0, "sb": 1.9, "cs": -2.8,
}
#: FanGraphs Offense and Defense runs, rescaled as the R script does:
#: (component / 10) * factor, then * 10 -- i.e. component * factor.
OFFENSE_FACTOR = 0.25
DEFENSE_FACTOR = 1.5

#: Offense, Defense and WAR exist only on FanGraphs among free sources. If it is
#: unreachable, scoring can fall back to counting stats alone -- but that is a
#: rules change, not a source swap: the advanced terms are 0.7-5% of a score and
#: they are not uniform, being worth most to glove-first players and least to
#: sluggers, so dropping them compresses the gap between those profiles.
#: Never flip this silently.
INCLUDE_ADVANCED_METRICS = True

# --- pitcher scoring ------------------------------------------------------
PITCHER_WEIGHTS = {
    "ip": 7.4, "so": 2.0, "h": -2.6, "bb": -3.0, "hbp": -3.0,
    "hr": -12.3, "sv": 5.0, "hld": 4.0,
}
WAR_FACTOR = 0.5

# --- team scoring ---------------------------------------------------------
BASE_REG_WIN = 2.0
BASE_PLAYOFF_WIN = 3.0
BIG_WIN_MARGIN = 5
PTS_BIG_WIN = 1.0
PTS_SHUTOUT = 2.0
PTS_RUN_DIFF = 0.05
PTS_DIV_CHAMP = 5.0
#: Flat series milestones -- deliberately not deflated by the year-N multiplier.
PTS_SERIES = {"wc": 5, "lds": 6, "lcs": 7, "ws": 8}
#: Top fifth of the league by wins is treated as a division winner.
DIV_CHAMP_PERCENTILE = 0.80

GAME_TYPE_REGULAR = "R"
GAME_TYPE_WC = "F"
GAME_TYPE_LDS = "D"
GAME_TYPE_LCS = "L"
GAME_TYPE_WS = "W"
PLAYOFF_GAME_TYPES = (GAME_TYPE_WC, GAME_TYPE_LDS, GAME_TYPE_LCS, GAME_TYPE_WS)


EMPTY_ROLE_COLUMNS = ["season", "player", "player_id", "games", "role_points", "role"]


def _empty_role_frame() -> pd.DataFrame:
    """An empty result with the right columns, so a missing feed degrades rather
    than raising on the first required-column lookup."""
    return pd.DataFrame({c: pd.Series(dtype="object") for c in EMPTY_ROLE_COLUMNS})


def _carry_advanced_share(work: pd.DataFrame, df: pd.DataFrame) -> None:
    """Keep the note saying the run values were shared out, not measured.

    The scoring frame is built column by column from names it knows, so
    anything the source attached is dropped unless it is named here. That is
    usually right and was wrong for this one: Offense, Defense and WAR are a
    share of a whole season, apportioned by the games a player actually played
    in the window, and without the share recorded alongside them the figure
    looks measured. A run value that looks measured and is not is exactly the
    number somebody checks against Baseball Reference and cannot find.

    Absent where the source could not compute one -- which is itself worth
    seeing, since it means the figure is a whole season's and has not been cut
    down to the window at all.
    """
    for name in ("advanced_share",):
        if name in df.columns:
            work[name] = pd.to_numeric(df[name], errors="coerce").to_numpy()


def score_batters(df: pd.DataFrame, include_advanced: bool | None = None) -> pd.DataFrame:
    """Season batting points per player.

    ``include_advanced`` gates the FanGraphs Offense and Defense terms; see
    ``INCLUDE_ADVANCED_METRICS``.
    """
    if df is None or df.empty:
        return _empty_role_frame()
    work = pd.DataFrame(
        {
            "season": resolve_num(df, ["season", "Season"], required=True).astype(int),
            "player": resolve_str(
                df, ["playername", "PlayerName", "player_name", "Name", "name"], required=True
            ),
            "player_id": resolve_str(df, ["playerid", "PlayerId", "playerId", "IDfg"]),
            "ab": resolve_num(df, ["ab", "AB"]),
            "h": resolve_num(df, ["h", "H"]),
            "doubles": resolve_num(df, ["2B", "x2b", "doubles"]),
            "triples": resolve_num(df, ["3B", "x3b", "triples"]),
            "hr": resolve_num(df, ["hr", "HR"]),
            "bb": resolve_num(df, ["bb", "BB"]),
            "hbp": resolve_num(df, ["hbp", "HBP"]),
            "sb": resolve_num(df, ["sb", "SB"]),
            "cs": resolve_num(df, ["cs", "CS"]),
            "offense": resolve_num(df, ["offense", "Off", "off"]),
            "defense": resolve_num(df, ["defense", "Def", "def"]),
            "games": resolve_num(df, ["g", "G", "games"]),
        }
    )
    _carry_advanced_share(work, df)
    advanced = INCLUDE_ADVANCED_METRICS if include_advanced is None else include_advanced
    work["role_points"] = sum(work[c] * w for c, w in BATTER_WEIGHTS.items())
    if advanced:
        work["role_points"] += work["offense"] * OFFENSE_FACTOR + work["defense"] * DEFENSE_FACTOR
    work["role"] = "Batter"
    return work[work["role_points"] > 0].reset_index(drop=True)


def score_pitchers(df: pd.DataFrame, include_advanced: bool | None = None) -> pd.DataFrame:
    """Season pitching points per player.

    ``include_advanced`` gates the FanGraphs WAR term; see
    ``INCLUDE_ADVANCED_METRICS``.
    """
    if df is None or df.empty:
        return _empty_role_frame()
    work = pd.DataFrame(
        {
            "season": resolve_num(df, ["season", "Season"], required=True).astype(int),
            "player": resolve_str(
                df, ["playername", "PlayerName", "player_name", "Name", "name"], required=True
            ),
            "player_id": resolve_str(df, ["playerid", "PlayerId", "playerId", "IDfg"]),
            "ip": resolve_num(df, ["ip", "IP"]),
            "so": resolve_num(df, ["so", "SO", "k", "K"]),
            "h": resolve_num(df, ["h", "H"]),
            "bb": resolve_num(df, ["bb", "BB"]),
            "hbp": resolve_num(df, ["hbp", "HBP"]),
            "hr": resolve_num(df, ["hr", "HR"]),
            "sv": resolve_num(df, ["sv", "SV"]),
            "hld": resolve_num(df, ["hld", "HLD", "holds"]),
            "war": resolve_num(df, ["war", "WAR", "fwar"]),
            "games": resolve_num(df, ["g", "G", "games"]),
        }
    )
    _carry_advanced_share(work, df)
    advanced = INCLUDE_ADVANCED_METRICS if include_advanced is None else include_advanced
    work["role_points"] = sum(work[c] * w for c, w in PITCHER_WEIGHTS.items())
    if advanced:
        work["role_points"] += work["war"] * WAR_FACTOR * 10
    work["role"] = "Pitcher"
    return work[work["role_points"] > 0].reset_index(drop=True)


def score_players(
    batters: pd.DataFrame,
    pitchers: pd.DataFrame,
    include_advanced: bool | None = None,
) -> pd.DataFrame:
    """One row per player *per role*, ready for normalization.

    Deliberately not combined here: batting is normalized against the batter
    benchmark and pitching against the pitcher benchmark, so the two halves must
    stay separate until ``apply_benchmarks`` has run. Call ``combine_two_way``
    afterwards to fold a two-way player's two rows into one.
    """
    frames = [
        f for f in (
            score_batters(batters, include_advanced),
            score_pitchers(pitchers, include_advanced),
        )
        if f is not None and not f.empty
    ]
    if not frames:
        empty = _empty_role_frame()
        empty["total_points"] = pd.Series(dtype="object")
        empty["league"] = pd.Series(dtype="object")
        return empty

    out = pd.concat(frames, ignore_index=True)
    # `total_points` is the column the normalization engine reads.
    out["total_points"] = out["role_points"]
    out["league"] = "MLB"
    return out.reset_index(drop=True)


def combine_two_way(scored: pd.DataFrame) -> pd.DataFrame:
    """Fold per-role normalized scores into one row per player.

    Applied **after** normalization: the primary role is whichever scored higher
    on the 0-100 scale, since raw batting and pitching points are not comparable.
    The secondary role contributes half its normalized score.
    """
    keys = ["season", "player"]
    if scored is None or scored.empty:
        return scored

    ranked = scored.sort_values("scaled_score", ascending=False)
    primary = ranked.groupby(keys, as_index=False).first()
    extras = ranked.groupby(keys, as_index=False).agg(
        role_count=("role", "size"),
        secondary_score=("scaled_score", lambda s: s.iloc[1] if len(s) > 1 else 0.0),
        secondary_role=("role", lambda s: s.iloc[1] if len(s) > 1 else ""),
    )

    out = primary.merge(extras, on=keys, how="left")
    out["is_two_way"] = out["role_count"] > 1
    out["primary_score"] = out["scaled_score"]
    out["scaled_score"] = (
        out["primary_score"] + out["secondary_score"] * SECONDARY_ROLE_WEIGHT
    ).round(2)
    return out.reset_index(drop=True)


def _team_games(schedule: pd.DataFrame) -> pd.DataFrame:
    """One row per team per game from a home/away schedule frame."""
    if schedule is None or schedule.empty:
        return pd.DataFrame(
            {c: pd.Series(dtype="object")
             for c in ("season", "game_type", "team", "runs_for", "runs_against",
                       "margin", "is_win", "is_reg")}
        )
    base = pd.DataFrame(
        {
            "season": resolve_num(schedule, ["season"], required=True).astype(int),
            "game_type": resolve_str(schedule, ["game_type", "gameType"], required=True),
            "home_team": resolve_str(schedule, ["home_team", "home_name"], required=True),
            "away_team": resolve_str(schedule, ["away_team", "away_name"], required=True),
            "home_score": resolve_num(schedule, ["home_score"], default=float("nan")),
            "away_score": resolve_num(schedule, ["away_score"], default=float("nan")),
        }
    )
    base = base[base["home_score"].notna() & base["away_score"].notna()]

    sides = []
    for side, other in (("home", "away"), ("away", "home")):
        sides.append(
            pd.DataFrame(
                {
                    "season": base["season"],
                    "game_type": base["game_type"],
                    "team": base[f"{side}_team"],
                    "runs_for": base[f"{side}_score"],
                    "runs_against": base[f"{other}_score"],
                }
            )
        )
    games = pd.concat(sides, ignore_index=True)
    games["margin"] = games["runs_for"] - games["runs_against"]
    games["is_win"] = games["margin"] > 0
    games["is_reg"] = games["game_type"] == GAME_TYPE_REGULAR
    return games


TEAM_SUMMARY_COLUMNS = [
    "season", "team", "reg_wins", "reg_big_wins", "shutouts", "run_diff",
    "wc_wins", "lds_wins", "lcs_wins", "ws_wins", "series_wc_or_bye",
    "series_lds", "series_lcs", "series_ws", "playoff_game_wins",
    "is_division_champ",
]


def summarize_teams(schedule: pd.DataFrame) -> pd.DataFrame:
    """Per-season team totals, before the contract weighting."""
    games = _team_games(schedule)
    if games.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in TEAM_SUMMARY_COLUMNS})
    summary = games.groupby(["season", "team"], as_index=False).apply(
        lambda g: pd.Series(
            {
                "reg_wins": int((g["is_win"] & g["is_reg"]).sum()),
                "reg_big_wins": int(
                    (g["is_win"] & g["is_reg"] & (g["margin"] >= BIG_WIN_MARGIN)).sum()
                ),
                "shutouts": int((g["is_win"] & g["is_reg"] & (g["runs_against"] == 0)).sum()),
                "run_diff": float(g.loc[g["is_reg"], "margin"].sum()),
                "wc_wins": int((g["is_win"] & (g["game_type"] == GAME_TYPE_WC)).sum()),
                "lds_wins": int((g["is_win"] & (g["game_type"] == GAME_TYPE_LDS)).sum()),
                "lcs_wins": int((g["is_win"] & (g["game_type"] == GAME_TYPE_LCS)).sum()),
                "ws_wins": int((g["is_win"] & (g["game_type"] == GAME_TYPE_WS)).sum()),
            }
        ),
        include_groups=False,
    )

    # Series milestones, allowing for first-round byes.
    summary["series_wc_or_bye"] = (
        (summary["lds_wins"] > 0) | (summary["wc_wins"] >= 2)
    ).astype(int)
    summary["series_lds"] = (summary["lds_wins"] >= 3).astype(int)
    summary["series_lcs"] = (summary["lcs_wins"] >= 4).astype(int)
    summary["series_ws"] = (summary["ws_wins"] == 4).astype(int)
    summary["playoff_game_wins"] = (
        summary["wc_wins"] + summary["lds_wins"] + summary["lcs_wins"] + summary["ws_wins"]
    )
    summary["is_division_champ"] = (
        summary.groupby("season")["reg_wins"].rank(pct=True) >= DIV_CHAMP_PERCENTILE
    ).astype(int)
    return summary


def _window_points(summary: pd.DataFrame) -> pd.DataFrame:
    """Points for the games in front of us, as components a prorater can scale.

    The historical path splits a whole season into its post- and pre-break
    shares. A live pull is not a whole season to split: it has already been cut
    to the league year's own window by the start date, so multiplying by
    ``SHARE_POST_ASB`` on top of that shortens it twice -- the first time by
    the calendar, the second by arithmetic that no longer describes it.

    So the window is scored at face value, and split into components rather
    than a single number, because the two kinds do not scale alike: wins, big
    wins, shutouts and run differential grow with games played, while a
    division title and a playoff run happen once however long the window is.
    ``whul.scoring.proration`` lifts the first four to a full season and leaves
    the rest where they are.
    """
    out = pd.DataFrame({
        "season": summary["season"],
        "team": summary["team"],
        "reg_wins": summary["reg_wins"],
        "pts_reg_wins": summary["reg_wins"] * BASE_REG_WIN,
        "pts_big_wins": summary["reg_big_wins"] * PTS_BIG_WIN,
        "pts_shutouts": summary["shutouts"] * PTS_SHUTOUT,
        "pts_run_diff": summary["run_diff"] * PTS_RUN_DIFF,
        "pts_div_champ": summary["is_division_champ"] * PTS_DIV_CHAMP,
        "pts_playoff": (
            summary["playoff_game_wins"] * BASE_PLAYOFF_WIN + _series_points(summary)
        ),
    })
    out["total_points"] = out[[c for c in out.columns if c.startswith("pts_")]].sum(axis=1)
    out["league"] = "MLB"
    return out.sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True)


#: Window components that grow with games played, and so scale to a full
#: season. The others -- a division title, a playoff run -- happen once.
WINDOW_COUNTING = ("pts_reg_wins", "pts_big_wins", "pts_shutouts", "pts_run_diff")


def _series_points(summary: pd.DataFrame) -> pd.Series:
    return (
        summary["series_wc_or_bye"] * PTS_SERIES["wc"]
        + summary["series_lds"] * PTS_SERIES["lds"]
        + summary["series_lcs"] * PTS_SERIES["lcs"]
        + summary["series_ws"] * PTS_SERIES["ws"]
    )


def score_teams(schedule: pd.DataFrame, partial: bool = False) -> pd.DataFrame:
    """Rolling twelve-month contract points per team.

    Each contract year pairs the post-break remainder of season N with the
    pre-break portion of season N+1, so a team only scores where both seasons are
    present in the data.

    That is right for a benchmark and wrong for a season in progress. The
    2026-27 contract year is post-break 2026 plus pre-break 2027, and pre-break
    2027 has not been played -- so an inner join drops every team and the league
    reports "no results yet for this season" in the middle of a pennant race.
    ``partial`` keeps the half that has been played and scores it, which is what
    a live standing is: incomplete on purpose.

    Benchmarks must not pass it. A pool of half-finished contract years would
    set the bar at roughly half a year's points, and every live team would score
    about double.
    """
    summary = summarize_teams(schedule)
    if summary.empty:
        return pd.DataFrame({
            c: pd.Series(dtype="object")
            for c in ("contract_year", "team", "year_n_points", "year_n1_points",
                      "total_points", "season", "league")
        })

    year_n = pd.DataFrame({
        "contract_year": summary["season"],
        "team": summary["team"],
        "year_n_points": (
            summary["reg_wins"] * SHARE_POST_ASB * BASE_REG_WIN * MULT_YEAR_N
            + summary["reg_big_wins"] * SHARE_POST_ASB * PTS_BIG_WIN * MULT_YEAR_N
            + summary["shutouts"] * SHARE_POST_ASB * PTS_SHUTOUT * MULT_YEAR_N
            + summary["run_diff"] * SHARE_POST_ASB * PTS_RUN_DIFF * MULT_YEAR_N
            + summary["is_division_champ"] * PTS_DIV_CHAMP * MULT_YEAR_N
            + summary["playoff_game_wins"] * BASE_PLAYOFF_WIN * MULT_YEAR_N
            + _series_points(summary)
        ),
    })

    year_n1 = pd.DataFrame({
        "contract_year": summary["season"] - 1,
        "team": summary["team"],
        "year_n1_points": (
            summary["reg_wins"] * SHARE_PRE_ASB * BASE_REG_WIN * MULT_YEAR_N1
            + summary["reg_big_wins"] * SHARE_PRE_ASB * PTS_BIG_WIN * MULT_YEAR_N1
            + summary["shutouts"] * SHARE_PRE_ASB * PTS_SHUTOUT * MULT_YEAR_N1
            + summary["run_diff"] * SHARE_PRE_ASB * PTS_RUN_DIFF * MULT_YEAR_N1
        ),
    })

    if partial:
        return _window_points(summary)

    out = year_n.merge(year_n1, on=["contract_year", "team"], how="inner")
    out["year_n1_points"] = out["year_n1_points"].fillna(0.0)
    out["total_points"] = out["year_n_points"] + out["year_n1_points"]
    out["season"] = out["contract_year"]
    out["league"] = "MLB"
    return out.sort_values(["season", "total_points"], ascending=[True, False]).reset_index(
        drop=True
    )
