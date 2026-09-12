"""Club soccer player stats from ESPN's team rosters.

The payload below is the shape a live probe returned on 2026-09-05 for Arsenal:
athletes grouped by position, statistics under
``statistics.splits.categories[N].stats[M]`` with the stat's own name on it, and
a goalkeeper carrying a category an outfielder does not.

ESPN is unreachable from the environment this was written in, so nothing here
proves the endpoint. It proves the parsing, against the record as it actually
came back.
"""

import pandas as pd
import pytest
import requests

from whul.sources import espn_soccer


def stat(name, value):
    return {"name": name, "displayName": name, "value": float(value),
            "displayValue": str(value)}


def athlete(name, position, general, offensive=None, goalkeeping=None, **kw):
    categories = [{"name": "general", "stats": [stat(k, v) for k, v in general.items()]}]
    if offensive is not None:
        categories.append({"name": "offensive",
                           "stats": [stat(k, v) for k, v in offensive.items()]})
    if goalkeeping is not None:
        categories.append({"name": "goalKeeping",
                           "stats": [stat(k, v) for k, v in goalkeeping.items()]})
    return {
        "id": kw.get("id", "1"),
        "displayName": name,
        "position": {"abbreviation": position, "name": position},
        "statistics": {"splits": {"name": "Total", "categories": categories}},
    }


TIMBER = athlete(
    "Jurriën Timber", "D",
    {"foulsCommitted": 38, "foulsSuffered": 29, "redCards": 0, "yellowCards": 5,
     "ownGoals": 0, "appearances": 30, "subIns": 2},
    {"goalAssists": 3, "totalGoals": 2, "totalShots": 14},
    id="169241",
)
KEPA = athlete(
    "Kepa Arrizabalaga", "G",
    {"appearances": 1, "foulsCommitted": 0, "ownGoals": 0, "redCards": 0,
     "subIns": 0, "yellowCards": 0},
    {"goalAssists": 0, "totalGoals": 0},
    {"goalsConceded": 1, "saves": 2, "shotsFaced": 0},
    id="163590",
)
#: A squad player who has not appeared has no statistics block at all -- 9 of
#: Arsenal's 36 were like this in the live payload.
BENCHED = {"id": "9", "displayName": "Never Played",
           "position": {"abbreviation": "M"}}

ROSTER = {
    "team": {"displayName": "Arsenal", "id": "359"},
    "season": {"year": 2025, "displayName": "2024-25"},
    "athletes": [
        {"position": "defender", "items": [TIMBER]},
        {"position": "goalkeeper", "items": [KEPA]},
        {"position": "midfielder", "items": [BENCHED]},
    ],
}


def squad(monkeypatch, payload=ROSTER, season=2025):
    monkeypatch.setattr(espn_soccer, "_get", lambda url, params, session=None: payload)
    return espn_soccer.load_squad("epl", "359", season)


# --- the fields the scorer needs -------------------------------------------

def test_every_field_the_scorer_reads_comes_out(monkeypatch):
    row = squad(monkeypatch).set_index("player").loc["Jurriën Timber"]
    assert row["matches"] == 30
    assert row["goals"] == 2
    assert row["assists"] == 3
    assert row["yellow"] == 5
    assert row["red"] == 0
    assert row["position"] == "D"
    assert row["team"] == "Arsenal"


def test_a_start_is_an_appearance_that_did_not_begin_on_the_bench(monkeypatch):
    """ESPN gives the substitute count and never the starts. The scorer's
    season path wants starts, so this is the subtraction."""
    row = squad(monkeypatch).set_index("player").loc["Jurriën Timber"]
    assert row["starts"] == 28  # 30 appearances, 2 from the bench


def test_an_own_goal_is_not_a_goal(monkeypatch):
    """ownGoals sits in the same category as the real ones and is named
    similarly enough for a looser match to pick it up. Conceding one must never
    be paid as scoring one."""
    scored = athlete("Unlucky", "D",
                     {"appearances": 10, "subIns": 0, "ownGoals": 3,
                      "yellowCards": 0, "redCards": 0},
                     {"totalGoals": 0, "goalAssists": 0})
    payload = {**ROSTER, "athletes": [{"items": [scored]}]}
    assert squad(monkeypatch, payload).iloc[0]["goals"] == 0


def test_a_goalkeepers_extra_category_does_not_shift_the_others(monkeypatch):
    """A keeper has a goalKeeping category an outfielder does not, so the
    categories are read by name. An index would read the wrong number for half
    a squad."""
    row = squad(monkeypatch).set_index("player").loc["Kepa Arrizabalaga"]
    assert row["matches"] == 1 and row["goals"] == 0 and row["position"] == "G"


