"""Best of the rest: the strongest roster nobody drafted, once a season is over.

Run by hand, one league group at a time, after that group's season has finished:
MLB's first half of the contract year whenever its 2026 postseason is done, the
whole MLB year with tennis, golf and motorsports when the league year closes,
international soccer once the Women's World Cup is over, and every other group
when its own season ends. Nothing here runs nightly and nothing reaches the
site: it is a stand-alone page, built the way the weekly recap is.

Every group is pulled league-wide through the same path the nightly run takes
(``whul.ingest._pull``), scored by the same scorers and placed on the frozen
scale -- so an undrafted player's figure is the one he would have carried on a
roster. The drafted assets are taken out by the matcher the standings use, and
what is left fills the group's slots, best first.

**Season slots only.** A best-performances slot is paid a player's best games,
and only rostered players have their games recorded, so the comparison is made
on the full-season slots alone -- for teams, which have no such slot, that is
the whole category. The best-performances slot is filled with the next-best
season so the roster reads whole, and marked as not compared.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Callable

import pandas as pd

from whul.config.league import (
    ALL_SLOTS, CATEGORY_COMPETITIONS, CROSS_POOL_SOCCER, LEAGUE_ABBR, LEAGUE_NAME,
    POOL_MAP_PLAYERS, POOL_MAP_TEAMS, SEASON, manager_name,
)
from whul.store.db import Store


# --- the groups ----------------------------------------------------------------

def _first_half_over() -> date:
    from whul.scoring import completion
    return completion.finishes("MLB", SEASON.start.year)


def _league_year_over() -> date:
    return SEASON.end


def _finished(*leagues: str) -> Callable[[], date]:
    def day() -> date:
        from whul.season_end import finished_by
        days = [finished_by(league) for league in leagues]
        return max(d for d in days if d is not None)
    return day


@dataclass(frozen=True)
class Group:
    """One stand-alone page: which categories, from which pulls, and when."""

    key: str
    label: str
    categories: tuple[str, ...]
    sources: tuple[str, ...]
    ready: Callable[[], date]
    #: Seasons to ask for instead of the league year's, for the first half of
    #: MLB's contract year: run in 2027 it must still be 2026 alone.
    seasons: tuple[int, ...] = ()
    #: The last day the managers' slots are read at, where that is not today.
    compare_until: Callable[[], date] | None = None


GROUPS: dict[str, Group] = {g.key: g for g in (
    Group("mlb-2026", f"MLB {SEASON.start.year}", ("MLB",), ("mlb", "mlb-teams"),
          _first_half_over, seasons=(SEASON.start.year,), compare_until=_first_half_over),
    Group("mlb", "MLB", ("MLB",), ("mlb", "mlb-teams"), _league_year_over),
    Group("tennis", "Tennis", ("Tennis",), ("tennis",), _league_year_over),
    Group("pga", "PGA", ("PGA",), ("pga",), _league_year_over),
    Group("motorsports", "Motorsports", ("Motorsports",), ("motorsports",), _league_year_over),
    Group("intl-soccer", "International soccer", ("Intl Soccer",), ("intl-soccer",),
          _finished("Women's Intl Soccer", "Men's Intl Soccer")),
    Group("nfl", "NFL", ("NFL",), ("nfl", "nfl-teams"), _finished("NFL")),
    Group("nba", "NBA", ("NBA",), ("nba", "nba-teams"), _finished("NBA")),
    Group("nhl", "NHL", ("NHL",), ("nhl", "nhl-teams"), _finished("NHL")),
    Group("ncaaf", "NCAAF", ("NCAAF",), ("ncaaf",), _finished("NCAAF")),
    Group("ncaam", "NCAAM", ("NCAAM",), ("ncaam",), _finished("NCAAM")),
    Group("ncaaw", "NCAAW", ("NCAAW",), ("ncaaw",), _finished("NCAAW")),
    Group("ncaa-baseball", "NCAA Baseball", ("NCAA Baseball",), ("ncaabaseball",),
          _finished("NCAA Baseball")),
    Group("ncaa-softball", "NCAA Softball", ("NCAA Softball",), ("ncaasoftball",),
          _finished("NCAA Softball")),
    Group("club-soccer", "Club soccer", CROSS_POOL_SOCCER,
          ("soccer-players", "epl", "laliga", "seriea", "bundesliga", "ligue1", "mls", "nwsl"),
          _finished("Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1",
                    "MLS", "NWSL")),
)}


# --- the result ----------------------------------------------------------------

@dataclass(frozen=True)
class Pick:
    name: str
    team: str
    league: str
    score: float
    #: "season" for a full-season slot, "best" for the best-performances one
    #: (filled with the next season and not compared), "bench" for the rest.
    slot: str


@dataclass
class Category:
    category: str
    asset_type: str
    picks: list[Pick]
    #: Each manager's full-season slots in this category, summed.
    managers: dict[str, float]

    @property
    def total(self) -> float:
        return sum(p.score for p in self.picks if p.slot == "season")


@dataclass
class Result:
    group: Group
    as_of: date
    compared_on: date | None
    categories: list[Category]
    notes: list[str] = field(default_factory=list)

    def totals(self) -> tuple[float, dict[str, float]]:
        rest = sum(c.total for c in self.categories)
        managers: dict[str, float] = {}
        for c in self.categories:
            for m, v in c.managers.items():
                managers[m] = managers.get(m, 0.0) + v
        return rest, managers

    def headline(self) -> str:
        rest, managers = self.totals()
        ahead = sorted(managers.items(), key=lambda kv: -kv[1])
        beaten = [manager_name(m) for m, v in ahead if v < rest]
        place = 1 + sum(1 for _, v in ahead if v > rest)
        said = (f"Best of the rest, {self.group.label}: {rest:.1f} on the season slots, "
                f"which would rank #{place} of {len(ahead) + 1}")
        return said + (f", ahead of {', '.join(beaten)}." if beaten else ".")


# --- building it ---------------------------------------------------------------

def _source(key: str, group: Group):
    """The nightly source, asked league-wide.

    A roster-scoped pull is narrowed to the drafted names; asked with none it
    reads nothing at all, except MLB's players, whose date-range totals are the
    whole league until names narrow them. The college sports fall back to the
    benchmark's builder, which reads the whole division.
    """
    from whul.benchmark_sources import SOURCES

    source = SOURCES[key]
    if source.roster_scoped and key != "mlb":
        source = replace(source, live=None, roster_scoped=False)
    if group.seasons:
        source = replace(source, seasons_for=lambda _day: list(group.seasons))
    return source


def _undrafted(store: Store, source, scored: pd.DataFrame) -> pd.DataFrame:
    """The scored rows no roster holds, by the matcher the standings use."""
    from whul import resolve as resolver

    assets = resolver.rostered_assets(store, SEASON.label, source.asset_type)
    if scored is None or scored.empty or assets.empty:
        return scored
    _, resolution = resolver.resolve(
        scored, assets, source.asset_type,
        aliases=resolver.load_aliases(store, source.key), league=source.league,
        many_per_asset=getattr(source, "post_normalize", None) is not None)
    name_col, _ = resolver._name_columns(scored, source.asset_type)
    taken = {m.feed_name for m in resolution.matched}
    return scored[~scored[name_col].astype(str).isin(taken)]


def _placed(store: Store, source, rows: pd.DataFrame) -> pd.DataFrame:
    """On the frozen scale, folded as the nightly run folds them."""
    from whul.normalize import apply_benchmarks
    from whul.store import benchmarks as store_benchmarks

    version = store_benchmarks.active_version(store, SEASON.label)
    if version is None:
        raise ValueError("no frozen benchmark, so nothing can be placed on the scale")
    placed = apply_benchmarks(rows, store_benchmarks.load(store, version.version),
                              source.asset_type, strict=False)
    placed = placed[placed["scaled_score"].notna()]
    fold = getattr(source, "post_normalize", None)
    return fold(placed) if fold is not None and not placed.empty else placed


def _identity(row: dict, asset_type: str) -> tuple[str, str]:
    if asset_type == "Team":
        return str(row.get("team") or row.get("display_name") or ""), ""
    name = str(row.get("player") or row.get("name") or row.get("display_name") or "")
    team = row.get("team") or row.get("affiliation") or ""
    return name, "" if pd.isna(team) else str(team)


def _category_of(league: str, asset_type: str) -> str:
    pool = POOL_MAP_TEAMS if asset_type == "Team" else POOL_MAP_PLAYERS
    return pool.get(str(league), "")


def _manager_totals(store: Store, day: date | None, category: str,
                    asset_type: str) -> dict[str, float]:
    if day is None:
        return {}
    rows = store.query(
        "SELECT r.manager_id, ss.score FROM slot_scores ss "
        "JOIN roster_slots r ON r.slot_id = ss.slot_id "
        "WHERE ss.season = ? AND ss.as_of = ? AND r.category = ? AND r.asset_type = ? "
        "AND ss.counts = 1 AND COALESCE(ss.scored_as, 'season') IN ('season', '')",
        (SEASON.label, day.isoformat(), category, asset_type))
    return {str(m): float(v) for m, v in rows.groupby("manager_id")["score"].sum().items()}


def compare_day(store: Store, group: Group, today: date) -> date | None:
    """The last stored day at or before the group's comparison point."""
    until = min(today, group.compare_until()) if group.compare_until else today
    day = store.scalar("SELECT MAX(as_of) FROM slot_scores WHERE season = ? AND as_of <= ?",
                       (SEASON.label, until.isoformat()))
    return date.fromisoformat(str(day)) if day else None


