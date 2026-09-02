"""NCAA team scoring tests.

All five NCAA categories are team slots only, so these exercise results-based
scoring with no box scores anywhere. Expected values come from the R scripts.
"""

import pandas as pd
import pytest

from whul.scoring.ncaa import score_basketball, score_diamond, score_football


def game(home, away, hs, as_, *, hc="ACC", ac="ACC", season_type=2, notes="", season=2025):
    return {
        "season": season, "season_type": season_type, "notes": notes,
        "home_team": home, "away_team": away,
        "home_conference": hc, "away_conference": ac,
        "home_score": hs, "away_score": as_, "completed": True,
    }


def pad(rows, n, **kw):
    """Filler games so both teams clear the minimum-games filter."""
    return rows + [game("A", "B", 50, 50, **kw) for _ in range(n)]


# --- football --------------------------------------------------------------

def test_football_scoring_matches_hand_calculation():
    """2 wins, 1 big win (>=9), 2 conference wins, +34 diff, sole ACC champion.

    2*10 + 1*2 + 2*2 + 6 (undivided title) + 34*0.05 = 33.7
    """
    sched = pd.DataFrame(pad([
        game("A", "B", 30, 0),      # win by 30: big win, conference game
        game("B", "A", 10, 14),     # win by 4
    ], 6))
    out = score_football(sched).set_index("team")
    assert out.loc["A", "wins"] == 2
    assert out.loc["A", "big_wins"] == 1
    assert out.loc["A", "conf_wins"] == 2
    assert out.loc["A", "point_diff"] == 34
    assert out.loc["A", "pts_reg_champ"] == pytest.approx(6.0)
    assert out.loc["A", "total_points"] == pytest.approx(33.7)


def test_football_big_win_threshold_is_nine():
    sched = pd.DataFrame(pad([game("A", "B", 9, 0), game("A", "B", 8, 0)], 6))
    assert score_football(sched).set_index("team").loc["A", "big_wins"] == 1


def test_football_conference_title_is_split_among_co_champions():
    """Two teams tied on conference wins take half the pool each."""
    sched = pd.DataFrame(pad([
        game("A", "C", 20, 10, ac="ACC"),
        game("B", "D", 20, 10, hc="ACC", ac="ACC"),
    ], 6))
    out = score_football(sched).set_index("team")
    assert out.loc["A", "pts_reg_champ"] == pytest.approx(3.0)
    assert out.loc["B", "pts_reg_champ"] == pytest.approx(3.0)


def test_football_playoff_appearance_and_wins():
    sched = pd.DataFrame(pad([
        game("A", "B", 30, 10, season_type=3, notes="CFP Semifinal at the Rose Bowl"),
        game("A", "B", 20, 10, season_type=3, notes="CFP National Championship"),
    ], 6))
    out = score_football(sched).set_index("team")
    assert out.loc["A", "playoff_app"] == 1
    assert out.loc["A", "playoff_wins"] == 2
    assert out.loc["B", "playoff_wins"] == 0


def test_football_needs_six_games():
    assert score_football(pd.DataFrame(pad([game("A", "B", 20, 10)], 2))).empty


def test_football_requires_conference_affiliation():
    """Conference wins are scored directly, so a feed without it cannot score."""
    sched = pd.DataFrame(pad([game("A", "B", 20, 10, hc="", ac="")], 6, hc="", ac=""))
    assert score_football(sched).empty


# --- basketball ------------------------------------------------------------

def test_basketball_scoring_matches_hand_calculation():
    """2 regular wins, 1 big conference win (>=15), 2 conference wins, +24 diff.

    2*2 + 1*1.5 + 2*1 + 8 (sole champion) + 24*0.03 = 16.22
    """
    sched = pd.DataFrame(pad([
        game("A", "B", 80, 60),   # +20 conference win: big
        game("B", "A", 70, 74),   # +4 win
    ], 10))
    out = score_basketball(sched, "NCAAM").set_index("team")
    assert out.loc["A", "reg_wins"] == 2
    assert out.loc["A", "big_wins"] == 1
    assert out.loc["A", "conf_wins"] == 2
    assert out.loc["A", "point_diff"] == 24
    assert out.loc["A", "total_points"] == pytest.approx(16.22)


def test_basketball_big_win_thresholds_differ_by_opponent():
    """25+ out of conference, 15+ within it."""
    sched = pd.DataFrame(pad([
        game("A", "X", 80, 60, ac="SEC"),   # +20 non-conference: not big
        game("A", "B", 80, 64),             # +16 conference: big
    ], 10))
    assert score_basketball(sched, "NCAAM").set_index("team").loc["A", "big_wins"] == 1


def test_march_madness_appearance_and_wins():
    sched = pd.DataFrame(pad([
        game("A", "B", 70, 60, season_type=3, notes="NCAA Tournament First Round"),
        game("A", "B", 70, 60, season_type=3, notes="NCAA Tournament Sweet 16"),
    ], 10))
    out = score_basketball(sched, "NCAAM").set_index("team")
    assert out.loc["A", "mm_appearance"] == 1
    assert out.loc["A", "mm_wins"] == 2