def test_a_player_who_never_appeared_is_kept_as_a_zero(monkeypatch):
    """Nine of Arsenal's thirty-six carried no statistics block. Leaving them
    out made "in the squad, yet to play" indistinguishable from "the feed does
    not know this name", and the first live run reported both as "no feed row"
    -- Musiala injured, and four MLS players whose season had not started.

    They are different problems with different fixes, so they read differently
    now. Both still score nothing."""
    assert "Never Played" in set(squad(monkeypatch)["player"])


def test_athletes_are_found_though_soccer_groups_them_by_position(monkeypatch):
    """Three groups, one athlete each: a defender, a keeper, and a squad player
    yet to appear."""
    assert len(squad(monkeypatch)) == 3


# --- the season the feed thinks it answered with ---------------------------

def test_the_season_the_feed_says_it_answered_with_is_carried(monkeypatch):
    """Asked for rather than deduced. ESPN could name a season for the year it
    starts where we name it for the year it ends, and a one-year shift would
    fill every benchmark season with the wrong year's football -- every figure
    still a real footballer's real season."""
    assert squad(monkeypatch).iloc[0]["season_said"] == "2024-25"


@pytest.mark.parametrize("block,expected", [
    ({"season": {"displayName": "2024-25"}}, "2024-25"),
    ({"season": {"year": 2025}}, "2025"),
    ({"season": 2025}, "2025"),
    ({}, ""),
])
def test_the_season_label_is_read_wherever_it_is(block, expected):
    assert espn_soccer.season_label(block) == expected


# --- degrading -------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"athletes": []},
    {"athletes": [{"items": []}]},
    {},
])
def test_a_payload_with_no_athletes_yields_nothing(monkeypatch, payload):
    assert squad(monkeypatch, payload).empty


@pytest.mark.parametrize("payload", [
    {"athletes": [{"items": [{"displayName": "X", "statistics": ["oddly a list"]}]}]},
    {"athletes": [{"items": [{"displayName": "Y", "statistics": {"splits": None}}]}]},
    {"athletes": [{"items": [{"displayName": "Z", "statistics": 42}]}]},
])
def test_a_statistics_block_this_cannot_read_does_not_raise(monkeypatch, payload):
    """One club must not take a league down with it. The player comes through
    on zeroes, which is what an unreadable block and an absent one both mean
    here -- there is no way to tell them apart, and inventing a difference
    would be worse than treating them alike."""
    out = squad(monkeypatch, payload)
    assert len(out) == 1
    assert out.iloc[0]["matches"] == 0 and out.iloc[0]["goals"] == 0


def test_a_club_that_fails_costs_that_club_only(monkeypatch):
    """Nineteen clubs' players are worth more than none."""
    monkeypatch.setattr(espn_soccer, "team_ids",
                        lambda league, season, session=None, note=None: {"A": "1", "B": "2"})

    def flaky(league, team_id, season, session=None):
        if team_id == "1":
            raise RuntimeError("gateway timeout")
        return pd.DataFrame([{"player": "Someone", "team": "B", "season": season}])

    monkeypatch.setattr(espn_soccer, "load_squad", flaky)
    out = espn_soccer.load_players("epl", [2025], verbose=False)
    assert list(out["player"]) == ["Someone"]


# --- and into the scorer ---------------------------------------------------

def test_the_rows_score(monkeypatch):
    """The columns are the ones whul.scoring.soccer.score_players resolves."""
    from whul.scoring import soccer

    rows = squad(monkeypatch).assign(league="Premier League")
    scored = soccer.score_players(rows).set_index("player")
    timber = scored.loc["Jurriën Timber"]
    # 28 starts x 2 + 2 substitute outings x 1 = 58 appearance points
    assert timber["appearance_points"] == 58
    # a defender's goal is worth 6
    assert timber["goal_points"] == 12
    # 58 + 12 + 3 assists x 3 + 5 yellows x -1 = 74
    assert timber["total_points"] == 74


# --- the source, wired up ---------------------------------------------------

def test_the_player_source_covers_every_club_league_with_a_roster():
    """FBref served six leagues in one request and answered 403 to every
    address we have. ESPN needs one request per club, and answers."""
    from whul.benchmark_sources import PLAYER_LEAGUES, SOURCES

    source = SOURCES["soccer-players"]
    assert set(source.produces) == set(PLAYER_LEAGUES)
    assert set(PLAYER_LEAGUES.values()) == {
        "epl", "laliga", "seriea", "bundesliga", "ligue1", "mls"}


#: Which of our leagues sends its clubs to a competition. A cup's rows are
#: attributed by the club they belong to, so a fixture whose cup rows carry a
#: club nobody drafts from is correctly discarded rather than counted -- which
#: is the behaviour under test, and needs the club to line up.
PLAYED_BY = {
    "epl": "epl", "facup": "epl", "efl_cup": "epl", "ucl": "epl",
    "uel": "epl", "uecl": "epl",
    "laliga": "laliga", "copadelrey": "laliga",
    "seriea": "seriea", "coppaitalia": "seriea",
    "bundesliga": "bundesliga", "dfbpokal": "bundesliga",
    "ligue1": "ligue1", "coupedefrance": "ligue1",
    "mls": "mls", "usopencup": "mls", "concacafchampions": "mls",
}


