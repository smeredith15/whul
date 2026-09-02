"""Tournament categories and draw sizes, scraped from the tours themselves.

The calendar has to be right and has to keep being right: events get renamed,
promoted from 250 to 500, and resized between seasons. Seeding it from a
checked-in list works for one year; scraping the schedule works for every year
after, which is why this exists.

Three sources, tried in order of authority:

    https://www.atptour.com/en/tournaments
    https://www.wtatennis.com/tournaments
    https://tennistonic.com/atp-wta-tournaments/     (both tours, one page)

The tours are authoritative but modern and JS-heavy; Tennistonic is a plain page
listing both tours and is the fallback when the official markup moves.

**These pages are read for their structure, not their instructions.** Only the
tournament name, category, draw size and dates are taken; nothing in the page
text directs what this module does.

Because the markup is not knowable in advance -- and cannot be inspected from
where this was written, since all three hosts are blocked by egress policy --
extraction runs several strategies and reports which one worked. Modern tour
sites usually ship the data as JSON inside the page (Next.js ``__NEXT_DATA__``,
a JSON-LD block, or a bare ``application/json`` script) long before they render
it as HTML, so the JSON strategies run first and the DOM walk is the backstop.

Run ``python -m whul.cli probe schedule --tour atp`` from a machine with
access. The probe prints the page's structure -- which JSON blobs are present,
which selectors match, what the candidate rows look like -- so a strategy that
does not fit can be corrected from its output.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from whul.scoring.tennis import (
    GRAND_SLAM, INTERNATIONAL, MASTERS_1000, TOUR_250, TOUR_500, TOUR_FINALS,
)

SOURCES = {
    "atp": "https://www.atptour.com/en/tournaments",
    "wta": "https://www.wtatennis.com/tournaments",
    "tennistonic": "https://tennistonic.com/atp-wta-tournaments/",
}
CACHE = Path("data/cache/tour_schedule")
TIMEOUT = 45

HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
}

#: Category as the pages word it. Order matters: "grand slam" must be tested
#: before "slam", and the team events before anything else, because "United Cup"
#: contains neither a level number nor a tour name.
CATEGORY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"davis\s*cup|billie\s*jean\s*king|bjk\s*cup|united\s*cup|"
                r"laver\s*cup|hopman", re.I), INTERNATIONAL),
    (re.compile(r"grand\s*slam|grandslam", re.I), GRAND_SLAM),
    (re.compile(r"\b(atp|wta|nitto)?\s*finals\b|tour\s*finals|"
                r"next\s*gen", re.I), TOUR_FINALS),
    (re.compile(r"masters\s*1000|atp\s*1000|wta\s*1000|\b1000\b|"
                r"premier\s*mandatory|premier\s*5", re.I), MASTERS_1000),
    (re.compile(r"atp\s*500|wta\s*500|\b500\b|premier\b", re.I), TOUR_500),
    (re.compile(r"atp\s*250|wta\s*250|\b250\b|international\b", re.I), TOUR_250),
)

#: A tour plays somewhere between 55 and 65 singles events a season. A scrape
#: returning far fewer has matched a fragment of the page -- a "this week"
#: strip, a featured carousel -- and must not be merged: the calendar would
#: gain a handful of rows and lose nothing, which reads as success while
#: leaving every other event on whatever it had.
MIN_PLAUSIBLE_EVENTS = 30

#: Names that are not tournaments. Governing bodies and tours appear in page
#: JSON next to real events and carry enough text to classify.
NON_TOURNAMENT_PATTERN = re.compile(
    r"^\s*(international tennis federation|association of tennis professionals|"
    r"women'?s tennis association|itf|atp tour|wta tour|atp|wta)\s*$",
    re.I,
)

#: Draw sizes a tour event actually uses. A number outside this set came from
#: somewhere other than the draw -- prize money, a year, a ranking -- and taking
#: it would move the event onto the wrong points table.
VALID_DRAW_SIZES = (28, 32, 48, 56, 64, 96, 128)

#: The word boundary on the second branch is load-bearing. Without it, a card
#: reading "ATP Masters 1000 Draw: 96" matches "000 Draw" first, which fails the
#: valid-size check *and* consumes the keyword, so the real "Draw: 96" is never
#: reached and the event ends up with no draw size at all.
_DRAW_PATTERN = re.compile(
    r"(?:draw|field|size)\D{0,12}(\d{2,3})\b|\b(\d{2,3})\s*(?:player\s*)?draw", re.I
)
_JSON_SCRIPT = re.compile(
    r'<script[^>]*(?:id="__NEXT_DATA__"|type="application/(?:ld\+)?json")[^>]*>(.*?)</script>',
    re.S | re.I,
)
#: Frameworks that are not Next.js hand their state over as a JS assignment
#: rather than a JSON script tag. The WTA site has no ``__NEXT_DATA__``, so
#: without this its payload is invisible to the JSON strategy.
_JS_STATE = re.compile(
    r"window\.(?:__NUXT__|__INITIAL_STATE__|__APOLLO_STATE__|__DATA__)\s*=\s*(\{.*?\});?\s*</script>",
    re.S,
)
#: Tournament detail links, which every schedule page has whatever it renders
#: them as. The slug is the most stable identifier on these pages.
_TOURNAMENT_HREF = re.compile(r"/tournaments?/", re.I)


def _soup(html: str):
    """Parse HTML, failing loudly when the parser is not installed.

    Returning an empty result instead would be indistinguishable from a page
    that simply has no tournaments in it -- which is exactly how a missing
    beautifulsoup4 read as "the DOM and link strategies found nothing", and
    sent the diagnosis after the page's markup rather than the environment.
    """
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "lxml")


def classify_category(text: str | None) -> str | None:
    """Category from a badge, label or event name; None when nothing matches."""
    if not text:
        return None
    for pattern, category in CATEGORY_PATTERNS:
        if pattern.search(str(text)):
            return category
    return None


def parse_draw_size(text: str | None) -> int | None:
    """Draw size from surrounding text, only when it is a real draw size."""
    if not text:
        return None
    for match in _DRAW_PATTERN.finditer(str(text)):
        raw = match.group(1) or match.group(2)
        if raw and int(raw) in VALID_DRAW_SIZES:
            return int(raw)
    return None


def _fetch(source: str, season: int | None = None, refresh: bool = False) -> str:
    """The page, cached so repeated extraction attempts cost one request."""
    key = f"{source}-{season or date.today().year}"
    cached = CACHE / f"{key}.html"
    if cached.exists() and not refresh:
        return cached.read_text()

    url = SOURCES[source]
    params = {"year": season} if season and source in ("atp", "wta") else None
    response = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(response.text)
    return response.text


# --- extraction strategies -------------------------------------------------
# Each takes the page and returns rows. The first to return anything wins, and
# the probe reports which one that was.

def _iter_json_blobs(html: str):
    """Every JSON blob embedded in the page that parses.

    Both shapes are read: a JSON script tag, and a framework state assignment.
    A blob that does not parse is skipped rather than aborting the sweep -- a
    page carries several and only one is usually the payload.
    """
    for pattern in (_JSON_SCRIPT, _JS_STATE):
        for match in pattern.finditer(html):
            try:
                yield json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                continue


def _walk(node, depth: int = 0):
    """Every dict in a nested structure, breadth unbounded but depth capped.

    Tour pages nest their payload a dozen levels down inside framework state,
    so the shape cannot be addressed directly -- but a tournament record is
    recognizable wherever it sits.
    """
    if depth > 12:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, depth + 1)


NAME_KEYS = ("name", "title", "tournamentName", "eventName", "displayName")
CATEGORY_KEYS = ("category", "level", "tournamentGroup", "eventType", "type",
                 "tourLevel", "classification", "badge")
DRAW_KEYS = ("drawSize", "draw_size", "singlesDrawSize", "drawSizeSingles", "draw")


def _first(record: dict, keys) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
        if isinstance(value, dict):
            for inner in ("name", "title", "value", "displayName"):
                if value.get(inner):
                    return str(value[inner])
    return None


def extract_from_json(html: str, tour: str, season: int) -> list[dict]:
    """Tournaments from JSON embedded in the page.

    A record counts as a tournament when it has a name and something that reads
    as a category. Requiring both keeps navigation entries and ad slots out.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for blob in _iter_json_blobs(html):
        for record in _walk(blob):
            name = _first(record, NAME_KEYS)
            if not name or len(name) > 80:
                continue
            category = classify_category(_first(record, CATEGORY_KEYS)) or classify_category(name)
            if not category:
                continue
            draw = _first(record, DRAW_KEYS)
            draw_size = int(draw) if draw and str(draw).isdigit() and int(draw) in VALID_DRAW_SIZES else None
            key = f"{name}|{category}"
            if key in seen or NON_TOURNAMENT_PATTERN.match(name):
                continue
            seen.add(key)
            rows.append({
                "season": season, "tour": tour.upper(), "tournament": name.strip(),
                "category": category, "draw_size": draw_size, "strategy": "json",
            })
    return rows


