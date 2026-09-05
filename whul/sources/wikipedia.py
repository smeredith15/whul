"""Reading UEFA entry off Wikipedia.

Which clubs entered the Champions League, the Europa League and the Conference
League -- and at which round -- is the one thing in club soccer that cannot be
derived from match results. It needs final league positions, both domestic cup
winners, the cascade when a cup winner has already qualified by position, and
the per-season coefficient allocation deciding how many places each league
gets. Four rules, each of which could be modelled and got wrong, and the last
one moves every year so it goes stale in silence.

The participant list is the outcome rather than the working. Reading it makes
all four disappear: whoever won the extra place is simply in the list.

**Only entry, never results.** Where a club got to once it was in is the daily
scraper's business, and it already does that. This answers one question -- who
entered what, and at which round -- because entry is worth points and cannot be
scraped from fixtures.

The article's "Teams" section holds a single table shaped like this::

    Entry round   Entry round.1   Teams    Teams.1   Teams.2   Teams.3
    League phase                  Club A   Club B    Club C    Club D

Clubs run four across, which is the detail worth stating: reading the first
column only returns roughly a quarter of them and looks like a full answer.

Nothing else in the article is read. The "Association team allocation" section
has its own ``Teams`` column holding a *count*, and the qualifying and league
phase sections hold results -- where a club knocked out of one competition
appears again in another, which is correct football and a wrong answer here.
"""

from __future__ import annotations

import re
import time
from io import StringIO

import pandas as pd
import requests

API = "https://en.wikipedia.org/w/api.php"

#: Wikipedia asks that a client identify itself and make requests in series.
HEADERS = {
    "User-Agent": "whul-fantasy/0.1 (fantasy league scoring; "
                  "https://github.com/smeredith15/whul)"
}
PAUSE = 1.0
TIMEOUT = 30

#: The article titles use an en-dash, not a hyphen.
DASH = "–"

COMPETITION_TITLES = {
    "Champions League": "{first}{dash}{second} UEFA Champions League",
    "Europa League": "{first}{dash}{second} UEFA Europa League",
    "Conference League": "{first}{dash}{second} UEFA Conference League",
}

#: The one section worth reading. Anchored, so it cannot also match
#: "Qualified teams" elsewhere in a long article.
TEAMS_SECTION = re.compile(r"^teams$", re.IGNORECASE)

#: Club columns are ``Teams``, ``Teams.1``, ``Teams.2``, ``Teams.3`` -- pandas
#: numbers repeated headers. Anchored so ``Teams entering in this round``, a
#: summary column elsewhere in the article, cannot match.
CLUB_COLUMN = re.compile(r"^teams(\.\d+)?$", re.IGNORECASE)
ENTRY_COLUMN = re.compile(r"^entry round(\.\d+)?$", re.IGNORECASE)

#: Where a club comes into the competition. This *is* the scoring distinction:
#: a place in the league phase and a place in a qualifying draw are not worth
#: the same. Anything outside this vocabulary means the table changed shape and
#: is reported rather than guessed at.
ENTRY_ROUNDS = (
    "League phase",
    "Play-off round",
    "Third qualifying round",
    "Second qualifying round",
    "First qualifying round",
    "Preliminary round",
)
DIRECT_ENTRY = "League phase"

#: A league phase holds this many clubs, of which some arrive through
#: qualifying -- so the direct entrants are fewer, never more.
LEAGUE_PHASE_SIZE = 36

#: Superscript markers Wikipedia appends to a club in these tables. Stripped by
#: exact match only: a trailing run of capitals is not safely removable when AZ,
#: PSV, RFS and FCSB are clubs.
#:
#: EPS is the European Performance Spot -- the extra Champions League place the
#: two best-performing associations earn each year. It is exactly the moving
#: allocation that reading the participant list was meant to avoid modelling,
#: and it turned up in the first live run: Newcastle United and Villarreal took
#: those places in 2025-26. Without it here, both names reach the roster with
#: " EPS" attached, match nothing, and score no entry at all -- twelve points
#: each, in silence.
MARKERS = ("TH", "CW", "EPS", "UCL", "UEL", "UECL")

#: Text in a club cell that names a slot rather than a club, which is what an
#: article carries before qualifying has been played.
PLACEHOLDER = re.compile(
    r"winner|loser|runner|qualif|play-?off|tbd|to be determined|\bvs\.?\b|"
    r"^\d+\s|champions from|teams? from|associations",
    re.IGNORECASE,
)

NOT_A_CLUB = {"", "nan", "team", "teams", "club", "association", "country"}


def _is_a_count(name: str) -> bool:
    """A cell holding only a number is a count, not a club.

    The allocation table has its own ``Teams`` column saying how many places
    each association gets, and reading it produced clubs called "4", "3", "2"
    and "0". Only the section headed exactly "Teams" is read, which should stop
    that table reaching here at all -- this is the second line, because the
    first one is a heading that Wikipedia could rename.

    Careful about what a club is allowed to look like: "1. FC Köln" and "TSG
    1899 Hoffenheim" both carry digits, so the test is that there is nothing
    else in the cell.
    """
    return name.replace(".", "").replace(",", "").replace(" ", "").isdigit()


