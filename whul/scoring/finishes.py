"""What an athlete actually did, for the profile window.

A points total says how much; it does not say what happened. "Daytona 500 4th ·
US Open R16 · Masters 2nd" is the natural thing to want from a profile, and the
rows behind it already exist -- ``tennis.match_events``, ``golf.score_events``
and ``motorsport.race_events`` each return one dated, scored row per event.
They were written for the window benchmarks, which sum them and throw the
detail away.

**Tennis is summarized per tournament, not per match.** Its event rows are one
per match, so a run to a final is seven of them; a reader wants one line saying
how far the player got and what the week was worth. Losing the final reads

    ATP Winston Salem 250 · F · 150

-- the furthest round reached, and every point earned there, straight-sets
bonuses included. Golf and motorsport already have one row per event, so their
finish *is* the row.

A loss is kept. A player who went out in the first round has played, and a
profile that omits him reads exactly like one for a player who is injured and
did not enter.
"""

from __future__ import annotations

import pandas as pd

#: Rounds from earliest to latest, so "furthest reached" is a max rather than a
#: string comparison. Qualifying sits below the main draw, and W is the title.
ROUND_ORDER = (
    "Q1", "Q2", "Q3", "RR", "R128", "R64", "R32", "R16", "QF", "SF", "F", "W",
)
_ROUND_RANK = {name: index for index, name in enumerate(ROUND_ORDER)}

#: How many finishes a profile carries. A season of tennis is fifty-odd
#: tournaments and the window is scrolled, not read end to end.
MAX_FINISHES = 40


def _round_rank(value) -> int:
    return _ROUND_RANK.get(str(value or "").strip().upper(), -1)


def _tier(category) -> str:
    """The tournament's tier, minus the tour that is already in the league."""
    text = str(category or "").strip()
    for prefix in ("ATP ", "WTA "):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def tennis_finishes(events: pd.DataFrame) -> pd.DataFrame:
    """One row per player per tournament: how far they got, and what it paid.

    The furthest round is taken across every match at that tournament, won or
    lost, because losing in the final is still reaching the final -- and the
    losing row is the only one that records having got there.
    """
    if events is None or events.empty:
        return pd.DataFrame()

    work = events.copy()
    for column in ("tournament", "category", "round", "league"):
        if column not in work.columns:
            work[column] = ""
    work["_rank"] = work["round"].map(_round_rank)
    # Not "_won": `itertuples` renames a leading underscore to a position, and
    # the label reader below takes its fields by name.
    work["wins"] = (work["result"].astype(str).str.upper() == "W").astype(int) \
        if "result" in work.columns else 0

    # Winning the final and losing it are both matches in the round "F", so
    # the furthest round alone cannot tell a champion from a runner-up: Gauff
    # winning Cincinnati and Pegula losing it both read "Cincinnati 1000 F".
    # A won final is the title, and the round order already has a rung above F
    # for it.
    if "result" in work.columns:
        won_the_final = (
            (work["round"].astype(str).str.upper() == "F")
            & (work["result"].astype(str).str.upper() == "W")
        )
        work.loc[won_the_final, "_rank"] = _ROUND_RANK["W"]

    grouped = work.groupby(
        ["player", "tournament", "category", "league"], as_index=False
    ).agg(
        points=("event_points", "sum"),
        date=("date", "max"),
        _rank=("_rank", "max"),
        matches=("event_points", "size"),
        wins=("wins", "sum"),
    )
    grouped["round"] = grouped["_rank"].map(
        lambda r: ROUND_ORDER[r] if 0 <= r < len(ROUND_ORDER) else ""
    )
    grouped["label"] = [_label_for(row) for row in grouped.itertuples()]
    return grouped.drop(columns=["_rank", "wins"])


#: Tiers with no draw at all. A Davis Cup rubber is a tie between two nations,
#: not a position in a bracket: there is no round to report and "International"
#: on a line says less than the tournament's own name already does. So their
#: finishes read as a record -- "Davis Cup - World Group 0-1" -- which is the
#: only form in which a tie a player lost says anything at all.
#:
#: Narrower than ``RECORD_TIERS`` on purpose. The Tour Finals is a record too,
#: but it has a knockout past its group, and a bare record there would lose the
#: final.
NO_DRAW = {"International"}


