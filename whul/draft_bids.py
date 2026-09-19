"""Import the auction's bid logs -- the losing bids as well as the winning ones.

The roster records what each asset cost. On its own that number cannot be read:
a club bought for $200 with nobody else bidding and a club bought for $200 over
a $195 rival are the same figure and opposite events, and only the losing bids
tell them apart. So the whole log goes in, round by round, including bids on
assets nobody ended up holding -- those are most of the market information in
the file.

The roster stays authoritative for who holds what and at what price. This reads
the market, not the outcome: where the two disagree the import says so and
changes nothing, because a trade entered by editing the draft sheet looks
exactly like a mistyped bid log and only the league knows which it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from whul.resolve import load_aliases, normalize_name, normalize_team
from whul.store.db import Store, _now

#: The alias source a hand-corrected draft name is filed under, so a name this
#: cannot read is fixed once rather than every time the log is re-imported.
ALIAS_SOURCE = "draft"

#: Column names to look for, most explicit first. The same shape as the roster
#: import's, and for the same reason: a spreadsheet's headers are whatever felt
#: natural on the day.
COLUMNS = {
    "name": ("name", "asset", "player", "team_name", "selection", "player_name"),
    "league": ("league", "competition", "comp"),
    "asset_type": ("asset_type", "type", "kind", "player_or_team"),
    "position": ("position", "role", "pos"),
    "manager": ("manager", "manager_id", "owner", "gm", "initials"),
    "bid": ("bid", "amount", "price", "winning_bid", "cost"),
    "status": ("bid_status", "status", "result", "outcome"),
}

#: What a status text means, matched on the first word. The logs spell out why
#: a bid was rejected -- "Roster Filled During Round" against "Roster Full
#: Pre-Round" -- and the distinction is kept in ``note`` because it is the
#: difference between over-allocating inside a round and bidding for a slot you
#: never had.
STATUSES = {"won": "won", "outbid": "outbid", "rejected": "rejected", "lost": "outbid"}

#: A rejected bid never competed for anything: the manager's roster was already
#: full when it was read. It counts toward what they wanted and not toward what
#: anything cost.
LIVE = ("won", "outbid")


@dataclass
class BidReport:
    """What the import read, matched, and could not place."""

    rounds: dict[int, int] = field(default_factory=dict)
    matched_columns: dict[str, str] = field(default_factory=dict)
    rows: int = 0
    resolved: int = 0
    by_name: int = 0
    by_alias: int = 0
    by_price: int = 0
    unresolved: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    unbid: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    written: bool = False

    def __str__(self) -> str:
        out = [f"Read {self.rows} bid(s) across "
               + ", ".join(f"round {r}: {n}" for r, n in sorted(self.rounds.items()))]
        out.append(
            f"  Matched to a rostered asset: {self.resolved} "
            f"(by name {self.by_name}, by alias {self.by_alias}, "
            f"by manager and price {self.by_price})"
        )
        if self.unresolved:
            out.append(f"  Won and not held ({len(self.unresolved)}) -- released "
                       "between rounds, most likely; assets could be dropped. "
                       "The bid is kept either way:")
            out += [f"    {line}" for line in self.unresolved[:SHOWN]]
            if len(self.unresolved) > SHOWN:
                out.append(f"    ... and {len(self.unresolved) - SHOWN} more")
        if self.moved:
            out.append(f"  Changed hands ({len(self.moved)}) -- won by one "
                       "manager and held by another at the same price, which "
                       "is what a transfer looks like from here:")
            out += [f"    {line}" for line in self.moved]
        if self.disagreements:
            out.append(f"  The roster and the log disagree on a price "
                       f"({len(self.disagreements)}). The roster wins; nothing "
                       "here was changed:")
            out += [f"    {line}" for line in self.disagreements]
        if self.unbid:
            out.append(f"  Held but never bid for ({len(self.unbid)}):")
            out += [f"    {line}" for line in self.unbid[:SHOWN]]
            if len(self.unbid) > SHOWN:
                out.append(f"    ... and {len(self.unbid) - SHOWN} more")
        for problem in self.problems:
            out.append(f"  PROBLEM: {problem}")
        return "\n".join(out)


#: How many lines a list in the report prints before it summarises the rest.
SHOWN = 12


def name_key(name: str, asset_type: str) -> str:
    """One spelling of a name, for matching a log row to a roster row.

    Clubs are reduced further than people because club naming is the noisier
    case -- "Chelsea" and "Chelsea FC" are one club and "Harry Kane" and "H
    Kane" are as far apart as this is willing to go.
    """
    text = str(name or "").strip()
    if not text:
        return ""
    return normalize_team(text) if str(asset_type) == "Team" else normalize_name(text)


def read_log(path: Path) -> pd.DataFrame:
    """One round's log, or a clear error about what is needed to read it."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")
    if path.suffix.lower() in (".csv", ".tsv"):
        return pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",")
    try:
        return pd.read_excel(path)
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            f"reading {path.suffix} needs openpyxl: pip install openpyxl"
        ) from exc


