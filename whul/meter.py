"""What a pull spent, split into waiting and everything else.

The run already says how long each feed took. That is enough to name the
expensive ones and not enough to fix them, because two very different costs
wear the same number. A source that spends four minutes waiting on a feed that
asks for one request every three seconds is fixed by making fewer requests or
by running it alongside something else. A source that spends four minutes
parsing a season it already had on disk is fixed by not re-deriving it. The
remedies have nothing in common, and a wall clock cannot tell them apart.

So this counts the requests and times them, in three buckets. *Waiting* is
the seconds between asking for a URL and having the answer. *Pausing* is the
politeness a source serves itself -- ESPN gets four tenths of a second after
every request, FBref three and a half -- which sits beside the request rather
than inside it and would otherwise be counted as work. What is left over --
the report calls it ``other`` -- is parsing, disk, and anything that went out
over a transport this module cannot see.

The three want different things. Waiting falls when a slow host is asked
alongside a different one; pausing falls when there are fewer requests to pay
for, or when two hosts pay in parallel; ``other`` falls only when the work
stops being done twice. Lumping the first two together, as the first cut of
this module did, made every ESPN-shaped source look like it was parsing.

**It does not see everything, and says so rather than reporting a zero.** Two
transports are counted: ``requests`` (which covers ``requests.get``, since that
opens a Session of its own) and ``urllib.request.urlopen`` (which is how pandas
reaches a CSV or a parquet over HTTP). A source that reaches the network by
some third route is invisible here and its cost lands in ``other``, where it
reads as parsing and is not. `blind` is the flag for that: a source that
finished with no requests counted did not necessarily make none.

The counting is done by wrapping those two callables for the length of a
``measure()`` block rather than by editing every fetch in ``whul.sources``.
That is the unusual choice and it is deliberate: there are a dozen ``_get``
implementations and more will be written, and an instrument that has to be
remembered at each new one is an instrument that will read low for whichever
source was added last. Silence about a cost is the failure this whole project
is arranged against; a wrapper that cannot be forgotten is worth the stranger
shape.
"""

from __future__ import annotations

import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class Spend:
    """One source's cost, as far as it can be seen."""

    #: Wall clock for the whole pull, waiting and working together.
    seconds: float = 0.0
    #: Requests that actually went out. A cache hit is not one.
    requests: int = 0
    #: Seconds inside those requests: the host's own latency.
    waiting: float = 0.0
    #: Seconds slept on purpose. A politeness pause is time the pull chose to
    #: spend and can choose to spend differently; it is not the feed being
    #: slow and it is certainly not parsing.
    pausing: float = 0.0

    def __add__(self, other: "Spend") -> "Spend":
        """Two readings of the same source, from two days of one run.

        A backfill pulls each source once a day across the range, and the
        report is about the source rather than about any one of its days.
        """
        return Spend(
            seconds=self.seconds + other.seconds,
            requests=self.requests + other.requests,
            waiting=self.waiting + other.waiting,
            pausing=self.pausing + other.pausing,
        )

    @property
    def other(self) -> float:
        """Everything that was neither waited for nor slept through: parsing,
        disk, and any transport this module does not see."""
        return max(0.0, self.seconds - self.waiting - self.pausing)

    @property
    def blind(self) -> bool:
        """Whether this reading is one the meter cannot vouch for.

        A pull that took real time and made no request it could see either
        replayed a cache -- the interesting answer -- or went out over a
        transport this module does not wrap. The two are not distinguishable
        from here, so the report marks the row rather than picking one.
        """
        return self.requests == 0 and self.seconds >= 1.0


#: The tally being filled, or None when nothing is being measured. A module
#: global rather than a context variable: the pull is single-threaded and one
#: source runs at a time, which is the whole reason the report is worth having.
_active: Spend | None = None


def record(seconds: float) -> None:
    """Add one request to whatever is being measured."""
    if _active is not None:
        _active.requests += 1
        _active.waiting += seconds


def slept(seconds: float) -> None:
    """Add a deliberate pause to whatever is being measured."""
    if _active is not None:
        _active.pausing += seconds


def _timed(call, into=None):
    """``call``, wrapped so its wall time lands in the tally."""
    into = into or record

    def wrapped(*args, **kwargs):
        began = time.monotonic()
        try:
            return call(*args, **kwargs)
        finally:
            into(time.monotonic() - began)

    wrapped.__whul_meter__ = True  # type: ignore[attr-defined]
    return wrapped


@contextmanager
def measure():
    """Count requests for the length of the block, yielding the tally.

    Nesting is not supported and not needed: one source is pulled at a time.
    An inner block would take the tally over and hand the outer one a reading
    short by whatever the inner one saw, so it is refused rather than allowed
    to under-report.
    """
    global _active
    if _active is not None:
        raise RuntimeError("a measure() block is already open")

    import requests as _requests

    spend = Spend()
    _active = spend
    session_request = _requests.sessions.Session.request
    urlopen = urllib.request.urlopen
    sleep = time.sleep
    began = time.monotonic()
    try:
        _requests.sessions.Session.request = _timed(session_request)
        urllib.request.urlopen = _timed(urlopen)
        # Every sleep inside a pull is a politeness pause or a retry backoff,
        # and both are time chosen rather than time worked. Wrapped for the
        # same reason as the other two: there is one of these in every source
        # and an instrument that has to be threaded through each would read
        # low for whichever was written last.
        time.sleep = _timed(sleep, into=slept)
        yield spend
    finally:
        _requests.sessions.Session.request = session_request
        urllib.request.urlopen = urlopen
        time.sleep = sleep
        spend.seconds = time.monotonic() - began
        _active = None
