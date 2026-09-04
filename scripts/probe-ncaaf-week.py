"""What the week query actually returns: how many, how complete, and who."""
import collections
from whul.sources.espn import BASE, LEAGUE_PATHS, _get

sport, path = LEAGUE_PATHS["ncaaf"]
url = f"{BASE}/{sport}/{path}/scoreboard"
ROSTER = {"Arkansas", "Indiana", "James Madison", "Notre Dame",
          "Ohio State", "Oregon", "Penn State", "Texas"}

def look(label, params):
    board = _get(url, params)
    events = board.get("events", [])
    done = collections.Counter(
        bool((e.get("competitions") or [{}])[0].get("status", {}).get("type", {}).get("completed"))
        for e in events)
    teams = sorted({
        (c.get("team") or {}).get("displayName", "")
        for e in events for c in (e.get("competitions") or [{}])[0].get("competitors", [])
    })
    mine = sorted(t for t in teams if any(t.startswith(r) for r in ROSTER))
    print(f"\n{label}")
    print(f"  events {len(events)}  completed {dict(done)}  teams {len(teams)}")
    print(f"  rostered present: {', '.join(mine) or 'none'}")
    return len(events)

for week in (1, 2):
    for extra in ({}, {"limit": 900}, {"limit": 900, "groups": 80}, {"page": 2}):
        tag = ",".join(f"{k}={v}" for k, v in extra.items()) or "plain"
        try:
            look(f"week {week} ({tag})", {"dates": "2026", "seasontype": 2, "week": week, **extra})
        except Exception as exc:
            print(f"\nweek {week} ({tag})\n  ERR {type(exc).__name__}: {exc}")
