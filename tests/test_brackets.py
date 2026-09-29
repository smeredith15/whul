"""NCAA brackets, worked out from their games, against brackets built by hand."""

import pandas as pd

from whul import brackets as bp


def _acc(labels=("First Round", "Second Round", "Quarterfinal", "Semifinal",
                 "Championship")):
    """A fifteen-team, five-round bracket: seeds 1-4 enter at the quarterfinal,
    5-9 at the second round, 10-15 at the first. The better seed always wins."""
    games = []

    def play(day, a, b, rnd):
        games.append({"season": 2026, "game_id": f"{a}-{b}", "game_date": day,
                      "season_type": 3, "completed": True,
                      "home_team": f"T{a}", "away_team": f"T{b}",
                      "home_score": 80, "away_score": 70,
                      "notes": f"ACC Tournament - {labels[rnd - 1]}"})
        return a

    r1 = [play("2026-03-10", a, b, 1) for a, b in ((10, 15), (11, 14), (12, 13))]
    r2 = [play("2026-03-11", a, b, 2) for a, b in ((5, r1[2]), (6, r1[1]), (7, r1[0]),
                                                   (8, 9))]
    qf = [play("2026-03-12", a, b, 3) for a, b in ((1, r2[3]), (2, r2[2]), (3, r2[1]),
                                                   (4, r2[0]))]
    sf = [play("2026-03-13", qf[0], qf[3], 4), play("2026-03-13", qf[1], qf[2], 4)]
    play("2026-03-14", sf[0], sf[1], 5)
    return pd.DataFrame(games)


def _fetch(frame):
    def schedule(team):
        mine = frame[(frame["home_team"] == team) | (frame["away_team"] == team)]
        return mine.reset_index(drop=True)
    return schedule


def test_a_whole_bracket_is_found_from_one_team():
    found = bp.collect("ncaam", 2026, ["T1"], _fetch(_acc()))
    assert len(found) == 1 and found[0].name == "ACC Tournament"
    assert len(found[0].teams) == 15 and len(found[0].games) == 14


def test_the_rounds_are_worked_out_from_the_games_alone():
    bracket = bp.collect("ncaam", 2026, ["T3"], _fetch(_acc()))[0]
    shape = bp.single_elimination(bracket)
    assert shape.rounds == 5
    assert {shape.entered[f"T{s}"] for s in (1, 2, 3, 4)} == {3}
    assert {shape.entered[f"T{s}"] for s in (5, 6, 7, 8, 9)} == {2}
    assert {shape.entered[f"T{s}"] for s in range(10, 16)} == {1}


def test_standard_labels_need_only_the_round_count():
    lines = bp.report(bp.collect("ncaam", 2026, ["T1"], _fetch(_acc()))[0])
    text = "\n".join(lines)
    assert "reads every team's entry round correctly (15 teams)" in text
    assert "suggested row for next season: 2027,NCAAM,ACC,5," in text


def test_labels_the_scorer_cannot_place_get_round_names():
    odd = ("Opening Round", "First Round", "Quarterfinal", "Semifinal", "Final")
    lines = bp.report(bp.collect("ncaam", 2026, ["T1"], _fetch(_acc(odd)))[0])
    text = "\n".join(lines)
    assert "misreads these" in text
    assert ("2027,NCAAM,ACC,5,Opening Round|First Round|Quarterfinal|Semifinal|Final"
            in text)


def test_a_double_elimination_bracket_is_listed_rather_than_guessed():
    games = pd.DataFrame([
        {"season": 2026, "game_id": str(i), "game_date": f"2026-05-2{i}",
         "season_type": 3, "completed": True, "home_team": h, "away_team": a,
         "home_score": 5, "away_score": 3, "notes": f"SEC Tournament - Game {i}"}
        for i, (h, a) in enumerate((("A", "B"), ("C", "D"), ("B", "D"), ("A", "C"),
                                    ("C", "B"), ("A", "C")), start=1)])
    bracket = bp.collect("ncaabaseball", 2026, ["A"], _fetch(games))[0]
    assert bp.single_elimination(bracket) is None
    text = "\n".join(bp.report(bracket))
    assert "not single elimination" in text and "[Game 1]" in text


def test_the_national_tournaments_are_not_brackets_here():
    assert bp.bracket_of("NCAAM", "Men's Basketball Championship - 1st Round", 3) is None
    assert bp.bracket_of("NCAAM", "National Invitation Tournament - First Round", 3) is None
    assert bp.bracket_of("NCAAM", "Big East Tournament - Quarterfinal", 3) == \
        "Big East Tournament"
    assert bp.bracket_of("NCAAF", "CFP Quarterfinal at the Rose Bowl Game", 3) == bp.CFP
    assert bp.bracket_of("NCAAF", "Big Ten Championship", 3) is None


def test_the_last_finished_season_by_how_each_sport_numbers_it():
    from datetime import date

    today = date(2026, 9, 29)
    assert bp.last_season("ncaam", today) == 2026
    assert bp.last_season("ncaaf", today) == 2025
    assert bp.last_season("ncaabaseball", today) == 2026


