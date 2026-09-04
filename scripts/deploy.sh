#!/usr/bin/env bash
#
# Take whatever is benchmarked today and put it on the web.
#
# The site is a batch job and a folder of HTML, so deploying is: freeze a
# scale, pull the leagues that are in season, roll up, build, and push the
# database to the `data` branch. GitHub Actions does the rest.
#
# Every step is safe to repeat. Nothing here computes a benchmark -- that is
# hours of work and a deliberate act; this only adopts one that already exists.
#
#   ./scripts/deploy.sh                 # freeze the newest draft, publish
#   ./scripts/deploy.sh <version>       # publish against a named version
#   INGEST_ONLY=1 ./scripts/deploy.sh   # skip the freeze, refresh the results
#
# A league with no benchmark is not a reason to wait. It records its raw
# figures, scores nothing, and the About page names it -- which is a season in
# progress rather than a season misreported.

set -u
cd "$(dirname "$0")/.." || exit 1

PY=.venv/bin/python
SEASON=${SEASON:-2026-27}
BRANCH=${BRANCH:-claude/fantasy-league-webapp-dp99e3}

# The leagues that have played since their own start date. Out-of-season
# leagues are left out because a pull that finds nothing is minutes of
# requests to learn that nobody played.
LEAGUES=${LEAGUES:-"nfl nfl-teams tennis pga motorsports mlb mlb-teams \
ncaaf epl laliga seriea bundesliga ligue1 mls nwsl"}

step () { printf '\n\n========== %s ==========\n' "$*"; }

# Which code this is. Three times now a deploy has run against a checkout that
# was behind the branch, and each time it took reconstructing a score by hand to
# notice -- the output of an old pipeline looks exactly like the output of a new
# one, only wrong. Printed first so it is at the top of the log.
step "the code this deploy is running"
git --no-pager log --oneline -1
BEHIND=$(git rev-list --count HEAD..@{u} 2>/dev/null || echo 0)
if [ "${BEHIND:-0}" -gt 0 ]; then
    echo
    echo "!! This checkout is $BEHIND commit(s) behind $(git rev-parse --abbrev-ref @{u})."
    echo "!! Deploying it would publish standings computed by superseded code."
    echo "!! Run: git pull"
    exit 1
fi
"$PY" - <<'CHECK'
from whul.config.league import SEASON, season_start
from whul.scoring.proration import built_in_rule
rule = built_in_rule("MLB", SEASON.label)
print(f"  season      {SEASON.label}")
print(f"  MLB starts  {season_start('MLB')}")
print(f"  proration   {'x%.3f' % rule.factor if rule else 'none'}")
CHECK

# --- 1. a scale to score against -------------------------------------------
if [ "${INGEST_ONLY:-}" != "1" ]; then
    VERSION=${1:-}
    if [ -z "$VERSION" ]; then
        # Newest first, so this picks the draft most recently built. The
        # season is matched here because `versions` lists every season it has.
        VERSION=$("$PY" -m whul.cli benchmarks versions \
            | awk -v s="$SEASON" '$2==s && $3=="draft"{print $1; exit}')
    fi
    if [ -z "$VERSION" ]; then
        echo "No unfrozen version to adopt, and none named."
        echo "Either name one, or run ./scripts/overnight-benchmarks.sh first."
        echo
        "$PY" -m whul.cli benchmarks versions
        exit 1
    fi

    step "what this scale covers"
    "$PY" -m whul.cli benchmarks coverage "$VERSION" --season "$SEASON"

    # A scale that covers less than the one already in force is a regression,
    # and it does not announce itself: the standings still build, the page still
    # renders, and the leagues that dropped out simply score nothing. That is
    # how an 18-group version came to be published over a 30-group one.
    "$PY" - "$VERSION" "$SEASON" <<'GUARD' || exit 1
import sys

from whul import benchmarks
from whul.store import benchmarks as store_benchmarks
from whul.store import open_store

