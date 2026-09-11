"""Keeping what a windowed feed forgets.

Flashscore's tennis feed serves seven days either side of today and has no
more to give, so a season total recomputed from it each night is a rolling
one-week figure wearing a season's name. Taylor Fritz held 150 points for six
days and then zero, having done nothing, because the tournament he won aged out
of the window -- and the standings ledger, which differences consecutive days,
read that as a loss.
"""

from datetime import date

import pandas as pd
import pytest

from whul.store import feed_ledger, open_store


@pytest.fixture
def store():
    return open_store(":memory:")


def _match(uid: str, winner: str, tournament: str = "US Open",
           round_name: str = "Final", season: int = 2026) -> dict:
    return {"match_uid": uid, "winner": winner, "loser": "Someone Else",
            "tournament": tournament, "round": round_name, "season": season,
            "tour": "ATP", "category": "Grand Slam"}


def test_a_row_the_feed_stops_showing_is_still_there(store):
    """The whole point. The feed's window moves on; the season does not."""
    week_one = pd.DataFrame([_match("m1", "Taylor Fritz")])
    feed_ledger.merge(store, "tennis", week_one, ("match_uid",))

    # A week later the feed has rolled past it and shows something else.
    week_two = pd.DataFrame([_match("m2", "Carlos Alcaraz")])
    held = feed_ledger.merge(store, "tennis", week_two, ("match_uid",))

    assert sorted(held["match_uid"]) == ["m1", "m2"]
    assert sorted(held["winner"]) == ["Carlos Alcaraz", "Taylor Fritz"]


def test_an_empty_window_still_answers_for_the_season(store):
    """A quiet week is not a season that unhappened. Between tournaments the
    feed has nothing in its window, and that must not read as nobody having
    played all year."""
    feed_ledger.merge(store, "tennis", pd.DataFrame([_match("m1", "Taylor Fritz")]),
                      ("match_uid",))
    held = feed_ledger.merge(store, "tennis", pd.DataFrame(), ("match_uid",))
    assert list(held["match_uid"]) == ["m1"]


def test_the_same_row_twice_is_one_row(store):
    """The window overlaps itself every night: seven days of it are the same
    seven days as yesterday, and paying for them again would multiply a
    tournament by the number of days it stayed in view."""
    frame = pd.DataFrame([_match("m1", "Taylor Fritz")])
    for _ in range(5):
        held = feed_ledger.merge(store, "tennis", frame, ("match_uid",))
    assert len(held) == 1


def test_a_corrected_result_replaces_the_one_held(store):
    """A retirement recorded as a walkover, a name spelled two ways. The newer
    payload wins; the first sighting does not move, because it is the only
    surviving record of when the match was actually played."""
    feed_ledger.merge(store, "tennis",
                      pd.DataFrame([_match("m1", "Taylor Fritz")]), ("match_uid",))
    first = store.query("SELECT first_seen FROM feed_rows")["first_seen"].iloc[0]

    fixed = _match("m1", "Taylor Fritz")
    fixed["score"] = "6-4 6-4 RET"
    held = feed_ledger.merge(store, "tennis", pd.DataFrame([fixed]), ("match_uid",))

    assert len(held) == 1
    assert held["score"].iloc[0] == "6-4 6-4 RET"
    assert store.query("SELECT first_seen FROM feed_rows")["first_seen"].iloc[0] == first


def test_two_meetings_between_the_same_players_are_two_rows(store):
    """Which is why the key is the feed's own match id and not the names and
    the round. Alcaraz and Sinner can meet in a March final and a June one, and
    a key built from what a person would call the match pays for one of them."""
    held = feed_ledger.merge(store, "tennis", pd.DataFrame([
        {**_match("m1", "Carlos Alcaraz", "Indian Wells"), "loser": "Jannik Sinner"},
        {**_match("m2", "Carlos Alcaraz", "Roland Garros"), "loser": "Jannik Sinner"},
    ]), ("match_uid",))
    assert len(held) == 2


