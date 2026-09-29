"""NCAA brackets as ESPN labels them, so the bye rule can be checked before it pays.

A team that skips rounds of a bracket by its seed is paid them as wins
(``whul.scoring.ncaa._bracket_byes``), and which round a team entered is read
off the game's note. Whether that works depends on words nobody here has seen:
the agent sandbox this project is developed in cannot reach ESPN. So this
reads last season's brackets, which are the best guide to how this season's
will be labelled, and says for each one whether the scorer would have paid
every team what the bracket itself says it skipped.

**The bracket, not the labels, is the answer.** A team's own schedule holds its
own games only, so the rest of its bracket is found by following its opponents'
schedules, and theirs, until no new team turns up -- a single-elimination
bracket is connected through its champion, so this reaches all of it. The round
each game was is then worked back from the final: whoever met in a game both
won their previous one, so each of those was one round earlier. That needs no
label at all, and is what the labels are checked against.

A bracket that is not single elimination -- double elimination, pool play --
is printed game by game with its labels, because its rounds cannot be worked
out that way. Those are the ones a stated ``round_names`` has to describe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import pandas as pd

from whul.scoring import ncaa

#: ESPN's league keys, and the scorer's names for them.
LEAGUES = {
    "ncaam": "NCAAM", "ncaaw": "NCAAW", "ncaaf": "NCAAF",
    "ncaabaseball": "NCAA Baseball", "ncaasoftball": "NCAA Softball",
}

#: A bracket is never bigger than this, so a walk that passes it has wandered
#: out of the bracket -- a label shared by two tournaments -- and stops.
MOST_TEAMS = 32

CFP = "College Football Playoff"


def last_season(league: str, today: date | None = None) -> int:
    """The most recent finished season, numbered as the feed numbers it."""
    today = today or date.today()
    y, m = today.year, today.month
    if league in ("ncaam", "ncaaw"):          # named for the year it ends in
        return y if m >= 5 else y - 1
    if league == "ncaaf":                     # named for the year it starts in
        return y - 1 if m >= 2 else y - 2
    return y if m >= 7 else y - 1             # played within the year


def bracket_of(league: str, notes: str, season_type: int) -> str | None:
    """Which bracket a game belongs to, from its note; None for any other game."""
    notes = str(notes or "")
    if league == "NCAAF":
        playoff = re.search(ncaa.FB_PLAYOFF_PATTERN, notes, re.IGNORECASE)
        return CFP if playoff and int(season_type) == 3 else None
    national = (ncaa.NOT_MM_PATTERN + "|" + ncaa.OTHER_POSTSEASON_PATTERN + "|"
                + ncaa.REGIONAL_PATTERN + "|" + ncaa.CWS_PATTERN)
    if not re.search(ncaa.CONF_TOURNEY_PATTERN, notes, re.IGNORECASE):
        return None
    if re.search(national, notes, re.IGNORECASE):
        return None
    return re.split(r"\s+[-–—]\s+", notes)[0].strip()


@dataclass
class Game:
    game_id: str
    day: str
    home: str
    away: str
    winner: str
    notes: str

    @property
    def teams(self) -> tuple[str, str]:
        return self.home, self.away

    @property
    def loser(self) -> str:
        return self.away if self.winner == self.home else self.home


@dataclass
class Bracket:
    league: str
    season: int
    name: str
    games: dict[str, Game] = field(default_factory=dict)
    unknown: set[str] = field(default_factory=set)   # opponents ESPN has no id for

    @property
    def teams(self) -> set[str]:
        return {t for g in self.games.values() for t in g.teams}


def collect(league: str, season: int, start: list[str],
            schedule: Callable[[str], pd.DataFrame | None]) -> list[Bracket]:
    """Every bracket the named teams played in, followed out to its edges.

    ``schedule`` returns a team's season, in the rows ``load_team_schedule``
    gives, or None where the feed has no such team.
    """
    scorer_league = LEAGUES[league]
    brackets: dict[str, Bracket] = {}
    seen: set[tuple[str, str]] = set()
    queue = [(name, None) for name in start]
    fetched: dict[str, pd.DataFrame | None] = {}
    while queue:
        team, wanted = queue.pop(0)
        if (team, wanted) in seen:
            continue
        seen.add((team, wanted))
        if team not in fetched:
            fetched[team] = schedule(team)
        rows = fetched[team]
        if rows is None:
            for b in brackets.values():
                if wanted == b.name:
                    b.unknown.add(team)
            continue
        for row in rows.itertuples(index=False):
            if not bool(row.completed):
                continue
            name = bracket_of(scorer_league, row.notes, row.season_type)
            if name is None or (wanted is not None and name != wanted):
                continue
            bracket = brackets.setdefault(name, Bracket(scorer_league, season, name))
            if len(bracket.teams) >= MOST_TEAMS:
                continue
            home_won = float(row.home_score) > float(row.away_score)
            bracket.games[str(row.game_id)] = Game(
                str(row.game_id), str(row.game_date), str(row.home_team),
                str(row.away_team), str(row.home_team if home_won else row.away_team),
                str(row.notes))
            for other in (row.home_team, row.away_team):
                if (str(other), name) not in seen:
                    queue.append((str(other), name))
    return list(brackets.values())


@dataclass
class Shape:
    """A single-elimination bracket's rounds, worked back from its final."""

    rounds: int
    round_of: dict[str, int]      # game id -> round, from 1
    entered: dict[str, int]       # team -> the round of its first game


