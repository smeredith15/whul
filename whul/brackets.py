"""NCAA brackets, worked out from their games.

A team that skips rounds of a bracket by its seed is paid them as wins
(``whul.scoring.ncaa._bracket_byes``), and which round a team entered is read
off the game's note where the note says. Where it does not -- the Big 12's
baseball tournament called its first three rounds all "Big 12 Tournament" --
the round is worked out from the bracket itself, here, and carried on the game
as ``bracket_round`` for the scorer to read instead.

**The bracket, not the labels, is the answer.** A team's own schedule holds its
own games only, so the rest of its bracket is found by following its opponents'
schedules, and theirs, until no new team turns up -- a single-elimination
bracket is connected through its champion, so this reaches all of it. The round
each game was is then worked back from the final: whoever met in a game both
won their previous one, so each of those was one round earlier. That needs no
label at all. It does need the final, so a bracket still being played is
placed once it is finished.

The same walk is the ``probe-brackets`` command, which reads last season's
brackets -- the best guide to how this season's will be labelled -- and says
for each whether the scorer reads every team's entry round right. A bracket
that is not single elimination (double elimination, pool play) is printed
game by game with its labels, because its rounds cannot be worked out this
way; those are the ones a stated ``round_names`` has to describe.
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


def bracket_of(league: str, notes: str, season_type: int,
               day: str = "") -> str | None:
    """Which bracket a game belongs to, from its note; None for any other game.

    Conference tournaments only in the weeks they are played, as the scorer
    has it: February's "OU Tournament" is a round robin of non-conference
    games, and November's championships are invitationals.
    """
    notes = str(notes or "")
    if league == "NCAAF":
        playoff = re.search(ncaa.FB_PLAYOFF_PATTERN, notes, re.IGNORECASE)
        return CFP if playoff and int(season_type) == 3 else None
    sport = "basketball" if league in ("NCAAM", "NCAAW") else "diamond"
    month = pd.to_datetime(day, errors="coerce").month if day else None
    if month is not None and month == month \
            and month not in ncaa.CONF_TOURNEY_MONTHS[sport]:
        return None
    national =(ncaa.NOT_MM_PATTERN + "|" + ncaa.OTHER_POSTSEASON_PATTERN + "|"
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
            name = bracket_of(scorer_league, row.notes, row.season_type,
                              str(row.game_date))
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

    rounds: int                   # rounds from the first, a play-in not among them
    round_of: dict[str, int]      # game id -> round, from 1; a play-in is 0
    entered: dict[str, int]       # team -> the round of its first game
    #: Whether the last game is called a final. Until the final is played the
    #: bracket's two halves meet nowhere, and a half on its own is a smaller
    #: bracket that works out perfectly -- a round short, with its semifinal
    #: for a final. Only the name of its last game can say it is the whole.
    final_named: bool = False


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
    # A single game ahead of a bigger round is a play-in -- the Pac-12's 8 v 9
    # -- and is not a round the seeds after it skipped: the conference calls
    # its top two seeds' bye a double one, not a triple. Numbered 0, so the
    # rounds a seed skipped are counted from the round after it.
    sizes = [sum(1 for r in round_of.values() if r == k) for k in (1, 2)]
    if rounds > 2 and sizes[0] == 1 and sizes[1] > 1:
        round_of = {gid: r - 1 for gid, r in round_of.items()}
        rounds -= 1
    entered = {t: round_of[gs[0].game_id] for t, gs in played.items()}
    return Shape(rounds, round_of, entered,
                 final_named=ncaa._round_from_end(games[-1].notes) == 0)


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
    if not shape.final_named:
        lines.append("  ! its last game is not called a final, so this may be only "
                     "part of the bracket -- the rounds below could be a round short")
    if any(r == 0 for r in shape.round_of.values()):
        lines.append("  its opening game is a play-in, which is not counted as a "
                     "round a seed skipped (round 0 below)")
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


def espn_schedules(league: str, season: int) -> Callable[[str], pd.DataFrame | None]:
    """A team's season from ESPN by name, or None where ESPN has no such team.

    Fetched once a team: a bracket walk meets most teams twice.
    """
    from whul.sources import espn

    index = espn.team_index(league)
    lookup = {espn._match_key(n): i for n, i in index.items()}
    cache: dict[str, pd.DataFrame | None] = {}

    def schedule(team: str) -> pd.DataFrame | None:
        if team in cache:
            return cache[team]
        team_id = lookup.get(espn._match_key(team))
        rows = None
        if team_id:
            try:
                rows = espn.load_team_schedule(league, team_id, season)
            except Exception as exc:  # noqa: BLE001 -- one team must not stop the walk
                print(f"  ! {team}: {type(exc).__name__}: {exc}", flush=True)
        cache[team] = rows
        return rows

    return schedule


def place_rounds(league: str, games: pd.DataFrame,
                 schedules: Callable[[int], Callable[[str], pd.DataFrame | None]]
                 ) -> pd.DataFrame:
    """Each bracket game's round, from the bracket, where its note cannot say.

    Only brackets with a game whose round the scorer cannot read are walked,
    so a conference that names its rounds costs nothing. ``schedules`` gives a
    season's fetcher. A walk that fails leaves the rows as they were: the
    scorer then pays no byes there rather than guessed ones.
    """
    if games is None or games.empty or "notes" not in games.columns:
        return games
    scorer_league = LEAGUES[league]
    stated = [t for t in ncaa.conference_tournaments() if t.league == scorer_league]
    out = games.copy()
    if "bracket_round" not in out.columns:
        out["bracket_round"] = pd.NA
    need: dict[tuple[int, str], set[str]] = {}
    for row in out.itertuples(index=False):
        if "completed" in out.columns and not bool(row.completed):
            continue
        name = bracket_of(scorer_league, row.notes, getattr(row, "season_type", 2),
                          str(getattr(row, "game_date", "") or ""))
        if name is None:
            continue
        season = int(row.season)
        bracket = next((t for t in stated if t.season == season
                        and t.called(row.notes, "")), None)
        read = (None if bracket is not None and bracket.from_bracket
                else bracket.round_of(row.notes) if bracket
                else ncaa.round_number(row.notes, None))
        if read is None:
            need.setdefault((season, name), set()).update(
                {str(row.home_team), str(row.away_team)})
    for (season, name), teams in need.items():
        try:
            found = [b for b in collect(league, season, sorted(teams), schedules(season))
                     if b.name == name]
        except Exception as exc:  # noqa: BLE001 -- the rows stand without it
            print(f"  {scorer_league}: the {name} bracket could not be walked "
                  f"({type(exc).__name__}); no byes are paid there yet", flush=True)
            continue
        shape = single_elimination(found[0]) if found else None
        stated_rounds = {t.rounds for t in stated if t.season == season
                         and t.called(name, "")}
        if shape is not None and not (shape.final_named
                                      or shape.rounds in stated_rounds):
            shape = None
        if shape is None:
            print(f"  {scorer_league}: the {name} bracket is not finished or not "
                  f"single elimination, so its rounds are not placed yet", flush=True)
            continue
        ids = out["game_id"].astype(str)
        for game_id, number in shape.round_of.items():
            out.loc[ids == game_id, "bracket_round"] = number
    return out


def postseason_lines(team: str, rows: pd.DataFrame | None) -> list[str]:
    """A starting team's postseason games and bracket games, with their notes,
    so a label the bracket reader does not recognise is seen rather than
    missed."""
    if rows is None:
        return [f"{team}: no such team in ESPN's index"]
    types = rows["season_type"].value_counts().to_dict() if not rows.empty else {}
    lines = [f"{team}: {len(rows)} games, by season type {types}"]
    notable = rows[(rows["season_type"] == 3)
                   | rows["notes"].astype(str).str.contains(
                       r"Tournament|Championship|Playoff|Bowl", case=False)]
    for row in notable.itertuples(index=False):
        lines.append(f"    {row.game_date}  type {row.season_type}  "
                     f"{row.away_team} at {row.home_team}  [{row.notes}]")
    return lines