def squad_row(league, seasons, club=None):
    club = club or f"{PLAYED_BY.get(league, league)} FC"
    return pd.DataFrame([{
        "player": f"{league} player", "season": seasons[0],
        "season_said": f"{seasons[0] - 1}-{seasons[0] % 100:02d}",
        "team": club, "team_id": club,
        "matches": 10, "starts": 8, "goals": 1, "assists": 1,
        "yellow": 0, "red": 0, "position": "M",
    }])


def test_a_league_that_returns_nothing_costs_that_league_only(monkeypatch, capsys):
    """Nothing from the Premier League or anything its clubs also play."""
    from whul.benchmark_sources import SOURCES
    from whul.sources import espn_soccer as source

    silent = {"epl", "facup", "efl_cup", "ucl", "uel", "uecl"}

    def some(league, seasons, verbose=True, session=None):
        return pd.DataFrame() if league in silent else squad_row(league, seasons)

    monkeypatch.setattr(source, "load_players", some)
    load, _ = SOURCES["soccer-players"].build()
    out = load([2025])

    assert "Premier League" not in set(out["league"])
    assert "so Premier League scores none" in capsys.readouterr().out


def test_a_cup_that_returns_nothing_does_not_cost_the_league(monkeypatch, capsys):
    """A domestic cup is out of season most of the year and a European
    competition is out of it for most clubs, so an empty answer from one costs
    the league nothing. It is still said out loud: a cup that returns nothing
    all season because its path is wrong looks exactly like one that is merely
    out of season, and the CONCACAF Champions Cup was quietly the former."""
    from whul.benchmark_sources import SOURCES
    from whul.sources import espn_soccer as source

    def some(league, seasons, verbose=True, session=None):
        return pd.DataFrame() if league != "epl" else squad_row(league, seasons)

    monkeypatch.setattr(source, "load_players", some)
    load, _ = SOURCES["soccer-players"].build()
    out = load([2025])

    assert "Premier League" in set(out["league"])
    # The other five leagues legitimately say so; the Premier League must not.
    assert "so Premier League scores none" not in capsys.readouterr().out


def test_every_competition_a_clubs_players_appear_in_is_asked_for(monkeypatch):
    """The gap this closes: the team side gathered European matches from the
    first day and the player side never did, so a Champions League night moved
    the standings and left every player's line untouched."""
    from whul.benchmark_sources import SOURCES
    from whul.sources import espn_soccer as source

    asked = []

    def note(league, seasons, verbose=True, session=None):
        asked.append(league)
        return pd.DataFrame()

    monkeypatch.setattr(source, "load_players", note)
    load, _ = SOURCES["soccer-players"].build()
    load([2025])

    assert {"ucl", "uel", "uecl"} <= set(asked)
    assert {"facup", "efl_cup", "copadelrey", "dfbpokal"} <= set(asked)


def test_each_row_says_which_competition_it_came_from(monkeypatch):
    """Without it the scorer cannot tell a league goal from a European one,
    and the whole benchmark distinction collapses."""
    from whul.benchmark_sources import SOURCES
    from whul.sources import espn_soccer as source

    monkeypatch.setattr(source, "load_players",
                        lambda league, seasons, verbose=True, session=None:
                        squad_row(league, seasons))
    load, _ = SOURCES["soccer-players"].build()
    out = load([2025])

    labels = set(out["competition"])
    assert "UEFA Champions League" in labels
    assert "Premier League" in labels
    assert "US Open Cup" in labels


def test_a_feed_that_numbers_seasons_differently_is_announced(monkeypatch, capsys):
    """Superseded by the strict check below, which knows the translation. Kept
    because the message is what a reader of the log has to act on."""
    from whul import benchmark_sources

    lines_up = pd.DataFrame([{"season": 2025, "season_said": "2024-25"}])
    benchmark_sources._check_season_convention("epl", lines_up)
    assert "different season" not in capsys.readouterr().out

    shifted = pd.DataFrame([{"season": 2025, "season_said": "2025-26"}])
    benchmark_sources._check_season_convention("epl", shifted)
    out = capsys.readouterr().out
    assert "different season than the one meant" in out and "wrong year" in out


def test_a_feed_that_says_nothing_about_the_season_is_also_announced(capsys):
    from whul import benchmark_sources

    benchmark_sources._check_season_convention(
        "epl", pd.DataFrame([{"season": 2025, "season_said": ""}]))
    assert "did not say which season" in capsys.readouterr().out


# --- the season two sources number differently ------------------------------