def _big12_baseball():
    """Twelve teams, five rounds, and the first three all called
    "Big 12 Tournament" -- as ESPN labelled the 2026 one. West Virginia, the
    top seed, enters in round three."""
    labels = ("", "", "", " - Semifinal", " - Championship")
    games = []

    def play(day, a, b, rnd):
        games.append({"season": 2027, "game_id": f"{a}-{b}", "game_date": day,
                      "season_type": 3, "completed": True,
                      "home_team": a, "away_team": b,
                      "home_score": 6, "away_score": 2,
                      "home_conference": "Big 12", "away_conference": "Big 12",
                      "notes": f"Big 12 Tournament{labels[rnd - 1]}"})
        return a

    r1 = [play("2027-05-19", a, b, 1) for a, b in (("T9", "T12"), ("T10", "T11"))]
    r2 = [play("2027-05-20", a, b, 2) for a, b in (("T7", r1[1]), ("T8", r1[0]))]
    r3 = [play("2027-05-21", a, b, 3) for a, b in (("West Virginia", r2[1]),
                                                   ("T2", r2[0]), ("T3", "T6"),
                                                   ("T4", "T5"))]
    sf = [play("2027-05-22", r3[0], r3[3], 4), play("2027-05-22", r3[1], r3[2], 4)]
    play("2027-05-23", sf[0], sf[1], 5)
    return pd.DataFrame(games)


def test_a_bracket_whose_notes_do_not_name_the_rounds_is_placed_from_its_games():
    from whul.scoring.ncaa import score_diamond

    everything = _big12_baseball()
    mine = _fetch(everything)("West Virginia")
    placed = bp.place_rounds("ncaabaseball", mine, lambda season: _fetch(everything))
    assert placed["bracket_round"].tolist() == [3, 4, 5]
    scored = score_diamond(placed, "NCAA Baseball").set_index("team")
    assert scored.loc["West Virginia", "conf_tourney_byes"] == 2
    # Without the walk the rounds cannot be read, and nothing is paid.
    unplaced = score_diamond(mine, "NCAA Baseball").set_index("team")
    assert unplaced.loc["West Virginia", "conf_tourney_byes"] == 0


def test_a_bracket_that_names_its_rounds_costs_no_requests():
    asked = []

    def schedules(season):
        asked.append(season)
        return _fetch(_acc())

    # T10 plays a first and a second round, both named from the start.
    mine = _fetch(_acc())("T10")
    placed = bp.place_rounds("ncaam", mine, schedules)
    assert asked == [] and placed["bracket_round"].isna().all()


def test_an_unfinished_bracket_is_left_unplaced():
    everything = _big12_baseball()
    unfinished = everything[everything["notes"] != "Big 12 Tournament - Championship"]
    mine = _fetch(unfinished)("West Virginia")
    placed = bp.place_rounds("ncaabaseball", mine, lambda season: _fetch(unfinished))
    assert placed["bracket_round"].isna().all()


def _pac12():
    """Nine teams, as the conference published the 2027 bracket: 8 v 9 on
    day one, then 5 v the play-in winner and 6 v 7, then 3 and 4 enter, then 1
    and 2. Labelled in a way nobody could read, to show the labels are not
    used."""
    games = []

    def play(day, a, b):
        games.append({"season": 2027, "game_id": f"{a}-{b}", "game_date": day,
                      "season_type": 2, "completed": True,
                      "home_team": a, "away_team": b,
                      "home_score": 80, "away_score": 70,
                      "home_conference": "Pac-12", "away_conference": "Pac-12",
                      "notes": "Pac-12 Tournament"
                      + (" - Championship" if day.endswith("13") else "")})
        return a

    p = play("2027-03-09", "S8", "S9")
    r1 = [play("2027-03-10", "S5", p), play("2027-03-10", "S6", "S7")]
    qf = [play("2027-03-11", "S4", r1[0]), play("2027-03-11", "S3", r1[1])]
    sf = [play("2027-03-12", "Gonzaga", qf[0]), play("2027-03-12", "S2", qf[1])]
    play("2027-03-13", sf[0], sf[1])
    return pd.DataFrame(games)


def test_a_one_game_opening_round_is_a_play_in():
    bracket = bp.collect("ncaam", 2027, ["Gonzaga"], _fetch(_pac12()))[0]
    shape = bp.single_elimination(bracket)
    assert shape.rounds == 4
    assert shape.entered["Gonzaga"] == 3 and shape.entered["S3"] == 2
    assert shape.entered["S5"] == 1 and shape.entered["S8"] == 0


def test_a_new_format_is_placed_from_its_bracket_and_pays_the_double_bye():
    from whul.scoring.ncaa import score_basketball

    everything = _pac12()
    mine = _fetch(everything)("Gonzaga")
    placed = bp.place_rounds("ncaam", mine, lambda season: _fetch(everything))
    assert placed["bracket_round"].tolist() == [3, 4]
    scored = score_basketball(placed, "NCAAM").set_index("team")
    assert scored.loc["Gonzaga", "conf_tourney_byes"] == 2


def test_a_benchmark_places_rounds_from_the_rows_it_already_has():
    """Every team in the division is in hand, so no request is made."""
    everything = _big12_baseball()
    placed = bp.place_rounds("ncaabaseball", everything, bp.local_schedules(everything))
    wv = placed[(placed["home_team"] == "West Virginia")
                | (placed["away_team"] == "West Virginia")]
    assert wv["bracket_round"].tolist() == [3, 4, 5]
