"""International football results, from the martj42 ledgers.

Two public CSVs, one per gender, each a row per match back to 1872 with the
tournament named. They are what the league's R script used, they are still
maintained, and they answer from a datacenter address -- which is more than
can be said for most of this project's sources.

    martj42/international_results          men, 49,547 rows
    martj42/womens-international-results   women, 11,650 rows

Both carry a separate ``shootouts.csv`` for ties decided on penalties, joined
on here so the scorer never has to think about where a shootout lives.

Three things about this data need saying out loud, because each of them is the
kind of fault that returns a full-looking answer:

**The tournament name is the only key, it is not consistent between the files,
and it changes.** Men file the Gold Cup as ``Gold Cup`` and women as
``CONCACAF Gold Cup``. The women's African championship has appeared under four
names, most recently changing to ``Africa Cup of Nations qualification`` in
2025 -- the spelling the R script's ``African Cup`` pattern does not match. So
the ladder is an allow-list of exact strings and every unmatched name is
reported. A pattern written against history keeps matching history and drops
the season being played.

**The women's file lags the men's.** A stale file and a quiet season look
identical, so the last date in each ledger is reported rather than inferred.

**Whole tournaments can be missing.** The 2024 CONCACAF W Gold Cup is absent
entirely -- its qualification is there, 87 matches of it, and not one match of
the tournament, though the United States won it. ``intl_supplement.csv`` fills
holes like that by hand, and any row in it that later turns up in the ledger is
reported so a supplement kept too long cannot double-count.

See docs/INTL_SOCCER.md for the ladder itself and what it is worth.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from whul.config.league import league_year

RAW = "https://raw.githubusercontent.com"
LEDGERS = {
    "M": f"{RAW}/martj42/international_results/master",
    "W": f"{RAW}/martj42/womens-international-results/master",
}

DATA = Path(__file__).resolve().parent.parent / "data"
LADDER = DATA / "intl_tournaments.csv"
SUPPLEMENT = DATA / "intl_supplement.csv"

#: How far apart two matches can be and still be one tournament. A block runs
#: two to five weeks; the next staging is a year or more away, and even a
#: Nations League league phase leaves a month between windows.
BLOCK_GAP_DAYS = 35

COLUMNS = [
    "date", "season", "gender", "competition", "rung", "kind", "phase",
    "home_team", "away_team", "home_score", "away_score", "shootout_winner",
]


def load_matches(
    seasons: list[int] | None = None, verbose: bool = True
) -> pd.DataFrame:
    """Every scoring match, classified, with its league year already decided.

    ``seasons`` are league years -- 2026 is 2026-27. The frame always carries
    the whole history and marks the asked-for years in ``wanted``; the scorer
    needs the rest to read each tournament's shape off the edition that was
    played, and drops them once it has.
    """
    games = _ledgers(verbose=verbose)
    games = _supplement(games, verbose=verbose)
    kept, dropped = _classify(games)
    if verbose:
        _report_dropped(dropped)
    if kept.empty:
        return pd.DataFrame(columns=COLUMNS)

    kept = _assign_season(kept)
    # The whole history goes back, with the asked-for seasons marked, and the
    # scorer drops the rest at the end. That is not laziness: a tournament's
    # shape is inferred from the edition that was played, so a pull for one
    # league year alone has nothing to infer from. Filtering here left the 2026
    # Women's Africa Cup of Nations with no group stage and no knockout, so two
    # won qualifiers paid a full ceiling and Ghana topped the women's board on
    # two matches.
    kept["wanted"] = True if not seasons else kept["season"].isin(
        [int(s) for s in seasons])
    return kept[COLUMNS + ["wanted"]].sort_values("date").reset_index(drop=True)


def _ledgers(verbose: bool = True) -> pd.DataFrame:
    frames = []
    for gender, base in LEDGERS.items():
        rows = pd.read_csv(f"{base}/results.csv")
        rows["gender"] = gender
        rows["date"] = pd.to_datetime(rows["date"])
        try:
            shootouts = pd.read_csv(f"{base}/shootouts.csv")
        except Exception:   # noqa: BLE001 -- a missing file is not a missing season
            shootouts = pd.DataFrame(columns=["date", "home_team", "away_team", "winner"])
        if not shootouts.empty:
            shootouts["date"] = pd.to_datetime(shootouts["date"])
            rows = rows.merge(
                shootouts[["date", "home_team", "away_team", "winner"]],
                on=["date", "home_team", "away_team"], how="left",
            )
        else:
            rows["winner"] = None
        rows = rows.rename(columns={"winner": "shootout_winner"})
        if verbose:
            print(f"  intl soccer: {gender} ledger {len(rows):,} matches, "
                  f"latest {rows['date'].max().date()}", flush=True)
        frames.append(rows)
    return pd.concat(frames, ignore_index=True)


def _supplement(games: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Matches the ledgers do not carry, and a check that they still don't."""
    if not SUPPLEMENT.exists():
        return games
    extra = pd.read_csv(SUPPLEMENT, comment="#")
    extra["date"] = pd.to_datetime(extra["date"])

    key = ["date", "home_team", "away_team", "gender"]
    both = games.merge(extra[key], on=key, how="inner")
    if len(both):
        # Not an error: the upstream fix is the outcome this is hoping for. But
        # keeping the block after it would double every match in it.
        print(f"  intl soccer: {len(both)} supplement row(s) are in the ledger "
              f"now -- delete that block from {SUPPLEMENT.name}", flush=True)
        extra = extra.merge(both[key], on=key, how="left", indicator=True)
        extra = extra[extra["_merge"] == "left_only"].drop(columns="_merge")
    if extra.empty:
        return games
    if verbose:
        print(f"  intl soccer: + {len(extra)} supplied match(es) the ledgers "
              f"lack ({', '.join(sorted(extra['tournament'].unique()))})", flush=True)
    return pd.concat([games, extra], ignore_index=True)


