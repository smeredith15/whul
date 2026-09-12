"""hoopR-data access (NBA historical).

WARNING: the sportsdataverse/hoopR-data repository was archived on 2026-08-07 and
its NBA files stop at the 2023 season. It is usable for building historical
benchmarks but cannot back live daily scoring -- use ``whul.sources.espn`` for
that. The archived schedule files also contain regular-season games only, so any
postseason terms computed from them will be zero.
"""

from __future__ import annotations

import pandas as pd

BASE = "https://raw.githubusercontent.com/sportsdataverse/hoopR-data/main/nba"
LAST_AVAILABLE_SEASON = 2023


def load_schedule(seasons: list[int]) -> pd.DataFrame:
    return pd.concat(
        [pd.read_parquet(f"{BASE}/schedules/parquet/nba_schedule_{y}.parquet") for y in seasons],
        ignore_index=True,
    )


def load_player_box(seasons: list[int]) -> pd.DataFrame:
    return pd.concat(
        [pd.read_parquet(f"{BASE}/player_box/parquet/player_box_{y}.parquet") for y in seasons],
        ignore_index=True,
    )
