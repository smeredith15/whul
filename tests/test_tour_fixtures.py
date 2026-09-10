"""What a golfer and a driver play next.

The two categories this column could never speak for: their next start is an
event with a field rather than a game against somebody, so `by_asset` matched
them to nothing and every cell was blank. The answer it can give is the tour's
own -- the next tournament or race on the calendar, which is the same for
everyone in the series.

Written against the payload shapes rather than the feeds: ESPN's racing and
golf scoreboards and the Ergast mirror are all unreachable from where this was
written, so these pin the reading and the probes pin the feeds.
"""

from datetime import date
from types import SimpleNamespace

import pandas as pd

from whul import fixtures, ingest
from whul.sources import espn_individual, jolpica
from whul.store import open_store


def rostered(store, asset_id, name, league, index):
    store.upsert("managers", [{"manager_id": "SS", "display_name": "Scott"}],
                 ["manager_id"])
    store.upsert("assets", [{
        "asset_id": asset_id, "asset_type": "Player", "league": league,
        "display_name": name, "affiliation": "", "created_at": "2026-08-21",
    }], ["asset_id"])
    slot = f"slot-{asset_id}"
    store.upsert("roster_slots", [{
        "slot_id": slot, "season": "2026-27", "manager_id": "SS",
        "category": "Test", "asset_type": "Player", "slot_index": index,
    }], ["slot_id"])
    store.upsert("slot_occupancy", [{
        "slot_id": slot, "asset_id": asset_id,
        "start_date": "2026-08-21", "end_date": None,
    }], ["slot_id", "start_date"])


# --- reading a calendar -----------------------------------------------------

GOLF = [
    {"id": "1", "name": "Wyndham Championship",
     "date": "2026-08-06T00:00Z", "endDate": "2026-08-09T00:00Z"},
    {"id": "2", "name": "Procore Championship",
     "date": "2026-09-10T00:00Z", "endDate": "2026-09-13T00:00Z"},
    {"id": "3", "name": "Sanderson Farms", "date": "2026-09-24T00:00Z"},
]


def test_the_next_event_is_the_next_one_on_the_calendar(monkeypatch):
    monkeypatch.setattr(espn_individual, "season_events", lambda l, s: GOLF)
    got = espn_individual.events_ahead("pga", [2026], date(2026, 9, 8))
    assert [e["name"] for e in got] == ["Procore Championship", "Sanderson Farms"]


def test_a_tournament_being_played_is_still_the_next_one(monkeypatch):
    """A four-day event read on the Friday. Judged on the day it ends, because
    dropping it mid-round would say a player's next start is a week away while
    he is on the course."""
    monkeypatch.setattr(espn_individual, "season_events", lambda l, s: GOLF)
    got = espn_individual.events_ahead("pga", [2026], date(2026, 9, 12))
    assert got[0]["name"] == "Procore Championship"


def test_a_finished_event_is_never_next(monkeypatch):
    monkeypatch.setattr(espn_individual, "season_events", lambda l, s: GOLF)
    got = espn_individual.events_ahead("pga", [2026], date(2026, 9, 14))
    assert [e["name"] for e in got] == ["Sanderson Farms"]


def test_the_calendar_is_read_by_date_and_not_by_status(monkeypatch):
    """The season list is cached to disk on first fetch, so its statuses are
    frozen at whatever they said the first time the year was asked for. An
    event marked complete in a stale copy is still next if its date says so."""
    stale = [{"id": "9", "name": "Tour Championship", "date": "2026-09-24T00:00Z",
              "status": {"type": {"completed": True, "state": "post"}}}]
    monkeypatch.setattr(espn_individual, "season_events", lambda l, s: stale)
    got = espn_individual.events_ahead("pga", [2026], date(2026, 9, 8))
    assert [e["name"] for e in got] == ["Tour Championship"]


def test_a_calendar_that_cannot_be_read_loses_no_pull(monkeypatch):
    def boom(league, season):
        raise RuntimeError("403")

    monkeypatch.setattr(espn_individual, "season_events", boom)
    assert espn_individual.events_ahead("pga", [2026], date(2026, 9, 8)) == []


def test_the_f1_calendar_is_the_races_not_the_results(monkeypatch):
    payload = {"MRData": {"RaceTable": {"Races": [
        {"round": "15", "raceName": "Dutch Grand Prix", "date": "2026-08-23"},
        {"round": "16", "raceName": "Italian Grand Prix", "date": "2026-09-13"},
    ]}}}
    asked = {}

    def fake(path, params, cache_key=None):
        asked["path"], asked["cache_key"] = path, cache_key
        return payload

    monkeypatch.setattr(jolpica, "_get", fake)
    got = jolpica.races_ahead([2026], date(2026, 9, 8))
    assert [r["name"] for r in got] == ["Italian Grand Prix"]
    assert asked["path"] == "2026/races/"
    # Uncached: a calendar is the one thing here that can change after it is
    # first read, and a frozen copy would go on naming a date that is gone.
    assert asked["cache_key"] is None


