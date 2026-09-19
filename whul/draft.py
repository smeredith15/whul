"""What the auction actually did, read off the bid log.

A price on its own says almost nothing. The same $200 bought Shohei Ohtani with
nobody else bidding and the Milwaukee Brewers over two rivals, and the roster
records both as 200. Five things separate them, and all five are in the log:

* **what was paid** -- the winning bid, which the roster already knows;
* **what the field would have paid** -- the best losing bid, which is the only
  price anyone but the buyer ever named;
* **how contested it was** -- how many managers bid at all, and how much they
  put up between them;
* **when it went** -- a round-one dollar and a round-three dollar bought
  different things, because the board and the budgets were different;
* **what the manager still needed** -- $200 with fifty slots still open is a
  different act from $200 with five, whatever it buys.

Nothing here scores a manager. It measures the purchase, and the purchase can
be measured on the day the draft ends -- months before the seasons that decide
whether it was a good one.

The one thing the auction cannot tell us is what a slot was worth for nothing,
and the draft answered that too: the snake round filled 25 slots at no cost at
all. Those are the measured replacement level, and a category whose paid assets
do not beat its free ones is a category where the money bought nothing.
"""

from __future__ import annotations

import pandas as pd

from whul.config.league import ALL_SLOTS, active_slots
from whul.draft_bids import LIVE
from whul.store.db import Store

#: One row per asset that drew a bid.
MARKET_COLUMNS = (
    "asset_id", "name_key", "name", "league", "category", "round",
    "winner", "paid", "field", "bidders", "demand", "contested", "premium",
)

#: The least a bid can be. A win at the floor with nobody else bidding paid no
#: premium -- there was nothing cheaper to bid.
FLOOR = 1.0


def market(store: Store, season: str) -> pd.DataFrame:
    """One row per asset that drew a live bid, with what the field said.

    Keyed on the name and the round rather than on the asset id, because most
    of these rows have no asset id: a losing bid on a player nobody drafted is
    still a price somebody named, and dropping it would leave only the bids
    that won -- which is the roster again.
    """
    from whul.draft_bids import load

    empty = pd.DataFrame({c: pd.Series(dtype="object") for c in MARKET_COLUMNS})
    bids = load(store, season)
    if bids.empty:
        return empty
    live = bids[bids["status"].isin(LIVE)].copy()
    if live.empty:
        return empty
    live["bid"] = pd.to_numeric(live["bid"], errors="coerce").fillna(0.0)

    rows = []
    for (key, league, rnd), group in live.groupby(
            ["name_key", "league", "round"], sort=False):
        ordered = group.sort_values("bid", ascending=False)
        top = ordered.iloc[0]
        offers = list(ordered["bid"])
        field = float(offers[1]) if len(offers) > 1 else 0.0
        paid = float(top["bid"])
        won = ordered[ordered["status"] == "won"]
        rows.append({
            "asset_id": str(top["asset_id"]),
            "name_key": str(key),
            "name": str(top["name"]),
            "league": str(league),
            "category": str(top["category"]),
            "round": int(rnd),
            "winner": str(won.iloc[0]["manager_id"]) if not won.empty else "",
            "paid": paid,
            "field": field,
            "bidders": int(len(offers)),
            "demand": float(sum(offers)),
            "contested": int(len(offers) > 1),
            # What the win cost above the next offer. With nobody else bidding
            # the comparison is the floor, not zero: the asset could have been
            # had for a dollar, and the rest was bid against nobody.
            "premium": paid - max(field, FLOOR),
        })
    return pd.DataFrame(rows, columns=list(MARKET_COLUMNS))


#: One row per winning bid, saying what the manager still had to fill.
NEEDS_COLUMNS = (
    "asset_id", "name_key", "league", "round", "manager_id", "category",
    "asset_type", "open_before", "roster_open",
)

#: Every slot a manager has to fill, which is what `roster_open` counts down
#: from. The inactive groups are left out because nobody was filling them.
ROSTER_SLOTS = sum(g.cap for g in active_slots(ALL_SLOTS))


