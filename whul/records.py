"""The record book: what has actually finished.

A record is of a completed thing. That is the whole design, and it is the one
decision here worth arguing about, so it is stated rather than implied: the
season's leader is not the best season, and whoever is top of a quarter that
still has three weeks to run has not won it. Taking the maximum of a running
total would put today's standings under a heading that says "best ever" and
then quietly restate it tomorrow.

So a season enters the book when the league year has closed, and a quarter when
its last day has passed. Everything still being played is reported separately
and marked, because a page that says only "nothing has finished yet" is true
and useless, and a reader wants to know what is on course.

In September 2026 that means the book is empty and everything is on course,
which is what a league one month into its first season should look like.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from whul.config.league import SEASON, SeasonWindow, quarters
from whul.store.db import Store


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return pd.to_datetime(value).date()
    except Exception:  # noqa: BLE001 -- a bad date is "no date", not a crash
        return None


@dataclass(frozen=True)
class Mark:
    """One line of the book: who, how much, and when.

    ``settled`` is what separates a record from a leaderboard. A mark that is
    not settled is still being played for, and every table here keeps the two
    apart rather than sorting them together.
    """

    manager: str
    value: float
    season: str
    label: str = ""
    settled: bool = True
    detail: str = ""


#: Seasons the league has finished, in order. Read from a window per season
#: rather than from the data: a season with scores in it is a season that
#: started, which is not the same as one that ended.
def finished_seasons(as_of: date, windows=None) -> list[str]:
    """Which league years have closed by ``as_of``."""
    windows = windows or {SEASON.label: SEASON}
    return sorted(label for label, window in windows.items()
                  if window.end < as_of)


def season_totals(store: Store, seasons: list[str] | None = None) -> pd.DataFrame:
    """Every manager's final standing in every season the store holds.

    The last snapshot of each season, which for a season still being played is
    today and for a closed one is the day it ended.
    """
    rows = store.query(
        "SELECT s.season, s.manager_id, s.total, s.rank, s.as_of "
        "FROM standings_snapshots s "
        "JOIN (SELECT season, MAX(as_of) AS last FROM standings_snapshots "
        "      GROUP BY season) latest "
        "  ON latest.season = s.season AND latest.last = s.as_of "
        "ORDER BY s.season, s.total DESC"
    )
    if rows.empty:
        return rows
    if seasons is not None:
        rows = rows[rows["season"].isin(seasons)]
    return rows.reset_index(drop=True)


def quarter_totals(store: Store, season: str,
                   window: SeasonWindow | None = None,
                   as_of: date | None = None) -> pd.DataFrame:
    """What each manager gained in each quarter of one season.

    The same arithmetic the standings page shows: a quarter's gain is the
    total on its last recorded day less the total on the day before it opened.
    A quarter with no snapshot inside it is left out rather than reported as
    zero -- nobody scored nothing, the season had not reached it.

    A quarter is settled when its last day is behind *both* the calendar and
    the data. Either alone is not enough: a quarter whose end has passed but
    whose feeds stopped a fortnight early would be recorded as final on a
    fortnight of missing scores, and one the data happens to run past has not
    been played yet if the day has not come.
    """
    window = window or SEASON
    snaps = store.query(
        "SELECT manager_id, as_of, total FROM standings_snapshots "
        "WHERE season = ? ORDER BY as_of", (season,),
    )
    if snaps.empty:
        return pd.DataFrame(columns=["season", "quarter", "manager_id", "gained",
                                     "through", "settled"])
    snaps["as_of"] = pd.to_datetime(snaps["as_of"]).dt.date
    reached = snaps["as_of"].max()
    last_day = min(reached, as_of) if as_of else reached

    rows = []
    for quarter in quarters(window):
        inside = snaps[(snaps["as_of"] >= quarter.start)
                       & (snaps["as_of"] <= quarter.end)]
        if inside.empty:
            continue
        before = snaps[snaps["as_of"] < quarter.start]
        base = {}
        if not before.empty:
            edge = before["as_of"].max()
            base = {str(r.manager_id): float(r.total)
                    for r in before[before["as_of"] == edge].itertuples()}
        through = inside["as_of"].max()
        closed = inside[inside["as_of"] == through]
        for row in closed.itertuples():
            who = str(row.manager_id)
            rows.append({
                "season": season,
                "quarter": quarter.label,
                "manager_id": who,
                "gained": float(row.total) - base.get(who, 0.0),
                "through": through,
                # Settled when the quarter's last day is behind both the
                # calendar and the data. A quarter the season has not reached
                # has no row at all; one it is inside is still moving.
                "settled": bool(quarter.end <= last_day),
            })
    return pd.DataFrame(rows)


@dataclass
class Book:
    """Everything the records page shows, computed once."""

    titles: pd.DataFrame = field(default_factory=pd.DataFrame)
    quarter_wins: pd.DataFrame = field(default_factory=pd.DataFrame)
    seasons: list[Mark] = field(default_factory=list)
    best_quarters: list[Mark] = field(default_factory=list)
    worst_quarters: list[Mark] = field(default_factory=list)
    best_assets: list = field(default_factory=list)
    worst_assets: list = field(default_factory=list)
    best_picks: list = field(default_factory=list)
    worst_picks: list = field(default_factory=list)
    margins: list[Mark] = field(default_factory=list)
    top_days: pd.DataFrame = field(default_factory=pd.DataFrame)
    head_to_head: pd.DataFrame = field(default_factory=pd.DataFrame)
    #: True while nothing at all has finished, which is the state of a league
    #: one month into its first season and is worth saying outright.
    empty: bool = True


def _title_rows(seasons: pd.DataFrame, closed: list[str],
                managers: list[str]) -> pd.DataFrame:
    """One row a manager: seasons won, and the podium beneath it.

    Only closed seasons count. A manager leading a season in September has won
    nothing, and a table that says otherwise will say something different in
    October.
    """
    done = seasons[seasons["season"].isin(closed)] if not seasons.empty else seasons
    counts = {m: {"titles": 0, "second": 0, "third": 0, "seasons": 0}
              for m in managers}
    if not done.empty:
        for label, group in done.groupby("season"):
            order = group.sort_values("total", ascending=False)
            for place, row in enumerate(order.itertuples(), start=1):
                who = str(row.manager_id)
                seat = counts.setdefault(
                    who, {"titles": 0, "second": 0, "third": 0, "seasons": 0})
                seat["seasons"] += 1
                if place == 1:
                    seat["titles"] += 1
                elif place == 2:
                    seat["second"] += 1
                elif place == 3:
                    seat["third"] += 1
    out = pd.DataFrame([{"manager_id": m, **counts[m]} for m in managers])
    return out.sort_values(["titles", "second", "third"], ascending=False)


def _quarter_win_rows(quarterly: pd.DataFrame, managers: list[str]) -> pd.DataFrame:
    """Quarters won, counting only the ones that have closed."""
    counts = {m: 0 for m in managers}
    played = 0
    if not quarterly.empty:
        settled = quarterly[quarterly["settled"]]
        for _, group in settled.groupby(["season", "quarter"]):
            played += 1
            best = group.sort_values("gained", ascending=False).iloc[0]
            who = str(best["manager_id"])
            counts[who] = counts.get(who, 0) + 1
    out = pd.DataFrame([{"manager_id": m, "won": counts.get(m, 0)} for m in managers])
    out.attrs["played"] = played
    return out.sort_values("won", ascending=False)


def _season_marks(seasons: pd.DataFrame, closed: list[str]) -> list[Mark]:
    if seasons.empty:
        return []
    return sorted(
        (Mark(manager=str(r.manager_id), value=float(r.total), season=str(r.season),
              label=str(r.season), settled=str(r.season) in closed,
              detail=f"through {r.as_of}")
         for r in seasons.itertuples()),
        key=lambda m: -m.value,
    )


def _quarter_marks(quarterly: pd.DataFrame) -> list[Mark]:
    if quarterly.empty:
        return []
    return [
        Mark(manager=str(r.manager_id), value=float(r.gained), season=str(r.season),
             label=f"{r.season} {r.quarter}", settled=bool(r.settled),
             detail=f"through {r.through}")
        for r in quarterly.itertuples()
    ]


def book(store: Store, managers: list[str], as_of: date | str,
         windows: dict[str, SeasonWindow] | None = None) -> Book:
    """The whole record book, from whatever the store holds."""
    today = _as_date(as_of) or SEASON.end
    windows = windows or {SEASON.label: SEASON}
    closed = finished_seasons(today, windows)

    seasons = season_totals(store)
    quarterly = pd.concat(
        [quarter_totals(store, label, windows.get(label), today)
         for label in (sorted(seasons["season"].unique()) if not seasons.empty else [])],
        ignore_index=True,
    ) if not seasons.empty else pd.DataFrame()

    marks = _quarter_marks(quarterly)
    season_marks = _season_marks(seasons, closed)
    # Empty is about what is in the book, not about what the calendar says
    # ought to be. A league year that closed before anybody played it is a
    # window with no season behind it, and it must not make the page claim
    # there are records to read.
    nothing = (not any(m.settled for m in season_marks)
               and not any(m.settled for m in marks))
    slots = final_slots(store)
    assets = _asset_marks(slots, closed)
    return Book(
        titles=_title_rows(seasons, closed, managers),
        quarter_wins=_quarter_win_rows(quarterly, managers),
        seasons=season_marks,
        best_quarters=sorted(marks, key=lambda m: -m.value),
        worst_quarters=sorted(marks, key=lambda m: m.value),
        best_assets=sorted(assets, key=lambda m: -m.score),
        worst_assets=sorted(assets, key=lambda m: m.score),
        best_picks=_ranked_picks(assets, worst=False),
        worst_picks=_ranked_picks(assets, worst=True),
        margins=sorted(_margins(seasons, closed), key=lambda m: -m.value),
        top_days=days_at_top(store, managers),
        head_to_head=career_head_to_head(store, managers),
        empty=nothing,
    )


# --- assets, picks, margins and days at the top -----------------------------

#: One row per slot per season, at that season's last recorded day.
FINAL_SLOTS = (
    "SELECT ss.season, ss.as_of, ss.asset_id, ss.score, ss.counts, "
    "       r.manager_id, r.category, a.display_name, a.asset_type, o.cost "
    "FROM slot_scores ss "
    "JOIN (SELECT season, MAX(as_of) AS d FROM slot_scores GROUP BY season) last "
    "  ON last.season = ss.season AND last.d = ss.as_of "
    "JOIN roster_slots r ON r.slot_id = ss.slot_id "
    "JOIN slot_occupancy o ON o.slot_id = ss.slot_id AND o.end_date IS NULL "
    "JOIN assets a ON a.asset_id = ss.asset_id"
)


def final_slots(store: Store) -> pd.DataFrame:
    """Every rostered slot with the score it finished the season on."""
    out = store.query(FINAL_SLOTS)
    if out.empty:
        return out
    out["score"] = pd.to_numeric(out["score"], errors="coerce").fillna(0.0)
    out["cost"] = pd.to_numeric(out["cost"], errors="coerce").fillna(0.0)
    return out


@dataclass(frozen=True)
class AssetMark:
    """One asset's season: what it scored, what it cost, who held it."""

    manager: str
    asset_id: str
    name: str
    asset_type: str
    category: str
    score: float
    cost: float
    season: str
    settled: bool = True

    @property
    def per_dollar(self) -> float:
        return self.score / self.cost if self.cost > 0 else 0.0


