"""FBref season stats -- club soccer players.

Thirty-two rostered picks are club soccer players, and no source served them:
the league adapters return *team* results. The scorer has been waiting for this
all along -- ``whul.scoring.soccer.score_players`` resolves ``Comp``, ``MP``,
``Starts``, ``Min``, ``Gls``, ``Ast``, ``CrdY`` and ``CrdR``, which are FBref's
column names, and ``goal_points_for`` reads FBref's position codes. So this
adapter's whole job is to fetch the table and hand it over unchanged.

The Big 5 European leagues come from one table per season, which is the reason
to prefer this over five separate league pages:

    /en/comps/Big5/{season}/stats/players/{season}-Big-5-European-Leagues-Stats

with a ``Comp`` column naming the league on each row -- exactly what
``score_players`` normalizes against, so the five leagues stay separate without
five requests. MLS has its own page and its own calendar.

FBref asks for no more than one request every three seconds and blocks clients
that ignore it. Seasons are cached whole, so a re-run costs nothing.

UNVERIFIED: fbref.com is unreachable from the environment this was written in.
Run ``python scripts/probe-fbref.py`` from a machine with access; it reports
which stage fails and prints the columns it actually got.
"""

from __future__ import annotations

import io
import re
import time
from pathlib import Path

import pandas as pd
import requests

BASE = "https://fbref.com"
CACHE = Path("data/cache/fbref")
#: FBref's stated limit is one request per three seconds.
REQUEST_PAUSE = 3.5
TIMEOUT = 60

HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "accept": "text/html,application/xhtml+xml",
    "accept-language": "en-US,en;q=0.9",
}

#: FBref's ``Comp`` values carry a country prefix. Mapped to the league names
#: the roster uses, which are also the names each benchmark normalizes against.
COMPETITIONS = {
    "eng Premier League": "Premier League",
    "es La Liga": "La Liga",
    "it Serie A": "Serie A",
    "de Bundesliga": "Bundesliga",
    "fr Ligue 1": "Ligue 1",
    "Premier League": "Premier League",
    "La Liga": "La Liga",
    "Serie A": "Serie A",
    "Bundesliga": "Bundesliga",
    "Ligue 1": "Ligue 1",
    "Major League Soccer": "MLS",
}

#: Leagues this source produces, in the roster's spelling.
BIG_FIVE = ("Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1")

#: Columns ``score_players`` needs, under FBref's own names.
WANTED = ("Player", "Pos", "Squad", "Comp", "MP", "Starts", "Min",
          "Gls", "Ast", "CrdY", "CrdR")

#: The player table's id on both the Big 5 page and a single league's page.
TABLE_ID = "stats_standard"


class FeedUnavailable(RuntimeError):
    """FBref answered, but not with the table we asked for."""


def season_label(season: int) -> str:
    """European seasons are named for both years: 2025 is "2024-2025"."""
    return f"{season - 1}-{season}"