#: Containers a tournament row has plausibly been rendered into. Broad on
#: purpose: a selector that matches too much is filtered by the category test,
#: whereas one that matches nothing loses the page.
CARD_SELECTORS = (
    "li.tourney-result", "div.tournament-card", "article.tournament",
    "div.tournament-title", "[class*='tournament-item']", "[class*='TournamentCard']",
    "[class*='tournament'][class*='card']", "tr[class*='tourney']", "tbody tr",
)


def extract_from_dom(html: str, tour: str, season: int) -> list[dict]:
    """Tournaments from the rendered markup, when the JSON is absent."""
    soup = _soup(html)
    rows: list[dict] = []
    seen: set[str] = set()
    for selector in CARD_SELECTORS:
        for card in soup.select(selector):
            text = card.get_text(" ", strip=True)
            if not text or len(text) > 600:
                continue
            category = classify_category(text)
            if not category:
                continue
            link = card.select_one("a[href]")
            name = (link.get_text(strip=True) if link else "") or text[:60]
            key = f"{name}|{category}"
            if not name or key in seen or NON_TOURNAMENT_PATTERN.match(name):
                continue
            seen.add(key)
            rows.append({
                "season": season, "tour": tour.upper(), "tournament": name.strip(),
                "category": category, "draw_size": parse_draw_size(text),
                "strategy": f"dom:{selector}",
            })
        if rows:
            break
    return rows


