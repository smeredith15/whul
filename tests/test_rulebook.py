"""The Scoring page's rules must stay attached to the scorers they describe.

A rules page is only worth having if it is right, and the way it goes wrong is
never dramatic: somebody adds a stat to a scorer, nobody adds it to the page,
and the page keeps saying something almost true for a year. These tests make
that a failing build instead.
"""

from whul.config.league import ALL_SLOTS, active_slots
from whul.scoring import mlb, nba, ncaa, nfl
from whul.site import rulebook


def test_every_weight_a_scorer_uses_is_named_on_the_page():
    """A stat the scorer pays for and the page does not mention is invisible."""
    tables = {
        "NFL players": (nfl.PLAYER_WEIGHTS, rulebook.NFL_PLAYER_LABELS),
        "NFL teams": (nfl.TEAM_WEIGHTS, rulebook.NFL_TEAM_LABELS),
        "NBA players": (nba.BOX_WEIGHTS, rulebook.NBA_PLAYER_LABELS),
        "NBA teams": (nba.TEAM_WEIGHTS, rulebook.NBA_TEAM_LABELS),
        "MLB batters": (mlb.BATTER_WEIGHTS, rulebook.MLB_BATTER_LABELS),
        "MLB pitchers": (mlb.PITCHER_WEIGHTS, rulebook.MLB_PITCHER_LABELS),
        "NCAAF teams": (ncaa.FB_WEIGHTS, rulebook.FB_LABELS),
        "NCAAB teams": (ncaa.BB_WEIGHTS, rulebook.BB_LABELS),
    }
    for what, (weights, labels) in tables.items():
        missing = set(weights) - set(labels)
        assert not missing, f"{what}: {sorted(missing)} scored but not explained"
        extra = set(labels) - set(weights)
        assert not extra, f"{what}: {sorted(extra)} explained but not scored"


#: Which section speaks for which roster category. Written out rather than
#: derived, because the mapping is not one to one -- the men's and women's
#: college basketball slots share a section, as do baseball and softball, and
#: NASCAR and Formula 1 share both a slot and a section.
COVERAGE = {
    ("Team", "Club Soccer Top 3"): "club-soccer-teams",
    ("Team", "Club Soccer Other"): "club-soccer-teams",
    ("Team", "NFL"): "nfl-teams",
    ("Team", "NBA"): "nba-teams",
    ("Team", "MLB"): "mlb-teams",
    ("Team", "NHL"): "nhl-teams",
    ("Team", "NCAAF"): "ncaaf-teams",
    ("Team", "NCAAM"): "ncaab-teams",
    ("Team", "NCAAW"): "ncaab-teams",
    ("Team", "NCAA Baseball"): "ncaa-diamond-teams",
    ("Team", "NCAA Softball"): "ncaa-diamond-teams",
    ("Team", "Intl Soccer"): "intl-soccer-teams",
    ("Player", "Club Soccer Top 3"): "club-soccer-players",
    ("Player", "Club Soccer Other"): "club-soccer-players",
    ("Player", "NFL"): "nfl-players",
    ("Player", "NBA"): "nba-players",
    ("Player", "MLB"): "mlb-players",
    ("Player", "NHL"): "nhl-players",
    ("Player", "PGA"): "pga-players",
    ("Player", "Tennis"): "tennis-players",
    ("Player", "Motorsports"): "motorsports-players",
}


def test_every_roster_slot_has_somewhere_to_read_its_rules():
    slugs = {rules.slug for rules in rulebook.sections()}
    for group in active_slots(ALL_SLOTS):
        key = (group.asset_type, group.category)
        assert key in COVERAGE, f"{key} holds slots and is not on the Scoring page"
        assert COVERAGE[key] in slugs, f"{key} points at a section that is gone"


def test_no_section_is_empty_and_no_slug_is_reused():
    sections = rulebook.sections()
    slugs = [rules.slug for rules in sections]
    assert len(slugs) == len(set(slugs)), "two sections would share an anchor"
    for rules in sections:
        assert rules.title and rules.intro, rules.slug
        assert rules.lines, f"{rules.slug} explains nothing"


def test_every_line_carries_a_number_or_is_a_heading():
    """A rules line with no figure in it is a sentence, and belongs in the notes."""
    for rules in rulebook.sections():
        for line in rules.lines:
            if isinstance(line, rulebook.Heading):
                continue
            assert any(c.isdigit() for c in line), f"{rules.slug}: {line}"


def test_negatives_read_as_minus_signs_not_hyphens():
    """A hyphen at that size reads as a dash, and a dash reads as a range."""
    assert rulebook.num(-2) == "−2"
    assert rulebook.num(0.5) == "0.5"
    assert rulebook.num(3.0) == "3"


def test_ordinals():
    assert [rulebook.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 30, 36)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "30th", "36th"
    ]
