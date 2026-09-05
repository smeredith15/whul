"""Which of a feed's seasons a span of dates touches.

Feeds number seasons three different ways -- inside a calendar year, by the
year the season ends in, by the year it begins in -- and a league year that
opens in August sits across the join of two of them for most sports. Asking a
feed for "this year" gets the wrong season for months at a time, and the answer
is a complete, plausible season rather than an error, so nothing downstream
would question it.

The window is declared once per feed and the season labels are computed from
it. Nothing here fetches anything; it is arithmetic on a calendar so that every
adapter answers the question the same way.
"""

from __future__ import annotations

from datetime import date

#: How a feed numbers a season that crosses new year.
#:
#:   "within" -- it begins and ends inside the year it is named for.
#:   "ends"   -- named for the year it finishes in, which is how the NBA, the
#:               NHL, college basketball and European football are all indexed.
#:   "starts" -- named for the year it begins, which is how the NFL and college
#:               football are indexed.
NUMBERING = ("within", "ends", "starts")

#: ``(start month, day), (end month, day), numbering``.
Window = tuple[tuple[int, int], tuple[int, int], str]


def span(window: Window, season: int) -> tuple[date, date]:
    """A season's first and last day, uncapped by today.

    Uncapped deliberately: capping at today is right for walking dates and
    wrong for asking whether a season overlaps a league year, because a season
    still to come would look like it never existed.
    """
    start_md, end_md, numbering = window
    if numbering == "ends":
        return date(season - 1, *start_md), date(season, *end_md)
    if numbering == "starts":
        return date(season, *start_md), date(season + 1, *end_md)
    return date(season, *start_md), date(season, *end_md)


def overlapping(window: Window, first: date, last: date) -> list[int]:
    """Every season label with any play inside a span, in order.

    Two answers where the span crosses a season boundary -- an August league
    year catches the tail of a spring-to-autumn season and the front of the
    next -- and none at all where the league has not played yet, which is a
    real answer and not the same as a feed with nothing in it.
    """
    found = []
    for label in range(first.year - 1, last.year + 2):
        opens, closes = span(window, label)
        if opens <= last and closes >= first:
            found.append(label)
    return found