def extract_from_links(html: str, tour: str, season: int) -> list[dict]:
    """Tournaments from their detail links.

    Every schedule page links each event to its own page, whatever markup it
    renders the row in. This is the least structured strategy and the most
    durable: it survives a redesign that moves every class name, because the
    links have to keep working.
    """
    soup = _soup(html)
    rows: list[dict] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        if not _TOURNAMENT_HREF.search(link["href"]):
            continue
        name = link.get_text(" ", strip=True)
        if not name or len(name) > 80 or NON_TOURNAMENT_PATTERN.match(name):
            continue
        # The category is rarely inside the link, so the row around it is read
        # too -- two levels up is usually the card.
        context = link
        for _ in range(3):
            context = context.parent or context
        text = context.get_text(" ", strip=True) if context else name
        category = classify_category(text) or classify_category(name)
        if not category:
            continue
        key = f"{name}|{category}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "season": season, "tour": tour.upper(), "tournament": name,
            "category": category, "draw_size": parse_draw_size(text),
            "strategy": "links",
        })
    return rows


STRATEGIES = (extract_from_json, extract_from_dom, extract_from_links)


def scrape(source: str, season: int | None = None, refresh: bool = False) -> pd.DataFrame:
    """Calendar rows from one source, by whichever strategy fits the page."""
    season = season or date.today().year
    html = _fetch(source, season, refresh)
    tour = "ATP" if source == "atp" else "WTA" if source == "wta" else ""
    # The first strategy to find anything is not necessarily the right one: on
    # the WTA page the JSON sweep finds six events out of sixty. So every
    # strategy runs and the fullest result wins.
    best: list[dict] = []
    for strategy in STRATEGIES:
        try:
            rows = strategy(html, tour, season)
        except Exception:  # noqa: BLE001 - one broken strategy must not lose the others
            continue
        if len(rows) > len(best):
            best = rows
    if best:
        return pd.DataFrame(best)
    return pd.DataFrame(columns=["season", "tour", "tournament", "category", "draw_size", "strategy"])