def build(store: Store, group: Group, today: date,
          pull: Callable | None = None) -> Result:
    """Every undrafted asset in the group, scored, and the best roster of them.

    ``pull(source, as_of)`` returns a source's scored league-wide frame; the
    nightly run's own ``_pull`` by default. A test hands in frames instead.
    """
    from whul import ingest
    from whul.scoring import completion

    completion.remember(store.read_finals())
    if pull is None:
        def pull(source, as_of):
            notes: list[str] = []
            return ingest._pull(source, as_of, True, names=[], notes=notes,
                                store=store, today=as_of)

    by_type: dict[str, list[pd.DataFrame]] = {"Team": [], "Player": []}
    notes = []
    for key in group.sources:
        source = _source(key, group)
        scored = pull(source, today)
        if scored is None or scored.empty:
            notes.append(f"{key}: the pull returned nothing")
            continue
        rest = _undrafted(store, source, scored)
        placed = _placed(store, source, rest)
        if not placed.empty:
            by_type[source.asset_type].append(placed)

    compared = compare_day(store, group, today)
    categories = []
    for group_slot in ALL_SLOTS:
        if group_slot.category not in group.categories or not group_slot.active:
            continue
        frames = by_type[group_slot.asset_type]
        if not frames:
            continue
        pool = pd.concat(frames, ignore_index=True)
        pool = pool[[_category_of(lg, group_slot.asset_type) == group_slot.category
                     for lg in pool["league"]]]
        ranked = pool.sort_values("scaled_score", ascending=False).to_dict("records")
        picks, seen = [], set()
        for row in ranked:
            name, team = _identity(row, group_slot.asset_type)
            if not name or name in seen:
                continue
            seen.add(name)
            index = len(picks)
            slot = ("season" if index < group_slot.starters
                    else "best" if index < group_slot.starters + group_slot.best
                    else "bench")
            picks.append(Pick(name, team, str(row.get("league", "")),
                              float(row["scaled_score"]), slot))
            if len(picks) == group_slot.cap:
                break
        categories.append(Category(group_slot.category, group_slot.asset_type, picks,
                                   _manager_totals(store, compared, group_slot.category,
                                                   group_slot.asset_type)))
    return Result(group, today, compared, categories, notes)


