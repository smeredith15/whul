"""International results the martj42 ledgers have not caught up with, from ESPN.

The ledgers are maintained by hand and lag. On 28 September 2026 the men's file
stopped at 26 August and the women's at 10 June, so September's Nations League
window was not in either and every international slot read zero -- a feed that
is behind looks exactly like a season that has not started.

ESPN carries the same competitions a day after they are played, and serves
every club result this project scores already. So for each competition on the
ladder a drafted side can play in, the scoreboard is asked for the dates after
the ledger's last one, and every completed match comes back in the ledger's own
shape: date, home and away side, score, the ladder's name for the tournament,
and a shootout winner.

Only dates after the ledger's last are asked for, per gender. Nothing here can
double-count a match the ledger has: the day the ledger catches up past a date,
that date stops being asked about.

ESPN keys each competition by a slug that is not published, and a guessed slug
answers with an empty board rather than an error. So each competition lists the
slugs worth trying, the first to return a match is used, and every run says
which answered -- or that none did, by name, which is a gap and not a quiet
window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

#: The ledger's name for a tournament, which gender, and ESPN's slugs for it.
#: The ladder (whul/data/intl_tournaments.csv) decides what each is worth; a
#: name here that is not on the ladder is simply not scored.
COMPETITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("M", "UEFA Nations League", ("uefa.nations",)),
    ("W", "UEFA Women's Nations League", ("uefa.wnations", "uefa.nations.w",
                                          "uefa.w.nations")),
    ("M", "CONCACAF Nations League", ("concacaf.nations.league", "concacaf.nations")),
    ("M", "FIFA World Cup qualification", ("fifa.worldq.uefa", "fifa.worldq.concacaf",
                                           "fifa.worldq.conmebol", "fifa.worldq.afc",
                                           "fifa.worldq.caf", "fifa.worldq.ofc")),
    ("W", "FIFA World Cup qualification", ("fifa.wworldq.uefa", "fifa.wworldq.concacaf",
                                           "fifa.wworldq.conmebol")),
    ("M", "UEFA Euro qualification", ("uefa.euroq",)),
    ("W", "UEFA Euro qualification", ("uefa.weuroq",)),
    ("W", "CONCACAF Championship qualification", ("concacaf.w.championship.qual",
                                                  "concacaf.wchampionship.qual")),
    ("W", "CONMEBOL Nations League", ("conmebol.w.nations", "conmebol.nations.w")),
)

#: ESPN's spellings where the ledger's differ, for the sides whose names a
#: roster or a dedupe would read. Everyone else passes through as ESPN has it.
NAMES = {
    "USA": "United States", "Korea Republic": "South Korea", "Türkiye": "Turkey",
    "Czechia": "Czech Republic", "IR Iran": "Iran", "Côte d'Ivoire": "Ivory Coast",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina", "Cape Verde Islands": "Cape Verde",
    "Republic of Ireland": "Republic of Ireland", "China PR": "China",
}

#: The longest span asked for in one request.
SPAN_DAYS = 30


@dataclass
class Report:
    used: dict[str, str] = field(default_factory=dict)
    found: dict[str, int] = field(default_factory=dict)
    silent: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"  intl soccer (ESPN): {name} via {slug}, {self.found.get(name, 0)} "
               f"match(es) past the ledger" for name, slug in sorted(self.used.items())]
        if self.silent:
            out.append("  intl soccer (ESPN): nothing past the ledger for "
                       + ", ".join(self.silent))
        out += [f"  intl soccer (ESPN): ! {p}" for p in self.problems]
        return out


def _side(name: str) -> str:
    text = str(name or "").strip()
    for suffix in (" Women", " W"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return NAMES.get(text, text)


def _board(slug: str, start: date, end: date) -> dict:
    from whul.sources import espn

    return espn._get(f"{espn.BASE}/soccer/{slug}/scoreboard",
                     {"dates": f"{start:%Y%m%d}-{end:%Y%m%d}", "limit": 900})


def _spans(start: date, end: date):
    while start <= end:
        stop = min(start + timedelta(days=SPAN_DAYS - 1), end)
        yield start, stop
        start = stop + timedelta(days=1)


def matches(since: dict[str, date], until: date, board=None,
            report: Report | None = None) -> pd.DataFrame:
    """Completed matches after each gender's ``since`` date, in ledger shape.

    ``since`` is the last date each gender's ledger holds; the day after it is
    the first asked about.
    """
    from whul.sources import espn

    board = board or _board
    report = report if report is not None else Report()
    rows: list[dict] = []
    for gender, tournament, slugs in COMPETITIONS:
        start = since.get(gender)
        if start is None:
            continue
        start = start + timedelta(days=1)
        if start > until:
            continue
        label = f"{'Men' if gender == 'M' else 'Women'}'s {tournament}"
        answered = 0
        for slug in slugs:
            found: list[dict] = []
            for lo, hi in _spans(start, until):
                try:
                    payload = board(slug, lo, hi) or {}
                except Exception as exc:  # noqa: BLE001 -- one slug, one span
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status not in (400, 404):
                        report.problems.append(f"{label} via {slug}: "
                                               f"{type(exc).__name__}: {exc}")
                    break
                for event in payload.get("events") or []:
                    day = espn._event_day(event, lo)
                    pair = espn._soccer_rows(event, slug, day)
                    if len(pair) != 2:
                        continue
                    home = pair[0]
                    shootout = None
                    if home["shootout_for"] or home["shootout_against"]:
                        shootout = (_side(home["team"])
                                    if home["shootout_for"] > home["shootout_against"]
                                    else _side(home["opponent"]))
                    found.append({
                        "date": pd.Timestamp(day), "gender": gender,
                        "home_team": _side(home["team"]),
                        "away_team": _side(home["opponent"]),
                        "home_score": home["goals_for"],
                        "away_score": home["goals_against"],
                        "tournament": tournament, "city": "", "country": "",
                        "neutral": False, "shootout_winner": shootout,
                        "espn_event": home["event_id"],
                    })
            if found:
                report.used[label] = slug
                report.found[label] = report.found.get(label, 0) + len(found)
                rows += found
                answered += 1
                # A competition split by confederation lists one slug each;
                # anything else stops at the first that answers.
                if tournament != "FIFA World Cup qualification":
                    break
        if not answered:
            report.silent.append(label)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.drop_duplicates(subset=["gender", "espn_event"]).reset_index(drop=True)
