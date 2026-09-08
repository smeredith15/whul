"""Fixtures: the unplayed half of a schedule the scorers throw away."""

from datetime import date

import pandas as pd

from whul import fixtures
from whul.store import open_store


def schedule(**over):
    """A two-game frame: one played, one not."""
    rows = [
        {"season": 2026, "game_date": "2026-09-01", "home_team": "Alpha FC",
         "away_team": "Beta FC", "home_score": 2.0, "away_score": 1.0},
        {"season": 2026, "game_date": "2026-09-20", "home_team": "Alpha FC",
         "away_team": "Gamma FC", "home_score": None, "away_score": None},
    ]
    frame = pd.DataFrame(rows)
    for column, value in over.items():
        frame[column] = value
    return frame


def test_only_the_unplayed_games_are_kept():
    got = fixtures.harvest("Test", "2026-27", schedule(), date(2026, 9, 8), "now")
    assert set(got["fixture_date"]) == {"2026-09-20"}
    assert set(got["opponent"]) == {"Alpha FC", "Gamma FC"}


def test_both_sides_get_a_row_with_the_right_venue():
    got = fixtures.harvest("Test", "2026-27", schedule(), date(2026, 9, 8), "now")
    home = got[got["team_key"] == "alpha"].iloc[0]
    away = got[got["team_key"] == "gamma"].iloc[0]
    assert home["home"] == 1 and home["opponent"] == "Gamma FC"
    assert away["home"] == 0 and away["opponent"] == "Alpha FC"


def test_a_fixture_already_in_the_past_is_dropped():
    """A schedule downloaded in October still lists August's postponements as
    unplayed, and a next fixture in the past is worse than none."""
    got = fixtures.harvest("Test", "2026-27", schedule(), date(2026, 10, 1), "now")
    assert got.empty


def test_a_frame_with_no_scores_to_read_yields_nothing():
    """The trap this project keeps falling into: `completed` is not carried by
    every feed, and reading its absence as "not played" once turned an entire
    NFL fixture list into finished games. Without a score pair to test, the
    honest answer is no fixtures rather than every row."""
    frame = schedule().drop(columns=["home_score", "away_score"])
    frame["completed"] = [True, False]
    assert fixtures.harvest("Test", "2026-27", frame, date(2026, 9, 8), "now").empty


def test_the_feeds_spelling_is_translated_to_the_rosters():
    """nflverse says SEA and the roster says Seattle Seahawks. Without the
    map the key matches nothing while the harvest looks like it worked."""
    frame = pd.DataFrame([{
        "season": 2026, "game_date": "2026-09-20", "home_team": "SEA",
        "away_team": "ARI", "home_score": None, "away_score": None,
    }])
    got = fixtures.harvest(
        "NFL", "2026-27", frame, date(2026, 9, 8), "now",
        rename={"SEA": "Seattle Seahawks", "ARI": "Arizona Cardinals"},
    )
    assert set(got["team_key"]) == {"seattle seahawks", "arizona cardinals"}
    assert "Arizona Cardinals" in set(got["opponent"])


def test_an_empty_or_shapeless_frame_is_not_an_error():
    for frame in (None, pd.DataFrame(), pd.DataFrame({"nothing": [1]})):
        assert fixtures.harvest("Test", "2026-27", frame, date(2026, 9, 8), "now").empty


# --- the store round trip ---------------------------------------------------

def stocked():
    store = open_store(":memory:")
    rows = fixtures.harvest("Test", "2026-27", schedule(), date(2026, 9, 8), "now")
    fixtures.replace(store, "2026-27", "Test", rows)
    return store


def test_a_postponed_fixture_disappears_rather_than_lingering():
    """Replaced wholesale, not upserted. A date that never arrives is the one
    way this column can be wrong without anyone noticing."""
    store = stocked()
    assert len(store.query("SELECT * FROM fixtures")) == 2
    fixtures.replace(store, "2026-27", "Test", pd.DataFrame())
    assert store.query("SELECT * FROM fixtures").empty


def test_one_league_is_replaced_without_touching_another():
    store = stocked()
    other = fixtures.harvest("Other", "2026-27", schedule(), date(2026, 9, 8), "now")
    fixtures.replace(store, "2026-27", "Other", other)
    fixtures.replace(store, "2026-27", "Test", pd.DataFrame())
    left = store.query("SELECT DISTINCT league FROM fixtures")
    assert list(left["league"]) == ["Other"]


def test_the_soonest_fixture_is_the_next_one():
    store = open_store(":memory:")
    frame = pd.DataFrame([
        {"season": 2026, "game_date": d, "home_team": "Alpha FC",
         "away_team": "Gamma FC", "home_score": None, "away_score": None}
        for d in ("2026-11-02", "2026-09-20", "2026-10-01")
    ])
    fixtures.replace(store, "2026-27", "Test", fixtures.harvest(
        "Test", "2026-27", frame, date(2026, 9, 8), "now"))
    assert fixtures.next_by_team(store, "2026-27", date(2026, 9, 8))["alpha"]["date"] \
        == "2026-09-20"


def test_a_fixture_before_today_is_not_offered_as_next():
    store = stocked()
    assert fixtures.next_by_team(store, "2026-27", date(2026, 12, 1)) == {}


# --- joining to the roster --------------------------------------------------

def rostered(store, asset_id, asset_type, name, affiliation=""):
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
        "category": "Test", "asset_type": asset_type,
        # Unique per (manager, season, category, type), so each test asset
        # needs an index of its own.
        "slot_index": abs(hash(asset_id)) % 10_000,
    }], ["slot_id"])
    store.upsert("slot_occupancy", [{
        "slot_id": slot, "asset_id": asset_id,
        "start_date": "2026-08-21", "end_date": None,
    }], ["slot_id", "start_date"])


def test_a_player_inherits_their_clubs_fixture():
    """A player's next fixture is their club's, joined through the club the
    spreadsheet records against them -- the same join the corner badge uses."""
    store = stocked()
    rostered(store, "team-alpha", "Team", "Alpha FC")
    rostered(store, "player-someone", "Player", "Someone", affiliation="Alpha FC")
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert got["team-alpha"]["opponent"] == "Gamma FC"
    assert got["player-someone"] == got["team-alpha"]


def test_an_athlete_with_no_club_matches_nothing():
    """A driver's affiliation is a country and a golfer has none. Their next
    event is a tournament, which no fixture here describes."""
    store = stocked()
    rostered(store, "player-driver", "Player", "A Driver", affiliation="Great Britain")
    rostered(store, "player-golfer", "Player", "A Golfer")
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert "player-driver" not in got
    assert "player-golfer" not in got


def test_coverage_says_which_leagues_have_fixtures():
    """A column of blanks must be explainable without reading the code."""
    store = stocked()
    cover = fixtures.coverage(store, "2026-27")
    assert list(cover["league"]) == ["Test"]
    assert int(cover.iloc[0]["teams"]) == 2
