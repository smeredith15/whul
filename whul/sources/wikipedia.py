"""Reading UEFA qualification off Wikipedia.

Which clubs qualified for the Champions League, the Europa League and the
Conference League is the one thing in club soccer that cannot be derived from
match results. It depends on final league positions, on who won each domestic
cup, and on a cascade when a cup winner has already qualified by position --
and on top of that, how many places each league gets moves every year with the
UEFA coefficients.

Every one of those is a rule that could be modelled and got wrong. The
participant list is the *outcome*, published as a table, and reading it
sidesteps all of them: whoever won the extra place is simply in the list.

**This is not a nightly feed.** Qualification is settled once, when the domestic
seasons end in May, and the scoring reads a dated record rather than a live
page -- so a later edit to Wikipedia can never move a score that was already
published. See ``admin_overrides`` in the schema, which exists for exactly this.

The API is used rather than the rendered page: ``action=parse`` is documented
and stable, ``prop=sections`` finds the right part of a long article by name,
and fetching one section at a time avoids guessing which of twenty tables on
the page is the participants.
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

#: Sections that might name the participants. Every match is read, not just the
#: first: the league phase and the qualifying rounds are in different sections
#: and the scoring needs both, because direct entry and a place in a qualifying
#: draw are not worth the same.
SECTION_PATTERNS = (
    re.compile(r"^teams$", re.IGNORECASE),
    re.compile(r"qualified teams", re.IGNORECASE),
    re.compile(r"^association team allocation$", re.IGNORECASE),
    re.compile(r"^league phase$", re.IGNORECASE),
    re.compile(r"qualifying round", re.IGNORECASE),
)

#: How many clubs a league phase holds since the 2024-25 reform. A parse that
#: returns some other number found the wrong table, and saying so is better than
#: scoring thirty-four clubs.
LEAGUE_PHASE_SIZE = 36

TEAM_HEADERS = re.compile(r"team|club", re.IGNORECASE)
METHOD_HEADERS = re.compile(r"qualif|method|via|entry", re.IGNORECASE)

#: A row naming a slot rather than a club, which is what an article carries
#: before qualifying has been played. Matching one to a club would invent a
#: qualification out of a placeholder.
PLACEHOLDER = re.compile(
    r"winner|loser|runner|qualif|play-?off|tbd|to be determined|\bvs\.?\b",
    re.IGNORECASE,
)

#: Text that is a column heading repeated in the body, not a club.
NOT_A_CLUB = {"", "nan", "team", "club", "association", "country"}


def title_for(competition: str, season: str) -> str:
    """``("Champions League", "2027-28")`` -> the article title."""
    first, second = season.replace(DASH, "-").split("-")
    return COMPETITION_TITLES[competition].format(
        first=int(first), second=f"{int(second):02d}", dash=DASH
    )


def clean(value) -> str:
    """A club name without Wikipedia's footnote markers or parentheticals.

    ``Manchester City (holders)[a]`` is Manchester City. Left as it comes, the
    name would match nothing on the roster and the club would silently score no
    qualification at all.
    """
    text = re.sub(r"\[[^\]]*\]", "", str(value))
    text = re.sub(r"\(.*?\)", "", text)
    return " ".join(text.split()).strip()


def is_placeholder(name: str) -> bool:
    return bool(PLACEHOLDER.search(name))


def clubs_from(frame: pd.DataFrame) -> list[tuple[str, str]]:
    """``(club, how it qualified)`` from one participants table.

    The method column is a bonus rather than a requirement: it is what lets a
    reader check that a club listed under the Champions League really did
    finish where the table says. Tables without one still yield their clubs.
    """
    if frame is None or frame.empty:
        return []
    columns = [str(c) for c in frame.columns]
    team_col = next((c for c in columns if TEAM_HEADERS.search(c)), None)
    method_col = next((c for c in columns if METHOD_HEADERS.search(c)), None)
    if team_col is None:
        team_col = columns[0] if columns else None
    if team_col is None:
        return []

    out: list[tuple[str, str]] = []
    for _, row in frame.iterrows():
        name = clean(row.get(team_col, ""))
        if name.lower() in NOT_A_CLUB:
            continue
        how = clean(row.get(method_col, "")) if method_col else ""
        out.append((name, how))
    return out


def _api(params: dict, session: requests.Session | None = None) -> dict:
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
        # article reads as unparseable.
        return pd.read_html(StringIO(html))
    except ValueError:
        return []


def matching_sections(found: list[dict]) -> list[dict]:
    """The sections whose heading suggests they name the participants."""
    return [
        section for section in found
        if any(p.search(str(section.get("line", ""))) for p in SECTION_PATTERNS)
    ]


def check(entrants: dict[str, dict[str, str]]) -> list[str]:
    """What is wrong with a set of three participant lists, if anything.

    Two checks, both cheap and both strong. A league phase holds exactly
    thirty-six clubs, so any other number means the wrong table was read. And a
    club plays in one competition, so a name in two lists means the parse has
    merged something.
    """
    problems = []
    for competition, clubs in entrants.items():
        if len(clubs) < LEAGUE_PHASE_SIZE:
            problems.append(
                f"{competition}: {len(clubs)} club(s), but a league phase holds "
                f"{LEAGUE_PHASE_SIZE}. The wrong table was read, or the season "
                f"is not settled yet."
            )
    everywhere: dict[str, list[str]] = {}
    for competition, clubs in entrants.items():
        for club in clubs:
            everywhere.setdefault(club, []).append(competition)
    for club, where in everywhere.items():
        if len(where) > 1:
            problems.append(
                f"{club} appears in {' and '.join(where)}, and a club plays in one"
            )
    return problems
