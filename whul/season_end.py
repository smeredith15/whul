"""When an asset's season is over, and when its score can no longer change.

Two questions with different answers, asked by the site.

**Is the season over?** For a club, once it has played its last game of the
league year: out of the postseason, or never in it once the regular season
ended. A scorer that can tell answers per club (MLB does -- see
``whul.scoring.mlb.team_status``), and the answer is kept for every club in
``club_games`` so a player's page can ask about his club. Everything else is
answered by the calendar: each league has a day by which nothing more can land
in this league year, and after it every asset in the league is done.

**Can the score still change?** Not once the season is over -- except MLB,
whose league year is the rest of 2026 *and* the first half of 2027, so a club
knocked out in October has a whole spring still to score in. The individual
sports are left out of both questions: a golfer's or a driver's year has no
last game the way a club's does, and the league admin asked for neither marker
there.

Only the calendar answers "final" for now. A club whose postseason ended early
can still move after its last game -- a player's postseason bonus is held until
the whole postseason is over (``whul.scoring.completion``) -- so a club flag
alone is not enough to call a score final, and the declared day is.

**Declared rather than derived**, for the reason ``completion`` gives: no feed
says a season is finished. The days are generous on purpose. Too late costs a
few days without a marker; too early marks a score final that then moves.
"""

from __future__ import annotations

from datetime import date

from whul.config.league import SEASON, covered_by
from whul.scoring import completion

#: Leagues whose score goes on changing after their season ends.
NEVER_FINAL = frozenset({"MLB"})

#: Leagues with no season to be over: a year of events rather than a schedule.
INDIVIDUAL = frozenset({"PGA", "ATP", "WTA", "Tennis", "F1", "NASCAR",
                        "Motorsports", "Olympics"})

#: The European competitions a club-soccer asset can still be playing in after
#: its league has finished.
EUROPE = ("UCL", "Europa League", "Europa Conference League")

#: Leagues ``completion`` has no date for, as (month, day) in the calendar year
#: the league year *ends* in. Each a real final plus a margin.
#:   NCAAF          the College Football Playoff final, mid-to-late January
#:   NCAAM, NCAAW   the Final Fours, early April
#:   NCAA Softball  the Women's College World Series, early June
#:   NCAA Baseball  the Men's College World Series, late June
#:   Intl Soccer    a tournament that begins inside the league year scores
#:                  whole in it (the 2027 Women's World Cup ends 25 July), so
#:                  the last one can finish weeks after the year does
DECLARED: dict[str, tuple[int, int]] = {
    "NCAAF": (1, 31),
    "NCAAM": (4, 30),
    "NCAAW": (4, 30),
    "NCAA Softball": (6, 20),
    "NCAA Baseball": (6, 30),
    "Men's Intl Soccer": (8, 15),
    "Women's Intl Soccer": (8, 15),
}

#: Club soccer leagues that finish in May and whose clubs play in Europe.
EUROPEAN_LEAGUES = frozenset({"Premier League", "La Liga", "Serie A",
                              "Bundesliga", "Ligue 1"})


def finished_by(league: str) -> date | None:
    """The day after which nothing more can land for ``league`` this league year.

    ``None`` where no day is known, which callers must read as "not over".
    MLB's is the end of its *2026* postseason: the half of the league year a
    profile's 2026 tab shows.
    """
    league = str(league)
    if league in INDIVIDUAL:
        return None
    first, last = SEASON.start.year, SEASON.end.year
    if league == "NFL":
        return completion.finishes("NFL", first)
    if league == "MLB":
        return completion.finishes("MLB", first)
    if league in ("NBA", "NHL"):
        return completion.finishes(league, last)
    if league in EUROPEAN_LEAGUES:
        days = [completion.finishes(c, last) for c in (league, *EUROPE)]
        return max(d for d in days if d is not None)
    if league in ("MLS", "NWSL"):
        # Drafted for their 2027 seasons, which run past the league year; a
        # result after its last day does not count.
        return SEASON.end
    if league in DECLARED:
        return date(last, *DECLARED[league])
    members = covered_by(league)
    if members:
        days = [finished_by(m) for m in members]
        return None if None in days else max(days)
    return None


def season_over(league: str, as_of: date, club_done: bool = False) -> bool:
    """Has this asset played its last game of the league year?"""
    if str(league) in INDIVIDUAL:
        return False
    if club_done:
        return True
    end = finished_by(league)
    return end is not None and as_of > end


def score_final(league: str, as_of: date) -> bool:
    """Can this asset's score no longer change?"""
    if str(league) in NEVER_FINAL or str(league) in INDIVIDUAL:
        return False
    end = finished_by(league)
    return end is not None and as_of > end
