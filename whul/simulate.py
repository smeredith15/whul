"""A simulated league, for building against before the draft is done.

The real season is ``2026-27``. Everything here goes under ``2026-27-SIM``,
which is not a label the pipeline ever produces on its own, so the two cannot
collide and purging is one delete rather than a careful untangling. Asset ids
are prefixed ``sim-`` for the same reason, and the season is recorded as
simulated in ``admin_overrides`` so any view that cares can say so.

The players are invented. The *shape* is not: five managers, the real roster
template, real leagues and categories, a full season of daily scores with
plausible dispersion, and a handful of mid-season trades. That is what the
standings table, the contribution bars and the progression line all need in
order to be built and looked at honestly.

Everything is seeded, so two runs produce the same league and a screenshot
taken today still matches the data tomorrow.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pandas as pd

from whul.config.league import ALL_SLOTS, SEASON, active_slots
from whul.pipeline import backfill, write_daily_scores
from whul.store import benchmarks as bm
from whul.store import rosters
from whul.store.db import Store, _now

#: Never the real season label, so the two can never be confused or merged.
SIM_SEASON = f"{SEASON.label}-SIM"
SIM_PREFIX = "sim-"

MANAGERS = ("avery", "blake", "casey", "devon", "emery")

#: Enough surnames to fill every category without repeating inside one.
_SURNAMES = (
    "Ashworth Brennan Calloway Delgado Ellery Fairbank Grayson Halloran "
    "Ingram Jarrett Kingsley Lockwood Mercer Nolan Osborne Pemberton Quinlan "
    "Radcliffe Sinclair Thorne Underhill Vance Whitlock Yarrow Ziegler "
    "Abbott Beckett Carver Donnelly Eastwood"
).split()
_FIRST_INITIALS = "ABCDEHJKLMNPRSTW"
_CITIES = (
    "Ashford Bellhaven Cotswold Dunmore Elmridge Fairhaven Glenmoor Harrow "
    "Ironvale Kestrel Larkspur Marlowe Northgate Oakhurst Pinecrest Redstone "
    "Stonebridge Thornbury Westmere Yarmouth"
).split()

#: A category's typical top-end scaled score, used to give each one a different
#: distribution. The numbers are illustrative: what matters is that categories
#: differ, so the contribution chart has something to show.
_CEILING = {
    "NFL": 105, "NBA": 100, "MLB": 95, "NHL": 95,
    "Club Soccer Top 3": 100, "Club Soccer Other": 85, "Intl Soccer": 90,
    "NCAAF": 95, "NCAAM": 90, "NCAAW": 90,
    "NCAA Baseball": 85, "NCAA Softball": 85,
    "PGA": 95, "Tennis": 100, "Motorsports": 90,
}
DEFAULT_CEILING = 90

#: How many trades to make, and the window they fall in.
TRADE_COUNT = 6


def _asset_name(rng: random.Random, asset_type: str, index: int) -> str:
    if asset_type == "Team":
        return f"{_CITIES[index % len(_CITIES)]} {rng.choice(('United', 'City', 'Athletic', 'Rovers'))}"
    return f"{rng.choice(_FIRST_INITIALS)}. {_SURNAMES[index % len(_SURNAMES)]}"


def build_assets(rng: random.Random) -> pd.DataFrame:
    """One pool of invented assets per category, large enough to draft from.

    Sized so every manager can be filled without any asset appearing twice,
    which the best-ball rollup would otherwise double-count.
    """
    rows = []
    for group in active_slots(ALL_SLOTS):
        needed = group.cap * len(MANAGERS) + 4  # a few spare, for trades
        for index in range(needed):
            key = group.category.lower().replace(" ", "-")
            rows.append({
                "asset_id": f"{SIM_PREFIX}{group.asset_type.lower()}-{key}-{index:02d}",
                "asset_type": group.asset_type,
                "display_name": _asset_name(rng, group.asset_type, index),
                "league": group.category,
                "role": "" if group.asset_type == "Team" else "Player",
                "norm_key": group.category,
                "active": 1,
                "created_at": _now(),
            })
    return pd.DataFrame(rows)


def _draft(
    rng: random.Random, assets: pd.DataFrame
) -> dict[str, dict[tuple[str, str], list[str]]]:
    """Deal each category's pool round-robin, so no asset is drafted twice.

    Keyed by ``(asset_type, category)`` rather than returned as a flat list.
    A flat list has to be zipped against the slots, and the two orderings are
    not the same -- the template is in declaration order and the store returns
    slots sorted -- so zipping put a golfer in a hockey slot.
    """
    picks: dict[str, dict[tuple[str, str], list[str]]] = {m: {} for m in MANAGERS}
    for group in active_slots(ALL_SLOTS):
        key = (group.asset_type, group.category)
        pool = assets[
            (assets["league"] == group.category)
            & (assets["asset_type"] == group.asset_type)
        ]["asset_id"].tolist()
        rng.shuffle(pool)
        for manager in MANAGERS:
            picks[manager][key] = pool[: group.cap]
            pool = pool[group.cap:]
    return picks


def _score_curve(
    rng: random.Random, ceiling: float, days: list[date]
) -> list[float]:
    """A season-to-date series that rises and never falls.

    Cumulative scores only go up, so the series is built from non-negative
    daily gains. Gains are uneven -- a burst then a quiet fortnight -- because
    a smooth line would hide exactly the behaviour the progression graph is
    meant to show.
    """
    final = ceiling * rng.uniform(0.25, 1.05)
    weights = [max(0.0, rng.gauss(1.0, 0.9)) for _ in days]
    total = sum(weights) or 1.0
    running = 0.0
    series = []
    for weight in weights:
        running += final * weight / total
        series.append(round(running, 2))
    return series


def generate(
    store: Store,
    seed: int = 2026,
    start: date | None = None,
    end: date | None = None,
    verbose: bool = True,
) -> dict:
    """Build the whole simulated league and roll up its standings."""
    rng = random.Random(seed)
    start = start or SEASON.start
    end = min(end or date.today(), SEASON.end)
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    if not days:
        raise ValueError(f"no days between {start} and {end}")

    store.upsert(
        "admin_overrides",
        [{
            "scope": "simulation", "key": "source", "value": "whul.simulate",
            "season": SIM_SEASON, "set_by": "simulate", "set_at": _now(),
            "note": "Invented assets and rosters. Safe to delete; the real "
                    "season is a different label.",
        }],
        keys=("scope", "key", "season"),
    )

    assets = build_assets(rng)
    store.insert_frame("assets", assets, keys=("asset_id",))

    for manager in MANAGERS:
        rosters.add_manager(store, manager, manager.title())
        rosters.create_slots(store, manager, SIM_SEASON)

    slots_by_manager = {
        m: rosters.load_slots(store, SIM_SEASON, m) for m in MANAGERS
    }
    picks = _draft(rng, assets)
    for manager, slots in slots_by_manager.items():
        remaining = {k: list(v) for k, v in picks[manager].items()}
        for slot in slots:
            pool = remaining.get((slot.asset_type, slot.category))
            if pool:
                rosters.assign(store, slot.slot_id, pool.pop(0), start)

    # Daily scores for every drafted asset, plus the spares so a trade has
    # somewhere to come from.
    drafted = {a for chosen in picks.values() for pool in chosen.values() for a in pool}
    curves = {
        row.asset_id: _score_curve(rng, _CEILING.get(row.league, DEFAULT_CEILING), days)
        for row in assets.itertuples()
        if row.asset_id in drafted
    }
    version = _freeze_benchmarks(store, assets)
    for index, day in enumerate(days):
        frame = pd.DataFrame({
            "asset_id": list(curves),
            "total_points": [c[index] * 4 for c in curves.values()],
            "scaled_score": [c[index] for c in curves.values()],
        })
        write_daily_scores(store, frame, SIM_SEASON, day, version)

    trades = _make_trades(store, rng, slots_by_manager, days)
    reports = backfill(
        store, SIM_SEASON, start=start, end=end, today=end, verbose=False
    )

    summary = {
        "season": SIM_SEASON,
        "managers": len(MANAGERS),
        "assets": len(assets),
        "slots": sum(len(s) for s in slots_by_manager.values()),
        "days": len(days),
        "trades": trades,
        "benchmark_version": version,
        "warnings": [w for r in reports for w in r.warnings],
    }
    if verbose:
        print(
            f"simulated {summary['season']}: {summary['managers']} managers, "
            f"{summary['slots']} slots, {summary['assets']} assets, "
            f"{summary['days']} days, {trades} trades",
            flush=True,
        )
    return summary


def _freeze_benchmarks(store: Store, assets: pd.DataFrame) -> str:
    """A benchmark of 100 per group, so a scaled score reads as itself.

    The simulation writes scaled scores directly rather than deriving them, so
    a flat scale keeps the two consistent -- and makes a wrong number in the UI
    obvious rather than plausible.
    """
    rows = pd.DataFrame({
        "asset_type": assets["asset_type"],
        "norm_key": assets["norm_key"],
        "benchmark": 100.0,
        "pool_size": 0,
        "seasons": "simulated",
    }).drop_duplicates(subset=["asset_type", "norm_key"])
    version = f"{SIM_SEASON}-flat"
    if bm.get_version(store, version) is None:
        bm.save(store, rows, SIM_SEASON, version=version, notes="simulated, flat scale")
    return bm.freeze(store, version).version


def _make_trades(
    store: Store, rng: random.Random, slots_by_manager: dict, days: list[date]
) -> int:
    """A few reciprocal swaps, so accrual splitting has something to split.

    Both sides of a trade must be the same category and asset type -- a team
    slot cannot hold a player -- so partners are drawn from matching slots.
    """
    if len(days) < 14:
        return 0

    made = 0
    for _ in range(TRADE_COUNT):
        left, right = rng.sample(MANAGERS, 2)
        group = rng.choice(active_slots(ALL_SLOTS))
        left_slots = [
            s for s in slots_by_manager[left]
            if s.category == group.category and s.asset_type == group.asset_type
        ]
        right_slots = [
            s for s in slots_by_manager[right]
            if s.category == group.category and s.asset_type == group.asset_type
        ]
        if not left_slots or not right_slots:
            continue

        left_slot = rng.choice(left_slots)
        right_slot = rng.choice(right_slots)
        left_asset = _occupant(store, left_slot.slot_id)
        right_asset = _occupant(store, right_slot.slot_id)
        if not left_asset or not right_asset or left_asset == right_asset:
            continue

        when = days[rng.randrange(7, len(days) - 1)]
        rosters.trade(store, left_slot.slot_id, right_slot.slot_id,
                      left_asset, right_asset, when, note="simulated trade")
        made += 1
    return made


def _occupant(store: Store, slot_id: str) -> str | None:
    row = store.conn.execute(
        "SELECT asset_id FROM slot_occupancy WHERE slot_id = ? AND end_date IS NULL "
        "ORDER BY start_date DESC LIMIT 1",
        (slot_id,),
    ).fetchone()
    return row["asset_id"] if row else None


def purge(store: Store) -> dict[str, int]:
    """Delete the simulated league entirely.

    Ordered so a foreign key never blocks a delete: the rows that point at
    others go first.
    """
    removed = {}
    with store.transaction() as conn:
        for table, sql in (
            ("standings_snapshots", "DELETE FROM standings_snapshots WHERE season = ?"),
            ("slot_scores", "DELETE FROM slot_scores WHERE season = ?"),
            ("slot_occupancy",
             "DELETE FROM slot_occupancy WHERE slot_id IN "
             "(SELECT slot_id FROM roster_slots WHERE season = ?)"),
            ("roster_slots", "DELETE FROM roster_slots WHERE season = ?"),
            ("daily_scores", "DELETE FROM daily_scores WHERE season = ?"),
            ("raw_stats", "DELETE FROM raw_stats WHERE season = ?"),
            ("benchmarks",
             "DELETE FROM benchmarks WHERE version IN "
             "(SELECT version FROM benchmark_versions WHERE season = ?)"),
            ("benchmark_versions", "DELETE FROM benchmark_versions WHERE season = ?"),
            ("admin_overrides", "DELETE FROM admin_overrides WHERE season = ?"),
        ):
            removed[table] = conn.execute(sql, (SIM_SEASON,)).rowcount
        removed["assets"] = conn.execute(
            "DELETE FROM assets WHERE asset_id LIKE ?", (f"{SIM_PREFIX}%",)
        ).rowcount
    return removed