def title_for(competition: str, season: str) -> str:
    """``("Champions League", "2027-28")`` -> the article title."""
    first, second = season.replace(DASH, "-").split("-")
    return COMPETITION_TITLES[competition].format(
        first=int(first), second=f"{int(second):02d}", dash=DASH
    )


def clean(value) -> str:
    """A club name without Wikipedia's footnotes, parentheticals or markers.

    ``Paris Saint-Germain TH`` is Paris Saint-Germain; ``Manchester City
    (holders)[a]`` is Manchester City. Left as they come, neither matches
    anything on a roster, and the club silently scores no entry at all.
    """
    text = re.sub(r"\[[^\]]*\]", "", str(value))
    text = re.sub(r"\(.*?\)", "", text)
    text = " ".join(text.split()).strip()
    for marker in MARKERS:
        if text.endswith(f" {marker}"):
            text = text[: -len(marker)].strip()
    return text


def is_placeholder(name: str) -> bool:
    return bool(PLACEHOLDER.search(name))


def entrants_from(frame: pd.DataFrame) -> dict[str, str]:
    """``{club: entry round}`` from the Teams table.

    Every column named ``Teams`` is read, not only the first. They run four
    across, so reading one returns a quarter of the field and looks complete.
    """
    if frame is None or frame.empty:
        return {}
    columns = [str(c) for c in frame.columns]
    club_cols = [c for c in columns if CLUB_COLUMN.match(c)]
    entry_cols = [c for c in columns if ENTRY_COLUMN.match(c)]
    if not club_cols:
        return {}

    out: dict[str, str] = {}
    for _, row in frame.iterrows():
        entry = ""
        for column in entry_cols:
            value = clean(row.get(column, ""))
            if value and value.lower() not in NOT_A_CLUB:
                entry = value
                break
        for column in club_cols:
            name = clean(row.get(column, ""))
            if (not name or name.lower() in NOT_A_CLUB or _is_a_count(name)
                    or is_placeholder(name)):
                continue
            out.setdefault(name, entry)
    return out


def _api(params: dict, session=None) -> dict:
    getter = session.get if session is not None else requests.get
    response = getter(
        API, params={**params, "format": "json", "formatversion": 2},
        headers=HEADERS, timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise LookupError(payload["error"].get("info", "unknown API error"))
    time.sleep(PAUSE)
    return payload


def sections(title: str, session=None) -> list[dict]:
    """Every section of an article: ``index`` and ``line`` (its heading)."""
    payload = _api({"action": "parse", "page": title, "prop": "sections"}, session)
    return payload.get("parse", {}).get("sections", [])


def teams_section(found: list[dict]) -> dict | None:
    """The one section that names the participants, if the article has it."""
    for section in found:
        if TEAMS_SECTION.match(str(section.get("line", "")).strip()):
            return section
    return None


def section_tables(title: str, index: str, session=None) -> list[pd.DataFrame]:
    """Every table in one section of an article."""
    payload = _api(
        {"action": "parse", "page": title, "prop": "text", "section": index},
        session,
    )
    html = payload.get("parse", {}).get("text", "") or ""
    try:
        # A bare string is read as a *filename* by pandas 3, so this must be
        # wrapped: without it every table raises FileNotFoundError and the
        # article reads as unparseable, with the network the obvious suspect.
        return pd.read_html(StringIO(html))
    except ValueError:
        return []


def load_entrants(competition: str, season: str, session=None) -> dict[str, str]:
    """``{club: entry round}`` for one competition and season."""
    title = title_for(competition, season)
    section = teams_section(sections(title, session))
    if section is None:
        raise LookupError(
            f"{title} has no section headed 'Teams'. The article changed shape, "
            f"and guessing which other section holds the participants is how a "
            f"coefficient table gets read as a club list."
        )
    entrants: dict[str, str] = {}
    for frame in section_tables(title, str(section.get("index")), session):
        entrants.update(entrants_from(frame))
    return entrants


def check(entrants: dict[str, dict[str, str]]) -> list[str]:
    """What is wrong with a set of participant lists, if anything.

    Deliberately not a disjointness test. A club knocked out of the Champions
    League qualifying rounds transfers into the Europa League and belongs in
    both articles, which is correct football and would fail such a check every
    season.

    What must hold is that every entry round is one this understands -- an
    unrecognised one means the table changed shape -- and that no competition
    admits more clubs directly to its league phase than the league phase holds.
    """
    problems = []
    for competition, clubs in entrants.items():
        if not clubs:
            problems.append(f"{competition}: no clubs read at all")
            continue
        unknown = sorted({r for r in clubs.values() if r and r not in ENTRY_ROUNDS})
        if unknown:
            problems.append(
                f"{competition}: entry round(s) this does not recognise: "
                f"{', '.join(unknown)}. The table changed shape."
            )
        blank = [c for c, r in clubs.items() if not r]
        if blank:
            problems.append(
                f"{competition}: {len(blank)} club(s) with no entry round, "
                f"e.g. {blank[0]}. Entry round is the scoring distinction, so a "
                f"club without one cannot be scored."
            )
        direct = [c for c, r in clubs.items() if r == DIRECT_ENTRY]
        if len(direct) > LEAGUE_PHASE_SIZE:
            problems.append(
                f"{competition}: {len(direct)} clubs entering directly at the "
                f"league phase, which holds {LEAGUE_PHASE_SIZE}."
            )
    return problems
