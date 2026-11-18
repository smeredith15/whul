#!/usr/bin/env bash
# Put the database from the `data` branch at data/whul.sqlite3.
#
# The branch holds it gzipped, as data/whul.sqlite3.gz: the raw file reached
# 100.56 MB on 18 November 2026 and GitHub refuses any file over 100 MB, so the
# night's Publish built everything and then could not save it. Compressed it is
# about a tenth of that. A branch still holding the raw file -- every save
# before this script existed -- is read as it is, so the first run after the
# change finds its database either way.
#
# Read with `git show`, never `git checkout <tree> -- <path>`: that stages the
# file as well as writing it, and a staged database rode into commits it had
# no business in more than once.
#
#   --optional   a missing data branch is not an error (a first-ever run)
set -euo pipefail

optional=false
[ "${1:-}" = "--optional" ] && optional=true

mkdir -p data
if ! git ls-remote --exit-code --heads origin data >/dev/null 2>&1; then
  if $optional; then
    echo "No data branch yet."
    exit 0
  fi
  echo "No data branch, so there is no database to restore." >&2
  exit 1
fi

git fetch -q origin data --depth 1
if git cat-file -e origin/data:data/whul.sqlite3.gz 2>/dev/null; then
  git show origin/data:data/whul.sqlite3.gz | gunzip > data/whul.sqlite3
elif git cat-file -e origin/data:data/whul.sqlite3 2>/dev/null; then
  git show origin/data:data/whul.sqlite3 > data/whul.sqlite3
else
  echo "The data branch holds no database." >&2
  $optional || exit 1
  exit 0
fi
echo "Restored data/whul.sqlite3 ($(du -h data/whul.sqlite3 | cut -f1))."