def _label_for(row) -> str:
    """One tournament's line: how far he got, or how the ties went."""
    tier = _tier(row.category)
    if tier in NO_DRAW:
        played = int(row.matches)
        won = int(row.wins)
        parts = (str(row.league or ""), str(row.tournament or ""),
                 f"{won}-{played - won}")
    else:
        parts = (str(row.league or ""), str(row.tournament or ""), tier,
                 str(row.round or ""))
    return " ".join(part for part in parts if part).strip()


#: How a finishing position reads: 1st, 2nd, 3rd, 4th.
def ordinal(position) -> str:
    try:
        number = int(float(position))
    except (TypeError, ValueError):
        return ""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def event_finishes(events: pd.DataFrame, position_col: str) -> pd.DataFrame:
    """Golf and motorsport: the row already is the finish."""
    if events is None or events.empty:
        return pd.DataFrame()

    work = events.copy()
    for column in ("tournament", "league"):
        if column not in work.columns:
            work[column] = ""
    where = work[position_col] if position_col in work.columns else pd.Series("", index=work.index)
    work["round"] = [ordinal(v) for v in where]
    work["points"] = pd.to_numeric(work["event_points"], errors="coerce").fillna(0.0)
    work["label"] = [
        " ".join(part for part in (
            str(row.tournament or "").strip() or str(row.league or ""),
            str(row.round or ""),
        ) if part).strip()
        for row in work.itertuples()
    ]
    return work[["player", "label", "points", "date"]]


def as_records(finishes: pd.DataFrame, id_col: str = "player") -> dict[str, list[dict]]:
    """``{athlete: [{label, points, date}, ...]}``, newest first.

    Newest first because a profile is opened to see what just happened; the
    Masters in April is context, last weekend is the question.
    """
    if finishes is None or finishes.empty:
        return {}

    out: dict[str, list[dict]] = {}
    ordered = finishes.sort_values("date", ascending=False, kind="mergesort")
    for athlete, block in ordered.groupby(id_col, sort=False):
        out[str(athlete)] = [
            {
                "label": str(row.label),
                "points": round(float(row.points), 1),
                "date": str(row.date)[:10],
            }
            for row in block.head(MAX_FINISHES).itertuples()
        ]
    return out


def summarize(events: pd.DataFrame) -> dict[str, list[dict]]:
    """Finishes for whichever individual sport these rows came from.

    Dispatched on the columns rather than on a league name: the three scorers
    have different vocabularies -- a round, a position, a finish -- and the
    column that is present is what says which sport wrote the frame. A league
    name would have to be kept in step by hand.
    """
    if events is None or events.empty:
        return {}
    if "round" in events.columns:
        return as_records(tennis_finishes(events))
    # `finish` first. Only a scorer that means "where this row placed in this
    # event" ever writes it, while `position` is overloaded -- a footballer's
    # playing position, a driver's championship standing, and golf's finish all
    # answer to it, and `CARRIED_IDENTITY` will put the feed's own onto a frame
    # that has one already. Preferring it labelled every one of Ryan Blaney's
    # races with his season standing: a win and a thirty-third both read "3rd",
    # beside points of 55 and 4 that said otherwise on the same line.
    for column in ("finish", "position"):
        if column in events.columns:
            return as_records(event_finishes(events, column))
    return {}


# --- tennis, by the size of the tournament ---------------------------------
#
# A tennis profile is unreadable as one list. Fifty tournaments a year, seven
# matches in a good week, and the one thing a reader wants -- how the player
# did against fields of each size -- is exactly what a flat list of matches
# buries. A first-round loss at a 250 and a first-round loss at a slam are the
# same line and not remotely the same result.

#: How each tier is named on a profile, and the order a reader looks for them
#: in: up the tour's own ladder, then the two that are not on it. ``{tour}`` is
#: the player's own, so a WTA profile says "WTA 1000" where an ATP one says
#: "ATP 1000" -- which is what both tours call them.
TIER_LABELS: tuple[tuple[str, str], ...] = (
    ("250", "{tour} 250"),
    ("500", "{tour} 500"),
    ("Masters 1000", "{tour} 1000"),
    ("Grand Slam", "Grand Slam"),
    ("Tour Finals", "{tour} Finals"),
    ("International", "Team Events"),
)

#: Tiers whose result is a record rather than a round. A Davis Cup tie is not
#: a knockout round in the sense the draw assumes, and the round-robin group at
#: the Tour Finals is three matches nobody is eliminated by -- "RR" on its own
#: says a player turned up.
RECORD_TIERS = {"Tour Finals", "International"}


