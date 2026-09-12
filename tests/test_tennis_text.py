"""Results typed by hand, checked before anything is written.

The feed reaches back seven days and the app's database was not to hand, so the
fortnight the ledger missed arrived as text. It comes from the player's point of
view, so a match appears twice -- once as a win, once as a loss -- and the
ledger wants each match once.
"""

from whul.sources import tennis_text


def test_the_same_match_from_both_players_is_one_match():
    """Fils lost to Tsitsipas and Tsitsipas beat Fils. One match."""
    rows, problems = tennis_text.parse(
        "ARTHUR FILS\n"
        "US Open, Grand Slam, R128, L, Stefanos Tsitsipas, 4-6 7-6 6-1 6-4\n"
        "\nSTEFANOS TSITSIPAS\n"
        "US Open, Grand Slam, R128, W, Arthur Fils, 4-6 7-6 6-1 6-4\n")
    assert len(rows) == 1
    assert rows[0]["winner"] == "Stefanos Tsitsipas"
    assert rows[0]["loser"] == "Arthur Fils"
    assert not problems


def test_a_row_with_no_opponent_is_left_out_and_named():
    """The one thing this import must not do is pay a match twice. A blank
    opponent keys differently from the same match arriving named later."""
    rows, problems = tennis_text.parse(
        "ARYNA SABALENKA\nUS Open, Grand Slam, QF, W, 7-6 3-6 7-6\n")
    assert rows == []
    assert len(problems) == 1
    assert "no opponent" in problems[0] and "QF" in problems[0]


def test_an_opponent_run_together_with_the_score_is_still_read():
    """One line had no comma between them: 'Mattia Bellucci 6-0 6-1 6-1'."""
    rows, _ = tennis_text.parse(
        "TAYLOR FRITZ\n"
        "US Open, Grand Slam, R64, W, Mattia Bellucci 6-0 6-1 6-1\n")
    assert rows[0]["loser"] == "Mattia Bellucci"
    assert rows[0]["score"] == "6-0 6-1 6-1"


def test_a_mistyped_tier_is_recovered_from_the_same_tournament():
    """'25p' for 250, and the same tournament is spelled correctly on another
    line -- which is evidence rather than a guess. It is still reported."""
    rows, problems = tennis_text.parse(
        "STEFANOS TSITSIPAS\n"
        "Winston Salem, 250, R32, W, Jenson Brooksby, 6-4 7-6\n"
        "Winston Salem, 25p, R16, L, Aleksandar Kovacevic, 6-3 7-6\n")
    assert len(rows) == 2
    assert {r["category"] for r in rows} == {"250"}
    assert any("25p" in p and "250" in p for p in problems)


def test_a_tier_nothing_names_is_refused_rather_than_guessed():
    rows, problems = tennis_text.parse(
        "SOMEBODY\nMystery Cup, 25p, R32, W, A Player, 6-4 6-4\n")
    assert rows == []
    assert any("no other line names this tournament" in p for p in problems)


def test_a_missing_middle_round_is_named():
    rows, _ = tennis_text.parse(
        "FLAVIO COBOLLI\n"
        "US Open, Grand Slam, R128, W, Francisco Comesana, 6-4 6-4\n"
        "US Open, Grand Slam, R32, L, Alexander Blockx, 6-7 6-3\n")
    found = tennis_text.gaps(rows, {"Flavio Cobolli"})
    assert any("no R64" in line for line in found)


def test_a_run_ending_in_a_win_short_of_the_final_is_named():
    """The quieter fault. Winning a semi-final means playing a final, and
    nothing about the rows themselves says the final is absent."""
    rows, _ = tennis_text.parse(
        "ARYNA SABALENKA\nUS Open, Grand Slam, SF, W, Jessica Pegula, 7-5 6-2\n")
    found = tennis_text.gaps(rows, {"Aryna Sabalenka"})
    assert any("won the SF" in line and "F was played" in line for line in found)


def test_winning_the_final_is_not_a_gap():
    rows, _ = tennis_text.parse(
        "ARYNA SABALENKA\nUS Open, Grand Slam, F, W, Elena Rybakina, 7-5 6-2\n")
    assert tennis_text.gaps(rows, {"Aryna Sabalenka"}) == []


def test_a_date_is_attached_per_tournament():
    rows, _ = tennis_text.parse(
        "TAYLOR FRITZ\nUS Open, Grand Slam, R128, W, Darwin Blanch, 6-3 6-2 6-4\n",
        {"us open": "2026-08-31"})
    assert rows[0]["date"] == "2026-08-31" and rows[0]["season"] == 2026


def test_a_round_the_project_does_not_know_is_refused():
    rows, problems = tennis_text.parse(
        "SOMEBODY\nUS Open, Grand Slam, Round Three, W, A Player, 6-4 6-4\n")
    assert rows == [] and "Round Three" in problems[0]
