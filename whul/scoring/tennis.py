"""Tennis scoring -- port of Tennis_Players.R.

Only wins score. A win is worth the ATP ranking points for the round it was won
in, on a table that varies by tournament tier and by how large the draw was, and
a win in straight sets is worth half again as much.

**Byes are paid on the first win, not on the bye itself.** A seed who receives a
first-round bye earns that round's points only by winning their opening match --
the documented league convention. The R script implements this by finding each
player's first win in a tournament and crediting the round before the one they
won in; a player who entered in the first round has no earlier round, so the
credit is zero and nothing is double-counted.

ATP and WTA share one roster category and one benchmark. Their points tables are
identical by round, so the pooled distribution is the meaningful one, and
``score_players`` marks both tours with ``norm_league = "Tennis"``.

Tennis is one of the three individual sports whose benchmark is drawn over the
league year's actual calendar window rather than a season -- see the project
plan's normalization section.
"""

from __future__ import annotations

import re

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str

#: Points by ``TIER_ROUND``. The tier encodes both the tournament's status and
#: its draw size, because a 64-draw Masters pays less for the early rounds than
#: a 128-draw one does: the same round is one win deeper into the field.
ATP_POINTS: dict[str, float] = {
    "GS_R128": 50, "GS_R64": 50, "GS_R32": 100, "GS_R16": 200,
    "GS_QF": 400, "GS_SF": 500, "GS_F": 700,
    "M1000_128_R128": 30, "M1000_128_R64": 20, "M1000_128_R32": 50,
    "M1000_128_R16": 100, "M1000_128_QF": 200, "M1000_128_SF": 250,
    "M1000_128_F": 350,
    "M1000_64_R64": 50, "M1000_64_R32": 50, "M1000_64_R16": 100,
    "M1000_64_QF": 200, "M1000_64_SF": 250, "M1000_64_F": 350,
    "A500_32_R32": 50, "A500_32_R16": 50, "A500_32_QF": 100,
    "A500_32_SF": 130, "A500_32_F": 170,
    "A500_64_R64": 25, "A500_64_R32": 25, "A500_64_R16": 50,
    "A500_64_QF": 100, "A500_64_SF": 130, "A500_64_F": 170,
    "A250_32_R32": 25, "A250_32_R16": 25, "A250_32_QF": 50,
    "A250_32_SF": 65, "A250_32_F": 85,
    "A250_64_R64": 13, "A250_64_R32": 12, "A250_64_R16": 25,
    "A250_64_QF": 50, "A250_64_SF": 65, "A250_64_F": 85,
    "FINALS_RR": 200, "FINALS_SF": 400, "FINALS_F": 500,
}

#: Round order per tier, used to find the round a bye covered.
TIER_ROUNDS: dict[str, tuple[str, ...]] = {
    "GS": ("R128", "R64", "R32", "R16", "QF", "SF", "F"),
    "M1000_128": ("R128", "R64", "R32", "R16", "QF", "SF", "F"),
    "M1000_64": ("R64", "R32", "R16", "QF", "SF", "F"),
    "A500_32": ("R32", "R16", "QF", "SF", "F"),
    "A500_64": ("R64", "R32", "R16", "QF", "SF", "F"),
    "A250_32": ("R32", "R16", "QF", "SF", "F"),
    "A250_64": ("R64", "R32", "R16", "QF", "SF", "F"),
}

#: Team events (United Cup, Davis Cup, BJK Cup) pay a flat rate per win: they
#: have no comparable draw structure, and their rounds are not knockout rounds
#: in the sense the table assumes.
INTERNATIONAL_WIN_POINTS = 50.0

STRAIGHT_SETS_MULTIPLIER = 1.5

#: Draw-size thresholds, expressed in total matches played at the tournament,
#: carried over from Tennis_Players.R unchanged.
#:
#: These sit well below the real draw boundaries: a 128-draw plays 127 matches,
#: a 96-draw 95, a 64-draw 63, a 56-draw 55, a 32-draw 31. At a cutoff of 50,
#: both Masters shapes -- the 96-draw events and the 56-draw ones -- land on the
#: ``M1000_128`` table, and at 30 a 32-draw 500 lands on ``A500_64``. The
#: smaller tables are effectively unreachable on complete tour data.
#:
#: They are kept as written because the benchmark and the live season are scored
#: by the same function: a shift in the table moves both the numerator and the
#: 99th percentile, so the 0-100 scale largely absorbs it, whereas changing the
#: cutoffs would silently restate every historical tennis score. Raising them to
#: 75 and 40 would separate the real shapes; that is a scoring decision, not a
#: bug fix, and it belongs in the project plan's open items.
MASTERS_LARGE_DRAW_MATCHES = 50
TOUR_LARGE_DRAW_MATCHES = 30
DEFAULT_MATCH_COUNT = 32