version, season = sys.argv[1], sys.argv[2]
store = open_store("data/whul.sqlite3")


def covered(label):
    table = benchmarks.coverage(store, label, season)
    if table.empty or "covered" not in table.columns:
        return 0
    return int(table["covered"].astype(bool).sum())


new = covered(version)
active = store_benchmarks.active_version(store, season)
if active is None:
    print(f"  nothing frozen yet; {version} covers {new} league/type pair(s)")
    raise SystemExit(0)

old = covered(active.version)
print(f"  {active.version} covers {old} pair(s); {version} covers {new}")
if new < old:
    print()
    print(f"  !! Freezing {version} would score fewer leagues than the version")
    print(f"  !! already in force. Nothing would fail -- the leagues that drop")
    print(f"  !! out simply score nothing, on a page that still renders.")
    print(f"  !! Add the missing leagues to it, or name the version you meant.")
    raise SystemExit(1)
GUARD

    step "freezing $VERSION"
    # Frozen, not edited ever after: the standings point at this, and a scale
    # that moves under them rewrites history silently.
    "$PY" -m whul.cli benchmarks freeze "$VERSION" || exit 1
fi

# --- 2. today's results ----------------------------------------------------
step "pulling $LEAGUES"
# Never fatal. A feed that is down must not take the site down with it:
# yesterday's standings beat none, and each problem is printed above.
"$PY" -m whul.cli ingest $LEAGUES --season "$SEASON" || true

# --- 3. standings ----------------------------------------------------------
step "rolling up every day since the season opened"
"$PY" -m whul.cli rollup --season "$SEASON" --backfill || exit 1

step "building the site"
"$PY" -m whul.cli site --season "$SEASON" || exit 1

# --- 4. publish ------------------------------------------------------------
step "pushing the database to the data branch"
# The database is the one piece of state. It lives on its own branch so a
# growing binary does not put an unreadable diff into every pull request.
#
# Built with plumbing rather than by checking out an orphan branch: this never
# touches the working tree, the index or HEAD, so an interrupted push cannot
# leave the checkout on a branch nobody meant to be on, and it works with
# uncommitted changes in the tree.
if [ ! -f data/whul.sqlite3 ]; then
    echo "No database at data/whul.sqlite3 -- nothing to publish."
    exit 1
fi

BLOB=$(git hash-object -w data/whul.sqlite3) || exit 1
INDEX=$(mktemp) || exit 1
TREE=$(
    GIT_INDEX_FILE="$INDEX" sh -c '
        git read-tree --empty &&
        git update-index --add --cacheinfo 100644,'"$BLOB"',data/whul.sqlite3 &&
        git write-tree'
) || exit 1
rm -f "$INDEX"

# No parent, and force-pushed: one commit holding one snapshot, which is what
# the nightly workflow also does. Keeping the history would mean a copy of a
# growing binary database in the repository for every night of the season, and
# the database is rebuildable from the feeds -- the history is not worth what
# it would cost to keep.
COMMIT=$(git commit-tree "$TREE" \
    -m "Database after $(date -u +%Y-%m-%dT%H:%M:%SZ)") || exit 1

pushed=0
for wait in 2 4 8 16; do
    if git push -f origin "$COMMIT:refs/heads/data"; then pushed=1; break; fi
    echo "push failed; retrying in ${wait}s"
    sleep "$wait"
done

if [ "$pushed" != "1" ]; then
    echo "Could not push the database. The site was built locally at ./site"
    exit 1
fi

cat <<NEXT


========== published ==========

The database is on the `data` branch. To put it on the web, run the
"Publish standings" workflow:

    https://github.com/smeredith15/whul/actions/workflows/publish.yml

  -> Run workflow, with
       season   $SEASON
       leagues  $LEAGUES
       backfill false

It restores this database, pulls today's results again, rebuilds and
deploys to Pages. After the first run it repeats nightly on its own.

To look at what was built before publishing it:

    $PY -m http.server -d site 8000     # then open http://localhost:8000
NEXT
