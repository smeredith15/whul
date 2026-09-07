#!/usr/bin/env python3
"""What the international soccer ladder does to real seasons.

The category is ten rostered national teams and no scorer. This is the
prototype for one: it applies the proposed ladder to the whole martj42 history
and prints what comes out, so the weights can be argued about against seasons
that happened rather than in the abstract. Nothing here writes to the database.

The scheme, in four steps:

  1. **A competition is its qualifying and its finals together**, at one of
     three rungs -- nations league, federation cup, world cup. World Cup
     qualifying is therefore worth more than Euro qualifying, which is right,
     and a team that fails to qualify has still spent its year on the World
     Cup rung, which stops "miss the World Cup, get your other points
     upscaled" from being a strategy.
  2. **A match is worth its result times its stage**: qualifying 1, group 2,
     knockout 3. Multiplied by 3 for a win, 2 for a shootout win, 1 for a draw
     or shootout loss, 0 for a loss -- the R script's own scale, kept.
  3. **A competition pays a ceiling, not a rate.** A team takes the share of
     it their results earned, measured against the champion's whole path:

         team points = ceiling x (its units / path_max units)

     so winning the Gold Cup (six matches) and winning AFCON (seven) are worth
     the same, and the 2026 World Cup's new Round of 32 changes nothing about
     what a World Cup is worth. This is the tennis tier model already in the
     codebase.
  4. **A season is its best competition plus half its second** -- the two-way
     rule the MLB scorer uses for a player who bats and pitches. Summing them
     instead put the 2018-19 United States at 500 against a 99th percentile of
     200: they won the World Cup and the championship that qualified them for
     it, and a benchmark nobody else can reach is a category decided by one
     season.
  5. **A fallow year can be scaled up** so the best rung actually in play that
     season reaches a full ceiling. Without it a European team's Nations League
     year -- which is all 2026-27 holds for England, France and Spain -- scores
     half what the same team's World Cup year does. Both are computed here.

Stage is inferred per edition rather than assumed. The R script took the first
three matches as the group stage, which is right for a four-team group and
wrong for the six-match Nations League phase, the five-team groups of the 2025
Copa América Femenina, and the nine-team CONMEBOL Women's Nations League that
has no knockout stage at all.

    python scripts/intl-soccer-ladder.py --data <dir>

Downloads the two ledgers to --data (default: a cache beside this script) and
reuses them, so re-running costs nothing.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MEN = "https://raw.githubusercontent.com/martj42/international_results/master"
WOMEN = "https://raw.githubusercontent.com/martj42/womens-international-results/master"

DATA = Path(__file__).resolve().parent.parent / "whul" / "data"
LADDER = DATA / "intl_tournaments.csv"
SUPPLEMENT = DATA / "intl_supplement.csv"
EDITIONS = DATA / "intl_editions.csv"

#: What a perfect run in a competition is worth, by rung.
#:
#: Deliberately shallow -- 2 : 1.5 : 1 rather than 3 : 2 : 1. A steeper ladder
#: put the United States' 2018-19 (they won the World Cup and the championship
#: that qualified them for it) at 500 against a 99th percentile of 200, which
#: is a benchmark nobody else can reach and a category decided by one season.
#: The fold below is the other half of that fix.
RUNG = {"nations_league": 1.0, "federation": 1.5, "world": 2.0}

#: What a match is worth by the stage it is played at.
STAGE = {"qualifying": 1.0, "group": 2.0, "knockout": 3.0}

#: A season is its best competition in full plus half of everything else --
#: the two-way rule the MLB scorer already uses for a player who bats and
#: pitches, where the primary role scores whole and the secondary contributes
#: half, extended to however many competitions a year holds.
#:
#: It applies *after* rung and stage, so "best" means the competition worth
#: most to this team this year, not the highest rung it entered.
BEYOND_BEST_SHARE = 0.5

#: The real league names, because `whul.normalize` keys its normalization
#: groups on them and is fed them directly here -- so the benchmark this
#: prints is the benchmark that would be frozen, buffer-pool truncation and
#: all, rather than a percentile of every national team that played.
LEAGUES = {"M": "Men's Intl Soccer", "W": "Women's Intl Soccer"}

#: Two full four-year cycles. Five seasons -- what the other leagues use --
#: holds one World Cup and either one continental championship or two, so a
#: five-year pool is a different mix depending on which year it starts in.
BENCHMARK_SEASONS = 8

#: The club soccer scale, unchanged: `whul.scoring.competition.OUTCOME_SHARE`
#: and the two bonuses from `whul.scoring.soccer`. A win is three, a shootout
#: win two because the ninety minutes were drawn, a draw and a shootout loss
#: one apiece, a loss nothing -- plus one for winning by two or more, and one
#: for conceding nothing whatever the result.
WIN, SHOOTOUT_WIN, DRAW, SHOOTOUT_LOSS, LOSS = 3.0, 2.0, 1.0, 1.0, 0.0
BIG_MARGIN, PTS_BIG_MARGIN, PTS_CLEAN_SHEET = 2, 1.0, 1.0

#: What one match can be worth at most: won by two or more, to nil. The
#: denominator is built from this rather than from a bare win, so a run cannot
#: exceed its own competition and the rung stays a rung.
MATCH_MAX = WIN + PTS_BIG_MARGIN + PTS_CLEAN_SHEET

#: League points are quoted per hundred so they read like the rest of the
#: project. The figure is arbitrary -- the 0-100 scale comes from dividing by
#: the pool's 99th percentile afterwards.
SCALE = 100.0

#: The league year normally runs mid-July to mid-July. 2026-27 is the
#: exception -- it opens on 21 August, because that is when the league was
#: drafted -- and it closes on 13 July 2027 like any other, so history is
#: partitioned from the 14th and every year is contiguous with the next.
#:
#: The date is load-bearing rather than cosmetic. Nearly every continental
#: championship and World Cup is played from mid-June to mid-July, so a
#: mid-July boundary falls *inside* them: Euro 2024 ran 14 June to 14 July, and
#: partitioning by match date alone would have put its final in the next league
#: year from its group stage. That is precisely what the block rule exists to
#: prevent, and against an August boundary it never fired.
YEAR_OPENS = (7, 14)

ROSTERED = {
    "M": ["England", "France", "Spain"],
    "W": ["Brazil", "Canada", "England", "France", "Germany", "Spain", "United States"],
}


def league_year(day: pd.Timestamp) -> int:
    """The label of the league year a date falls in. 2026 means 2026-27."""
    return day.year if (day.month, day.day) >= YEAR_OPENS else day.year - 1


def load(data: Path, refresh: bool = False) -> pd.DataFrame:
    """Both ledgers, one frame, with the shootout winner joined on."""
    data.mkdir(parents=True, exist_ok=True)
    frames = []
    for gender, base in (("M", MEN), ("W", WOMEN)):
        rows = _cached(data / f"{gender}-results.csv", f"{base}/results.csv", refresh)
        shoot = _cached(data / f"{gender}-shootouts.csv", f"{base}/shootouts.csv", refresh)
        rows["gender"] = gender
        rows["date"] = pd.to_datetime(rows["date"])
        if not shoot.empty:
            shoot["date"] = pd.to_datetime(shoot["date"])
            rows = rows.merge(
                shoot[["date", "home_team", "away_team", "winner"]],
                on=["date", "home_team", "away_team"], how="left",
            )
        else:
            rows["winner"] = None
        frames.append(rows)
    return _supplement(pd.concat(frames, ignore_index=True))


def _supplement(games: pd.DataFrame) -> pd.DataFrame:
    """Add the matches the ledgers do not carry, and say so.

    The 2024 CONCACAF W Gold Cup is absent from the women's ledger entirely --
    its qualification is there and not one match of the tournament -- which
    left Canada and the United States with four blank years running. A hand
    file is the answer for a hole in someone else's dataset; the check that it
    stays one is reporting every row that turns up on both sides, so a block
    kept after the upstream fix cannot double-count in silence.
    """
    if not SUPPLEMENT.exists():
        return games
    extra = pd.read_csv(SUPPLEMENT, comment="#")
    extra["date"] = pd.to_datetime(extra["date"])
    extra = extra.rename(columns={"shootout_winner": "winner"})

    key = ["date", "home_team", "away_team", "gender"]
    already = games.merge(extra[key], on=key, how="inner")
    if len(already):
        print(f"  !! {len(already)} supplement row(s) are now in the ledger too "
              f"-- delete that block from {SUPPLEMENT.name}:", flush=True)
        for row in already.head(5).itertuples():
            print(f"     {row.date.date()} {row.home_team} v {row.away_team}")
        extra = extra.merge(already[key], on=key, how="left", indicator=True)
        extra = extra[extra["_merge"] == "left_only"].drop(columns="_merge")
    if extra.empty:
        return games
    print(f"  + {len(extra)} supplied match(es) the ledgers do not carry: "
          f"{', '.join(sorted(extra['tournament'].unique()))}", flush=True)
    return pd.concat([games, extra], ignore_index=True)


def _cached(path: Path, url: str, refresh: bool) -> pd.DataFrame:
    if path.exists() and not refresh:
        return pd.read_csv(path)
    print(f"  fetching {url}", flush=True)
    frame = pd.read_csv(url)
    frame.to_csv(path, index=False)
    return frame


def classify(games: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split into what the ladder scores and what it does not.

    An allow-list, not a pattern. The women's African championship has been
    filed under five names and changed spelling again in 2025, so a regex
    written against history keeps matching history and drops the season being
    played -- which is this project's whole failure mode in one column.
    """
    ladder = pd.read_csv(LADDER, comment="#")
    scored = games.merge(ladder, on=["gender", "tournament"], how="left")
    return scored[scored["rung"].notna()].copy(), scored[scored["rung"].isna()].copy()


