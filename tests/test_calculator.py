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


def test_each_kind_of_calculator_carries_what_its_kind_needs():
    for calc in calculator.calculators():
        assert calc.groups, calc.slug
        assert calc.title and calc.intro, calc.slug
        if calc.kind == "events":
            assert calc.options and not calc.fields, calc.slug
        else:
            assert calc.fields and not calc.options, calc.slug
        if calc.kind == "intl":
            assert calc.ladder, calc.slug
        else:
            assert not calc.ladder, calc.slug


def test_only_the_result_scales_in_a_league_with_a_premium():
    calc = spec("calc-soccer-teams")
    scaled = {f.key for f in calc.fields if f.scaled}
    assert scaled == {"win", "shootout_win", "draw", "shootout_loss"}


def test_the_payload_carries_the_benchmarks_it_will_divide_by():
    got = calculator.payload({"NFL_QB": 389.6}, "v1")
    assert got["benchmarks"]["NFL_QB"] == pytest.approx(389.6)
    assert got["version"] == "v1"
    assert any(c["slug"] == "calc-nfl-players" for c in got["calcs"])


# --- international football -------------------------------------------------

def intl_matches():
    """A complete small tournament, so the scorer can read its own shape.

    Four teams, a round robin, then semi-finals and a final. Every team
    reaches the knockout, so the *group* is four matches and the champion's
    knockout path is one -- which is what the scorer derives, and is the pair
    the calculator asks a reader for. Getting this wrong is the whole risk in
    the panel: the denominator is the champion's path, not the team's.
    """
    rows = []

    def match(date, home, away, home_score, away_score, kind):
        rows.append({"date": date, "season": 2026, "gender": "M",
                     "home_team": home, "away_team": away,
                     "home_score": home_score, "away_score": away_score,
                     "competition": "Test Cup", "rung": "federation",
                     "kind": kind, "shootout_winner": None})

    match("2026-06-01", "Alpha", "Beta", 2, 0, "finals")
    match("2026-06-01", "Gamma", "Delta", 1, 1, "finals")
    match("2026-06-05", "Alpha", "Gamma", 2, 0, "finals")
    match("2026-06-05", "Beta", "Delta", 1, 0, "finals")
    match("2026-06-09", "Alpha", "Delta", 3, 0, "finals")
    match("2026-06-09", "Beta", "Gamma", 0, 0, "finals")
    match("2026-06-14", "Alpha", "Delta", 1, 0, "finals")
    match("2026-06-14", "Beta", "Gamma", 2, 1, "finals")
    match("2026-06-18", "Alpha", "Beta", 1, 0, "finals")
    match("2025-09-01", "Alpha", "Zeta", 2, 0, "qualifying")
    match("2025-09-05", "Alpha", "Eta", 1, 0, "qualifying")
    match("2025-09-09", "Alpha", "Theta", 1, 1, "qualifying")
    match("2025-10-01", "Alpha", "Iota", 3, 1, "qualifying")
    return pd.DataFrame(rows)


def intl_total(units, quals, group, knockout, rung, best_rung, best=True):
    """The calculator's arithmetic, in Python. The browser does exactly this."""
    from whul.scoring.intl_soccer import BEYOND_BEST_SHARE, MATCH_MAX, RUNG, SCALE, STAGE

    path_max = MATCH_MAX * (quals * STAGE["qualifying"] + group * STAGE["group"]
                            + knockout * STAGE["knockout"])
    points = RUNG[rung] * SCALE * units / path_max
    folded = points * (1.0 if best else BEYOND_BEST_SHARE)
    return folded * (max(RUNG.values()) / RUNG[best_rung])


def test_an_international_season_matches_the_scorer():
    from whul.scoring import intl_soccer as isoc
    from whul.scoring.intl_soccer import STAGE

    scored = isoc.score_teams(intl_matches())
    alpha = float(scored[scored["team"] == "Alpha"].iloc[0]["total_points"])

    # Alpha's units, stage by stage, exactly as the panel adds them up.
    quals = (3 * 3 + 1 * 1 + 2 * 1 + 2 * 1) * STAGE["qualifying"]
    group = ((3 * 3 + 3 * 1 + 3 * 1) + (1 * 3 + 0 + 1 * 1)) * STAGE["group"]
    knockout = (1 * 3 + 0 + 1 * 1) * STAGE["knockout"]
    # The season's biggest rung is the federation cup, because it is the only
    # thing Alpha played. Saying "World Cup" here would be describing a
    # different season, and the lift would be 1 instead of 2/1.5 -- which is
    # exactly the mistake the selector exists to let a reader make on purpose.
    got = intl_total(quals + group + knockout, quals=4, group=4, knockout=1,
                     rung="federation", best_rung="federation")
    assert got == pytest.approx(alpha)


