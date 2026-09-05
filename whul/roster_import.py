"""Import drafted rosters from the league's spreadsheet.

The draft happens in a spreadsheet, so that file is the roster's source of
truth and this reads it rather than asking anyone to retype it. The column
names are not known in advance -- a spreadsheet's headers are whatever felt
natural on the day -- so each field is resolved from a list of plausible names
and the run reports what it matched. Check the mapping with ``--dry-run``
before anything is written.

**A partly-finished draft imports fine.** A row with no asset is a slot nobody
has taken yet, which is the normal state between rounds; it is counted and
reported, not treated as an error.

Ids over names, wherever the sheet has them. A name is how the league talks
about a player and an id is how a feed does, and the two disagree constantly --
so a sheet that carries a feed id is matched on that, and names are the
fallback with anything ambiguous reported rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from whul.config.league import ALL_SLOTS, SEASON, active_slots, manager_name
from whul.store import rosters
from whul.store.db import Store, _now

DEFAULT_PATH = Path("Master_Drafted_Assets.xlsx")

#: Column names to look for, most explicit first.
COLUMNS = {
    "manager": ("manager", "manager_id", "owner", "team", "gm", "initials"),
    "category": ("category", "roster_category", "slot", "pool", "group", "position_group"),
    "asset_type": ("asset_type", "type", "kind", "player_or_team"),
    "asset": ("asset", "player", "name", "player_name", "team_name", "selection"),
    "asset_id": ("asset_id", "id", "feed_id", "espn_id", "external_id"),
    "slot_index": ("slot_index", "slot_no", "slot", "index", "pick_in_slot"),
    "role": ("role", "position", "pos"),
    # The competition the asset actually plays in, which is not the roster
    # category: a Bundesliga player fills a "Club Soccer Other" slot, and the
    # scoring needs the former while the roster needs the latter.
    "league": ("league", "competition", "comp"),
    "cost": ("winning_bid", "bid", "cost", "price", "salary", "amount"),
}

#: What counts as "no pick yet" in a spreadsheet cell.
BLANK = {"", "-", "--", "n/a", "na", "none", "tbd", "empty", "nan", "null"}

#: Marks an occupancy as this import's own, so a re-import may take it back
#: while leaving a dated trade from the admin page untouched.
IMPORT_NOTE = "draft"


@dataclass
class ImportReport:
    """What the import found, matched and could not place."""

    path: str = ""
    rows: int = 0
    matched_columns: dict[str, str] = field(default_factory=dict)
    missing_columns: list[str] = field(default_factory=list)
    managers: dict[str, int] = field(default_factory=dict)
    filled: int = 0
    empty: int = 0
    assets: int = 0
    problems: list[str] = field(default_factory=list)
    #: Faults worth seeing that must not stop the import. A structural problem
    #: -- more picks than the roster allows -- means the sheet cannot be
    #: written at all. A name collision means one asset is wrong while the
    #: other 283 are fine, and refusing the lot would quietly freeze the
    #: nightly roster refresh over it.
    warnings: list[str] = field(default_factory=list)
    released: list[str] = field(default_factory=list)
    written: bool = False

    def __str__(self) -> str:
        lines = [f"{self.path}: {self.rows} rows"]
        for field_name, column in self.matched_columns.items():
            lines.append(f"  {field_name:<12} <- {column!r}")
        for field_name in self.missing_columns:
            lines.append(f"  {field_name:<12} <- NOT FOUND")
        lines.append(
            f"  {self.filled} picks, {self.empty} slots still open, "
            f"{self.assets} distinct assets"
        )
        for manager, count in sorted(self.managers.items()):
            lines.append(f"    {manager_name(manager)} ({manager}): {count} picks")
        for release in self.released:
            lines.append(f"  - {release}")
        for warning in self.warnings:
            lines.append(f"  ? {warning}")
        for problem in self.problems:
            lines.append(f"  ! {problem}")
        lines.append("  written" if self.written else "  dry run -- nothing written")
        return "\n".join(lines)


def _find_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    """The first candidate present, matched on whole words in the header.

    Word boundaries, not substrings. A bare ``endswith`` matched "Winning_Bid"
    against the candidate "id" and bound every asset's identity to its auction
    price -- which the dry run showed as 61 distinct assets out of 205 picks,
    because bids collide and names do not.
    """
    normalized = {
        str(c).strip().lower().replace(" ", "_").replace("-", "_"): c
        for c in frame.columns
    }
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    # A header like "Manager Name" or "Player ID" should still match, but only
    # where the candidate is one of the header's own words.
    for candidate in candidates:
        for key, original in normalized.items():
            if candidate in key.split("_"):
                return original
    return None


def _number(value) -> float | None:
    """A numeric cell, or None. A blank price is unknown, not zero."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in BLANK else text


