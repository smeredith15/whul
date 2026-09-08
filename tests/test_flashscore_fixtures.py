"""Upcoming team-sport matches from the Flashscore feed.

Written against the wire format the tennis reader in ``whul.sources.flashscore``
already parses in production, because the feed itself is unreachable from where
this was written. These tests pin the parse; the probe pins the feed.
"""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from whul import fixtures
from whul.resolve import normalize_team
from whul.sources import flashscore_fixtures as feed
from whul.store import open_store


def stamp(when: date) -> int:
    return int(datetime(when.year, when.month, when.day, 18,
                        tzinfo=timezone.utc).timestamp())


def header(name: str) -> str:
    return f"ZA÷{name}¬ZEE÷x¬"


def match(uid, home, away, when, status="1") -> str:
    return (f"AA÷{uid}¬AD÷{stamp(when)}¬AC÷{status}¬"
            f"AE÷{home}¬AF÷{away}¬")


def payload(*records: str) -> str:
    return "~".join(records)


SOON = date(2026, 9, 20)


# --- the parse --------------------------------------------------------------

def test_an_upcoming_match_is_read_with_its_teams_and_date():
    raw = payload(header("ENGLAND: Premier League"),
                  match("a1", "Arsenal", "Chelsea", SOON))
    got = list(feed.iter_fixtures(raw))
    assert len(got) == 1
    assert got[0]["home_team"] == "Arsenal"
    assert got[0]["away_team"] == "Chelsea"
    assert got[0]["game_date"] == "2026-09-20"
    assert got[0]["competition"] == "Premier League"


def test_a_finished_match_is_not_a_fixture():
    """The exact inverse of the tennis parser, which keeps these and drops
    the rest."""
    raw = payload(header("ENGLAND: Premier League"),
                  match("a1", "Arsenal", "Chelsea", SOON, status="3"))
    assert list(feed.iter_fixtures(raw)) == []


def test_the_competition_comes_from_the_header_above_the_match():
    """Position is what assigns a match to a competition; a parser that
    ignored order would file every game under whichever header it saw last."""
    raw = payload(
        header("ENGLAND: Premier League"),
        match("a1", "Arsenal", "Chelsea", SOON),
        header("EUROPE: Champions League - League phase"),
        match("a2", "Barcelona", "Inter", SOON),
    )
    got = {m["home_team"]: m["competition"] for m in feed.iter_fixtures(raw)}
    assert got == {"Arsenal": "Premier League", "Barcelona": "Champions League"}


def test_a_match_before_any_header_still_reads():
    """A payload that opens mid-list loses its competition, not its fixture."""
    raw = payload(match("a1", "Arsenal", "Chelsea", SOON))
    got = list(feed.iter_fixtures(raw))
    assert len(got) == 1 and got[0]["competition"] == ""


def test_a_match_with_no_kickoff_is_dropped():
    raw = payload(header("ENGLAND: Premier League"),
                  "AA÷a1¬AC÷1¬AE÷Arsenal¬AF÷Chelsea¬")
    assert list(feed.iter_fixtures(raw)) == []


def test_a_match_missing_a_side_is_dropped():
    raw = payload(header("ENGLAND: Premier League"),
                  f"AA÷a1¬AD÷{stamp(SOON)}¬AC÷1¬AE÷Arsenal¬")
    assert list(feed.iter_fixtures(raw)) == []


def test_nothing_at_all_is_not_an_error():
    assert list(feed.iter_fixtures("")) == []


@pytest.mark.parametrize("raw,expected", [
    ("ENGLAND: Premier League - Round 5", "Premier League"),
    ("USA: NBA", "NBA"),
    ("EUROPE: Champions League - League phase", "Champions League"),
    ("SPAIN: LaLiga", "LaLiga"),
    ("no colon here", "no colon here"),
])
def test_the_country_and_stage_are_stripped_from_a_header(raw, expected):
    assert feed.competition_of(raw) == expected


