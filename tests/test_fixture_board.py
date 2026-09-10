"""Who plays whom: every upcoming fixture with a drafted asset in it.

The roster pages answer "what does this asset play next". This answers the
other half of the same question -- who is playing whom, and which of the
league's assets are on each side -- and a tie with assets on *both* sides is
the row it exists for, because no roster page can show one.
"""

from datetime import date

import pandas as pd
import pytest

from whul import fixtures
from whul.store import open_store


def rostered(store, asset_id, asset_type, name, league, manager="SS",
             affiliation="", index=1):
    store.upsert("managers", [
        {"manager_id": "SS", "display_name": "Scott"},
        {"manager_id": "TG", "display_name": "Tyler"},
    ], ["manager_id"])
    store.upsert("assets", [{
        "asset_id": asset_id, "asset_type": asset_type, "league": league,
        "display_name": name, "affiliation": affiliation,
        "created_at": "2026-08-21",
    }], ["asset_id"])
    slot = f"slot-{asset_id}"
    store.upsert("roster_slots", [{
        "slot_id": slot, "season": "2026-27", "manager_id": manager,
        "category": "Test", "asset_type": asset_type, "slot_index": index,
    }], ["slot_id"])
    store.upsert("slot_occupancy", [{
        "slot_id": slot, "asset_id": asset_id,
        "start_date": "2026-08-21", "end_date": None,
    }], ["slot_id", "start_date"])


def fixture(league, team_key, opponent, home, when="2026-09-12",
            competition="Premier League"):
    return {"season": "2026-27", "league": league, "team_key": team_key,
            "fixture_date": when, "opponent": opponent, "home": home,
            "competition": competition, "round_name": "", "fetched_at": "now"}


def held(store, league, *rows):
    fixtures.replace(store, "2026-27", league, pd.DataFrame(list(rows)))


SOON = date(2026, 9, 8)


# --- one match, not two rows ------------------------------------------------

def test_the_two_rows_of_a_tie_become_one_entry():
    """The table is keyed by side because that is what a lookup wants. A
    fixture list wants the fixture."""
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    rostered(store, "team-chelsea", "Team", "Chelsea", "Premier League",
             manager="TG", index=2)
    held(store, "Flashscore/1",
         fixture("Flashscore/1", "arsenal", "Chelsea", 1),
         fixture("Flashscore/1", "chelsea", "Arsenal", 0))
    got = fixtures.board(store, "2026-27", SOON)
    assert len(got) == 1
    assert [s["name"] for s in got[0]["sides"]] == ["Arsenal", "Chelsea"]
    assert got[0]["owners"] == ["SS", "TG"]


def test_a_one_sided_fixture_still_names_its_opponent():
    """Flashscore keeps only the sides somebody owns, so the other side exists
    only as the `opponent` on the row that survived."""
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    held(store, "Flashscore/1", fixture("Flashscore/1", "arsenal", "Everton", 1))
    got = fixtures.board(store, "2026-27", SOON)
    assert [s["name"] for s in got[0]["sides"]] == ["Arsenal", "Everton"]
    assert got[0]["sides"][1]["assets"] == []


def test_the_home_side_is_the_left_hand_column():
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    held(store, "Flashscore/1", fixture("Flashscore/1", "arsenal", "Everton", 0))
    got = fixtures.board(store, "2026-27", SOON)
    assert [s["name"] for s in got[0]["sides"]] == ["Everton", "Arsenal"]


# --- who is under each side -------------------------------------------------

def test_a_club_and_its_players_stand_under_the_same_side():
    """Which is the point: a manager holding four Bayern players and no Bayern
    still has a stake in the fixture."""
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    rostered(store, "player-saka", "Player", "Bukayo Saka", "Premier League",
             manager="TG", affiliation="Arsenal", index=2)
    held(store, "Flashscore/1", fixture("Flashscore/1", "arsenal", "Everton", 1))
    got = fixtures.board(store, "2026-27", SOON)
    assert got[0]["sides"][0]["assets"] == ["team-arsenal", "player-saka"]
    assert got[0]["sides"][0]["owners"] == ["SS", "TG"]


def test_a_fixture_from_the_wrong_feed_puts_nobody_on_a_side():
    """The same guard the roster column has. A soccer match that reached an
    NFL club read as a plausible NFL game."""
    store = open_store(":memory:")
    rostered(store, "team-bills", "Team", "Buffalo Bills", "NFL")
    held(store, "Flashscore/1",
         fixture("Flashscore/1", "buffalo bills", "New England Patriots", 1))
    assert fixtures.board(store, "2026-27", SOON) == []


def test_a_fixture_with_nobody_in_it_is_not_on_the_board():
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    held(store, "Flashscore/1", fixture("Flashscore/1", "everton", "Fulham", 1))
    assert fixtures.board(store, "2026-27", SOON) == []


