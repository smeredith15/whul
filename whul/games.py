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
applies -- see ``whul.scoring.best_game``.

Every game counts, not only the regular season's: playoff games, the NBA
Play-In, and in club soccer the domestic cups and every European tie,
qualifying rounds included. Each is scored exactly as a regular-season game is,
on the same scale. ``phase`` says which kind a game was -- ``regular`` for what
the season line is made of (a soccer player's league and domestic cups),
``playoffs``, ``play-in``, or ``europe`` -- so the check against the season line
reads only the games the season line holds.

A game is written whether or not it will ever count: the slot's best k are
chosen at rollup, from whatever the slot's occupants played while they held it.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from whul.config.league import ALL_SLOTS, SEASON, season_start
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

#: What each phase is called where a game is listed.
PHASE_LABELS = {"playoffs": "Playoffs", "play-in": "Play-In", "europe": ""}

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
    # Playoff weeks come too.
    scored = nfl_game_points(raw, postseason=True)
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
            "phase": "playoffs" if bool(row["post"]) else "regular",
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
    games = nba.game_points(raw, include=(nba.SEASON_TYPE_REGULAR, nba.SEASON_TYPE_POST,
                                          nba.SEASON_TYPE_PLAYIN))
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
            "phase": {nba.SEASON_TYPE_POST: "playoffs",
                      nba.SEASON_TYPE_PLAYIN: "play-in"}.get(int(row.season_type), "regular"),
            "points": float(row.game_points), "norm_key": str(row.norm_key),
            "opponent": "",
            "detail": {c: _number(getattr(row, c)) for c in NBA_DETAIL
                       if _number(getattr(row, c))},
        })
    return rows, []


# --- NHL ----------------------------------------------------------------------

#: A game's figures, read from the game log; hits and blocked shots are not
#: in it and come from the realtime report (``realtime_loader``).
NHL_FIELDS = {"goals": "goals", "assists": "assists", "shots": "shots",
              "plus_minus": "plusMinus", "pp_points": "powerPlayPoints",
              "sh_points": "shorthandedPoints", "game_winners": "gameWinningGoals"}
NHL_REALTIME_FIELDS = {"hits": "hits", "blocks": "blockedShots"}

#: (month, day) from which the NHL playoffs can have started.
NHL_PLAYOFFS_FROM = (4, 1)


def nhl_games(store: Store, players: list[Rostered], as_of,
              log_loader=None, realtime_loader=None) -> tuple[list[dict], list[str]]:
    """Each rostered skater's games, from his game log and the realtime report.

    Both or neither, per skater and phase: a game written without its hits and
    blocks would be a game worth less than it was, and the row it replaced
    stays as it was when this one is skipped.
    """
    from whul.scoring import nhl as scoring
    from whul.sources import nhl as source

    log_loader = log_loader or (lambda pid, season_id, game_type=source.GAME_TYPE_REGULAR:
                                source._web(f"/player/{pid}/game-log/{season_id}/{game_type}"))
    realtime_loader = realtime_loader or source.load_skater_game_realtime
    start, end = _window("NHL", as_of)
    # The season the league year's hockey is, by the API's own numbering.
    ends = date.fromisoformat(start).year + 1
    season_id = source.season_id(ends)
    # The playoffs are asked for once they can have started, so the rest of
    # the year costs nothing.
    kinds = [(source.GAME_TYPE_REGULAR, "regular")]
    if end >= date(ends, *NHL_PLAYOFFS_FROM).isoformat():
        kinds.append((source.GAME_TYPE_PLAYOFFS, "playoffs"))
    rows, problems = [], []
    for p in players:
        if str(p.line.get("role") or p.role) == scoring.GOALIE_ROLE:
            continue
        pid = str(p.line.get("player_id") or "")
        if not pid:
            if p.line:
                problems.append(f"NHL: {p.name} has no player id to ask for")
            continue
        logged = []
        for game_type, phase in kinds:
            try:
                payload = (log_loader(pid, season_id) if game_type == source.GAME_TYPE_REGULAR
                           else log_loader(pid, season_id, game_type))
                games = (payload or {}).get("gameLog") or []
                extra = realtime_loader(pid, ends, game_type) if games else {}
            except Exception as exc:  # noqa: BLE001 -- one player, not the run
                problems.append(f"NHL: {p.name} ({phase}): {type(exc).__name__}: {exc}")
                continue
            unmatched = [g for g in games if str(g.get("gameId")) not in extra]
            if unmatched:
                # The two reports disagree about which games he played -- one
                # a night behind the other. Skipped whole rather than written
                # short; tomorrow they agree.
                problems.append(f"NHL: {p.name} ({phase}): {len(unmatched)} game(s) "
                                f"not yet in the realtime report; kept as they were")
                continue
            logged += [(game, phase, extra[str(game.get("gameId"))]) for game in games]
        for game, phase, more in logged:
            day = str(game.get("gameDate") or "")[:10]
            if not (start <= day <= end):
                continue
            stat = {ours: _number(game.get(theirs)) for ours, theirs in NHL_FIELDS.items()}
            stat.update({ours: _number(more.get(theirs))
                         for ours, theirs in NHL_REALTIME_FIELDS.items()})
            points = scoring.skater_points(stat)
            rows.append({
                "asset_id": p.asset_id, "game_key": str(game.get("gameId")),
                "date": day, "role": "", "phase": phase, "points": points,
                "norm_key": "NHL",
                "opponent": str(game.get("opponentAbbrev") or ""),
                "detail": {k: v for k, v in stat.items() if v},
            })
    return rows, problems


