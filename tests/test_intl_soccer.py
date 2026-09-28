"""International soccer: the ladder, the ceiling, the weighting and the lift.

Every case here is a wrong answer that would have looked right -- a plausible
number, returned without an error, for a season it does not describe.
"""

from datetime import date

import pandas as pd
import pytest

from whul.config.league import league_year
from whul.scoring import intl_soccer as scorer


def match(home, away, hs, aa, competition="FIFA World Cup", rung="world",
          kind="finals", day="2026-06-15", season=2025, gender="M",
          shootout=None, phase="block"):
    return {
        "date": pd.Timestamp(day), "season": season, "gender": gender,
        "competition": competition, "rung": rung, "kind": kind, "phase": phase,
        "home_team": home, "away_team": away,
        "home_score": hs, "away_score": aa, "shootout_winner": shootout,
    }


def points(rows):
    """Per-team match points before the ladder, keyed by team."""
    scored = scorer.per_team(pd.DataFrame(rows))
    return dict(zip(scored["team"], scored["base"]))


# --- a match is scored exactly as a club match is ---------------------------

def test_the_outcome_table_is_the_club_one():
    got = points([match("A", "B", 1, 0)])
    # 3 for the win, 1 for the clean sheet; the margin is one so no big-win.
    assert got["A"] == 4 and got["B"] == 0


def test_winning_by_two_or_more_and_to_nil_is_the_maximum():
    got = points([match("A", "B", 2, 0)])
    assert got["A"] == scorer.MATCH_MAX, "a win by two to nil is the ceiling"


def test_a_goalless_draw_pays_both_sides_for_the_clean_sheet():
    got = points([match("A", "B", 0, 0)])
    assert got["A"] == 2 and got["B"] == 2, "one for the draw, one for the sheet"


def test_a_shootout_win_takes_no_margin_bonus():
    """The match itself was drawn, so there is no margin to be big. The clean
    sheet still applies -- neither side conceded in normal time."""
    got = points([match("A", "B", 0, 0, shootout="A")])
    assert got["A"] == 3, "two for the shootout win, one for the clean sheet"
    assert got["B"] == 2, "a shootout loss is a draw plus the sheet"


def test_a_shootout_is_only_consulted_on_a_level_score():
    """A feed that folded a shootout into the score can misread a tie; it must
    not be able to turn a 3-2 into anything else."""
    got = points([match("A", "B", 3, 2, shootout="B")])
    assert got["A"] == 3 and got["B"] == 0, "a decided match cannot go to penalties"


# --- the ceiling is a ceiling ----------------------------------------------

def _run(wins, competition="CONCACAF Gold Cup", rung="federation", n_group=3):
    """A champion's perfect run: every match won by two to nil."""
    rows = []
    for i in range(wins):
        rows.append(match("Champ", f"Foe{i}", 2, 0, competition=competition,
                          rung=rung, day=f"2026-06-{10 + i:02d}"))
    # A side eliminated in the group stage, so the shape can be inferred.
    for i in range(n_group):
        rows.append(match("Early", f"Foe{i}", 0, 1, competition=competition,
                          rung=rung, day=f"2026-06-{10 + i:02d}"))
    return pd.DataFrame(rows)


def _ceiling(rung):
    return scorer.RUNG[rung] * scorer.SCALE


def test_a_perfect_run_pays_exactly_its_rung_and_the_title_on_top():
    """Winning every match by two to nil is the ceiling, and nothing can exceed
    its own competition -- except by winning it, which adds a quarter."""
    out = scorer.score_teams(_run(6)).set_index("team")
    assert out.loc["Champ", "gross"] == pytest.approx(
        _ceiling("federation") * (1 + scorer.TITLE_BONUS))
    # A continental year with nothing bigger is doubled, so its champion is on
    # the World Cup's scale.
    assert out.loc["Champ", "lift"] == pytest.approx(2.0)