def per_team(games: pd.DataFrame) -> pd.DataFrame:
    """One row per team per match, priced on the club soccer scale.

    The same outcome table and the same two bonuses a club gets, so a national
    team's 2-0 and a club's 2-0 are worth the same thing before the tournament
    ladder touches them. A shootout is only ever consulted on a level score,
    which is the only way one can happen.
    """
    sides = []
    for side, other in (("home", "away"), ("away", "home")):
        mine, theirs = games[f"{side}_score"], games[f"{other}_score"]
        team = games[f"{side}_team"]
        won, drew = mine > theirs, mine == theirs
        shootout_won = drew & (games["winner"] == team)
        shootout_lost = drew & games["winner"].notna() & ~shootout_won

        base = pd.Series(LOSS, index=games.index)
        base[won] = WIN
        base[drew] = DRAW
        base[shootout_lost] = SHOOTOUT_LOSS
        base[shootout_won] = SHOOTOUT_WIN
        # A shootout win is deliberately not a win for the margin bonus: the
        # match itself was drawn, so there is no margin to be big. A clean
        # sheet is not gated on the result at all -- a side that conceded
        # nothing cannot have lost in normal time, so it reaches exactly wins
        # to nil and goalless draws.
        base = base + (won & (mine - theirs >= BIG_MARGIN)) * PTS_BIG_MARGIN
        base = base + (theirs == 0) * PTS_CLEAN_SHEET

        sides.append(pd.DataFrame({
            "date": games["date"], "gender": games["gender"], "team": team,
            "competition": games["competition"], "rung": games["rung"],
            "kind": games["kind"], "phase": games["phase"], "base": base,
        }))
    out = pd.concat(sides, ignore_index=True)
    out = out[out["base"].notna()]
    return _assign_year(out)


