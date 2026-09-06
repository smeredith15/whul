#!/usr/bin/env python3
"""How many of the league's images can be fetched, and how many must be found.

The site wants, per the league's spec:

    a team-sport player   headshot, with the club's logo bottom-right
    an individual athlete headshot, with their country's flag bottom-right
    a club or programme   team logo, with the league's logo bottom-right
    an international squad the confederation's shield
    an Olympic entry      the country's flag, with the rings bottom-right

That is several hundred files. Some of them ESPN publishes on a predictable
URL, some it publishes on an unpredictable one that has to be looked up, and
some it does not have at all. Which is which decides whether this is an
afternoon of downloads or a month of hunting, and guessing at the split is how
a month of hunting gets committed to by accident.

So this asks. It resolves every rostered asset against ESPN, checks that the
image URL it derives actually answers, and prints what is missing **by name**,
because "83% found" is not a task list and "these fourteen players" is.

    python scripts/probe-images.py --db data/whul.sqlite3

Nothing is downloaded and nothing is written into the repository. The only
output is the report and, with --json, a machine-readable copy of every URL
that answered -- which is what a later fetch step would read.

RUN IT WHERE ESPN IS REACHABLE. A sandbox whose egress is filtered answers 403
to the CONNECT and every single check fails identically, which looks exactly
like ESPN having no images at all. The nightly workflow's runner can reach it;
`.github/workflows/probe-images.yml` is this script wired to a button.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

BASE = "https://site.api.espn.com/apis/site/v2/sports"
SEARCH = "https://site.web.api.espn.com/apis/search/v2"
WEB = "https://site.web.api.espn.com/apis/common/v3/sports"
TIMEOUT = 25
PAUSE = 0.25          # ESPN publishes no rate limit; be a considerate client

#: Our league label -> ESPN's (sport, league) path. Only what is rostered.
#: Deliberately not imported from whul.sources.espn: that map exists to score
#: results and is missing every league scored from somewhere else, and a probe
#: that quietly skipped the NFL because another module had no use for its path
#: would report a gap that is not there.
ESPN_PATH = {
    "NFL": ("football", "nfl"),
    "NBA": ("basketball", "nba"),
    "MLB": ("baseball", "mlb"),
    "NHL": ("hockey", "nhl"),
    "NCAAF": ("football", "college-football"),
    "NCAAM": ("basketball", "mens-college-basketball"),
    "NCAAW": ("basketball", "womens-college-basketball"),
    "NCAA Baseball": ("baseball", "college-baseball"),
    "NCAA Softball": ("baseball", "college-softball"),
    "Premier League": ("soccer", "eng.1"),
    "La Liga": ("soccer", "esp.1"),
    "Serie A": ("soccer", "ita.1"),
    "Bundesliga": ("soccer", "ger.1"),
    "Ligue 1": ("soccer", "fra.1"),
    "MLS": ("soccer", "usa.1"),
    "NWSL": ("soccer", "usa.nwsl"),
    "PGA": ("golf", "pga"),
    "Tennis": ("tennis", "atp"),
    "ATP": ("tennis", "atp"),
    "WTA": ("tennis", "wta"),
    "F1": ("racing", "f1"),
    "NASCAR": ("racing", "nascar-premier"),
    "Motorsports": ("racing", "f1"),
    "Men's Intl Soccer": ("soccer", "fifa.world"),
    "Women's Intl Soccer": ("soccer", "fifa.wwc"),
}

#: Categories whose players are individuals rather than club employees. These
#: take a country flag where a footballer takes a club badge.
INDIVIDUAL = {"Tennis", "PGA", "Motorsports"}

#: Confederation shields are not ESPN's to serve and are not looked for here.
#: Six files, once, by hand -- which is the answer this probe would reach
#: anyway, at the cost of sixty requests to find out.
CONFEDERATIONS = ("UEFA", "CONMEBOL", "CONCACAF", "CAF", "AFC", "OFC")


def normalize(name: str, team: bool) -> str:
    """The project's own name key, so this matches the way ingest matches."""
    from whul.resolve import normalize_team, split_name

    return normalize_team(name) if team else split_name(name)[0]


