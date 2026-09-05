"""ESPN adapter tests.

The fixture below mirrors a real payload returned by the live API on 2026-01-15:
the label order is ESPN's actual order, and position sits at
``entry["athlete"]["position"]`` while ``entry["position"]`` is an empty dict --
reading the latter is what once made positions look absent.

These prove the parsing and the stat mapping. Only `python -m whul.cli probe nba`
proves the endpoint itself.
"""

from datetime import date

import pandas as pd
import pytest

from whul.sources import espn

LABELS = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK",
          "OREB", "DREB", "PF", "+/-"]

# Jaren Jackson Jr., MEM, 2026-01-15: 30 pts, 3 reb, 1 ast, 4 to, 2 stl, 2 blk,
# 3 threes, -21. Position comes back empty, exactly as the live API returns it.
JJJ = ["38", "30", "10-20", "3-9", "7-8", "3", "1", "4", "2", "2", "0", "3", "3", "-21"]

BOX = {
    "boxscore": {
        "players": [
            {
                "team": {"abbreviation": "MEM"},
                "statistics": [
                    {
                        "labels": LABELS,
                        "athletes": [
                            {
                                # ESPN populates the athlete-level position and
                                # leaves the entry-level one empty.
                                "athlete": {
                                    "id": "4277961",
                                    "displayName": "Jaren Jackson Jr.",
                                    "position": {"abbreviation": "F"},
                                },
                                "position": {},
                                "stats": JJJ,
                            },
                            {
                                "athlete": {"id": "9999", "displayName": "Did Not Play"},
                                "stats": [],
                            },
                        ],
                    }
                ],
            }
        ]
    }
}


def parse(positions=None, season_type=espn.SEASON_TYPE_REGULAR):
    return espn._parse_box(BOX, "401810433", date(2026, 1, 15), 2026, season_type, positions)


# --- stat mapping ----------------------------------------------------------

def test_stats_are_looked_up_by_label_not_position():
    """ESPN's label order is not the order the scorer needs, so lookup is by name."""
    row = parse()[0]
    assert row["athlete_display_name"] == "Jaren Jackson Jr."
    assert row["points"] == "30"
    assert row["rebounds"] == "3"
    assert row["assists"] == "1"
    assert row["turnovers"] == "4"
    assert row["steals"] == "2"
    assert row["blocks"] == "2"
    assert row["plus_minus"] == "-21"


def test_three_pointers_come_from_the_made_side_of_the_split():
    """ESPN reports '3-9' made-attempted; only the made count scores."""
    assert parse()[0]["three_point_field_goals_made"] == "3"


def test_players_who_did_not_play_are_skipped():
    assert len(parse()) == 1


def test_season_context_is_carried():
    row = parse(season_type=espn.SEASON_TYPE_POST)[0]
    assert row["season"] == 2026
    assert row["season_type"] == espn.SEASON_TYPE_POST
    assert row["game_date"] == "2026-01-15"
    assert row["game_id"] == "401810433"
    assert row["team"] == "MEM"


def test_parse_box_tolerates_an_empty_payload():
    assert espn._parse_box({}, "1", date(2026, 1, 1), 2026, 2) == []


# --- position resolution ---------------------------------------------------

def test_position_is_read_from_the_athlete_not_the_entry():
    """entry["position"] is an empty dict; the real value is one level in."""
    assert parse()[0]["athlete_position_abbreviation"] == "F"


def test_roster_map_only_fills_what_the_boxscore_omits():
    entry = {"athlete": {"id": "4277961"}}
    assert espn._position(entry, {"4277961": "PF"}) == "PF"


def test_inline_position_wins_when_present():
    entry = {"athlete": {"id": "1"}, "position": {"abbreviation": "SG"}}
    assert espn._position(entry, {"1": "PF"}) == "SG"


def test_position_found_under_the_athlete_too():
    entry = {"athlete": {"id": "1", "position": {"abbreviation": "C"}}}
    assert espn._position(entry) == "C"


def test_position_is_empty_when_nothing_supplies_it():
    assert espn._position({"athlete": {"id": "1"}}) == ""
    assert espn._position({"athlete": {"id": "1"}}, {"2": "PG"}) == ""


