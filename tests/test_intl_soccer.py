"""International soccer: the ladder, the ceiling, the fold and the lift.

Every case here is a wrong answer that would have looked right -- a plausible
number, returned without an error, for a season it does not describe.
"""

from datetime import date

import pandas as pd
import pytest

from whul.config.league import league_year
from whul.scoring import intl_soccer as scorer


def match(home, away, hs, aa, competition="FIFA World Cup", rung="world",
          kind="finals", day="2026-06-15", season=2025, gender="M",
          shootout=None, phase="block"):
    return {
        "date": pd.Timestamp(day), "season": season, "gender": gender,
        "competition": competition, "rung": rung, "kind": kind, "phase": phase,
        "home_team": home, "away_team": away,
        "home_score": hs, "away_score": aa, "shootout_winner": shootout,
    }


def points(rows):
    """Per-team match points before the ladder, keyed by team."""
    scored = scorer.per_team(pd.DataFrame(rows))
    return dict(zip(scored["team"], scored["base"]))


# --- a match is scored exactly as a club match is ---------------------------

def test_the_outcome_table_is_the_club_one():
    got = points([match("A", "B", 1, 0)])
    # 3 for the win, 1 for the clean sheet; the margin is one so no big-win.
    assert got["A"] == 4 and got["B"] == 0


def test_winning_by_two_or_more_and_to_nil_is_the_maximum():
    got = points([match("A", "B", 2, 0)])
    assert got["A"] == scorer.MATCH_MAX, "a win by two to nil is the ceiling"


def test_a_goalless_draw_pays_both_sides_for_the_clean_sheet():
    got = points([match("A", "B", 0, 0)])
    assert got["A"] == 2 and got["B"] == 2, "one for the draw, one for the sheet"


def test_a_shootout_win_takes_no_margin_bonus():
    """The match itself was drawn, so there is no margin to be big. The clean
    sheet still applies -- neither side conceded in normal time."""
    got = points([match("A", "B", 0, 0, shootout="A")])
    assert got["A"] == 3, "two for the shootout win, one for the clean sheet"
    assert got["B"] == 2, "a shootout loss is a draw plus the sheet"


def test_a_shootout_is_only_consulted_on_a_level_score():
    """A feed that folded a shootout into the score can misread a tie; it must
    not be able to turn a 3-2 into anything else."""
    got = points([match("A", "B", 3, 2, shootout="B")])
    assert got["A"] == 3 and got["B"] == 0, "a decided match cannot go to penalties"


# --- the ceiling is a ceiling ----------------------------------------------

def _run(wins, competition="CONCACAF Gold Cup", rung="federation", n_group=3):
    """A champion's perfect run: every match won by two to nil."""
    rows = []
    for i in range(wins):
        rows.append(match("Champ", f"Foe{i}", 2, 0, competition=competition,
                          rung=rung, day=f"2026-06-{10 + i:02d}"))
    # A side eliminated in the group stage, so the shape can be inferred.
    for i in range(n_group):
        rows.append(match("Early", f"Foe{i}", 0, 1, competition=competition,
                          rung=rung, day=f"2026-06-{10 + i:02d}"))
    return pd.DataFrame(rows)


def test_a_perfect_run_pays_exactly_its_rung_and_no_more():
    """Before the lift. Winning every match by two to nil is the ceiling, and
    nothing can exceed its own competition."""
    out = scorer.score_teams(_run(6)).set_index("team")
    assert out.loc["Champ", "folded"] == pytest.approx(
        scorer.RUNG["federation"] * scorer.SCALE)
    # ...and the lift then takes a federation-only year to a full ceiling,
    # because that is the best rung this team played.
    assert out.loc["Champ", "total_points"] == pytest.approx(
        max(scorer.RUNG.values()) * scorer.SCALE)


def test_two_formats_of_the_same_rung_are_worth_the_same():
    """Six matches to win the Gold Cup and seven to win AFCON. The rung is a
    rung, so the extra match must not pay extra -- which is the whole reason
    the ceiling is divided by the champion's own path."""
    short = scorer.score_teams(_run(6)).set_index("team").loc["Champ", "folded"]
    long = scorer.score_teams(
        _run(7, competition="Africa Cup of Nations")
    ).set_index("team").loc["Champ", "folded"]
    assert short == pytest.approx(long)


