"""International results from the Flashscore day feed, past the ledgers.

The second of two top-ups for the martj42 ledgers (the first is
``whul.sources.intl_espn``). The first run of the ESPN top-up, on 28 September
2026, came back with nothing from any of the nine competitions it asked about
while the Nations League window was being played -- and ESPN cannot be reached
from where this was written, so which of its slugs is wrong cannot be settled
by looking. Flashscore can be read from the runner: the nightly fixture pull
asks its soccer feed for the week ahead every night, and the NHL results it
reads off the same feed have been scoring since the preseason.

The feed is a fortnight wide, seven days back and seven ahead, so this sees the
last week and nothing older. That is enough for a nightly job, and what ages out
of the window is kept by the caller (see ``intl_soccer._past_the_ledger``) until
the ledger itself catches up.

**What is kept.** A completed match between two senior national sides, under a
competition header the ladder has a name for. The header is read by keyword --
"Nations League" under EUROPE is the UEFA one, anything with "Qualification" and
"World" is World Cup qualifying -- because the feed's own spelling of a
competition is not something this project has seen yet, and a keyword survives
"World Cup" against "World Championship" where an exact string would not. A
header naming an age group is refused outright: the Euro U21 qualifiers are
"Euro" and "Qualification" too.

**UNVERIFIED** against a real payload for this sport's national-team headers,
for the reason above. Every run names the headers it scored and the national
competitions it saw and left out, which is what a correction would be made from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from whul.sources.intl_espn import NAMES

#: The feed's days, relative to today. Only the past half holds results.
DAYS = range(-7, 1)

#: A competition for an age group, in the header or the side's name.
AGE_GROUP = re.compile(r"\bU[\s-]?\d{2}\b", re.IGNORECASE)

#: Headers whose country says the competition is between national sides.
CONFEDERATIONS = {"EUROPE", "WORLD", "NORTH & CENTRAL AMERICA", "SOUTH AMERICA",
                  "AFRICA", "ASIA", "OCEANIA"}

#: Flashscore's spellings where the ledger's differ, on top of ESPN's.
FLASHSCORE_NAMES = {
    **NAMES,
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Ireland": "Republic of Ireland",
    "Rep. of Ireland": "Republic of Ireland",
    "Czech Rep.": "Czech Republic",
    "Korea Rep.": "South Korea",
}

#: Memo of today's window, so a backfill that loads the ledgers once per day
#: it rebuilds asks the feed once rather than eight times over.
_WINDOW: dict[date, str] = {}


@dataclass
class Report:
    found: dict[str, int] = field(default_factory=dict)
    unscored: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = []
        if self.found:
            out.append("  intl soccer (Flashscore): " + ", ".join(
                f"{name} {n}" for name, n in sorted(self.found.items()))
                + " match(es) past the ledger")
        else:
            out.append("  intl soccer (Flashscore): nothing past the ledger "
                       "under a competition the ladder scores")
        if self.unscored:
            out.append("  intl soccer (Flashscore): national-team headers seen "
                       "and not scored -- " + ", ".join(
                           f"{h} ({n})" for h, n in sorted(self.unscored.items())[:12]))
        out += [f"  intl soccer (Flashscore): ! {p}" for p in self.problems]
        return out


def _name(side: str) -> str:
    from whul.sources.flashscore_fixtures import strip_womens

    text = strip_womens(side)
    for suffix in (" Women",):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return FLASHSCORE_NAMES.get(text, text)


def tournament_of(header: str, gender: str) -> str | None:
    """The ledger's name for the competition a header names, or None."""
    text = str(header or "")
    if AGE_GROUP.search(text):
        return None
    country, _, rest = text.partition(":")
    country = country.strip().upper()
    rest = rest.lower() if rest else text.lower()
    if "friendl" in rest:
        return None
    qualifying = "qualif" in rest
    if "nations league" in rest:
        if country == "EUROPE":
            return "UEFA Nations League"
        if country == "NORTH & CENTRAL AMERICA" and gender == "M":
            return ("CONCACAF Nations League qualification" if qualifying
                    else "CONCACAF Nations League")
        if country == "SOUTH AMERICA" and gender == "W":
            return "CONMEBOL Nations League"
        return None
    if not qualifying:
        return None
    if "world" in rest:
        return "FIFA World Cup qualification"
    if re.search(r"\beuro\b", rest):
        return "UEFA Euro qualification"
    if "gold cup" in rest:
        return "Gold Cup qualification" if gender == "M" else "CONCACAF Gold Cup qualification"
    if gender == "W" and "championship" in rest and country == "NORTH & CENTRAL AMERICA":
        return "CONCACAF Championship qualification"
    if "africa" in rest:
        return ("African Cup of Nations qualification" if gender == "M"
                else "Africa Cup of Nations qualification")
    if "asian cup" in rest:
        return "AFC Asian Cup qualification"
    return None


