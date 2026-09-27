"""Every rostered player's games, one row each, dated and scored.

The best-performances slot counts a player's k best games, so it needs what a
season-to-date feed cannot give: which night was which. This writes that record
into ``game_scores`` -- one row per game a rostered player played in a team
sport, with the day it was played, its counting-stat points by the league's own
weights, and those points on the 0-100 scale against the same frozen divisor
his season is measured by.

Where each sport's games come from:

    NFL          the player weeks the nightly pull already keeps (nflverse),
                 dated by the schedule it also keeps. No requests.
    NBA          the box scores the nightly pull already keeps (ESPN). No
                 requests.
    MLB          the Stats API's game logs, one or two requests a player. The
                 log names each game, so a doubleheader is two games and a day
                 the pull ran late is still the day the game was played.
    NHL          the NHL's own game logs, one request a skater.
    Club soccer  each club's domestic matches from the ledger the team pull
                 keeps, and each match's own summary for who played and what
                 they did in it -- fetched once, after full time, and kept.

Only counting stats are in a game. MLB's Offense, Defense and WAR are run
values for a season with no share in any one game, and no year multiplier
applies -- see ``whul.scoring.best_game``. European matches are a season bonus
in club soccer, not domestic matches, so they are not games here either.

A game is written whether or not it will ever count: the slot's best k are
chosen at rollup, from whatever the slot's occupants played while they held it.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from whul.config.league import ALL_SLOTS, season_start
from whul.store.db import Store, _as_text, _now

#: The roster categories whose players have a best-performances slot, and the
#: league each game record is read for.
SPORTS = ("NFL", "NBA", "MLB", "NHL", "Club Soccer")

#: Where a soccer league's clubs' matches are kept by the team pull, and which
#: of its competitions are domestic. European ties are a bonus, not a match
#: that counts toward the season, so they are not read.
SOCCER_LEDGERS = {
    "Premier League": "epl",
    "La Liga": "laliga",
    "Serie A": "seriea",
    "Bundesliga": "bundesliga",
    "Ligue 1": "ligue1",
    "MLS": "mls",
}

#: What a match summary calls each figure the soccer scorer reads. More than
#: one name where ESPN has been seen to use more than one.
SOCCER_STATS = {
    "goals": ("totalGoals", "goals"),
    "assists": ("goalAssists", "assists"),
    "yellow": ("yellowCards",),
    "red": ("redCards",),
}



@dataclass
class Report:
    """What one run recorded, per sport, and what did not add up."""

    recorded: dict[str, int] = field(default_factory=dict)
    players: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = ["Game records:"]
        for sport in SPORTS:
            if sport in self.players:
                lines.append(f"  {sport}: {self.recorded.get(sport, 0)} game(s) "
                             f"for {self.players[sport]} player(s)")
        lines += [f"  ! {p}" for p in self.problems]
        return "\n".join(lines)


@dataclass
class Rostered:
    """A player who has held a best-performances category's slot."""

    asset_id: str
    name: str
    league: str
    role: str
    category: str
    line: dict