def test_a_champion_is_only_the_team_that_won_every_knockout():
    """A semi-final loser who wins the third-place match has played as many
    knockout matches as the champion. Counting knockout wins alone paid it."""
    rows = []
    day = 1
    # Two groups of four; the second group's sides go out after three matches,
    # which is what tells the scorer where the group ends.
    for group in (["A", "B", "C", "D"], ["E", "F", "G", "H"]):
        for i, home in enumerate(group):
            for away in group[i + 1:]:
                rows.append(match(home, away, 1, 0, competition="Copa América",
                                  rung="federation", day=f"2026-06-{day:02d}"))
                day += 1
    rows += [
        match("A", "D", 2, 0, competition="Copa América", rung="federation", day="2026-06-20"),
        match("B", "C", 0, 1, competition="Copa América", rung="federation", day="2026-06-20"),
        match("D", "B", 0, 1, competition="Copa América", rung="federation", day="2026-06-25"),
        match("A", "C", 1, 0, competition="Copa América", rung="federation", day="2026-06-25"),
    ]
    rows = scorer.per_team(pd.DataFrame(rows))
    rows["edition"] = scorer._editions(rows)
    priced = scorer._price(rows, scorer._shape(rows))
    titled = set(priced.loc[priced["title"] > 0, "team"])
    assert titled == {"A"}, f"titles went to {sorted(titled)}"


def test_two_formats_of_the_same_rung_are_worth_the_same():
    """Six matches to win the Gold Cup and seven to win AFCON. The rung is a
    rung, so the extra match must not pay extra -- which is the whole reason
    the ceiling is divided by the champion's own path."""
    short = scorer.score_teams(_run(6)).set_index("team").loc["Champ", "folded"]
    long = scorer.score_teams(
        _run(7, competition="Africa Cup of Nations")
    ).set_index("team").loc["Champ", "folded"]
    assert short == pytest.approx(long)


def test_the_lift_puts_each_years_biggest_competition_on_one_scale():
    def won(competition, rung):
        return scorer.score_teams(
            _run(6, competition=competition, rung=rung)
        ).set_index("team").loc["Champ", "total_points"]

    # A World Cup winner in a World Cup year and a Gold Cup winner in a
    # continental year are the same achievement for the year they were in.
    assert won("FIFA World Cup", "world") == pytest.approx(won("CONCACAF Gold Cup", "federation"))
    # A Nations League pays no title, so its perfect run is the ceiling alone.
    assert won("UEFA Nations League", "nations_league") == pytest.approx(
        max(scorer.RUNG.values()) * scorer.SCALE)


def test_every_competition_after_the_first_counts_a_quarter():
    """Summing them put one two-trophy year at twice the 99th percentile, and
    half let a runner-up's busy year beat the champion's."""
    three = pd.concat([
        _run(6, "FIFA World Cup", "world"),
        _run(6, "CONCACAF Gold Cup", "federation"),
        _run(6, "UEFA Nations League", "nations_league"),
    ])
    out = scorer.score_teams(three).set_index("team")
    bonus = 1 + scorer.TITLE_BONUS
    expected = (_ceiling("world") * bonus
                + scorer.SECONDARY_SHARE * _ceiling("federation") * bonus
                + scorer.SECONDARY_SHARE * _ceiling("nations_league"))
    assert out.loc["Champ", "total_points"] == pytest.approx(expected)
    assert out.loc["Champ", "competitions"] == 3


def test_the_first_competition_below_the_top_rung_counts_by_its_rung():
    """A Nations League champion in a year with a continental championship
    keeps two thirds of it, not a quarter: a team that won a whole competition
    is worth more than a rounding error."""
    both = pd.concat([
        _run(6, "CONCACAF Gold Cup", "federation"),
        _run(6, "UEFA Nations League", "nations_league").replace(
            {"Champ": "Other", "Early": "Late"}),
    ])
    out = scorer.score_teams(both).set_index("team")
    weight = scorer.RUNG["nations_league"] / scorer.RUNG["federation"]
    assert out.loc["Other", "folded"] == pytest.approx(_ceiling("nations_league") * weight)


# --- the lift ---------------------------------------------------------------

def test_a_year_is_lifted_by_the_biggest_competition_in_it():
    """A Nations League year would otherwise score a third of a World Cup year
    for reasons of the calendar alone."""
    for competition, rung, lift in (("UEFA Nations League", "nations_league", 3.0),
                                    ("CONCACAF Gold Cup", "federation", 2.0),
                                    ("FIFA World Cup", "world", 1.0)):
        out = scorer.score_teams(_run(6, competition, rung)).set_index("team")
        assert out.loc["Champ", "lift"] == pytest.approx(lift), competition


# --- which league year a match scores in ------------------------------------

