"""The weekly recap: one page on Tuesday morning, saying what the week did.

Everything here is read from what the nightly run already stores -- the
standings snapshots, each asset's score by day, the fixtures -- so the recap
costs no request and cannot disagree with the site. It answers the questions a
manager asks over coffee: who won the week, where the quarter and the season
stand, which assets carried it, who led each league, and who meets whom next.

A week is the seven days the standings moved over: from the last snapshot a
week or more before the recap's day to that day. On a Tuesday morning that is
the previous Tuesday's run to this one, which is every game from Tuesday to
Monday -- one whole NFL week, Monday night included. Sent on a Monday it was
Monday to Sunday, so Monday night fell into the following week's recap and a
player who played Monday and then Sunday had two games in one week.

Points are normalized points throughout -- the season scale every asset is
scored on, which is what the standings add up -- so an NFL running back and a
Premier League keeper sit in the same list.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import pandas as pd

from whul import pipeline, records
from whul.config.league import (
    CATEGORY_COMPETITIONS, CROSS_POOL_SOCCER, LEAGUE_ABBR, LEAGUE_NAME, SEASON,
    manager_name, quarter_of, quarters,
)
from whul.store.db import Store

#: How far back the week reaches.
WEEK_DAYS = 7

#: Rows in the week's top assets.
TOP_ASSETS = 10

#: Assets shown under each roster category. The club soccer categories each
#: span three leagues, so they get two places a league.
PER_CATEGORY = 3
PER_CATEGORY_WIDE = {"Club Soccer Top 3": 6, "Club Soccer Other": 6}

#: Roster categories that span several leagues. Their lists name each asset's
#: league every week -- not only in a week whose best came from more than one,
#: which left Club Soccer Other bare the week Bundesliga took all six places.
MIXED_CATEGORIES = frozenset(CATEGORY_COMPETITIONS) | frozenset(CROSS_POOL_SOCCER)

#: Head-to-head fixtures listed for the week ahead.
RIVALRIES = 10

#: A gain smaller than this is a rounding difference, not a week's work.
MOVED = 0.05


@dataclass(frozen=True)
class Standing:
    """One manager's line in each of the three tables."""

    manager: str
    week: float
    week_rank: int
    quarter: float
    quarter_rank: int
    total: float
    rank: int
    rank_before: int | None
    behind: float
    held: float = 0.0

    @property
    def name(self) -> str:
        return manager_name(self.manager)

    @property
    def climbed(self) -> int:
        """Places gained in the season table this week; negative for a fall."""
        return 0 if self.rank_before is None else self.rank_before - self.rank


@dataclass(frozen=True)
class AssetWeek:
    """What one rostered asset added this week."""

    asset_id: str
    name: str
    league: str
    manager: str
    gained: float
    total: float
    counts: bool
    #: The roster category the asset is drafted in -- "Tennis" for ATP and
    #: WTA alike -- which is how the recap groups them, as the rosters do.
    category: str = ""


@dataclass
class Recap:
    season: str
    as_of: date
    since: date | None
    first_day: date
    last_day: date
    week_number: int
    managers: list[str]
    standings: list[Standing]
    quarter_label: str
    quarter_end: date | None
    closed_quarter: tuple[str, list[tuple[str, float]]] | None
    top_assets: list[AssetWeek]
    by_category: dict[str, list[AssetWeek]]
    best_bench: AssetWeek | None
    games_ahead: dict[str, int] = field(default_factory=dict)
    rivalries: list[dict] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{LEAGUE_ABBR} Week {self.week_number} recap"

    def headline(self) -> str:
        """One sentence for the top of an email or a notification."""
        top = min(self.standings, key=lambda s: s.week_rank)
        lead = min(self.standings, key=lambda s: s.rank)
        said = f"{top.name} won the week ({_signed(top.week)})"
        if lead.manager != top.manager:
            said += f"; {lead.name} leads the season"
        else:
            said += " and leads the season"
        if self.top_assets:
            best = self.top_assets[0]
            said += (f". Asset of the week: {best.name} "
                     f"({_signed(best.gained)}, {manager_name(best.manager)})")
        return said + "."


def _signed(value: float) -> str:
    return f"{value:+.1f}"


