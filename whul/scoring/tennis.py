"""Tennis scoring.

Only wins score. A win is worth the ATP ranking points for the round it was won
in -- the same table for both tours -- with a bonus for winning in straight sets
that depends on the format: 1.5x at best-of-five, where straight sets skipped
two, and 1.25x at best-of-three, where it skipped one.

Everything else here follows the engine in ``smeredith15/tennis2026``, whose
approach to the two hard parts is the correct one:

**The tier comes from the tournament's category and draw size, not its name.**
Which points table applies depends on what kind of event it is (slam, 1000, 500,
250, Tour Finals, team event) and how big the field was. Both facts are carried
by the feeds -- Flashscore states the category in its tournament header,
Sackmann's archives carry ``tourney_level`` and ``draw_size`` -- so neither has
to be guessed. The earlier port of ``Tennis_Players.R`` inferred both from a
keyword list of host cities and a count of matches played, which misread the
draw shape on essentially every event: at its cutoffs a 56-draw Masters and a
96-draw Masters landed on the same table, and a 32-draw 500 landed on the
64-draw one.

**A bye is the absence of a result in the preceding round.** Per ATP rules a
player who receives a bye in round N and wins round N+1 earns both rounds'
points for that single win. Having no result in round N is the authoritative
signal, and it covers structural byes too -- in a 96-draw the 32 seeds skip the
opening round without a bye ever being recorded as such. Scoring the credit off
a player's *first win* instead, as the R script did, agrees in a full knockout
bracket but not in a round robin, and says nothing about why the round was
skipped.

Qualifying rounds are never scored. They are worth listing in a round map only
so a feed's qualifying matches can be recognized and dropped rather than falling
through to a main-draw round.
"""

from __future__ import annotations

import re

import pandas as pd

from whul.config.league import norm_league
from whul.scoring.base import resolve_num, resolve_str

# --- rounds ---------------------------------------------------------------
RR, R128, R64, R32, R16, QF, SF, F, W = (
    "RR", "R128", "R64", "R32", "R16", "QF", "SF", "F", "W"
)
QUALIFYING_ROUNDS = ("Q1", "Q2", "Q3")

#: ``W`` is how some feeds label the final; it pays the same as ``F`` and is
#: folded into it so a champion is never credited with two wins for one match.
FINAL_ALIASES = (F, W)

# --- categories -----------------------------------------------------------
GRAND_SLAM = "Grand Slam"
MASTERS_1000 = "Masters 1000"
TOUR_500 = "500"
TOUR_250 = "250"
TOUR_FINALS = "Tour Finals"
INTERNATIONAL = "International"

CATEGORIES = (
    GRAND_SLAM, MASTERS_1000, TOUR_500, TOUR_250, TOUR_FINALS, INTERNATIONAL,
)

# --- points ---------------------------------------------------------------
#: Points per round *won*, as increments rather than totals: winning a round is
#: worth what reaching the next one adds. Each column sums to the event's face
#: value for a champion -- 2000 at a slam, 1000 at a Masters, and so on -- which
#: is the arithmetic check that the table is right.
ATP_WIN_POINTS: dict[tuple[str, str], float] = {
    # Grand Slam: 50+50+100+200+400+500+700 = 2000
    ("GS", R128): 50, ("GS", R64): 50, ("GS", R32): 100, ("GS", R16): 200,
    ("GS", QF): 400, ("GS", SF): 500, ("GS", F): 700,
    # Masters 1000, 128-bracket (the 96-draw events): 30+20+50+100+200+250+350 = 1000.
    # A seed's first win is R64 after a bye, worth 20 + the 30 bye credit = 50,
    # which puts them on the same 1000 as an unseeded champion.
    ("M1000_128", R128): 30, ("M1000_128", R64): 20, ("M1000_128", R32): 50,
    ("M1000_128", R16): 100, ("M1000_128", QF): 200, ("M1000_128", SF): 250,
    ("M1000_128", F): 350,
    # Masters 1000, 64-bracket (the 56-draw events): 50+50+100+200+250+350 = 1000
    ("M1000_64", R64): 50, ("M1000_64", R32): 50, ("M1000_64", R16): 100,
    ("M1000_64", QF): 200, ("M1000_64", SF): 250, ("M1000_64", F): 350,
    # 500, 32-bracket: 50+50+100+130+170 = 500
    ("A500_32", R32): 50, ("A500_32", R16): 50, ("A500_32", QF): 100,
    ("A500_32", SF): 130, ("A500_32", F): 170,
    # 500, 64-bracket (48-draw): 25+25+50+100+130+170 = 500
    ("A500_64", R64): 25, ("A500_64", R32): 25, ("A500_64", R16): 50,
    ("A500_64", QF): 100, ("A500_64", SF): 130, ("A500_64", F): 170,
    # 250, 32-bracket: 25+25+50+65+85 = 250
    ("A250_32", R32): 25, ("A250_32", R16): 25, ("A250_32", QF): 50,
    ("A250_32", SF): 65, ("A250_32", F): 85,
    # 250, 64-bracket (48-draw): 13+12+25+50+65+85 = 250
    ("A250_64", R64): 13, ("A250_64", R32): 12, ("A250_64", R16): 25,
    ("A250_64", QF): 50, ("A250_64", SF): 65, ("A250_64", F): 85,
    # Tour Finals: three round-robin wins plus the knockout.
    ("FINALS", RR): 200, ("FINALS", SF): 400, ("FINALS", F): 500,
}

