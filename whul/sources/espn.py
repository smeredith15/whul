"""ESPN site API.

The sportsdataverse packages (hoopR, wehoop) wrap this API; their data
repositories are archived and stop at season 2023, so live scoring has to come
from ESPN directly. One adapter serves NBA, WNBA and the NCAA leagues.

    scoreboard: /apis/site/v2/sports/{sport}/{league}/scoreboard?dates=YYYYMMDD
    boxscore:   /apis/site/v2/sports/{sport}/{league}/summary?event={id}

UNVERIFIED: this module could not be exercised where it was written -- ESPN is
blocked by that environment's egress policy. Run ``probe()`` (or
``python -m whul.cli probe nba``) from a machine with access before trusting it;
the probe reports exactly which stage fails.

Fetching a season means walking its dates, so a backfill is slow (thousands of
requests) while a daily update is cheap (one date). ``load_nba_player_box``
caches per-date responses under ``data/cache`` so a re-run costs nothing.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from whul.sources import season_window

BASE = "https://site.api.espn.com/apis/site/v2/sports"
CACHE = Path("data/cache/espn")
REQUEST_PAUSE = 0.4  # be a considerate client; ESPN publishes no rate limit
TIMEOUT = 30

LEAGUE_PATHS = {
    "nba": ("basketball", "nba"),
    "wnba": ("basketball", "wnba"),
    "ncaam": ("basketball", "mens-college-basketball"),
    "ncaaw": ("basketball", "womens-college-basketball"),
    "ncaaf": ("football", "college-football"),
    # Club soccer. ESPN keys each competition separately, so a club's European
    # and domestic-cup matches come from different requests than its league ones
    # -- all of which must be gathered for the competition tiers to mean anything.
    "epl": ("soccer", "eng.1"),
    "laliga": ("soccer", "esp.1"),
    "seriea": ("soccer", "ita.1"),
    "bundesliga": ("soccer", "ger.1"),
    "ligue1": ("soccer", "fra.1"),
    "mls": ("soccer", "usa.1"),
    "nwsl": ("soccer", "usa.nwsl"),
    "ucl": ("soccer", "uefa.champions"),
    "uel": ("soccer", "uefa.europa"),
    "uecl": ("soccer", "uefa.europa.conf"),
    "facup": ("soccer", "eng.fa"),
    "efl_cup": ("soccer", "eng.league_cup"),
    "copadelrey": ("soccer", "esp.copa_del_rey"),
    "dfbpokal": ("soccer", "ger.dfb_pokal"),
    "coppaitalia": ("soccer", "ita.coppa_italia"),
    "coupedefrance": ("soccer", "fra.coupe_de_france"),
    "usopencup": ("soccer", "usa.open"),
    # MLS's continental competition, renamed from the Champions League in 2024.
    # UNVERIFIED path, like the rest of this table's newer entries: run
    # `python -m whul.cli discover concacafchampions` from a machine with
    # access before trusting it.
    "concacafchampions": ("soccer", "concacaf.champions_cup"),
    "ncaabaseball": ("baseball", "college-baseball"),
    # College softball lives under the *baseball* sport path; every
    # softball/... variant answers 404.
    "ncaasoftball": ("baseball", "college-softball"),
}

#: ESPN group id for the top division. Without it the scoreboard returns only a
#: featured subset, which would silently omit most of the field.
#: Softball is absent deliberately: groups=29 returns zero events on dates that
#: bare requests show 52 games on, so the filter excludes everything rather than
#: narrowing to a division.
DIVISION_I_GROUPS = {
    "ncaam": 50, "ncaaw": 50, "ncaaf": 80,
    "ncaabaseball": 26,
}

#: Leagues whose scoring actually uses conference affiliation. Baseball and
#: softball score wins, run differential and series milestones only, so a blank
#: conference costs them nothing.
CONFERENCE_REQUIRED = {"ncaaf", "ncaam", "ncaaw"}

#: Candidate sport/league paths to try when a league's usual path is rejected.
PATH_CANDIDATES = {
    "ncaasoftball": [
        ("baseball", "college-softball"),
        ("softball", "college-softball"),
    ],
}

#: Group ids worth trying when the configured one returns an implausible count.
GROUP_CANDIDATES = {
    "ncaaf": [80, 81, 90, None],
    "ncaam": [50, 51, None],
    "ncaaw": [50, 51, None],
    "ncaabaseball": [26, 27, None],
    "ncaasoftball": [29, 30, 100, None],
}

#: An NBA season labelled 2026 runs Oct 2025 - Jun 2026.
NBA_SEASON_START = (10, 1)
NBA_SEASON_END = (6, 30)

#: Soccer competitions a club may play in, beyond its own league. Gathering
#: these is what makes the competition tiers meaningful: without them every win
#: is a league win.
SOCCER_LEAGUES = ("epl", "laliga", "seriea", "bundesliga", "ligue1", "mls", "nwsl")
EUROPEAN_COMPETITIONS = ("ucl", "uel", "uecl")
#: Whose clubs can appear in EUROPEAN_COMPETITIONS. MLS walking the Champions
#: League cost 4,560 requests and about an hour a run for nothing, and turned up
#: only near-misses for the club matcher to reject -- Inter Milan against Inter
#: Miami, five seasons running.
EUROPEAN_LEAGUES = ("epl", "laliga", "seriea", "bundesliga", "ligue1")
DOMESTIC_CUPS = {
    "epl": ("facup", "efl_cup"),
    "laliga": ("copadelrey",),
    "bundesliga": ("dfbpokal",),
    "seriea": ("coppaitalia",),
    "ligue1": ("coupedefrance",),
    "mls": ("usopencup",),
    "nwsl": (),
}

#: A league's continental competition, where it is not UEFA's. Kept apart from
#: DOMESTIC_CUPS because these are not domestic and not scored as one: they are
#: paid as a bonus rather than counted, like the European competitions.
#: Empty, and deliberately: ESPN answered every CONCACAF Champions Cup roster
#: request with a 404 across five seasons, and its scoreboard returned no
#: matches on any of the 751 dates walked for it. A competition that can only
#: ever contribute zero is worse than one left out, because zero reads as a
#: quiet Champions Cup rather than as no data -- and walking it cost about
#: eleven minutes of every benchmark run to learn nothing.
#:
#: Qualifying for it is still paid, on the team side, and is unaffected by
#: this: the entrants are read from the published participant list rather than
#: from match data. See whul.benchmark_sources._concacaf_entrants.
#:
#: To restore it once a working path is found: put ``"mls":
#: ("concacafchampions",)`` back here and re-run `discover concacafchampions`
#: to confirm the path first. The tier, the rule and the 2.5% share are all
#: still in place and will start paying the moment rows arrive.
CONTINENTAL_CUPS: dict[str, tuple[str, ...]] = {}


def continental_for(league: str) -> tuple[str, ...]:
    """The continental competitions a league's clubs can actually play in."""
    european = EUROPEAN_COMPETITIONS if league in EUROPEAN_LEAGUES else ()
    return tuple(european) + tuple(CONTINENTAL_CUPS.get(league, ()))

#: (start month, day) -> (end month, day) -> how the season is numbered.
#:
#:   "within" -- it begins and ends inside the year it is named for.
#:   "ends"   -- it crosses new year and is named for the year it finishes in,
#:               which is how college basketball, the NBA and European football
#:               are all spoken of and indexed.
#:   "starts" -- it crosses new year and is named for the year it begins, which
#:               is how college football is spoken of: the 2026 season runs to
#:               January 2027, and ESPN indexes it under 2026.
#:
#: The distinction was previously a boolean with only the first two cases, and
#: football was set to "ends" while the comment above it said the opposite. That
#: asked ESPN for next season and grouped the COVID-shortened 2020 season under
#: 2021, where a five-season reach picked it up.
SEASON_WINDOWS = {
    "ncaaf": ((8, 1), (1, 31), "starts"),
    "ncaam": ((11, 1), (4, 15), "ends"),
    "ncaaw": ((11, 1), (4, 15), "ends"),
    "ncaabaseball": ((2, 1), (6, 30), "within"),
    "ncaasoftball": ((2, 1), (6, 30), "within"),
    # European seasons are labelled by the year they end. The window runs to the
    # end of June, not the end of May: the southern leagues now finish in the
    # first days of June, and a window closing on 31 May cut the last matchday
    # off La Liga, Serie A and Ligue 1 in 2022-23, and took Serie A's 2 June
    # replay of Atalanta-Fiorentina out of 2023-24. Those matches were not
    # missing from anywhere -- they were a smaller pool, a lower benchmark, and
    # every score measured against it larger.
    **{key: ((8, 1), (6, 30), "ends") for key in
       ("epl", "laliga", "seriea", "bundesliga", "ligue1",
        "ucl", "uel", "uecl", "facup", "efl_cup", "copadelrey",
        "dfbpokal", "coppaitalia", "coupedefrance")},
    # MLS and NWSL run within a calendar year.
    "mls": ((2, 20), (12, 15), "within"),
    "nwsl": ((3, 1), (11, 30), "within"),
    # The US Open Cup runs inside the MLS calendar year, its qualifying rounds
    # from March and its final in September.
    "usopencup": ((3, 1), (10, 15), "within"),
    # The CONCACAF Champions Cup runs February to June. The 2021 edition, played
    # April to October around the pandemic, falls partly outside this window and
    # will come back short rather than empty.
    "concacafchampions": ((2, 1), (6, 30), "within"),
    "nba": (NBA_SEASON_START, NBA_SEASON_END, "ends"),
}

