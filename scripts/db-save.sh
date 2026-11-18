#!/usr/bin/env bash
# Replace the `data` branch with one commit holding the database, gzipped.
#
# Gzipped because GitHub refuses a file over 100 MB, and the raw database
# passed that on 18 November 2026 (see db-restore.sh). A fresh orphan commit
# every time, force-pushed, so the branch never accumulates a history of
# hundred-megabyte blobs.
#
#   scripts/db-save.sh "commit message"
set -euo pipefail

message="${1:?a commit message}"
[ -f data/whul.sqlite3 ] || { echo "No data/whul.sqlite3 to save." >&2; exit 1; }

gzip -9 -n -c data/whul.sqlite3 > data/whul.sqlite3.gz
echo "Saving data/whul.sqlite3: $(du -h data/whul.sqlite3 | cut -f1), $(du -h data/whul.sqlite3.gz | cut -f1) gzipped."

git config user.name "whul-bot"
git config user.email "whul-bot@users.noreply.github.com"
git checkout -q --orphan data-tmp
# -f: the database's staged content can differ from both the file and HEAD,
# and without it the unstage is refused and the whole tree rides along.
git rm -rqf --cached .
git add -f data/whul.sqlite3.gz
git commit -qm "$message"
git push -f origin data-tmp:data