#: Team events -- Davis Cup, Billie Jean King Cup, the United Cup -- pay a flat
#: rate per win. Their ties are not knockout rounds in the sense the table
#: assumes, and their draws are not comparable to a tour event's.
INTERNATIONAL_WIN_POINTS = 50.0

#: Round order per tier, which is what makes "the preceding round" meaningful.
TIER_ROUNDS: dict[str, tuple[str, ...]] = {
    "GS": (R128, R64, R32, R16, QF, SF, F),
    "M1000_128": (R128, R64, R32, R16, QF, SF, F),
    "M1000_64": (R64, R32, R16, QF, SF, F),
    "A500_32": (R32, R16, QF, SF, F),
    "A500_64": (R64, R32, R16, QF, SF, F),
    "A250_32": (R32, R16, QF, SF, F),
    "A250_64": (R64, R32, R16, QF, SF, F),
    "FINALS": (RR, SF, F),
}

#: A straight-sets win is worth more the more sets it skipped. Winning
#: best-of-five in three avoids two sets; winning best-of-three in two avoids
#: one, so it pays less.
STRAIGHT_SETS_MULTIPLIER = {3: 1.25, 5: 1.5}
DEFAULT_BEST_OF = 3

#: Only ATP main-draw matches at a slam are best-of-five. The WTA plays
#: best-of-three everywhere, and no other tier plays five on either tour.
BEST_OF_FIVE_TOURS = ("ATP",)
BEST_OF_FIVE_CATEGORIES = (GRAND_SLAM,)

#: Bracket size when the draw size is unknown. 64 is the middle of the three and
#: the most common tour draw, so it is the least wrong default.
DEFAULT_BRACKET = 64

_SET_PATTERN = re.compile(r"^(\d+)-(\d+)(?:\(\d+\))?$")
#: A retirement marker, matched on word boundaries so a player whose name
#: contains the letters is not mistaken for one.
_RETIREMENT_PATTERN = re.compile(r"\bret\b|\bretired\b", re.IGNORECASE)


def effective_bracket(draw_size: float | None) -> int:
    """The bracket a draw of this size plays in: 32, 64 or 128.

    Draws are not powers of two -- 48, 56 and 96 are all common -- and the
    bracket is the next power of two up, with the difference made up by byes.
    """
    if draw_size is None or pd.isna(draw_size):
        return DEFAULT_BRACKET
    size = int(draw_size)
    if size <= 32:
        return 32
    if size <= 64:
        return 64
    return 128


def scoring_tier(category: str | None, draw_size: float | None = None) -> str:
    """Points-table key for a tournament's category and draw size."""
    name = str(category or "").strip()
    if name == GRAND_SLAM:
        return "GS"
    if name == TOUR_FINALS:
        return "FINALS"
    if name == INTERNATIONAL:
        return "INTERNATIONAL"
    bracket = effective_bracket(draw_size)
    if name == MASTERS_1000:
        # Most 1000s are 96-draw since the 2024 reform, so an unknown draw is
        # more likely large than small.
        if draw_size is None or pd.isna(draw_size):
            return "M1000_128"
        return "M1000_128" if bracket >= 128 else "M1000_64"
    if name == TOUR_500:
        return "A500_32" if bracket <= 32 else "A500_64"
    return "A250_32" if bracket <= 32 else "A250_64"


def normalize_round(round_name: str | None) -> str:
    """Canonical round label, or '' for anything unrecognized.

    Unlike the R script this does not fall back to R32. A round it cannot read
    is a round it should not score: guessing produces points for a match nobody
    can trace back to a bracket position.
    """
    text = str(round_name or "").strip().upper()
    if not text:
        return ""
    if text in FINAL_ALIASES:
        return F
    if text in QUALIFYING_ROUNDS:
        return text
    if text in (RR, R128, R64, R32, R16, QF, SF):
        return text
    return ""


