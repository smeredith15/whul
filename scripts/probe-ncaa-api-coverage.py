"""Why the NCAA API returns about a third of a college football season.

Five seasons walked, no dates failed, and every season came back at ~4 games
per team where FBS plays twelve. About 66 rows come back per request and each
game arrives ~31 times over the walk. A week is seven days, so 31 copies means
most requests are returning the *same* games -- and 420 distinct games a season
means we are only ever seeing a fraction of the weeks.

Three things would explain it, and they need opposite fixes:

  a) one request returns a whole week, and our 184 dates map onto far fewer
     distinct weeks than the season has;
  b) the endpoint clamps out-of-season dates to the nearest slate, so the 100+
     non-game days each re-return the same week;
  c) the response is paginated or capped, and we take the first page.

This asks the questions that separate them. It also dumps one raw game so we
can see whether the payload carries the game's own date -- we currently stamp
the *requested* date onto every row and throw the real one away.
"""
import collections
import json
from datetime import date, timedelta

from whul.sources import ncaa_api


def ids_for(day):
    payload = ncaa_api.scoreboard("ncaaf", day)
    games = payload.get("games", [])
    inner = [g.get("game", g) for g in games]
    return payload, inner, {str(g.get("gameID") or g.get("url", "")) for g in inner}


print("=" * 70)
print("1. one request -- how much comes back, and what is in it?")
print("=" * 70)
saturday = date(2024, 11, 9)
payload, inner, ids = ids_for(saturday)
print(f"  {saturday}: {len(inner)} games, {len(ids)} distinct ids")
print(f"  top-level keys: {sorted(payload)}")
for key in ("inputMD5Sum", "updated", "hasSchedule", "conferences", "week"):
    if key in payload:
        print(f"    {key} = {json.dumps(payload[key])[:200]}")
if inner:
    print(f"\n  one game's keys: {sorted(inner[0])}")
    print("  " + json.dumps(inner[0], indent=2)[:1200])

print()
print("=" * 70)
print("2. do consecutive dates return the same games? (a week, or a clamp)")
print("=" * 70)
seen = {}
for offset in range(0, 10):
    day = saturday + timedelta(days=offset)
    try:
        _, games, day_ids = ids_for(day)
    except Exception as exc:
        print(f"  {day}: ERR {type(exc).__name__}: {exc}")
        continue
    seen[day] = day_ids
    overlap = len(day_ids & seen.get(saturday, set()))
    print(f"  {day}: {len(games):>3} games, {len(day_ids):>3} ids, "
          f"{overlap:>3} shared with {saturday}")

print()
print("=" * 70)
print("3. how many distinct games does a whole month yield?")
print("=" * 70)
month, dates_ok = set(), 0
for offset in range(31):
    day = date(2024, 10, 1) + timedelta(days=offset)
    try:
        _, _, day_ids = ids_for(day)
    except Exception:
        continue
    dates_ok += 1
    month |= day_ids
print(f"  October 2024: {dates_ok} dates fetched, {len(month)} distinct games")
print("  FBS plays about 60 games a week, so a month should be near 250.")

print()
print("=" * 70)
print("4. is it the same handful of weeks over and over?")
print("=" * 70)
weeks = collections.Counter()
for offset in range(0, 184, 3):          # every third day of one season
    day = date(2024, 8, 1) + timedelta(days=offset)
    try:
        _, games, _ = ids_for(day)
    except Exception:
        continue
    # Whatever date field the payload carries, grouped -- if every request
    # answers with the same slate, one week will dominate this count.
    for game in games:
        stamp = (game.get("startDate") or game.get("gameDate")
                 or game.get("startTimeEpoch") or "?")
        weeks[str(stamp)[:10]] += 1
print(f"  {len(weeks)} distinct game dates seen across the season")
for stamp, count in weeks.most_common(12):
    print(f"    {stamp}: {count}")