def test_a_lesser_competition_counts_at_half():
    """The best competition whole and everything after it at half, so winning
    two trophies does not simply double."""
    whole = intl_total(60, 4, 4, 1, "federation", "world", best=True)
    lesser = intl_total(60, 4, 4, 1, "federation", "world", best=False)
    from whul.scoring.intl_soccer import BEYOND_BEST_SHARE
    assert lesser == pytest.approx(whole * BEYOND_BEST_SHARE)


def test_a_fallow_year_is_lifted_so_its_best_rung_reaches_a_full_ceiling():
    """A Nations League year is not worth half a World Cup year for reasons of
    the calendar alone."""
    from whul.scoring.intl_soccer import RUNG

    world = intl_total(60, 4, 4, 1, "world", "world")
    nations = intl_total(60, 4, 4, 1, "nations_league", "nations_league")
    assert nations == pytest.approx(world)
    # But a Nations League run in a year that also held a World Cup is not.
    alongside = intl_total(60, 4, 4, 1, "nations_league", "world")
    assert alongside == pytest.approx(
        nations * RUNG["nations_league"] / RUNG["world"])


def test_the_international_calculator_carries_the_whole_ladder():
    calc = spec("calc-intl-soccer")
    from whul.scoring.intl_soccer import MATCH_MAX, RUNG, STAGE

    assert calc.kind == "intl"
    assert calc.ladder["stages"] == {k: float(v) for k, v in STAGE.items()}
    assert calc.ladder["match_max"] == pytest.approx(MATCH_MAX)
    assert calc.ladder["best_rung"] == pytest.approx(max(RUNG.values()))
    assert {f["key"] for f in calc.ladder["format"]} == {
        "group_matches", "knockout_rounds"}


# --- one game rather than a season -----------------------------------------

def test_a_postseason_game_is_worth_its_own_rate_again():
    """The bonus is a rate, not a tally: one playoff game at a given line is
    worth that line times a fixed share of a regular season."""
    from whul.scoring.postseason import RULES

    for slug, league in (("calc-nfl-players", "NFL"), ("calc-nba-players", "NBA"),
                         ("calc-mlb-batters", "MLB"), ("calc-nhl-players", "NHL")):
        calc = spec(slug)
        assert calc.postseason is not None, slug
        assert calc.postseason.scalar == pytest.approx(RULES[league].scalar), slug


def test_mlb_shows_both_halves_of_a_bisected_season():
    from whul.scoring import mlb as scoring

    for slug in ("calc-mlb-batters", "calc-mlb-pitchers"):
        calc = spec(slug)
        assert calc.bisection.year_n == pytest.approx(scoring.MULT_YEAR_N)
        assert calc.bisection.year_n1 == pytest.approx(scoring.MULT_YEAR_N1)


def test_the_doubles_a_single_game_derives_are_the_ones_the_scorer_counts():
    calc = spec("calc-nba-players")
    assert calc.doubles.keys == list(nba.DOUBLE_CATEGORIES)
    assert calc.doubles.double == pytest.approx(nba.DOUBLE_DOUBLE_BONUS)
    assert calc.doubles.triple == pytest.approx(nba.TRIPLE_DOUBLE_BONUS)


def test_a_double_double_count_is_only_asked_for_over_a_season():
    """In one game it is a fact about the line, not a number anybody types."""
    calc = spec("calc-nba-players")
    seasonal = {f.key for f in calc.fields if f.mode == "season"}
    assert seasonal == {"dd", "td"}


def test_every_calculator_offering_a_game_says_something_about_each_span():
    """A mode that changes the arithmetic and says nothing about why is a
    calculator that looks broken."""
    for calc in calculator.calculators():
        if len(calc.modes) > 1:
            assert calc.postseason or calc.bisection or calc.mode_notes, calc.slug
