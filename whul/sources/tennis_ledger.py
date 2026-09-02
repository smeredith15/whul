"""Tennis match ledgers.

Tennis is the one league scored from files rather than an API: the existing
Flashscore scrapers already produce a season ledger per tour, one row per match,
and Tennis_Players.R reads those directly. This module reads the same files.

Expected names, as the R script writes them::

    2024-atp-season.csv  2025-atp-season.csv
    2024-wta-season.csv  2025-wta-season.csv

Column names are normalized the way ``janitor::clean_names`` does, so a header
that arrives as ``Winner Code`` or ``winner.code`` still lands on ``winner_code``
and the scoring module finds it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

LEDGER_DIR = Path("data/tennis")
FILE_PATTERN = "{season}-{tour}-season.csv"
TOURS = ("atp", "wta")

#: Columns the scoring module cannot work without. Anything else is optional --
#: a ledger missing ``status_extra`` simply has no cancelled matches to drop.
REQUIRED_COLUMNS = (
    "tournament",
    "round",
    "season_year",
    "home_name",
    "away_name",
    "winner_code",
    "home_set_score",
    "away_set_score",
)

TOUR_LABELS = {"atp": "ATP Tour", "wta": "WTA Tour"}


def clean_names(columns) -> list[str]:
    """Header names as ``janitor::clean_names`` would render them.

    Lowercase, non-alphanumerics collapsed to single underscores, edges trimmed.
    """
    cleaned = []
    for name in columns:
        text = re.sub(r"[^0-9a-zA-Z]+", "_", str(name)).strip("_").lower()
        cleaned.append(re.sub(r"_+", "_", text))
    return cleaned


def ledger_path(season: int, tour: str, directory: Path | None = None) -> Path:
    base = directory or LEDGER_DIR
    return base / FILE_PATTERN.format(season=season, tour=tour.lower())


def read_ledger(path: Path, tour: str | None = None, season: int | None = None) -> pd.DataFrame:
    """One ledger file, with normalized headers and the gaps filled in.

    ``season_year`` and ``tour_type_human`` are derived from the filename when
    the file itself omits them -- the filename carries both, and a ledger
    without a season column would otherwise pool every year together.
    """
    frame = pd.read_csv(path)
    frame.columns = clean_names(frame.columns)

    if "season_year" not in frame.columns and season is not None:
        frame["season_year"] = season
    if "tour_type_human" not in frame.columns and tour is not None:
        frame["tour_type_human"] = TOUR_LABELS.get(tour.lower(), tour.upper())
    return frame


def load_matches(
    seasons: list[int],
    tours: tuple[str, ...] = TOURS,
    directory: Path | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Every available ledger for the seasons and tours requested.

    A missing file is reported rather than raised: a season part-way through has
    no completed ledger for the current year yet, and a WTA file may land later
    than the ATP one.
    """
    frames: list[pd.DataFrame] = []
    missing: list[str] = []
    for season in seasons:
        for tour in tours:
            path = ledger_path(season, tour, directory)
            if not path.exists():
                missing.append(str(path))
                continue
            frame = read_ledger(path, tour=tour, season=season)
            frames.append(frame)
            if verbose:
                print(f"{tour.upper()} {season}: {len(frame):,} matches from {path}", flush=True)

    if verbose and missing:
        print(f"missing ledgers: {', '.join(missing)}", flush=True)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def missing_columns(frame: pd.DataFrame) -> list[str]:
    """Required columns the ledger does not carry."""
    return [c for c in REQUIRED_COLUMNS if c not in frame.columns]


def probe(
    seasons: list[int] | None = None, directory: Path | None = None
) -> dict:
    """Check the ledgers are present and carry what scoring needs."""
    from datetime import date

    seasons = seasons or [date.today().year - 1]
    base = directory or LEDGER_DIR
    report: dict = {"directory": str(base), "seasons": seasons, "stages": {}}

    found = {}
    for season in seasons:
        for tour in TOURS:
            path = ledger_path(season, tour, base)
            found[f"{tour}-{season}"] = path.exists()
    report["stages"]["files"] = {
        "ok": any(found.values()),
        "present": sorted(k for k, v in found.items() if v),
        "absent": sorted(k for k, v in found.items() if not v),
    }
    if not any(found.values()):
        report["stages"]["files"]["note"] = (
            f"no ledgers under {base} -- point --dir at the Flashscore output, "
            f"or copy the {FILE_PATTERN.format(season='YYYY', tour='atp')} files there"
        )
        return report

    try:
        matches = load_matches(seasons, directory=base, verbose=False)
    except Exception as exc:  # noqa: BLE001 -- the probe reports, it does not raise
        report["stages"]["read"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return report

    gaps = missing_columns(matches)
    report["stages"]["read"] = {
        "ok": not gaps,
        "rows": len(matches),
        "columns": sorted(matches.columns)[:30],
        "missing_required": gaps,
    }
    if gaps:
        report["stages"]["read"]["note"] = (
            "the scraper's headers do not match what scoring expects -- the "
            "column list above says what did arrive"
        )
        return report

    from whul.scoring.tennis import eligible_matches, score_players

    eligible = eligible_matches(matches)
    totals = score_players(matches)
    report["stages"]["score"] = {
        "ok": not totals.empty,
        "eligible": f"{len(eligible)}/{len(matches)}",
        "players": len(totals),
        "top": totals.head(5)[["player", "league", "matches_won", "total_points"]].to_dict("records")
        if not totals.empty
        else [],
    }
    return report
