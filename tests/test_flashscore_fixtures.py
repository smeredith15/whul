"""Upcoming team-sport matches from the Flashscore feed.

Written against the wire format the tennis reader in ``whul.sources.flashscore``
already parses in production, because the feed itself is unreachable from where
this was written. These tests pin the parse; the probe pins the feed.
"""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from whul import fixtures
from whul.resolve import normalize_team
from whul.sources import flashscore_fixtures as feed
from whul.store import open_store


def stamp(when: date) -> int:
    return int(datetime(when.year, when.month, when.day, 18,
                        tzinfo=timezone.utc).timestamp())


def header(name: str) -> str:
    return f"ZA÷{name}¬ZEE÷x¬"


def match(uid, home, away, when, status="1") -> str:
    return (f"AA÷{uid}¬AD÷{stamp(when)}¬AC÷{status}¬"
            f"AE÷{home}¬AF÷{away}¬")


def payload(*records: str) -> str:
    return "~".join(records)


SOON = date(2026, 9, 20)


# --- the parse --------------------------------------------------------------

def test_an_upcoming_match_is_read_with_its_teams_and_date():
    raw = payload(header("ENGLAND: Premier League"),
                  match("a1", "Arsenal", "Chelsea", SOON))
    got = list(feed.iter_fixtures(raw))
    assert len(got) == 1
    assert got[0]["home_team"] == "Arsenal"
    assert got[0]["away_team"] == "Chelsea"
    assert got[0]["game_date"] == "2026-09-20"
    assert got[0]["competition"] == "Premier League"


def test_a_finished_match_is_not_a_fixture():
    """The exact inverse of the tennis parser, which keeps these and drops
    the rest."""
    raw = payload(header("ENGLAND: Premier League"),
                  match("a1", "Arsenal", "Chelsea", SOON, status="3"))
    assert list(feed.iter_fixtures(raw)) == []


def test_the_competition_comes_from_the_header_above_the_match():
    """Position is what assigns a match to a competition; a parser that
    ignored order would file every game under whichever header it saw last."""
    raw = payload(
        header("ENGLAND: Premier League"),
        match("a1", "Arsenal", "Chelsea", SOON),
        header("EUROPE: Champions League - League phase"),
        match("a2", "Barcelona", "Inter", SOON),
    )
    got = {m["home_team"]: m["competition"] for m in feed.iter_fixtures(raw)}
    assert got == {"Arsenal": "Premier League", "Barcelona": "Champions League"}


def test_a_match_before_any_header_still_reads():
    """A payload that opens mid-list loses its competition, not its fixture."""
    raw = payload(match("a1", "Arsenal", "Chelsea", SOON))
    got = list(feed.iter_fixtures(raw))
    assert len(got) == 1 and got[0]["competition"] == ""


def test_a_match_with_no_kickoff_is_dropped():
    raw = payload(header("ENGLAND: Premier League"),
                  "AA÷a1¬AC÷1¬AE÷Arsenal¬AF÷Chelsea¬")
    assert list(feed.iter_fixtures(raw)) == []


def test_a_match_missing_a_side_is_dropped():
    raw = payload(header("ENGLAND: Premier League"),
                  f"AA÷a1¬AD÷{stamp(SOON)}¬AC÷1¬AE÷Arsenal¬")
    assert list(feed.iter_fixtures(raw)) == []


def test_nothing_at_all_is_not_an_error():
    assert list(feed.iter_fixtures("")) == []


@pytest.mark.parametrize("raw,expected", [
    ("ENGLAND: Premier League - Round 5", "Premier League"),
    ("USA: NBA", "NBA"),
    ("EUROPE: Champions League - League phase", "Champions League"),
    ("SPAIN: LaLiga", "LaLiga"),
    ("no colon here", "no colon here"),
])
def test_the_country_and_stage_are_stripped_from_a_header(raw, expected):
    assert feed.competition_of(raw) == expected


