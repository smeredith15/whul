"""Tennis results typed by hand, turned into the rows the ledger keeps.

The Flashscore feed reaches back seven days and the app's own database was not
to hand, so the fortnight the ledger missed is being supplied as text. That is a
perfectly good source -- it is the same matches -- but it arrives from the
*player's* point of view, one block per player, and a match therefore appears
twice: once as the winner's win and once as the loser's loss. The ledger wants
each match once, with a winner and a loser.

Nothing here guesses. A field that is missing is reported by name and left out,
because a row imported with a blank opponent would key differently from the
same row arriving later with the opponent filled in -- and the two would be
paid twice, which is the one thing this import must not do.
"""

from __future__ import annotations

import re

#: A score, so an opponent and a score run together can be told apart. Tennis
#: scores are pairs of small numbers, sometimes with a tiebreak in brackets.
SCORE = re.compile(r"\b\d+\s*-\s*\d+")

#: What the category column says, as the tournament tiers this project scores.
#: "25p" is a typo for 250 and is recovered rather than dropped -- but only
#: because the same tournament is spelled correctly on another line, which is
#: evidence rather than a guess.
CATEGORIES = {
    "grand slam": "Grand Slam", "1000": "Masters 1000", "masters": "Masters 1000",
    "500": "500", "250": "250", "finals": "Tour Finals",
    "international": "International",
}

ROUNDS = {"RR", "R128", "R64", "R32", "R16", "QF", "SF", "F"}


def _when(dates: dict, tournament: str, round_name: str, tour: str):
    """A round's date: the most specific of the three keys that is given.

    A round is not always one day for both tours -- the US Open's men's and
    women's semi-finals are played on different ones -- so a tour-specific key
    wins, then the round, then the tournament as a whole.
    """
    name = tournament.casefold()
    for key in (f"{name}:{round_name.casefold()}:{tour.casefold()}",
                f"{name}:{round_name.casefold()}", name):
        if key in dates:
            return dates[key]
    return None


def parse(text: str, dates: dict | None = None) -> tuple[list[dict], list[str]]:
    """``(matches, problems)``.

    A player's name is a line with no commas in it. Everything after it, until
    the next such line, is that player's matches.
    """
    dates = dates or {}
    player = ""
    seen: dict[tuple, dict] = {}
    problems: list[str] = []
    categories: dict[str, str] = {}

    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if "," not in line:
            player = " ".join(w.capitalize() if w.isupper() else w
                              for w in line.split())
            continue
        if not player:
            problems.append(f"line {number}: a match before any player was named")
            continue

        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            problems.append(f"line {number} ({player}): only {len(parts)} field(s)")
            continue
        tournament, category, round_name, result = parts[:4]
        rest = parts[4:]

        if round_name.upper() not in ROUNDS:
            problems.append(f"line {number} ({player}): round {round_name!r} "
                            f"is not one this project knows")
            continue
        if result.upper() not in ("W", "L"):
            problems.append(f"line {number} ({player}): result {result!r} "
                            f"is neither W nor L")
            continue

        opponent, score = _split_opponent(rest)
        if not opponent:
            problems.append(
                f"line {number} ({player}): no opponent named for the "
                f"{round_name} at {tournament}. Left out -- importing it blank "
                f"would key differently from the same match arriving named, "
                f"and the two would be paid twice")
            continue

        tier = _tier(category, tournament, categories, problems, number, player)
        if tier is None:
            continue
        won = result.upper() == "W"
        winner, loser = (player, opponent) if won else (opponent, player)
        key = (tournament.casefold(), round_name.upper(),
               winner.casefold(), loser.casefold())
        if key in seen:
            continue  # the other player's copy of the same match
        row = {
            "tournament": tournament, "category": tier,
            "round": round_name.upper(), "winner": winner, "loser": loser,
            "score": score, "tour": _tour(tier, player),
        }
        when = _when(dates, tournament, row["round"], row["tour"])
        if when:
            row["date"] = when
            row["season"] = int(str(when)[:4])
        seen[key] = row

    return list(seen.values()), problems