def test_the_league_year_opens_in_mid_july():
    assert league_year(date(2024, 7, 14)) == 2024
    assert league_year(date(2024, 7, 13)) == 2023


def test_a_block_tournament_is_not_split_by_the_league_year():
    """Euro 2024 ran 14 June to 14 July and the year turns on the 14th. By
    match date alone the final would score in a different year from the group
    stage, which is the case the rule exists for."""
    from whul.sources.intl_soccer import _assign_season

    rows = pd.DataFrame([
        match("A", "B", 1, 0, day="2024-06-14", phase="block"),
        match("A", "C", 1, 0, day="2024-07-14", phase="block"),
    ])
    out = _assign_season(rows)
    assert list(out["season"]) == [2023, 2023]


def test_a_windowed_phase_scores_where_it_was_played():
    """Qualifying campaigns and Nations League phases run across windows months
    apart and line up with no league year reliably."""
    from whul.sources.intl_soccer import _assign_season

    rows = pd.DataFrame([
        match("A", "B", 1, 0, day="2024-06-14", phase="windows"),
        match("A", "C", 1, 0, day="2024-07-14", phase="windows"),
    ])
    assert list(_assign_season(rows)["season"]) == [2023, 2024]


def test_two_stagings_a_year_apart_are_not_one_block():
    from whul.sources.intl_soccer import _assign_season

    rows = pd.DataFrame([
        match("A", "B", 1, 0, day="2024-06-14", phase="block"),
        match("A", "C", 1, 0, day="2026-06-14", phase="block"),
    ])
    assert list(_assign_season(rows)["season"]) == [2023, 2025]


# --- the data files ---------------------------------------------------------

def test_the_ladder_is_an_allow_list_and_covers_the_rostered_confederations():
    """A regex written against history keeps matching history and drops the
    season being played -- the women's African championship changed spelling in
    2025 and the old pattern stopped matching it."""
    ladder = pd.read_csv(scorer.EDITIONS.parent / "intl_tournaments.csv", comment="#")
    assert set(ladder["rung"]) <= set(scorer.RUNG)
    assert set(ladder["kind"]) == {"finals", "qualifying"}
    assert set(ladder["phase"]) == {"block", "windows"}
    # Qualifying is played across windows whatever the competition.
    quals = ladder[ladder["kind"] == "qualifying"]
    assert set(quals["phase"]) == {"windows"}
    # Both spellings of the African championship, which is the case that bit.
    names = set(ladder["tournament"])
    assert {"African Cup of Nations qualification",
            "Africa Cup of Nations qualification"} <= names


def test_every_stated_edition_names_a_competition_the_ladder_knows():
    """A row keyed on the ledger's spelling rather than the ladder's matches
    nothing and silently does nothing. Two did."""
    ladder = pd.read_csv(scorer.EDITIONS.parent / "intl_tournaments.csv", comment="#")
    stated = pd.read_csv(scorer.EDITIONS, comment="#")
    known = set(zip(ladder["gender"], ladder["competition"]))
    unknown = [(g, c) for g, c in zip(stated["gender"], stated["competition"])
               if (g, c) not in known]
    assert not unknown, f"stated editions nothing can match: {unknown}"


def test_the_supplement_is_shaped_like_the_ledger():
    from whul.sources.intl_soccer import SUPPLEMENT

    extra = pd.read_csv(SUPPLEMENT, comment="#")
    assert {"date", "home_team", "away_team", "home_score", "away_score",
            "tournament", "gender", "shootout_winner"} <= set(extra.columns)
    ladder = pd.read_csv(scorer.EDITIONS.parent / "intl_tournaments.csv", comment="#")
    known = set(zip(ladder["gender"], ladder["tournament"]))
    for gender, tournament in zip(extra["gender"], extra["tournament"]):
        assert (gender, tournament) in known, \
            f"supplement carries {tournament} which the ladder does not score"