def test_the_rows_are_shaped_like_a_schedule_so_harvest_reads_them():
    """The point of the shape: one harvester serves nflverse and this alike."""
    raw = payload(header("ENGLAND: Premier League"),
                  match("a1", "Arsenal", "Chelsea", SOON))
    frame = pd.DataFrame(list(feed.iter_fixtures(raw)))
    got = fixtures.harvest("Test", "2026-27", frame, date(2026, 9, 8), "now")
    assert set(got["team_key"]) == {"arsenal", "chelsea"}
    assert set(got["opponent"]) == {"Arsenal", "Chelsea"}


# --- reading the feed's spelling -------------------------------------------

#: ``{normalized: (roster spelling, the league it plays in)}``.
ROSTER = {
    normalize_team(name): (name, league) for name, league in (
        ("Manchester City", "Premier League"),
        ("Manchester United", "Premier League"),
        ("Arsenal", "Premier League"),
        ("Tottenham Hotspur", "Premier League"),
        ("Wolverhampton Wanderers", "Premier League"),
        ("Internazionale", "Serie A"),
        ("Bayern Munich", "Bundesliga"),
        ("Borussia Dortmund", "Bundesliga"),
        ("Paris Saint-Germain", "Ligue 1"),
        ("Real Madrid", "La Liga"),
        ("Real Betis", "La Liga"),
        ("Athletic Club", "La Liga"),
    )
}


@pytest.mark.parametrize("feed_name,expected", [
    ("Arsenal", "Arsenal"),                       # exact
    ("Man City", "Manchester City"),              # word abbreviation
    ("Man Utd", "Manchester United"),             # contraction
    ("Inter", "Internazionale"),                  # prefix
    ("Bayern", "Bayern Munich"),                  # leading word
    ("Dortmund", "Borussia Dortmund"),            # trailing word
    ("PSG", "Paris Saint-Germain"),               # initialism
    ("Spurs", "Tottenham Hotspur"),               # nickname
    ("Wolves", "Wolverhampton Wanderers"),
])
def test_the_feeds_spelling_reaches_the_rosters(feed_name, expected):
    assert fixtures.match_team(feed_name, ROSTER) == expected


@pytest.mark.parametrize("ambiguous", ["Man", "Manchester", "Real", "City Man"])
def test_a_name_that_could_mean_two_clubs_matches_neither(ambiguous):
    """A wrong fixture beside the right badge is worse than a blank cell.

    "City Man" is here for word order: without it, a rule loose enough to
    read "Man City" would read any two of a club's words in any arrangement.
    """
    assert fixtures.match_team(ambiguous, ROSTER) is None


def test_a_club_nobody_rosters_matches_nothing():
    assert fixtures.match_team("Liverpool", ROSTER) is None


def test_a_two_letter_fragment_is_too_short_to_stand_for_a_club():
    assert fixtures.match_team("Ma City", ROSTER) is None


# --- the roster side --------------------------------------------------------

def rostered(store, asset_id, asset_type, name, affiliation="", index=1,
             league="Premier League"):
    store.upsert("managers", [{"manager_id": "SS", "display_name": "Scott"}],
                 ["manager_id"])
    store.upsert("assets", [{
        "asset_id": asset_id, "asset_type": asset_type, "league": league,
        "display_name": name, "affiliation": affiliation,
        "created_at": "2026-08-21",
    }], ["asset_id"])
    slot = f"slot-{asset_id}"
    store.upsert("roster_slots", [{
        "slot_id": slot, "season": "2026-27", "manager_id": "SS",
        "category": "Test", "asset_type": asset_type, "slot_index": index,
    }], ["slot_id"])
    store.upsert("slot_occupancy", [{
        "slot_id": slot, "asset_id": asset_id,
        "start_date": "2026-08-21", "end_date": None,
    }], ["slot_id", "start_date"])


def test_a_players_club_is_looked_for_even_when_nobody_rosters_it():
    """The ordinary case: four Bayern players and no Bayern. Matching only
    team assets would leave all four blank while looking like it worked."""
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", index=1)
    rostered(store, "player-kane", "Player", "Harry Kane",
             affiliation="Bayern Munich", index=2, league="Bundesliga")
    wanted = fixtures.wanted_teams(store, "2026-27")
    assert {name for name, _ in wanted.values()} == {"Arsenal", "Bayern Munich"}


