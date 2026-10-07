"""Every result an asset has had this league year, for its profile's Results tab.

The Best performances tab lists the few games a slot counts; this lists all of
them, and the same for the clubs and the individual sports. Each asset's list
is a file of its own beside the pages (``results/<asset>.json``), fetched when
the tab is opened: a season of every game for three hundred assets is a few
megabytes, and written into every page as the profiles are it would be
downloaded with every page whether anyone looked or not.

**What a row is worth.** A player's game is priced as his game record is
(``whul.games``). A club's is the game-level terms of its own scorer at that
scorer's weights -- a win, a margin, a big win, a playoff game won -- through
each scorer's ``team_game_points``; the season's items, a title or a series or
a berth, are the panel's and are not shared out across the games. The games
plus those items are the season total, which ``tests/test_team_game_points``
pins for every sport.

**How a list is grouped**, newest first throughout:

    NFL, NCAAF, tennis, golf, motorsports   one list
    MLB, NBA, NHL                           by month, the playoffs apart
    club soccer                             by competition
    NCAA basketball, baseball, softball     season, conference tournament,
                                            NCAA tournament
    international teams                     by tournament

A month is open when it is the current one, and the month before stays open
through the seventh -- the progression table's rule, for the same reason.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from html import escape

import pandas as pd

from whul.config.league import SEASON, season_start

#: Where the files are written, beside the pages.
RESULTS_DIR = "results"

#: How long the month before stays open once a new one has begun.
PREVIOUS_MONTH_OPEN_DAYS = 7

#: The leagues whose lists are grouped by month.
BY_MONTH = {"MLB", "NBA", "NHL"}

#: The leagues whose lists are one list.
FLAT = {"NFL", "NCAAF"}

#: Each team league's game ledger, as its source keeps it.
TEAM_LEDGERS = {
    "NFL": "nfl-teams",
    "MLB": "mlb-teams",
    "NBA": "nba-teams",
    "NHL": "nhl-team-games",
    "NCAAF": "ncaaf",
    "NCAAM": "ncaam",
    "NCAAW": "ncaaw",
    "NCAA Baseball": "ncaabaseball",
    "NCAA Softball": "ncaasoftball",
    "Premier League": "epl",
    "La Liga": "laliga",
    "Serie A": "seriea",
    "Bundesliga": "bundesliga",
    "Ligue 1": "ligue1",
    "MLS": "mls",
    "NWSL": "nwsl",
}

SOCCER = {"Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1",
          "MLS", "NWSL"}
INDIVIDUAL = {"ATP", "WTA", "PGA", "F1", "NASCAR"}
INTL = {"Men's Intl Soccer", "Women's Intl Soccer"}


def file_name(asset_id: str) -> str:
    """An asset's file, in characters that read the same as a path and a URL.

    An id can carry an accent -- ``team-la-liga-atlético-madrid`` -- and a
    percent-encoded name is decoded by the server before the file is looked
    up, so it would never be found. Each character outside the safe set is
    written as its code point instead, which keeps two ids from colliding.
    """
    return re.sub(r"[^A-Za-z0-9_.-]", lambda m: f"~{ord(m.group()):x}~",
                  str(asset_id)) + ".json"


# --- grouping ------------------------------------------------------------------

def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _short(day: date | None) -> str:
    return f"{day:%b} {day.day}" if day else ""


def month_open(month: tuple[int, int], latest: date) -> bool:
    """Whether a month's group starts open on a list drawn on ``latest``."""
    if month == (latest.year, latest.month):
        return True
    before = (latest.year - 1, 12) if latest.month == 1 else (latest.year, latest.month - 1)
    return month == before and latest.day <= PREVIOUS_MONTH_OPEN_DAYS


