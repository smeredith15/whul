"""Reading and writing rosters.

A slot is the scoring unit and it persists for the season; a trade changes who
occupies it. So the store keeps slots and occupancies separately, and the
occupancy dates are what split a slot's points between the managers who earned
them.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from whul.bestball import Occupancy, RosterSlot
from whul.config.league import ALL_SLOTS, active_slots
from whul.store.db import Store, _as_text, _now


def create_slots(store: Store, manager_id: str, season: str, only_active: bool = True) -> int:
    """Lay out one manager's roster from the league template.

    Inactive categories keep their slots as placeholders by default only when
    asked for: a season that does not field them should not show empty rows in
    the standings, but the template still carries them so a later season can
    switch them back on without a migration.
    """
    groups = active_slots(ALL_SLOTS) if only_active else ALL_SLOTS
    rows = []
    for group in groups:
        for index in range(1, group.cap + 1):
            rows.append({
                "slot_id": f"{manager_id}:{season}:{group.asset_type}:{group.category}:{index}",
                "manager_id": manager_id,
                "season": season,
                "category": group.category,
                "asset_type": group.asset_type,
                "slot_index": index,
            })
    return store.upsert("roster_slots", rows, keys=("slot_id",))


def assign(
    store: Store,
    slot_id: str,
    asset_id: str,
    start: date | str,
    end: date | str | None = None,
    note: str = "",
    cost: float | None = None,
) -> None:
    """Put an asset in a slot from ``start``.

    Does not close any previous occupancy: a slot can legitimately be empty
    between two, and closing one implicitly would hide a gap that ought to be
    visible. Use ``release`` to end one.
    """
    store.upsert(
        "slot_occupancy",
        [{
            "slot_id": slot_id, "asset_id": asset_id,
            "start_date": _as_text(start), "end_date": _as_text(end) if end else None,
            "cost": cost, "note": note,
        }],
        keys=("slot_id", "start_date"),
    )


def release(store: Store, slot_id: str, end: date | str) -> int:
    """Close whichever occupancy of a slot is still open."""
    with store.transaction() as conn:
        cursor = conn.execute(
            "UPDATE slot_occupancy SET end_date = ? "
            "WHERE slot_id = ? AND end_date IS NULL",
            (_as_text(end), slot_id),
        )
    return cursor.rowcount


def double_rostered(store: Store, season: str) -> pd.DataFrame:
    """Assets sitting in more than one open slot at once.

    The mirror of ``overlaps``, and the one that actually happened. That check
    asks whether a slot has two occupants; this asks whether an occupant has
    two slots, which is what a trade recorded on one side only looks like --
    the player scores for both managers, and every total that includes him is
    too high.
    """
    rows = store.query(
        "SELECT o.asset_id, a.display_name, s.manager_id, s.category, "
        "       s.slot_index, o.slot_id, o.note "
        "FROM slot_occupancy o "
        "JOIN roster_slots s ON s.slot_id = o.slot_id "
        "LEFT JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE s.season = ? AND o.end_date IS NULL "
        "ORDER BY o.asset_id, s.manager_id, s.slot_index",
        (season,),
    )
    if rows.empty:
        return rows
    held = rows.groupby("asset_id")["slot_id"].transform("size")
    return rows[held > 1].reset_index(drop=True)


def drop_unlisted(
    store: Store, season: str, keep: set[tuple[str, str]], note: str
) -> list[tuple[str, str]]:
    """Remove ``note`` occupancies the roster sheet no longer supports.

    The sheet carries no dates -- every pick is written from the season's
    start -- so it describes the whole season rather than a moment in it. That
    makes it the truth about who holds what, and an occupancy it stopped naming
    is one the import itself wrote and should now take back.

    Scoped to its own ``note`` so a trade entered with a real effective date is
    left alone. Otherwise the nightly re-import would silently undo every
    correction made through the admin page, which is worse than the fault this
    fixes.

    Returns what it removed, so the caller can say so rather than doing it
    quietly.
    """
    open_rows = store.query(
        "SELECT o.slot_id, o.asset_id FROM slot_occupancy o "
        "JOIN roster_slots s ON s.slot_id = o.slot_id "
        "WHERE s.season = ? AND o.end_date IS NULL AND o.note = ?",
        (season, note),
    )
    if open_rows.empty:
        return []
    stale = [
        (row.slot_id, row.asset_id)
        for row in open_rows.itertuples()
        if (row.slot_id, row.asset_id) not in keep
    ]
    if not stale:
        return []
    with store.transaction() as conn:
        conn.executemany(
            "DELETE FROM slot_occupancy WHERE slot_id = ? AND asset_id = ? "
            "AND end_date IS NULL AND note = ?",
            [(slot, asset, note) for slot, asset in stale],
        )
    return stale


def trade(
    store: Store,
    out_slot: str,
    in_slot: str,
    out_asset: str,
    in_asset: str,
    effective: date | str,
    note: str = "",
) -> None:
    """A reciprocal swap, applied to both slots as one act.

    The outgoing asset stops accruing the day before the trade and the incoming
    one starts on it, so no day is counted twice and none is lost. Written in a
    single transaction because a half-applied trade would leave an asset in two
    slots at once, and the best-ball rollup would count it twice.
    """
    effective_date = pd.Timestamp(_as_text(effective)).date()
    last_day = effective_date - timedelta(days=1)

    with store.transaction() as conn:
        for slot in (out_slot, in_slot):
            conn.execute(
                "UPDATE slot_occupancy SET end_date = ? "
                "WHERE slot_id = ? AND end_date IS NULL",
                (_as_text(last_day), slot),
            )
        for slot, asset in ((out_slot, in_asset), (in_slot, out_asset)):
            conn.execute(
                "INSERT INTO slot_occupancy (slot_id, asset_id, start_date, end_date, note) "
                "VALUES (?, ?, ?, NULL, ?) "
                "ON CONFLICT (slot_id, start_date) DO UPDATE SET "
                "asset_id = excluded.asset_id, end_date = NULL, note = excluded.note",
                (slot, asset, _as_text(effective_date), note or "trade"),
            )


def load_slots(store: Store, season: str, manager_id: str | None = None) -> list[RosterSlot]:
    """Slots with their occupancies, ready for the best-ball rollup."""
    sql = "SELECT * FROM roster_slots WHERE season = ?"
    params: list = [season]
    if manager_id:
        sql += " AND manager_id = ?"
        params.append(manager_id)
    slots = store.query(sql + " ORDER BY manager_id, asset_type, category, slot_index", params)
    if slots.empty:
        return []

    occupancies = store.query(
        "SELECT o.* FROM slot_occupancy o "
        "JOIN roster_slots s ON s.slot_id = o.slot_id "
        "WHERE s.season = ? ORDER BY o.slot_id, o.start_date",
        [season],
    )
    by_slot: dict[str, list[Occupancy]] = {}
    for row in occupancies.itertuples():
        # An open occupancy reads back as NaT rather than None, because pandas
        # types the column from the rows that do have an end. NaT is truthy, so
        # a plain falsiness test leaves it in place -- and NaT compares False
        # against everything, which would silently truncate the slot's window
        # instead of running it to today.
        end = None if pd.isna(row.end_date) else pd.Timestamp(row.end_date).date()
        by_slot.setdefault(row.slot_id, []).append(
            Occupancy(
                asset_id=row.asset_id,
                start=pd.Timestamp(row.start_date).date(),
                end=end,
            )
        )

    return [
        RosterSlot(
            slot_id=row.slot_id, manager=row.manager_id,
            category=row.category, asset_type=row.asset_type,
            occupancies=by_slot.get(row.slot_id, []),
        )
        for row in slots.itertuples()
    ]


def overlaps(store: Store, season: str) -> pd.DataFrame:
    """Slots with two occupants on the same day.

    Should never happen and would double-count an asset, so the nightly run
    checks rather than assuming. Two rows overlap when one starts before the
    other ends, treating an open end as running to the season's last day.
    """
    occupancies = store.query(
        "SELECT o.slot_id, o.asset_id, o.start_date, o.end_date FROM slot_occupancy o "
        "JOIN roster_slots s ON s.slot_id = o.slot_id "
        "WHERE s.season = ? ORDER BY o.slot_id, o.start_date",
        [season],
    )
    if occupancies.empty:
        return occupancies

    clashes = []
    for slot_id, group in occupancies.groupby("slot_id"):
        rows = group.to_dict("records")
        for earlier, later in zip(rows, rows[1:]):
            end = earlier["end_date"]
            if pd.isna(end) or str(end) >= str(later["start_date"]):
                clashes.append({
                    "slot_id": slot_id,
                    "first_asset": earlier["asset_id"],
                    "first_end": end,
                    "second_asset": later["asset_id"],
                    "second_start": later["start_date"],
                })
    return pd.DataFrame(clashes)


def add_manager(store: Store, manager_id: str, display_name: str | None = None) -> None:
    store.upsert(
        "managers",
        [{"manager_id": manager_id, "display_name": display_name or manager_id, "active": 1}],
        keys=("manager_id",),
    )
