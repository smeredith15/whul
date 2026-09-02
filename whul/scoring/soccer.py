"""Club soccer scoring -- port of Club_Soccer.R.

Teams score only for winning, at a value that depends on where the win happened
(see ``whul.scoring.competition``), plus a point for a two-goal margin and a
point for a clean sheet.

Players score appearance points, goals weighted by position, assists, and card
penalties.

**Each league normalizes against itself.** Premier League players are measured
against the Premier League, not against a pooled European field.

Season years roll in August for European leagues -- a match in September 2026
belongs to 2026-27 -- while MLS and NWSL run within a calendar year, so their
season is the year the match was played in. MLS moves to a fall-spring calendar
in 2027, at which point it joins the European convention.
"""

from __future__ import annotations

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str
from whul.scoring.competition import Tier, bye_credit, classify

# --- teams ----------------------------------------------------------------
BIG_MARGIN = 2
PTS_BIG_MARGIN = 1
PTS_CLEAN_SHEET = 1

# --- players --------------------------------------------------------------
#: Appearance points, as the R script intends rather than as it computes: the
#: script tests season-total minutes against 60, which awards 2 points for an
#: entire season. Applied per appearance -- 2 for a start, 1 off the bench --
#: the term is meaningful and matches the usual fantasy-soccer convention.
PTS_START = 2
PTS_SUBSTITUTE = 1
START_MINUTES = 60

#: Goals are worth more the further back the scorer plays.
GOAL_POINTS_BY_POSITION = {"defender": 6, "midfielder": 5, "forward": 4}
PTS_ASSIST = 3
PTS_YELLOW = -1
PTS_RED = -3

DEFENDER_CODES = ("DF", "CB", "RB", "LB", "GK", "D", "G")
MIDFIELD_CODES = ("MF", "CM", "CD", "LM", "RM", "DM", "AM", "M")

#: Calendar-year leagues. Everything else rolls its season in August.
CALENDAR_YEAR_LEAGUES = ("MLS", "NWSL")
SEASON_ROLLS_AFTER_MONTH = 7


def season_for(match_date: pd.Series, league: pd.Series) -> pd.Series:
    """Which league year a match belongs to."""
    dates = pd.to_datetime(match_date, errors="coerce")
    calendar = league.astype(str).str.upper().isin(CALENDAR_YEAR_LEAGUES)
    rolled = dates.dt.year + (dates.dt.month > SEASON_ROLLS_AFTER_MONTH).astype(int)
    return rolled.where(~calendar, dates.dt.year)


def goal_points_for(position: str | None) -> int:
    """Goal value by position, defaulting to forward for unknown codes."""
    code = (position or "FW").strip().upper()[:2]
    if code in DEFENDER_CODES or code[:1] in ("D", "G"):
        return GOAL_POINTS_BY_POSITION["defender"]
    if code in MIDFIELD_CODES or code[:1] == "M":
        return GOAL_POINTS_BY_POSITION["midfielder"]
    return GOAL_POINTS_BY_POSITION["forward"]


def score_team_matches(matches: pd.DataFrame) -> pd.DataFrame:
    """Per-match team points.

    Expects one row per team per match: ``team``, ``league``, ``date``,
    ``competition``, ``goals_for``, ``goals_against``.
    """
    if matches is None or matches.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "team": resolve_str(matches, ["team"], required=True),
            "league": resolve_str(matches, ["league", "primary_league"], required=True),
            "date": resolve_str(matches, ["date", "game_date"], required=True),
            "competition": resolve_str(matches, ["competition", "comp"]).fillna(""),
            "goals_for": resolve_num(matches, ["goals_for", "gf"]),
            "goals_against": resolve_num(matches, ["goals_against", "ga"]),
        }
    )
    classified = work["competition"].map(classify)
    work["tier"] = [c.tier.value for c in classified]
    work["counts"] = [c.counts for c in classified]
    # Qualifying rounds are dropped outright: they are neither scored nor
    # allowed to pad a team's match count.
    work = work[work["counts"]].copy()
    if work.empty:
        return pd.DataFrame()

    work["season"] = season_for(work["date"], work["league"])
    work["margin"] = work["goals_for"] - work["goals_against"]
    work["is_win"] = work["margin"] > 0
    work["base_points"] = [c.win_points for c in work["competition"].map(classify)]

    work["match_points"] = (
        work["is_win"] * work["base_points"]
        + (work["is_win"] & (work["margin"] >= BIG_MARGIN)) * PTS_BIG_MARGIN
        + (work["is_win"] & (work["goals_against"] == 0)) * PTS_CLEAN_SHEET
    )
    return work


