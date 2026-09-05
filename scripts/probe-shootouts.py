#!/usr/bin/env python3
"""Does ESPN say a cup tie went to penalties, and where does it put the score?

A shootout win is worth two thirds of a win and a shootout loss a third, so the
scorer needs to tell a shootout from a draw. Nothing in a scoreline says which:
both sides finish level either way.

Answered on 2025-05 for the Copa del Rey final of 2024, which ESPN returned as

    Athletic Club   {"score": "1", "shootoutScore": 4}
    Mallorca        {"score": "1", "shootoutScore": 2}

So the penalties sit *beside* the score in ``shootoutScore``, not inside it,
and the key is absent entirely -- not zero -- on a match that did not go to
penalties. It also arrives as a number where ``score`` is a string. The adapter
reads all three of those correctly. This script re-checks that, because it is
the assumption the whole shootout rule rests on.

Run it from a machine that can reach site.api.espn.com:

    python scripts/probe-shootouts.py
    python scripts/probe-shootouts.py --league facup --date 2022-05-14

Each tie below is one that really was decided on penalties, with the score it
really finished at, so the check is an exact comparison rather than a guess.
An earlier version of this list had three finals in it that were won in normal
time; the probe dutifully reported them as not-shootouts and the list was
wrong, not the feed. If you add a tie here, check the result first.

The failure that matters most is the one the Copa del Rey answer rules out:
penalties folded into ``score``. A 1-1 that finished 4-2 would arrive as a 4-2
win -- a full win, a two-goal margin bonus, and a loss recorded against a side
that did not lose. It is worse than finding no shootout field at all, because
that one merely underpays a winner, and this one is invisible in a total.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Ties decided on penalties, with what they actually finished at.
#:
#: Every figure here is **the home side's**, which is why the home side is
#: named rather than implied: the obvious mistake is to write down the winner's
#: score, and in two of these three the winner was away. Naming the club makes
#: that a mismatch the probe reports rather than a wrong number it blames on
#: the feed -- which is exactly what happened when this list said 6-5 for a
#: final Chelsea hosted and lost 5-6.
#:
#: Two of the three are goalless, which is also the case the clean-sheet rule
#: turns on: a 0-0 shootout earns the point for both sides.
KNOWN_SHOOTOUTS = [
    # home side          goals   penalties
    ("copadelrey", date(2024, 4, 6), "Copa del Rey final",
     "Athletic Club", (1, 1), (4, 2)),   # Athletic won it at home
    ("facup", date(2022, 5, 14), "FA Cup final",
     "Chelsea", (0, 0), (5, 6)),         # Liverpool won it from away
    ("efl_cup", date(2022, 2, 27), "EFL Cup final",
     "Chelsea", (0, 0), (10, 11)),       # Liverpool won it from away
]


def scoring_keys(entry: dict) -> dict:
    """Everything on a competitor that could be carrying a shootout score."""
    return {
        key: value for key, value in entry.items()
        if isinstance(value, (str, int, float, bool))
        and ("score" in key.lower() or "shootout" in key.lower()
             or "penalt" in key.lower() or key in ("winner", "homeAway"))
    }


def check(event: dict, label: str, home_side=None, goals=None,
          penalties=None) -> bool:
    """Print one match's scoring fields, and say whether it read correctly."""
    from whul.sources.espn import _competitor, _soccer_rows

    inner = (event.get("competitions") or [{}])[0]
    home, away = _competitor(inner, "home"), _competitor(inner, "away")
    if not home or not away:
        print("    competitors missing -- cannot read this event")
        return False

    print(f"\n  {label}: {(event.get('name') or '').strip()}")
    for side, entry in (("home", home), ("away", away)):
        team = (entry.get("team") or {}).get("displayName", "?")
        print(f"    {side:<5}{team:<28}{json.dumps(scoring_keys(entry))}")

    rows = _soccer_rows(event, "", date.today(), "")
    if not rows:
        print("    the adapter dropped this event (not completed?)")
        return False
    row = rows[0]
    read_goals = (row["goals_for"], row["goals_against"])
    read_pens = (row["shootout_for"], row["shootout_against"])
    print(f"    adapter reads: goals {read_goals[0]:.0f}-{read_goals[1]:.0f}"
          f", shootout {read_pens[0]:.0f}-{read_pens[1]:.0f}")

    if goals is None:
        # Ad-hoc date: nothing to compare against, so only describe.
        if read_goals[0] == read_goals[1] and read_pens[0] != read_pens[1]:
            print("    -> a shootout, read as one.")
            return True
        print("    -> not read as a shootout. That is correct for a tie won in")
        print("       normal time, and wrong only if this one was drawn.")
        return False

    # Everything below is the home side's, since that is the row taken above.
    # Check whose row it is first: a mismatch here means this script has the
    # tie the wrong way round, and reporting that as a scoreline error would
    # blame the feed for a mistake in the table.
    if home_side and row["team"] != home_side:
        print(f"    -> this script expected {home_side} at home, and the feed")
        print(f"       says {row['team']}. The table above is wrong, not the feed;")
        print("       the figures in it are the home side's.")
        return False
    if read_goals != tuple(float(g) for g in goals):
        print(f"    -> WRONG. It finished {goals[0]}-{goals[1]}. Penalties folded")
        print("       into `score` would look exactly like this, and would pay a")
        print("       full win and a margin bonus for a drawn match.")
        return False
    if read_pens != tuple(float(p) for p in penalties):
        print(f"    -> WRONG. The shootout was {penalties[0]}-{penalties[1]},"
              f" read as {read_pens[0]:.0f}-{read_pens[1]:.0f}.")
        print("       A shootout read as a draw underpays whoever won it.")
        return False
    print(f"    -> correct: {goals[0]}-{goals[1]}, "
          f"{penalties[0]}-{penalties[1]} on penalties.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", help="one league key instead of the known list")
    parser.add_argument("--date", help="YYYY-MM-DD, with --league")
    args = parser.parse_args()

    from whul.sources.espn import scoreboard

    ad_hoc = bool(args.league and args.date)
    if ad_hoc:
        wanted = [(args.league, date.fromisoformat(args.date), "requested",
                   None, None, None)]
    elif args.league or args.date:
        print("--league and --date go together.", file=sys.stderr)
        return 2
    else:
        wanted = KNOWN_SHOOTOUTS

    print(__doc__.split("Run it")[0].strip())
    checked = passed = 0
    for league, day, label, home_side, goals, penalties in wanted:
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
        # A final is the only tie on its date, but a league key can return a
        # full card, so check them all and count only the ones that matched.
        for event in events:
            checked += 1
            if check(event, label, home_side, goals, penalties):
                passed += 1

    print(f"\n{'=' * 68}")
    if ad_hoc:
        print(f"{passed} of {checked} event(s) on that date read as a shootout.")
        return 0 if passed else 1
    if passed == len(wanted):
        print(f"All {passed} known shootout(s) read exactly right: the penalties")
        print("are beside the score, not inside it, and the adapter finds them.")
        return 0
    print(f"{passed} of {len(wanted)} known shootout(s) read correctly.")
    print("Read the printed keys above before trusting the shootout rule --")
    print("either the tie is not where this script says it is, or the feed")
    print("has changed where it puts the penalties.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
