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
    DOMESTIC_CUP = "domestic_cup"
    CHAMPIONS_LEAGUE = "champions_league"
    EUROPA = "europa"
    CONFERENCE = "conference"
    QUALIFYING = "qualifying"


#: Win value by tier. Champions League is the premium; Europa, Conference and
#: domestic cups sit together a rung below; the league is the baseline.
WIN_POINTS: dict[Tier, int] = {
    Tier.CHAMPIONS_LEAGUE: 5,
    Tier.EUROPA: 4,
    Tier.CONFERENCE: 4,
    Tier.DOMESTIC_CUP: 4,
    Tier.LEAGUE: 3,
    Tier.QUALIFYING: 0,
}

#: Tested before anything else: a qualifying tie carries its competition's name,
#: so "Champions League Qualifying" must not read as the Champions League.
QUALIFYING_PATTERN = re.compile(
    r"qualif|prelim|play-?off round|1st round|2nd round|3rd round|"
    r"first qualifying|second qualifying|third qualifying",
    re.IGNORECASE,
)

#: The knockout play-off *within* the competition proper is not qualifying --
#: it sits between the league phase and the round of 16.
KNOCKOUT_PLAYOFF_PATTERN = re.compile(
    r"knockout (phase )?play-?off|knockout round play-?off", re.IGNORECASE
)

TIER_PATTERNS: tuple[tuple[Tier, re.Pattern], ...] = (
    (Tier.CHAMPIONS_LEAGUE, re.compile(r"champions league|uefa champions|ucl", re.IGNORECASE)),
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
