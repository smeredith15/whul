"""What a pull spent, and what the meter will not pretend to know.

A wall clock says a feed is slow. It does not say whether the fix is to ask
for less or to stop re-deriving what is already on disk, and those are the two
things the pull is made of. These are about the split -- and about the meter
saying so when it cannot see.
"""

import time
import urllib.request

import pytest
import requests

from whul import meter


def test_a_request_is_counted_and_timed():
    """The seconds inside a request are waiting; the rest is not."""
    with meter.measure() as spend:
        meter.record(0.25)
        meter.record(0.75)

    assert spend.requests == 2
    assert spend.waiting == pytest.approx(1.0)
    assert spend.seconds >= 0.0


def test_a_politeness_pause_is_not_work():
    """ESPN gets four tenths of a second after every request and FBref three
    and a half. That sleep sits beside the request rather than inside it, and
    counting it as work -- which the first cut of this module did -- made
    every source that pays it look like it was parsing."""
    with meter.measure() as spend:
        meter.record(0.2)
        time.sleep(0.05)
    spend.seconds = 1.0

    assert spend.waiting == pytest.approx(0.2)
    assert spend.pausing == pytest.approx(0.05, abs=0.03)
    assert spend.other == pytest.approx(0.75, abs=0.03)


def test_sleep_is_put_back_when_the_block_ends():
    """It replaces a standard callable for the length of a pull. Leaving it
    wrapped would have every later sleep in the process reporting into a tally
    nobody reads."""
    before = time.sleep
    with meter.measure():
        assert time.sleep is not before
    assert time.sleep is before


def test_the_three_buckets_account_for_the_whole_pull():
    """Total is what a person sees. If the columns beside it do not add back
    up to it, the table is telling three separate stories."""
    with meter.measure() as spend:
        meter.record(1.0)
        time.sleep(0.02)
    spend.seconds = 4.0

    assert (spend.waiting + spend.pausing + spend.other) == pytest.approx(4.0)


def test_what_was_not_waiting_is_everything_else():
    with meter.measure() as spend:
        meter.record(0.1)
    spend.seconds = 5.0

    assert spend.other == pytest.approx(4.9)


def test_other_never_goes_negative():
    """A politeness pause served inside a fetch is counted as waiting, and a
    clock that disagrees with itself by a millisecond must not turn that into
    a negative share of a pull."""
    with meter.measure() as spend:
        meter.record(2.0)
    spend.seconds = 1.999

    assert spend.other == 0.0


def test_a_pull_that_cost_time_and_showed_no_request_is_flagged():
    """Either it replayed a cache -- the answer worth having -- or it went out
    over a transport the meter does not wrap. The two are not distinguishable
    from here, so the reading is marked rather than believed."""
    with meter.measure() as spend:
        pass
    spend.seconds = 30.0

    assert spend.blind, "a silent half-minute is not a free half-minute"

    with meter.measure() as busy:
        meter.record(1.0)
    busy.seconds = 30.0
    assert not busy.blind


def test_a_pull_that_cost_nothing_is_not_flagged():
    """An out-of-season league returns without asking anybody anything, and
    marking every one of those would bury the rows that matter."""
    with meter.measure() as spend:
        pass
    spend.seconds = 0.01

    assert not spend.blind


def test_the_meter_counts_pandas_http_as_well_as_requests():
    """`intl-soccer` is the second most expensive source in the pull and it
    reaches its ledgers through `pd.read_csv(url)`, which never touches
    `requests`. A meter that watched only one transport would report the
    other's cost as parsing."""
    seen = []
    with meter.measure() as spend:
        # Both wrapped callables, exercised without going near a network:
        # what is under test is that the wrapper is installed on each.
        assert getattr(requests.sessions.Session.request, "__whul_meter__", False)
        assert getattr(urllib.request.urlopen, "__whul_meter__", False)
        seen.append(True)

    assert seen == [True]
    assert spend.requests == 0


def test_the_wrapping_is_undone_however_the_block_ends():
    """It patches two standard callables for the length of a pull. Leaving
    either one wrapped would make every later request in the process report
    into a tally nobody is reading."""
    before = (requests.sessions.Session.request, urllib.request.urlopen)

    with pytest.raises(ValueError):
        with meter.measure():
            raise ValueError("the pull fell over")

    assert (requests.sessions.Session.request, urllib.request.urlopen) == before
    assert meter._active is None


def test_a_second_block_inside_the_first_is_refused():
    """Nesting would hand the outer tally a reading short by whatever the
    inner one took, which is the understatement this module exists to stop."""
    with meter.measure():
        with pytest.raises(RuntimeError):
            with meter.measure():
                pass


def test_recording_outside_a_block_is_harmless():
    """A source fetching outside a measured pull -- a probe, a benchmark
    rebuild -- must not raise for want of somewhere to put the number."""
    assert meter._active is None
    meter.record(1.0)  # no exception


def test_two_days_of_one_source_add_up():
    """A backfill pulls each source once a day, and the report is about the
    source rather than about any one of its days."""
    one, two = meter.Spend(seconds=10.0, requests=3, waiting=4.0), \
        meter.Spend(seconds=6.0, requests=2, waiting=1.0)

    total = one + two
    assert (total.seconds, total.requests, total.waiting) == (16.0, 5, 5.0)
    assert total.other == pytest.approx(11.0)


# --- against a real socket ---------------------------------------------------

