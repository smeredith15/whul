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

ESPN keys each competition by a slug that is not published. So each
competition lists the slugs worth trying, the first to return a match is used,
and every run says which answered -- or that none did, by name, with the status
each slug was refused with, which is a gap and not a quiet window.

The first run asked nine competitions and heard nothing from any of them while
the Nations League window was being played: every slug was refused on its first
request (the request count says so, and the run printed no other fault). A
refused *range* and a refused *slug* look the same from one request, so a slug
whose range is refused is asked for a single date too -- the request shape every
club pull began with -- and, if that is answered, walked a date at a time over
the recent days. Flashscore is the second top-up either way; see
``whul.sources.intl_flashscore``.
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

#: How far back a slug that refuses ranges is walked a date at a time. The
#: nightly job needs yesterday; three weeks survives a fortnight of missed runs
#: without walking the whole summer for a women's ledger that stopped in June.
WALK_DAYS = 21

#: Boards already fetched this run. A backfill loads the ledgers once for each
#: day it rebuilds, and the spans asked for are the same every time.
_BOARDS: dict[tuple[str, str], dict] = {}


@dataclass
class Report:
    used: dict[str, str] = field(default_factory=dict)
    found: dict[str, int] = field(default_factory=dict)
    silent: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    refused: dict[str, list[str]] = field(default_factory=dict)

    def lines(self) -> list[str]:
        out = [f"  intl soccer (ESPN): {name} via {slug}, {self.found.get(name, 0)} "
               f"match(es) past the ledger" for name, slug in sorted(self.used.items())]
        if self.silent:
            out.append("  intl soccer (ESPN): nothing past the ledger for "
                       + ", ".join(self.silent))
        for label, said in sorted(self.refused.items()):
            out.append(f"  intl soccer (ESPN): {label} refused -- {', '.join(said)}")
        out += [f"  intl soccer (ESPN): ! {p}" for p in self.problems]
        return out


def _side(name: str) -> str:
    text = str(name or "").strip()
    for suffix in (" Women", " W"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return NAMES.get(text, text)


def _status(exc: Exception) -> int | None:
    return getattr(getattr(exc, "response", None), "status_code", None)


def _board(slug: str, start: date, end: date) -> dict:
    """One span's board, trying the shapes the club range pull tries.

    A single date is asked for as a date rather than a one-day range. Raises
    the last refusal when every shape is refused.
    """
    from whul.sources import espn

    span = (f"{start:%Y%m%d}" if start == end else f"{start:%Y%m%d}-{end:%Y%m%d}")
    if (slug, span) in _BOARDS:
        return _BOARDS[(slug, span)]
    last: Exception | None = None
    for params in ({"dates": span, "limit": 900}, {"dates": span}):
        try:
            payload = espn._get(f"{espn.BASE}/soccer/{slug}/scoreboard", params)
        except Exception as exc:  # noqa: BLE001 -- the next shape may answer
            if _status(exc) not in (400, 404):
                raise
            last = exc
            continue
        _BOARDS[(slug, span)] = payload
        return payload
    raise last  # type: ignore[misc]


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
            for lo, hi, day_by_day in _asks(slug, start, until, board, label, report):
                try:
                    payload = board(slug, lo, hi) or {}
                except Exception as exc:  # noqa: BLE001 -- one slug, one span
                    if _status(exc) not in (400, 404):
                        report.problems.append(f"{label} via {slug}: "
                                               f"{type(exc).__name__}: {exc}")
                    if not day_by_day:
                        break
                    continue
                for event in payload.get("events") or []:
                    row = _row(event, slug, lo, gender, tournament)
                    if row is not None:
                        found.append(row)
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
    return frame.drop_duplicates(subset=["gender", "event"]).reset_index(drop=True)


def _asks(slug, start, until, board, label, report):
    """The requests to make for one slug: ranges, or dates if ranges are refused.

    Yields ``(lo, hi, day_by_day)``. The first range is tried; if it is
    refused, one date is, and only if that is answered is the slug walked a
    date at a time over the last ``WALK_DAYS`` -- a slug refused both ways is
    a slug ESPN does not have, and walking it would be a request per date for
    nothing.
    """
    spans = list(_spans(start, until))
    try:
        board(slug, *spans[0])
    except Exception as exc:  # noqa: BLE001 -- told apart below
        status = _status(exc)
        if status not in (400, 404):
            yield (*spans[0], False)   # the caller reports it
            return
        try:
            board(slug, until, until)
        except Exception as again:  # noqa: BLE001
            report.refused.setdefault(label, []).append(
                f"{slug} {status} for a range, {_status(again) or type(again).__name__} "
                f"for a date")
            return
        report.refused.setdefault(label, []).append(
            f"{slug} {status} for a range, answered a date at a time")
        day = max(start, until - timedelta(days=WALK_DAYS - 1))
        while day <= until:
            yield day, day, True
            day += timedelta(days=1)
        return
    for lo, hi in spans:
        yield lo, hi, False


def _row(event: dict, slug: str, asked: date, gender: str, tournament: str) -> dict | None:
    """One completed match as a ledger row, or None."""
    from whul.sources import espn

    day = espn._event_day(event, asked)
    pair = espn._soccer_rows(event, slug, day)
    if len(pair) != 2:
        return None
    home = pair[0]
    shootout = None
    if home["shootout_for"] or home["shootout_against"]:
        shootout = (_side(home["team"])
                    if home["shootout_for"] > home["shootout_against"]
                    else _side(home["opponent"]))
    return {
        "date": pd.Timestamp(day), "gender": gender,
        "home_team": _side(home["team"]), "away_team": _side(home["opponent"]),
        "home_score": home["goals_for"], "away_score": home["goals_against"],
        "tournament": tournament, "city": "", "country": "",
        "neutral": False, "shootout_winner": shootout,
        "event": f"espn:{home['event_id']}",
    }
