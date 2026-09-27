"""What the standings would be under each best-game proposal.

The three options put to the league:

1. **Change nothing.** The top season-long scores in each category count.
2. **A best-game slot in every team-sport player category.** In the NFL, NBA,
   MLB and NHL a bench slot becomes a best-game slot; in each club-soccer
   category one of the four season slots does.
3. **Wildcard best-game slots.** Rosters as they are, plus five or six
   best-game slots open to any NFL, NBA, MLB, NHL or club-soccer player.

Everything else in a manager's total -- teams, golf, tennis, motorsport,
international soccer -- is the same in all three, so each proposal's total is
the published total with the six player categories' contribution swapped for
that proposal's.

Season scores are read from the day's ``slot_scores``, exactly as published. A
best-game score is the k best games at face value under the rules in
``whul.scoring.best_game``, taken from the games the published season score
counts and no others -- so the two sit on the same record:

* **NFL**: nflverse's weekly lines, the player's first N weeks, N being the
  games his published line counts. nflverse is often a day ahead of the
  published line; taking the same weeks keeps the two comparable. The weeks are
  checked against the published points.
* **MLB**: the Stats API's game logs, from the league's MLB start date, the
  first N games. Counting stats only, without the season line's year lift --
  both as agreed. Answers from GitHub Actions only.
* **Club soccer**: no per-match record is stored and none is fetched. With k =
  6 a player with six matches or fewer has every match counted, so his best-k is
  his published points exactly. A player with more has his worst matches
  dropped; where a match was stored on its own day its score is known, and
  otherwise it is taken as a scoreless appearance. Those rows say so.
* **NBA and NHL** have not started, so every score there is zero.

A proposal's evidence, not a scoring step: it reads the database and writes
nothing back.
"""

from __future__ import annotations

import itertools
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from whul.scoring import best_game as rules

#: k per sport, at the NFL best-three-weeks anchor. See the calibration.
K = {"NFL": 3, "NBA": 14, "NHL": 7, "Soccer": 6}
MLB_K = {rules.BAT: 10, rules.START: 4, rules.RELIEF: 13}

SOCCER = ("Club Soccer Top 3", "Club Soccer Other")
CATEGORIES = ("NFL", "NBA", "MLB", "NHL", *SOCCER)
WILDCARDS = (5, 6)

#: What a soccer match is worth with nothing in it but the appearance: a start
#: is two points and a substitute's appearance one. The estimate for a match
#: that was never stored on its own.
SOCCER_START, SOCCER_SUB = 2.0, 1.0

#: How far a set of weeks may sit from the published points and still be the
#: same games. Published totals are rounded to two places.
RECONCILE = 0.05


@dataclass
class Player:
    manager: str
    category: str
    asset_id: str
    name: str
    league: str
    role: str
    season: float
    counts: bool
    best: float = 0.0
    games: int = 0
    note: str = ""

    @property
    def is_soccer(self) -> bool:
        return self.category in SOCCER


@dataclass
class Pick:
    """One manager's lineup under one proposal."""

    total: float
    season: list[str]
    best: list[str]


@dataclass
class Report:
    as_of: str
    totals: dict[str, float]
    players: list[Player]
    current: dict[str, float]
    proposals: dict[str, dict[str, Pick]]
    problems: list[str] = field(default_factory=list)

    def standings(self) -> pd.DataFrame:
        rows = []
        for manager, total in self.totals.items():
            row = {"manager": manager, "Now": total}
            for name, picks in self.proposals.items():
                row[name] = total - self.current[manager] + picks[manager].total
            rows.append(row)
        return pd.DataFrame(rows).set_index("manager")


# --- the roster ---------------------------------------------------------------

def latest_day(store, season: str) -> str:
    day = store.scalar("SELECT MAX(as_of) FROM standings_snapshots WHERE season = ?",
                       (season,))
    if not day:
        raise RuntimeError(f"no standings for {season}")
    return str(day)


def roster(store, season: str, as_of: str) -> list[Player]:
    frame = store.query(
        f"""SELECT r.manager_id, r.category, s.asset_id, s.score, s.counts,
                   a.display_name, a.league, a.role
            FROM slot_scores s
            JOIN roster_slots r ON r.slot_id = s.slot_id
            JOIN assets a ON a.asset_id = s.asset_id
            WHERE r.season = ? AND s.as_of = ? AND r.asset_type = 'Player'
              AND r.category IN ({','.join('?' * len(CATEGORIES))})
            ORDER BY r.manager_id, r.category, s.score DESC""",
        (season, as_of, *CATEGORIES))
    return [Player(r.manager_id, r.category, r.asset_id, r.display_name,
                   str(r.league or ""), str(r.role or ""), float(r.score or 0.0),
                   bool(r.counts))
            for r in frame.itertuples()]


