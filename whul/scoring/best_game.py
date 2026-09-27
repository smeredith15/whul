"""The rules for the best-performances slot.

Adopted for 2026-27 after the season opened: each team sport's player category
holds one slot that counts a player's k best games rather than his season. The
rollup (``whul.bestball``) scores the slot through these functions, and so does
the calibration in ``whul.best_game_calibration`` -- so the k it recommends is
the k for exactly these rules and not for a paraphrase of them. The k's
themselves are ``whul.config.league.BEST_K``.

What was settled, and where each rule lives:

**A best game is scored on counting stats alone, at face value.** Face value is
the game's points over the frozen season divisor for the player's group, the
same units a season is in. MLB's Offense, Defense and WAR are run values for a
whole season with no share in any one game, so they are left out; the calibrated
k absorbs the level. The contract engine's year multipliers do not apply: they
discount what was knowable at the draft, and a four-homer game was not.

**k is set per sport, and per role in MLB, so the slot is worth the same on
average everywhere.** Batters, starts and relief appearances are calibrated
separately. See ``best_k``.

**Games of different roles trade at the ratio of their k's.** k is set so each
role's slot is worth the same on average, so a game of a role with k games is
1/k of the slot, and a player who played more than one role takes whichever
whole combination scores most. See ``exchange``.

**A pitcher's starts and relief appearances trade.** At the calibrated four
starts and thirteen appearances, one start is worth about three appearances.
See ``pitcher_best``.

**A two-way player's appearance is decided within the game.** Whichever role
scored more *in that game* counts in full and the other at half -- the season
rule's 1x / 0.5x, decided game by game -- and the leading role is also what the
appearance counts as against the slot. A game in which he played one role is
that role alone. See ``two_way_game``, ``primary_role`` and ``two_way_best``.

**The slots are filled in whichever way scores most.** A player with a strong
season and a stronger handful of games may be worth more in the best-game slot,
with a weaker season-long player taking his place in the season slots. The
assignment that maximises the category's total is the one used. See
``best_configuration``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Mapping, Sequence

#: The season rule's weight on a two-way player's secondary role, reused so the
#: game rule and the season rule cannot disagree about what "half" is.
from whul.scoring.mlb import SECONDARY_ROLE_WEIGHT


def best_k(scores: Iterable[float], k: int) -> float:
    """The sum of a player's k best games.

    Fewer than k games is the sum of what there is. A player hurt in week two
    has his two weeks and no more -- which is the point of the slot, and also
    why it cannot be worth as much as a season.
    """
    if k <= 0:
        return 0.0
    return float(sum(sorted(scores, reverse=True)[:k]))


def two_way_game(batting: float | None = None,
                 pitching: float | None = None) -> float:
    """One game's score for a player who may have batted, pitched, or both.

    Both figures are already on the league scale, each against its own
    divisor: raw batting and pitching points are different currencies, and it
    is the scaled ones that decide which role led the game.

    A role he did not play in the game is ``None``, not zero. A zero would be a
    role he played and did nothing in, and would turn an 0-for-4 on a day he
    never pitched into half an 0-for-4.
    """
    if batting is None and pitching is None:
        return 0.0
    if pitching is None:
        return float(batting)
    if batting is None:
        return float(pitching)
    high, low = max(batting, pitching), min(batting, pitching)
    return float(high + SECONDARY_ROLE_WEIGHT * low)


def exchange(games: Mapping[str, Sequence[float]], k: Mapping[str, int]) -> float:
    """The best whole combination of games from roles sharing one slot.

    A game of role r is worth 1/k[r] of the slot, each k being the one
    calibrated to make that role's slot worth the same on average -- so games
    of different roles trade at the ratio of their k's. Every whole combination
    that fits is tried: some number of games from each role but the last, from
    none up to all the slot has room for, and the last role filling what is
    left. Within a role the best games are always the ones taken. Any one role
    alone is among the combinations, so this never scores below the best of
    them taken separately.

    Worked in exact fractions: a slot that is 3/4 spent has room for exactly
    3.25 relief appearances at k=13, and a float that came to 3.2499999 would
    quietly take two.
    """
    return exchange_pick(games, k)[0]


def exchange_pick(games: Mapping[str, Sequence[float]],
                  k: Mapping[str, int]) -> tuple[float, dict[str, int]]:
    """``exchange``, and how many of each role's best games it took.

    The counts are what a page needs to list the games that count: within a
    role the best games are always the ones taken, so a count names them.
    Ties between combinations go to the first found, which takes fewer games
    of the earlier roles.
    """
    roles = [r for r in k if k[r] > 0]
    if not roles:
        return 0.0, {}
    ranked = {r: sorted(games.get(r, ()), reverse=True) for r in roles}

    def search(i: int, room: Fraction) -> tuple[float, tuple[int, ...]]:
        role = roles[i]
        cap = math.floor(room * k[role])
        if i == len(roles) - 1:
            take = min(cap, len(ranked[role]))
            # A game below zero is never forced in: the slot may stay short.
            while take and ranked[role][take - 1] < 0:
                take -= 1
            return best_k(ranked[role], take), (take,)
        best, pick = -math.inf, ()
        for count in range(0, min(cap, len(ranked[role])) + 1):
            if count and ranked[role][count - 1] < 0:
                break
            rest = room - Fraction(count, k[role])
            value, tail = search(i + 1, rest)
            value += best_k(ranked[role], count)
            if value > best + 1e-12:
                best, pick = value, (count, *tail)
        return best, pick

    value, counts = search(0, Fraction(1))
    return float(value), dict(zip(roles, counts))


def pitcher_best(starts: Sequence[float], reliefs: Sequence[float],
                 n: int, m: int, mix: bool = True) -> float:
    """A pitcher's slot: his starts and relief appearances, traded.

    The agreed rule. n and m are calibrated so a pitcher's best n starts and a
    reliever's best m appearances are worth the same on average, so a start is
    1/n of the slot and an appearance 1/m, and a swingman takes whichever whole
    combination of the two scores most. At n=4 and m=13 that is one start for
    about three appearances.

    ``mix=False`` is the rule first agreed -- the better of the two taken apart
    -- kept so the calibration can say what trading them changed.
    """
    if not mix:
        return max(best_k(starts, n), best_k(reliefs, m))
    return exchange({"start": starts, "relief": reliefs}, {"start": n, "relief": m})


#: The roles an MLB appearance can count as.
BAT, START, RELIEF = "bat", "start", "relief"


def primary_role(batting: float | None, pitching: float | None,
                 pitched: str = START, k: Mapping[str, int] | None = None) -> str:
    """The role an appearance counts as: whichever scored more in it.

    The agreed rule for a two-way player. Both figures are on the league scale,
    each against its own divisor, which is what makes them comparable at all.
    The leading role is the 1x of ``two_way_game`` and the role the appearance
    uses its slot budget as; the other is the 0.5x. So the night he threw six
    innings and homered twice may count as a batting game, if the homers were
    worth more.

    ``pitched`` is the kind of pitching appearance it was, start or relief.
    A tie scores the same whichever role it is called, so it is called the one
    that costs less of the slot -- the one with the larger k -- which can never
    score less.
    """
    if pitching is None:
        return BAT
    if batting is None:
        return pitched
    if batting > pitching:
        return BAT
    if pitching > batting:
        return pitched
    if k and k.get(pitched, 0) > k.get(BAT, 0):
        return pitched
    return BAT


@dataclass(frozen=True)
class Appearance:
    """One game of a player who may have batted, pitched, or both."""

    batting: float | None = None
    pitching: float | None = None
    started: bool = False


def two_way_best(appearances: Sequence[Appearance], k: Mapping[str, int]) -> float:
    """A two-way player's slot, across every role he played.

    Each appearance is scored whole by ``two_way_game`` -- the leading role in
    full, the other at half -- and counts as its leading role by
    ``primary_role``. The slot then takes the best whole combination across
    batting games, starts and relief appearances at their calibrated rates.
    ``k`` needs all three.
    """
    by_role: dict[str, list[float]] = {}
    for game in appearances:
        pitched = START if game.started else RELIEF
        role = primary_role(game.batting, game.pitching, pitched, k)
        by_role.setdefault(role, []).append(two_way_game(game.batting, game.pitching))
    return exchange(by_role, k)


@dataclass(frozen=True)
class Candidate:
    """One rostered player in a category, with both of his possible values."""

    name: str
    season: float
    best: float


@dataclass(frozen=True)
class Configuration:
    """Who fills which slot, and what the category is worth that way."""

    season: tuple[str, ...]
    best: str | None
    total: float


#: How close two lineups' totals must be to count as the same. Scores are
#: shown to one decimal and stored to four.
TIE = 0.01


def best_configuration(players: Sequence[Candidate],
                       season_slots: int) -> Configuration:
    """The assignment of players to slots that scores most.

    ``season_slots`` hold season-long scores and one slot holds a best-game
    score; nobody fills two. Plain best-ball would fill the season slots first
    and hand the best-game slot to whoever is left, and that is not always the
    highest total -- a player whose best games are worth more than the gap
    between his season and the next player's is worth more in the best-game
    slot. So every player is tried there, the season slots take the strongest
    of the rest, and the largest total wins.

    Leaving the best-game slot empty is one of the options, so a best-game
    score below zero is never forced into the total.

    Ties go to the configuration with more in its season slots, which is the
    one plain best-ball would have chosen; then to roster order. A tie is
    anything within ``TIE`` of the best total: a player whose every game
    counts has a best-k equal to his season, and the two are computed by
    different routes that can differ in the fourth decimal -- which is not a
    reason to move him.
    """
    roster = list(players)

    def season_only(pool):
        ranked = sorted(pool, key=lambda p: p.season, reverse=True)[:season_slots]
        return ranked, sum(p.season for p in ranked)

    chosen, total = season_only(roster)
    options = [(total, total, Configuration(
        tuple(p.name for p in chosen), None, total))]
    for i, player in enumerate(roster):
        rest = roster[:i] + roster[i + 1:]
        seated, seasons = season_only(rest)
        value = seasons + player.best
        options.append((value, seasons, Configuration(
            tuple(p.name for p in seated), player.name, value)))
    top = max(o[0] for o in options)
    close = [o for o in options if o[0] >= top - TIE]
    # max() keeps the first of equal keys, and the options are in roster order.
    return max(close, key=lambda o: round(o[1], 9))[2]