def test_a_single_season_pull_still_knows_each_tournament_s_shape():
    """The bug this guards: a tournament's shape is read off the edition that
    was played, so a pull for one league year alone has nothing to read. The
    2026 Women's Africa Cup of Nations came out with no group stage and no
    knockout, its qualifiers became the whole competition, and Ghana topped the
    women's board on two won matches at a full ceiling.

    So the loader returns the whole history with the asked-for years marked,
    and the scorer drops the rest once it has the shapes."""
    played = pd.DataFrame([
        # An edition that was played, in an earlier league year: three group
        # matches and a three-round knockout.
        *[match("Champ", f"F{i}", 2, 0, competition="Women's Africa Cup of Nations",
                rung="federation", gender="W", season=2024,
                day=f"2025-06-{10 + i:02d}") for i in range(6)],
        *[match("Early", f"F{i}", 0, 1, competition="Women's Africa Cup of Nations",
                rung="federation", gender="W", season=2024,
                day=f"2025-06-{10 + i:02d}") for i in range(3)],
        # ...and two qualifiers for the next one, in the year being pulled.
        *[match("Ghana", f"Q{i}", 2, 0, competition="Women's Africa Cup of Nations",
                rung="federation", kind="qualifying", phase="windows",
                gender="W", season=2025, day=f"2025-10-{10 + i:02d}")
          for i in range(2)],
    ])
    played["wanted"] = played["season"] == 2025

    out = scorer.score_teams(played)
    assert set(out["season"]) == {2025}, "seasons outside the ask are dropped"
    ghana = out.set_index("team").loc["Ghana", "gross"]
    # Qualifying is its own competition on the Nations League rung, whatever
    # it leads to: two won qualifiers are a perfect campaign of that, never a
    # continental title.
    assert ghana == pytest.approx(_ceiling("nations_league"))


def test_a_short_campaign_is_measured_against_the_typical_one():
    """Panama's women reached the 2023 World Cup through a two-match play-off,
    and on its own length that play-off paid a whole ceiling."""
    def qualifier(home, away, hs, as_, day):
        return match(home, away, hs, as_, competition="FIFA Women's World Cup",
                     kind="qualifying", phase="windows", gender="W", day=day)

    # The play-off: two matches for Panama and for Haiti.
    rows = [qualifier("Panama", "Haiti", 2, 0, "2025-10-10"),
            qualifier("Haiti", "Panama", 0, 2, "2025-10-14")]
    # A qualifying group whose three sides play each other four times: eight
    # matches each, which is the typical campaign.
    group = ("Spain", "England", "France")
    for n in range(4):
        for i, home in enumerate(group):
            for away in group[i + 1:]:
                rows.append(qualifier(home, away, 1, 1, f"2025-11-{10 + n:02d}"))
    out = scorer.score_teams(pd.DataFrame(rows)).set_index("team")
    assert out.loc["Panama", "gross"] == pytest.approx(_ceiling("nations_league") * 2 / 8)


def test_a_national_team_carries_its_whole_record_not_only_its_wins():
    """Unscored, and counted. A side that played four and won one drew or lost
    the other three, and "Wins 1" cannot say which -- nor can it separate a
    shootout from the draw it came out of, which the club vocabulary already
    keeps apart because the match itself was drawn."""
    out = scorer.score_teams(pd.DataFrame([
        match("A", "B", 2, 0),
        match("A", "B", 1, 1),
        match("A", "B", 0, 0, shootout="A"),
        match("A", "B", 0, 1),
    ])).set_index("team")

    assert (out.loc["A", "wins"], out.loc["A", "draws"],
            out.loc["A", "shootout_wins"], out.loc["A", "shootout_losses"],
            out.loc["A", "losses"]) == (1, 1, 1, 0, 1)
    assert (out.loc["B", "wins"], out.loc["B", "draws"],
            out.loc["B", "shootout_wins"], out.loc["B", "shootout_losses"],
            out.loc["B", "losses"]) == (1, 1, 0, 1, 1)
    assert out.loc["A", "matches"] == 4


# --- where a score came from ------------------------------------------------

BOX_PARTS = ("wins", "draws", "losses", "shootout_wins", "shootout_losses",
             "big_margins", "clean_sheets", "title")


def _campaign():
    """One team's year: World Cup qualifying, the finals, and a Nations League."""
    rows = [
        # World Cup qualifying, then the finals: two competitions.
        match("Spain", "Malta", 4, 0, kind="qualifying", day="2025-09-05"),
        match("Norway", "Spain", 0, 2, kind="qualifying", day="2025-09-08"),
        match("Spain", "Brazil", 2, 1, day="2026-06-15"),
        match("Spain", "Japan", 1, 1, day="2026-06-20"),
        match("Spain", "Mexico", 3, 0, day="2026-06-25"),
        match("Spain", "France", 1, 1, day="2026-07-01", shootout="Spain"),
        # A second competition, on a lower rung.
        match("Spain", "Italy", 2, 0, competition="UEFA Nations League",
              rung="nations_league", day="2025-10-10"),
        match("Spain", "Denmark", 0, 1, competition="UEFA Nations League",
              rung="nations_league", day="2025-10-13"),
    ]
    return scorer.score_teams(pd.DataFrame(rows))