def _classify(games: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    ladder = pd.read_csv(LADDER, comment="#")
    joined = games.merge(ladder, on=["gender", "tournament"], how="left")
    return joined[joined["rung"].notna()].copy(), joined[joined["rung"].isna()]


def _report_dropped(dropped: pd.DataFrame, since: int = 2015) -> None:
    """Names the ladder does not carry, so a competition cannot go missing."""
    if dropped.empty:
        return
    recent = dropped[dropped["date"] >= f"{since}-01-01"]
    names = recent.groupby("tournament").size().sort_values(ascending=False)
    if names.empty:
        return
    print(f"  intl soccer: {len(names)} tournament name(s) since {since} are not "
          f"on the ladder and score nothing -- {', '.join(names.head(4).index)}"
          f"{' ...' if len(names) > 4 else ''}", flush=True)


def _assign_season(rows: pd.DataFrame) -> pd.DataFrame:
    """Which league year each match scores in. Two rules, by phase.

    A **block** -- a group stage running directly into a knockout, played as
    one tournament -- is scored whole into the year it began in, however long
    after that year's end it finishes. The 2027 Women's World Cup ends twelve
    days after the 2026-27 year closes and belongs to it entirely, paying the
    rosters that held those teams when it kicked off. Splitting one at a date
    nobody playing in it would recognise is worse than letting it finish
    outside the year, and the boundary really does fall inside them: Euro 2024
    ran 14 June to 14 July.

    A **windowed** phase -- every qualifying campaign, and the Nations Leagues'
    league phases -- scores where it was played. Those run across international
    windows months apart and line up with no league year reliably: the 2022-23
    UEFA Nations League opened in June 2022 and finished in June 2023, so a
    whole-block rule would have to pick a year and be wrong about half of it.
    """
    rows = rows.sort_values("date").reset_index(drop=True)
    rows["season"] = rows["date"].map(league_year)

    block = rows["phase"] == "block"
    if not block.any():
        return rows
    inside = rows[block]
    gap = inside.groupby(["gender", "competition"])["date"].diff()
    label = ((gap.isna()) | (gap > pd.Timedelta(days=BLOCK_GAP_DAYS))).cumsum()
    rows.loc[block, "season"] = inside.groupby(label)["season"].transform("first")
    return rows