def needs(store: Store, season: str) -> pd.DataFrame:
    """How much of each roster was still empty at the moment of each buy.

    Replayed in order, because need is a fact about the moment: $200 with
    fifty slots still open is a different act from $200 with five, whatever it
    buys. ``open_before`` is that category's remaining seats and
    ``roster_open`` the whole roster's.

    Counts, and deliberately only counts. An earlier version divided by the
    rounds remaining and called the result pressure, which was hindsight
    wearing a constraint's clothes: nobody knew there would be exactly three
    rounds, and slots were left open on purpose to be filled in the snake. A
    buy made with a category empty was not forced, it was a manager choosing
    when to spend, and this says how much he had left to buy rather than
    whether he had a choice.
    """
    from whul.draft_bids import load

    empty = pd.DataFrame({c: pd.Series(dtype="object") for c in NEEDS_COLUMNS})
    bids = load(store, season)
    if bids.empty:
        return empty
    won = bids[bids["status"] == "won"].copy()
    if won.empty:
        return empty
    won["round"] = pd.to_numeric(won["round"], errors="coerce").fillna(0).astype(int)

    caps = {(g.category, g.asset_type): g.cap for g in ALL_SLOTS}
    filled: dict[tuple[str, str, str], int] = {}
    taken: dict[str, int] = {}
    rows = []
    for rnd in sorted(won["round"].unique()):
        for row in won[won["round"] == rnd].itertuples():
            manager = str(row.manager_id)
            seat = (manager, str(row.category), str(row.asset_type))
            cap = caps.get((str(row.category), str(row.asset_type)), 0)
            rows.append({
                "asset_id": str(row.asset_id),
                "name_key": str(row.name_key),
                "league": str(row.league),
                "round": int(rnd),
                "manager_id": manager,
                "category": str(row.category),
                "asset_type": str(row.asset_type),
                "open_before": max(0, cap - filled.get(seat, 0)),
                "roster_open": max(0, ROSTER_SLOTS - taken.get(manager, 0)),
            })
            filled[seat] = filled.get(seat, 0) + 1
            taken[manager] = taken.get(manager, 0) + 1
    return pd.DataFrame(rows, columns=list(NEEDS_COLUMNS))


def replacement(priced: pd.DataFrame) -> pd.Series:
    """What a free slot returned in each category.

    The snake round is the experiment nobody designed: 25 slots filled at no
    cost, in the same categories as everything else. Where a category has one,
    that is its replacement level -- measured rather than assumed. Where it has
    none, the worst asset anybody rostered stands in, which understates
    replacement, because the first undrafted asset sits below the last drafted
    one and not above it.
    """
    if priced is None or priced.empty:
        return pd.Series(dtype="float64")
    free = priced[priced["cost"].fillna(0) <= 0]
    floor = free.groupby("category")["score"].mean()
    worst = priced.groupby("category")["score"].min()
    return floor.reindex(worst.index).fillna(worst)


def categories(priced: pd.DataFrame) -> pd.DataFrame:
    """What each category cost the league and what it returned.

    Scarcity lives here rather than on a slot. Whether a better asset was worth
    paying for is a fact about the category -- how far the top of it sits above
    the bottom -- and repeating that figure beside every buy in the category
    would say it thirty times and add nothing.
    """
    if priced is None or priced.empty:
        return pd.DataFrame()
    floor = replacement(priced)
    paid = priced[priced["cost"].fillna(0) > 0]
    out = paid.groupby("category").agg(
        slots=("score", "size"),
        spend=("cost", "sum"),
        top=("score", "max"),
        scored=("score", "mean"),
    )
    free = priced[priced["cost"].fillna(0) <= 0].groupby("category").agg(
        free_slots=("score", "size"), free_scored=("score", "mean"))
    out = out.join(free, how="left")
    out["replacement"] = floor.reindex(out.index)
    # What the money could have bought: the gap between the best asset in the
    # category and what a free one returned. Where it is near zero no price
    # bought anything, however the league spent.
    out["spread"] = out["top"] - out["replacement"]
    out["over_free"] = out["scored"] - out["replacement"]
    out["per_slot"] = out["spend"] / out["slots"].where(out["slots"] > 0)
    return out.reset_index().sort_values("spend", ascending=False)


