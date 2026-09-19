"""Where two managers' assets met.

Nothing here scores. These check that a meeting is found where there was one,
is not found where there was not, and is counted once however many feeds
carried it.
"""

import json
from datetime import date

import pytest

from whul import headtohead
from whul.store import open_store, rosters

SEASON = "2026-27"


def _store(tmp_path, assets, rows, opened="2026-08-21"):
    """A store holding ``assets`` -- ``(asset_id, name, league, manager)`` --
    and ``rows`` -- ``(source, payload)`` -- in the feed ledger."""
    store = open_store(str(tmp_path / "h.sqlite3"))
    # The managers only. `create_slots` would lay down the whole 60-slot
    # roster and these tests want exactly the slots they name.
    for manager in {m for *_, m in assets}:
        rosters.add_manager(store, manager)
    store.upsert("assets", [
        {"asset_id": a, "asset_type": "Team", "display_name": n, "league": lg,
         "role": "", "norm_key": lg, "affiliation": "", "active": 1,
         "created_at": opened}
        for a, n, lg, _ in assets
    ], keys=("asset_id",))
    seats: dict[tuple[str, str], int] = {}
    slots = []
    for a, _, lg, m in assets:
        seats[(m, lg)] = seats.get((m, lg), 0) + 1
        slots.append({"slot_id": f"slot-{a}", "manager_id": m,
                      "season": SEASON, "category": lg, "asset_type": "Team",
                      "slot_index": seats[(m, lg)]})
    store.upsert("roster_slots", slots, keys=("slot_id",))
    store.upsert("slot_occupancy", [
        {"slot_id": f"slot-{a}", "asset_id": a, "start_date": opened,
         "end_date": None, "cost": 1.0, "note": ""}
        for a, _, _, _ in assets
    ], keys=("slot_id", "start_date"))
    store.upsert("feed_rows", [
        {"source": src, "row_key": f"{src}-{i}", "season": SEASON,
         "payload": json.dumps(payload), "first_seen": opened,
         "last_seen": opened}
        for i, (src, payload) in enumerate(rows)
    ], keys=("source", "row_key"))
    store.conn.commit()
    return store


TWO_CLUBS = [("a-rays", "Tampa Bay Rays", "MLB", "JM"),
             ("a-as", "Athletics", "MLB", "SS")]


def test_a_game_both_managers_held_is_a_meeting(tmp_path):
    store = _store(tmp_path, TWO_CLUBS, [
        ("mlb-teams", {"game_id": 1, "game_date": "2026-09-17",
                       "home_team": "Tampa Bay Rays", "away_team": "Athletics",
                       "home_score": 10, "away_score": 1}),
    ])

    out = headtohead.meetings(store, SEASON)

    assert len(out) == 1
    row = out.iloc[0]
    assert (row["a_name"], row["b_name"]) == ("Tampa Bay Rays", "Athletics")
    assert (row["a_manager"], row["b_manager"]) == ("JM", "SS")
    assert row["won"] == "a"


def test_a_fixture_nobody_has_played_is_not_a_meeting(tmp_path):
    """Every one of these feeds carries the games still to come -- that is what
    the fixture board is built from -- and a scheduled game in a table of
    results is a row with no result in it."""
    store = _store(tmp_path, TWO_CLUBS, [
        ("mlb-teams", {"game_id": 2, "game_date": "2026-11-28",
                       "home_team": "Tampa Bay Rays", "away_team": "Athletics",
                       "home_score": None, "away_score": None}),
    ])

    assert headtohead.meetings(store, SEASON).empty


def test_one_manager_holding_both_sides_is_not_a_meeting(tmp_path):
    """It is a fixture he cannot lose, which is not a story."""
    store = _store(tmp_path, [("a-rays", "Tampa Bay Rays", "MLB", "JM"),
                              ("a-as", "Athletics", "MLB", "JM")], [
        ("mlb-teams", {"game_id": 3, "game_date": "2026-09-17",
                       "home_team": "Tampa Bay Rays", "away_team": "Athletics",
                       "home_score": 4, "away_score": 3}),
    ])

    assert headtohead.meetings(store, SEASON).empty


