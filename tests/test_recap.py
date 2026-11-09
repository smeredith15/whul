"""The weekly recap: what it says, when it goes, and what it sends."""

from datetime import date, timedelta

import pytest

from whul import recap, simulate
from whul.store import open_store

END = date(2026, 10, 31)


@pytest.fixture(scope="module")
def store():
    store = open_store(":memory:")
    simulate.generate(store, seed=2026, end=END, verbose=False)
    return store


@pytest.fixture(scope="module")
def week(store):
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
    # And those categories name each asset's league, whatever the week held.
    assert {"Tennis", "Motorsports", "Club Soccer Top 3",
            "Club Soccer Other"} <= recap.MIXED_CATEGORIES
    assert "NFL" not in recap.MIXED_CATEGORIES


def test_a_mixed_category_names_its_leagues_even_when_one_league_filled_it(week):
    from dataclasses import replace

    one = recap.AssetWeek("a", "Michael Olise", "Bundesliga", week.managers[0],
                          6.7, 40.0, True, "Club Soccer Other")
    page = recap.render(replace(week, by_category={"Club Soccer Other": [one]},
                                top_assets=[]))
    assert "<div class='meta'>Bundesliga</div>" in page
    # A one-league category does not repeat its own name under every asset.
    nfl = replace(one, name="Brock Bowers", league="NFL", category="NFL")
    single = recap.render(replace(week, by_category={"NFL": [nfl]}, top_assets=[]))
    assert "<div class='meta'>NFL</div>" not in single


def test_the_page_carries_every_section(week):
    page = recap.render(week, "https://example.test/whul/")
    for heading in ("This week", "so far", "Season", "Top assets of the week",
                    "Best in each league"):
        assert heading in page
    assert "https://example.test/whul/" in page
    assert week.headline().endswith(".")


def _run(run_id, stamp, built=True):
    return {"id": run_id, "created_at": stamp, "built": built}


def test_only_the_first_tuesday_publish_after_six_eastern_sends():
    # Tuesday 10 November 2026: 15:00 UTC is 10:00 Eastern.
    runs = [_run(1, "2026-11-10T15:00:00Z"), _run(2, "2026-11-10T19:00:00Z")]
    assert recap.first_of_the_week(runs, 1)
    assert not recap.first_of_the_week(runs, 2)

    # A run whose build failed did not write the standings, so the next one is
    # the first.
    runs = [_run(1, "2026-11-10T15:00:00Z", built=False), _run(2, "2026-11-10T19:00:00Z")]
    assert not recap.first_of_the_week(runs, 1)
    assert recap.first_of_the_week(runs, 2)

    # 05:40 UTC is 00:40 Tuesday in the East: too early for Monday night's
    # player lines, so it neither sends nor takes the later run's place.
    runs = [_run(3, "2026-11-10T05:40:00Z"), _run(4, "2026-11-10T15:00:00Z")]
    assert not recap.first_of_the_week(runs, 3)
    assert recap.first_of_the_week(runs, 4)
    # 11:00 UTC is 06:00 Eastern, the first minute that counts.
    assert recap.first_of_the_week([_run(5, "2026-11-10T11:00:00Z")], 5)

    # Monday no longer sends, and nor does Wednesday.
    assert not recap.first_of_the_week([_run(6, "2026-11-09T15:00:00Z")], 6)
    assert not recap.first_of_the_week([_run(7, "2026-11-11T15:00:00Z")], 7)


def test_a_tuesday_recap_covers_tuesday_to_monday_night(store):
    tuesday = date(2026, 10, 27)
    week = recap.build(store, simulate.SIM_SEASON, tuesday)
    assert week.as_of == tuesday
    assert week.since == tuesday - timedelta(days=7)
    assert (week.first_day.weekday(), week.last_day.weekday()) == (1, 0)


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
