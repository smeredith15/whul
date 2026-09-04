"""Club soccer player stats from FBref.

Thirty-two rostered picks are club soccer players and no source served them.
The live site is unreachable from the environment this was written in, so these
exercise the parsing against a page shaped like FBref's.
"""

import pandas as pd
import pytest

from whul.sources import fbref


def page(rows, *, table_id="stats_standard", commented=True):
    """A page shaped like FBref's: two header rows, groups repeating names."""
    head = (
        '<tr><th></th><th></th><th></th><th></th>'
        '<th colspan="3">Playing Time</th>'
        '<th colspan="4">Performance</th>'
        '<th colspan="2">Per 90 Minutes</th></tr>'
        '<tr><th>Player</th><th>Pos</th><th>Squad</th><th>Comp</th>'
        '<th>MP</th><th>Starts</th><th>Min</th>'
        '<th>Gls</th><th>Ast</th><th>CrdY</th><th>CrdR</th>'
        '<th>Gls</th><th>Ast</th></tr>'
    )
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    table = (
        f'<table id="{table_id}"><thead>{head}</thead><tbody>{body}</tbody></table>'
    )
    return f"<html><body><!--{table}--></body></html>" if commented else \
        f"<html><body>{table}</body></html>"


SALAH = ["Mohamed Salah", "FW", "Liverpool", "eng Premier League",
         "38", "37", "3,301", "29", "18", "2", "0", "0.79", "0.49"]
KANE = ["Harry Kane", "FW", "Bayern Munich", "de Bundesliga",
        "32", "31", "2,712", "26", "8", "3", "0", "0.86", "0.27"]


def test_a_commented_table_is_still_read():
    """FBref serves most of its tables inside HTML comments and renders them
    with JavaScript. A parser that respects comments finds one table on a page
    showing six -- and reports it as a page with no player stats on it."""
    frame = fbref.parse_players(page([SALAH]))
    assert len(frame) == 1
    assert frame.loc[0, "Player"] == "Mohamed Salah"


def test_season_totals_are_taken_not_per_90_rates():
    """Gls appears twice: a season total under Performance and a rate under Per
    90 Minutes. Take the last and a 29-goal season becomes 0.79 -- a number
    between 0 and 2 that looks entirely reasonable and is wrong by a factor of
    thirty-seven."""
    frame = fbref.parse_players(page([SALAH]))
    assert frame.loc[0, "Gls"] == 29
    assert frame.loc[0, "Ast"] == 18


def test_minutes_survive_their_thousands_separator():
    frame = fbref.parse_players(page([SALAH]))
    assert frame.loc[0, "Min"] == 3301


def test_the_repeated_header_row_is_not_a_player():
    """FBref repeats its header every twenty-five rows so the table stays
    readable while scrolling. Parsed, each one is a player called "Player"."""
    header = ["Player", "Pos", "Squad", "Comp", "MP", "Starts", "Min",
              "Gls", "Ast", "CrdY", "CrdR", "Gls", "Ast"]
    frame = fbref.parse_players(page([SALAH, header, KANE]))
    assert list(frame["Player"]) == ["Mohamed Salah", "Harry Kane"]


def test_each_league_is_named_the_way_the_roster_names_it():
    """The benchmark groups are these names; a competition that does not map
    would be a whole league scored against nothing."""
    frame = fbref.parse_players(page([SALAH, KANE]))
    assert list(frame["league"]) == ["Premier League", "Bundesliga"]


def test_an_unrecognised_competition_is_raised_not_dropped():
    """Silently dropping it loses a league of players, and the pool it leaves
    behind looks like a smaller league rather than a broken one."""
    row = list(SALAH)
    row[3] = "pt Primeira Liga"
    with pytest.raises(fbref.FeedUnavailable, match="Primeira Liga"):
        fbref.parse_players(page([row]))


def test_a_challenge_page_is_named_as_such():
    """FBref serves a challenge page to clients it dislikes. It parses as no
    tables at all, which must not read as a season with no players in it."""
    with pytest.raises(fbref.FeedUnavailable, match="no table"):
        fbref.parse_players("<html><body><p>checking your browser</p></body></html>")


def test_a_missing_column_is_named_in_the_error():
    html = page([SALAH]).replace("<th>Gls</th>", "<th>Goals</th>")
    with pytest.raises(fbref.FeedUnavailable, match="missing"):
        fbref.parse_players(html)


def test_per_90_rates_reaching_the_totals_are_refused():
    """If the season total is dropped -- a renamed group, a reordered table --
    the per-90 column of the same name is taken in its place, and a 29-goal
    season becomes 0.79. That is a plausible-looking number no other check
    would question, so it is checked here: counts are whole."""
    html = page([SALAH]).replace("<th>Gls</th>", "<th>Goals</th>", 1)
    with pytest.raises(fbref.FeedUnavailable, match="per-90"):
        fbref.parse_players(html)


def test_european_seasons_are_named_for_both_years():
    assert fbref.season_label(2025) == "2024-2025"
    assert fbref.season_label(2022) == "2021-2022"


def test_a_league_missing_from_one_season_is_raised(monkeypatch):
    """A league absent from a season does not show up in a total: the pool is
    simply smaller, and its benchmark is drawn from four seasons while the
    version claims five."""
    everything = pd.DataFrame([
        {"season": s, "league": lg, "Player": "x"}
        for s in (2024, 2025)
        for lg in fbref.BIG_FIVE + ("MLS",)
    ])
    fbref._check_leagues(everything, [2024, 2025])   # no raise

    short = everything[~((everything["season"] == 2024)
                         & (everything["league"] == "Ligue 1"))]
    with pytest.raises(fbref.FeedUnavailable, match="Ligue 1 2024"):
        fbref._check_leagues(short, [2024, 2025])


def test_the_scorer_reads_this_frame_without_translation():
    """The whole reason to hand FBref's columns over unchanged: score_players
    already resolves Comp, MP, Starts, Min, Gls, Ast, CrdY and CrdR."""
    from whul.scoring.soccer import score_players

    frame = fbref.parse_players(page([SALAH, KANE])).assign(season=2025)
    scored = score_players(frame).set_index("player")
    # 37 starts x 2 + 1 substitute appearance + 29 goals x 4 (forward)
    # + 18 assists x 3 - 2 yellows = 75 + 116 + 54 - 2 = 243
    assert scored.loc["Mohamed Salah", "total_points"] == pytest.approx(243.0)
    assert scored.loc["Mohamed Salah", "league"] == "Premier League"


def test_the_source_is_registered_for_every_club_league_rostered():
    """Six leagues have rostered players and one pull serves all of them."""
    from whul.benchmark_sources import SOURCES

    source = SOURCES["soccer-players"]
    assert source.asset_type == "Player"
    assert set(source.produces) == {
        "Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1", "MLS"}