def test_the_meter_actually_counts_both_transports(tmp_path):
    """The wrapper being installed is not proof that it counts. This serves two
    files over a real socket and fetches one with `requests` and one with
    pandas, which is exactly the pair the pull uses."""
    import functools
    import http.server
    import threading

    import pandas as pd

    (tmp_path / "rows.csv").write_text("a,b\n1,2\n")
    (tmp_path / "hi.txt").write_text("hello")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(tmp_path))
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with meter.measure() as spend:
            assert requests.get(f"{base}/hi.txt", timeout=10).text == "hello"
            assert len(pd.read_csv(f"{base}/rows.csv")) == 1
    finally:
        server.shutdown()
        server.server_close()

    assert spend.requests == 2, "one transport went uncounted"
    assert spend.waiting > 0.0
    assert not spend.blind


# --- the reading that outlives the log ---------------------------------------

def test_a_pull_writes_down_what_it_cost(tmp_path):
    """The log ages out. The question worth asking is not what last night cost
    but whether it costs more than November did, and only a stored reading can
    answer that."""
    from datetime import date

    from whul.cli import _ingest_one_day
    from whul.store import open_store

    class FakeSource:
        key = "epl"
        league = "Premier League"
        asset_type = "Team"

    class FakeIngest:
        @staticmethod
        def ingest(store, source, season, as_of, hold=True, today=None):
            # Long enough that the stored wall time is not a rounding artefact.
            # The recorded waiting is declared rather than spent, so it is
            # larger than the pull it sits inside -- which cannot happen for
            # real, where waiting is measured within the same block.
            time.sleep(0.01)
            meter.record(0.5)
            meter.record(0.25)
            time.sleep(0.02)
            from whul.ingest import IngestReport
            return IngestReport(league=source.league, asset_type="Team", pulled=3)

    store = open_store(str(tmp_path / "cost.sqlite3"))
    spent: dict = {}
    _ingest_one_day(FakeIngest, store, [FakeSource()], "2026-27",
                    date(2026, 11, 3), spent)

    row = store.query("SELECT * FROM ingest_timings").iloc[0]
    assert row["source"] == "epl"
    assert row["as_of"] == "2026-11-03"
    assert row["requests"] == 2
    assert row["waiting"] == pytest.approx(0.75, abs=0.01)
    assert row["pausing"] >= 0.02, "the pause was not written down"
    assert row["seconds"] >= 0.03, "the wall clock was not written down"
    assert spent["epl"].requests == 2


def test_a_second_pull_on_one_day_revises_it(tmp_path):
    """The day key matches every other table, so the stored figure is the last
    pull of that day -- which is also the run that produced the standings."""
    from datetime import date

    from whul.cli import _ingest_one_day
    from whul.store import open_store

    class FakeSource:
        key = "nhl"
        league = "NHL"
        asset_type = "Team"

    def fake(pause):
        class FakeIngest:
            @staticmethod
            def ingest(store, source, season, as_of, hold=True, today=None):
                meter.record(pause)
                from whul.ingest import IngestReport
                return IngestReport(league=source.league, asset_type="Team")
        return FakeIngest

    store = open_store(str(tmp_path / "twice.sqlite3"))
    day = date(2026, 11, 3)
    _ingest_one_day(fake(4.0), store, [FakeSource()], "2026-27", day, {})
    _ingest_one_day(fake(1.0), store, [FakeSource()], "2026-27", day, {})

    rows = store.query("SELECT * FROM ingest_timings")
    assert len(rows) == 1, "a re-run added a row rather than revising the day"
    assert rows.iloc[0]["waiting"] == pytest.approx(1.0, abs=0.01)


def test_a_run_survives_a_reading_it_cannot_store(tmp_path):
    """An instrument that can take the nightly publish down costs more than it
    measures."""
    from datetime import date

    from whul.cli import _ingest_one_day
    from whul.store import open_store

    class FakeSource:
        key = "mlb"
        league = "MLB"
        asset_type = "Player"

    class FakeIngest:
        @staticmethod
        def ingest(store, source, season, as_of, hold=True, today=None):
            from whul.ingest import IngestReport
            return IngestReport(league=source.league, pulled=1)

    store = open_store(str(tmp_path / "broken.sqlite3"))
    store.conn.execute("DROP TABLE ingest_timings")
    store.conn.commit()

    reports = _ingest_one_day(FakeIngest, store, [FakeSource()], "2026-27",
                              date(2026, 11, 3), {})
    assert reports[0].pulled == 1, "the pull was lost to a bookkeeping failure"


def test_a_day_recorded_before_pauses_were_measured_is_called_out():
    """The pausing column arrived after the first readings did, and those days
    put their pauses in `work`. Read straight, the first comparison across the
    changeover shows work collapsing and pausing appearing, having done
    neither."""
    import pandas as pd

    from whul.cli import _unmeasured_pauses

    old = pd.DataFrame([{"requests": 48, "pausing": 0.0}])
    assert _unmeasured_pauses(old)

    measured = pd.DataFrame([{"requests": 48, "pausing": 19.2}])
    assert not _unmeasured_pauses(measured)

    # A day where nothing was in season paused for nothing and is not a gap in
    # the instrument, so it must not carry the warning.
    quiet = pd.DataFrame([{"requests": 0, "pausing": 0.0}])
    assert not _unmeasured_pauses(quiet)