def _plain(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


def _number(value) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(out) else out


def sport_of(category: str) -> str:
    return "Club Soccer" if category.startswith("Club Soccer") else category


def rostered(store: Store, season: str, as_of: date | str) -> list[Rostered]:
    """Everyone who has sat in a player slot that has a best-performances
    slot beside it, this season, with the line their season was scored from.

    Past occupants too: a player traded away in October keeps the games he
    played for the slot that held him then.
    """
    wanted = [g.category for g in ALL_SLOTS if g.asset_type == "Player" and g.best]
    frame = store.query(
        f"""SELECT DISTINCT o.asset_id, a.display_name, a.league, a.role, r.category
            FROM slot_occupancy o
            JOIN roster_slots r ON r.slot_id = o.slot_id
            JOIN assets a ON a.asset_id = o.asset_id
            WHERE r.season = ? AND r.asset_type = 'Player'
              AND r.category IN ({','.join('?' * len(wanted))})""",
        (season, *wanted))
    out = []
    for row in frame.itertuples():
        text = store.scalar(
            "SELECT stats FROM raw_stats WHERE asset_id = ? AND as_of <= ? "
            "AND phase = 'regular' ORDER BY as_of DESC LIMIT 1",
            (row.asset_id, _as_text(as_of)))
        line = json.loads(text) if text else {}
        league = str(line.get("league") or row.league or "")
        out.append(Rostered(str(row.asset_id), str(row.display_name), league,
                            str(row.role or line.get("role") or ""),
                            str(row.category), line))
    return out


def divisors(store: Store, season: str) -> dict[str, float]:
    """The frozen season divisor for every player benchmark group."""
    from whul.store import benchmarks as bm

    version = bm.active_version(store, season)
    if version is None:
        raise RuntimeError(f"no frozen benchmark for {season}")
    frame = bm.load(store, version.version)
    frame = frame[frame["asset_type"] == "Player"]
    return {str(k): float(v) for k, v in zip(frame["norm_key"], frame["benchmark"])}


def _ledger(store: Store, source: str) -> list[dict]:
    frame = store.query("SELECT payload FROM feed_rows WHERE source = ?", (source,))
    return [json.loads(p) for p in frame["payload"]] if not frame.empty else []


def _window(league: str, as_of: date | str) -> tuple[str, str]:
    return season_start(league).isoformat(), _as_text(as_of)


# --- NFL ----------------------------------------------------------------------

NFL_DETAIL = ("passing_yards", "passing_tds", "interceptions", "rushing_yards",
              "rushing_tds", "receptions", "receiving_yards", "receiving_tds",
              "fumbles_lost")


def nfl_games(store: Store, players: list[Rostered], as_of) -> tuple[list[dict], list[str]]:
    from whul.best_game_calibration import nfl_game_points

    ids = {str(p.line.get("player_id") or ""): p for p in players}
    ids.pop("", None)
    raw = pd.DataFrame(_ledger(store, "nfl"))
    if raw.empty or not ids:
        return [], ([] if ids else [f"NFL: {len(players)} player(s) with no gsis id"])
    raw = raw[raw["player_id"].astype(str).isin(ids)].reset_index(drop=True)
    if raw.empty:
        return [], []
    dates = {str(g.get("game_id")): str(g.get("gameday") or "")[:10]
             for g in _ledger(store, "nfl-teams")}
    # Scored rows keep the ledger rows' index, which is how each finds its game.
    scored = nfl_game_points(raw)
    start, end = _window("NFL", as_of)
    rows, problems = [], []
    for i, row in scored.iterrows():
        game = str(raw.at[i, "game_id"])
        day = dates.get(game, "")
        if not day:
            problems.append(f"NFL: no date for game {game}")
            continue
        if not (start <= day <= end):
            continue
        rows.append({
            "asset_id": ids[str(row["player"])].asset_id,
            "game_key": game, "date": day, "role": "",
            "points": float(row["points"]), "norm_key": str(row["key"]),
            "opponent": str(raw.at[i, "opponent_team"]) if "opponent_team" in raw else "",
            "detail": {c: _number(row[c]) for c in NFL_DETAIL if _number(row[c])},
        })
    return rows, problems


# --- NBA ----------------------------------------------------------------------

NBA_DETAIL = ("points", "rebounds", "assists", "steals", "blocks", "turnovers",
              "three_pt_made", "plus_minus", "double_doubles", "triple_doubles")


def nba_games(store: Store, players: list[Rostered], as_of) -> tuple[list[dict], list[str]]:
    from whul.normalize import assign_norm_key
    from whul.scoring import nba

    ids = {str(p.line.get("athlete_id") or ""): p for p in players}
    ids.pop("", None)
    raw = pd.DataFrame(_ledger(store, "nba"))
    if raw.empty or not ids:
        return [], []
    raw = raw[raw["athlete_id"].astype(str).isin(ids)].reset_index(drop=True)
    if raw.empty:
        return [], []
    games = nba.game_points(raw)
    games["league"] = "NBA"
    games["role"] = games["position"]
    games["norm_key"] = assign_norm_key(games, "Player")
    start, end = _window("NBA", as_of)
    rows = []
    for row in games.itertuples():
        day = str(row.game_date)[:10]
        if not (start <= day <= end):
            continue
        rows.append({
            "asset_id": ids[str(row.athlete_id)].asset_id,
            "game_key": str(row.game_id), "date": day, "role": "",
            "points": float(row.game_points), "norm_key": str(row.norm_key),
            "opponent": "",
            "detail": {c: _number(getattr(row, c)) for c in NBA_DETAIL
                       if _number(getattr(row, c))},
        })
    return rows, []


# --- NHL ----------------------------------------------------------------------

NHL_FIELDS = {"goals": "goals", "assists": "assists", "shots": "shots",
              "plus_minus": "plusMinus"}


def nhl_games(store: Store, players: list[Rostered], as_of,
              log_loader=None) -> tuple[list[dict], list[str]]:
    from whul.scoring import nhl as scoring
    from whul.sources import nhl as source

    log_loader = log_loader or (lambda pid, season_id: source._web(
        f"/player/{pid}/game-log/{season_id}/{source.GAME_TYPE_REGULAR}"))
    start, end = _window("NHL", as_of)
    # The season the league year's hockey is, by the API's own numbering.
    season_id = source.season_id(date.fromisoformat(start).year + 1)
    rows, problems = [], []
    for p in players:
        if str(p.line.get("role") or p.role) == scoring.GOALIE_ROLE:
            continue
        pid = str(p.line.get("player_id") or "")
        if not pid:
            if p.line:
                problems.append(f"NHL: {p.name} has no player id to ask for")
            continue
        try:
            payload = log_loader(pid, season_id)
        except Exception as exc:  # noqa: BLE001 -- one player, not the run
            problems.append(f"NHL: {p.name}: {type(exc).__name__}: {exc}")
            continue
        for game in (payload or {}).get("gameLog") or []:
            day = str(game.get("gameDate") or "")[:10]
            if not (start <= day <= end):
                continue
            stat = {ours: _number(game.get(theirs)) for ours, theirs in NHL_FIELDS.items()}
            points = (stat["goals"] * scoring.PTS_GOAL
                      + stat["assists"] * scoring.PTS_ASSIST
                      + stat["shots"] * scoring.PTS_SHOT
                      + stat["plus_minus"] * scoring.PTS_PLUS_MINUS)
            rows.append({
                "asset_id": p.asset_id, "game_key": str(game.get("gameId")),
                "date": day, "role": "", "points": points, "norm_key": "NHL",
                "opponent": str(game.get("opponentAbbrev") or ""),
                "detail": {k: v for k, v in stat.items() if v},
            })
    return rows, problems


# --- MLB ----------------------------------------------------------------------

BAT_DETAIL = ("atBats", "hits", "doubles", "triples", "homeRuns", "baseOnBalls",
              "hitByPitch", "stolenBases", "caughtStealing")
PITCH_DETAIL = ("inningsPitched", "strikeOuts", "hits", "baseOnBalls",
                "hitByPitch", "homeRuns", "saves", "holds")


def mlb_games(store: Store, players: list[Rostered], as_of, divisor: dict,
              line_loader=None, log_loader=None) -> tuple[list[dict], list[str]]:
    """Each game from the Stats API's log, split 1x / 0.5x for a two-way
    player by whichever role led that game, and counted as that role."""
    from whul.config.league import SEASON
    from whul.scoring import best_game as rules
    from whul.sources import mlb

    line_loader = line_loader or mlb.load_stats_api_players
    log_loader = log_loader or mlb.load_game_log
    start, end = _window("MLB", as_of)
    years = sorted({SEASON.start.year, date.fromisoformat(end).year})
    ids: dict[str, str] = {}
    problems: list[str] = []
    for year in years:
        for group in ("hitting", "pitching"):
            try:
                found = line_loader(year, group)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"MLB: {year} {group} lines: {type(exc).__name__}: {exc}")
                continue
            for name, pid in zip(found.get("player", []), found.get("player_id", [])):
                ids.setdefault(_plain(name), str(pid))

    rows: list[dict] = []
    for p in players:
        pid = ids.get(_plain(p.name))
        if pid is None:
            problems.append(f"MLB: {p.name} is not in the Stats API's lines")
            continue
        spans = p.line.get("season_lines") or [p.line]
        batted = p.role == "Batter" or any(_number(s.get("ab")) for s in spans)
        pitched = p.role == "Pitcher" or any(_number(s.get("ip")) for s in spans)
        for year in years:
            logs = {}
            for group, wanted in (("hitting", batted), ("pitching", pitched)):
                if not wanted:
                    continue
                try:
                    logs[group] = log_loader(pid, year, group)
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"MLB: {p.name} {year} {group}: "
                                    f"{type(exc).__name__}: {exc}")
            rows += _mlb_rows(p, logs, divisor, start, end, rules, mlb)
    return rows, problems