def read_sheet(path: Path, sheet: str | int = 0) -> pd.DataFrame:
    """The spreadsheet, or a clear error about what is needed to read it."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Put the draft spreadsheet there, or pass --path."
        )
    if path.suffix.lower() in (".csv", ".tsv"):
        return pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",")
    try:
        return pd.read_excel(path, sheet_name=sheet)
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            f"reading {path.suffix} needs openpyxl: pip install openpyxl"
        ) from exc


def plan(frame: pd.DataFrame, path: str = "") -> tuple[list[dict], ImportReport]:
    """Turn the sheet into rows to write, without writing any of them."""
    report = ImportReport(path=path, rows=len(frame))
    columns = {}
    for field_name, candidates in COLUMNS.items():
        found = _find_column(frame, candidates)
        if found:
            columns[field_name] = found
            report.matched_columns[field_name] = str(found)
        else:
            report.missing_columns.append(field_name)

    for required in ("manager", "category"):
        if required not in columns:
            report.problems.append(
                f"no column looks like {required!r}; tried {COLUMNS[required]}"
            )
    if "asset" not in columns and "asset_id" not in columns:
        report.problems.append("no column names the drafted asset")
    if report.problems:
        return [], report

    known = {(g.asset_type, g.category): g for g in active_slots(ALL_SLOTS)}
    by_category: dict[str, list] = {}
    for group in active_slots(ALL_SLOTS):
        by_category.setdefault(group.category, []).append(group)

    picks: list[dict] = []
    counters: dict[tuple[str, str, str], int] = {}
    for position, row in enumerate(frame.to_dict("records"), start=2):
        manager = _clean(row.get(columns["manager"]))
        category = _clean(row.get(columns["category"]))
        if not manager or not category:
            continue

        asset = _clean(row.get(columns.get("asset"), ""))
        asset_id = _clean(row.get(columns.get("asset_id"), ""))
        asset_type = _clean(row.get(columns.get("asset_type"), ""))
        if not asset_type:
            groups = by_category.get(category, [])
            # A category with one asset type needs no column to say which.
            asset_type = groups[0].asset_type if len(groups) == 1 else "Player"

        if (asset_type, category) not in known:
            report.problems.append(
                f"row {position}: no {asset_type} slot for category {category!r}"
            )
            continue

        key = (manager, asset_type, category)
        counters[key] = counters.get(key, 0) + 1
        index = counters[key]
        cap = known[(asset_type, category)].cap
        if index > cap:
            report.problems.append(
                f"row {position}: {manager} has {index} {category} "
                f"{asset_type.lower()}s but the roster allows {cap}"
            )
            continue

        if not asset and not asset_id:
            report.empty += 1
            continue

        report.filled += 1
        report.managers[manager] = report.managers.get(manager, 0) + 1
        league = _clean(row.get(columns.get("league"), "")) or category
        picks.append({
            "manager": manager,
            "category": category,
            "league": league,
            "asset_type": asset_type,
            "slot_index": index,
            "asset_id": asset_id or _asset_id(league, asset_type, asset),
            "display_name": asset or asset_id,
            "role": _clean(row.get(columns.get("role"), "")),
            "cost": _number(row.get(columns.get("cost"))),
        })

    # Slots the sheet never mentioned are open too, not missing.
    expected = sum(g.cap for g in active_slots(ALL_SLOTS)) * len(report.managers or {1})
    report.empty += max(0, expected - report.filled - report.empty)
    report.assets = len({p["asset_id"] for p in picks})

    # Two rows landing on one asset id is not a duplicate pick -- it is one
    # asset in two slots, which scores for both managers. It happens when two
    # different teams share a name and the league column does not separate
    # them: Michigan's men's and women's sides were both entered as NCAAM, so
    # both became `team-ncaam-michigan-wolverines`. Reported rather than merged
    # or silently split, because only the sheet knows which was meant.
    seen: dict[str, list[str]] = {}
    for pick in picks:
        seen.setdefault(pick["asset_id"], []).append(
            f"{pick['manager']}/{pick['category']}#{pick['slot_index']}"
        )
    for asset_id, where in seen.items():
        if len(where) > 1:
            name = next(p["display_name"] for p in picks if p["asset_id"] == asset_id)
            report.warnings.append(
                f"{name!r} fills {len(where)} slots at once ({', '.join(where)}) "
                f"and scores for each -- they share the id {asset_id}, so the "
                f"league column does not tell them apart"
            )
    return picks, report


def _asset_id(category: str, asset_type: str, name: str) -> str:
    """A stable id for an asset the sheet named but did not identify.

    Derived from the name, so re-importing the same sheet lands on the same id
    rather than creating a second copy of every player. A feed id replaces it
    later without disturbing anything, because the alias table maps both.
    """
    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    kind = "team" if asset_type == "Team" else "player"
    pool = "".join(c if c.isalnum() else "-" for c in category.lower()).strip("-")
    return f"{kind}-{pool}-{slug}"


def apply(
    store: Store,
    picks: list[dict],
    season: str,
    start: date | None = None,
) -> int:
    """Write the roster. Creates managers, assets and slots as needed."""
    start = start or SEASON.start
    for manager in sorted({p["manager"] for p in picks}):
        rosters.add_manager(store, manager, manager_name(manager))
        rosters.create_slots(store, manager, season)

    store.upsert(
        "assets",
        [
            {
                "asset_id": p["asset_id"], "asset_type": p["asset_type"],
                "display_name": p["display_name"], "league": p["league"],
                "role": p["role"], "norm_key": p["league"],
                "active": 1, "created_at": _now(),
            }
            for p in {p["asset_id"]: p for p in picks}.values()
        ],
        keys=("asset_id",),
    )

    slots = {
        (s["manager_id"], s["asset_type"], s["category"], s["slot_index"]): s["slot_id"]
        for s in store.query(
            "SELECT slot_id, manager_id, asset_type, category, slot_index "
            "FROM roster_slots WHERE season = ?",
            (season,),
        ).to_dict("records")
    }
    # A slot whose draft occupancy has been closed has moved on from the sheet
    # -- someone released or traded it with a real effective date. Assigning to
    # it again would not merely add a row: the upsert is keyed on the slot and
    # the start date, so it would set `end_date` back to NULL and reopen the
    # occupancy that was deliberately ended. The slot would then have two open
    # at once, which is the overlap the rollup warns about, and the trade would
    # be quietly undone by the next nightly run.
    moved_on = set(store.query(
        "SELECT o.slot_id FROM slot_occupancy o "
        "JOIN roster_slots s ON s.slot_id = o.slot_id "
        "WHERE s.season = ? AND o.note = ? AND o.end_date IS NOT NULL",
        (season, IMPORT_NOTE),
    )["slot_id"]) if picks else set()

    written = 0
    held: set[tuple[str, str]] = set()
    for pick in picks:
        slot_id = slots.get(
            (pick["manager"], pick["asset_type"], pick["category"], pick["slot_index"])
        )
        if slot_id in moved_on:
            continue
        if slot_id:
            rosters.assign(
                store, slot_id, pick["asset_id"], start,
                note=IMPORT_NOTE, cost=pick.get("cost"),
            )
            held.add((slot_id, pick["asset_id"]))
            written += 1

    # Assigning is not enough on its own. A pick moving to another slot leaves
    # the old occupancy open, so the asset sits in two slots and scores for
    # both managers -- which is what a trade entered by editing the sheet did:
    # Shai Gilgeous-Alexander counted for LS and JM at once.
    dropped = rosters.drop_unlisted(store, season, held, IMPORT_NOTE)
    return written, dropped


def run(
    store: Store,
    season: str,
    path: Path | None = None,
    sheet: str | int = 0,
    dry_run: bool = True,
) -> ImportReport:
    """Read, plan, and write unless this is a dry run."""
    path = Path(path or DEFAULT_PATH)
    frame = read_sheet(path, sheet)
    picks, report = plan(frame, str(path))
    if picks and not dry_run and not report.problems:
        _, dropped = apply(store, picks, season)
        report.released = [
            f"{slot} no longer holds {asset}; the sheet moved it" 
            for slot, asset in dropped
        ]
        report.written = True
    return report
