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

ROSTER = {
    normalize_team(n): n for n in (
        "Manchester City", "Manchester United", "Internazionale", "Arsenal",
        "Bayern Munich", "Borussia Dortmund", "Paris Saint-Germain",
        "Tottenham Hotspur", "Wolverhampton Wanderers", "Real Madrid",
        "Real Betis",
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

def rostered(store, asset_id, asset_type, name, affiliation="", index=1):
    store.upsert("managers", [{"manager_id": "SS", "display_name": "Scott"}],
                 ["manager_id"])
    store.upsert("assets", [{
        "asset_id": asset_id, "asset_type": asset_type, "league": "Test",
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
             affiliation="Bayern Munich", index=2)
    wanted = fixtures.wanted_teams(store, "2026-27")
    assert set(wanted.values()) == {"Arsenal", "Bayern Munich"}


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
