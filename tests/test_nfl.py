"""NFL scoring tests.

Expected values are computed by hand from the formulas in NFL_Players.R and
NFL_Teams.R, so these assert the port reproduces the R semantics rather than
merely reproducing itself.
"""

import pandas as pd
import pytest

from whul.scoring.nfl import score_players, score_teams


def weekly(**over):
    row = {
        "season": 2026, "season_type": "REG", "player_id": "00-1", "player_display_name": "Test QB",
        "position": "QB", "recent_team": "BUF", "week": 1,
        "passing_yards": 0, "passing_tds": 0, "interceptions": 0,
        "rushing_yards": 0, "rushing_tds": 0, "receptions": 0,
        "receiving_yards": 0, "receiving_tds": 0,
        "sack_fumbles_lost": 0, "rushing_fumbles_lost": 0, "receiving_fumbles_lost": 0,
    }
    row.update(over)
    return row


# --- players ---------------------------------------------------------------

def test_half_ppr_totals_match_hand_calculation():
    """4000*0.04 + 30*4 + 10*-2 + 400*0.1 + 3*6 + 1*-2 = 316.0"""
    stats = pd.DataFrame([
        weekly(week=1, passing_yards=2000, passing_tds=15, interceptions=6,
               rushing_yards=200, rushing_tds=2, sack_fumbles_lost=1),
        weekly(week=2, passing_yards=2000, passing_tds=15, interceptions=4,
               rushing_yards=200, rushing_tds=1),
    ])
    out = score_players(stats)
    assert len(out) == 1
    assert out.iloc[0]["total_points"] == pytest.approx(316.0)
    assert out.iloc[0]["games_played"] == 2


def test_receptions_are_half_a_point():
    stats = pd.DataFrame([weekly(position="WR", receptions=10, receiving_yards=100, receiving_tds=1)])
    # 10*0.5 + 100*0.1 + 1*6 = 21.0
    assert score_players(stats).iloc[0]["total_points"] == pytest.approx(21.0)


def test_fumbles_lost_are_summed_across_all_three_sources():
    stats = pd.DataFrame([weekly(
        position="RB", rushing_yards=1000,
        sack_fumbles_lost=1, rushing_fumbles_lost=2, receiving_fumbles_lost=1,
    )])
    # 1000*0.1 - 4*2 = 92.0
    assert score_players(stats).iloc[0]["total_points"] == pytest.approx(92.0)


def test_only_skill_positions_score():
    stats = pd.DataFrame([
        weekly(position="K", player_id="00-2", passing_yards=1000),
        weekly(position="QB", player_id="00-3", passing_yards=1000),
    ])
    assert set(score_players(stats)["position"]) == {"QB"}


def test_non_positive_scorers_are_dropped():
    """Matches the filter(half_ppr_pts > 0) in NFL_Players.R."""
    stats = pd.DataFrame([weekly(interceptions=3)])  # -6.0
    assert score_players(stats).empty


def test_postseason_counts_as_a_bonus_not_raw_stats():
    """One 40-point regular game and one 200-point playoff game.

    Postseason rate 200/game * NFL scalar 1.7 = 340 bonus, on top of the 40
    regular-season points. Raw playoff stats never enter the total directly.
    """
    stats = pd.DataFrame([
        weekly(week=1, passing_yards=1000),
        weekly(week=20, season_type="POST", passing_yards=5000),
    ])
    out = score_players(stats).iloc[0]
    assert out["regular_points"] == pytest.approx(40.0)
    assert out["postseason_games"] == 1
    assert out["postseason_rate"] == pytest.approx(200.0)
    assert out["postseason_bonus"] == pytest.approx(340.0)
    assert out["total_points"] == pytest.approx(380.0)


def test_regular_season_only_mode_ignores_the_postseason():
    """Benchmarks are built this way, so the scale is never skewed by playoffs."""
    stats = pd.DataFrame([
        weekly(week=1, passing_yards=1000),
        weekly(week=20, season_type="POST", passing_yards=5000),
    ])
    out = score_players(stats, postseason=False).iloc[0]
    assert out["total_points"] == pytest.approx(40.0)
    assert out["postseason_bonus"] == 0.0