def test_the_rows_are_shaped_like_a_schedule_so_harvest_reads_them():
    """The point of the shape: one harvester serves nflverse and this alike."""
    raw = payload(header("ENGLAND: Premier League"),
                  match("a1", "Arsenal", "Chelsea", SOON))
    frame = pd.DataFrame(list(feed.iter_fixtures(raw)))
    got = fixtures.harvest("Test", "2026-27", frame, date(2026, 9, 8), "now")
    assert set(got["team_key"]) == {"arsenal", "chelsea"}
    assert set(got["opponent"]) == {"Arsenal", "Chelsea"}


# --- reading the feed's spelling -------------------------------------------

#: ``{normalized: (roster spelling, the league it plays in)}``.
ROSTER = {
    normalize_team(name): (name, league) for name, league in (
        ("Manchester City", "Premier League"),
        ("Manchester United", "Premier League"),
        ("Arsenal", "Premier League"),
        ("Tottenham Hotspur", "Premier League"),
        ("Wolverhampton Wanderers", "Premier League"),
        ("Internazionale", "Serie A"),
        ("Bayern Munich", "Bundesliga"),
        ("Borussia Dortmund", "Bundesliga"),
        ("Paris Saint-Germain", "Ligue 1"),
        ("Real Madrid", "La Liga"),
        ("Real Betis", "La Liga"),
        ("Athletic Club", "La Liga"),
    )
}


@pytest.mark.parametrize("feed_name,expected", [
    ("Arsenal", "Arsenal"),                       # exact
    ("Man City", "Manchester City"),              # word abbreviation
    ("Man Utd", "Manchester United"),             # contraction
    ("Inter", "Internazionale"),                  # prefix
    ("Bayern", "Bayern Munich"),                  # leading word
    ("Dortmund", "Borussia Dortmund"),            # trailing word
    ("PSG", "Paris Saint-Germain"),               # initialism
    ("Spurs", "Tottenham Hotspur"),               # nickname
    ("Wolves", "Wolverhampton Wanderers"),
])
def test_the_feeds_spelling_reaches_the_rosters(feed_name, expected):
    assert fixtures.match_team(feed_name, ROSTER) == expected


@pytest.mark.parametrize("ambiguous", ["Man", "Manchester", "Real", "City Man"])
def test_a_name_that_could_mean_two_clubs_matches_neither(ambiguous):
    """A wrong fixture beside the right badge is worse than a blank cell.

    "City Man" is here for word order: without it, a rule loose enough to
    read "Man City" would read any two of a club's words in any arrangement.
    """
    assert fixtures.match_team(ambiguous, ROSTER) is None


def test_a_club_nobody_rosters_matches_nothing():
    assert fixtures.match_team("Liverpool", ROSTER) is None


def test_a_two_letter_fragment_is_too_short_to_stand_for_a_club():
    assert fixtures.match_team("Ma City", ROSTER) is None


# --- the roster side --------------------------------------------------------

def rostered(store, asset_id, asset_type, name, affiliation="", index=1,
             league="Premier League"):
    store.upsert("managers", [{"manager_id": "SS", "display_name": "Scott"}],
                 ["manager_id"])
    store.upsert("assets", [{
        "asset_id": asset_id, "asset_type": asset_type, "league": league,
        "display_name": name, "affiliation": affiliation,
        "created_at": "2026-08-21",
    }], ["asset_id"])
    slot = f"slot-{asset_id}"
    store.upsert("roster_slots", [{
        "slot_id": slot, "season": "2026-27", "manager_id": "SS",
        "category": "Test", "asset_type": asset_type, "slot_index": index,
    }], ["slot_id"])
    store.upsert("slot_occupancy", [{
        "slot_id": slot, "asset_id": asset_id,
        "start_date": "2026-08-21", "end_date": None,
    }], ["slot_id", "start_date"])


def test_a_players_club_is_looked_for_even_when_nobody_rosters_it():
    """The ordinary case: four Bayern players and no Bayern. Matching only
    team assets would leave all four blank while looking like it worked."""
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal", index=1)
    rostered(store, "player-kane", "Player", "Harry Kane",
             affiliation="Bayern Munich", index=2, league="Bundesliga")
    wanted = fixtures.wanted_teams(store, "2026-27")
    assert {name for name, _ in wanted.values()} == {"Arsenal", "Bayern Munich"}


