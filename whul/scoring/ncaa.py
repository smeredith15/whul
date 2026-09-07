"""NCAA team scoring -- ports of the five NCAA R scripts.

All five NCAA categories are **team slots only**; there are no NCAA player slots,
so nothing here needs box scores. Game results plus conference affiliation are
enough, which is why these leagues are cheap to scrape: one scoreboard request
per date rather than one per game.

Three shapes cover the five leagues:

* **Football** -- wins, blowouts, conference record and title, CFP appearance.
* **Basketball** (men's and women's, identical but for the minimum-games filter)
  -- conference tournament and March Madness on top of the regular season.
* **Diamond** (baseball and softball) -- wins, run differential, and flat
  postseason series milestones.

Conference affiliation is load-bearing in football and basketball: conference
wins are scored directly, and the regular-season title is split among
co-champions. A feed that omits conference data cannot score these leagues.

Every scorer takes an optional ``eligible`` set of team names. A scoreboard
request returns games *involving* a listed team, so the opponent may be from a
lower division -- those teams would otherwise enter the pool with one or two
games apiece and drag the benchmark down. The R scripts approximated this with a
minimum-games filter; naming the division's members is exact, and leaves genuine
short seasons intact.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str

# --- football -------------------------------------------------------------
#: A blowout is harder to achieve against a conference opponent or in the
#: postseason, where the field is stronger, so the bar is lower there.
FB_BIG_WIN_CONF = 13
FB_BIG_WIN_NONCONF = 20
FB_WEIGHTS = {
    "wins": 10.0, "big_wins": 2.0, "conf_wins": 2.0,
    "conf_title_win": 6.0, "playoff_app": 10.0, "playoff_wins": 15.0,
    "point_diff": 0.05,
}
FB_REG_CHAMP_POOL = 6.0  # split evenly among co-champions
FB_PLAYOFF_PATTERN = r"Playoff|CFP|Rose|Sugar|Orange|Cotton|Fiesta|Peach"
FB_TITLE_PATTERN = r"Championship"

# --- basketball -----------------------------------------------------------
BB_BIG_WIN_NONCONF = 25
BB_BIG_WIN_CONF = 15
BB_WEIGHTS = {
    "reg_wins": 2.0, "big_wins": 1.5, "conf_wins": 1.0,
    "conf_tourney_wins": 2.0, "conf_tourney_champ": 6.0,
    "mm_appearance": 8.0, "mm_wins": 5.0, "point_diff": 0.03,
}
BB_REG_CHAMP_POOL = 8.0
MM_PATTERN = (
    r"NCAA Tournament|March Madness|First Four|First Round|Second Round|"
    r"Sweet 16|Elite Eight|Final Four|National Championship"
)
CONF_TOURNEY_PATTERN = r"Tournament"
NOT_MM_PATTERN = r"NCAA|March Madness"
CONF_TOURNEY_TITLE_PATTERN = r"Championship|Final"

SEASON_TYPE_REGULAR = 2
SEASON_TYPE_POST = 3

# --- diamond --------------------------------------------------------------
DIAMOND_REG_WIN = 2.0
DIAMOND_RUN_DIFF = 0.05
PTS_SERIES_REGIONAL = 5.0
PTS_SERIES_SUPER = 6.0
PTS_SERIES_CWS = 8.0
REGIONAL_PATTERN = r"Regional"
SUPER_PATTERN = r"Super Regional"
CWS_PATTERN = r"College World Series|Women's College World Series|WCWS|CWS"


@dataclass(frozen=True)
class DiamondRules:
    """Baseball and softball differ only in the College World Series threshold."""

    league: str
    cws_wins_for_title: int


DIAMOND = {
    "NCAA Baseball": DiamondRules("NCAA Baseball", 4),
    "NCAA Softball": DiamondRules("NCAA Softball", 5),
}


def _restrict(games: pd.DataFrame, eligible: set[str] | None) -> pd.DataFrame:
    """Keep only the division's own teams, when the caller knows who they are."""
    if not eligible:
        return games
    return games[games["team"].isin(eligible)]