# --- a tour event -----------------------------------------------------------

def test_a_tour_event_is_one_heading_with_everyone_under_it():
    """A field is not a fixture. Fifteen golfers at one tournament is one row,
    not fifteen rows with a field of one."""
    store = open_store(":memory:")
    rostered(store, "player-rory", "Player", "Rory McIlroy", "PGA")
    rostered(store, "player-scottie", "Player", "Scottie Scheffler", "PGA",
             manager="TG", index=2)
    held(store, "PGA",
         fixture("PGA", "rory mcilroy", "", 1, competition="Procore"),
         fixture("PGA", "scottie scheffler", "", 1, competition="Procore"))
    got = fixtures.board(store, "2026-27", SOON)
    assert len(got) == 1
    assert got[0]["event"] is True
    assert len(got[0]["sides"]) == 1
    assert got[0]["sides"][0]["name"] == "Procore"
    assert len(got[0]["sides"][0]["assets"]) == 2


# --- what is left out -------------------------------------------------------

def test_a_fixture_with_no_date_is_not_shown():
    """It cannot be placed on a list ordered by date, and a blank at the top
    reads as today."""
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    held(store, "Flashscore/1",
         fixture("Flashscore/1", "arsenal", "Everton", 1, when=""))
    assert fixtures.board(store, "2026-27", SOON) == []


def test_a_fixture_already_played_is_not_upcoming():
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    held(store, "Flashscore/1",
         fixture("Flashscore/1", "arsenal", "Everton", 1, when="2026-09-01"))
    assert fixtures.board(store, "2026-27", SOON) == []


def test_the_board_is_ordered_by_date():
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    held(store, "Flashscore/1",
         fixture("Flashscore/1", "arsenal", "Everton", 1, when="2026-09-13"),
         fixture("Flashscore/1", "arsenal", "Fulham", 1, when="2026-09-10"))
    assert [e["date"] for e in fixtures.board(store, "2026-27", SOON)] == [
        "2026-09-10", "2026-09-13"]


# --- how it renders ---------------------------------------------------------

def test_the_owner_list_is_padded_so_a_filter_cannot_half_match():
    """The filter tests for " SS " in the attribute. Unpadded, a manager whose
    id is a substring of another's would match both."""
    from whul.site.build import _fixture_board

    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    held(store, "Flashscore/1", fixture("Flashscore/1", "arsenal", "Everton", 1))
    html = _fixture_board(store, "2026-27", SOON, {}, ["SS", "TG"])
    assert 'data-owners=" SS "' in html


@pytest.mark.parametrize("competition,feed,expected", [
    # A club plays in five competitions and which one changes what the fixture
    # means.
    ("Champions League", "Flashscore/1", "Champions League"),
    # A season-type code is not a competition. "REG" under a fixture says
    # nothing anybody wants; the league's own name does.
    ("REG", "NFL", "NFL"),
    ("POST", "NFL", "Playoffs"),
    # And a feed id can speak for nobody.
    ("", "Flashscore/6", ""),
])
def test_what_a_fixture_is_labelled_with(competition, feed, expected):
    from whul.site.build import _board_label

    assert _board_label({"competition": competition, "feed": feed}) == expected


def test_a_hidden_row_is_actually_hidden():
    """`display: grid` on a class beats the browser's own `[hidden]` rule, so
    a filtered-out fixture went on being drawn while the count above it
    correctly said it was gone."""
    from whul.site import theme

    assert "[hidden] { display: none !important; }" in theme.STYLESHEET


# --- the horizon ------------------------------------------------------------

def test_only_the_next_week_is_on_the_board():
    """A horizon rather than a default. The first version put the whole stored
    schedule on the standings page -- a hundred and thirteen days of it -- and
    collapsing the far ones hid them without making them cheap: the markup was
    still parsed and the page took a second to open."""
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    held(store, "Flashscore/1",
         # The day itself, the last day inside the week, and the first outside.
         fixture("Flashscore/1", "arsenal", "Everton", 1, when="2026-09-08"),
         fixture("Flashscore/1", "arsenal", "Fulham", 1, when="2026-09-14"),
         fixture("Flashscore/1", "arsenal", "Brentford", 1, when="2026-09-15"))
    assert [e["date"] for e in fixtures.board(store, "2026-27", SOON)] == [
        "2026-09-08", "2026-09-14"]


def test_a_caller_can_still_ask_for_everything():
    """`coverage` and the fixtures command report on the table, not on a page,
    and a horizon would understate what is held."""
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", "Premier League")
    held(store, "Flashscore/1",
         fixture("Flashscore/1", "arsenal", "Brentford", 1, when="2026-11-15"))
    assert fixtures.board(store, "2026-27", SOON) == []
    assert len(fixtures.board(store, "2026-27", SOON, days=None)) == 1