def iter_results(raw: str):
    """Completed matches with the header each sat under, in feed order."""
    from whul.sources.flashscore import STATUS_COMPLETED, _field
    from whul.sources.flashscore_fixtures import (
        RESERVE_PATTERN, _score, _when, is_womens,
    )

    header = ""
    for segment in (s for s in str(raw).split("~") if s):
        if segment.startswith("ZA÷"):
            header = _field(segment, "ZA") or ""
            continue
        if not segment.startswith("AA÷"):
            continue
        if (_field(segment, "AC") or "") != STATUS_COMPLETED:
            continue
        home = (_field(segment, "AE") or "").strip()
        away = (_field(segment, "AF") or "").strip()
        when = _when(segment)
        if not home or not away or when is None:
            continue
        if any(RESERVE_PATTERN.search(s) or AGE_GROUP.search(s) for s in (home, away)):
            continue
        code = _field(segment, "AS")
        won = {"1": "home", "2": "away", "3": "draw"}.get(code or "", "")
        if not won:
            continue
        here, there = _score(segment, won)
        womens = (is_womens(home) or is_womens(away)
                  or "women" in header.lower())
        yield {
            "event": _field(segment, "AA") or "", "header": header, "date": when,
            "gender": "W" if womens else "M", "home": home, "away": away,
            "home_score": here, "away_score": there,
        }


def _window() -> str:
    from whul.sources.flashscore import SPORT_SOCCER
    from whul.sources.flashscore_fixtures import fetch_window

    today = date.today()
    if today not in _WINDOW:
        _WINDOW.clear()
        _WINDOW[today] = fetch_window(SPORT_SOCCER, DAYS, verbose=False)
    return _WINDOW[today]


def matches(since: dict[str, date], until: date, raw: str | None = None,
            report: Report | None = None) -> pd.DataFrame:
    """Completed matches after each gender's ``since`` date, in ledger shape."""
    report = report if report is not None else Report()
    raw = _window() if raw is None else raw
    rows = []
    for match in iter_results(raw):
        gender, header = match["gender"], match["header"]
        tournament = tournament_of(header, gender)
        if tournament is None:
            if header.partition(":")[0].strip().upper() in CONFEDERATIONS \
                    and not AGE_GROUP.search(header):
                report.unscored[header] = report.unscored.get(header, 0) + 1
            continue
        start = since.get(gender)
        if start is None or not (start < match["date"] <= until):
            continue
        if match["home_score"] is None or match["away_score"] is None:
            report.problems.append(
                f"{match['home']} v {match['away']} on {match['date']}: a result "
                f"with no score that agrees with it, left out")
            continue
        label = f"{'Men' if gender == 'M' else 'Women'}'s {tournament}"
        report.found[label] = report.found.get(label, 0) + 1
        rows.append({
            "date": pd.Timestamp(match["date"]), "gender": gender,
            "home_team": _name(match["home"]), "away_team": _name(match["away"]),
            "home_score": match["home_score"], "away_score": match["away_score"],
            "tournament": tournament, "city": "", "country": "", "neutral": False,
            "shootout_winner": None, "event": f"flashscore:{match['event']}",
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.drop_duplicates(subset=["event"]).reset_index(drop=True)
