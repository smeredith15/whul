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


def row_key(row: dict, keys: tuple[str, ...],
            names: dict[str, str] | None = None) -> str:
    """One row's identity, as one string.

    Normalized, because the same match reaches this table from more than one
    source: tonight's feed, a database the history was seeded from, a list
    typed by hand. A key that treated those spellings as different would pay
    for the same win twice.

    ``names`` closes the gap normalizing cannot: case and spacing are the only
    differences it can settle, and two feeds naming the same person differently
    is not one of them. The Flashscore scraper resolves Carlos Alcaraz as
    "Carlos Alcaraz Garfia" and a list typed by hand calls him "Carlos
    Alcaraz"; both are in the alias table against the same player, and without
    reading it the ledger held his season as two players, 575 points under the
    name on the roster and 525 under one nobody holds.
    """
    if names:
        return KEY_SEPARATOR.join(
            _part(names.get(_part(row.get(k)), row.get(k))) for k in keys)
    return KEY_SEPARATOR.join(_part(row.get(k)) for k in keys)


def canonical_names(store: Store, source: str) -> dict[str, str]:
    """Every spelling of a player this source knows, mapped onto one of them.

    From the alias table, which is where the resolver has already recorded that
    two spellings are the same person. Only the ones it has judged: a name it
    has never seen is left as it is, because guessing that two similar names
    are one player is the mistake this whole file is written to avoid.

    Which spelling wins is decided by the ledger rather than by us -- the one
    already holding rows is the one the rest fold onto, so a list arriving
    later defers to what is stored instead of renaming it.
    """
    aliases = store.query(
        "SELECT a.source_key, a.asset_id, s.display_name FROM asset_aliases a "
        "JOIN assets s ON s.asset_id = a.asset_id WHERE a.source = ?",
        (source,),
    )
    if aliases.empty:
        return {}
    spellings: dict[str, list[str]] = {}
    for row in aliases.itertuples():
        # The roster's own spelling counts as one of them. The alias table
        # records what the *feed* called him, and a list typed by hand uses
        # the name on the roster -- which is how "Carlos Alcaraz" and "Carlos
        # Alcaraz Garfia" sat in the same ledger as two players.
        for name in (row.source_key, row.display_name):
            if str(name).strip():
                spellings.setdefault(str(row.asset_id), []).append(str(name))

    held = store.query(
        "SELECT payload FROM feed_rows WHERE source = ?", (source,))
    seen: dict[str, int] = {}
    for payload in held["payload"] if not held.empty else []:
        try:
            row = json.loads(payload)
        except (TypeError, ValueError):
            continue
        for value in row.values():
            if isinstance(value, str) and value.strip():
                key = _part(value)
                seen[key] = seen.get(key, 0) + 1

    out: dict[str, str] = {}
    for variants in spellings.values():
        if len(variants) < 2:
            continue
        # Most seen in the ledger, then longest, then alphabetical: a rule that
        # gives the same answer every run, whatever order the aliases arrive in.
        best = max(variants, key=lambda name: (seen.get(_part(name), 0),
                                               len(name), name))
        for name in variants:
            if _part(name) != _part(best):
                out[_part(name)] = best
    return out


def canonical_row(row: dict, keys: tuple[str, ...],
                  names: dict[str, str] | None) -> dict:
    """The row with its identity fields spelled the way the ledger spells them.

    The key alone is not enough. A match only the typed list holds keeps its
    own payload, and the scorer reads the payload -- so Carlos Alcaraz's first
    two rounds went on scoring under the name the list used while the rest of
    his tournament scored under the feed's, and the roster holds one of them.

    Only the identity fields, and only the spellings the alias table has
    already judged to be the same person.
    """
    if not names:
        return row
    out = dict(row)
    for key in keys:
        value = out.get(key)
        if isinstance(value, str):
            wanted = names.get(_part(value))
            if wanted:
                out[key] = wanted
    return out


def record(store: Store, source: str, frame: pd.DataFrame,
           keys: tuple[str, ...], now: str | None = None,
           overwrite: bool = True, names: dict[str, str] | None = None) -> int:
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
        clean = canonical_row(clean, keys, names)
        rows.append((
            source, row_key(clean, keys), str(clean.get("season") or ""),
            json.dumps(clean, default=str), stamp, stamp,
        ))
    _say_if_it_collapsed(source, keys, frame, rows)
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


#: Keys that turned out not to tell two rows apart, for the ingest report.
COLLAPSES: list[str] = []

#: How many to name before the count stands in for the rest.
COLLAPSES_SHOWN = 3


def take_collapses() -> list[str]:
    """Drain what the last pull's keys could not distinguish."""
    said, COLLAPSES[:] = list(COLLAPSES), []
    return said