def single_elimination(bracket: Bracket) -> Shape | None:
    """The rounds, from the games alone; None where it is not single elimination.

    Whoever met in a game both won the game before it, so each of those was one
    round earlier. Worked back from the final, that places every game -- and a
    game it cannot place, or places twice differently, is a bracket of some
    other kind.
    """
    games = sorted(bracket.games.values(), key=lambda g: (g.day, g.game_id))
    teams = bracket.teams
    if len(games) != len(teams) - 1:
        return None
    losses: dict[str, int] = {}
    for g in games:
        losses[g.loser] = losses.get(g.loser, 0) + 1
    if any(n > 1 for n in losses.values()) or len(teams) - len(losses) != 1:
        return None
    played: dict[str, list[Game]] = {}
    for g in games:
        for t in g.teams:
            played.setdefault(t, []).append(g)
    depth: dict[str, int] = {games[-1].game_id: 0}
    for g in reversed(games):
        if g.game_id not in depth:
            return None
        for t in g.teams:
            before = [p for p in played[t] if (p.day, p.game_id) < (g.day, g.game_id)]
            if not before:
                continue
            prev = before[-1].game_id
            if depth.setdefault(prev, depth[g.game_id] + 1) != depth[g.game_id] + 1:
                return None
    rounds = max(depth.values()) + 1
    round_of = {gid: rounds - d for gid, d in depth.items()}
    entered = {t: round_of[gs[0].game_id] for t, gs in played.items()}
    return Shape(rounds, round_of, entered)


def _label(notes: str) -> str:
    """The round's part of a note: what follows the bracket's name."""
    parts = re.split(r"\s+[-–—]\s+", str(notes), maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else str(notes).strip()


def _conference(name: str) -> str:
    """"Men's ACC Tournament" -> "ACC", which is what a table row matches on."""
    if name == CFP:
        return f"{CFP}|CFP"
    out = re.sub(r"\b(?:Men's|Women's|Baseball|Softball|Basketball)\b", "", name)
    out = re.sub(r"\bTournament\b", "", out)
    return re.sub(r"\s+", " ", out).strip()


def report(bracket: Bracket) -> list[str]:
    """What the bracket was, how ESPN labelled it, and whether the scorer agrees."""
    lines = [f"{bracket.league} {bracket.season}  {bracket.name}  "
             f"({len(bracket.teams)} teams, {len(bracket.games)} games)"]
    if bracket.unknown:
        lines.append(f"  ! no ESPN team for {', '.join(sorted(bracket.unknown))} "
                     f"-- the bracket may be short of their games")
    shape = single_elimination(bracket)
    games = sorted(bracket.games.values(), key=lambda g: (g.day, g.game_id))
    if shape is None:
        lines.append("  not single elimination (or not all of it was found), so its "
                     "rounds cannot be worked out from the games. Game by game:")
        for g in games:
            lines.append(f"    {g.day}  {g.winner} beat {g.loser}  [{_label(g.notes)}]")
        firsts: dict[str, str] = {}
        for g in games:
            for t in g.teams:
                firsts.setdefault(t, _label(g.notes))
        lines.append("  first game, by team:")
        for t in sorted(firsts):
            lines.append(f"    {t}: [{firsts[t]}]")
        return lines

    lines.append(f"  single elimination, {shape.rounds} rounds")
    by_round: dict[int, dict[str, int]] = {}
    for g in games:
        labels = by_round.setdefault(shape.round_of[g.game_id], {})
        labels[_label(g.notes)] = labels.get(_label(g.notes), 0) + 1
    for r in sorted(by_round):
        said = ", ".join(f"[{k}] x{v}" for k, v in by_round[r].items())
        entering = sorted(t for t, e in shape.entered.items() if e == r)
        lines.append(f"    round {r}: {said}  -- entered here: {', '.join(entering)}")

    # The check: the scorer's reading of each team's first label, against the
    # round the bracket says it entered.
    row = ncaa.ConferenceTournament(
        league=bracket.league, season=bracket.season,
        names=tuple(_conference(bracket.name).split("|")), rounds=shape.rounds)
    wrong = []
    for team, entered in sorted(shape.entered.items()):
        first = min((g for g in games if team in g.teams), key=lambda g: (g.day, g.game_id))
        read = row.round_of(first.notes)
        if read != entered:
            wrong.append(f"{team}: [{_label(first.notes)}] read as round {read}, "
                         f"entered at {entered}")
    names = ""
    if wrong:
        # Where every round has one label of its own, stating them in order
        # fixes it; where not, it needs a person.
        if all(len(v) == 1 for v in by_round.values()):
            names = "|".join(next(iter(by_round[r])) for r in sorted(by_round))
            lines.append("  the scorer misreads these with the rounds alone; with "
                         "round_names stated it would not:")
        else:
            lines.append("  the scorer misreads these, and a round carries more than "
                         "one label, so round_names cannot fix it -- ask:")
        lines.extend(f"    {w}" for w in wrong)
    else:
        lines.append("  the scorer reads every team's entry round correctly "
                     f"({len(shape.entered)} teams)")
    lines.append(f"  suggested row for next season: {bracket.season + 1},"
                 f"{bracket.league},{_conference(bracket.name)},{shape.rounds},{names}")
    return lines


def rostered(db_path: str, season: str, league: str) -> list[str]:
    """The league's rostered teams, straight off the roster."""
    import sqlite3
    from pathlib import Path

    if not Path(db_path).exists():
        return []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT DISTINCT a.display_name FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE r.season = ? AND a.league = ? ORDER BY 1", (season, league),
    ).fetchall()
    return [str(r[0]) for r in rows]
