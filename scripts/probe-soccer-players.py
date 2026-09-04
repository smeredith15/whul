"""Which source will give us club soccer player stats.

FBref answered 403 -- Cloudflare, which no header will reliably talk past. The
scorer needs, per player per season: appearances, starts (or substitute
outings), minutes, goals, assists, yellows and reds, for the five European
leagues and MLS.

Four candidates, cheapest and most-likely-reachable first. This tries each and
prints what came back, so one run says which to build against rather than
another round of guessing.

  1. FBref again with fuller browser headers -- cheap to rule out.
  2. ESPN's by-athlete statistics. We already reach ESPN for six other
     leagues, and its soccer athlete stats carry appearances and subIns,
     which give starts by subtraction -- exactly what the scorer wants.
  3. ESPN's core API, a different host with a different shape.
  4. Understat, which embeds its data as JSON inside the page and is not
     defended. No MLS, and no starts -- minutes and games only.
"""
import json
import re

import requests

TIMEOUT = 30
SEASON = 2025           # the 2024-25 European season

BROWSER = {
    "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "accept-language": "en-US,en;q=0.9",
    "accept-encoding": "gzip, deflate, br",
    "referer": "https://www.google.com/",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "cross-site",
    "upgrade-insecure-requests": "1",
}


def head(n, text):
    print()
    print("=" * 70)
    print(f"{n}. {text}")
    print("=" * 70)


def show(url, response, extra=""):
    print(f"  {response.status_code}  {url}")
    if extra:
        print(f"       {extra}")


head(1, "FBref with fuller browser headers")
for url in (
    "https://fbref.com/en/comps/Big5/2024-2025/stats/players/"
    "2024-2025-Big-5-European-Leagues-Stats",
):
    try:
        r = requests.get(url, headers=BROWSER, timeout=TIMEOUT)
        show(url, r, f"{len(r.text):,} bytes, "
                     f"'stats_standard' present: {'stats_standard' in r.text}")
    except Exception as exc:
        print(f"  ERR  {type(exc).__name__}: {exc}")


head(2, "ESPN by-athlete statistics (we already reach ESPN)")
for league in ("eng.1", "usa.1"):
    for url in (
        f"https://site.web.api.espn.com/apis/common/v3/sports/soccer/{league}"
        f"/statistics/byathlete?region=us&lang=en&season={SEASON}&limit=50",
        f"https://site.web.api.espn.com/apis/common/v3/sports/soccer/{league}"
        f"/statistics/byathlete?season={SEASON}&limit=50",
        f"https://site.web.api.espn.com/apis/common/v3/sports/soccer/{league}"
        f"/athletes/statistics?season={SEASON}&limit=50",
    ):
        try:
            r = requests.get(url, timeout=TIMEOUT)
        except Exception as exc:
            print(f"  ERR  {type(exc).__name__}: {str(exc)[:90]}")
            continue
        note = ""
        if r.ok:
            try:
                payload = r.json()
                note = f"top-level keys {sorted(payload)[:8]}"
                athletes = (payload.get("athletes")
                            or payload.get("items")
                            or (payload.get("categories") or [{}]))
                note += f"; {len(athletes)} entries"
                if athletes and isinstance(athletes[0], dict):
                    print(f"  200  {url}")
                    print(f"       {note}")
                    print("       one entry:")
                    print("       " + json.dumps(athletes[0])[:900])
                    continue
            except ValueError:
                note = "not JSON"
        show(url, r, note)


head(3, "ESPN core API -- different host, different shape")
for url in (
    f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1/seasons/"
    f"{SEASON}/types/1/athletes?limit=5",
    f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1/seasons/"
    f"{SEASON}/athletes?limit=5",
):
    try:
        r = requests.get(url, timeout=TIMEOUT)
        note = ""
        if r.ok:
            payload = r.json()
            note = f"keys {sorted(payload)[:8]}, count {payload.get('count')}"
        show(url, r, note)
    except Exception as exc:
        print(f"  ERR  {type(exc).__name__}: {str(exc)[:90]}")


head(4, "Understat -- data embedded as JSON in the page")
for league in ("EPL", "La_liga", "Serie_A", "Bundesliga", "Ligue_1"):
    url = f"https://understat.com/league/{league}/{SEASON - 1}"
    try:
        r = requests.get(url, headers=BROWSER, timeout=TIMEOUT)
    except Exception as exc:
        print(f"  ERR  {league}: {type(exc).__name__}: {str(exc)[:90]}")
        continue
    match = re.search(r"playersData\s*=\s*JSON\.parse\('([^']+)'\)", r.text)
    if not r.ok or not match:
        show(url, r, "no playersData block" if r.ok else "")
        continue
    players = json.loads(match.group(1).encode().decode("unicode_escape"))
    print(f"  200  {url}")
    print(f"       {len(players)} players; fields {sorted(players[0])}")
    print("       " + json.dumps(players[0])[:500])

print()
print("Send me this whole output -- the first source that returns per-player")
print("season totals is the one I build against.")