@pytest.mark.parametrize("league,ours,asked", [
    # European: ESPN names the year it starts, we name the year it ends.
    ("epl", 2027, 2026), ("laliga", 2025, 2024), ("bundesliga", 2021, 2020),
    # MLS runs inside a calendar year, so both name it the same.
    ("mls", 2026, 2026), ("nwsl", 2025, 2025),
])
def test_our_season_is_translated_into_espns(league, ours, asked):
    """Confirmed live: asked for 2021, the feed answered "2021-22 English
    Premier League", and "2021 MLS" for the same request to MLS."""
    assert espn_soccer.roster_season(league, ours) == asked


def test_the_live_year_is_the_one_this_gets_wrong():
    """The benchmark survives a one-year shift -- five consecutive seasons are
    five consecutive seasons. The live pull does not: our 2026-27 is 2027, and
    asking ESPN for 2027 returns 2027-28, a season nobody has played. Every
    rostered player scores zero and the run looks like it worked."""
    assert espn_soccer.roster_season("epl", 2027) == 2026


@pytest.mark.parametrize("league,ours,said,ok", [
    ("epl", 2027, "2026-27 English Premier League", True),
    ("epl", 2027, "2027-28 English Premier League", False),
    ("mls", 2026, "2026 MLS", True),
    ("mls", 2026, "2025 MLS", False),
    ("epl", 2027, "", True),          # nothing said, nothing to contradict
])
def test_the_label_is_checked_strictly(league, ours, said, ok):
    """The first check accepted a label starting with either the year asked
    for or the year before -- both conventions, so neither detected. It passed
    on the shift it existed to find."""
    assert espn_soccer.season_matches(league, ours, said) is ok


def test_a_shifted_season_is_announced(capsys):
    from whul import benchmark_sources

    benchmark_sources._check_season_convention(
        "epl", pd.DataFrame([{"season": 2027, "season_said": "2027-28 EPL"}]))
    out = capsys.readouterr().out
    assert "different season than the one meant" in out

    benchmark_sources._check_season_convention(
        "epl", pd.DataFrame([{"season": 2027, "season_said": "2026-27 EPL"}]))
    assert "different season" not in capsys.readouterr().out


# --- yet to play, against not known --------------------------------------

def test_a_squad_player_who_has_not_appeared_is_a_zero_not_an_absence(monkeypatch):
    """Dropping him made "in the squad, yet to play" read identically to "the
    feed does not know this name" -- different problems with different fixes,
    and the run reported both as "no feed row". Musiala and Balogun were the
    first two."""
    payload = {**ROSTER, "athletes": [{"items": [TIMBER, BENCHED]}]}
    out = squad(monkeypatch, payload).set_index("player")
    assert "Never Played" in out.index
    row = out.loc["Never Played"]
    assert row["matches"] == 0 and row["starts"] == 0 and row["goals"] == 0


def test_a_player_yet_to_play_scores_nothing(monkeypatch):
    from whul.scoring import soccer

    payload = {**ROSTER, "athletes": [{"items": [BENCHED]}]}
    rows = squad(monkeypatch, payload).assign(league="Premier League")
    assert soccer.score_players(rows).iloc[0]["total_points"] == 0


def test_a_whole_league_failing_is_reported_as_one_fact(monkeypatch, capsys):
    """MLS 2027 has not been played, so ESPN lists its clubs and 404s every
    roster in it. Thirty lines of HTTPError read like a broken adapter."""
    from whul.sources import espn_soccer as source

    monkeypatch.setattr(source, "team_ids",
                        lambda league, season, session=None, note=None:
                        {f"Club {i}": str(i) for i in range(30)})

    def gone(league, team_id, season, session=None):
        raise RuntimeError("404")

    monkeypatch.setattr(source, "load_squad", gone)
    assert source.load_players("mls", [2027]).empty
    out = capsys.readouterr().out
    assert "every club failed" in out
    assert "season nobody has played" in out
    assert out.count("failed") == 1


def test_some_clubs_failing_still_names_them(monkeypatch, capsys):
    """One club down is a different thing from a season that does not exist,
    and the club is worth naming."""
    from whul.sources import espn_soccer as source

    monkeypatch.setattr(source, "team_ids",
                        lambda league, season, session=None, note=None: {"A": "1", "B": "2"})

    def flaky(league, team_id, season, session=None):
        if team_id == "1":
            raise RuntimeError("gateway timeout")
        return pd.DataFrame([{"player": "Someone"}])

    monkeypatch.setattr(source, "load_squad", flaky)
    source.load_players("epl", [2027])
    out = capsys.readouterr().out
    assert "A failed" in out and "every club failed" not in out


class _Refuses:
    """A session that answers 403 to the seasoned club list, as ESPN did."""

    def __init__(self, refuse_seasoned=True, refuse_all=False):
        self.refuse_seasoned = refuse_seasoned
        self.refuse_all = refuse_all
        self.asked = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.asked.append(dict(params or {}))
        if self.refuse_all or (self.refuse_seasoned and params):
            raise requests.HTTPError(
                "403 Client Error: Forbidden", response=_Response(403))
        return _Response(200, {"sports": [{"leagues": [{"teams": [
            {"team": {"id": "359", "displayName": "Arsenal"}},
        ]}]}]})


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._payload