def round_points(tier: str, round_name: str) -> float:
    """Points for winning a round at a tier."""
    if tier == "INTERNATIONAL":
        return INTERNATIONAL_WIN_POINTS
    return float(ATP_WIN_POINTS.get((tier, round_name), 0.0))


def previous_round(tier: str, round_name: str) -> str:
    """The round before this one in the tier's sequence, empty if none."""
    sequence = TIER_ROUNDS.get(tier, ())
    if round_name not in sequence:
        return ""
    index = sequence.index(round_name)
    return sequence[index - 1] if index > 0 else ""


def bye_bonus(tier: str, round_name: str, rounds_played: set[str]) -> float:
    """Points for a bye in the round before this one.

    ``rounds_played`` is every round the player has a result in at this
    tournament. No result in the preceding round means they were not in it --
    a bye, whether awarded outright or created by a partial bracket -- and per
    ATP rules that round's points come with the next win.
    """
    prev = previous_round(tier, round_name)
    if not prev or prev in rounds_played:
        return 0.0
    return round_points(tier, prev)


def parse_sets(score: str | None) -> list[tuple[int, int]]:
    """Set scores from a string like '6-3 7-6(4)', ignoring status tokens."""
    if score is None or (isinstance(score, float) and pd.isna(score)):
        return []
    sets: list[tuple[int, int]] = []
    for token in str(score).split():
        # The pattern already allows the tiebreak suffix; stripping parentheses
        # first would leave '7-6(4' and silently drop the set, which turns a
        # lost tiebreak set into a straight-sets win.
        match = _SET_PATTERN.match(token)
        if match:
            sets.append((int(match.group(1)), int(match.group(2))))
    return sets


def is_walkover(score: str | None) -> bool:
    """A win awarded without a match being played."""
    text = str(score or "").lower()
    return "w/o" in text or "walkover" in text or text.strip() in ("wo", "def")


def is_retirement(score: str | None) -> bool:
    """Whether the loser stopped rather than lost."""
    return bool(_RETIREMENT_PATTERN.search(str(score or "")))


def is_complete_set(games: tuple[int, int]) -> bool:
    """Whether a set was actually finished.

    Six games with two clear, or seven in a tiebreak. '3-1' is a set in
    progress, and telling the two apart is what decides whether a retirement
    happened during the first set or after it.
    """
    high, low = max(games), min(games)
    return high >= 6 and (high - low >= 2 or high == 7)


def best_of_for(category: str | None, tour: str | None) -> int:
    """How many sets the match was scheduled over.

    Only ATP main-draw matches at a slam go to five; the WTA plays three
    everywhere and no other tier plays five on either tour.
    """
    is_slam = str(category or "").strip() in BEST_OF_FIVE_CATEGORIES
    is_mens = str(tour or "").upper().startswith(BEST_OF_FIVE_TOURS)
    return 5 if (is_slam and is_mens) else DEFAULT_BEST_OF


def is_straight_sets(score: str | None, winner_first: bool = True) -> bool:
    """Whether the winner dropped no set, and played enough of a match to say.

    A walkover never counts -- there is nothing to be straight about. A
    retirement counts only if no set had been dropped *and* at least one set
    was completed: '6-3 3-1 RET' qualifies and '6-3 4-6 1-0 RET' does not, but
    neither does '3-1 RET', where the loser stopped during the opening set and
    nothing was really won.
    """
    if is_walkover(score):
        return False
    sets = parse_sets(score)
    if not sets:
        return False
    if not winner_first:
        sets = [(b, a) for a, b in sets]
    if any(b > a for a, b in sets):
        return False
    return not (is_retirement(score) and not any(is_complete_set(s) for s in sets))


def straight_sets_multiplier(best_of: float | None) -> float:
    """What a straight-sets win at this format is worth."""
    if best_of is None or pd.isna(best_of):
        best_of = DEFAULT_BEST_OF
    return STRAIGHT_SETS_MULTIPLIER.get(int(best_of), STRAIGHT_SETS_MULTIPLIER[DEFAULT_BEST_OF])


