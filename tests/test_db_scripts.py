"""The database's trip to and from the `data` branch, run for real.

The file passed GitHub's 100 MB limit on 18 November 2026 and the night's
Publish could not save it, so the branch holds it gzipped. These run the two
scripts against a throwaway remote, because a workflow's mistakes are found by
a run that reported success and did nothing.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("db-restore.sh", "db-save.sh")
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("gzip") is None,
                                reason="needs git and gzip")


def _git(cwd, *args, check=True):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=check,
                          capture_output=True, text=True)


def _run(cwd, script, *args):
    return subprocess.run(["bash", str(cwd / script), *args], cwd=cwd,
                          capture_output=True, text=True)


@pytest.fixture
def clone(tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(remote), str(work))
    (work / "README").write_text("code\n")
    _git(work, "add", "README")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "origin", "HEAD:main")
    for name in SCRIPTS:
        shutil.copy(ROOT / "scripts" / name, work / name)
    return work


def _old_style_branch(work, content: bytes):
    """A data branch as every save before November 2026 left it: the raw file."""
    (work / "data").mkdir(exist_ok=True)
    (work / "data" / "whul.sqlite3").write_bytes(content)
    _git(work, "checkout", "-q", "--orphan", "old")
    _git(work, "rm", "-rqf", "--cached", ".")
    _git(work, "add", "-f", "data/whul.sqlite3")
    _git(work, "commit", "-qm", "old")
    _git(work, "push", "-q", "-f", "origin", "old:data")
    _git(work, "checkout", "-q", "-f", "main")
    shutil.rmtree(work / "data", ignore_errors=True)


def test_a_raw_database_saves_gzipped_and_comes_back_whole(clone):
    content = os.urandom(200_000) + b"\0" * 2_000_000
    _old_style_branch(clone, content)

    restored = _run(clone, "db-restore.sh")
    assert restored.returncode == 0, restored.stderr
    assert (clone / "data" / "whul.sqlite3").read_bytes() == content
    # Read, not checked out: nothing left staged to ride into a commit.
    assert _git(clone, "diff", "--cached", "--name-only").stdout == ""

    saved = _run(clone, "db-save.sh", "Database after today")
    assert saved.returncode == 0, saved.stderr
    _git(clone, "fetch", "-q", "origin", "data")
    tree = _git(clone, "ls-tree", "-r", "-l", "origin/data").stdout.split()
    # mode, type, object, size, path -- and nothing else on the branch.
    assert len(tree) == 5 and tree[4] == "data/whul.sqlite3.gz", tree
    assert int(tree[3]) < len(content) / 2, "stored compressed"
    # One commit with no parent: the branch keeps no history of databases.
    assert _git(clone, "rev-list", "--count", "origin/data").stdout.strip() == "1"

    _git(clone, "checkout", "-q", "-f", "main")
    shutil.rmtree(clone / "data", ignore_errors=True)
    again = _run(clone, "db-restore.sh")
    assert again.returncode == 0, again.stderr
    assert (clone / "data" / "whul.sqlite3").read_bytes() == content


def test_a_missing_data_branch_is_an_error_unless_it_may_be(clone):
    assert _run(clone, "db-restore.sh").returncode == 1
    optional = _run(clone, "db-restore.sh", "--optional")
    assert optional.returncode == 0 and not (clone / "data" / "whul.sqlite3").exists()


def test_the_save_unstages_forcibly_and_does_not_swallow_it():
    """The checks the inline save steps had, on the script they moved into."""
    lines = [ln.strip() for ln in (ROOT / "scripts/db-save.sh").read_text().splitlines()]
    unstage = [ln for ln in lines if ln.startswith("git rm ")]
    assert unstage == ["git rm -rqf --cached ."]
    assert "git add -f data/whul.sqlite3.gz" in lines


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_goes_through_the_scripts(path):
    """A workflow reading the raw file finds nothing once the branch holds the
    gzipped one, and one saving it raw is refused past 100 MB."""
    text = path.read_text()
    assert "origin/data:" not in text and "origin/data --" not in text, path.name
    assert "git add -f data/whul.sqlite3\n" not in text, path.name
    assert "data-tmp" not in text, path.name