def _team_games(schedule: pd.DataFrame) -> pd.DataFrame:
    """One row per team per completed game, with the opponent's conference."""
    if schedule is None or schedule.empty:
        return pd.DataFrame()

    base = pd.DataFrame(
        {
            "season": resolve_num(schedule, ["season"], required=True).astype(int),
            "season_type": resolve_num(schedule, ["season_type"], default=SEASON_TYPE_REGULAR),
            "notes": resolve_str(schedule, ["notes", "notes_headline"]).fillna(""),
            "home_team": resolve_str(schedule, ["home_team"], required=True),
            "away_team": resolve_str(schedule, ["away_team"], required=True),
            "home_conference": resolve_str(schedule, ["home_conference"]),
            "away_conference": resolve_str(schedule, ["away_conference"]),
            "home_score": resolve_num(schedule, ["home_score"], default=float("nan")),
            "away_score": resolve_num(schedule, ["away_score"], default=float("nan")),
        }
    )
    if "completed" in schedule.columns:
        base = base[schedule["completed"].fillna(False).astype(bool).to_numpy()]
    base = base[base["home_score"].notna() & base["away_score"].notna()]

    sides = []
    for side, other in (("home", "away"), ("away", "home")):
        sides.append(
            pd.DataFrame(
                {
                    "season": base["season"],
                    "season_type": base["season_type"],
                    "notes": base["notes"],
                    "team": base[f"{side}_team"],
                    "opp_team": base[f"{other}_team"],
                    "conference": base[f"{side}_conference"],
                    "opp_conference": base[f"{other}_conference"],
                    "points_for": base[f"{side}_score"],
                    "points_against": base[f"{other}_score"],
                }
            )
        )
    games = pd.concat(sides, ignore_index=True)
    # Before `is_conf_game` is decided, since that is what the override is for.
    games = _apply_overrides(games)
    games["margin"] = games["points_for"] - games["points_against"]
    games["is_win"] = games["margin"] > 0
    games["is_reg"] = games["season_type"] == SEASON_TYPE_REGULAR
    games["is_post"] = games["season_type"] == SEASON_TYPE_POST
    has_both = games["conference"].notna() & games["opp_conference"].notna()
    has_both &= (games["conference"] != "") & (games["opp_conference"] != "")
    games["is_conf_game"] = has_both & (games["conference"] == games["opp_conference"])
    return games


#: Teams the league scores as a member of a conference they do not belong to.
#:
#: Notre Dame plays football as an independent. ESPN reports them that way, and
#: an independent has no conference games, so the conference-wins term is zero
#: for them no matter how the season goes -- which scores a genuinely strong
#: programme short against a field where everyone else can earn it. The league's
#: rule is to treat them as ACC, which is where their other sports play and
#: where their football scheduling agreement points.
#:
#: Keyed on the conference *value the feed uses*, resolved from the data rather
#: than written down: the id is ESPN's and could change, and a wrong one would
#: group Notre Dame with some other conference silently. `_apply_overrides`
#: takes the conference of the named exemplar wherever it appears in the same
#: frame, so the mapping is only ever as right as the feed itself.
CONFERENCE_OVERRIDES = {
    "Notre Dame Fighting Irish": "Miami Hurricanes",
}

#: ...and are never credited with winning it.
#:
#: An independent scored as a conference member could top that conference's
#: table on a technicality and take a title it is not eligible for. The league's
#: rule is explicit: treat them as ACC for scoring, award no conference title.
#: Kept as its own set rather than inferred from the override, because the two
#: are different decisions -- a future override might be a real member.
NO_CONFERENCE_TITLE = frozenset({"Notre Dame Fighting Irish"})


def _apply_overrides(games: pd.DataFrame) -> pd.DataFrame:
    """Put an independent into the conference the league scores it in.

    Both its own rows and its opponents' view of it: a conference game is a
    game whose two sides share a conference, so moving one side without the
    other would leave the ACC's games against Notre Dame counting for Notre
    Dame and not for the ACC team, which is a table that does not add up.
    """
    if games.empty or "team" not in games.columns:
        return games
    for team, exemplar in CONFERENCE_OVERRIDES.items():
        rows = games["team"] == team
        if not rows.any():
            continue
        borrowed = games.loc[games["team"] == exemplar, "conference"]
        borrowed = borrowed[borrowed.astype(str) != ""]
        if borrowed.empty:
            # Nothing to copy from, so leave it alone rather than invent a
            # conference. Scoring short is recoverable; a wrong conference is
            # a title awarded to the wrong programme.
            continue
        value = borrowed.iloc[0]
        games.loc[rows, "conference"] = value
        games.loc[games["opp_team"] == team, "opp_conference"] = value
    return games


def _matches(series: pd.Series, pattern: str) -> pd.Series:
    return series.str.contains(pattern, case=False, regex=True, na=False)


