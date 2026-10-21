"""The league's clock: US Eastern.

Every feed stamps a game in UTC, and a date taken from a UTC stamp puts an
evening game in North America on the next day -- a 10 pm Eastern first pitch
is 02:00 the following morning in UTC. The league's managers read the site in
Eastern time, so a game's day is its Eastern day, and "today" is the Eastern
date, not the date of whichever machine runs the nightly job.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def eastern_day(stamp) -> date | None:
    """The Eastern date of a moment given as a Unix timestamp or an ISO string.

    An ISO string with no time zone is taken as UTC, which is what every feed
    here writes. A bare date ("2026-09-29") is returned as it is: it names a
    day already, and shifting it would move it back one.
    """
    if stamp is None:
        return None
    if isinstance(stamp, (int, float)):
        try:
            moment = datetime.fromtimestamp(float(stamp), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return moment.astimezone(EASTERN).date()
    text = str(stamp).strip()
    if not text:
        return None
    if len(text) == 10:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(EASTERN).date()


def eastern_clock(stamp) -> str:
    """The Eastern wall-clock time of a moment, as ``HH:MM``, or "" if unknown.

    Takes what ``eastern_day`` takes. Midnight is read as unknown: ESPN files a
    game whose time is still to be set at midnight Eastern, and a fixture board
    saying "12:00 AM" for every TBD game would be inventing a kickoff.
    """
    if stamp is None:
        return ""
    if isinstance(stamp, (int, float)):
        try:
            moment = datetime.fromtimestamp(float(stamp), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return ""
    else:
        text = str(stamp).strip()
        if len(text) <= 10 or "T" not in text and " " not in text:
            return ""
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    clock = moment.astimezone(EASTERN).strftime("%H:%M")
    return "" if clock == "00:00" else clock


def today() -> date:
    """Today's date in Eastern time."""
    return datetime.now(EASTERN).date()
