"""The days before a player's first stored score, rebuilt from his games.

MLB players and club-soccer players reached the database two weeks into the
league year -- MLB on 4 September, soccer on the 5th -- each arriving with a
season to date. Every day before that stored nothing, and the progression
graph read it as a leap: every manager's line jumped on the 5th by a fortnight
of baseball and six matchdays of football.

The game record fixes that, because it is dated. A player's score on a day is
his first stored score scaled by how much of it he had earned by then:

    score(D) = score(F) x points(before D) / points(before F)

where F is the first day stored and "before" means games dated earlier than
the day -- a day's pull sees the previous night's games, not that evening's.
The scorers are linear in their counting stats, so this is exact wherever the
season is nothing but counting stats. MLB's is not quite: a share of it is
Offense and Defense, run values the feed reports for a season and that are
shared out across a player's own games (see ``whul.sources.mlb``). That share
is scaled by games played instead of points, which is how it was shared out.

Nothing is invented where there is nothing to scale from. A player with no
stored score, or no games before his first stored day, is left alone.

Days that *were* stored are also rebuilt, never written, and compared with what
is there: that is the check that the method reproduces the scorer, reported in
the run's output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from whul.config.league import SEASON, season_start
from whul.store.db import Store, _now


#: How far a rebuilt day may sit from the stored one and still agree.
AGREES = 0.05


@dataclass
class Report:
    written: int = 0
    players: int = 0
    checked: int = 0
    agreed: int = 0
    restated: int = 0
    disagreeing: dict[str, int] = field(default_factory=dict)
    per_player: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"Rebuilt {self.written} day(s) for {self.players} player(s)."]
        if self.checked:
            lines.append(
                f"  Checked by rebuilding {self.checked} stored day(s): "
                f"{self.agreed} agree within {AGREES}.")
            if self.disagreeing:
                named = ", ".join(f"{a} ({n})" for a, n in
                                  sorted(self.disagreeing.items())[:12])
                lines.append(
                    "  Stored days that do not match the games played by then "
                    f"-- the stored day is the one to doubt: {named}")
        lines += [f"  {line}" for line in self.per_player]
        return "\n".join(lines)


def _fraction(games: pd.DataFrame, day: date, first: date,
              advanced: float) -> float | None:
    """How much of the first stored day's score had been earned by ``day``."""
    before = games[games["date"] < day]
    total = games[games["date"] < first]
    points_total = float(total["points"].sum())
    count_total = len(total)
    if points_total <= 0 or count_total == 0:
        return None
    counting = float(before["points"].sum()) / points_total
    played = len(before) / count_total
    return (1 - advanced) * counting + advanced * played