def published_line(store, asset_id: str, as_of: str) -> dict:
    """The stored line the day's score was built from."""
    text = store.scalar(
        "SELECT stats FROM raw_stats WHERE asset_id = ? AND as_of <= ? "
        "ORDER BY as_of DESC LIMIT 1", (asset_id, as_of))
    return json.loads(text) if text else {}


def _count(line: dict, *names: str) -> int:
    for name in names:
        value = line.get(name)
        if value is not None and not (isinstance(value, float) and math.isnan(value)):
            return int(round(float(value)))
    return 0


# --- best-k, sport by sport ---------------------------------------------------

def nfl_best(players: list[Player], lines: dict[str, dict], divisor: dict,
             season: int, loader=None) -> None:
    from whul.best_game_calibration import nfl_game_points

    mine = [p for p in players if p.category == "NFL"]
    if not mine:
        return
    if loader is None:
        from whul.sources.nflverse import load_player_stats as loader
    weeks = nfl_game_points(loader([season]))
    for p in mine:
        line = lines[p.asset_id]
        games = _count(line, "regular_games", "games_played")
        if not games:
            continue
        block = weeks[weeks["player"] == str(line.get("player_id", ""))
                      ].sort_values("week").head(games)
        if len(block) < games:
            p.note = f"nflverse has {len(block)} of his {games} games"
        points = float(block["points"].sum())
        published = float(line.get("regular_points", line.get("total_points")) or 0)
        if abs(points - published) > RECONCILE:
            p.note = (f"weeks come to {points:.2f} points, the published "
                      f"line {published:.2f}")
        scores = 100 * block["points"] / divisor[f"NFL_{line.get('position')}"]
        p.best, p.games = rules.best_k(scores, K["NFL"]), len(block)


def single_matches(points: list[tuple[str, float]],
                   matches: dict[str, int]) -> list[float]:
    """The scores of the matches that were stored on a day of their own.

    ``points`` is the published points by day, in order, and ``matches`` the
    match count by day where a line was stored. A day that added exactly one
    match added exactly that match's score; a day that added more lumped them
    together and says nothing about any one of them.
    """
    singles, prev_points, prev_matches = [], None, None
    for day, value in points:
        count = matches.get(day)
        if prev_matches is not None and count is not None and count - prev_matches == 1:
            singles.append(float(value) - float(prev_points))
        prev_points = value
        if count is not None:
            prev_matches = count
    return singles


def soccer_steps(store, asset_id: str, as_of: str) -> list[float]:
    points = store.query(
        "SELECT as_of, league_points FROM daily_scores WHERE asset_id = ? "
        "AND as_of <= ? ORDER BY as_of", (asset_id, as_of))
    matches = {day: _count(json.loads(text), "matches")
               for day, text in store.conn.execute(
                   "SELECT as_of, stats FROM raw_stats WHERE asset_id = ? AND as_of <= ?",
                   (asset_id, as_of))}
    return single_matches(list(zip(points["as_of"], points["league_points"].fillna(0))),
                          matches)


def soccer_best(store, players: list[Player], lines: dict[str, dict],
                divisor: dict, as_of: str, steps=None) -> None:
    steps = steps or (lambda asset_id: soccer_steps(store, asset_id, as_of))
    for p in players:
        if not p.is_soccer:
            continue
        line = lines[p.asset_id]
        matches = _count(line, "matches")
        points = float(line.get("regular_points", line.get("total_points")) or 0)
        league = line.get("league") or p.league
        if not matches or league not in divisor:
            continue
        p.games = matches
        extra = matches - K["Soccer"]
        if extra <= 0:
            # Every match counts, so the best-k is the season score itself --
            # taken as published rather than recomputed through a divisor
            # that could differ from it in the last place.
            p.best = p.season
            continue
        known = steps(p.asset_id)
        starts = _count(line, "starts")
        floor = SOCCER_START if starts >= matches else SOCCER_SUB
        # The matches not stored alone are taken as scoreless appearances, the
        # least a match can plausibly be; the worst ``extra`` of those and the
        # known ones are dropped.
        # known ones are dropped. On a tie the known match is the one dropped,
        # so the figure is estimated only when it has to be.
        pool = ([(v, 0) for v in known]
                + [(floor, 1)] * max(matches - len(known), 0))
        dropped = sorted(pool)[:extra]
        p.best = 100 * (points - sum(v for v, _ in dropped)) / divisor[league]
        if any(guessed for _, guessed in dropped):
            p.note = (f"{matches} matches; the dropped match is estimated as a "
                      f"scoreless {'start' if floor == SOCCER_START else 'appearance'}")


