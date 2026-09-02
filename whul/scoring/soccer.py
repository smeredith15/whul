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
#: Appearance points are **per game**: 2 for playing 60 minutes or more in a
#: match, 1 for a shorter appearance. The R script tested *season-total* minutes
#: against 60, which awarded 2 points for an entire year -- a per-game rule
#: applied to aggregate data.
PTS_FULL_APPEARANCE = 2
PTS_SHORT_APPEARANCE = 1
FULL_APPEARANCE_MINUTES = 60

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


def appearance_points_from_matches(minutes: pd.Series) -> pd.Series:
    """Exact appearance points, given one row per player per match.

    The rule as written: 60 minutes or more is a full appearance, anything less
    is a short one. Use this wherever per-match minutes are available.
    """
    played = pd.to_numeric(minutes, errors="coerce").fillna(0.0)
    return played.where(played <= 0, 0).mask(
        played >= FULL_APPEARANCE_MINUTES, PTS_FULL_APPEARANCE
    ).mask(
        (played > 0) & (played < FULL_APPEARANCE_MINUTES), PTS_SHORT_APPEARANCE
    )


def appearance_points_from_season(starts: pd.Series, matches: pd.Series) -> pd.Series:
    """Appearance points approximated from season aggregates.

    Starts stand in for full appearances and substitute outings for short ones.
    That is very close but not exact: a starter withdrawn at 50 minutes earns 2
    here and 1 under the true rule, and a substitute who plays 45 earns 1 here
    and 2. Season feeds carry only totals, so this is the best available from
    them -- prefer ``appearance_points_from_matches`` where per-match minutes
    exist.
    """
    starts = pd.to_numeric(starts, errors="coerce").fillna(0.0)
    matches = pd.to_numeric(matches, errors="coerce").fillna(0.0)
    substitute = (matches - starts).clip(lower=0)
    return starts * PTS_FULL_APPEARANCE + substitute * PTS_SHORT_APPEARANCE


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


def score_players(players: pd.DataFrame) -> pd.DataFrame:
    """Season totals per player.

    Appearance points are per game. Where the input carries per-match minutes in
    a ``match_minutes`` column they are used exactly; otherwise starts and
    substitute outings approximate them from season aggregates.
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

    if "match_minutes" in players.columns:
        work["appearance_points"] = appearance_points_from_matches(players["match_minutes"])
    else:
        work["appearance_points"] = appearance_points_from_season(
            work["starts"], work["matches"]
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