def _mlb_side(log, group, mlb) -> dict:
    """``{game_pk: (the log's row, its points)}`` for one group's log."""
    if log is None or not len(log):
        return {}
    points = mlb.game_points(log, group).set_index("game_pk")["points"]
    return {row["game_pk"]: (row, float(points.get(row["game_pk"], 0.0)))
            for row in log.to_dict("records")}


def _mlb_rows(p, logs, divisor, start, end, rules, mlb) -> list[dict]:
    bat = _mlb_side(logs.get("hitting"), "hitting", mlb)
    arm = _mlb_side(logs.get("pitching"), "pitching", mlb)
    out = []
    for pk in dict.fromkeys([*bat, *arm]):
        b, a = bat.get(pk), arm.get(pk)
        record = (a or b)[0]
        day = str(record.get("date") or "")[:10]
        # Up to the day before, as the season line is: last night's games are
        # over and today's may not be.
        if not (start <= day < end):
            continue
        bat_score = 100 * b[1] / divisor["MLB_Batter"] if b else None
        arm_score = 100 * a[1] / divisor["MLB_Pitcher"] if a else None
        started = bool(a and _number(a[0].get("gamesStarted")) >= 1)
        role = rules.primary_role(bat_score, arm_score,
                                  rules.START if started else rules.RELIEF)
        detail = {}
        if b:
            detail["batting"] = {c: _number(b[0].get(c)) for c in BAT_DETAIL
                                 if _number(b[0].get(c))}
        if a:
            pitching = {c: _number(a[0].get(c)) for c in PITCH_DETAIL[1:]
                        if _number(a[0].get(c))}
            detail["pitching"] = {"inningsPitched": str(a[0].get("inningsPitched") or "0.0"),
                                  "started": started, **pitching}
        out.append({
            "asset_id": p.asset_id,
            "game_key": str(pk),
            "date": day,
            "role": role,
            "points": (b[1] if b else 0.0) + (a[1] if a else 0.0),
            "score": rules.two_way_game(batting=bat_score, pitching=arm_score),
            "opponent": str(record.get("opponent") or ""),
            "detail": detail,
        })
    return out