def _plain(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


def mlb_best(players: list[Player], lines: dict[str, dict], scale, as_of: str,
             season: int, line_loader=None, log_loader=None) -> list[str]:
    from whul import best_game_calibration as calibration
    from whul.config.league import season_start
    from whul.sources import mlb

    mine = [p for p in players if p.category == "MLB"]
    if not mine:
        return []
    line_loader = line_loader or mlb.load_stats_api_players
    ids: dict[str, str] = {}
    for group in ("hitting", "pitching"):
        found = line_loader(season, group)
        for name, pid in zip(found.get("player", []), found.get("player_id", [])):
            ids.setdefault(_plain(name), str(pid))

    start, end = season_start("MLB").isoformat(), as_of
    problems = []
    for p in mine:
        line = lines[p.asset_id]
        pid = ids.get(_plain(p.name))
        if pid is None:
            problems.append(f"MLB: {p.name} is not in the Stats API's {season} lines")
            continue
        spans = line.get("season_lines") or [line]
        batted = any(_count(s, "ab") for s in spans)
        pitched = any(float(s.get("ip") or 0) > 0 for s in spans
                      if not (isinstance(s.get("ip"), float) and math.isnan(s["ip"])))
        group = "Batter" if p.role == "Batter" else "Starter"
        subject = calibration.Subject(pid, p.name, season, group,
                                      two_way=batted and pitched)
        try:
            games = calibration.mlb_games(subject, scale, log_loader=log_loader)
        except Exception as exc:  # noqa: BLE001 -- one player, not the run
            problems.append(f"MLB: {p.name}: {type(exc).__name__}: {exc}")
            continue
        dates = games["date"].astype(str).str[:10]
        games = games[(dates >= start) & (dates <= end)].sort_values("date")
        wanted = _count(line, "games")
        if wanted and len(games) > wanted:
            games = games.head(wanted)
        if wanted and len(games) != wanted:
            p.note = f"{len(games)} logged games against {wanted} published"
        by_role: dict[str, list[float]] = {}
        for row in games.itertuples():
            by_role.setdefault(row.role, []).append(float(row.score))
        p.best, p.games = rules.exchange(by_role, MLB_K), len(games)
    return problems


# --- the proposals ------------------------------------------------------------

def _counting_slots(players: list[Player]) -> dict[tuple[str, str], int]:
    slots: dict[tuple[str, str], int] = {}
    for p in players:
        slots[(p.manager, p.category)] = slots.get((p.manager, p.category), 0) + p.counts
    return slots


def current(players: list[Player]) -> dict[str, float]:
    """What the six categories contribute to each total today."""
    out: dict[str, float] = {}
    for p in players:
        out[p.manager] = out.get(p.manager, 0.0) + p.season * p.counts
    return out


def proposal_two(players: list[Player]) -> dict[str, Pick]:
    """One best-game slot a category: soccer's from a season slot, the rest
    from the bench."""
    slots = _counting_slots(players)
    out: dict[str, Pick] = {}
    for (manager, category), n in sorted(slots.items()):
        pool = [p for p in players if (p.manager, p.category) == (manager, category)]
        seats = n - 1 if category in SOCCER else n
        found = rules.best_configuration(
            [rules.Candidate(p.asset_id, p.season, p.best) for p in pool], seats)
        pick = out.setdefault(manager, Pick(0.0, [], []))
        pick.total += found.total
        pick.season += list(found.season)
        pick.best += [found.best] if found.best else []
    return out


def proposal_three(players: list[Player], wildcards: int) -> dict[str, Pick]:
    """Season slots as they are, plus ``wildcards`` best-game slots open to
    any player in the six categories, filled whichever way scores most.

    Every set of up to ``wildcards`` players with a positive best-game score is
    tried in the wildcard slots, each category's season slots taking the best
    of who is left. Ties go to the lineup with more in its season slots, which
    is the one plain best-ball would have chosen.
    """
    slots = _counting_slots(players)
    out: dict[str, Pick] = {}
    for manager in sorted({p.manager for p in players}):
        mine = [p for p in players if p.manager == manager]
        hopefuls = [p for p in mine if p.best > 0]

        def seated(wild: set[str]) -> tuple[float, list[str]]:
            total, names = 0.0, []
            for category in CATEGORIES:
                pool = sorted((p for p in mine if p.category == category
                               and p.asset_id not in wild),
                              key=lambda p: p.season, reverse=True)
                chosen = pool[:slots.get((manager, category), 0)]
                total += sum(p.season for p in chosen)
                names += [p.asset_id for p in chosen]
            return total, names

        best_key, best_pick = None, None
        for size in range(0, min(wildcards, len(hopefuls)) + 1):
            for wild in itertools.combinations(hopefuls, size):
                ids = {p.asset_id for p in wild}
                seasons, names = seated(ids)
                total = seasons + sum(p.best for p in wild)
                key = (round(total, 9), round(seasons, 9))
                if best_key is None or key > best_key:
                    best_key = key
                    best_pick = Pick(total, names,
                                     [p.asset_id for p in sorted(wild, key=lambda p: -p.best)])
        out[manager] = best_pick
    return out


def compute(store, season_label: str | None = None, as_of: str | None = None,
            nfl_loader=None, mlb_line_loader=None, mlb_log_loader=None) -> Report:
    from whul import best_game_calibration as calibration
    from whul.config.league import SEASON

    label = season_label or SEASON.label
    day = as_of or latest_day(store, label)
    players = roster(store, label, day)
    lines = {p.asset_id: published_line(store, p.asset_id, day) for p in players}
    scale = calibration.frozen_scale(store, label)
    year = date.fromisoformat(day).year

    problems: list[str] = []
    try:
        nfl_best(players, lines, scale.divisor, year, loader=nfl_loader)
    except Exception as exc:  # noqa: BLE001 -- say so and carry on
        problems.append(f"NFL: {type(exc).__name__}: {exc}")
    soccer_best(store, players, lines, scale.divisor, day)
    try:
        problems += mlb_best(players, lines, scale, day, year,
                             line_loader=mlb_line_loader, log_loader=mlb_log_loader)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"MLB: {type(exc).__name__}: {exc}")

    totals = dict(store.conn.execute(
        "SELECT manager_id, total FROM standings_snapshots WHERE season = ? "
        "AND as_of = ? ORDER BY total DESC", (label, day)).fetchall())
    proposals = {"Proposal 2": proposal_two(players)}
    for w in WILDCARDS:
        proposals[f"Proposal 3 ({w})"] = proposal_three(players, w)
    return Report(day, totals, players, current(players), proposals, problems)