def build_calendar(season: int | None = None, refresh: bool = False) -> pd.DataFrame:
    """Both tours' schedules, ready to merge into the calendar.

    Tennistonic is only consulted for a tour whose own site yielded nothing --
    it carries both tours on one page, so it can stand in for either.
    """
    season = season or date.today().year
    frames = []
    for source in ("atp", "wta"):
        try:
            frame = scrape(source, season, refresh)
        except requests.RequestException as exc:
            print(f"{source}: unreachable ({exc})", flush=True)
            frame = pd.DataFrame()
        if not frame.empty:
            frames.append(frame)
        else:
            print(f"{source}: no tournaments extracted", flush=True)

    if len(frames) < 2:
        try:
            fallback = scrape("tennistonic", season, refresh)
            if not fallback.empty:
                have = {f["tour"].iloc[0] for f in frames if not f.empty}
                frames.append(fallback[~fallback["tour"].isin(have)])
        except requests.RequestException as exc:
            print(f"tennistonic: unreachable ({exc})", flush=True)

    if not frames:
        return pd.DataFrame(columns=["season", "tour", "tournament", "category", "draw_size"])
    return pd.concat(frames, ignore_index=True)


def completeness(scraped: pd.DataFrame) -> dict[str, int]:
    """Events found per tour -- the number the plausibility gate turns on."""
    if scraped is None or scraped.empty:
        return {}
    return scraped.groupby("tour").size().to_dict()


def is_plausible(scraped: pd.DataFrame, minimum: int = MIN_PLAUSIBLE_EVENTS) -> bool:
    """Whether a scrape found enough of a season to be worth merging."""
    counts = completeness(scraped)
    return bool(counts) and all(n >= minimum for n in counts.values())