def test_a_section_adds_up_to_itself_and_the_sections_add_up_to_the_score():
    """The whole reason the panel is laid out this way. A club's competitions
    sum to its season and a national team's do not -- the best one counts
    whole, every other at half, and the year is lifted afterwards -- so a
    section carries what it earned *and* what that became, and only the second
    of those adds up to the score."""
    scored = _campaign()
    mine = scored[scored["team"] == "Spain"].iloc[0]
    sections = mine["sections"]
    assert len(sections) == 3

    for section in sections:
        boxes = sum(section[f"pts_{part}"] for part in BOX_PARTS)
        assert round(boxes, 1) == pytest.approx(section["points"], abs=0.2), (
            f"{section['name']}'s boxes come to {boxes} and the section says "
            f"{section['points']}")

    counted = sum(s["counted"] for s in sections)
    assert counted == pytest.approx(mine["total_points"], abs=0.2)
    # And the two numbers really are different, or the test proves nothing.
    assert sum(s["points"] for s in sections) != pytest.approx(
        mine["total_points"], abs=0.2)


def test_qualifying_is_a_competition_of_its_own():
    """Priced on the Nations League rung and shown as its own section."""
    mine = _campaign()
    row = mine[mine["team"] == "Spain"].iloc[0]
    names = {s["name"]: s for s in row["sections"]}
    assert set(names) == {"FIFA World Cup", "FIFA World Cup qualifying",
                          "UEFA Nations League"}
    assert names["FIFA World Cup"]["matches"] == 4
    assert names["FIFA World Cup qualifying"]["matches"] == 2
    assert names["FIFA World Cup qualifying"]["rung"] == "Qualifying"


def test_the_first_competition_is_the_one_shown_first():
    """A panel is read top down and the year's answer is the first thing on
    it -- and it is also the one competition counted whole."""
    row = _campaign()
    mine = row[row["team"] == "Spain"].iloc[0]
    assert mine["sections"][0]["name"] == "FIFA World Cup"
    best = mine["sections"][0]
    assert best["counted"] == pytest.approx(best["points"] * mine["lift"], abs=0.2)
    for rest in mine["sections"][1:]:
        assert rest["counted"] == pytest.approx(
            rest["points"] * scorer.SECONDARY_SHARE * mine["lift"], abs=0.2)


# --- the history is evidence, not output ------------------------------------

def _two_seasons(wanted):
    """One league year asked for, one carried only so a shape can be read."""
    rows = []
    for season, day in ((2025, "2025-06-1"), (2026, "2026-06-1")):
        for i in range(4):
            rows.append(match("Champ", f"Foe{i}", 2, 0, competition="CONCACAF Gold Cup",
                              rung="federation", day=f"{day}{i}", season=season))
        for i in range(3):
            rows.append(match("Early", f"Foe{i}", 0, 1, competition="CONCACAF Gold Cup",
                              rung="federation", day=f"{day}{i}", season=season))
    frame = pd.DataFrame(rows)
    frame["wanted"] = frame["season"] == wanted
    return frame


def test_sections_are_built_only_for_the_seasons_that_survive(monkeypatch):
    """The ledger runs to 1872 and the score is narrowed to one league year,
    so sections for every other year are computed, never read, and thrown
    away. It was four minutes of a six-minute pull -- 27,424 calls into
    `_counted` to answer about a few dozen team-seasons."""
    seen = {}
    real = scorer.season_sections

    def watched(rows, shares):
        seen["seasons"] = set(rows["season"])
        return real(rows, shares)

    monkeypatch.setattr(scorer, "season_sections", watched)
    scorer.score_teams(_two_seasons(wanted=2026))

    assert seen["seasons"] == {2026}, (
        f"sections were built for {sorted(seen['seasons'])}, and only 2026 is "
        f"ever read back"
    )


