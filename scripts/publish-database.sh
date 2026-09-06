#!/usr/bin/env bash
#
# Push data/whul.sqlite3 to the `data` branch. Nothing else.
#
#   ./scripts/publish-database.sh
#
# `deploy.sh` does this as its last step, after freezing, pulling, rolling up
# and building -- any of which can stop first, and each prints its own reason
# among a page of other output. When the database is already right and only the
# push is in question, that is a lot of moving parts between a question and its
# answer. This is the one step, and it says plainly whether it worked.
#
# It reports what it is about to publish before publishing it, because pushing
# the wrong database over this branch is the one mistake here that costs
# something -- the nightly job writes it too, and a force-push discards
# whatever it recorded.

set -u
cd "$(dirname "$0")/.." || exit 1

DB=data/whul.sqlite3
SEASON=${SEASON:-2026-27}

if [ ! -f "$DB" ]; then
    echo "No database at $DB -- nothing to publish."
    exit 1
fi

echo
echo "About to publish $DB:"
.venv/bin/python - "$DB" "$SEASON" <<'SHOW'
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
season = sys.argv[2]
row = db.execute(
    "SELECT version FROM benchmark_versions WHERE frozen_at IS NOT NULL "
    "AND season = ? ORDER BY frozen_at DESC LIMIT 1", (season,)).fetchone()
print(f"  frozen scale   {row[0] if row else 'NONE -- nothing would score'}")
if row:
    groups = db.execute(
        "SELECT COUNT(*) FROM benchmarks WHERE version = ?", (row[0],)).fetchone()[0]
    print(f"  groups         {groups}")
days = [r[0] for r in db.execute(
    "SELECT DISTINCT as_of FROM daily_scores WHERE season = ? ORDER BY as_of",
    (season,))]
print(f"  days of scores {len(days)}"
      + (f"  ({days[0]} to {days[-1]})" if days else ""))
SHOW
echo

# The editor's askpass points at a socket inside its own process. When that
# socket has gone, git does not fall back to asking: the helper errors, git
# goes anonymous, and the push is refused. Clearing it is what lets the token
# below, or a terminal prompt, actually be reached.
unset GIT_ASKPASS SSH_ASKPASS
unset VSCODE_GIT_ASKPASS_NODE VSCODE_GIT_ASKPASS_MAIN VSCODE_GIT_ASKPASS_EXTRA_ARGS
unset VSCODE_GIT_IPC_HANDLE
export GIT_TERMINAL_PROMPT=1

if [ -z "${GITHUB_TOKEN:-}" ]; then
    printf 'GitHub token (Contents: read and write): '
    stty -echo
    read -r GITHUB_TOKEN
    stty echo
    echo
    export GITHUB_TOKEN
fi

# Ask GitHub about the token before asking git to use it. "Invalid username or
# token" is what git reports for every credential fault alike -- expired,
# revoked, wrong scope, or right token and wrong repository -- and those have
# different fixes.
#
# Read from `permissions.push` rather than from the status code: this
# repository answers 200 to an unauthenticated request, so a status check
# passes a bogus token straight through. `permissions` appears only when the
# request was authenticated, which is exactly the question.
#
# Passed through --config on stdin rather than as an argument, so the token
# does not appear in the process list.
echo "Checking the token ..."
VERDICT=$(
    printf 'header = "Authorization: Bearer %s"\nheader = "Accept: application/vnd.github+json"\n' "$GITHUB_TOKEN" \
    | curl -sS --config - https://api.github.com/repos/smeredith15/whul 2>/dev/null \
    | .venv/bin/python -c '
import json, sys
try:
    body = json.load(sys.stdin)
except Exception:
    print("unreachable"); raise SystemExit
perms = body.get("permissions")
if perms is None:
    print("anonymous")
elif perms.get("push"):
    print("ok")
else:
    print("readonly")
'
) || VERDICT=unreachable

case "$VERDICT" in
    ok)
        echo "  the token can write to smeredith15/whul"
        ;;
    anonymous|readonly)
        # Not split further on purpose. This repository answers an
        # unauthenticated request with a permissions block of its own, so a
        # rejected token and a read-only one are indistinguishable from here --
        # and the fix is the same list either way.
        cat <<'BAD'

  This token cannot push to smeredith15/whul, so the push below would fail
  the way it just did. One of:

    - expired or revoked
    - truncated when pasted (they are long; paste rather than type)
    - Contents is set to read, not read *and write*
    - fine-grained, and this repository is not in its list. "All
      repositories" on the account is not the same as this repository
      being selected.

  Settings -> Developer settings -> Personal access tokens.

BAD
        exit 1
        ;;
    *)
        echo "  could not reach the API to check; trying the push anyway"
        ;;
esac
echo

# One commit holding one snapshot, with no parent, built through a temporary
# index so the working tree, the real index and HEAD are all left alone.
BLOB=$(git hash-object -w "$DB") || exit 1
INDEX=$(mktemp) || exit 1
TREE=$(
    GIT_INDEX_FILE="$INDEX" sh -c '
        git read-tree --empty &&
        git update-index --add --cacheinfo 100644,'"$BLOB"',data/whul.sqlite3 &&
        git write-tree'
) || { rm -f "$INDEX"; exit 1; }
rm -f "$INDEX"
COMMIT=$(git commit-tree "$TREE" -m "Database after $(date -u +%Y-%m-%dT%H:%M:%SZ)") || exit 1

AUTH=(-c credential.helper=)
AUTH+=(-c "credential.helper=!f() { echo username=x-access-token; echo \"password=\$GITHUB_TOKEN\"; }; f")

echo "Pushing ..."
if OUT=$(git "${AUTH[@]}" push -f origin "$COMMIT:refs/heads/data" 2>&1); then
    echo
    echo "PUBLISHED. The data branch now holds this database."
    echo "Next: Actions -> Publish standings -> Run workflow, with backfill checked."
    exit 0
fi

echo "$OUT"
echo
echo "NOT PUBLISHED. The database here is untouched, so nothing is lost and"
echo "this is safe to run again once the reason above is dealt with."
case "$OUT" in
    *"Authentication failed"*|*"could not read Username"*|*"no anonymous write"*|*"Invalid username"*)
        echo
        echo "That is a credential problem. The token needs Contents: read and"
        echo "write on this repository, and a fine-grained token must list this"
        echo "repository explicitly -- 'All repositories' is not the same thing."
        ;;
esac
exit 1
