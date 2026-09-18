"""Where two managers' assets met each other.

A cross-sport league has no fixtures. Nobody plays anybody; every score is a
number measured against a frozen bar, and the season is a marathon run in
parallel. That is what makes the scale work across twenty leagues, and it is
also why the league has no stories in it -- until two managers happen to own
both sides of the same match.

This finds those. It reads the feed ledger, which already holds one row per
game for every source that reports games, and keeps the rows where the two
sides resolve to assets on two different rosters. Nothing is scored here and
nothing feeds the standings: a meeting is a fact about a fixture, and the
moment it pays points it becomes a lottery on the schedule -- a Premier League
club this season faces a rostered opponent in 47% of its games and a Serie A
club in 21%, which is a fact about who drafted what, not about either manager.

Three shapes reach the ledger and all three are read here, dispatched on the
payload's own keys rather than on a source name, since a league renamed in the
source table should not quietly stop having a history:

``home_team``/``away_team``   a game, with both scores -- MLB, the NCAA
``team``/``opponent``         one row per side of a match -- club soccer
``winner``/``loser``          a match with no draw in it -- tennis

Golf and motorsport have neither shape and are absent by construction rather
than by exclusion: a field of a hundred and fifty is not a meeting, and one
event routinely holds several assets from the same roster.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from whul.resolve import load_aliases, normalize_name, normalize_team
from whul.store.db import Store

#: What one meeting carries. The two sides are ``a`` and ``b`` rather than
#: home and away: a tennis match has no home side, and the winner is named
#: outright rather than left to be worked out from a score that may not exist.
MEETING_COLUMNS = (
    "date", "competition",
    "a_id", "a_name", "a_manager", "a_league", "a_category",
    "b_id", "b_name", "b_manager", "b_league", "b_category",
    "a_score", "b_score", "won", "detail",
)


@dataclass(frozen=True)
class Side:
    """One half of a fixture, before anything is known about who holds it."""

    name: str
    score: float | None


def _sides(row: dict) -> tuple[Side, Side, str] | None:
    """The two sides of a ledger row, or None where it is not a fixture.

    The third value is the key a duplicate folds onto. Club soccer writes a
    row per side, so one match arrives twice and the same meeting would be
    counted twice -- once from each end, with the score the other way round.
    """
    if row.get("winner") and row.get("loser"):
        # A completed match, so the winner is known and no score is needed to
        # find them. The set score rides along as detail.
        return (Side(str(row["winner"]), None), Side(str(row["loser"]), None),
                str(row.get("match_uid")
                    or f"{row.get('date')}|{row['winner']}|{row['loser']}"))

    if row.get("home_team") and row.get("away_team"):
        return (Side(str(row["home_team"]), _number(row.get("home_score"))),
                Side(str(row["away_team"]), _number(row.get("away_score"))),
                str(row.get("game_id")
                    or f"{row.get('game_date')}|{row['home_team']}"))

    if row.get("team") and row.get("opponent"):
        # Both halves of a tie are in the ledger; the key holds the pair
        # unordered so the two fold together, and the date keeps the two legs
        # of a cup tie apart.
        pair = "|".join(sorted((str(row["team"]), str(row["opponent"]))))
        return (Side(str(row["team"]), _number(row.get("goals_for"))),
                Side(str(row["opponent"]), _number(row.get("goals_against"))),
                str(row.get("event_id") or f"{row.get('date')}|{pair}"))
    return None


def _number(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _day(row: dict) -> str:
    for key in ("game_date", "date", "match_date"):
        value = row.get(key)
        if value:
            return str(value)[:10]
    return ""


def _holders(store: Store, season: str) -> pd.DataFrame:
    """Every rostered asset with the days it was somebody's.

    The occupancy rather than the current roster: a meeting that happened in
    September belongs to whoever held the asset in September, and attributing
    it to whoever holds it now would rewrite history every time anyone trades.
    """
    return store.query(
        "SELECT o.asset_id, r.manager_id, r.category, o.start_date, o.end_date, "
        "       a.display_name, a.league, a.asset_type "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE r.season = ?", (season,),
    )


def _spellings(store: Store, held: pd.DataFrame) -> dict[str, str]:
    """``{name as anything calls it: asset_id}``.

    Three spellings of one asset reach this: the roster's, the feed's, and the
    feed's reduced to its distinguishing words. The alias table is what makes
    the second work -- the tennis feed files Carlos Alcaraz as "Carlos Alcaraz
    Garfia", and a lookup on display names alone loses every match he played.
    """
    out: dict[str, str] = {}
    for row in held.itertuples():
        for key in _keys(str(row.display_name)):
            out.setdefault(key, str(row.asset_id))
    known = set(held["asset_id"].astype(str))
    for source in _ledger_sources(store):
        for feed_name, asset_id in load_aliases(store, source).items():
            if str(asset_id) in known:
                for key in _keys(str(feed_name)):
                    out.setdefault(key, str(asset_id))
    return out


def _keys(name: str) -> tuple[str, ...]:
    """The forms a name is looked up under, most exact first."""
    text = str(name or "").strip()
    if not text:
        return ()
    return tuple(dict.fromkeys(
        k for k in (text.lower(), normalize_name(text), normalize_team(text)) if k
    ))


def _ledger_sources(store: Store) -> list[str]:
    rows = store.query("SELECT DISTINCT source FROM feed_rows")
    return [str(s) for s in rows["source"]] if not rows.empty else []


def meetings(store: Store, season: str) -> pd.DataFrame:
    """One row per fixture where two managers held the two sides."""
    empty = pd.DataFrame({c: pd.Series(dtype="object") for c in MEETING_COLUMNS})
    held = _holders(store, season)
    if held.empty:
        return empty
    spellings = _spellings(store, held)
    by_id = {str(r.asset_id): r for r in held.itertuples()}

    rows = store.query("SELECT source, payload FROM feed_rows")
    if rows.empty:
        return empty

    found: dict[str, dict] = {}
    for source, payload in zip(rows["source"], rows["payload"]):
        try:
            row = json.loads(payload)
        except (TypeError, ValueError):
            continue
        read = _sides(row)
        if read is None:
            continue
        first, second, key = read
        day = _day(row)
        pair = []
        for side in (first, second):
            asset_id = next((spellings[k] for k in _keys(side.name)
                             if k in spellings), None)
            pair.append(asset_id)
        if not all(pair):
            continue
        one, two = (_owner(by_id, held, asset_id, day) for asset_id in pair)
        if one is None or two is None or one["manager_id"] == two["manager_id"]:
            continue
        made = _meeting(day, row, first, second, one, two)
        # A fixture is not a meeting. Every one of these feeds carries the
        # games nobody has played yet -- that is what the fixture board is
        # built from -- and a scheduled Ohio State v Michigan would sit in a
        # table of results with no result in it.
        if not made["won"]:
            continue
        # Keyed on the fixture, not on the feed that carried it. A European
        # tie reaches the ledger twice -- once from each club's own league
        # pull, with the score the other way round -- and both rows name the
        # same ESPN event, so the two fold onto one meeting.
        found[key] = made
    if not found:
        return empty
    out = pd.DataFrame(list(found.values()), columns=list(MEETING_COLUMNS))
    return out.sort_values("date", ascending=False).reset_index(drop=True)


def _owner(by_id, held: pd.DataFrame, asset_id: str, day: str) -> dict | None:
    """Who held this asset on that day, if anybody did."""
    mine = held[held["asset_id"].astype(str) == str(asset_id)]
    for row in mine.itertuples():
        start = str(row.start_date or "")
        end = str(row.end_date or "")
        if day and start and day < start:
            continue
        if day and end and day > end:
            continue
        return {"manager_id": str(row.manager_id), "category": str(row.category),
                "league": str(row.league), "name": str(row.display_name),
                "asset_id": str(asset_id)}
    return None


def _meeting(day: str, row: dict, first: Side, second: Side,
             one: dict, two: dict) -> dict:
    """One fixture, from the side the ledger happened to write it from."""
    won = ""
    if first.score is not None and second.score is not None:
        won = "a" if first.score > second.score else \
              "b" if second.score > first.score else "draw"
    elif row.get("winner"):
        won = "a"
    return {
        "date": day,
        # Both, because a meeting can cross them: Como play in Serie A and RB
        # Leipzig in the Bundesliga, and their Champions League tie belongs to
        # a reader looking for either. A single league would file it under
        # whichever club the ledger happened to write the row from.
        "a_league": one["league"], "b_league": two["league"],
        "a_category": one["category"], "b_category": two["category"],
        "competition": _competition(row),
        "a_id": one["asset_id"], "a_name": one["name"],
        "a_manager": one["manager_id"],
        "b_id": two["asset_id"], "b_name": two["name"],
        "b_manager": two["manager_id"],
        "a_score": first.score, "b_score": second.score, "won": won,
        # What a tennis match has instead of a score: sets, in the order they
        # were played. A game with numbers on it needs nothing here.
        "detail": "" if first.score is not None
                  else _sets_the_winner_first(str(row.get("score") or ""), won),
    }


def _sets_the_winner_first(score: str, won: str) -> str:
    """A set score turned round so it agrees with who won.

    The feed writes its sets home-first and names its winner separately, so
    the two disagree whenever the away player won: Rybakina beat Sabalenka and
    the string read "4-6 7-5 2-6", which is a loss. Counting the sets is
    enough to tell -- whoever took more of them won the match -- and a printed
    score that contradicts the name beside it is worse than no score at all.
    """
    sets = []
    for token in str(score).split():
        halves = token.split("-")
        if len(halves) != 2:
            return ""
        try:
            sets.append((int(halves[0].split("(")[0]), int(halves[1].split("(")[0])))
        except ValueError:
            return ""
    if not sets:
        return ""
    first = sum(1 for one, two in sets if one > two)
    second = sum(1 for one, two in sets if two > one)
    leads = "a" if first > second else "b" if second > first else ""
    if not leads or not won or leads == won:
        return " ".join(f"{one}-{two}" for one, two in sets)
    return " ".join(f"{two}-{one}" for one, two in sets)


def _competition(row: dict) -> str:
    """What to call the competition, or "" where it was a league fixture.

    Named off the feed's own key through the tables the scorer uses, so a
    Champions League tie reads as one here and is worth five points there
    without the two ever being written down separately. A league match returns
    nothing: the clubs' own leagues are already on the row, and repeating one
    of them in a column headed "competition" says less than a blank does.
    """
    from whul.scoring.competition import CUP_NAMES, classify_key, continental_name

    key = str(row.get("competition_key") or "").strip().lower()
    if not key:
        # Tennis names the tournament outright, and there is no league behind
        # it to fall back on -- the US Open is the competition.
        return str(row.get("tournament") or "")
    if key in CUP_NAMES:
        return CUP_NAMES[key]
    return continental_name(classify_key(key).tier)


def records(found: pd.DataFrame) -> pd.DataFrame:
    """Each pair of managers, and how the meetings between them went.

    One row a pair rather than two: "Jake 7-4 Scott" and "Scott 4-7 Jake" are
    one fact, and a table holding both invites a reader to add them up.
    """
    columns = ("one", "two", "one_won", "two_won", "drawn", "played")
    if found is None or found.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    tally: dict[tuple[str, str], list] = {}
    for row in found.itertuples():
        first, second = sorted((str(row.a_manager), str(row.b_manager)))
        slot = tally.setdefault((first, second), [0, 0, 0])
        if row.won == "draw":
            slot[2] += 1
        else:
            winner = str(row.a_manager) if row.won == "a" else str(row.b_manager)
            slot[0 if winner == first else 1] += 1
    return pd.DataFrame(
        [{"one": a, "two": b, "one_won": w, "two_won": l, "drawn": d,
          "played": w + l + d}
         for (a, b), (w, l, d) in tally.items()],
        columns=list(columns),
    ).sort_values("played", ascending=False).reset_index(drop=True)