def group_rows(rows: list[dict], how: str, latest: date) -> list[dict]:
    """``rows`` (each with ``_day`` and ``_group``) as groups, newest first.

    ``how`` is ``flat``, ``month`` or ``named``. A named group is labelled by
    the row's ``_group`` and opens where the newest result in the list is.
    """
    rows = sorted(rows, key=lambda r: (r.get("_day") or date.min), reverse=True)
    if not rows:
        return []
    if how == "flat":
        return [{"label": "", "open": True, "rows": [_clean(r) for r in rows]}]
    groups: list[dict] = []
    index: dict[str, dict] = {}
    for row in rows:
        if how == "month" and not row.get("_group"):
            day = row.get("_day")
            label = f"{day:%B %Y}" if day else "Undated"
            opened = bool(day) and month_open((day.year, day.month), latest)
        else:
            label = str(row.get("_group") or "")
            opened = False
        if label not in index:
            index[label] = {"label": label, "open": opened, "rows": []}
            groups.append(index[label])
        index[label]["rows"].append(_clean(row))
    if how == "named" or not any(g["open"] for g in groups):
        groups[0]["open"] = True
    # A playoff group heads the list in October and is open while it is
    # current, the way a month is.
    for group in groups:
        if group["label"] == "Playoffs" and group is groups[0]:
            group["open"] = True
    return groups


#: Kept on every row; anything else is left off where it is empty, since a
#: season of games repeats each key a few hundred times.
ALWAYS = {"date", "points"}


def _clean(row: dict) -> dict:
    return {k: v for k, v in row.items()
            if not k.startswith("_") and (v or k in ALWAYS)}


def _points(value: float) -> str:
    """A figure as a row shows it; never "-0.0"."""
    text = f"{float(value):,.1f}"
    return "0.0" if text == "-0.0" else text


def escaped(value):
    """Every string in a payload, HTML-escaped, as the window inserts them."""
    if isinstance(value, str):
        return escape(value)
    if isinstance(value, list):
        return [escaped(v) for v in value]
    if isinstance(value, dict):
        return {k: escaped(v) for k, v in value.items()}
    return value


def _payload(kind: str, groups: list[dict], note: str = "") -> dict:
    return {"kind": kind, "note": note, "groups": groups,
            "count": sum(len(g["rows"]) for g in groups)}


# --- players: every game on record ---------------------------------------------

def player_results(store, season: str, latest: date, assets: dict[str, dict],
                   best_keys: dict[str, set]) -> dict[str, dict]:
    """Every recorded game of every rostered team-sport player."""
    from whul.games import PHASE_LABELS, sport_of
    from whul.site.build import _game_figures

    records = store.query(
        "SELECT asset_id, game_key, date, role, phase, points, score, opponent, "
        "detail FROM game_scores WHERE season = ? AND date <= ?",
        (season, str(latest)))
    if records.empty:
        return {}
    out: dict[str, dict] = {}
    for asset_id, block in records.groupby("asset_id"):
        info = assets.get(str(asset_id))
        if not info:
            continue
        league = str(info.get("league") or "")
        sport = sport_of(info.get("category") or league)
        if league in SOCCER or sport == "Club Soccer":
            sport, how = "Club Soccer", "named"
        elif league in BY_MONTH:
            how = "month"
        else:
            how = "flat"
        chosen = best_keys.get(str(asset_id), set())
        rows = []
        for r in block.itertuples():
            try:
                detail = json.loads(r.detail or "{}")
            except (TypeError, ValueError):
                detail = {}
            day = _day(r.date)
            phase = str(r.phase or "regular")
            where = (str(detail.get("competition") or "")
                     or PHASE_LABELS.get(phase, "") or "")
            role = {"start": "Start", "relief": "Relief", "bat": "At the plate"}.get(
                str(r.role or ""), "") if sport == "MLB" else ""
            if how == "named":
                group = where or league
            elif how == "month" and phase in ("playoffs", "play-in"):
                group = "Playoffs"
                # The group says so; a Play-In game still says which it was.
                where = "" if where == "Playoffs" else where
            else:
                group = ""
            rows.append({
                "_day": day, "_group": group,
                "date": _short(day),
                "vs": f"v {r.opponent}" if r.opponent else "",
                "phase": where if how != "named" else
                         (PHASE_LABELS.get(phase, "") if phase != "regular" else ""),
                "role": role,
                "figs": _game_figures(sport, detail),
                "score": _points(r.score),
                "points": _points(r.points),
                "best": str(r.game_key) in chosen,
            })
        out[str(asset_id)] = _payload("games", group_rows(rows, how, latest))
    return out