def _ranks(values: dict[str, float]) -> dict[str, int]:
    order = sorted(values, key=lambda m: (-values[m], manager_name(m)))
    return {m: i for i, m in enumerate(order, start=1)}


def build(store: Store, season: str, as_of: date | None = None) -> Recap:
    """The recap for the week ending on ``as_of`` (the latest day by default)."""
    progression = pipeline.progression(store, season)
    if progression.empty:
        raise ValueError(f"no standings for {season} yet, so there is no week to recap")
    days = sorted(set(progression["as_of"]))
    if as_of is not None:
        days = [d for d in days if d <= as_of]
        if not days:
            raise ValueError(f"no standings for {season} on or before {as_of}")
    latest = days[-1]
    earlier = [d for d in days if d <= latest - timedelta(days=WEEK_DAYS)]
    since = earlier[-1] if earlier else None

    def at(day):
        if day is None:
            return {}
        rows = progression[progression["as_of"] == day]
        return {str(r.manager_id): (float(r.total), int(r.rank)) for r in rows.itertuples()}

    now, then = at(latest), at(since)
    managers = sorted(now)
    week = {m: now[m][0] - then.get(m, (0.0, None))[0] for m in managers}

    quarter = quarter_of(latest)
    by_quarter = records.quarter_totals(store, season, as_of=latest)
    gained_q: dict[str, float] = {m: 0.0 for m in managers}
    closed = None
    if quarter is not None and not by_quarter.empty:
        mine = by_quarter[by_quarter["quarter"] == quarter.label]
        gained_q.update({str(r.manager_id): float(r.gained) for r in mine.itertuples()})
        # A quarter that ended inside this week is reported as final, once.
        order = [q.label for q in quarters()]
        at_index = order.index(quarter.label)
        if since is not None and at_index > 0 and since < quarter.start:
            previous = order[at_index - 1]
            done = by_quarter[by_quarter["quarter"] == previous]
            if not done.empty:
                closed = (previous, sorted(
                    ((str(r.manager_id), float(r.gained)) for r in done.itertuples()),
                    key=lambda pair: -pair[1]))

    try:
        held = pipeline.held_by_manager(store, season, latest)
    except Exception:  # noqa: BLE001 -- a figure on the side must not stop the page
        held = {}

    week_rank, quarter_rank = _ranks(week), _ranks(gained_q)
    leader = max(total for total, _ in now.values())
    standings = sorted(
        (Standing(
            manager=m, week=week[m], week_rank=week_rank[m],
            quarter=gained_q.get(m, 0.0), quarter_rank=quarter_rank[m],
            total=now[m][0], rank=now[m][1],
            rank_before=then.get(m, (None, None))[1],
            behind=leader - now[m][0], held=float(held.get(m, 0.0)))
         for m in managers),
        key=lambda s: s.rank)

    assets = asset_weeks(store, season, since, latest)
    moved = [a for a in assets if abs(a.gained) >= MOVED]
    top = sorted(moved, key=lambda a: -a.gained)[:TOP_ASSETS]
    by_category: dict[str, list[AssetWeek]] = {}
    for item in sorted(moved, key=lambda a: -a.gained):
        if item.gained <= 0:
            break
        group = item.category or item.league
        if len(by_category.setdefault(group, [])) < PER_CATEGORY_WIDE.get(group, PER_CATEGORY):
            by_category[group].append(item)
    bench = [a for a in moved if not a.counts and a.gained > 0]

    games, rivals = week_ahead(store, season, latest)
    return Recap(
        season=season, as_of=latest, since=since,
        # The days the week's games were played on: a run on a morning carries
        # the night before, so the week since last Tuesday's run is Tuesday
        # to Monday.
        first_day=since if since is not None else days[0],
        last_day=latest - timedelta(days=1) if since is not None else latest,
        week_number=max(1, math.ceil(((latest - SEASON.start).days + 1) / WEEK_DAYS)),
        managers=managers, standings=standings,
        quarter_label=quarter.label if quarter else "",
        quarter_end=quarter.end if quarter else None,
        closed_quarter=closed, top_assets=top,
        by_category=dict(sorted(by_category.items(), key=lambda kv: _roster_order(kv[0]))),
        best_bench=max(bench, key=lambda a: a.gained) if bench else None,
        games_ahead=games, rivalries=rivals,
    )


