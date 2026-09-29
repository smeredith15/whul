"""NCAA team scoring tests.

All five NCAA categories are team slots only, so these exercise results-based
scoring with no box scores anywhere. Expected values come from the R scripts.
"""

import pandas as pd
import pytest

from whul.scoring.ncaa import (
    FB_REG_CHAMP_POOL, _split_conference_title, score_basketball,
    score_diamond, score_football,
)


def game(home, away, hs, as_, *, hc="ACC", ac="ACC", season_type=2, notes="",
         season=2025, game_date="2025-11-15"):
    """One played game.

    The date is not decoration. A conference title is only awarded once the
    season's last game has been played, and a frame with no dates on it cannot
    say whether that has happened -- so these carry one, in the past, which is
    what a hand calculation of a finished season means.
    """
    return {
        "season": season, "season_type": season_type, "notes": notes,
        "home_team": home, "away_team": away,
        "home_conference": hc, "away_conference": ac,
        "home_score": hs, "away_score": as_, "completed": True,
        "game_date": game_date,
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
        game("A", "B", 70, 60, season_type=3, notes="ACC Tournament Championship",
             game_date="2025-03-15"),
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


# --- a title is a season outcome, not a running one -------------------------

def test_no_conference_title_until_the_season_has_been_played():
    """Miami was carrying six points for an ACC title on a 1-0 conference
    record in week two: whoever won the first conference game of the year took
    the whole pool."""
    rows = pad([game("MIA", "FSU", 30, 10, game_date="2025-09-06")], 10,
               game_date="2025-09-06")
    rows.append({
        "season": 2025, "season_type": 2, "notes": "",
        "home_team": "MIA", "away_team": "FSU",
        "home_conference": "ACC", "away_conference": "ACC",
        "home_score": None, "away_score": None, "completed": False,
        "game_date": "2125-11-29",
    })
    out = score_football(pd.DataFrame(rows)).set_index("team")
    assert out.loc["MIA", "conf_wins"] == 1
    assert out.loc["MIA", "pts_reg_champ"] == 0.0


def test_the_conference_title_lands_once_the_season_is_played_out():
    rows = pad([game("MIA", "FSU", 30, 10)], 10)
    out = score_football(pd.DataFrame(rows)).set_index("team")
    assert out.loc["MIA", "pts_reg_champ"] > 0


# --- a conference title is a record, not a win count ------------------------

def conf_summary(*teams, season=2025):
    """(team, conference wins, conference games) rows."""
    return pd.DataFrame([
        {"season": season, "conference": "ACC", "team": t,
         "conf_wins": w, "conf_games": g} for t, w, g in teams
    ])


def test_eight_and_oh_beats_eight_and_one():
    """A conference schedule is not balanced, so both had eight conference
    wins and both were being crowned and splitting the pool."""
    got = _split_conference_title(
        conf_summary(("A", 8, 8), ("B", 8, 9)), FB_REG_CHAMP_POOL, {2025})
    assert list(got) == [FB_REG_CHAMP_POOL, 0.0]


def test_a_short_schedule_cannot_tie_a_champion():
    """Most wins then fewest losses, not a win rate: a rate rewards a short
    schedule, and a team misfiled into a conference with one game in it would
    go 1-0 and tie a champion at 8-0 for the top of the table."""
    got = _split_conference_title(
        conf_summary(("A", 8, 8), ("Z", 1, 1)), FB_REG_CHAMP_POOL, {2025})
    assert list(got) == [FB_REG_CHAMP_POOL, 0.0]


def test_a_genuine_tie_is_still_shared():
    got = _split_conference_title(
        conf_summary(("A", 8, 9), ("B", 8, 9)), FB_REG_CHAMP_POOL, {2025})
    assert list(got) == [FB_REG_CHAMP_POOL / 2, FB_REG_CHAMP_POOL / 2]


def test_an_unsettled_season_wins_nothing():
    got = _split_conference_title(
        conf_summary(("A", 8, 8), ("B", 8, 9)), FB_REG_CHAMP_POOL, set())
    assert list(got) == [0.0, 0.0]


def test_a_college_team_carries_what_it_lost_as_well_as_what_it_won():
    """Unscored, and counted: a win total on its own cannot say whether the
    rest of the schedule was lost or has not been played."""
    sched = pd.DataFrame(pad([
        game("A", "B", 30, 0),
        game("B", "A", 24, 10),
    ], 6))
    out = score_football(sched).set_index("team")
    assert (out.loc["A", "wins"], out.loc["A", "losses"]) == (1, 1)
    assert (out.loc["B", "wins"], out.loc["B", "losses"]) == (1, 1)


def test_a_night_the_walk_skipped_cannot_take_a_win_off_a_team():
    """Ohio State showed one game played, no wins and a point differential of
    -1 on the sixteenth of September, having won on the sixth. The NCAA walk
    reads twenty weeks and skips any that raises, and ESPN's per-team schedule
    drops a team whose request failed, so a week that does not answer is a week
    nobody played. A game that has been played cannot be un-played, so a pull
    that comes back without one is never a correction."""
    from datetime import date

    from whul import ingest as ing
    from whul.benchmark_sources import GAME_KEYS
    from whul.scoring.ncaa import score_football
    from whul.store import open_store

    US, THEM = "Ohio State Buckeyes", "Texas Longhorns"

    def played(day, ident, ours):
        return dict(game(US, THEM, ours, 0, hc="Big Ten", ac="SEC",
                         season=2026, game_date=day), game_id=ident)

    whole = [played("2026-09-06", "401700001", 14),
             played("2026-09-13", "401700002", 21)]
    shown = {"whole": whole, "short": whole[1:]}

    class Feed:
        key, league, asset_type = "ncaaf-test", "NCAAF", "Team"
        accumulates = GAME_KEYS
        windowed, dated_by_source, cumulative = False, False, False
        produces, roster_scoped = (), True
        seasons_for = staticmethod(lambda day: [2026])
        which = "whole"

        @staticmethod
        def build():
            raise AssertionError("the live loader is the one under test")

        @staticmethod
        def live():
            return (lambda _seasons, _names: pd.DataFrame(shown[Feed.which]),
                    lambda raw: score_football(raw, {US, THEM}))

    def week(keep):
        Feed.accumulates = GAME_KEYS if keep else ()
        store = open_store(":memory:")
        out = []
        for day, which in (("2026-09-14", "whole"), ("2026-09-15", "short"),
                           ("2026-09-16", "whole")):
            Feed.which = which
            got = ing._pull(Feed(), date.fromisoformat(day), verbose=False,
                            store=store, names=[US, THEM])
            row = got[got["team"] == US]
            out.append(0 if row.empty else int(row["wins"].iloc[0]))
        return out

    assert week(keep=False) == [2, 1, 2], "the fault, for the record"
    assert week(keep=True) == [2, 2, 2]


def test_a_season_wide_feed_that_forgets_a_game_says_so():
    """The ledger restores it either way. For a feed asked about the whole
    season the restoring is not the end of it: a week that did not answer is a
    fault in the pull, not a window rolling over, and the run's report is where
    that has to show up."""
    from whul import ingest as ing
    from whul.benchmark_sources import GAME_KEYS

    class Feed:
        key, league, windowed = "ncaaf-test", "NCAAF", False
        accumulates = GAME_KEYS

    held = pd.DataFrame([
        dict(game(_A := "A", "B", 14, 0, season=2026, game_date="2026-09-06"),
             game_id="1"),
        dict(game(_A, "B", 21, 0, season=2026, game_date="2026-09-13"),
             game_id="2"),
    ])
    notes: list[str] = []
    ing._report_forgotten(Feed(), held.iloc[1:], held, False, notes)
    assert notes and "1" in notes[0]


# --- conference tournament byes ---------------------------------------------

def _acc_bracket(tmp_path, monkeypatch, rows="2027,NCAAM,ACC,5,"):
    from whul.scoring import ncaa

    table = tmp_path / "formats.csv"
    table.write_text("season,league,conference,rounds,round_names\n" + rows + "\n")
    monkeypatch.setattr(ncaa, "CONF_TOURNEY_FORMATS", table)


def _tourney(notes_by_team, season=2027):
    """Each team's conference tournament games, first to last, all won but the
    last, against filler opponents."""
    rows = []
    for team, notes in notes_by_team.items():
        for i, note in enumerate(notes):
            won = i < len(notes) - 1
            rows.append(game(team, f"{team} opp {i}", 80 if won else 60,
                             60 if won else 80, season_type=3, notes=note,
                             season=season, game_date="2027-03-12"))
    return pd.DataFrame(rows)


def test_a_seed_that_skipped_rounds_is_paid_them_as_wins(tmp_path, monkeypatch):
    """Duke enters a five-round bracket at the quarterfinal: two rounds skipped,
    each paid as a conference tournament win."""
    _acc_bracket(tmp_path, monkeypatch)
    out = score_basketball(_tourney({
        "Duke": ["ACC Tournament - Quarterfinal", "ACC Tournament - Semifinal"],
        "Clemson": ["ACC Tournament - Second Round", "ACC Tournament - Quarterfinal"],
        "Boston College": ["ACC Tournament - First Round"],
    })).set_index("team")
    assert out.loc["Duke", "conf_tourney_byes"] == 2
    assert out.loc["Duke", "conf_tourney_wins"] == 1, "a bye is not a win"
    assert out.loc["Clemson", "conf_tourney_byes"] == 1
    assert out.loc["Boston College", "conf_tourney_byes"] == 0
    counted = out.loc["Duke", "conf_tourney_wins"] * 2.0
    assert out.loc["Duke", "total_points"] - out.loc["Duke", "pts_reg_champ"] \
        - out.loc["Duke", "reg_wins"] * 2.0 - out.loc["Duke", "big_wins"] * 1.5 \
        - out.loc["Duke", "conf_wins"] * 1.0 - out.loc["Duke", "point_diff"] * 0.03 \
        == pytest.approx(counted + 2 * 2.0)


def test_a_round_counted_from_the_start_needs_no_stated_bracket(tmp_path, monkeypatch):
    _acc_bracket(tmp_path, monkeypatch, rows="")
    out = score_basketball(_tourney({
        "Clemson": ["ACC Tournament - Second Round"],
        "Duke": ["ACC Tournament - Quarterfinal"],
    })).set_index("team")
    assert out.loc["Clemson", "conf_tourney_byes"] == 1
    # A quarterfinal is the second round of one bracket and the third of
    # another, so without the bracket nothing is paid rather than a guess.
    assert out.loc["Duke", "conf_tourney_byes"] == 0


def test_a_stepladder_names_its_own_rounds(tmp_path, monkeypatch):
    _acc_bracket(tmp_path, monkeypatch, rows=(
        "2027,NCAAM,WCC|West Coast,6,First Round|Second Round|Third Round|"
        "Quarterfinal|Semifinal|Final"))
    out = score_basketball(_tourney({
        "Saint Mary's": ["WCC Tournament - Semifinal"],
    }, ).assign(home_conference="WCC", away_conference="WCC")).set_index("team")
    assert out.loc["Saint Mary's", "conf_tourney_byes"] == 4


def test_a_round_that_cannot_be_read_pays_no_byes(tmp_path, monkeypatch):
    """A later round read as the first would pay for rounds that were played."""
    _acc_bracket(tmp_path, monkeypatch)
    out = score_basketball(_tourney({
        "Duke": ["ACC Tournament", "ACC Tournament - Semifinal"],
    })).set_index("team")
    assert out.loc["Duke", "conf_tourney_byes"] == 0


def test_a_semifinal_win_is_not_the_conference_title(tmp_path, monkeypatch):
    _acc_bracket(tmp_path, monkeypatch)
    out = score_basketball(_tourney({
        "Duke": ["ACC Tournament - Semifinal", "ACC Tournament - Final"],
        "Clemson": ["ACC Tournament - Semifinal", "ACC Tournament - Championship",
                    "ACC Tournament - Championship"],
    })).set_index("team")
    assert out.loc["Duke", "conf_tourney_champ"] == 0
    assert out.loc["Clemson", "conf_tourney_champ"] == 1


def test_a_november_first_round_is_not_march_madness():
    out = score_basketball(pd.DataFrame([
        game("A", "B", 80, 60, hc="ACC", ac="SEC", notes="Maui Invitational - First Round",
             game_date="2026-11-24"),
        game("A", "C", 80, 60, hc="ACC", ac="B1G", season_type=3,
             notes="NIT - First Round", game_date="2027-03-19"),
    ])).set_index("team")
    assert out.loc["A", "mm_appearance"] == 0 and out.loc["A", "mm_wins"] == 0
    assert out.loc["A", "reg_wins"] == 1, "the invitational is a regular-season game"
    assert out.loc["A", "conf_tourney_wins"] == 0, "the NIT is not a conference's"


def test_the_playoff_final_is_not_a_conference_title():
    out = score_football(pd.DataFrame([
        game("A", "B", 30, 20, hc="B1G", ac="SEC", season_type=3,
             notes="College Football Playoff National Championship",
             game_date="2027-01-19"),
        game("A", "C", 30, 20, hc="B1G", ac="B1G", season_type=3,
             notes="Big Ten Championship", game_date="2026-12-05"),
    ])).set_index("team")
    assert out.loc["A", "conf_title_win"] == 1
    assert out.loc["A", "playoff_wins"] == 1


def test_a_playoff_top_seed_is_paid_the_first_round_it_skipped():
    """The twelve-team playoff: the top four seeds enter at the quarterfinals,
    and the round they skipped is paid as a playoff win (15)."""
    conference = {"Ohio State": "B1G", "Texas": "SEC", "Clemson": "ACC"}

    def cfp(home, away, note, day):
        return game(home, away, 30, 20, hc=conference[home], ac=conference[away],
                    season_type=3, notes=note, season=2026, game_date=day)
    out = score_football(pd.DataFrame([
        cfp("Ohio State", "Texas", "CFP Quarterfinal at the Rose Bowl Game", "2027-01-01"),
        cfp("Texas", "Clemson", "College Football Playoff First Round", "2026-12-19"),
    ])).set_index("team")
    assert out.loc["Ohio State", "playoff_byes"] == 1
    assert out.loc["Ohio State", "playoff_wins"] == 1, "a bye is not a win"
    assert out.loc["Texas", "playoff_byes"] == 0
    assert out.loc["Clemson", "playoff_byes"] == 0
    with_bye = out.loc["Ohio State"]
    counted = sum(with_bye[c] * w for c, w in __import__(
        "whul.scoring.ncaa", fromlist=["FB_WEIGHTS"]).FB_WEIGHTS.items())
    assert with_bye["total_points"] - with_bye["pts_reg_champ"] - counted \
        == pytest.approx(15.0)


def test_the_four_team_playoff_had_no_byes():
    out = score_football(pd.DataFrame([
        game("A", "B", 30, 20, hc="B1G", ac="SEC", season_type=3, season=2022,
             notes="College Football Playoff Semifinal at the Fiesta Bowl",
             game_date="2022-12-31"),
    ])).set_index("team")
    assert out.loc["A", "playoff_byes"] == 0


def _diamond(notes, season_type=3, **kw):
    return game("LSU", "Ole Miss", 6, 2, hc="SEC", ac="SEC", season=2027,
                season_type=season_type, notes=notes, game_date="2027-05-22", **kw)


def test_a_conference_tournament_game_is_a_win_like_the_rest_of_the_season():
    """The benchmark's feed calls every game regular season and counted these,
    so a live feed that calls them postseason must not drop them."""
    out = score_diamond(pd.DataFrame([_diamond("SEC Tournament - First Round")]),
                        "NCAA Baseball").set_index("team")
    assert out.loc["LSU", "reg_wins"] == 1 and out.loc["LSU", "run_diff"] == 4
    assert out.loc["Ole Miss", "reg_losses"] == 1


def test_a_conference_tournament_bye_in_baseball_is_paid_as_a_win():
    out = score_diamond(pd.DataFrame([_diamond("SEC Tournament - Second Round")]),
                        "NCAA Softball").set_index("team")
    assert out.loc["LSU", "conf_tourney_byes"] == 1
    assert out.loc["LSU", "total_points"] == pytest.approx(
        (1 + 1) * 2.0 + 4 * 0.05)


def test_the_ncaa_tournament_in_baseball_is_not_a_conference_tournament():
    out = score_diamond(pd.DataFrame([
        _diamond("NCAA Baseball Championship - Baton Rouge Regional - Game 1")]),
        "NCAA Baseball").set_index("team")
    assert out.loc["LSU", "reg_wins"] == 0 and out.loc["LSU", "regional_wins"] == 1
    assert out.loc["LSU", "conf_tourney_byes"] == 0


def test_a_first_four_win_is_a_march_madness_win_and_nobody_else_has_a_bye():
    out = score_basketball(pd.DataFrame([
        game("A", "B", 70, 60, hc="ACC", ac="MWC", season_type=3,
             notes="NCAA Men's Basketball Championship - First Four",
             game_date="2027-03-17"),
        game("C", "A", 80, 60, hc="SEC", ac="ACC", season_type=3,
             notes="NCAA Men's Basketball Championship - South Region - 1st Round",
             game_date="2027-03-19"),
    ])).set_index("team")
    assert out.loc["A", "mm_wins"] == 1 and out.loc["A", "mm_appearance"] == 1
    assert out.loc["C", "mm_wins"] == 1
    assert out.loc["C", "conf_tourney_byes"] == 0
    assert out.loc["C", "total_points"] == pytest.approx(8 + 5 + 20 * 0.0)


# --- what the 2026 probe showed -----------------------------------------------

def test_a_conference_that_calls_it_a_championship_has_a_tournament():
    """The MAAC's is "MAAC Championship", and ESPN calls it regular season."""
    out = score_basketball(pd.DataFrame([
        game("Fairfield", "Sacred Heart", 70, 60, hc="MAAC", ac="MAAC",
             notes="MAAC Championship - 1st Round", game_date="2027-03-05"),
        game("Fairfield", "Merrimack", 70, 60, hc="MAAC", ac="MAAC",
             notes="MAAC Championship - Final", game_date="2027-03-09"),
    ]), "NCAAW").set_index("team")
    assert out.loc["Fairfield", "conf_tourney_wins"] == 2
    assert out.loc["Fairfield", "conf_wins"] == 0, "not regular-season conference games"
    assert out.loc["Fairfield", "conf_tourney_champ"] == 1
    assert out.loc["Fairfield", "conf_tourney_byes"] == 0, \
        "a 1st Round is a first round, whatever the tournament is called"


def test_a_november_championship_between_conference_rivals_is_not_theirs():
    out = score_basketball(pd.DataFrame([
        game("Duke", "Clemson", 70, 60, notes="Players Era Championship - Championship",
             game_date="2026-11-27"),
    ])).set_index("team")
    assert out.loc["Duke", "conf_tourney_wins"] == 0
    assert out.loc["Duke", "conf_tourney_champ"] == 0


def test_a_play_in_is_not_a_round_anyone_skipped(tmp_path, monkeypatch):
    """The Pac-12 calls its top two seeds' bye a double one: the 8 v 9 play-in
    ahead of the first round does not count."""
    _acc_bracket(tmp_path, monkeypatch, rows="2027,NCAAM,Pac-12,4,")
    out = score_basketball(_tourney({
        "Boise State": ["Pac-12 Tournament - First Round"],
        "Texas State": ["Pac-12 Tournament - Play-In", "Pac-12 Tournament - First Round"],
        "Gonzaga": ["Pac-12 Tournament - Semifinal"],
    }).assign(home_conference="Pac-12", away_conference="Pac-12")).set_index("team")
    assert out.loc["Boise State", "conf_tourney_byes"] == 0
    assert out.loc["Texas State", "conf_tourney_byes"] == 0
    assert out.loc["Gonzaga", "conf_tourney_byes"] == 2


def test_a_conference_title_game_espn_calls_regular_season_still_pays():
    """"SEC Championship" is type 2 in ESPN's feed."""
    out = score_football(pd.DataFrame([
        game("Georgia", "Alabama", 28, 7, hc="SEC", ac="SEC", season_type=2,
             notes="SEC Championship", game_date="2025-12-06"),
    ])).set_index("team")
    assert out.loc["Georgia", "conf_title_win"] == 1


def test_the_game_that_wins_a_regional_is_a_regional_game():
    """"... Auburn Regional - Auburn advances to Super Regional" was being read
    as a Super Regional win, leaving the winner a win short of its Regional."""
    regional = [game("Auburn", opp, 6, 2, hc="", ac="", season_type=3, season=2026,
                     notes=f"NCAA Baseball Championship - Auburn Regional{tail}",
                     game_date="2026-05-30")
                for opp, tail in (("Milwaukee", ""), ("UCF", " - Elimination Game"),
                                  ("NC State", " - Auburn advances to Super Regional"))]
    out = score_diamond(pd.DataFrame(regional), "NCAA Baseball").set_index("team")
    assert out.loc["Auburn", "regional_wins"] == 3 and out.loc["Auburn", "super_wins"] == 0
    assert out.loc["Auburn", "series_regional"] == 1


def test_a_diamond_conference_tournament_is_found_without_a_conference():
    """ESPN calls it regular season, and baseball is scored with no conference
    on the rows -- the note and the month still say what it is."""
    out = score_diamond(pd.DataFrame([
        game("LSU", "Auburn", 6, 2, hc="", ac="", season=2027,
             notes="SEC Tournament - Second Round", game_date="2027-05-20"),
        game("Oklahoma", "Sam Houston", 6, 2, hc="", ac="", season=2027,
             notes="OU Tournament", game_date="2027-02-27"),
    ]), "NCAA Softball").set_index("team")
    assert out.loc["LSU", "conf_tourney_byes"] == 1
    assert out.loc["Oklahoma", "conf_tourney_byes"] == 0
    assert out.loc["Oklahoma", "reg_wins"] == 1


def test_a_new_years_six_bowl_that_was_not_a_playoff_game_is_not_one():
    """The four-team years: the Rose Bowl was a playoff semifinal only when
    ESPN says "College Football Playoff Semifinal at the Rose Bowl"."""
    out = score_football(pd.DataFrame([
        game("A", "B", 30, 20, hc="B1G", ac="Pac-12", season_type=3, season=2022,
             notes="Rose Bowl Game Presented by Prudential", game_date="2023-01-02"),
        game("C", "D", 30, 20, hc="SEC", ac="B1G", season_type=3, season=2022,
             notes="College Football Playoff Semifinal at the Chick-fil-A Peach Bowl",
             game_date="2022-12-31"),
    ])).set_index("team")
    assert out.loc["A", "playoff_app"] == 0 and out.loc["A", "playoff_wins"] == 0
    assert out.loc["C", "playoff_app"] == 1 and out.loc["C", "playoff_wins"] == 1


def test_the_womens_basketball_invitational_is_not_a_conference_tournament():
    out = score_basketball(pd.DataFrame([
        game("A", "B", 70, 60, hc="MAC", ac="MAC", season_type=3,
             notes="WBI Tournament - Championship", game_date="2024-03-30"),
    ]), "NCAAW").set_index("team")
    assert out.loc["A", "conf_tourney_wins"] == 0 and out.loc["A", "conf_tourney_champ"] == 0
    assert out.loc["A", "mm_appearance"] == 0