#: How far apart two matches can be and still be the same tournament. A block
#: tournament runs two to five weeks with a rest day or two inside it; the next
#: staging is a year or two away, and even a Nations League league phase leaves
#: a month between windows.
BLOCK_GAP_DAYS = 35


def _assign_year(rows: pd.DataFrame) -> pd.DataFrame:
    """Which league year each match scores in.

    Two rules, and the difference is the phase rather than the competition:

    **A block goes whole into the year it began in.** A group stage that runs
    directly into a knockout is one event, and splitting it at a date nobody
    playing in it would recognise is worse than letting it finish outside the
    year. The 2027 Women's World Cup ends twelve days after the 2026-27 league
    year closes and belongs to it entirely, including the final -- which is
    played after the next draft, and still pays the rosters that held those
    teams when it kicked off.

    **A windowed phase scores where it was played.** Qualifying campaigns and
    the Nations Leagues' league phases run across international windows months
    apart, and they do not line up with a league year in any reliable way --
    the 2022-23 UEFA Nations League opened in June 2022 and finished in June
    2023, so a whole-block rule would have to pick one year and be wrong about
    half the fixtures either way.
    """
    rows = rows.sort_values("date").reset_index(drop=True)
    rows["year"] = rows["date"].map(league_year)

    block = rows["phase"] == "block"
    if not block.any():
        return rows
    inside = rows[block]
    gap = inside.groupby(["gender", "competition"])["date"].diff()
    started = (gap.isna()) | (gap > pd.Timedelta(days=BLOCK_GAP_DAYS))
    # Every match of a block takes the year of the block's first match.
    label = started.cumsum()
    first = inside.groupby(label)["year"].transform("first")
    rows.loc[block, "year"] = first
    return rows


