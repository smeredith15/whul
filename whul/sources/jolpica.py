"""Formula 1 results from Jolpica, the Ergast successor.

Ergast shut down after the 2024 season; Jolpica serves the same JSON at a new
host, so the response shape below is the long-standing Ergast one:

    season results: /ergast/f1/{season}/results/?limit=100&offset=N
    sprint results: /ergast/f1/{season}/sprint/?limit=100&offset=N

Results are paginated 100 at a time and a season runs to roughly 480 finishes,
so a backfill is a handful of requests per season and a nightly update is one.

Points are taken from the feed rather than recomputed. Formula 1 has changed its
points system repeatedly -- the fastest-lap point existed only from 2019 to 2024,
sprints have been scored three different ways -- and the feed already reflects
whichever rules were in force, which a fixed table cannot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import requests

BASE = "https://api.jolpi.ca/ergast/f1"
CACHE = Path("data/cache/jolpica")
PAGE_SIZE = 100
TIMEOUT = 30
#: Guards against an endless loop if the feed ever reports a total it does not
#: serve. A season has never exceeded ~500 result rows.
MAX_PAGES = 20


def _get(path: str, params: dict, cache_key: str | None = None) -> dict:
    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        if cached.exists():
            return json.loads(cached.read_text())

    response = requests.get(f"{BASE}/{path}", params=params, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload))
    return payload


def _races(payload: dict) -> list[dict]:
    return ((payload.get("MRData") or {}).get("RaceTable") or {}).get("Races") or []


def _total(payload: dict) -> int:
    return int((payload.get("MRData") or {}).get("total") or 0)


def fetch_season(season: int, kind: str = "results") -> list[dict]:
    """Every race in a season, following pagination to the reported total."""
    races: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        payload = _get(
            f"{season}/{kind}/",
            {"limit": PAGE_SIZE, "offset": offset},
            cache_key=f"{kind}/{season}-{offset}",
        )
        page = _races(payload)
        races.extend(page)
        offset += PAGE_SIZE
        if offset >= _total(payload) or not page:
            break
    return races


def _result_rows(races: list[dict], season: int, kind: str) -> list[dict]:
    key = "SprintResults" if kind == "sprint" else "Results"
    rows: list[dict] = []
    for race in races:
        for entry in race.get(key) or []:
            driver = entry.get("Driver") or {}
            name = " ".join(
                p for p in (driver.get("givenName"), driver.get("familyName")) if p
            ).strip()
            rows.append(
                {
                    "season": season,
                    "round": race.get("round"),
                    "race": race.get("raceName"),
                    "date": race.get("date"),
                    "driver_name": name or driver.get("driverId", ""),
                    "driver_id": driver.get("driverId", ""),
                    "position": pd.to_numeric(entry.get("position"), errors="coerce"),
                    "points": pd.to_numeric(entry.get("points"), errors="coerce"),
                    "status": entry.get("status", ""),
                    "is_sprint": kind == "sprint",
                }
            )
    return rows


def load_results(seasons: list[int], verbose: bool = True) -> pd.DataFrame:
    """One row per driver per race and per sprint, across the seasons given.

    Sprints are separate rows rather than folded into the grand prix, so the
    two can be told apart downstream -- a sprint win and a grand prix win are
    not the same result.
    """
    rows: list[dict] = []
    for season in seasons:
        for kind in ("results", "sprint"):
            races = fetch_season(season, kind)
            rows.extend(_result_rows(races, season, kind))
        if verbose:
            print(f"F1 {season}: {len(rows)} cumulative rows", flush=True)
    frame = pd.DataFrame(rows)
    # Retirements are real results that score nothing, not missing data; keeping
    # them lets a driver's starts be counted correctly.
    return frame


def daily_update_cost(season: int | None = None) -> float:
    """Seconds for one incremental update -- the current season's first page."""
    import time
    from datetime import date

    season = season or date.today().year
    started = time.monotonic()
    _get(f"{season}/results/", {"limit": PAGE_SIZE, "offset": 0})
    return time.monotonic() - started


def probe(season: int | None = None) -> dict:
    """Check reachability and schema, reporting which stage fails."""
    from datetime import date

    season = season or (date.today().year - 1)
    report: dict = {"season": season, "stages": {}}

    try:
        payload = _get(f"{season}/results/", {"limit": PAGE_SIZE, "offset": 0})
    except Exception as exc:  # noqa: BLE001 -- the probe reports, it does not raise
        report["stages"]["fetch"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return report

    races = _races(payload)
    report["stages"]["fetch"] = {
        "ok": bool(races),
        "total_reported": _total(payload),
        "races_on_page": len(races),
        "sample": [r.get("raceName") for r in races[:3]],
    }
    if not races:
        report["stages"]["fetch"]["note"] = (
            "no races -- the season may not have run, or the response shape moved"
        )
        return report

    rows = _result_rows(races, season, "results")
    named = [r for r in rows if r["driver_name"]]
    scored = [r for r in rows if pd.notna(r["points"])]
    report["stages"]["parse"] = {
        "ok": bool(named) and bool(scored),
        "rows": len(rows),
        "named": f"{len(named)}/{len(rows)}",
        "with_points": f"{len(scored)}/{len(rows)}",
        "sample": rows[:3],
    }

    try:
        sprint = _races(_get(f"{season}/sprint/", {"limit": PAGE_SIZE, "offset": 0}))
        report["stages"]["sprint"] = {"ok": True, "races": len(sprint)}
    except Exception as exc:  # noqa: BLE001
        report["stages"]["sprint"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "note": "sprints are optional -- a season before 2021 has none",
        }
    return report
