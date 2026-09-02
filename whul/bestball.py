"""Season-long best-ball rollup over roster slots.

The scoring unit is the *slot*, not the asset. A slot is a persistent container
owned by a manager; a trade swaps which asset occupies it. The slot's score is
the sum of what each occupant accrued while it sat there, so points earned before
a trade stay with the manager who earned them::

    slot_score = sum over occupancies of (cumulative[end] - cumulative[start - 1])

A manager's total is then, for each category, the sum of the top-K slot scores
where K is that category's starter count. Bench slots never score directly --
they only matter when an occupant stops accruing and sinks below the cut.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from whul.config.league import ALL_SLOTS, SlotGroup


@dataclass(frozen=True)
class Occupancy:
    """One asset's tenure in a slot. ``end=None`` means still occupied.

    Occupancies within a slot must not overlap; ``start`` is inclusive and
    ``end`` is inclusive of the asset's last accruing day.
    """

    asset_id: str
    start: date
    end: date | None = None


@dataclass
class RosterSlot:
    slot_id: str
    manager: str
    category: str
    asset_type: str
    occupancies: list[Occupancy] = field(default_factory=list)


def _starter_counts() -> dict[tuple[str, str], int]:
    return {(s.asset_type, s.category): s.starters for s in ALL_SLOTS}


def accrue(
    cumulative: pd.DataFrame,
    asset_id: str,
    start: date,
    end: date,
) -> float:
    """Score an asset accrued over an inclusive date window.

    ``cumulative`` holds season-to-date scores with columns ``asset_id``,
    ``date``, ``score``. Because normalization is linear in points, differencing
    the cumulative series is equivalent to summing daily deltas.
    """
    rows = cumulative[cumulative["asset_id"] == asset_id]
    if rows.empty:
        return 0.0
    rows = rows.sort_values("date")
    at_end = rows.loc[rows["date"] <= end, "score"]
    before = rows.loc[rows["date"] <= start - timedelta(days=1), "score"]
    end_val = float(at_end.iloc[-1]) if not at_end.empty else 0.0
    start_val = float(before.iloc[-1]) if not before.empty else 0.0
    return end_val - start_val


def slot_score(slot: RosterSlot, cumulative: pd.DataFrame, as_of: date) -> float:
    """Total accrued in a slot through ``as_of``, across every occupant."""
    total = 0.0
    for occ in slot.occupancies:
        if occ.start > as_of:
            continue
        window_end = min(occ.end, as_of) if occ.end else as_of
        total += accrue(cumulative, occ.asset_id, occ.start, window_end)
    return total


def score_slots(
    slots: list[RosterSlot],
    cumulative: pd.DataFrame,
    as_of: date,
) -> pd.DataFrame:
    """Per-slot scores with the live best-ball selection marked.

    ``counts`` is what the standings and the contribution bar chart both read:
    it flips as scores move, with no manager action.
    """
    starters = _starter_counts()
    rows = [
        {
            "slot_id": s.slot_id,
            "manager": s.manager,
            "category": s.category,
            "asset_type": s.asset_type,
            "asset_id": _current_asset(s, as_of),
            "score": slot_score(s, cumulative, as_of),
        }
        for s in slots
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return df.assign(rank_in_group=[], counts=[])

    df = df.sort_values("score", ascending=False, kind="mergesort")
    df["rank_in_group"] = df.groupby(["manager", "asset_type", "category"]).cumcount() + 1
    limit = df.set_index(["asset_type", "category"]).index.map(lambda k: starters.get(k, 0))
    df["counts"] = df["rank_in_group"] <= limit
    return df.reset_index(drop=True)


def _current_asset(slot: RosterSlot, as_of: date) -> str | None:
    for occ in slot.occupancies:
        if occ.start <= as_of and (occ.end is None or occ.end >= as_of):
            return occ.asset_id
    return None


def standings(
    slots: list[RosterSlot],
    cumulative: pd.DataFrame,
    as_of: date,
) -> pd.DataFrame:
    """Manager totals: the sum of counting slot scores."""
    scored = score_slots(slots, cumulative, as_of)
    if scored.empty:
        return pd.DataFrame(columns=["manager", "total"])
    totals = (
        scored[scored["counts"]]
        .groupby("manager")["score"]
        .sum()
        .round(2)
        .rename("total")
        .reset_index()
        .sort_values("total", ascending=False)
        .reset_index(drop=True)
    )
    totals.insert(0, "rank", totals.index + 1)
    return totals
