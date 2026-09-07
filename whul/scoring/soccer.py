"""Club soccer scoring -- port of Club_Soccer.R.

Teams score for how a match ended -- a win, a shootout win, a draw or a shootout
loss are worth 3, 2, 1 and 1 on the league scale, and a loss nothing -- times
what the competition it happened in is worth (see ``whul.scoring.competition``).
Conceding nothing earns a point however the match ended, so a goalless draw is
worth more than a 1-1. Winning by two goals or more earns another, which only a
win can: a drawn match has no margin to be big.

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
from whul.scoring.competition import (
    Outcome, Tier, bye_credit, classify, classify_key, outcome_points,
    uefa_entry_points,
)

# --- teams ----------------------------------------------------------------
BIG_MARGIN = 2
PTS_BIG_MARGIN = 1
PTS_CLEAN_SHEET = 1

#: How long a word must be before it counts as identifying a club. Five, so
#: "real" does not make Real Betis look like Real Madrid, while "inter" still
#: finds Internazionale behind "Inter Milan".
DISTINCT = 5

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


def _outcome(margin: float, shootout_for: float, shootout_against: float) -> str:
    """How the match ended for this side.

    A shootout is only ever consulted on a level score, which is the only way
    one can happen. That ordering also makes a feed that folds the shootout
    into the score harmless to the *decided* matches: it can misread a tie, but
    it cannot turn a 2-0 into anything else.
    """
    if margin > 0:
        return Outcome.WIN.value
    if margin < 0:
        return Outcome.LOSS.value
    if shootout_for > shootout_against:
        return Outcome.SHOOTOUT_WIN.value
    if shootout_for < shootout_against:
        return Outcome.SHOOTOUT_LOSS.value
    return Outcome.DRAW.value


def score_team_matches(matches: pd.DataFrame) -> pd.DataFrame:
    """Per-match team points.

    Expects one row per team per match: ``team``, ``league``, ``date``,
    ``competition``, ``goals_for``, ``goals_against``, and optionally
    ``shootout_for`` and ``shootout_against`` for a tie decided on penalties.
    """
    if matches is None or matches.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "team": resolve_str(matches, ["team"], required=True),
            "league": resolve_str(matches, ["league", "primary_league"], required=True),
            "date": resolve_str(matches, ["date", "game_date"], required=True),
            "competition": resolve_str(matches, ["competition", "comp"]).fillna(""),
            "competition_key": resolve_str(matches, ["competition_key"]).fillna(""),
            # Required, now that a level score is worth something. Defaulted,
            # a feed that renamed its goals column would report every match as
            # 0-0 -- which used to mean every club scored nothing, obvious at a
            # glance, and now means every club is paid for a season of draws.
            "goals_for": resolve_num(matches, ["goals_for", "gf"], required=True),
            "goals_against": resolve_num(
                matches, ["goals_against", "ga"], required=True),
            # Absent for all but a handful of cup ties, and absent entirely
            # from a feed that does not report one. Zero on both sides is the
            # right reading of "no shootout": a shootout nobody scored in does
            # not exist, so it cannot be confused with one.
            "shootout_for": resolve_num(
                matches, ["shootout_for", "penalties_for", "so_for"]),
            "shootout_against": resolve_num(
                matches, ["shootout_against", "penalties_against", "so_against"]),
        }
    )
    # Prefer the feed's own key: we chose it when making the request, so unlike a
    # display name it cannot arrive missing or worded unexpectedly.
    classified = [
        classify_key(key, label) if key else classify(label)
        for key, label in zip(work["competition_key"], work["competition"])
    ]
    work["tier"] = [c.tier.value for c in classified]
    work["counts"] = [c.counts for c in classified]
    # Qualifying rounds are dropped outright: they are neither scored nor
    # allowed to pad a team's match count.
    work = work[work["counts"]].copy()
    if work.empty:
        return pd.DataFrame()

    work["season"] = season_for(work["date"], work["league"])
    work["margin"] = work["goals_for"] - work["goals_against"]
    work["outcome"] = [
        _outcome(margin, so_for, so_against)
        for margin, so_for, so_against in zip(
            work["margin"], work["shootout_for"], work["shootout_against"]
        )
    ]
    # A regulation win, which is what the two bonuses are gated on. A shootout
    # win is deliberately not one: the match itself was drawn, so there is no
    # margin to be big and no clean sheet to keep.
    work["is_win"] = work["outcome"] == Outcome.WIN.value
    work["base_points"] = [
        (classify_key(key, label) if key else classify(label)).win_points
        for key, label in zip(work["competition_key"], work["competition"])
    ]

    work["outcome_points"] = [
        outcome_points(outcome, base)
        for outcome, base in zip(work["outcome"], work["base_points"])
    ]
    # A clean sheet is conceding nothing, whatever the match ended as. Only the
    # margin bonus is a win's alone -- a drawn match has no margin to be big.
    # Nothing is given away by not gating this on the result: a side that
    # conceded nothing cannot have lost in normal time, so the bonus reaches
    # exactly wins to nil and goalless draws, penalties or no penalties.
    work["clean_sheet"] = work["goals_against"] == 0
    work["match_points"] = (
        work["outcome_points"]
        + (work["is_win"] & (work["margin"] >= BIG_MARGIN)) * PTS_BIG_MARGIN
        + work["clean_sheet"] * PTS_CLEAN_SHEET
    )
    return work


def score_teams(
    matches: pd.DataFrame,
    byes: pd.DataFrame | None = None,
    uefa_entry: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Season totals per club.

    ``byes`` credits rounds a team skipped by finishing high enough to earn one,
    scored as a sweep. Expects ``team``, ``season``, ``tier`` and optionally
    ``legs``; without it a bye is indistinguishable from an early exit.

    ``uefa_entry`` credits a place in Europe earned by the season's league
    finish -- ``team``, ``season``, ``competition``, ``entry_round``. Nothing in
    a club's own results says it earned one, so without this the biggest
    outcome of a domestic season short of the title is worth nothing.
    """
    scored = score_team_matches(matches)
    if scored.empty:
        return pd.DataFrame()

    # The components, not only the sum. A club with two wins and eleven points
    # has not won twice in its league -- the league pays three a win and five
    # at most with both bonuses -- but the total alone cannot say that, and a
    # reader looking at the profile has no way to reach the same number. It is
    # also what makes the arithmetic checkable at all: a competition the
    # classifier could not place falls through to the league and pays three
    # instead of five, and that is invisible in a total.
    scored = scored.copy()
    scored["big_margin"] = scored["is_win"] & (scored["margin"] >= BIG_MARGIN)
    # One count and one points column per ending, so a total can be rebuilt
    # from the profile. A draw is worth a third of a win, so a club with no
    # wins is no longer a club with no points, and a total read on its own can
    # no longer be bounded by the win count alone.
    for outcome in Outcome:
        scored[outcome.value] = scored["outcome"] == outcome.value
        scored[f"pts_{outcome.value}"] = (
            scored[outcome.value] * scored["outcome_points"]
        )

    totals = scored.groupby(["league", "team", "season"], as_index=False).agg(
        matches_played=("match_points", "size"),
        wins=("win", "sum"),
        shootout_wins=("shootout_win", "sum"),
        draws=("draw", "sum"),
        shootout_losses=("shootout_loss", "sum"),
        losses=("loss", "sum"),
        pts_wins=("pts_win", "sum"),
        pts_shootout_wins=("pts_shootout_win", "sum"),
        pts_draws=("pts_draw", "sum"),
        pts_shootout_losses=("pts_shootout_loss", "sum"),
        big_margins=("big_margin", "sum"),
        clean_sheets=("clean_sheet", "sum"),
        total_points=("match_points", "sum"),
    )
    totals["pts_big_margin"] = totals["big_margins"] * PTS_BIG_MARGIN
    totals["pts_clean_sheet"] = totals["clean_sheets"] * PTS_CLEAN_SHEET

    # Wins by where they happened, so the tier premium is visible rather than
    # folded into one figure.
    for tier in Tier:
        if tier is Tier.QUALIFYING:
            continue
        column = f"wins_{tier.value}"
        won_here = scored["is_win"] & (scored["tier"] == tier.value)
        by_club = scored.assign(_w=won_here).groupby(
            ["league", "team", "season"], as_index=False
        )["_w"].sum().rename(columns={"_w": column})
        totals = totals.merge(by_club, on=["league", "team", "season"], how="left")
        totals[column] = totals[column].fillna(0).astype(int)

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

    totals = _with_uefa_entry(totals, uefa_entry)

    return totals.sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True)