# --- clubs: every game, priced on its own --------------------------------------

def _ledger(store, source: str) -> pd.DataFrame:
    frame = store.query("SELECT payload FROM feed_rows WHERE source = ?", (source,))
    if frame.empty:
        return pd.DataFrame()
    return pd.DataFrame([json.loads(p) for p in frame["payload"]])


def _priced_games(league: str, raw: pd.DataFrame, line: dict) -> pd.DataFrame:
    """The league's games as one row per club per game, each priced.

    Columns: ``team``, ``date``, ``opponent``, ``home``, ``for``, ``against``,
    ``phase`` (a group label or ""), ``points``.
    """
    from whul.scoring import mlb, nba, ncaa, nfl, nhl, soccer

    if raw is None or raw.empty:
        return pd.DataFrame()
    if league == "NFL":
        games = nfl._team_games(raw)
        if games.empty:
            return games
        return pd.DataFrame({
            "team": games["team"], "date": games["date"], "opponent": games["opponent"],
            "home": games["home"], "for": games["points_for"],
            "against": games["points_against"],
            "phase": games["is_playoff"].map({True: "Playoffs", False: ""}),
            "points": nfl.team_game_points(games),
        })
    if league == "NBA":
        games = nba._team_games(raw)
        if games.empty:
            return games
        phase = pd.Series("", index=games.index)
        phase = phase.mask(games["is_playin"], "Play-In").mask(games["is_playoff"], "Playoffs")
        return pd.DataFrame({
            "team": games["team"], "date": games["date"], "opponent": games["opponent"],
            "home": games["home"], "for": games["points_for"],
            "against": games["points_against"], "phase": phase,
            "points": nba.team_game_points(games),
        })
    if league == "MLB":
        games = mlb._team_games(raw)
        if games.empty:
            return games
        lift = float(line.get("proration_factor") or 1.0) or 1.0
        weights = mlb.contract_weight(games["season"], opened=SEASON.start.year)
        points = pd.Series(0.0, index=games.index)
        for season in games["season"].unique():
            here = games["season"] == season
            weight = float(weights[here].iloc[0])
            points[here] = mlb.team_game_points(games[here], weight=weight, lift=lift)
        october = games["game_type"].isin(
            (mlb.GAME_TYPE_WC, mlb.GAME_TYPE_LDS, mlb.GAME_TYPE_LCS, mlb.GAME_TYPE_WS))
        return pd.DataFrame({
            "team": games["team"], "date": games["date"], "opponent": games["opponent"],
            "home": games["home"], "for": games["runs_for"],
            "against": games["runs_against"],
            "phase": october.map({True: "Playoffs", False: ""}),
            "points": points,
        })
    if league == "NHL":
        team = raw.get("teamFullName", pd.Series("", index=raw.index)).astype(str)
        kind = pd.to_numeric(raw.get("game_type", 2), errors="coerce").fillna(2)
        return pd.DataFrame({
            "team": team,
            "date": raw.get("gameDate", pd.Series("", index=raw.index)).astype(str).str[:10],
            "opponent": raw.get("opponentTeamAbbrev", pd.Series("", index=raw.index)),
            "home": raw.get("homeRoad", pd.Series("", index=raw.index)).astype(str) == "H",
            "for": pd.to_numeric(raw.get("goalsFor"), errors="coerce"),
            "against": pd.to_numeric(raw.get("goalsAgainst"), errors="coerce"),
            "phase": (kind == 3).map({True: "Playoffs", False: ""}),
            "points": nhl.team_game_points(raw),
            "_otl": pd.to_numeric(raw.get("otLosses", 0), errors="coerce").fillna(0) > 0,
            "_team_id": pd.to_numeric(raw.get("teamId"), errors="coerce")
                          .fillna(-1).astype(int).astype(str),
        })
    if league in ("NCAAF", "NCAAM", "NCAAW", "NCAA Baseball", "NCAA Softball"):
        if league == "NCAAF":
            games = ncaa.football_games(raw)
            points = ncaa.football_game_points(games)
            phase = games["is_playoff"].map({True: "Playoffs", False: ""}) \
                if not games.empty else None
        elif league in ("NCAAM", "NCAAW"):
            games = ncaa.basketball_games(raw)
            points = ncaa.basketball_game_points(games)
            if not games.empty:
                # A game neither of those nor the regular season is the NIT or
                # another invitational, which scores nothing.
                phase = pd.Series("Other postseason", index=games.index)
                phase = phase.mask(games["is_reg"], "Regular season")
                phase = phase.mask(games["is_conf_tourney"], "Conference tournament")
                phase = phase.mask(games["is_mm"], "NCAA tournament")
        else:
            games = ncaa.diamond_games(raw)
            points = ncaa.diamond_game_points(games)
            if not games.empty:
                phase = pd.Series("Regular season", index=games.index)
                phase = phase.mask(games["is_conf_tourney"], "Conference tournament")
                phase = phase.mask(games["is_postseason"], "NCAA tournament")
        if games.empty:
            return games
        return pd.DataFrame({
            "team": games["team"], "date": games["game_date"].astype(str).str[:10],
            "opponent": games["opp_team"], "home": pd.Series(None, index=games.index),
            "for": games["points_for"], "against": games["points_against"],
            "phase": phase, "points": points,
        })
    if league in SOCCER:
        scored = soccer.score_team_matches(raw)
        if scored.empty:
            return scored
        return pd.DataFrame({
            "team": scored["team"], "date": scored["date"].astype(str).str[:10],
            "opponent": scored["opponent"], "home": pd.Series(None, index=scored.index),
            "for": scored["goals_for"], "against": scored["goals_against"],
            "phase": [_competition(k, label, league)
                      for k, label in zip(scored["competition_key"], scored["competition"])],
            "points": scored["match_points"].astype(float),
            "_so": scored["outcome"].astype(str),
        })
    return pd.DataFrame()