# --- MLB ----------------------------------------------------------------------

#: (month, day) from which a season's postseason can have started.
MLB_OCTOBER_FROM = (9, 28)

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
            # October's rounds once the regular season can be over: one log a
            # round, and nothing asked for the rest of the year.
            rounds = (mlb.POSTSEASON_GAME_TYPES
                      if end >= date(year, *MLB_OCTOBER_FROM).isoformat() else ())
            logs = {}
            for group, wanted in (("hitting", batted), ("pitching", pitched)):
                if not wanted:
                    continue
                parts = []
                for game_type in ("R", *rounds):
                    try:
                        log = (log_loader(pid, year, group) if game_type == "R"
                               else log_loader(pid, year, group, game_type=game_type))
                    except Exception as exc:  # noqa: BLE001
                        problems.append(f"MLB: {p.name} {year} {group} {game_type}: "
                                        f"{type(exc).__name__}: {exc}")
                        continue
                    if log is not None and len(log):
                        parts.append(log.assign(
                            _phase="regular" if game_type == "R" else "playoffs"))
                if parts:
                    logs[group] = pd.concat(parts, ignore_index=True)
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
            "phase": str(record.get("_phase") or "regular"),
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


#: A competition's name as a game row shows it, by the feed's key.
COMPETITION_LABELS = {
    "ucl": "Champions League", "uel": "Europa League", "uecl": "Conference League",
    "facup": "FA Cup", "efl_cup": "EFL Cup", "copadelrey": "Copa del Rey",
    "dfbpokal": "DFB-Pokal", "coppaitalia": "Coppa Italia",
    "coupedefrance": "Coupe de France", "usopencup": "US Open Cup",
}


def _competition_label(key: str, described: str) -> str:
    """The competition's name, saying so where the tie was a qualifier."""
    label = COMPETITION_LABELS.get(key, key)
    words = described.lower()
    if "qualif" in words:
        label += " qualifying"
    elif "playoff" in words or "play-off" in words or "knockout round play" in words:
        label += " play-off"
    return label


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


#: A match's figures a game row keeps, beside the competition and whether he
#: started: the ones a profile lists, and the rating behind the bonus.
SOCCER_DETAIL = ("goals", "assists", "shots_on_target", "chances_created", "tackles",
                 "interceptions", "shot_blocks", "clearances", "dribbles",
                 "dispossessed", "yellow", "red", "own_goals", "rating")


def _fotmob_walker(start: date, end: date, keys: tuple[str, ...]) -> list[dict]:
    """Every finished match in ``keys`` between two days, from FotMob -- read
    from what tonight's pull already kept, so this costs nothing it did not."""
    from whul import clock
    from whul.sources import fotmob

    found = fotmob.walk(fotmob.Client(), start, end, keys, clock.today())
    if found.stopped:
        raise RuntimeError(f"FotMob stopped answering ({found.matches} match(es) read)")
    return found.lines


