"""The date-range probe: does one request return what a span of them does.

A league-season is 304 daily requests and about five minutes, so a range that
works turns an overnight benchmark recompute into a short one. The risk is the
one this project keeps meeting: a feed that *accepts* a parameter and quietly
returns less. A smaller pool is a lower benchmark and every score above it
larger, and nothing raises.

ESPN is unreachable from where this was written, so these test the comparison
-- which is the part that decides -- against payloads shaped like its own.
"""

from datetime import date

import pytest

from whul.sources import espn

START, END = date(2025, 8, 15), date(2025, 8, 17)


def event(home, away, hg, ag, day, completed=True):
    return {
        "date": f"{day}T14:00Z",
        "season": {"slug": "regular-season"},
        "competitions": [{
            "status": {"type": {"completed": completed}},
            "competitors": [
                {"homeAway": "home", "score": str(hg),
                 "team": {"displayName": home}},
                {"homeAway": "away", "score": str(ag),
                 "team": {"displayName": away}},
            ],
        }],
    }


def board(*events):
    return {"leagues": [{"name": "English Premier League"}], "events": list(events)}


WEEKEND = {
    date(2025, 8, 15): board(event("Arsenal", "Everton", 4, 0, "2025-08-15")),
    date(2025, 8, 16): board(event("Chelsea", "Fulham", 1, 1, "2025-08-16")),
    date(2025, 8, 17): board(event("Liverpool", "Brentford", 3, 2, "2025-08-17")),
}


def run(monkeypatch, range_payload, days=None):
    monkeypatch.setattr(
        espn, "scoreboard",
        lambda league, day: (WEEKEND if days is None else days)
                            .get(day, {"events": []}))
    monkeypatch.setattr(espn, "_get", lambda url, params: range_payload)
    return espn.probe_soccer_range("epl", START, END)


def shapes(found):
    return [v for k, v in found.items()
            if isinstance(v, dict) and k.startswith("dates=")]


# --- the baseline -----------------------------------------------------------

def test_the_walk_is_measured_before_anything_is_compared(monkeypatch):
    """One request a day, exactly as production does. It is the thing the
    range has to match, so it is what the probe trusts."""
    found = run(monkeypatch, board())
    assert found["day_by_day_requests"] == 3
    assert found["day_by_day_rows"] == 6          # two sides a match
    assert found["day_by_day_big_wins"] == 1      # Arsenal by four
    assert found["day_by_day_clean_sheets"] == 1  # Arsenal, and only Arsenal


# --- what the range has to prove -------------------------------------------

def test_a_range_that_returns_the_same_matches_reads_as_identical(monkeypatch):
    found = run(monkeypatch, board(
        event("Arsenal", "Everton", 4, 0, "2025-08-15"),
        event("Chelsea", "Fulham", 1, 1, "2025-08-16"),
        event("Liverpool", "Brentford", 3, 2, "2025-08-17"),
    ))
    for report in shapes(found):
        assert report["verdict"].startswith("IDENTICAL")
        assert report["missing"] == 0 and report["extra"] == 0
        assert report["big_wins"] == found["day_by_day_big_wins"]
        assert report["clean_sheets"] == found["day_by_day_clean_sheets"]
        # Three days in one response, not one day repeated.
        assert report["distinct_dates"] == 3


def test_a_capped_range_is_caught_and_named(monkeypatch):
    """The failure this exists for. A feed that returns the first N events of a
    span looks like it worked: fewer matches, a lower benchmark, every score
    above it larger, and nothing raised."""
    found = run(monkeypatch, board(
        event("Arsenal", "Everton", 4, 0, "2025-08-15"),
    ))
    for report in shapes(found):
        assert report["verdict"].startswith("DIFFERENT")
        assert report["missing"] == 4
        assert any("Liverpool" in line for line in report["missing_matches"])


def test_a_score_that_disagrees_is_caught(monkeypatch):
    """What would cost a big win and a clean sheet without losing a match: the
    row is there, and the goals are not what was scored."""
    found = run(monkeypatch, board(
        event("Arsenal", "Everton", 0, 0, "2025-08-15"),
        event("Chelsea", "Fulham", 1, 1, "2025-08-16"),
        event("Liverpool", "Brentford", 3, 2, "2025-08-17"),
    ))
    for report in shapes(found):
        assert report["verdict"].startswith("DIFFERENT")
        assert report["scores_differ"] == 2
        assert report["big_wins"] == 0
        assert any("day-by-day 4-0, range 0-0" in line
                   for line in report["differing_matches"])


