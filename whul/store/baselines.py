"""What a cumulative feed had already counted when the league year opened.

Most feeds report season to date, and the ones that matter here will not serve
a date range: the MLB Stats API's sabermetrics type ignores the dates it is
given, Baseball Savant's leaderboards ignore them too, and FanGraphs -- which
would serve them -- refuses a datacenter address. So the share of a season that
belongs to this league year cannot be *asked* for.

It can be subtracted. Record each asset's figures once, on the first day it is
rostered, and every later pull minus that baseline is what it earned since:

    year N      = (season to date now) - (baseline taken at the year's start)
    year N + 1  = season to date, because that season began inside the league
                  year and its baseline is zero
    contribution = the two, summed

Exact rather than approximate, for every cumulative figure the feed carries --
counting stats, run values and WAR alike -- and it needs nothing from the feed
beyond what it already gives.

The baseline is written once and never updated. One that moved would silently
rewrite every score derived from it, and being the fixed point is the whole of
its job.
"""

from __future__ import annotations

import json

import pandas as pd

from whul.store.db import Store, _now

#: Columns that identify a row rather than accumulate, and so are never
#: differenced. Subtracting a season number or a player id is meaningless, and
#: doing it silently would be worse than failing.
NOT_CUMULATIVE = {
    "season", "player", "player_id", "playerid", "PlayerName", "playername",
    "Name", "name", "team", "team_name", "league", "role", "asset_id",
    "position", "Pos", "_phase", "advanced_share",
}


def record(
    store: Store,
    asset_id: str,
    season: str,
    source: str,
    feed_season: int,
    stats: dict,
    captured_for: str,
) -> bool:
    """Store a baseline, unless one is already held. True when one was written.

    ``INSERT OR IGNORE`` rather than an upsert: the second call for an asset is
    the ordinary case -- every night after the first -- and it must leave the
    first day's figures exactly as they were.
    """
    with store.transaction() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO stat_baselines "
            "(asset_id, season, source, feed_season, captured_at, captured_for, stats) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (asset_id, season, source, int(feed_season), _now(),
             str(captured_for), json.dumps(stats, default=str)),
        )
        return cursor.rowcount > 0


def load(store: Store, season: str, source: str, feed_season: int) -> dict[str, dict]:
    """``{asset_id: figures}`` for one feed season of one league year."""
    rows = store.query(
        "SELECT asset_id, stats FROM stat_baselines "
        "WHERE season = ? AND source = ? AND feed_season = ?",
        (season, source, int(feed_season)),
    )
    if rows.empty:
        return {}
    return {
        str(row.asset_id): json.loads(row.stats) for row in rows.itertuples()
    }


def subtract(frame: pd.DataFrame, baselines: dict[str, dict],
             id_col: str = "asset_id") -> pd.DataFrame:
    """Take each asset's baseline off its season-to-date figures.

    Only the numeric columns, and only the ones the baseline carries. A figure
    the baseline does not have is left whole, which is right: it means the feed
    started reporting it after the baseline was taken, and all of it was earned
    inside the league year.

    Negative results are kept. A run value can genuinely fall -- a player who
    was above average in July and below it since has earned negative value in
    the window, and clamping that at zero would pay him for a bad month.
    """
    if frame is None or frame.empty or not baselines or id_col not in frame.columns:
        return frame

    out = frame.copy()
    for column in out.columns:
        if column in NOT_CUMULATIVE:
            continue
        values = pd.to_numeric(out[column], errors="coerce")
        if values.isna().all():
            continue
        was = out[id_col].map(
            lambda a: pd.to_numeric(
                pd.Series([baselines.get(str(a), {}).get(column)]), errors="coerce"
            ).iloc[0]
        )
        out[column] = (values - was.fillna(0.0)).where(values.notna(), out[column])
    return out


def combine_seasons(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Sum an asset's figures across the calendar seasons a league year spans.

    A league year that opens in August covers the tail of one season and the
    front of the next, and a manager holds the player through both. Scored one
    season at a time they would appear twice, each half measured against a
    benchmark drawn from whole years -- so the halves are added first and
    scored once.
    """
    if frame is None or frame.empty:
        return frame

    present = [k for k in keys if k in frame.columns]
    if not present:
        return frame

    numeric, carried = [], []
    for column in frame.columns:
        if column in present:
            continue
        if column in NOT_CUMULATIVE or pd.to_numeric(
            frame[column], errors="coerce"
        ).isna().all():
            carried.append(column)
        else:
            numeric.append(column)

    # ``dropna=False``: a blank conference or a missing role is a key value
    # like any other, and dropping those rows would lose the asset entirely.
    summed = frame.groupby(present, as_index=False, dropna=False)[numeric] \
        .sum(min_count=1) \
        if numeric else frame[present].drop_duplicates()
    if carried:
        # The first value wins for anything that does not add: a name is the
        # same in both halves, and a season number keeps the earlier of the two
        # -- the row now spans both, and the earlier one is where the league
        # year's share of this asset started.
        first = frame.groupby(present, as_index=False, dropna=False)[carried].first()
        summed = summed.merge(first, on=present, how="left")
    return summed.reset_index(drop=True)


def usable(
    store: Store, season: str, source: str, feed_season: int,
    opens, grace_days: int = 2,
) -> dict[str, dict]:
    """Baselines taken early enough to stand for the league year's opening.

    A baseline is only the year's starting state if it was taken at the start.
    One recorded three weeks in describes a player who has already been
    accumulating, and subtracting it would credit a manager with none of what
    their player did in between -- an error that looks like a quiet slump
    rather than a bug.

    Late ones are kept rather than deleted: they are the honest record of when
    differencing became possible, and next league year's baseline is taken on
    day one, where it is right.
    """
    from datetime import date, timedelta

    rows = store.query(
        "SELECT asset_id, stats, captured_at, captured_for FROM stat_baselines "
        "WHERE season = ? AND source = ? AND feed_season = ?",
        (season, source, int(feed_season)),
    )
    if rows.empty:
        return {}

    if isinstance(opens, str):
        opens = date.fromisoformat(opens)
    cutoff = opens + timedelta(days=grace_days)

    held = {}
    for row in rows.itertuples():
        taken = str(row.captured_at)[:10]
        try:
            if date.fromisoformat(taken) > cutoff:
                continue
        except ValueError:
            continue
        held[str(row.asset_id)] = json.loads(row.stats)
    return held