# --- club soccer --------------------------------------------------------------

def soccer_lineup(payload: dict) -> list[dict]:
    """Who played in a match and what each did, from its ESPN summary.

    A matchday squad is twenty names a side, and ``active`` is true for all of
    them; ``starter`` or ``subbedIn`` is what separates a player from the
    bench (see ``espn_soccer.lineup_of``). Each figure is read by its stat's
    own name, never by position in the list.
    """
    out = []
    for block in payload.get("rosters") or []:
        if not isinstance(block, dict):
            continue
        club = str((block.get("team") or {}).get("displayName") or "")
        for entry in block.get("roster") or []:
            if not isinstance(entry, dict):
                continue
            started = bool(entry.get("starter"))
            came_on = entry.get("subbedIn")
            came_on = bool(came_on.get("didSub")) if isinstance(came_on, dict) else bool(came_on)
            if not (started or came_on):
                continue
            athlete = entry.get("athlete") or {}
            stats = {str(s.get("name")): s.get("value")
                     for s in entry.get("stats") or [] if isinstance(s, dict)}
            figures = {}
            for ours, names in SOCCER_STATS.items():
                found = next((stats[n] for n in names if n in stats), None)
                figures[ours] = None if found is None else _number(found)
            out.append({
                "athlete_id": str(athlete.get("id") or ""),
                "name": str(athlete.get("displayName") or ""),
                "club": club, "started": started, **figures,
            })
    return out