def test_every_match_stamped_with_one_date_is_caught(monkeypatch):
    """A single-date request can stamp every row with the date it asked for; a
    range cannot. A row carrying the wrong day is filtered by the wrong season
    start and lands on the wrong line of the daily ledger."""
    found = run(monkeypatch, board(
        event("Arsenal", "Everton", 4, 0, "2025-08-15"),
        event("Chelsea", "Fulham", 1, 1, "2025-08-15"),
        event("Liverpool", "Brentford", 3, 2, "2025-08-15"),
    ))
    for report in shapes(found):
        assert report["distinct_dates"] == 1
        assert report["verdict"].startswith("DIFFERENT")


def test_two_empty_answers_agree_about_nothing(monkeypatch):
    """A quiet fortnight, a blocked host, a season that had not started -- any
    of them would otherwise read as proof the range works, on no evidence."""
    found = run(monkeypatch, board(), days={})
    for report in shapes(found):
        assert report["verdict"].startswith("NO EVIDENCE")


def test_a_shape_the_feed_refuses_is_reported_not_raised(monkeypatch):
    def refuse(url, params):
        raise RuntimeError("400")

    monkeypatch.setattr(espn, "scoreboard", lambda league, day: WEEKEND.get(day, {}))
    monkeypatch.setattr(espn, "_get", refuse)
    found = espn.probe_soccer_range("epl", START, END)
    assert any(isinstance(v, str) and v.startswith("FAILED")
               for k, v in found.items() if k.startswith("dates="))


def test_a_league_with_no_path_says_so():
    found = espn.probe_soccer_range("nosuchleague", START, END)
    assert str(found["path"]).startswith("FAILED")


# --- the report as a file ---------------------------------------------------

def result(monkeypatch, range_payload):
    return run(monkeypatch, range_payload)


def test_the_report_is_written_where_it_can_be_attached(monkeypatch, tmp_path):
    """The terminal is where a verdict is read; the file is what gets sent."""
    from whul import cli

    found = result(monkeypatch, board(
        event("Arsenal", "Everton", 4, 0, "2025-08-15"),
        event("Chelsea", "Fulham", 1, 1, "2025-08-16"),
        event("Liverpool", "Brentford", 3, 2, "2025-08-17"),
    ))
    target = tmp_path / "report.txt"
    assert cli._print_range_probe(found, str(target)) == 0
    written = target.read_text()
    assert "IDENTICAL" in written
    assert "day_by_day_big_wins" in written and "clean_sheets" in written


def test_the_file_carries_every_match_and_the_terminal_a_sample(capsys, monkeypatch, tmp_path):
    """"Eleven matches missing" is a fact and *which* eleven is the diagnosis,
    and a diagnosis that scrolls off the top of a terminal is one nobody sends
    on."""
    from whul import cli

    days = {
        date(2025, 8, 15 + i): board(
            event(f"Home{i}", f"Away{i}", 3, 0, f"2025-08-{15 + i}"))
        for i in range(3)
    }
    found = run(monkeypatch, board(
        event("Home0", "Away0", 3, 0, "2025-08-15")), days=days)
    target = tmp_path / "report.txt"
    cli._print_range_probe(found, str(target))

    shown = capsys.readouterr().out
    written = target.read_text()
    # Four rows missing -- two matches, two sides each.
    assert "missing                4" in written
    for team in ("Home1", "Home2"):
        assert team in written, team
    assert f"Written to {target}" in shown


def test_a_file_that_cannot_be_written_does_not_lose_the_run(capsys, monkeypatch, tmp_path):
    """The terminal copy is still the answer, and this says why there is no
    file rather than leaving somebody looking for one."""
    from whul import cli

    found = result(monkeypatch, board(
        event("Arsenal", "Everton", 4, 0, "2025-08-15"),
        event("Chelsea", "Fulham", 1, 1, "2025-08-16"),
        event("Liverpool", "Brentford", 3, 2, "2025-08-17"),
    ))
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory")
    assert cli._print_range_probe(found, str(blocked / "report.txt")) == 0
    out = capsys.readouterr()
    assert "Could not write" in out.err
    assert "IDENTICAL" in out.out