# --- the world in one payload ----------------------------------------------

def test_a_club_name_shared_across_countries_goes_to_the_right_one():
    """Found by the probe on a real payload: Brazil's Serie B has an Athletic
    Club and so does Bilbao. Without the country one gets the other's
    fixtures, and the page looks entirely right while being wrong."""
    assert fixtures.match_team("Athletic Club", ROSTER, "SPAIN") == "Athletic Club"
    assert fixtures.match_team("Athletic Club", ROSTER, "BRAZIL") is None


def test_a_club_is_found_in_its_own_country_and_in_europe():
    """A club's next game is often a European night, not a league match."""
    assert fixtures.match_team("Arsenal", ROSTER, "ENGLAND") == "Arsenal"
    assert fixtures.match_team("Arsenal", ROSTER, "EUROPE") == "Arsenal"
    assert fixtures.match_team("Arsenal", ROSTER, "ARGENTINA") is None


def test_no_country_given_searches_everywhere():
    """Right for a feed that is not global; the caller passes one when it is."""
    assert fixtures.match_team("Arsenal", ROSTER) == "Arsenal"


def test_a_reserve_side_is_not_its_first_team():
    raw = payload(header("ARGENTINA: Reserve League - Clausura"),
                  match("a1", "River Plate 2", "Aldosivi 2", SOON))
    assert list(feed.iter_fixtures(raw)) == []


@pytest.mark.parametrize("name", [
    "Schalke 04", "Hannover 96", "Mainz 05", "Bologna 1909", "Arsenal",
])
def test_a_club_named_after_its_founding_year_is_not_a_reserve_side(name):
    """A bare trailing-number rule would drop three Bundesliga clubs."""
    assert not feed.RESERVE_PATTERN.search(name)


@pytest.mark.parametrize("name", ["River Plate 2", "Barcelona B", "Bayern II",
                                  "Chelsea U21", "Ajax Youth"])
def test_a_second_string_is_recognised(name):
    assert feed.RESERVE_PATTERN.search(name)


def test_the_country_is_carried_on_every_parsed_row():
    raw = payload(header("ENGLAND: Premier League"),
                  match("a1", "Arsenal", "Chelsea", SOON))
    assert list(feed.iter_fixtures(raw))[0]["country"] == "ENGLAND"


def test_an_athlete_with_no_club_adds_nothing_to_look_for():
    store = open_store(":memory:")
    rostered(store, "player-golfer", "Player", "A Golfer", index=1)
    assert fixtures.wanted_teams(store, "2026-27") == {}


def test_only_the_rostered_side_of_a_tie_becomes_a_row(monkeypatch):
    """Both sides are translated so the opponent reads properly, but a club
    nobody holds must not acquire a fixture row of its own."""
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal")

    raw = payload(header("ENGLAND: Premier League"),
                  match("a1", "Arsenal", "Chelsea", SOON))
    monkeypatch.setattr(
        feed, "load_upcoming",
        lambda sport, days=None, verbose=True: pd.DataFrame(list(feed.iter_fixtures(raw))),
    )
    fixtures.from_flashscore(store, "2026-27", date(2026, 9, 8),
                            leagues=["Premier League"], verbose=False)
    rows = store.query("SELECT team_key, opponent FROM fixtures")
    assert list(rows["team_key"]) == ["arsenal"]
    assert list(rows["opponent"]) == ["Chelsea"]


def test_a_sport_that_fails_does_not_lose_the_others(monkeypatch):
    store = open_store(":memory:")
    rostered(store, "team-arsenal", "Team", "Arsenal")

    def explode(sport, days=None, verbose=True):
        raise RuntimeError("feed down")

    monkeypatch.setattr(feed, "load_upcoming", explode)
    # Must return rather than raise: a fixture is a convenience on a page.
    assert fixtures.from_flashscore(
        store, "2026-27", date(2026, 9, 8), verbose=False) == {}


