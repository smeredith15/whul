"""Upcoming team-sport matches from the Flashscore feed.

The leagues whose results reach this project through a scoreboard walk -- MLB,
the NBA, and every club soccer competition -- carry no fixtures with them: the
walk asks for dates that have already happened. Their next games come from
here instead, off the same feed ``whul.sources.flashscore`` already reads for
tennis, at a different sport id.

The payload is the same ``KEY÷value¬`` / ``~`` format, and the same two facts
govern the parse: a ``ZA÷`` header opens a competition and every ``AA÷`` record
after it belongs to that competition until the next header, so position
matters; and ``AC÷`` carries the status, of which only the upcoming codes are
wanted here. That is the exact inverse of the tennis parser, which keeps the
finished ones.

Team names come from ``AE÷`` and ``AF÷`` -- display names, not the ``WU``/``WV``
slugs tennis uses, because a club is written out in full where a player is not.

**No competition filter.** A club's next game is its next game whether it is a
league match, a cup tie or a European night, and a table of competition names
would be one more thing to keep in step with a feed that renames things. The
filter is the roster instead: whatever the feed offers, only the clubs somebody
holds are kept. That also makes the parse indifferent to which of the twenty
leagues a club plays in.

**UNVERIFIED.** Flashscore is blocked by egress policy from where this was
written, so no line below has met a real payload. It is modelled on the tennis
parser in the sibling module, which is running against this feed in
``smeredith15/tennis2026``. Run ``python -m whul.cli probe fixtures`` from a
machine with access: it prints what came back, what parsed, and which rostered
clubs matched, which is what a correction would be made from.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone

import pandas as pd
import requests

from whul.sources.flashscore import (
    REQUEST_PAUSE, SPORT_BASEBALL, SPORT_BASKETBALL, SPORT_SOCCER, _field, _get,
)

#: Which sport id serves each league's fixtures.
SPORTS: dict[str, int] = {
    "MLB": SPORT_BASEBALL,
    "NBA": SPORT_BASKETBALL,
    "Premier League": SPORT_SOCCER,
    "La Liga": SPORT_SOCCER,
    "Serie A": SPORT_SOCCER,
    "Bundesliga": SPORT_SOCCER,
    "Ligue 1": SPORT_SOCCER,
    "MLS": SPORT_SOCCER,
    "NWSL": SPORT_SOCCER,
    "Club Soccer": SPORT_SOCCER,
}

#: How far ahead to look. The feed's window is a fortnight wide and only the
#: future half is wanted, so this is half the requests the tennis reader makes.
#: Seven days is enough for any of these sports to have played: a club that has
#: no game inside a week is between competitions, and saying nothing is then
#: the true answer rather than a gap.
AHEAD = range(0, 8)

#: Statuses meaning "has not started". Everything else -- in play, finished,
#: postponed, cancelled -- is not a fixture anyone can turn up to.
STATUS_UPCOMING = {"1", "18"}

#: A competition header is "COUNTRY: Competition Name", sometimes with a stage
#: after it. Both halves matter: the name is what a reader sees, and the
#: country is what stops a club being given somebody else's fixture.
_HEADER_SPLIT = re.compile(r"^\s*[^:]*:\s*")
_COUNTRY = re.compile(r"^\s*([^:]+):")


def competition_of(header: str) -> str:
    """'ENGLAND: Premier League - Round 5' -> 'Premier League'."""
    without_country = _HEADER_SPLIT.sub("", str(header or "")).strip()
    return re.split(r"\s+[-–]\s+", without_country)[0].strip()


def country_of(header: str) -> str:
    """'ENGLAND: Premier League - Round 5' -> 'ENGLAND'.

    This feed is the whole world at once -- the probe's first three days came
    back with Argentine Primera C, Armenian second tier and Western Australian
    play-offs -- and club names are not unique across it. Brazil's Serie B has
    an Athletic Club; so does Bilbao. Without the country, one of them gets the
    other's fixture and the page looks right while being wrong.
    """
    found = _COUNTRY.match(str(header or ""))
    return found.group(1).strip().upper() if found else ""


#: Names that are a club's second string rather than the club. Flashscore
#: writes River Plate's reserves as "River Plate 2"; the age-group sides carry
#: their bracket. Excluded outright: a reserve fixture beside a first-team
#: badge is a wrong answer, not a partial one.
#: A *single* trailing digit, 2 to 9. Not any number: Schalke 04, Hannover 96
#: and Mainz 05 are first teams whose names end in a year, and a bare "\s\d+"
#: rule would quietly drop all three. A reserve side is never "1".
RESERVE_PATTERN = re.compile(
    r"(\s[2-9]|\bU\s?1[5-9]\b|\bU\s?2[0-3]\b|\breserves?\b|\byouth\b|"
    r"\bII\b|\sB)$",
    re.IGNORECASE,
)


def _when(segment: str) -> date | None:
    """The kickoff, as a date in UTC.

    ``AD`` is a Unix timestamp. Read as UTC rather than local: the machine that
    runs the nightly job and the machine someone reads the page on are in
    different places, and a fixture that moves a day depending on who is asking
    is worse than one that is occasionally a few hours out.
    """
    raw = _field(segment, "AD")
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def iter_fixtures(raw: str):
    """One dict per upcoming match in a raw payload.

    Header position assigns a match to a competition, so records are walked in
    order and the most recent header applies -- the same rule the tennis parser
    follows, and for the same reason.
    """
    competition = ""
    country = ""
    for segment in (s for s in str(raw).split("~") if s):
        if segment.startswith("ZA÷"):
            header = _field(segment, "ZA") or ""
            competition = competition_of(header)
            country = country_of(header)
            continue
        if not segment.startswith("AA÷"):
            continue
        if (_field(segment, "AC") or "") not in STATUS_UPCOMING:
            continue
        home = (_field(segment, "AE") or "").strip()
        away = (_field(segment, "AF") or "").strip()
        when = _when(segment)
        if not home or not away or when is None:
            continue
        if RESERVE_PATTERN.search(home) or RESERVE_PATTERN.search(away):
            continue
        yield {
            "match_uid": _field(segment, "AA") or "",
            "game_date": when.isoformat(),
            "home_team": home,
            "away_team": away,
            "competition": competition,
            "country": country,
            # A schedule frame's shape, so `whul.fixtures.harvest` reads this
            # exactly as it reads nflverse's. Null both sides: these are the
            # games nobody has played.
            "home_score": None,
            "away_score": None,
        }


def fetch_window(sport: int, days: range = AHEAD, verbose: bool = True) -> str:
    """The next week of one sport, as one payload."""
    chunks = []
    for day in days:
        try:
            chunks.append(_get(day, cache_key=None, sport=sport))
        except requests.RequestException as exc:
            # One bad day must not lose the other six.
            if verbose:
                print(f"  flashscore sport {sport} day {day}: "
                      f"{type(exc).__name__}", flush=True)
            continue
        time.sleep(0)  # _get already paces itself; kept for readability
    return "~".join(chunks)


def load_upcoming(sport: int, days: range = AHEAD, verbose: bool = True) -> pd.DataFrame:
    """Every upcoming match one sport has in the window, unfiltered.

    Unfiltered on purpose: the caller narrows by roster, which is both cheaper
    to keep right than a competition table and indifferent to a club playing in
    a competition nobody listed.
    """
    raw = fetch_window(sport, days, verbose=verbose)
    rows = list(iter_fixtures(raw))
    if verbose:
        competitions = sorted({r["competition"] for r in rows if r["competition"]})
        print(f"  flashscore sport {sport}: {len(rows)} upcoming match(es) "
              f"across {len(competitions)} competition(s)", flush=True)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    return frame.drop_duplicates(subset=["match_uid"]).reset_index(drop=True)


def probe(sport: int = SPORT_SOCCER, days: range = range(0, 3)) -> dict:
    """What the feed actually returns, for correcting this from its output.

    Every guess in this module is reported separately, because they fail
    separately: the request, the record split, the status codes present, the
    name fields, and the competition headers. A parser that returns nothing
    could be any one of them, and the whole point of a probe is to say which.
    """
    out: dict[str, object] = {"sport": sport, "days": str(days)}
    try:
        raw = fetch_window(sport, days, verbose=False)
    except Exception as exc:  # noqa: BLE001
        out["fetch"] = f"FAILED: {type(exc).__name__}: {exc}"
        return out
    out["bytes"] = len(raw)
    if not raw:
        out["fetch"] = "EMPTY -- the request worked and returned nothing"
        return out

    segments = [s for s in raw.split("~") if s]
    out["records"] = len(segments)
    out["headers"] = sum(1 for s in segments if s.startswith("ZA÷"))
    out["matches"] = sum(1 for s in segments if s.startswith("AA÷"))
    statuses: dict[str, int] = {}
    for segment in segments:
        if segment.startswith("AA÷"):
            code = _field(segment, "AC") or "(none)"
            statuses[code] = statuses.get(code, 0) + 1
    out["status_codes"] = dict(sorted(statuses.items(), key=lambda kv: -kv[1]))
    out["upcoming_expected"] = sorted(STATUS_UPCOMING)

    # Whether the name fields are the ones assumed. A team sport writes clubs
    # out in AE/AF; if this reports zero, the field codes are the bug and the
    # sample below says what to use instead.
    named = [s for s in segments if s.startswith("AA÷") and _field(s, "AE")]
    out["records_with_AE"] = len(named)
    if segments:
        sample = next((s for s in segments if s.startswith("AA÷")), "")
        out["sample_match_fields"] = sorted(
            set(re.findall(r"([A-Z]{2})÷", sample))
        )
    out["sample_headers"] = [
        _field(s, "ZA") for s in segments if s.startswith("ZA÷")
    ][:8]

    parsed = list(iter_fixtures(raw))
    out["parsed"] = len(parsed)
    out["sample_fixtures"] = [
        f"{p['game_date']}  {p['home_team']} v {p['away_team']}  "
        f"({p['country']}: {p['competition']})"
        for p in parsed[:8]
    ]
    out["countries"] = sorted({p["country"] for p in parsed if p["country"]})[:25]
    return out
