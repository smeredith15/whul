"""Reading UEFA qualification off Wikipedia.

The article structure cannot be verified from the environment this was written
in -- en.wikipedia.org is unreachable through its proxy -- so nothing here
proves the pages look as expected. What it does prove is the parsing: given a
table shaped the way Wikipedia shapes one, the right clubs come out and the
placeholders do not. `scripts/probe-uefa-wikipedia.py` is what checks the shape,
from a machine with access.
"""

from io import StringIO

import pandas as pd
import pytest

from whul.sources import wikipedia

#: Shaped like the Champions League "Teams" table: grouped by association with
#: rowspans, footnote markers, a parenthetical, and -- for a season whose
#: qualifying has not been played -- rows naming a slot rather than a club.
TEAMS_TABLE = """
<table class="wikitable">
<tr><th>Association</th><th>Team</th><th>Qualification method</th><th>Coeff.</th></tr>
<tr><td rowspan="3">England</td><td>Liverpool[a]</td><td>1st in Premier League</td><td>112</td></tr>
<tr><td>Arsenal</td><td>2nd in Premier League</td><td>103</td></tr>
<tr><td>Manchester City (holders)</td><td>3rd in Premier League</td><td>131</td></tr>
<tr><td>Spain</td><td>Real Madrid</td><td>1st in La Liga</td><td>136</td></tr>
<tr><td>France</td><td>Play-off round winner</td><td>4th in Ligue 1</td><td>—</td></tr>
<tr><td>—</td><td>Qualifying round winner</td><td>TBD</td><td>—</td></tr>
</table>
"""


def table(html=TEAMS_TABLE):
    return pd.read_html(StringIO(html))[0]


# --- names -----------------------------------------------------------------

def test_a_footnote_marker_is_not_part_of_the_name():
    """`Liverpool[a]` matches nothing on a roster, so the club would score no
    qualification at all and nothing would say why."""
    assert wikipedia.clean("Liverpool[a]") == "Liverpool"
    assert wikipedia.clean("Arsenal[1][note 2]") == "Arsenal"


def test_a_parenthetical_is_not_part_of_the_name():
    assert wikipedia.clean("Manchester City (holders)") == "Manchester City"
    assert wikipedia.clean("Porto (via Europa League)") == "Porto"


def test_whitespace_is_collapsed():
    assert wikipedia.clean("  Real   Madrid \n") == "Real Madrid"


# --- placeholders ----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Play-off round winner", "Qualifying round winner", "Loser of Q3",
    "TBD", "To be determined", "Third qualifying round winner",
])
def test_a_slot_is_not_a_club(text):
    """An article carries these before qualifying is played. Matching one to a
    club would invent a qualification out of a placeholder."""
    assert wikipedia.is_placeholder(text)


@pytest.mark.parametrize("club", [
    "Liverpool", "Real Madrid", "Bayern Munich", "Internazionale",
    "Paris Saint-Germain", "Union Saint-Gilloise",
])
def test_a_club_is_not_mistaken_for_a_slot(club):
    assert not wikipedia.is_placeholder(club)


# --- pulling the clubs out -------------------------------------------------

def test_clubs_and_how_they_qualified_come_out_together():
    found = wikipedia.clubs_from(table())
    real = [(n, h) for n, h in found if not wikipedia.is_placeholder(n)]
    assert real == [
        ("Liverpool", "1st in Premier League"),
        ("Arsenal", "2nd in Premier League"),
        ("Manchester City", "3rd in Premier League"),
        ("Real Madrid", "1st in La Liga"),
    ]


def test_an_association_rowspan_does_not_become_a_club():
    """pandas fills a rowspan down, so England appears three times in the
    association column -- it must not be read as a team."""
    names = [n for n, _ in wikipedia.clubs_from(table())]
    assert "England" not in names and "Spain" not in names


def test_a_table_with_no_method_column_still_yields_clubs():
    html = """<table><tr><th>Team</th><th>Coeff.</th></tr>
              <tr><td>Ajax</td><td>60</td></tr></table>"""
    assert wikipedia.clubs_from(table(html)) == [("Ajax", "")]


def test_an_unnamed_first_column_is_taken_as_the_team():
    html = """<table><tr><th>0</th><th>Coeff.</th></tr>
              <tr><td>Ajax</td><td>60</td></tr></table>"""
    assert wikipedia.clubs_from(table(html))[0][0] == "Ajax"


def test_an_empty_table_yields_nothing():
    assert wikipedia.clubs_from(pd.DataFrame()) == []


# --- the structural checks -------------------------------------------------

def full(prefix, n=wikipedia.LEAGUE_PHASE_SIZE):
    return {f"{prefix} {i}": "1st" for i in range(n)}


def test_three_full_and_disjoint_lists_pass():
    entrants = {
        "Champions League": full("CL"),
        "Europa League": full("UEL"),
        "Conference League": full("UECL"),
    }
    assert wikipedia.check(entrants) == []


def test_a_short_list_is_the_wrong_table():
    """Thirty-four clubs is not a league phase, and scoring them as one would
    quietly leave two clubs unqualified."""
    entrants = {"Champions League": full("CL", 34)}
    assert any("34 club(s)" in p for p in wikipedia.check(entrants))


def test_a_club_in_two_competitions_means_a_merged_parse():
    entrants = {
        "Champions League": {"Arsenal": "1st", **full("CL", 35)},
        "Europa League": {"Arsenal": "5th", **full("UEL", 35)},
    }
    problems = wikipedia.check(entrants)
    assert any("Arsenal appears in" in p for p in problems)


# --- titles ----------------------------------------------------------------

def test_the_article_title_uses_an_en_dash():
    """A hyphen is a different article, and Wikipedia will not redirect to it."""
    title = wikipedia.title_for("Champions League", "2027-28")
    assert title == "2027–28 UEFA Champions League"
    assert wikipedia.title_for("Europa League", "2027–28").startswith("2027–")


def test_a_single_digit_second_year_keeps_its_zero():
    assert wikipedia.title_for("Conference League", "2029-30").startswith("2029–30")
    assert wikipedia.title_for("Conference League", "2030-31").startswith("2030–31")
