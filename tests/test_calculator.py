"""The calculator must agree with the scorer, or it teaches the wrong thing.

These are cross-checks, not unit tests: a statline is put through the real
scoring function and through the spec the browser is handed, and the two totals
have to match. That is the whole point of generating the spec from the scorers'
own weight tables rather than writing the rules out a second time in
JavaScript -- and it is only true while something checks it.
"""

import pandas as pd
import pytest

from whul.scoring import mlb, nba, nfl, nhl, soccer
from whul.scoring.competition import LEAGUE_WIN, Tier, WIN_POINTS
from whul.site import calculator


def spec(slug: str) -> calculator.Calc:
    found = [c for c in calculator.calculators() if c.slug == slug]
    assert found, f"no calculator called {slug}"
    return found[0]


def total(calc: calculator.Calc, values: dict, scale: float = 1.0) -> float:
    """What the browser will compute, in Python."""
    return sum(
        values.get(f.key, 0) * f.points * (scale if f.scaled else 1.0)
        for f in calc.fields
    )


# --- statlines through both paths ------------------------------------------

def test_an_nfl_line_matches_the_scorer():
    line = {"passing_yards": 4200, "passing_tds": 30, "interceptions": 9,
            "rushing_yards": 340, "rushing_tds": 3, "receptions": 0,
            "receiving_yards": 0, "receiving_tds": 0, "fumbles_lost": 4}
    scored = sum(line[c] * w for c, w in nfl.PLAYER_WEIGHTS.items())
    assert total(spec("calc-nfl-players"), line) == pytest.approx(scored)


def test_an_nfl_team_season_matches_the_scorer():
    season = {"reg_wins": 13, "reg_big_wins": 7, "reg_shutouts": 1,
              "div_wins": 5, "div_champ": 1, "playoff_appearance": 1,
              "playoff_wins": 2, "point_diff": 140}
    scored = sum(season[c] * w for c, w in nfl.TEAM_WEIGHTS.items())
    assert total(spec("calc-nfl-teams"), season) == pytest.approx(scored)


def test_an_nba_line_matches_the_box_score_scorer():
    """Only the box weights are compared: the double-double bonus is a
    per-game test the scorer applies game by game, and the calculator asks for
    the count instead."""
    line = {"points": 2100, "rebounds": 550, "assists": 480, "steals": 90,
            "blocks": 40, "turnovers": 210, "three_pt_made": 190}
    scored = sum(line[c] * w for c, w in nba.BOX_WEIGHTS.items())
    calc = spec("calc-nba-players")
    box_only = sum(
        line.get(f.key, 0) * f.points for f in calc.fields if f.key in line
    )
    assert box_only == pytest.approx(scored)


def test_an_nhl_line_matches_the_scorer():
    frame = pd.DataFrame([{
        "season": 2026, "player": "A Skater", "games_played": 82,
        "goals": 45, "assists": 60, "shots": 300, "plus_minus": 22,
    }])
    scored = float(nhl.score_skaters(frame).iloc[0]["total_points"])
    line = {"goals": 45, "assists": 60, "shots": 300, "plus_minus": 22}
    assert total(spec("calc-nhl-players"), line) == pytest.approx(scored)


def test_a_batting_line_matches_the_scorer():
    line = {"ab": 550, "h": 170, "doubles": 35, "triples": 2, "hr": 38,
            "bb": 70, "hbp": 6, "sb": 12, "cs": 4}
    scored = sum(line[c] * w for c, w in mlb.BATTER_WEIGHTS.items())
    assert total(spec("calc-mlb-batters"), line) == pytest.approx(scored)


def test_a_pitching_line_matches_the_scorer():
    line = {"ip": 190.2, "so": 220, "h": 150, "bb": 45, "hbp": 6, "hr": 18,
            "sv": 0, "hld": 0}
    scored = sum(line[c] * w for c, w in mlb.PITCHER_WEIGHTS.items())
    assert total(spec("calc-mlb-pitchers"), line) == pytest.approx(scored)


# --- the competition premium ------------------------------------------------

