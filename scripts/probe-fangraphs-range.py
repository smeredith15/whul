"""Does FanGraphs serve Off, Def and WAR over a date range?

This is the source the MLB scoring was written against -- Off, Def and WAR are
its metrics, on its scale, and the benchmark was drawn from the Stats API's
reconstruction of them. So a date range here needs no new scale, no new
benchmark and no substitution of one quantity for another. It is the only
remaining option that changes nothing except the span.

Savant is settled and the answer was no: its leaderboards ignore every date
parameter tried, and the pitch-level search that does take one gives batting run
value only -- no base running, no fielding, no WAR -- for about eleven hundred
requests.

FanGraphs' custom range is `month=1000` with startdate and enddate filled in;
the parameter shapes in whul/sources/mlb.py already carry those fields with
month=0. Two things decide it: whether the site answers this machine at all --
it blocks datacenter addresses, which is why the Stats API replaced it -- and
whether the range moves the totals rather than being ignored, which is how both
of the last two sources failed.
"""
import time

import pandas as pd
import requests

API = "https://www.fangraphs.com/api/leaders/major-league/data"
TIMEOUT = 45
HEADERS = {
    "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0"),
    "accept": "application/json",
    "referer": "https://www.fangraphs.com/leaders/major-league",
}

SEASON = 2025
FROM, TO = "2025-08-15", "2025-09-05"

COMMON = {"pos": "all", "stats": "bat", "lg": "all", "qual": "0", "type": "8",
          "season": SEASON, "season1": SEASON, "ind": 0,
          "pageitems": 2000, "pagenum": 1}


def pull(params, note):
    started = time.monotonic()
    try:
        r = requests.get(API, params=params, headers=HEADERS, timeout=TIMEOUT)
    except Exception as exc:
        print(f"  ERR  {type(exc).__name__}: {str(exc)[:90]}")
        return None
    took = time.monotonic() - started
    print(f"  {r.status_code}  {len(r.content):>10,} bytes  {took:5.1f}s  {note}")
    if not r.ok:
        return None
    try:
        payload = r.json()
    except ValueError:
        print(f"       not JSON; first 160 chars: {r.text[:160]!r}")
        return None
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        print(f"       no rows; keys were {sorted(payload)[:10] if isinstance(payload, dict) else type(payload)}")
        return None
    frame = pd.DataFrame(rows)
    print(f"       {len(frame):,} rows x {len(frame.columns)} columns")
    return frame


def total(frame, column="AB"):
    if frame is None or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").sum())


print("=" * 72)
print("1. does FanGraphs answer this machine?")
print("=" * 72)
whole = pull({**COMMON, "month": 0, "startdate": "", "enddate": ""}, "whole 2025")
if whole is None:
    print()
    print("  No answer. FanGraphs blocks datacenter addresses, which is why the")
    print("  Stats API replaced it. If this machine is behind such an address")
    print("  there is nothing to do here, and the run-value share-out stands.")
    raise SystemExit(0)

baseline = total(whole)
print(f"       {baseline:,.0f} at-bats")
have = [c for c in ("Off", "Def", "WAR", "AB", "PA", "Name", "playerid")
        if c in whole.columns]
print(f"       the columns that matter: {have}")
missing = [c for c in ("Off", "Def", "WAR") if c not in whole.columns]
if missing:
    print(f"       MISSING {missing} -- these are the whole reason to be here")

print()
print("=" * 72)
print("2. does month=1000 with a date range move the totals?")
print("=" * 72)
for month, note in ((1000, "month=1000, the custom-range flag"), (0, "month=0, as a control")):
    frame = pull(
        {**COMMON, "month": month, "startdate": FROM, "enddate": TO},
        f"{note}: {FROM} to {TO}",
    )
    value = total(frame)
    if frame is None:
        continue
    if not baseline:
        print("       no baseline to compare against")
    elif value >= baseline * 0.95:
        print(f"       {value:,.0f} at-bats -- IGNORED, the whole season came back")
    else:
        print(f"       {value:,.0f} at-bats -- APPLIED, {value / baseline:.0%} of the season")
        for column in ("Off", "Def", "WAR"):
            if column in frame.columns:
                a = total(whole, column)
                b = total(frame, column)
                print(f"       {column}: season {a:,.1f} -> window {b:,.1f}")
    time.sleep(1)

print()
print("=" * 72)
print("3. and on a past season, which is what a benchmark needs")
print("=" * 72)
past = {**COMMON, "season": 2023, "season1": 2023, "month": 1000,
        "startdate": "2023-08-15", "enddate": "2024-07-13"}
frame = pull(past, "2023-08-15 to 2024-07-13")
if frame is not None:
    print(f"       {total(frame):,.0f} at-bats over the league-year window")
    print("       (a range crossing new year may be refused; if so, two pulls "
          "summed would do it)")

print()
print("If section 2 says APPLIED and Off, Def and WAR are all present, then the")
print("MLB benchmark can be drawn over the league year's own window and both")
print("approximations -- the proration and the run-value share-out -- go away.")