GRAND_SLAM_PATTERN = re.compile(
    r"australian open|roland garros|french open|wimbledon|us open|grand slam",
    re.IGNORECASE,
)
INTERNATIONAL_PATTERN = re.compile(
    r"united cup|davis cup|billie jean king|bjk cup", re.IGNORECASE
)
FINALS_PATTERN = re.compile(r"atp finals|wta finals", re.IGNORECASE)
MASTERS_PATTERN = re.compile(
    r"indian wells|miami|madrid|rome|shanghai|cincinnati|canada|toronto|"
    r"montreal|paris|monte carlo|1000",
    re.IGNORECASE,
)
TOUR_500_PATTERN = re.compile(
    r"500|halle|vienna|beijing|tokyo|washington|queen|basel|rotterdam|dubai|"
    r"acapulco|hamburg|barcelona",
    re.IGNORECASE,
)

#: Everything below the main tour is out: those draws are not comparable, and
#: including them would let a player pad a season on the second tier.
EXCLUDED_TOURNAMENT_PATTERN = re.compile(
    r"qualification|qualifier|challenger|chall|itf|exhibition|exhib", re.IGNORECASE
)
EXCLUDED_TOUR_PATTERN = re.compile(
    r"challenger|chall|exhibition|exhib", re.IGNORECASE
)

#: Round labels differ by feed -- '1/16', 'Round of 32' and 'R32' all mean the
#: same thing. Matched in order, first hit wins, as the R ``case_when`` does.
ROUND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("R128", re.compile(r"1/64|r128|round 1|128", re.IGNORECASE)),
    ("R64", re.compile(r"1/32|r64|round 2|64", re.IGNORECASE)),
    ("R32", re.compile(r"1/16|r32|round 3|32", re.IGNORECASE)),
    ("R16", re.compile(r"1/8|r16|round 4|16", re.IGNORECASE)),
    ("QF", re.compile(r"quarter|qf", re.IGNORECASE)),
    ("SF", re.compile(r"semi|sf", re.IGNORECASE)),
    ("F", re.compile(r"final", re.IGNORECASE)),
    ("RR", re.compile(r"group|round robin|rr", re.IGNORECASE)),
)
DEFAULT_ROUND = "R32"


def classify_tier(tournament: str | None, match_count: float = DEFAULT_MATCH_COUNT) -> str:
    """Tier key for a tournament, given how many matches it played."""
    name = str(tournament or "")
    if GRAND_SLAM_PATTERN.search(name):
        return "GS"
    if INTERNATIONAL_PATTERN.search(name):
        return "INTERNATIONAL"
    if FINALS_PATTERN.search(name):
        return "FINALS"
    count = DEFAULT_MATCH_COUNT if pd.isna(match_count) else float(match_count)
    if MASTERS_PATTERN.search(name):
        return "M1000_128" if count >= MASTERS_LARGE_DRAW_MATCHES else "M1000_64"
    if TOUR_500_PATTERN.search(name):
        return "A500_64" if count >= TOUR_LARGE_DRAW_MATCHES else "A500_32"
    return "A250_64" if count >= TOUR_LARGE_DRAW_MATCHES else "A250_32"


def normalize_round(round_str: str | None) -> str:
    """Canonical round label, defaulting to R32 for anything unrecognized."""
    text = str(round_str or "")
    for label, pattern in ROUND_PATTERNS:
        if pattern.search(text):
            return label
    return DEFAULT_ROUND


def previous_round(tier: str, round_clean: str) -> str:
    """The round before this one in the tier's sequence, empty if none.

    Empty for the opening round, and for tiers with no knockout sequence
    (INTERNATIONAL, FINALS), which is what makes the bye credit self-limiting.
    """
    sequence = TIER_ROUNDS.get(tier)
    if not sequence or round_clean not in sequence:
        return ""
    index = sequence.index(round_clean)
    return sequence[index - 1] if index > 0 else ""


def round_points(tier: str, round_clean: str) -> float:
    """Points for winning a round at a tier."""
    if tier == "INTERNATIONAL":
        return INTERNATIONAL_WIN_POINTS
    return float(ATP_POINTS.get(f"{tier}_{round_clean}", 0.0))


def bye_bonus(tier: str, round_clean: str) -> float:
    """Points for the round a bye covered, credited on the first win."""
    prev = previous_round(tier, round_clean)
    if not prev:
        return 0.0
    return float(ATP_POINTS.get(f"{tier}_{prev}", 0.0))


