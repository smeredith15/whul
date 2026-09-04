"""NCAA stats API adapter tests.

The live service is unreachable from the environment this was written in, so
these exercise the parsing against a payload shaped like the API's. Its value
over ESPN is that division membership is explicit in the URL.
"""

from datetime import date

import pandas as pd
import pytest

from whul.sources import ncaa_api
from whul.scoring.ncaa import score_football


PAYLOAD = {
    "games": [
        {
            "game": {
                "gameID": "6301234",
                "gameState": "final",
                "title": "Ohio State vs UCLA",
                "home": {
                    "names": {"full": "Ohio State Buckeyes"},
                    "score": "48",
                    "conferences": [{"conferenceName": "Big Ten"}],
                },
                "away": {
                    "names": {"full": "UCLA Bruins"},
                    "score": "10",
                    "conferences": [{"conferenceName": "Big Ten"}],
                },
            }
        },
        {
            "game": {
                "gameID": "6301235",
                "gameState": "live",
                "home": {"names": {"full": "A"}, "score": "0", "conferences": []},
                "away": {"names": {"full": "B"}, "score": "0", "conferences": []},
            }
        },
    ]
}


def test_division_is_explicit_in_the_url():
    """The whole reason to prefer this over ESPN, whose teams endpoint returns
    all 760 college football programs whatever group filter is passed."""
    assert ncaa_api.SPORT_PATHS["ncaaf"] == ("football", "fbs")
    assert ncaa_api.SPORT_PATHS["ncaam"] == ("basketball-men", "d1")


def test_parse_matches_the_espn_adapter_column_shape():
    """Identical columns mean the same scoring modules work against either source."""
    rows = ncaa_api.parse_scoreboard(PAYLOAD, "ncaaf", date(2025, 11, 15))
    assert len(rows) == 2
    expected = {
        "season", "game_id", "game_date", "season_type", "completed",
        "home_team", "away_team", "home_conference", "away_conference",
        "home_score", "away_score", "notes",
    }
    assert set(rows[0]) == expected


def test_parse_extracts_scores_and_conferences():
    row = ncaa_api.parse_scoreboard(PAYLOAD, "ncaaf", date(2025, 11, 15))[0]
    assert row["home_team"] == "Ohio State Buckeyes"
    assert row["home_score"] == 48.0
    assert row["away_score"] == 10.0
    assert row["home_conference"] == "Big Ten"
    assert row["completed"] is True


def test_unfinished_games_are_marked_incomplete():
    row = ncaa_api.parse_scoreboard(PAYLOAD, "ncaaf", date(2025, 11, 15))[1]
    assert row["completed"] is False


def test_parsed_rows_feed_the_ncaa_scorer():
    """End to end: this source can drive scoring without any translation layer."""
    rows = ncaa_api.parse_scoreboard(PAYLOAD, "ncaaf", date(2025, 11, 15))
    scored = score_football(pd.DataFrame(rows), eligible={"Ohio State Buckeyes", "UCLA Bruins"})
    out = scored.set_index("team")
    assert out.loc["Ohio State Buckeyes", "wins"] == 1
    # +38 in a conference game clears the 13-point bar
    assert out.loc["Ohio State Buckeyes", "big_wins"] == 1
    assert out.loc["Ohio State Buckeyes", "conf_wins"] == 1


def test_empty_payload_is_tolerated():
    assert ncaa_api.parse_scoreboard({}, "ncaaf", date(2025, 11, 15)) == []


# --- name and conference extraction ----------------------------------------

def test_team_name_falls_back_through_the_name_forms():
    """`full` came back blank for football; `short` carried the value."""
    assert ncaa_api._team_name({"names": {"full": "Ohio State Buckeyes"}}) == "Ohio State Buckeyes"
    assert ncaa_api._team_name({"names": {"full": "", "short": "Massachusetts"}}) == "Massachusetts"
    assert ncaa_api._team_name({"names": {"char6": "AKRON"}}) == "AKRON"
    assert ncaa_api._team_name({"names": {}}) == ""


def test_conference_falls_back_through_key_names():
    assert ncaa_api._team_conference({"conferences": [{"conferenceName": "Big Ten"}]}) == "Big Ten"
    assert ncaa_api._team_conference({"conferences": [{"name": "SEC"}]}) == "SEC"
    assert ncaa_api._team_conference({"conference": "ACC"}) == "ACC"
    assert ncaa_api._team_conference({"conferences": []}) == ""