def test_espn_position_vocabulary_maps_to_the_normalization_groups():
    """ESPN returns generic G/F/C and hyphenated forms, not PG/SG/SF/PF."""
    from whul.normalize import assign_norm_key

    df = pd.DataFrame({"league": ["NBA"] * 5, "role": ["G", "F", "C", "G-F", "F-C"]})
    assert list(assign_norm_key(df, "Player")) == [
        "NBA_Backcourt", "NBA_Frontcourt", "NBA_Frontcourt",
        "NBA_Backcourt", "NBA_Frontcourt",
    ]


def test_an_unresolved_position_collapses_the_group():
    """The failure mode to guard against: no position means no Backcourt /
    Frontcourt split, silently, with no error anywhere."""
    from whul.normalize import assign_norm_key

    df = pd.DataFrame({"league": ["NBA"], "role": [""]})
    assert assign_norm_key(df, "Player").iloc[0] == "NBA", "the split is lost"


# --- integration with the scorer -------------------------------------------

def test_parsed_rows_feed_the_nba_scorer():
    from whul.scoring.nba import score_players

    box = pd.DataFrame(parse() * 20)  # clear the 15-game minimum
    scored = score_players(box)
    assert len(scored) == 1
    # 30 + 3*1.2 + 1*1.5 + 2*3 + 2*3 + 4*-1 + 3*0.5 = 44.6, no double-double,
    # plus 0.1 * -21 = -2.1 -> 42.5 per game
    assert scored.iloc[0]["regular_points"] == pytest.approx(42.5 * 20)
    assert scored.iloc[0]["role"] == "F"


# --- season windows --------------------------------------------------------

def test_season_dates_spans_october_to_june():
    days = espn.season_dates(2024)
    assert days[0] == date(2023, 10, 1)
    assert days[-1] == date(2024, 6, 30)
    assert len(days) > 250


def test_season_dates_never_runs_past_today():
    for season in (2024, 2025, 2026, 2027, 2028):
        assert all(d <= date.today() for d in espn.season_dates(season)), season


def test_default_probe_date_lands_in_season():
    """Yesterday is a poor default -- for much of the year it is the offseason."""
    assert espn.default_probe_date(date(2026, 9, 2)) == date(2026, 1, 15)
    assert espn.default_probe_date(date(2026, 1, 3)) == date(2025, 1, 15)
    assert espn.default_probe_date(date(2026, 1, 15)) == date(2026, 1, 15)


# --- caching and rate limiting ---------------------------------------------

def test_cache_hit_skips_both_the_request_and_the_pause(monkeypatch, tmp_path):
    """A warm replay must be near-free.

    Pausing on cache hits made re-running a backfill cost almost as much as the
    original fetch: a warm NBA season still took 689s of pure sleeping.
    """
    calls = {"requests": 0, "sleeps": 0}

    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"ok": True}

    def fake_get(*args, **kwargs):
        calls["requests"] += 1
        return FakeResponse()

    monkeypatch.setattr(espn.requests, "get", fake_get)
    monkeypatch.setattr(espn.time, "sleep", lambda _: calls.__setitem__("sleeps", calls["sleeps"] + 1))
    monkeypatch.setattr(espn, "CACHE", tmp_path)

    first = espn._get("http://x", {}, cache_key="nba/thing")
    assert first == {"ok": True}
    assert calls == {"requests": 1, "sleeps": 1}, "a real fetch pauses"

    second = espn._get("http://x", {}, cache_key="nba/thing")
    assert second == {"ok": True}
    assert calls == {"requests": 1, "sleeps": 1}, "a cache hit does neither"