def _roster_order(category: str) -> tuple[int, str]:
    """Where a category sits on a roster page: teams, then players."""
    from whul.config.league import ALL_SLOTS

    order = list(dict.fromkeys(group.category for group in ALL_SLOTS))
    return (order.index(category) if category in order else len(order), category)


def asset_weeks(store: Store, season: str, since: date | None,
                latest: date) -> list[AssetWeek]:
    """Every rostered asset's normalized points over the week.

    Each asset's own latest score on either day, so a league that was not
    pulled on one of them still counts its week. An asset with no score by
    ``since`` started the week from nothing.
    """
    now = store.latest_scores(season, latest)
    if now.empty:
        return []
    before = store.latest_scores(season, since) if since is not None else now.iloc[0:0]
    was = dict(zip(before["asset_id"], pd.to_numeric(before["scaled_score"],
                                                     errors="coerce").fillna(0.0)))
    owned = store.query(
        "SELECT o.slot_id, o.asset_id, r.manager_id, r.category, a.display_name, a.league "
        "FROM slot_occupancy o JOIN roster_slots r ON r.slot_id = o.slot_id "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE r.season = ? AND o.start_date <= ? "
        "AND (o.end_date IS NULL OR o.end_date >= ?)",
        (season, latest.isoformat(), latest.isoformat()))
    counting = store.query(
        "SELECT slot_id, asset_id, counts FROM slot_scores WHERE season = ? AND as_of = ?",
        (season, latest.isoformat()))
    counts = {(str(r.slot_id), str(r.asset_id)): bool(r.counts)
              for r in counting.itertuples()}
    score = dict(zip(now["asset_id"], pd.to_numeric(now["scaled_score"],
                                                    errors="coerce").fillna(0.0)))
    out = []
    for row in owned.itertuples():
        asset = str(row.asset_id)
        if asset not in score:
            continue
        out.append(AssetWeek(
            asset_id=asset, name=str(row.display_name), league=str(row.league),
            manager=str(row.manager_id), gained=float(score[asset] - was.get(asset, 0.0)),
            total=float(score[asset]),
            counts=counts.get((str(row.slot_id), asset), False),
            category=str(row.category or "")))
    return out


def week_ahead(store: Store, season: str, latest: date) -> tuple[dict[str, int], list[dict]]:
    """Games each manager has in the next seven days, and the ones between two."""
    from whul import fixtures

    try:
        board = fixtures.board(store, season, latest, days=WEEK_DAYS)
        owner = fixtures.owners(store, season)
    except Exception:  # noqa: BLE001 -- the week ahead is a garnish
        return {}, []
    # One per asset per fixture: a manager with both sides of a tie plays twice.
    games: dict[str, int] = {}
    rivals = []
    for entry in board:
        for side in entry["sides"]:
            for asset in side["assets"]:
                who = owner.get(asset, "")
                if who:
                    games[who] = games.get(who, 0) + 1
        if entry["event"]:
            continue
        # Two drafted teams meeting, held by different managers. Players are
        # left out: with four or five managers holding someone at every big
        # club, nearly every match would qualify and the list would say
        # nothing.
        clubs = [{owner.get(a, "") for a in side["assets"] if a.startswith("team-")} - {""}
                 for side in entry["sides"][:2]]
        if len(clubs) == 2 and clubs[0] and clubs[1] and clubs[0] != clubs[1]:
            rivals.append({**entry, "clubs": clubs})
    return games, rivals[:RIVALRIES]


# --- the page ----------------------------------------------------------------

def _colour(managers: list[str], manager: str) -> str:
    from whul.site.theme import SERIES_LIGHT, series_index

    if manager not in managers:
        return "#8a8f98"
    return SERIES_LIGHT[series_index(managers, manager)]


def _chip(recap: Recap, manager: str) -> str:
    return (f'<span class="chip" style="--c:{_colour(recap.managers, manager)}">'
            f"{html.escape(manager_name(manager))}</span>")