def test_probe_reports_raw_keys_when_extraction_comes_up_empty():
    """A blank name should hand back the payload keys needed to fix it, rather
    than costing another round trip to diagnose."""
    payload = {"games": [{"game": {"gameID": "1", "gameState": "final",
                                   "home": {"names": {"unknownKey": "X"}, "score": "1"},
                                   "away": {"names": {}, "score": "0"}}}]}
    rows = ncaa_api.parse_scoreboard(payload, "ncaaf", date(2025, 11, 15))
    assert rows[0]["home_team"] == ""
    inner = payload["games"][0]["game"]
    assert "names" in inner["home"]


# --- one row per game -----------------------------------------------------

def test_a_game_returned_on_several_dates_counts_once(monkeypatch, capsys):
    """The scoreboard is week-based for some sports, so walking every date
    returns the same game repeatedly. Summed, that multiplies a team's wins and
    point differential by however many days its week spans -- which looks like
    a season in which everyone played eighty games, and raises nowhere."""
    from datetime import date

    from whul.sources import ncaa_api

    game = {
        "season": 2024, "game_id": "555", "game_date": "2024-09-07",
        "season_type": 2, "completed": True,
        "home_team": "Alabama", "away_team": "Georgia",
        "home_conference": "SEC", "away_conference": "SEC",
        "home_score": 38.0, "away_score": 14.0, "notes": "",
    }
    monkeypatch.setattr(ncaa_api, "season_days", lambda l, s: [date(2024, 9, d) for d in range(1, 8)])
    monkeypatch.setattr(ncaa_api, "scoreboard", lambda l, d: {})
    monkeypatch.setattr(ncaa_api, "parse_scoreboard", lambda b, l, d: [dict(game)])

    frame = ncaa_api.load_team_results("ncaam", [2024], verbose=True)
    assert len(frame) == 1
    assert "6 duplicate game rows dropped" in capsys.readouterr().out


def test_two_different_games_both_survive(monkeypatch):
    from datetime import date

    from whul.sources import ncaa_api

    def rows(board, league, day):
        return [{
            "season": 2024, "game_id": f"g{day.day}", "game_date": day.isoformat(),
            "season_type": 2, "completed": True,
            "home_team": "Alabama", "away_team": "Georgia",
            "home_conference": "SEC", "away_conference": "SEC",
            "home_score": 38.0, "away_score": 14.0, "notes": "",
        }]

    monkeypatch.setattr(ncaa_api, "season_days", lambda l, s: [date(2024, 9, 7), date(2024, 9, 14)])
    monkeypatch.setattr(ncaa_api, "scoreboard", lambda l, d: {})
    monkeypatch.setattr(ncaa_api, "parse_scoreboard", rows)
    assert len(ncaa_api.load_team_results("ncaam", [2024], verbose=False)) == 2


def test_a_game_with_no_id_falls_back_to_what_identifies_it(monkeypatch):
    from datetime import date

    from whul.sources import ncaa_api

    game = {
        "season": 2024, "game_id": "", "game_date": "2024-09-07",
        "season_type": 2, "completed": True,
        "home_team": "Alabama", "away_team": "Georgia",
        "home_conference": "SEC", "away_conference": "SEC",
        "home_score": 38.0, "away_score": 14.0, "notes": "",
    }
    monkeypatch.setattr(ncaa_api, "season_days", lambda l, s: [date(2024, 9, d) for d in (7, 8)])
    monkeypatch.setattr(ncaa_api, "scoreboard", lambda l, d: {})
    monkeypatch.setattr(ncaa_api, "parse_scoreboard", lambda b, l, d: [dict(game)])
    assert len(ncaa_api.load_team_results("ncaam", [2024], verbose=False)) == 1


def test_a_season_that_crosses_new_year_is_not_cut_in_half():
    """College football runs August to January. Labelled by the calendar year,
    a thirteen-win season becomes eleven wins in one season and two bowl games
    in the next, and the benchmark is drawn from half-seasons nobody played.

    The whole season is the one that opened in August, so both halves carry the
    opening year -- which is also how ESPN indexes it."""
    from datetime import date

    from whul.sources import ncaa_api

    payload = {"games": [{"game": {
        "gameID": "1",
        "home": {"names": {"short": "Alabama"}, "score": "38", "conferences": []},
        "away": {"names": {"short": "Georgia"}, "score": "14", "conferences": []},
        "gameState": "final",
    }}]}

    autumn = ncaa_api.parse_scoreboard(payload, "ncaaf", date(2024, 11, 9))
    bowls = ncaa_api.parse_scoreboard(payload, "ncaaf", date(2025, 1, 9))
    assert autumn[0]["season"] == bowls[0]["season"] == 2024