def soccer_games(store: Store, players: list[Rostered], as_of,
                 walker=None) -> tuple[list[dict], list[str]]:
    """Each rostered player's matches, priced one by one at the values in
    ``whul.scoring.soccer_match`` -- the same lines his season is summed from.

    A player is found by the name his stored line carries, which is FotMob's
    own spelling once the pull has run; where two of FotMob's players share
    it, the one at his club. He is priced at the position the league holds
    for him, as his season is.
    """
    from whul.config.league import SEASON
    from whul.resolve import normalize_name, normalize_team
    from whul.scoring import soccer_match
    from whul.sources import espn

    walker = walker or _fotmob_walker
    rows, problems = [], []
    by_league: dict[str, list[Rostered]] = {}
    for p in players:
        if p.line:
            by_league.setdefault(str(p.line.get("league") or p.league), []).append(p)
    plan = {}
    for league, mine in by_league.items():
        key = SOCCER_LEDGERS.get(league)
        if key is None:
            continue
        domestic = {key, *espn.DOMESTIC_CUPS.get(key, ())}
        start, end = _window(league, as_of)
        # From the league year's opening if the league itself opens later: a
        # club's cup tie can come first -- Frankfurt played the DFB-Pokal on
        # the 21st, a week before the Bundesliga began -- and the season line
        # counts it, so its match record has to.
        start = min(start, SEASON.start.isoformat())
        plan[league] = (key, domestic, domestic | set(espn.continental_for(key)),
                        start, end, mine)
    if not plan:
        return rows, problems
    keys = tuple(dict.fromkeys(k for v in plan.values() for k in sorted(v[2])))
    first = min(date.fromisoformat(v[3]) for v in plan.values())
    # Before the day, not on it: a match dated today may still be on.
    last = max(date.fromisoformat(v[4]) for v in plan.values()) - timedelta(days=1)
    try:
        lines = walker(first, last, keys)
    except Exception as exc:  # noqa: BLE001 -- the records stay as they were
        said = (f"Soccer: FotMob could not be read, so no match was recorded: "
                f"{type(exc).__name__}: {exc}")
        return rows, [said]
    named: dict[str, list[dict]] = {}
    for line in lines:
        named.setdefault(normalize_name(str(line.get("player") or "")), []).append(line)

    for league, (key, domestic, everything, start, end, mine) in sorted(plan.items()):
        for p in mine:
            his = [line for line in named.get(normalize_name(
                       str(p.line.get("player") or p.name)), [])
                   if line.get("competition_key") in everything
                   and start <= str(line.get("date") or "")[:10] < end]
            ids = {str(line.get("player_id")) for line in his}
            if len(ids) > 1:
                club = normalize_team(str(p.line.get("team") or ""))
                at_club = {str(line.get("player_id")) for line in his
                           if normalize_team(str(line.get("team") or "")) == club}
                if len(at_club) == 1:
                    his = [line for line in his if str(line.get("player_id")) in at_club]
                else:
                    problems.append(f"Soccer: {len(ids)} players called {p.name} "
                                    f"in FotMob, and none of them only at his club")
                    continue
            held = str(p.line.get("position") or "").upper()[:1]
            played = 0
            for line in his:
                priced = {**line, "position": held if held in soccer_match.SCORED_POSITIONS
                          else line.get("position")}
                competition = str(line["competition_key"])
                phase = "regular" if competition in domestic else "europe"
                played += phase == "regular"
                label = _competition_label(competition, "")
                rows.append({
                    "asset_id": p.asset_id, "game_key": f"fotmob-{line['match_id']}",
                    "date": str(line["date"])[:10], "role": "", "phase": phase,
                    "points": soccer_match.match_points(priced), "norm_key": league,
                    "opponent": str(line.get("opponent") or ""),
                    "detail": {"started": bool(line.get("started")),
                               "minutes": line.get("minutes"),
                               **({"competition": label} if competition != key else {}),
                               **({"potm": True} if line.get("potm") else {}),
                               **{k: line[k] for k in SOCCER_DETAIL if line.get(k)}},
                })
            # Domestic matches only: they are what the season line counts.
            expected = _number(p.line.get("matches"))
            if expected and played != expected:
                problems.append(f"Soccer: {p.name} has {played} domestic match(es) "
                                f"on record against {expected:g} in his season line")
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
                                           log_loader=loaders.get("nhl"),
                                           realtime_loader=loaders.get("nhl_realtime"))
            elif sport == "MLB":
                rows, problems = mlb_games(store, mine, as_of, divisor,
                                           line_loader=loaders.get("mlb_lines"),
                                           log_loader=loaders.get("mlb_log"))
            else:
                rows, problems = soccer_games(store, mine, as_of,
                                              walker=loaders.get("soccer_lines"))
                _drop_espn_matches(store, season, rows)
        except Exception as exc:  # noqa: BLE001 -- one sport, not the run
            report.problems.append(f"{sport}: {type(exc).__name__}: {exc}")
            continue
        report.problems += problems + reconcile(sport, mine, rows)
        report.recorded[sport] = write(store, season, rows, divisor, sport)
    if "NHL" in sports:
        _record_club_games(store, season, report, loaders.get("nhl_teams"))
    if verbose:
        print(report, flush=True)
    return report