def test_renamed_upstream_columns_resolve():
    """nflverse has shipped both passing_interceptions and interceptions."""
    stats = pd.DataFrame([weekly(passing_yards=1000)]).rename(
        columns={"interceptions": "passing_interceptions"}
    )
    assert score_players(stats).iloc[0]["total_points"] == pytest.approx(40.0)


def test_missing_required_column_raises():
    stats = pd.DataFrame([weekly()]).drop(columns=["position"])
    with pytest.raises(KeyError):
        score_players(stats)


# --- teams -----------------------------------------------------------------

def game(home, away, hs, as_, game_type="REG", div=0, season=2026):
    return {
        "season": season, "game_type": game_type, "home_team": home, "away_team": away,
        "home_score": hs, "away_score": as_, "div_game": div,
    }


DIVISIONS = pd.DataFrame([
    {"season": 2026, "team_abbr": t, "team_division": "AFC East"} for t in ("BUF", "MIA")
])


def test_team_points_match_hand_calculation():
    """BUF: 2 reg wins, 1 big win (>=9), 1 shutout, 2 div wins, div champ, +38 diff.

    2*10 + 1*3 + 1*5 + 2*2 + 15 + 38*0.1 = 50.8
    """
    sched = pd.DataFrame([
        game("BUF", "MIA", 30, 0, div=1),   # win by 30, shutout, big win, div
        game("MIA", "BUF", 10, 18, div=1),  # BUF win by 8: not a big win
    ])
    out = score_teams(sched, DIVISIONS).set_index("team")
    assert out.loc["BUF", "reg_wins"] == 2
    assert out.loc["BUF", "reg_big_wins"] == 1
    assert out.loc["BUF", "reg_shutouts"] == 1
    assert out.loc["BUF", "div_wins"] == 2
    assert out.loc["BUF", "point_diff"] == 38
    assert out.loc["BUF", "total_points"] == pytest.approx(50.8)


def test_big_win_threshold_is_nine_points():
    sched = pd.DataFrame([game("BUF", "MIA", 9, 0), game("MIA", "BUF", 8, 0)])
    out = score_teams(sched, DIVISIONS).set_index("team")
    assert out.loc["BUF", "reg_big_wins"] == 1  # the 9-point win only
    assert out.loc["MIA", "reg_big_wins"] == 0


def test_shutout_requires_a_win_not_just_a_scoreless_opponent():
    sched = pd.DataFrame([game("BUF", "MIA", 0, 0)])
    out = score_teams(sched, DIVISIONS).set_index("team")
    assert out.loc["BUF", "reg_shutouts"] == 0, "a 0-0 tie is not a shutout win"


def test_exactly_one_division_champion_per_division():
    sched = pd.DataFrame([game("BUF", "MIA", 20, 10), game("MIA", "BUF", 3, 0)])
    out = score_teams(sched, DIVISIONS)
    assert out["div_champ"].sum() == 1


def test_playoff_appearance_and_wins():
    sched = pd.DataFrame([
        game("BUF", "MIA", 20, 10, game_type="WC"),
        game("BUF", "MIA", 20, 10, game_type="SB"),
    ])
    out = score_teams(sched, DIVISIONS).set_index("team")
    assert out.loc["BUF", "playoff_appearance"] == 1
    assert out.loc["BUF", "playoff_wins"] == 2
    assert out.loc["MIA", "playoff_appearance"] == 1
    assert out.loc["MIA", "playoff_wins"] == 0
    assert out.loc["BUF", "point_diff"] == 0, "playoff margins excluded from point diff"


def test_unplayed_games_ignored():
    sched = pd.DataFrame([game("BUF", "MIA", 20, 10), game("BUF", "MIA", None, None)])
    assert score_teams(sched, DIVISIONS).set_index("team").loc["BUF", "reg_wins"] == 1


# --- integration -----------------------------------------------------------

@pytest.mark.network
def test_real_2024_season_sanity():
    from whul.sources import nflverse

    players = score_players(nflverse.load_player_stats([2024]))
    top = players.nlargest(1, "total_points").iloc[0]
    assert top["player"] == "Lamar Jackson"
    assert top["regular_points"] == pytest.approx(428.38, abs=0.01)

    reg_only = score_players(nflverse.load_player_stats([2024]), postseason=False)
    assert reg_only.nlargest(1, "total_points").iloc[0]["total_points"] == pytest.approx(
        428.38, abs=0.01
    )

    teams = score_teams(nflverse.load_schedules([2024]), nflverse.load_teams([2024]))
    assert len(teams) == 32
    assert teams["div_champ"].sum() == 8
    champ = teams.nlargest(1, "total_points").iloc[0]
    assert champ["team"] == "PHI" and champ["playoff_wins"] == 4


