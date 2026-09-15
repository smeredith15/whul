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


def club_football(matches: pd.DataFrame, league: str) -> tuple:
    """``(domestic, held, {keys})`` -- what the club itself played.

    The club is the authority this module was written around, and it is the
    only hard ceiling available: a player cannot appear in more domestic
    matches than his club played. Counted on distinct events, since a club's
    match list holds a row per side.
    """
    from whul.scoring.competition import classify_key
    from whul.scoring.postseason import rule_for

    domestic = held = 0.0
    keys: set[str] = set()
    if matches is None or matches.empty or "competition_key" not in matches:
        return domestic, held, keys
    seen: set[str] = set()
    for row in matches.itertuples():
        event = str(getattr(row, "event_id", "") or "")
        if event and event in seen:
            continue
        seen.add(event)
        key = str(row.competition_key)
        found = classify_key(key, key)
        if not found.counts:
            continue
        keys.add(key)
        if rule_for(found.tier.value, league) is not None:
            held += 1.0
        else:
            domestic += 1.0
    return domestic, held, keys


def disagreements(
    attributed: pd.DataFrame, claimed: float, player: str, league: str,
    matches: pd.DataFrame | None = None,
) -> list[dict]:
    """Where the stored figure counts more domestic football than was played.

    ``claimed`` is the counted appearance total the pull recorded, which is
    domestic football alone -- Europe is held, not counted.

    Judged against two different things, because they answer differently. The
    club's own domestic matches are a hard ceiling: nobody appears in more of
    them than were played, so a claim above that is wrong whatever else is
    true. The player's gamelog is the sharper instrument -- it is what catches
    a European match wearing a domestic figure -- but it does not return every
    competition. It drops the domestic cups, and every player this flagged on
    first run was flagged by exactly the matches it had not returned: Chelsea's
    two League Cup ties, Bayern's Pokal tie.

    So a claim above what the gamelog saw, in a competition the gamelog never
    returned at all, is the gamelog's silence and is reported as unjudged. The
    alternative is a check that cries wolf, and a check that cries wolf cannot
    be promoted to one that blocks.
    """
    domestic, held, held_names = counted_appearances(attributed, league)
    if claimed - domestic <= TOLERANCE:
        return []

    club_domestic, club_held, club_keys = club_football(matches, league)
    seen_keys = ({str(r.competition_key) for r in attributed.itertuples()}
                 if attributed is not None and not attributed.empty else set())
    entry = {
        "player": player,
        "league": league,
        "roster": float(claimed),
        "played": domestic,
        "excess": round(float(claimed) - domestic, 1),
        "held": held,
        "held_in": held_names,
        "club": club_domestic,
        "missing": sorted(club_keys - seen_keys),
    }
    if club_keys and claimed - club_domestic > TOLERANCE:
        # More domestic football than the club played. Impossible, so it is a
        # fault however the gamelog behaved.
        entry["verdict"] = "impossible"
    elif club_keys and (club_keys - seen_keys):
        # A whole competition the gamelog did not return. It cannot say whether
        # he missed those matches or it simply did not fetch them.
        entry["verdict"] = "unjudged"
    else:
        entry["verdict"] = "excess"
    return [entry]


#: What each verdict is called, and what a reader should do about it.
VERDICTS = {
    "impossible": (
        "counted more domestic football than their club played",
        "      A season aggregate carries whatever ESPN scoped the request to. "
        "The club's results carry what was actually played. Nobody appears in "
        "more of his club's matches than it played, so these are wrong.",
    ),
    "excess": (
        "counted more domestic football than their own gamelog accounts for",
        "      The gamelog returned every competition the club played, so the "
        "excess is a match he did not play or one he played in Europe.",
    ),
    "unjudged": (
        "could not be judged: their gamelog did not return every competition "
        "their club played",
        "      The missing competitions are named. A figure larger than a "
        "gamelog that never fetched the domestic cup is that gamelog's "
        "silence, not the figure's fault.",
    ),
}


def report(found: list[dict]) -> list[str]:
    """The disagreements as lines, named, worst verdict first."""
    if not found:
        return []
    lines: list[str] = []
    for verdict in ("impossible", "excess", "unjudged"):
        group = [e for e in found if e.get("verdict", "excess") == verdict]
        if not group:
            continue
        heading, footer = VERDICTS[verdict]
        lines.append(f"  {len(group)} player(s) {heading}:")
        for entry in sorted(group, key=lambda e: -e["excess"])[:20]:
            where = (f", and {entry['held']:g} in {', '.join(entry['held_in'])}"
                     if entry.get("held") else "")
            # Only where it is the reason. On an impossible claim the club's
            # own ceiling decides, and naming a gap the verdict does not rest
            # on reads as the excuse for it.
            missing = (f"; its gamelog never returned "
                       f"{', '.join(entry['missing'])}"
                       if entry.get("missing") and verdict == "unjudged" else "")
            club = (f", the club played {entry['club']:g}"
                    if entry.get("club") else "")
            lines.append(
                f"      {entry['player']} ({entry['league']}): the figures "
                f"count {entry['roster']:g}, his gamelog shows "
                f"{entry['played']:g}{where}{club}{missing}  "
                f"({entry['excess']:+g})"
            )
        lines.append(footer)
    return lines
