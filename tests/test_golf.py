"""PGA scoring tests.

Expected values are read off the points table in PGA.R.
"""

import pandas as pd

from whul.scoring.golf import (
    FINISH_POINTS,
    MAJOR_MULTIPLIER,
    finish_points,
    parse_position,
    score_events,
    score_players,
)


def result(player="Scottie Scheffler", position="1", tournament="Genesis Invitational",
           season=2026, date="2026-02-15"):
    return {"player": player, "position": position, "tournament": tournament,
            "season": season, "date": date}


# --- the points table ------------------------------------------------------

def test_finish_points_match_the_table():
    assert finish_points(1) == 500
    assert finish_points(2) == 300
    assert finish_points(3) == 190
    assert finish_points(10) == 75
    assert finish_points(30) == 10


def test_nothing_scores_below_thirtieth():
    assert finish_points(31) == 0.0
    assert finish_points(70) == 0.0


def test_the_table_covers_exactly_thirty_places():
    assert len(FINISH_POINTS) == 30
    assert list(FINISH_POINTS) == sorted(FINISH_POINTS, reverse=True)


def test_missing_position_scores_nothing():
    assert finish_points(None) == 0.0
    assert finish_points(float("nan")) == 0.0


# --- position parsing ------------------------------------------------------

def test_ties_take_the_position_they_are_tied_at():
    """A five-way tie for third pays each player third-place points; the R
    script does not split them."""
    assert parse_position("T3") == 3.0
    assert parse_position("3") == 3.0


def test_unplaced_entries_parse_to_nothing():
    for value in (None, "CUT", "WD", "MDF", ""):
        assert parse_position(value) is None, value


# --- event scoring ---------------------------------------------------------

def test_majors_are_worth_half_again_as_much():
    events = score_events(pd.DataFrame([
        result(position="1", tournament="Masters Tournament"),
        result(position="1", tournament="Genesis Invitational"),
    ]))
    major = events[events["is_major"]].iloc[0]
    regular = events[~events["is_major"]].iloc[0]
    assert major["event_points"] == 500 * MAJOR_MULTIPLIER
    assert regular["event_points"] == 500


def test_the_players_counts_as_a_major():
    events = score_events(pd.DataFrame([result(tournament="THE PLAYERS Championship")]))
    assert bool(events.iloc[0]["is_major"]) is True


def test_all_four_majors_are_recognized():
    names = ["Masters Tournament", "PGA Championship", "U.S. Open", "The Open Championship"]
    events = score_events(pd.DataFrame([result(tournament=n) for n in names]))
    assert events["is_major"].all()


def test_missed_cuts_are_dropped_not_zeroed():
    """A missed cut has no position to score, so it produces no row -- it must
    not land as a zero that drags an average down."""
    events = score_events(pd.DataFrame([result(position="CUT"), result(position="5")]))
    assert len(events) == 1
    assert events.iloc[0]["position"] == 5


# --- season totals ---------------------------------------------------------

def test_season_total_sums_events():
    results = pd.DataFrame([
        result(position="1"),
        result(position="2", tournament="Arnold Palmer Invitational"),
        result(position="1", tournament="Masters Tournament"),
    ])
    totals = score_players(results, min_events=1)
    row = totals.iloc[0]
    assert row["total_points"] == 500 + 300 + 500 * MAJOR_MULTIPLIER
    assert row["events_played"] == 3
    assert row["wins"] == 2
    assert row["top_tens"] == 3


def test_short_seasons_are_kept_out_of_the_pool():
    """Eight starts is the R script's floor: fewer, and a single good week
    dominates a season that was never really played."""
    results = pd.DataFrame([
        result(player="Regular", tournament=f"Event {i}", position="5")
        for i in range(8)
    ] + [result(player="One Off", position="1")])
    totals = score_players(results)
    assert list(totals["player"]) == ["Regular"]


def test_empty_input_is_empty_output():
    assert score_players(pd.DataFrame()).empty
    assert score_events(pd.DataFrame()).empty


def test_a_tournament_is_marked_for_the_counts_a_profile_shows():
    import pandas as pd

    from whul.scoring import golf as scorer

    events = scorer.score_events(pd.DataFrame([
        {"player": "S. Scheffler", "tournament": "Masters", "position": p,
         "date": d, "season": 2026}
        for p, d in [(1, "2026-08-20"), (4, "2026-08-27"),
                     (9, "2026-09-03"), (75, "2026-09-10")]
    ]))

    assert list(events["wins"]) == [1, 0, 0, 0]
    assert list(events["top_fives"]) == [1, 1, 0, 0]
    assert list(events["top_tens"]) == [1, 1, 1, 0]
    assert list(events["made_cut"]) == [True, True, True, False]
    assert list(events["starts"]) == [1, 1, 1, 1]


def test_the_counts_are_summed_over_a_window_not_a_season():
    """A season total is grouped by calendar year and a league year is not."""
    from datetime import date

    import pandas as pd

    from whul.scoring import golf as scorer
    from whul.scoring.window import season_windows, window_totals

    events = scorer.score_events(pd.DataFrame([
        {"player": "S. Scheffler", "tournament": "Open", "position": p,
         "date": d, "season": 2026}
        for p, d in [(1, "2026-07-05"), (3, "2026-09-06"), (80, "2026-09-13")]
    ]))
    window = season_windows(0, start=date(2026, 8, 1), end=date(2027, 7, 31))[-1]
    out = window_totals(events, [window]).iloc[0]

    assert out["wins"] == 0, "a July win is in the season and not in the year"
    assert (out["starts"], out["top_fives"], out["made_cut"]) == (2, 1, 1)


