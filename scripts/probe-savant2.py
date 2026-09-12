"""Which date parameter, if any, Savant's leaderboards actually read.

The first probe passed startDate/endDate and got back a byte-identical whole
season -- so the range was ignored, not applied. That is either the wrong
parameter name or an endpoint that does not filter by date at all, and those
have opposite consequences: the first is a one-line fix, the second means the
only date-ranged path is pitch-level Statcast Search, which is capped at 25,000
rows a query against a season of roughly 700,000 pitches.

So this tries the spellings Savant has used, and judges each one the only way
that cannot be fooled: by whether the totals move. A parameter that is read
returns a fraction of the season. One that is ignored returns all of it, and
looks perfectly valid doing so.

It also asks the three run-value leaderboards the first probe found -- swing
and take for batting, baserunning, outs above average for fielding -- since
those carry the numbers a benchmark would be built from.
"""
import io
import itertools
import time

import pandas as pd
import requests

BASE = "https://baseballsavant.mlb.com"
TIMEOUT = 60
HEADERS = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0"}

SEASON = 2025
FROM, TO = "2025-08-15", "2025-09-05"   # about three weeks of a season

#: Spellings Savant has used across its pages, plus the pitch-level pair that
#: is known to work on Statcast Search.
DATE_PARAMS = [
    ("startDate", "endDate"),
    ("start_date", "end_date"),
    ("game_date_gt", "game_date_lt"),
    ("dateStart", "dateEnd"),
    ("min_date", "max_date"),
]


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:70]}"
    if not r.ok:
        return None, f"HTTP {r.status_code}"
    try:
        return pd.read_csv(io.StringIO(r.text)), ""
    except Exception:
        return None, f"not CSV ({len(r.content):,} bytes)"


def total(frame, columns=("ab", "pa", "n", "attempts")):
    if frame is None or frame.empty:
        return 0.0, 0
    for column in columns:
        if column in frame.columns:
            return float(pd.to_numeric(frame[column], errors="coerce").sum()), len(frame)
    return 0.0, len(frame)


print("=" * 72)
print("1. the custom leaderboard: does any spelling move the totals?")
print("=" * 72)
selections = "ab,pa,hit,home_run"
base = (f"{BASE}/leaderboard/custom?year={SEASON}&type=batter&min=1"
        f"&selections={selections}&csv=true")

whole, err = fetch(base)
baseline, rows = total(whole)
print(f"  whole season          {baseline:12,.0f} at-bats  {rows:>5} rows  {err}")

for lo, hi in DATE_PARAMS:
    frame, err = fetch(f"{base}&{lo}={FROM}&{hi}={TO}")
    value, rows = total(frame)
    if err:
        verdict = err
    elif not baseline:
        verdict = "no baseline to compare against"
    elif value >= baseline * 0.95:
        verdict = "IGNORED -- the whole season came back"
    else:
        verdict = f"APPLIED -- {value / baseline:.0%} of the season"
    print(f"  {lo:<16} {value:12,.0f} at-bats  {rows:>5} rows  {verdict}")
    time.sleep(0.5)

print()
print("=" * 72)
print("2. the run-value leaderboards, and whether they take a range")
print("=" * 72)
BOARDS = {
    "swing-take (batting runs)":
        f"{BASE}/leaderboard/swing-take?year={SEASON}&type=batter&sub_type=all&csv=true",
    "baserunning-run-value":
        f"{BASE}/leaderboard/baserunning-run-value?year={SEASON}&min=1&csv=true",
    "outs_above_average (fielding)":
        f"{BASE}/leaderboard/outs_above_average?type=Fielder&year={SEASON}&min=1&csv=true",
}
for label, url in BOARDS.items():
    frame, err = fetch(url)
    if frame is None:
        print(f"  {label:32} {err}")
        continue
    print(f"  {label:32} {len(frame):>5} rows x {len(frame.columns)} cols  {err}")
    values = [c for c in frame.columns
              if any(k in c.lower() for k in ("run", "oaa", "value"))]
    print(f"       {values[:8]}")
    ranged, err = fetch(f"{url}&startDate={FROM}&endDate={TO}")
    same = (ranged is not None and len(ranged) == len(frame))
    print(f"       with a range: "
          f"{'same row count -- likely ignored' if same else 'row count differs'}")
    time.sleep(0.5)

print()
print("=" * 72)
print("3. pitch-level search: the one path known to take a range")
print("=" * 72)
print("  Capped at 25,000 rows a query against ~700,000 pitches a season, so")
print("  this is about whether it works and how heavy one week is.")
url = (f"{BASE}/statcast_search/csv?all=true&type=details"
       f"&game_date_gt=2025-08-15&game_date_lt=2025-08-22&player_type=batter")
started = time.monotonic()
frame, err = fetch(url)
took = time.monotonic() - started
if frame is None:
    print(f"  one week: {err}")
else:
    print(f"  one week: {len(frame):,} rows x {len(frame.columns)} cols in {took:.1f}s")
    has = [c for c in frame.columns if "delta_run_exp" in c or c in
           ("batter", "pitcher", "game_date", "events")]
    print(f"  the columns a run value would be summed from: {has}")
    if len(frame) >= 24_000:
        print("  ^ at or near the 25,000 cap: a week does not fit in one query,")
        print("    so a season would need daily chunks -- about 190 requests each.")

print()
print("What decides it: whether any spelling in section 1 says APPLIED. If none")
print("does, the leaderboards cannot serve a windowed benchmark and section 3's")
print("request count is the real cost of doing it from pitch level.")
