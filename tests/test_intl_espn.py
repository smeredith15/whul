"""International results past the end of the hand-kept ledgers, from ESPN.

On 28 September 2026 the men's ledger stopped at 26 August and the women's at
10 June, so the September window scored nothing. These pin the top-up: only
dates after the ledger's last, in the ledger's own shape, with the slug that
answered named and the competitions nobody answered for listed.
"""

from datetime import date

import pandas as pd

import requests

from whul.sources import intl_espn, intl_flashscore, intl_soccer
from whul.store import open_store


def event(eid, day, home, away, hs, as_, done=True, shootout=None):
    def side(name, where, score, pens):
        entry = {"homeAway": where, "team": {"displayName": name, "id": name},
                 "score": str(score)}
        if pens is not None:
            entry["shootoutScore"] = pens
        return entry

    pens = shootout or (None, None)
    return {"id": eid, "date": f"{day}T18:45Z",
            "competitions": [{"status": {"type": {"completed": done}},
                              "competitors": [side(home, "home", hs, pens[0]),
                                              side(away, "away", as_, pens[1])]}]}


BOARDS = {
    "uefa.nations": [event("1", "2026-09-25", "England", "Germany", 2, 1),
                     event("2", "2026-09-28", "Spain", "France", 1, 1, done=False),
                     event("3", "2026-08-20", "Italy", "Spain", 0, 0)],
    "uefa.wnations": [],
    "uefa.nations.w": [event("9", "2026-09-26", "England Women", "USA", 1, 1,
                             shootout=(4, 3))],
}


def board(slug, start, end):
    return {"events": [e for e in BOARDS.get(slug, [])
                       if start.isoformat() <= e["date"][:10] <= end.isoformat()]}


def test_completed_matches_after_the_ledger_come_back_in_its_shape():
    report = intl_espn.Report()
    got = intl_espn.matches({"M": date(2026, 8, 26), "W": date(2026, 6, 10)},
                            date(2026, 9, 28), board=board, report=report)
    men = got[got["gender"] == "M"]
    # The 20 August match is inside the ledger's dates and not asked about;
    # the 28th is not over.
    assert list(men["event"]) == ["espn:1"]
    row = men.iloc[0]
    assert (row["home_team"], row["away_team"], row["home_score"], row["away_score"]) == \
        ("England", "Germany", 2.0, 1.0)
    assert row["tournament"] == "UEFA Nations League"


def test_the_first_slug_that_answers_is_used_and_named():
    report = intl_espn.Report()
    got = intl_espn.matches({"M": date(2026, 8, 26), "W": date(2026, 6, 10)},
                            date(2026, 9, 28), board=board, report=report)
    women = got[got["gender"] == "W"].iloc[0]
    assert report.used["Women's UEFA Women's Nations League"] == "uefa.nations.w"
    # ESPN's spellings become the ledger's, and a shootout names its winner.
    assert (women["home_team"], women["away_team"]) == ("England", "United States")
    assert women["shootout_winner"] == "England"
    assert "Men's CONCACAF Nations League" in report.silent


def test_nothing_is_asked_for_dates_the_ledger_already_has():
    asked = []

    def spy(slug, start, end):
        asked.append((slug, start))
        return {"events": []}

    intl_espn.matches({"M": date(2026, 9, 28), "W": date(2026, 9, 28)},
                      date(2026, 9, 28), board=spy)
    assert asked == []


def test_the_ladder_scores_what_espn_found(monkeypatch):
    ledger = pd.DataFrame([
        {"date": pd.Timestamp("2026-08-26"), "home_team": "Brazil", "away_team": "Chile",
         "home_score": 1, "away_score": 0, "tournament": "Friendly", "city": "",
         "country": "", "neutral": False, "gender": "M", "shootout_winner": None},
        {"date": pd.Timestamp("2026-06-10"), "home_team": "Brazil", "away_team": "Chile",
         "home_score": 1, "away_score": 0, "tournament": "Friendly", "city": "",
         "country": "", "neutral": False, "gender": "W", "shootout_winner": None},
    ])
    monkeypatch.setattr(intl_soccer, "_ledgers", lambda verbose=True: ledger)
    got = intl_soccer.load_matches([2026], verbose=False, board=board, flashscore="")
    mine = got[got["home_team"] == "England"]
    assert set(mine["competition"]) == {"UEFA Nations League",
                                        "UEFA Women's Nations League"}
    assert set(mine["season"]) == {2026} and mine["wanted"].all()