def test_a_season_inside_one_calendar_year_is_unchanged():
    from datetime import date

    from whul.sources import ncaa_api

    payload = {"games": [{"game": {
        "gameID": "1",
        "home": {"names": {"short": "LSU"}, "score": "7", "conferences": []},
        "away": {"names": {"short": "Texas"}, "score": "3", "conferences": []},
        "gameState": "final",
    }}]}
    rows = ncaa_api.parse_scoreboard(payload, "ncaabaseball", date(2025, 4, 12))
    assert rows[0]["season"] == 2025


def test_the_live_ncaa_source_reads_espn_not_the_ncaa_api():
    """The NCAA API states the division in its URL, which is why it is the
    historical source -- but for the current season it serves fixtures without
    results: 2026 games come back not completed with no score, while 2024 comes
    back final. ESPN has the results."""
    import pandas as pd

    from whul.benchmark_sources import SOURCES

    for key in ("ncaaf", "ncaam", "ncaaw", "ncaabaseball", "ncaasoftball"):
        assert SOURCES[key].live is not None, key

    _, score = SOURCES["ncaaf"].live()
    games = pd.DataFrame([{
        "season": 2027, "game_id": str(i), "game_date": "2026-08-30",
        "season_type": 2, "completed": True,
        "home_team": "Alabama", "away_team": f"Opponent {i}",
        "home_conference": "SEC", "away_conference": "SEC",
        "home_score": 38.0, "away_score": 14.0, "notes": "",
    } for i in range(4)])

    scored = score(games)
    assert scored.set_index("team").loc["Alabama", "wins"] == 4
    assert set(scored["league"]) == {"NCAAF"}


def test_both_ncaa_sources_score_the_same_rows_the_same_way():
    import pandas as pd

    from whul.benchmark_sources import SOURCES

    games = pd.DataFrame([{
        "season": 2025, "game_id": str(i), "game_date": "2024-11-09",
        "season_type": 2, "completed": True,
        "home_team": "Alabama", "away_team": f"Opponent {i}",
        "home_conference": "SEC", "away_conference": "SEC",
        "home_score": 31.0, "away_score": 17.0, "notes": "",
    } for i in range(6)])

    _, historical = SOURCES["ncaaf"].build()
    _, live = SOURCES["ncaaf"].live()
    # One scorer, two feeds: a season must not be worth more from one of them.
    assert historical(games).equals(live(games))


# --- a pull that comes back short --------------------------------------------

def test_an_overtime_final_is_a_final():
    """The API writes overtime into the state field -- "FINAL(OT)", "Final/2OT".
    Compared for equality against "final", every overtime game is discarded, and
    discarded as though it had never been played: the teams lose the win, the
    margin and the game from their record, and nothing reports it."""
    for state in ("final", "FINAL", "Final/OT", "FINAL(2OT)", "Final "):
        assert ncaa_api._is_final(state), state
    for state in ("", None, "live", "scheduled", "postponed", "cancelled"):
        assert not ncaa_api._is_final(state), state


def test_a_date_that_fails_is_counted_not_swallowed(monkeypatch, capsys):
    """This API is rate limited. A date that 429s is a Saturday of missing
    games, and skipping it silently produces a season nobody played."""
    import requests

    monkeypatch.setattr(ncaa_api, "season_days", lambda l, s: [date(2024, 11, 9)])
    monkeypatch.setattr(ncaa_api, "RETRY_BACKOFF", 0)

    def refuse(league, day):
        response = requests.Response()
        response.status_code = 429
        raise requests.HTTPError("too many requests", response=response)

    monkeypatch.setattr(ncaa_api, "scoreboard", refuse)
    with pytest.raises(ncaa_api.IncompleteSeason, match="HTTP 429"):
        ncaa_api.load_team_results("ncaam", [2024])


def test_a_rate_limit_is_waited_out_before_it_is_counted(monkeypatch):
    """A 429 is a wait, not an answer."""
    import requests

    monkeypatch.setattr(ncaa_api, "RETRY_BACKOFF", 0)
    calls = []

    def flaky(league, day):
        calls.append(day)
        if len(calls) < 3:
            response = requests.Response()
            response.status_code = 429
            raise requests.HTTPError("slow down", response=response)
        return {"games": []}

    monkeypatch.setattr(ncaa_api, "scoreboard", flaky)
    assert ncaa_api._with_retry(lambda: ncaa_api.scoreboard("ncaaf", date(2024, 11, 9))) == {"games": []}
    assert len(calls) == 3


