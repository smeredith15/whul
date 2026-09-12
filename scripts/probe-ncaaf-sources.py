"""Two ways to get a rostered team's completed games; which one works.

ESPN's scoreboard caps at 25 events a request and ignores limit and page, so it
returns the featured games rather than the slate. Two alternatives:

  1. The NCAA API, which returned all 54 games for 2026-08-29 but with no
     scores. If that is a lag rather than a limit, it fills in and nothing needs
     writing.
  2. ESPN's per-team schedule, which is the right shape anyway -- eight
     rostered teams is eight requests, and a team's own schedule cannot be
     short of its own games.
"""
import collections
from datetime import date

from whul.sources import ncaa_api
from whul.sources.espn import BASE, LEAGUE_PATHS, _get

print("=" * 62)
print("1. NCAA API -- has it filled in the scores yet?")
print("=" * 62)
for day in (date(2026, 8, 29), date(2026, 9, 1), date(2025, 11, 8)):
    try:
        rows = ncaa_api.parse_scoreboard(ncaa_api.scoreboard("ncaaf", day), "ncaaf", day)
        done = collections.Counter(r["completed"] for r in rows)
        scored = sum(1 for r in rows if r["home_score"] is not None)
        print(f"  {day}: {len(rows)} games, completed {dict(done)}, with a score {scored}")
    except Exception as exc:
        print(f"  {day}: ERR {type(exc).__name__}: {exc}")

print()
print("=" * 62)
print("2. ESPN per-team schedule -- eight requests, no cap to hit")
print("=" * 62)
sport, path = LEAGUE_PATHS["ncaaf"]
for team in ("ohio-state", "251", "2294"):   # slug, Texas id, James Madison id
    for season in ("2026",):
        url = f"{BASE}/{sport}/{path}/teams/{team}/schedule"
        try:
            payload = _get(url, {"season": season})
            events = payload.get("events", [])
            done = collections.Counter(
                bool((e.get("competitions") or [{}])[0].get("status", {})
                     .get("type", {}).get("completed"))
                for e in events
            )
            name = (payload.get("team") or {}).get("displayName", "?")
            days = sorted({str(e.get("date", ""))[:10] for e in events})
            print(f"  {team} -> {name}: {len(events)} games, completed {dict(done)}")
            print(f"      {days[0] if days else '-'} .. {days[-1] if days else '-'}")
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            print(f"  {team}: ERR {status} {type(exc).__name__}")