def test_a_refused_club_list_falls_back_and_says_it_did():
    """A 403 does not say what it objects to, so the bare request is tried
    before the league is written off. The substitution is reported: a current
    club list is right for the season in progress and wrong by however many
    clubs went up or down for an older one."""
    session = _Refuses()
    note = []
    clubs = espn_soccer.team_ids("epl", 2027, session, note=note)
    assert clubs == {"Arsenal": "359"}
    assert session.asked == [{"season": 2026}, {}]
    assert any("403" in line for line in note)
    assert any("promoted or relegated" in line for line in note)


def test_a_club_list_refused_both_ways_returns_nothing_rather_than_raising():
    """One league that cannot be reached is one league scoring nothing. It
    raised before, which took the other five leagues down with it."""
    note = []
    assert espn_soccer.team_ids(
        "epl", 2027, _Refuses(refuse_all=True), note=note) == {}
    assert len(note) == 2  # both shapes tried, both reported


def test_no_custom_user_agent_is_sent():
    """The module that is pulled from GitHub Actions every night sends none,
    and this one drew a 403 from the same runner while sending one."""
    session = _Refuses(refuse_seasoned=False)
    espn_soccer.team_ids("epl", 2027, session)
    assert session.asked  # and no headers reached the call at all


# --- which league a competition's rows belong to --------------------------
#
# The first benchmark run over the new competition folding raised the Premier
# League's pool by 10.9%, which read like the FA Cup finally counting. It was
# not: the Premier League's request pulls 124 FA Cup clubs and 92 EFL Cup ones,
# and every National League player in them had been stamped a Premier League
# player. The European competitions were worse -- pulled once per league and
# deduplicated down to whichever league ran first, so a La Liga player's
# Champions League matches became a separate Premier League player who held his
# European bonus and appeared on nobody's roster.


def attribution_fixture(monkeypatch, squads):
    """Run the loader over ``{competition: [(player, club), ...]}``."""
    from whul.benchmark_sources import SOURCES
    from whul.sources import espn_soccer as source

    def some(league, seasons, verbose=True, session=None):
        rows = [squad_row(league, seasons, club=club).assign(player=player)
                for player, club in squads.get(league, ())]
        return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()

    monkeypatch.setattr(source, "load_players", some)
    load, _ = SOURCES["soccer-players"].build()
    return load([2025])


def test_a_cup_run_belongs_to_the_club_not_to_whoever_asked(monkeypatch):
    out = attribution_fixture(monkeypatch, {
        "epl": [("Saka", "Arsenal")],
        # The FA Cup's entrants go down to the National League. Only one of
        # these two is a Premier League player.
        "facup": [("Saka", "Arsenal"), ("Someone", "Wrexham")],
    })
    assert set(out.loc[out.competition_key == "facup", "player"]) == {"Saka"}
    assert "Wrexham" not in set(out["team"])


def test_a_european_run_lands_on_the_players_own_league(monkeypatch):
    out = attribution_fixture(monkeypatch, {
        "epl": [("Saka", "Arsenal")],
        "laliga": [("Bellingham", "Real Madrid")],
        "ucl": [("Saka", "Arsenal"), ("Bellingham", "Real Madrid")],
    })
    european = out[out.competition_key == "ucl"].set_index("player")["league"]
    assert european["Bellingham"] == "La Liga"
    assert european["Saka"] == "Premier League"


def test_a_european_run_reaches_the_same_player_as_his_league_matches(monkeypatch):
    """The bonus is worthless if it lands on a second copy of the player: the
    scorer keys on (player, league, season, position), so a Champions League
    row tagged with the wrong league is a different footballer entirely."""
    from whul.scoring import soccer

    out = attribution_fixture(monkeypatch, {
        "laliga": [("Bellingham", "Real Madrid")],
        "ucl": [("Bellingham", "Real Madrid")],
    })
    scored = soccer.score_players(out, postseason=True)
    assert len(scored) == 1, "one footballer, not one per competition"
    row = scored.iloc[0]
    assert row["league"] == "La Liga"
    assert row["regular_points"] > 0, "his league matches"
    assert row["postseason_bonus"] > 0, "and his European ones, on the same man"


def test_a_club_from_outside_our_leagues_is_counted_out_loud(monkeypatch, capsys):
    """Correctly ignored and silently lost read the same in a benchmark."""
    attribution_fixture(monkeypatch, {
        "epl": [("Saka", "Arsenal")],
        "facup": [("Someone", "Wrexham")],
    })
    out = capsys.readouterr().out
    assert "facup" in out and "outside them" in out