class Checker:
    """HEAD every URL once, and remember the answer.

    A club's logo is the badge on twenty players and the main image on the club
    itself; the league's logo is on every one of its teams. Without this the
    probe asks ESPN the same question a hundred times.
    """

    def __init__(self, session: requests.Session) -> None:
        self.session = session
        self.seen: dict[str, bool] = {}
        self.calls = 0

    def ok(self, url: str) -> bool:
        if not url:
            return False
        if url in self.seen:
            return self.seen[url]
        try:
            reply = self.session.head(url, timeout=TIMEOUT, allow_redirects=True)
            # ESPN answers 200 with a 1x1 "no photo" silhouette for some
            # athletes rather than 404ing, and that is a miss dressed as a hit.
            # Judged on size: a real headshot is tens of kilobytes.
            length = int(reply.headers.get("content-length") or 0)
            good = reply.status_code == 200 and length > 2000
        except Exception:
            good = False
        self.seen[url] = good
        self.calls += 1
        time.sleep(PAUSE)
        return good


def get(session: requests.Session, url: str, params: dict | None = None):
    try:
        reply = session.get(url, params=params or {}, timeout=TIMEOUT)
        reply.raise_for_status()
        time.sleep(PAUSE)
        return reply.json()
    except Exception as exc:
        print(f"    ! {type(exc).__name__} on {url}", flush=True)
        return None


def rostered(db: sqlite3.Connection, season: str) -> list[dict]:
    """Every filled slot: what it is, what league, and which category holds it."""
    rows = db.execute(
        "SELECT DISTINCT a.asset_id, a.asset_type, a.league, a.display_name, "
        "       r.category "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE r.season = ?", (season,),
    ).fetchall()
    return [
        {"id": r[0], "type": r[1], "league": str(r[2] or ""),
         "name": str(r[3] or ""), "category": str(r[4] or "")}
        for r in rows
    ]


_PAYLOADS: dict[str, dict] = {}


def league_payload(session: requests.Session, league: str) -> dict:
    """One league's teams endpoint, fetched once.

    Both the club logos and the competition's own mark come out of this
    response, and it is a large one. Fetching it twice a league was the first
    version and doubled the run for nothing.
    """
    if league in _PAYLOADS:
        return _PAYLOADS[league]
    path = ESPN_PATH.get(league)
    payload = {}
    if path:
        payload = get(
            session, f"{BASE}/{path[0]}/{path[1]}/teams", {"limit": 1000}
        ) or {}
    _PAYLOADS[league] = payload
    return payload