def _competition(key, label, league: str) -> str:
    """A club match's competition as its group is headed: the feed's key read
    through the names the game rows use, since its own label is the round as
    well -- "English Carabao Cup third round" -- and splits one cup into four."""
    from whul.games import COMPETITION_LABELS
    from whul.scoring.competition import POSTSEASON_PATTERN

    key = str(key or "").strip().lower()
    if key in COMPETITION_LABELS:
        return COMPETITION_LABELS[key]
    if key in TEAM_LEDGERS.values() or not key:
        return "Playoffs" if POSTSEASON_PATTERN.search(str(label or "")) else league
    return str(label or key)


def _where(home) -> str:
    """"v " at home, "@ " away; "v " where the feed does not say."""
    if home is False or (hasattr(home, "dtype") and not bool(home)):
        return "@ "
    return "v "


def _result_text(row) -> str:
    """"W 5–3", "L 2–3 OT", "D 1–1", or the score alone where it is unknown."""
    try:
        here, there = float(row["for"]), float(row["against"])
    except (TypeError, ValueError):
        return ""
    if math.isnan(here) or math.isnan(there):
        return ""
    outcome = str(row.get("_so", "") or "")
    if outcome == "shootout_win":
        mark, tail = "W", " (pens)"
    elif outcome == "shootout_loss":
        mark, tail = "L", " (pens)"
    else:
        mark = "W" if here > there else "L" if here < there else "D"
        tail = " OT" if (mark == "L" and bool(row.get("_otl"))) else ""
    return f"{mark} {here:g}–{there:g}{tail}"


