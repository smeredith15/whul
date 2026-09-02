"""nflverse data access.

The R scripts used ``nflreadr``; these read the same underlying nflverse assets
directly, which keeps the dependency free and language-agnostic.

``load_schedules`` and ``load_teams`` read from raw.githubusercontent rather than
github.com/.../raw/... -- the latter is 403-blocked through the agent proxy.
"""

from __future__ import annotations

import pandas as pd

RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
NFLDATA = "https://raw.githubusercontent.com/nflverse/nfldata/master/data"


def load_player_stats(seasons: list[int]) -> pd.DataFrame:
    """Weekly offensive player stats, one parquet per season."""
    return pd.concat(
        [pd.read_parquet(f"{RELEASES}/player_stats/player_stats_{y}.parquet") for y in seasons],
        ignore_index=True,
    )


def load_schedules(seasons: list[int] | None = None) -> pd.DataFrame:
    """Game results for every season; optionally filtered."""
    df = pd.read_csv(f"{NFLDATA}/games.csv", low_memory=False)
    return df[df["season"].isin(seasons)].copy() if seasons else df


def load_teams(seasons: list[int] | None = None) -> pd.DataFrame:
    """Season-aware ``team_abbr`` -> ``team_division`` mapping.

    Derived from standings.csv because it tracks divisions per season, so
    relocations and realignments resolve correctly for historical years.
    """
    df = pd.read_csv(f"{NFLDATA}/standings.csv")
    if seasons:
        df = df[df["season"].isin(seasons)]
    return (
        df[["season", "team", "division"]]
        .rename(columns={"team": "team_abbr", "division": "team_division"})
        .drop_duplicates()
        .reset_index(drop=True)
    )
