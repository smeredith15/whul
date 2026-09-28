"""International results past the end of the hand-kept ledgers, from ESPN.

On 28 September 2026 the men's ledger stopped at 26 August and the women's at
10 June, so the September window scored nothing. These pin the top-up: only
dates after the ledger's last, in the ledger's own shape, with the slug that
answered named and the competitions nobody answered for listed.
"""

from datetime import date

import pandas as pd

from whul.sources import intl_espn, intl_soccer


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
    assert list(men["espn_event"]) == ["1"]
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
    got = intl_soccer.load_matches([2026], verbose=False, board=board)
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

    out = intl_soccer._past_the_ledger(ledger, verbose=False, board=down,
                                       today=date(2026, 9, 28))
    assert len(out) == 1
