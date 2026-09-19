"""Reading the auction's bid logs.

The roster already knows what everything cost. These check that the part it
cannot know -- what everybody else was willing to pay -- comes in intact, and
that where the log and the roster tell different stories the roster wins and
the difference is said out loud.
"""

import pandas as pd

from whul import draft_bids
from whul.store import open_store, rosters

SEASON = "2026-27"


def _store(tmp_path, held):
    """A store holding ``held`` -- ``(asset_id, name, type, league, category,
    manager, cost)``."""
    store = open_store(str(tmp_path / "b.sqlite3"))
    for manager in {h[5] for h in held}:
        rosters.add_manager(store, manager)
    store.upsert("assets", [
        {"asset_id": a, "asset_type": t, "display_name": n, "league": lg,
         "role": "", "norm_key": lg, "affiliation": "", "active": 1,
         "created_at": "2026-08-21"}
        for a, n, t, lg, _, _, _ in held
    ], keys=("asset_id",))
    seats: dict[tuple, int] = {}
    slots = []
    for a, _, t, _, cat, m, _ in held:
        seats[(m, cat, t)] = seats.get((m, cat, t), 0) + 1
        slots.append({"slot_id": f"slot-{a}", "manager_id": m, "season": SEASON,
                      "category": cat, "asset_type": t,
                      "slot_index": seats[(m, cat, t)]})
    store.upsert("roster_slots", slots, keys=("slot_id",))
    store.upsert("slot_occupancy", [
        {"slot_id": f"slot-{a}", "asset_id": a, "start_date": "2026-08-21",
         "end_date": None, "cost": cost, "note": "draft"}
        for a, _, _, _, _, _, cost in held
    ], keys=("slot_id", "start_date"))
    store.conn.commit()
    return store


def log(*rows) -> pd.DataFrame:
    """A bid log frame, in the spreadsheet's own column names."""
    return pd.DataFrame(
        [dict(zip(("Name", "League", "Asset_Type", "Manager", "Bid",
                   "Bid_Status"), r)) for r in rows])


TWO_CLUBS = [
    ("team-arsenal", "Arsenal", "Team", "Premier League", "Club Soccer Top 3",
     "JM", 40.0),
    ("team-chelsea", "Chelsea", "Team", "Premier League", "Club Soccer Top 3",
     "SS", 35.0),
]


# --- reading the file -------------------------------------------------------

def test_the_round_comes_off_the_filename():
    assert draft_bids.round_of("All_Bids_Log_Round_2.xlsx") == 2
    assert draft_bids.round_of("/tmp/round 3.csv") == 3
    assert draft_bids.round_of("bids.xlsx") is None


def test_a_status_is_read_from_its_first_word():
    assert draft_bids._status("Won") == ("won", "Won")
    assert draft_bids._status("Outbid") == ("outbid", "Outbid")
    # The logs spell out why, and the why is kept: over-allocating inside a
    # round is a different mistake from bidding for a slot you never had.
    assert draft_bids._status("Rejected (Roster Filled During Round)") == (
        "rejected", "Rejected (Roster Filled During Round)")
    assert draft_bids._status("Rejected (Roster Full Pre-Round)")[0] == "rejected"
    assert draft_bids._status("") == ("", "")


def test_a_losing_bid_is_kept(tmp_path):
    """The point of importing the file. A winning bid is already on the
    roster; what somebody else would have paid is only here."""
    store = _store(tmp_path, TWO_CLUBS)
    rows, report = draft_bids.plan(store, SEASON, {1: log(
        ("Arsenal", "Premier League", "Team", "JM", 40, "Won"),
        ("Arsenal", "Premier League", "Team", "SS", 30, "Outbid"),
    )})
    assert len(rows) == 2
    assert {r["status"] for r in rows} == {"won", "outbid"}
    assert report.resolved == 2


def test_a_bid_on_an_asset_nobody_drafted_is_kept_without_an_id(tmp_path):
    """Most of the market information in the file is here: the players four
    managers wanted and none of them got."""
    store = _store(tmp_path, TWO_CLUBS)
    rows, report = draft_bids.plan(store, SEASON, {2: log(
        ("Everton", "Premier League", "Team", "JM", 12, "Outbid"),
    )})
    assert len(rows) == 1
    assert rows[0]["asset_id"] == ""
    assert report.resolved == 0
    # Not a complaint: only an unmatched *winner* is worth a line.
    assert report.unresolved == []


def test_a_winner_nobody_holds_is_reported(tmp_path):
    store = _store(tmp_path, TWO_CLUBS)
    _, report = draft_bids.plan(store, SEASON, {1: log(
        ("Everton", "Premier League", "Team", "JM", 12, "Won"),
    )})
    assert len(report.unresolved) == 1
    assert "Everton" in report.unresolved[0]


# --- matching a name to an asset --------------------------------------------