# ESPN season_type ids, matching the codes hoopR exposed.
SEASON_TYPE_REGULAR = 2
SEASON_TYPE_POST = 3
SEASON_TYPE_PLAYIN = 5


def _get(url: str, params: dict, cache_key: str | None = None) -> dict:
    """Fetch, caching by key. Rate limiting applies only to real requests.

    The pause lives here rather than in the callers so a cached replay costs
    nothing: paying it on cache hits made re-running a backfill take almost as
    long as the original fetch, which defeats the point of caching.
    """
    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        if cached.exists():
            return json.loads(cached.read_text())

    response = requests.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload))

    time.sleep(REQUEST_PAUSE)
    return payload


def season_dates(season: int, league: str = "nba") -> list[date]:
    """Every date a season labelled ``season`` could have games on.

    Never runs past today, so a season that has not started yields nothing.
    """
    if league not in SEASON_WINDOWS:
        # Adding a competition to LEAGUE_PATHS without a window here used to
        # raise a bare KeyError from inside a date walk, twenty-two minutes
        # into a benchmark run, naming only the key. Say what is missing and
        # where it goes.
        raise KeyError(
            f"no season window for {league!r}: add its (start, end, numbering) "
            f"to espn.SEASON_WINDOWS, or its dates cannot be walked"
        )
    start_md, end_md, numbering = SEASON_WINDOWS[league]
    if numbering == "ends":
        start, end = date(season - 1, *start_md), date(season, *end_md)
    elif numbering == "starts":
        start, end = date(season, *start_md), date(season + 1, *end_md)
    else:
        start, end = date(season, *start_md), date(season, *end_md)
    end = min(end, date.today())
    if end < start:
        return []
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def season_span(season: int, league: str) -> tuple[date, date]:
    """A season's first and last day, uncapped by today.

    ``season_dates`` stops at today, which is right for walking dates and wrong
    for asking whether a season overlaps a league year: a season still to come
    would look like it did not exist.
    """
    return season_window.span(SEASON_WINDOWS[league], season)


def seasons_overlapping(league: str, first: date, last: date) -> list[int]:
    """Every season label this feed numbers with play inside a span."""
    return season_window.overlapping(SEASON_WINDOWS[league], first, last)


def season_label(league: str, day: date) -> int:
    """The season number a date belongs to, in this feed's numbering.

    European football and the college seasons are labelled by the year they
    *end* in, so a match in September 2026 is part of season 2027. Asking for
    the calendar year instead returns the season that finished in May -- a full
    set of results, from last year, which is exactly the kind of wrong answer
    that looks right.
    """
    start_md, _, numbering = SEASON_WINDOWS[league]
    if numbering == "within":
        return day.year
    started = (day.month, day.day) >= start_md
    if numbering == "starts":
        return day.year if started else day.year - 1
    return day.year + 1 if started else day.year


def _opening_saturday(year: int) -> date:
    """College football's week one, which is the last Saturday in August."""
    day = date(year, 8, 31)
    while day.weekday() != 5:  # Saturday
        day -= timedelta(days=1)
    return day