# --- the leagues this feed must not reach ----------------------------------

def test_a_league_the_feed_does_not_serve_is_not_even_searched_for():
    """The bug this fixes, at its root.

    Unscoped, the matcher was offered all 176 clubs on the roster -- the NFL,
    the NHL, college football, golfers -- and Flashscore's bare city names
    ("Buffalo", "New England", "Denver") then reached NFL clubs through the
    abbreviation rule. The country guard did not stop it: those leagues have
    no country list, and an empty list was read as "anywhere".
    """
    store = open_store(":memory:")
    rostered(store, "team-bills", "Team", "Buffalo Bills", index=1, league="NFL")
    rostered(store, "team-arsenal", "Team", "Arsenal", index=2,
             league="Premier League")

    everything = fixtures.wanted_teams(store, "2026-27")
    assert {n for n, _ in everything.values()} == {"Buffalo Bills", "Arsenal"}

    soccer_only = fixtures.wanted_teams(
        store, "2026-27", leagues={"Premier League"})
    assert {n for n, _ in soccer_only.values()} == {"Arsenal"}


def test_a_bare_city_name_no_longer_reaches_an_nfl_club():
    store = open_store(":memory:")
    rostered(store, "team-bills", "Team", "Buffalo Bills", index=1, league="NFL")
    rostered(store, "team-arsenal", "Team", "Arsenal", index=2,
             league="Premier League")
    wanted = fixtures.wanted_teams(store, "2026-27",
                                   leagues=set(feed.SPORTS))
    assert fixtures.match_team("Buffalo", wanted, "USA") is None


def test_a_fixture_from_the_wrong_feed_is_never_shown():
    """The second guard, and the one that matters most.

    Both sides of a Flashscore tie are translated into the roster's spelling,
    so a soccer match that reached an NFL club read as a plausible NFL game --
    "New England Patriots vs Buffalo Bills", on a Wednesday in September.
    Nothing about the row looked wrong, which is why matching alone is not
    trusted: the fixture has to have come from a feed that covers the league.
    """
    store = open_store(":memory:")
    rostered(store, "team-bills", "Team", "Buffalo Bills", index=1, league="NFL")
    fixtures.replace(store, "2026-27", "Flashscore/1", pd.DataFrame([{
        "season": "2026-27", "league": "Flashscore/1", "team_key": "buffalo bills",
        "fixture_date": "2026-09-16", "opponent": "New England Patriots",
        "home": 0, "competition": "MLS", "fetched_at": "now",
    }]))
    assert fixtures.by_asset(store, "2026-27", date(2026, 9, 8)) == {}


def test_the_leagues_own_feed_is_shown_even_when_a_stray_row_is_sooner():
    """A wrong row must not merely lose the tie-break -- it must not count."""
    store = open_store(":memory:")
    rostered(store, "team-bills", "Team", "Buffalo Bills", index=1, league="NFL")
    fixtures.replace(store, "2026-27", "Flashscore/1", pd.DataFrame([{
        "season": "2026-27", "league": "Flashscore/1", "team_key": "buffalo bills",
        "fixture_date": "2026-09-16", "opponent": "Somebody", "home": 0,
        "competition": "MLS", "fetched_at": "now",
    }]))
    fixtures.replace(store, "2026-27", "NFL", pd.DataFrame([{
        "season": "2026-27", "league": "NFL", "team_key": "buffalo bills",
        "fixture_date": "2026-09-17", "opponent": "Detroit Lions", "home": 1,
        "competition": "REG", "fetched_at": "now",
    }]))
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert got["team-bills"]["opponent"] == "Detroit Lions"
    assert got["team-bills"]["league"] == "NFL"


def test_which_feeds_may_speak_for_which_league():
    assert fixtures.feeds_for("MLB") == {"Flashscore/6"}
    assert fixtures.feeds_for("Premier League") == {"Flashscore/1"}
    # A league not listed takes its fixtures from its own scoring pull,
    # recorded under its own name.
    assert fixtures.feeds_for("NFL") == {"NFL"}
    assert fixtures.feeds_for("NCAAF") == {"NCAAF"}


