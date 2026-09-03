"""Session-wide checks that run before any test does.

A declared dependency that is not installed produces failures that look like
code bugs. That is what happened on a checkout whose virtual environment
predated two dependencies: four tests failed with ``ModuleNotFoundError: bs4``
and an ``IndexError`` from a function that degrades quietly without a parser,
and the spreadsheet importer then failed separately with a third message. None
of the three said the environment was stale.

So the environment is checked once, up front, and the run stops with an
instruction rather than a scatter of unrelated-looking failures.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

#: Declared runtime dependency -> the module it provides. Kept beside the
#: dependency list in pyproject.toml; a name added there wants a line here, or
#: its absence goes back to looking like a bug in whatever imports it.
REQUIRED = {
    "pandas": "pandas",
    "pyarrow": "pyarrow",
    "requests": "requests",
    "beautifulsoup4": "bs4",
    "lxml": "lxml",
    "pyreadr": "pyreadr",
    "openpyxl": "openpyxl",
}


def pytest_sessionstart(session: pytest.Session) -> None:
    missing = sorted(
        package for package, module in REQUIRED.items()
        if importlib.util.find_spec(module) is None
    )
    if not missing:
        return

    venv = sys.prefix
    has_pip = importlib.util.find_spec("pip") is not None
    # A venv built by `uv venv` has no pip in it, which is the state that made
    # the obvious `.venv/bin/pip install` fail with "No such file or directory"
    # and left the environment half-updated.
    fix = (
        f"{venv}/bin/pip install -e '.[dev]'"
        if has_pip
        else f"uv pip install --python {venv}/bin/python -e '.[dev]'"
    )
    raise pytest.UsageError(
        f"\n\nThis environment is missing {len(missing)} declared "
        f"dependenc{'y' if len(missing) == 1 else 'ies'}: {', '.join(missing)}.\n"
        f"They are in pyproject.toml, so the checkout is fine and the "
        f"environment is stale -- most likely it was built before these were "
        f"added.\n\nReinstall, then re-run:\n\n    {fix}\n"
    )
