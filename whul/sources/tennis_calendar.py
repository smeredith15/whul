"""The tournament calendar -- what kind of event each tournament is.

Which points table a win pays comes from the tournament's category and draw
size, and neither can be inferred reliably from its name. A keyword list of
host cities misreads renamed and relocated events; a count of matches played
cannot tell a 96-draw from a 56-draw. So both facts live in an explicit table,
checked in and versioned, and every other module reads them from here.

The 2026 table was taken from the season schedule in ``smeredith15/tennis2026``
and is versioned here; ``whul.sources.tour_schedule`` refreshes it for later
seasons. ``unresolved`` reports any tournament a match set references that the
calendar does not know, so a new or renamed event surfaces as a named gap
rather than as quietly wrong points.

The calendar also decides what is a tour event at all. The historical snapshot
labels ITF W15 draws as ``MainTour``, so nothing but this table can be trusted
to keep a $15,000 field out of the benchmark pool.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from whul.scoring.tennis import (
    CATEGORIES, GRAND_SLAM, INTERNATIONAL, TOUR_250, TOUR_FINALS,
)

#: Categories whose points table does not vary with the draw. A slam is always
#: a 128 bracket, the Tour Finals is a round robin, and a team event pays a flat
#: rate per win -- so a missing draw size costs nothing for these.
DRAW_SIZE_OPTIONAL = (GRAND_SLAM, TOUR_FINALS, INTERNATIONAL)

CALENDAR_PATH = Path("data/tennis/calendar.csv")
COLUMNS = ("season", "tour", "tournament", "category", "draw_size")

#: Feeds spell the same event differently -- "Roland Garros" and "French Open",
#: "Cincinnati Masters" and "Cincinnati". Matching on a normalized key absorbs
#: punctuation, casing and sponsor noise without needing an alias per spelling.
_NOISE = re.compile(
    r"\b(atp|wta|open|masters|championships?|international|cup|classic|"
    r"tournament|presented|by|the)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^0-9a-z]+")

#: Events whose feeds disagree on the name itself rather than on punctuation.
#: Normalization cannot bridge these -- "Roland Garros" and "French Open" share
#: no words -- so they are listed, keyed by normalized form and mapped to one
#: canonical key. Kept deliberately short: anything that normalization already
#: handles does not belong here.
ALIASES = {
    "frenchopen": "rolandgarros",
    "french": "rolandgarros",
    "usta": "us",
    "indianwellsmasters": "indianwells",
    "bnpparibas": "indianwells",
    "miamimasters": "miami",
    "canadian": "canada",
    "rogers": "canada",
    "nationalbank": "canada",
    "montreal": "canada",
    "toronto": "canada",
    "italian": "rome",
    "madridmasters": "madrid",
    "monte": "montecarlo",
    "tourfinals": "finals",
    "nittotourfinals": "finals",
}


def normalize_name(name: str | None) -> str:
    """Match key for a tournament name.

    Strips punctuation and the words that appear in half of all tour event
    names, so what remains is the part that identifies the event -- usually its
    city -- then applies the alias table for the events whose feeds disagree on
    more than wording.
    """
    text = _NOISE.sub(" ", str(name or ""))
    key = _NON_ALNUM.sub("", text.lower())
    return ALIASES.get(key, key)


def load(path: Path | None = None) -> pd.DataFrame:
    """The calendar, or an empty frame when the file does not exist yet."""
    target = path or CALENDAR_PATH
    if not target.exists():
        return pd.DataFrame(columns=list(COLUMNS))
    frame = pd.read_csv(target)
    frame["draw_size"] = pd.to_numeric(frame.get("draw_size"), errors="coerce")
    frame["season"] = pd.to_numeric(frame.get("season"), errors="coerce").astype("Int64")
    frame["key"] = frame["tournament"].map(normalize_name)
    return frame


def save(calendar: pd.DataFrame, path: Path | None = None) -> Path:
    """Write the calendar, sorted so a diff between seasons stays readable."""
    target = path or CALENDAR_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    out = calendar[list(COLUMNS)].sort_values(["season", "tour", "tournament"])
    out.to_csv(target, index=False)
    return target


def resolve(
    matches: pd.DataFrame, calendar: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Attach ``category`` and ``draw_size`` to matches from the calendar.

    A season-specific entry wins; failing that, an entry for the same
    tournament in any season is used, since an event's category rarely changes
    and last year's answer beats no answer. Anything the calendar does not know
    keeps whatever the feed supplied, and ``unresolved`` will name it.
    """
    if matches is None or matches.empty:
        return matches if matches is not None else pd.DataFrame()

    table = calendar if calendar is not None else load()
    out = matches.copy()
    out["key"] = out["tournament"].map(normalize_name)
    if table.empty:
        return out.drop(columns=["key"])
    if "key" not in table.columns:
        table = table.assign(key=table["tournament"].map(normalize_name))

    exact = table.dropna(subset=["season"]).set_index(["season", "tour", "key"])
    # One row per tournament regardless of season, for the fallback.
    latest = (
        table.sort_values("season")
        .groupby(["tour", "key"], as_index=False)
        .last()
        .set_index(["tour", "key"])
    )

    # ``DataFrame.get`` returns the *scalar* default when a column is absent,
    # not a column of it. Zipping that crashes on a float and, worse, silently
    # iterates the characters of a string default -- so the columns are built
    # explicitly.
    def column(name: str, default):
        if name in out.columns:
            return out[name]
        return pd.Series([default] * len(out), index=out.index)

    categories, draws, sources = [], [], []
    for season, tour, key, feed_category, feed_draw in zip(
        column("season", None), column("tour", ""), out["key"],
        column("category", ""), column("draw_size", float("nan")),
    ):
        row = None
        source = "feed"
        if (season, tour, key) in exact.index:
            row = exact.loc[(season, tour, key)]
            source = "calendar"
        elif (tour, key) in latest.index:
            row = latest.loc[(tour, key)]
            source = "calendar-other-season"
        if row is not None:
            # A duplicated index yields a frame rather than a row; take the first.
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            categories.append(row["category"])
            draws.append(row["draw_size"])
        else:
            categories.append(feed_category)
            draws.append(feed_draw)
        sources.append(source)

    out["category"] = categories
    out["draw_size"] = draws
    out["category_source"] = sources
    return out.drop(columns=["key"])


