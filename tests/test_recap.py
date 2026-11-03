"""The weekly recap: what it says, when it goes, and what it sends."""

from datetime import date, timedelta

import pytest

from whul import recap, simulate
from whul.store import open_store

END = date(2026, 10, 31)


@pytest.fixture(scope="module")
def week():
    store = open_store(":memory:")
    simulate.generate(store, seed=2026, end=END, verbose=False)
    return recap.build(store, simulate.SIM_SEASON)


def test_the_week_is_the_seven_days_the_standings_moved_over(week):
    assert week.as_of == END
    assert week.since == END - timedelta(days=7)
    assert (week.first_day, week.last_day) == (week.since, END - timedelta(days=1))


def test_the_three_tables_rank_the_same_managers(week):
    managers = {s.manager for s in week.standings}
    assert len(managers) == len(week.standings) >= 2
    for key in ("week_rank", "quarter_rank", "rank"):
        assert sorted(getattr(s, key) for s in week.standings) == list(
            range(1, len(managers) + 1))
    by_week = sorted(week.standings, key=lambda s: s.week_rank)
    assert [s.week for s in by_week] == sorted((s.week for s in by_week), reverse=True)
    leader = min(week.standings, key=lambda s: s.rank)
    assert leader.behind == 0


def test_the_assets_are_the_weeks_best_and_grouped_as_the_rosters_are(week):
    gains = [a.gained for a in week.top_assets]
    assert gains == sorted(gains, reverse=True) and len(gains) <= recap.TOP_ASSETS
    assert week.by_category
    for group, items in week.by_category.items():
        room = recap.PER_CATEGORY_WIDE.get(group, recap.PER_CATEGORY)
        assert 0 < len(items) <= room
        assert all(a.category == group and a.gained > 0 for a in items)
    # One heading for the leagues a roster category spans, never one each.
    assert not {"ATP", "WTA", "F1", "NASCAR", "Premier League"} & set(week.by_category)


def test_the_page_carries_every_section(week):
    page = recap.render(week, "https://example.test/whul/")
    for heading in ("This week", "so far", "Season", "Top assets of the week",
                    "Best in each league"):
        assert heading in page
    assert "https://example.test/whul/" in page
    assert week.headline().endswith(".")


def _run(run_id, stamp, built=True):
    return {"id": run_id, "created_at": stamp, "built": built}


def test_only_the_first_publish_of_an_eastern_monday_sends():
    # Monday 9 November 2026: 07:37 UTC is 02:37 Eastern.
    runs = [_run(1, "2026-11-09T07:37:00Z"), _run(2, "2026-11-09T15:00:00Z")]
    assert recap.first_on_monday(runs, 1)
    assert not recap.first_on_monday(runs, 2)

    # A run whose build failed did not write Monday's standings, so the next
    # one is the first.
    runs = [_run(1, "2026-11-09T07:37:00Z", built=False), _run(2, "2026-11-09T15:00:00Z")]
    assert not recap.first_on_monday(runs, 1)
    assert recap.first_on_monday(runs, 2)

    # 03:00 UTC on Monday is still Sunday evening in the East; Tuesday is not
    # a Monday.
    assert not recap.first_on_monday([_run(3, "2026-11-09T03:00:00Z")], 3)
    assert not recap.first_on_monday([_run(4, "2026-11-10T07:37:00Z")], 4)
    # Sunday's late manual run does not take Monday's place.
    runs = [_run(5, "2026-11-09T03:00:00Z"), _run(6, "2026-11-09T07:37:00Z")]
    assert recap.first_on_monday(runs, 6)


def test_the_email_carries_the_image_in_its_body_and_the_page_attached(week, monkeypatch):
    import smtplib

    sent = {}

    class Server:
        def __init__(self, host, port, context=None, timeout=None):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(smtplib, "SMTP_SSL", Server)
    to = recap.send_email(week, "<html>page</html>", b"\x89PNG fake", {
        "host": "smtp.example.test", "port": 465, "user": "me@example.test",
        "password": "app-password", "to": "a@example.test, b@example.test"})
    message = sent["message"]
    assert to == "a@example.test, b@example.test"
    assert message["Subject"].startswith(f"WHUL Week {week.week_number} recap")
    assert sent["login"] == ("me@example.test", "app-password")
    types = [part.get_content_type() for part in message.walk()]
    assert "image/png" in types and "text/html" in types
    names = [part.get_filename() for part in message.walk() if part.get_filename()]
    assert any(n.endswith("-recap.html") for n in names)
    body = next(p for p in message.walk() if p.get_content_type() == "text/html"
                and not p.get_filename())
    image = next(p for p in message.walk() if p.get_content_type() == "image/png")
    assert "cid:recap" in body.get_content() and image["Content-ID"] == "<recap>"


def test_the_push_carries_the_image_and_the_headline(week, monkeypatch):
    import requests

    called = {}

    class Reply:
        def raise_for_status(self):
            pass

    def put(url, data=None, params=None, timeout=None):
        called.update(url=url, data=data, params=params)
        return Reply()

    monkeypatch.setattr(requests, "put", put)
    recap.send_push(week, b"\x89PNG fake", "secret-topic", site_url="https://example.test/")
    assert called["url"] == "https://ntfy.sh/secret-topic"
    assert called["data"] == b"\x89PNG fake"
    assert called["params"]["message"] == week.headline()
    assert called["params"]["click"] == "https://example.test/"