def _asset_marks(slots: pd.DataFrame, closed: list[str]) -> list[AssetMark]:
    if slots.empty:
        return []
    return [
        AssetMark(manager=str(r.manager_id), asset_id=str(r.asset_id),
                  name=str(r.display_name), asset_type=str(r.asset_type),
                  category=str(r.category), score=float(r.score),
                  cost=float(r.cost), season=str(r.season),
                  settled=str(r.season) in closed)
        for r in slots.itertuples()
    ]


def _ranked_picks(marks: list[AssetMark], worst: bool) -> list[AssetMark]:
    """Value for money, ranked.

    Best is points per dollar outright. Worst clamps a negative score to zero
    before dividing, because dividing a negative by a larger price makes the
    bigger overpay look *better* -- the Broncos at $141 for −0.80 would rank
    above the Rams at $125 for −0.77, which is backwards. Clamped, the two tie
    at nothing per dollar and the tie-break is price, so the worst pick is the
    one that spent most to get nothing.

    No price floor either way. One was tried and it was borrowed reasoning: a
    dollar pick that returns nothing really is a worse buy than nothing, and
    the tie-break already keeps it off the top of the list, since every
    scoreless pick ties at nought per dollar and the dearest sorts first.
    """
    priced = [m for m in marks if m.cost > 0]
    if not worst:
        return sorted(priced, key=lambda m: -m.per_dollar)
    return sorted(priced, key=lambda m: (max(m.score, 0.0) / m.cost, -m.cost))


