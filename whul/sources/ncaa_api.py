"""NCAA stats API (henrygd/ncaa-api).

A thin wrapper over the NCAA's own stats site, suggested as an alternative to
ESPN for the college leagues. Its advantage is decisive for our purposes:
**division membership is explicit in the URL** (``football/fbs``, ``basketball-men/d1``),
where ESPN's teams endpoint ignores the `groups` filter and returns all 760
college football programs regardless.

    scoreboard: /scoreboard/{sport}/{division}/{year}/{mm}/{dd}/all-conf

A public instance runs at ``ncaa-api.henrygd.me``; it is rate limited and may
require an ``x-ncaa-key`` header. For daily production use the project is
self-hostable, which removes the dependency on someone else's uptime.

UNVERIFIED: unreachable from the environment this was written in. Run
``python -m whul.cli probe-ncaa-api`` from a machine with access.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE = "https://ncaa-api.henrygd.me"

#: Sports this scoreboard addresses by *week* rather than by date. The NCAA's
#: own football URL is /scoreboard/football/fbs/{year}/{week}/all-conf, and this
#: API passes the path through: a request for .../2024/11/09/all-conf is read as
#: week 11 of 2024, with the "09" discarded. Walking 184 dates of a season
#: therefore asks for only the six distinct month-numbers those dates contain --
#: weeks 8 to 12 and 1 -- and returns about six weeks of a fifteen-week season,
#: which is how ~800 games a season came back as ~420 with nothing reporting a
#: failure. Basketball and the diamond sports are addressed by date.
WEEK_INDEXED = {"ncaaf"}

#: Weeks to ask for. Walked in full rather than stopped at the first empty one:
#: seasons differ in how many weeks they ran, and the bowls and playoff sit at
#: the top of the range, so any guess at an end cuts the postseason off some
#: seasons and not others. An empty week costs one request.
FOOTBALL_WEEKS = range(1, 21)

#: Statuses worth waiting out rather than treating as "no games that day".
RETRY_STATUSES = (429, 500, 502, 503, 504)
RETRY_BACKOFF = 2.0

#: Fraction of a season's dates that may fail before the pull is not a season.
FAILED_DATE_LIMIT = 0.02

#: Roughly how many games a team plays, used only to notice a pull that came
#: back thin. Football is 12 regular-season games plus a bowl.
EXPECTED_GAMES_PER_TEAM = {
    "ncaaf": 12, "ncaam": 31, "ncaaw": 31,
    "ncaabaseball": 55, "ncaasoftball": 55,
}
THIN_SEASON_LIMIT = 0.75


class IncompleteSeason(RuntimeError):
    """Too many dates failed for what came back to be called a season."""


CACHE = Path("data/cache/ncaa_api")
REQUEST_PAUSE = 0.5
TIMEOUT = 30

#: Our league key -> (sport path, division path).
SPORT_PATHS = {
    "ncaaf": ("football", "fbs"),
    "ncaam": ("basketball-men", "d1"),
    "ncaaw": ("basketball-women", "d1"),
    "ncaabaseball": ("baseball", "d1"),
    "ncaasoftball": ("softball", "d1"),
}


def _get(path: str, cache_key: str | None = None) -> dict:
    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        if cached.exists():
            return json.loads(cached.read_text())

    response = requests.get(
        f"{BASE}{path}", timeout=TIMEOUT, headers={"User-Agent": "whul-fantasy/0.1"}
    )
    response.raise_for_status()
    payload = response.json()

    if cache_key:
        cached = CACHE / f"{cache_key}.json"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload))

    time.sleep(REQUEST_PAUSE)
    return payload


def scoreboard(league: str, day: date) -> dict:
    sport, division = SPORT_PATHS[league]
    path = f"/scoreboard/{sport}/{division}/{day.year}/{day.month:02d}/{day.day:02d}/all-conf"
    return _get(path, cache_key=f"{league}/{day.isoformat()}")


def scoreboard_week(league: str, season: int, week: int) -> dict:
    """One week of a week-indexed sport.

    The NCAA's football scoreboard is addressed by week, not by date, and this
    API passes the path straight through. A request for .../2024/11/09/all-conf
    is read as *week 11* of 2024 with the "09" discarded, which is why every
    date in November came back with the same 53 games.
    """
    sport, division = SPORT_PATHS[league]
    path = f"/scoreboard/{sport}/{division}/{season}/{week:02d}/all-conf"
    return _get(path, cache_key=f"{league}/{season}-week{week:02d}")


#: The API exposes several name forms and does not populate all of them; the
#: first non-empty one wins. `full` came back blank for football, where `short`
#: carries the value.
NAME_KEYS = ("full", "short", "seo", "char6", "sixCharacter", "nameShort")
CONFERENCE_KEYS = ("conferenceName", "name", "conferenceSeo")


def _team_name(side: dict) -> str:
    names = side.get("names") or {}
    for key in NAME_KEYS:
        value = names.get(key)
        if value:
            return str(value)
    for key in ("nameShort", "name", "shortName"):
        if side.get(key):
            return str(side[key])
    return ""


def _team_conference(side: dict) -> str:
    conferences = side.get("conferences") or []
    if conferences and isinstance(conferences[0], dict):
        for key in CONFERENCE_KEYS:
            if conferences[0].get(key):
                return str(conferences[0][key])
    for key in ("conference", "conferenceName"):
        if side.get(key):
            return str(side[key])
    return ""


#: Game states that mean the result is settled. The API writes overtime into
#: the same field -- "FINAL(OT)", "Final/2OT" -- so an equality test against
#: "final" throws away every game that went to overtime, and throws it away
#: as though it had not been played.
FINAL_STATES = ("final", "complete")


def _is_final(state) -> bool:
    text = str(state or "").strip().lower()
    return any(text.startswith(prefix) for prefix in FINAL_STATES)


#: Where the payload puts the game's own date, and how it spells it. A request
#: for one date can answer with a week's slate, so the requested date is not the
#: date the game was played -- stamping it on the row loses the real one, which
#: is what the live start-date filter reads and what tells a January bowl from a
#: November Saturday.
DATE_KEYS = ("startDate", "gameDate", "date", "startDateTime")
DATE_FORMATS = ("%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y")


def _game_day(inner: dict, fallback: date) -> date:
    """The date the game was played, falling back to the date requested."""
    from datetime import datetime

    for key in DATE_KEYS:
        text = str(inner.get(key) or "").strip()[:10]
        if not text:
            continue
        for shape in DATE_FORMATS:
            try:
                return datetime.strptime(text, shape).date()
            except ValueError:
                continue
    epoch = inner.get("startTimeEpoch")
    try:
        return datetime.fromtimestamp(int(epoch)).date()
    except (TypeError, ValueError):
        return fallback


def parse_scoreboard(payload: dict, league: str, day: date) -> list[dict]:
    """Flatten one date's games into rows matching the ESPN adapter's shape.

    Keeping the column names identical means the same scoring modules work
    against either source.

    The season is the one the date belongs to, not the calendar year it falls
    in. College football and basketball cross new year, so labelling by the
    calendar year cuts every season in half: a thirteen-win football season
    becomes eleven wins in one season and two bowl games in the next, and the
    benchmark is then drawn from half-seasons that no team ever played.
    """
    from whul.sources.espn import season_label

    rows: list[dict] = []
    for game in payload.get("games", []):
        inner = game.get("game", game)
        home = inner.get("home", {}) or {}
        away = inner.get("away", {}) or {}

        def score(side: dict) -> float | None:
            try:
                return float(side.get("score"))
            except (TypeError, ValueError):
                return None

        played = _game_day(inner, day)
        rows.append(
            {
                "season": season_label(league, played),
                "game_id": inner.get("gameID") or inner.get("url", ""),
                "game_date": played.isoformat(),
                "season_type": 2,
                "completed": _is_final(inner.get("gameState", "")),
                "home_team": _team_name(home),
                "away_team": _team_name(away),
                "home_conference": _team_conference(home),
                "away_conference": _team_conference(away),
                "home_score": score(home),
                "away_score": score(away),
                "notes": inner.get("title", "") or inner.get("bracketRound", ""),
            }
        )
    return rows


def season_days(league: str, seasons: list[int]) -> list[date]:
    """Dates to walk, reusing the per-sport season windows."""
    from whul.sources import espn

    days: list[date] = []
    for season in seasons:
        days.extend(espn.season_dates(season, league))
    return days


def load_team_results(
    league: str, seasons: list[int], verbose: bool = True
) -> pd.DataFrame:
    """Completed results for whole seasons."""
    if league in WEEK_INDEXED:
        return _load_by_week(league, seasons, verbose)
    return _load_by_date(league, seasons, verbose)


def _load_by_week(
    league: str, seasons: list[int], verbose: bool = True
) -> pd.DataFrame:
    """A week-indexed sport, one request per week.

    Seventeen requests a season rather than a hundred and eighty-four, and --
    the point of the change -- seventeen *different* weeks rather than the six
    that a walk of dates collapses onto.

    The week range is walked in full and a week with nothing in it is simply
    empty: the seasons differ in how many weeks they ran, and the postseason
    lives at the top of the range, so guessing an end would cut bowls off some
    seasons and not others.
    """
    rows: list[dict] = []
    failures: dict[str, int] = {}
    for season in seasons:
        found = 0
        for week in FOOTBALL_WEEKS:
            try:
                payload = _with_retry(lambda: scoreboard_week(league, season, week))
            except Exception as exc:  # noqa: BLE001 -- one week must not lose the season
                failures[_failure_label(exc)] = failures.get(_failure_label(exc), 0) + 1
                continue
            week_rows = parse_scoreboard(payload, league, date(season, 8, 1))
            found += len(week_rows)
            rows.extend(week_rows)
        if verbose:
            print(f"    {season}: {found:,} game rows over "
                  f"{len(FOOTBALL_WEEKS)} weeks", flush=True)

    return _finish(rows, league, failures, len(seasons) * len(FOOTBALL_WEEKS), verbose)


def _load_by_date(
    league: str, seasons: list[int], verbose: bool = True
) -> pd.DataFrame:
    """A date-indexed sport, one request per date."""
    days = season_days(league, seasons)
    if verbose:
        print(f"  {league}: walking {len(days)} dates ...", flush=True)

    rows: list[dict] = []
    failures: dict[str, int] = {}
    for index, day in enumerate(days):
        try:
            payload = _with_retry(lambda: scoreboard(league, day))
        except Exception as exc:  # noqa: BLE001 -- one date must not lose the season
            # Counted, never swallowed. This API is rate limited, and a date that
            # 429s is a Saturday of missing games -- which is not an error
            # anywhere downstream, just a season in which nobody played much.
            failures[_failure_label(exc)] = failures.get(_failure_label(exc), 0) + 1
            continue
        rows.extend(parse_scoreboard(payload, league, day))
        if verbose and index and index % 50 == 0:
            print(f"    {index}/{len(days)} dates, {len(rows):,} games", flush=True)

    return _finish(rows, league, failures, len(days), verbose)


def _failure_label(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _finish(
    rows: list[dict], league: str, failures: dict[str, int],
    attempted: int, verbose: bool,
) -> pd.DataFrame:
    """Report what could not be fetched, then dedupe and describe what could."""
    if failures:
        detail = ", ".join(f"{n} x {why}" for why, n in sorted(failures.items()))
        message = (
            f"{league}: {sum(failures.values())} of {attempted} requests could not "
            f"be fetched ({detail})"
        )
        if verbose:
            print(f"  ! {message}", flush=True)
        if sum(failures.values()) > attempted * FAILED_DATE_LIMIT:
            raise IncompleteSeason(
                message + ". Every missing request is missing games, and a "
                "benchmark drawn from a partial season sets the bar too low for "
                "every team in it. Re-run once the API stops refusing -- "
                "successful requests are cached, so a re-run only fetches what "
                "is still missing."
            )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame[frame["completed"]]
    frame = _once_each(frame, league, verbose)
    if verbose:
        _report_coverage(frame, league)
    return frame


def _with_retry(call, attempts: int = 3):
    """Retry the errors that go away on their own.

    A rate limit is a wait, not an answer. Without this the walk treats it as a
    request with no games.
    """
    for attempt in range(attempts):
        try:
            return call()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in RETRY_STATUSES or attempt == attempts - 1:
                raise
        except requests.RequestException:
            if attempt == attempts - 1:
                raise
        time.sleep(RETRY_BACKOFF * (2 ** attempt))
    raise RuntimeError("unreachable")


def _report_coverage(frame: pd.DataFrame, league: str) -> None:
    """Games per team per season, which is how a half-empty pull shows itself.

    A season total looks plausible at almost any size -- there is no number of
    college football games that reads as obviously wrong. Games per team does:
    everyone plays about twelve, and a pull averaging four has lost two thirds
    of the season without failing at anything.
    """
    expected = EXPECTED_GAMES_PER_TEAM.get(league)
    for season, games in frame.groupby("season"):
        counts = pd.concat([games["home_team"], games["away_team"]]).value_counts()
        counts = counts[counts.index.astype(str) != ""]
        # The median, not the mean. A division's scoreboard carries its
        # opponents too -- about a hundred FCS teams appear in FBS results
        # having played the single game that put them there -- and averaged in,
        # they drag a complete season of 13.8 games a team down to 8.3, which
        # reads as a pull that lost a third of its games. The median ignores
        # them, because they are a minority of one-game visitors.
        middle = float(counts.median()) if len(counts) else 0.0
        flag = ""
        if expected and middle < expected * THIN_SEASON_LIMIT:
            flag = f"  <-- thin; a full season is about {expected} per team"
        dates = games["game_date"].nunique() if "game_date" in games else 0
        print(
            f"    {season}: {len(games):,} games, {len(counts)} teams, "
            f"{middle:.0f} games per team (median), "
            f"{dates} distinct game dates{flag}",
            flush=True,
        )


def _once_each(frame: pd.DataFrame, league: str, verbose: bool = True) -> pd.DataFrame:
    """One row per game, however many dates returned it.

    This scoreboard is week-based for some sports: a request for a Tuesday can
    come back with the whole week's games, so walking every date returns the
    same game several times over. Summed, that multiplies a team's wins and
    point differential by however many days its week spans -- which is not an
    error anywhere, just a season in which everyone played eighty games.
    """
    keyed = frame[frame["game_id"].astype(str) != ""]
    unkeyed = frame[frame["game_id"].astype(str) == ""]
    deduped = keyed.drop_duplicates(subset=["game_id"])
    if not unkeyed.empty:
        # No id to trust, so fall back to what identifies a game without one.
        unkeyed = unkeyed.drop_duplicates(
            subset=["season", "game_date", "home_team", "away_team"]
        )
    out = pd.concat([deduped, unkeyed], ignore_index=True)

    dropped = len(frame) - len(out)
    if dropped and verbose:
        print(
            f"  {league}: {dropped:,} duplicate game rows dropped "
            f"({len(out):,} distinct games)",
            flush=True,
        )
    return out.reset_index(drop=True)


def load_eligible_teams(league: str, seasons: list[int]) -> set[str]:
    """Teams in the division, taken from the games the API returns.

    The URL already restricts to the division, so every team appearing in these
    results belongs to it -- which is exactly what ESPN could not express.
    """
    results = load_team_results(league, seasons, verbose=False)
    if results.empty:
        return set()
    return set(results["home_team"]) | set(results["away_team"])


def daily_update_cost(league: str, day: date | None = None) -> float:
    """Seconds to pull one date -- the nightly job for a results-only league."""
    day = day or date(2025, 11, 15)
    sport, division = SPORT_PATHS[league]
    path = f"/scoreboard/{sport}/{division}/{day.year}/{day.month:02d}/{day.day:02d}/all-conf"
    started = time.monotonic()
    _get(path)
    return time.monotonic() - started


def probe(league: str = "ncaaf", day: date | None = None) -> dict:
    """Reachability and shape check, reporting what ESPN could not give us."""
    day = day or date(2025, 11, 15)
    result: dict[str, object] = {"league": league, "date": day.isoformat(), "base": BASE}
    try:
        payload = scoreboard(league, day)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "?")
        result["scoreboard"] = f"FAILED ({status}): {type(exc).__name__}: {exc}"
        return result

    result["scoreboard"] = "ok"
    rows = parse_scoreboard(payload, league, day)
    result["games"] = len(rows)
    if rows:
        with_conf = sum(1 for r in rows if r["home_conference"] and r["away_conference"])
        result["conference_coverage"] = f"{with_conf}/{len(rows)}"
        names = {r["home_team"] for r in rows} | {r["away_team"] for r in rows}
        result["teams_seen"] = len(names - {""})
        result["sample"] = rows[0]

    # When extraction comes up empty the payload keys are what is needed to fix
    # it, so report them rather than requiring another round trip.
    raw_games = payload.get("games") or []
    if raw_games and (not rows or not rows[0]["home_team"] or not rows[0]["home_conference"]):
        inner = raw_games[0].get("game", raw_games[0])
        result["raw_game_keys"] = sorted(inner)
        home = inner.get("home") or {}
        result["raw_home_keys"] = sorted(home)
        result["raw_home_names"] = home.get("names")
        result["raw_home_conferences"] = home.get("conferences")
    return result
