"""Soccer competition classification.

A club's win is worth different points depending on where it happened, so every
match has to be placed before it can be scored. Three rules govern this:

* **Qualifying rounds do not count.** Only competition proper. For the current
  Champions League format that means the league phase onward.
* **Domestic cups count at their ordinary win value**, since every club enters.
* **A bye scores as though the team swept the round it skipped.** Finishing top
  eight in the league phase skips the knockout play-off, and that is credited
  rather than left as an absence.

The classifier works on competition names because that is what every feed
provides; names vary by source, so matching is generous and the residual falls
through to the domestic league, which is the overwhelmingly common case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Tier(str, Enum):
    """What a competition is worth, and whether it counts at all."""

    LEAGUE = "league"
    DOMESTIC_POSTSEASON = "domestic_postseason"
    DOMESTIC_CUP = "domestic_cup"
    CHAMPIONS_LEAGUE = "champions_league"
    EUROPA = "europa"
    CONFERENCE = "conference"
    QUALIFYING = "qualifying"


#: Win value by tier. Champions League is the premium; Europa, Conference and
#: domestic cups sit together a rung below; the league is the baseline.
WIN_POINTS: dict[Tier, int] = {
    Tier.CHAMPIONS_LEAGUE: 5,
    # The R script groups Play-off competitions with the Champions League at 5.
    # For MLS and NWSL that is the postseason, where a win is worth more than a
    # regular-season one.
    Tier.DOMESTIC_POSTSEASON: 5,
    Tier.EUROPA: 4,
    Tier.CONFERENCE: 4,
    Tier.DOMESTIC_CUP: 4,
    Tier.LEAGUE: 3,
    Tier.QUALIFYING: 0,
}

class Outcome(str, Enum):
    """How a match ended, for the side being scored."""

    WIN = "win"
    SHOOTOUT_WIN = "shootout_win"
    DRAW = "draw"
    SHOOTOUT_LOSS = "shootout_loss"
    LOSS = "loss"


#: The scale the shares below are quoted on -- a league win, the baseline
#: competition. Every other tier is this scaled up.
LEAGUE_WIN = 3

#: What each ending is worth *in league points*, before the competition
#: premium. A win is the full three; a shootout win two, since the ninety
#: minutes were drawn and only the shootout separated them; a draw one; a
#: shootout loss the same one, because the side that loses on penalties drew
#: the match it played. A loss is nothing.
OUTCOME_SHARE: dict[Outcome, float] = {
    Outcome.WIN: 3.0,
    Outcome.SHOOTOUT_WIN: 2.0,
    Outcome.DRAW: 1.0,
    Outcome.SHOOTOUT_LOSS: 1.0,
    Outcome.LOSS: 0.0,
}


def outcome_points(outcome: Outcome | str, win_points: int | float) -> float:
    """What an ending is worth in the competition it happened in.

    The tier premium is a multiplier on the league scale rather than a separate
    table, so ``x3/3``, ``x4/3``, ``x5/3`` -- a Champions League win is five,
    exactly as before, and a Champions League draw is five thirds.
    """
    return OUTCOME_SHARE[Outcome(outcome)] * win_points / LEAGUE_WIN


#: Tested before anything else: a qualifying tie carries its competition's name,
#: so "Champions League Qualifying" must not read as the Champions League.
#:
#: Deliberately *not* matching bare ordinal rounds. An earlier version included
#: "1st round|2nd round|3rd round" to catch UEFA qualifiers, and would have
#: dropped legitimate FA Cup ties -- that competition's proper rounds are named
#: exactly that. UEFA always says "qualifying" or "play-off round", so those two
#: forms are sufficient and far safer.
QUALIFYING_PATTERN = re.compile(r"qualif|prelim|play-?off round", re.IGNORECASE)

#: A domestic postseason -- "MLS Cup Playoffs" -- as distinct from UEFA's
#: qualifying "play-off round", which the pattern above has already removed.
POSTSEASON_PATTERN = re.compile(r"play-?offs?\b|postseason", re.IGNORECASE)

#: The knockout play-off *within* the competition proper is not qualifying --
#: it sits between the league phase and the round of 16.
KNOCKOUT_PLAYOFF_PATTERN = re.compile(
    r"knockout (phase )?play-?off|knockout round play-?off", re.IGNORECASE
)

#: Order matters and mirrors the R script's `case_when`, which tests
#: "Champions League|Play-off|Playoff" before the cup line. "MLS Cup Playoffs"
#: contains "Cup", so testing cups first would score a postseason tie as a cup
#: tie -- 4 points instead of 5.
TIER_PATTERNS: tuple[tuple[Tier, re.Pattern], ...] = (
    (Tier.CHAMPIONS_LEAGUE, re.compile(r"champions league|uefa champions|ucl", re.IGNORECASE)),
    (Tier.DOMESTIC_POSTSEASON, POSTSEASON_PATTERN),
    (Tier.CONFERENCE, re.compile(r"conference league|uecl", re.IGNORECASE)),
    (Tier.EUROPA, re.compile(r"europa league|uefa europa|uel", re.IGNORECASE)),
    (
        Tier.DOMESTIC_CUP,
        re.compile(
            r"\bfa cup\b|\befl cup\b|carabao|league cup|copa del rey|dfb[- ]?pokal|"
            r"coppa italia|coupe de france|supercopa|super cup|community shield|"
            r"trophée|trophy|us open cup|\bcup\b",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class Classification:
    tier: Tier
    win_points: int
    counts: bool
    is_knockout_playoff: bool = False


#: Tier by the feed's own competition key. Far more reliable than reading a
#: display name: we choose the key when making the request, so it cannot be
#: absent or worded unexpectedly. Names are only needed to spot qualifying
#: rounds within a competition, and for sources that supply nothing else.
KEY_TIERS: dict[str, Tier] = {
    "ucl": Tier.CHAMPIONS_LEAGUE,
    "uel": Tier.EUROPA,
    "uecl": Tier.CONFERENCE,
    "facup": Tier.DOMESTIC_CUP,
    "efl_cup": Tier.DOMESTIC_CUP,
    "copadelrey": Tier.DOMESTIC_CUP,
    "dfbpokal": Tier.DOMESTIC_CUP,
    "coppaitalia": Tier.DOMESTIC_CUP,
    "coupedefrance": Tier.DOMESTIC_CUP,
    "epl": Tier.LEAGUE,
    "laliga": Tier.LEAGUE,
    "seriea": Tier.LEAGUE,
    "bundesliga": Tier.LEAGUE,
    "ligue1": Tier.LEAGUE,
    "mls": Tier.LEAGUE,
    "nwsl": Tier.LEAGUE,
}


def classify_key(key: str | None, label: str | None = None) -> Classification:
    """Classify by the feed's competition key, refining with the round name.

    Preferred over ``classify`` wherever the key is known. A display name can be
    missing or worded unexpectedly -- ESPN returns the league name at the top of
    the response rather than on each event, so reading it per-event yields the
    bare key -- and a Champions League tie mis-read as a league fixture would
    silently score 3 points instead of 5.
    """
    tier = KEY_TIERS.get((key or "").strip().lower())
    if tier is None:
        return classify(label or key)

    # The round name still decides whether this is the competition proper.
    text = label or ""
    knockout_playoff = bool(KNOCKOUT_PLAYOFF_PATTERN.search(text))
    if QUALIFYING_PATTERN.search(text) and not knockout_playoff:
        return Classification(Tier.QUALIFYING, 0, False)

    # A domestic postseason outranks its own regular season.
    if tier is Tier.LEAGUE and POSTSEASON_PATTERN.search(text):
        return Classification(
            Tier.DOMESTIC_POSTSEASON, WIN_POINTS[Tier.DOMESTIC_POSTSEASON], True
        )
    return Classification(tier, WIN_POINTS[tier], True, knockout_playoff)


def classify(competition: str | None) -> Classification:
    """Place a competition name into a scoring tier.

    Qualifying is tested first: those ties carry the parent competition's name,
    so checking tiers first would score a qualifier as the competition proper.
    The knockout play-off is exempted because it is inside the competition
    despite reading like a qualifying round.
    """
    name = (competition or "").strip()
    if not name:
        return Classification(Tier.LEAGUE, WIN_POINTS[Tier.LEAGUE], True)

    knockout_playoff = bool(KNOCKOUT_PLAYOFF_PATTERN.search(name))
    if QUALIFYING_PATTERN.search(name) and not knockout_playoff:
        return Classification(Tier.QUALIFYING, 0, False)

    for tier, pattern in TIER_PATTERNS:
        if pattern.search(name):
            return Classification(tier, WIN_POINTS[tier], True, knockout_playoff)

    return Classification(Tier.LEAGUE, WIN_POINTS[Tier.LEAGUE], True)


#: A swept two-legged tie is two wins; European knockout rounds are two legs.
LEGS_PER_KNOCKOUT_TIE = 2


def bye_credit(tier: Tier, legs: int = LEGS_PER_KNOCKOUT_TIE) -> int:
    """Points for a round a team skipped by finishing high enough to earn a bye.

    Credited as a sweep: the wins the team never had to play for. Without this a
    bye is indistinguishable from an early exit, which would punish the very
    performance that earned it.

    Tennis is the documented exception to this rule and does not use it -- there
    a bye earns first-round credit only on winning the second-round match.
    """
    return WIN_POINTS.get(tier, 0) * legs


# --- European entry -------------------------------------------------------
#: What reaching a European competition is worth, before a ball is kicked in it.
#:
#: A domestic season earns a place in Europe, and that place is the biggest
#: outcome of the season short of the title -- but nothing in a club's own
#: results says it got one. It depends on where everyone else finished, on both
#: cup winners, on the cascade when a cup winner has already qualified, and on
#: an allocation that moves with the UEFA coefficients. So it is read from the
#: published participant list rather than derived. See ``whul.sources.wikipedia``.
#:
#: Sized against the club benchmarks, which run 158-227 for a season: reaching
#: the Champions League is about 6% of a benchmark year, in line with what
#: terminal achievements are worth elsewhere in the league -- an NCAAF playoff
#: appearance is 4.6% of its benchmark, an MLB division title 1.6%.
#:
#: The 12/8/4 ladder is deliberately wider than the 5/4/4/3 win ladder. The gap
#: between reaching the Champions League and reaching the Conference League is
#: far larger than the gap between a win in each.
UEFA_LEAGUE_PHASE_POINTS: dict[str, float] = {
    "Champions League": 12.0,
    "Europa League": 8.0,
    "Conference League": 4.0,
}

#: Entering before the league phase, which is a place in a draw rather than in
#: the competition. Discounted for the two senior competitions, where the tie is
#: a real contest and losing it drops the club a tier -- a Ligue 1 fourth-placed
#: side entering the Champions League third qualifying round may end up in the
#: Europa League instead.
#:
#: Not discounted for the Conference League, and that is not an oversight: the
#: play-off round *is* how a club from a top-five league enters it, against
#: opposition it is overwhelmingly expected to beat. Halving it would price a
#: near-certainty as a coin toss.
UEFA_QUALIFYING_DISCOUNT: dict[str, float] = {
    "Champions League": 0.5,
    "Europa League": 0.5,
    "Conference League": 1.0,
}

#: The entry round that means the competition proper.
UEFA_LEAGUE_PHASE = "League phase"


def uefa_entry_points(competition: str, entry_round: str) -> float:
    """Points for entering a European competition at a given round."""
    full = UEFA_LEAGUE_PHASE_POINTS.get(str(competition))
    if full is None:
        return 0.0
    if str(entry_round).strip() == UEFA_LEAGUE_PHASE:
        return full
    return full * UEFA_QUALIFYING_DISCOUNT.get(str(competition), 0.5)