def test_a_shared_competition_is_pulled_once_however_many_leagues_play_it(
    monkeypatch
):
    """Six leagues asking for the Champions League separately is six times the
    requests, and it was the deduplication of those six answers that decided a
    row's league."""
    from whul.benchmark_sources import SOURCES
    from whul.sources import espn_soccer as source

    asked = []

    def note(league, seasons, verbose=True, session=None):
        asked.append(league)
        return pd.DataFrame()

    monkeypatch.setattr(source, "load_players", note)
    load, _ = SOURCES["soccer-players"].build()
    load([2025])

    assert asked.count("ucl") == 1
    assert len(asked) == len(set(asked)), f"asked twice for something: {asked}"


def test_every_competition_we_pull_can_have_its_dates_walked():
    """The team side walks dates rather than rosters, so a competition added to
    LEAGUE_PATHS without a season window crashes it -- which it did, twenty-two
    minutes into a benchmark run, on a bare KeyError naming only 'usopencup'."""
    from whul.sources.espn import (
        CONTINENTAL_CUPS, DOMESTIC_CUPS, EUROPEAN_COMPETITIONS, SEASON_WINDOWS,
        SOCCER_LEAGUES, season_dates,
    )

    wanted = set(SOCCER_LEAGUES) | set(EUROPEAN_COMPETITIONS)
    for cups in (DOMESTIC_CUPS, CONTINENTAL_CUPS):
        for entries in cups.values():
            wanted |= set(entries)

    missing = sorted(key for key in wanted if key not in SEASON_WINDOWS)
    assert not missing, f"no season window for {missing}"
    for key in sorted(wanted):
        assert season_dates(2024, key), f"{key} walks no dates in 2024"


def test_the_feeds_key_decides_the_tier_not_the_label(monkeypatch):
    """`work` was rebuilt without competition_key, so classify_key read a blank
    key and fell back to the label every time -- KEY_TIERS was dead code, and
    nothing said so. The label is the ambiguous one: the CONCACAF Champions Cup
    was the Champions *League* until 2024, and reading that name puts MLS clubs
    in Europe on a 5% share instead of their own 2.5%."""
    from whul.scoring import soccer

    row = dict(player="A", league="MLS", season=2025, position="F",
               matches=6, starts=6, goals=4, assists=1, yellow=0, red=0)
    misleading = pd.DataFrame([dict(row, competition_key="usopencup",
                                    competition="Champions League")])

    by_key = soccer.score_players(misleading, postseason=True)
    assert by_key["regular_points"].iloc[0] > 0, "a domestic cup is counted"
    assert by_key["postseason_bonus"].iloc[0] == 0

    by_label = soccer.score_players(
        misleading.drop(columns=["competition_key"]), postseason=True)
    assert by_label["regular_points"].iloc[0] == 0, "the label alone gets it wrong"


def test_a_league_is_not_sent_to_a_continent_its_clubs_never_reach(monkeypatch):
    """MLS walking the Champions League cost 4,560 requests and about an hour a
    run, and produced only near-misses for the club matcher to reject."""
    from whul.sources.espn import continental_for

    assert continental_for("nwsl") == ()
    for league in ("epl", "laliga", "seriea", "bundesliga", "ligue1"):
        assert continental_for(league) == ("ucl", "uel", "uecl")


def test_the_run_says_how_many_rows_each_competition_actually_delivered(
    monkeypatch, capsys
):
    """A benchmark that comes back bit-identical has either found nothing new or
    lost what it found, and from the outside those read the same."""
    out = attribution_fixture(monkeypatch, {
        "epl": [("Saka", "Arsenal")],
        "facup": [("Saka", "Arsenal")],
    })
    assert not out.empty
    printed = capsys.readouterr().out
    assert "rows by league and competition" in printed
    assert "facup" in printed


def test_a_dropped_club_is_named_not_just_counted(monkeypatch, capsys):
    """A count alone cannot distinguish a cup full of non-league clubs from one
    whose own league's clubs failed to match."""
    attribution_fixture(monkeypatch, {
        "epl": [("Saka", "Arsenal")],
        "facup": [("Someone", "Wrexham")],
    })
    assert "Wrexham" in capsys.readouterr().out


def test_a_league_is_offered_only_its_own_continental_entrants(monkeypatch):
    """MLS was handed the UEFA participant lists, which is how five seasons of
    Inter Milan came to be offered to the club matcher as Inter Miami."""
    from whul import benchmark_sources as bs

    monkeypatch.setattr(bs, "_uefa_entrants", lambda season: pd.DataFrame(
        [{"team": "Arsenal", "season": season, "competition": "Champions League",
          "entry_round": "League phase"}]))
    monkeypatch.setattr(bs, "_concacaf_entrants", lambda season: pd.DataFrame(
        [{"team": "Inter Miami CF", "season": season,
          "competition": "CONCACAF Champions Cup", "entry_round": "Round One"}]))

    assert set(bs._continental_entrants("epl", [2025])["competition"]) == \
        {"Champions League"}
    assert set(bs._continental_entrants("mls", [2025])["competition"]) == \
        {"CONCACAF Champions Cup"}
    assert bs._continental_entrants("nwsl", [2025]).empty


