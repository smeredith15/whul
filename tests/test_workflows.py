"""The workflow files, checked for the mistakes YAML will not catch.

A workflow cannot be unit tested the way the code it runs can, and its
mistakes are expensive in a way a test failure is not: they are found by a
run that reported success and did nothing.
"""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github/workflows").glob("*.yml"))


def _steps(path: Path):
    spec = yaml.safe_load(path.read_text())
    for job in (spec.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            yield step


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_a_workflow_parses(path):
    assert yaml.safe_load(path.read_text())


#: `a && b || c` in a GitHub expression is not a ternary. The operators return
#: values, so when `b` is falsy -- the empty string above all -- a true `a`
#: yields `b`, which is falsy, and the expression falls through to `c`. The
#: retraction workflow said `${{ inputs.apply && '' || '--dry-run' }}` and
#: dry-ran every run including the one meant to write, while its sibling
#: `${{ inputs.all && '--all' || '' }}` worked -- the same construct, right
#: only because '--all' happens to be a truthy string.
EMPTY_TRUE_BRANCH = re.compile(r"&&\s*''\s*\|\|")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_expression_returns_the_empty_string_when_true(path):
    # Comments are skipped: this file's own explanation of the bug quotes it.
    hit = [line.strip() for line in path.read_text().splitlines()
           if EMPTY_TRUE_BRANCH.search(line) and not line.lstrip().startswith("#")]
    assert not hit, (
        f"{path.name} uses `&& '' ||`, which is not a ternary: a true condition "
        f"yields '', which is falsy, so the expression takes the right-hand "
        f"branch anyway. Build the flags in the shell instead. {hit}"
    )


#: A pipeline exits with its last command's status, and the default shell is
#: `bash -e`, not `bash -eo pipefail`. A step that pipes a command into `tee`
#: reports whatever `tee` did, which is always success.
@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_a_step_that_pipes_into_tee_asks_for_pipefail(path):
    bad = []
    for step in _steps(path):
        run = step.get("run") or ""
        if "| tee" not in run:
            continue
        if "set -o pipefail" in run or step.get("continue-on-error"):
            continue
        bad.append(step.get("name") or run.splitlines()[0])
    assert not bad, (
        f"{path.name}: {bad} pipe into `tee` without `set -o pipefail`, so the "
        f"step reports success however the command exited"
    )


#: `git rm --cached` refuses a path whose staged content differs from both the
#: working tree and HEAD, and the database always does: the run rewrites it
#: before this step. `git checkout --orphan` keeps the index, so the unstage is
#: the only thing standing between the orphan commit and the whole source tree.
#: It errored on the database and stopped there, `|| true` swallowed it, and
#: three workflows force-pushed a full copy of the repository to the data
#: branch every night. `-f` is what makes the unstage actually happen.
@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_orphan_save_unstages_forcibly(path):
    bad = []
    for step in _steps(path):
        run = step.get("run") or ""
        if "--orphan" not in run:
            continue
        for line in run.splitlines():
            line = line.strip()
            if not line.startswith("git rm "):
                continue
            if "--cached" in line and "f" not in line.split("--cached")[0].replace(
                    "git rm ", ""):
                bad.append(f"{step.get('name') or 'step'}: {line}")
    assert not bad, (
        f"{path.name}: {bad} -- `git rm --cached` without `-f` refuses the "
        f"database, whose staged content differs from both the file and HEAD, "
        f"and leaves the rest of the tree staged into the orphan commit"
    )


#: The same step, from the other side: swallowing the unstage is what let the
#: fault run for months without a single red run.
@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_orphan_save_does_not_swallow_the_unstage(path):
    bad = [
        line.strip() for step in _steps(path)
        if "--orphan" in (step.get("run") or "")
        for line in (step.get("run") or "").splitlines()
        if line.strip().startswith("git rm ") and "|| true" in line
    ]
    assert not bad, (
        f"{path.name}: {bad} -- `|| true` hides an unstage that failed, which "
        f"is precisely how the whole repository reached the data branch"
    )
