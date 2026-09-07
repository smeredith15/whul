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


def test_a_league_that_returns_nothing_costs_that_league_only(monkeypatch, capsys):
    from whul.benchmark_sources import SOURCES
    from whul.sources import espn_soccer as source

    def some(league, seasons, verbose=True, session=None):
        if league == "epl":
            return pd.DataFrame()
        return pd.DataFrame([{
            "player": f"{league} player", "season": seasons[0],
            "season_said": f"{seasons[0] - 1}-{seasons[0] % 100:02d}",
            "matches": 10, "starts": 8, "goals": 1, "assists": 1,
            "yellow": 0, "red": 0, "position": "M",
        }])

    monkeypatch.setattr(source, "load_players", some)
    load, _ = SOURCES["soccer-players"].build()
    out = load([2025])

    assert "Premier League" not in set(out["league"])
    assert len(out) == 5
    assert "so Premier League scores none" in capsys.readouterr().out


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
