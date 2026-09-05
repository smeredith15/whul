"""Reading UEFA entry off Wikipedia.

The fixture below mirrors what the live article returned on 2026-09-05: the
"Teams" section holds one table whose columns are

    ['Entry round', 'Entry round.1', 'Teams', 'Teams.1', 'Teams.2', 'Teams.3']

with clubs running four across. The first draft of this reader took only the
first ``Teams`` column and returned 23 of the Champions League's entrants,
which looked like a complete answer. These tests exist so it cannot happen
again.

`scripts/probe-uefa-wikipedia.py` is what checks the live shape; the article is
unreachable from the environment this was written in.
"""

from io import StringIO

import pandas as pd
import pytest

from whul.sources import wikipedia

#: The Teams table, shaped as the live article shapes it. Includes a title
#: holder marker, a footnote, a parenthetical, a row of fewer than four clubs,
#: and a qualifying entry round -- the one that separates 12 points from 6.
TEAMS_TABLE = """
<table class="wikitable">
<tr><th>Entry round</th><th>Entry round</th><th>Teams</th><th>Teams</th><th>Teams</th><th>Teams</th></tr>
<tr><td>League phase</td><td></td><td>Paris Saint-Germain TH</td><td>Manchester City</td><td>Inter Milan</td><td>Real Madrid</td></tr>
<tr><td>League phase</td><td></td><td>Liverpool[a]</td><td>Arsenal (holders)</td><td>Bayern Munich</td><td>Barcelona</td></tr>
<tr><td>Third qualifying round</td><td></td><td>Monaco</td><td>Malmö FF</td><td></td><td></td></tr>
<tr><td>Play-off round</td><td></td><td>Celtic</td><td></td><td></td><td></td></tr>
</table>
"""

#: The coefficient table from the "Association team allocation" section, whose
#: ``Teams`` column is a *count*. Reading it produced clubs called "3", "2",
#: "1" and "0", which is why only the section headed exactly "Teams" is read.
ALLOCATION_TABLE = """
<table class="wikitable">
<tr><th>Rank</th><th>Association</th><th>Coeff.</th><th>Teams</th><th>Notes</th></tr>
<tr><td>1</td><td>England</td><td>94.267</td><td>4</td><td></td></tr>
<tr><td>2</td><td>Italy</td><td>85.052</td><td>4</td><td></td></tr>
</table>
"""


def table(html=TEAMS_TABLE):
    return pd.read_html(StringIO(html))[0]


# --- names -----------------------------------------------------------------

def test_a_footnote_marker_is_not_part_of_the_name():
    """`Liverpool[a]` matches nothing on a roster, so the club scores no entry
    at all and nothing says why."""
    assert wikipedia.clean("Liverpool[a]") == "Liverpool"
    assert wikipedia.clean("Arsenal[1][note 2]") == "Arsenal"


def test_a_parenthetical_is_not_part_of_the_name():
    assert wikipedia.clean("Manchester City (holders)") == "Manchester City"


def test_a_title_holder_marker_is_stripped():
    """The live table returned exactly this."""
    assert wikipedia.clean("Paris Saint-Germain TH") == "Paris Saint-Germain"


@pytest.mark.parametrize("club", ["AZ", "PSV Eindhoven", "RFS", "FCSB", "NSÍ"])
def test_a_club_that_is_capitals_survives(club):
    """A trailing pair of capitals is not safely removable when AZ and RFS are
    clubs, so markers are stripped by exact match and nothing else."""
    assert wikipedia.clean(club) == club


# --- placeholders ----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Play-off round winner", "Qualifying round winner", "Loser of Q3", "TBD",
    "28 champions from associations 25–27 and 30–55",
    "16 domestic cup winners from associations 17–34",
])
def test_a_slot_is_not_a_club(text):
    """Articles carry these before qualifying is played, and the summary
    tables carry the last two. Matching one to a club would invent an entry."""
    assert wikipedia.is_placeholder(text)


@pytest.mark.parametrize("club", [
    "Liverpool", "Real Madrid", "Bayern Munich", "Internazionale",
    "Paris Saint-Germain", "Union Saint-Gilloise", "Bodø/Glimt",
])
def test_a_club_is_not_mistaken_for_a_slot(club):
    assert not wikipedia.is_placeholder(club)


# --- the Teams table -------------------------------------------------------

def test_clubs_run_four_across_and_all_of_them_are_read():
    """The bug this file exists for. Reading the first `Teams` column only
    gave 23 of the Champions League's entrants and looked like an answer."""
    found = wikipedia.entrants_from(table())
    assert len(found) == 11
    for club in ("Paris Saint-Germain", "Manchester City", "Inter Milan",
                 "Real Madrid", "Liverpool", "Arsenal", "Bayern Munich",
                 "Barcelona"):
        assert found[club] == "League phase", club


