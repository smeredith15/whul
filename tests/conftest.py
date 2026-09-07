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


# --- the network -----------------------------------------------------------
#
# Every feed this project reads is unreachable from the environment it is
# developed in, so a test that quietly makes a request does not fail. It
# succeeds a little more slowly, and the assertion it was meant to make against
# real data is never made.
#
# That happened. Adding European entry to the club soccer loader made two
# existing tests fetch three Wikipedia articles apiece; they still passed,
# because the loader catches a missing article and carries on -- right in
# production, and exactly what hides it here.

import socket  # noqa: E402

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex


@pytest.fixture(autouse=True)
def no_network(request):
    """A socket is an error unless the test is marked ``network``."""
    if request.node.get_closest_marker("network"):
        yield
        return

    def refuse(self, address, *args, **kwargs):
        raise AssertionError(
            f"this test opened a network connection to {address}. Feeds are "
            f"stubbed in tests -- mark it @pytest.mark.network if the request "
            f"is the point of it."
        )

    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    try:
        yield
    finally:
        socket.socket.connect = _connect
        socket.socket.connect_ex = _connect_ex
