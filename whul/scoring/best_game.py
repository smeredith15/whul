"""The rules for a best-game slot, on record before anything scores by them.

A proposal, not a rule of the league: nothing in the standings reads this
module. It exists so that the rules agreed for the slot are written down in the
one form that cannot drift from what gets measured -- the calibration in
``whul.best_game_calibration`` scores games through these functions, so the k it
recommends is the k for exactly these rules and not for a paraphrase of them.

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

**A two-way player's game is split within the game.** Whichever role scored
more *in that game* counts in full and the other at half -- the season rule's
1x / 0.5x, decided game by game rather than by the season's role. A game in
which he played one role is that role alone. See ``two_way_game``.

**A two-way player's slot trades pitching games for batting games.** A game he
pitched in is a pitching game, worth 1/n of the slot; one he only batted in is
a batting game, worth 1/m; the slot takes the best whole combination. See
``two_way_best``.

**Starts and relief appearances are never mixed** by default: a pitcher's slot
is his best n starts or his best m relief appearances, whichever is more. The
exchange-rate reconciliation is here too, switched off, for the league to
decide on. See ``pitcher_best``.

**The slots are filled in whichever way scores most.** A player with a strong
season and a stronger handful of games may be worth more in the best-game slot,
with a weaker season-long player taking his place in the season slots. The
assignment that maximises the category's total is the one used. See
``best_configuration``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

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


def pitcher_best(starts: Sequence[float], reliefs: Sequence[float],
                 n: int, m: int, mix: bool = False) -> float:
    """A pitcher's slot: his best n starts or his best m relief appearances.

    ``mix=False`` is the agreed rule. The two are never combined, so a pitcher
    who did both takes whichever of the two is larger.

    ``mix=True`` is the proposed reconciliation, on record for the league to
    decide. n and m are calibrated to be worth the same on average, so a start
    is worth 1/n of the slot and a relief appearance 1/m -- an exchange rate of
    m/n appearances per start. A pitcher may use any whole combination that
    fits: s starts and the floor of (1 - s/n) * m appearances, for whichever s
    scores most. Using all starts or all appearances is one of the choices, so
    the mix can never score below the agreed rule; it only credits a swingman
    whose best outings are split across both.
    """
    if not mix:
        return max(best_k(starts, n), best_k(reliefs, m))
    return exchange_best(starts, reliefs, n, m)


def exchange_best(first: Sequence[float], second: Sequence[float],
                  n: int, m: int) -> float:
    """The best whole combination of games from two roles sharing one slot.

    A game of the first role is worth 1/n of the slot and a game of the second
    1/m, n and m being the k's calibrated to make each role's slot worth the
    same on average -- so m/n games of the second trade for one of the first.
    Every whole combination that fits is tried: s games of the first and the
    floor of (1 - s/n) * m of the second, for each s from none to n. All of
    one role is among them, so this never scores below the better of the two
    taken alone.
    """
    best = -math.inf
    for s in range(0, n + 1):
        # The small epsilon keeps 2.9999999 from flooring to 2.
        r = math.floor((1 - s / n) * m + 1e-9) if n else m
        best = max(best, best_k(first, s) + best_k(second, r))
    return float(best)


def two_way_best(pitching_games: Sequence[float], batting_games: Sequence[float],
                 n: int, m: int) -> float:
    """A two-way player's slot: his pitching games and batting games, traded.

    The agreed rule. A game in which he pitched is a pitching game, worth 1/n
    of the slot, where n is the pitching role's k; its score is still the whole
    game's, both lines split 1x / 0.5x by ``two_way_game``, because the batting
    points count whether or not they led. A game in which he only batted is a
    batting game, worth 1/m, m being the batter's k. The slot takes whichever
    whole combination of the two scores most -- the exchange rate the swingman
    proposal uses, and here the rule rather than an option, since nobody would
    call a two-way player's season one role or the other.
    """
    return exchange_best(pitching_games, batting_games, n, m)


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
    one plain best-ball would have chosen; then to roster order.
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
    # max() keeps the first of equal keys, and the options are in roster order.
    return max(options, key=lambda o: (round(o[0], 9), round(o[1], 9)))[2]