# --- the rows it becomes ----------------------------------------------------

def test_a_tour_row_names_the_event_and_no_opponent():
    rows = fixtures.tour_rows(
        "PGA", "2026-27", {"Rory McIlroy": "PGA"},
        {"PGA": [{"name": "Procore Championship", "date": "2026-09-10"}]}, "now")
    assert len(rows) == 1
    got = rows.to_dict("records")[0]
    assert got["team_key"] == "rory mcilroy"
    assert got["competition"] == "Procore Championship"
    assert got["opponent"] == ""


def test_an_athlete_whose_series_has_nothing_left_gets_no_row():
    rows = fixtures.tour_rows(
        "Motorsports", "2026-27", {"Kyle Larson": "NASCAR"},
        {"NASCAR": []}, "now")
    assert rows.empty


def test_a_tour_event_reaches_the_athletes_card():
    store = open_store(":memory:")
    rostered(store, "player-rory", "Rory McIlroy", "PGA", 1)
    fixtures.replace(store, "2026-27", "PGA", fixtures.tour_rows(
        "PGA", "2026-27", {"Rory McIlroy": "PGA"},
        {"PGA": [{"name": "Procore Championship", "date": "2026-09-10"}]}, "now"))
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert got["player-rory"]["competition"] == "Procore Championship"
    assert got["player-rory"]["opponent"] == ""


def test_a_tour_event_from_the_wrong_feed_is_never_shown():
    """The same guard every other league has. A golfer's cell may only be
    filled by the source that pulls golf."""
    store = open_store(":memory:")
    rostered(store, "player-rory", "Rory McIlroy", "PGA", 1)
    fixtures.replace(store, "2026-27", "Flashscore/1", pd.DataFrame([{
        "season": "2026-27", "league": "Flashscore/1", "team_key": "rory mcilroy",
        "fixture_date": "2026-09-10", "opponent": "", "home": 1,
        "competition": "Procore Championship", "fetched_at": "now",
    }]))
    assert fixtures.by_asset(store, "2026-27", date(2026, 9, 8)) == {}


def test_the_cell_reads_as_an_event_rather_than_a_game():
    from whul.site.build import _fixture_cell

    cell = _fixture_cell({"date": "2026-09-13", "opponent": "", "home": True,
                          "competition": "Italian Grand Prix", "badge": ""})
    assert "Italian Grand Prix" in cell
    # "vs" would be a sentence about nobody.
    assert ">vs<" not in cell and ">at<" not in cell


# --- which series an athlete is actually in ---------------------------------

def test_the_series_comes_from_the_results_not_the_league_label(monkeypatch):
    """The spreadsheet files the same F1 driver under "F1" and under
    "Motorsports", so the roster's label cannot say which car anyone is in.
    The scored events can, and do."""
    scored = pd.DataFrame([
        {"player": "Lando Norris", "league": "F1"},
        {"player": "Kyle Larson", "league": "NASCAR"},
    ])
    monkeypatch.setattr(
        ingest, "_tour_calendar",
        lambda series, seasons, as_of: [
            {"name": f"next {series}", "date": "2026-09-13"}],
    )
    upcoming: list = []
    ingest._harvest_tour(
        SimpleNamespace(league="Motorsports"), scored, date(2026, 9, 8),
        [2026], ["Lando Norris", "Kyle Larson"], upcoming, verbose=False)
    got = pd.concat(upcoming).set_index("team_key")["competition"].to_dict()
    assert got == {"lando norris": "next F1", "kyle larson": "next NASCAR"}


def test_an_athlete_nobody_rosters_gets_no_row(monkeypatch):
    scored = pd.DataFrame([{"player": "Somebody Else", "league": "F1"}])
    monkeypatch.setattr(
        ingest, "_tour_calendar",
        lambda series, seasons, as_of: [{"name": "next", "date": "2026-09-13"}])
    upcoming: list = []
    ingest._harvest_tour(
        SimpleNamespace(league="Motorsports"), scored, date(2026, 9, 8),
        [2026], ["Lando Norris"], upcoming, verbose=False)
    assert upcoming == []


def test_a_league_that_is_not_a_tour_is_left_alone(monkeypatch):
    scored = pd.DataFrame([{"player": "Somebody", "league": "NFL"}])
    upcoming: list = []
    ingest._harvest_tour(
        SimpleNamespace(league="NFL"), scored, date(2026, 9, 8),
        [2026], ["Somebody"], upcoming, verbose=False)
    assert upcoming == []


def test_every_tour_league_can_name_the_feed_that_serves_it():
    """A rostered league whose fixtures are recorded under another source's
    name reads nothing at all unless `FEEDS` says so -- and the roster files
    F1 drivers under two labels."""
    for league in fixtures.TOUR:
        assert fixtures.feeds_for(league), league
    assert fixtures.feeds_for("F1") == {"Motorsports"}
    assert fixtures.feeds_for("NASCAR") == {"Motorsports"}
