"""Whole seasons from FotMob, player by player, for deciding how to score them.

Before club soccer players are scored on FotMob's figures the league chooses
between its rating, the counting stats priced in ``whul.scoring.soccer_match``,
or both -- and that is a question about what each does to a real season: who
tops each position, how far defenders sit from forwards, how closely the
rating follows the stats. This pulls the seasons to answer it with.

It writes, to ``out``:

    lines-<competition>-<season>.csv.gz   one row per player per match, every
                                          figure the reader keeps and the
                                          match's points under the agreed values
    study.txt                             what was read, what was not, and each
                                          position's leaders three ways

Read only; nothing reaches the database. Finished matches are kept in
``whul.sources.fotmob.CACHE``, so a second run over the same seasons costs no
requests.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from whul.scoring import soccer_match as sm
from whul.sources import fotmob

#: Where a season's days lie, for the day-by-day fallback when a competition's
#: own page will not list its matches. Generous: a day with nothing costs one
#: request and finds nothing.
EUROPEAN_SPAN = ((7, 15), (6, 15))     # mid-July to mid-June
CALENDAR_SPAN = ((2, 1), (12, 15))


def span(key: str, season: int) -> tuple[date, date]:
    if key in fotmob.CALENDAR_YEAR:
        (m1, d1), (m2, d2) = CALENDAR_SPAN
        return date(season, m1, d1), date(season, m2, d2)
    (m1, d1), (m2, d2) = EUROPEAN_SPAN
    return date(season, m1, d1), date(season + 1, m2, d2)


def study_competition(client: fotmob.Client, key: str, season: int,
                      limit: int | None = None) -> tuple[pd.DataFrame, dict]:
    """Every finished match of one competition's season, as player lines."""
    start, end = span(key, season)
    listed = [m for m in fotmob.season_matches(client, key, season, start, end)
              if m["finished"]]
    if limit:
        listed = listed[:limit]
    rows, unread = [], []
    for match in listed:
        lines = fotmob.cached_lines(match["match_id"], client)
        if lines is None:
            unread.append(match["match_id"])
            if client.stopped:
                break
            continue
        for line in lines:
            parts = sm.components(line)
            rows.append({
                **{k: v for k, v in line.items() if k != "goal_xg"},
                "goal_xg": json.dumps(line.get("goal_xg") or []),
                "study_competition": key, "study_season": season,
                "points": round(sum(parts.values()), 4),
                **{f"pts_{k}": v for k, v in parts.items()},
            })
    summary = {"listed": len(listed), "read": len(listed) - len(unread),
               "unread": unread[:20], "unread_count": len(unread),
               "player_lines": len(rows), "stopped": client.stopped}
    return pd.DataFrame(rows), summary


def leaders(frame: pd.DataFrame, top: int = 10) -> list[str]:
    """Each position's season leaders under the agreed values, under the
    rating, and under the scoring now in use (appearances, goals, assists,
    cards), side by side."""
    if frame.empty:
        return ["  (no player lines)"]
    work = frame[frame["position"].isin(sm.SCORED_POSITIONS)].copy()
    work["old_points"] = (
        work["minutes"].fillna(0).ge(60).map({True: 2.0, False: 1.0})
        + work["goals"].fillna(0) * work["position"].map({"D": 6, "M": 5, "F": 4})
        + work["assists"].fillna(0) * 3 - work["yellow"] - 3 * work["red"])
    # A rating-only score: what each match's rating stands above or below an
    # ordinary performance, summed -- one plausible way to score on it alone.
    work["rating_points"] = (work["rating"].fillna(6.0) - 6.0)
    season = work.groupby(["player_id", "player", "position"], as_index=False).agg(
        team=("team", "last"), matches=("match_id", "nunique"),
        minutes=("minutes", "sum"), points=("points", "sum"),
        old_points=("old_points", "sum"), rating_points=("rating_points", "sum"),
        rating_mean=("rating", "mean"))
    out = []
    for position in sm.SCORED_POSITIONS:
        mine = season[season["position"] == position]
        out.append(f"  {position}: {len(mine)} players")
        for column, label in (("points", "agreed values"), ("rating_points", "rating - 6"),
                              ("old_points", "scoring now")):
            best = mine.nlargest(top, column)
            out.append(f"    by {label}:")
            out += [f"      {r.player:<26} {str(r.team)[:18]:<18} {r.matches:>3} m  "
                    f"pts {r.points:7.1f}  rating-6 {r.rating_points:6.1f}  "
                    f"avg {r.rating_mean:4.2f}  now {r.old_points:6.1f}"
                    for r in best.itertuples()]
    return out


def run(keys: list[str], season: int, out: Path, limit: int | None = None,
        client: fotmob.Client | None = None) -> int:
    client = client or fotmob.Client()
    out.mkdir(parents=True, exist_ok=True)
    report = [f"Soccer study: {', '.join(keys)}, season starting {season}", ""]
    for key in keys:
        frame, summary = study_competition(client, key, season, limit)
        report.append(f"{key} {fotmob.season_label(key, season)}: "
                      f"{summary['read']} of {summary['listed']} finished matches read, "
                      f"{summary['player_lines']} player lines"
                      + (f"; unread {summary['unread_count']}: {summary['unread']}"
                         if summary["unread_count"] else "")
                      + ("; STOPPED after repeated failures" if summary["stopped"] else ""))
        if not frame.empty:
            frame.to_csv(out / f"lines-{key}-{season}.csv.gz", index=False)
            shots = frame["has_shotmap"].mean() if "has_shotmap" in frame else 0
            report.append(f"  lines with a shot map: {shots:.0%}")
            report += leaders(frame)
        report.append("")
    report.append(f"{client.sent} request(s) sent.")
    (out / "study.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report), flush=True)
    return 1 if client.stopped else 0