def edition_of(rows: pd.DataFrame) -> pd.Series:
    """Which staging of a competition a match belongs to.

    The league year names it, not the calendar year, and that is not a
    presentational choice -- it is what holds a tournament together. The 2025
    Africa Cup of Nations was played across new year and a calendar key split
    it in two, leaving an "edition" whose teams had played one match each and
    a group stage inferred as one match long. The UEFA and CONCACAF Nations
    Leagues split the same way, their league phase landing in one year and
    their finals in the next, so an edition came out as four teams.

    Every competition here is staged at most once a league year, so the year
    is a complete key. A qualifying campaign runs for two or three years and
    belongs to the finals it feeds, so it takes the next finals year -- which
    is what makes a qualifying match part of the World Cup rather than an
    event of its own.
    """
    edition = rows["year"].astype(int).copy()
    finals = rows[rows["kind"] == "finals"]
    for (gender, competition), block in finals.groupby(["gender", "competition"]):
        years = sorted(block["year"].unique())
        mask = (rows["gender"] == gender) & (rows["competition"] == competition) \
            & (rows["kind"] == "qualifying")
        if not mask.any():
            continue
        # A campaign for finals the ledger has not reached yet -- the 2027
        # World Cups, the 2027 Asian Cup -- has no finals year to point at.
        # Falling back to "the last one plus a year" filed the 2027 Women's
        # World Cup qualifiers against the 2023 tournament, which had already
        # been played and scored. The competition's own cadence names the
        # staging they belong to instead.
        gaps = pd.Series(years).diff().dropna()
        cadence = int(gaps.median()) if len(gaps) else 2
        ahead = max(years) + max(cadence, 1)
        edition.loc[mask] = rows.loc[mask, "year"].map(
            lambda y: next((f for f in years if f >= y), ahead)
        )
    return edition


def straddling(rows: pd.DataFrame, days: int = 21) -> list[str]:
    """Competitions with play on both sides of a league-year boundary.

    The league year opens on 21 August and the rule is that a tournament is
    scored whole into the year it began in. Nothing in the record needs that
    rule today -- the 2023 Women's World Cup final was played on 20 August,
    one day inside -- but a future tournament that straddles the date would be
    cut in half silently, so it is checked rather than assumed.
    """
    warned = []
    for (gender, competition), block in rows[rows["kind"] == "finals"].groupby(
            ["gender", "competition"]):
        late = block[(block["date"].dt.month == 7) & (block["date"].dt.day > 14 - days)
                     & (block["date"].dt.day < 14)]
        early = block[(block["date"].dt.month == 7) & (block["date"].dt.day >= 14)]
        shared = set(late["year"] + 1) & set(early["year"])
        if shared:
            warned.append(f"{gender} {competition} {sorted(shared)}")
    return warned


