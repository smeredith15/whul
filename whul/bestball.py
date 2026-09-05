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

from bisect import bisect_right
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


class ScoreIndex:
    """Season-to-date scores, arranged for repeated point lookups.

    The rollup asks "what had this asset scored by this date" once per slot per
    occupancy per day. Answering that by filtering the frame each time scans
    every row -- at 300 slots against a season's 400,000 rows that is a second
    per day, and it grows as the season does, so a full backfill would take
    minutes and keep getting slower.

    Grouping once and bisecting instead makes each lookup logarithmic in one
    asset's own series rather than linear in the whole league's.
    """

    __slots__ = ("_dates", "_scores")

    def __init__(self, cumulative: pd.DataFrame):
        self._dates: dict[str, list[date]] = {}
        self._scores: dict[str, list[float]] = {}
        if cumulative is None or cumulative.empty:
            return
        ordered = cumulative.sort_values(["asset_id", "date"])
        for asset_id, group in ordered.groupby("asset_id", sort=False):
            self._dates[asset_id] = group["date"].tolist()
            self._scores[asset_id] = group["score"].astype(float).tolist()

    @property
    def is_empty(self) -> bool:
        return not self._dates

    def value_at(self, asset_id: str, day: date) -> float:
        """The most recent score on or before ``day``, or 0 before the first.

        Carrying the last value forward is deliberate: feeds do not report
        every day, and a season-to-date figure stands until the next one
        arrives. Reading a gap as zero would make every quiet day look like a
        collapse and then a recovery.
        """
        dates = self._dates.get(asset_id)
        if not dates:
            return 0.0
        position = bisect_right(dates, day)
        return self._scores[asset_id][position - 1] if position else 0.0


def as_index(cumulative: pd.DataFrame | ScoreIndex) -> ScoreIndex:
    """Accept either a frame or an already-built index."""
    return cumulative if isinstance(cumulative, ScoreIndex) else ScoreIndex(cumulative)


def accrue(
    cumulative: pd.DataFrame | ScoreIndex,
    asset_id: str,
    start: date,
    end: date,
) -> float:
    """Score an asset accrued over an inclusive date window.

    ``cumulative`` holds season-to-date scores with columns ``asset_id``,
    ``date``, ``score``. Because normalization is linear in points, differencing
    the cumulative series is equivalent to summing daily deltas.

    Pass a ``ScoreIndex`` when calling this repeatedly; building one per call
    would put the cost back.
    """
    index = as_index(cumulative)
    return index.value_at(asset_id, end) - index.value_at(asset_id, start - timedelta(days=1))


def slot_score(
    slot: RosterSlot, cumulative: pd.DataFrame | ScoreIndex, as_of: date
) -> float:
    """Total accrued in a slot through ``as_of``, across every occupant."""
    cumulative = as_index(cumulative)
    total = 0.0
    for occ in slot.occupancies:
        if occ.start > as_of:
            continue
        window_end = min(occ.end, as_of) if occ.end else as_of
        total += accrue(cumulative, occ.asset_id, occ.start, window_end)
    return total


def score_slots(
    slots: list[RosterSlot],
    cumulative: pd.DataFrame | ScoreIndex,
    as_of: date,
) -> pd.DataFrame:
    """Per-slot scores with the live best-ball selection marked.

    ``counts`` is what the standings and the contribution bar chart both read:
    it flips as scores move, with no manager action.
    """
    starters = _starter_counts()
    # Built once for the whole day rather than per slot.
    cumulative = as_index(cumulative)
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
    cumulative: pd.DataFrame | ScoreIndex,
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