def merge(
    existing: pd.DataFrame,
    scraped: pd.DataFrame,
    require_complete: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fold scraped rows into a calendar, reporting what changed.

    Returns the merged calendar and a frame of differences. Changes are not
    applied blind: an event moving between categories restates its points, so
    the diff is meant to be read before the result is saved.

    A scrape that found too few events for a tour is refused outright rather
    than merged. A partial scrape adds a few rows and removes none, so the
    result looks like a success while leaving every event it missed untouched
    -- and the missing ones are reported as "missing from scrape", which reads
    as though the tour dropped fifty tournaments. Pass ``require_complete=False``
    only to merge a deliberately partial list.
    """
    from whul.sources.tennis_calendar import COLUMNS, normalize_name

    if scraped is None or scraped.empty:
        return existing, pd.DataFrame(columns=["season", "tour", "tournament", "change"])

    if require_complete and not is_plausible(scraped):
        found = completeness(scraped)
        return existing, pd.DataFrame([{
            "season": None, "tour": tour, "tournament": "",
            "change": f"refused: only {n} events found, fewer than the "
                      f"{MIN_PLAUSIBLE_EVENTS} a real season has",
        } for tour, n in found.items()])

    new = scraped[list(COLUMNS)].copy()
    new["key"] = new["tournament"].map(normalize_name)
    old = existing.copy()
    if old.empty:
        return new.drop(columns=["key"]), new.assign(change="added")[
            ["season", "tour", "tournament", "change"]
        ]
    if "key" not in old.columns:
        old["key"] = old["tournament"].map(normalize_name)

    merged = old.merge(
        new, on=["season", "tour", "key"], how="outer",
        suffixes=("_old", "_new"), indicator=True,
    )
    changes = []
    for _, row in merged.iterrows():
        if row["_merge"] == "right_only":
            changes.append({"season": row["season"], "tour": row["tour"],
                            "tournament": row["tournament_new"], "change": "added"})
        elif row["_merge"] == "left_only":
            changes.append({"season": row["season"], "tour": row["tour"],
                            "tournament": row["tournament_old"], "change": "missing from scrape"})
        elif row["category_old"] != row["category_new"]:
            changes.append({
                "season": row["season"], "tour": row["tour"],
                "tournament": row["tournament_old"],
                "change": f"category {row['category_old']} -> {row['category_new']}",
            })

    combined = merged.assign(
        tournament=merged["tournament_new"].fillna(merged["tournament_old"]),
        category=merged["category_new"].fillna(merged["category_old"]),
        # A scraped draw of NaN means the page did not state one, not that the
        # event has none -- so the known value is kept.
        draw_size=merged["draw_size_new"].fillna(merged["draw_size_old"]),
    )[list(COLUMNS)]
    return combined.reset_index(drop=True), pd.DataFrame(changes)


def probe(source: str = "atp", season: int | None = None) -> dict:
    """Fetch one page and report what can be pulled out of it.

    The structure summary is the point: it says which JSON blobs are present,
    which selectors matched and what the candidate rows look like, so a
    strategy that does not fit this markup can be corrected without a second
    round trip.
    """
    season = season or date.today().year
    report: dict = {"source": source, "season": season, "url": SOURCES[source], "stages": {}}

    try:
        html = _fetch(source, season, refresh=True)
    except Exception as exc:  # noqa: BLE001 -- the probe reports, it does not raise
        report["stages"]["fetch"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "note": "403 or a challenge page usually means the site wants a "
                    "real browser; try the tennistonic source instead",
        }
        return report

    blobs = list(_iter_json_blobs(html))
    report["stages"]["fetch"] = {
        "ok": len(html) > 5000,
        "bytes": len(html),
        "json_blobs": len(blobs),
        "has_next_data": "__NEXT_DATA__" in html,
        "title": (re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [None, ""])[1].strip()[:120],
    }

    tour = "ATP" if source == "atp" else "WTA" if source == "wta" else ""
    per_strategy = {}
    for strategy in STRATEGIES:
        try:
            rows = strategy(html, tour, season)
        except Exception as exc:  # noqa: BLE001
            per_strategy[strategy.__name__] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        per_strategy[strategy.__name__] = {
            "rows": len(rows),
            "with_draw_size": sum(1 for r in rows if r["draw_size"]),
            "categories": sorted({r["category"] for r in rows}),
            "sample": rows[:5],
        }
    report["stages"]["extract"] = {
        "ok": any(s.get("rows") for s in per_strategy.values()),
        **per_strategy,
    }

    best = max((s.get("rows", 0) for s in per_strategy.values()), default=0)
    if best < MIN_PLAUSIBLE_EVENTS:
        # Too little matched, so hand back the raw structure to fix the
        # selectors from -- the only thing that makes a blind fix possible.
        # This runs for a short scrape as well as an empty one: six events out
        # of sixty needs the same diagnosis as zero.
        try:
            soup = _soup(html)
            classes: dict[str, int] = {}
            for element in soup.find_all(class_=True):
                for name in element.get("class", []):
                    if "tourn" in name.lower() or "event" in name.lower() or "card" in name.lower():
                        classes[name] = classes.get(name, 0) + 1
            report["stages"]["extract"]["candidate_classes"] = sorted(
                classes.items(), key=lambda kv: -kv[1]
            )[:20]
            report["stages"]["extract"]["script_ids"] = [
                tag.get("id") or tag.get("type") for tag in soup.find_all("script")
                if tag.get("id") or tag.get("type")
            ][:20]
        except ImportError as exc:
            report["stages"]["extract"]["parser_missing"] = (
                f"{exc} -- run `pip install -e .` to pick up beautifulsoup4 "
                f"and lxml; without them only the JSON strategy runs"
            )
        report["stages"]["extract"]["note"] = (
            f"best strategy found {best} events, fewer than the "
            f"{MIN_PLAUSIBLE_EVENTS} a real season has -- the class names and "
            f"script ids above say what this page actually uses"
        )
        report["stages"]["extract"]["ok"] = False
        if best == 0:
            return report

    scraped = scrape(source, season)
    from whul.sources import tennis_calendar

    complete = is_plausible(scraped)
    merged, changes = merge(tennis_calendar.load(), scraped)
    report["stages"]["merge"] = {
        # A partial scrape is a failed scrape, not a small success.
        "ok": complete,
        "scraped": len(scraped),
        "per_tour": completeness(scraped),
        "calendar_after": len(merged),
        "changes": changes.head(15).to_dict("records"),
        "problems": tennis_calendar.validate(merged),
        "note": "" if complete else (
            f"refused: a tour season has 55-65 events, so this matched a "
            f"fragment of the page. The extract stage's sample says which "
            f"fragment; the calendar was left alone."
        ),
    }
    return report