def round_of(path: Path) -> int | None:
    """The round a file is for, from its name. ``All_Bids_Log_Round_2`` is 2."""
    import re

    found = re.search(r"round[_\s-]*(\d+)", str(Path(path).stem), re.IGNORECASE)
    return int(found.group(1)) if found else None


def _find_column(frame: pd.DataFrame, candidates) -> str | None:
    lowered = {str(c).strip().lower().replace(" ", "_"): c for c in frame.columns}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _status(text: str) -> tuple[str, str]:
    """``(won|outbid|rejected, the log's own words)``."""
    raw = str(text or "").strip()
    first = raw.split()[0].lower().strip("(),") if raw else ""
    return STATUSES.get(first, "outbid" if raw else ""), raw


def _number(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _roster_index(store: Store, season: str) -> tuple[dict, dict, dict]:
    """Three ways to turn a name in the log into a rostered asset.

    ``by_name`` answers the ordinary case. ``by_name_league`` exists for the
    names that are two assets: England field a men's and a women's side, Notre
    Dame an NCAAF and an NCAAW one, and one manager won both halves of each
    pair -- so a name held by more than one asset is only answered when the
    league agrees too. ``by_price`` is the last resort described in
    ``_resolve``.
    """
    held = store.query(
        "SELECT a.asset_id, a.display_name, a.asset_type, a.league, "
        "       r.manager_id, r.category, o.cost "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE r.season = ?", (season,),
    )
    by_name: dict[str, set] = {}
    by_name_league: dict[tuple[str, str], str] = {}
    by_price: dict[tuple[str, str, float], set] = {}
    for row in held.itertuples():
        key = name_key(row.display_name, row.asset_type)
        if not key:
            continue
        by_name.setdefault(key, set()).add(str(row.asset_id))
        by_name_league[(key, str(row.league))] = str(row.asset_id)
        cost = _number(row.cost)
        if cost:
            by_price.setdefault(
                (str(row.manager_id), str(row.category), cost), set()
            ).add(str(row.asset_id))
    return by_name, by_name_league, by_price


def _resolve(row: dict, index, aliases: dict, report: BidReport) -> str:
    """The asset id a bid names, or ``''``.

    Three passes, each requiring a unique answer. A name that matches one
    rostered asset is that asset; a name the alias table has been told about is
    that one. The third is for a winning bid alone: the log and the roster
    spell a handful of clubs differently -- "Oklahoma" against "Oklahoma
    Sooners", "Texas Tech Lady Raiders" against "Texas Tech Red Raiders" -- and
    a manager who won one thing in a category for one price held exactly one
    thing in that category at that price. Uniqueness is the guard, as
    everywhere else: two candidates is not a near miss to be broken by a
    further rule, it is a name this cannot read.

    Returning ``''`` is a normal answer, not a failure. Most bids in the file
    are losing bids on assets nobody ended up holding, and those rows are kept:
    what somebody was willing to pay for a player they did not get is the point
    of importing the file at all.
    """
    by_name, by_name_league, by_price = index
    key = row["name_key"]
    found = by_name_league.get((key, row["league"]))
    if found:
        report.by_name += 1
        return found
    hits = by_name.get(key) or set()
    if len(hits) == 1:
        report.by_name += 1
        return next(iter(hits))
    aliased = aliases.get(key) or aliases.get(str(row["name"]).strip())
    if aliased:
        report.by_alias += 1
        return str(aliased)
    if row["status"] == "won" and row["bid"]:
        priced = by_price.get(
            (row["manager_id"], row["category"], float(row["bid"]))
        ) or set()
        if len(priced) == 1:
            report.by_price += 1
            return next(iter(priced))
    return ""


def plan(store: Store, season: str, logs: dict[int, pd.DataFrame]) -> tuple[list[dict], BidReport]:
    """Turn the logs into rows to write, without writing any of them.

    ``logs`` is ``{round: frame}``. The round is not in the file -- it is in
    the filename -- so the caller says which is which, and a round imported
    twice replaces itself rather than doubling.
    """
    report = BidReport()
    index = _roster_index(store, season)
    aliases = load_aliases(store, ALIAS_SOURCE)
    by_name, _, _ = index

    rows: list[dict] = []
    for rnd, frame in sorted(logs.items()):
        columns = {f: _find_column(frame, c) for f, c in COLUMNS.items()}
        for name_field in ("name", "manager", "bid", "status"):
            if not columns.get(name_field):
                report.problems.append(
                    f"round {rnd}: no column looks like {name_field!r}; "
                    f"tried {COLUMNS[name_field]}"
                )
        report.matched_columns.update(
            {f: str(c) for f, c in columns.items() if c is not None})
        if report.problems:
            continue

        report.rounds[rnd] = len(frame)
        report.rows += len(frame)
        for record in frame.to_dict("records"):
            name = str(record.get(columns["name"]) or "").strip()
            manager = str(record.get(columns["manager"]) or "").strip()
            bid = _number(record.get(columns["bid"]))
            status, note = _status(record.get(columns["status"]))
            if not name or not manager or bid is None or not status:
                continue
            asset_type = str(record.get(columns.get("asset_type")) or "").strip()
            row = {
                "season": season,
                "round": int(rnd),
                "name_key": name_key(name, asset_type),
                "league": str(record.get(columns.get("league")) or "").strip(),
                "manager_id": manager,
                "asset_id": "",
                "name": name,
                "asset_type": asset_type,
                "category": "",
                "bid": float(bid),
                "status": status,
                "note": note,
                "recorded_at": _now(),
            }
            rows.append(row)

    if report.problems:
        return [], report

    # The category comes off the roster where the asset is held and off the
    # league where it is not, so a losing bid on an undrafted player still
    # lands in the category it was competing in.
    leagues = _league_categories(store, season)
    for row in rows:
        row["category"] = leagues.get(row["league"], "")
    for row in rows:
        row["asset_id"] = _resolve(row, index, aliases, report)
        if row["asset_id"]:
            report.resolved += 1
        elif row["status"] == "won":
            report.unresolved.append(
                f"round {row['round']}: {row['manager_id']} won "
                f"{row['name']!r} ({row['league']}) for ${row['bid']:,.0f} "
                "and nobody holds them now")
    _check_against_roster(store, season, rows, report)
    return rows, report


def _league_categories(store: Store, season: str) -> dict[str, str]:
    """``{league: the roster category it is drafted into}``.

    Read off what is actually held rather than declared, because the log and
    the roster do not always agree on what a league is called -- the log files
    every tennis player under "Tennis" and the roster under ATP or WTA -- and
    the only leagues this needs an answer for are the ones somebody bid in.
    """
    rows = store.query(
        "SELECT a.league, r.category, COUNT(*) AS n "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE r.season = ? GROUP BY a.league, r.category ORDER BY n DESC",
        (season,),
    )
    out: dict[str, str] = {}
    for row in rows.itertuples():
        out.setdefault(str(row.league), str(row.category))
    # A category is its own league's name often enough to be worth the line.
    for category in set(out.values()):
        out.setdefault(category, category)
    return out


def _check_against_roster(store: Store, season: str, rows: list[dict],
                          report: BidReport) -> None:
    """Say where the log and the roster tell different stories.

    Two different stories, and they are worth separating. An asset held by
    someone other than the manager who won it, *at the price he won it for*,
    has changed hands: the price travelled with the asset, which is what a
    transfer does and what a mistyped initial does not. A price that differs
    is a trade struck at a new figure -- Harry Kane went for $40 and moved for
    $100 -- or an entry error, and only the league knows which.

    Said rather than fixed, either way. The roster is what the league plays
    by, so it wins, and the difference is printed for someone who knows.
    """
    held = store.query(
        "SELECT o.asset_id, r.manager_id, o.cost, a.display_name "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE r.season = ?", (season,),
    )
    wins = {r["asset_id"]: r for r in rows if r["status"] == "won" and r["asset_id"]}
    for row in held.itertuples():
        cost = _number(row.cost) or 0.0
        win = wins.get(str(row.asset_id))
        if win is None:
            if cost > 0:
                report.unbid.append(
                    f"{row.display_name} ({row.manager_id}, ${cost:,.0f})")
            continue
        if abs(float(win["bid"]) - cost) >= 0.5:
            report.disagreements.append(
                f"{row.display_name}: the roster says {row.manager_id} paid "
                f"${cost:,.0f}, the log says {win['manager_id']} won it for "
                f"${win['bid']:,.0f} in round {win['round']}")
        elif str(win["manager_id"]) != str(row.manager_id):
            report.moved.append(
                f"{row.display_name} (${cost:,.0f}): {win['manager_id']} won "
                f"it in round {win['round']}, {row.manager_id} holds it")


def apply(store: Store, season: str, rows: list[dict]) -> int:
    """Write the bids, replacing any round that is imported again."""
    if not rows:
        return 0
    for rnd in sorted({int(r["round"]) for r in rows}):
        store.conn.execute(
            "DELETE FROM draft_bids WHERE season = ? AND round = ?", (season, rnd))
    written = store.upsert(
        "draft_bids", rows,
        keys=("season", "round", "name_key", "league", "manager_id"))
    store.conn.commit()
    return written


def run(store: Store, season: str, paths, dry_run: bool = True) -> BidReport:
    """Read every log, plan, and write unless this is a dry run."""
    logs: dict[int, pd.DataFrame] = {}
    report = BidReport()
    for path in paths:
        path = Path(path)
        rnd = round_of(path)
        if rnd is None:
            report.problems.append(
                f"{path.name}: no round in the filename. Name it like "
                "All_Bids_Log_Round_2.xlsx, or pass --round.")
            continue
        logs[rnd] = read_log(path)
    if report.problems:
        return report

    rows, report = plan(store, season, logs)
    if rows and not dry_run and not report.problems:
        apply(store, season, rows)
        report.written = True
    return report


def load(store: Store, season: str) -> pd.DataFrame:
    """Every recorded bid, as a frame."""
    return store.query(
        "SELECT season, round, name_key, league, manager_id, asset_id, name, "
        "       asset_type, category, bid, status, note "
        "FROM draft_bids WHERE season = ? ORDER BY round, name_key, bid DESC",
        (season,),
    )