def _margins(seasons: pd.DataFrame, closed: list[str]) -> list[Mark]:
    """How far the winner of each season finished ahead of second.

    One row a season rather than a row a manager: the margin belongs to the
    season, and the name on it is whoever won.
    """
    if seasons.empty:
        return []
    out = []
    for label, group in seasons.groupby("season"):
        order = group.sort_values("total", ascending=False)
        if len(order) < 2:
            continue
        first, second = order.iloc[0], order.iloc[1]
        out.append(Mark(
            manager=str(first["manager_id"]),
            value=float(first["total"]) - float(second["total"]),
            season=str(label), label=str(label),
            settled=str(label) in closed,
            detail=f"over {second['manager_id']}",
        ))
    return out


def days_at_top(store: Store, managers: list[str]) -> pd.DataFrame:
    """How many recorded days each manager has spent leading, and the longest
    run of them.

    Every day counts, including days inside a season still being played. A
    title is awarded when a season ends and a day at the top is a thing that
    happened on the day, so unlike the rest of this module there is nothing
    here to wait for.

    **A run is counted in recorded days, not calendar days.** The site has not
    published every day of its life and never will -- a failed build, a season
    that had not started -- and a day nobody recorded is a day with no evidence
    the lead changed hands, so it must not break a run that the evidence says
    held. Counting recorded days also keeps the two columns in the same unit:
    the run is a stretch of the days the total is made of.

    A season boundary does break one. Being top in May and top again in
    September is two runs, and calling it one would be a claim about a summer
    in which the league was not being played.
    """
    rows = store.query(
        "SELECT season, as_of, manager_id FROM standings_snapshots "
        "WHERE rank = 1 ORDER BY season, as_of"
    )
    tops: dict[str, set[tuple[str, str]]] = {}
    for r in rows.itertuples():
        tops.setdefault(str(r.manager_id), set()).add((str(r.season), str(r.as_of)))

    every = store.query(
        "SELECT DISTINCT season, as_of FROM standings_snapshots "
        "ORDER BY season, as_of"
    )
    days: list[tuple[str, str]] = [
        (str(r.season), str(r.as_of)) for r in every.itertuples()
    ]

    out = []
    for manager in managers:
        held = tops.get(manager, set())
        run = best = 0
        current = None
        for season, when in days:
            if season != current:
                run = 0
                current = season
            run = run + 1 if (season, when) in held else 0
            best = max(best, run)
        out.append({"manager_id": manager, "days": len(held), "streak": best})
    frame = pd.DataFrame(out, columns=["manager_id", "days", "streak"])
    frame.attrs["days"] = len(days)
    return frame.sort_values(["days", "streak"], ascending=False)


def career_head_to_head(store: Store, managers: list[str]) -> pd.DataFrame:
    """Every meeting in every season, as one record a pair.

    The same reader the standings page uses, run over each season and added
    up. A pair that has never met is absent rather than nil-nil.
    """
    from whul import headtohead

    seasons = store.query("SELECT DISTINCT season FROM roster_slots ORDER BY season")
    tally: dict[tuple[str, str], list[int]] = {}
    for season in (seasons["season"] if not seasons.empty else []):
        found = headtohead.meetings(store, str(season))
        if found.empty:
            continue
        for row in found.itertuples():
            one, two = str(row.a_manager), str(row.b_manager)
            key = (one, two) if one < two else (two, one)
            slot = tally.setdefault(key, [0, 0, 0])
            if str(row.won) == "draw":
                slot[2] += 1
            else:
                winner = one if str(row.won) == "a" else two
                slot[0 if winner == key[0] else 1] += 1
    rows = []
    for (one, two), (first, second, drawn) in tally.items():
        rows.append({"one": one, "two": two, "one_won": first,
                     "two_won": second, "drawn": drawn,
                     "played": first + second + drawn})
    return pd.DataFrame(rows, columns=["one", "two", "one_won", "two_won",
                                       "drawn", "played"])