def _split_conference_title(summary: pd.DataFrame, pool: float) -> pd.Series:
    """Points for the regular-season conference title, split among co-champions.

    A shared title is worth proportionally less to each holder, as the R scripts
    have it -- two co-champions take half the pool each.
    """
    best = summary.groupby(["season", "conference"])["conf_wins"].transform("max")
    is_champ = (summary["conf_wins"] == best) & (summary["conf_wins"] > 0)
    # An independent scored as a conference member could top that conference's
    # table on a technicality and take a title it is not eligible for. Excluded
    # *after* the maximum is taken, not before: a team that is not eligible for
    # the title still played the games, and dropping it earlier would hand the
    # title to whoever finished behind it rather than leaving it unwon.
    if "team" in summary.columns:
        is_champ &= ~summary["team"].isin(NO_CONFERENCE_TITLE)
    ties = is_champ.groupby([summary["season"], summary["conference"]]).transform("sum")
    return (is_champ.astype(float) * pool / ties.where(ties > 0, 1)).fillna(0.0)


class MissingConference(ValueError):
    """Completed games arrived with no conference on any of them.

    Football scoring cannot proceed without it, and the failure has to be loud:
    silently returning nothing is indistinguishable from a week with no games,
    which is how a whole league can go unscored without anyone noticing.
    """


def score_football(
    schedule: pd.DataFrame, eligible: set[str] | None = None
) -> pd.DataFrame:
    """NCAAF team scoring."""
    games = _restrict(_team_games(schedule), eligible)
    if games.empty:
        return pd.DataFrame()

    named = games[games["conference"].notna() & (games["conference"] != "")]
    if named.empty:
        # No conference data means conference wins and the title split cannot be
        # scored at all. Returning an empty frame here reads downstream as "the
        # league has not played yet", which is the opposite of what happened --
        # it played, and the feed described it without conferences. Say so.
        raise MissingConference(
            f"{len(games)} completed game(s) arrived with no conference on any "
            "team, so conference wins and the regular-season title cannot be "
            "scored. This is a feed problem, not an empty week."
        )
    games = named
    games["is_playoff"] = games["is_post"] & _matches(games["notes"], FB_PLAYOFF_PATTERN)
    tougher_field = games["is_conf_game"] | games["is_post"]
    games["is_big_win"] = games["is_win"] & (
        (tougher_field & (games["margin"] >= FB_BIG_WIN_CONF))
        | (~tougher_field & (games["margin"] >= FB_BIG_WIN_NONCONF))
    )
    games["is_conf_title"] = games["is_post"] & _matches(games["notes"], FB_TITLE_PATTERN)

    summary = games.groupby(["season", "team", "conference"], as_index=False).apply(
        lambda g: pd.Series(
            {
                "games_played": len(g),
                "wins": int(g["is_win"].sum()),
                "big_wins": int(g["is_big_win"].sum()),
                "conf_wins": int((g["is_win"] & g["is_conf_game"]).sum()),
                "point_diff": float(g["margin"].sum()),
                "conf_title_win": int((g["is_win"] & g["is_conf_title"]).sum()),
                "playoff_app": int(g["is_playoff"].any()),
                "playoff_wins": int((g["is_win"] & g["is_playoff"]).sum()),
            }
        ),
        include_groups=False,
    ).reset_index(drop=True)
    if summary.empty:
        return summary

    summary["pts_reg_champ"] = _split_conference_title(summary, FB_REG_CHAMP_POOL)
    summary["total_points"] = (
        sum(summary[c] * w for c, w in FB_WEIGHTS.items()) + summary["pts_reg_champ"]
    )
    summary["league"] = "NCAAF"
    return summary.sort_values(["season", "total_points"], ascending=[True, False]).reset_index(
        drop=True
    )


