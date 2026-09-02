"""Historical ATP and WTA matches from Jeff Sackmann's archives.

    https://github.com/JeffSackmann/tennis_atp   atp_matches_{year}.csv
    https://github.com/JeffSackmann/tennis_wta   wta_matches_{year}.csv

These are the standard public record of tour matches and they carry the two
facts the scoring tier turns on -- ``tourney_level`` and ``draw_size`` -- as
columns, per event, going back decades. That makes them the source for
benchmark seasons, and the source the tournament calendar is seeded from.

Their round labels (R128, R64, R32, R16, QF, SF, F, RR) are already the ones
``whul.scoring.tennis`` uses, and ``score`` is the same '6-3 7-6(4)' shape, so
nothing needs translating.

One gap: ``tourney_level`` marks the 500s and the 250s alike as ``A``. Which of
the two an event is has to come from the calendar, which is why the calendar
exists rather than the level being read straight through.

UNVERIFIED here: raw.githubusercontent.com answers 404 for these paths from
this sandbox, whose GitHub reads are scoped to the session's own repositories.
Run ``python -m whul.cli probe sackmann`` from a machine with open access.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from whul.scoring.tennis import (
    GRAND_SLAM, INTERNATIONAL, MASTERS_1000, TOUR_250, TOUR_FINALS,
)

REPOS = {
    "ATP": ("JeffSackmann/tennis_atp", "atp_matches_{year}.csv"),
    "WTA": ("JeffSackmann/tennis_wta", "wta_matches_{year}.csv"),
}
#: GitHub renamed default branches en masse, and these repos 404 on ``master``.
#: Both are tried rather than one being assumed, so a rename either way keeps
#: working; the probe reports which one actually answered.
BRANCHES = ("main", "master")
RAW = "https://raw.githubusercontent.com/{repo}/{branch}/{filename}"
API = "https://api.github.com/repos/{repo}"
CACHE = Path("data/cache/sackmann")
TIMEOUT = 60
HEADERS = {"user-agent": "whul/1.0 (fantasy league scoring)"}

#: ``tourney_level`` codes. ``A`` is the ambiguous one -- it covers both the
#: 500s and the 250s -- so it maps to 250 as the more common of the two and the
#: calendar overrides it for the events that are actually 500s.
LEVEL_MAP = {
    "G": GRAND_SLAM,     # Grand Slam
    "M": MASTERS_1000,   # Masters 1000
    "P": MASTERS_1000,   # WTA Premier / 1000
    "PM": MASTERS_1000,  # WTA Premier Mandatory
    "F": TOUR_FINALS,    # Tour Finals
    "D": INTERNATIONAL,  # Davis Cup
    "W": INTERNATIONAL,  # WTA team events (BJK Cup)
    "A": TOUR_250,       # tour-level: 500 or 250, see above
    "I": TOUR_250,       # WTA International
}

#: Levels that are not main tour and never score.
EXCLUDED_LEVELS = ("C", "S", "O")  # Challenger, Satellite/futures, Olympics


def urls_for(tour: str, year: int) -> list[str]:
    """Candidate raw URLs for one tour-season, in branch order."""
    repo, filename = REPOS[tour]
    return [
        RAW.format(repo=repo, branch=branch, filename=filename.format(year=year))
        for branch in BRANCHES
    ]


def _fetch(tour: str, year: int) -> str:
    cached = CACHE / f"{tour.lower()}_{year}.csv"
    if cached.exists():
        return cached.read_text()

    last: Exception | None = None
    for url in urls_for(tour, year):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status != 404:
                raise
            last = exc
            continue
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(response.text)
        return response.text

    raise last if last else RuntimeError(f"no branch served {tour} {year}")


def describe_repo(tour: str) -> dict:
    """What the repository actually looks like right now.

    Called only when every candidate URL 404s. A 404 on a raw path cannot tell
    a wrong branch from a missing file from a moved repository, and the API
    answers all three at once -- which turns the next probe run into a fix
    rather than another guess.
    """
    repo, filename = REPOS[tour]
    try:
        response = requests.get(API.format(repo=repo), headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if response.status_code == 404:
        return {"repo": repo, "exists": False,
                "note": "the repository itself is not there under this name"}
    if response.status_code != 200:
        return {"repo": repo, "status": response.status_code,
                "note": "GitHub API rate limits unauthenticated calls; try again shortly"}
    body = response.json()
    return {
        "repo": repo,
        "exists": True,
        "default_branch": body.get("default_branch"),
        "pushed_at": body.get("pushed_at"),
        "note": f"put {body.get('default_branch')!r} in BRANCHES if it is not there",
    }


def load_matches(
    seasons: list[int], tours: tuple[str, ...] = ("ATP", "WTA"), verbose: bool = True
) -> pd.DataFrame:
    """Main-tour matches for the seasons given, in scoring's own shape.

    A season a tour has not published yet is reported and skipped rather than
    raising -- the current year's file appears part-way through it.
    """
    frames: list[pd.DataFrame] = []
    for season in seasons:
        for tour in tours:
            try:
                text = _fetch(tour, season)
            except requests.RequestException as exc:
                if verbose:
                    print(f"{tour} {season}: unavailable ({exc})", flush=True)
                continue
            raw = pd.read_csv(StringIO(text))
            frames.append(_to_matches(raw, tour, season))
            if verbose:
                print(f"{tour} {season}: {len(raw):,} matches", flush=True)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _to_matches(raw: pd.DataFrame, tour: str, season: int) -> pd.DataFrame:
    level = raw.get("tourney_level", pd.Series("", index=raw.index)).astype(str).str.strip()
    keep = ~level.isin(EXCLUDED_LEVELS)
    raw = raw[keep]
    level = level[keep]

    return pd.DataFrame(
        {
            "season": season,
            "tour": tour,
            "tournament": raw.get("tourney_name", "").astype(str),
            "tourney_id": raw.get("tourney_id", "").astype(str),
            "level": level.to_numpy(),
            "category": level.map(LEVEL_MAP).fillna(TOUR_250).to_numpy(),
            "draw_size": pd.to_numeric(raw.get("draw_size"), errors="coerce").to_numpy(),
            "date": pd.to_datetime(
                raw.get("tourney_date"), format="%Y%m%d", errors="coerce"
            ).dt.date.astype(str).to_numpy(),
            "round": raw.get("round", "").astype(str).str.upper().to_numpy(),
            "winner": raw.get("winner_name", "").astype(str).to_numpy(),
            "loser": raw.get("loser_name", "").astype(str).to_numpy(),
            "score": raw.get("score", "").astype(str).to_numpy(),
            "best_of": pd.to_numeric(raw.get("best_of"), errors="coerce").to_numpy(),
        }
    ).reset_index(drop=True)


def tournaments(matches: pd.DataFrame) -> pd.DataFrame:
    """The distinct events in a match set -- the calendar's raw material.

    ``draw_size`` is taken as the maximum reported for an event: the column is
    per match and constant in practice, but a stray row should not shrink an
    event's draw and move it onto a smaller points table.
    """
    if matches is None or matches.empty:
        return pd.DataFrame(
            columns=["season", "tour", "tournament", "category", "draw_size", "level"]
        )
    return (
        matches.groupby(["season", "tour", "tournament"], as_index=False)
        .agg(
            category=("category", "first"),
            draw_size=("draw_size", "max"),
            level=("level", "first"),
            matches=("round", "size"),
        )
        .sort_values(["season", "tour", "tournament"])
        .reset_index(drop=True)
    )


def probe(season: int | None = None) -> dict:
    """Check the archives are reachable and still shaped as expected."""
    from datetime import date

    season = season or (date.today().year - 1)
    report: dict = {"season": season, "stages": {}}

    try:
        text = _fetch("ATP", season)
    except Exception as exc:  # noqa: BLE001 -- the probe reports, it does not raise
        report["stages"]["fetch"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "urls_tried": urls_for("ATP", season),
            "repository": describe_repo("ATP"),
            "note": "every branch 404'd -- the repository block above gives "
                    "the real default branch, or says the repo has moved",
        }
        return report

    raw = pd.read_csv(StringIO(text))
    needed = ["tourney_name", "tourney_level", "draw_size", "round", "score", "winner_name"]
    missing = [c for c in needed if c not in raw.columns]
    report["stages"]["fetch"] = {
        "ok": not missing,
        "rows": len(raw),
        "missing_columns": missing,
        "columns": sorted(raw.columns)[:20],
    }
    if missing:
        return report

    matches = _to_matches(raw, "ATP", season)
    events = tournaments(matches)
    report["stages"]["parse"] = {
        "ok": not events.empty,
        "matches": len(matches),
        "tournaments": len(events),
        "levels": sorted(set(matches["level"])),
        "ambiguous_500_or_250": int((events["level"] == "A").sum()),
        "sample": events.head(5).to_dict("records"),
    }

    from whul.scoring.tennis import score_players

    totals = score_players(matches)
    report["stages"]["score"] = {
        "ok": not totals.empty,
        "players": len(totals),
        "top": totals.head(5)[["player", "league", "matches_won", "total_points"]].to_dict("records")
        if not totals.empty
        else [],
    }
    return report
