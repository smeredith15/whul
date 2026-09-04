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
from datetime import date
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
    #: Which season number to ask a source for on a given day, where that is
    #: not simply the calendar year. Set for the feeds that number a season by
    #: the year it ends in.
    seasons_for: Callable[[date], list[int]] | None = None
    #: True when the live loader takes the rostered names as a second argument.
    #: A team league is far cheaper and far more complete pulled team by team
    #: than by walking dates -- eight requests instead of a season of them, and
    #: a team's own schedule cannot be short of its own games.
    roster_scoped: bool = False
    #: Where the *current* season comes from, when that is not where the
    #: history comes from. Tennis history is a static snapshot and the live feed
    #: is a rolling fortnight; neither can do the other's job.
    live: Callable[[], tuple[Callable, Callable]] | None = None
    #: Run over the scored, normalized rows before they are recorded, for a
    #: scorer that emits several rows per asset on purpose. MLB scores a player
    #: once as a batter and once as a pitcher -- the two are normalized against
    #: different benchmarks and only comparable afterwards -- and this folds
    #: them into the one row the standings hold. Its presence is also what tells
    #: the resolver that two rows for one name are the design rather than a
    #: collision.
    post_normalize: Callable | None = None
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


def _mlb_two_way(scored):
    """Fold a two-way player's batting and pitching rows into one.

    Only after normalization: raw batting and pitching points are not
    comparable, so the primary role is whichever scored higher on the 0-100
    scale and the secondary contributes half.
    """
    from whul.scoring import mlb

    return mlb.combine_two_way(scored)


def _mlb_teams():
    from whul.scoring import mlb
    from whul.sources import mlb as source

    # The contract engine pairs consecutive seasons, so each scored season needs
    # the one after it to exist in the frame.
    return (
        lambda seasons: source.load_schedule(sorted(set(seasons) | {max(seasons) + 1})),
        mlb.score_teams,
    )


def _mlb_teams_live():
    """The contract year that is running, scored on the half that has been played.

    The 2026-27 contract year is post-break 2026 plus pre-break 2027. Asking for
    both and joining them the way a benchmark does drops every team, because
    nobody has played 2027 -- which the report then states as "no results yet
    for this season", in September, mid-pennant-race.
    """
    from whul.scoring import mlb
    from whul.sources import mlb as source

    return (
        lambda seasons: source.load_schedule(seasons),
        lambda raw: mlb.score_teams(raw, partial=True),
    )


def _nba_players():
    from whul.scoring import nba
    from whul.sources import espn

    return (
        lambda seasons: espn.load_nba_player_box(seasons),
        lambda raw: nba.score_players(raw, postseason=False),
    )


def _nba_teams():
    """Results from ESPN, not hoopR.

    The hoopR archive stops at 2023, so a five-season pull reaching 2024 and
    2025 raised on a missing file and lost every NBA team -- which is why
    coverage kept saying to run a command that could not work. ESPN's
    scoreboard carries the same columns the scorer resolves, including the
    season type that separates the regular season from the play-in, the
    playoffs and the In-Season Tournament.
    """
    from whul.scoring import nba
    from whul.sources import espn

    return lambda seasons: espn.load_team_results("nba", seasons), nba.score_teams


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


def _ncaa_score(category: str):
    from whul.scoring.ncaa import SCORERS

    def score(raw):
        # Whatever the feed returned is the division: the NCAA API states it in
        # the URL and ESPN is asked for it by group, so there is nothing here to
        # filter out that the request did not already exclude.
        eligible = set(raw["home_team"]) | set(raw["away_team"])
        return SCORERS[category](raw, eligible)

    return score


def _ncaa(key: str, category: str):
    def build():
        from whul.sources import ncaa_api

        return (
            lambda seasons: ncaa_api.load_team_results(key, seasons),
            _ncaa_score(category),
        )

    return build


