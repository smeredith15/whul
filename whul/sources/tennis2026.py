"""Tennis results from the tennis2026 app's own database.

The static snapshot stops in February 2026. The app that produced it keeps
going -- its scrapers write every completed match into ``tennis2026.db`` -- so
the ongoing record is a local file read rather than a scrape, and it covers the
gap the Flashscore feed's seven-day window cannot reach back into.

The two sources are the same data at different vintages, so they are read into
the same shape and can be concatenated: the snapshot for the seasons it covers,
this for everything after.

**Only completed main-draw wins are returned.** Qualifying is excluded here
rather than downstream, because a missing qualifying result would otherwise
read as a main-draw bye and pay for a round nobody played.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from whul.scoring.tennis import (
    GRAND_SLAM, INTERNATIONAL, MASTERS_1000, TOUR_250, TOUR_500, TOUR_FINALS,
)

#: The app keeps its database at the backend root by default.
DB_NAME = "tennis2026.db"
CHECKOUT_CANDIDATES = (
    Path("../tennis2026"),
    Path("../smeredith15/tennis2026"),
    Path("~/tennis2026"),
)
#: Where inside a checkout the file tends to sit. The app resolves a relative
#: sqlite URL against wherever it was started from, so both are worth trying.
DB_LOCATIONS = (Path("backend") / DB_NAME, Path(DB_NAME))

#: The app's category vocabulary, which names the tour; ours does not, because
#: the points table is the same for both and only the tier matters.
CATEGORIES = {
    "Grand Slam": GRAND_SLAM,
    "ATP Masters 1000": MASTERS_1000,
    "WTA 1000": MASTERS_1000,
    "ATP 500": TOUR_500,
    "WTA 500": TOUR_500,
    "ATP 250": TOUR_250,
    "WTA 250": TOUR_250,
    "ATP Finals": TOUR_FINALS,
    "WTA Finals": TOUR_FINALS,
    "International": INTERNATIONAL,
}

#: Main-draw rounds only. Qualifying is dropped in the query itself.
MAIN_DRAW = ("RR", "R128", "R64", "R32", "R16", "QF", "SF", "F")

QUERY = """
    SELECT
        m.match_date      AS date,
        t.name            AS tournament,
        t.tour            AS tour,
        t.category        AS category,
        t.draw_size       AS draw_size,
        m.round           AS round,
        m.score           AS score,
        m.walkover        AS walkover,
        m.retired         AS retired,
        w.name            AS winner,
        l.name            AS loser
    FROM match_results m
    JOIN tournaments t ON t.id = m.tournament_id
    JOIN players w     ON w.id = m.player_id
    LEFT JOIN players l ON l.id = m.opponent_id
    WHERE m.won = 1
      AND m.match_date IS NOT NULL
      AND m.round IN ({rounds})
"""


def candidate_paths(path: Path | None = None) -> list[Path]:
    """Everywhere the app's database might be, in the order worth trying."""
    if path is not None:
        return [Path(path)]
    env = os.environ.get("WHUL_TENNIS2026_DB")
    if env:
        return [Path(env)]
    roots = [Path(os.environ["WHUL_TENNIS2026"])] if os.environ.get("WHUL_TENNIS2026") \
        else list(CHECKOUT_CANDIDATES)
    return [
        Path(root).expanduser() / location
        for root in roots for location in DB_LOCATIONS
    ]


def default_path(path: Path | None = None) -> Path:
    """Where the app's database is, tried in the order it tends to be found."""
    candidates = candidate_paths(path)
    return next((c for c in candidates if c.exists()), candidates[0])


def load_matches(
    path: Path | None = None,
    since: date | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Completed main-draw wins, in the shape ``tennis.score_matches`` expects.

    ``since`` is applied in the query rather than after, so a database holding
    several seasons costs no more to read for one of them.
    """
    database = default_path(path)
    if not database.exists():
        looked = "\n  ".join(str(c) for c in candidate_paths(path))
        raise FileNotFoundError(
            f"No tennis2026 database found. It is the app's own file, written by "
            f"its scrapers and never committed, so a fresh clone will not have "
            f"one -- it lives wherever the app actually runs. Copy it over, or "
            f"set WHUL_TENNIS2026_DB to point at it.\n\nLooked in:\n  {looked}"
        )

    sql = QUERY.format(rounds=", ".join("?" for _ in MAIN_DRAW))
    params: list = list(MAIN_DRAW)
    if since:
        sql += " AND m.match_date >= ?"
        params.append(since.isoformat())

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        frame = pd.read_sql_query(sql, conn, params=params)

    if frame.empty:
        if verbose:
            print(f"tennis2026: no completed matches in {database}", flush=True)
        return frame

    return _shape(frame, verbose)


def _shape(frame: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Rename the app's vocabulary into the scorer's."""
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out[out["date"].notna()]
    out["season"] = out["date"].dt.year
    out["date"] = out["date"].dt.date.astype(str)

    # The app stores the enum's name in some columns and its value in others,
    # depending on how the row was written; both spellings resolve here rather
    # than leaving an unmapped category to be scored as nothing.
    spelled = out["category"].astype(str)
    out["category"] = spelled.map(CATEGORIES).fillna(
        spelled.str.replace("_", " ").str.title().map(CATEGORIES)
    )
    unknown = sorted(set(spelled[out["category"].isna()]))
    out = out[out["category"].notna()]
    if unknown and verbose:
        print(f"tennis2026: unmapped categories dropped: {', '.join(unknown)}", flush=True)

    out["tour"] = out["tour"].astype(str).str.upper().str.replace("TOUR.", "", regex=False)
    # A walkover is not a win anyone played for, and the scorer refuses to pay a
    # straight-sets bonus on one; dropping it here keeps it out of the round set
    # a bye is inferred from as well.
    played = ~out["walkover"].fillna(0).astype(bool)
    if verbose:
        print(
            f"tennis2026: {int(played.sum()):,} completed main-draw wins "
            f"({len(out) - int(played.sum()):,} walkovers dropped)",
            flush=True,
        )
    keep = ["season", "date", "tour", "tournament", "category", "draw_size",
            "round", "winner", "loser", "score"]
    return out[played][keep].reset_index(drop=True)


def probe(path: Path | None = None) -> dict:
    """What the database holds, so a gap is visible before it is scored."""
    database = default_path(path)
    report: dict = {
        "path": str(database),
        "exists": database.exists(),
        "looked_in": [str(c) for c in candidate_paths(path)],
    }
    if not database.exists():
        report["error"] = (
            "no database found; it is never committed, so a fresh clone will not "
            "have one. Copy it from wherever the app runs, or set "
            "WHUL_TENNIS2026_DB"
        )
        return report
    try:
        frame = load_matches(database, verbose=False)
    except Exception as exc:  # noqa: BLE001 -- a probe reports, it does not raise
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report

    report["matches"] = len(frame)
    if frame.empty:
        return report
    report["first"] = str(frame["date"].min())
    report["last"] = str(frame["date"].max())
    report["seasons"] = sorted(int(s) for s in frame["season"].unique())
    report["tours"] = sorted(set(frame["tour"]))
    report["by_season"] = (
        frame.groupby("season").size().sort_index().to_dict()
    )
    return report