def team_index(session: requests.Session, league: str) -> dict[str, dict]:
    """``{normalized name: {id, logo}}`` for one league.

    The teams endpoint is the reliable path for a club: it gives ESPN's own id
    and its own logo URL, so nothing has to be guessed from an abbreviation.
    Athletes have no such endpoint and go through search instead.
    """
    payload = league_payload(session, league)
    if not payload:
        return {}
    out: dict[str, dict] = {}
    try:
        entries = payload["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError, TypeError):
        print(f"    ! {league}: teams payload was not the shape expected; "
              f"top-level keys {sorted(payload)[:8]}", flush=True)
        return {}
    for entry in entries:
        team = (entry or {}).get("team") or {}
        logos = team.get("logos") or []
        record = {
            "id": str(team.get("id") or ""),
            "logo": str((logos[0] or {}).get("href", "")) if logos else "",
        }
        for name in (team.get("displayName"), team.get("shortDisplayName"),
                     team.get("name"), team.get("location")):
            if name:
                out.setdefault(normalize(str(name), team=True), record)
    return out


def league_logo(session: requests.Session, league: str) -> str:
    """The competition's own mark, off the same teams payload."""
    payload = league_payload(session, league)
    try:
        logos = payload["sports"][0]["leagues"][0].get("logos") or []
        return str((logos[0] or {}).get("href", "")) if logos else ""
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def find_athlete(session: requests.Session, name: str, league: str) -> dict:
    """ESPN's id, headshot and flag for one athlete, via search.

    Search rather than a per-league athlete list because the lists are large,
    paginated and shaped differently per sport, and this is a one-off count
    rather than something the nightly run does.
    """
    payload = get(session, SEARCH, {"query": name, "limit": 8})
    if not payload:
        return {}
    wanted = (ESPN_PATH.get(league) or ("", ""))[1]
    best: dict = {}
    for group in payload.get("results") or []:
        if str(group.get("type")) not in ("player", "athlete"):
            continue
        for item in group.get("contents") or []:
            record = {
                "id": str(item.get("id") or ""),
                "image": str((item.get("image") or {}).get("default", "")
                             if isinstance(item.get("image"), dict)
                             else item.get("image") or ""),
                "subtitle": str(item.get("subtitle") or ""),
                "link": str((item.get("link") or {}).get("web", "")
                            if isinstance(item.get("link"), dict) else ""),
            }
            if not best:
                best = record          # first athlete, whatever league
            # A name can belong to several sports. Prefer the one whose link
            # names the league we drafted them from.
            if wanted and wanted.split(".")[0] in record["link"]:
                return record
    return best


def athlete_flag(session: requests.Session, league: str, athlete_id: str) -> str:
    """The country flag ESPN carries for an individual athlete, where it does.

    Tennis and golf pages show one beside the name, which is exactly the badge
    the league's spec asks for on these. Team-sport athletes have a club
    instead and are not asked.
    """
    path = ESPN_PATH.get(league)
    if not path or not athlete_id:
        return ""
    payload = get(session, f"{WEB}/{path[0]}/{path[1]}/athletes/{athlete_id}")
    if not payload:
        return ""
    for holder in (payload, payload.get("athlete") or {}):
        flag = holder.get("flag") if isinstance(holder, dict) else None
        if isinstance(flag, dict) and flag.get("href"):
            return str(flag["href"])
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/whul.sqlite3")
    parser.add_argument("--season", default="2026-27")
    parser.add_argument("--json", help="write every URL that answered here")
    parser.add_argument("--limit", type=int, default=0,
                        help="only this many assets, for a quick shape check")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"No database at {args.db}.", file=sys.stderr)
        return 1

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    assets = rostered(db, args.season)
    if args.limit:
        assets = assets[:args.limit]
    if not assets:
        print(f"Nothing rostered in {args.season}.")
        return 1

    session = requests.Session()
    check = Checker(session)
    found: dict[str, dict] = {}
    misses: dict[str, list[str]] = defaultdict(list)
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])   # [found, needed]

    def record(kind: str, key: str, url: str, label: str) -> None:
        tally[kind][1] += 1
        if url and check.ok(url):
            tally[kind][0] += 1
            found.setdefault(kind, {})[key] = url
        else:
            misses[kind].append(label)

    leagues = sorted({a["league"] for a in assets if a["league"]})

    print(f"\nProbing {len(assets)} rostered asset(s) across {len(leagues)} "
          f"league(s).\nEach line is one request or better; this takes a while.\n")

    # --- teams, and the league marks that badge them ------------------------
    print("Team logos and league logos")
    indexes = {league: team_index(session, league) for league in leagues}
    for league in leagues:
        url = league_logo(session, league)
        record("league logo", league, url, league)

    for asset in assets:
        if asset["type"] != "Team":
            continue
        if "Intl" in asset["category"]:
            # A national side. Its confederation shield is not ESPN's to serve;
            # counted separately and not asked for here.
            continue
        entry = indexes.get(asset["league"], {}).get(
            normalize(asset["name"], team=True), {})
        record("team logo", asset["id"], entry.get("logo", ""),
               f"{asset['name']} ({asset['league']})")

    # --- players ------------------------------------------------------------
    print("\nHeadshots, and the badge each one wears")
    for asset in assets:
        if asset["type"] != "Player":
            continue
        who = find_athlete(session, asset["name"], asset["league"])
        record("headshot", asset["id"], who.get("image", ""),
               f"{asset['name']} ({asset['league']})")

        if asset["category"] in INDIVIDUAL:
            record("athlete flag", asset["id"],
                   athlete_flag(session, asset["league"], who.get("id", "")),
                   f"{asset['name']} ({asset['league']})")
        # A team-sport player's badge is his club's logo, which needs his club.
        # That is recorded against the asset only from the first nightly run
        # after the identity carry landed; until then the club set cannot be
        # counted and saying so beats guessing at it.

    # --- what it adds up to -------------------------------------------------
    print(f"\n{'=' * 68}\n{check.calls} image URL(s) checked.\n")
    print(f"  {'what':<16}{'found':>7}{'needed':>8}   by hand")
    for kind in ("headshot", "team logo", "league logo", "athlete flag"):
        got, need = tally[kind]
        if need:
            print(f"  {kind:<16}{got:>7}{need:>8}   {need - got}")

    squads = sum(1 for a in assets if a["type"] == "Team" and "Intl" in a["category"])
    print(f"\n  Not asked of ESPN, and yours to supply either way:")
    print(f"    confederation shields   {len(CONFEDERATIONS)}   "
          f"(for {squads} international squad slots)")
    print(f"    Olympic rings           1   (once the Olympic slots are live)")
    print(f"    club logos for players  ?   one per club a rostered player is at,")
    print(f"                                countable once a nightly run has")
    print(f"                                recorded each player's club")

    for kind in ("headshot", "team logo", "league logo", "athlete flag"):
        if misses[kind]:
            print(f"\n  {kind} — {len(misses[kind])} to find yourself:")
            for label in sorted(misses[kind]):
                print(f"    {label}")

    if args.json:
        Path(args.json).write_text(json.dumps(found, indent=2, sort_keys=True))
        print(f"\n  Every URL that answered is in {args.json}.")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