def eligible_matches(matches: pd.DataFrame) -> pd.DataFrame:
    """Drop unfinished, cancelled, qualifying and below-tour matches."""
    if matches is None or matches.empty:
        return pd.DataFrame()

    status = resolve_str(matches, ["status"], default="FINISHED").str.upper()
    status_extra = resolve_str(matches, ["status_extra"], default="").str.upper()
    round_raw = resolve_str(matches, ["round", "round_name"], default="")
    tournament = resolve_str(matches, ["tournament", "tourney_name", "event"], default="")
    tour = resolve_str(matches, ["tour_type_human", "tour", "tour_type"], default="")

    keep = (
        (status == "FINISHED")
        & (status_extra != "CANCELED")
        & (round_raw.str.strip().str.lower() != "qualifier")
        & ~tournament.str.contains(EXCLUDED_TOURNAMENT_PATTERN, na=False)
        & ~tour.str.contains(EXCLUDED_TOUR_PATTERN, na=False)
    )
    return matches[keep.fillna(False).to_numpy()].copy()


def score_matches(matches: pd.DataFrame) -> pd.DataFrame:
    """Points for every scoring win.

    Expects one row per match with ``tournament``, ``round``, ``season_year``,
    ``home_name``/``away_name``, ``winner_code`` (1 = home), the two set scores,
    and ``date_timestamp`` for ordering. Losses produce no row.
    """
    played = eligible_matches(matches)
    if played.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "tournament": resolve_str(played, ["tournament", "tourney_name", "event"], default=""),
            "tour": resolve_str(played, ["tour_type_human", "tour", "tour_type"], default=""),
            "season": resolve_num(played, ["season_year", "season", "year"], required=True).astype(int),
            "round_raw": resolve_str(played, ["round", "round_name"], default=""),
            "timestamp": resolve_num(played, ["date_timestamp", "timestamp", "start_time"]),
            "home": resolve_str(played, ["home_name", "home", "player_home"], required=True),
            "away": resolve_str(played, ["away_name", "away", "player_away"], required=True),
            "winner_code": resolve_num(played, ["winner_code", "winner"]),
            "home_sets": resolve_num(played, ["home_set_score", "home_sets"]),
            "away_sets": resolve_num(played, ["away_set_score", "away_sets"]),
        }
    )

    # Draw size is inferred from the tournament's own match count, so a feed that
    # names events inconsistently still lands on the right points table.
    counts = work.groupby("tournament")["round_raw"].transform("size")
    work["total_matches"] = counts
    work["tier"] = [
        classify_tier(name, n) for name, n in zip(work["tournament"], work["total_matches"])
    ]
    work["round_clean"] = work["round_raw"].map(normalize_round)

    home_won = work["winner_code"] == 1
    work["winner"] = work["home"].where(home_won, work["away"])
    work["loser"] = work["away"].where(home_won, work["home"])
    work["winner_sets"] = work["home_sets"].where(home_won, work["away_sets"])
    work["loser_sets"] = work["away_sets"].where(home_won, work["home_sets"])
    work["is_straight_sets"] = (work["loser_sets"] == 0) & (work["winner_sets"] > 0)

    # A knockout loss ends a player's tournament, so their first win is their
    # first match -- which is exactly the match a bye would have preceded.
    work = work.sort_values("timestamp", kind="stable")
    work["win_number"] = (
        work.groupby(["season", "tournament", "winner"]).cumcount() + 1
    )
    work["is_first_win"] = work["win_number"] == 1

    work["base_points"] = [
        round_points(t, r) for t, r in zip(work["tier"], work["round_clean"])
    ]
    work["bye_points"] = [
        bye_bonus(t, r) for t, r in zip(work["tier"], work["round_clean"])
    ]
    work["bye_points"] = work["bye_points"].where(work["is_first_win"], 0.0)
    work["win_points"] = work["base_points"] + work["bye_points"]
    work["match_points"] = work["win_points"] * work["is_straight_sets"].map(
        {True: STRAIGHT_SETS_MULTIPLIER, False: 1.0}
    )
    return work.reset_index(drop=True)


def score_players(matches: pd.DataFrame) -> pd.DataFrame:
    """Season totals per player."""
    scored = score_matches(matches)
    if scored.empty:
        return pd.DataFrame()

    totals = scored.groupby(["season", "tour", "winner"], as_index=False).agg(
        matches_won=("match_points", "size"),
        straight_set_wins=("is_straight_sets", "sum"),
        titles=("round_clean", lambda s: int((s == "F").sum())),
        total_points=("match_points", "sum"),
    )
    totals = totals.rename(columns={"winner": "player"})
    totals["league"] = totals["tour"].str.upper().str.contains("WTA").map(
        {True: "WTA", False: "ATP"}
    )
    totals["role"] = "Singles"
    # Both tours are measured against one distribution: the points tables are
    # identical by round, so a pooled benchmark is the comparable one.
    totals["norm_league"] = "Tennis"
    return totals.sort_values(
        ["season", "total_points"], ascending=[True, False]
    ).reset_index(drop=True)
