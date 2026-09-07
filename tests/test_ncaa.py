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
    """2 wins, 1 big win, 2 conference wins, +34 diff, sole ACC champion.

    2*10 + 1*2 + 2*2 + 6 (undivided title) + 34*0.05 = 33.7
    """
    sched = pd.DataFrame(pad([
        game("A", "B", 30, 0),      # win by 30 in conference: big
        game("B", "A", 10, 14),     # win by 4
    ], 6))
    out = score_football(sched).set_index("team")
    assert out.loc["A", "wins"] == 2
    assert out.loc["A", "big_wins"] == 1
    assert out.loc["A", "conf_wins"] == 2
    assert out.loc["A", "point_diff"] == 34
    assert out.loc["A", "pts_reg_champ"] == pytest.approx(6.0)
    assert out.loc["A", "total_points"] == pytest.approx(33.7)


def test_football_big_win_bar_is_lower_against_a_stronger_field():
    """13 in conference, 20 out of conference: a blowout is harder to achieve
    against a conference opponent, so it takes fewer points to count."""
    sched = pd.DataFrame(pad([
        game("A", "B", 13, 0),                 # conference, +13: big
        game("A", "B", 12, 0),                 # conference, +12: not big
        game("A", "X", 20, 0, ac="SEC"),       # non-conference, +20: big
        game("A", "Y", 19, 0, ac="Big Ten"),   # non-conference, +19: not big
    ], 6))
    assert score_football(sched).set_index("team").loc["A", "big_wins"] == 2


def test_football_playoff_games_use_the_conference_bar():
    """The postseason field is strong, so the lower bar applies there too."""
    sched = pd.DataFrame(pad([
        game("A", "X", 13, 0, ac="SEC", season_type=3, notes="CFP Quarterfinal"),
    ], 6))
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


def test_lower_division_opponents_are_excluded_by_name():
    """A scoreboard request returns games *involving* a listed team, so the
    opponent may be from a lower division. Those teams played one or two games in
    the ledger and would drag the benchmark down if scored.
    """
    sched = pd.DataFrame(pad([game("A", "FCS School", 45, 3, ac="ACC")], 6))
    out = score_football(sched, eligible={"A", "B"})
    assert set(out["team"]) == {"A", "B"}
    assert "FCS School" not in set(out["team"])


def test_short_seasons_are_kept_when_the_team_belongs():
    """Removing the games floor means a genuinely short season still scores."""
    sched = pd.DataFrame([game("A", "B", 20, 10)])
    out = score_football(sched, eligible={"A", "B"}).set_index("team")
    assert out.loc["A", "wins"] == 1


def test_football_requires_conference_affiliation():
    """Conference wins are scored directly, so a feed without it cannot score --
    and it must say so rather than return nothing. An empty frame here is read
    downstream as "the league has not played yet", which is the opposite of what
    happened: it played, and the feed described it without conferences. That is
    how a whole league goes unscored with no one the wiser."""
    from whul.scoring.ncaa import MissingConference

    sched = pd.DataFrame(pad([game("A", "B", 20, 10, hc="", ac="")], 6, hc="", ac=""))
    with pytest.raises(MissingConference, match="feed problem"):
        score_football(sched)


def test_a_week_with_no_games_is_still_quietly_empty():
    """The loud failure is only for games that arrived unusable. Nothing played
    is not a problem, and must not be reported as one."""
    assert score_football(pd.DataFrame()).empty


def test_football_scores_on_partial_conference_data():
    """One unaffiliated opponent is not a feed failure -- an independent has no
    conference, and the games that do carry one still score."""
    sched = pd.DataFrame(pad([game("A", "B", 20, 10, ac="")], 6))
    out = score_football(sched).set_index("team")
    assert out.loc["A", "wins"] == 1


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


def test_mens_and_womens_basketball_score_identically():
    sched = pd.DataFrame(pad([game("A", "B", 80, 60)], 6))
    men = score_basketball(sched, "NCAAM").set_index("team")["total_points"]
    women = score_basketball(sched, "NCAAW").set_index("team")["total_points"]
    assert men.equals(women)


def test_basketball_excludes_teams_outside_the_division():
    sched = pd.DataFrame(pad([game("A", "DII School", 110, 40, ac="SEC")], 10))
    out = score_basketball(sched, "NCAAM", eligible={"A", "B"})
    assert "DII School" not in set(out["team"])


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
        + [diamond_game("A", "B", 3, 3)]
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
        + [diamond_game("A", "B", 3, 3)]
    )
    assert score_diamond(four, "NCAA Baseball").set_index("team").loc["A", "series_cws_champ"] == 1
    assert score_diamond(four, "NCAA Softball").set_index("team").loc["A", "series_cws_champ"] == 0