# --- tennis: a named opponent in a named round -----------------------------

def tennis_match(uid, home_slug, away_slug, when, status="1"):
    """A tennis record: players are WU/WV slugs, surname first."""
    return (f"AA÷{uid}¬AD÷{stamp(when)}¬AC÷{status}¬"
            f"WU÷{home_slug}¬WV÷{away_slug}¬")


def test_an_upcoming_tennis_match_carries_its_opponent_and_round():
    raw = payload(
        header("ATP - SINGLES: Rome - Quarterfinal"),
        tennis_match("t1", "sinner-jannik", "alcaraz-carlos", SOON),
    )
    got = list(feed.iter_tennis_fixtures(raw))
    assert len(got) == 1
    assert got[0]["home_team"] == "Jannik Sinner"
    assert got[0]["away_team"] == "Carlos Alcaraz"
    assert got[0]["competition"] == "Rome"
    assert got[0]["round"] == "QF"


def test_a_finished_tennis_match_is_not_a_fixture():
    raw = payload(header("ATP - SINGLES: Rome - Quarterfinal"),
                  tennis_match("t1", "sinner-jannik", "alcaraz-carlos", SOON, "3"))
    assert list(feed.iter_tennis_fixtures(raw)) == []


def test_qualifying_and_doubles_are_not_the_main_draw():
    """Reusing the production header reader means the definition of
    "main-tour singles" lives in one place rather than two."""
    for name in ("ATP - SINGLES: Rome - Qualification",
                 "ATP - DOUBLES: Rome - Quarterfinal",
                 "ATP - SINGLES: Some Challenger"):
        raw = payload(header(name),
                      tennis_match("t1", "sinner-jannik", "alcaraz-carlos", SOON))
        assert list(feed.iter_tennis_fixtures(raw)) == [], name


def test_a_tennis_player_is_matched_on_their_own_name():
    """They have no club to join through -- their next fixture is their own
    match, so the roster name is the key."""
    store = open_store(":memory:")
    rostered(store, "player-sinner", "Player", "Jannik Sinner", league="ATP")
    wanted = fixtures.wanted_teams(store, "2026-27", leagues={"ATP"})
    assert [n for n, _ in wanted.values()] == ["Jannik Sinner"]


def test_a_tennis_fixture_reaches_the_player_and_shows_the_round(monkeypatch):
    store = open_store(":memory:")
    rostered(store, "player-sinner", "Player", "Jannik Sinner", league="ATP")
    raw = payload(
        header("ATP - SINGLES: Rome - Semifinal"),
        tennis_match("t1", "sinner-jannik", "alcaraz-carlos", SOON),
    )
    monkeypatch.setattr(
        feed, "load_upcoming",
        lambda sport, days=None, verbose=True:
            pd.DataFrame(list(feed.iter_tennis_fixtures(raw))),
    )
    fixtures.from_flashscore(store, "2026-27", date(2026, 9, 8),
                             leagues=["ATP"], verbose=False)
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert got["player-sinner"]["opponent"] == "Carlos Alcaraz"
    assert got["player-sinner"]["badge"] == "SF"
    assert got["player-sinner"]["competition"] == "Rome"


def test_the_candidate_sports_are_distinct_ids():
    """`discover` walks these; a duplicate id would silently ask twice and
    report the second answer as the first sport's."""
    ids = [sport for sport, _ in feed.CANDIDATE_SPORTS]
    assert len(ids) == len(set(ids))


# --- the men's game and the women's, in one payload -------------------------
#
# The only place in this project where two rostered assets share a display
# name: England, France and Spain are held in both international categories.
# Nothing in the name separates them, so the feed's marker does -- and getting
# it wrong is not a blank cell but a real opponent on a real date, on the
# wrong card.