# --- the world in one payload ----------------------------------------------

def test_a_club_name_shared_across_countries_goes_to_the_right_one():
    """Found by the probe on a real payload: Brazil's Serie B has an Athletic
    Club and so does Bilbao. Without the country one gets the other's
    fixtures, and the page looks entirely right while being wrong."""
    assert fixtures.match_team("Athletic Club", ROSTER, "SPAIN") == "Athletic Club"
    assert fixtures.match_team("Athletic Club", ROSTER, "BRAZIL") is None


def test_a_club_is_found_in_its_own_country_and_in_europe():
    """A club's next game is often a European night, not a league match."""
    assert fixtures.match_team("Arsenal", ROSTER, "ENGLAND") == "Arsenal"
    assert fixtures.match_team("Arsenal", ROSTER, "EUROPE") == "Arsenal"
    assert fixtures.match_team("Arsenal", ROSTER, "ARGENTINA") is None


def test_no_country_given_searches_everywhere():
    """Right for a feed that is not global; the caller passes one when it is."""
    assert fixtures.match_team("Arsenal", ROSTER) == "Arsenal"


def test_a_reserve_side_is_not_its_first_team():
    raw = payload(header("ARGENTINA: Reserve League - Clausura"),
                  match("a1", "River Plate 2", "Aldosivi 2", SOON))
    assert list(feed.iter_fixtures(raw)) == []


@pytest.mark.parametrize("name", [
    "Schalke 04", "Hannover 96", "Mainz 05", "Bologna 1909", "Arsenal",
])
def test_a_club_named_after_its_founding_year_is_not_a_reserve_side(name):
    """A bare trailing-number rule would drop three Bundesliga clubs."""
    assert not feed.RESERVE_PATTERN.search(name)


@pytest.mark.parametrize("name", ["River Plate 2", "Barcelona B", "Bayern II",
                                  "Chelsea U21", "Ajax Youth"])
def test_a_second_string_is_recognised(name):
    assert feed.RESERVE_PATTERN.search(name)


def test_the_country_is_carried_on_every_parsed_row():
    raw = payload(header("ENGLAND: Premier League"),
                  match("a1", "Arsenal", "Chelsea", SOON))
    assert list(feed.iter_fixtures(raw))[0]["country"] == "ENGLAND"


def test_an_athlete_with_no_club_adds_nothing_to_look_for():
    store = open_store(":memory:")
    rostered(store, "player-golfer", "Player", "A Golfer", index=1)
    assert fixtures.wanted_teams(store, "2026-27") == {}


def test_only_the_rostered_side_of_a_tie_becomes_a_row(monkeypatch):
    """Both sides are translated so the opponent reads properly, but a club
    nobody holds must not acquire a fixture row of its own."""
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal")

    raw = payload(header("ENGLAND: Premier League"),
                  match("a1", "Arsenal", "Chelsea", SOON))
    monkeypatch.setattr(
        feed, "load_upcoming",
        lambda sport, days=None, verbose=True: pd.DataFrame(list(feed.iter_fixtures(raw))),
    )
    fixtures.from_flashscore(store, "2026-27", date(2026, 9, 8),
                            leagues=["Premier League"], verbose=False)
    rows = store.query("SELECT team_key, opponent FROM fixtures")
    assert list(rows["team_key"]) == ["arsenal"]
    assert list(rows["opponent"]) == ["Chelsea"]


def test_a_sport_that_fails_does_not_lose_the_others(monkeypatch):
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal")

    def explode(sport, days=None, verbose=True):
        raise RuntimeError("feed down")

    monkeypatch.setattr(feed, "load_upcoming", explode)
    # Must return rather than raise: a fixture is a convenience on a page.
    assert fixtures.from_flashscore(
        store, "2026-27", date(2026, 9, 8), verbose=False) == {}


# --- the leagues this feed must not reach ----------------------------------

