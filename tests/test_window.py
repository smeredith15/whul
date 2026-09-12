"""Window-based benchmarking for the individual sports."""

from datetime import date

import pandas as pd

from whul.scoring.window import (
    DEFAULT_YEARS,
    Window,
    _shift_years,
    assign_windows,
    describe,
    season_windows,
    window_totals,
)


def events(rows):
    return pd.DataFrame(
        [{"player": p, "date": d, "event_points": pts, "league": "PGA"} for p, d, pts in rows]
    )


# --- the windows themselves ------------------------------------------------

def test_every_window_spans_the_same_months():
    """That is the whole point: a benchmark window containing a different share
    of offseason than the season would misprice it, and no scalar fixes that."""
    windows = season_windows(4)
    assert len({(w.start.month, w.start.day) for w in windows}) == 1
    assert len({(w.end.month, w.end.day) for w in windows}) == 1


def test_the_current_league_year_is_included_and_last():
    windows = season_windows(3)
    assert len(windows) == 4, "three prior years plus the current one"
    assert windows[-1].start == date(2026, 8, 21)
    assert windows[-1].end == date(2027, 7, 13)


def test_windows_run_oldest_first():
    windows = season_windows(3)
    assert [w.start for w in windows] == sorted(w.start for w in windows)


def test_a_leap_day_moves_back_a_day_not_forward_a_month():
    """March 1 would put the day in the wrong month and could shift it across
    a window boundary."""
    assert _shift_years(date(2028, 2, 29), 1) == date(2027, 2, 28)
    assert _shift_years(date(2028, 3, 1), 1) == date(2027, 3, 1)


def test_a_custom_window_is_honoured():
    """A future season with different dates needs no special handling."""
    windows = season_windows(1, start=date(2030, 1, 1), end=date(2030, 12, 31))
    assert windows[-1].start == date(2030, 1, 1)
    assert windows[0].start == date(2029, 1, 1)


def test_windows_describe_themselves_for_a_report():
    assert describe(season_windows(1))[0].startswith("2025-26: 2025-08-21")


# --- assignment ------------------------------------------------------------

def test_an_event_lands_in_the_window_that_contains_it():
    windows = season_windows(1)
    labelled = assign_windows(events([("A", "2026-09-01", 500)]), windows)
    assert labelled.iloc[0]["window"] == "2026-27"


def test_an_event_in_the_offseason_gap_is_dropped_not_absorbed():
    """Pulling it into the nearest window would inflate whichever absorbed it."""
    windows = season_windows(1)
    # 2026-08-01 falls between the 2025-26 window's end (Jul 13) and the
    # 2026-27 window's start (Aug 21).
    assert assign_windows(events([("A", "2026-08-01", 500)]), windows).empty


def test_an_event_before_the_earliest_window_is_dropped():
    windows = season_windows(1)
    assert assign_windows(events([("A", "2019-09-01", 500)]), windows).empty


def test_an_unparseable_date_is_dropped_rather_than_defaulting():
    windows = season_windows(1)
    assert assign_windows(events([("A", "not a date", 500)]), windows).empty


def test_window_boundaries_are_inclusive_at_both_ends():
    window = Window("test", date(2026, 8, 21), date(2027, 7, 13))
    assert window.contains(date(2026, 8, 21))
    assert window.contains(date(2027, 7, 13))
    assert not window.contains(date(2026, 8, 20))
    assert not window.contains(date(2027, 7, 14))


# --- totals ----------------------------------------------------------------

def test_totals_sum_a_players_events_within_each_window():
    windows = season_windows(1)
    totals = window_totals(events([
        ("A", "2026-09-01", 500),
        ("A", "2027-05-01", 300),
        ("B", "2026-10-01", 190),
    ]), windows).set_index("player")
    assert totals.loc["A", "total_points"] == 800
    assert totals.loc["A", "events"] == 2
    assert totals.loc["B", "total_points"] == 190


def test_a_player_gets_one_row_per_window_they_played_in():
    windows = season_windows(1)
    totals = window_totals(events([
        ("A", "2025-09-01", 100),
        ("A", "2026-09-01", 200),
    ]), windows)
    assert set(totals["season"]) == {"2025-26", "2026-27"}
    assert len(totals) == 2


def test_the_window_label_becomes_the_season_column():
    """That is what lets the benchmark machinery truncate each window to its
    own buffer pool before pooling the survivors."""
    totals = window_totals(events([("A", "2026-09-01", 500)]), season_windows(1))
    assert "season" in totals.columns
    assert totals.iloc[0]["season"] == "2026-27"


def test_window_totals_feed_the_benchmark_machinery():
    from whul.normalize import compute_benchmarks

    rows = []
    for year in (2024, 2025, 2026):
        for i in range(6):
            rows.append((f"P{i}", f"{year}-09-01", 500 - i * 50))
    totals = window_totals(events(rows), season_windows(2))
    totals["role"] = "Golfer"
    totals["league"] = "PGA"
    bench = compute_benchmarks(totals, "Player", season_col="season")
    assert bench.iloc[0]["norm_key"] == "PGA"
    assert bench.iloc[0]["benchmark"] > 0


def test_empty_input_is_empty_output():
    assert window_totals(pd.DataFrame(), season_windows(1)).empty
    assert assign_windows(pd.DataFrame(), season_windows(1)).empty


def test_the_default_reaches_back_far_enough_to_be_stable():
    assert DEFAULT_YEARS >= 3
    assert len(season_windows()) == DEFAULT_YEARS + 1
