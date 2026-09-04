"""What the week query actually returns: how many, how complete, and who."""
import collections
from whul.sources.espn import BASE, LEAGUE_PATHS, _get

sport, path = LEAGUE_PATHS["ncaaf"]
url = f"{BASE}/{sport}/{path}/scoreboard"
# Whole names: "Arkansas" as a prefix also matches Arkansas-Pine Bluff, and
# "Texas" matches three other programs, which made the first run of this probe
# look far more encouraging than it was.
ROSTER = {"Arkansas Razorbacks", "Indiana Hoosiers", "James Madison Dukes",
          "Notre Dame Fighting Irish", "Ohio State Buckeyes", "Oregon Ducks",
          "Penn State Nittany Lions", "Texas Longhorns"}

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
    mine = sorted(set(teams) & ROSTER)
    print(f"\n{label}")
    played = sorted({
        str(e.get("date", ""))[:10] for e in events
        if (e.get("competitions") or [{}])[0].get("status", {}).get("type", {}).get("completed")
    })
    span = sorted({str(e.get("date", ""))[:10] for e in events})
    print(f"  events {len(events)}  completed {dict(done)}  teams {len(teams)}")
    print(f"  dates {span[0] if span else '-'} .. {span[-1] if span else '-'}")
    print(f"  completed on: {', '.join(played) or 'none'}")
    print(f"  rostered present: {', '.join(mine) or 'none'}")
    return len(events)

# Week 0 is the late-August opener. ESPN's week 1 turned out to be the first
# week of September -- three completed games on a Thursday, the rest to come --
# which is why a week-1 query looked like a season that had not started.
for week in (0, 1, 2):
    for extra in ({}, {"limit": 900}):
        tag = ",".join(f"{k}={v}" for k, v in extra.items()) or "plain"
        try:
            look(f"week {week} ({tag})", {"dates": "2026", "seasontype": 2, "week": week, **extra})
        except Exception as exc:
            print(f"\nweek {week} ({tag})\n  ERR {type(exc).__name__}: {exc}")