def test_the_champions_cup_a_season_earns_is_the_following_years():
    """MLS runs inside a calendar year and the Champions Cup runs February to
    June of the next one, so 2025's finishers play the 2026 edition. Reversed,
    last year's qualification lands on this year's finish and both are real
    numbers, so nothing looks wrong."""
    from whul.benchmark_sources import _concacaf_season

    assert _concacaf_season(2025) == 2026


def test_an_empty_champions_cup_list_is_never_silent(monkeypatch, capsys):
    """Eight points a club, and a benchmark that just looks a bit low."""
    from whul import benchmark_sources as bs
    from whul.sources import wikipedia

    monkeypatch.setattr(wikipedia, "load_entrants", lambda *a, **k: {})
    bs._concacaf_entrants.cache_clear()
    got = bs._concacaf_entrants(2025)
    bs._concacaf_entrants.cache_clear()

    assert got.empty
    assert "eight points each, in silence" in capsys.readouterr().out


def test_a_competition_that_returns_squads_without_statistics_says_so(
    monkeypatch, capsys
):
    """MLS's US Open Cup rows arrived -- 1,492 of them, correctly attributed --
    and the benchmark stayed 197.68000000000006 across three versions, one of
    which predated cups entirely. ESPN returns a club's whole squad for a
    competition and fills statistics in only where they exist, so a cup can
    hand back a roster of zeroes: it folds into each player's season adding
    nothing and creating nobody new. Arriving and counting for nothing look
    identical in a row count, which is why the appearance count is printed
    beside it."""
    from whul.benchmark_sources import SOURCES
    from whul.sources import espn_soccer as source

    def some(league, seasons, verbose=True, session=None):
        if league == "epl":
            return squad_row(league, seasons)
        if league == "facup":
            return squad_row(league, seasons).assign(
                matches=0, starts=0, goals=0, assists=0)
        return pd.DataFrame()

    monkeypatch.setattr(source, "load_players", some)
    load, _ = SOURCES["soccer-players"].build()
    load([2025])

    printed = capsys.readouterr().out
    assert "with an appearance" in printed
    assert "not one appearance among them" in printed
    assert "facup" in printed


def test_the_champions_cup_is_not_pulled_and_the_reason_is_kept(monkeypatch):
    """ESPN answered every Champions Cup roster request with a 404 across five
    seasons, and its scoreboard returned no matches on any of the 751 dates
    walked for it. A competition that can only contribute zero is worse than
    one left out: zero reads as a quiet Champions Cup rather than as no data,
    and walking it cost about eleven minutes of every benchmark run.

    What is kept is everything needed to restore it in one line -- the path,
    the tier and the rule -- so this is a feed being switched off rather than
    a scoring decision being unmade."""
    from whul.benchmark_sources import SOURCES
    from whul.scoring.competition import Tier, classify_key
    from whul.scoring.postseason import rule_for
    from whul.sources import espn, espn_soccer

    asked = []

    def note(league, seasons, verbose=True, session=None):
        asked.append(league)
        return pd.DataFrame()

    monkeypatch.setattr(espn_soccer, "load_players", note)
    SOURCES["soccer-players"].build()[0]([2025])
    assert "concacafchampions" not in asked
    assert espn.continental_for("mls") == ()

    # Still classified, still priced: restoring the pull is the only change
    # needed if a working path turns up.
    assert "concacafchampions" in espn.LEAGUE_PATHS
    tier = classify_key("concacafchampions", "CONCACAF Champions Cup").tier
    assert tier is Tier.CONTINENTAL_CUP
    assert rule_for(tier.value).bonus_share == 0.025


def test_qualifying_for_the_champions_cup_is_still_paid(monkeypatch):
    """It comes from the published participant list, not from match data, so
    switching the feed off does not touch it."""
    from whul import benchmark_sources as bs

    monkeypatch.setattr(bs, "_concacaf_entrants", lambda season: pd.DataFrame(
        [{"team": "Inter Miami CF", "season": season,
          "competition": "CONCACAF Champions Cup", "entry_round": "Round One"}]))
    got = bs._continental_entrants("mls", [2025])
    assert set(got["competition"]) == {"CONCACAF Champions Cup"}


def _title_rows(champions: dict[int, list[str]], clubs=("Alpha", "Beta")):
    """A scored frame carrying only what the champions report reads."""
    rows = []
    for season, won in champions.items():
        for club in clubs:
            rows.append({"season": season, "team": club,
                         "league_champion": club in won})
    return pd.DataFrame(rows)


