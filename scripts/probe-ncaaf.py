#!/usr/bin/env python3
"""Where does ESPN keep a college football team's conference?

Ten rostered NCAAF teams have scored nothing all season. The ingest is not
failing quietly -- it says exactly what is wrong, every night:

    could not pull: MissingConference: 18 completed game(s) arrived with no
    conference on any team, so conference wins and the regular-season title
    cannot be scored. This is a feed problem, not an empty week.

That guard is right to refuse. Conference wins and the regular-season title are
two of the scored terms, so games with no conference on them would score low
rather than not at all, and a whole league would drift quietly downward instead
of stopping. What it cannot say is where the conference *should* have come
from, and `whul probe ncaaf` only reports the coverage as a fraction -- "0/18"
is the symptom restated, not a diagnosis.

So this asks the question three ways in one run, because a second round trip
costs a day:

  1. the scoreboard, which is where the adapter looks now -- every key on a
     competitor and on its team, so a conference sitting under a name nobody
     checks is visible rather than absent;
  2. the teams endpoint, which the adapter already calls for the eligible-team
     list, in case conference membership is published there;
  3. the standings endpoint, which is how ESPN's own pages group a division.

Conference membership is a property of a team for a season, not of a game, so
(2) or (3) answering would be the better fix even if (1) can be made to work:
a map built once per season cannot go missing on the night of a particular
slate.

    python scripts/probe-ncaaf.py
    python scripts/probe-ncaaf.py --date 2026-09-05

RUN IT WHERE ESPN IS REACHABLE. The sandbox this is developed in answers 403 to
every outbound CONNECT, so every request fails identically there and the report
reads as "ESPN has no conferences", which is the one answer it must not invent.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def last_saturday(today: date | None = None) -> date:
    """College football is played on Saturdays; a Tuesday probe sees nothing.

    Defaulting to a date with no games is how a probe reports an empty feed and
    means an empty calendar.
    """
    today = today or date.today()
    return today - timedelta(days=(today.weekday() - 5) % 7 or 7)


def keys_of(node, want=("conf", "group", "division")) -> dict:
    """Every key whose name suggests it might carry a conference, with values."""
    if not isinstance(node, dict):
        return {}
    return {
        key: value for key, value in node.items()
        if any(word in key.lower() for word in want)
        and not isinstance(value, (list,)) or key.lower() in ("groups",)
    }


def show_scoreboard(day: date) -> None:
    from whul.sources.espn import (
        BASE, LEAGUE_PATHS, _competitor, _conference, scoreboard_variants, _get,
    )

    sport, path = LEAGUE_PATHS["ncaaf"]
    board = None
    for params in scoreboard_variants("ncaaf", day):
        shape = ",".join(k for k in params if k != "dates") or "dates only"
        try:
            board = _get(f"{BASE}/{sport}/{path}/scoreboard", params,
                         cache_key=f"probe-ncaaf/{day.isoformat()}/{shape}")
            print(f"    accepted request shape: {shape}")
            break
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            print(f"    {shape} -> {status}")
    if board is None:
        print("    every request shape was rejected.")
        return

    events = board.get("events") or []
    print(f"    {len(events)} event(s) on {day}")
    if not events:
        print("    Nothing was played. Try --date on a Saturday in season.")
        return

    inner = (events[0].get("competitions") or [{}])[0]
    print(f"    competition keys: {sorted(inner)}")
    for side in ("home", "away"):
        entry = _competitor(inner, side)
        if not entry:
            continue
        team = entry.get("team") or {}
        print(f"\n    --- {side}: {team.get('displayName', '?')}")
        print(f"        competitor keys : {sorted(entry)}")
        print(f"        team keys       : {sorted(team)}")
        print(f"        conference-ish  : {json.dumps(keys_of(entry) | keys_of(team), default=str)[:400]}")
        print(f"        _conference()   : {_conference(entry)!r}")

    covered = 0
    for event in events:
        block = (event.get("competitions") or [{}])[0]
        if all(_conference(_competitor(block, s)) for s in ("home", "away")):
            covered += 1
    print(f"\n    conference on both sides: {covered}/{len(events)} event(s)")


def show_teams() -> None:
    """The endpoint the adapter already calls for the eligible-team list."""
    from whul.sources.espn import BASE, DIVISION_I_GROUPS, LEAGUE_PATHS, _get

    sport, path = LEAGUE_PATHS["ncaaf"]
    payload = _get(f"{BASE}/{sport}/{path}/teams",
                   {"limit": 1000, "groups": DIVISION_I_GROUPS["ncaaf"]},
                   cache_key="probe-ncaaf/teams")
    entries = []
    for block in payload.get("sports", []):
        for league_block in block.get("leagues", []):
            entries.extend(league_block.get("teams", []))
    print(f"    {len(entries)} team(s) listed")
    if not entries:
        return
    team = (entries[0].get("team") or {})
    print(f"    entry keys : {sorted(entries[0])}")
    print(f"    team keys  : {sorted(team)}")
    print(f"    conference-ish: {json.dumps(keys_of(team), default=str)[:400]}")


def show_standings() -> None:
    """How ESPN's own pages group a division, and the likeliest good answer."""
    from whul.sources.espn import BASE, LEAGUE_PATHS, _get

    sport, path = LEAGUE_PATHS["ncaaf"]
    for url, params in (
        (f"{BASE}/{sport}/{path}/standings", {}),
        (f"https://site.web.api.espn.com/apis/v2/sports/{sport}/{path}/standings",
         {"level": 2}),
    ):
        try:
            payload = _get(url, params, cache_key=f"probe-ncaaf/standings/{params}")
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            print(f"    {url.rsplit('/', 2)[-1]} {params} -> {status}")
            continue
        print(f"    {url}")
        print(f"      top-level keys: {sorted(payload)[:14]}")
        # A standings payload nests groups; walk for anything that looks like a
        # conference holding teams, rather than guessing the path.
        found: list[tuple[str, int]] = []

        def walk(node) -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item)
                return
            if not isinstance(node, dict):
                return
            name = node.get("name") or node.get("displayName") or node.get("shortName")
            entries = node.get("standings")
            if isinstance(entries, dict):
                rows = entries.get("entries") or []
                if name and rows:
                    found.append((str(name), len(rows)))
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)

        walk(payload)
        if found:
            print(f"      {len(found)} group(s) with teams; first few:")
            for name, count in found[:8]:
                print(f"        {name} ({count} teams)")
            print(f"      total teams across groups: {sum(n for _, n in found)}")
        else:
            print("      no group/teams structure found by walking")
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="YYYY-MM-DD; default the last Saturday")
    args = parser.parse_args()
    day = date.fromisoformat(args.date) if args.date else last_saturday()

    print(__doc__.split("So this asks")[0].strip())
    for title, run in (
        (f"1. THE SCOREBOARD ({day}) -- where the adapter looks now", lambda: show_scoreboard(day)),
        ("2. THE TEAMS ENDPOINT -- already called for the eligible list", show_teams),
        ("3. THE STANDINGS ENDPOINT -- how ESPN groups a division", show_standings),
    ):
        print(f"\n{'=' * 70}\n{title}\n")
        try:
            run()
        except Exception as exc:  # noqa: BLE001 -- one source must not stop the rest
            print(f"    FAILED: {type(exc).__name__}: {exc}")

    print(f"\n{'=' * 70}")
    print("Read (2) and (3) first. A team -> conference map built once a season")
    print("cannot go missing on the night of a particular slate, which is what")
    print("reading it off each game leaves open.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
