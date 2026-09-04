"""League configuration.

Everything here is data that the admin dashboard will eventually own. It lives in
code for now so the scoring engine has a single, testable source of truth.

Two manager counts exist and must not be conflated:

* ``LEAGUE_MANAGER_COUNT`` -- how many managers actually play (5 in 2026-27).
* ``BENCHMARK_MANAGER_COUNT`` -- the deliberately generous count used only to size
  the "fantasy relevant" pool the 99th-percentile benchmark is drawn from (15).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: The league's managers: id -> display name. The id is the initials and is
#: what appears in URLs, filenames and badges, where a full name would not
#: fit; the display name is used wherever there is room for it.
MANAGERS: dict[str, str] = {
    "TG": "Tyler",
    "LS": "Luke",
    "SS": "Shelby",
    "JM": "Jake",
    "SM": "Scott",
}


def manager_name(manager_id: str) -> str:
    """A manager's display name, falling back to the id itself.

    The fallback matters during a draft: a roster file can name someone the
    league config has not been told about yet, and showing their id is better
    than showing nothing or refusing to build the page.
    """
    return MANAGERS.get(manager_id, manager_id)


LEAGUE_MANAGER_COUNT = 5
BENCHMARK_MANAGER_COUNT = 15

# Buffer expansion applied on top of Target_N before computing the benchmark.
PLAYER_BUFFER_MULTIPLIER = 1.50
TEAM_BUFFER_MULTIPLIER = 1.33

BENCHMARK_QUANTILE = 0.99


@dataclass(frozen=True)
class SeasonWindow:
    """The scoring window for a league year.

    ``benchmark_cutoff`` is the last date whose data may enter a frozen benchmark;
    anything at or after the season start would leak live results into the scale.
    """

    label: str
    start: date
    end: date
    benchmark_cutoff: date


#: The end date tracks the MLB All-Star Game and so shifts a little each year.
#: The drift is small enough that benchmarks do not need re-basing for it.
SEASON = SeasonWindow(
    label="2026-27",
    start=date(2026, 8, 21),
    end=date(2027, 7, 13),  # MLB All-Star Game
    benchmark_cutoff=date(2026, 8, 20),
)


#: When each league's results start counting, where that is not the league
#: year's own start. Sports do not begin together, and a league year that opens
#: on a fixed date will otherwise either miss a competition already under way or
#: swallow the tail of the previous season.
#:
#: A date *earlier* than ``SEASON.start`` is deliberate and legitimate: La Liga
#: was already three matchdays old when the WHUL year opened, and those results
#: belong to this league year because there is no other one they could belong
#: to. A date *later* excludes something the league considers last season's --
#: tennis starts on the 23rd so the Cincinnati final does not count twice.
LEAGUE_START: dict[str, date] = {
    "ATP": date(2026, 8, 23),
    "WTA": date(2026, 8, 23),
    "Tennis": date(2026, 8, 23),
    "NASCAR": date(2026, 8, 23),
    "PGA": date(2026, 8, 20),
    "Premier League": date(2026, 8, 21),
    "Ligue 1": date(2026, 8, 21),
    "Bundesliga": date(2026, 8, 28),
    "La Liga": date(2026, 8, 15),
    "Serie A": date(2026, 8, 22),
    # ESPN labels Week 1 as August 22 to September 7, so the season's opening
    # weekend is the 22nd, not the 27th. Starting on the 27th would discard
    # real Week 1 results from the games played that first weekend.
    "NCAAF": date(2026, 8, 22),
}


def season_start(league: str, default: date | None = None) -> date:
    """The first day a league's results count toward this league year."""
    return LEAGUE_START.get(league, default or SEASON.start)


@dataclass(frozen=True)
class SlotGroup:
    """One roster category for one asset type.

    ``cap`` is how many may be drafted; ``starters`` is how many count under
    best-ball scoring. ``bench`` is the remainder and never scores directly -- it
    only supplies replacements when a starter stops accruing.
    """

    category: str
    asset_type: str  # "Team" | "Player"
    cap: int
    starters: int
    benchmark_rate: int  # Target_N per benchmark manager
    active: bool = True
    inactive_reason: str = ""

    @property
    def bench(self) -> int:
        return self.cap - self.starters


# --- Team slots: no bench, every slot counts -------------------------------
TEAM_SLOTS: tuple[SlotGroup, ...] = (
    SlotGroup("Club Soccer Top 3", "Team", 4, 4, 4),
    SlotGroup("Club Soccer Other", "Team", 4, 4, 4),
    SlotGroup("NFL", "Team", 2, 2, 2),
    SlotGroup("NBA", "Team", 2, 2, 2),
    SlotGroup("MLB", "Team", 2, 2, 2),
    SlotGroup("NHL", "Team", 2, 2, 2),
    SlotGroup("NCAAF", "Team", 2, 2, 2),
    SlotGroup("NCAAM", "Team", 2, 2, 2),
    SlotGroup("NCAAW", "Team", 2, 2, 2),
    SlotGroup("NCAA Baseball", "Team", 1, 1, 1),
    SlotGroup("NCAA Softball", "Team", 1, 1, 1),
    SlotGroup("Intl Soccer", "Team", 2, 2, 2),
    SlotGroup(
        "WNBA", "Team", 1, 1, 1,
        active=False, inactive_reason="Season nearly over at the 2026-08-21 start",
    ),
    SlotGroup(
        "Olympics", "Team", 2, 2, 2,
        active=False, inactive_reason="No Games before the next draft",
    ),
)