def _competition(entry: dict) -> str:
    """What a head-to-head is part of. A harvested league's own name -- its
    "competition" is the game's billing, "UConn Huskies at Michigan Wolverines"
    -- and Flashscore's competition, since its feed is a sport, not a league."""
    feed = str(entry.get("feed") or "")
    return str(entry.get("competition") or feed) if feed.startswith("Flashscore") else feed


def _day(value: date) -> str:
    return f"{value:%a %b} {value.day}"


def _move(standing: Standing) -> str:
    if standing.rank_before is None or not standing.climbed:
        return '<span class="mv flat">–</span>'
    arrow, kind = ("▲", "up") if standing.climbed > 0 else ("▼", "down")
    return f'<span class="mv {kind}">{arrow}{abs(standing.climbed)}</span>'


def _table(recap: Recap, title: str, rows, figure, rank, extra=None) -> str:
    body = "".join(
        f"<tr><td class='rk'>{rank(s)}</td><td>{_chip(recap, s.manager)}</td>"
        f"<td class='num'>{figure(s)}</td>"
        + (f"<td class='num sub'>{extra(s)}</td>" if extra else "")
        + "</tr>"
        for s in rows)
    return f"<section class='card'><h2>{title}</h2><table>{body}</table></section>"


def _asset_rows(recap: Recap, items: list[AssetWeek], numbered: bool,
                leagues: bool = True) -> str:
    """``leagues`` names each asset's league under it, which a category of one
    league would only repeat."""
    out = []
    for i, item in enumerate(items, start=1):
        bench = "" if item.counts else "<span class='tag'>bench</span>"
        league = (f"<div class='meta'>{html.escape(item.league)}</div>"
                  if leagues else "")
        out.append(
            "<tr>" + (f"<td class='rk'>{i}</td>" if numbered else "")
            + f"<td><div class='asset'>{html.escape(item.name)}{bench}</div>"
            f"{league}</td>"
            f"<td>{_chip(recap, item.manager)}</td>"
            f"<td class='num'>{_signed(item.gained)}</td></tr>")
    return "".join(out)