def test_a_league_the_feed_does_not_serve_is_not_even_searched_for():
    """The bug this fixes, at its root.

    Unscoped, the matcher was offered all 176 clubs on the roster -- the NFL,
    the NHL, college football, golfers -- and Flashscore's bare city names
    ("Buffalo", "New England", "Denver") then reached NFL clubs through the
    abbreviation rule. The country guard did not stop it: those leagues have
    no country list, and an empty list was read as "anywhere".
    """
    store = open_store(":memory:")
    rostered(store, "team-bills", "Team", "Buffalo Bills", index=1, league="NFL")
    rostered(store, "team-arsenal", "Team", "Arsenal", index=2,
             league="Premier League")

    everything = fixtures.wanted_teams(store, "2026-27")
    assert {n for n, _ in everything.values()} == {"Buffalo Bills", "Arsenal"}

    soccer_only = fixtures.wanted_teams(
        store, "2026-27", leagues={"Premier League"})
    assert {n for n, _ in soccer_only.values()} == {"Arsenal"}


def test_a_bare_city_name_no_longer_reaches_an_nfl_club():
    store = open_store(":memory:")
    rostered(store, "team-bills", "Team", "Buffalo Bills", index=1, league="NFL")
    rostered(store, "team-arsenal", "Team", "Arsenal", index=2,
             league="Premier League")
    wanted = fixtures.wanted_teams(store, "2026-27",
                                   leagues=set(feed.SPORTS))
    assert fixtures.match_team("Buffalo", wanted, "USA") is None


def test_a_fixture_from_the_wrong_feed_is_never_shown():
    """The second guard, and the one that matters most.

    Both sides of a Flashscore tie are translated into the roster's spelling,
    so a soccer match that reached an NFL club read as a plausible NFL game --
    "New England Patriots vs Buffalo Bills", on a Wednesday in September.
    Nothing about the row looked wrong, which is why matching alone is not
    trusted: the fixture has to have come from a feed that covers the league.
    """
    store = open_store(":memory:")
    rostered(store, "team-bills", "Team", "Buffalo Bills", index=1, league="NFL")
    fixtures.replace(store, "2026-27", "Flashscore/1", pd.DataFrame([{
        "season": "2026-27", "league": "Flashscore/1", "team_key": "buffalo bills",
        "fixture_date": "2026-09-16", "opponent": "New England Patriots",
        "home": 0, "competition": "MLS", "fetched_at": "now",
    }]))
    assert fixtures.by_asset(store, "2026-27", date(2026, 9, 8)) == {}


def test_the_leagues_own_feed_is_shown_even_when_a_stray_row_is_sooner():
    """A wrong row must not merely lose the tie-break -- it must not count."""
    store = open_store(":memory:")
    rostered(store, "team-bills", "Team", "Buffalo Bills", index=1, league="NFL")
    fixtures.replace(store, "2026-27", "Flashscore/1", pd.DataFrame([{
        "season": "2026-27", "league": "Flashscore/1", "team_key": "buffalo bills",
        "fixture_date": "2026-09-16", "opponent": "Somebody", "home": 0,
        "competition": "MLS", "fetched_at": "now",
    }]))
    fixtures.replace(store, "2026-27", "NFL", pd.DataFrame([{
        "season": "2026-27", "league": "NFL", "team_key": "buffalo bills",
        "fixture_date": "2026-09-17", "opponent": "Detroit Lions", "home": 1,
        "competition": "REG", "fetched_at": "now",
    }]))
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert got["team-bills"]["opponent"] == "Detroit Lions"
    assert got["team-bills"]["league"] == "NFL"


def test_which_feeds_may_speak_for_which_league():
    assert fixtures.feeds_for("MLB") == {"Flashscore/6"}
    assert fixtures.feeds_for("Premier League") == {"Flashscore/1"}
    # A league not listed takes its fixtures from its own scoring pull,
    # recorded under its own name.
    assert fixtures.feeds_for("NFL") == {"NFL"}
    assert fixtures.feeds_for("NCAAF") == {"NCAAF"}