# --- Player slots: NFL/NBA/MLB/NHL carry 2 bench, everything else 1 --------
PLAYER_SLOTS: tuple[SlotGroup, ...] = (
    SlotGroup("Club Soccer Top 3", "Player", 5, 4, 6),
    SlotGroup("Club Soccer Other", "Player", 5, 4, 6),
    SlotGroup("NFL", "Player", 4, 2, 3),
    SlotGroup("NBA", "Player", 4, 2, 3),
    SlotGroup("MLB", "Player", 4, 2, 3),
    SlotGroup("NHL", "Player", 4, 2, 3),
    SlotGroup("PGA", "Player", 3, 2, 3),
    SlotGroup("Tennis", "Player", 3, 2, 3),
    SlotGroup("Motorsports", "Player", 2, 1, 2),
    SlotGroup(
        "WNBA", "Player", 2, 1, 2,
        active=False, inactive_reason="Season nearly over at the 2026-08-21 start",
    ),
)

ALL_SLOTS: tuple[SlotGroup, ...] = TEAM_SLOTS + PLAYER_SLOTS


def active_slots(slots: tuple[SlotGroup, ...] = ALL_SLOTS) -> tuple[SlotGroup, ...]:
    """Slot groups in play this season. Inactive groups stay in the schema as
    placeholders so they can be switched back on without a migration."""
    return tuple(s for s in slots if s.active)


def target_n(group: SlotGroup, managers: int = BENCHMARK_MANAGER_COUNT) -> int:
    """Exact rostered need used to size the benchmark pool.

    Reproduces the Target_N values in All_Analysis.R, which are all exact
    multiples of 15 -- storing the per-manager rate lets the pool scale when the
    benchmark manager count changes.
    """
    return group.benchmark_rate * managers


def buffer_n(group: SlotGroup, managers: int = BENCHMARK_MANAGER_COUNT) -> int:
    """Target_N expanded to capture the fantasy-relevant reach/bench pool."""
    mult = TEAM_BUFFER_MULTIPLIER if group.asset_type == "Team" else PLAYER_BUFFER_MULTIPLIER
    return round(target_n(group, managers) * mult)


# --- League -> draft pool -------------------------------------------------
POOL_MAP_PLAYERS: dict[str, str] = {
    "NFL": "NFL", "NBA": "NBA", "WNBA": "WNBA", "MLB": "MLB", "NHL": "NHL",
    "ATP": "Tennis", "WTA": "Tennis", "Tennis": "Tennis",
    "F1": "Motorsports", "NASCAR": "Motorsports", "Motorsports": "Motorsports",
    "PGA": "PGA",
    "Premier League": "Club Soccer Top 3", "La Liga": "Club Soccer Top 3",
    "Serie A": "Club Soccer Top 3",
    "MLS": "Club Soccer Other", "NWSL": "Club Soccer Other",
    "Ligue 1": "Club Soccer Other", "Bundesliga": "Club Soccer Other",
}

POOL_MAP_TEAMS: dict[str, str] = {
    "NFL": "NFL", "NBA": "NBA", "WNBA": "WNBA", "MLB": "MLB", "NHL": "NHL",
    "NCAAF": "NCAAF", "NCAAM": "NCAAM", "NCAAW": "NCAAW",
    "NCAABaseball": "NCAA Baseball", "NCAA Baseball": "NCAA Baseball",
    "NCAASoftball": "NCAA Softball", "NCAA Softball": "NCAA Softball",
    "Olympics": "Olympics",
    "Men's Soccer": "Intl Soccer", "Women's Soccer": "Intl Soccer",
    "Men's Intl Soccer": "Intl Soccer", "Women's Intl Soccer": "Intl Soccer",
    "Premier League": "Club Soccer Top 3", "La Liga": "Club Soccer Top 3",
    "Serie A": "Club Soccer Top 3",
    "MLS": "Club Soccer Other", "NWSL": "Club Soccer Other",
    "Ligue 1": "Club Soccer Other", "Bundesliga": "Club Soccer Other",
}

# Soccer pools whose players may transfer across the pool boundary mid-season.
CROSS_POOL_SOCCER = ("Club Soccer Top 3", "Club Soccer Other")

#: Roster categories that are open to more than one competition, and which
#: competitions those are. Every league is normalized against its own history --
#: ATP against ATP, WTA against WTA, F1 against F1, NASCAR against NASCAR -- but
#: a roster slot does not always say which one an asset plays in: the draft
#: sheet records twelve players as "Tennis" and three as "Motorsports", because
#: that is the category they were drafted into.
#:
#: So this is not a normalization rule. It is what a coverage check needs to
#: answer "can this version score that pick", and the answer is only yes when
#: *every* competition the category admits has a benchmark -- there is no way to
#: tell from the roster whether a "Tennis" pick is on the ATP or the WTA tour.
CATEGORY_COMPETITIONS: dict[str, tuple[str, ...]] = {
    "Tennis": ("ATP", "WTA"),
    "Motorsports": ("F1", "NASCAR"),
}


def competitions_for(league: str) -> tuple[str, ...]:
    """The competitions a rostered league covers, which is usually just itself."""
    return CATEGORY_COMPETITIONS.get(league, (league,))
