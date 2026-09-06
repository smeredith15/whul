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
from collections import Counter, defaultdict
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


def player_clubs(db: sqlite3.Connection, season: str) -> tuple[set[str], int]:
    """Every club a rostered player is at, and how many need a new logo file.

    A player's corner badge is his club's crest, and most of those clubs are
    drafted teams already -- the same file, twice. Counting the overlap is the
    difference between six files and twenty-two.

    Reads the clubs off the recorded stats rather than off a feed, because that
    is where they are: the ingest carries the club through from the squad now,
    so a nightly run leaves the answer in the database.
    """
    from whul.resolve import normalize_team

    day = db.execute(
        "SELECT MAX(as_of) FROM raw_stats WHERE season = ?", (season,)
    ).fetchone()[0]
    if not day:
        return set(), 0
    clubs: set[str] = set()
    for stats, in db.execute(
        "SELECT r.stats FROM raw_stats r JOIN assets a ON a.asset_id = r.asset_id "
        "WHERE r.as_of = ? AND a.asset_type = 'Player'", (day,),
    ):
        try:
            row = json.loads(stats)
        except (TypeError, ValueError):
            continue
        club = row.get("team") or row.get("team_name")
        if club:
            clubs.add(str(club))
    drafted = {
        normalize_team(str(r[0])) for r in
        db.execute("SELECT display_name FROM assets WHERE asset_type = 'Team'")
    }
    return clubs, sum(1 for c in clubs if normalize_team(c) not in drafted)


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


#: ESPN's own headshot path, once an athlete id is known. Soccer players are
#: absent from the search results' image field but present here, which is the
#: whole reason the squad walk below exists.
HEADSHOT = "https://a.espncdn.com/i/headshots/soccer/players/full/{id}.png"

#: Candidate homes for a competition's own mark, tried in order. The teams
#: endpoint carries `leagues[0].logos` for some sports and not others -- the
#: first version read only that and reported nought out of twenty-four, which
#: is what a wrong lookup and an absent image look like alike. Trying several
#: and naming the winner is how the next run gets to stop guessing.
def league_logo_candidates(league: str, payload: dict) -> list[tuple[str, str]]:
    sport, key = ESPN_PATH.get(league, ("", ""))
    out: list[tuple[str, str]] = []
    try:
        block = payload["sports"][0]["leagues"][0]
    except (KeyError, IndexError, TypeError):
        block = {}
    for logo in (block.get("logos") or []):
        if isinstance(logo, dict) and logo.get("href"):
            out.append(("teams payload", str(logo["href"])))
    if block.get("id"):
        out.append(("leaguelogos by id",
                    f"https://a.espncdn.com/i/leaguelogos/{sport}/500/{block['id']}.png"))
    if key:
        out.append(("teamlogos/leagues by key",
                    f"https://a.espncdn.com/i/teamlogos/leagues/500/{key}.png"))
    return out


def squad_headshots(session: requests.Session, league: str,
                    index: dict[str, dict]) -> dict[str, str]:
    """``{normalized player name: headshot url}`` for one soccer league.

    One request a club. Expensive, and the only path that works: ESPN's search
    returns soccer players without an image, so the first run of this probe
    reported forty-five footballers as having no photograph when every one of
    them has one on the club's own roster page.
    """
    sport, key = ESPN_PATH.get(league, ("", ""))
    if sport != "soccer":
        return {}
    out: dict[str, str] = {}
    for record in {r["id"]: r for r in index.values() if r.get("id")}.values():
        payload = get(session, f"{BASE}/{sport}/{key}/teams/{record['id']}/roster")
        if not payload:
            continue
        for athlete in (payload.get("athletes") or []):
            name = str(athlete.get("displayName") or athlete.get("fullName") or "")
            ident = str(athlete.get("id") or "")
            if name and ident:
                out.setdefault(normalize(name, team=False), HEADSHOT.format(id=ident))
    return out


def numeric_id(record: dict) -> str:
    """ESPN's numeric athlete id, out of the web link the search returns.

    Search answers with a GUID, and the athlete endpoint wants the number --
    every one of the forty flag lookups 404'd on a GUID the first time this
    ran. The number is in the link: .../player/_/id/2452/carlos-alcaraz.
    """
    import re

    found = re.search(r"/id/(\d+)", record.get("link", "") or "")
    return found.group(1) if found else ""


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


