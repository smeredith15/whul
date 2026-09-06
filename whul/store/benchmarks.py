"""Freezing and reading benchmark sets.

A benchmark is the number a raw score is divided by to reach the 0-100 scale,
so changing one restates every score measured against it. That is fine before a
season and unacceptable during one: a manager who was on 2,100 yesterday should
not be on 2,050 today because a formula moved underneath them.

So a benchmark set is a stored, versioned artifact. Computing one writes a new
version; freezing it makes it the one standings are scored against; and a frozen
version is never edited -- superseding it means a new version, which leaves both
on the record and makes the change explainable rather than invisible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from whul.config.league import BENCHMARK_MANAGER_COUNT, BENCHMARK_QUANTILE
from whul.normalize import compute_benchmarks
from whul.store.db import Store, _now


class FrozenBenchmarkError(RuntimeError):
    """Raised when something would change a version the standings depend on."""


@dataclass(frozen=True)
class BenchmarkVersion:
    version: str
    season: str
    quantile: float
    managers: int
    computed_at: str
    frozen_at: str | None
    notes: str

    @property
    def is_frozen(self) -> bool:
        return bool(self.frozen_at)


def make_version_id(season: str, when: date | datetime | None = None) -> str:
    """A version id that sorts chronologically and reads as what it is."""
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{season}-{stamp}"


def compute(
    scored: pd.DataFrame,
    asset_type: str,
    season: str,
    season_col: str | None = "season",
    managers: int = BENCHMARK_MANAGER_COUNT,
) -> pd.DataFrame:
    """Benchmarks for one asset type, ready to store.

    Takes scored historical rows -- regular-season production only, which is
    what ``whul.validate`` already assembles -- and returns the per-group
    percentiles with the pool size each was drawn from. The pool size is stored
    because a benchmark from four players and one from sixty deserve different
    amounts of trust, and only the row can say which this was.
    """
    bench = compute_benchmarks(scored, asset_type, managers=managers, season_col=season_col)
    if bench.empty:
        return pd.DataFrame(columns=["asset_type", "norm_key", "benchmark", "pool_size", "seasons"])

    seasons = ""
    if season_col and season_col in scored.columns:
        years = sorted(str(s) for s in scored[season_col].dropna().unique())
        seasons = ",".join(years)

    out = bench.copy()
    out["asset_type"] = asset_type
    out["seasons"] = seasons
    # compute_benchmarks names it n_in_pool; the stored column is pool_size.
    # Carrying it through matters: a benchmark drawn from four players and one
    # drawn from sixty deserve different amounts of trust, and only the row can
    # say which this was.
    out["pool_size"] = out["n_in_pool"].astype(int)
    keep = ["asset_type", "norm_key", "benchmark", "pool_size", "seasons"]
    return out[keep].reset_index(drop=True)


def save(
    store: Store,
    benchmarks: pd.DataFrame,
    season: str,
    version: str | None = None,
    notes: str = "",
    quantile: float = BENCHMARK_QUANTILE,
    managers: int = BENCHMARK_MANAGER_COUNT,
) -> str:
    """Store a benchmark set as a new, unfrozen version.

    Unfrozen on purpose: computing a set and adopting it are separate acts, so
    a set can be inspected and compared against the live one before anything
    starts scoring against it.
    """
    if benchmarks is None or benchmarks.empty:
        raise ValueError("refusing to save an empty benchmark set")

    version = version or make_version_id(season)
    if get_version(store, version):
        raise ValueError(f"benchmark version {version!r} already exists")

    store.upsert(
        "benchmark_versions",
        [{
            "version": version, "season": season, "quantile": quantile,
            "managers": managers, "computed_at": _now(), "frozen_at": None,
            "notes": notes,
        }],
        keys=("version",),
    )
    rows = benchmarks.assign(version=version)
    store.insert_frame("benchmarks", rows, keys=("version", "asset_type", "norm_key"))
    return version


def adopt(store: Store, source_db: str, version: str) -> tuple[str, int]:
    """Copy one benchmark version out of another database, as a draft.

    A scale is computed where the feeds are reachable and a laptop can run for
    hours; the results it will score live in the database the nightly job owns.
    Those are different files, and the only bridge was to publish the whole
    local database over the branch -- which loses every day the job recorded in
    the meantime, and which is why a computed scale reached the site on none of
    the first three attempts.

    Arrives unfrozen, like every other way a version arrives. Copying a scale
    and adopting it stay separate acts.
    """
    import sqlite3

    if not Path(source_db).exists():
        raise ValueError(f"no database at {source_db}")
    # Read-only, so a mistyped path cannot write a schema into some other file
    # and so the laptop's copy is untouched by publishing from it.
    conn = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row   # as `connect` does; the readers want names
    try:
        other = Store(conn)
        record = get_version(other, version)
        if record is None:
            raise ValueError(
                f"{source_db} has no benchmark version {version!r}. "
                f"`benchmarks versions --db {source_db}` lists what it does have."
            )
        rows = load(other, version)
    finally:
        conn.close()
    if rows.empty:
        raise ValueError(f"{version!r} exists in {source_db} but holds no benchmarks")
    if get_version(store, version):
        raise ValueError(
            f"{version!r} is already here; nothing to copy. "
            f"`benchmarks versions` says whether it is frozen."
        )

    store.upsert(
        "benchmark_versions",
        [{
            "version": version, "season": record.season,
            "quantile": record.quantile, "managers": record.managers,
            "computed_at": record.computed_at, "frozen_at": None,
            "notes": record.notes,
        }],
        keys=("version",),
    )
    store.insert_frame(
        "benchmarks", rows.assign(version=version),
        keys=("version", "asset_type", "norm_key"),
    )
    return record.season, len(rows)


def extend(
    store: Store, version: str, benchmarks: pd.DataFrame, notes: str = ""
) -> BenchmarkVersion:
    """Add or replace groups in an existing unfrozen version.

    Twenty leagues cannot always be pulled in one sitting -- a feed goes down, a
    laptop sleeps -- and a second ``save`` would make a second version with a
    different set of holes in it. Extending keeps one version growing until it
    covers the roster and can be frozen.

    Refuses on a frozen version. Once standings are measured against a set,
    adding a group to it would restate scores that were already published;
    superseding it means a new version, which leaves both on the record.
    """
    existing = get_version(store, version)
    if existing is None:
        raise ValueError(f"no benchmark version {version!r}")
    if existing.is_frozen:
        raise FrozenBenchmarkError(
            f"{version} was frozen at {existing.frozen_at}; standings are measured "
            f"against it. Compute a new version instead of adding to this one."
        )
    if benchmarks is None or benchmarks.empty:
        raise ValueError("refusing to extend with an empty benchmark set")

    rows = benchmarks.assign(version=version)
    store.insert_frame("benchmarks", rows, keys=("version", "asset_type", "norm_key"))
    if notes:
        with store.transaction() as conn:
            conn.execute(
                "UPDATE benchmark_versions SET notes = ? WHERE version = ?",
                (notes, version),
            )
    return get_version(store, version)


def _free_version_id(store: Store, season: str) -> str:
    """A version id nothing is using yet.

    Ids carry a timestamp to the second, and a derive that lands in the same
    second as the version it copies would produce that same id -- whereupon the
    upsert would overwrite the source's row and set ``frozen_at`` to NULL,
    silently unfreezing the scale the standings are measured against. Rare, and
    catastrophic when it happens, which is the combination worth ruling out
    rather than hoping about.
    """
    base = make_version_id(season)
    version, attempt = base, 1
    while get_version(store, version) is not None:
        attempt += 1
        version = f"{base}-{attempt}"
    return version


def derive(
    store: Store, source: str, season: str | None = None, notes: str = ""
) -> str:
    """Start a new unfrozen version holding everything an existing one holds.

    A frozen scale is never edited, so correcting one league's benchmark means a
    new version -- and a new version has to cover the whole roster or the freeze
    is refused, which would mean recomputing twenty leagues to change one. Some
    of those are date-walked and take hours, so in practice the correction does
    not get made.

    Copying the rows first makes it cheap: derive, recompute the one league into
    it, compare, freeze. The copied rows carry the seasons they were drawn from,
    because that is still true of them -- what changed is the one group that was
    recomputed, and ``benchmarks compare`` is what shows which.

    The provenance goes in the notes rather than being left to memory. Anyone
    reading the frozen scale later should be able to see that most of it was
    inherited unchanged.
    """
    origin = get_version(store, source)
    if origin is None:
        raise ValueError(f"no benchmark version {source!r}")

    rows = store.query(
        "SELECT asset_type, norm_key, benchmark, pool_size, seasons "
        "FROM benchmarks WHERE version = ?",
        (source,),
    )
    if rows.empty:
        raise ValueError(f"benchmark version {source!r} holds no benchmarks")

    season = season or origin.season
    version = _free_version_id(store, season)
    provenance = f"derived from {source}"
    store.upsert(
        "benchmark_versions",
        [{
            "version": version, "season": season, "quantile": origin.quantile,
            "managers": origin.managers, "computed_at": _now(), "frozen_at": None,
            "notes": f"{provenance}; {notes}" if notes else provenance,
        }],
        keys=("version",),
    )
    store.insert_frame(
        "benchmarks", rows.assign(version=version),
        keys=("version", "asset_type", "norm_key"),
    )
    return version


def freeze(store: Store, version: str, notes: str = "") -> BenchmarkVersion:
    """Adopt a version as the one standings are scored against.

    Idempotent: freezing an already-frozen version returns it unchanged rather
    than restamping it, so a nightly job that calls this every run does not
    rewrite the date the scale was adopted.
    """
    existing = get_version(store, version)
    if existing is None:
        raise ValueError(f"no benchmark version {version!r}")
    if existing.is_frozen:
        return existing

    with store.transaction() as conn:
        conn.execute(
            "UPDATE benchmark_versions SET frozen_at = ?, notes = ? WHERE version = ?",
            (_now(), notes or existing.notes, version),
        )
    return get_version(store, version)


def get_version(store: Store, version: str) -> BenchmarkVersion | None:
    row = store.conn.execute(
        "SELECT * FROM benchmark_versions WHERE version = ?", (version,)
    ).fetchone()
    return _to_version(row) if row else None


#: The simulator writes under the real season's label with this appended, so a
#: placeholder run cannot be mistaken for the season itself.
SIMULATED_SUFFIX = "-SIM"


def latest_draft(store: Store, season: str) -> BenchmarkVersion | None:
    """The newest unfrozen version for a season -- the one still being built.

    A twenty-league run happens over several sittings, and having to carry the
    version id between them is the part that goes wrong: an unset shell variable
    turns into an argument error at best and a second half-built version at
    worst. Frozen versions are excluded because adding to one is refused
    anyway.
    """
    row = store.conn.execute(
        "SELECT * FROM benchmark_versions WHERE season = ? AND frozen_at IS NULL "
        "ORDER BY computed_at DESC, rowid DESC LIMIT 1",
        (season,),
    ).fetchone()
    return _to_version(row) if row else None


def active_version(store: Store, season: str) -> BenchmarkVersion | None:
    """The frozen version a season's scores are measured against.

    The most recently frozen one wins. More than one frozen version in a season
    is legitimate -- a mid-season correction is exactly that -- and the rows in
    ``daily_scores`` each name the version they used, so the older scores stay
    explainable.

    A simulated season falls back to the real one's scale. The simulator exists
    to show what the standings will look like, which it can only do if its
    scores are on the same scale the season itself will use; freezing a second,
    identical version under the ``-SIM`` label would just be a copy that can
    drift.
    """
    def frozen(label: str):
        return store.conn.execute(
            # rowid breaks a tie deterministically by insertion order, so even
            # two versions frozen in the same millisecond resolve the same way on
            # every read rather than by whatever the engine returns first.
            "SELECT * FROM benchmark_versions WHERE season = ? AND frozen_at IS NOT NULL "
            "ORDER BY frozen_at DESC, rowid DESC LIMIT 1",
            (label,),
        ).fetchone()

    row = frozen(season)
    if row is None and season.endswith(SIMULATED_SUFFIX):
        row = frozen(season[: -len(SIMULATED_SUFFIX)])
    return _to_version(row) if row else None


def load(store: Store, version: str) -> pd.DataFrame:
    """A version's benchmarks, in the shape ``apply_benchmarks`` expects."""
    return store.query(
        "SELECT asset_type, norm_key, benchmark, pool_size, seasons "
        "FROM benchmarks WHERE version = ? ORDER BY asset_type, norm_key",
        (version,),
    )


def compare(store: Store, left: str, right: str) -> pd.DataFrame:
    """What changed between two versions, per normalization group.

    The point of versioning is being able to answer "what would adopting this
    do to the standings" before adopting it. A benchmark that moves 4% moves
    every score in its group by 4%.
    """
    a = load(store, left).rename(columns={"benchmark": "before", "pool_size": "pool_before"})
    b = load(store, right).rename(columns={"benchmark": "after", "pool_size": "pool_after"})
    merged = a.merge(b, on=["asset_type", "norm_key"], how="outer")
    merged["change_pct"] = (
        (merged["after"] - merged["before"]) / merged["before"] * 100
    ).round(2)
    return merged[
        ["asset_type", "norm_key", "before", "after", "change_pct", "pool_before", "pool_after"]
    ].sort_values("change_pct", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)


def _to_version(row) -> BenchmarkVersion:
    return BenchmarkVersion(
        version=row["version"], season=row["season"], quantile=row["quantile"],
        managers=row["managers"], computed_at=row["computed_at"],
        frozen_at=row["frozen_at"], notes=row["notes"],
    )