def test_postseason_games_counts_player_appearances():
    """The rate denominator is the player's own games, not his team's."""
    stats = pd.DataFrame(
        [weekly(week=w, passing_yards=1000) for w in range(1, 18)]
        + [weekly(week=19, season_type="POST", passing_yards=500),
           weekly(week=21, season_type="POST", passing_yards=500)]
    )
    out = score_players(stats).iloc[0]
    assert out["postseason_games"] == 2, "week 20 was missed and must not count"
    assert out["postseason_rate"] == pytest.approx(20.0)


def test_a_season_with_no_games_played_scores_nothing_rather_than_raising():
    """groupby.apply over an empty frame returns a frame with no columns, so
    every reference after it raises a KeyError naming whichever column is read
    first. In September that read as a broken scorer; it was a season that had
    not kicked off."""
    from whul.scoring.nfl import score_teams

    schedules = pd.DataFrame({
        "season": [2026, 2026], "game_type": ["REG", "REG"],
        "home_team": ["BUF", "KC"], "away_team": ["NYJ", "LV"],
        "home_score": [None, None], "away_score": [None, None],
    })
    assert score_teams(schedules, pd.DataFrame({"team_abbr": [], "team_division": []})).empty


# --- a season nflverse has not reached ------------------------------------

def _nflverse_tables(monkeypatch, standings_seasons, teams_seasons):
    """Stand in for standings.csv and teams.csv at whatever vintage."""
    import pandas as pd

    from whul.sources import nflverse

    abbrs = {"SEA": ("NFC West", "Seattle Seahawks"),
             "SF": ("NFC West", "San Francisco 49ers")}
    standings = pd.DataFrame([
        {"season": s, "team": a, "division": d}
        for s in standings_seasons for a, (d, _) in abbrs.items()
    ])
    named = pd.DataFrame([
        {"season": s, "team": a, "full": n}
        for s in teams_seasons for a, (_, n) in abbrs.items()
    ])
    monkeypatch.setattr(
        nflverse.pd, "read_csv",
        lambda path, **kw: standings if path.endswith("standings.csv") else named,
    )
    return nflverse


def test_a_season_the_tables_have_not_reached_borrows_the_last_one(monkeypatch, capsys):
    """Both files gain a season when it exists, not when it is scheduled, so on
    the morning of week one they can stop at last year. Returning nothing is
    not neutral: every name comes back blank, each team falls back to its
    abbreviation, and "SEA" is offered to a roster that says "Seattle
    Seahawks" -- ten slots on zero in a season being played."""
    nflverse = _nflverse_tables(monkeypatch, [2024, 2025], [2024, 2025])

    teams = nflverse.load_teams([2026])
    assert set(teams["season"]) == {2026}
    assert sorted(teams["team_name"]) == ["San Francisco 49ers", "Seattle Seahawks"]
    assert set(teams["team_division"]) == {"NFC West"}
    # A carried-forward value is a guess, even a good one, so it is announced.
    assert "no 2026 teams yet" in capsys.readouterr().out


def test_a_season_the_tables_do_have_is_left_alone(monkeypatch, capsys):
    nflverse = _nflverse_tables(monkeypatch, [2024, 2025], [2024, 2025])

    teams = nflverse.load_teams([2025])
    assert set(teams["season"]) == {2025}
    assert sorted(teams["team_name"]) == ["San Francisco 49ers", "Seattle Seahawks"]
    assert capsys.readouterr().out == "", "nothing was borrowed, so nothing to say"


def test_standings_reaching_a_season_the_name_table_has_not(monkeypatch):
    """The two files are published separately and need not move together. A
    division without a name is still an abbreviation offered to a roster."""
    nflverse = _nflverse_tables(monkeypatch, [2024, 2025, 2026], [2024, 2025])

    teams = nflverse.load_teams([2026])
    assert sorted(teams["team_name"]) == ["San Francisco 49ers", "Seattle Seahawks"]
