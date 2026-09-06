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
ncaaf epl laliga seriea bundesliga ligue1 mls nwsl soccer-players"}

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
# And which database. The last step force-pushes the local file over the `data`
# branch, and the nightly workflow writes that branch too -- so a local copy
# that has not been refreshed since the workflow last ran would silently throw
# away every day it recorded in between. The code check above catches a stale
# checkout; this catches a stale database, which looks identical afterwards.
step "the database this deploy would publish"
if git fetch --quiet origin data 2>/dev/null && \
   git cat-file -e origin/data:data/whul.sqlite3 2>/dev/null; then
    THEIRS=$(mktemp) || exit 1
    git show origin/data:data/whul.sqlite3 > "$THEIRS" || exit 1
    "$PY" - "$THEIRS" data/whul.sqlite3 "$SEASON" <<'DBCHECK'
import sqlite3
import sys

theirs, ours, season = sys.argv[1], sys.argv[2], sys.argv[3]


def days(path):
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        return {r[0] for r in db.execute(
            "SELECT DISTINCT as_of FROM daily_scores WHERE season = ?", (season,))}
    except sqlite3.Error:
        return set()


here, there = days(ours), days(theirs)
missing = sorted(there - here)
print(f"  local {len(here)} day(s), data branch {len(there)}")
if not missing:
    print("  nothing on the branch that is not here; safe to publish over it")
    raise SystemExit(0)
shown = ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "")
print()
print(f"  !! The data branch has {len(missing)} day(s) this copy does not: {shown}")
print("  !! Publishing would force-push over them and they are not recomputable")
print("  !! -- a feed reports today, not last Tuesday.")
print()
print("  Refresh this copy first, keeping whatever you computed here:")
print()
print("      git fetch origin data")
print("      git show origin/data:data/whul.sqlite3 > data/whul.sqlite3")
print()
print("  A benchmark draft computed locally lives in this file and would be")
print("  lost by that. Freeze and publish it BEFORE refreshing, or recompute")
print("  it after -- `benchmarks versions` says whether you have one.")
raise SystemExit(1)
DBCHECK
    rc=$?
    rm -f "$THEIRS"
    [ "$rc" -ne 0 ] && exit 1
else
    echo "  no data branch yet, or it carries no database -- nothing to lose"
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
    NAMED=1
    if [ -z "$VERSION" ]; then
        NAMED=0
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
    set +e
    "$PY" - "$VERSION" "$SEASON" "$NAMED" <<'GUARD'
import sys

from whul import benchmarks
from whul.store import benchmarks as store_benchmarks
from whul.store import open_store

version, season = sys.argv[1], sys.argv[2]
named = sys.argv[3] == "1"
store = open_store("data/whul.sqlite3")


def covered(label):
    table = benchmarks.coverage(store, label, season)
    if table.empty or "covered" not in table.columns:
        return 0
    return int(table["covered"].astype(bool).sum())


new = covered(version)
active = store_benchmarks.active_version(store, season)
old = covered(active.version) if active else 0
if active:
    print(f"  {active.version} covers {old} pair(s); {version} covers {new}")
else:
    print(f"  nothing frozen yet; {version} covers {new} league/type pair(s)")

if new < old:
    print()
    print(f"  Freezing {version} would score fewer leagues than the version")
    print(f"  already in force. Nothing would fail -- the leagues that drop out")
    print(f"  simply score nothing, on a page that still renders.")
    if named:
        print()
        print(f"  !! You named this version, so this stops here rather than")
        print(f"  !! quietly adopting something else. Name the one you meant.")
        raise SystemExit(1)
    # Nobody named it: this is the newest *draft*, which after a freeze by hand
    # is whatever was left over -- an older, thinner version that was never
    # meant to be adopted. The scale in force is the better one, so keep it and
    # get on with the deploy. Refusing to publish a downgrade should not also
    # refuse to publish today's results.
    print()
    print(f"  Nothing named it, so it is the newest draft left over after a")
    print(f"  freeze by hand. Keeping {active.version} and skipping the freeze.")
    raise SystemExit(3)