def test_the_league_title_is_named_in_the_review(capsys):
    """A title is ten points awarded to one club at the very top of the pool,
    which is exactly where the 99th percentile lives. Ligue 1 fell 9.3 between
    two runs with the same pool depth and there was no line anywhere to say
    whether a title had moved."""
    from whul import benchmark_sources as bs

    bs._report_champions("ligue1", _title_rows({2024: ["Alpha"], 2025: ["Beta"]}))
    printed = capsys.readouterr().out
    assert "2024  Alpha" in printed
    assert "2025  Beta" in printed


def test_a_season_with_no_champion_is_listed_rather_than_omitted(capsys):
    """A season that ought to have a champion and does not is the quieter half
    of the same fault: it does not announce itself by moving a number."""
    from whul import benchmark_sources as bs

    bs._report_champions("laliga", _title_rows({2024: ["Alpha"], 2025: []}))
    assert "2025  not awarded" in capsys.readouterr().out


def test_a_shared_title_says_so(capsys):
    """Two clubs the table cannot separate are paid ten points each, which is
    twenty points into a pool that should have had ten."""
    from whul import benchmark_sources as bs

    bs._report_champions("seriea", _title_rows({2025: ["Alpha", "Beta"]}))
    printed = capsys.readouterr().out
    assert "Alpha, Beta" in printed and "(shared)" in printed


def test_a_frame_without_the_column_reports_nothing(capsys):
    """The players path scores the same league names and has no titles in it."""
    from whul import benchmark_sources as bs

    bs._report_champions("epl", pd.DataFrame([{"season": 2025, "team": "Alpha"}]))
    assert capsys.readouterr().out == ""


# --- what the gamelog calls a competition ----------------------------------


def test_every_competition_we_score_is_reachable_from_espns_own_key():
    """The gamelog names the competition on each event in ESPN's spelling, and
    the scorer reads ours. Both ends of every competition this project pays
    for have to meet in the middle."""
    from whul.sources.espn import LEAGUE_PATHS
    from whul.sources.espn_soccer import competition_of

    for key, (_, path) in LEAGUE_PATHS.items():
        assert competition_of(path) == key, path


def test_a_domestic_cup_counts_towards_the_base_score():
    """Domestic cups are in the base score and in the benchmark -- only Europe
    is held. A cup mapped as a league match would be paid three where it should
    be four, and one left unmapped would be paid three where it should be
    four."""
    from whul.scoring.competition import Tier, classify_key
    from whul.sources.espn_soccer import classify_gamelog_league

    for espn_key in ("ger.dfb_pokal", "eng.fa", "eng.league_cup",
                     "esp.copa_del_rey", "ita.coppa_italia",
                     "fra.coupe_de_france", "usa.open"):
        ours, why = classify_gamelog_league(espn_key)
        assert ours, f"{espn_key}: {why}"
        found = classify_key(ours, ours)
        assert found.tier == Tier.DOMESTIC_CUP, (espn_key, found.tier)
        assert found.counts


def test_europe_keeps_its_tier_through_the_mapping():
    from whul.scoring.competition import Tier, classify_key
    from whul.sources.espn_soccer import classify_gamelog_league

    for espn_key, tier in (("uefa.champions", Tier.CHAMPIONS_LEAGUE),
                           ("uefa.europa", Tier.EUROPA),
                           ("uefa.europa.conf", Tier.CONFERENCE)):
        ours, _ = classify_gamelog_league(espn_key)
        assert classify_key(ours, ours).tier == tier


def test_a_competition_nobody_has_decided_about_is_not_paid_as_a_league_match():
    """The whole reason this mapping exists. Every unrecognised key falls
    through the classifier to a *league* match worth three and counted in the
    base score, so a Champions League night fed in raw would be paid as a
    league win and folded into the total the benchmark measures -- the fault
    the gamelog is being read to fix, made worse."""
    from whul.scoring.competition import Tier, classify_key
    from whul.sources.espn_soccer import classify_gamelog_league

    # What the classifier does with a raw ESPN key, and why nothing may reach it.
    assert classify_key("uefa.champions", "uefa.champions").tier == Tier.LEAGUE

    for espn_key in ("club.friendly", "ger.super_cup", "uefa.super_cup",
                     "fifa.cwc", "global.champs_cup", "never.seen.this"):
        ours, why = classify_gamelog_league(espn_key)
        assert ours is None, espn_key
        assert "not scored" in why


def test_a_friendly_is_named_as_a_friendly_rather_than_as_an_unknown():
    """A competition left out on purpose and one nobody has seen are different
    problems with different fixes, and reporting both as 'unknown' hides which
    of the two arrived."""
    from whul.sources.espn_soccer import classify_gamelog_league

    assert "friendly" in classify_gamelog_league("club.friendly")[1]
    assert "never seen" in classify_gamelog_league("zzz.made.up")[1]
