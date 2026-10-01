"""The best-performances slot in the daily rollup.

Each team sport's player category holds one slot scored on a player's k best
games instead of his season, filled whichever way scores most. These pin that
the rollup does what ``whul.scoring.best_game`` says, on real slot shapes:
occupancy windows, trades, MLB's roles, and the day a game falls on.
"""

from datetime import date

import pandas as pd
import pytest

from whul.bestball import (
    GameIndex, Occupancy, RosterSlot, best_performances, score_slots, standings,
)

D0 = date(2026, 9, 1)


def season(rows):
    """Cumulative season scores: one row per asset, on D0."""
    return pd.DataFrame([{"asset_id": a, "date": D0, "score": s} for a, s in rows])


def games(rows):
    return GameIndex(pd.DataFrame(
        [{"asset_id": a, "game_key": f"{a}-{i}", "date": d, "score": s, "role": r}
         for i, (a, d, s, r) in enumerate(rows)]))


def nfl_slots(*assets, manager="A", category="NFL"):
    return [RosterSlot(f"{manager}:{category}:{i}", manager, category, "Player",
                       [Occupancy(a, D0)]) for i, a in enumerate(assets)]


def test_the_best_slot_takes_the_bench_players_best_weeks():
    slots = nfl_slots("one", "two", "three", "four")
    scores = season([("one", 30), ("two", 20), ("three", 10), ("four", 5)])
    played = games([("three", D0, 4.0, ""), ("three", D0, 6.0, ""),
                    ("four", D0, 3.0, "")])
    out = score_slots(slots, scores, D0, played).set_index("slot_id")

    assert list(out["scored_as"]) == ["season", "season", "best", ""]
    assert out.loc["A:NFL:2", "best_score"] == pytest.approx(10.0)
    total = standings(slots, scores, D0, played)["total"].iloc[0]
    assert total == pytest.approx(30 + 20 + 10)


def test_a_player_worth_more_for_his_best_games_moves_there():
    """His three weeks are worth more than the gap between his season and the
    bench player who takes his seat."""
    slots = nfl_slots("star", "two", "bench")
    scores = season([("star", 30), ("two", 20), ("bench", 19)])
    played = games([("star", D0, 12.0, "")] * 3 + [("bench", D0, 1.0, "")])
    out = score_slots(slots, scores, D0, played).set_index("slot_id")

    assert out.loc["A:NFL:0", "scored_as"] == "best"
    assert out.loc["A:NFL:2", "scored_as"] == "season"
    assert standings(slots, scores, D0, played)["total"].iloc[0] == pytest.approx(20 + 19 + 36)


def test_only_k_games_count():
    played = games([("x", D0, float(s), "") for s in (9, 8, 7, 6)])
    value, chosen = best_performances("NFL", played.between("x", D0, D0))
    assert value == pytest.approx(24.0)
    assert [g.score for g in chosen] == [9.0, 8.0, 7.0]


def test_a_game_after_the_day_is_not_in_it():
    slots = nfl_slots("one", "two", "three")
    scores = season([("one", 30), ("two", 20), ("three", 0)])
    played = games([("three", date(2026, 9, 8), 9.0, "")])
    out = score_slots(slots, scores, D0, played).set_index("slot_id")
    assert out.loc["A:NFL:2", "best_score"] == 0.0


def test_a_trade_keeps_each_game_with_the_slot_that_held_him():
    """Traded on the 10th: his games before it are the first slot's, the ones
    after the second's -- exactly as a season is split."""
    traded = date(2026, 9, 10)
    first = RosterSlot("A:NFL:3", "A", "NFL", "Player",
                       [Occupancy("x", D0, traded - pd.Timedelta(days=1))])
    second = RosterSlot("B:NFL:3", "B", "NFL", "Player", [Occupancy("x", traded)])
    played = games([("x", date(2026, 9, 5), 7.0, ""), ("x", date(2026, 9, 12), 9.0, "")])
    day = date(2026, 9, 20)
    out = score_slots([first, second], season([("x", 0)]), day, played).set_index("slot_id")
    assert out.loc["A:NFL:3", "best_score"] == pytest.approx(7.0)
    assert out.loc["B:NFL:3", "best_score"] == pytest.approx(9.0)


def test_mlb_starts_and_relief_share_the_slot_at_their_rates():
    """k is 4 starts or 13 relief outings, so one start costs a quarter of the
    slot and leaves room for nine outings and three quarters."""
    starts = [("p", D0, 10.0, "start")]
    reliefs = [("p", D0, 1.0, "relief")] * 12
    value, chosen = best_performances("MLB", games(starts + reliefs).between("p", D0, D0))
    assert value == pytest.approx(10.0 + 9 * 1.0)
    assert len(chosen) == 10


def test_soccer_counts_three_seasons_and_one_best():
    slots = nfl_slots("a", "b", "c", "d", "e", category="Club Soccer Top 3")
    scores = season([("a", 30), ("b", 20), ("c", 10), ("d", 8), ("e", 1)])
    played = games([("d", D0, 8.0, ""), ("e", D0, 1.0, "")])
    out = score_slots(slots, scores, D0, played)
    assert sorted(out["scored_as"]) == ["", "best", "season", "season", "season"]
    assert standings(slots, scores, D0, played)["total"].iloc[0] == pytest.approx(68.0)


def test_team_slots_are_untouched():
    slots = [RosterSlot(f"A:NFL:T{i}", "A", "NFL", "Team", [Occupancy(t, D0)])
             for i, t in enumerate(("t1", "t2"))]
    out = score_slots(slots, season([("t1", 30), ("t2", 20)]), D0, games([]))
    assert list(out["scored_as"]) == ["season", "season"]
    assert (out["best_score"] == 0).all()


def test_without_game_records_the_best_slot_is_still_somebody_s():
    """Decided October 2026: the slot is always shown. Before a league had
    played enough for it to pay, the roster showed no best-performances slot
    at all. It holds nothing until it does, so the total is unchanged."""
    from whul.bestball import counted

    slots = nfl_slots("one", "two", "three")
    scores = season([("one", 30), ("two", 20), ("three", 10)])
    out = score_slots(slots, scores, D0)
    assert sorted(out["scored_as"]) == ["best", "season", "season"]
    held = out.set_index("asset_id").loc["three"]
    assert held["scored_as"] == "best"
    assert not held["counts"], "it pays nothing yet, so it is not counting"
    assert counted(out).sum() == pytest.approx(50.0)


def test_a_one_game_best_beats_a_level_season_for_the_slot():
    """Auston Matthews: +0.5 then -0.5, a season of 0.0 and a best game of
    0.5. The slot is his, and the 0.5 counts."""
    from whul.bestball import counted

    slots = nfl_slots("matthews", "hutson", "kaprizov", "hughes", category="NHL")
    scores = season([("matthews", 0.0), ("hutson", 0.0),
                     ("kaprizov", 0.0), ("hughes", 0.0)])
    played = games([("matthews", D0, 0.5, ""), ("matthews", D0, -0.5, "")])
    out = score_slots(slots, scores, D0, played).set_index("asset_id")
    assert out.loc["matthews", "scored_as"] == "best"
    assert out.loc["matthews", "best_score"] == pytest.approx(0.5)
    assert counted(out.reset_index()).sum() == pytest.approx(0.5)