def score_teams(
    matches: pd.DataFrame, byes: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Season totals per club.

    ``byes`` credits rounds a team skipped by finishing high enough to earn one,
    scored as a sweep. Expects ``team``, ``season``, ``tier`` and optionally
    ``legs``; without it a bye is indistinguishable from an early exit.
    """
    scored = score_team_matches(matches)
    if scored.empty:
        return pd.DataFrame()

    totals = scored.groupby(["league", "team", "season"], as_index=False).agg(
        matches_played=("match_points", "size"),
        wins=("is_win", "sum"),
        total_points=("match_points", "sum"),
    )

    if byes is not None and not byes.empty:
        credit = byes.copy()
        credit["legs"] = credit.get("legs", pd.Series(2, index=credit.index)).fillna(2)
        credit["bye_points"] = [
            bye_credit(Tier(t), int(legs))
            for t, legs in zip(credit["tier"], credit["legs"])
        ]
        credit = credit.groupby(["team", "season"], as_index=False)["bye_points"].sum()
        totals = totals.merge(credit, on=["team", "season"], how="left")
        totals["bye_points"] = totals["bye_points"].fillna(0.0)
        totals["total_points"] = totals["total_points"] + totals["bye_points"]
    else:
        totals["bye_points"] = 0.0

    return totals.sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True)


def score_players(players: pd.DataFrame, per_appearance: bool = True) -> pd.DataFrame:
    """Season totals per player.

    ``per_appearance`` awards 2 points for a start and 1 for a substitute
    appearance. With it off, the R script's literal behaviour is reproduced:
    season-total minutes tested against 60, which gives every regular 2 points
    for the whole year and makes the term meaningless.
    """
    if players is None or players.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "player": resolve_str(players, ["player", "Player", "name"], required=True),
            "league": resolve_str(players, ["league", "Comp_clean", "Comp"], required=True),
            "season": resolve_num(players, ["season", "Season"], required=True).astype(int),
            "position": resolve_str(players, ["position", "Pos"], default="FW"),
            "matches": resolve_num(players, ["matches", "MP", "games"]),
            "starts": resolve_num(players, ["starts", "Starts"]),
            "minutes": resolve_num(players, ["minutes", "Min"]),
            "goals": resolve_num(players, ["goals", "Gls"]),
            "assists": resolve_num(players, ["assists", "Ast"]),
            "yellow": resolve_num(players, ["yellow", "CrdY"]),
            "red": resolve_num(players, ["red", "CrdR"]),
        }
    )

    if per_appearance:
        subs = (work["matches"] - work["starts"]).clip(lower=0)
        work["appearance_points"] = work["starts"] * PTS_START + subs * PTS_SUBSTITUTE
    else:
        work["appearance_points"] = (work["minutes"] >= START_MINUTES).map(
            {True: PTS_START, False: PTS_SUBSTITUTE}
        )

    work["goal_points"] = work["goals"] * work["position"].map(goal_points_for)
    work["total_points"] = (
        work["appearance_points"]
        + work["goal_points"]
        + work["assists"] * PTS_ASSIST
        + work["yellow"] * PTS_YELLOW
        + work["red"] * PTS_RED
    )
    return work.reset_index(drop=True)
