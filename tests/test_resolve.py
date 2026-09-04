"""Matching a feed's name to the asset a manager drafted.

The failure this guards against is silent: an unmatched asset scores nothing,
and nothing in the standings says why. So the tests are mostly about what must
be *reported* rather than what must be matched.
"""

import pandas as pd
import pytest

from whul import resolve
from whul.store import open_store, rosters


@pytest.fixture
def store():
    return open_store(":memory:")


def feed(*rows, asset_type="Player"):
    name = "team" if asset_type == "Team" else "player"
    return pd.DataFrame([
        {name: n, "league": lg, "total_points": 100.0} for n, lg in rows
    ])


def roster(*rows):
    return pd.DataFrame([
        {"asset_id": f"a{i}", "display_name": n, "asset_type": t, "league": lg}
        for i, (n, lg, t) in enumerate(rows)
    ])


# --- normalizing ---------------------------------------------------------

def test_accents_and_punctuation_do_not_stop_a_match():
    assert resolve.normalize_name("José Ramírez") == resolve.normalize_name("Jose Ramirez")
    assert resolve.normalize_name("Ja'Marr Chase") == resolve.normalize_name("JaMarr Chase")


def test_a_generational_suffix_is_dropped():
    assert resolve.normalize_name("Ken Griffey Jr.") == resolve.normalize_name("Ken Griffey")


def test_club_noise_words_go_but_the_distinguishing_part_stays():
    assert resolve.normalize_team("Nottingham Forest FC") == "nottingham forest"
    # The words that tell two clubs in one city apart are never noise.
    assert resolve.normalize_team("Manchester United") != resolve.normalize_team("Manchester City")


def test_a_name_that_is_only_noise_keeps_itself():
    # Stripping every word would make two unrelated clubs identical.
    assert resolve.normalize_team("Club de Futbol") != ""


# --- matching ------------------------------------------------------------

def test_a_rostered_asset_is_matched_to_its_feed_row():
    scored = feed(("Ja'Marr Chase", "NFL"), ("Someone Else", "NFL"))
    assets = roster(("Ja'Marr Chase", "NFL", "Player"))
    matched, report = resolve.resolve(scored, assets, "Player")

    assert list(matched["asset_id"]) == ["a0"]
    assert len(matched) == 1, "a feed carries everyone; only the roster matters"
    assert report.unmatched == []


def test_a_category_pick_matches_either_competition():
    # A tennis pick is recorded as "Tennis" because that is the category it was
    # drafted into; the feed says which tour.
    scored = feed(("Iga Swiatek", "WTA"), ("Carlos Alcaraz", "ATP"))
    assets = roster(("Iga Swiatek", "Tennis", "Player"), ("Carlos Alcaraz", "Tennis", "Player"))
    matched, report = resolve.resolve(scored, assets, "Player")

    assert len(matched) == 2 and report.unmatched == []


def test_an_asset_with_no_feed_row_is_named_not_dropped():
    scored = feed(("Someone Else", "NFL"))
    assets = roster(("Jeremiyah Love", "NFL", "Player"))
    matched, report = resolve.resolve(scored, assets, "Player")

    assert matched.empty
    assert report.unmatched == [("Jeremiyah Love", "NFL")]
    assert "will score nothing" in str(report)


def test_two_feed_rows_with_one_name_link_neither():
    """A coin flip between two players who share a name is the failure that
    looks like success."""
    scored = feed(("Josh Allen", "NFL"), ("Josh Allen", "NFL"))
    assets = roster(("Josh Allen", "NFL", "Player"))
    matched, report = resolve.resolve(scored, assets, "Player")

    assert matched.empty
    assert report.ambiguous == [("Josh Allen", "NFL", 2)]
    assert "rather than guessed" in str(report)


def test_a_name_in_another_league_is_not_a_match():
    scored = feed(("Josh Allen", "NFL"))
    assets = roster(("Josh Allen", "NBA", "Player"))
    _, report = resolve.resolve(scored, assets, "Player")
    assert report.unmatched == [("Josh Allen", "NBA")]