def _get(path: str, cache_key: str) -> str:
    cached = CACHE / f"{cache_key}.html"
    if cached.exists():
        return cached.read_text()

    response = requests.get(f"{BASE}{path}", headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(response.text)
    time.sleep(REQUEST_PAUSE)
    return response.text


def _uncomment(html: str) -> str:
    """FBref serves most of its tables inside HTML comments.

    The page renders them with JavaScript, so a parser that respects comments
    finds one table on a page showing six. Stripping the markers is the
    documented way round it and costs nothing when a table is not commented.
    """
    return html.replace("<!--", "").replace("-->", "")


def parse_players(html: str) -> pd.DataFrame:
    """The standard player table, in the shape ``score_players`` reads."""
    try:
        # flavor is pinned: given no tables, pandas otherwise falls through to a
        # parser we do not depend on and reports a missing package, which reads
        # as a broken install rather than as a page with no table on it.
        tables = pd.read_html(
            io.StringIO(_uncomment(html)), attrs={"id": TABLE_ID}, flavor="lxml"
        )
    except ValueError as exc:
        raise FeedUnavailable(
            f"no table with id {TABLE_ID!r} on the page. FBref renames these "
            f"occasionally, and serves a challenge page to clients it does not "
            f"like -- which parses as no tables at all. ({exc})"
        ) from exc
    if not tables:
        raise FeedUnavailable(f"table {TABLE_ID!r} came back empty")

    frame = _flatten(tables[0])
    missing = [c for c in WANTED if c not in frame.columns]
    if missing:
        raise FeedUnavailable(
            f"the table is missing {missing}; it has {sorted(frame.columns)[:25]}"
        )

    frame = frame[list(WANTED)].copy()
    # FBref repeats its header row every twenty-five players so the table stays
    # readable while scrolling. Parsed, each one is a player called "Player".
    frame = frame[frame["Player"].astype(str).str.strip().ne("Player")]
    frame = frame[frame["Player"].astype(str).str.strip().ne("")]

    for column in ("MP", "Starts", "Min", "Gls", "Ast", "CrdY", "CrdR"):
        # Minutes carry thousands separators: "1,842".
        text = frame[column].astype(str).str.replace(",", "", regex=False)
        frame[column] = pd.to_numeric(text, errors="coerce").fillna(0.0)

    _check_totals(frame)

    frame["league"] = frame["Comp"].astype(str).str.strip().map(COMPETITIONS)
    unknown = sorted(set(frame.loc[frame["league"].isna(), "Comp"].astype(str)))
    if unknown:
        raise FeedUnavailable(
            f"unrecognised competitions {unknown}. Each one is a whole league "
            f"of players that would be dropped without a word; add them to "
            f"COMPETITIONS."
        )
    return frame.reset_index(drop=True)


#: Columns whose values are counts of things that happened, so whole numbers.
COUNTING = ("MP", "Starts", "Gls", "Ast", "CrdY", "CrdR")


def _check_totals(frame: pd.DataFrame) -> None:
    """Refuse a table whose totals are not whole numbers.

    ``Gls`` and ``Ast`` appear twice, as season totals and as per-90 rates, and
    ``_flatten`` keeps the first of each because FBref puts the rates last. If
    that ordering ever changes, or a group is renamed so the total is dropped,
    the rate is taken instead -- and a 29-goal season becomes 0.79, which is a
    plausible-looking number that no other check would question.
    """
    fractional = [
        column for column in COUNTING
        if column in frame.columns and ((frame[column] % 1) != 0).any()
    ]
    if fractional:
        raise FeedUnavailable(
            f"{fractional} hold fractional values, so these are per-90 rates "
            f"rather than season totals. Scored, every player's season would "
            f"come out about a fortieth of its real size."
        )


def _flatten(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse FBref's two-row header, keeping the counting stats.

    The table repeats several column names: ``Gls`` appears under Performance
    as a season total and again under Per 90 Minutes as a rate. Taking the first
    occurrence of each name keeps the totals, because FBref orders the groups
    Playing Time, Performance, Expected, Progression, Per 90 Minutes -- the
    rates always come last. Taking the last would score a season on goals per
    ninety, which is a number between 0 and 2 and looks entirely reasonable.
    """
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame.loc[:, ~frame.columns.duplicated(keep="first")]
    # Level 0 is the group ("Playing Time"), level 1 the column ("MP"). An
    # ungrouped column has a placeholder in level 0, so level 1 is the name in
    # every case; the fallback is only for a table with the levels the other way
    # round, which FBref has served before.
    groups = frame.columns.get_level_values(0)
    columns = frame.columns.get_level_values(1)
    names = [str(column) if not str(column).startswith("Unnamed") else str(group)
             for group, column in zip(groups, columns)]
    flat = frame.copy()
    flat.columns = names
    return flat.loc[:, ~pd.Index(names).duplicated(keep="first")]


def load_big_five(season: int) -> pd.DataFrame:
    """One season of the five European leagues, in one request."""
    label = season_label(season)
    html = _get(
        f"/en/comps/Big5/{label}/stats/players/{label}-Big-5-European-Leagues-Stats",
        cache_key=f"big5-{label}",
    )
    return parse_players(html).assign(season=season)


def load_mls(season: int) -> pd.DataFrame:
    """MLS runs within a calendar year and has its own page."""
    html = _get(
        f"/en/comps/22/{season}/stats/{season}-Major-League-Soccer-Stats",
        cache_key=f"mls-{season}",
    )
    return parse_players(html).assign(season=season, league="MLS")


def load_players(seasons: list[int], verbose: bool = True) -> pd.DataFrame:
    """Every rostered club-soccer league, for the seasons asked for.

    A season that fails is reported and skipped rather than losing the rest;
    a season that returns nothing for a league it should have is the failure
    worth raising, and ``_check_leagues`` is what raises it.
    """
    frames, problems = [], []
    for season in seasons:
        for name, load in (("Big 5", load_big_five), ("MLS", load_mls)):
            try:
                frame = load(season)
            except Exception as exc:  # noqa: BLE001 -- one season must not lose the rest
                problems.append(f"{name} {season}: {type(exc).__name__}: {exc}")
                continue
            if verbose:
                print(f"  fbref: {name} {season}: {len(frame):,} players",
                      flush=True)
            frames.append(frame)

    if problems and verbose:
        for problem in problems:
            print(f"  ! {problem}", flush=True)
    if not frames:
        raise FeedUnavailable(
            "no season could be fetched: " + "; ".join(problems or ["no reason given"])
        )

    players = pd.concat(frames, ignore_index=True)
    _check_leagues(players, seasons)
    return players


def _check_leagues(players: pd.DataFrame, seasons: list[int]) -> None:
    """Every league must appear in every season that was asked for.

    A league missing from one season is not visible in a total: the pool simply
    has fewer players in it, and its benchmark is drawn from four seasons while
    claiming five.
    """
    holes = []
    for season in seasons:
        present = set(players.loc[players["season"] == season, "league"])
        for league in BIG_FIVE + ("MLS",):
            if league not in present:
                holes.append(f"{league} {season}")
    if holes:
        raise FeedUnavailable(
            f"no players at all for {', '.join(holes)}. A league missing from a "
            f"season does not show up in a total -- the pool is simply smaller "
            f"and its benchmark is drawn from fewer seasons than it claims."
        )


def probe(seasons: list[int] | None = None) -> int:
    """Fetch one season and report what came back, stage by stage."""
    seasons = seasons or [2025]
    print(f"fbref: probing seasons {seasons}")
    for season in seasons:
        for name, load in (("Big 5", load_big_five), ("MLS", load_mls)):
            try:
                frame = load(season)
            except Exception as exc:  # noqa: BLE001 -- a probe reports, never raises
                print(f"  FAIL {name} {season}: {type(exc).__name__}: {exc}")
                continue
            print(f"  ok   {name} {season}: {len(frame):,} rows, "
                  f"leagues {sorted(set(frame['league']))}")
            print(f"       columns {list(frame.columns)}")
            print(frame.head(3).to_string())
            totals = frame[["MP", "Gls", "Ast"]].max()
            print(f"       max MP {totals['MP']:.0f}, Gls {totals['Gls']:.0f}, "
                  f"Ast {totals['Ast']:.0f}")
            if totals["Gls"] < 5:
                print("       ^ suspiciously low -- these look like per-90 "
                      "rates rather than season totals; check _flatten")
    return 0