def _finished(payload: dict) -> bool:
    """Whether a summary says its match is over. A payload that does not say
    either way is taken as over: the match list only names played matches."""
    try:
        status = payload["header"]["competitions"][0]["status"]["type"]
    except (KeyError, IndexError, TypeError):
        return True
    return bool(status.get("completed", True))


def _lineup(store: Store, competition: str, event_id: str, loader) -> list[dict] | None:
    """A match's lineup, from the store if it has been asked before."""
    key = str(event_id)
    # Its own table, not the fixture ledger: every reader of `feed_rows` takes
    # a row to be one fixture, and a lineup is not one.
    text = store.scalar("SELECT payload FROM match_lineups WHERE event_id = ?", (key,))
    if text:
        return json.loads(text)
    try:
        payload = loader(competition, event_id)
    except Exception:  # noqa: BLE001 -- one match; asked again next run
        return None
    if not _finished(payload or {}):
        # Kept only once it is over: a lineup read at half time would be
        # stored as the match and never asked about again.
        return None
    lineup = soccer_lineup(payload or {})
    if not lineup:
        return None
    store.upsert("match_lineups", [{
        "event_id": key, "competition": str(competition),
        "payload": json.dumps(lineup), "fetched_at": _now(),
    }], keys=("event_id",))
    return lineup


def soccer_games(store: Store, players: list[Rostered], as_of,
                 loader=None) -> tuple[list[dict], list[str]]:
    from whul.scoring import soccer as scoring
    from whul.sources import espn

    loader = loader or espn.summary
    rows, problems = [], []
    missing_stats = 0
    by_league: dict[str, list[Rostered]] = {}
    for p in players:
        if p.line:
            by_league.setdefault(str(p.line.get("league") or p.league), []).append(p)
    for league, mine in sorted(by_league.items()):
        ledger = SOCCER_LEDGERS.get(league)
        if ledger is None:
            continue
        domestic = {ledger, *espn.DOMESTIC_CUPS.get(ledger, ())}
        start, end = _window(league, as_of)
        # Before the day, not on it: a match dated today may still be on.
        matches = [m for m in _ledger(store, ledger)
                   if str(m.get("competition_key")) in domestic
                   and start <= str(m.get("date") or "")[:10] < end
                   and m.get("goals_for") is not None]
        for p in mine:
            club = _plain(p.line.get("team") or "")
            theirs = [m for m in matches if _plain(m.get("team")) == club]
            if club and not theirs:
                problems.append(f"Soccer: no {league} matches found for "
                                f"{p.name}'s club, {p.line.get('team')}")
            position = str(p.line.get("position") or "")
            per_goal = scoring.goal_points_for(position)
            played = 0
            for match in theirs:
                lineup = _lineup(store, str(match["competition_key"]),
                                 str(match["event_id"]), loader)
                if lineup is None:
                    problems.append(f"Soccer: could not read match {match['event_id']} "
                                    f"({match.get('team')} v {match.get('opponent')})")
                    continue
                entry = next((e for e in lineup if _plain(e["name"]) == _plain(p.name)), None)
                if entry is None:
                    continue
                if any(entry[k] is None for k in SOCCER_STATS):
                    missing_stats += 1
                figures = {k: (entry[k] or 0.0) for k in SOCCER_STATS}
                appearance = (scoring.PTS_FULL_APPEARANCE if entry["started"]
                              else scoring.PTS_SHORT_APPEARANCE)
                points = (appearance + figures["goals"] * per_goal
                          + figures["assists"] * scoring.PTS_ASSIST
                          + figures["yellow"] * scoring.PTS_YELLOW
                          + figures["red"] * scoring.PTS_RED)
                played += 1
                rows.append({
                    "asset_id": p.asset_id, "game_key": str(match["event_id"]),
                    "date": str(match["date"])[:10], "role": "",
                    "points": points, "norm_key": league,
                    "opponent": str(match.get("opponent") or ""),
                    "detail": {"started": entry["started"],
                               **{k: v for k, v in figures.items() if v}},
                })
            expected = _number(p.line.get("matches"))
            if expected and played != expected:
                problems.append(f"Soccer: {p.name} has {played} match(es) on record "
                                f"against {expected:g} in his season line")
    if missing_stats:
        problems.append(f"Soccer: {missing_stats} appearance(s) whose summary "
                        f"carried no goals/assists/cards; scored as appearances")
    return rows, problems