def team_results(store, season: str, latest: date, assets: dict[str, dict],
                 lines: dict[str, dict], benchmarks: dict[str, float]) -> dict[str, dict]:
    """Every rostered club's games this league year, each priced on its own.

    A game's score is its points on the scale the club's total is shown on --
    the day's own ratio of the two, so a list and the total beside it can never
    be on different benchmarks; the benchmark where the club has no score yet.
    """
    scores = store.latest_scores(season, latest)
    scales = {str(r.asset_id): float(r.scaled_score) / float(r.league_points)
              for r in scores.itertuples()
              if r.league_points and abs(float(r.league_points)) > 1e-9}
    by_league: dict[str, list[str]] = {}
    for asset_id, info in assets.items():
        if info.get("kind") == "Team" and info.get("league") in TEAM_LEDGERS:
            by_league.setdefault(str(info["league"]), []).append(asset_id)
    out: dict[str, dict] = {}
    for league, held in by_league.items():
        raw = _ledger(store, TEAM_LEDGERS[league])
        if raw.empty:
            continue
        start = str(season_start(league))
        divisor = benchmarks.get(league)
        how = ("month" if league in BY_MONTH else "flat" if league in FLAT
               else "named")
        cache: dict[str, pd.DataFrame] = {}
        for asset_id in held:
            line = lines.get(asset_id) or {}
            per_point = scales.get(asset_id) or (100 / divisor if divisor else None)
            team = str(line.get("team") or "").strip()
            if not team:
                continue
            # Once a league, except baseball, whose window lift is the club's.
            key = asset_id if league == "MLB" else league
            if key not in cache:
                try:
                    cache[key] = _priced_games(league, raw, line)
                except Exception:  # noqa: BLE001 -- one club's list, not the build
                    cache[key] = pd.DataFrame()
            priced = cache[key]
            if priced.empty:
                continue
            # Hockey's ledger names a club as the league's stats do, which is
            # also the id the line keeps; a name is only the fallback.
            same = priced["team"].astype(str) == team
            if "_team_id" in priced.columns and line.get("team_id") not in (None, ""):
                same = priced["_team_id"].astype(str) == str(int(float(line["team_id"])))
            mine = priced[same
                          & (priced["date"].astype(str) >= start)
                          & (priced["date"].astype(str) <= str(latest))]
            rows = []
            for r in mine.to_dict("records"):
                day = _day(r["date"])
                points = float(r["points"])
                where = _where(r.get("home"))
                phase = str(r.get("phase") or "")
                rows.append({
                    "_day": day,
                    "_group": phase if how == "named" else
                              ("Playoffs" if phase in ("Playoffs", "Play-In") and how == "month"
                               else ""),
                    "date": _short(day),
                    "vs": f"{where}{r['opponent']}" if r.get("opponent") else "",
                    "res": _result_text(r),
                    "phase": phase if how == "flat" and phase else "",
                    "role": "",
                    "figs": [],
                    "score": _points(points * per_point) if per_point else "",
                    "points": _points(points),
                    "best": False,
                })
            if rows:
                out[asset_id] = _payload("games", group_rows(rows, how, latest))
    return out


# --- the individual sports and the national teams -------------------------------

def finish_results(assets: dict[str, dict], lines: dict[str, dict],
                   latest: date) -> dict[str, dict]:
    """Tennis tournaments with their rounds as badges; golf and motorsport
    finishes; a national team's tournaments."""
    out: dict[str, dict] = {}
    for asset_id, info in assets.items():
        league = str(info.get("league") or "")
        line = lines.get(asset_id) or {}
        if league in INTL:
            sections = line.get("sections") or []
            rows = []
            for s in sections if isinstance(sections, list) else []:
                wins, draws, losses = (int(s.get(k) or 0) + (
                    int(s.get(f"shootout_{k}") or 0) if k != "draws" else 0)
                    for k in ("wins", "draws", "losses"))
                rows.append({
                    "_day": None, "_group": "", "name": str(s.get("name") or ""),
                    "res": f"{wins}-{draws}-{losses}",
                    "points": _points(s.get('counted', s.get('points')) or 0),
                })
            if rows:
                out[asset_id] = _payload("sections", group_rows(rows, "flat", latest))
            continue
        if league not in INDIVIDUAL and info.get("kind") != "Player":
            continue
        finishes = line.get("finishes") or []
        if not isinstance(finishes, list) or not finishes:
            continue
        tennis = league in ("ATP", "WTA") or any(f.get("rounds") is not None
                                                 for f in finishes)
        rows = []
        for f in finishes:
            day = _day(f.get("date"))
            row = {"_day": day, "_group": "", "date": _short(day),
                   "points": _points(f.get('points') or 0)}
            if tennis:
                row["name"] = str(f.get("name") or f.get("label") or "")
                row["rounds"] = f.get("rounds") or []
                if not row["rounds"]:
                    # A list written before the rounds were kept, or a team
                    # event with no draw: the line it always had.
                    row["name"] = str(f.get("label") or row["name"])
            else:
                row["name"], row["finish"] = _name_and_finish(f)
            rows.append(row)
        out[asset_id] = _payload("tennis" if tennis else "events",
                                 group_rows(rows, "flat", latest))
    return out