#: The ledger NHL club games are written to, for a club's results list. The
#: team summary the NHL club source scores from is a season total; this is
#: the same endpoint one game at a time.
NHL_TEAM_GAMES = "nhl-team-games"
NHL_TEAM_GAME_KEYS = ("season", "gameId", "teamId")


def _record_club_games(store: Store, season_label: str, report: Report,
                       loader=None) -> None:
    """Every NHL club game this league year, kept for the results lists.

    Every other team league's games are already kept by its own source's
    ledger; the NHL's club source reads season totals, so this is the one
    asked for here. Never fatal: a results list a day behind is not a reason
    to lose the players' games recorded above.
    """
    from whul.benchmark_sources import _nhl_playoffs_possible
    from whul.store import feed_ledger

    held = store.scalar(
        "SELECT COUNT(*) FROM slot_occupancy o "
        "JOIN roster_slots r ON r.slot_id = o.slot_id "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE r.season = ? AND a.league = 'NHL' AND a.asset_type = 'Team'",
        (season_label,)) or 0
    if not held:
        return
    if loader is None:
        from whul.sources import nhl as source

        def loader(seasons, kind):
            return source.load_team_games(seasons, kind)

    season = SEASON.end.year
    kinds = [2] + ([3] if _nhl_playoffs_possible(season) else [])
    try:
        frames = [loader([season], kind) for kind in kinds]
        frame = pd.concat([f for f in frames if f is not None and not f.empty],
                          ignore_index=True) if any(
            f is not None and not f.empty for f in frames) else pd.DataFrame()
        if frame.empty:
            return
        feed_ledger.record(store, NHL_TEAM_GAMES, frame, NHL_TEAM_GAME_KEYS)
        report.recorded["NHL clubs"] = len(frame)
    except Exception as exc:  # noqa: BLE001 -- one ledger, not the run
        report.problems.append(f"NHL club games: {type(exc).__name__}: {exc}")


def _drop_espn_matches(store: Store, season: str, rows: list[dict]) -> None:
    """The matches recorded from ESPN, for every player FotMob has just
    recorded: the same matches under ESPN's ids, which kept would count twice
    in a best-performances slot and on a results list."""
    assets = sorted({row["asset_id"] for row in rows})
    if not assets:
        return
    with store.transaction() as conn:
        conn.execute(
            f"DELETE FROM game_scores WHERE season = ? AND source = 'Club Soccer' "
            f"AND game_key NOT LIKE 'fotmob-%' AND asset_id IN "
            f"({','.join('?' * len(assets))})", (season, *assets))


def reconcile(sport: str, players: list[Rostered], rows: list[dict]) -> list[str]:
    """Each player's games against the season line he is scored on.

    The case for a game record rests on this: if the games do not add up to
    the season, a best game is being read out of an incomplete record, or the
    season is. Either way it is named. MLB's season carries run values no game
    has, so there the games are counted rather than summed; soccer's matches
    are counted where they are read (see ``soccer_games``) and summed here,
    being priced from the same FotMob lines as the season.
    """
    mine: dict[str, list[dict]] = {}
    for row in rows:
        # The season line is the regular season's; playoff and European games
        # are in the record and not in the line.
        if row.get("phase", "regular") != "regular":
            continue
        mine.setdefault(row["asset_id"], []).append(row)
    out = []
    for p in players:
        if not p.line:
            continue
        if sport == "Club Soccer" and "pts_appearance" not in p.line:
            # A line scored before the match-by-match scoring has no terms
            # its matches could add up to; tonight's pull replaces it.
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
            "role": row.get("role", ""), "phase": row.get("phase", "regular"),
            "points": round(float(row["points"]), 4),
            "score": round(float(score), 4), "opponent": row.get("opponent", ""),
            "detail": json.dumps(row.get("detail") or {}, sort_keys=True),
            "source": source, "recorded_at": now,
        })
    return store.upsert("game_scores", out, keys=("season", "asset_id", "game_key"))