@pytest.mark.parametrize("tier", [
    Tier.LEAGUE, Tier.DOMESTIC_CUP, Tier.EUROPA, Tier.CHAMPIONS_LEAGUE,
])
def test_a_win_is_worth_what_the_competition_pays(tier):
    """The thing the request was actually about: a Champions League win is
    five and a league win is three, and the calculator has to say so."""
    calc = spec("calc-soccer-teams")
    scale = WIN_POINTS[tier] / LEAGUE_WIN
    assert total(calc, {"win": 1}, scale) == pytest.approx(WIN_POINTS[tier])


def test_a_draw_is_a_third_of_whatever_a_win_is_there():
    calc = spec("calc-soccer-teams")
    scale = WIN_POINTS[Tier.CHAMPIONS_LEAGUE] / LEAGUE_WIN
    assert total(calc, {"draw": 1}, scale) == pytest.approx(5 / 3)


def test_the_bonuses_do_not_scale_with_the_competition():
    """A clean sheet is worth one wherever it happens; only the result moves."""
    calc = spec("calc-soccer-teams")
    for scale in (1.0, WIN_POINTS[Tier.CHAMPIONS_LEAGUE] / LEAGUE_WIN):
        assert total(calc, {"clean": 1, "big": 1}, scale) == pytest.approx(
            soccer.PTS_CLEAN_SHEET + soccer.PTS_BIG_MARGIN
        )


def test_a_soccer_season_matches_the_match_scorer():
    """Ten league wins, four draws, three clean sheets, two won by two --
    through the calculator and through `score_team_matches`.

    The scores are what make it so: the scorer reads the result off the goals
    rather than trusting a stated outcome, which is how a first draft of this
    test came to claim a 1-1 win.
    """
    def match(goals_for, goals_against, outcome):
        return {"league": "Premier League", "team": "Alpha", "season": 2026,
                "date": "2026-09-01", "competition": "Premier League",
                "competition_key": "", "outcome": outcome,
                "goals_for": goals_for, "goals_against": goals_against}

    matches = (
        [match(2, 0, "win")] * 2      # big and clean
        + [match(1, 0, "win")]        # clean
        + [match(2, 1, "win")] * 7
        + [match(1, 1, "draw")] * 4
    )
    scored = soccer.score_team_matches(pd.DataFrame(matches))
    expected = float(scored["match_points"].sum())
    assert expected == pytest.approx(3 * 10 + 1 * 4 + 1 * 2 + 1 * 3)

    calc = spec("calc-soccer-teams")
    got = total(calc, {"win": 10, "draw": 4, "big": 2, "clean": 3}, scale=1.0)
    assert got == pytest.approx(expected)


# --- the shape the page is handed ------------------------------------------

def test_every_calculator_names_a_benchmark_that_exists():
    """A group pointing at a key with no benchmark would divide by nothing and
    show a score of infinity, or silently show none at all."""
    from whul.store import benchmarks as bm
    from whul.store import open_store

    store = open_store("data/whul.sqlite3")
    version = bm.active_version(store, "2026-27")
    known = set(bm.load(store, version.version)["norm_key"].astype(str))
    for calc in calculator.calculators():
        for label, key in calc.groups:
            assert key in known, f"{calc.slug}: {label} -> {key}"


def test_a_linear_calculator_has_fields_and_a_ladder_has_options():
    for calc in calculator.calculators():
        assert calc.groups, calc.slug
        assert calc.title and calc.intro, calc.slug
        if calc.kind == "linear":
            assert calc.fields and not calc.options, calc.slug
        else:
            assert calc.options and not calc.fields, calc.slug


def test_only_the_result_scales_in_a_league_with_a_premium():
    calc = spec("calc-soccer-teams")
    scaled = {f.key for f in calc.fields if f.scaled}
    assert scaled == {"win", "shootout_win", "draw", "shootout_loss"}


def test_the_payload_carries_the_benchmarks_it_will_divide_by():
    got = calculator.payload({"NFL_QB": 389.6}, "v1")
    assert got["benchmarks"]["NFL_QB"] == pytest.approx(389.6)
    assert got["version"] == "v1"
    assert any(c["slug"] == "calc-nfl-players" for c in got["calcs"])