def _assets(store, season: str) -> dict[str, dict]:
    """Everyone rostered this league year, with the slot category they sit in."""
    frame = store.query(
        "SELECT DISTINCT o.asset_id, a.league, a.asset_type, r.category "
        "FROM slot_occupancy o JOIN roster_slots r ON r.slot_id = o.slot_id "
        "JOIN assets a ON a.asset_id = o.asset_id WHERE r.season = ?", (season,))
    return {str(r.asset_id): {"league": str(r.league or ""), "kind": str(r.asset_type),
                              "category": str(r.category or "")}
            for r in frame.itertuples()}


def _lines(store, season: str, latest: date) -> dict[str, dict]:
    """Each asset's latest stored row, as the feed wrote it.

    Each asset's own latest, not everybody's at the newest day: a run that
    pulled one league wrote that day for it alone, and every club, golfer and
    driver lost its results on the site.
    """
    frame = store.query(
        "SELECT r.asset_id, r.phase, r.stats FROM raw_stats r JOIN ("
        "  SELECT asset_id, MAX(as_of) AS last FROM raw_stats "
        "  WHERE season = ? AND as_of <= ? GROUP BY asset_id"
        ") l ON l.asset_id = r.asset_id AND l.last = r.as_of WHERE r.season = ?",
        (season, str(latest), season))
    out: dict[str, dict] = {}
    # The regular-season row where there are two: it is the one naming the
    # club and carrying the finishes.
    for r in sorted(frame.itertuples(), key=lambda r: r.phase != "regular"):
        if str(r.asset_id) in out:
            continue
        try:
            out[str(r.asset_id)] = json.loads(r.stats)
        except (TypeError, ValueError):
            continue
    return out


#: The end of a line written before the finish was kept on its own:
#: "TOUR Championship 1st", "Azerbaijan Grand Prix 19th", "Open T12", "MC".
FINISH_TAIL = re.compile(
    r"\s+(T?\d+(?:st|nd|rd|th)?|MC|CUT|DNF|DNS|DSQ|DQ|WD|MDF)$", re.IGNORECASE)


def _name_and_finish(entry: dict) -> tuple[str, str]:
    name, finish = str(entry.get("name") or ""), str(entry.get("finish") or "")
    if name:
        return name, finish
    label = str(entry.get("label") or "")
    found = FINISH_TAIL.search(label)
    if found:
        return label[:found.start()], found.group(1)
    return label, finish


def build(store, season: str, latest, best_keys: dict[str, set] | None = None
          ) -> dict[str, dict]:
    """Every rostered asset's results, keyed by asset id.

    ``best_keys`` is the game keys each player's best-performances slot counts,
    which the list marks.
    """
    from whul.store import benchmarks as bm

    day = latest if isinstance(latest, date) else date.fromisoformat(str(latest))
    assets = _assets(store, season)
    lines = _lines(store, season, day)
    version = bm.active_version(store, season)
    divisors: dict[str, float] = {}
    if version is not None:
        frame = bm.load(store, version.version)
        frame = frame[frame["asset_type"] == "Team"]
        divisors = {str(k): float(v) for k, v in zip(frame["norm_key"], frame["benchmark"])}
    out: dict[str, dict] = {}
    out.update(finish_results(assets, lines, day))
    out.update(team_results(store, season, day, assets, lines, divisors))
    out.update(player_results(store, season, day, assets, best_keys or {}))
    return out