def test_a_name_two_assets_share_is_only_read_with_its_league(tmp_path):
    """England field a men's and a women's side and one manager won both. A
    name alone cannot say which, so the league has to agree too."""
    store = _store(tmp_path, [
        ("team-eng-m", "England", "Team", "Men's Intl Soccer", "Intl Soccer",
         "LS", 50.0),
        ("team-eng-w", "England", "Team", "Women's Intl Soccer", "Intl Soccer",
         "SS", 80.0),
    ])
    rows, _ = draft_bids.plan(store, SEASON, {3: log(
        ("England", "Men's Intl Soccer", "Team", "LS", 50, "Won"),
        ("England", "Women's Intl Soccer", "Team", "SS", 80, "Won"),
    )})
    found = {r["manager_id"]: r["asset_id"] for r in rows}
    assert found == {"LS": "team-eng-m", "SS": "team-eng-w"}


def test_a_club_the_two_files_spell_differently_is_found_by_its_price(tmp_path):
    """The log says "Oklahoma" and the roster says "Oklahoma Sooners". A
    manager who won one thing in a category for one price holds exactly one
    thing in that category at that price, so the price settles it."""
    store = _store(tmp_path, [
        ("team-ou", "Oklahoma Sooners", "Team", "NCAA Softball",
         "NCAA Softball", "TG", 5.0),
    ])
    rows, report = draft_bids.plan(store, SEASON, {2: log(
        ("Oklahoma", "NCAA Softball", "Team", "TG", 5, "Won"),
    )})
    assert rows[0]["asset_id"] == "team-ou"
    assert report.by_price == 1


def test_a_replaced_player_keeps_the_bid_that_bought_the_slot(tmp_path):
    """Logan Thompson was dropped for Cole Hutson at the same price. The bid
    that bought the slot is the bid that bought the slot, whoever ends up in
    it, and the price is what says so."""
    store = _store(tmp_path, [
        ("player-hutson", "Cole Hutson", "Player", "NHL", "NHL", "SM", 1.0),
    ])
    rows, report = draft_bids.plan(store, SEASON, {2: log(
        ("Logan Thompson", "NHL", "Player", "SM", 1, "Won"),
    )})
    assert rows[0]["asset_id"] == "player-hutson"
    assert report.by_price == 1


def test_a_price_two_slots_share_decides_nothing(tmp_path):
    """Uniqueness is the guard here as everywhere else. Two candidates is not
    a near miss to be broken by a further rule."""
    store = _store(tmp_path, [
        ("team-a", "Alpha", "Team", "NCAAM", "NCAAM", "TG", 10.0),
        ("team-b", "Beta", "Team", "NCAAM", "NCAAM", "TG", 10.0),
    ])
    rows, _ = draft_bids.plan(store, SEASON, {1: log(
        ("Gamma", "NCAAM", "Team", "TG", 10, "Won"),
    )})
    assert rows[0]["asset_id"] == ""


def test_a_losing_bid_is_never_matched_on_price(tmp_path):
    """The price rule works because a winning bid bought a slot. A losing bid
    bought nothing, so a matching price is a coincidence."""
    store = _store(tmp_path, [
        ("team-ou", "Oklahoma Sooners", "Team", "NCAA Softball",
         "NCAA Softball", "TG", 5.0),
    ])
    rows, _ = draft_bids.plan(store, SEASON, {2: log(
        ("Oklahoma", "NCAA Softball", "Team", "TG", 5, "Outbid"),
    )})
    assert rows[0]["asset_id"] == ""


# --- where the two files disagree -------------------------------------------

def test_a_traded_asset_is_reported_and_not_corrected(tmp_path):
    """Harry Kane was won for $40 and traded for $100. The roster is what the
    league plays by, so it wins; the difference is printed for someone who
    knows which it was."""
    store = _store(tmp_path, [
        ("player-kane", "Harry Kane", "Player", "Bundesliga",
         "Club Soccer Other", "SS", 100.0),
    ])
    rows, report = draft_bids.plan(store, SEASON, {1: log(
        ("Harry Kane", "Bundesliga", "Player", "SM", 40, "Won"),
    )})
    assert len(report.disagreements) == 1
    assert "SS paid $100" in report.disagreements[0]
    assert "SM won it for $40" in report.disagreements[0]
    # And the bid is stored as the log wrote it: it is a record of the
    # auction, not of the roster.
    assert rows[0]["manager_id"] == "SM" and rows[0]["bid"] == 40


def test_an_asset_with_a_price_and_no_bid_is_reported(tmp_path):
    store = _store(tmp_path, TWO_CLUBS)
    _, report = draft_bids.plan(store, SEASON, {1: log(
        ("Arsenal", "Premier League", "Team", "JM", 40, "Won"),
    )})
    assert len(report.unbid) == 1 and "Chelsea" in report.unbid[0]


def test_a_free_slot_is_not_reported_as_unbid(tmp_path):
    """The snake round filled twenty-five slots at no cost. They were never
    bid for and that is not a gap in the file."""
    store = _store(tmp_path, [
        ("team-arsenal", "Arsenal", "Team", "Premier League",
         "Club Soccer Top 3", "JM", 0.0),
    ])
    _, report = draft_bids.plan(store, SEASON, {1: log(
        ("Everton", "Premier League", "Team", "JM", 3, "Outbid"),
    )})
    assert report.unbid == []