@pytest.mark.parametrize("name,womens,bare", [
    ("England W", True, "England"),
    ("England (W)", True, "England"),
    ("England", False, "England"),
    ("Barcelona W", True, "Barcelona"),
    ("Bayern Munich", False, "Bayern Munich"),
    # A name that is only the marker would strip to nothing, and "the empty
    # club" would then be offered to every lookup in the women's bucket.
    ("W", False, ""),
])
def test_the_womens_marker_is_read_off_the_team_name(name, womens, bare):
    assert feed.is_womens(name) is womens
    assert feed.strip_womens(name) == bare


def intl_store():
    store = open_store(":memory:")
    rostered(store, "team-england-men", "Team", "England", index=1,
             league="Men's Intl Soccer")
    rostered(store, "team-england-women", "Team", "England", index=2,
             league="Women's Intl Soccer")
    return store


def pull_soccer(store, monkeypatch, raw, leagues, sport=None):
    """Serve ``raw`` for one sport id and nothing for the others.

    Sport-aware on purpose. Answering every id with the same payload would
    hand a soccer match to the hockey reader, which is the exact confusion the
    per-sport narrowing exists to prevent -- a fake feed that cannot tell the
    sports apart cannot test that it does.
    """
    only = feed.SPORTS[leagues[0]] if sport is None else sport
    monkeypatch.setattr(
        feed, "load_upcoming",
        lambda s, days=None, verbose=True:
            pd.DataFrame(list(feed.iter_fixtures(raw))) if s == only
            else pd.DataFrame(),
    )
    # The season pages are a separate fetch and a separate test; stubbed here
    # so a unit test never reaches the network.
    monkeypatch.setattr(
        feed, "load_season",
        lambda league, session=None, verbose=True: pd.DataFrame())
    return fixtures.from_flashscore(store, "2026-27", date(2026, 9, 8),
                                    leagues=leagues, verbose=False)


def test_a_womens_international_never_lands_on_the_mens_card(monkeypatch):
    store = intl_store()
    raw = payload(header("EUROPE: Euro Qualification Women"),
                  match("w1", "England W", "Norway W", SOON))
    pull_soccer(store, monkeypatch, raw,
                ["Men's Intl Soccer", "Women's Intl Soccer"])
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert "team-england-men" not in got
    assert got["team-england-women"]["opponent"] == "Norway"


def test_a_mens_international_never_lands_on_the_womens_card(monkeypatch):
    store = intl_store()
    raw = payload(header("WORLD: World Cup Qualification UEFA"),
                  match("m1", "England", "Serbia", SOON))
    pull_soccer(store, monkeypatch, raw,
                ["Men's Intl Soccer", "Women's Intl Soccer"])
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert "team-england-women" not in got
    assert got["team-england-men"]["opponent"] == "Serbia"


def test_a_womens_club_match_does_not_reach_the_mens_club(monkeypatch):
    """The marker is stripped before the name is matched, so the women's
    bucket fills with names the men's roster also uses. `FEEDS` is what keeps
    the two apart, and this is the case that proves it."""
    store = open_store(":memory:")
    rostered(store, "team-barcelona", "Team", "Barcelona", index=1,
             league="La Liga")
    raw = payload(header("SPAIN: Liga F"),
                  match("b1", "Barcelona W", "Real Madrid W", SOON))
    pull_soccer(store, monkeypatch, raw, ["La Liga"])
    assert fixtures.by_asset(store, "2026-27", date(2026, 9, 8)) == {}


def test_a_side_against_a_womens_side_is_not_a_fixture_either_way(monkeypatch):
    """Not a match in any competition this reads. Dropped rather than
    assigned, because either bucket would be a guess."""
    store = intl_store()
    raw = payload(header("WORLD: Friendly"),
                  match("x1", "England", "Norway W", SOON))
    pull_soccer(store, monkeypatch, raw,
                ["Men's Intl Soccer", "Women's Intl Soccer"])
    assert fixtures.by_asset(store, "2026-27", date(2026, 9, 8)) == {}