def value(store: Store, season: str, priced: pd.DataFrame,
          everything: pd.DataFrame | None = None) -> pd.DataFrame:
    """Every priced slot with what the market said about it at the time.

    ``priced`` is the slot table the results page already builds -- cost, score
    and the share arithmetic. This adds the four things the roster cannot know:
    the round, the best rival bid, how contested it was, and how many slots the
    manager still had to fill when they bought it.

    A slot with no bid behind it keeps its score and its price and gets blanks
    for the rest, which is the truth: the snake picks were not bid for.
    """
    if priced is None or priced.empty:
        return priced if priced is not None else pd.DataFrame()
    out = priced.copy()
    # Replacement is read off the whole roster, free picks included, because
    # the free picks are the only measurement of it there is. `priced` has them
    # filtered out by the time it gets here.
    floor = replacement(everything if everything is not None else out)
    out["replacement"] = out["category"].map(floor).fillna(0.0)
    # What this slot returned above a free one. The scarcity test, and the one
    # measure here that does not depend on what anybody paid.
    out["over_free"] = out["score"] - out["replacement"]

    facts = market(store, season)
    if not facts.empty:
        facts = facts[facts["asset_id"].astype(str) != ""]
        facts = facts.sort_values("round").drop_duplicates("asset_id", keep="last")
        out = out.merge(
            facts[["asset_id", "round", "field", "bidders", "demand",
                   "contested", "premium"]],
            on="asset_id", how="left")

    seats = needs(store, season)
    if not seats.empty:
        seats = seats[seats["asset_id"].astype(str) != ""]
        seats = seats.sort_values("round").drop_duplicates("asset_id", keep="last")
        out = out.merge(seats[["asset_id", "open_before", "roster_open"]],
                        on="asset_id", how="left")
    for column in ("round", "field", "bidders", "demand", "contested", "premium",
                   "open_before", "roster_open"):
        if column not in out.columns:
            out[column] = pd.NA
    return out


def by_round(priced: pd.DataFrame) -> pd.DataFrame:
    """What each round cost and what it bought.

    The round is in here twice over -- a round's budget and the board it was
    spent on -- and neither is separable from the other with one season's data.
    What can be said is what a round's dollars went on and what has come back,
    which is worth saying because the three rounds do not look alike.
    """
    if priced is None or priced.empty or "round" not in priced.columns:
        return pd.DataFrame()
    bought = priced[priced["round"].notna()].copy()
    if bought.empty:
        return pd.DataFrame()
    bought["round"] = bought["round"].astype(int)
    out = bought.groupby("round").agg(
        slots=("cost", "size"),
        spend=("cost", "sum"),
        median=("cost", "median"),
        contested=("contested", "sum"),
        premium=("premium", "sum"),
        score=("score", "sum"),
    )
    out["per_hundred"] = out["score"] / out["spend"].where(out["spend"] > 0) * 100
    # What one asset cost in that round's money. The clearest single reading of
    # a round's economy: the board thins, the rollover does not, and the last
    # round pays most for least.
    out["per_asset"] = out["spend"] / out["slots"].where(out["slots"] > 0)
    if "roster_open" in bought.columns:
        out["roster_open"] = bought.groupby("round")["roster_open"].mean()
    return out.reset_index()


def by_manager(priced: pd.DataFrame, managers) -> pd.DataFrame:
    """One row a manager: how they bought, before any question of what it scored."""
    if priced is None or priced.empty:
        return pd.DataFrame()
    bought = priced[priced["cost"].fillna(0) > 0]
    if bought.empty:
        return pd.DataFrame()
    rows = []
    for manager in managers:
        mine = bought[bought["manager_id"] == manager]
        if mine.empty:
            continue
        spend = float(mine["cost"].sum())
        contested = pd.to_numeric(mine.get("contested"), errors="coerce").fillna(0)
        premium = pd.to_numeric(mine.get("premium"), errors="coerce").fillna(0)
        left = pd.to_numeric(mine.get("roster_open"), errors="coerce")
        rows.append({
            "manager": manager,
            "slots": int(len(mine)),
            "spend": spend,
            "contested": int(contested.sum()),
            # Money spent above the best rival offer. In a sealed-bid auction
            # this is what the winner's own number cost them, and with most
            # assets drawing a single bid it is most of the money.
            "premium": float(premium.sum()),
            # How empty their roster was, averaged over their own buys. A
            # manager who spent early bought everything else into a near-full
            # roster; one who held back was still building at every price.
            "roster_open": float(left.mean()) if left.notna().any() else 0.0,
            "over_free": float(pd.to_numeric(
                mine.get("over_free"), errors="coerce").fillna(0).sum()),
        })
    return pd.DataFrame(rows)