def test_a_world_cup_outranks_a_federation_cup_outranks_a_nations_league():
    def won(competition, rung):
        return scorer.score_teams(
            _run(6, competition=competition, rung=rung)
        ).set_index("team").loc["Champ", "total_points"]

    world = won("FIFA World Cup", "world")
    federation = won("CONCACAF Gold Cup", "federation")
    nations = won("UEFA Nations League", "nations_league")
    # The lift pulls the lower two up to a full ceiling, which is the point of
    # it: each is the best rung that team played that year.
    assert world == federation == nations
    # ...and inside a year that also holds a World Cup, the ladder shows.
    both = pd.concat([_run(6, "FIFA World Cup", "world"),
                      _run(6, "UEFA Nations League", "nations_league")])
    out = scorer.score_teams(both).set_index("team")
    assert out.loc["Champ", "total_points"] == pytest.approx(
        scorer.RUNG["world"] * scorer.SCALE
        + scorer.BEYOND_BEST_SHARE * scorer.RUNG["nations_league"] * scorer.SCALE)


# --- the fold ---------------------------------------------------------------

def test_everything_beyond_the_best_competition_is_halved():
    """Summing them put one two-trophy year at twice the 99th percentile."""
    three = pd.concat([
        _run(6, "FIFA World Cup", "world"),
        _run(6, "CONCACAF Gold Cup", "federation"),
        _run(6, "UEFA Nations League", "nations_league"),
    ])
    out = scorer.score_teams(three).set_index("team")
    expected = scorer.SCALE * (2.0 + 0.5 * 1.5 + 0.5 * 1.0)
    assert out.loc["Champ", "total_points"] == pytest.approx(expected)
    assert out.loc["Champ", "competitions"] == 3


# --- the lift ---------------------------------------------------------------

def test_a_fallow_year_is_lifted_to_the_rung_it_actually_played():
    """A Nations League year would otherwise score half a World Cup year for
    reasons of the calendar alone, and the category goes quiet two years in
    three."""
    out = scorer.score_teams(
        _run(6, "UEFA Nations League", "nations_league")).set_index("team")
    assert out.loc["Champ", "lift"] == pytest.approx(2.0)
    out = scorer.score_teams(_run(6, "FIFA World Cup", "world")).set_index("team")
    assert out.loc["Champ", "lift"] == pytest.approx(1.0)


# --- which league year a match scores in ------------------------------------

def test_the_league_year_opens_in_mid_july():
    assert league_year(date(2024, 7, 14)) == 2024
    assert league_year(date(2024, 7, 13)) == 2023


def test_a_block_tournament_is_not_split_by_the_league_year():
    """Euro 2024 ran 14 June to 14 July and the year turns on the 14th. By
    match date alone the final would score in a different year from the group
    stage, which is the case the rule exists for."""
    from whul.sources.intl_soccer import _assign_season

    rows = pd.DataFrame([
        match("A", "B", 1, 0, day="2024-06-14", phase="block"),
        match("A", "C", 1, 0, day="2024-07-14", phase="block"),
    ])
    out = _assign_season(rows)
    assert list(out["season"]) == [2023, 2023]


def test_a_windowed_phase_scores_where_it_was_played():
    """Qualifying campaigns and Nations League phases run across windows months
    apart and line up with no league year reliably."""
    from whul.sources.intl_soccer import _assign_season

    rows = pd.DataFrame([
        match("A", "B", 1, 0, day="2024-06-14", phase="windows"),
        match("A", "C", 1, 0, day="2024-07-14", phase="windows"),
    ])
    assert list(_assign_season(rows)["season"]) == [2023, 2024]


def test_two_stagings_a_year_apart_are_not_one_block():
    from whul.sources.intl_soccer import _assign_season

    rows = pd.DataFrame([
        match("A", "B", 1, 0, day="2024-06-14", phase="block"),
        match("A", "C", 1, 0, day="2026-06-14", phase="block"),
    ])
    assert list(_assign_season(rows)["season"]) == [2023, 2025]