def _espn_week(day: date) -> int:
    """Which week of the college football season a date falls in.

    Counted from the opening Saturday rather than from the date the data walk
    starts, which is the first of August and three weeks early. Only used for
    discovery, where being a week out would still answer the question -- does a
    week query return a full slate where a date query does not.
    """
    year = day.year if day.month >= 8 else day.year - 1
    return max(1, ((day - _opening_saturday(year)).days // 7) + 1)


def scoreboard_variants(league: str, day: date) -> list[dict]:
    """Request shapes to try, most informative first.

    Leagues do not accept the same parameters: college softball answers 400 to
    both ``groups`` and ``limit``, so a single fixed shape loses that league
    entirely. Falling back progressively costs nothing when the first shape
    works, since only the successful response is cached.
    """
    dates = day.strftime("%Y%m%d")
    variants: list[dict] = []
    if league in DIVISION_I_GROUPS:
        variants.append({"dates": dates, "limit": 900, "groups": DIVISION_I_GROUPS[league]})
        variants.append({"dates": dates, "groups": DIVISION_I_GROUPS[league]})
    variants.append({"dates": dates, "limit": 900})
    variants.append({"dates": dates})
    return variants


def scoreboard(league: str, day: date) -> dict:
    """One date's games, trying request shapes until one returns games.

    A 200 is not sufficient: college softball *accepts* ``limit`` and then
    returns zero events for a date a bare request shows 52 games on. Accepting
    the first non-error response would silently yield an empty season with
    nothing logged, so a shape that returns no games is treated as suspect and
    the next one is tried. An empty response is still returned if every shape
    gives one, since a date genuinely without games looks the same.

    Variants are fetched uncached -- the cache key is shared, so caching a shape
    under test would short-circuit the search on the next run -- and only the
    chosen payload is written.
    """
    sport, path = LEAGUE_PATHS[league]
    url = f"{BASE}/{sport}/{path}/scoreboard"
    cached = CACHE / f"{league}/scoreboard/{day.isoformat()}.json"
    settled = _has_settled(day)
    if settled and cached.exists():
        return json.loads(cached.read_text())

    best: dict | None = None
    last: Exception | None = None
    for params in scoreboard_variants(league, day):
        try:
            payload = _get(url, params)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in (400, 404):
                raise
            last = exc
            continue
        if payload.get("events"):
            best = payload
            break
        if best is None:
            best = payload

    if best is None:
        raise last if last else RuntimeError(f"no scoreboard variant succeeded for {league}")

    if settled:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(best))
    return best


#: How long a date is given to finish before its scoreboard is cached forever.
#: A day cached while its matches were still being played would freeze a
#: half-finished result, and the nightly job would read that copy every night
#: after -- the score would simply never update. A full day clears both the
#: matches and the timezone the feed dates them in.
CACHE_SETTLE_DAYS = 1


def _has_settled(day: date, today: date | None = None) -> bool:
    """Whether a date's results can no longer change."""
    return day < (today or date.today()) - timedelta(days=CACHE_SETTLE_DAYS)


def load_eligible_teams(league: str) -> set[str]:
    """Display names of the league's own teams.

    Needed because a scoreboard request returns games *involving* a listed team,
    so the opponent may be from a lower division. Those opponents would otherwise
    enter the team pool with one or two games apiece and distort the benchmark.
    The R scripts approximated this with a minimum-games filter; asking the feed
    which teams belong is exact.
    """
    sport, path = LEAGUE_PATHS[league]
    params: dict = {"limit": 1000}
    if league in DIVISION_I_GROUPS:
        params["groups"] = DIVISION_I_GROUPS[league]
    try:
        payload = _get(f"{BASE}/{sport}/{path}/teams", params, cache_key=f"{league}/teams")
    except Exception:
        return set()

    names: set[str] = set()
    for sport_block in payload.get("sports", []):
        for league_block in sport_block.get("leagues", []):
            for entry in league_block.get("teams", []):
                team = entry.get("team") or {}
                name = team.get("displayName")
                if name:
                    names.add(str(name))
    return names


def load_eligible_team_ids(league: str) -> set[str]:
    """The same teams, by the feed's own id for them.

    A display name is the feed's opinion and it is not the same opinion in
    every competition: Bayern's Champions League matches came back naming a
    club the Bundesliga's own team list does not contain, so the filter that
    keeps a league to its own clubs dropped them and Bayern finished a European
    week with nothing. An id is the feed agreeing with itself.

    UNVERIFIED that ESPN uses one id per club across competitions -- it is not
    reachable from where this was written -- so this is used to *keep* rows the
    name filter would drop and never to drop rows it would keep. If the ids do
    not line up across competitions, nothing is lost that is not already lost.
    """
    sport, path = LEAGUE_PATHS[league]
    params: dict = {"limit": 1000}
    if league in DIVISION_I_GROUPS:
        params["groups"] = DIVISION_I_GROUPS[league]
    try:
        payload = _get(f"{BASE}/{sport}/{path}/teams", params, cache_key=f"{league}/teams")
    except Exception:  # noqa: BLE001 -- a filter that cannot load must not stop a pull
        return set()

    ids: set[str] = set()
    for sport_block in payload.get("sports", []):
        for league_block in sport_block.get("leagues", []):
            for entry in league_block.get("teams", []):
                team = entry.get("team") or {}
                if team.get("id") not in (None, ""):
                    ids.add(str(team["id"]))
    return ids


def _competitor(competition: dict, home_away: str) -> dict:
    for entry in competition.get("competitors", []):
        if entry.get("homeAway") == home_away:
            return entry
    return {}


def _conference(entry: dict) -> str:
    """Conference identifier, wherever ESPN happens to put it.

    Load-bearing for football and basketball: conference wins are scored, and the
    regular-season title is split among co-champions. A blank here silently
    zeroes those terms rather than erroring, so the probe reports coverage.
    """
    team = entry.get("team", {}) or {}
    for value in (team.get("conferenceId"), entry.get("conferenceId")):
        if value not in (None, ""):
            return str(value)
    for group in (team.get("groups") or {}), (entry.get("groups") or {}):
        if isinstance(group, dict):
            for key in ("id", "parentGroupId"):
                if group.get(key):
                    return str(group[key])
    return ""


def _event_rows(event: dict, league: str, season: int, day: date) -> dict | None:
    """Flatten one scoreboard event into a single game row."""
    competition = (event.get("competitions") or [{}])[0]
    status = (competition.get("status") or {}).get("type", {}) or {}
    home, away = _competitor(competition, "home"), _competitor(competition, "away")
    if not home or not away:
        return None

    notes = " ".join(
        str(n.get("headline", "")) for n in (competition.get("notes") or []) if isinstance(n, dict)
    )
    if not notes:
        notes = str(event.get("name", ""))

    def score(entry: dict) -> float | None:
        value = entry.get("score")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return {
        "season": season,
        "game_id": event.get("id"),
        "game_date": day.isoformat(),
        "season_type": int((event.get("season") or {}).get("type", 2)),
        "completed": bool(status.get("completed")),
        "home_team": (home.get("team") or {}).get("displayName", ""),
        "away_team": (away.get("team") or {}).get("displayName", ""),
        "home_conference": _conference(home),
        "away_conference": _conference(away),
        "home_score": score(home),
        "away_score": score(away),
        "notes": notes,
    }


def _score_of(entry: dict) -> float | None:
    """A competitor's score, however this endpoint spells it.

    The scoreboard puts a bare string here and the team schedule an object with
    ``value`` and ``displayValue``. Reading only one of them turns every game
    from the other endpoint into a fixture with no result.
    """
    value = entry.get("score")
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def team_index(league: str) -> dict[str, str]:
    """``{team display name: ESPN id}`` for a league.

    The schedule endpoint takes an id and answers 400 to a slug, so a roster
    written in names needs this to reach it.
    """
    sport, path = LEAGUE_PATHS[league]
    payload = _get(
        f"{BASE}/{sport}/{path}/teams", {"limit": 1000},
        cache_key=f"{league}/teams-index",
    )
    found: dict[str, str] = {}
    for group in payload.get("sports", []):
        for entry in group.get("leagues", []):
            for row in entry.get("teams", []):
                team = row.get("team") or {}
                name = team.get("displayName") or team.get("name") or ""
                if name and team.get("id"):
                    found[str(name)] = str(team["id"])
    return found


def load_team_schedule(league: str, team_id: str, season: int) -> pd.DataFrame:
    """One team's whole season, in the shape the scorers read.

    A team's own schedule cannot be short of its own games, which the
    scoreboard can: it caps at twenty-five events a request and ignores both
    ``limit`` and ``page``, so it returns the featured games rather than the
    slate. For a roster of eight teams this is eight requests and complete.
    """
    sport, path = LEAGUE_PATHS[league]
    payload = _get(
        f"{BASE}/{sport}/{path}/teams/{team_id}/schedule",
        {"season": season},
        cache_key=None,   # a season in progress changes daily
    )
    rows: list[dict] = []
    for event in payload.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        status = (competition.get("status") or {}).get("type", {}) or {}
        home, away = _competitor(competition, "home"), _competitor(competition, "away")
        if not home or not away:
            continue
        day = str(event.get("date", ""))[:10]
        rows.append({
            "season": season,
            "game_id": str(event.get("id", "")),
            "game_date": day,
            "season_type": int((event.get("seasonType") or {}).get("id", 2) or 2),
            "completed": bool(status.get("completed")),
            "home_team": (home.get("team") or {}).get("displayName", ""),
            "away_team": (away.get("team") or {}).get("displayName", ""),
            "home_conference": _conference(home),
            "away_conference": _conference(away),
            "home_score": _score_of(home),
            "away_score": _score_of(away),
            "notes": str(event.get("name", "")),
        })
    return pd.DataFrame(rows)


#: Where a college team's conference actually lives.
#:
#: ``/teams/{id}/schedule`` -- the endpoint ``load_rostered_schedules`` reads --
#: carries no conference of any kind. Not on the competition, not on either
#: competitor, not on the team: the competitor keys are ``curatedRank, homeAway,
#: id, leaders, order, record, score, team, type, winner`` and the team keys are
#: ``abbreviation, displayName, id, links, location, logos, nickname,
#: shortDisplayName``. ``conferenceCompetition`` is absent too, so not even
#: "was this a conference game" can be read off it. That is why ten rostered
#: NCAAF teams scored nothing while the nightly ingest raised
#: ``MissingConference`` -- and why the historical backfill was unaffected: it
#: walks the scoreboard, which does carry conferences.
#:
#: ``/teams/{id}`` carries it, as ``groups``:
#:
#:     Arkansas       groups.id 8    isConference true    parent 80
#:     Indiana        groups.id 5    isConference true    parent 80
#:     Miami          groups.id 1    isConference true    parent 80
#:     Notre Dame     groups.id 18   isConference true    parent 80
#:     James Madison  groups.id 167  isConference false   parent 37
#:
#: 8 is the SEC and 1 the ACC; 167 is the Sun Belt *East*, a division, whose
#: parent 37 is the Sun Belt itself. So the conference is ``groups.id`` when
#: ``isConference``, and ``groups.parent.id`` otherwise. Every one of those
#: values matches what the scoreboard reports for the same team on the same
#: weekend, which is two independent sources agreeing.
#:
#: Notre Dame's 18 is FBS Independents -- a real answer, not a missing one,
#: which is why the league's ACC rule lives in the scorer as an override rather
#: than here as a patched-up feed value.
def _conference_of_team(team: dict) -> str:
    """The conference id on a team record, division-aware."""
    groups = team.get("groups")
    if not isinstance(groups, dict):
        return ""
    if not groups.get("isConference"):
        parent = groups.get("parent")
        if isinstance(parent, dict) and parent.get("id"):
            return str(parent["id"])
        # A group that is neither a conference nor has a parent is a shape
        # nobody has seen. Its own id is the better guess than nothing: a
        # division id still groups a team with the teams it plays for the
        # title, where a blank drops it out of scoring entirely.
    return str(groups.get("id") or "")


def team_conference(league: str, team_id: str, season: int) -> str:
    """One team's conference, from its own record. Cached for the season.

    Membership is a property of a team for a season, so this cannot go missing
    on the night of a particular slate the way a per-game field can -- and the
    cache key is the season, so the whole map costs one request a team a year.
    """
    sport, path = LEAGUE_PATHS[league]
    payload = _get(
        f"{BASE}/{sport}/{path}/teams/{team_id}", {},
        cache_key=f"{league}/conference/{season}/{team_id}",
    )
    return _conference_of_team(payload.get("team") or payload)


def fill_conferences(
    league: str, games: pd.DataFrame, season: int, verbose: bool = True
) -> pd.DataFrame:
    """Put each side's conference on rows that arrived without one.

    Opponents are looked up as well as rostered teams, and that is the whole
    point: a conference game is a game whose two sides share a conference, so a
    map covering only the ten teams the league drafted would report every one of
    their conference games as non-conference and score the term at zero. It
    would look like a working feed.

    What cannot be resolved is named and counted rather than passed over. A
    partial map understates silently -- fewer conference wins, a title split
    among the wrong teams -- and nothing downstream can tell it from a team that
    genuinely lost those games.
    """
    if games is None or games.empty:
        return games
    if not {"home_team", "away_team"} <= set(games.columns):
        return games
    for column in ("home_conference", "away_conference"):
        if column not in games.columns:
            games[column] = ""
        games[column] = games[column].fillna("").astype(str)

    wanted: set[str] = set()
    for side in ("home", "away"):
        blank = games[f"{side}_conference"] == ""
        wanted |= {str(n) for n in games.loc[blank, f"{side}_team"] if str(n)}
    if not wanted:
        return games

    index = team_index(league)
    lookup = {_match_key(name): team_id for name, team_id in index.items()}
    found: dict[str, str] = {}
    unknown: list[str] = []
    for name in sorted(wanted):
        team_id = lookup.get(_match_key(name))
        if not team_id:
            unknown.append(name)
            continue
        try:
            conference = team_conference(league, team_id, season)
        except Exception:  # noqa: BLE001 -- one team must not lose the rest
            conference = ""
        if conference:
            found[name] = conference
        else:
            unknown.append(name)

    for side in ("home", "away"):
        filled = games[f"{side}_team"].astype(str).map(found).fillna("")
        blank = games[f"{side}_conference"] == ""
        games.loc[blank, f"{side}_conference"] = filled[blank]

    if unknown and verbose:
        print(
            f"  {league}: no conference for {len(unknown)} of {len(wanted)} "
            f"team(s) -- their games cannot count as conference games: "
            f"{', '.join(unknown[:12])}"
            f"{' ...' if len(unknown) > 12 else ''}",
            flush=True,
        )
    return games


def load_rostered_schedules(
    league: str, seasons: list[int], names: list[str], verbose: bool = True
) -> pd.DataFrame:
    """Every game the named teams played, one request per team per season.

    Names that the feed does not know are reported rather than skipped: a
    rostered team quietly absent scores nothing, and nothing else would say so.
    """
    index = team_index(league)
    lookup = {_match_key(name): team_id for name, team_id in index.items()}

    frames, missing = [], []
    found_any = False
    for name in names:
        team_id = lookup.get(_match_key(name))
        if not team_id:
            missing.append(name)
            continue
        found_any = True
        for season in seasons:
            try:
                frames.append(load_team_schedule(league, team_id, season))
            except Exception as exc:  # noqa: BLE001 -- one team must not lose the rest
                if verbose:
                    print(f"  {league}: {name} season {season} failed: "
                          f"{type(exc).__name__}", flush=True)
    if missing and verbose:
        print(
            f"  {league}: no ESPN team called {', '.join(missing)} -- "
            f"they will score nothing",
            flush=True,
        )
    if names and not found_any:
        # Every rostered name failed to resolve, so no schedule was ever
        # requested. That is not an empty week and must not read as one: an
        # empty frame here is indistinguishable from a season nobody has played,
        # and it would cost the whole year without anything raising.
        raise LookupError(
            f"none of the {len(names)} rostered {league} team(s) match a team in "
            f"ESPN's index of {len(index)}: {', '.join(missing)}. No schedule was "
            f"requested, so nothing can score."
        )
    if not frames:
        return pd.DataFrame()
    both = pd.concat(frames, ignore_index=True)
    # Two rostered teams playing each other return the same game twice.
    both = both.drop_duplicates(subset=["game_id"]).reset_index(drop=True)
    # This endpoint carries no conference at all, and football and basketball
    # scoring cannot proceed without one, so it is joined on from the teams'
    # own records rather than left blank. Deduplicated first: it is one lookup
    # per distinct team either way, and there is no sense paying for the same
    # game twice.
    return fill_conferences(league, both, max(seasons), verbose=verbose)


def _match_key(name: str) -> str:
    from whul.resolve import normalize_team

    return normalize_team(name)


def load_team_results(league: str, seasons: list[int], verbose: bool = True) -> pd.DataFrame:
    """Completed game results for whole seasons -- no box scores.

    This is all the NCAA leagues need, since they have team slots only. One
    scoreboard request per date rather than one per game, which is what keeps
    these leagues affordable despite their game volume.
    """
    rows: list[dict] = []
    for season in seasons:
        days = season_dates(season, league)
        if verbose:
            print(f"  {league} {season}: walking {len(days)} dates ...", flush=True)
        for index, day in enumerate(days):
            try:
                board = scoreboard(league, day)
            except Exception:
                continue
            for event in board.get("events", []):
                row = _event_rows(event, league, season, day)
                if row and row["completed"]:
                    rows.append(row)
            if verbose and index and index % 50 == 0:
                print(f"    {index}/{len(days)} dates, {len(rows):,} games", flush=True)
    return pd.DataFrame(rows)


def daily_results_cost(league: str, day: date | None = None) -> float:
    """Seconds to pull one date of results -- the nightly job for a team league.

    Bypasses the cache so the figure reflects real network cost. Results-only
    leagues cost a single request per date, whatever their game volume.
    """
    day = day or default_probe_date()
    params: dict = {"dates": day.strftime("%Y%m%d"), "limit": 900}
    if league in DIVISION_I_GROUPS:
        params["groups"] = DIVISION_I_GROUPS[league]
    sport, path = LEAGUE_PATHS[league]
    started = time.monotonic()
    _get(f"{BASE}/{sport}/{path}/scoreboard", params)
    return time.monotonic() - started


def discover(league: str, day: date | None = None) -> dict:
    """Report what each candidate path and group id actually returns.

    Used when a league's configured path or division filter looks wrong -- a
    softball endpoint that rejects everything, or a football team list far larger
    than the division it should describe. Rather than guessing from here, this
    asks the API and reports counts so the right values can be chosen.
    """
    day = day or default_probe_date()
    dates = day.strftime("%Y%m%d")
    out: dict[str, object] = {"league": league, "date": day.isoformat()}

    paths = PATH_CANDIDATES.get(league, [LEAGUE_PATHS[league]])
    path_report: list[str] = []
    for sport, path in paths:
        teams_n: object = "?"
        try:
            payload = _get(f"{BASE}/{sport}/{path}/teams", {"limit": 1000})
            teams_n = sum(
                len(lb.get("teams", []))
                for sb in payload.get("sports", [])
                for lb in sb.get("leagues", [])
            )
        except Exception as exc:
            teams_n = f"ERR {getattr(getattr(exc, 'response', None), 'status_code', '?')}"
        try:
            board = _get(f"{BASE}/{sport}/{path}/scoreboard", {"dates": dates})
            games_n: object = len(board.get("events", []))
        except Exception as exc:
            games_n = f"ERR {getattr(getattr(exc, 'response', None), 'status_code', '?')}"
        path_report.append(f"{sport}/{path}: teams={teams_n} games={games_n}")
    out["paths"] = path_report

    sport, path = LEAGUE_PATHS[league]
    group_report: list[str] = []
    for group in GROUP_CANDIDATES.get(league, [None]):
        params: dict = {"limit": 1000}
        if group is not None:
            params["groups"] = group
        try:
            payload = _get(f"{BASE}/{sport}/{path}/teams", params)
            count: object = sum(
                len(lb.get("teams", []))
                for sb in payload.get("sports", [])
                for lb in sb.get("leagues", [])
            )
        except Exception as exc:
            count = f"ERR {getattr(getattr(exc, 'response', None), 'status_code', '?')}"
        group_report.append(f"groups={group}: teams={count}")
    out["group_ids"] = group_report

    # The teams endpoint may ignore `groups` entirely (college football returns
    # every division whatever is passed), so measure the scoreboard directly:
    # which parameter combination actually narrows the field, and to what.
    group = DIVISION_I_GROUPS.get(league) or (GROUP_CANDIDATES.get(league) or [None])[0]
    grouped = {"groups": group} if group else {}
    combos: list[tuple[str, dict]] = [
        ("groups+limit", {"dates": dates, "limit": 900, **grouped}),
        ("groups only", {"dates": dates, **grouped}),
        ("limit only", {"dates": dates, "limit": 900}),
        ("bare", {"dates": dates}),
        # A week, and a week's worth of dates. College football is organised by
        # week rather than by day, and a single date has come back with eight
        # games on a Saturday that had sixty -- so whether the date query is
        # simply the wrong question is worth asking directly.
        ("week", {"dates": str(day.year), "seasontype": 2,
                  "week": _espn_week(day), "limit": 900, **grouped}),
        ("date range", {
            "dates": f"{(day - timedelta(days=3)).strftime('%Y%m%d')}-"
                     f"{(day + timedelta(days=3)).strftime('%Y%m%d')}",
            "limit": 900, **grouped,
        }),
    ]
    combo_report: list[str] = []
    conferences: dict[str, int] = {}
    for label, params in combos:
        try:
            board = _get(f"{BASE}/{sport}/{path}/scoreboard", params)
            events = board.get("events", [])
            combo_report.append(f"{label}: {len(events)} events")
            if label == "groups+limit":
                names = sorted(
                    {
                        (c.get("team") or {}).get("displayName", "")
                        for e in events
                        for c in ((e.get("competitions") or [{}])[0]).get("competitors", [])
                    }
                )
                out["sample_teams"] = names[:6]
            for event in events:
                for competitor in ((event.get("competitions") or [{}])[0]).get("competitors", []):
                    conf = _conference(competitor)
                    if conf:
                        conferences[conf] = conferences.get(conf, 0) + 1
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            combo_report.append(f"{label}: ERR {status}")
    out["scoreboard_by_params"] = combo_report
    out["conference_ids"] = sorted(conferences.items(), key=lambda kv: -kv[1])[:20]
    return out


def scoreboard_league_name(board: dict) -> str:
    """The competition's display name, which sits at the top of the response."""
    for entry in board.get("leagues", []) or []:
        for key in ("name", "abbreviation", "slug"):
            if entry.get(key):
                return str(entry[key])
    return ""


def load_soccer_matches(
    league: str, seasons: list[int], include_cups: bool = True, verbose: bool = True
) -> pd.DataFrame:
    """Every match a league's clubs played, across all their competitions.

    Gathering the European competitions and domestic cups alongside the league
    is what gives the competition tiers meaning -- restricted to league fixtures,
    every win would be worth three points and the Champions League premium would
    never appear.

    Returns two rows per match, one per club, which is the shape the soccer
    scorer expects.
    """
    competitions = [league]
    if include_cups:
        competitions += (
            list(DOMESTIC_CUPS.get(league, ()))
            + list(continental_for(league))
        )

    rows: list[dict] = []
    lost: dict[str, int] = {}
    produced: dict[str, int] = {}
    for competition in competitions:
        if competition not in LEAGUE_PATHS:
            continue
        before_competition = len(rows)
        for season in seasons:
            days = season_dates(season, competition)
            if verbose:
                print(f"  {competition} {season}: {len(days)} dates ...", flush=True)
            failed = 0
            season_rows, walk = _by_range(competition, days, verbose=verbose)
            for index, day in enumerate(walk):
                board = _scoreboard_or_none(competition, day)
                if board is None:
                    # A date that cannot be read is a day of matches missing
                    # from the pool, not a day without matches. Counted here
                    # and reported below; see the note on `lost`.
                    failed += 1
                    continue
                name = scoreboard_league_name(board)
                for event in board.get("events", []):
                    season_rows.extend(_soccer_rows(event, competition, day, name))
                if verbose and index and index % 60 == 0:
                    print(f"    {index}/{len(walk)} dates, "
                          f"{len(season_rows):,} rows", flush=True)
            season_rows = _the_season_asked_for(season_rows, competition, verbose)
            if verbose:
                _report_unbalanced(season_rows, competition, season)
            rows.extend(season_rows)
            if failed:
                lost[competition] = lost.get(competition, 0) + failed
                print(f"    {competition} {season}: {failed} of {len(days)} date(s) "
                      f"could not be read, so their matches are missing",
                      flush=True)
        produced[competition] = len(rows) - before_competition
    if lost:
        # This is the failure the whole file is written against. A benchmark
        # built from fewer matches than were played is not an error anywhere --
        # it is a lower number, and a lower benchmark makes every score above
        # it larger. It was found by two runs of the same five MLS seasons
        # disagreeing: 121.35 and then 117.18, the second of which had *more*
        # scoring in it. Before this, every one of those dates was skipped by a
        # bare `except: continue`.
        # Which competition lost them decides whether it matters. Three dates
        # missing from a feed that answered every other date with nothing are
        # three more nothings; three missing from the league is a week of
        # results. Saying only the total makes those read alike, and the
        # cautious reading of the harmless one is a re-run that changes nothing.
        total = sum(lost.values())
        empty = [c for c in lost if not produced.get(c)]
        for competition, count in sorted(lost.items()):
            got = produced.get(competition, 0)
            note = ("and that competition returned no matches at all on the "
                    "dates that did answer, so these are probably more of the "
                    "same" if not got else
                    f"and that competition returned {got:,} row(s) on the dates "
                    f"that did answer")
            print(f"  {competition}: {count} date(s) unread, {note}", flush=True)
        if len(empty) == len(lost):
            print(f"\n  {total} date(s) could not be read, all of them in "
                  f"competitions that produced nothing anywhere. The pool is "
                  f"very likely complete, but this cannot be proven from here.\n",
                  flush=True)
        else:
            print(f"\n  {total} date(s) in total could not be read. The pool below "
                  f"is drawn from fewer matches than were played, which lowers "
                  f"the benchmark and raises every score measured against it. "
                  f"Re-run before freezing.\n", flush=True)
    # The feed's season label chose the rows; it is not for the scorers to read.
    return pd.DataFrame(rows).drop(columns=["season_year"], errors="ignore")


#: Dates asked for in one range request. The probe settled that the soccer
#: scoreboard answers a range with exactly what walking the same dates returns:
#: on `epl` across 2025-08-15..08-31, both range shapes gave 30 events, 60 rows,
#: 7 big wins and 16 clean sheets, with none missing, none extra and no score
#: differing from the walk. That is what makes this safe to rely on, and the
#: saving is the difference between a recompute that finishes and one that does
#: not: the six-league, five-season walk asks 42,160 dates, which is 4.7 hours
#: of politeness pause before a single byte moves. In thirty-date spans it is
#: 1,405 requests and about nine minutes.
#: Set WHUL_ESPN_RANGE_DAYS=0 to walk dates the old way. The range is new and
#: the walk is the thing it was measured against, so there is a way back that
#: does not need a release.
RANGE_DAYS = int(os.environ.get("WHUL_ESPN_RANGE_DAYS") or 30)

#: A span shorter than this is walked date by date. A range buys little on a
#: handful of dates, and the walk accounts for what it loses one date at a time,
#: which is the finer report when there is little to report on.
RANGE_MINIMUM = 7


def _date_chunks(days: list[date], size: int) -> list[list[date]]:
    """A contiguous run of dates cut into spans of at most ``size``."""
    return [days[i:i + size] for i in range(0, len(days), size)]


def scoreboard_range(competition: str, start: date, end: date) -> dict:
    """A span of dates in one request, trying shapes until one returns events.

    The same suspicion `scoreboard` applies to a single date applies here: a
    200 carrying no events is not proof of a quiet month, so a shape that
    returns nothing is kept only if every other shape also returns nothing.
    """
    sport, path = LEAGUE_PATHS[competition]
    url = f"{BASE}/{sport}/{path}/scoreboard"
    cached = CACHE / f"{competition}/range/{start.isoformat()}_{end.isoformat()}.json"
    settled = _has_settled(end)
    if settled and cached.exists():
        return json.loads(cached.read_text())

    best: dict | None = None
    last: Exception | None = None
    for params in range_variants(start, end):
        try:
            payload = _get(url, params)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in (400, 404):
                raise
            last = exc
            continue
        if payload.get("events"):
            best = payload
            break
        if best is None:
            best = payload

    if best is None:
        raise last if last else RuntimeError(
            f"no range variant succeeded for {competition}")

    if settled:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(best))
    return best