def test_an_unreachable_feed_leaves_the_ledgers_as_they_were(monkeypatch):
    ledger = pd.DataFrame([{
        "date": pd.Timestamp("2026-08-26"), "home_team": "Brazil", "away_team": "Chile",
        "home_score": 1, "away_score": 0, "tournament": "FIFA World Cup qualification",
        "city": "", "country": "", "neutral": False, "gender": "M",
        "shootout_winner": None}])

    def down(slug, start, end):
        raise ConnectionError("proxy said no")

    out = intl_soccer._past_the_ledger(ledger, verbose=False, board=down, flashscore="",
                                       today=date(2026, 9, 28))
    assert len(out) == 1


def refusal(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status}", response=response)


def test_a_refused_slug_is_named_with_its_status():
    def refuses(slug, start, end):
        raise refusal(400)

    report = intl_espn.Report()
    intl_espn.matches({"M": date(2026, 8, 26)}, date(2026, 9, 28), board=refuses,
                      report=report)
    said = report.refused["Men's UEFA Nations League"]
    assert said == ["uefa.nations 400 for a range, 400 for a date"]
    assert "refused -- uefa.nations 400" in "\n".join(report.lines())


def test_a_slug_that_refuses_ranges_is_walked_a_date_at_a_time():
    asked = []

    def dates_only(slug, start, end):
        if slug != "uefa.nations" or start != end:
            raise refusal(400)
        asked.append(start)
        return {"events": [e for e in BOARDS["uefa.nations"]
                           if e["date"][:10] == start.isoformat()]}

    report = intl_espn.Report()
    got = intl_espn.matches({"M": date(2026, 8, 26)}, date(2026, 9, 28),
                            board=dates_only, report=report)
    assert list(got["event"]) == ["espn:1"]
    assert report.used["Men's UEFA Nations League"] == "uefa.nations"
    # Three weeks back from the day asked about, not the whole gap.
    assert min(asked) == date(2026, 9, 8)


# --- Flashscore -------------------------------------------------------------

def stamp(day):
    return int(pd.Timestamp(f"{day} 18:45", tz="UTC").timestamp())


def header(name):
    return f"ZA÷{name}¬ZEE÷x¬"


def result(uid, day, home, away, hs, as_, status="3"):
    code = "1" if hs > as_ else "2" if as_ > hs else "3"
    return (f"AA÷{uid}¬AD÷{stamp(day)}¬AC÷{status}¬AE÷{home}¬AF÷{away}¬"
            f"AS÷{code}¬AG÷{hs}¬AH÷{as_}¬")


FEED = "~".join([
    header("EUROPE: UEFA Nations League - League A"),
    result("f1", "2026-09-25", "England", "Germany", 2, 1),
    result("f2", "2026-09-26", "France", "Spain", 1, 1),
    result("f3", "2026-09-28", "Italy", "Belgium", 0, 0, status="1"),
    header("EUROPE: UEFA Nations League Women - League A"),
    result("f4", "2026-09-26", "England W", "Spain W", 0, 2),
    header("EUROPE: Euro U21 - Qualification"),
    result("f5", "2026-09-25", "England U21", "Serbia U21", 3, 0),
    header("WORLD: Friendly International"),
    result("f6", "2026-09-25", "USA", "Brazil", 1, 0),
    header("WORLD: World Cup Women - Qualification - North & Central America"),
    result("f7", "2026-09-27", "USA W", "Mexico W", 4, 0),
    header("ENGLAND: Premier League"),
    result("f8", "2026-09-27", "Arsenal", "Chelsea", 2, 0),
])