# --- the page ------------------------------------------------------------------

MIXED = set(CATEGORY_COMPETITIONS) | set(CROSS_POOL_SOCCER)


def render(result: Result) -> str:
    from whul.site.theme import SERIES_LIGHT, series_index

    rest, totals = result.totals()
    managers = sorted(totals)

    def chip(m):
        colour = SERIES_LIGHT[series_index(managers, m)] if m in managers else "#8a8f98"
        return (f"<span class='chip' style='--c:{colour}'>"
                f"{html.escape(manager_name(m))}</span>")

    board = sorted([(m, v) for m, v in totals.items()] + [("", rest)], key=lambda kv: -kv[1])
    table = "".join(
        f"<tr class='{'rest' if not m else ''}'><td class='rk'>{i}</td>"
        f"<td>{chip(m) if m else '<b>Best of the rest</b>'}</td>"
        f"<td class='num'>{v:.1f}</td></tr>"
        for i, (m, v) in enumerate(board, start=1))

    cards = []
    for c in result.categories:
        rows = []
        for p in c.picks:
            tag = {"best": "<span class='tag'>best games · not compared</span>",
                   "bench": "<span class='tag'>bench</span>"}.get(p.slot, "")
            sub = p.league if c.asset_type == "Team" and c.category in MIXED else p.team
            rows.append(f"<tr class='{p.slot}'><td><div class='nm'>{html.escape(p.name)}{tag}</div>"
                        + (f"<div class='meta'>{html.escape(sub)}</div>" if sub else "")
                        + f"</td><td class='num'>{p.score:.1f}</td></tr>")
        versus = " · ".join(f"{html.escape(manager_name(m))} {v:.1f}"
                            for m, v in sorted(c.managers.items(), key=lambda kv: -kv[1]))
        kind = "Teams" if c.asset_type == "Team" else "Players"
        cards.append(
            f"<section class='card'><h2>{html.escape(c.category)} · {kind}"
            f"<span class='hint'>{c.total:.1f}</span></h2>"
            f"<table>{''.join(rows) or '<tr><td class=meta>Nobody left to pick</td></tr>'}</table>"
            + (f"<div class='versus'>Managers: {versus}</div>" if versus else "")
            + "</section>")

    when = (f"managers' slots as of {result.compared_on:%b} {result.compared_on.day}"
            if result.compared_on else "no stored standings to compare against")
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in result.notes)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(LEAGUE_ABBR)} best of the rest: {html.escape(result.group.label)}</title>
<style>{STYLE}</style></head><body><main>
<header><div class="kicker">{html.escape(LEAGUE_NAME)}</div>
<h1>Best of the rest: {html.escape(result.group.label)}</h1>
<div class="span">The strongest roster nobody drafted · {when}</div></header>
<section class="card"><h2>Where it would finish <span class="hint">season slots</span></h2>
<table>{table}</table></section>
<div class="cards">{''.join(cards)}</div>
{f'<ul class="notes">{notes}</ul>' if notes else ''}
<footer>Normalized points on the frozen scale, from the same scoring as the standings.
Only full-season slots are compared: a best-games slot pays a player's best games, and
undrafted players' games are not recorded.</footer>
</main></body></html>"""


STYLE = """
:root { --bg:#f4f5f7; --card:#fff; --ink:#16181d; --muted:#626873; --line:#e3e5e9; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.4 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { width:760px; max-width:100%; margin:0 auto; padding:20px 16px 24px; }
.kicker { font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
h1 { margin:2px 0; font-size:26px; }
.span { color:var(--muted); font-size:14px; margin-bottom:12px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; margin-bottom:12px; }
.cards { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:0 12px; }
h2 { font-size:15px; margin:0 0 6px; display:flex; justify-content:space-between; }
.hint { font-weight:400; color:var(--muted); font-size:13px; }
table { width:100%; border-collapse:collapse; }
td { padding:4px; border-top:1px solid var(--line); vertical-align:middle; }
tr:first-child td { border-top:0; }
tr.rest td { background:#fffaf0; }
.rk { width:28px; color:var(--muted); }
.num { text-align:right; font-weight:600; font-variant-numeric:tabular-nums; white-space:nowrap; }
.nm { font-weight:600; }
.meta { color:var(--muted); font-size:12.5px; }
tr.bench .nm, tr.bench .num, tr.best .num { color:var(--muted); }
.tag { font-size:10.5px; font-weight:600; color:var(--muted); border:1px solid var(--line);
  border-radius:4px; padding:0 4px; margin-left:6px; }
.chip { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12.5px;
  font-weight:600; color:#fff; background:var(--c); }
.versus { color:var(--muted); font-size:12.5px; margin-top:6px; }
.notes, footer { color:var(--muted); font-size:12px; }
@media (max-width:600px) { .cards { grid-template-columns:1fr; } }
"""
