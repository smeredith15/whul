"""Bisection weighting for leagues whose season straddles the draft.

The league drafts in the summer. A league whose season is already running at
that point is *bisected*: a manager drafting an MLB player in July knows what
the first half looked like and is really buying the second half, plus the first
half of the following year. Weighting both halves equally would pay for
information the manager already had.

So each bisected league gets two multipliers:

``mult_n``
    Applied to the remainder of the season in progress. Below 1, because that
    stretch is partly known at draft time -- the further into the season the
    draft falls, the less is left to find out and the more it is discounted.

``mult_n1``
    Applied to the following season's pre-draft stretch, and derived rather
    than chosen::

        mult_n1 = (1 - share_post * mult_n) / share_pre

    That is what makes the two weighted stretches reconcile to one full
    season's worth of scoring. Without it, discounting the known half would
    quietly shrink the league's whole contribution relative to the unbisected
    ones.

MLS is **not** here. It moves to a fall-spring calendar and the league drafts
for the 2027 season, which is not bisected -- it needs short-season proration
instead (see ``whul.scoring.proration``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BisectionRule:
    """How a bisected league's two stretches are weighted.

    ``share_pre`` and ``share_post`` are the fractions of a season falling
    before and after the draft date, and must sum to 1 -- they partition one
    season, so anything else means one of them was measured wrong.
    """

    league: str
    share_pre: float
    share_post: float
    mult_n: float
    #: What the shares were measured from, so the numbers can be re-derived
    #: rather than taken on faith.
    basis: str = ""

    @property
    def mult_n1(self) -> float:
        """The inflation that makes both stretches reconcile to a full season."""
        return (1 - self.share_post * self.mult_n) / self.share_pre

    def validate(self) -> list[str]:
        problems = []
        if abs(self.share_pre + self.share_post - 1.0) > 0.005:
            problems.append(
                f"shares sum to {self.share_pre + self.share_post:.3f}, not 1 -- "
                f"they partition one season, so one was measured wrong"
            )
        if self.share_pre <= 0:
            problems.append("share_pre must be positive; mult_n1 divides by it")
        if not 0 < self.mult_n <= 1:
            problems.append(
                f"mult_n is {self.mult_n}; it discounts the partly-known stretch, "
                f"so it belongs in (0, 1]"
            )
        return problems


def from_days(league: str, pre_days: int, post_days: int, mult_n: float) -> BisectionRule:
    """A rule from the number of season days either side of the draft.

    The shares are the day counts normalized, so the arithmetic is reproducible
    from the schedule rather than from a rounded percentage.
    """
    total = pre_days + post_days
    if total <= 0:
        raise ValueError(f"{league}: {pre_days} + {post_days} days is not a season")
    return BisectionRule(
        league=league,
        share_pre=pre_days / total,
        share_post=post_days / total,
        mult_n=mult_n,
        basis=f"{pre_days} season days before the draft, {post_days} after",
    )


#: MLB bisects at the All-Star break. Roughly 55-58% of games fall before it;
#: 0.42/0.58 and a 0.75 discount are the league's own figures, carried over
#: from the R scripts unchanged.
MLB = BisectionRule(
    league="MLB", share_pre=0.58, share_post=0.42, mult_n=0.75,
    basis="~58% of a 162-game season before the All-Star break",
)

#: WNBA bisects at July 13. The schedule gives 66 season days before and 73
#: after, which is 47.5%/52.5%; the league's stated figures round that to
#: 47/53, and those are used so the multiplier matches the number the rule was
#: set with. From the raw day counts mult_n1 would be 1.2212 rather than
#: 1.2255 -- a 0.35% difference, well inside the uncertainty in mult_n itself.
#: More of the season remains than in MLB, so less is known at draft time and
#: the discount is lighter: 0.80 rather than 0.75.
WNBA = BisectionRule(
    league="WNBA", share_pre=0.47, share_post=0.53, mult_n=0.80,
    basis="66 season days before July 13, 73 after (stated as 47/53)",
)

#: NWSL barely starts before the draft -- 17 season days against 112 after --
#: so almost nothing is known and the discount is nearly none at 0.95. The
#: small pre-draft share is also what makes mult_n1 large: a 5% discount on
#: 87% of the season has to be recovered across the remaining 13%.
NWSL = from_days("NWSL", pre_days=17, post_days=112, mult_n=0.95)

RULES: dict[str, BisectionRule] = {rule.league: rule for rule in (MLB, WNBA, NWSL)}

#: Leagues that are deliberately not bisected, and why. Kept explicit so a
#: missing rule reads as a decision rather than an oversight.
NOT_BISECTED = {
    "MLS": "drafting for 2027, which is not bisected; needs short-season "
           "proration for the shortened transition year instead",
}


def rule_for(league: str) -> BisectionRule | None:
    """The rule for a league, or None when its season does not straddle the draft."""
    return RULES.get(league)


def weights(league: str) -> tuple[float, float]:
    """``(mult_n, mult_n1)`` for a league; ``(1.0, 1.0)`` when unbisected."""
    rule = rule_for(league)
    return (rule.mult_n, rule.mult_n1) if rule else (1.0, 1.0)


def describe() -> list[str]:
    """One line per rule, for a report a person has to check."""
    lines = []
    for rule in RULES.values():
        lines.append(
            f"{rule.league}: pre {rule.share_pre:.3f} / post {rule.share_post:.3f}, "
            f"mult_n {rule.mult_n:.2f} -> mult_n1 {rule.mult_n1:.4f}  ({rule.basis})"
        )
    for league, reason in NOT_BISECTED.items():
        lines.append(f"{league}: not bisected -- {reason}")
    return lines