def test_a_game_played_before_anyone_held_them_is_not_a_meeting(tmp_path):
    """The MLB ledger reaches back to March and the league year opened in
    August. A game from April belongs to nobody."""
    store = _store(tmp_path, TWO_CLUBS, [
        ("mlb-teams", {"game_id": 4, "game_date": "2026-04-02",
                       "home_team": "Tampa Bay Rays", "away_team": "Athletics",
                       "home_score": 5, "away_score": 2}),
    ])

    assert headtohead.meetings(store, SEASON).empty


def test_a_tie_between_two_leagues_is_counted_once(tmp_path):
    """Como play in Serie A and RB Leipzig in the Bundesliga, so their
    Champions League tie reaches the ledger twice -- once from each club's own
    league pull, with the score the other way round. Both rows name the same
    ESPN event."""
    store = _store(tmp_path, [("a-como", "Como", "Serie A", "JM"),
                              ("a-rbl", "RB Leipzig", "Bundesliga", "LS")], [
        ("seriea", {"event_id": "401915441", "date": "2026-09-10",
                    "team": "Como", "opponent": "RB Leipzig",
                    "goals_for": 4.0, "goals_against": 1.0,
                    "competition_key": "ucl"}),
        ("bundesliga", {"event_id": "401915441", "date": "2026-09-10",
                        "team": "RB Leipzig", "opponent": "Como",
                        "goals_for": 1.0, "goals_against": 4.0,
                        "competition_key": "ucl"}),
    ])

    out = headtohead.meetings(store, SEASON)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["competition"] == "Champions League"
    # And it answers to a reader looking for either league.
    assert {row["a_league"], row["b_league"]} == {"Serie A", "Bundesliga"}
    winner = row["a_name"] if row["won"] == "a" else row["b_name"]
    assert winner == "Como"


def test_a_match_is_found_through_the_feeds_own_spelling(tmp_path):
    """The tennis feed files Carlos Alcaraz as "Carlos Alcaraz Garfia", and a
    lookup on roster names alone loses every match he played."""
    store = _store(tmp_path, [("p-shelton", "Ben Shelton", "ATP", "TG"),
                              ("p-alcaraz", "Carlos Alcaraz", "ATP", "SS")], [
        ("tennis", {"match_uid": "x1", "date": "2026-09-09",
                    "tournament": "US Open", "winner": "Ben Shelton",
                    "loser": "Carlos Alcaraz Garfia", "score": "6-7 6-1 6-3"}),
    ])
    store.upsert("asset_aliases", [{
        "source": "tennis", "source_key": "Carlos Alcaraz Garfia",
        "asset_id": "p-alcaraz", "match_kind": "name", "needs_review": 0,
        "created_at": "2026-09-09"}], keys=("source", "source_key"))
    store.conn.commit()

    out = headtohead.meetings(store, SEASON)

    assert len(out) == 1
    assert out.iloc[0]["competition"] == "US Open"


def test_a_set_score_is_turned_round_to_agree_with_the_winner():
    """The feed writes its sets home-first and names its winner separately, so
    the two disagree whenever the away player won: Rybakina beat Sabalenka and
    the string read "4-6 7-5 2-6", which is a loss."""
    assert headtohead._sets_the_winner_first("4-6 7-5 2-6", "a") == "6-4 5-7 6-2"
    assert headtohead._sets_the_winner_first("6-2 6-3", "a") == "6-2 6-3"
    assert headtohead._sets_the_winner_first("6-2 6-3", "b") == "2-6 3-6"
    assert headtohead._sets_the_winner_first("not a score", "a") == ""


def test_a_record_is_one_row_a_pair(tmp_path):
    """"Jake 7-4 Scott" and "Scott 4-7 Jake" are one fact, and a table holding
    both invites a reader to add them up."""
    store = _store(tmp_path, TWO_CLUBS, [
        ("mlb-teams", {"game_id": i, "game_date": f"2026-09-1{i}",
                       "home_team": "Tampa Bay Rays", "away_team": "Athletics",
                       "home_score": 10 if i < 2 else 1, "away_score": 1 if i < 2 else 10})
        for i in range(3)
    ])

    out = headtohead.records(headtohead.meetings(store, SEASON))

    assert len(out) == 1
    row = out.iloc[0]
    assert (row["one"], row["two"]) == ("JM", "SS")
    assert (row["one_won"], row["two_won"], row["played"]) == (2, 1, 3)