#: Wikipedia and the match feed do not always share a word, let alone a
#: spelling. Only pairs that no rule can reach belong here -- every entry is a
#: decision someone has to keep true, so the list should stay short.
UEFA_NAME_ALIASES = {
    # No word in common at all, in either direction.
    "inter milan": "internazionale",
}

#: Words too common to identify a club on their own. Used only when reporting a
#: near miss, never when matching: "Dundee United" against "Manchester United"
#: and "Racing Union" against "Union Berlin" are noise, and a report full of
#: noise is a report nobody reads.
COMMON_CLUB_WORDS = {
    "united", "union", "city", "town", "club", "athletic", "atletico",
    "racing", "dynamo", "dinamo", "sporting", "real", "saints", "rovers",
    "wanderers", "olympique", "borussia",
}


def _compare_key(name: str) -> str:
    """A club name reduced to the words that identify it.

    Bare numerals go, because they are a naming convention rather than an
    identity: the feed's "1. FC Union Berlin" and Wikipedia's "Union Berlin"
    are one club, as are "Mainz 05" and "Mainz". A numeral never distinguishes
    two clubs in the same league.
    """
    from whul.resolve import normalize_team

    words = [w for w in normalize_team(name).split() if not w.isdigit()]
    return " ".join(words) or normalize_team(name)