def test_uncached_requests_still_pause(monkeypatch, tmp_path):
    """The daily-cost probe deliberately bypasses the cache; it must still be polite."""
    sleeps = []

    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(espn.requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(espn.time, "sleep", sleeps.append)
    monkeypatch.setattr(espn, "CACHE", tmp_path)

    espn._get("http://x", {})
    espn._get("http://x", {})
    assert len(sleeps) == 2


# --- request-shape fallback -------------------------------------------------

def test_scoreboard_variants_try_the_filtered_shape_first():
    """A league with a division filter should prefer it, then degrade."""
    shapes = [
        ",".join(k for k in p if k != "dates") or "dates only"
        for p in espn.scoreboard_variants("ncaam", date(2026, 1, 15))
    ]
    assert shapes == ["limit,groups", "groups", "limit", "dates only"]


def test_leagues_without_a_division_filter_have_fewer_variants():
    shapes = espn.scoreboard_variants("nba", date(2026, 1, 15))
    assert all("groups" not in p for p in shapes)


def test_scoreboard_falls_back_when_a_shape_is_rejected(monkeypatch, tmp_path):
    """College softball answers 400 to both groups and limit, which a single
    fixed request shape would turn into a total loss of that league."""
    import requests

    seen = []

    class Response:
        def __init__(self, status):
            self.status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        @staticmethod
        def json():
            return {"events": [{"id": "1"}]}

    def fake_get(url, params=None, **kwargs):
        seen.append(sorted(k for k in (params or {}) if k != "dates"))
        # Reject anything carrying groups or limit, as softball does.
        bad = {"groups", "limit"} & set(params or {})
        return Response(400 if bad else 200)

    monkeypatch.setattr(espn.requests, "get", fake_get)
    monkeypatch.setattr(espn.time, "sleep", lambda _: None)
    monkeypatch.setattr(espn, "CACHE", tmp_path)

    board = espn.scoreboard("ncaam", date(2026, 1, 15))
    assert board["events"][0]["id"] == "1"
    assert seen[-1] == [], "the bare request is what finally succeeds"
    assert len(seen) == 4


def test_scoreboard_reraises_a_non_parameter_error(monkeypatch, tmp_path):
    """A 500 is a real outage, not a bad request shape -- do not mask it."""
    import requests

    class Response:
        status_code = 500

        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    monkeypatch.setattr(espn.requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr(espn.time, "sleep", lambda _: None)
    monkeypatch.setattr(espn, "CACHE", tmp_path)

    with pytest.raises(requests.HTTPError):
        espn.scoreboard("ncaam", date(2026, 1, 15))


# --- conference reporting ---------------------------------------------------

def test_conference_is_only_required_where_scoring_uses_it():
    """Baseball and softball score wins, run differential and series milestones
    only, so a blank conference costs them nothing and must not read as a fault."""
    assert espn.CONFERENCE_REQUIRED == {"ncaaf", "ncaam", "ncaaw"}
    assert "ncaabaseball" not in espn.CONFERENCE_REQUIRED
    assert "ncaasoftball" not in espn.CONFERENCE_REQUIRED


def test_diamond_scoring_never_reads_conference():
    """Pins the assumption behind the exemption above."""
    import inspect

    from whul.scoring import ncaa

    assert "conference" not in inspect.getsource(ncaa.score_diamond)


def test_discovery_offers_alternatives_for_the_failing_league():
    """Softball's configured path is rejected outright, so candidates are needed."""
    assert len(espn.PATH_CANDIDATES["ncaasoftball"]) > 1
    assert espn.LEAGUE_PATHS["ncaasoftball"] in espn.PATH_CANDIDATES["ncaasoftball"]


def test_group_candidates_include_dropping_the_filter():
    """None must be among the options -- a league may not accept the filter."""
    for league, candidates in espn.GROUP_CANDIDATES.items():
        assert None in candidates, league
        configured = espn.DIVISION_I_GROUPS.get(league)
        if configured is not None:
            assert configured in candidates, league


def test_softball_has_no_division_filter():
    """groups=29 returns zero events on dates a bare request shows 52 games on,
    so it excludes everything rather than narrowing to a division."""
    assert "ncaasoftball" not in espn.DIVISION_I_GROUPS
    shapes = espn.scoreboard_variants("ncaasoftball", date(2026, 5, 1))
    assert all("groups" not in p for p in shapes)


def test_softball_lives_under_the_baseball_sport_path():
    """Every softball/... variant answers 404; baseball/college-softball works."""
    assert espn.LEAGUE_PATHS["ncaasoftball"] == ("baseball", "college-softball")


def test_a_shape_that_returns_no_games_is_treated_as_suspect(monkeypatch, tmp_path):
    """College softball accepts `limit` and then returns zero events for a date a
    bare request shows 52 games on. Taking the first 200 would silently produce
    an empty season with nothing logged."""
    class Response:
        status_code = 200

        def __init__(self, params):
            self.params = params

        def raise_for_status(self):
            return None

        def json(self):
            # Only the bare request returns games, as softball behaves.
            if set(self.params) - {"dates"}:
                return {"events": []}
            return {"events": [{"id": "real"}]}

    monkeypatch.setattr(espn.requests, "get", lambda url, params=None, **k: Response(params or {}))
    monkeypatch.setattr(espn.time, "sleep", lambda _: None)
    monkeypatch.setattr(espn, "CACHE", tmp_path)

    board = espn.scoreboard("ncaam", date(2026, 1, 15))
    assert board["events"][0]["id"] == "real"


def test_an_empty_date_is_still_returned(monkeypatch, tmp_path):
    """An offseason date legitimately has no games and must not raise."""
    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"events": []}

    monkeypatch.setattr(espn.requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr(espn.time, "sleep", lambda _: None)
    monkeypatch.setattr(espn, "CACHE", tmp_path)

    assert espn.scoreboard("ncaam", date(2026, 7, 4))["events"] == []


def test_variant_search_is_not_short_circuited_by_the_cache(monkeypatch, tmp_path):
    """Variants share a cache key, so caching a shape under test would poison the
    search on the next run."""
    calls = []

    class Response:
        status_code = 200

        def __init__(self, params):
            self.params = params

        def raise_for_status(self):
            return None

        def json(self):
            calls.append(sorted(k for k in self.params if k != "dates"))
            return {"events": [] if set(self.params) - {"dates"} else [{"id": "x"}]}

    monkeypatch.setattr(espn.requests, "get", lambda url, params=None, **k: Response(params or {}))
    monkeypatch.setattr(espn.time, "sleep", lambda _: None)
    monkeypatch.setattr(espn, "CACHE", tmp_path)

    espn.scoreboard("ncaam", date(2026, 1, 15))
    assert len(calls) == 4, "every shape should have been tried"
    calls.clear()
    espn.scoreboard("ncaam", date(2026, 1, 15))
    assert calls == [], "the chosen payload is cached"


# --- soccer -----------------------------------------------------------------

SOCCER_EVENT = {
    "league": {"name": "UEFA Champions League"},
    "competitions": [
        {
            "status": {"type": {"completed": True}},
            "notes": [{"headline": "League Phase - Matchday 3"}],
            "competitors": [
                {"homeAway": "home", "score": "3",
                 "team": {"displayName": "Arsenal", "conferenceId": ""}},
                {"homeAway": "away", "score": "1",
                 "team": {"displayName": "Bayern Munich", "conferenceId": ""}},
            ],
        }
    ],
}


def test_a_soccer_match_becomes_two_team_rows():
    rows = espn._soccer_rows(SOCCER_EVENT, "ucl", date(2026, 10, 22))
    assert len(rows) == 2
    home = next(r for r in rows if r["team"] == "Arsenal")
    assert home["goals_for"] == 3.0 and home["goals_against"] == 1.0
    assert home["opponent"] == "Bayern Munich"


def test_the_competition_label_carries_the_round():
    """The round distinguishes a qualifying tie from the competition proper, and
    the knockout play-off from either."""
    rows = espn._soccer_rows(SOCCER_EVENT, "ucl", date(2026, 10, 22))
    assert "Champions League" in rows[0]["competition"]
    assert "League Phase" in rows[0]["competition"]


def test_soccer_rows_feed_the_scorer_at_the_right_tier():
    import pandas as pd

    from whul.scoring.soccer import score_team_matches

    rows = espn._soccer_rows(SOCCER_EVENT, "ucl", date(2026, 10, 22))
    frame = pd.DataFrame(rows).assign(league="Premier League")
    scored = score_team_matches(frame).set_index("team")
    # Champions League win, two-goal margin, no clean sheet: 5 + 1 = 6
    assert scored.loc["Arsenal", "match_points"] == 6
    assert scored.loc["Bayern Munich", "match_points"] == 0


def test_unfinished_matches_are_skipped():
    event = {**SOCCER_EVENT, "competitions": [
        {**SOCCER_EVENT["competitions"][0], "status": {"type": {"completed": False}}}
    ]}
    assert espn._soccer_rows(event, "ucl", date(2026, 10, 22)) == []


def test_every_scored_league_has_a_path_and_a_season_window():
    for key in espn.SOCCER_LEAGUES:
        assert key in espn.LEAGUE_PATHS, key
        assert key in espn.SEASON_WINDOWS, key


def test_european_competitions_and_cups_are_reachable():
    """Restricted to league fixtures, every win would be worth three points and
    the Champions League premium would never appear."""
    for key in espn.EUROPEAN_COMPETITIONS:
        assert key in espn.LEAGUE_PATHS, key
    for cups in espn.DOMESTIC_CUPS.values():
        for cup in cups:
            assert cup in espn.LEAGUE_PATHS, cup


def test_mls_and_nwsl_run_within_a_calendar_year():
    for key in ("mls", "nwsl"):
        assert espn.SEASON_WINDOWS[key][2] == "within", key
    for key in ("epl", "laliga", "ucl"):
        assert espn.SEASON_WINDOWS[key][2] == "ends", key


# --- how a season is numbered ----------------------------------------------

def test_college_football_is_numbered_by_the_year_it_starts():
    """ESPN indexes college football under its opening year, and so does
    everyone who talks about it: the 2026 season, not the 2027 season. Asking
    for the year it *finishes* in asks for a season nobody has played yet, which
    returns nothing at all -- and, looking backwards, groups the COVID-shortened
    2020 season under 2021 where a five-season reach picks it up."""
    assert espn.season_label("ncaaf", date(2026, 9, 4)) == 2026
    assert espn.season_label("ncaaf", date(2026, 12, 6)) == 2026
    assert espn.season_label("ncaaf", date(2026, 7, 1)) == 2025


def test_a_january_bowl_belongs_to_the_season_that_opened_in_august():
    """The national championship is played in January and is the last game of
    the previous autumn's season, not the first of the coming one."""
    assert espn.season_label("ncaaf", date(2027, 1, 9)) == 2026


def test_a_football_seasons_dates_open_in_its_own_august():
    days = espn.season_dates(2024, "ncaaf")
    assert days[0] == date(2024, 8, 1)
    assert days[-1] == date(2025, 1, 31)


def test_basketball_and_european_football_are_numbered_by_the_year_they_end():
    assert espn.season_label("ncaam", date(2026, 12, 1)) == 2027
    assert espn.season_label("ncaam", date(2027, 3, 1)) == 2027
    assert espn.season_label("epl", date(2026, 8, 22)) == 2027
    assert espn.season_label("nba", date(2026, 10, 25)) == 2027


def test_a_season_played_inside_one_year_is_numbered_by_it():
    assert espn.season_label("mls", date(2026, 8, 22)) == 2026
    assert espn.season_label("mls", date(2026, 2, 1)) == 2026


def test_every_season_window_declares_a_numbering_we_understand():
    for key, (_, _, numbering) in espn.SEASON_WINDOWS.items():
        assert numbering in ("within", "ends", "starts"), key


def test_a_label_round_trips_through_the_dates_it_names():
    """The two halves have to agree: every day ``season_dates`` produces for a
    season must be a day ``season_label`` calls that season. They were derived
    separately, and disagreed for football for as long as both existed."""
    for league in ("ncaaf", "ncaam", "epl", "mls", "nba"):
        for day in espn.season_dates(2024, league):
            assert espn.season_label(league, day) == 2024, (league, day)


# --- caching a date that is still being played -----------------------------

def test_a_day_still_in_play_is_not_cached_forever(tmp_path, monkeypatch):
    """A day cached mid-match would freeze a half-finished result, and every
    nightly run after would read that copy rather than the final score."""
    from datetime import date

    from whul.sources import espn

    monkeypatch.setattr(espn, "CACHE", tmp_path)
    calls = []

    def fetch(url, params, cache_key=None):
        calls.append(params)
        return {"events": [{"id": "1"}]}

    monkeypatch.setattr(espn, "_get", fetch)
    today = date.today()

    espn.scoreboard("epl", today)
    espn.scoreboard("epl", today)
    assert len(calls) >= 2, "today must be refetched, not served from disk"
    assert not (tmp_path / f"epl/scoreboard/{today.isoformat()}.json").exists()


def test_a_settled_day_is_cached(tmp_path, monkeypatch):
    from datetime import date, timedelta

    from whul.sources import espn

    monkeypatch.setattr(espn, "CACHE", tmp_path)
    calls = []
    monkeypatch.setattr(espn, "_get", lambda url, params, cache_key=None: (
        calls.append(params) or {"events": [{"id": "1"}]}
    ))
    old = date.today() - timedelta(days=30)

    espn.scoreboard("epl", old)
    before = len(calls)
    espn.scoreboard("epl", old)
    assert len(calls) == before, "a finished day should come from disk"


# --- asking ESPN the right question about college football -----------------

def test_the_college_football_week_is_counted_from_the_opening_saturday():
    """Counted from the data walk's August 1 start it was three weeks early,
    which would have made the week query look broken when it was the arithmetic."""
    from datetime import date

    from whul.sources.espn import _espn_week, _opening_saturday

    assert _opening_saturday(2026) == date(2026, 8, 29)
    assert _opening_saturday(2024) == date(2024, 8, 31)
    assert _espn_week(date(2026, 8, 29)) == 1
    assert _espn_week(date(2026, 9, 5)) == 2
    assert _espn_week(date(2024, 11, 9)) == 11


def test_january_belongs_to_the_season_that_opened_in_august():
    from datetime import date

    from whul.sources.espn import _espn_week

    # Bowl season, not week one of a season that has not started.
    assert _espn_week(date(2027, 1, 9)) > 15


def test_discovery_asks_for_a_week_and_a_range_as_well_as_a_date(monkeypatch):
    from datetime import date

    from whul.sources import espn

    asked = []

    def fake(url, params, cache_key=None):
        asked.append(params)
        return {"events": []}

    monkeypatch.setattr(espn, "_get", fake)
    espn.discover("ncaaf", date(2026, 8, 29))

    assert any("week" in p for p in asked), "a date query is not how CFB is organised"
    assert any("-" in str(p.get("dates", "")) for p in asked), "nor is one day"


# --- a team's own schedule -------------------------------------------------

def test_a_score_is_read_however_the_endpoint_spells_it():
    """The scoreboard puts a bare string here and the team schedule an object.
    Reading only one turns every game from the other into a fixture."""
    from whul.sources.espn import _score_of

    assert _score_of({"score": "24"}) == 24.0
    assert _score_of({"score": {"value": 31, "displayValue": "31"}}) == 31.0
    assert _score_of({"score": {"displayValue": "17"}}) == 17.0
    assert _score_of({}) is None
    assert _score_of({"score": None}) is None


def test_a_team_schedule_becomes_the_rows_the_scorers_read(monkeypatch):
    from whul.sources import espn

    payload = {"team": {"displayName": "Ohio State Buckeyes"}, "events": [{
        "id": "401", "date": "2026-08-30T23:00Z", "name": "Texas at Ohio State",
        "seasonType": {"id": 2},
        "competitions": [{
            "status": {"type": {"completed": True}},
            "competitors": [
                {"homeAway": "home", "score": {"value": 31},
                 "team": {"displayName": "Ohio State Buckeyes"}},
                {"homeAway": "away", "score": {"value": 17},
                 "team": {"displayName": "Texas Longhorns"}},
            ],
        }],
    }]}
    monkeypatch.setattr(espn, "_get", lambda url, params, cache_key=None: payload)

    rows = espn.load_team_schedule("ncaaf", "194", 2026)
    assert len(rows) == 1
    row = rows.iloc[0]
    assert row["home_team"] == "Ohio State Buckeyes" and row["home_score"] == 31.0
    assert row["away_team"] == "Texas Longhorns" and row["away_score"] == 17.0
    assert bool(row["completed"]) and row["game_date"] == "2026-08-30"


def test_two_rostered_teams_playing_each_other_is_one_game(monkeypatch):
    from whul.sources import espn

    game = {"season": 2026, "game_id": "401", "game_date": "2026-08-30",
            "season_type": 2, "completed": True,
            "home_team": "Ohio State Buckeyes", "away_team": "Texas Longhorns",
            "home_conference": "", "away_conference": "",
            "home_score": 31.0, "away_score": 17.0, "notes": ""}
    monkeypatch.setattr(espn, "team_index", lambda league: {
        "Ohio State Buckeyes": "194", "Texas Longhorns": "251"})
    monkeypatch.setattr(
        espn, "load_team_schedule",
        lambda league, team_id, season: pd.DataFrame([game]),
    )

    rows = espn.load_rostered_schedules(
        "ncaaf", [2026], ["Ohio State Buckeyes", "Texas Longhorns"], verbose=False
    )
    assert len(rows) == 1


def test_a_team_the_feed_does_not_know_is_named(monkeypatch, capsys):
    """One unknown name among several is reported and costs only that team.
    A roster where *nothing* resolves is a different case and raises -- see
    below, because an empty frame there reads as a season nobody has played."""
    from whul.sources import espn

    monkeypatch.setattr(espn, "team_index", lambda league: {"Texas Longhorns": "251"})
    monkeypatch.setattr(
        espn, "load_team_schedule",
        lambda league, team_id, season: pd.DataFrame(),
    )
    espn.load_rostered_schedules(
        "ncaaf", [2026], ["Texas Longhorns", "Nowhere State"], verbose=True
    )
    assert "Nowhere State" in capsys.readouterr().out


def test_a_roster_that_matches_no_espn_team_raises(monkeypatch):
    """An empty frame here is indistinguishable from a season nobody has
    played, and it would cost the whole year without anything raising. Eight
    NCAAF teams on zero in September is not an empty week."""
    from whul.sources import espn

    monkeypatch.setattr(espn, "team_index", lambda league: {"Ohio State": "194"})
    with pytest.raises(LookupError, match="none of the 2 rostered"):
        espn.load_rostered_schedules(
            "ncaaf", [2026], ["Bogus Team", "Another Bogus"], verbose=False
        )


def test_one_unknown_team_among_several_does_not_raise(monkeypatch):
    """A single misspelling costs that team and is reported; it must not take
    the other seven down with it."""
    import pandas as pd

    from whul.sources import espn

    monkeypatch.setattr(espn, "team_index", lambda league: {"Ohio State": "194"})
    monkeypatch.setattr(
        espn, "load_team_schedule",
        lambda league, team_id, season: pd.DataFrame([
            {"game_id": "1", "season": season, "game_date": "2026-08-30"}
        ]),
    )
    out = espn.load_rostered_schedules(
        "ncaaf", [2026], ["Ohio State", "Bogus Team"], verbose=False
    )
    assert len(out) == 1


SHOOTOUT_EVENT = {
    "league": {"name": "Emirates FA Cup"},
    "competitions": [
        {
            "status": {"type": {"completed": True}},
            "notes": [{"headline": "Final"}],
            "competitors": [
                {"homeAway": "home", "score": "1", "shootoutScore": "4",
                 "team": {"displayName": "Crystal Palace"}},
                {"homeAway": "away", "score": "1", "shootoutScore": "2",
                 "team": {"displayName": "Manchester City"}},
            ],
        }
    ],
}


def test_a_shootout_reaches_the_scorer_as_a_shootout():
    """ESPN carries the penalties beside the score rather than inside it, which
    is the only reason a shootout is recoverable: both sides finish level, so
    the scoreline alone cannot say a tie was settled."""
    rows = espn._soccer_rows(SHOOTOUT_EVENT, "facup", date(2025, 5, 17))
    palace = next(r for r in rows if r["team"] == "Crystal Palace")
    city = next(r for r in rows if r["team"] == "Manchester City")
    assert palace["goals_for"] == 1.0 and palace["goals_against"] == 1.0
    assert palace["shootout_for"] == 4.0 and palace["shootout_against"] == 2.0
    assert city["shootout_for"] == 2.0 and city["shootout_against"] == 4.0

    import pandas as pd

    from whul.scoring.soccer import score_team_matches

    # The league label is attached downstream, when the rows are filtered back
    # to the clubs that belong to one.
    scored = score_team_matches(pd.DataFrame(rows).assign(league="Premier League"))
    by_team = dict(zip(scored["team"], scored["outcome"]))
    assert by_team["Crystal Palace"] == "shootout_win"
    assert by_team["Manchester City"] == "shootout_loss"


def test_a_match_with_no_shootout_reports_zero_rather_than_nothing():
    """Absent is not the same as unknown here: no shootout means no penalties
    scored, and zero on both sides reads as the draw or win it actually was."""
    rows = espn._soccer_rows(SOCCER_EVENT, "ucl", date(2026, 10, 22))
    assert all(r["shootout_for"] == 0.0 and r["shootout_against"] == 0.0
               for r in rows)
