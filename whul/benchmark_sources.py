"""Where each league's benchmark history comes from.

``whul.benchmarks`` knows how a benchmark is arrived at; this knows which
function to call to get five seasons of one sport. The split matters because
the method must stay identical across sports -- the whole point of the 0-100
scale is that a 92 in tennis means what a 92 in the NFL means -- while the
sources differ wildly in shape, cost and reliability.

Every entry is lazy. Importing this must not import twenty source modules or
touch the network, so each source is a factory that binds its loader only when
that league is actually asked for.

``league`` is the key ``whul.scoring.schedule`` consults for excluded seasons
and for the earliest usable one. It is not always a benchmark group: tennis is
registered under ``Tennis`` because ATP and WTA share a calendar and so share
their COVID exclusions, but one pull produces two benchmarks, one per tour,
because each tour is normalized against its own history. ``produces`` names
those groups where they differ from ``league``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass(frozen=True)
class Source:
    """One benchmarkable slice of one league."""

    key: str
    league: str
    asset_type: str
    build: Callable[[], tuple[Callable, Callable]]
    #: League key in ``SCHEDULE_CHANGES`` whose schedule lengthening should lift
    #: these benchmarks. Set where history is shorter than the season being
    #: scored, so an 82-game past does not understate an 84-game present.
    scale_for: str | None = None
    #: The benchmark groups one pull produces, when they are not just
    #: ``league``. A tennis pull scores ATP and WTA in one pass and each is
    #: normalized against itself, so one source yields two groups.
    produces: tuple[str, ...] = ()
    #: Where the *current* season comes from, when that is not where the
    #: history comes from. Tennis history is a static snapshot and the live feed
    #: is a rolling fortnight; neither can do the other's job.
    live: Callable[[], tuple[Callable, Callable]] | None = None
    #: True for the sports that run continuously, whose benchmark is drawn over
    #: the league year's own August-to-July window rather than over calendar
    #: seasons (PROJECT_PLAN 2.3). Their ``build`` returns an event-level scorer
    #: -- one dated row per match, tournament or race -- instead of a season one.
    windowed: bool = False
    #: Rough confidence in the source, shown by ``benchmarks list`` so the
    #: easiest leagues can be frozen first and the shaky ones chased separately.
    reliability: str = "unverified"
    note: str = ""


def _nfl_players():
    from whul.scoring import nfl
    from whul.sources import nflverse

    return (
        lambda seasons: nflverse.load_player_stats(seasons),
        lambda raw: nfl.score_players(raw, postseason=False),
    )


def _nfl_teams():
    from whul.scoring import nfl
    from whul.sources import nflverse

    # Two frames in, one out. The second is held in the closure rather than
    # stapled onto the first: the load/score contract is one frame wide, and
    # widening it for the three leagues that need it would complicate every
    # league that does not.
    held: dict[str, pd.DataFrame] = {}

    def load(seasons):
        held["teams"] = nflverse.load_teams(seasons)
        return nflverse.load_schedules(seasons)

    return load, lambda schedules: nfl.score_teams(schedules, held["teams"])


def _mlb_players():
    from whul.scoring import mlb
    from whul.sources import mlb as source

    def load(seasons):
        batters = source.load_batters(seasons).assign(_phase="bat")
        pitchers = source.load_pitchers(seasons).assign(_phase="pit")
        return pd.concat([batters, pitchers], ignore_index=True)

    def score(raw):
        return mlb.score_players(raw[raw["_phase"] == "bat"], raw[raw["_phase"] == "pit"])

    return load, score


def _mlb_teams():
    from whul.scoring import mlb
    from whul.sources import mlb as source

    # The contract engine pairs consecutive seasons, so each scored season needs
    # the one after it to exist in the frame.
    return (
        lambda seasons: source.load_schedule(sorted(set(seasons) | {max(seasons) + 1})),
        mlb.score_teams,
    )


def _nba_players():
    from whul.scoring import nba
    from whul.sources import espn

    return (
        lambda seasons: espn.load_nba_player_box(seasons),
        lambda raw: nba.score_players(raw, postseason=False),
    )


def _nba_teams():
    from whul.scoring import nba
    from whul.sources import hoopr

    return lambda seasons: hoopr.load_schedule(seasons), nba.score_teams


def _nhl_players():
    from whul.scoring import nhl
    from whul.sources import nhl as source

    return (
        lambda seasons: source.load_skaters(seasons, source.GAME_TYPE_REGULAR),
        nhl.score_skaters,
    )


def _nhl_teams():
    from whul.scoring import nhl
    from whul.sources import nhl as source

    held: dict[str, pd.DataFrame] = {}

    def load(seasons):
        held["playoffs"] = source.load_teams(seasons, source.GAME_TYPE_PLAYOFFS)
        return source.load_teams(seasons, source.GAME_TYPE_REGULAR)

    return load, lambda regular: nhl.score_teams(regular, held["playoffs"])


def _ncaa(key: str, category: str):
    def build():
        from whul.scoring.ncaa import SCORERS
        from whul.sources import ncaa_api

        def score(raw):
            eligible = set(raw["home_team"]) | set(raw["away_team"])
            return SCORERS[category](raw, eligible)

        return lambda seasons: ncaa_api.load_team_results(key, seasons), score

    return build


def _soccer(key: str, category: str):
    def build():
        from whul.scoring import soccer
        from whul.sources import espn

        def load(seasons):
            matches = espn.load_soccer_matches(key, seasons)
            if not matches.empty:
                matches["league"] = category
            return matches

        return load, soccer.score_teams

    return build


def _pga_players():
    from whul.scoring import golf
    from whul.sources import espn_individual

    return lambda seasons: espn_individual.load_results("pga", seasons), golf.score_events


def _motorsports_players():
    from whul.scoring import motorsport
    from whul.sources import espn_individual, jolpica

    held: dict[str, pd.DataFrame] = {}

    def load(seasons):
        held["f1"] = jolpica.load_results(seasons)
        return espn_individual.load_results("nascar", seasons)

    return load, lambda nascar: motorsport.race_events(nascar, held["f1"])


def _tennis_live():
    """The current tennis season, from the app's database and today's feed.

    Three vintages of the same data. The static snapshot ends in February; the
    tennis2026 app's own database carries on from there, because its scrapers
    keep writing; and the Flashscore feed covers the last seven days, which is
    all it serves. The database is the one that closes the gap -- the feed alone
    forgets a week every week.
    """
    from whul.scoring import tennis
    from whul.sources import flashscore, tennis2026

    def load(_years):
        frames = []
        try:
            frames.append(tennis2026.load_matches())
        except FileNotFoundError as exc:
            # Not fatal: the feed still covers the last week. But it is the
            # difference between the season and the last seven days of it.
            print(f"  tennis2026 database unavailable ({exc.args[0].splitlines()[0]})",
                  flush=True)
        frames.append(flashscore.load_matches())
        frames = [f for f in frames if f is not None and not f.empty]
        if not frames:
            return pd.DataFrame()
        both = pd.concat(frames, ignore_index=True)
        # The two overlap over the last week; the same win must not be paid
        # twice, and either copy will do.
        return both.drop_duplicates(
            subset=["season", "tournament", "round", "winner"], keep="first"
        )

    return load, tennis.match_events


def _tennis_players():
    """History comes from the snapshot; the live feed reaches back a fortnight.

    The snapshot is the only surviving copy of the Sackmann archive, and it
    resolves each tournament's category through the calendar rather than
    guessing from the field, so a benchmark built from it is built from the
    same tier definitions the season will be scored with.
    """
    from whul.scoring import tennis
    from whul.sources import snapshot

    return lambda seasons: snapshot.load_matches(seasons), tennis.match_events


NCAA_CATEGORIES = {
    "ncaaf": "NCAAF", "ncaam": "NCAAM", "ncaaw": "NCAAW",
    "ncaabaseball": "NCAA Baseball", "ncaasoftball": "NCAA Softball",
}

SOCCER_CATEGORIES = {
    "epl": "Premier League", "laliga": "La Liga", "seriea": "Serie A",
    "bundesliga": "Bundesliga", "ligue1": "Ligue 1", "mls": "MLS", "nwsl": "NWSL",
}


def _register(*sources: Source) -> dict[str, Source]:
    return {s.key: s for s in sources}


SOURCES: dict[str, Source] = _register(
    Source("nfl", "NFL", "Player", _nfl_players, reliability="verified",
           note="nflverse release parquet; the only source reachable without a proxy"),
    Source("nfl-teams", "NFL", "Team", _nfl_teams, reliability="verified"),
    Source("mlb", "MLB", "Player", _mlb_players,
           note="FanGraphs leaderboards; season aggregates, no phase split"),
    Source("mlb-teams", "MLB", "Team", _mlb_teams),
    Source("nba", "NBA", "Player", _nba_players,
           note="ESPN box scores, one date at a time -- slow to backfill"),
    Source("nba-teams", "NBA", "Team", _nba_teams,
           note="hoopR archive stops at 2023"),
    Source("nhl", "NHL", "Player", _nhl_players, scale_for="NHL",
           note="82-game history lifted to the 84-game 2026-27 season"),
    Source("nhl-teams", "NHL", "Team", _nhl_teams, scale_for="NHL"),
    Source("pga", "PGA", "Player", _pga_players, windowed=True),
    Source("motorsports", "Motorsports", "Player", _motorsports_players, windowed=True,
           produces=("F1", "NASCAR"),
           note="one pull, two benchmarks -- each series against itself"),
    Source("tennis", "Tennis", "Player", _tennis_players, live=_tennis_live,
           windowed=True, produces=("ATP", "WTA"),
           note="one pull, two benchmarks; the 2022-23 window is the earliest"),
    *[
        Source(key, category, "Team", _ncaa(key, category))
        for key, category in NCAA_CATEGORIES.items()
    ],
    *[
        Source(key, category, "Team", _soccer(key, category))
        for key, category in SOCCER_CATEGORIES.items()
    ],
)

#: Run in this order. Cheap, verified sources first, so a failure late in the
#: list still leaves a reviewable set of the leagues that did work.
ORDER = [
    "nfl", "nfl-teams", "tennis", "pga", "motorsports",
    "nhl", "nhl-teams", "mlb", "mlb-teams", "nba", "nba-teams",
    *NCAA_CATEGORIES, *SOCCER_CATEGORIES,
]


def resolve(names: list[str] | None) -> list[Source]:
    """Sources for the given keys, in run order. ``None`` means all of them."""
    if not names:
        return [SOURCES[key] for key in ORDER if key in SOURCES]
    unknown = sorted(set(names) - set(SOURCES))
    if unknown:
        raise KeyError(
            f"no benchmark source for {unknown}; known keys: {', '.join(sorted(SOURCES))}"
        )
    ranked = {key: i for i, key in enumerate(ORDER)}
    return sorted((SOURCES[n] for n in dict.fromkeys(names)), key=lambda s: ranked.get(s.key, 99))