def test_the_entry_round_is_carried_because_it_is_the_scoring_distinction():
    """A place in the league phase and a place in a qualifying draw are not
    worth the same, and the source states which is which."""
    found = wikipedia.entrants_from(table())
    assert found["Monaco"] == "Third qualifying round"
    assert found["Malmö FF"] == "Third qualifying round"
    assert found["Celtic"] == "Play-off round"


def test_a_row_with_fewer_than_four_clubs_does_not_invent_any():
    found = wikipedia.entrants_from(table())
    assert "" not in found and "nan" not in found


def test_an_allocation_table_yields_no_clubs():
    """Its `Teams` column is a count. Reading it produced clubs called 3, 2,
    1 and 0 -- which is why only the section headed exactly `Teams` is read,
    and why this table must come out empty even if one reaches the parser."""
    found = wikipedia.entrants_from(table(ALLOCATION_TABLE))
    assert found == {}, found


def test_an_empty_table_yields_nothing():
    assert wikipedia.entrants_from(pd.DataFrame()) == {}


# --- finding the right section ---------------------------------------------

def test_only_the_section_headed_exactly_teams_is_taken():
    """The live article has seven sections whose heading could plausibly name
    the participants. Six of them are allocation tables or results."""
    found = [
        {"index": "1", "line": "Association team allocation"},
        {"index": "4", "line": "Teams"},
        {"index": "6", "line": "Qualifying rounds"},
        {"index": "11", "line": "League phase"},
    ]
    assert wikipedia.teams_section(found)["index"] == "4"


def test_no_teams_section_is_reported_rather_than_guessed_at():
    found = [{"index": "1", "line": "Association team allocation"}]
    assert wikipedia.teams_section(found) is None


# --- the structural checks -------------------------------------------------

def league_phase(prefix, n):
    return {f"{prefix} {i}": "League phase" for i in range(n)}


def test_a_sound_set_of_lists_passes():
    entrants = {
        "Champions League": {**league_phase("CL", 29),
                             "Monaco": "Third qualifying round"},
        "Europa League": league_phase("UEL", 30),
    }
    assert wikipedia.check(entrants) == []


def test_a_club_in_two_competitions_is_not_an_error():
    """A club knocked out of Champions League qualifying transfers into the
    Europa League and belongs in both articles. A disjointness check would
    fail every season on correct data."""
    entrants = {
        "Champions League": {"Celtic": "Play-off round", **league_phase("CL", 29)},
        "Europa League": {"Celtic": "League phase", **league_phase("UEL", 29)},
    }
    assert wikipedia.check(entrants) == []


def test_an_unrecognised_entry_round_means_the_table_changed():
    entrants = {"Champions League": {"Arsenal": "Group stage"}}
    problems = wikipedia.check(entrants)
    assert any("does not recognise" in p and "Group stage" in p for p in problems)


def test_a_club_with_no_entry_round_cannot_be_scored():
    entrants = {"Champions League": {"Arsenal": ""}}
    assert any("no entry round" in p for p in wikipedia.check(entrants))


def test_more_direct_entrants_than_the_league_phase_holds():
    entrants = {"Champions League": league_phase("CL", 40)}
    problems = wikipedia.check(entrants)
    assert any("40 clubs entering directly" in p for p in problems)


def test_reading_nothing_is_reported():
    assert any("no clubs read" in p for p in wikipedia.check({"Europa League": {}}))


# --- titles ----------------------------------------------------------------

def test_the_article_title_uses_an_en_dash():
    """A hyphen is a different article, and Wikipedia will not redirect."""
    assert wikipedia.title_for("Champions League", "2027-28") == \
        "2027–28 UEFA Champions League"


def test_a_single_digit_second_year_keeps_its_zero():
    assert wikipedia.title_for("Conference League", "2029-30").startswith("2029–30")


@pytest.mark.parametrize("club", ["1. FC Köln", "TSG 1899 Hoffenheim", "Pafos"])
def test_a_club_with_digits_in_its_name_survives(club):
    """The allocation column holds a bare count, but a club may legitimately
    carry numerals -- so the test is that there is nothing else in the cell."""
    assert not wikipedia._is_a_count(club)


@pytest.mark.parametrize("count", ["4", "0", "1"])
def test_a_bare_number_is_a_count(count):
    assert wikipedia._is_a_count(count)


@pytest.mark.parametrize("name,club", [
    ("Newcastle United EPS", "Newcastle United"),
    ("Villarreal EPS", "Villarreal"),
    ("Paris Saint-Germain TH", "Paris Saint-Germain"),
])
def test_the_extra_place_marker_is_stripped(name, club):
    """EPS is the European Performance Spot, the extra Champions League place
    the two best-performing associations earn. It appeared in the first live
    run on Newcastle and Villarreal, and left in the name it would have cost
    both clubs twelve points without anything reading as wrong."""
    assert wikipedia.clean(name) == club
