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
    return Book(
        titles=_title_rows(seasons, closed, managers),
        quarter_wins=_quarter_win_rows(quarterly, managers),
        seasons=season_marks,
        best_quarters=sorted(marks, key=lambda m: -m.value),
        worst_quarters=sorted(marks, key=lambda m: m.value),
        empty=nothing,
    )
