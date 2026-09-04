#!/usr/bin/env bash
#
# Compute every league into one new benchmark version, cheapest first.
#
# One version, not eight: the scale is a single artifact the standings point
# at, and a half-league version is a scale with holes in it. The first batch
# starts the version and every batch after adds to it, so a league that fails
# costs that league and nothing else.
#
# Nothing here freezes anything. Read the output in the morning, then freeze.
#
#   nohup ./scripts/overnight-benchmarks.sh > ~/benchmarks.log 2>&1 &
#   tail -f ~/benchmarks.log

set -u
cd "$(dirname "$0")/.." || exit 1

PY=.venv/bin/python
SEASON=2026-27

batch () {
    local mode=$1; shift
    printf '\n\n========== %s ==========\n' "$*"
    date -u +'started %Y-%m-%d %H:%M:%SZ'
    if [ "$mode" = "new" ]; then
        "$PY" -m whul.cli benchmarks compute "$@" --season "$SEASON" --save
    else
        "$PY" -m whul.cli benchmarks compute "$@" --season "$SEASON" --save --into
    fi
    date -u +'finished %Y-%m-%d %H:%M:%SZ'
}

# Verified and local first, so a failure later still leaves a reviewable set.
batch new  nfl nfl-teams tennis
batch into pga motorsports

# API-per-season: minutes each.
batch into nhl nhl-teams
batch into mlb mlb-teams

# Club soccer players. FBref answers 403 from behind Cloudflare, so this batch
# is expected to fail until `scripts/probe-soccer-players.py` says which source
# to build against instead. It is left in deliberately: a batch that fails costs
# that batch and nothing else, and the version records the hole rather than
# hiding it.
batch into soccer-players

# Date-walked. The European competitions are shared between the leagues and
# cached under their own name, so the first soccer batch pays for the rest.
batch into epl laliga seriea
batch into bundesliga ligue1 mls nwsl
batch into ncaaf ncaam ncaaw ncaabaseball ncaasoftball

# Last, and by far the longest: ESPN box scores, one date at a time.
batch into nba nba-teams

printf '\n\n========== what it produced ==========\n'
VERSION=$("$PY" -m whul.cli benchmarks versions | awk '$3=="draft"{print $1; exit}')
if [ -z "$VERSION" ]; then
    echo "No draft version was created -- the first batch must have failed."
    exit 1
fi
echo "version: $VERSION"
"$PY" -m whul.cli benchmarks coverage "$VERSION" --season "$SEASON"

cat <<NEXT

Nothing is frozen. Read the run above for FAILED lines and thin pools, then:

    .venv/bin/python -m whul.cli benchmarks compare <old> $VERSION
    .venv/bin/python -m whul.cli benchmarks freeze $VERSION
    .venv/bin/python -m whul.cli rollup --season $SEASON --backfill
NEXT