def test_narrowing_the_sections_does_not_change_them():
    """The saving is only worth having if the answer is the same one. A
    section is read by the season it belongs to and nothing in building one
    consults another year -- the shape inference that does has already run by
    this point."""
    games = _two_seasons(wanted=2026)

    narrowed = scorer.score_teams(games)

    # The same pull with nothing held back: every season wanted, then cut down
    # to 2026 afterwards. If the narrowing were unsound these would differ.
    whole = games.assign(wanted=True)
    full = scorer.score_teams(whole)
    full = full[full["season"] == 2026].reset_index(drop=True)

    assert list(narrowed["team"]) == list(full["team"])
    for one, two in zip(narrowed["sections"], full["sections"]):
        assert one == two


# --- the year's lift and the edition in progress ---------------------------

def _classified(rows):
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def _match(day, season, gender, home, away, hs, as_, competition, rung, kind):
    return {"date": day, "season": season, "gender": gender, "home_team": home,
            "away_team": away, "home_score": hs, "away_score": as_,
            "competition": competition, "rung": rung, "kind": kind,
            "phase": "windows", "shootout_winner": None}


def test_the_lift_is_the_years_and_either_gender_sets_it():
    """A men's Nations League year is not lifted when the women play a World
    Cup in it: the year's biggest competition sets one scale for all."""
    rows = [
        _match("2019-09-05", 2019, "M", "Spain", "Italy", 2, 0,
               "UEFA Nations League", "nations_league", "finals"),
        _match("2019-06-10", 2018, "W", "Spain", "Italy", 1, 0,
               "FIFA Women's World Cup", "world", "finals"),
        _match("2018-09-05", 2018, "M", "Spain", "Italy", 2, 0,
               "UEFA Nations League", "nations_league", "finals"),
    ]
    scored = scorer.score_teams(_classified(rows))
    men = scored[(scored["league"] == "Men's Intl Soccer")
                 & (scored["team"] == "Spain")].set_index("season")
    assert men.loc[2018, "lift"] == 1.0
    # A year holding nothing bigger than a Nations League, for anyone.
    assert men.loc[2019, "lift"] == pytest.approx(3.0)


def test_the_calendar_sets_the_lift_before_the_tournament_is_played():
    """2026-27 holds the Women's World Cup from June 2027; in September it is
    already a World Cup year."""
    rows = [_match("2026-09-25", 2026, "M", "Turkey", "France", 0, 1,
                   "UEFA Nations League", "nations_league", "finals")]
    scored = scorer.score_teams(_classified(rows))
    assert scored.set_index("team").loc["France", "lift"] == 1.0


def test_an_edition_in_progress_is_not_sized_by_its_first_matchday():
    """One matchday of a long league phase is one match of it, not a one-match
    competition: the last completed edition supplies the shape."""
    rows = []
    teams = ["A", "B", "C", "D"]
    day = 1
    # The completed edition: every team plays three group matches, and the
    # champion two knockouts beyond them.
    for i, home in enumerate(teams):
        for away in teams[i + 1:]:
            rows.append(_match(f"2024-09-{day:02d}", 2024, "M", home, away, 1, 0,
                               "Test League", "nations_league", "finals"))
            day += 1
    rows.append(_match("2025-03-01", 2024, "M", "A", "B", 1, 0,
                       "Test League", "nations_league", "finals"))
    rows.append(_match("2025-03-05", 2024, "M", "A", "C", 1, 0,
                       "Test League", "nations_league", "finals"))
    # The new edition, one matchday in.
    rows.append(_match("2026-09-25", 2026, "M", "A", "B", 1, 0,
                       "Test League", "nations_league", "finals"))
    rows.append(_match("2026-09-25", 2026, "M", "C", "D", 1, 0,
                       "Test League", "nations_league", "finals"))
    scored = scorer.score_teams(_classified(rows))
    now = scored[scored["season"] == 2026].set_index("team")
    # A 1-0 win is 3 for the win and 1 for the clean sheet, at the group
    # stage's weight, against a path of 3 group matches and 2 knockouts.
    path = scorer.MATCH_MAX * (3 * scorer.STAGE["group"] + 2 * scorer.STAGE["knockout"])
    assert now.loc["A", "gross"] == pytest.approx(
        _ceiling("nations_league") * scorer.STAGE["group"] * 4 / path)