def test_a_draw_is_neither_side_winning(tmp_path):
    store = _store(tmp_path, [("a-psg", "Paris Saint-Germain", "Ligue 1", "SM"),
                              ("a-lille", "Lille", "Ligue 1", "JM")], [
        ("ligue1", {"event_id": "9", "date": "2026-08-28",
                    "team": "Paris Saint-Germain", "opponent": "Lille",
                    "goals_for": 2.0, "goals_against": 2.0,
                    "competition_key": "fra.1"}),
    ])

    found = headtohead.meetings(store, SEASON)

    assert found.iloc[0]["won"] == "draw"
    record = headtohead.records(found).iloc[0]
    assert (record["one_won"], record["two_won"], record["drawn"]) == (0, 0, 1)


def test_a_league_fixture_names_no_competition(tmp_path):
    """The clubs' own leagues are already on the row; repeating one of them in
    a column headed "competition" says less than a blank does."""
    store = _store(tmp_path, [("a-ars", "Arsenal", "Premier League", "SM"),
                              ("a-che", "Chelsea", "Premier League", "TG")], [
        ("epl", {"event_id": "11", "date": "2026-09-06", "team": "Arsenal",
                 "opponent": "Chelsea", "goals_for": 2.0, "goals_against": 1.0,
                 "competition_key": "epl"}),
    ])

    assert headtohead.meetings(store, SEASON).iloc[0]["competition"] == ""


def test_an_event_with_no_two_sides_is_not_read_at_all(tmp_path):
    """Golf and motorsport have neither shape, so they are absent by
    construction rather than by exclusion -- a field of a hundred and fifty is
    not a meeting, and one event routinely holds several assets from the same
    roster."""
    store = _store(tmp_path, TWO_CLUBS, [
        ("pga", {"date": "2026-09-14", "tournament": "Procore",
                 "player": "Rory McIlroy", "position": "3"}),
    ])

    assert headtohead.meetings(store, SEASON).empty


def test_an_nfl_game_is_read_through_the_feeds_abbreviations(tmp_path):
    """nflverse names its clubs "SEA" and "NE" and its date column `gameday`,
    and the alias table already holds the abbreviations because the scorer
    needed them. The ledger is new; everything it is read with is not."""
    store = _store(tmp_path, [("t-sea", "Seattle Seahawks", "NFL", "JM"),
                              ("t-ne", "New England Patriots", "NFL", "SS")], [
        ("nfl-teams", {"season": 2026, "game_id": "2026_03_NE_SEA",
                       "gameday": "2026-09-20", "home_team": "SEA",
                       "away_team": "NE", "home_score": 24.0,
                       "away_score": 20.0}),
    ])
    store.upsert("asset_aliases", [
        {"source": "nfl-teams", "source_key": key, "asset_id": asset,
         "match_kind": "name", "needs_review": 0, "created_at": "2026-08-21"}
        for key, asset in (("SEA", "t-sea"), ("NE", "t-ne"))
    ], keys=("source", "source_key"))
    store.conn.commit()

    out = headtohead.meetings(store, SEASON)

    assert len(out) == 1
    row = out.iloc[0]
    assert (row["a_name"], row["b_name"]) == ("Seattle Seahawks",
                                              "New England Patriots")
    assert row["won"] == "a" and row["date"] == "2026-09-20"


def test_a_feeds_own_name_does_not_leak_into_another_feed(tmp_path):
    """nflverse files the Rams as "LA", which is a club in more than one
    sport. An alias belongs to the feed that uses it."""
    store = _store(tmp_path, [("t-rams", "Los Angeles Rams", "NFL", "JM"),
                              ("t-sea", "Seattle Seahawks", "NFL", "SS")], [
        ("epl", {"event_id": "77", "date": "2026-09-12", "team": "LA",
                 "opponent": "Seattle Seahawks", "goals_for": 2.0,
                 "goals_against": 1.0, "competition_key": "epl"}),
    ])
    store.upsert("asset_aliases", [{
        "source": "nfl-teams", "source_key": "LA", "asset_id": "t-rams",
        "match_kind": "name", "needs_review": 0, "created_at": "2026-08-21"}],
        keys=("source", "source_key"))
    store.conn.commit()

    assert headtohead.meetings(store, SEASON).empty
