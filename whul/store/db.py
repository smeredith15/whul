"""The scoring store.

SQLite today, Postgres later: the schema is written in portable SQL and this
module keeps the dialect-specific parts in one place -- the connection, the
parameter style, and the upsert. A five-manager season is on the order of
660,000 score rows, which SQLite handles without effort, so the server is a
deployment decision rather than a development one.

Everything is stored as text and read back as text. Dates are ISO-8601 strings
because both engines sort them correctly and neither needs a driver-specific
type to do it, and stats are JSON in a TEXT column for the same reason.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import pandas as pd

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_VERSION = 1
DEFAULT_PATH = Path("data/whul.sqlite3")


def _now() -> str:
    """Now, to the millisecond.

    Second resolution is not enough. Two benchmark versions frozen in the same
    second tie on ``frozen_at``, and the tie decides which scale the standings
    are measured against -- which must never come down to which row the engine
    happened to return first.
    """
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _as_text(value: Any) -> Any:
    """Dates as ISO strings; everything else unchanged."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the database, creating its directory if needed.

    ``:memory:`` is passed through, which is what the tests use.
    """
    target = str(path or DEFAULT_PATH)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    # Off by default in SQLite, and the schema is full of them.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def apply_schema(conn: sqlite3.Connection) -> int:
    """Create anything missing and record the version.

    Every statement is ``IF NOT EXISTS``, so this is safe to run on every
    startup and is how a new table reaches an existing database.
    """
    conn.executescript(SCHEMA_PATH.read_text())
    _add_missing_columns(conn)
    current = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    if current is None or current["v"] is None:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, _now()),
        )
    elif current["v"] < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, _now()),
        )
    conn.commit()
    return SCHEMA_VERSION


#: Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS does
#: nothing to a table that already exists, so a new column needs its own step
#: or an existing database silently stays one version behind.
ADDED_COLUMNS = (
    ("slot_occupancy", "cost", "REAL"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> list[str]:
    added = []
    for table, column, kind in ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            added.append(f"{table}.{column}")
    return added


@dataclass
class Store:
    """A thin wrapper over the connection.

    Deliberately not an ORM. The queries here are a handful of inserts and a
    few aggregate reads, all of which are clearer as SQL than as objects, and
    an ORM would put a translation layer between the schema and the code that
    has to explain it.
    """

    conn: sqlite3.Connection

    # -- writing ----------------------------------------------------------
    def upsert(self, table: str, rows: Iterable[dict], keys: Sequence[str]) -> int:
        """Insert rows, replacing any that collide on the primary key.

        ``keys`` names the conflict target so the statement reads the same as
        the Postgres form will; SQLite infers it from the primary key, but
        spelling it out keeps the two dialects one edit apart.
        """
        rows = [r for r in rows]
        if not rows:
            return 0
        columns = list(rows[0])
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{c} = excluded.{c}" for c in columns if c not in keys
        )
        conflict = ", ".join(keys)
        statement = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
            if updates
            else f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
                 f"ON CONFLICT ({conflict}) DO NOTHING"
        )
        payload = [tuple(_as_text(r[c]) for c in columns) for r in rows]
        with self.transaction():
            self.conn.executemany(statement, payload)
        return len(payload)

    def insert_frame(
        self, table: str, frame: pd.DataFrame, keys: Sequence[str]
    ) -> int:
        """Upsert a data frame, which is what the pipeline actually holds."""
        if frame is None or frame.empty:
            return 0
        return self.upsert(table, frame.to_dict("records"), keys)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit on success, roll back on anything raised.

        A half-written day is worse than no day: the standings would be scored
        from a roster that never existed.
        """
        try:
            yield self.conn
        except Exception:
            self.conn.rollback()
            raise
        self.conn.commit()

    # -- reading ----------------------------------------------------------
    def query(self, sql: str, params: Sequence = ()) -> pd.DataFrame:
        """A query as a data frame, which is what the scoring code consumes.

        SQL NULL comes back as ``None``, not NaN. pandas substitutes NaN only
        because of its own type system, and NaN in a text column has caused the
        same bug four times in this codebase: NaN is *truthy*, so
        ``if row.asset_id`` passes for a slot with nobody in it, and
        ``str(x or "")`` yields the string ``"nan"``. Fixing it here rather
        than at each call site is the only version of the fix that stays fixed.

        Numeric columns keep NaN, where it means what it says: a number that is
        not there is not the same as zero.
        """
        frame = pd.read_sql_query(sql, self.conn, params=tuple(params))
        for column in frame.columns:
            # Both dtypes, not just object: pandas 3 gives text columns the
            # ``str`` dtype, whose missing value is still NaN and still truthy.
            # Testing only for object silently skipped every text column on a
            # modern pandas, which is how this survived a first attempt at the
            # fix.
            if pd.api.types.is_object_dtype(frame[column]) or pd.api.types.is_string_dtype(
                frame[column]
            ):
                frame[column] = frame[column].astype(object).where(
                    frame[column].notna(), None
                )
        return frame

    def scalar(self, sql: str, params: Sequence = ()):
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return row[0] if row else None

    # -- ingest -----------------------------------------------------------
    def record_stats(
        self,
        rows: Iterable[dict],
        source: str,
        season: str,
        as_of: date | str,
        league: str,
        phase: str = "regular",
    ) -> int:
        """Append one day's season-to-date figures.

        Each row needs an ``asset_id``; every other column is kept as the JSON
        payload, so a feed adding a stat needs no migration.
        """
        when = _as_text(as_of)
        fetched = _now()
        payload = []
        for row in rows:
            asset_id = row.get("asset_id")
            if not asset_id:
                continue
            stats = {k: v for k, v in row.items() if k != "asset_id"}
            payload.append({
                "asset_id": asset_id,
                "league": league,
                "season": season,
                "as_of": when,
                "source": source,
                "phase": phase,
                "stats": json.dumps(stats, default=str),
                "fetched_at": fetched,
            })
        written = self.upsert(
            "raw_stats", payload,
            keys=("asset_id", "season", "as_of", "source", "phase"),
        )
        self.record_source_status(
            source, league, ok=True, rows=written, last_data_date=when
        )
        return written

    def read_stats(
        self, season: str, as_of: date | str, league: str | None = None
    ) -> pd.DataFrame:
        """One day's stats, with the JSON payload expanded back into columns."""
        sql = "SELECT * FROM raw_stats WHERE season = ? AND as_of = ?"
        params: list = [season, _as_text(as_of)]
        if league:
            sql += " AND league = ?"
            params.append(league)
        rows = self.query(sql, params)
        if rows.empty:
            return rows
        expanded = pd.json_normalize(rows["stats"].map(json.loads))
        expanded.index = rows.index
        return pd.concat([rows.drop(columns=["stats"]), expanded], axis=1)

    def record_source_status(
        self,
        source: str,
        league: str,
        ok: bool,
        rows: int = 0,
        last_data_date: str | None = None,
        message: str = "",
    ) -> None:
        self.upsert(
            "source_status",
            [{
                "source": source, "league": league,
                "last_run_at": _now(), "last_data_date": last_data_date,
                "last_ok": int(ok), "rows_last_run": rows, "message": message,
            }],
            keys=("source", "league"),
        )

    def stale_sources(self, as_of: date | str, max_age_days: int = 2) -> pd.DataFrame:
        """Sources whose most recent data is older than they should allow.

        A feed that stops updating leaves standings frozen and plausible, which
        is why this is a query the nightly job runs rather than something a
        person notices weeks later.
        """
        cutoff = pd.Timestamp(_as_text(as_of)) - pd.Timedelta(days=max_age_days)
        status = self.query("SELECT * FROM source_status")
        if status.empty:
            return status
        status["last_data_date"] = pd.to_datetime(
            status["last_data_date"], errors="coerce"
        )
        stale = status["last_data_date"].isna() | (status["last_data_date"] < cutoff)
        return status[stale | (status["last_ok"] == 0)].reset_index(drop=True)


def open_store(path: Path | str | None = None) -> Store:
    """Open a database and bring its schema up to date."""
    conn = connect(path)
    apply_schema(conn)
    return Store(conn)
