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
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

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
DOMESTIC_CUPS = {
    "epl": ("facup", "efl_cup"),
    "laliga": ("copadelrey",),
    "bundesliga": ("dfbpokal",),
    "seriea": ("coppaitalia",),
    "ligue1": ("coupedefrance",),
    "mls": (),
    "nwsl": (),
}

#: (start month, day) -> (end month, day), and whether the season label is the
#: calendar year it ends in. Football is labelled by the year it starts.
SEASON_WINDOWS = {
    "ncaaf": ((8, 1), (1, 31), True),
    "ncaam": ((11, 1), (4, 15), True),
    "ncaaw": ((11, 1), (4, 15), True),
    "ncaabaseball": ((2, 1), (6, 30), False),
    "ncaasoftball": ((2, 1), (6, 30), False),
    # European seasons run August to May and are labelled by the year they end.
    **{key: ((8, 1), (5, 31), True) for key in
       ("epl", "laliga", "seriea", "bundesliga", "ligue1",
        "ucl", "uel", "uecl", "facup", "efl_cup", "copadelrey",
        "dfbpokal", "coppaitalia", "coupedefrance")},
    # MLS and NWSL run within a calendar year.
    "mls": ((2, 20), (12, 15), False),
    "nwsl": ((3, 1), (11, 30), False),
    "nba": (NBA_SEASON_START, NBA_SEASON_END, True),
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
    start_md, end_md, ends_in_label_year = SEASON_WINDOWS[league]
    if ends_in_label_year:
        start = date(season - 1, *start_md)
        end = date(season, *end_md)
    else:
        start = date(season, *start_md)
        end = date(season, *end_md)
    end = min(end, date.today())
    if end < start:
        return []
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def season_label(league: str, day: date) -> int:
    """The season number a date belongs to, in this feed's numbering.

    European football and the college seasons are labelled by the year they
    *end* in, so a match in September 2026 is part of season 2027. Asking for
    the calendar year instead returns the season that finished in May -- a full
    set of results, from last year, which is exactly the kind of wrong answer
    that looks right.
    """
    start_md, _, ends_in_label_year = SEASON_WINDOWS[league]
    if not ends_in_label_year:
        return day.year
    return day.year + 1 if (day.month, day.day) >= start_md else day.year


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
    combos: list[tuple[str, dict]] = [
        ("groups+limit", {"dates": dates, "limit": 900, **({"groups": group} if group else {})}),
        ("groups only", {"dates": dates, **({"groups": group} if group else {})}),
        ("limit only", {"dates": dates, "limit": 900}),
        ("bare", {"dates": dates}),
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
        competitions += list(DOMESTIC_CUPS.get(league, ())) + list(EUROPEAN_COMPETITIONS)

    rows: list[dict] = []
    for competition in competitions:
        if competition not in LEAGUE_PATHS:
            continue
        for season in seasons:
            days = season_dates(season, competition)
            if verbose:
                print(f"  {competition} {season}: {len(days)} dates ...", flush=True)
            for index, day in enumerate(days):
                try:
                    board = scoreboard(competition, day)
                except Exception:
                    continue
                name = scoreboard_league_name(board)
                for event in board.get("events", []):
                    rows.extend(_soccer_rows(event, competition, day, name))
                if verbose and index and index % 60 == 0:
                    print(f"    {index}/{len(days)} dates, {len(rows):,} rows", flush=True)
    return pd.DataFrame(rows)


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

    # The round name matters: it distinguishes a qualifying tie from the
    # competition proper, and the knockout play-off from either.
    name = league_name or (event.get("league") or {}).get("name", "") or competition
    notes = " ".join(
        str(n.get("headline", "")) for n in (inner.get("notes") or []) if isinstance(n, dict)
    )
    season_type = ((event.get("season") or {}).get("slug") or "").replace("-", " ")
    competition_label = " ".join(p for p in (name, notes, season_type) if p).strip()

    rows = []
    for side, other in ((home, away), (away, home)):
        rows.append({
            "team": (side.get("team") or {}).get("displayName", ""),
            "opponent": (other.get("team") or {}).get("displayName", ""),
            "date": day.isoformat(),
            "competition": competition_label,
            "competition_key": competition,
            "goals_for": score(side),
            "goals_against": score(other),
        })
    return rows


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