def _split_opponent(rest: list[str]) -> tuple[str, str]:
    """The opponent and the score, however they were separated.

    One line ran them together -- "Mattia Bellucci 6-0 6-1 6-1" -- so a comma
    cannot be relied on, and the score is found by where the numbers start.
    """
    if len(rest) >= 2:
        return rest[0], ", ".join(rest[1:])
    if not rest:
        return "", ""
    found = SCORE.search(rest[0])
    if not found:
        return rest[0].strip(), ""
    name = rest[0][:found.start()].strip()
    return name, rest[0][found.start():].strip()


def _tier(category, tournament, categories, problems, number, player):
    """The tournament's tier, recovered from the same tournament where needed."""
    key = category.strip().casefold()
    found = CATEGORIES.get(key)
    if found:
        categories.setdefault(tournament.casefold(), found)
        return found
    held = categories.get(tournament.casefold())
    if held:
        problems.append(
            f"line {number} ({player}): category {category!r} is not one this "
            f"project knows; read as {held!r}, which is what the same "
            f"tournament says on another line")
        return held
    problems.append(f"line {number} ({player}): category {category!r} is not "
                    f"one this project knows, and no other line names this "
                    f"tournament's tier")
    return None


#: Which tour a match belongs to, from the players in it. Only used to label
#: the row; the points table is the same for both.
WOMEN = {
    "Jessica Pegula", "Aryna Sabalenka", "Coco Gauff", "Mirra Andreeva",
    "Elena Rybakina", "Iga Swiatek", "Linda Noskova",
}


def _tour(tier: str, player: str) -> str:
    return "WTA" if player in WOMEN else "ATP"


#: The draw, in order. A player's run through it should be unbroken, and should
#: end in a defeat or in the trophy.
DRAW = ["R128", "R64", "R32", "R16", "QF", "SF", "F"]


def gaps(rows: list[dict], players: set, as_of=None) -> list[str]:
    """Where a player's run through a draw does not hold together.

    Two faults, and the second is the one a reader would not think to look for.
    A missing middle round is obvious once stated -- reaching the last sixteen
    without a third round means a match nobody typed. A run that *ends* in a win
    short of the final is the quieter one: winning a semi-final means playing a
    final, so a player whose last given match is a win has a match missing after
    it, and nothing about the rows themselves says so.

    Only the players whose results were supplied are checked. Their opponents
    appear once each and are not expected to have runs here.
    """
    import collections

    per: dict = collections.defaultdict(dict)
    for row in rows:
        for who, won in ((row["winner"], True), (row["loser"], False)):
            if who in players:
                per[(who, row["tournament"])][row["round"]] = won

    found: list[str] = []
    for (who, tournament), rounds in sorted(per.items()):
        at = sorted(DRAW.index(r) for r in rounds)
        missing = [DRAW[i] for i in range(at[0], at[-1] + 1)
                   if DRAW[i] not in rounds]
        if missing:
            found.append(f"{who} at {tournament}: reached "
                         f"{DRAW[at[-1]]} but no {', '.join(missing)}")
        last = DRAW[at[-1]]
        if not rounds[last] or last == "F":
            continue
        after = DRAW[DRAW.index(last) + 1]
        # A win short of the final means the next round exists -- but not
        # necessarily that it has been played. The semi-finals were the last
        # matches at Flushing Meadows when these were typed, and calling three
        # unplayed finals "missing" would be crying wolf at the one check whose
        # value is that it is quiet until something is actually wrong.
        played = _played_on(rows, tournament, after)
        if as_of is not None and (played is None or played >= str(as_of)):
            continue
        found.append(f"{who} at {tournament}: won the {last}, so the "
                     f"{after} was played and is not here")
    return found


def _played_on(rows: list[dict], tournament: str, round_name: str):
    """When a round was played, from the rows themselves, or None."""
    dates = [str(r.get("date")) for r in rows
             if r["tournament"] == tournament and r["round"] == round_name
             and r.get("date")]
    return min(dates) if dates else None
