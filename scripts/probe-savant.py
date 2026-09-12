"""Can Baseball Savant serve a benchmark, not just a live window?

The question is not only whether Savant answers with date ranges -- it does --
but whether it answers with them *historically*, per player, in aggregate form.
If it does, MLB stops needing proration at all: the benchmark can be drawn over
the same 15 August to 13 July window the league year covers, in each of the past
five seasons, and a live figure is then measured against a bar of the same
shape rather than a whole season's.

Four things decide it, and this asks each in turn:

  1. Is Savant reachable at all from here?
  2. Do the per-player leaderboards accept a date range, or only whole seasons?
     Pitch-level Statcast Search does accept one, but a season of it is
     hundreds of thousands of rows -- far too heavy to pull five times over.
  3. Do those ranges work on *past* seasons, which is what a benchmark needs?
  4. What does a row actually contain -- is there a batting run value, a
     baserunning one, a fielding one, and anything for pitchers?

Nothing here is built against. Send the output back and the answers decide
whether the design is worth having.
"""
import io
import sys
import time

import pandas as pd
import requests

TIMEOUT = 60
HEADERS = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0"}

BASE = "https://baseballsavant.mlb.com"

#: The window the league year covers, and the same window one year back --
#: which is what a benchmark season would look like.
LIVE = ("2026-08-15", "2026-09-05")
PAST = ("2025-08-15", "2026-07-13")


def head(n, text):
    print()
    print("=" * 72)
    print(f"{n}. {text}")
    print("=" * 72)


def get(url, note=""):
    started = time.monotonic()
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception as exc:
        print(f"  ERR  {type(exc).__name__}: {str(exc)[:100]}")
        print(f"       {url[:110]}")
        return None
    took = time.monotonic() - started
    print(f"  {r.status_code}  {len(r.content):>10,} bytes  {took:5.1f}s  {note}")
    if not r.ok:
        print(f"       {url[:110]}")
        return None
    return r


def as_frame(response):
    if response is None:
        return None
    try:
        frame = pd.read_csv(io.StringIO(response.text))
    except Exception as exc:
        print(f"       not a CSV: {type(exc).__name__}; first 200 chars:")
        print(f"       {response.text[:200]!r}")
        return None
    print(f"       {len(frame):,} rows x {len(frame.columns)} columns")
    return frame


head(1, "is Savant reachable?")
get(f"{BASE}/leaderboard/custom?year=2025&type=batter&csv=true", "custom leaderboard, 2025")


head(2, "does the custom leaderboard take a date range?")
# If startDate/endDate are honoured the row count and the totals fall sharply
# against the same season pulled whole; if they are ignored the two match.
whole = as_frame(get(
    f"{BASE}/leaderboard/custom?year=2025&type=batter&filter=&min=1"
    f"&selections=player_age,ab,pa,hit,home_run,strikeout,walk&csv=true",
    "2025 whole season"))
ranged = as_frame(get(
    f"{BASE}/leaderboard/custom?year=2025&type=batter&filter=&min=1"
    f"&selections=player_age,ab,pa,hit,home_run,strikeout,walk"
    f"&startDate=2025-08-15&endDate=2025-09-05&csv=true",
    "2025, 15 Aug to 5 Sep"))
for name, frame in (("whole", whole), ("ranged", ranged)):
    if frame is not None and "ab" in frame.columns:
        print(f"       {name}: {pd.to_numeric(frame['ab'], errors='coerce').sum():,.0f} "
              f"at-bats across {len(frame):,} players")
if whole is not None and ranged is not None and "ab" in whole.columns:
    a = pd.to_numeric(whole["ab"], errors="coerce").sum()
    b = pd.to_numeric(ranged["ab"], errors="coerce").sum()
    if a and b >= a * 0.95:
        print("       ^^ the range was IGNORED -- same totals as the whole season")
    elif a:
        print(f"       ^^ the range was applied: {b / a:.0%} of the season")


head(3, "what run values does a row carry?")
if ranged is not None:
    print(f"       columns: {sorted(ranged.columns)}")


head(4, "the run-value leaderboards, whole season and ranged")
for label, url in (
    ("swing/take run value",
     f"{BASE}/leaderboard/swing-take?year=2025&type=batter&csv=true"),
    ("swing/take, ranged",
     f"{BASE}/leaderboard/swing-take?year=2025&type=batter"
     f"&startDate=2025-08-15&endDate=2025-09-05&csv=true"),
    ("outs above average (fielding)",
     f"{BASE}/leaderboard/outs_above_average?type=Fielder&year=2025&csv=true"),
    ("baserunning run value",
     f"{BASE}/leaderboard/baserunning-run-value?year=2025&csv=true"),
    ("pitcher run value",
     f"{BASE}/leaderboard/custom?year=2025&type=pitcher&min=1&csv=true"),
):
    frame = as_frame(get(url, label))
    if frame is not None:
        interesting = [c for c in frame.columns
                       if "run" in c.lower() or "value" in c.lower()
                       or "oaa" in c.lower() or "war" in c.lower()]
        print(f"       run-value-ish columns: {interesting[:12]}")


head(5, "does a past season take a range? (a benchmark needs five of them)")
for season in (2021, 2023):
    as_frame(get(
        f"{BASE}/leaderboard/custom?year={season}&type=batter&min=1"
        f"&selections=ab,pa,hit&startDate={season}-08-15&endDate={season + 1}-07-13"
        f"&csv=true",
        f"{season}-08-15 to {season + 1}-07-13"))
    print("       (a range crossing new year may not be accepted; if not, two "
          "half-window pulls would have to be summed)")

print()
print("Send this whole output back. What matters: whether the ranges are")
print("applied rather than ignored, whether past seasons accept them, and")
print("which run-value columns exist for batters, fielders and pitchers.")
