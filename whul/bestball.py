"""Season-long best-ball rollup over roster slots.

The scoring unit is the *slot*, not the asset. A slot is a persistent container
owned by a manager; a trade swaps which asset occupies it. The slot's score is
the sum of what each occupant accrued while it sat there, so points earned before
a trade stay with the manager who earned them::

    slot_score = sum over occupancies of (cumulative[end] - cumulative[start - 1])

A manager's total is then, for each category, the sum of the top-K slot scores
where K is that category's starter count. Bench slots never score directly --
they only matter when an occupant stops accruing and sinks below the cut.

The team sports' player categories also hold a **best-performances** slot,
which counts a player's k best games rather than his season. A slot's best
games are the games its occupants played while they sat in it, so a trade
splits them exactly as it splits a season. Which player fills which slot is
whichever arrangement scores most -- a player whose best games are worth more
than the gap between his season and the next man's is worth more in the
best-performances slot -- and it is recomputed every day, like the rest of
best ball. See ``whul.scoring.best_game`` for the rules.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from whul.config.league import ALL_SLOTS, SlotGroup, best_k
from whul.scoring import best_game as rules


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


def _best_counts() -> dict[tuple[str, str], int]:
    return {(s.asset_type, s.category): s.best for s in ALL_SLOTS}


@dataclass(frozen=True)
class Game:
    """One game a player played: when, in what role, and what it scored."""

    game_key: str
    date: date
    score: float
    role: str = ""


class GameIndex:
    """Every rostered player's games, by asset, in date order."""

    __slots__ = ("_games",)

    def __init__(self, games: pd.DataFrame | None = None):
        self._games: dict[str, list[Game]] = {}
        if games is None or games.empty:
            return
        days = pd.to_datetime(games["date"]).dt.date
        roles = games["role"] if "role" in games.columns else pd.Series("", index=games.index)
        for asset_id, key, day, score, role in sorted(zip(
                games["asset_id"].astype(str), games["game_key"].astype(str),
                days, games["score"].astype(float), roles.fillna("").astype(str)),
                key=lambda row: (row[0], row[2], row[1])):
            self._games.setdefault(asset_id, []).append(Game(key, day, score, role))

    @property
    def is_empty(self) -> bool:
        return not self._games

    def between(self, asset_id: str, start: date, end: date) -> list[Game]:
        return [g for g in self._games.get(asset_id, ()) if start <= g.date <= end]


def slot_games(slot: RosterSlot, games: GameIndex, as_of: date) -> list[Game]:
    """The games a slot's occupants played while they sat in it."""
    out: list[Game] = []
    for occ in slot.occupancies:
        if occ.start > as_of:
            continue
        window_end = min(occ.end, as_of) if occ.end else as_of
        out += games.between(occ.asset_id, occ.start, window_end)
    return out