def _find_club(name: str, ours: dict[str, str]) -> str | None:
    """The club in ``ours`` that this entrant is, if it can be told safely.

    Three rules, in order of how much they assume:

    1. The reduced names agree.
    2. One name's words are all in the other's *and they start with the same
       word*, which is what separates "West Ham United" from "West Ham" and
       "Athletic Bilbao" from "Athletic Club".
    3. A recorded alias, for pairs no rule can reach.

    The first-word condition in (2) is the guard that matters. Without it,
    "Inter Milan" contains every word of "Milan" and would be scored as AC
    Milan -- twelve points to the wrong club, which is worse than none to the
    right one. A match must also be unique: two candidates is not an answer.
    """
    key = _compare_key(name)
    if key in ours:
        return ours[key]

    words = key.split()
    candidates = []
    for other, full in ours.items():
        theirs = other.split()
        if not words or not theirs or words[0] != theirs[0]:
            continue
        if set(words) <= set(theirs) or set(theirs) <= set(words):
            candidates.append(full)
    if len(candidates) == 1:
        return candidates[0]

    aliased = UEFA_NAME_ALIASES.get(key)
    return ours.get(aliased) if aliased else None


def _with_uefa_entry(
    totals: pd.DataFrame, entry: pd.DataFrame | None
) -> pd.DataFrame:
    """Add the points for a place in Europe earned by this season's finish.

    Matched on a reduced name rather than the feed's exact string, because the
    participant list and the match feed spell clubs differently -- and a name
    that fails to match costs the club up to twelve points while reading as
    nothing at all. ``unmatched_uefa_entry`` is what names those.
    """
    totals = totals.copy()
    totals["uefa_entry"] = ""
    totals["pts_uefa_entry"] = 0.0
    if entry is None or entry.empty:
        totals["total_points"] = totals["total_points"] + totals["pts_uefa_entry"]
        return totals

    by_season: dict[int, dict[str, str]] = {}
    for row in totals.itertuples():
        by_season.setdefault(int(row.season), {})[_compare_key(str(row.team))] = \
            str(row.team)

    wanted: dict[tuple[str, int], tuple[str, str]] = {}
    for row in entry.itertuples():
        season = int(row.season)
        club = _find_club(str(row.team), by_season.get(season, {}))
        if club is not None:
            wanted[(club, season)] = (str(row.competition), str(row.entry_round))

    labels, points = [], []
    for row in totals.itertuples():
        found = wanted.get((str(row.team), int(row.season)))
        if found is None:
            labels.append("")
            points.append(0.0)
            continue
        competition, entry_round = found
        labels.append(f"{competition} -- {entry_round}")
        points.append(uefa_entry_points(competition, entry_round))
    totals["uefa_entry"] = labels
    totals["pts_uefa_entry"] = points
    totals["total_points"] = totals["total_points"] + totals["pts_uefa_entry"]
    return totals


def unmatched_uefa_entry(
    totals: pd.DataFrame, entry: pd.DataFrame | None
) -> list[tuple[str, int, str]]:
    """Entrants that look like one of our clubs but matched none of them.

    Returns the club it came nearest to as well as its own name, because a
    report that only says "Aston Villa did not match" invites the reader to
    hunt for a Villa that is not there. Saying "nearest: Villarreal" makes a
    false alarm obvious at a glance, and a real miss equally so.

    The participant list holds every club in Europe, most of which have nothing
    to do with the five leagues scored here, so an unmatched name is usually
    correct. Only names sharing a distinctive word with a club in the frame are
    returned -- and words like "United" and "Union" are not distinctive.
    """
    if entry is None or entry.empty or totals is None or totals.empty:
        return []

    ours: dict[int, dict[str, str]] = {}
    for row in totals.itertuples():
        ours.setdefault(int(row.season), {})[_compare_key(str(row.team))] = \
            str(row.team)

    def nearest(name: str, pool: dict[str, str]) -> str | None:
        words = [w for w in _compare_key(name).split()
                 if len(w) >= DISTINCT and w not in COMMON_CLUB_WORDS]
        for other, full in pool.items():
            for theirs in other.split():
                if theirs in COMMON_CLUB_WORDS or len(theirs) < DISTINCT:
                    continue
                if any(w == theirs or w.startswith(theirs) or theirs.startswith(w)
                       for w in words):
                    return full
        return None

    missed = []
    for row in entry.itertuples():
        season = int(row.season)
        pool = ours.get(season, {})
        if _find_club(str(row.team), pool) is not None:
            continue
        close = nearest(str(row.team), pool)
        if close:
            missed.append((str(row.team), season, close))
    return sorted(set(missed))


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
