#!/usr/bin/env bash
#
# Move a benchmark computed here onto the database the nightly job owns.
#
# A scale is computed where the feeds are reachable and a laptop can run for
# hours. The results it will score live in the database the nightly job writes.
# Those are two files, and the compute takes long enough that the second has
# moved on by the time the first is finished -- so publishing the local copy
# over the branch would throw away every day recorded in between, and
# `deploy.sh` rightly refuses to.
#
# This carries the scale across instead: keep a copy, take the branch's
# database, and adopt the one version out of the copy.
#
#   ./scripts/carry-benchmark.sh              # the newest draft
#   ./scripts/carry-benchmark.sh <version>    # a named one
#
# Freezing stays a separate act. This ends by printing the deploy line rather
# than running it, because adopting a scale and scoring against it are
# different decisions and the second one is not reversible.

set -u
cd "$(dirname "$0")/.." || exit 1

PY=.venv/bin/python
SEASON=${SEASON:-2026-27}
KEPT=data/carried-from.sqlite3
REVIEW=benchmark-review.txt

step () { printf '\n\n========== %s ==========\n' "$*"; }

versions () { "$PY" -m whul.cli benchmarks versions; }
pick () { versions | awk -v s="$SEASON" -v want="$1" '$2==s && $3==want{print $1; exit}'; }

VERSION=${1:-}
[ -z "$VERSION" ] && VERSION=$(pick draft)
if [ -z "$VERSION" ]; then
    echo "No unfrozen version here to carry, and none named."
    echo
    versions
    exit 1
fi
ACTIVE=$(pick FROZEN)

step "what $VERSION would change"
if [ -n "$ACTIVE" ]; then
    # Kept as a file as well as printed: this is the record of what was adopted
    # and why, and it is the one thing worth reading twice before freezing.
    "$PY" -m whul.cli benchmarks compare "$ACTIVE" "$VERSION" | tee "$REVIEW"
    echo "  (also written to $REVIEW)"
else
    echo "  nothing frozen yet, so there is nothing to compare against"
fi

step "keeping this database"
cp data/whul.sqlite3 "$KEPT" || exit 1
echo "  $KEPT"

step "taking the branch's database"
# Into a temporary file first. Redirecting straight onto the database truncates
# it before git has written a byte, so a failed fetch would destroy the copy
# this whole script exists to preserve.
TMP=$(mktemp) || exit 1
if ! git fetch --quiet origin data || \
   ! git show origin/data:data/whul.sqlite3 > "$TMP"; then
    echo "  Could not read the branch's database. Nothing has been changed;"
    echo "  $KEPT still holds everything, as does data/whul.sqlite3."
    rm -f "$TMP"
    exit 1
fi
mv "$TMP" data/whul.sqlite3 || exit 1
echo "  data/whul.sqlite3 is now the branch's copy"

step "carrying $VERSION across"
if ! "$PY" -m whul.cli benchmarks adopt "$VERSION" --from "$KEPT"; then
    echo
    echo "  The scale did not come across, so nothing should be frozen."
    echo "  $KEPT still holds it; put it back with"
    echo
    echo "      cp $KEPT data/whul.sqlite3"
    exit 1
fi

step "next"
cat <<NEXT
  Read $REVIEW, then publish:

      printf 'GitHub token: '; stty -echo
      read -r GITHUB_TOKEN; stty echo; echo
      export GITHUB_TOKEN
      ./scripts/deploy.sh $VERSION

  Then run the Publish standings workflow with backfill checked.

  $KEPT is no longer needed once that has run, and is ignored by git.
NEXT