def rebuild(store: Store, season: str, write: bool = False,
            check_days: int = 14, restate: tuple[str, ...] = ()) -> Report:
    """Fill the days before each player's first stored score from his games.

    ``restate`` names leagues whose stored days are rebuilt too, from the
    latest one, rather than only the days before the first. MLB is the case:
    its stored days came from date-range totals that ran days behind the games,
    and its latest day is summed from the game logs themselves. Only the score
    and the points are rewritten on a stored day; a held bonus stays as it was.
    """
    report = Report()
    restating: list[tuple] = []
    games = store.query(
        # The regular season's games: they are what a season score is made
        # of, and a playoff game in the ratio would spread October into August.
        "SELECT asset_id, date, points FROM game_scores WHERE season = ? "
        "AND phase = 'regular'", (season,))
    if games.empty:
        return report
    games["date"] = pd.to_datetime(games["date"]).dt.date
    stored = store.query(
        "SELECT d.asset_id, d.as_of, d.scaled_score, d.league_points, "
        "       d.benchmark_version, a.league "
        "FROM daily_scores d JOIN assets a ON a.asset_id = d.asset_id "
        "WHERE d.season = ? ORDER BY d.asset_id, d.as_of", (season,))
    if stored.empty:
        return report
    stored["as_of"] = pd.to_datetime(stored["as_of"]).dt.date

    rows: list[dict] = []
    for asset_id, mine in games.groupby("asset_id"):
        series = stored[stored["asset_id"] == asset_id]
        if series.empty:
            continue
        first = series.iloc[0]
        first_day = first["as_of"]
        league = str(first["league"] or "")
        advanced = _advanced_share(store, asset_id, first_day)
        opened = max(SEASON.start, season_start(league) if league else SEASON.start)

        # The check: every day that was stored, rebuilt by the same formula
        # from the latest one, and compared. It is the backfill's own
        # arithmetic pointed at days whose answer is known.
        last = series.iloc[-1]
        for earlier in series.iloc[:-1].tail(check_days).itertuples():
            share = _fraction(mine, earlier.as_of, last["as_of"], advanced)
            if share is None:
                continue
            report.checked += 1
            if abs(float(last["scaled_score"]) * share
                   - float(earlier.scaled_score)) <= AGREES:
                report.agreed += 1
            else:
                report.disagreeing[asset_id] = report.disagreeing.get(asset_id, 0) + 1

        if league in restate and len(series) > 1:
            # Every day before the latest, from the latest: stored days are
            # rewritten in place, and the days before the first are filled.
            anchor = last
            advanced = _advanced_share(store, asset_id, anchor["as_of"])
            stored_days = set(series["as_of"])
            day = opened
            count = 0
            while day < anchor["as_of"]:
                share = _fraction(mine, day, anchor["as_of"], advanced)
                if share is not None:
                    score = round(float(anchor["scaled_score"]) * share, 6)
                    points = round(float(anchor["league_points"]) * share, 6)
                    if day in stored_days:
                        restating.append((score, points, asset_id, season, day.isoformat()))
                    else:
                        rows.append({
                            "asset_id": asset_id, "season": season,
                            "as_of": day.isoformat(), "league_points": points,
                            "postseason_bonus": 0.0, "held_score": 0.0,
                            "scaled_score": score,
                            "benchmark_version": anchor["benchmark_version"],
                            "computed_at": _now(),
                        })
                    count += 1
                day += timedelta(days=1)
            if count:
                report.players += 1
                report.restated += 1
                report.per_player.append(
                    f"{asset_id}: {count} day(s) restated from {anchor['as_of']}'s "
                    f"{float(anchor['scaled_score']):.2f}")
            continue

        days = []
        day = opened
        while day < first_day:
            share = _fraction(mine, day, first_day, advanced)
            if share is not None:
                days.append((day, share))
            day += timedelta(days=1)
        if not days:
            continue
        report.players += 1
        report.per_player.append(
            f"{asset_id}: {len(days)} day(s) from {days[0][0]} to {days[-1][0]}, "
            f"reaching {float(first['scaled_score']) * days[-1][1]:.2f} before "
            f"the {float(first['scaled_score']):.2f} stored on {first_day}")
        for day, share in days:
            rows.append({
                "asset_id": asset_id, "season": season, "as_of": day.isoformat(),
                "league_points": round(float(first["league_points"]) * share, 6),
                "postseason_bonus": 0.0, "held_score": 0.0,
                "scaled_score": round(float(first["scaled_score"]) * share, 6),
                "benchmark_version": first["benchmark_version"],
                "computed_at": _now(),
            })

    if write and rows:
        store.upsert("daily_scores", rows, keys=("asset_id", "season", "as_of"))
    if write and restating:
        with store.transaction() as conn:
            conn.executemany(
                "UPDATE daily_scores SET scaled_score = ?, league_points = ? "
                "WHERE asset_id = ? AND season = ? AND as_of = ?", restating)
    report.written = len(rows) + len(restating)
    return report


def _advanced_share(store: Store, asset_id: str, day: date) -> float:
    """The share of an MLB player's season that is run values rather than
    counting stats, as the line his first score was built from says."""
    text = store.scalar(
        "SELECT stats FROM raw_stats WHERE asset_id = ? AND as_of = ? "
        "AND phase = 'regular' LIMIT 1", (asset_id, day.isoformat()))
    if not text:
        return 0.0
    try:
        share = float(json.loads(text).get("advanced_share") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return min(max(share, 0.0), 1.0)