def structure(rows: pd.DataFrame) -> pd.DataFrame:
    """Group and knockout matches per edition, inferred from the ledger.

    ``G`` is the fewest matches any team played: a side eliminated in the group
    stage plays exactly the group. ``K`` is what the longest run adds on top,
    which is the champion's knockout path. Reported per edition rather than
    trusted, because a withdrawal would drag the minimum down and nothing else
    would say so.
    """
    finals = rows[rows["kind"] == "finals"]
    out = []
    for key, block in finals.groupby(["gender", "competition", "edition"]):
        counts = block.groupby("team").size()
        group, longest = int(counts.min()), int(counts.max())
        out.append({
            "gender": key[0], "competition": key[1], "edition": key[2],
            "teams": len(counts), "G": group, "K": max(longest - group, 0),
            "shape_from": "played",
        })
    shape = pd.DataFrame(out)

    # An edition whose finals have not been played yet -- which is every
    # qualifying campaign in progress -- has no shape to read, and a missing
    # shape is not a neutral zero. It makes the denominator the team's own
    # qualifiers alone, so winning both matches of a two-game preliminary tie
    # paid a full World Cup ceiling. Seven African sides and the US Virgin
    # Islands scored a perfect season that way, on one or two matches.
    #
    # The format is the most stable thing about a competition, so the last
    # edition that was played supplies it, and every carry is marked.
    every = rows[["gender", "competition", "edition"]].drop_duplicates()
    shape = every.merge(shape, on=["gender", "competition", "edition"], how="left")
    shape = shape.sort_values(["gender", "competition", "edition"])
    for column in ("teams", "G", "K"):
        shape[column] = shape.groupby(["gender", "competition"])[column].ffill().bfill()
    shape["shape_from"] = shape["shape_from"].fillna("carried")
    return _override(shape.dropna(subset=["G", "K"]))


def _override(shape: pd.DataFrame) -> pd.DataFrame:
    """Replace an inferred shape where the ledger cannot supply one.

    Only the Nations Leagues, and for reasons the file states at length: the
    division is not recorded, so the smallest league sets G and the largest
    sets K, and an edition does not fit inside a league year predictably enough
    for any date rule to separate one from the next.
    """
    if not EDITIONS.exists():
        return shape
    given = pd.read_csv(EDITIONS, comment="#")
    merged = shape.merge(given, on=["gender", "competition", "edition"], how="left")
    stated = merged["group"].notna()
    merged.loc[stated, "G"] = merged.loc[stated, "group"]
    merged.loc[stated, "K"] = merged.loc[stated, "knockout"]
    merged.loc[stated, "shape_from"] = merged.loc[stated, "source"]

    # A row nobody uses is a row nobody checks. Say so rather than leaving it
    # to be discovered when a competition is renamed and its overrides stop
    # matching anything.
    keys = set(zip(shape["gender"], shape["competition"], shape["edition"]))
    unused = given[[
        (g, c, e) not in keys
        for g, c, e in zip(given["gender"], given["competition"], given["edition"])
    ]]
    for row in unused.itertuples():
        print(f"  !! {EDITIONS.name}: no {row.gender} {row.competition} "
              f"{row.edition} in the data -- that override does nothing",
              flush=True)
    return merged.drop(columns=["group", "knockout", "source", "note"])