def test_an_international_is_refused_under_its_own_countrys_heading():
    """A national side plays under a confederation or WORLD. Its own country's
    heading is where its clubs are, and "England" there is a club."""
    wanted = {"england": ("England", "Men's Intl Soccer")}
    assert fixtures.match_team("England", wanted, "WORLD") == "England"
    assert fixtures.match_team("England", wanted, "EUROPE") == "England"
    assert fixtures.match_team("England", wanted, "ENGLAND") is None


# --- hockey -----------------------------------------------------------------

def test_an_nhl_fixture_reaches_a_club_and_its_players(monkeypatch):
    """The NHL reports season totals and no schedule, so unlike the NFL there
    is nothing to harvest on the way past. This feed is the only place its
    fixtures can come from."""
    store = open_store(":memory:")
    rostered(store, "team-oilers", "Team", "Edmonton Oilers", index=1,
             league="NHL")
    rostered(store, "player-mcdavid", "Player", "Connor McDavid",
             affiliation="Edmonton Oilers", index=2, league="NHL")
    raw = payload(header("USA: NHL"),
                  match("h1", "Edmonton Oilers", "Calgary Flames", SOON))
    pull_soccer(store, monkeypatch, raw, ["NHL"])
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert got["team-oilers"]["opponent"] == "Calgary Flames"
    assert got["player-mcdavid"]["opponent"] == "Calgary Flames"
    assert got["team-oilers"]["league"] == "Flashscore/4"


def test_an_nhl_club_is_never_offered_to_the_soccer_payload(monkeypatch):
    """The country guard cannot settle this one: an NHL club and an MLS club
    can both legitimately be playing in the USA, and "New York" abbreviates
    into either. The payload is narrowed to the leagues its sport serves
    before a single name is read."""
    store = open_store(":memory:")
    rostered(store, "team-rangers", "Team", "New York Rangers", index=1,
             league="NHL")
    raw = payload(header("USA: MLS"),
                  match("s1", "New York", "Chicago Fire", SOON))
    pull_soccer(store, monkeypatch, raw, ["MLS", "NHL"],
                sport=feed.SPORTS["MLS"])
    assert fixtures.by_asset(store, "2026-27", date(2026, 9, 8)) == {}


def test_the_hockey_sport_id_is_not_one_of_the_others():
    from whul.sources import flashscore

    ids = {flashscore.SPORT_SOCCER, flashscore.SPORT_TENNIS,
           flashscore.SPORT_BASKETBALL, flashscore.SPORT_BASEBALL}
    assert flashscore.SPORT_HOCKEY not in ids
    assert feed.SPORTS["NHL"] == flashscore.SPORT_HOCKEY


# --- a league's own season page --------------------------------------------
#
# The day feed is a week wide, which is the right window for a league in
# season and no window at all for one that is not. The NHL and the NBA open in
# October: through September their next game is real, published and six weeks
# away, and every cell was blank while the feed worked perfectly.

def page_record(uid, home, away, when, status="1"):
    """A match as a league page carries it: no ZA header above it, because the
    page is one competition."""
    code = f"AC÷{status}¬" if status is not None else ""
    return f"AA÷{uid}¬AD÷{stamp(when)}¬{code}AE÷{home}¬AF÷{away}¬"


def test_a_season_page_is_read_without_a_competition_header():
    raw = payload(page_record("h1", "Edmonton Oilers", "Calgary Flames",
                              date(2026, 10, 8)))
    got = list(feed.iter_page_fixtures(raw, "NHL", "USA"))
    assert len(got) == 1
    assert got[0]["competition"] == "NHL"
    assert got[0]["country"] == "USA"
    assert got[0]["game_date"] == "2026-10-08"


def test_a_page_record_with_no_status_is_still_a_fixture():
    """A fixtures page carries fixtures. Dropping every record because the
    status field moved would be a blank column reported as a working one."""
    raw = payload(page_record("h1", "Edmonton Oilers", "Calgary Flames",
                              date(2026, 10, 8), status=None))
    assert len(list(feed.iter_page_fixtures(raw, "NHL", "USA"))) == 1


