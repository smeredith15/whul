"""International football scoring.

A club plays thirty-eight league matches a season; a national team plays a
handful of tournaments across a four-year cycle, and which tournaments depends
on where in that cycle the year falls. So the club scorer's shape does not
transfer, and this one is built to a design the league admin settled:

1. **A competition is its qualifying and its finals together**, at one of three
   rungs. World Cup qualifying is therefore on the World Cup rung and worth
   more than Euro qualifying, and a team that fails to qualify has still spent
   its year on the World Cup rung -- which is what stops "miss the World Cup,
   get the rest of your year upscaled" from being a strategy.

2. **A match is scored exactly as a club match is** -- the same outcome table
   and the same two bonuses -- then multiplied by its stage and its rung.

3. **A competition pays a ceiling, divided by the champion's own path.** So
   winning the Gold Cup in six matches and AFCON in seven are worth the same,
   and the 2026 World Cup's new Round of 32 changes nothing about what a World
   Cup is worth. This is the tennis tier model in `whul.scoring.competition`,
   applied to tournaments instead of tours.

4. **A season is its best competition whole plus half of everything else** --
   the two-way rule `whul.scoring.mlb` uses for a player who bats and pitches.
   Summing them put the United States' 2018-19, when they won the World Cup and
   the championship that qualified them for it, at twice the 99th percentile.

5. **A fallow year is lifted** so the best rung the team actually played
   reaches a full ceiling. Without it a European team's Nations League year
   scores half its World Cup year for reasons of the calendar alone, and the
   category goes quiet two years in three.

See docs/INTL_SOCCER.md for how each of those was arrived at and what it does
to real seasons.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from whul.scoring.competition import LEAGUE_WIN, Outcome, outcome_points
from whul.scoring.soccer import BIG_MARGIN, PTS_BIG_MARGIN, PTS_CLEAN_SHEET

EDITIONS = Path(__file__).resolve().parent.parent / "data" / "intl_editions.csv"

#: What a perfect run in a competition is worth, by rung. Deliberately shallow
#: -- 2 : 1.5 : 1 rather than 3 : 2 : 1 -- because a steeper ladder let one
#: two-trophy year outscore two very good ones put together.
RUNG = {"nations_league": 1.0, "federation": 1.5, "world": 2.0}

#: What a match is worth by the stage it is played at.
STAGE = {"qualifying": 1.0, "group": 2.0, "knockout": 3.0}

#: Every competition after the best one in a season contributes half.
BEYOND_BEST_SHARE = 0.5

#: What one match can be worth at most: won by two or more, to nil. The
#: denominator is built from this rather than from a bare win, so no run can
#: exceed its own competition and a rung stays a rung.
MATCH_MAX = LEAGUE_WIN + PTS_BIG_MARGIN + PTS_CLEAN_SHEET

#: League points are quoted per hundred so they read like the rest of the
#: project. The figure is arbitrary: the 0-100 scale comes from dividing by the
#: pool's 99th percentile afterwards, which is why a perfect run lands above
#: 100 rather than exactly on it.
SCALE = 100.0

LEAGUES = {"M": "Men's Intl Soccer", "W": "Women's Intl Soccer"}


def score_teams(matches: pd.DataFrame) -> pd.DataFrame:
    """Season totals per national team, from classified match rows."""
    if matches is None or matches.empty:
        return pd.DataFrame()
    rows = per_team(matches)
    if rows.empty:
        return pd.DataFrame()
    rows["edition"] = _editions(rows)
    rows = _price(rows, _shape(rows))
    scored = _fold(rows)
    # Seasons outside the ask were carried this far only so each tournament's
    # shape could be read off an edition that was played. See `load_matches`.
    if "wanted" in matches.columns:
        keep = set(matches.loc[matches["wanted"].astype(bool), "season"])
        scored = scored[scored["season"].isin(keep)].reset_index(drop=True)
    return scored


def per_team(matches: pd.DataFrame) -> pd.DataFrame:
    """One row per team per match, priced on the club soccer scale.

    The same outcome table and the same two bonuses a club gets, so a national
    team's 2-0 and a club's 2-0 are worth the same before the tournament ladder
    touches them. A shootout is only consulted on a level score, which is the
    only way one can happen.
    """
    sides = []
    for side, other in (("home", "away"), ("away", "home")):
        mine = pd.to_numeric(matches[f"{side}_score"], errors="coerce")
        theirs = pd.to_numeric(matches[f"{other}_score"], errors="coerce")
        team = matches[f"{side}_team"].astype(str)
        won, drew = mine > theirs, mine == theirs
        shootout = matches.get("shootout_winner")
        shootout = pd.Series([None] * len(matches), index=matches.index) \
            if shootout is None else shootout
        won_shootout = drew & (shootout.astype(str) == team)
        lost_shootout = drew & shootout.notna() & ~won_shootout

        ending = pd.Series(Outcome.LOSS.value, index=matches.index)
        ending[won] = Outcome.WIN.value
        ending[drew] = Outcome.DRAW.value
        ending[lost_shootout] = Outcome.SHOOTOUT_LOSS.value
        ending[won_shootout] = Outcome.SHOOTOUT_WIN.value
        base = ending.map(lambda o: outcome_points(o, LEAGUE_WIN))
        # A shootout win takes no margin bonus: the match itself was drawn, so
        # there is no margin to be big. The clean sheet is not gated on the
        # result -- a side that conceded nothing cannot have lost in normal
        # time, so it reaches exactly wins to nil and goalless draws.
        base = base + (won & (mine - theirs >= BIG_MARGIN)) * PTS_BIG_MARGIN
        base = base + (theirs == 0) * PTS_CLEAN_SHEET

        sides.append(pd.DataFrame({
            "date": matches["date"], "season": matches["season"],
            "gender": matches["gender"], "team": team,
            "competition": matches["competition"], "rung": matches["rung"],
            "kind": matches["kind"], "outcome": ending, "base": base,
        }))
    rows = pd.concat(sides, ignore_index=True)
    return rows[rows["base"].notna()].reset_index(drop=True)


def _editions(rows: pd.DataFrame) -> pd.Series:
    """Which staging of a competition a match belongs to.

    The league year names it. A qualifying campaign runs for two or three years
    and belongs to the finals it feeds, so it takes the next finals year --
    which is what makes a qualifying match part of the World Cup rather than an
    event of its own. Where those finals have not been played yet, the
    competition's own cadence names the staging ahead; falling back to "the last
    one plus a year" filed the 2027 World Cup qualifiers against the 2023
    tournament, which had already been scored.
    """
    edition = rows["season"].astype(int).copy()
    finals = rows[rows["kind"] == "finals"]
    for (gender, competition), block in finals.groupby(["gender", "competition"]):
        years = sorted(block["season"].unique())
        mask = (rows["gender"] == gender) & (rows["competition"] == competition) \
            & (rows["kind"] == "qualifying")
        if not mask.any():
            continue
        gaps = pd.Series(years).diff().dropna()
        ahead = max(years) + max(int(gaps.median()) if len(gaps) else 2, 1)
        edition.loc[mask] = rows.loc[mask, "season"].map(
            lambda y: next((f for f in years if f >= y), ahead)
        )
    return edition


def _shape(rows: pd.DataFrame) -> pd.DataFrame:
    """Group and knockout matches per edition.

    ``G`` is the fewest matches any team played -- a side eliminated in the
    group stage plays exactly the group -- and ``K`` is what the longest run
    adds, which is the champion's knockout path.

    An edition whose finals have not been played yet, which is every qualifying
    campaign in progress, has no shape to read; and a missing shape is not a
    neutral zero. It would leave the denominator as the team's own qualifiers,
    so winning both legs of a two-match preliminary tie would pay a full World
    Cup ceiling. The format is the most stable thing about a competition, so
    the last edition played supplies it.
    """
    finals = rows[rows["kind"] == "finals"]
    known = []
    for key, block in finals.groupby(["gender", "competition", "edition"]):
        counts = block.groupby("team").size()
        group = int(counts.min())
        known.append({
            "gender": key[0], "competition": key[1], "edition": key[2],
            "G": group, "K": max(int(counts.max()) - group, 0),
        })
    shape = pd.DataFrame(known, columns=["gender", "competition", "edition", "G", "K"])

    every = rows[["gender", "competition", "edition"]].drop_duplicates()
    shape = every.merge(shape, on=["gender", "competition", "edition"], how="left")
    shape = shape.sort_values(["gender", "competition", "edition"])
    for column in ("G", "K"):
        shape[column] = shape.groupby(["gender", "competition"])[column].ffill().bfill()
    return _stated(shape.dropna(subset=["G", "K"]))


def _stated(shape: pd.DataFrame) -> pd.DataFrame:
    """Replace an inferred shape where the ledger cannot supply one.

    Only the Nations Leagues, for two reasons that compound: the ledger does
    not record whether a match is League A, B or C, so the smallest division
    sets ``G`` and the largest sets ``K``; and an edition does not fit a league
    year predictably, the 2020-21 Finals having been played eleven months after
    their league phase. ``whul/data/intl_editions.csv`` states both, and says
    at length why.
    """
    if not EDITIONS.exists():
        return shape
    given = pd.read_csv(EDITIONS, comment="#")
    merged = shape.merge(given, on=["gender", "competition", "edition"], how="left")
    stated = merged["group"].notna()
    merged.loc[stated, "G"] = merged.loc[stated, "group"]
    merged.loc[stated, "K"] = merged.loc[stated, "knockout"]

    # No warning for a stated edition that matches nothing. It sounds like the
    # right guard and is not: what it matched against is whatever seasons were
    # pulled, so it fired on every ordinary run and said nothing true. The
    # check belongs against the ladder rather than against the data, where it
    # cannot be fooled by a quiet year -- see
    # tests/test_intl_soccer.py::test_every_stated_edition_names_a_competition_the_ladder_knows.
    return merged.drop(columns=[c for c in ("group", "knockout", "source", "note")
                                if c in merged.columns])


def _price(rows: pd.DataFrame, shape: pd.DataFrame) -> pd.DataFrame:
    """Each match's share of its competition's ceiling."""
    rows = rows.merge(shape, on=["gender", "competition", "edition"], how="left")
    rows["G"] = rows["G"].fillna(0).astype(int)
    rows["K"] = rows["K"].fillna(0).astype(int)

    # Within a finals tournament a team's first G matches are its group and the
    # rest are knockouts. Taking the first three, as the R script did, scored
    # the Nations League's fourth, fifth and sixth league matches as knockout
    # football and five of the CONMEBOL Women's eight.
    rows = rows.sort_values("date")
    order = rows.groupby(["gender", "competition", "edition", "team", "kind"]).cumcount()
    rows["stage"] = "qualifying"
    finals = rows["kind"] == "finals"
    rows.loc[finals, "stage"] = [
        "group" if o < g else "knockout"
        for o, g in zip(order[finals], rows.loc[finals, "G"])
    ]
    rows["units"] = rows["base"] * rows["stage"].map(STAGE)

    # The denominator is the champion's whole path: their qualifying, their
    # group, their knockouts. Qualifying length is the team's own, because a
    # CONMEBOL campaign is eighteen matches and a CAF one is six and both are
    # the same achievement.
    quals = rows[rows["kind"] == "qualifying"].groupby(
        ["gender", "competition", "edition", "team"]).size().rename("Q")
    rows = rows.merge(quals, on=["gender", "competition", "edition", "team"], how="left")
    rows["Q"] = rows["Q"].fillna(0).astype(int)
    rows["path_max"] = MATCH_MAX * (
        rows["Q"] * STAGE["qualifying"]
        + rows["G"] * STAGE["group"]
        + rows["K"] * STAGE["knockout"]
    )
    rows["ceiling"] = rows["rung"].map(RUNG) * SCALE
    rows["points"] = rows["ceiling"] * rows["units"] / rows["path_max"].where(
        rows["path_max"] > 0)
    return rows[rows["points"].notna()]


def _fold(rows: pd.DataFrame) -> pd.DataFrame:
    """Team-seasons: the best competition whole, everything else at half,
    then lifted so the year's best rung reaches a full ceiling."""
    per_comp = rows.groupby(
        ["gender", "team", "season", "competition"], as_index=False
    ).agg(points=("points", "sum"), ceiling=("ceiling", "max"),
          matches=("points", "size"),
          wins=("outcome", lambda s: int((s == Outcome.WIN.value).sum())))

    ranked = per_comp.sort_values("points", ascending=False)
    share = pd.Series(BEYOND_BEST_SHARE, index=ranked.index)
    share[ranked.groupby(["gender", "team", "season"]).cumcount() == 0] = 1.0
    ranked["folded"] = ranked["points"] * share

    out = ranked.groupby(["gender", "team", "season"], as_index=False).agg(
        folded=("folded", "sum"), gross=("points", "sum"),
        matches=("matches", "sum"), wins=("wins", "sum"),
        competitions=("competition", "size"), top_rung=("ceiling", "max"),
    )
    out["lift"] = max(RUNG.values()) * SCALE / out["top_rung"]
    out["total_points"] = out["folded"] * out["lift"]
    out["league"] = out["gender"].map(LEAGUES)
    return out.drop(columns="gender").sort_values(
        ["season", "total_points"], ascending=[True, False]).reset_index(drop=True)