def render(recap: Recap, site_url: str = "") -> str:
    """The recap as one self-contained page, sized for a phone screen."""
    s = recap.standings
    week_order = sorted(s, key=lambda x: x.week_rank)
    quarter_order = sorted(s, key=lambda x: x.quarter_rank)
    top = week_order[0]
    climber = max(s, key=lambda x: x.climbed)

    span = (f"{_day(recap.first_day)} – {_day(recap.last_day)}"
            if recap.since is not None else f"through {_day(recap.as_of)}")
    tiles = [
        ("Manager of the week", _chip(recap, top.manager), _signed(top.week)),
    ]
    if recap.top_assets:
        best = recap.top_assets[0]
        tiles.append(("Asset of the week", html.escape(best.name),
                      f"{_signed(best.gained)} · {html.escape(manager_name(best.manager))}"))
    if climber.climbed > 0:
        tiles.append(("Biggest climber", _chip(recap, climber.manager),
                      f"▲{climber.climbed} to #{climber.rank}"))
    elif recap.best_bench is not None:
        b = recap.best_bench
        tiles.append(("Best on the bench", html.escape(b.name),
                      f"{_signed(b.gained)} · {html.escape(manager_name(b.manager))}"))
    tile_html = "".join(
        f"<div class='tile'><div class='lbl'>{label}</div><div class='big'>{value}</div>"
        f"<div class='sub'>{note}</div></div>" for label, value, note in tiles)

    days_left = ((recap.quarter_end - recap.as_of).days
                 if recap.quarter_end and recap.quarter_end >= recap.as_of else None)
    quarter_title = (f"{recap.quarter_label} so far"
                     + (f" <span class='hint'>{days_left} days left</span>"
                        if days_left is not None else ""))
    held_any = any(x.held for x in s)
    tables = (
        _table(recap, "This week", week_order, lambda x: _signed(x.week),
               lambda x: x.week_rank)
        + _table(recap, quarter_title, quarter_order, lambda x: f"{x.quarter:.1f}",
                 lambda x: x.quarter_rank)
        + _table(recap, "Season", s, lambda x: f"{x.total:.1f}",
                 lambda x: f"{x.rank}{_move(x)}",
                 (lambda x: f"+{x.held:.1f} held" if x.held else "")
                 if held_any else
                 (lambda x: f"−{x.behind:.1f}" if x.behind else "leader")))

    closed = ""
    if recap.closed_quarter:
        label, rows = recap.closed_quarter
        body = "".join(
            f"<tr><td class='rk'>{i}</td><td>{_chip(recap, m)}</td>"
            f"<td class='num'>{g:.1f}</td></tr>" for i, (m, g) in enumerate(rows, start=1))
        closed = (f"<section class='card wide final'><h2>{label} final — "
                  f"{html.escape(manager_name(rows[0][0]))} takes the quarter</h2>"
                  f"<table>{body}</table></section>")

    assets = (f"<section class='card wide'><h2>Top assets of the week "
              f"<span class='hint'>normalized points</span></h2>"
              f"<table class='assets'>{_asset_rows(recap, recap.top_assets, True)}</table>"
              f"</section>") if recap.top_assets else ""

    leagues = "".join(
        f"<div class='league'><h3>{html.escape(group)}</h3><table class='assets'>"
        f"{_asset_rows(recap, items, False, group in MIXED_CATEGORIES)}"
        f"</table></div>"
        for group, items in recap.by_category.items())
    leagues = (f"<section class='card wide'><h2>Best in each league</h2>"
               f"<div class='leagues'>{leagues}</div></section>") if leagues else ""

    ahead = ""
    if recap.games_ahead or recap.rivalries:
        counts = "".join(
            f"<span class='count'>{_chip(recap, m)} {recap.games_ahead.get(m, 0)}</span>"
            for m in sorted(recap.managers, key=lambda m: -recap.games_ahead.get(m, 0)))
        rows = "".join(
            f"<tr><td class='meta'>{_day(date.fromisoformat(e['date']))}</td>"
            f"<td>{html.escape(e['sides'][0]['name'])} "
            + "".join(_chip(recap, o) for o in sorted(e['clubs'][0]))
            + f" <span class='vs'>vs</span> {html.escape(e['sides'][1]['name'])} "
            + "".join(_chip(recap, o) for o in sorted(e['clubs'][1]))
            + f"</td><td class='meta'>{html.escape(_competition(e))}</td></tr>"
            for e in recap.rivalries)
        ahead = (f"<section class='card wide'><h2>The week ahead</h2>"
                 f"<div class='counts'><span class='meta'>Games on the slate:</span>"
                 f"{counts}</div>"
                 + (f"<h3>Head to head</h3><table>{rows}</table>" if rows else "")
                 + "</section>")

    link = (f"<a href='{html.escape(site_url)}'>{html.escape(site_url)}</a>"
            if site_url else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(recap.title)}</title>
