#!/usr/bin/env python3
"""Does the Stats API say which division each club played in?

A division title is worth 5 points and is the one team scoring term a schedule
cannot supply. This asks the endpoint the scoring now depends on, and reports
what came back -- before anything is built on the answer.

Run it from a machine that can reach statsapi.mlb.com:

    python scripts/probe-mlb-divisions.py
    python scripts/probe-mlb-divisions.py --seasons 2021 2022 2023 2024 2025

What a good answer looks like: 30 clubs a season, six divisions, five clubs in
each. Anything else is reported rather than smoothed over -- the failure this
guards against is a feed that answers 200 with a plausible subset.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXPECTED_CLUBS = 30
EXPECTED_DIVISIONS = 6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, nargs="+",
                    default=[2021, 2022, 2023, 2024, 2025, 2026])
    args = ap.parse_args()

    from whul.sources import mlb as source

    ok = True
    for season in args.seasons:
        try:
            frame = source.load_divisions([season])
        except Exception as exc:  # noqa: BLE001 -- the point is to report it
            print(f"  {season}  FAILED  {type(exc).__name__}: {exc}")
            ok = False
            continue

        if frame.empty:
            print(f"  {season}  no rows -- the scoring would award no division "
                  f"title at all for this season")
            ok = False
            continue

        clubs = len(frame)
        sizes = frame.groupby("division").size().sort_index()
        verdict = []
        if clubs != EXPECTED_CLUBS:
            verdict.append(f"expected {EXPECTED_CLUBS} clubs")
        if len(sizes) != EXPECTED_DIVISIONS:
            verdict.append(f"expected {EXPECTED_DIVISIONS} divisions")
        odd = sizes[sizes != clubs / max(len(sizes), 1)]
        if len(sizes) and not odd.empty and len(set(sizes)) > 1:
            verdict.append("uneven divisions: " + ", ".join(
                f"{name} {n}" for name, n in odd.items()))
        ok = ok and not verdict

        print(f"  {season}  {clubs} clubs, {len(sizes)} divisions"
              + ("   <-- " + "; ".join(verdict) if verdict else "   ok"))
        for name, n in sizes.items():
            print(f"           {name:<22} {n}")

    print()
    if ok:
        print("Every season came back whole. The division title is scoreable.")
    else:
        print("Something is off above. A benchmark computed from this would set "
              "the bar low for whoever is missing a title, so fix it before "
              "computing one -- the benchmark source raises rather than "
              "defaulting, so it will stop you.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
