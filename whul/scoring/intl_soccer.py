"""International football scoring.

A club plays thirty-eight league matches a season; a national team plays a
handful of tournaments across a four-year cycle, and which tournaments depends
on where in that cycle the year falls. So the club scorer's shape does not
transfer, and this one is built to a design the league admin settled on
28 September 2026, after checking the top ten men's and women's teams of every
league year from 2018-19 against what the scores should say:

1. **A match is scored exactly as a club match is** -- the same outcome table
   and the same two bonuses -- then multiplied by its stage (qualifying 1,
   group 2, knockout 3).

2. **A finals tournament pays a ceiling shared along the champion's finals
   path**: World Cup 200, continental championship 100, Nations League 67. The
   World Cup is worth twice a continental title so that a World Cup semi-final
   generally beats one. The path is the group and the knockout only, so
   qualifiers played in earlier league years no longer take part of the
   champion's ceiling -- which left Argentina's 2022 World Cup the tenth-best
   men's season of its year.

3. **Qualifying is its own competition, on the Nations League rung**, whatever
   it leads to. It is measured against the team's own campaign, but never a
   shorter one than the edition's typical campaign, so a two-match play-off
   cannot pay a whole ceiling.

4. **Winning a finals tournament adds a quarter of its ceiling** -- 50 for a
   World Cup, 25 for a continental title. Not a Nations League: the ledger does
   not say which division a match was in.

5. **Prestige decides what counts whole.** A team's competitions are ranked by
   rung, then points. The first counts whole if it is on the biggest rung its
   gender plays anywhere that year, and in proportion to its rung otherwise (a
   Nations League in a continental year counts two thirds); everything else
   counts a quarter. So New Zealand's OFC title in the year of its World Cup
   group exit counts a quarter, and a team that won a whole competition at a
   lower level still has most of it.

6. **One lift per league year, for the whole category**: 200 / the biggest
   rung either gender plays that year. A World Cup year is x1, a continental
   year x2, a year of Nations Leagues and qualifying x3. Men and women are one
   asset type; only their benchmarks are separate. The year in progress also
   reads ``whul/data/intl_calendar.csv``, so the lift and the rung weights are
   right before the tournaments that set them are played.

See docs/INTL_SOCCER.md for how each of those was arrived at and what it does
to real seasons.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from whul.scoring.competition import LEAGUE_WIN, Outcome, outcome_points
from whul.scoring.soccer import BIG_MARGIN, PTS_BIG_MARGIN, PTS_CLEAN_SHEET

EDITIONS = Path(__file__).resolve().parent.parent / "data" / "intl_editions.csv"
CALENDAR = Path(__file__).resolve().parent.parent / "data" / "intl_calendar.csv"
LADDER = Path(__file__).resolve().parent.parent / "data" / "intl_tournaments.csv"

#: What a perfect run in a competition is worth, by rung, as a multiple of
#: SCALE. The World Cup is twice a continental championship; the Nations League
#: two thirds of one. Chosen against the 2018-2025 seasons: at 4:3 a continental
#: title beat every Women's World Cup semi-finalist of its year.
RUNG = {"nations_league": 2 / 3, "federation": 1.0, "world": 2.0}

#: The rung every qualifying campaign is priced on, whatever it leads to.
QUALIFYING_RUNG = "nations_league"

#: What a match is worth by the stage it is played at.
STAGE = {"qualifying": 1.0, "group": 2.0, "knockout": 3.0}

#: What every competition after a team's first contributes.
SECONDARY_SHARE = 0.25

#: What winning a finals tournament adds, as a share of its ceiling.
TITLE_BONUS = 0.25

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
    scored, shares = _fold(rows)
    # Seasons outside the ask were carried this far only so each tournament's
    # shape could be read off an edition that was played. See `load_matches`.
    if "wanted" in matches.columns:
        keep = set(matches.loc[matches["wanted"].astype(bool), "season"])
        scored = scored[scored["season"].isin(keep)].reset_index(drop=True)
    # Only for the seasons that survived. The line above narrows the score to
    # the league years asked for, and a section is read back by the season it
    # belongs to -- so building them for the whole ledger is answering about
    # 1872 to put a number on this year. It was most of the pull: 27,424 calls
    # into `_counted`, four minutes of them, for a frame that kept a few dozen
    # team-seasons and threw the rest away unread.
    #
    # Narrowed here rather than above because the shape of a tournament is
    # read off whichever edition was played, and `_editions`, `_shape`,
    # `_price` and `_fold` all need the history to do it. This is the first
    # point at which the rest has stopped being evidence.
    sections = season_sections(rows[rows["season"].isin(set(scored["season"]))],
                               shares)
    scored["sections"] = [
        sections.get((str(l), str(t), int(y)), [])
        for l, t, y in zip(scored["league"], scored["team"], scored["season"])
    ]
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
        result = ending.map(lambda o: outcome_points(o, LEAGUE_WIN))
        # A shootout win takes no margin bonus: the match itself was drawn, so
        # there is no margin to be big. The clean sheet is not gated on the
        # result -- a side that conceded nothing cannot have lost in normal
        # time, so it reaches exactly wins to nil and goalless draws.
        big = won & (mine - theirs >= BIG_MARGIN)
        clean = theirs == 0

        sides.append(pd.DataFrame({
            "date": matches["date"], "season": matches["season"],
            "gender": matches["gender"], "team": team,
            "competition": matches["competition"], "rung": matches["rung"],
            "kind": matches["kind"], "outcome": ending,
            # The three parts as well as their sum. A panel prints a count and
            # what that count was worth, and a `base` that has already added
            # them together cannot say what the clean sheets paid -- which
            # leaves a section whose boxes do not add up to it.
            "outcome_points": result,
            "big_margin": big.astype(int),
            "clean_sheet": clean.astype(int),
            "base": result + big * PTS_BIG_MARGIN + clean * PTS_CLEAN_SHEET,
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

    # An edition of the league year in progress is not read off its own
    # matches: they are the part of it played so far. On 28 September 2026 the
    # new Nations League had one or two matches a team, so it read as a
    # two-match competition and one win paid a sixth of its ceiling -- 4.8
    # times what the same win is worth in the six-match league phase and four
    # knockout rounds it actually is. The last completed edition stands in, as
    # it does for a qualifying campaign, and the stated file overrides both.
    current = rows["season"].max()
    partial = shape[shape["edition"] >= current]
    shape = shape[shape["edition"] < current]

    every = rows[["gender", "competition", "edition"]].drop_duplicates()
    shape = every.merge(shape, on=["gender", "competition", "edition"], how="left")
    # Stated shapes first, so an edition with none of its own inherits the
    # stated one. Carried forward before, a CONCACAF Nations League edition in
    # progress took 2024-25's shape as read off the ledger -- the reading the
    # stated file exists to replace -- and two League C wins led the year.
    shape = _stated(shape)
    shape = shape.sort_values(["gender", "competition", "edition"])
    by = shape.groupby(["gender", "competition"])
    for column in ("G", "K"):
        # Both directions within the competition. An ungrouped backward fill
        # reached across into the next competition's shape.
        shape[column] = by[column].ffill()
        shape[column] = shape.groupby(["gender", "competition"])[column].bfill()
    # A competition with no completed edition at all has nothing to stand in,
    # and reading its own matches is better than scoring them at nothing.
    if not partial.empty:
        keys = ["gender", "competition", "edition"]
        fill = shape[keys].merge(partial, on=keys, how="left")
        for column in ("G", "K"):
            shape[column] = shape[column].fillna(pd.Series(fill[column].values,
                                                           index=shape.index))
    return shape.dropna(subset=["G", "K"])


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
    """Each match's share of its competition's ceiling, and any title bonus."""
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

    # A qualifying campaign is measured against the team's own length --
    # CONMEBOL's eighteen matches and CAF's six are the same achievement -- but
    # never a shorter one than the edition's typical campaign. Panama's women
    # reached the 2023 World Cup through a two-match play-off, and on its own
    # length that play-off paid a whole ceiling.
    keys = ["gender", "competition", "edition"]
    quals = rows[rows["kind"] == "qualifying"].groupby(keys + ["team"]).size().rename("Q")
    typical = quals.groupby(level=keys).median().rename("Q_typical")
    rows = rows.merge(quals, on=keys + ["team"], how="left")
    rows = rows.merge(typical, on=keys, how="left")
    rows["Q"] = rows["Q"].fillna(0).astype(int)
    q_path = rows[["Q", "Q_typical"]].max(axis=1).fillna(rows["Q"])
    finals = rows["kind"] == "finals"

    # Finals are measured along the finals path only. With the qualifiers in
    # it too, a campaign played across earlier league years took most of the
    # champion's ceiling with it: Argentina's 2022 World Cup paid 64 of 200.
    rows["path_max"] = (MATCH_MAX * (
        rows["G"] * STAGE["group"] + rows["K"] * STAGE["knockout"])).astype(float)
    rows.loc[~finals, "path_max"] = MATCH_MAX * q_path[~finals] * STAGE["qualifying"]
    rows["level"] = rows["rung"].where(finals, QUALIFYING_RUNG)
    rows["ceiling"] = rows["level"].map(RUNG) * SCALE
    rows["section"] = rows["competition"].where(finals, rows["competition"] + " qualifying")
    # What one club-scale point is worth in this match. Every part of the match
    # is multiplied by the same thing, which is what lets a section's boxes be
    # priced in the currency the section is quoted in.
    rows["factor"] = rows["ceiling"] * rows["stage"].map(STAGE) / rows[
        "path_max"].where(rows["path_max"] > 0)
    rows["points"] = rows["base"] * rows["factor"]
    rows = rows[rows["points"].notna()].copy()
    rows["title"] = _titles(rows)
    rows["points"] = rows["points"] + rows["title"]
    return rows


#: Endings that win a knockout tie.
_WON = (Outcome.WIN.value, Outcome.SHOOTOUT_WIN.value)


def _titles(rows: pd.DataFrame) -> pd.Series:
    """The title bonus, on the final each champion won.

    A champion is a team that played the whole group and every knockout round
    and won each knockout match. Counting knockout wins alone is not enough: a
    semi-final loser who wins the third-place match has played as many.
    Nations Leagues are left out because the ledger does not say which
    division a match was in.
    """
    bonus = pd.Series(0.0, index=rows.index)
    cups = rows[(rows["kind"] == "finals") & (rows["K"] > 0)
                & ~rows["competition"].str.contains("Nations League")]
    for _, mine in cups.groupby(["gender", "competition", "edition", "team"]):
        g, k = int(mine["G"].iloc[0]), int(mine["K"].iloc[0])
        knockout = mine[mine["stage"] == "knockout"].sort_values("date")
        if len(mine) == g + k and len(knockout) == k and knockout["outcome"].isin(_WON).all():
            bonus[knockout.index[-1]] = TITLE_BONUS * float(mine["ceiling"].iloc[0])
    return bonus


#: What a rung is called on a page. The keys price the competition; these say
#: what the price is for.
RUNG_NAMES = {
    "world": "World", "federation": "Federation",
    "nations_league": "Nations League",
}

#: A stage, as a phase heading, with the shape of boxes it wants. Qualifying
#: and a group are round-robin -- a draw is an ordinary result and there is no
#: shootout -- and a knockout tie is not.
STAGE_PHASES: tuple[tuple[str, str, str], ...] = (
    ("qualifying", "Qualifying", "round-robin"),
    ("group", "Group stage", "round-robin"),
    ("knockout", "Knockout", "knockout"),
)


def _counted(block: pd.DataFrame) -> dict:
    """One block of matches as the figures a section shows.

    Every count carries what it was worth, so the boxes can be checked against
    the section they sit in. Priced in the section's own currency rather than
    on the club scale: each part of a match is multiplied by the same `factor`
    the match itself was, which is the only way a win box and the section total
    can be the same kind of number. A title bonus is its own box.
    """
    out: dict = {"matches": int(len(block))}
    for name, ending in (("wins", Outcome.WIN), ("draws", Outcome.DRAW),
                         ("losses", Outcome.LOSS),
                         ("shootout_wins", Outcome.SHOOTOUT_WIN),
                         ("shootout_losses", Outcome.SHOOTOUT_LOSS)):
        here = block[block["outcome"] == ending.value]
        out[name] = int(len(here))
        out[f"pts_{name}"] = round(
            float((here["outcome_points"] * here["factor"]).sum()), 1)
    for name, column, worth in (("big_margins", "big_margin", PTS_BIG_MARGIN),
                                ("clean_sheets", "clean_sheet", PTS_CLEAN_SHEET)):
        out[name] = int(block[column].sum())
        out[f"pts_{name}"] = round(
            float((block[column] * worth * block["factor"]).sum()), 1)
    title = block["title"] if "title" in block.columns else pd.Series(dtype=float)
    out["title"] = int((title > 0).sum())
    out["pts_title"] = round(float(title.sum()), 1)
    out["points"] = round(float(block["points"].sum()), 1)
    return out


def season_sections(rows: pd.DataFrame, shares: pd.DataFrame) -> dict:
    """A national team's year grouped by the competition it was played in.

    The club panel's shape, because the question is the same one: a season
    total says how much and cannot say where. What differs is that a club's
    competitions add up to its season and a national team's do not -- one
    counts whole or by its rung, every other a quarter, and the year is then
    lifted by the biggest competition either gender plays in it.

    So a section carries two numbers. ``points`` is what the team earned there,
    which is the sum of the section's own boxes and can be read against its
    matches. ``counted`` is what that became in the season total, after the
    weighting and the lift. The counted figures are what add up to the score.

    Qualifying is a section of its own, because the scoring says it is its own
    competition.
    """
    if rows is None or rows.empty:
        return {}
    share = {
        (r.gender, r.team, int(r.season), r.section): (r.share, r.lift)
        for r in shares.itertuples()
    }
    out: dict = {}
    for (gender, team, season), block in rows.groupby(
            ["gender", "team", "season"]):
        sections: list[dict] = []
        for name, here in block.groupby("section", sort=False):
            got, lift = share.get((gender, team, int(season), name), (1.0, 1.0))
            qualifying = bool((here["kind"] == "qualifying").all())
            section = {
                "kind": "international",
                "name": str(name),
                "rung": "Qualifying" if qualifying
                        else RUNG_NAMES.get(str(here["rung"].iloc[0]), ""),
                "weight": round(float(got), 3),
                "counted": round(float(here["points"].sum()) * got * lift, 1),
                **_counted(here),
            }
            phases = [
                {"label": label, "shape": shape, **_counted(part)}
                for stage, label, shape in STAGE_PHASES
                if len(part := here[here["stage"] == stage])
            ]
            # Only where every match was placed. A campaign half split is a
            # section whose phases do not add up to it, which is worse than one
            # that was never divided at all.
            if len(phases) > 1 and sum(
                    p["matches"] for p in phases) == section["matches"]:
                section["phases"] = phases
            sections.append(section)
        sections.sort(key=lambda s: s["counted"], reverse=True)
        out[(LEAGUES.get(gender, gender), str(team), int(season))] = sections
    return out


def headlines(rows: pd.DataFrame) -> dict[tuple[str, int], float]:
    """The biggest ceiling each gender plays in each league year.

    What the matches show, raised to what ``intl_calendar.csv`` says the year
    will hold, so a year's weights are right before its biggest tournament is
    played: a men's Nations League in the autumn of an AFCON year is already
    below the year's top rung, rather than whole until January and a fraction
    after it.
    """
    played = rows.groupby(["gender", "season"])["ceiling"].max()
    tops = {(str(g), int(y)): float(v) for (g, y), v in played.items()}
    if CALENDAR.exists() and LADDER.exists():
        ladder = pd.read_csv(LADDER, comment="#")
        rung = dict(zip(zip(ladder["gender"], ladder["competition"]), ladder["rung"]))
        for entry in pd.read_csv(CALENDAR, comment="#").itertuples():
            ceiling = RUNG.get(rung.get((entry.gender, entry.competition), ""))
            if ceiling is not None:
                key = (str(entry.gender), int(entry.season))
                tops[key] = max(tops.get(key, 0.0), ceiling * SCALE)
    return tops


def year_rungs(rows: pd.DataFrame) -> dict[int, float]:
    """The biggest ceiling each league year holds, whichever gender plays it.

    Both genders count because the category is one category: the league
    admin's ruling, on seeing a men's Nations League win doubled in the year of
    the Women's World Cup, was that the year's biggest tournament sets the
    scale for everyone.
    """
    tops: dict[int, float] = {}
    for (_, year), ceiling in headlines(rows).items():
        tops[year] = max(tops.get(year, 0.0), ceiling)
    return tops


def _fold(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Team-seasons: competitions weighted by prestige, then the year's lift.

    A team's competitions are ranked by rung, then points. The first counts
    whole if it is on the biggest rung its gender plays that year, and in
    proportion to its rung otherwise -- never less than a secondary one. Every
    other competition counts ``SECONDARY_SHARE``.

    Returns the seasons and, beside them, what each competition was multiplied
    by to get there -- which is what a panel needs to say where a score came
    from without doing the arithmetic a second time and drifting from it.
    """
    # Every ending counted, not only the winning one. A record is the whole
    # line -- a side that played six and won two drew or lost the other four,
    # and "Wins 2" alone cannot say which. The club vocabulary, so a national
    # team's line reads like a club's: shootouts kept apart from the draws
    # they came out of, because the match itself was drawn and the shootout
    # was a separate thing that happened after it.
    def counted(ending: Outcome):
        return ("outcome", lambda s, v=ending.value: int((s == v).sum()))

    per_comp = rows.groupby(
        ["gender", "team", "season", "section"], as_index=False
    ).agg(points=("points", "sum"), ceiling=("ceiling", "max"),
          matches=("points", "size"),
          wins=counted(Outcome.WIN),
          shootout_wins=counted(Outcome.SHOOTOUT_WIN),
          draws=counted(Outcome.DRAW),
          shootout_losses=counted(Outcome.SHOOTOUT_LOSS),
          losses=counted(Outcome.LOSS))

    heads = headlines(rows)
    years = year_rungs(rows)
    ranked = per_comp.sort_values(["ceiling", "points"], ascending=False)
    head = pd.Series([heads.get((g, int(y)), c) for g, y, c in zip(
        ranked["gender"], ranked["season"], ranked["ceiling"])], index=ranked.index)
    first = ranked.groupby(["gender", "team", "season"]).cumcount() == 0
    share = pd.Series(SECONDARY_SHARE, index=ranked.index)
    share[first] = (ranked["ceiling"] / head).clip(lower=SECONDARY_SHARE, upper=1.0)[first]
    ranked["folded"] = ranked["points"] * share

    out = ranked.groupby(["gender", "team", "season"], as_index=False).agg(
        folded=("folded", "sum"), gross=("points", "sum"),
        matches=("matches", "sum"), wins=("wins", "sum"),
        shootout_wins=("shootout_wins", "sum"), draws=("draws", "sum"),
        shootout_losses=("shootout_losses", "sum"), losses=("losses", "sum"),
        competitions=("section", "size"), top_rung=("ceiling", "max"),
    )
    # One lift for everyone in a year. No team is held below it by its own
    # rung: a guard that did so let a team's World Cup qualifying keep it at x1
    # while the rest of its year was lifted.
    out["year_rung"] = out["season"].map(years).fillna(out["top_rung"])
    out["lift"] = max(RUNG.values()) * SCALE / out["year_rung"]
    out["total_points"] = out["folded"] * out["lift"]
    out["league"] = out["gender"].map(LEAGUES)
    shares = ranked.assign(share=share).merge(
        out[["gender", "team", "season", "lift"]],
        on=["gender", "team", "season"], how="left",
    )[["gender", "team", "season", "section", "share", "lift"]]
    return out.drop(columns="gender").sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True), shares