def test_flashscore_keeps_national_sides_under_a_ladder_competition():
    report = intl_flashscore.Report()
    got = intl_flashscore.matches({"M": date(2026, 8, 26), "W": date(2026, 6, 10)},
                                  date(2026, 9, 28), raw=FEED, report=report)
    rows = {(r.gender, r.home_team, r.away_team): r for r in got.itertuples()}
    assert set(rows) == {("M", "England", "Germany"), ("M", "France", "Spain"),
                         ("W", "England", "Spain"), ("W", "United States", "Mexico")}
    assert rows[("M", "England", "Germany")].tournament == "UEFA Nations League"
    assert rows[("W", "England", "Spain")].tournament == "UEFA Nations League"
    assert rows[("W", "United States", "Mexico")].tournament == \
        "FIFA World Cup qualification"
    assert (rows[("M", "France", "Spain")].home_score,
            rows[("M", "France", "Spain")].away_score) == (1.0, 1.0)
    # A friendly is seen and named as not scored; a club match is not national.
    assert report.unscored == {"WORLD: Friendly International": 1}


def test_flashscore_takes_only_dates_after_the_ledger():
    got = intl_flashscore.matches({"M": date(2026, 9, 25), "W": date(2026, 9, 28)},
                                  date(2026, 9, 28), raw=FEED)
    assert list(got["event"]) == ["flashscore:f2"]


def test_a_match_both_feeds_have_is_counted_once():
    got = intl_soccer.one_per_match([
        intl_espn.matches({"M": date(2026, 8, 26), "W": date(2026, 6, 10)},
                          date(2026, 9, 28), board=board),
        intl_flashscore.matches({"M": date(2026, 8, 26), "W": date(2026, 6, 10)},
                                date(2026, 9, 28), raw=FEED),
    ])
    england = got[(got["gender"] == "M") & (got["home_team"] == "England")]
    assert list(england["event"]) == ["espn:1"], "the ESPN copy is the one kept"


def test_what_the_top_up_found_is_kept_after_the_window_forgets_it(monkeypatch):
    ledger = pd.DataFrame([
        {"date": pd.Timestamp(d), "home_team": "Brazil", "away_team": "Chile",
         "home_score": 1, "away_score": 0, "tournament": "Friendly", "city": "",
         "country": "", "neutral": False, "gender": g, "shootout_winner": None}
        for g, d in (("M", "2026-08-26"), ("W", "2026-06-10"))])
    monkeypatch.setattr(intl_soccer, "_ledgers", lambda verbose=True: ledger)
    quiet = lambda slug, start, end: {"events": []}  # noqa: E731
    store = open_store(":memory:")

    first = intl_soccer.load_matches([2026], verbose=False, board=quiet,
                                     store=store, flashscore=FEED)
    later = intl_soccer.load_matches([2026], verbose=False, board=quiet,
                                     store=store, flashscore="")
    for got in (first, later):
        england = got[(got["home_team"] == "England") & (got["gender"] == "M")]
        assert len(england) == 1 and england["wanted"].all()


def test_kept_top_ups_step_aside_once_the_ledger_has_their_dates(monkeypatch):
    store = open_store(":memory:")
    ledger = pd.DataFrame([
        {"date": pd.Timestamp(d), "home_team": "Brazil", "away_team": "Chile",
         "home_score": 1, "away_score": 0, "tournament": "Friendly", "city": "",
         "country": "", "neutral": False, "gender": g, "shootout_winner": None}
        for g, d in (("M", "2026-08-26"), ("W", "2026-06-10"))])
    quiet = lambda slug, start, end: {"events": []}  # noqa: E731
    intl_soccer._past_the_ledger(ledger, verbose=False, board=quiet,
                                 today=date(2026, 9, 28), store=store, flashscore=FEED)
    caught_up = ledger.assign(date=pd.Timestamp("2026-09-27"))
    out = intl_soccer._past_the_ledger(caught_up, verbose=False, board=quiet,
                                       today=date(2026, 9, 28), store=store,
                                       flashscore="")
    assert len(out) == len(caught_up), "a match the ledger now holds came back twice"
