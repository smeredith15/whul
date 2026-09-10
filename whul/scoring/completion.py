"""When a postseason or European competition is finished.

A postseason bonus is a *rate*: the points a player scored there divided by the
games that produced them, credited as though played over a share of a regular
season. Which means it is at its noisiest after one game -- Mbappe's single
Champions League goal projected to 11.4 points, nearly half his league season,
off a sample of one -- and that playing a second match without scoring *lowers*
it. He would have lost 1.9 normalized points for taking the pitch.

So the bonus is held until the competition is over, and only then credited. Up
to that point it is carried as a pending figure and shown as one. Scores in
these sports otherwise only rise, and a manager cannot be told that a player's
score fell because he played.

**Declared rather than derived.** There is no signal in any feed here that says
"this competition is finished": a scoreboard says what happened on a date, and
the absence of a fixture is equally the absence of a fixture *feed*. What is
knowable is that every one of these competitions ends within a few weeks of the
same date each year, so the date is written down, with a margin, and reviewed
when it moves. A date that is too late costs a few days of holding a bonus that
was already earned. One that is too early credits a rate that can still fall,
which is the thing this exists to prevent -- so where the two are in tension the
later date wins.
"""

from __future__ import annotations

from datetime import date

#: (month, day) by which a competition is certainly finished, in the calendar
#: year its season *ends* in. Generous on purpose: see the module docstring.
#:
#: Every entry is a real final, plus a margin:
#:   NFL      Super Bowl, early February
#:   NBA/NHL  Finals and the Stanley Cup, mid-June
#:   MLB      World Series, early November
#:   WNBA     Finals, mid-October
#:   NWSL     Championship, late November
#:   MLS      MLS Cup, early December
#:   UEFA     the three finals, late May into early June
#:   CONCACAF Champions Cup final, early June
FINISHED_BY: dict[str, tuple[int, int]] = {
    "NFL": (2, 28),
    "NBA": (7, 15),
    "NHL": (7, 15),
    "MLB": (11, 20),
    "WNBA": (10, 31),
    "NWSL": (12, 15),
    "MLS": (12, 31),
    # The European domestic seasons, which end in May. Named for the year they
    # finish in throughout this project -- 2026-27 is season 2027 -- so no
    # entry in NAMED_FOR_ITS_START.
    "Premier League": (6, 15),
    "La Liga": (6, 15),
    "Serie A": (6, 15),
    "Bundesliga": (6, 15),
    "Ligue 1": (6, 15),
    "UCL": (6, 30),
    "Europa League": (6, 30),
    "Europa Conference League": (6, 30),
    "CONCACAF Champions Cup": (6, 30),
}

#: A competition whose season is named for the year it *starts* in, so the
#: finishing date falls in the year after.
#:
#: Only the NFL. Its 2026 season is played from September 2026 and settled at a
#: Super Bowl in February 2027. Everything else here is labelled by the year it
#: finishes in -- the European competitions and the NBA and NHL because their
#: seasons cross the new year and are named for the second half of it, and MLB,
#: the WNBA, the NWSL and MLS because theirs begin and end inside one calendar
#: year. Both of those arrive at the same rule from opposite directions, which
#: is why this set is a list of exceptions rather than a mapping.
NAMED_FOR_ITS_START = {"NFL"}


def finishes(competition: str, season: int) -> date | None:
    """The date this competition's season is certainly over, or None.

    ``None`` for a competition with no entry, which callers must read as "not
    known to be finished" rather than as finished. A competition added to
    ``postseason.RULES`` without a date here would otherwise start crediting a
    rate that can still move, silently.
    """
    when = FINISHED_BY.get(str(competition))
    if when is None:
        return None
    year = int(season) + 1 if competition in NAMED_FOR_ITS_START else int(season)
    return date(year, *when)


def is_complete(competition: str, season: int, as_of: date | None = None) -> bool:
    """Has this competition's season finished as of ``as_of``?

    False where the date is unknown. Holding a bonus that was earned is a
    figure arriving late; crediting one that can still fall is a score going
    down, and only one of those is worth risking.
    """
    end = finishes(competition, season)
    if end is None:
        return False
    return (as_of or date.today()) >= end