def test_a_page_record_that_says_it_was_played_is_still_refused():
    raw = payload(page_record("h1", "Edmonton Oilers", "Calgary Flames",
                              date(2026, 10, 8), status="3"))
    assert list(feed.iter_page_fixtures(raw, "NHL", "USA")) == []


def test_the_season_page_fills_a_league_the_week_cannot_reach(monkeypatch):
    """The case this exists for: nothing inside the day feed's window, and a
    published schedule six weeks out."""
    store = open_store(":memory:")
    rostered(store, "team-oilers", "Team", "Edmonton Oilers", index=1,
             league="NHL")
    raw = payload(page_record("h1", "Edmonton Oilers", "Calgary Flames",
                              date(2026, 10, 8)))
    monkeypatch.setattr(feed, "load_upcoming",
                        lambda s, days=None, verbose=True: pd.DataFrame())
    monkeypatch.setattr(
        feed, "load_season",
        lambda league, session=None, verbose=True:
            pd.DataFrame(list(feed.iter_page_fixtures(raw, "NHL", "USA")))
            if league == "NHL" else pd.DataFrame(),
    )
    fixtures.from_flashscore(store, "2026-27", date(2026, 9, 8),
                             leagues=["NHL"], verbose=False)
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert got["team-oilers"]["opponent"] == "Calgary Flames"
    assert got["team-oilers"]["date"] == "2026-10-08"


def test_the_week_and_the_season_page_do_not_double_a_match(monkeypatch):
    """Both carry the same match with the same id when the windows overlap."""
    store = open_store(":memory:")
    rostered(store, "team-oilers", "Team", "Edmonton Oilers", index=1,
             league="NHL")
    both = payload(match("h1", "Edmonton Oilers", "Calgary Flames", SOON))
    monkeypatch.setattr(
        feed, "load_upcoming",
        lambda s, days=None, verbose=True:
            pd.DataFrame(list(feed.iter_fixtures(
                payload(header("USA: NHL"),
                        match("h1", "Edmonton Oilers", "Calgary Flames", SOON)))))
            if s == feed.SPORTS["NHL"] else pd.DataFrame(),
    )
    monkeypatch.setattr(
        feed, "load_season",
        lambda league, session=None, verbose=True:
            pd.DataFrame(list(feed.iter_page_fixtures(both, "NHL", "USA"))),
    )
    fixtures.from_flashscore(store, "2026-27", date(2026, 9, 8),
                             leagues=["NHL"], verbose=False)
    held = store.query("SELECT * FROM fixtures WHERE team_key = 'edmonton oilers'")
    assert len(held) == 1


def test_a_season_page_that_will_not_load_loses_nothing_else(monkeypatch):
    store = open_store(":memory:")
    rostered(store, "team-oilers", "Team", "Edmonton Oilers", index=1,
             league="NHL")

    def boom(league, session=None, verbose=True):
        raise RuntimeError("403")

    monkeypatch.setattr(
        feed, "load_upcoming",
        lambda s, days=None, verbose=True:
            pd.DataFrame(list(feed.iter_fixtures(
                payload(header("USA: NHL"),
                        match("h1", "Edmonton Oilers", "Calgary Flames", SOON)))))
            if s == feed.SPORTS["NHL"] else pd.DataFrame(),
    )
    monkeypatch.setattr(feed, "load_season", boom)
    fixtures.from_flashscore(store, "2026-27", date(2026, 9, 8),
                             leagues=["NHL"], verbose=False)
    got = fixtures.by_asset(store, "2026-27", date(2026, 9, 8))
    assert got["team-oilers"]["opponent"] == "Calgary Flames"


def test_each_season_page_is_filed_under_the_sport_that_serves_it():
    """The page is fetched inside the sport loop, so a league whose entry names
    a different sport than `SPORTS` does would be fetched under neither."""
    for league, (sport, path, _, _) in feed.SEASON_PAGES.items():
        assert feed.SPORTS[league] == sport, league
        assert path.endswith("/fixtures/"), league