def test_conference_tournament_is_distinguished_from_march_madness():
    sched = pd.DataFrame(pad([
        game("A", "B", 70, 60, season_type=3, notes="ACC Tournament Championship"),
    ], 10))
    out = score_basketball(sched, "NCAAM").set_index("team")
    assert out.loc["A", "conf_tourney_wins"] == 1
    assert out.loc["A", "conf_tourney_champ"] == 1
    assert out.loc["A", "mm_appearance"] == 0, "a conference tournament is not the NCAAs"


def test_postseason_margins_excluded_from_point_diff():
    sched = pd.DataFrame(pad([
        game("A", "B", 120, 40, season_type=3, notes="NCAA Tournament First Round"),
    ], 10))
    assert score_basketball(sched, "NCAAM").set_index("team").loc["A", "point_diff"] == 0


def test_womens_basketball_uses_a_lower_games_floor():
    """Identical scoring; NCAAW qualifies at 6 games where NCAAM needs 10."""
    sched = pd.DataFrame(pad([game("A", "B", 80, 60)], 6))
    assert score_basketball(sched, "NCAAM").empty
    assert not score_basketball(sched, "NCAAW").empty


# --- baseball and softball -------------------------------------------------

def diamond_game(home, away, hs, as_, notes="", season_type=2, season=2025):
    return game(home, away, hs, as_, notes=notes, season_type=season_type, season=season)


def test_diamond_regular_season_scoring():
    """3 wins * 2.0 + run diff 15 * 0.05 = 6.75"""
    sched = pd.DataFrame(
        [diamond_game("A", "B", 7, 2)] * 3 + [diamond_game("A", "B", 3, 3)] * 9
    )
    out = score_diamond(sched, "NCAA Baseball").set_index("team")
    assert out.loc["A", "reg_wins"] == 3
    assert out.loc["A", "run_diff"] == 15
    assert out.loc["A", "total_points"] == pytest.approx(6.75)


def test_super_regional_is_not_counted_as_a_regional():
    """'Super Regional' contains 'Regional', so order of testing matters."""
    sched = pd.DataFrame(
        [diamond_game("A", "B", 5, 1, notes="Super Regional Game 1", season_type=3)] * 2
        + [diamond_game("A", "B", 3, 3)] * 10
    )
    out = score_diamond(sched, "NCAA Baseball").set_index("team")
    assert out.loc["A", "super_wins"] == 2
    assert out.loc["A", "regional_wins"] == 0
    assert out.loc["A", "series_super"] == 1
    assert out.loc["A", "series_regional"] == 0


def test_series_milestones_have_thresholds():
    sched = pd.DataFrame(
        [diamond_game("A", "B", 5, 1, notes="Regional Game", season_type=3)] * 3
        + [diamond_game("A", "B", 3, 3)] * 10
    )
    out = score_diamond(sched, "NCAA Baseball").set_index("team")
    assert out.loc["A", "series_regional"] == 1


def test_softball_needs_a_fifth_college_world_series_win():
    """Baseball crowns a champion at 4 CWS wins, softball at 5."""
    four = pd.DataFrame(
        [diamond_game("A", "B", 5, 1, notes="Women's College World Series", season_type=3)] * 4
        + [diamond_game("A", "B", 3, 3)] * 20
    )
    assert score_diamond(four, "NCAA Baseball").set_index("team").loc["A", "series_cws_champ"] == 1
    assert score_diamond(four, "NCAA Softball").set_index("team").loc["A", "series_cws_champ"] == 0


def test_postseason_excluded_from_run_differential():
    sched = pd.DataFrame(
        [diamond_game("A", "B", 20, 0, notes="Regional Game", season_type=3)]
        + [diamond_game("A", "B", 3, 3)] * 10
    )
    assert score_diamond(sched, "NCAA Baseball").set_index("team").loc["A", "run_diff"] == 0


def test_diamond_minimum_games_differ():
    sched = pd.DataFrame([diamond_game("A", "B", 5, 1)] * 12)
    assert not score_diamond(sched, "NCAA Baseball").empty
    assert score_diamond(sched, "NCAA Softball").empty


def test_empty_schedules_return_empty():
    for league, fn in (("NCAAF", score_football),):
        assert fn(pd.DataFrame()).empty
    assert score_basketball(pd.DataFrame(), "NCAAM").empty
    assert score_diamond(pd.DataFrame(), "NCAA Baseball").empty


def test_incomplete_games_are_ignored():
    rows = pad([game("A", "B", 20, 10)], 6)
    rows.append({**game("A", "B", 0, 0), "completed": False})
    out = score_football(pd.DataFrame(rows)).set_index("team")
    assert out.loc["A", "wins"] == 1