def score_matches(matches: pd.DataFrame) -> pd.DataFrame:
    """Points for every scoring win.

    Expects one row per completed match: ``tournament``, ``category``,
    ``draw_size``, ``round``, ``season``, ``winner``, ``loser``, ``score``, and
    ``tour``. Losses are not represented; a match produces one row.
    """
    if matches is None or matches.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "tournament": resolve_str(matches, ["tournament", "tourney_name", "event"], required=True),
            "tour": resolve_str(matches, ["tour", "tour_type_human", "tour_type"], default=""),
            "category": resolve_str(matches, ["category", "tourney_category"], default=""),
            "season": resolve_num(matches, ["season", "season_year", "year"], required=True).astype(int),
            "draw_size": resolve_num(matches, ["draw_size", "draw"], default=float("nan")),
            "round_raw": resolve_str(matches, ["round", "round_name"], default=""),
            "date": resolve_str(matches, ["date", "match_date"], default=""),
            "winner": resolve_str(matches, ["winner", "winner_name"], required=True),
            "loser": resolve_str(matches, ["loser", "loser_name"], default=""),
            "score": resolve_str(matches, ["score"], default=""),
            "best_of": resolve_num(matches, ["best_of"], default=float("nan")),
        }
    )
    # The feeds that state best_of are believed; the rest are derived from the
    # tier, since only an ATP slam plays five.
    derived = [best_of_for(c, t) for c, t in zip(work["category"], work["tour"])]
    work["best_of"] = work["best_of"].fillna(pd.Series(derived, index=work.index))

    work["tier"] = [
        scoring_tier(c, d) for c, d in zip(work["category"], work["draw_size"])
    ]
    work["round"] = work["round_raw"].map(normalize_round)

    # Qualifying and unreadable rounds are dropped before anything is scored:
    # they must not pad a player's round set either, or a missing qualifying
    # result would read as a main-draw bye.
    scorable = work["round"].isin([RR, R128, R64, R32, R16, QF, SF, F])
    work = work[scorable].copy()
    if work.empty:
        return pd.DataFrame()

    # Which rounds each player has a result in at each tournament. Only wins
    # appear in this feed, so a player's last round is the one they lost -- but
    # the bye test only ever looks at rounds *before* a win, which are wins.
    played: dict[tuple, set[str]] = {}
    for key, group in work.groupby(["season", "tournament", "winner"]):
        played[key] = set(group["round"])

    work["base_points"] = [
        round_points(t, r) for t, r in zip(work["tier"], work["round"])
    ]
    work["bye_points"] = [
        bye_bonus(tier, rnd, played.get((season, tourney, winner), set()))
        for tier, rnd, season, tourney, winner in zip(
            work["tier"], work["round"], work["season"],
            work["tournament"], work["winner"],
        )
    ]
    work["win_points"] = work["base_points"] + work["bye_points"]
    work["is_straight_sets"] = work["score"].map(is_straight_sets)
    work["straight_sets_bonus"] = [
        straight_sets_multiplier(b) if straight else 1.0
        for straight, b in zip(work["is_straight_sets"], work["best_of"])
    ]
    work["match_points"] = work["win_points"] * work["straight_sets_bonus"]
    return work.reset_index(drop=True)


def match_events(matches: pd.DataFrame) -> pd.DataFrame:
    """One dated, scored row per win, for a window-based benchmark.

    The season-total view groups by calendar year; the benchmark for an
    August-to-July league year cannot, because tennis runs continuously and a
    calendar year contains a different slice of the tour than the league year
    does. Both views are aggregations of the same scored matches -- this is the
    one that keeps the date.
    """
    scored = score_matches(matches)
    if scored.empty:
        return pd.DataFrame()

    return pd.DataFrame({
        "player": scored["winner"],
        "date": scored["date"],
        "season": scored["season"],
        "tournament": scored["tournament"],
        "event_points": scored["match_points"],
        "league": scored["tour"].str.upper().str.contains("WTA").map(
            {True: "WTA", False: "ATP"}
        ),
        "role": "Singles",
        "norm_league": norm_league("ATP"),
    })


def score_players(matches: pd.DataFrame) -> pd.DataFrame:
    """Season totals per player."""
    scored = score_matches(matches)
    if scored.empty:
        return pd.DataFrame()

    totals = scored.groupby(["season", "tour", "winner"], as_index=False).agg(
        matches_won=("match_points", "size"),
        straight_set_wins=("is_straight_sets", "sum"),
        titles=("round", lambda s: int((s == F).sum())),
        total_points=("match_points", "sum"),
    )
    totals = totals.rename(columns={"winner": "player"})
    totals["league"] = totals["tour"].str.upper().str.contains("WTA").map(
        {True: "WTA", False: "ATP"}
    )
    totals["role"] = "Singles"
    # Both tours are measured against one distribution: the points table is the
    # same by round, so the pooled field is the comparable one.
    totals["norm_league"] = norm_league("ATP")
    return totals.sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True)