def test_a_404_is_not_retried(monkeypatch):
    """A date with no page is not a date being throttled."""
    import requests

    monkeypatch.setattr(ncaa_api, "RETRY_BACKOFF", 0)
    calls = []

    def missing(league, day):
        calls.append(day)
        response = requests.Response()
        response.status_code = 404
        raise requests.HTTPError("no such date", response=response)

    monkeypatch.setattr(ncaa_api, "scoreboard", missing)
    with pytest.raises(requests.HTTPError):
        ncaa_api._with_retry(lambda: ncaa_api.scoreboard("ncaaf", date(2024, 11, 9)))
    assert len(calls) == 1


def test_a_division_that_is_a_minority_of_who_appears_still_reads_as_full():
    """D1 men's basketball is 364 of the 763 teams that appear in its results.
    A division under half of who shows up puts the median among the visitors,
    where it reads 4 games a team off a pull that is complete -- the same pull
    that reads 29 for women's basketball, which is 55%. The 75th percentile is
    inside the division for every one of these."""
    import pandas as pd

    games = [
        {"season": 2024, "game_date": f"2024-11-{d:02d}",
         "home_team": f"D1-{i}", "away_team": f"D1-{(i + d) % 40}"}
        for i in range(40) for d in range(1, 29)
    ] + [
        # More one-game visitors than there are division teams.
        {"season": 2024, "game_date": "2024-11-01",
         "home_team": f"D1-{i % 40}", "away_team": f"visitor-{i}"}
        for i in range(45)
    ]
    frame = pd.DataFrame(games)
    counts = pd.concat([frame["home_team"], frame["away_team"]]).value_counts()
    assert counts.median() < 10, "the median really does land among the visitors"
    assert counts.quantile(0.75) >= 28


def test_a_divisions_one_game_visitors_do_not_read_as_a_thin_season(capsys):
    """A division's scoreboard carries its opponents too: about a hundred FCS
    teams appear in FBS results having played the single game that put them
    there. Averaged in, a complete season of 13.8 games a team reads as 8.3 --
    a pull that has apparently lost a third of its games. The median ignores
    them, because they are a minority of one-game visitors."""
    import pandas as pd

    games = [
        {"season": 2024, "game_date": f"2024-09-{d:02d}",
         "home_team": f"FBS{i}", "away_team": f"FBS{(i + d) % 40}"}
        for i in range(40) for d in range(1, 13)
    ] + [
        {"season": 2024, "game_date": "2024-09-01",
         "home_team": f"FBS{i}", "away_team": f"FCS{i}"}
        for i in range(30)
    ]
    ncaa_api._report_coverage(pd.DataFrame(games), "ncaaf")
    assert "thin" not in capsys.readouterr().out


def test_a_thin_season_says_so(capsys):
    """A season total looks plausible at almost any size -- there is no number
    of college football games that reads as obviously wrong. Games per team
    does: everyone plays about twelve."""
    frame = pd.DataFrame([
        {"season": 2024, "home_team": f"T{i}", "away_team": f"T{i + 1}"}
        for i in range(40)
    ])
    ncaa_api._report_coverage(frame, "ncaaf")
    out = capsys.readouterr().out
    assert "per team" in out
    assert "thin" in out


def test_a_full_season_is_not_flagged(capsys):
    games = [
        {"season": 2024, "home_team": f"T{i}", "away_team": f"T{j}"}
        for i in range(20) for j in range(20) if i < j
    ]
    ncaa_api._report_coverage(pd.DataFrame(games), "ncaaf")
    assert "thin" not in capsys.readouterr().out


def test_a_game_carries_the_date_it_was_played_not_the_date_requested():
    """One request can answer with a whole week's slate. Stamping the requested
    date on every row loses the real one -- which is what the live start-date
    filter reads, and what tells a January bowl from a November Saturday."""
    payload = {"games": [{"game": {
        "gameID": "1", "startDate": "01-09-2027",
        "home": {"names": {"short": "Ohio State"}, "score": "34",
                 "conferences": [{"conferenceName": "Big Ten"}]},
        "away": {"names": {"short": "Texas"}, "score": "21",
                 "conferences": [{"conferenceName": "SEC"}]},
        "gameState": "final",
    }}]}
    rows = ncaa_api.parse_scoreboard(payload, "ncaaf", date(2027, 1, 5))
    assert rows[0]["game_date"] == "2027-01-09"
    # January belongs to the season that opened the previous August.
    assert rows[0]["season"] == 2026