# --- the report ---------------------------------------------------------------

def _name(report: Report, asset_id: str) -> str:
    return next(p.name for p in report.players if p.asset_id == asset_id)


def render(report: Report) -> str:
    out = []
    say = out.append
    table = report.standings()
    say(f"\nStandings under each proposal, {report.as_of}\n")
    head = f"  {'':4s}" + "".join(f"{c:>20s}" for c in table.columns)
    say(head)
    ranks = table.rank(ascending=False, method="min").astype(int)
    for manager, row in table.sort_values("Now", ascending=False).iterrows():
        cells = "".join(f"{row[c]:>15.1f} ({ranks.loc[manager, c]})" for c in table.columns)
        say(f"  {manager:4s}{cells}")

    say("\n  Best-game slots used:")
    for name, picks in report.proposals.items():
        say(f"\n  {name}")
        for manager in table.sort_values("Now", ascending=False).index:
            pick = picks[manager]
            by_id = {p.asset_id: p for p in report.players if p.manager == manager}
            best = ", ".join(f"{by_id[a].name} {by_id[a].best:.1f}" for a in pick.best)
            gain = pick.total - report.current[manager]
            say(f"    {manager:4s}{gain:+7.1f}  {best or '(none)'}")

    say("\n  Every player, season score and best-k:")
    for p in sorted(report.players, key=lambda p: (p.manager, CATEGORIES.index(p.category),
                                                   -p.season)):
        if not p.season and not p.best:
            continue
        say(f"    {p.manager:4s}{p.category:20s}{p.name:28s}{p.season:7.1f}"
            f"{p.best:7.1f}  {p.games:>3d} games"
            f"{'  counts' if p.counts else ''}{'  -- ' + p.note if p.note else ''}")
    if report.problems:
        say(f"\n  {len(report.problems)} problem(s):")
        for line in report.problems:
            say(f"    {line}")
    say("")
    return "\n".join(out)


def to_json(report: Report) -> str:
    return json.dumps({
        "as_of": report.as_of,
        "standings": report.standings().reset_index().to_dict("records"),
        "current": report.current,
        "players": [p.__dict__ for p in report.players],
        "proposals": {n: {m: pick.__dict__ for m, pick in picks.items()}
                      for n, picks in report.proposals.items()},
        "problems": report.problems,
    }, indent=1, default=str)