def price(rows: pd.DataFrame, shape: pd.DataFrame) -> pd.DataFrame:
    """Each match's share of its competition's ceiling."""
    rows = rows.merge(shape, on=["gender", "competition", "edition"], how="left")
    rows["G"] = rows["G"].fillna(0).astype(int)
    rows["K"] = rows["K"].fillna(0).astype(int)

    # Within a finals tournament, a team's first G matches are its group and
    # the rest are knockouts. The R script fixed G at three, which scored the
    # Nations League's fourth, fifth and sixth league matches as knockout
    # football and five of the CONMEBOL Women's eight.
    rows = rows.sort_values("date")
    order = rows.groupby(["gender", "competition", "edition", "team", "kind"]).cumcount()
    rows["stage"] = "qualifying"
    finals = rows["kind"] == "finals"
    rows.loc[finals, "stage"] = pd.Series(
        ["group" if o < g else "knockout" for o, g in zip(order[finals], rows.loc[finals, "G"])],
        index=rows.index[finals],
    )
    rows["units"] = rows["base"] * rows["stage"].map(STAGE)

    # The denominator is the champion's whole path -- their qualifying, their
    # group, their knockouts -- so the ceiling is what a run of wins by two
    # or more to nil pays, and nothing else reaches it. Qualifying length is the team's own, because a CONMEBOL
    # campaign is eighteen matches and a CAF one is six, and both are the same
    # achievement.
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
    rows["points"] = rows["ceiling"] * rows["units"] / rows["path_max"].where(rows["path_max"] > 0)
    return rows[rows["points"].notna()]


