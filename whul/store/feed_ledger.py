"""What a windowed feed said, kept after it stops saying it.

A feed that serves a rolling window cannot be asked about the start of the
season. Flashscore's tennis feed reaches seven days either side of today, so a
total recomputed from it each night is a one-week figure wearing a season's
name -- and the standings ledger, which differences consecutive days, reads
every roll-off as a loss by a player who did nothing.

So the rows are written down as they go past. This is deliberately generic: it
stores a source's rows as the source produced them, keyed on the feed's own
identifier, and hands them all back on the next run. Nothing here knows what a
tennis match is.
"""

from __future__ import annotations

import json

from datetime import datetime, timezone

import pandas as pd

from whul.store.db import Store

#: Joins the parts of a compound key. A unit separator, because it cannot occur
#: in a feed's own identifiers the way a hyphen or a colon can.
KEY_SEPARATOR = "\x1f"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def row_key(row: dict, keys: tuple[str, ...]) -> str:
    """The feed's own identifier for a row, as one string."""
    return KEY_SEPARATOR.join(str(row.get(k, "")) for k in keys)


def record(store: Store, source: str, frame: pd.DataFrame,
           keys: tuple[str, ...], now: str | None = None) -> int:
    """Write down every row, keeping the first sighting of each.

    A row already held has its payload replaced -- a result the feed corrects
    should win -- and its ``first_seen`` left alone, which is the only record
    of when it was actually played that survives the feed forgetting it.
    """
    if frame is None or frame.empty:
        return 0
    missing = [k for k in keys if k not in frame.columns]
    if missing:
        raise KeyError(
            f"{source} rows have no {', '.join(missing)} to key on; a ledger "
            f"keyed on the wrong column would merge rows that are not the same"
        )
    stamp = now or _now()
    rows = []
    for payload in frame.to_dict("records"):
        clean = {k: (None if pd.isna(v) else v) if not isinstance(v, (list, dict))
                 else v for k, v in payload.items()}
        rows.append((
            source, row_key(clean, keys), str(clean.get("season") or ""),
            json.dumps(clean, default=str), stamp, stamp,
        ))
    with store.transaction() as conn:
        conn.executemany(
            "INSERT INTO feed_rows (source, row_key, season, payload, "
            "first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (source, row_key) DO UPDATE SET "
            "payload = excluded.payload, season = excluded.season, "
            "last_seen = excluded.last_seen",
            rows,
        )
    return len(rows)


def load(store: Store, source: str) -> pd.DataFrame:
    """Every row ever recorded for a source, as the source produced them."""
    held = store.query(
        "SELECT payload FROM feed_rows WHERE source = ? ORDER BY first_seen, row_key",
        (source,),
    )
    if held.empty:
        return pd.DataFrame()
    return pd.DataFrame([json.loads(p) for p in held["payload"]])


def merge(store: Store, source: str, frame: pd.DataFrame,
          keys: tuple[str, ...]) -> pd.DataFrame:
    """Record what the feed is showing now, and return everything it ever has.

    The union, not the window. A feed with nothing in its window today still
    answers for the season, which is the difference between a quiet week and a
    season that unhappened.
    """
    record(store, source, frame, keys)
    held = load(store, source)
    return held if not held.empty else (frame if frame is not None else pd.DataFrame())