# --- the data files ---------------------------------------------------------

def test_the_ladder_is_an_allow_list_and_covers_the_rostered_confederations():
    """A regex written against history keeps matching history and drops the
    season being played -- the women's African championship changed spelling in
    2025 and the old pattern stopped matching it."""
    ladder = pd.read_csv(scorer.EDITIONS.parent / "intl_tournaments.csv", comment="#")
    assert set(ladder["rung"]) <= set(scorer.RUNG)
    assert set(ladder["kind"]) == {"finals", "qualifying"}
    assert set(ladder["phase"]) == {"block", "windows"}
    # Qualifying is played across windows whatever the competition.
    quals = ladder[ladder["kind"] == "qualifying"]
    assert set(quals["phase"]) == {"windows"}
    # Both spellings of the African championship, which is the case that bit.
    names = set(ladder["tournament"])
    assert {"African Cup of Nations qualification",
            "Africa Cup of Nations qualification"} <= names


def test_every_stated_edition_names_a_competition_the_ladder_knows():
    """A row keyed on the ledger's spelling rather than the ladder's matches
    nothing and silently does nothing. Two did."""
    ladder = pd.read_csv(scorer.EDITIONS.parent / "intl_tournaments.csv", comment="#")
    stated = pd.read_csv(scorer.EDITIONS, comment="#")
    known = set(zip(ladder["gender"], ladder["competition"]))
    unknown = [(g, c) for g, c in zip(stated["gender"], stated["competition"])
               if (g, c) not in known]
    assert not unknown, f"stated editions nothing can match: {unknown}"


def test_the_supplement_is_shaped_like_the_ledger():
    from whul.sources.intl_soccer import SUPPLEMENT

    extra = pd.read_csv(SUPPLEMENT, comment="#")
    assert {"date", "home_team", "away_team", "home_score", "away_score",
            "tournament", "gender", "shootout_winner"} <= set(extra.columns)
    ladder = pd.read_csv(scorer.EDITIONS.parent / "intl_tournaments.csv", comment="#")
    known = set(zip(ladder["gender"], ladder["tournament"]))
    for gender, tournament in zip(extra["gender"], extra["tournament"]):
        assert (gender, tournament) in known, \
            f"supplement carries {tournament} which the ladder does not score"


def test_a_single_season_pull_still_knows_each_tournament_s_shape():
    """The bug this guards: a tournament's shape is read off the edition that
    was played, so a pull for one league year alone has nothing to read. The
    2026 Women's Africa Cup of Nations came out with no group stage and no
    knockout, its qualifiers became the whole competition, and Ghana topped the
    women's board on two won matches at a full ceiling.

    So the loader returns the whole history with the asked-for years marked,
    and the scorer drops the rest once it has the shapes."""
    played = pd.DataFrame([
        # An edition that was played, in an earlier league year: three group
        # matches and a three-round knockout.
        *[match("Champ", f"F{i}", 2, 0, competition="Women's Africa Cup of Nations",
                rung="federation", gender="W", season=2024,
                day=f"2025-06-{10 + i:02d}") for i in range(6)],
        *[match("Early", f"F{i}", 0, 1, competition="Women's Africa Cup of Nations",
                rung="federation", gender="W", season=2024,
                day=f"2025-06-{10 + i:02d}") for i in range(3)],
        # ...and two qualifiers for the next one, in the year being pulled.
        *[match("Ghana", f"Q{i}", 2, 0, competition="Women's Africa Cup of Nations",
                rung="federation", kind="qualifying", phase="windows",
                gender="W", season=2025, day=f"2025-10-{10 + i:02d}")
          for i in range(2)],
    ])
    played["wanted"] = played["season"] == 2025

    out = scorer.score_teams(played)
    assert set(out["season"]) == {2025}, "seasons outside the ask are dropped"
    ghana = out.set_index("team").loc["Ghana", "total_points"]
    ceiling = max(scorer.RUNG.values()) * scorer.SCALE
    assert ghana < ceiling / 3, (
        f"two won qualifiers scored {ghana:.0f} of a {ceiling:.0f} ceiling; the "
        f"finals are missing from the denominator"
    )
