"""Which competition a player's appearance belongs to, decided by his club's.

A club-soccer player reaches this project as a season aggregate per
competition, and the aggregate is only as good as ESPN's scoping of the request
that fetched it. That scoping wobbles: Bayern's Bundesliga roster returned Harry
Kane with his Champions League match among his appearances, so it was counted as
domestic football -- three points and into the base score -- and held as a
European bonus at the same time, and was gone again the next night.

The club's own results never wobbled. Bayern kept that Champions League win on
both days, because team results are read from a scoreboard walk where every
match is fetched under the competition it was played in. So the club is the
authority, and the player's gamelog is what connects him to it: each of his
events carries ESPN's own match id, and so does each of his club's matches.

The join is therefore exact rather than a date-and-name guess. A player who
appeared in event 401915443 played in whatever competition his club played
401915443 in, whatever the roster that fetched him was told it was asking for.
"""

from __future__ import annotations

import pandas as pd

#: What a disagreement is worth reporting over. A tenth of an appearance is not
#: a thing, and floating point makes exact equality the wrong test.
TOLERANCE = 0.05


def attribute(events: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """A player's appearances, counted per competition, on the club's authority.

    ``events`` are the player's, from his gamelog; ``matches`` are his club's,
    from the scoreboard walk. Both carry ``event_id``.

    An event the club's results do not contain keeps the competition its own
    gamelog gave it, which is honest -- the gamelog names each event truthfully
    -- and is flagged, because a club match list that does not contain a match
    the club played is its own fault worth seeing.
    """
    columns = ["competition_key", "appearances", "from_club"]
    if events is None or events.empty:
        return pd.DataFrame(columns=columns)

    known: dict[str, str] = {}
    if matches is not None and not matches.empty and "event_id" in matches.columns:
        for row in matches.itertuples():
            if str(row.event_id):
                known[str(row.event_id)] = str(row.competition_key)

    rows = []
    for event in events.itertuples():
        found = known.get(str(event.event_id))
        rows.append({
            "competition_key": found or _from_name(str(event.competition)),
            "appearances": 1.0,
            "from_club": found is not None,
        })
    counted = pd.DataFrame(rows)
    return (counted.groupby("competition_key", as_index=False)
            .agg(appearances=("appearances", "sum"),
                 from_club=("from_club", "all")))


def _from_name(name: str) -> str:
    """A competition's key from the name a gamelog event gave it.

    Only used where the club's results do not hold the match. The names are
    ESPN's own and the classifier reads ours, so this goes through the same
    labels the rest of the project matches competitions by.
    """
    from whul.benchmark_sources import COMPETITION_LABELS

    wanted = name.strip().casefold()
    for key, label in COMPETITION_LABELS.items():
        if label.casefold() == wanted:
            return key
    return name.strip() or "unknown"


def counted_appearances(attributed: pd.DataFrame, league: str) -> tuple:
    """``(domestic, held, [names])`` -- how the club's matches split.

    Domestic football is the base score and the benchmark: the league and its
    cups. Europe is held until its competition finishes. The split is made by
    the same classifier the scorer uses, so the two cannot disagree about which
    a competition is.
    """
    from whul.scoring.competition import classify_key
    from whul.scoring.postseason import rule_for

    domestic, held, names = 0.0, 0.0, []
    if attributed is None or attributed.empty:
        return domestic, held, names
    for row in attributed.itertuples():
        key = str(row.competition_key)
        found = classify_key(key, key)
        if not found.counts:
            continue
        if rule_for(found.tier.value, league) is not None:
            held += float(row.appearances)
            names.append(key)
        else:
            domestic += float(row.appearances)
    return domestic, held, sorted(names)


def disagreements(
    attributed: pd.DataFrame, claimed: float, player: str, league: str,
) -> list[dict]:
    """Where the stored figure counts more domestic football than was played.

    ``claimed`` is the counted appearance total the pull recorded, which is
    domestic football alone -- Europe is held, not counted. So it should equal
    the club's own domestic matches this player appeared in, and anything above
    that is a European match wearing a domestic figure.

    Only *more*. The gamelog does not return every competition, so what it
    finds is a floor and not a total, and a claim below it is the ordinary case.
    """
    domestic, held, held_names = counted_appearances(attributed, league)
    if claimed - domestic <= TOLERANCE:
        return []
    return [{
        "player": player,
        "league": league,
        "roster": float(claimed),
        "played": domestic,
        "excess": round(float(claimed) - domestic, 1),
        "held": held,
        "held_in": held_names,
    }]


def report(found: list[dict]) -> list[str]:
    """The disagreements as lines, named."""
    if not found:
        return []
    lines = [
        f"  {len(found)} player(s) counted more domestic football than their "
        f"club played:",
    ]
    for entry in sorted(found, key=lambda e: -e["excess"])[:20]:
        where = (f", and {entry['held']:g} in {', '.join(entry['held_in'])}"
                 if entry.get("held") else "")
        lines.append(
            f"      {entry['player']} ({entry['league']}): the figures count "
            f"{entry['roster']:g}, the club played {entry['played']:g} "
            f"domestically{where}  ({entry['excess']:+g})"
        )
    lines.append(
        "      A season aggregate carries whatever ESPN scoped the request to. "
        "The club's results carry what was actually played."
    )
    return lines
