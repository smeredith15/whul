"""Flashscore internal feed -- live tennis results.

Ported from the scraper in ``smeredith15/tennis2026``, which is already running
against this feed. It serves a rolling window of days:

    https://global.flashscore.ninja/2/x/feed/f_2_{day}_-4_en_1

The payload is not JSON. Fields are ``KEY÷value`` pairs terminated by ``¬``,
records separated by ``~``; a tournament header opens with ``ZA÷`` and every
match record after it (``AA÷``) belongs to that tournament until the next
header. Position matters, so the parser tracks the current header as it walks.

Because the window is only a fortnight wide, this is a source for the season in
progress, not for history: run it nightly and it accumulates. Benchmarks come
from ``whul.sources.sackmann`` instead.

UNVERIFIED here: written where Flashscore is blocked by egress policy. Run
``python -m whul.cli probe tennis`` from a machine with access.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Iterator

import pandas as pd
import requests

from whul.scoring.tennis import (
    GRAND_SLAM, INTERNATIONAL, MASTERS_1000, TOUR_250, TOUR_500, TOUR_FINALS,
)

API_URL = "https://global.flashscore.ninja/2/x/feed/f_2_{day}_-4_en_1"
#: The feed's own window. Fetching wider gains nothing -- days outside it come
#: back empty.
DAY_RANGE = range(-7, 8)
CACHE = Path("data/cache/flashscore")
REQUEST_PAUSE = 0.4
TIMEOUT = 30

HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://www.flashscore.com",
    "referer": "https://www.flashscore.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "x-fsign": "SW9D1eZo",
    "x-geoip": "1",
}

# Status codes in the AC field.
STATUS_UPCOMING = {"1", "18"}
STATUS_COMPLETED = "3"
STATUS_RETIRED = "8"
STATUS_WALKOVER = "9"
STATUS_WITHDRAWAL = "5"

#: Set scores arrive as five pairs of fields, one per set.
SET_FIELDS = (("BA", "BB"), ("BC", "BD"), ("BE", "BF"), ("BG", "BH"), ("BI", "BJ"))

#: Events that are not main-tour singles. Men's Challengers are dropped; the
#: word also appears in some women's event names, which is why the test is not
#: a bare substring match.
DOUBLES_PATTERN = re.compile(r"doubles", re.IGNORECASE)
EXCLUDED_PATTERN = re.compile(r"exhibition|itf men", re.IGNORECASE)
ATP_PATTERN = re.compile(r"atp\s*-\s*singles", re.IGNORECASE)
WTA_PATTERN = re.compile(r"wta\s*-\s*singles", re.IGNORECASE)

GRAND_SLAM_NAMES = ("Australian Open", "French Open", "Wimbledon", "US Open")

#: Flashscore's round labels, several spellings each. Qualifying rounds are
#: mapped rather than ignored so they can be recognized and dropped -- an
#: unmapped qualifying label would otherwise be indistinguishable from a round
#: the parser simply failed to read.
ROUND_MAP: dict[str, str] = {
    "rr": "RR", "round robin": "RR", "group": "RR",
    "r128": "R128", "1r": "R128", "1st round": "R128",
    "round of 128": "R128", "1/64-finals": "R128", "1/64 finals": "R128",
    "r64": "R64", "2r": "R64", "2nd round": "R64",
    "round of 64": "R64", "1/32-finals": "R64", "1/32 finals": "R64",
    "r32": "R32", "3r": "R32", "3rd round": "R32",
    "round of 32": "R32", "1/16-finals": "R32", "1/16 finals": "R32",
    "r16": "R16", "4r": "R16", "4th round": "R16",
    "round of 16": "R16", "1/8-finals": "R16", "1/8 finals": "R16",
    "qf": "QF", "quarterfinal": "QF", "quarterfinals": "QF",
    "quarter-final": "QF", "quarter-finals": "QF",
    "1/4-finals": "QF", "1/4 finals": "QF",
    "sf": "SF", "semifinal": "SF", "semifinals": "SF",
    "semi-final": "SF", "semi-finals": "SF", "1/2-finals": "SF", "1/2 finals": "SF",
    "f": "F", "final": "F", "finals": "F", "w": "F", "winner": "F",
    "q1": "Q1", "qualification 1": "Q1", "qualifying 1st round": "Q1",
    "qualification round 1": "Q1", "1st qualifying round": "Q1",
    "q2": "Q2", "qualification 2": "Q2", "qualifying 2nd round": "Q2",
    "qualification round 2": "Q2", "2nd qualifying round": "Q2",
    "q3": "Q3", "qualification 3": "Q3", "qualifying 3rd round": "Q3",
    "qualification round 3": "Q3", "3rd qualifying round": "Q3",
    "final qualifying round": "Q3",
    "qualification": "Q1",
}


def _field(segment: str, code: str) -> str | None:
    match = re.search(rf"{re.escape(code)}÷([^¬]*)", segment)
    return match.group(1).strip() if match else None


def slug_to_name(slug: str | None) -> str | None:
    """'sinner-jannik' -> 'Jannik Sinner'.

    Flashscore slugs put the surname first and may carry several name parts,
    so everything but the last token is the surname.
    """
    if not slug:
        return None
    parts = [p for p in slug.split("-") if p]
    if len(parts) < 2:
        return slug.title() if slug else None
    return f"{parts[-1].title()} {' '.join(p.title() for p in parts[:-1])}"


def parse_score(segment: str) -> str | None:
    """Set scores as 'X-Y X-Y', in the order the feed lists them."""
    sets = []
    for home_field, away_field in SET_FIELDS:
        home = _field(segment, home_field)
        if home is None:
            continue
        away = _field(segment, away_field)
        sets.append(f"{home}-{away or '?'}")
    return " ".join(sets) if sets else None


def parse_tournament_header(segment: str) -> dict | None:
    """One ZA header, or None for an event that is not main-tour singles."""
    raw = _field(segment, "ZA")
    if not raw:
        return None
    lower = raw.lower()

    if DOUBLES_PATTERN.search(raw) or EXCLUDED_PATTERN.search(raw):
        return None
    if "challenger" in lower and "women" not in lower and "wta" not in lower:
        return None

    if ATP_PATTERN.search(raw):
        tour = "ATP"
    elif WTA_PATTERN.search(raw):
        tour = "WTA"
    else:
        return None

    if any(name in raw for name in GRAND_SLAM_NAMES):
        category = GRAND_SLAM
    elif "masters" in lower or "1000" in lower:
        category = MASTERS_1000
    elif "500" in raw:
        category = TOUR_500
    elif "finals" in lower:
        category = TOUR_FINALS
    elif re.search(r"davis cup|billie jean king|bjk cup|united cup", lower):
        category = INTERNATIONAL
    else:
        category = TOUR_250

    name = re.sub(r"(ATP|WTA)\s*-\s*SINGLES:\s*", "", raw, flags=re.IGNORECASE)
    name = re.sub(r"\s*-\s*Qualification", "", name, flags=re.IGNORECASE)
    name = re.split(r"[,(]", name)[0].strip()

    # The daily feed carries the round in the header's trailing segment
    # ("... Rome - Quarterfinal") rather than per match.
    suffix = re.split(r"[-–]\s*", raw)[-1].strip().lower()

    return {
        "tour": tour,
        "category": category,
        "tournament": name,
        "round": ROUND_MAP.get(suffix, ""),
        "is_qualifying": "qualification" in lower,
    }


def iter_matches(raw: str) -> Iterator[dict]:
    """One dict per match in a raw feed payload.

    Header position is what assigns a match to a tournament, so records are
    walked in order and the most recent header applies.
    """
    header: dict | None = None
    for segment in (s for s in raw.split("~") if s):
        if segment.startswith("ZA÷"):
            header = parse_tournament_header(segment)
            continue
        if header is None or not segment.startswith("AA÷"):
            continue

        uid = _field(segment, "AA")
        home = slug_to_name(_field(segment, "WU"))
        away = slug_to_name(_field(segment, "WV"))
        if not uid or not home or not away:
            continue

        status = _field(segment, "AC") or ""
        if status in STATUS_UPCOMING:
            continue  # not yet a result

        timestamp = _field(segment, "AD")
        try:
            when = date.fromtimestamp(int(timestamp))
        except (TypeError, ValueError, OSError):
            when = None

        winner_code = _field(segment, "AS")
        walkover = status in (STATUS_WALKOVER, STATUS_WITHDRAWAL)
        winner = home if winner_code == "1" else away if winner_code == "2" else None
        if winner is None:
            continue  # result unknown; scoring it would invent a win

        score = parse_score(segment)
        if walkover:
            score = "W/O"
        elif status == STATUS_RETIRED and score:
            score = f"{score} RET"

        yield {
            "match_uid": uid,
            "date": when.isoformat() if when else "",
            # A tennis season is a calendar year, so the match date names it.
            "season": when.year if when else None,
            "tournament": header["tournament"],
            "tour": header["tour"],
            "category": header["category"],
            "round": header["round"],
            "is_qualifying": header["is_qualifying"],
            "winner": winner,
            "loser": away if winner == home else home,
            "score": score or "",
        }


def _get(day: int, cache_key: str | None = None) -> str:
    if cache_key:
        cached = CACHE / f"{cache_key}.txt"
        if cached.exists():
            return cached.read_text()

    response = requests.get(API_URL.format(day=day), headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.text

    if cache_key:
        cached = CACHE / f"{cache_key}.txt"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(payload)

    time.sleep(REQUEST_PAUSE)
    return payload


def fetch_window(days: range = DAY_RANGE, cache: bool = False) -> str:
    """The whole rolling window as one payload.

    Days are not cached by default: today's feed changes through the day, and a
    stale copy would hide results that landed since.
    """
    chunks = []
    for day in days:
        key = f"{date.today().isoformat()}/day{day}" if cache else None
        try:
            chunks.append(_get(day, key))
        except requests.RequestException:
            # One bad day should not lose the other thirteen.
            continue
    return "~".join(chunks)


def load_matches(
    days: range = DAY_RANGE, verbose: bool = True, use_calendar: bool = True
) -> pd.DataFrame:
    """Completed main-draw matches from the rolling window.

    The category this feed reports is a fallback, not an answer. Its header
    names the event and little else -- "ATP - SINGLES: Rome" says nothing about
    Rome being a 1000 -- so the category is resolved through the tournament
    calendar, which is the authority, and the header's guess is used only where
    the calendar has no entry. ``tennis_calendar.unresolved`` names those.

    Draw size is not in the feed at all. The calendar supplies it;
    ``infer_draw_sizes`` can estimate it from the field where an event is
    missing from the calendar and the whole of it is in the window.
    """
    raw = fetch_window(days)
    rows = [m for m in iter_matches(raw) if not m["is_qualifying"]]
    frame = pd.DataFrame(rows)
    if verbose:
        print(f"flashscore: {len(rows)} completed main-draw matches in window", flush=True)
    if frame.empty or not use_calendar:
        return frame

    from whul.sources import tennis_calendar

    resolved = tennis_calendar.resolve(frame)
    if verbose:
        gaps = tennis_calendar.unresolved(frame)
        if not gaps.empty:
            print(
                f"not in the calendar, scored on the feed's guess: "
                f"{', '.join(gaps['tournament'].head(10))}",
                flush=True,
            )
    return resolved


def infer_draw_sizes(matches: pd.DataFrame) -> pd.DataFrame:
    """Draw size per tournament, from the distinct players seen in it.

    The feed never states the draw, but every player in it appears at least
    once, so counting winners and losers recovers the field -- provided the
    whole event is in the window. A partially-observed tournament will
    under-count, which is why the result is advisory: check it against the
    bracket sizes before trusting a tier that turns on it.
    """
    if matches is None or matches.empty:
        return pd.DataFrame(columns=["season", "tournament", "draw_size"])
    people = pd.concat(
        [
            matches[["season", "tournament", "winner"]].rename(columns={"winner": "player"}),
            matches[["season", "tournament", "loser"]].rename(columns={"loser": "player"}),
        ],
        ignore_index=True,
    )
    return people.groupby(["season", "tournament"], as_index=False)["player"].nunique().rename(
        columns={"player": "draw_size"}
    )


def daily_update_cost() -> float:
    """Seconds for one nightly update -- the whole window, which is the job."""
    started = time.monotonic()
    fetch_window()
    return time.monotonic() - started


def probe(days: range | None = None) -> dict:
    """Check the feed is reachable and still parses, stage by stage."""
    days = days or range(-2, 1)
    report: dict = {"days": f"{days.start}..{days.stop - 1}", "stages": {}}

    try:
        raw = _get(days.start)
    except Exception as exc:  # noqa: BLE001 -- the probe reports, it does not raise
        report["stages"]["fetch"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return report

    headers = [s for s in raw.split("~") if s.startswith("ZA÷")]
    records = [s for s in raw.split("~") if s.startswith("AA÷")]
    report["stages"]["fetch"] = {
        "ok": bool(headers and records),
        "bytes": len(raw),
        "tournament_headers": len(headers),
        "match_records": len(records),
    }
    if not headers or not records:
        report["stages"]["fetch"]["note"] = (
            "no ZA/AA records -- the feed's encoding or the x-fsign header has "
            "changed; the first 300 bytes are: " + raw[:300]
        )
        return report

    parsed = [parse_tournament_header(h) for h in headers]
    kept = [p for p in parsed if p]
    report["stages"]["headers"] = {
        "ok": bool(kept),
        "kept": f"{len(kept)}/{len(headers)}",
        "categories": sorted({p["category"] for p in kept}),
        "rounds": sorted({p["round"] for p in kept if p["round"]}),
        "sample": [
            {"tournament": p["tournament"], "tour": p["tour"],
             "category": p["category"], "round": p["round"]}
            for p in kept[:5]
        ],
    }
    if not kept:
        report["stages"]["headers"]["note"] = (
            "every header was filtered out -- the ATP/WTA singles pattern no "
            "longer matches the feed's wording"
        )
        return report

    matches = list(iter_matches(raw))
    report["stages"]["matches"] = {
        "ok": bool(matches),
        "completed": len(matches),
        "with_round": sum(1 for m in matches if m["round"]),
        "with_score": sum(1 for m in matches if m["score"]),
        "sample": matches[:3],
    }
    if not matches:
        report["stages"]["matches"]["note"] = (
            "headers parsed but no completed matches -- a quiet day, or the "
            "status/winner fields moved"
        )
        return report

    from whul.scoring.tennis import score_players
    from whul.sources import tennis_calendar

    frame = pd.DataFrame([m for m in matches if not m["is_qualifying"]])
    resolved = tennis_calendar.resolve(frame)
    gaps = tennis_calendar.unresolved(frame)
    report["stages"]["calendar"] = {
        # A gap is not a failure -- the feed's guess still scores -- but every
        # gap is an event being paid on a category nobody checked.
        "ok": True,
        "entries": len(tennis_calendar.load()),
        "resolved": int((resolved.get("category_source", pd.Series(dtype=str)) != "feed").sum()),
        "unresolved_tournaments": gaps["tournament"].tolist()[:10],
        "note": "" if gaps.empty else (
            "these events are scored on the feed's category guess -- add them "
            "to data/tennis/calendar.csv"
        ),
    }
    totals = score_players(resolved)
    report["stages"]["score"] = {
        "ok": not totals.empty,
        "players": len(totals),
        "top": totals.head(5)[["player", "league", "matches_won", "total_points"]].to_dict("records")
        if not totals.empty
        else [],
        "note": "" if not totals.empty else (
            "matches parsed but nothing scored -- most likely every round came "
            "through empty; check the 'rounds' list above"
        ),
    }
    return report