<style>{STYLE}</style></head>
<body><main>
<header><div class="kicker">{html.escape(LEAGUE_NAME)}</div>
<h1>Week {recap.week_number} recap</h1>
<div class="span">{span} · standings through {_day(recap.as_of)}</div></header>
<div class="tiles">{tile_html}</div>
<div class="three">{tables}</div>
{closed}{assets}{leagues}{ahead}
<footer>Normalized points: the season scale every asset is scored on, which the
standings add up. Held: postseason and European bonuses waiting on their
competition to finish. {link}</footer>
</main></body></html>"""


STYLE = """
:root { --bg:#f4f5f7; --card:#ffffff; --ink:#16181d; --muted:#626873;
  --line:#e3e5e9; --up:#13804f; --down:#c23b32; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.4 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { width:760px; max-width:100%; margin:0 auto; padding:20px 16px 24px; }
header { margin-bottom:14px; }
.kicker { font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
h1 { margin:2px 0 2px; font-size:28px; }
.span { color:var(--muted); font-size:14px; }
.tiles { display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:10px; margin-bottom:12px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
.tile .lbl { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
.tile .big { font-size:18px; font-weight:650; margin:4px 0 2px; overflow-wrap:anywhere; }
.tile .sub { font-size:13px; color:var(--muted); }
.three { display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:10px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; margin-bottom:12px; }
.card.final { border-color:#d9b44a; background:#fffaf0; }
h2 { font-size:15px; margin:0 0 8px; }
h3 { font-size:13px; margin:8px 0 4px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
.hint { font-weight:400; color:var(--muted); font-size:12px; margin-left:4px; }
table { width:100%; border-collapse:collapse; }
td { padding:4px 4px; border-top:1px solid var(--line); vertical-align:middle; }
tr:first-child td { border-top:0; }
.rk { width:30px; color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }
.num { text-align:right; font-variant-numeric:tabular-nums; font-weight:600; white-space:nowrap; }
.num.sub { font-weight:400; color:var(--muted); font-size:12px; }
.chip { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12.5px;
  font-weight:600; color:#fff; background:var(--c); margin-right:3px; white-space:nowrap; }
.mv { font-size:11px; margin-left:3px; }
.mv.up { color:var(--up); } .mv.down { color:var(--down); } .mv.flat { color:var(--muted); }
.asset { font-weight:600; }
.meta { color:var(--muted); font-size:12.5px; }
.tag { font-size:10.5px; font-weight:600; color:var(--muted); border:1px solid var(--line);
  border-radius:4px; padding:0 4px; margin-left:6px; vertical-align:1px; }
.leagues { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:4px 16px; }
.league h3 { margin-top:6px; }
.counts { display:flex; flex-wrap:wrap; gap:6px 10px; align-items:center; margin-bottom:4px; }
.vs { color:var(--muted); font-size:12px; margin:0 2px; }
footer { color:var(--muted); font-size:12px; margin-top:4px; }
footer a { color:var(--muted); }
@media (max-width:600px) { .tiles, .three, .leagues { grid-template-columns:1fr; } }
"""


# --- delivery ----------------------------------------------------------------

def to_png(page: str, path, executable: str | None = None) -> None:
    """The page as one image, at twice the pixels for a phone screen.

    Playwright's Chromium, which the recap workflow installs. ``executable``
    points it at a browser already on the machine instead.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        browser = play.chromium.launch(**({"executable_path": executable}
                                          if executable else {}))
        try:
            view = browser.new_page(viewport={"width": 760, "height": 800},
                                    device_scale_factor=2)
            view.set_content(page, wait_until="load")
            view.screenshot(path=str(path), full_page=True)
        finally:
            browser.close()


def send_email(recap: Recap, page: str, png: bytes | None, settings: dict,
               site_url: str = "") -> str:
    """Mail the recap: the image in the body, the page attached.

    ``settings`` carries ``host``, ``port``, ``user``, ``password``, ``to``
    (comma separated) and optionally ``from``. Port 465 is SMTP over TLS,
    anything else starts TLS on a plain connection -- Gmail answers on both.
    Returns who it went to.
    """
    import smtplib
    import ssl
    from email.message import EmailMessage

    to = [a.strip() for a in str(settings["to"]).split(",") if a.strip()]
    if not to:
        raise ValueError("no recipient: set RECAP_TO")
    message = EmailMessage()
    message["Subject"] = f"{recap.title}: {_day(recap.first_day)} – {_day(recap.last_day)}"
    message["From"] = settings.get("from") or settings["user"]
    message["To"] = ", ".join(to)
    line = recap.headline()
    link = f"\n\n{site_url}" if site_url else ""
    message.set_content(f"{line}{link}\n\nThe full recap is attached.")
    body = f"<p style='font:15px/1.4 sans-serif'>{html.escape(line)}</p>"
    if png:
        body += "<img src='cid:recap' alt='Weekly recap' style='max-width:100%'>"
    if site_url:
        body += f"<p style='font:13px sans-serif'><a href='{html.escape(site_url)}'>Open the site</a></p>"
    message.add_alternative(body, subtype="html")
    if png:
        message.get_payload()[1].add_related(png, maintype="image", subtype="png",
                                             cid="<recap>", filename="recap.png")
    message.add_attachment(page.encode(), maintype="text", subtype="html",
                           filename=f"whul-week-{recap.week_number}-recap.html")

    port = int(settings.get("port") or 465)
    context = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(settings["host"], port, context=context, timeout=60)
    else:
        server = smtplib.SMTP(settings["host"], port, timeout=60)
        server.starttls(context=context)
    with server:
        server.login(settings["user"], settings["password"])
        server.send_message(message)
    return ", ".join(to)


def send_push(recap: Recap, png: bytes | None, topic: str,
              server: str = "https://ntfy.sh", site_url: str = "") -> None:
    """Push the recap to a phone through ntfy, the image attached.

    ntfy is free and needs no account: install its app, subscribe to a topic
    name nobody would guess, and anything sent to that topic arrives as a
    notification. The topic name is the only secret, so it is kept as one.
    """
    import requests

    # As query parameters rather than headers: a header must be Latin-1, and
    # a recap names Jokić and Mbappé.
    params = {"title": recap.title, "message": recap.headline(), "tags": "trophy"}
    if site_url:
        params["click"] = site_url
    url = f"{server.rstrip('/')}/{topic}"
    if png:
        params["filename"] = "recap.png"
        reply = requests.put(url, data=png, params=params, timeout=60)
    else:
        reply = requests.post(url, data=params.pop("message").encode(),
                              params=params, timeout=60)
    reply.raise_for_status()


# --- when to send ------------------------------------------------------------

#: The weekday the recap goes out on, as ``date.weekday()`` counts: Tuesday,
#: so the week it reports ends with Monday night's game.
SEND_ON = 1

#: The earliest Eastern time a Publish may start and still send it. Monday
#: night's game ends around midnight and its player lines take hours to reach
#: the feed: the scheduled run on 3 November 2026 (10:27 Eastern) had Seattle's
#: Monday night, and a manual run at 00:40 that morning most likely did not. The
#: scheduled run is set for 03:37 and GitHub has started it between about 9:30
#: and 13:00, so this costs it nothing; it only keeps a run just after
#: midnight from sending a week without its last game.
SEND_AFTER = time(6, 0)


def _eastern(stamp: str) -> datetime:
    from whul.clock import EASTERN

    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(EASTERN)


def _eligible(run: dict) -> bool:
    """Built, and started on the sending day after ``SEND_AFTER`` Eastern."""
    when = _eastern(run["created_at"])
    return bool(run.get("built")) and when.weekday() == SEND_ON \
        and when.time() >= SEND_AFTER


def first_of_the_week(runs: list[dict], run_id: int) -> bool:
    """Is ``run_id`` the Publish the week's recap goes out after?

    The first of an Eastern Tuesday that started after ``SEND_AFTER`` and
    built. ``runs`` are that workflow's runs, each with ``id``, ``created_at``
    and ``built`` -- whether its build job succeeded, which is what wrote the
    database the recap reads. Built, not succeeded: the falls check runs after
    the deploy and can turn a run red whose standings are perfectly good, and
    that run is still the one that published Tuesday's page.
    """
    mine = next((r for r in runs if int(r["id"]) == int(run_id)), None)
    if mine is None or not _eligible(mine):
        return False
    day = _eastern(mine["created_at"]).date()
    same_day = [r for r in runs if _eligible(r)
                and _eastern(r["created_at"]).date() == day]
    first = min(same_day, key=lambda r: (_eastern(r["created_at"]), int(r["id"])))
    return int(first["id"]) == int(run_id)


def publish_runs(repo: str, token: str, around: str,
                 workflow: str = "publish.yml") -> list[dict]:
    """The Publish runs created on the Eastern day of ``around``, with
    ``built`` filled in from each run's build job."""
    import requests

    from whul.clock import EASTERN

    day = _eastern(around).date()
    start = datetime(day.year, day.month, day.day, tzinfo=EASTERN)
    end = start + timedelta(days=1)
    api = f"https://api.github.com/repos/{repo}"
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}",
                            "Accept": "application/vnd.github+json"})
    reply = session.get(f"{api}/actions/workflows/{workflow}/runs", timeout=30, params={
        "created": f"{start.isoformat()}..{end.isoformat()}", "per_page": 100})
    reply.raise_for_status()
    out = []
    for run in reply.json().get("workflow_runs", []):
        if not start <= _eastern(run["created_at"]) < end:
            continue
        built = run.get("conclusion") == "success"
        if not built and run.get("status") == "completed":
            jobs = session.get(f"{api}/actions/runs/{run['id']}/jobs", timeout=30)
            jobs.raise_for_status()
            built = any(j.get("name") == "build" and j.get("conclusion") == "success"
                        for j in jobs.json().get("jobs", []))
        out.append({"id": run["id"], "created_at": run["created_at"], "built": built})
    return out
