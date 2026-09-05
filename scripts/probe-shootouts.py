#!/usr/bin/env python3
"""Does ESPN say a cup tie went to penalties, and where does it put the score?

A shootout win is worth two thirds of a win and a shootout loss a third, so the
scorer now needs to tell a shootout from a draw. Nothing in a scoreline says
which: both sides finish level either way. The adapter reads ``shootoutScore``
off each competitor -- this asks whether that key is really there, and what it
holds, before the scoring is trusted.

Run it from a machine that can reach site.api.espn.com:

    python scripts/probe-shootouts.py
    python scripts/probe-shootouts.py --date 2025-05-17 --league facup

Two failures it is looking for, and they fail in opposite directions:

* **No shootout field at all.** Then a shootout is indistinguishable from a
  draw, both sides score a draw's points, and the winner is underpaid. Visible
  here as a level match with no shootout key on either competitor.

* **The penalties folded into ``score``.** Then a 1-1 that finished 4-2 on
  penalties arrives as a 4-2 win -- worth a full win, a two-goal margin bonus,
  and a loss for a side that did not lose. Far worse than the first, and
  invisible in a total. Visible here as a level-looking tie whose scores are
  not level, or as a ``score`` that matches the shootout rather than the goals.

The dates below are finals that went to penalties. A date with no shootout on
it proves nothing either way, so the probe says so rather than reporting a
clean run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Ties known to have been decided on penalties: (league key, date, what it was).
#: Each is a final, so it is on the date given and not moved.
KNOWN_SHOOTOUTS = [
    ("facup", date(2025, 5, 17), "FA Cup final"),
    ("copadelrey", date(2024, 4, 6), "Copa del Rey final"),
    ("coppaitalia", date(2024, 5, 15), "Coppa Italia final"),
    ("uel", date(2024, 5, 22), "Europa League final"),
]


def competitor_keys(entry: dict) -> dict:
    """Everything on a competitor that could be carrying a shootout score."""
    return {
        key: value for key, value in entry.items()
        if isinstance(value, (str, int, float, bool))
        and ("score" in key.lower() or "shootout" in key.lower()
             or "penalt" in key.lower() or key in ("winner", "homeAway"))
    }


def report_event(event: dict, label: str) -> bool:
    """Print one match's scoring fields. True if it looks like a shootout."""
    from whul.sources.espn import _competitor, _soccer_rows

    inner = (event.get("competitions") or [{}])[0]
    home, away = _competitor(inner, "home"), _competitor(inner, "away")
    if not home or not away:
        print("    competitors missing -- cannot read this event")
        return False

    name = (event.get("name") or "").strip()
    print(f"\n  {label}: {name}")
    for side, entry in (("home", home), ("away", away)):
        team = (entry.get("team") or {}).get("displayName", "?")
        print(f"    {side:<5}{team:<28}{json.dumps(competitor_keys(entry))}")

    rows = _soccer_rows(event, "", date.today(), "")
    if not rows:
        print("    the adapter dropped this event (not completed?)")
        return False
    row = rows[0]
    print(f"    adapter reads: goals {row['goals_for']:.0f}-{row['goals_against']:.0f}"
          f", shootout {row['shootout_for']:.0f}-{row['shootout_against']:.0f}")

    level = row["goals_for"] == row["goals_against"]
    shootout = row["shootout_for"] != row["shootout_against"]
    if level and shootout:
        print("    -> reads as a shootout. This is the answer we want.")
        return True
    if level and not shootout:
        print("    -> reads as a DRAW. Either this tie did not go to penalties,")
        print("       or the shootout is somewhere the adapter is not looking:")
        print("       compare the keys printed above against what it reads.")
        return False
    print("    -> NOT level. If this tie was drawn in normal time, the penalties")
    print("       have been folded into `score` -- the dangerous case, because")
    print("       it pays a full win and a margin bonus for a drawn match.")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", help="one league key instead of the known list")
    parser.add_argument("--date", help="YYYY-MM-DD, with --league")
    args = parser.parse_args()

    from whul.sources.espn import scoreboard

    if args.league and args.date:
        wanted = [(args.league, date.fromisoformat(args.date), "requested")]
    elif args.league or args.date:
        print("--league and --date go together.", file=sys.stderr)
        return 2
    else:
        wanted = KNOWN_SHOOTOUTS

    print(__doc__.split("Run it")[0].strip())
    found = 0
    for league, day, label in wanted:
        print(f"\n{'=' * 68}\n{league} {day}  ({label})")
        try:
            board = scoreboard(league, day)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            print(f"  FAILED ({status}): {type(exc).__name__}: {exc}")
            continue
        events = board.get("events", [])
        if not events:
            print("  no events on this date -- nothing to read")
            continue
        for event in events:
            if report_event(event, label):
                found += 1

    print(f"\n{'=' * 68}")
    if found:
        print(f"{found} tie(s) read as a shootout. The adapter is reading the right")
        print("key, and a shootout win can be told from a draw.")
        return 0
    print("No tie on these dates read as a shootout.")
    print("That is not a pass. Either the dates returned nothing, or the")
    print("shootout is not where the adapter looks -- read the printed keys")
    print("above and say which one holds the penalties.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