def best_performances(category: str, played: list[Game]) -> tuple[float, list[Game]]:
    """A slot's k best games, and which games they were.

    MLB's roles each have their own k and trade at the ratio of them, so a
    swingman's best starts and best relief outings can share the slot. Every
    other sport is one role. A game below zero is never forced in.
    """
    by_role: dict[str, list[Game]] = {}
    for game in played:
        role = game.role if category == "MLB" else ""
        by_role.setdefault(role, []).append(game)
    ks = {role: best_k(category, role) for role in by_role}
    value, taken = rules.exchange_pick(
        {role: [g.score for g in listed] for role, listed in by_role.items()}, ks)
    chosen: list[Game] = []
    for role, count in taken.items():
        ranked = sorted(by_role[role], key=lambda g: (-g.score, g.date, g.game_key))
        chosen += ranked[:count]
    return value, sorted(chosen, key=lambda g: (-g.score, g.date))


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
    games: GameIndex | None = None,
) -> pd.DataFrame:
    """Per-slot scores with the live best-ball selection marked.

    ``counts`` is what the standings and the contribution bar chart both read:
    it flips as scores move, with no manager action. ``scored_as`` says which
    of a slot's two values counts -- ``season`` for ``score``, ``best`` for
    ``best_score`` -- and is blank on the bench.

    ``games`` is the per-game record the best-performances slots are scored
    from. Without it every best score is zero and those slots stay empty.
    """
    starters = _starter_counts()
    best_slots = _best_counts()
    # Built once for the whole day rather than per slot.
    cumulative = as_index(cumulative)
    games = games or GameIndex()
    rows = []
    for s in slots:
        wants_best = best_slots.get((s.asset_type, s.category), 0) > 0
        best = (best_performances(s.category, slot_games(s, games, as_of))[0]
                if wants_best else 0.0)
        rows.append({
            "slot_id": s.slot_id,
            "manager": s.manager,
            "category": s.category,
            "asset_type": s.asset_type,
            "asset_id": _current_asset(s, as_of),
            "score": slot_score(s, cumulative, as_of),
            "best_score": best,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df.assign(rank_in_group=[], counts=[], scored_as=[])

    df = df.sort_values("score", ascending=False, kind="mergesort")
    df["rank_in_group"] = df.groupby(["manager", "asset_type", "category"]).cumcount() + 1
    limit = df.set_index(["asset_type", "category"]).index.map(lambda k: starters.get(k, 0))
    df["scored_as"] = ["season" if rank <= cap else ""
                       for rank, cap in zip(df["rank_in_group"], limit)]

    # Where a category holds a best-performances slot, the arrangement that
    # scores most replaces the plain top-K.
    held_open: set[str] = set()
    for (manager, asset_type, category), group in df.groupby(
            ["manager", "asset_type", "category"], sort=False):
        if best_slots.get((asset_type, category), 0) <= 0:
            continue
        found = rules.best_configuration(
            [rules.Candidate(row.slot_id, row.score, row.best_score)
             for row in group.itertuples()],
            starters.get((asset_type, category), 0))
        seated = set(found.season)
        best = found.best
        if best is None:
            # The slot is always somebody's. Leaving it empty is the right
            # total when no best-games figure would add anything, and the wrong
            # page: before a league has played enough for the slot to pay, the
            # roster showed no best-performances slot at all, and a 0.1 sat
            # crossed out on a player in a season slot. It goes to the strongest
            # best-games figure on the bench, which is never below zero -- a
            # game below zero is never forced in -- so the total is unchanged.
            spare = group[~group["slot_id"].isin(seated)]
            if not spare.empty:
                best = spare.sort_values(
                    "best_score", ascending=False, kind="mergesort"
                )["slot_id"].iloc[0]
                held_open.add(best)
        df.loc[group.index, "scored_as"] = [
            "season" if slot in seated else "best" if slot == best else ""
            for slot in group["slot_id"]]

    # A slot held open pays nothing yet, so it does not count: its occupant's
    # season is still something the manager is carrying outside the total.
    df["counts"] = (df["scored_as"] != "") & ~df["slot_id"].isin(held_open)
    return df.reset_index(drop=True)


def counted(scored: pd.DataFrame) -> pd.Series:
    """What each slot adds to its manager's total today."""
    if scored.empty:
        return pd.Series(dtype=float)
    best = scored["best_score"] if "best_score" in scored.columns else 0.0
    kind = scored["scored_as"] if "scored_as" in scored.columns else (
        scored["counts"].map(lambda c: "season" if c else ""))
    return (scored["score"].where(kind == "season", 0.0)
            + (best if isinstance(best, float) else best.where(kind == "best", 0.0)))


def _current_asset(slot: RosterSlot, as_of: date) -> str | None:
    for occ in slot.occupancies:
        if occ.start <= as_of and (occ.end is None or occ.end >= as_of):
            return occ.asset_id
    return None


def standings(
    slots: list[RosterSlot],
    cumulative: pd.DataFrame | ScoreIndex,
    as_of: date,
    games: GameIndex | None = None,
    scored: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Manager totals: what every counting slot adds.

    ``scored`` is ``score_slots``'s answer for the day, where the caller has it
    already; recomputing it would score every best-performances slot twice.
    """
    if scored is None:
        scored = score_slots(slots, cumulative, as_of, games)
    if scored.empty:
        return pd.DataFrame(columns=["manager", "total"])
    totals = (
        scored.assign(added=counted(scored))
        .groupby("manager")["added"]
        .sum()
        .round(2)
        .rename("total")
        .reset_index()
        .sort_values("total", ascending=False)
        .reset_index(drop=True)
    )
    totals.insert(0, "rank", totals.index + 1)
    return totals
