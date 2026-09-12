"""Make a small copy of the tennis2026 database, holding only what WHUL reads.

The app's own file is about 83 MB, most of it predictions, draws and scraper
history that nothing here touches. WHUL's query joins three tables --
match_results, tournaments and players -- and those come to a few megabytes,
which is the difference between a browser upload that works and one that does
not.

Run it on the machine where the tennis2026 app lives:

    python3 scripts/export-tennis2026.py ~/projects/tennis2026/backend/tennis2026.db

It writes tennis2026-whul.db in the current directory. Move that to the machine
running WHUL and point WHUL_TENNIS2026_DB at it.

The source is opened read-only, so a scraper writing to it mid-copy is not a
reason to lose the copy.
"""
import sqlite3
import sys
from pathlib import Path

#: Exactly the tables whul/sources/tennis2026.py joins. Anything else in the
#: app's database is its own business and none of WHUL's.
TABLES = ("match_results", "tournaments", "players")


def export(source: Path, out: Path) -> int:
    if not source.exists():
        print(f"No database at {source}")
        print("Pass the path: python3 scripts/export-tennis2026.py path/to/tennis2026.db")
        return 1

    out.unlink(missing_ok=True)
    conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        present = {
            row[0] for row in
            conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing = [t for t in TABLES if t not in present]
        if missing:
            print(f"{source} has no {missing}. Tables it does have: "
                  f"{sorted(present)[:20]}")
            return 1

        conn.execute("ATTACH DATABASE ? AS slim", (str(out),))
        for table in TABLES:
            conn.execute(f"CREATE TABLE slim.{table} AS SELECT * FROM {table}")
        conn.commit()
        conn.execute("DETACH DATABASE slim")
    finally:
        conn.close()

    # VACUUM cannot run inside the attaching connection's transaction.
    with sqlite3.connect(out) as slim:
        slim.execute("VACUUM")

    with sqlite3.connect(f"file:{out}?mode=ro", uri=True) as slim:
        wins, first, last = slim.execute(
            "SELECT COUNT(*), MIN(match_date), MAX(match_date) "
            "FROM match_results WHERE won = 1"
        ).fetchone()
        events = slim.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0]
        players = slim.execute("SELECT COUNT(*) FROM players").fetchone()[0]

    print(f"\nwrote {out}")
    print(f"  size    {out.stat().st_size / 1e6:.1f} MB "
          f"(from {source.stat().st_size / 1e6:.0f} MB)")
    print(f"  wins    {wins:,}  ({first} -> {last})")
    print(f"  events  {events:,}")
    print(f"  players {players:,}")
    print("""
Move it to the machine running WHUL, then there:

    export WHUL_TENNIS2026_DB=~/whul/data/tennis2026.db
    python -m whul.cli probe tennis2026
""")
    return 0


if __name__ == "__main__":
    default = Path("backend/tennis2026.db")
    source = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else default
    target = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 \
        else Path("tennis2026-whul.db")
    raise SystemExit(export(source, target))