def test_postseason_excluded_from_run_differential():
    sched = pd.DataFrame(
        [diamond_game("A", "B", 20, 0, notes="Regional Game", season_type=3)]
        + [diamond_game("A", "B", 3, 3)] * 10
    )
    assert score_diamond(sched, "NCAA Baseball").set_index("team").loc["A", "run_diff"] == 0


def test_diamond_excludes_teams_outside_the_division():
    sched = pd.DataFrame(
        [diamond_game("A", "JC School", 15, 0)] + [diamond_game("A", "B", 3, 3)] * 10
    )
    out = score_diamond(sched, "NCAA Baseball", eligible={"A", "B"})
    assert "JC School" not in set(out["team"])


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


# --- Notre Dame -----------------------------------------------------------

def _fb_game(home, away, home_conf, away_conf, hs, as_, season_type=2, notes=""):
    return {
        "season": 2026, "season_type": season_type, "notes": notes,
        "home_team": home, "away_team": away,
        "home_conference": home_conf, "away_conference": away_conf,
        "home_score": hs, "away_score": as_, "completed": True,
    }


def test_notre_dame_is_scored_as_a_conference_member():
    """They play football as an independent, so an unmodified feed gives them
    no conference games at all and the conference-wins term is zero however the
    season goes. The league's rule is to score them as ACC."""
    import pandas as pd

    from whul.scoring.ncaa import _team_games

    games = _team_games(pd.DataFrame([
        # Miami is the exemplar: whatever the feed calls the ACC, they have it.
        _fb_game("Miami Hurricanes", "Notre Dame Fighting Irish", "1", "18", 20, 17),
    ]))
    nd = games[games["team"] == "Notre Dame Fighting Irish"].iloc[0]
    miami = games[games["team"] == "Miami Hurricanes"].iloc[0]
    assert nd["conference"] == "1", "put into the ACC"
    assert bool(nd["is_conf_game"]) and bool(miami["is_conf_game"]), \
        "and both sides agree it was a conference game"


def test_the_override_moves_the_opponents_view_too():
    """A conference game is one whose two sides share a conference. Moving one
    side and not the other leaves a table that does not add up."""
    import pandas as pd

    from whul.scoring.ncaa import _team_games

    games = _team_games(pd.DataFrame([
        _fb_game("Miami Hurricanes", "Notre Dame Fighting Irish", "1", "18", 20, 17),
    ]))
    miami = games[games["team"] == "Miami Hurricanes"].iloc[0]
    assert miami["opp_conference"] == "1"


def test_notre_dame_never_wins_the_conference():
    """Scored as a member, they could top the table on a technicality and take
    a title they are not eligible for."""
    import pandas as pd

    from whul.scoring.ncaa import score_football

    rows = [
        _fb_game("Notre Dame Fighting Irish", "Miami Hurricanes", "18", "1", 30, 3),
        _fb_game("Notre Dame Fighting Irish", "Duke Blue Devils", "18", "1", 30, 3),
        _fb_game("Miami Hurricanes", "Duke Blue Devils", "1", "1", 21, 20),
    ]
    scored = score_football(pd.DataFrame(rows))
    by_team = scored.set_index("team")
    assert by_team.loc["Notre Dame Fighting Irish", "pts_reg_champ"] == 0.0
    # They still played, and still lead the table on conference wins. The
    # title is simply unwon -- excluded after the maximum is taken, so it is
    # not handed down to whoever finished behind them.
    assert by_team.loc["Notre Dame Fighting Irish", "conf_wins"] == 2
    assert by_team.loc["Miami Hurricanes", "pts_reg_champ"] == 0.0


def test_a_team_with_no_exemplar_in_the_frame_is_left_alone():
    """Scoring short is recoverable; a wrong conference is a title awarded to
    the wrong programme."""
    import pandas as pd

    from whul.scoring.ncaa import _team_games

    games = _team_games(pd.DataFrame([
        _fb_game("Notre Dame Fighting Irish", "Navy Midshipmen", "18", "18", 30, 3),
    ]))
    nd = games[games["team"] == "Notre Dame Fighting Irish"].iloc[0]
    assert nd["conference"] == "18", "unchanged, since no ACC team was present"
