from datetime import date

import pandas as pd
import pytest

from whul.bestball import Occupancy, RosterSlot, accrue, score_slots, slot_score, standings


def cumulative(rows):
    """rows: (asset_id, 'YYYY-MM-DD', cumulative_score)"""
    return pd.DataFrame(
        [{"asset_id": a, "date": date.fromisoformat(d), "score": s} for a, d, s in rows]
    )


def nfl_player_slot(slot_id, manager, asset, start="2026-08-21"):
    return RosterSlot(
        slot_id, manager, "NFL", "Player", [Occupancy(asset, date.fromisoformat(start))]
    )


# --- accrual ---------------------------------------------------------------

def test_accrue_differences_the_cumulative_series():
    cum = cumulative([("a", "2026-09-01", 10.0), ("a", "2026-09-10", 40.0)])
    assert accrue(cum, "a", date(2026, 9, 2), date(2026, 9, 10)) == 30.0


def test_accrue_from_season_start_takes_the_full_total():
    cum = cumulative([("a", "2026-09-10", 40.0)])
    assert accrue(cum, "a", date(2026, 8, 21), date(2026, 9, 10)) == 40.0


def test_accrue_is_zero_for_unknown_asset():
    assert accrue(cumulative([("a", "2026-09-01", 10.0)]), "ghost", date(2026, 8, 21), date(2026, 9, 1)) == 0.0


# --- best-ball selection ---------------------------------------------------

def test_top_k_counts_and_the_rest_are_bench():
    """NFL players: 4 rostered, only the best 2 count."""
    cum = cumulative(
        [(a, "2026-12-01", v) for a, v in [("p1", 100.0), ("p2", 98.0), ("p3", 97.0), ("p4", 80.0)]]
    )
    slots = [nfl_player_slot(f"s{i}", "M1", f"p{i}") for i in range(1, 5)]
    scored = score_slots(slots, cum, date(2026, 12, 1))
    counting = scored[scored["counts"]]
    assert sorted(counting["score"], reverse=True) == [100.0, 98.0]
    assert counting["score"].sum() == 198.0


def test_selection_is_live_and_flips_as_scores_move():
    cum = cumulative(
        [("p1", "2026-10-01", 50.0), ("p2", "2026-10-01", 10.0),
         ("p1", "2026-12-01", 55.0), ("p2", "2026-12-01", 90.0)]
    )
    slots = [nfl_player_slot("s1", "M1", "p1"), nfl_player_slot("s2", "M1", "p2")]
    early = score_slots(slots, cum, date(2026, 10, 1)).set_index("slot_id")["score"]
    late = score_slots(slots, cum, date(2026, 12, 1)).set_index("slot_id")["score"]
    assert early["s1"] > early["s2"]
    assert late["s2"] > late["s1"], "no manager action required for the cut to change"


def test_team_slots_all_count():
    """Team categories have no bench, so every slot scores."""
    cum = cumulative([("t1", "2026-12-01", 60.0), ("t2", "2026-12-01", 40.0)])
    slots = [
        RosterSlot("s1", "M1", "NFL", "Team", [Occupancy("t1", date(2026, 8, 21))]),
        RosterSlot("s2", "M1", "NFL", "Team", [Occupancy("t2", date(2026, 8, 21))]),
    ]
    scored = score_slots(slots, cum, date(2026, 12, 1))
    assert scored["counts"].all()


def test_injured_starter_sinks_below_the_cut_automatically():
    """A player who stops accruing is replaced by the bench with no transaction."""
    cum = cumulative(
        [("hurt", "2026-10-01", 60.0), ("hurt", "2027-01-01", 60.0),
         ("depth", "2026-10-01", 10.0), ("depth", "2027-01-01", 75.0),
         ("star", "2026-10-01", 80.0), ("star", "2027-01-01", 140.0),
         ("mid", "2026-10-01", 20.0), ("mid", "2027-01-01", 55.0)]
    )
    slots = [nfl_player_slot(f"s{i}", "M1", a) for i, a in enumerate(["star", "hurt", "depth", "mid"])]
    late = score_slots(slots, cum, date(2027, 1, 1))
    counting = set(late.loc[late["counts"], "asset_id"])
    assert counting == {"star", "depth"}, "the injured starter drops out on his own"


# --- trades ----------------------------------------------------------------

def test_trade_keeps_prior_points_with_the_slot():
    """OPEN-5: slot score = outgoing player's start-to-trade + incoming's trade-to-now."""
    cum = cumulative(
        [("out", "2026-11-30", 50.0), ("out", "2027-01-31", 80.0),
         ("in", "2026-11-30", 12.0), ("in", "2027-01-31", 42.0)]
    )
    slot = RosterSlot(
        "s1", "M1", "NFL", "Player",
        [
            Occupancy("out", date(2026, 8, 21), date(2026, 11, 30)),
            Occupancy("in", date(2026, 12, 1)),
        ],
    )
    # 50 earned by the outgoing player + 30 earned by the incoming player since.
    assert slot_score(slot, cum, date(2027, 1, 31)) == pytest.approx(80.0)


def test_traded_away_player_stops_contributing_to_the_old_slot():
    cum = cumulative([("out", "2026-11-30", 50.0), ("out", "2027-06-01", 500.0)])
    slot = RosterSlot(
        "s1", "M1", "NFL", "Player", [Occupancy("out", date(2026, 8, 21), date(2026, 11, 30))]
    )
    assert slot_score(slot, cum, date(2027, 6, 1)) == 50.0, "later points belong to the new owner"


def test_current_occupant_reported_for_display():
    cum = cumulative([("out", "2026-11-30", 50.0), ("in", "2027-01-31", 42.0)])
    slot = RosterSlot(
        "s1", "M1", "NFL", "Player",
        [Occupancy("out", date(2026, 8, 21), date(2026, 11, 30)), Occupancy("in", date(2026, 12, 1))],
    )
    scored = score_slots([slot], cum, date(2027, 1, 31))
    assert scored.iloc[0]["asset_id"] == "in"


# --- standings -------------------------------------------------------------

def test_standings_sum_only_counting_slots():
    cum = cumulative(
        [("a1", "2026-12-01", 90.0), ("a2", "2026-12-01", 80.0), ("a3", "2026-12-01", 70.0),
         ("b1", "2026-12-01", 100.0), ("b2", "2026-12-01", 60.0), ("b3", "2026-12-01", 55.0)]
    )
    slots = [nfl_player_slot(f"a{i}", "Alice", f"a{i}") for i in (1, 2, 3)] + [
        nfl_player_slot(f"b{i}", "Bob", f"b{i}") for i in (1, 2, 3)
    ]
    table = standings(slots, cum, date(2026, 12, 1))
    assert list(table["manager"]) == ["Alice", "Bob"]
    assert list(table["total"]) == [170.0, 160.0]  # 90+80 vs 100+60; the 3rd is bench
    assert list(table["rank"]) == [1, 2]