def _say_if_it_collapsed(source: str, keys: tuple[str, ...],
                         frame: pd.DataFrame, rows: list[tuple]) -> None:
    """Name a key that put two different rows in one place.

    A key column that is absent raises -- there is no reading of that which is
    correct. A key column that is present and blank does not: motorsport's
    ``session`` is empty for every race that is not a sprint, and that is the
    key working, not failing. The two are only distinguishable by what the key
    does, so this looks at that: rows going in, distinct keys coming out, and
    the difference named where there is one.

    Reported rather than raised. A feed serving the same game twice is a real
    possibility and losing the ledger over it would cost more than it saves --
    but a key that silently keeps one row of two is exactly the quiet
    understatement everything here exists to prevent, so it is said out loud.
    """
    distinct = len({key for _, key, _, _, _, _ in rows})
    if distinct >= len(rows):
        return
    blank = [k for k in keys if not frame[k].map(lambda v: bool(_part(v))).any()]
    because = (f" -- {', '.join(blank)} is blank on every row"
               if blank else "")
    COLLAPSES.append(
        f"{source}: {len(rows)} row(s) share {distinct} key(s) on "
        f"({', '.join(keys)}){because}; the ledger keeps one of each"
    )


def load(store: Store, source: str) -> pd.DataFrame:
    """Every row ever recorded for a source, as the source produced them."""
    held = store.query(
        "SELECT payload FROM feed_rows WHERE source = ? ORDER BY first_seen, row_key",
        (source,),
    )
    if held.empty:
        return pd.DataFrame()
    return pd.DataFrame([json.loads(p) for p in held["payload"]])


def rekey(store: Store, source: str, keys: tuple[str, ...],
          names: dict[str, str] | None = None) -> int:
    """Move rows written under a superseded key scheme onto the current one.

    A ledger row is only unique for the key it was written under, so changing
    what identifies a row leaves every earlier row filed under a name nothing
    will ever collide with -- and the union then hands the same match back
    twice. Coco Gauff's US Open was scored as nine matches: the four her
    tournament was re-recorded under both schemes, counted once each way. Her
    Grand Slam total read 1500 where the tour's own list says 800.

    Re-keyed rather than deleted, because a row the feed has stopped serving
    is exactly what this table exists to keep: dropping the old copy of a match
    that has aged out of the window would lose it for good. Where the newer
    scheme already holds the match, the older row folds into it and the earlier
    sighting wins -- that date is the only record of when it was played that
    survives the feed forgetting.
    """
    held = store.query(
        "SELECT row_key, payload, first_seen FROM feed_rows WHERE source = ?",
        (source,),
    )
    if held.empty:
        return 0
    stale = []
    for row in held.itertuples():
        try:
            payload = json.loads(row.payload)
        except (TypeError, ValueError):
            continue
        payload = canonical_row(payload, keys, names)
        wanted = row_key(payload, keys)
        if wanted != row.row_key or json.dumps(payload, default=str) != row.payload:
            stale.append((row.row_key, wanted,
                          json.dumps(payload, default=str), str(row.first_seen)))
    if not stale:
        return 0
    with store.transaction() as conn:
        for was, wanted, payload, first in stale:
            conn.execute(
                "INSERT INTO feed_rows (source, row_key, season, payload, "
                "first_seen, last_seen) "
                "SELECT ?, ?, season, ?, ?, last_seen FROM feed_rows "
                "WHERE source = ? AND row_key = ? "
                "ON CONFLICT (source, row_key) DO UPDATE SET "
                "  first_seen = MIN(feed_rows.first_seen, excluded.first_seen)",
                (source, wanted, payload, first, source, was),
            )
            if wanted != was:
                conn.execute(
                    "DELETE FROM feed_rows WHERE source = ? AND row_key = ?",
                    (source, was),
                )
    return len(stale)


def merge(store: Store, source: str, frame: pd.DataFrame,
          keys: tuple[str, ...]) -> pd.DataFrame:
    """Record what the feed is showing now, and return everything it ever has.

    The union, not the window. A feed with nothing in its window today still
    answers for the season, which is the difference between a quiet week and a
    season that unhappened.
    """
    names = canonical_names(store, source)
    moved = rekey(store, source, keys, names)
    if moved:
        # A correction to what is stored, not a fact about tonight's feed, so
        # it is said out loud once rather than left to be inferred from a
        # total that quietly halved.
        print(f"  {source}: {moved} row(s) were filed under a superseded key "
              f"and were being counted twice; moved onto the current one",
              flush=True)
    record(store, source, frame, keys, names=names)
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

    Keyed through the alias table, because a list typed by hand uses the name
    on the roster and the feed uses whatever its scraper resolved. Without that
    the two never collide, and "adds only what is missing" adds a second copy
    of a match that was already there under another spelling of the same
    player.
    """
    held = read_seed(source, root)
    if held.empty:
        return 0
    # Adds only what is missing. The seed overlaps the feed -- both cover the
    # last week of it -- and where they describe the same match the one the
    # feed wrote down is the one to keep.
    return record(store, source, held, keys, overwrite=False,
                  names=canonical_names(store, source))
