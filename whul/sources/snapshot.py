"""Historical tennis matches from the Phase7B snapshot.

``model_data_snapshot.rds`` in ``smeredith15/tennis2026`` holds 215,386 matches
back to December 2014 -- ATP and WTA, with round, score, surface and best-of.
It began as a Sackmann export and is now the only copy: the
``JeffSackmann/tennis_atp`` repository has been removed, so the archives this
module originally read no longer exist anywhere to read.

That makes the file itself the asset. It is not vendored here -- it is 7.9 MB
of someone else's dataset -- so the path is configurable and defaults to a
sibling checkout of tennis2026. Copy it somewhere stable and back it up; it
cannot be re-downloaded.

**The calendar decides what counts, not the snapshot's own level column.** That
column marks ITF W15 and W35 events as ``MainTour``, so trusting it would score
a $15,000 Monastir draw as a tour 250. An event the calendar does not know is
not scored, and ``whul.sources.tennis_calendar.unresolved`` names it -- which is
how a historical-only event (Shenzhen, the ATP Cup, an Olympic tournament) gets
noticed and added deliberately rather than scored by accident.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from whul.scoring.tennis import F, QF, R16, R32, R64, R128, RR, SF

#: Where the snapshot and its player mapping live inside ``tennis2026``.
BETTING_DIR = Path("backend/data/betting")
SNAPSHOT_NAME = "model_data_snapshot.rds"
MAPPING_NAME = "player_mapping_table.csv"

#: Places a ``tennis2026`` checkout tends to be, tried in order. The snapshot
#: is the only surviving copy of the tennis history, so it is worth finding
#: wherever it happens to sit rather than insisting on one layout: a dev box, a
#: CI checkout and a cloud workspace all put a sibling repository somewhere
#: different. ``WHUL_TENNIS2026`` wins over all of them.
CHECKOUT_CANDIDATES = (
    Path("../tennis2026"),
    Path("../smeredith15/tennis2026"),
    Path("~/tennis2026"),
)


def default_root(root: Path | None = None) -> Path:
    """The betting directory to read from.

    Returns the first candidate that actually holds the snapshot, so a missing
    file reports the place it was looked for rather than a path nobody chose.
    """
    if root is not None:
        return Path(root)
    env = os.environ.get("WHUL_TENNIS2026")
    candidates = [Path(env)] if env else []
    candidates += list(CHECKOUT_CANDIDATES)
    dirs = [Path(c).expanduser() / BETTING_DIR for c in candidates]
    for directory in dirs:
        if (directory / SNAPSHOT_NAME).exists():
            return directory
    return dirs[0]


#: Kept as the documented default for callers that only want the usual layout.
DEFAULT_ROOT = CHECKOUT_CANDIDATES[0] / BETTING_DIR
#: A small supplement in the same schema, one directory up. It adds spelling
#: variants for ids the main table already carries, so it is read second and
#: never displaces a name the main table gave.
EXTRA_MAPPING = Path("../../aliases.csv")

#: Main-draw rounds. Qualifying (Q1-Q3) is excluded here rather than later, so
#: a missing qualifying result can never read as a main-draw bye.
MAIN_DRAW_ROUNDS = (R128, R64, R32, R16, QF, SF, F, RR)


def default_paths(root: Path | None = None) -> tuple[Path, Path]:
    base = default_root(root)
    return base / SNAPSHOT_NAME, base / MAPPING_NAME


def mapping_paths(root: Path | None = None) -> list[Path]:
    """Every player key file, main table first."""
    base = default_root(root)
    return [base / MAPPING_NAME, (base / EXTRA_MAPPING).resolve()]


def read_snapshot(path: Path) -> pd.DataFrame:
    """The raw snapshot as a frame.

    ``pyreadr`` warns while casting the date column, which carries nulls the
    R side allowed; the nulls are handled downstream and the warning says
    nothing a reader can act on.
    """
    import warnings

    import pyreadr

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        result = pyreadr.read_r(str(path))
    if not result:
        raise ValueError(f"{path} contains no data frame")
    return next(iter(result.values()))


def player_names(path: Path | list[Path]) -> dict[str, str]:
    """``{lowercased canonical_player_id: display name}``.

    The mapping keys are cased (``atp-J-Sinner``) and the snapshot's own ids
    are not (``atp-j-sinner``), so the key is lowercased on both sides -- an
    exact match resolves nothing at all.

    Several aliases map to one id, so the first is kept: they differ in
    spelling, not in person.
    """
    names: dict[str, str] = {}
    for source in ([path] if isinstance(path, Path) else list(path)):
        if not source.exists():
            continue
        mapping = pd.read_csv(source)
        for player_id, alias in zip(
            mapping["canonical_player_id"], mapping["player_name_alias"]
        ):
            names.setdefault(str(player_id).lower(), str(alias))
    return names


def to_matches(raw: pd.DataFrame, names: dict[str, str] | None = None) -> pd.DataFrame:
    """The snapshot in the shape ``whul.scoring.tennis`` expects."""
    names = names or {}
    work = raw.copy()
    dates = pd.to_datetime(work.get("date"), errors="coerce")

    out = pd.DataFrame(
        {
            # A tennis season is a calendar year, so the match date names it.
            "season": dates.dt.year,
            "date": dates.dt.date.astype(str),
            "tour": work.get("tour", "").astype(str),
            "tournament": work.get("tourney_name", "").astype(str),
            "level": work.get("tourney_level", "").astype(str),
            "round": work.get("round", "").astype(str).str.upper(),
            "score": work.get("score", "").astype(str),
            "surface": work.get("surface", "").astype(str),
            "best_of": pd.to_numeric(work.get("best_of"), errors="coerce"),
            "winner_id": work.get("winner_id", "").astype(str),
            "loser_id": work.get("loser_id", "").astype(str),
        }
    )
    out["winner"] = out["winner_id"].map(lambda i: names.get(str(i).lower(), i))
    out["loser"] = out["loser_id"].map(lambda i: names.get(str(i).lower(), i))
    out = out[out["round"].isin(MAIN_DRAW_ROUNDS)]
    return out[out["season"].notna()].assign(season=lambda d: d["season"].astype(int))


def infer_draw_sizes(matches: pd.DataFrame) -> pd.DataFrame:
    """Draw size per event, counted from the players who appear in it.

    It under-counts -- a 128-draw shows about 125, because a walkover leaves a
    player with no match row -- but the bracket is the next power of two up, so
    125 and 128 both land on 128. Used only where the calendar has no draw size
    to give, since the calendar's is exact.
    """
    if matches is None or matches.empty:
        return pd.DataFrame(columns=["season", "tour", "tournament", "draw_size"])
    people = pd.concat(
        [
            matches[["season", "tour", "tournament", "winner"]].rename(columns={"winner": "player"}),
            matches[["season", "tour", "tournament", "loser"]].rename(columns={"loser": "player"}),
        ],
        ignore_index=True,
    )
    return (
        people.groupby(["season", "tour", "tournament"], as_index=False)["player"]
        .nunique()
        .rename(columns={"player": "draw_size"})
    )


def load_matches(
    seasons: list[int] | None = None,
    root: Path | None = None,
    verbose: bool = True,
    strict: bool = True,
) -> pd.DataFrame:
    """Scoreable historical matches, gated by the calendar.

    With ``strict`` -- the default -- an event the calendar does not know is
    dropped rather than scored on a guess. The snapshot's own level column
    cannot be trusted to tell a tour event from an ITF one, so a permissive
    default would put $15,000 draws in the benchmark pool.
    """
    from whul.sources import tennis_calendar

    snapshot_path, mapping_path = default_paths(root)
    if not snapshot_path.exists():
        raise FileNotFoundError(
            f"{snapshot_path} not found. The Sackmann repository is gone, so this "
            f"file is the only copy of the history -- clone smeredith15/tennis2026 "
            f"alongside this one, or pass root= to point at it."
        )

    names = player_names(mapping_paths(root))
    matches = to_matches(read_snapshot(snapshot_path), names)
    if seasons:
        matches = matches[matches["season"].isin(seasons)]

    resolved = tennis_calendar.resolve(matches)
    known = resolved[resolved["category_source"] != "feed"].copy()

    # Fill any gap in the calendar's draw sizes from the field itself.
    if known["draw_size"].isna().any():
        inferred = infer_draw_sizes(known).rename(columns={"draw_size": "inferred"})
        known = known.merge(inferred, on=["season", "tour", "tournament"], how="left")
        known["draw_size"] = known["draw_size"].fillna(known["inferred"])
        known = known.drop(columns=["inferred"])

    if verbose:
        dropped = len(resolved) - len(known)
        print(f"snapshot: {len(known):,} scoreable matches, {dropped:,} dropped", flush=True)
        gaps = tennis_calendar.unresolved(matches)
        if not gaps.empty:
            top = gaps.head(8)
            print(
                "not in the calendar (add any that belong): "
                + ", ".join(f"{r.tournament} {r.season} ({r.matches})" for r in top.itertuples()),
                flush=True,
            )
    return known if strict else resolved


def probe(season: int | None = None, root: Path | None = None) -> dict:
    """Check the snapshot is present, readable and scoreable."""
    from datetime import date

    season = season or (date.today().year - 1)
    snapshot_path, mapping_path = default_paths(root)
    report: dict = {"season": season, "snapshot": str(snapshot_path), "stages": {}}

    if not snapshot_path.exists():
        report["stages"]["file"] = {
            "ok": False,
            "note": f"{snapshot_path} not found. The Sackmann repository has been "
                    f"removed, so this file is the only copy of the history. Clone "
                    f"smeredith15/tennis2026 next to this repo, or copy the file in.",
        }
        return report

    try:
        raw = read_snapshot(snapshot_path)
    except Exception as exc:  # noqa: BLE001 -- the probe reports, it does not raise
        report["stages"]["file"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "note": "pyreadr is needed to read an .rds; run `pip install -e .`",
        }
        return report

    report["stages"]["file"] = {
        "ok": True,
        "rows": len(raw),
        "mapping_present": mapping_path.exists(),
        "columns": sorted(raw.columns)[:15],
    }

    names = player_names(mapping_paths(root))
    matches = to_matches(raw, names)
    for_season = matches[matches["season"] == season]
    report["stages"]["parse"] = {
        "ok": not for_season.empty,
        "main_draw_matches": len(matches),
        "in_season": len(for_season),
        "seasons": f"{matches['season'].min()}-{matches['season'].max()}" if not matches.empty else "",
        "named": f"{int((for_season['winner'] != for_season['winner_id']).sum())}/{len(for_season)}",
        "levels": sorted(set(matches["level"])),
    }
    if for_season.empty:
        return report

    from whul.sources import tennis_calendar

    resolved = tennis_calendar.resolve(for_season)
    known = resolved[resolved["category_source"] != "feed"]
    gaps = tennis_calendar.unresolved(for_season)
    report["stages"]["calendar"] = {
        "ok": not known.empty,
        "scoreable": f"{len(known)}/{len(for_season)}",
        "categories": sorted(set(known["category"])) if not known.empty else [],
        "unresolved_events": gaps["tournament"].head(10).tolist(),
        "note": "unresolved events are dropped, not guessed -- the level column "
                "in this file marks ITF W15 draws as MainTour, so it cannot "
                "gate them itself",
    }

    from whul.scoring.tennis import score_players

    totals = score_players(known)
    report["stages"]["score"] = {
        "ok": not totals.empty,
        "players": len(totals),
        "top": totals.head(5)[["player", "league", "matches_won", "total_points"]].to_dict("records")
        if not totals.empty
        else [],
    }
    return report