# --- writing ----------------------------------------------------------------

def test_a_round_imported_twice_replaces_itself(tmp_path):
    store = _store(tmp_path, TWO_CLUBS)
    first = log(("Arsenal", "Premier League", "Team", "JM", 40, "Won"),
                ("Arsenal", "Premier League", "Team", "SS", 30, "Outbid"))
    rows, _ = draft_bids.plan(store, SEASON, {1: first})
    draft_bids.apply(store, SEASON, rows)
    assert len(draft_bids.load(store, SEASON)) == 2

    # The same round again, corrected down to one bid.
    again = log(("Arsenal", "Premier League", "Team", "JM", 40, "Won"))
    rows, _ = draft_bids.plan(store, SEASON, {1: again})
    draft_bids.apply(store, SEASON, rows)
    kept = draft_bids.load(store, SEASON)
    assert len(kept) == 1 and kept.iloc[0]["manager_id"] == "JM"


def test_a_file_with_no_round_in_its_name_is_refused(tmp_path):
    store = _store(tmp_path, TWO_CLUBS)
    path = tmp_path / "bids.csv"
    path.write_text("Name,League,Asset_Type,Manager,Bid,Bid_Status\n")
    report = draft_bids.run(store, SEASON, [path], dry_run=True)
    assert report.problems and "round" in report.problems[0]


def test_a_file_with_no_bid_column_is_refused(tmp_path):
    store = _store(tmp_path, TWO_CLUBS)
    frame = pd.DataFrame([{"Name": "Arsenal", "Manager": "JM",
                           "Bid_Status": "Won"}])
    _, report = draft_bids.plan(store, SEASON, {1: frame})
    assert report.problems and "bid" in report.problems[0]


def test_the_category_comes_off_the_league_for_an_undrafted_asset(tmp_path):
    """A losing bid still lands in the category it was competing in, which is
    what lets the round and category tables count it."""
    store = _store(tmp_path, TWO_CLUBS)
    rows, _ = draft_bids.plan(store, SEASON, {2: log(
        ("Everton", "Premier League", "Team", "JM", 12, "Outbid"),
    )})
    assert rows[0]["category"] == "Club Soccer Top 3"


def test_the_logs_in_the_repo_are_named_so_the_round_can_be_read():
    """The round is not in the file, it is in the filename, and the publish
    run passes whatever `draft/` holds. A file renamed on the way in stops the
    import rather than landing in the wrong round -- but it stops the import,
    so the names are worth pinning."""
    from pathlib import Path

    logs = sorted(Path("draft").glob("All_Bids_Log_Round_*.xlsx"))
    assert logs, "no bid logs in draft/"
    assert [draft_bids.round_of(p) for p in logs] == list(range(1, len(logs) + 1))


def test_an_asset_that_changed_hands_is_not_called_a_disagreement(tmp_path):
    """João Pedro and Chelsea were transferred at the price they were bid at.
    The price travelled with the asset, which is what a transfer does and what
    a mistyped initial does not."""
    store = _store(tmp_path, [
        ("player-pedro", "João Pedro", "Player", "Premier League",
         "Club Soccer Top 3", "TG", 38.0),
    ])
    _, report = draft_bids.plan(store, SEASON, {3: log(
        ("João Pedro", "Premier League", "Player", "SM", 38, "Won"),
    )})
    assert report.disagreements == []
    assert len(report.moved) == 1
    assert "SM won it" in report.moved[0] and "TG holds it" in report.moved[0]


def test_a_price_that_moved_is_still_a_disagreement(tmp_path):
    """Harry Kane went for $40 and moved for $100. A new figure is a trade
    struck at a new figure or an entry error, and only the league knows."""
    store = _store(tmp_path, [
        ("player-kane", "Harry Kane", "Player", "Bundesliga",
         "Club Soccer Other", "SS", 100.0),
    ])
    _, report = draft_bids.plan(store, SEASON, {1: log(
        ("Harry Kane", "Bundesliga", "Player", "SM", 40, "Won"),
    )})
    assert report.moved == [] and len(report.disagreements) == 1


def test_a_released_asset_keeps_its_bid(tmp_path):
    """Shelby won Casper Ruud for a dollar and dropped him when he turned out
    to be injured; assets could be released between rounds. The auction still
    happened, so the bid stays."""
    store = _store(tmp_path, [
        ("player-fils", "Arthur Fils", "Player", "ATP", "Tennis", "SS", 75.0),
    ])
    rows, report = draft_bids.plan(store, SEASON, {1: log(
        ("Arthur Fils", "ATP", "Player", "SS", 75, "Won"),
        ("Casper Ruud", "Tennis", "Player", "SS", 1, "Won"),
    )})
    assert len(rows) == 2
    assert len(report.unresolved) == 1
    assert "nobody holds them now" in report.unresolved[0]
