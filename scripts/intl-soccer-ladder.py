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
  3. **A competition pays a purse, not a rate.** A team takes the share of the
     purse its results earned against the champion's whole path:

         team points = purse x (its units / path_max units)

     so winning the Gold Cup (six matches) and winning AFCON (seven) are worth
     the same, and the 2026 World Cup's new Round of 32 changes nothing about
     what a World Cup is worth. This is the tennis tier model already in the
     codebase.
  4. **A fallow year is scaled up** so the best rung actually in play that
     season is worth a full purse. Without it a European team's Nations League
     year -- which is all 2026-27 holds for England, France and Spain -- scores
     a third of what the same team's World Cup year does, and the category
     goes quiet for two years in three.

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

LADDER = Path(__file__).resolve().parent.parent / "whul" / "data" / "intl_tournaments.csv"

#: What a competition pays a champion for a perfect run, by rung. Only the
#: ratios matter: the figures become league points, and the 0-100 scale comes
#: from dividing by the pool's 99th percentile afterwards -- which is why a
#: perfect run lands well above 100 rather than exactly on it.
PURSE = {"nations_league": 100.0, "federation": 200.0, "world": 300.0}

#: What a match is worth by the stage it is played at.
STAGE = {"qualifying": 1.0, "group": 2.0, "knockout": 3.0}

WIN, SHOOTOUT_WIN, DRAW, LOSS = 3.0, 2.0, 1.0, 0.0

#: The league year opens on 21 August. History is partitioned into contiguous
#: windows from that date so no match falls outside one -- the real 2026-27
#: window closes on 13 July, and international tournaments are the admin's
#: stated exception to that close: a tournament is scored whole into the year
#: it began in, even when its final is played after the year has ended.
YEAR_OPENS = (8, 21)

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
    return pd.concat(frames, ignore_index=True)


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
    ladder = pd.read_csv(LADDER)
    scored = games.merge(ladder, on=["gender", "tournament"], how="left")
    return scored[scored["rung"].notna()].copy(), scored[scored["rung"].isna()].copy()


def per_team(games: pd.DataFrame) -> pd.DataFrame:
    """One row per team per match, with the result already priced."""
    sides = []
    for side, other in (("home", "away"), ("away", "home")):
        mine, theirs = games[f"{side}_score"], games[f"{other}_score"]
        team = games[f"{side}_team"]
        base = pd.Series(LOSS, index=games.index)
        base[mine > theirs] = WIN
        drew = mine == theirs
        base[drew] = DRAW
        base[drew & (games["winner"] == team)] = SHOOTOUT_WIN
        sides.append(pd.DataFrame({
            "date": games["date"], "gender": games["gender"], "team": team,
            "competition": games["competition"], "rung": games["rung"],
            "kind": games["kind"], "base": base,
        }))
    out = pd.concat(sides, ignore_index=True)
    out = out[out["base"].notna()]
    out["year"] = out["date"].map(league_year)
    return out


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
        late = block[(block["date"].dt.month == 8) & (block["date"].dt.day > 21 - days)
                     & (block["date"].dt.day < 21)]
        early = block[(block["date"].dt.month == 8) & (block["date"].dt.day >= 21)]
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
    # paid a full World Cup purse. Seven African sides and the US Virgin
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
    return shape.dropna(subset=["G", "K"])


def price(rows: pd.DataFrame, shape: pd.DataFrame) -> pd.DataFrame:
    """Each match's share of its competition's purse."""
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
    # group, their knockouts -- so the purse is what a perfect run pays and
    # nothing else. Qualifying length is the team's own, because a CONMEBOL
    # campaign is eighteen matches and a CAF one is six, and both are the same
    # achievement.
    quals = rows[rows["kind"] == "qualifying"].groupby(
        ["gender", "competition", "edition", "team"]).size().rename("Q")
    rows = rows.merge(quals, on=["gender", "competition", "edition", "team"], how="left")
    rows["Q"] = rows["Q"].fillna(0).astype(int)
    rows["path_max"] = WIN * (
        rows["Q"] * STAGE["qualifying"]
        + rows["G"] * STAGE["group"]
        + rows["K"] * STAGE["knockout"]
    )
    rows["purse"] = rows["rung"].map(PURSE)
    rows["points"] = rows["purse"] * rows["units"] / rows["path_max"].where(rows["path_max"] > 0)
    return rows[rows["points"].notna()]


def seasons(rows: pd.DataFrame, upscale: bool) -> pd.DataFrame:
    """Team-seasons, optionally lifted so the year's best rung pays a full purse."""
    grouped = rows.groupby(["gender", "team", "year"]).agg(
        points=("points", "sum"), matches=("points", "size"),
        top_rung=("purse", "max"),
    ).reset_index()
    if upscale:
        grouped["points"] = grouped["points"] * max(PURSE.values()) / grouped["top_rung"]
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
    rows["edition"] = edition_of(rows)
    shape = structure(rows)
    priced = price(rows, shape)

    print(f"\n{'=' * 74}\nInferred tournament shape -- check these against the formats\n")
    print("    G is group matches per team, K the champion's knockout path.")
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
        table = seasons(priced, upscale)
        label = "WITH fallow-year upscaling" if upscale else "raw purse shares"
        print(f"  --- {label}")
        for gender, names in ROSTERED.items():
            mine = table[(table["gender"] == gender) & table["team"].isin(names)
                         & (table["year"] >= args.from_year)]
            wide = mine.pivot_table(index="team", columns="year", values="points")
            print(f"\n    {gender}")
            print("      " + wide.round(0).fillna(0).astype(int).to_string().replace("\n", "\n      "))
        print()

    print(f"{'=' * 74}\nWhat a benchmark would be\n")
    for upscale in (False, True):
        table = seasons(priced, upscale)
        pool = table[table["year"] >= args.from_year]
        label = "upscaled" if upscale else "raw     "
        p99 = pool["points"].quantile(0.99)
        best = pool.nlargest(1, "points").iloc[0]
        print(f"    {label}  pool {len(pool):>5} team-seasons   p99 {p99:7.1f}   "
              f"best {best['points']:7.1f} ({best['team']} {int(best['year'])})")
        print(f"              a perfect World Cup run ({max(PURSE.values()):.0f}) would score "
              f"{max(PURSE.values()) / p99 * 100:6.1f} on the 0-100 scale")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