#: Printed once, the first time an athlete payload carries no flag, so a run
#: that finds none says what it did find rather than only that it found nothing.
_FLAG_SHAPE_SHOWN = False


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
        if not isinstance(holder, dict):
            continue
        flag = holder.get("flag")
        if isinstance(flag, dict) and flag.get("href"):
            return str(flag["href"])
        # Some sports file the nationality rather than a rendered flag.
        for key in ("citizenship", "birthCountry", "country"):
            value = holder.get(key)
            if isinstance(value, dict) and value.get("flag"):
                inner = value["flag"]
                if isinstance(inner, dict) and inner.get("href"):
                    return str(inner["href"])
    global _FLAG_SHAPE_SHOWN
    if not _FLAG_SHAPE_SHOWN:
        _FLAG_SHAPE_SHOWN = True
        print(f"    (no flag on the athlete payload; its keys are "
              f"{sorted(payload)[:16]})", flush=True)
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

    #: Which candidate answered, per class. A probe that only says "24 missing"
    #: sends someone hunting; one that says "the key path works and the id path
    #: does not" retires a guess.
    winners: dict[str, Counter] = defaultdict(Counter)

    def record(kind: str, key: str, url: str, label: str) -> None:
        tally[kind][1] += 1
        if url and check.ok(url):
            tally[kind][0] += 1
            found.setdefault(kind, {})[key] = url
        else:
            misses[kind].append(label)

    def record_first(kind: str, key: str, label: str,
                     candidates: list[tuple[str, str]]) -> None:
        """The first candidate that answers, and a note of which one it was."""
        tally[kind][1] += 1
        for how, url in candidates:
            if check.ok(url):
                tally[kind][0] += 1
                found.setdefault(kind, {})[key] = url
                winners[kind][how] += 1
                return
        misses[kind].append(label)

    leagues = sorted({a["league"] for a in assets if a["league"]})

    print(f"\nProbing {len(assets)} rostered asset(s) across {len(leagues)} "
          f"league(s).\nEach line is one request or better; this takes a while.\n")

    # --- teams, and the league marks that badge them ------------------------
    print("Team logos and league logos")
    indexes = {league: team_index(session, league) for league in leagues}
    for league in leagues:
        record_first("league logo", league, league,
                     league_logo_candidates(league, league_payload(session, league)))

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
    # Footballers first, off their clubs' roster pages. Search knows who they
    # are and has no photograph of any of them; the roster page has one for
    # nearly all of them.
    print("\nSquad photographs, one request a club")
    # Only where players are actually rostered. The international competitions
    # are soccer too and hold nothing but squads, and walking their clubs was
    # forty requests for nobody.
    with_players = {a["league"] for a in assets if a["type"] == "Player"}
    squads: dict[str, dict[str, str]] = {}
    for league in sorted(with_players):
        if ESPN_PATH.get(league, ("", ""))[0] == "soccer":
            squads[league] = squad_headshots(session, league, indexes.get(league, {}))
            print(f"    {league}: {len(squads[league])} player(s) with an id", flush=True)

    print("\nHeadshots, and the badge each one wears")
    for asset in assets:
        if asset["type"] != "Player":
            continue
        label = f"{asset['name']} ({asset['league']})"
        from_squad = squads.get(asset["league"], {}).get(
            normalize(asset["name"], team=False), "")
        if from_squad:
            record_first("headshot", asset["id"], label,
                         [("club roster page", from_squad)])
            continue
        who = find_athlete(session, asset["name"], asset["league"])
        record_first("headshot", asset["id"], label,
                     [("search", who.get("image", ""))])

        if asset["category"] in INDIVIDUAL:
            record("athlete flag", asset["id"],
                   athlete_flag(session, asset["league"], numeric_id(who)),
                   label)
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

    for kind, counts in sorted(winners.items()):
        if counts:
            how = ", ".join(f"{name} {n}" for name, n in counts.most_common())
            print(f"    {kind} came from: {how}")

    squad_slots = sum(1 for a in assets
                      if a["type"] == "Team" and "Intl" in a["category"])
    print(f"\n  Not asked of ESPN, and yours to supply either way:")
    print(f"    confederation shields   {len(CONFEDERATIONS)}   "
          f"(for {squad_slots} international squad slots)")
    print(f"    Olympic rings           1   (once the Olympic slots are live)")
    clubs, fresh = player_clubs(db, args.season)
    if clubs:
        print(f"    club logos for players  {fresh}   {len(clubs)} club(s) hold a "
              f"rostered player;")
        print(f"                                {len(clubs) - fresh} of them are "
              f"drafted teams already")
    else:
        print(f"    club logos for players  ?   no nightly run has recorded a "
              f"player's club yet")

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