def _ncaa_live(key: str, category: str):
    """The rostered teams' own schedules, from ESPN.

    Neither of the obvious sources works for a season in progress. The NCAA API
    serves fixtures without results -- every 2026 game comes back not completed
    with no score, while 2025 comes back final -- and that is a limit rather
    than a lag: the same date still had no scores a week later. ESPN's
    scoreboard has the results but caps at twenty-five events a request and
    ignores both ``limit`` and ``page``, so it returns the featured games; the
    big programs appear and a smaller fixture does not, which is the shape of
    mistake that scores a team short without saying so.

    A team's own schedule has neither problem. Eight rostered teams is eight
    requests, and no cap can hide a team's own game from it.
    """
    def build():
        from whul.sources import espn

        return (
            lambda seasons, names: espn.load_rostered_schedules(key, seasons, names),
            _ncaa_score(category),
        )

    return build


def _soccer_players():
    """Club soccer players, from FBref's season stats.

    One pull covers six leagues and each is normalized against itself, the way
    a Premier League pick is measured against the Premier League rather than
    against a pooled European field. The scorer already reads FBref's own
    column names, so nothing is translated between them.
    """
    from whul.scoring import soccer
    from whul.sources import fbref

    return lambda seasons: fbref.load_players(seasons), soccer.score_players


def _soccer(key: str, category: str):
    """A club's league, cup and European matches, gathered into one total.

    Reading every competition is what gives the tiers meaning -- restricted to
    league fixtures, every win would be worth three points and the Champions
    League premium would never appear. But a competition's scoreboard returns
    *every* match in it, not only the ones this league's clubs played, so the
    rows have to be filtered back to the league's own clubs. Without that the
    Premier League pool was 213 clubs a season instead of 20, with Real Madrid
    and every lower-division cup opponent labelled Premier League.
    """
    def build():
        from whul.scoring import soccer
        from whul.sources import espn

        def load(seasons):
            matches = espn.load_soccer_matches(key, seasons)
            if matches.empty:
                return matches
            own = espn.load_eligible_teams(key)
            if own:
                matches = matches[matches["team"].isin(own)]
            else:
                # Better to say so than to quietly benchmark against Europe.
                print(
                    f"  {key}: could not read the league's own clubs; the pool "
                    f"will include every opponent it met",
                    flush=True,
                )
            return matches.assign(league=category)

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

    # Losses count here and not in the benchmark: a rostered player who lost
    # their opening match has played, and the profile should say so rather than
    # leave them looking absent. The row is worth nothing, so no total moves.
    return load, lambda matches: tennis.match_events(matches, losses=True)


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


def _espn_seasons(key: str):
    """This feed numbers some seasons by the year they end in."""
    def seasons(day: date) -> list[int]:
        from whul.sources import espn

        return [espn.season_label(key, day)]

    return seasons


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
           post_normalize=_mlb_two_way,
           note="FanGraphs leaderboards; one row per player-role, folded after "
                "normalization by the two-way rule"),
    Source("mlb-teams", "MLB", "Team", _mlb_teams, live=_mlb_teams_live,
           note="a live contract year is scored on the half already played"),
    Source("nba", "NBA", "Player", _nba_players,
           note="ESPN box scores, one date at a time -- slow to backfill"),
    Source("nba-teams", "NBA", "Team", _nba_teams,
           seasons_for=_espn_seasons("nba"),
           note="ESPN scoreboard; hoopR's archive stops at 2023"),
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
        Source(key, category, "Team", _ncaa(key, category),
               live=_ncaa_live(key, category), roster_scoped=True,
               seasons_for=_espn_seasons(key))
        for key, category in NCAA_CATEGORIES.items()
    ],
    *[
        Source(key, category, "Team", _soccer(key, category),
               seasons_for=_espn_seasons(key))
        for key, category in SOCCER_CATEGORIES.items()
    ],
    Source("soccer-players", "Club Soccer", "Player", _soccer_players,
           produces=("Premier League", "La Liga", "Serie A", "Bundesliga",
                     "Ligue 1", "MLS"),
           seasons_for=_espn_seasons("epl"),
           note="FBref Big 5 in one request per season, plus MLS; "
                "six benchmarks, each league against itself"),
)

#: Run in this order. Cheap, verified sources first, so a failure late in the
#: list still leaves a reviewable set of the leagues that did work.
ORDER = [
    "nfl", "nfl-teams", "tennis", "pga", "motorsports",
    "nhl", "nhl-teams", "mlb", "mlb-teams", "nba", "nba-teams",
    "soccer-players",
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
