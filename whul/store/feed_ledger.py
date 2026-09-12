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
import re

from datetime import datetime, timezone

import pandas as pd

from pathlib import Path

from whul.store.db import Store

#: Joins the parts of a compound key. A unit separator, because it cannot occur
#: in a feed's own identifiers the way a hyphen or a colon can.
KEY_SEPARATOR = "\x1f"

_SPACES = re.compile(r"\s+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _part(value) -> str:
    """One part of a key, in the one spelling every source will agree on.

    Case and spacing only. Two sources describing the same match will differ
    over "US Open" and "Us Open" and will not differ over which two players
    played it, so this closes the gap that is closeable and leaves the rest to
    be seen: a tournament the sources genuinely name differently arrives as two
    rows, which is visible, where a fuzzy match that guessed wrong would not be.
    """
    return _SPACES.sub(" ", str("" if value is None else value)).strip().casefold()


def row_key(row: dict, keys: tuple[str, ...]) -> str:
    """One row's identity, as one string.

    Normalized, because the same match reaches this table from more than one
    source: tonight's feed, a database the history was seeded from, a list
    typed by hand. A key that treated those spellings as different would pay
    for the same win twice.
    """
    return KEY_SEPARATOR.join(_part(row.get(k)) for k in keys)


def record(store: Store, source: str, frame: pd.DataFrame,
           keys: tuple[str, ...], now: str | None = None,
           overwrite: bool = True) -> int:
    """Write down every row, keeping the first sighting of each.

    A row already held has its payload replaced -- a result the feed corrects
    should win -- and its ``first_seen`` left alone, which is the only record
    of when it was actually played that survives the feed forgetting it.

    ``overwrite=False`` adds only what is absent, which is what a seed wants.
    A seed is a reconstruction: typed by hand, or read out of another
    application's database, and it overlaps whatever the feed has already
    written down. Where the two describe the same match the feed's copy is the
    better one -- it came from the source rather than from somebody reading a
    draw sheet -- so the seed fills gaps and defers to what is held.
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
    conflict = (
        "ON CONFLICT (source, row_key) DO UPDATE SET "
        "payload = excluded.payload, season = excluded.season, "
        "last_seen = excluded.last_seen"
        if overwrite else
        "ON CONFLICT (source, row_key) DO NOTHING"
    )
    with store.transaction() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM feed_rows WHERE source = ?", (source,)
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO feed_rows (source, row_key, season, payload, "
            f"first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?) {conflict}",
            rows,
        )
        after = conn.execute(
            "SELECT COUNT(*) FROM feed_rows WHERE source = ?", (source,)
        ).fetchone()[0]
    # What was added, not what was offered. A seed reloaded every night would
    # otherwise report its whole size as if it were news.
    return after - before if not overwrite else len(rows)


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


#: Where a source's history lives, for the part of it that predates the ledger.
#: In the repository rather than in the database, because the database is
#: rebuilt and force-pushed by three different workflows and the history has to
#: survive all of them. A file in git survives anything short of a revert.
SEED_DIR = Path("data/seed")


def seed_path(source: str, root: Path | None = None) -> Path:
    return (root or SEED_DIR) / f"{source}.jsonl"


def read_seed(source: str, root: Path | None = None) -> pd.DataFrame:
    """A source's committed history, or nothing if it has none."""
    path = seed_path(source, root)
    if not path.exists():
        return pd.DataFrame()
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def write_seed(source: str, frame: pd.DataFrame, keys: tuple[str, ...],
               root: Path | None = None) -> int:
    """Merge rows into a source's seed file, keeping it stable for git.

    Sorted and one row per line, so that adding a fortnight to a season shows
    up as a fortnight of new lines rather than a rewritten file nobody can
    review.
    """
    held = read_seed(source, root)
    frames = [f for f in (held, frame) if f is not None and not f.empty]
    if not frames:
        return 0
    both = pd.concat(frames, ignore_index=True)
    both["_key"] = [row_key(r, keys) for r in both.to_dict("records")]
    both = both.drop_duplicates(subset=["_key"], keep="last").drop(columns=["_key"])
    order = [c for c in ("date", "tournament", "round", "winner", "loser")
             if c in both.columns]
    if order:
        both = both.sort_values(order, kind="stable")
    path = seed_path(source, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(
        json.dumps({k: v for k, v in row.items() if pd.notna(v)},
                   sort_keys=True, default=str) + "\n"
        for row in both.to_dict("records")
    ))
    return len(both)


def apply_seed(store: Store, source: str, keys: tuple[str, ...],
               root: Path | None = None) -> int:
    """Load a source's committed history into the ledger.

    Run on every pull, not once: the ledger lives in a database that is rebuilt
    and force-pushed by three workflows, and a history that had to be restored
    by remembering to restore it is one that will eventually not be.
    """
    held = read_seed(source, root)
    if held.empty:
        return 0
    # Adds only what is missing. The seed overlaps the feed -- both cover the
    # last week of it -- and where they describe the same match the one the
    # feed wrote down is the one to keep.
    return record(store, source, held, keys, overwrite=False)