def _range_or_none(competition: str, start: date, end: date) -> dict | None:
    """One span's board, or None having tried twice."""
    for attempt in range(2):
        try:
            return scoreboard_range(competition, start, end)
        except Exception:  # noqa: BLE001 -- one span, not the season
            if attempt == 0:
                time.sleep(RETRY_PAUSE)
    return None


def _event_ids(board: dict) -> set[str]:
    return {str(event.get("id") or "") for event in board.get("events", []) or []}


def _range_holds_its_halves(
    competition: str, start: date, end: date, board: dict
) -> set[str]:
    """Matches the two halves of a span return that the whole span did not.

    A feed that caps how many events it will return does not say so. It returns
    a valid response holding fewer matches than were played, which is not an
    error anywhere -- it is a smaller pool, a lower benchmark, and every score
    measured against it larger. The cap cannot be found by reading one response,
    because a short answer and a quiet month look alike; it can be found by
    asking the same question in two pieces, because a capped whole is missing
    what its halves are not. Two requests per competition-season buys that.
    """
    middle = start + timedelta(days=(end - start).days // 2)
    found: set[str] = set()
    for lower, upper in ((start, middle), (middle + timedelta(days=1), end)):
        half = _range_or_none(competition, lower, upper)
        if half is None:
            return set()  # An unread half proves nothing either way.
        found |= _event_ids(half)
    return found - _event_ids(board)


def _by_range(
    competition: str, days: list[date], verbose: bool = True
) -> tuple[list[dict], list[date]]:
    """A season's matches in a handful of requests, and the dates still to walk.

    Returns the rows gathered and the dates the range path did not account for
    -- none when it accounted for all of them, every date when it is not to be
    trusted. Degrading to the walk costs time; trusting a range that drops
    matches costs a benchmark nobody can tell is wrong.

    Rows are stamped with the event's own date rather than the date that was
    asked for, since one response spans weeks. For a league whose matches kick
    off late in local time that date can read a day later than the walk's
    bucket; the probe measures exactly this, and reported no disagreement at
    all across the seventeen days it compared.
    """
    if len(days) < RANGE_MINIMUM or RANGE_DAYS < RANGE_MINIMUM:
        return [], days

    chunks = _date_chunks(days, RANGE_DAYS)
    boards: list[tuple[list[date], dict]] = []
    for chunk in chunks:
        board = _range_or_none(competition, chunk[0], chunk[-1])
        if board is None:
            # One unread span is the whole season's worth of trust: walk it all
            # rather than report a pool short by a month with nothing to show.
            return [], days
        boards.append((chunk, board))

    if not any(_event_ids(board) for _, board in boards):
        # Every span quiet is what a capped or refused feed looks like as well
        # as a season that has not started. The walk tells them apart, and it
        # only costs anything in the case that was going to cost anyway.
        return [], days

    fullest, board = max(boards, key=lambda pair: len(_event_ids(pair[1])))
    hidden = _range_holds_its_halves(competition, fullest[0], fullest[-1], board)
    if hidden:
        print(f"  {competition}: a range of {len(fullest)} dates returned "
              f"{len(_event_ids(board))} match(es) but its two halves found "
              f"{len(hidden)} more, so the feed is capping what it returns. "
              f"Walking {len(days)} dates instead.", flush=True)
        return [], days

    rows: list[dict] = []
    for chunk, board in boards:
        name = scoreboard_league_name(board)
        for event in board.get("events", []) or []:
            rows.extend(_soccer_rows(
                event, competition, _event_day(event, chunk[0]), name))
    if verbose:
        print(f"    {len(days)} dates in {len(chunks) + 2} requests, "
              f"{len(rows):,} rows", flush=True)
    return rows, []


#: A second attempt costs one request and saves a day of matches. The pause is
#: deliberately longer than the ordinary one: the failures worth retrying are
#: the ones where the feed wants to be left alone for a moment.
RETRY_PAUSE = 2.0


def _the_season_asked_for(
    rows: list[dict], competition: str, verbose: bool = True
) -> list[dict]:
    """Rows the feed itself labels as one season, out of a window holding more.

    A window is a pair of calendar dates and a season is not. Serie A's 2019-20
    ended on 1 August 2020, which is the day the window for 2020-21 opens, so
    that pool carried ten matches none of its clubs played that season: 390
    where a 20-club league plays 380. The Europa League is worse, its 2019-20
    knockout rounds having been played in August 2020 outright.

    Which year ESPN numbers a European season by is not something to guess at,
    so this does not compare against the season asked for. It takes the label
    the bulk of the window carries and drops what disagrees with it, which
    needs no convention at all -- and a feed that states no season leaves every
    row alone, since dropping rows on the strength of a missing field is the
    direction that quietly shrinks a pool.
    """
    stated = [row["season_year"] for row in rows if row.get("season_year")]
    if not stated:
        return rows
    belongs = Counter(stated).most_common(1)[0][0]
    kept = [row for row in rows
            if row.get("season_year") in (None, "", belongs)]
    if verbose and len(kept) != len(rows):
        print(f"    {len(rows) - len(kept)} row(s) the feed labels another "
              f"season than {belongs}, left out of {competition}", flush=True)
    return kept


#: Leagues that play a balanced double round-robin. They are the one pool whose
#: size is known before it is fetched -- N clubs playing each other home and
#: away is N*(N-1) matches and 2*(N-1) apiece -- which makes a missing fixture
#: nameable rather than merely countable.
BALANCED_LEAGUES = ("epl", "laliga", "seriea", "bundesliga", "ligue1")


def _report_unbalanced(rows: list[dict], competition: str, season: int) -> None:
    """Name the fixtures a league season did not come back whole without.

    Every other check here can say only that a number looks wrong. A balanced
    league is the one pool whose size is known before it is fetched -- N clubs
    playing each other home and away is N*(N-1) matches, 2*(N-1) apiece and
    every pairing exactly twice -- so what is missing can be named down to the
    two clubs who were meant to play it. That is the difference between five
    matches missing from somewhere in La Liga 2022-23 and five fixtures a
    person can look up in a minute.

    It reports and does not drop. Serie A 2022-23 came back with a 381st match
    because Hellas Verona and Spezia finished level and played off to stay up,
    which is a real match this check cannot be allowed to hide, and is why it
    names pairings met too often as well as too seldom.
    """
    if competition not in BALANCED_LEAGUES or not rows:
        return
    played = Counter(row["team"] for row in rows)
    clubs = len(played)
    # Two rows a match, so a pairing played home and away is four rows.
    met = Counter(frozenset((row["team"], row["opponent"])) for row in rows)
    odd = sorted((sorted(pair), n // 2) for pair, n in met.items()
                 if len(pair) == 2 and n != 4)
    missing = sorted(team for team, n in played.items() if n != 2 * (clubs - 1))
    if not odd and not missing:
        return
    print(f"  {competition} {season}: {len(rows) // 2} match(es), where {clubs} "
          f"clubs playing each other home and away is {clubs * (clubs - 1)}.",
          flush=True)
    for (one, other), meetings in odd:
        print(f"      {one} v {other}: {meetings} of 2", flush=True)
    named = {club for pair, _ in odd for club in pair}
    for club in missing:
        if club not in named:
            # Short a match against nobody in particular: an opponent it never
            # met at all leaves no pairing to count.
            print(f"      {club}: {played[club]} of {2 * (clubs - 1)}, "
                  f"no pairing to name", flush=True)


def _scoreboard_or_none(competition: str, day: date) -> dict | None:
    """One day's board, or None having tried twice."""
    for attempt in range(2):
        try:
            return scoreboard(competition, day)
        except Exception:  # noqa: BLE001 -- one date, not the season
            if attempt == 0:
                time.sleep(RETRY_PAUSE)
    return None


def _soccer_rows(
    event: dict, competition: str, day: date, league_name: str = ""
) -> list[dict]:
    """One match as two team rows, carrying the competition key and round.

    ``league_name`` comes from the top of the scoreboard response, not from the
    event -- reading it per-event yields nothing, which is how the label first
    came back as the bare key.
    """
    inner = (event.get("competitions") or [{}])[0]
    status = (inner.get("status") or {}).get("type", {}) or {}
    if not status.get("completed"):
        return []

    home, away = _competitor(inner, "home"), _competitor(inner, "away")
    if not home or not away:
        return []

    def score(entry: dict) -> float | None:
        try:
            return float(entry.get("score"))
        except (TypeError, ValueError):
            return None

    home_goals, away_goals = score(home), score(away)
    if home_goals is None or away_goals is None:
        return []

    def shootout(entry: dict) -> float:
        """Penalties scored in a shootout, or zero where there was none.

        ESPN carries this beside the score rather than inside it, which is what
        makes a shootout recoverable at all -- a feed that folded the penalties
        into ``score`` would report a drawn tie as a win and there would be
        nothing here to tell the difference. Zero for both sides is the honest
        reading of a match that did not go to penalties.
        """
        for key in ("shootoutScore", "shootout_score", "penaltyScore"):
            value = entry.get(key)
            if value not in (None, ""):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    # The round name matters: it distinguishes a qualifying tie from the
    # competition proper, and the knockout play-off from either.
    name = league_name or (event.get("league") or {}).get("name", "") or competition
    notes = " ".join(
        str(n.get("headline", "")) for n in (inner.get("notes") or []) if isinstance(n, dict)
    )
    season = event.get("season") or {}
    season_type = (season.get("slug") or "").replace("-", " ")
    season_year = season.get("year")
    competition_label = " ".join(p for p in (name, notes, season_type) if p).strip()

    rows = []
    for side, other in ((home, away), (away, home)):
        rows.append({
            "team": (side.get("team") or {}).get("displayName", ""),
            "opponent": (other.get("team") or {}).get("displayName", ""),
            # The feed's own id for each side. A name is the feed's opinion and
            # differs between competitions; an id is the feed agreeing with
            # itself, which is what the league's own-club filter needs.
            "team_id": str((side.get("team") or {}).get("id") or ""),
            "opponent_id": str((other.get("team") or {}).get("id") or ""),
            "date": day.isoformat(),
            "competition": competition_label,
            "competition_key": competition,
            "goals_for": score(side),
            "goals_against": score(other),
            "shootout_for": shootout(side),
            "shootout_against": shootout(other),
            "season_year": season_year,
        })
    return rows


#: Request shapes for a span of dates, most informative first. ESPN takes a
#: range in the same ``dates`` parameter a single day goes in, separated by a
#: hyphen -- the golf and racing paths already ask for a whole year that way.
#: Whether the *soccer* scoreboard honours it, and whether it caps the number
#: of events it will return, is what the probe is for.
def range_variants(start: date, end: date) -> list[dict]:
    span = f"{start:%Y%m%d}-{end:%Y%m%d}"
    return [
        {"dates": span, "limit": 900},
        {"dates": span},
    ]


def _event_day(event: dict, fallback: date) -> date:
    """The day an event was played, from the event itself.

    A single-date request can stamp every row with the date it asked for. A
    range cannot: one response spans weeks, and a row carrying the wrong day
    would be filtered by the wrong season start and land on the wrong line of
    the daily ledger. So this reads the event's own date, and the probe checks
    that it agrees with the day the per-day walk found the match on.
    """
    raw = str(event.get("date") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return fallback


def _rows_by_match(rows: list[dict]) -> dict[tuple, dict]:
    """Rows keyed by the match and side they describe."""
    return {
        (r["date"], r["competition_key"], r["team"], r["opponent"]): r
        for r in rows
    }


def probe_soccer_range(league: str, start: date, end: date) -> dict:
    """Whether one range request returns what a span of daily requests does.

    A league-season is 304 daily requests and about five minutes. If the
    scoreboard honours a date range, it is a handful of requests instead, and a
    benchmark recompute stops being an overnight job. The catch is the one this
    project keeps meeting: a feed that *accepts* a parameter and quietly
    returns less is worse than one that refuses it, because the result is a
    smaller pool, a lower benchmark, and every score above it larger.

    So this does not ask whether the range works. It walks the span a day at a
    time -- exactly as production does -- then asks for the same span in one
    request, turns both into scored rows through ``_soccer_rows``, and compares
    them match by match. Anything the range is missing, anything extra, and any
    match whose score differs is named.

    Margins and clean sheets are counted from both paths and reported side by
    side, because those are the two figures that would degrade *silently* if
    the range returned matches without their scores: a missing goal total reads
    as a 0-0, which is a clean sheet for both sides and a big win for neither.
    """
    out: dict[str, object] = {
        "league": league,
        "span": f"{start.isoformat()} to {end.isoformat()}",
        "days": (end - start).days + 1,
    }
    if league not in LEAGUE_PATHS:
        out["path"] = f"FAILED: no ESPN path configured for {league}"
        return out
    sport, path = LEAGUE_PATHS[league]
    out["path"] = f"{sport}/{path}"
    url = f"{BASE}/{sport}/{path}/scoreboard"

    # --- the baseline: one request a day, which is what production does ------
    began = time.monotonic()
    day_rows: list[dict] = []
    day_events = 0
    unread = 0
    label = ""
    day = start
    while day <= end:
        try:
            board = scoreboard(league, day)
        except Exception as exc:  # noqa: BLE001 -- one date, not the probe
            unread += 1
            out.setdefault("unread_dates", []).append(f"{day}: {type(exc).__name__}")
            day += timedelta(days=1)
            continue
        label = label or scoreboard_league_name(board)
        events = board.get("events") or []
        day_events += len(events)
        for event in events:
            day_rows.append((day, event))
        day += timedelta(days=1)
    out["day_by_day_seconds"] = round(time.monotonic() - began, 1)
    out["day_by_day_requests"] = out["days"]
    out["day_by_day_events"] = day_events
    out["day_by_day_unread"] = unread
    out["league_name_day_by_day"] = label or "(absent)"

    baseline = _rows_by_match([
        row for day, event in day_rows
        for row in _soccer_rows(event, league, day, label)
    ])
    out["day_by_day_rows"] = len(baseline)
    out["day_by_day_big_wins"] = sum(
        1 for r in baseline.values() if r["goals_for"] - r["goals_against"] >= 3)
    out["day_by_day_clean_sheets"] = sum(
        1 for r in baseline.values()
        if r["goals_against"] == 0 and r["goals_for"] > r["goals_against"])

    # --- the range, uncached, one shape at a time ---------------------------
    for params in range_variants(start, end):
        shape = "dates=range" + (" + limit" if "limit" in params else "")
        began = time.monotonic()
        try:
            payload = _get(url, params)
        except Exception as exc:  # noqa: BLE001
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            out[shape] = f"FAILED ({status}): {type(exc).__name__}"
            continue
        seconds = round(time.monotonic() - began, 1)
        events = payload.get("events") or []
        name = scoreboard_league_name(payload)
        rows = _rows_by_match([
            row for event in events
            for row in _soccer_rows(event, league, _event_day(event, start), name)
        ])

        report: dict[str, object] = {
            "seconds": seconds,
            "requests": 1,
            "events": len(events),
            "rows": len(rows),
            "league_name": name or "(absent)",
            "distinct_dates": len({key[0] for key in rows}),
            "big_wins": sum(
                1 for r in rows.values() if r["goals_for"] - r["goals_against"] >= 3),
            "clean_sheets": sum(
                1 for r in rows.values()
                if r["goals_against"] == 0 and r["goals_for"] > r["goals_against"]),
        }

        missing = sorted(set(baseline) - set(rows))
        extra = sorted(set(rows) - set(baseline))
        differs = [
            key for key in set(baseline) & set(rows)
            if any(baseline[key][field] != rows[key][field]
                   for field in ("goals_for", "goals_against",
                                 "shootout_for", "shootout_against"))
        ]
        report["missing"] = len(missing)
        report["extra"] = len(extra)
        report["scores_differ"] = len(differs)
        # Every one of them, not a sample. The terminal shows the first few;
        # the file this is written to carries the lot, because "eleven matches
        # missing" is a fact and *which* eleven is the diagnosis.
        report["missing_matches"] = [" ".join(map(str, k)) for k in missing]
        report["extra_matches"] = [" ".join(map(str, k)) for k in extra]
        report["differing_matches"] = [
            f"{' '.join(map(str, k))}: day-by-day "
            f"{baseline[k]['goals_for']:.0f}-{baseline[k]['goals_against']:.0f}, "
            f"range {rows[k]['goals_for']:.0f}-{rows[k]['goals_against']:.0f}"
            for k in sorted(differs)
        ]
        if not baseline:
            # Two empty answers agree about nothing. A span the walk found no
            # completed matches in -- a blocked host, a quiet fortnight, a
            # season that had not started -- would otherwise read as proof
            # that the range works, on no evidence at all.
            report["verdict"] = (
                "NO EVIDENCE -- the day-by-day walk found no completed match "
                "in this span, so there is nothing for the range to match. "
                "Pick a span with league football in it."
            )
        elif not missing and not extra and not differs:
            report["verdict"] = (
                "IDENTICAL -- the range returns exactly what the walk does")
        else:
            report["verdict"] = "DIFFERENT -- do not use the range; see the samples"
        out[shape] = report
    return out


def probe_soccer(league: str, day: date | None = None) -> dict:
    """Reachability and shape check for one soccer competition."""
    day = day or date(2025, 10, 25)
    result: dict[str, object] = {"league": league, "date": day.isoformat()}
    if league not in LEAGUE_PATHS:
        result["path"] = f"FAILED: no ESPN path configured for {league}"
        return result

    result["path"] = "/".join(LEAGUE_PATHS[league])
    try:
        board = scoreboard(league, day)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "?")
        result["scoreboard"] = f"FAILED ({status}): {type(exc).__name__}: {exc}"
        return result

    events = board.get("events", [])
    result["scoreboard"] = "ok"
    result["events"] = len(events)
    name = scoreboard_league_name(board)
    result["league_name"] = name or "(absent -- falling back to the key)"
    rows = [r for e in events for r in _soccer_rows(e, league, day, name)]
    result["team_rows"] = len(rows)
    if rows:
        from whul.scoring.competition import classify_key

        labels = sorted({r["competition"] for r in rows})
        result["competition_labels"] = labels[:4]
        result["classified_as"] = {
            label: (lambda c: f"{c.tier.value} ({c.win_points} per win)")(
                classify_key(league, label)
            )
            for label in labels[:4]
        }
        result["sample"] = rows[0]
    return result


def probe_results(league: str, day: date | None = None) -> dict:
    """Reachability and shape check for a results-only league.

    Reports which request shape the endpoint accepted, so a league that rejects
    the usual parameters is diagnosable rather than simply broken.
    """
    day = day or default_probe_date()
    sport, path = LEAGUE_PATHS[league]
    result: dict[str, object] = {"league": league, "date": day.isoformat()}

    # Probe the team list first: it establishes whether the sport/league path is
    # valid at all, independently of whether that date had games.
    eligible = load_eligible_teams(league)
    result["eligible_teams"] = len(eligible)
    if not eligible:
        result["teams_endpoint"] = "EMPTY -- the sport/league path may be wrong"

    attempts: list[str] = []
    board = None
    for params in scoreboard_variants(league, day):
        shape = ",".join(k for k in params if k != "dates") or "dates only"
        try:
            board = _get(
                f"{BASE}/{sport}/{path}/scoreboard",
                params,
                cache_key=f"{league}/scoreboard/{day.isoformat()}",
            )
            result["accepted_params"] = shape
            break
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            attempts.append(f"{shape} -> {status}")

    if board is None:
        result["scoreboard"] = f"FAILED: every request shape rejected ({'; '.join(attempts)})"
        return result
    if attempts:
        result["rejected_params"] = "; ".join(attempts)

    events = board.get("events", [])
    result["scoreboard"] = "ok"
    result["events"] = len(events)
    rows = [r for r in (_event_rows(e, league, day.year, day) for e in events) if r]
    result["parsed_games"] = len(rows)
    if rows:
        if league in CONFERENCE_REQUIRED:
            with_conf = sum(1 for r in rows if r["home_conference"] and r["away_conference"])
            result["conference_coverage"] = f"{with_conf}/{len(rows)}"
        else:
            result["conference_coverage"] = "not used by this league's scoring"
        result["sample"] = rows[0]

    if rows and eligible:
        seen = {r["home_team"] for r in rows} | {r["away_team"] for r in rows}
        outside = sorted(seen - eligible)
        result["opponents_outside_division"] = len(outside)
        if outside:
            result["example_outside"] = outside[:3]
    return result


def summary(league: str, event_id: str) -> dict:
    sport, path = LEAGUE_PATHS[league]
    return _get(
        f"{BASE}/{sport}/{path}/summary",
        {"event": event_id},
        cache_key=f"{league}/summary/{event_id}",
    )


def _position(entry: dict, positions: dict[str, str] | None = None) -> str:
    """Player position.

    ESPN carries this at ``entry["athlete"]["position"]``. The entry itself also
    has a ``position`` key, but it is an empty dict -- reading that one is what
    made positions look absent. Both are checked, then the roster map as a last
    resort for a player the boxscore does not describe.

    Values are the generic ``G`` / ``F`` / ``C`` (and hyphenated forms like
    ``G-F``), not the fine-grained PG/SG/SF/PF, which is all the Backcourt and
    Frontcourt split needs.
    """
    athlete = entry.get("athlete", {}) or {}
    for candidate in (
        entry.get("position"),
        athlete.get("position"),
    ):
        if isinstance(candidate, dict):
            abbr = candidate.get("abbreviation") or candidate.get("displayName") or ""
            if abbr:
                return str(abbr)
        elif isinstance(candidate, str) and candidate:
            return candidate

    if positions:
        return positions.get(str(athlete.get("id", "")), "")
    return ""


def _parse_box(
    payload: dict,
    event_id: str,
    day: date,
    season: int,
    season_type: int,
    positions: dict[str, str] | None = None,
) -> list[dict]:
    """Flatten one game's boxscore into per-player rows.

    ESPN returns stats as a parallel list of labels and string values, so
    everything is looked up by label rather than by position.
    """
    rows: list[dict] = []
    for team_block in payload.get("boxscore", {}).get("players", []):
        team = team_block.get("team", {}).get("abbreviation", "")
        for stat_block in team_block.get("statistics", []):
            labels = [label.upper() for label in stat_block.get("labels", [])]
            for entry in stat_block.get("athletes", []):
                athlete = entry.get("athlete", {})
                values = entry.get("stats", [])
                if not values:
                    continue  # did not play
                stats = dict(zip(labels, values))
                rows.append(
                    {
                        "season": season,
                        "season_type": season_type,
                        "game_id": event_id,
                        "game_date": day.isoformat(),
                        "team": team,
                        "athlete_id": str(athlete.get("id", "")),
                        "athlete_display_name": athlete.get("displayName", ""),
                        "athlete_position_abbreviation": _position(entry, positions),
                        "points": stats.get("PTS"),
                        "rebounds": stats.get("REB"),
                        "assists": stats.get("AST"),
                        "steals": stats.get("STL"),
                        "blocks": stats.get("BLK"),
                        "turnovers": stats.get("TO"),
                        "three_point_field_goals_made": (stats.get("3PT") or "0-0").split("-")[0],
                        "plus_minus": stats.get("+/-", "0"),
                    }
                )
    return rows


def load_nba_player_box(seasons: list[int], verbose: bool = True) -> pd.DataFrame:
    """Per-player, per-game box scores for whole NBA seasons.

    Slow on a cold cache -- a season is ~250 game days and ~1,300 games, so a
    backfill is thousands of requests. Responses are cached per date and per
    game, so this is a one-time cost and a daily update is a single date.
    """
    # Only pay for the roster walk if the boxscore turns out to need it; that is
    # decided lazily on the first game, since it is a property of the feed.
    positions: dict[str, str] | None = None

    rows: list[dict] = []
    for season in seasons:
        days = season_dates(season)
        if verbose:
            print(f"  {season}: walking {len(days)} dates ...", flush=True)
        for index, day in enumerate(days):
            try:
                board = scoreboard("nba", day)
            except Exception:
                continue
            for event in board.get("events", []):
                competition = (event.get("competitions") or [{}])[0]
                if not competition.get("status", {}).get("type", {}).get("completed"):
                    continue
                season_type = int(
                    (event.get("season") or {}).get("type", SEASON_TYPE_REGULAR)
                )
                try:
                    parsed = _parse_box(
                        summary("nba", event["id"]),
                        event["id"],
                        day,
                        season,
                        season_type,
                        positions,
                    )
                except Exception:
                    continue

                if positions is None and any(
                    not r["athlete_position_abbreviation"] for r in parsed
                ):
                    positions = load_positions("nba")
                    if verbose:
                        print(
                            f"  boxscore lacks positions; loaded {len(positions)} from rosters",
                            flush=True,
                        )
                    parsed = _parse_box(
                        summary("nba", event["id"]),
                        event["id"],
                        day,
                        season,
                        season_type,
                        positions,
                    )
                rows.extend(parsed)
            if verbose and index % 50 == 0 and index:
                # flush: stdout is block-buffered when redirected to a file, so
                # without this a long backfill shows no progress until it ends.
                print(
                    f"    {index}/{len(days)} dates, {len(rows):,} rows",
                    flush=True,
                )
    return pd.DataFrame(rows)


def default_probe_date(today: date | None = None) -> date:
    """A date likely to have games: mid-January of the most recent season.

    Yesterday is a poor default for basketball -- for much of the year it lands in
    the offseason and returns zero events, which reads like a failure.
    """
    today = today or date.today()
    candidate = date(today.year, 1, 15)
    return candidate if candidate <= today else date(today.year - 1, 1, 15)


def load_positions(league: str = "nba") -> dict[str, str]:
    """Map athlete id -> position abbreviation, from every team's roster.

    Insurance rather than the primary path: the boxscore does carry position, so
    this only fills in players it fails to describe. Thirty requests for the NBA,
    cached, and skipped entirely when the boxscore already resolves everyone.
    """
    sport, path = LEAGUE_PATHS[league]
    teams = _get(
        f"{BASE}/{sport}/{path}/teams",
        {"limit": 1000},
        cache_key=f"{league}/teams",
    )

    entries: list[dict] = []
    for sport_block in teams.get("sports", []):
        for league_block in sport_block.get("leagues", []):
            entries.extend(league_block.get("teams", []))

    positions: dict[str, str] = {}
    for entry in entries:
        team_id = str((entry.get("team") or {}).get("id", ""))
        if not team_id:
            continue
        try:
            roster = _get(
                f"{BASE}/{sport}/{path}/teams/{team_id}/roster",
                {},
                cache_key=f"{league}/roster/{team_id}",
            )
        except Exception:
            continue
        for athlete in roster.get("athletes", []):
            # Some leagues nest athletes one level deeper, under position groups.
            group = athlete.get("items") if isinstance(athlete, dict) else None
            for person in group or [athlete]:
                pid = str(person.get("id", ""))
                pos = (person.get("position") or {}).get("abbreviation", "")
                if pid and pos:
                    positions[pid] = pos
    return positions


def daily_update_cost(league: str = "nba", day: date | None = None) -> float:
    """Seconds to pull one date -- exactly what the nightly job does.

    Measured on a cold cache: the cache is bypassed so the number reflects real
    network cost rather than a replay.
    """
    day = day or default_probe_date()
    sport, path = LEAGUE_PATHS[league]

    started = time.monotonic()
    board = _get(f"{BASE}/{sport}/{path}/scoreboard", {"dates": day.strftime("%Y%m%d")})
    for event in board.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        if not competition.get("status", {}).get("type", {}).get("completed"):
            continue
        _get(f"{BASE}/{sport}/{path}/summary", {"event": event["id"]})
    return time.monotonic() - started


def probe(league: str = "nba", day: date | None = None) -> dict:
    """Check reachability and schema without pulling a whole season.

    Returns a dict of stage -> outcome so a failure says which stage broke.
    """
    day = day or default_probe_date()
    result: dict[str, object] = {"league": league, "date": day.isoformat()}

    try:
        board = scoreboard(league, day)
        result["scoreboard"] = "ok"
        events = board.get("events", [])
        result["events"] = len(events)
    except Exception as exc:
        result["scoreboard"] = f"FAILED: {type(exc).__name__}: {exc}"
        return result

    if not events:
        result["boxscore"] = "skipped (no games that date)"
        return result

    try:
        payload = summary(league, events[0]["id"])
        players = payload.get("boxscore", {}).get("players", [])
        result["boxscore"] = "ok"
        result["teams_in_box"] = len(players)
        if players:
            result["stat_labels"] = players[0].get("statistics", [{}])[0].get("labels", [])
        rows = _parse_box(payload, events[0]["id"], day, day.year, SEASON_TYPE_REGULAR)
        result["parsed_rows"] = len(rows)
        inline = sum(1 for r in rows if r["athlete_position_abbreviation"])
        result["positions_inline"] = f"{inline}/{len(rows)}"

        if inline < len(rows):
            try:
                positions = load_positions(league)
                rows = _parse_box(
                    payload, events[0]["id"], day, day.year, SEASON_TYPE_REGULAR, positions
                )
                filled = sum(1 for r in rows if r["athlete_position_abbreviation"])
                result["position_map_size"] = len(positions)
                result["positions_after_roster"] = f"{filled}/{len(rows)}"
            except Exception as exc:
                result["positions_after_roster"] = f"FAILED: {type(exc).__name__}: {exc}"

        result["sample"] = rows[0] if rows else None
    except Exception as exc:
        result["boxscore"] = f"FAILED: {type(exc).__name__}: {exc}"
    return result
