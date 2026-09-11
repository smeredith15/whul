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