def seasons(rows: pd.DataFrame, upscale: bool = True, fold: bool = True) -> pd.DataFrame:
    """Team-seasons: the best competition whole, everything else at half.

    ``fold=False`` sums every competition instead, which is what produced the
    outlier that started this. ``upscale`` lifts a year whose best rung is not the
    top one, so a Nations League season is not worth half a World Cup season by
    the calendar alone.
    """
    per_comp = rows.groupby(
        ["gender", "team", "year", "competition"]
    ).agg(points=("points", "sum"), ceiling=("ceiling", "max"),
          matches=("points", "size")).reset_index()

    ranked = per_comp.sort_values("points", ascending=False)
    ranked["rank"] = ranked.groupby(["gender", "team", "year"]).cumcount()
    if fold:
        share = pd.Series(BEYOND_BEST_SHARE, index=ranked.index)
        share[ranked["rank"] == 0] = 1.0
        ranked["points"] = ranked["points"] * share

    grouped = ranked.groupby(["gender", "team", "year"]).agg(
        points=("points", "sum"), matches=("matches", "sum"),
        entered=("competition", "size"), top_rung=("ceiling", "max"),
    ).reset_index()
    if upscale:
        grouped["points"] = grouped["points"] * max(RUNG.values()) * SCALE / grouped["top_rung"]
    return grouped.sort_values(["year", "points"], ascending=[True, False])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(Path(__file__).parent / ".intl-cache"))
    parser.add_argument("--refresh", action="store_true", help="re-download the ledgers")
    parser.add_argument("--from-year", type=int, default=2015)
    args = parser.parse_args()

    print(__doc__.split("The scheme, in four steps:")[0].strip())
    print(f"\n{'=' * 74}\nLoading\n")
    games = load(Path(args.data), args.refresh)
    print(f"  {len(games):,} matches, {games['date'].min().date()} to "
          f"{games['date'].max().date()}")
    for gender in ("M", "W"):
        block = games[games["gender"] == gender]
        print(f"    {gender}: {len(block):,}, latest {block['date'].max().date()}")

    kept, dropped = classify(games)
    recent = dropped[dropped["date"] >= f"{args.from_year}-01-01"]
    print(f"\n{'=' * 74}\nWhat the ladder does not score "
          f"({len(recent):,} matches since {args.from_year})\n")
    counts = recent.groupby(["gender", "tournament"]).size().sort_values(ascending=False)
    for (gender, name), n in counts.head(18).items():
        print(f"    {gender}  {name:<50} {n:>5}")
    print(f"    ... {len(counts)} names in all. Every one is excluded on purpose;")
    print(f"    anything here that should score is a row missing from the ladder.")

    # The list above is mostly regional cups nobody on the roster enters. This
    # is the half that costs the league something, and it is the one worth
    # arguing about.
    print(f"\n  What the exclusions cost the rostered teams (since {args.from_year}):\n")
    for gender, names in ROSTERED.items():
        block = recent[(recent["gender"] == gender)
                       & (recent["home_team"].isin(names) | recent["away_team"].isin(names))]
        for name, hits in sorted(block.groupby("tournament"), key=lambda kv: -len(kv[1]))[:6]:
            print(f"    {gender}  {name:<44} {len(hits):>4}  "
                  f"{hits['date'].min().date()} to {hits['date'].max().date()}")

    rows = per_team(kept)
    by_date = rows["date"].map(league_year)
    moved = rows[(rows["year"] != by_date) & (rows["year"] >= args.from_year)]
    print(f"\n  Block tournaments held whole in the year they began: "
          f"{len(moved)} team-match(es) since {args.from_year} score in a league "
          f"year their date alone would not have put them in.")
    if len(moved):
        for key, block in moved.groupby(["gender", "competition", "year"]):
            print(f"    {key[0]}  {key[1]} -> {int(key[2])}  ({len(block)} matches)")
    rows["edition"] = edition_of(rows)
    shape = structure(rows)
    priced = price(rows, shape)

    print(f"\n{'=' * 74}\nInferred tournament shape -- check these against the formats\n")
    print("    G is group matches per team, K the champion's knockout path.")
    stated = shape[shape["shape_from"].isin(("derived", "assumed", "admin"))]
    if len(stated):
        print("    Stated rather than inferred, from whul/data/intl_editions.csv:")
        for row in stated.sort_values(["gender", "competition", "edition"]).itertuples():
            print(f"      {row.gender}  {row.competition:<32} {int(row.edition)}  "
                  f"G={int(row.G)}  K={int(row.K)}   [{row.shape_from}]")
        print()
    look = shape[(shape["edition"] >= 2018) & (shape["shape_from"] == "played")].sort_values(
        ["competition", "edition"]).drop_duplicates(["gender", "competition"], keep="last")
    for row in look.itertuples():
        print(f"    {row.gender}  {row.competition:<32} {row.edition}  "
              f"{int(row.teams):>3} teams  G={int(row.G)}  K={int(row.K)}")
    carried = shape[(shape["shape_from"] == "carried") & (shape["edition"] >= 2024)]
    if len(carried):
        print(f"\n    {len(carried)} edition(s) in progress take their shape from the last "
              f"one played:")
        for row in carried.itertuples():
            print(f"      {row.gender}  {row.competition:<32} {row.edition}  "
                  f"G={int(row.G)}  K={int(row.K)}")

    print(f"\n{'=' * 74}\nThe rostered teams, by league year\n")
    for upscale in (False, True):
        table = seasons(priced, upscale=upscale)
        label = "best + half the rest, WITH the fallow-year lift (adopted)" if upscale \
            else "best + half the rest, no lift"
        print(f"  --- {label}")
        for gender, names in ROSTERED.items():
            mine = table[(table["gender"] == gender) & table["team"].isin(names)
                         & (table["year"] >= args.from_year)]
            wide = mine.pivot_table(index="team", columns="year", values="points")
            print(f"\n    {gender}")
            print("      " + wide.round(0).fillna(0).astype(int).to_string().replace("\n", "\n      "))
        print()

    print(f"{'=' * 74}\nThe benchmark, through the real machinery\n")
    print(f"    `whul.normalize.compute_benchmarks`, not a flat percentile: the")
    print(f"    pool is truncated to the top {40} of each season before the 99th")
    print(f"    percentile is taken, so 100 means the best of the draftable field")
    print(f"    rather than of every national team that played a match.\n")

    window = sorted(priced["year"].unique())[-BENCHMARK_SEASONS:]
    for label, kwargs in (
        ("summed, no fold, no lift", dict(fold=False, upscale=False)),
        ("folded, no lift", dict(upscale=False)),
        ("folded and lifted -- ADOPTED", dict()),
    ):
        table = seasons(priced, **kwargs)
        scored = normalized(table, window)
        print(f"  --- {label}")
        for gender, league in LEAGUES.items():
            block = scored[scored["league"] == league]
            pool = block[block["in_pool"]]
            if pool.empty:
                continue
            over = [(t, int((pool["scaled"] > t).sum())) for t in (100, 125, 150, 200)]
            print(f"    {league:<22} benchmark {block['benchmark'].iloc[0]:6.1f} "
                  f"from {len(pool)} pooled seasons; best raw {pool['points'].max():6.1f}"
                  f" = {pool['scaled'].max():5.1f}")
            print(f"      seasons over: " + ",  ".join(
                f"{t} -> {n}" for t, n in over))
        print()

    print(f"{'=' * 74}\nNormalized scores -- every team, not only the roster\n")
    table = seasons(priced)
    scored = normalized(table, window)
    for gender, league in LEAGUES.items():
        block = scored[(scored["league"] == league) & scored["in_pool"]]
        print(f"  --- {league}: the 20 highest seasons in the pool")
        top = block.nlargest(20, "scaled")
        for row in top.itertuples():
            print(f"      {int(row.year)}  {row.team:<28} {row.points:7.1f} raw  "
                  f"{row.scaled:6.1f}  ({row.entered} competition(s), {row.matches} matches)")
        share = block["scaled"]
        print(f"      median {share.median():5.1f}   75th {share.quantile(.75):5.1f}   "
              f"90th {share.quantile(.90):5.1f}   99th {share.quantile(.99):5.1f}   "
              f"max {share.max():5.1f}\n")

    print(f"{'=' * 74}\nThe rostered teams, normalized\n")
    for gender, names in ROSTERED.items():
        block = scored[(scored["league"] == LEAGUES[gender]) & scored["team"].isin(names)]
        wide = block.pivot_table(index="team", columns="year", values="scaled")
        print(f"  {LEAGUES[gender]}")
        print("    " + wide.round(0).fillna(0).astype(int).to_string().replace("\n", "\n    "))
        print()

    print(f"{'=' * 74}\nHow often a competition beyond the best is halved\n")
    counts = table[table["year"].isin(window)]["entered"].value_counts().sort_index()
    for entered, n in counts.items():
        note = "  <- all but the best are halved" if entered >= 2 else ""
        print(f"    {entered} competition(s) in a season: {n:>5} team-seasons{note}")
    return 0


