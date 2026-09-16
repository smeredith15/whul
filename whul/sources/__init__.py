"""Feeds, and the one rule they all share about what may be kept."""

from __future__ import annotations

from datetime import date


def season_is_over(season: int, today: date | None = None) -> bool:
    """May a season's results be cached forever?

    Only once the season cannot gain another result. A finished season is the
    same answer every time it is asked, so caching it permanently saves a
    request and risks nothing. A season still being run is the opposite: its
    results grow every race weekend, and a cache with no expiry goes on serving
    whatever was current the first night it was written.

    That is not hypothetical. Formula 1 and NASCAR both froze on it -- eleven
    days of identical figures with the source reporting ten healthy rows every
    night, because the first pull of the 2026 season had been written to disk
    and never asked again. `jolpica.races_ahead` already reasons this way about
    the calendar ("a frozen copy would go on naming a date that no longer
    exists"); this is the same thought about the results.

    The calendar year is the test because every series here runs inside one.
    A season is over when its year is.
    """
    return int(season) < (today or date.today()).year