# --- the run ------------------------------------------------------------------

def record(store: Store, season: str, as_of: date | str, verbose: bool = True,
           sports: tuple[str, ...] = SPORTS, loaders: dict | None = None) -> Report:
    """Write every rostered player's games up to ``as_of``.

    Idempotent: a game already recorded is rewritten with what the source says
    now, which is how a stat correction reaches the record.
    """
    loaders = loaders or {}
    report = Report()
    everyone = rostered(store, season, as_of)
    divisor = divisors(store, season)
    for sport in sports:
        mine = [p for p in everyone if sport_of(p.category) == sport]
        if not mine:
            continue
        report.players[sport] = len(mine)
        try:
            if sport == "NFL":
                rows, problems = nfl_games(store, mine, as_of)
            elif sport == "NBA":
                rows, problems = nba_games(store, mine, as_of)
            elif sport == "NHL":
                rows, problems = nhl_games(store, mine, as_of,
                                           log_loader=loaders.get("nhl"))
            elif sport == "MLB":
                rows, problems = mlb_games(store, mine, as_of, divisor,
                                           line_loader=loaders.get("mlb_lines"),
                                           log_loader=loaders.get("mlb_log"))
            else:
                rows, problems = soccer_games(store, mine, as_of,
                                              loader=loaders.get("soccer"))
        except Exception as exc:  # noqa: BLE001 -- one sport, not the run
            report.problems.append(f"{sport}: {type(exc).__name__}: {exc}")
            continue
        report.problems += problems + reconcile(sport, mine, rows)
        report.recorded[sport] = write(store, season, rows, divisor, sport)
    if verbose:
        print(report, flush=True)
    return report


def reconcile(sport: str, players: list[Rostered], rows: list[dict]) -> list[str]:
    """Each player's games against the season line he is scored on.

    The case for a game record rests on this: if the games do not add up to
    the season, a best game is being read out of an incomplete record, or the
    season is. Either way it is named. MLB's season carries run values no game
    has, so there the games are counted rather than summed; soccer's matches
    are counted where the lineups are read (see ``soccer_games``).
    """
    if sport == "Club Soccer":
        return []
    mine: dict[str, list[dict]] = {}
    for row in rows:
        mine.setdefault(row["asset_id"], []).append(row)
    out = []
    for p in players:
        if not p.line:
            continue
        games = mine.get(p.asset_id, [])
        if sport == "MLB":
            expected = _number(p.line.get("games"))
            if expected and len(games) != expected:
                out.append(f"MLB: {p.name} has {len(games)} game(s) on record "
                           f"against {expected:g} in his season line")
            continue
        season = _number(p.line.get("regular_points", p.line.get("total_points")))
        got = sum(g["points"] for g in games)
        if abs(got - season) > 0.05:
            out.append(f"{sport}: {p.name}'s {len(games)} game(s) come to "
                       f"{got:.2f} points against {season:.2f} in his season line")
    return out


def write(store: Store, season: str, rows: list[dict], divisor: dict,
          source: str) -> int:
    if not rows:
        return 0
    now = _now()
    out = []
    for row in rows:
        score = row.get("score")
        if score is None:
            key = row["norm_key"]
            if key not in divisor:
                continue
            score = 100 * row["points"] / divisor[key]
        out.append({
            "season": season, "asset_id": row["asset_id"],
            "game_key": row["game_key"], "date": row["date"],
            "role": row.get("role", ""), "points": round(float(row["points"]), 4),
            "score": round(float(score), 4), "opponent": row.get("opponent", ""),
            "detail": json.dumps(row.get("detail") or {}, sort_keys=True),
            "source": source, "recorded_at": now,
        })
    return store.upsert("game_scores", out, keys=("season", "asset_id", "game_key"))