# `benchmarks freeze` refuses a version with rostered assets it cannot score,
# which is right for a first freeze and wrong for every one after: the gaps --
# club soccer players with no reachable source, international soccer awaiting a
# scoring decision -- are known, and already accepted in the version this
# replaces. Exit 2 says "carry the accepted gaps over"; it is safe precisely
# because the check above established that no league is being dropped, and the
# About page names whatever stays uncovered so nobody reads the standings as
# complete. With nothing frozen yet there is nothing to have accepted, so the
# first freeze is left to refuse and be decided deliberately.
raise SystemExit(2 if active else 0)
GUARD
    FREEZE=1
    case $? in
        0) FORCE="" ;;
        2) FORCE="--force" ;;
        3) FREEZE=0 ;;
        *) set -e; exit 1 ;;
    esac
    set -e

    if [ "$FREEZE" = "1" ]; then
    step "freezing $VERSION"
    # Frozen, not edited ever after: the standings point at this, and a scale
    # that moves under them rewrites history silently.
    # shellcheck disable=SC2086 -- FORCE is a flag or empty, deliberately unquoted
    "$PY" -m whul.cli benchmarks freeze "$VERSION" $FORCE || exit 1
    fi
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

# An inherited credential helper that is broken -- an editor's, whose socket has
# gone -- does not fail over to asking: it errors, git falls back to anonymous,
# and the push is refused with no prompt. An empty `-c credential.helper=` is
# the documented way to clear the inherited list, so what follows is the only
# helper in play.
#
# With GITHUB_TOKEN set, the token is read from the environment by a helper
# rather than put in the remote URL, so it never appears in the command line,
# in `git remote -v`, or in an error message that echoes the URL back.
AUTH=(-c credential.helper=)
if [ -n "${GITHUB_TOKEN:-}" ]; then
    AUTH+=(-c "credential.helper=!f() { echo username=x-access-token; echo \"password=\$GITHUB_TOKEN\"; }; f")
fi

pushed=0
for wait in 2 4 8 16; do
    OUT=$(git "${AUTH[@]}" push -f origin "$COMMIT:refs/heads/data" 2>&1) && { pushed=1; break; }
    echo "$OUT"
    # A refused credential is not a blip. Retrying it four times just prints the
    # same failure four times and buries the one line that says what to do.
    if printf '%s' "$OUT" | grep -qiE 'Authentication failed|could not read Username|no anonymous write|invalid credentials'; then
        cat <<'AUTH'

Git could not authenticate, so the retries are pointless -- this is a
credential problem, not a network one. Everything before this step worked:
the scale is frozen, the standings are rolled up, and the site is built in
./site, which you can read right now with

    python -m http.server -d site 8000

To finish, git needs to be able to push. Two ways, quickest first:

  1. Give it a token directly. It is read from the environment, so it never
     reaches the command line, the remote URL, or an error message:

         printf 'GitHub token: '; stty -echo
         read -r GITHUB_TOKEN; stty echo; echo
         export GITHUB_TOKEN
         INGEST_ONLY=1 ./scripts/deploy.sh

     The token needs Contents: read and write on this repository. `stty -echo`
     keeps it off the screen, and reading it rather than typing it as an
     argument keeps it out of shell history.

     Spelled this way because it has to work in whatever shell the terminal
     opened with. `read -rsp` is bash; in zsh -- the default on macOS, and so
     in an editor's terminal there -- `-p` means "read from the coprocess"
     rather than "prompt", so that form does not do what it looks like.

  2. Or fix the editor's helper: reload the window (Command Palette ->
     "Developer: Reload Window") and open a fresh terminal.

  INGEST_ONLY=1 skips straight past the freeze, which has already happened.

AUTH
        exit 1
    fi
    echo "push failed; retrying in ${wait}s"
    sleep "$wait"
done

if [ "$pushed" != "1" ]; then
    echo
    echo "Could not push the database after four attempts. The site was built"
    echo "locally at ./site, and nothing before this step was lost -- re-run"
    echo "with INGEST_ONLY=1 once the push works."
    exit 1
fi

# Unquoted, because $SEASON and $LEAGUES below are meant to expand -- which
# also means backticks would run as commands, so the branch name is plain.
cat <<NEXT


========== published ==========

The database is on the data branch. To put it on the web, run the
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