def test_the_requested_date_is_used_when_the_payload_has_none():
    payload = {"games": [{"game": {
        "gameID": "1",
        "home": {"names": {"short": "A"}, "score": "1", "conferences": []},
        "away": {"names": {"short": "B"}, "score": "0", "conferences": []},
        "gameState": "final",
    }}]}
    rows = ncaa_api.parse_scoreboard(payload, "ncaaf", date(2024, 11, 9))
    assert rows[0]["game_date"] == "2024-11-09"


def test_an_epoch_timestamp_is_read_when_no_date_string_is_given():
    import time as _time

    stamp = int(_time.mktime(date(2024, 11, 9).timetuple()))
    payload = {"games": [{"game": {
        "gameID": "1", "startTimeEpoch": str(stamp),
        "home": {"names": {"short": "A"}, "score": "1", "conferences": []},
        "away": {"names": {"short": "B"}, "score": "0", "conferences": []},
        "gameState": "final",
    }}]}
    rows = ncaa_api.parse_scoreboard(payload, "ncaaf", date(2024, 12, 25))
    assert rows[0]["game_date"] == "2024-11-09"


# --- football is addressed by week, not by date ------------------------------

def test_football_asks_for_a_week_not_a_date():
    """The NCAA's own football URL is /{year}/{week}/all-conf, and this API
    passes the path through. A request for .../2024/11/09/all-conf is read as
    week 11 of 2024 with the "09" discarded -- which is why every date in
    November returned the same 53 games."""
    seen = {}
    original = ncaa_api._get
    try:
        ncaa_api._get = lambda path, cache_key=None: seen.setdefault("path", path) and {}
        ncaa_api.scoreboard_week("ncaaf", 2024, 3)
    finally:
        ncaa_api._get = original
    assert seen["path"] == "/scoreboard/football/fbs/2024/03/all-conf"


def test_a_week_walk_asks_for_every_week_once(monkeypatch):
    """Walking dates asks for the six month-numbers a season contains and gets
    six weeks of a fifteen-week season. Walking weeks asks for each of them."""
    asked = []

    def fake(league, season, week):
        asked.append((season, week))
        return {}

    monkeypatch.setattr(ncaa_api, "scoreboard_week", fake)
    monkeypatch.setattr(ncaa_api, "parse_scoreboard", lambda p, l, d: [])
    ncaa_api.load_team_results("ncaaf", [2024, 2025], verbose=False)

    assert asked == [(s, w) for s in (2024, 2025) for w in ncaa_api.FOOTBALL_WEEKS]
    assert len(asked) == 2 * len(ncaa_api.FOOTBALL_WEEKS)


def test_the_week_range_reaches_the_postseason():
    """Bowls and the playoff sit at the top of the range, and seasons differ in
    how many weeks they ran, so the walk cannot stop at the first empty week
    without cutting the postseason off some seasons and not others."""
    assert max(ncaa_api.FOOTBALL_WEEKS) >= 17
    assert min(ncaa_api.FOOTBALL_WEEKS) == 1


def test_a_week_walk_takes_its_dates_from_the_games(monkeypatch):
    """A week request carries no date, so every row's date and season have to
    come out of the payload. Without that the whole season would be stamped
    with whatever placeholder the request used."""
    payload = {"games": [{"game": {
        "gameID": "1", "startDate": "01/09/2025", "gameState": "final",
        "home": {"names": {"short": "Ohio State"}, "score": "34",
                 "conferences": [{"conferenceName": "Big Ten"}]},
        "away": {"names": {"short": "Texas"}, "score": "21",
                 "conferences": [{"conferenceName": "SEC"}]},
    }}]}
    monkeypatch.setattr(ncaa_api, "scoreboard_week",
                        lambda l, s, w: payload if w == 17 else {})
    frame = ncaa_api.load_team_results("ncaaf", [2024], verbose=False)
    assert frame.loc[0, "game_date"] == "2025-01-09"
    assert frame.loc[0, "season"] == 2024


def test_basketball_is_still_walked_by_date(monkeypatch):
    """Only football is week-indexed. Sending basketball down the week path
    would ask for twenty weeks of a season played over four months."""
    assert "ncaam" not in ncaa_api.WEEK_INDEXED
    assert "ncaaw" not in ncaa_api.WEEK_INDEXED
    assert "ncaaf" in ncaa_api.WEEK_INDEXED