def unresolved(matches: pd.DataFrame, calendar: pd.DataFrame | None = None) -> pd.DataFrame:
    """Tournaments in ``matches`` the calendar has no entry for.

    This is the maintenance list: an event here is being scored on whatever the
    feed guessed, which for a 500 misread as a 250 is half the points.
    """
    resolved = resolve(matches, calendar)
    if resolved.empty or "category_source" not in resolved.columns:
        return pd.DataFrame(columns=["season", "tour", "tournament", "matches"])
    gaps = resolved[resolved["category_source"] == "feed"]
    if gaps.empty:
        return pd.DataFrame(columns=["season", "tour", "tournament", "matches"])
    return (
        gaps.groupby(["season", "tour", "tournament"], as_index=False)
        .size()
        .rename(columns={"size": "matches"})
        .sort_values("matches", ascending=False)
        .reset_index(drop=True)
    )


def validate(calendar: pd.DataFrame) -> list[str]:
    """Problems that would make the calendar score things wrongly."""
    problems: list[str] = []
    if calendar.empty:
        return ["calendar is empty"]

    unknown = sorted(set(calendar["category"].dropna()) - set(CATEGORIES))
    if unknown:
        problems.append(f"unknown categories: {unknown}")

    # Only the categories whose table varies with the draw need one.
    needs_draw = ~calendar["category"].isin(DRAW_SIZE_OPTIONAL)
    missing_draw = calendar[needs_draw & calendar["draw_size"].isna()]
    if not missing_draw.empty:
        problems.append(
            f"{len(missing_draw)} entries without a draw size, which picks the "
            f"wrong bracket for 1000s, 500s and 250s: "
            f"{missing_draw['tournament'].head(5).tolist()}"
        )

    # Each tour plays four slams. A count that is not four per tour means an
    # event was missed or mislabelled -- which is exactly how WTA Wimbledon
    # arrived from the tennis2026 seed data marked as a 1000.
    slams = calendar[calendar["category"] == GRAND_SLAM]
    for (season, tour), group in slams.groupby(["season", "tour"]):
        if len(group) != 4:
            problems.append(
                f"{tour} {season} has {len(group)} slams, not 4: "
                f"{sorted(group['tournament'])}"
            )

    duplicated = calendar.duplicated(subset=["season", "tour", "tournament"], keep=False)
    if duplicated.any():
        problems.append(
            f"{int(duplicated.sum())} duplicate entries: "
            f"{calendar.loc[duplicated, 'tournament'].unique()[:5].tolist()}"
        )

    # Every 500 has to have been corrected by hand, so a calendar with none is
    # almost certainly a raw Sackmann seed rather than a finished table.
    if not (calendar["category"] == "500").any():
        problems.append(
            "no 500s -- Sackmann marks 500s and 250s alike as level A, so a "
            "seeded calendar needs its 500s designated before it is trusted"
        )
    return problems