def _tier_of(category) -> str:
    """The tier a tournament belongs to, minus the tour that names it."""
    return _tier(category)


def _round_counts(block: pd.DataFrame) -> str:
    """The furthest round reached at each tournament of this tier, grouped.

    "W x1 · SF x1" rather than a line per tournament: the count is the point,
    and at a tier a player enters nine times the list is the noise.
    """
    order = {name: index for index, name in enumerate(ROUND_ORDER)}
    tally: dict[str, int] = {}
    for name in block["round"]:
        text = str(name or "").strip()
        if text:
            tally[text] = tally.get(text, 0) + 1
    ranked = sorted(tally.items(), key=lambda pair: -order.get(pair[0], -1))
    return " · ".join(
        name if count == 1 else f"{name} ×{count}" for name, count in ranked
    )


def _record(events: pd.DataFrame, group: str = "") -> str:
    """Matches won and lost, for a tier whose result is a record.

    The round-robin at the Tour Finals is named, because a player who came
    through it has a knockout round to show as well and the two are different
    kinds of answer: "RR 2-1 · SF" is a group won and a semi-final lost.
    """
    results = events["result"].astype(str).str.upper()
    won = int((results == "W").sum())
    lost = int((results == "L").sum())
    record = f"{won}-{lost}"
    return f"{group} {record}".strip()


def _note_for(tier: str, block: pd.DataFrame, reached: pd.DataFrame) -> str:
    """What happened at this tier, in the words the tier is played in."""
    if tier == "International":
        return _record(block)
    if tier == "Tour Finals":
        group = block[block["round"].astype(str).str.upper() == "RR"]
        said = _record(group, "RR") if not group.empty else ""
        # Past the group, the round he went out in -- which is the knockout
        # answer, and the group's record does not contain it.
        beyond = _round_counts(
            reached[reached["round"].astype(str).str.upper() != "RR"])
        return " \u00b7 ".join(part for part in (said, beyond) if part)
    return _round_counts(reached)


def tier_summary(events: pd.DataFrame) -> dict[str, list[dict]]:
    """Per player, one entry a tier: what it paid and what happened in it.

    ``events`` are match rows -- ``player``, ``category``, ``round``,
    ``result``, ``event_points`` and ``straight_points`` -- already cut to the
    window being shown. Every tier is returned, including the ones a player
    never entered, because a profile that omits them cannot say whether he
    skipped the 500s or the feed did.

    ``points`` is the ranking total and ``straight`` is what winning quickly
    added; the two sum to what the tier scored. Split because only the first
    can be checked against anything: the tour publishes it, and a figure with
    our bonus already inside it matches no list anywhere.
    """
    if events is None or events.empty or "player" not in events.columns:
        return {}
    work = events.copy()
    for column in ("category", "round", "result", "league"):
        if column not in work.columns:
            work[column] = ""
    if "straight_points" not in work.columns:
        work["straight_points"] = 0.0
    work["_tier"] = work["category"].map(_tier_of)

    reached = tennis_finishes(work)
    out: dict[str, list[dict]] = {}
    for player, played in work.groupby("player", sort=False):
        tour = str(played["league"].iloc[0] or "").strip().upper() or "ATP"
        mine = reached[reached["player"] == player] if not reached.empty \
            else pd.DataFrame(columns=["category", "round"])
        entries = []
        for tier, template in TIER_LABELS:
            block = played[played["_tier"] == tier]
            here = (mine[mine["category"].map(_tier_of) == tier]
                    if not mine.empty else mine)
            entries.append({
                "tier": tier,
                "label": template.format(tour=tour),
                # The ranking points, which is what the figure has to be if
                # anyone is to check it: 550 at a Masters is a number that
                # appears on the tour's own list, and 687.5 is a number that
                # appears nowhere. What the straight-sets rule added rides
                # above it as the superscript, and the two sum to what scored.
                #
                # None, not zero, for a tier he never entered. Zero is the
                # honest answer for a tournament he lost his opening match at
                # -- only wins pay, so a first-round exit earns none -- and
                # the wrong one for a 500 he did not play: one says he earned
                # nothing there, the other that there is nothing to say.
                "points": (None if block.empty else float(
                    (block["event_points"] - block["straight_points"]).sum())),
                "straight": float(block["straight_points"].sum()),
                "entered": 0 if block.empty else int(len(here)),
                "note": "" if block.empty else _note_for(tier, block, here),
            })
        out[str(player)] = entries
    return out