def test_a_feed_that_forgets_a_tournament_cannot_take_it_off_a_golfer():
    """The driver's fault in golf's words. A tournament that has been played
    cannot be un-played, so a feed that comes back without one is never
    correcting anything -- it is keeping less, and the difference reads in the
    standings as a golfer who lost points by playing."""
    from datetime import date

    import pandas as pd

    from whul import ingest as ing
    from whul.benchmark_sources import resolve
    from whul.store import open_store

    ME = "S. Scheffler"
    whole = [
        {"season": 2026, "event_id": "1", "tournament": "BMW",
         "date": "2026-08-20", "player": ME, "position": 12},
        {"season": 2026, "event_id": "2", "tournament": "TOUR Championship",
         "date": "2026-08-27", "player": ME, "position": 1},
    ]
    shown = {"whole": whole, "short": whole[:1]}
    keys = next(s for s in resolve(None) if s.key == "pga").accumulates

    class Feed:
        key, league, asset_type = "pga-test", "PGA", "Player"
        accumulates = keys
        windowed, dated_by_source, cumulative = True, False, False
        produces, roster_scoped, live = ("PGA",), False, None
        seasons_for = staticmethod(lambda day: [2026])
        which = "whole"

        @staticmethod
        def build():
            from whul.scoring import golf as scorer
            return (lambda _s: pd.DataFrame(shown[Feed.which]), scorer.score_events)

    def week(keep):
        Feed.accumulates = keys if keep else ()
        store = open_store(":memory:")
        out = []
        for day, which in (("2026-09-14", "whole"), ("2026-09-15", "short"),
                           ("2026-09-16", "whole")):
            Feed.which = which
            got = ing._pull(Feed(), date.fromisoformat(day), verbose=False,
                            store=store)
            out.append(0.0 if got is None or got.empty
                       else float(got["total_points"].sum()))
        return out

    before = week(keep=False)
    assert before[1] < before[0], "the fault, for the record"
    after = week(keep=True)
    assert after[0] == after[1] == after[2]


def test_the_autumn_belongs_to_next_year_and_is_asked_for():
    """Scottie Scheffler held 565.0 points off two August events from the
    fourth of September to the sixteenth, with the pull succeeding every night
    and matching thirteen of the fifteen rostered golfers. The PGA Tour's 2026
    season closed six days after the league year opened; the Procore
    Championship, played inside the same league year, is a 2027 event. Asked
    for the calendar year, ESPN returns the first and not the second."""
    from datetime import date

    from whul import ingest as ing
    from whul.scoring import golf as scorer

    ME = "S. Scheffler"
    by_season = {
        2026: [{"season": 2026, "event_id": "1", "tournament": "TOUR Championship",
                "date": "2026-08-27", "player": ME, "position": 1}],
        2027: [{"season": 2027, "event_id": "2", "tournament": "Procore Championship",
                "date": "2026-09-14", "player": ME, "position": 1}],
    }
    asked: list[list[int]] = []

    def load(seasons):
        asked.append(list(seasons))
        rows = [r for s in seasons for r in by_season.get(s, [])]
        return pd.DataFrame(rows)

    class Feed:
        key, league, asset_type = "pga-test", "PGA", "Player"
        accumulates = ()
        windowed, dated_by_source, cumulative = True, False, False
        produces, roster_scoped, live = ("PGA",), False, None
        seasons_for = None

        @staticmethod
        def build():
            return load, scorer.score_events

    def events_on(day):
        got = ing._pull(Feed(), date.fromisoformat(day), verbose=False)
        return 0 if got is None or got.empty else int(got["events"].iloc[0])

    # The calendar year alone, which is what the windowed path used to compute.
    assert events_on("2026-09-16") == 1, "the fault, for the record"
    assert asked[-1] == [2026]

    # And with the source saying which labels its league year can carry.
    from whul.benchmark_sources import _tour_season_labels
    Feed.seasons_for = staticmethod(_tour_season_labels)
    assert events_on("2026-09-16") == 2
    assert asked[-1] == [2026, 2027]


def test_a_tour_that_has_gone_quiet_while_answering_says_so():
    """The pull had no error to report and its figures were plausible -- a tour
    does take weeks off -- so the only thing that could have said anything was
    the gap itself."""
    from datetime import date

    from whul import ingest as ing

    class Feed:
        league = "PGA"

    events = pd.DataFrame([{"date": "2026-08-27", "total_points": 500.0}])
    notes: list[str] = []
    ing._say_if_nothing_new(Feed(), events, date(2026, 9, 16), notes)
    assert notes and "2026-08-27" in notes[0] and "wrong season" in notes[0]

    quiet: list[str] = []
    ing._say_if_nothing_new(Feed(), events, date(2026, 9, 6), quiet)
    assert quiet == [], "a fortnight off is a fortnight off"
