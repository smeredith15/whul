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


def today() -> date:
    """Today's date in Eastern time."""
    return datetime.now(EASTERN).date()