def test_any_name_the_feed_carries_can_match():
    # The NFL feed identifies a team as "SEA" and names it in another column;
    # a roster has only the full name, and nothing matches those by spelling.
    scored = pd.DataFrame([
        {"team": "SEA", "team_name": "Seattle Seahawks", "league": "NFL",
         "total_points": 266.1},
    ])
    assets = roster(("Seattle Seahawks", "NFL", "Team"))
    matched, report = resolve.resolve(scored, assets, "Team")

    assert list(matched["asset_id"]) == ["a0"]
    # The scorer's own column stays primary, so the rest of the engine still
    # sees the key it speaks.
    assert report.matched[0].feed_name == "SEA"


def test_a_frame_with_no_league_column_still_matches_on_the_name():
    scored = pd.DataFrame([{"player": "Rory McIlroy", "total_points": 900.0}])
    assets = roster(("Rory McIlroy", "PGA", "Player"))
    matched, _ = resolve.resolve(scored, assets, "Player")
    assert list(matched["asset_id"]) == ["a0"]


# --- aliases -------------------------------------------------------------

def add_asset(store, asset_id="a0", name="Ja'Marr Chase", league="NFL"):
    store.upsert("assets", [{
        "asset_id": asset_id, "asset_type": "Player", "display_name": name,
        "league": league, "role": "", "norm_key": league,
        "active": 1, "created_at": "2026-08-21",
    }], keys=("asset_id",))


def test_a_saved_alias_makes_the_next_run_a_lookup(store):
    add_asset(store)
    scored = feed(("Ja'Marr Chase", "NFL"))
    assets = roster(("Ja'Marr Chase", "NFL", "Player"))
    _, first = resolve.resolve(scored, assets, "Player")
    resolve.save_aliases(store, "nfl", first.matched)

    aliases = resolve.load_aliases(store, "nfl")
    assert aliases == {"Ja'Marr Chase": "a0"}


def test_an_alias_matches_a_name_the_spelling_never_would(store):
    add_asset(store, name="Ja'Marr Chase")
    store.upsert("asset_aliases", [{
        "source": "nfl", "source_key": "Chase, JaMarr", "asset_id": "a0",
        "match_kind": "manual", "needs_review": 0, "created_at": "2026-08-21",
    }], keys=("source", "source_key"))

    scored = feed(("Chase, JaMarr", "NFL"))
    assets = roster(("Ja'Marr Chase", "NFL", "Player"))
    matched, report = resolve.resolve(
        scored, assets, "Player", aliases=resolve.load_aliases(store, "nfl")
    )
    assert list(matched["asset_id"]) == ["a0"]
    assert report.matched[0].how == "alias"


def test_an_alias_awaiting_review_is_not_used(store):
    add_asset(store)
    store.upsert("asset_aliases", [{
        "source": "nfl", "source_key": "Someone Else", "asset_id": "a0",
        "match_kind": "name", "needs_review": 1, "created_at": "2026-08-21",
    }], keys=("source", "source_key"))
    assert resolve.load_aliases(store, "nfl") == {}


# --- the roster query ----------------------------------------------------

def test_only_assets_currently_in_a_slot_are_returned(store):
    rosters.add_manager(store, "TG")
    rosters.create_slots(store, "TG", "2026-27")
    add_asset(store, "kept")
    add_asset(store, "released", name="Someone Else")
    slots = store.query(
        "SELECT slot_id FROM roster_slots WHERE season = ? AND asset_type = 'Player'",
        ("2026-27",),
    )
    rosters.assign(store, slots.loc[0, "slot_id"], "kept", "2026-08-21")
    rosters.assign(store, slots.loc[1, "slot_id"], "released", "2026-08-21")
    rosters.release(store, slots.loc[1, "slot_id"], "2026-09-01")

    held = resolve.rostered_assets(store, "2026-27")
    assert list(held["asset_id"]) == ["kept"]


def test_initials_match_however_the_feed_punctuates_them():
    assert resolve.normalize_name("A.J. Brown") == resolve.normalize_name("AJ Brown")


def test_a_suffix_is_only_dropped_at_the_end():
    """Stripping every "jr" would leave J.R. Smith as "smith"."""
    assert resolve.normalize_name("J.R. Smith") == resolve.normalize_name("JR Smith")
    assert resolve.normalize_name("JR Smith") != resolve.normalize_name("Smith")
    assert resolve.normalize_name("Robert Griffin III") == resolve.normalize_name("Robert Griffin")


def test_a_period_that_separates_words_still_does():
    assert resolve.normalize_team("St. Louis Cardinals") == "st louis cardinals"