def normalized(table: pd.DataFrame, window: list[int]) -> pd.DataFrame:
    """Team-seasons on the 0-100 scale, against a benchmark computed as WHUL does.

    The buffer-pool truncation is the whole point of routing through
    `whul.normalize` rather than taking a percentile here: it keeps only the
    top of each season before pooling, so the benchmark is the best of a
    draftable field. A flat percentile over every nation that played a
    competitive match puts the bar around a team that lost in qualifying, and
    every good season then scores three figures.
    """
    from whul.normalize import buffer_pool, compute_benchmarks

    live = table[table["year"].isin(window)].copy()
    live["league"] = live["gender"].map(LEAGUES)
    live["total_points"] = live["points"]

    bench = compute_benchmarks(live, "Team", season_col="year")
    pooled = buffer_pool(live, "Team", season_col="year")
    keys = set(zip(pooled["league"], pooled["team"], pooled["year"]))

    out = live.merge(
        bench[["norm_key", "benchmark"]].rename(columns={"norm_key": "league"}),
        on="league", how="left",
    )
    out["scaled"] = out["points"] / out["benchmark"] * 100.0
    out["in_pool"] = [
        (lg, tm, yr) in keys
        for lg, tm, yr in zip(out["league"], out["team"], out["year"])
    ]
    return out


if __name__ == "__main__":
    raise SystemExit(main())