def score_basketball(
    schedule: pd.DataFrame, league: str = "NCAAM", eligible: set[str] | None = None
) -> pd.DataFrame:
    """NCAAM and NCAAW team scoring -- the two are scored identically."""
    games = _restrict(_team_games(schedule), eligible)
    if games.empty:
        return pd.DataFrame()

    games["is_big_win"] = games["is_win"] & (
        ((~games["is_conf_game"]) & (games["margin"] >= BB_BIG_WIN_NONCONF))
        | (games["is_conf_game"] & (games["margin"] >= BB_BIG_WIN_CONF))
    )
    games["is_conf_tourney"] = (
        games["is_post"]
        & _matches(games["notes"], CONF_TOURNEY_PATTERN)
        & ~_matches(games["notes"], NOT_MM_PATTERN)
    )
    # Anything in the postseason that is not a conference tournament game is
    # treated as the national tournament, which catches rounds the notes do not
    # name explicitly.
    games["is_mm"] = _matches(games["notes"], MM_PATTERN) | (
        games["is_post"] & ~games["is_conf_tourney"]
    )
    games["is_ct_title"] = games["is_conf_tourney"] & _matches(
        games["notes"], CONF_TOURNEY_TITLE_PATTERN
    )

    summary = games.groupby(["season", "team", "conference"], as_index=False).apply(
        lambda g: pd.Series(
            {
                "games_played": len(g),
                "reg_wins": int((g["is_win"] & g["is_reg"]).sum()),
                "big_wins": int(g["is_big_win"].sum()),
                "conf_wins": int((g["is_win"] & g["is_reg"] & g["is_conf_game"]).sum()),
                "point_diff": float(g.loc[g["is_reg"], "margin"].sum()),
                "conf_tourney_wins": int((g["is_win"] & g["is_conf_tourney"]).sum()),
                "conf_tourney_champ": int((g["is_win"] & g["is_ct_title"]).any()),
                "mm_appearance": int(g["is_mm"].any()),
                "mm_wins": int((g["is_win"] & g["is_mm"]).sum()),
            }
        ),
        include_groups=False,
    ).reset_index(drop=True)
    if summary.empty:
        return summary

    summary["pts_reg_champ"] = _split_conference_title(summary, BB_REG_CHAMP_POOL)
    summary["total_points"] = (
        sum(summary[c] * w for c, w in BB_WEIGHTS.items()) + summary["pts_reg_champ"]
    )
    summary["league"] = league
    return summary.sort_values(["season", "total_points"], ascending=[True, False]).reset_index(
        drop=True
    )


def score_diamond(
    schedule: pd.DataFrame,
    league: str = "NCAA Baseball",
    eligible: set[str] | None = None,
) -> pd.DataFrame:
    """NCAA Baseball and Softball team scoring."""
    rules = DIAMOND[league]
    games = _restrict(_team_games(schedule), eligible)
    if games.empty:
        return pd.DataFrame()

    # Super Regional must be tested before Regional, since it contains the word.
    games["is_super"] = _matches(games["notes"], SUPER_PATTERN)
    games["is_cws"] = _matches(games["notes"], CWS_PATTERN)
    games["is_regional"] = (
        _matches(games["notes"], REGIONAL_PATTERN) & ~games["is_super"] & ~games["is_cws"]
    )
    games["is_postseason"] = games["is_post"] | games["is_regional"] | games["is_super"] | games["is_cws"]

    summary = games.groupby(["season", "team"], as_index=False).apply(
        lambda g: pd.Series(
            {
                "games_played": len(g),
                "reg_wins": int((g["is_win"] & ~g["is_postseason"]).sum()),
                "run_diff": float(g.loc[~g["is_postseason"], "margin"].sum()),
                "regional_wins": int((g["is_win"] & g["is_regional"]).sum()),
                "super_wins": int((g["is_win"] & g["is_super"]).sum()),
                "cws_wins": int((g["is_win"] & g["is_cws"]).sum()),
            }
        ),
        include_groups=False,
    ).reset_index(drop=True)
    if summary.empty:
        return summary

    summary["series_regional"] = (summary["regional_wins"] >= 3).astype(int)
    summary["series_super"] = (summary["super_wins"] >= 2).astype(int)
    summary["series_cws_champ"] = (summary["cws_wins"] >= rules.cws_wins_for_title).astype(int)

    summary["total_points"] = (
        summary["reg_wins"] * DIAMOND_REG_WIN
        + summary["run_diff"] * DIAMOND_RUN_DIFF
        + summary["series_regional"] * PTS_SERIES_REGIONAL
        + summary["series_super"] * PTS_SERIES_SUPER
        + summary["series_cws_champ"] * PTS_SERIES_CWS
    )
    summary["league"] = league
    return summary.sort_values(["season", "total_points"], ascending=[True, False]).reset_index(
        drop=True
    )


SCORERS = {
    "NCAAF": score_football,
    "NCAAM": lambda s, eligible=None: score_basketball(s, "NCAAM", eligible),
    "NCAAW": lambda s, eligible=None: score_basketball(s, "NCAAW", eligible),
    "NCAA Baseball": lambda s, eligible=None: score_diamond(s, "NCAA Baseball", eligible),
    "NCAA Softball": lambda s, eligible=None: score_diamond(s, "NCAA Softball", eligible),
}