def test_a_key_the_rows_do_not_carry_is_refused(store):
    """A ledger keyed on a column the feed does not produce would give every
    row the same key and keep exactly one of them -- silently, and looking
    exactly like a feed that had gone quiet."""
    with pytest.raises(KeyError, match="event_id"):
        feed_ledger.record(store, "tennis",
                           pd.DataFrame([_match("m1", "Taylor Fritz")]),
                           ("event_id",))


def test_one_source_does_not_see_another(store):
    feed_ledger.merge(store, "tennis", pd.DataFrame([_match("m1", "A")]), ("match_uid",))
    feed_ledger.merge(store, "golf", pd.DataFrame([_match("g1", "B")]), ("match_uid",))
    assert list(feed_ledger.load(store, "tennis")["match_uid"]) == ["m1"]


def test_the_pull_scores_the_union_not_the_window(store, capsys):
    """End to end through the pull, which is where it has to work: the feed is
    asked for its window and the scorer is handed the season."""
    from whul import ingest

    seen = []

    class Windowed:
        key, league, asset_type = "tennis", "Tennis", "Player"
        accumulates = ("match_uid",)
        windowed = False
        dated_by_source = True
        cumulative = True
        produces = ()
        roster_scoped = False
        live = None
        seasons_for = None

        @staticmethod
        def build():
            def load(seasons):
                return pd.DataFrame([_match(f"m{len(seen) + 1}", "Taylor Fritz")])

            def score(matches):
                seen.append(sorted(matches["match_uid"]))
                return pd.DataFrame([
                    {"player": "Taylor Fritz", "total_points": 50.0 * len(matches)}])

            return load, score

    for _ in range(3):
        ingest._pull(Windowed(), date(2026, 9, 11), verbose=False, store=store)

    assert seen[0] == ["m1"]
    assert seen[-1] == ["m1", "m2", "m3"], "the scorer sees every match, not the window"


def test_a_windowed_total_survives_the_window_rolling_past_it(store):
    """Fritz's week, replayed. Tennis is scored over a league-year window, and
    the bug was that the *feed's* window -- seven days -- decided what was in
    it. He held 150 for six days and then zero, having played nothing in
    between."""
    from whul import ingest

    def match(uid, when):
        return {"match_uid": uid, "winner": "Taylor Fritz", "loser": "A N Other",
                "tournament": "Cincinnati", "round": "Final", "season": 2026,
                "date": when, "tour": "ATP", "category": "Masters"}

    shown = {
        "2026-09-05": [match("won-it", "2026-09-03")],
        "2026-09-10": [match("won-it", "2026-09-03")],
        "2026-09-11": [],  # aged out of the feed's window
    }

    def score(matches):
        columns = ["player", "league", "date", "event_points"]
        if matches.empty:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame([
            {"player": "Taylor Fritz", "league": "ATP", "date": row["date"],
             "event_points": 150.0}
            for _, row in matches.iterrows()])

    class Tennis:
        key, league, asset_type = "tennis", "ATP", "Player"
        windowed = dated_by_source = cumulative = True
        produces, roster_scoped, live, seasons_for = ("ATP",), False, None, None
        accumulates = ("match_uid",)
        day = None

        @staticmethod
        def build():
            return (lambda years: pd.DataFrame(shown[Tennis.day]), score)

    def totals(keep):
        held = open_store(":memory:")
        source = Tennis()
        source.accumulates = keep
        out = []
        for day in shown:
            Tennis.day = day
            got = ingest._pull(source, date.fromisoformat(day), verbose=False,
                               store=held)
            out.append(0.0 if got.empty else float(got["total_points"].iloc[0]))
        return out

    assert totals(()) == [150.0, 150.0, 0.0], "the bug, for the record"
    assert totals(("match_uid",)) == [150.0, 150.0, 150.0]
