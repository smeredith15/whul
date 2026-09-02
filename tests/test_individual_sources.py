"""Source adapters for the individual sports.

The feeds themselves cannot be reached from here, so these exercise the parsing
against recorded response shapes -- which is where the adapters have actually
broken before (a value nested one level deeper than the code looked).
"""

import json

import pandas as pd

from whul.sources import espn_individual as espn_ind
from whul.sources import flashscore, jolpica, sackmann
from whul.sources import tennis_calendar as calendar
from whul.sources import tour_schedule as schedule


# --- ESPN golf and racing --------------------------------------------------

def test_the_field_is_found_under_competitions():
    payload = {"competitions": [{"competitors": [{"athlete": {"displayName": "Rory McIlroy"}}]}]}
    assert len(espn_ind._competitors(payload)) == 1


def test_the_field_is_found_under_a_top_level_leaderboard():
    """Golf summaries have shipped the field under 'leaderboard' as well as
    'competitions'; reading only one location loses the other outright."""
    payload = {"leaderboard": [{"competitors": [{"athlete": {"displayName": "Rory McIlroy"}}]}]}
    assert len(espn_ind._competitors(payload)) == 1


def test_the_field_is_found_under_the_header():
    payload = {"header": {"competitions": [{"competitors": [{"athlete": {"name": "Kyle Larson"}}]}]}}
    assert len(espn_ind._competitors(payload)) == 1


def test_an_empty_response_yields_no_field_rather_than_raising():
    assert espn_ind._competitors({}) == []
    assert espn_ind._competitors({"competitions": [{}]}) == []


def test_the_athlete_name_is_read_from_whichever_key_carries_it():
    assert espn_ind._athlete_name({"athlete": {"displayName": "A"}}) == "A"
    assert espn_ind._athlete_name({"athlete": {"fullName": "B"}}) == "B"
    assert espn_ind._athlete_name({"displayName": "C"}) == "C"
    assert espn_ind._athlete_name({}) == ""


def test_a_tie_keeps_its_t_prefix_out_of_the_adapter():
    """Scoring decides what a tie is worth; collapsing 'T12' to 12 here would
    lose the distinction before anything can use it."""
    entry = {"status": {"position": {"displayName": "T12"}}}
    assert espn_ind._position(entry) == "T12"


def test_a_missed_cut_comes_through_as_a_status_not_a_place():
    entry = {"status": {"type": {"shortDetail": "CUT"}}}
    assert espn_ind._position(entry) == "CUT"


def test_the_position_falls_back_to_finishing_order():
    assert espn_ind._position({"order": 3}) == "3"


def test_only_completed_events_are_scored():
    """A Thursday leaderboard would credit the first-round leader with a win."""
    assert espn_ind._is_final({"status": {"type": {"completed": True}}}) is True
    assert espn_ind._is_final({"status": {"type": {"completed": False}}}) is False
    assert espn_ind._is_final({}) is False


def test_season_shapes_are_tried_most_specific_first():
    variants = espn_ind.scoreboard_variants(2025)
    assert variants[0] == {"dates": "2025", "limit": 200}
    assert {"dates": "2025"} in variants
    assert any("-" in v["dates"] for v in variants)


def test_every_registered_league_has_a_path():
    for league in ("pga", "nascar", "f1"):
        sport, path = espn_ind.LEAGUE_PATHS[league]
        assert sport and path


def test_golf_results_flow_into_scoring(monkeypatch):
    """End to end on the parsing side: a recorded ESPN shape has to reach the
    scorer as the columns it expects."""
    from whul.scoring import golf

    event = {"id": "401", "name": "Masters Tournament", "date": "2026-04-12T18:00Z",
             "status": {"type": {"completed": True}}}
    summary = {"competitions": [{"competitors": [
        {"athlete": {"displayName": "Winner"}, "status": {"position": {"displayName": "1"}}},
        {"athlete": {"displayName": "Runner Up"}, "status": {"position": {"displayName": "T2"}}},
        {"athlete": {"displayName": "Missed"}, "status": {"type": {"shortDetail": "CUT"}}},
    ]}]}
    monkeypatch.setattr(espn_ind, "season_events", lambda league, season: [event])
    monkeypatch.setattr(espn_ind, "event_summary", lambda league, event_id: summary)

    raw = espn_ind.load_results("pga", [2026], verbose=False)
    assert list(raw["player"]) == ["Winner", "Runner Up", "Missed"]

    scored = golf.score_players(raw, min_events=1)
    assert scored.set_index("player").loc["Winner", "total_points"] == 500 * golf.MAJOR_MULTIPLIER
    # The missed cut has no position, so it never reaches the totals.
    assert "Missed" not in set(scored["player"])


# --- Jolpica / Ergast ------------------------------------------------------

def test_races_and_totals_are_read_from_the_ergast_envelope():
    payload = {"MRData": {"total": "480", "RaceTable": {"Races": [{"raceName": "Bahrain"}]}}}
    assert jolpica._total(payload) == 480
    assert len(jolpica._races(payload)) == 1


def test_a_missing_envelope_is_empty_rather_than_an_error():
    assert jolpica._races({}) == []
    assert jolpica._total({}) == 0


def test_driver_names_are_joined_from_the_two_name_fields():
    races = [{"round": "1", "raceName": "Bahrain", "date": "2026-03-08", "Results": [
        {"position": "1", "points": "25", "status": "Finished",
         "Driver": {"givenName": "Max", "familyName": "Verstappen", "driverId": "max_verstappen"}},
    ]}]
    row = jolpica._result_rows(races, 2026, "results")[0]
    assert row["driver_name"] == "Max Verstappen"
    assert row["points"] == 25
    assert row["is_sprint"] is False


def test_sprints_are_read_from_their_own_key_and_marked():
    races = [{"round": "1", "raceName": "Bahrain", "date": "2026-03-08", "SprintResults": [
        {"position": "1", "points": "8", "Driver": {"givenName": "Max", "familyName": "Verstappen"}},
    ]}]
    rows = jolpica._result_rows(races, 2026, "sprint")
    assert rows[0]["is_sprint"] is True
    assert rows[0]["points"] == 8


def test_a_retirement_is_kept_as_a_result_worth_nothing():
    """Dropping it would understate the driver's starts, which is what the
    minimum-races floor and any per-race rate depend on."""
    races = [{"round": "1", "raceName": "Bahrain", "date": "2026-03-08", "Results": [
        {"position": "20", "points": "0", "status": "Engine",
         "Driver": {"givenName": "A", "familyName": "B"}},
    ]}]
    rows = jolpica._result_rows(races, 2026, "results")
    assert len(rows) == 1
    assert rows[0]["status"] == "Engine"


def test_the_feeds_points_reach_the_scorer():
    from whul.scoring import motorsport

    races = [{"round": "1", "raceName": "Bahrain", "date": "2026-03-08", "Results": [
        {"position": "1", "points": "26", "Driver": {"givenName": "Max", "familyName": "Verstappen"}},
    ]}]
    raw = pd.DataFrame(jolpica._result_rows(races, 2026, "results"))
    assert motorsport.score_f1(raw).iloc[0]["total_points"] == 26


# --- Flashscore live feed --------------------------------------------------

HEADER = ("ZA÷ATP - SINGLES: Rome (Italy), clay - Quarterfinal¬"
          "ZL÷/tennis/atp-singles/rome/¬")
RECORD = ("AA÷abc123¬AD÷1770000000¬AC÷3¬AS÷1¬"
          "WU÷sinner-jannik¬WV÷alcaraz-carlos¬BA÷6¬BB÷3¬BC÷7¬BD÷5¬")


def test_a_slug_becomes_a_readable_name():
    assert flashscore.slug_to_name("sinner-jannik") == "Jannik Sinner"


def test_a_multi_part_surname_keeps_all_its_parts():
    """Flashscore puts the surname first, so everything but the last token is
    the surname -- 'auger-aliassime-felix' is one player, not two."""
    assert flashscore.slug_to_name("auger-aliassime-felix") == "Felix Auger Aliassime"


def test_set_scores_come_from_paired_fields():
    assert flashscore.parse_score(RECORD) == "6-3 7-5"


def test_a_header_yields_tour_category_and_round():
    header = flashscore.parse_tournament_header(HEADER)
    assert header["tour"] == "ATP"
    assert header["tournament"] == "Rome"
    assert header["round"] == "QF"


def test_slams_are_recognized_in_the_header():
    header = flashscore.parse_tournament_header(
        "ZA÷ATP - SINGLES: Australian Open (Australia), hard - Final¬"
    )
    assert header["category"] == "Grand Slam"
    assert header["round"] == "F"


def test_doubles_and_below_tour_events_are_filtered_out():
    for raw in ("ZA÷ATP - DOUBLES: Rome¬",
                "ZA÷ATP - SINGLES: Challenger Phoenix¬",
                "ZA÷Exhibition: Some Event¬",
                "ZA÷ITF MEN - SINGLES: Tunis¬"):
        assert flashscore.parse_tournament_header(raw) is None, raw


def test_a_match_belongs_to_the_header_above_it():
    """Records carry no tournament of their own -- position in the stream is
    what assigns them, so the parser has to walk in order."""
    rows = list(flashscore.iter_matches(HEADER + "~" + RECORD))
    assert len(rows) == 1
    assert rows[0]["tournament"] == "Rome"
    assert rows[0]["winner"] == "Jannik Sinner"
    assert rows[0]["loser"] == "Carlos Alcaraz"
    assert rows[0]["score"] == "6-3 7-5"


def test_a_record_before_any_header_is_skipped():
    assert list(flashscore.iter_matches(RECORD)) == []


def test_upcoming_matches_are_not_results():
    upcoming = RECORD.replace("AC÷3¬", "AC÷1¬")
    assert list(flashscore.iter_matches(HEADER + "~" + upcoming)) == []


def test_a_match_with_no_stated_winner_is_skipped():
    """Scoring it would have to invent which player won."""
    unknown = RECORD.replace("AS÷1¬", "AS÷¬")
    assert list(flashscore.iter_matches(HEADER + "~" + unknown)) == []


def test_a_retirement_is_marked_in_the_score():
    retired = RECORD.replace("AC÷3¬", "AC÷8¬")
    row = list(flashscore.iter_matches(HEADER + "~" + retired))[0]
    assert row["score"].endswith("RET")


def test_a_walkover_replaces_the_score_entirely():
    walkover = RECORD.replace("AC÷3¬", "AC÷9¬")
    row = list(flashscore.iter_matches(HEADER + "~" + walkover))[0]
    assert row["score"] == "W/O"


def test_the_season_is_the_year_the_match_was_played():
    row = list(flashscore.iter_matches(HEADER + "~" + RECORD))[0]
    assert row["season"] == 2026


def test_draw_sizes_can_be_recovered_from_the_field():
    """The feed never states a draw. Every player in it appears at least once,
    so counting distinct winners and losers recovers the field size."""
    matches = pd.DataFrame([
        {"season": 2026, "tournament": "Rome", "winner": "A", "loser": "B"},
        {"season": 2026, "tournament": "Rome", "winner": "A", "loser": "C"},
        {"season": 2026, "tournament": "Rome", "winner": "D", "loser": "E"},
    ])
    sizes = flashscore.infer_draw_sizes(matches).set_index("tournament")
    assert sizes.loc["Rome", "draw_size"] == 5


# --- Sackmann archives -----------------------------------------------------

def sackmann_rows():
    return pd.DataFrame([
        {"tourney_name": "Roland Garros", "tourney_id": "2025-520", "tourney_level": "G",
         "draw_size": 128, "tourney_date": 20250525, "round": "F",
         "winner_name": "Champion", "loser_name": "Finalist", "score": "6-3 6-4 6-4",
         "best_of": 5},
        {"tourney_name": "Rome", "tourney_id": "2025-416", "tourney_level": "M",
         "draw_size": 96, "tourney_date": 20250507, "round": "R64",
         "winner_name": "Seed", "loser_name": "Qualifier", "score": "6-4 6-4",
         "best_of": 3},
        {"tourney_name": "Phoenix", "tourney_id": "2025-C01", "tourney_level": "C",
         "draw_size": 32, "tourney_date": 20250310, "round": "F",
         "winner_name": "Journeyman", "loser_name": "Other", "score": "6-4 6-4",
         "best_of": 3},
    ])


def test_the_level_column_gives_the_category():
    parsed = sackmann._to_matches(sackmann_rows(), "ATP", 2025)
    by_event = parsed.set_index("tournament")["category"]
    assert by_event["Roland Garros"] == "Grand Slam"
    assert by_event["Rome"] == "Masters 1000"


def test_challengers_are_excluded():
    """Below-tour draws are not comparable and would let a player pad a season
    on the second tier."""
    parsed = sackmann._to_matches(sackmann_rows(), "ATP", 2025)
    assert "Phoenix" not in set(parsed["tournament"])


def test_the_five_hundreds_arrive_marked_as_two_fifties():
    """Sackmann's 'A' level covers both, so the calendar has to correct them --
    this pins the behaviour so the gap is not mistaken for a bug."""
    rows = sackmann_rows().assign(tourney_level="A", tourney_name="Basel")
    parsed = sackmann._to_matches(rows, "ATP", 2025)
    assert set(parsed["category"]) == {"250"}


def test_rounds_and_scores_need_no_translation():
    """Sackmann already uses the round labels and score format scoring wants."""
    from whul.scoring.tennis import score_matches

    parsed = sackmann._to_matches(sackmann_rows(), "ATP", 2025)
    scored = score_matches(parsed)
    assert scored.set_index("tournament").loc["Roland Garros", "base_points"] == 700


def test_tournaments_takes_the_largest_reported_draw():
    """The column is per match and constant in practice; a stray row must not
    shrink an event onto a smaller points table."""
    rows = sackmann_rows().head(2)
    rows.loc[1, "draw_size"] = 96
    parsed = sackmann._to_matches(rows, "ATP", 2025)
    parsed.loc[1, "draw_size"] = 56
    parsed = pd.concat([parsed, parsed.iloc[[1]].assign(draw_size=96)], ignore_index=True)
    events = sackmann.tournaments(parsed).set_index("tournament")
    assert events.loc["Rome", "draw_size"] == 96


# --- the tournament calendar -----------------------------------------------

def test_the_same_event_under_different_names_gets_one_key():
    assert calendar.normalize_name("Cincinnati Masters") == calendar.normalize_name("Cincinnati")
    assert calendar.normalize_name("Italian Open") == calendar.normalize_name("Rome")


def test_events_whose_names_share_no_words_are_aliased():
    """Normalization cannot bridge 'Roland Garros' and 'French Open' -- they
    have nothing in common -- so they are listed explicitly."""
    assert calendar.normalize_name("Roland Garros") == calendar.normalize_name("French Open")


def test_the_calendar_overrides_what_the_feed_guessed():
    """This is the whole point: Flashscore's header says nothing about Rome
    being a 1000, and the calendar does."""
    table = pd.DataFrame([{"season": 2025, "tour": "ATP", "tournament": "Rome",
                           "category": "Masters 1000", "draw_size": 96}])
    matches = pd.DataFrame([{"season": 2025, "tour": "ATP", "tournament": "Rome",
                             "category": "250", "draw_size": float("nan")}])
    resolved = calendar.resolve(matches, table)
    assert resolved.iloc[0]["category"] == "Masters 1000"
    assert resolved.iloc[0]["draw_size"] == 96
    assert resolved.iloc[0]["category_source"] == "calendar"


def test_another_seasons_entry_is_better_than_none():
    """An event's category rarely changes, so last year's answer beats falling
    back to the feed's guess."""
    table = pd.DataFrame([{"season": 2024, "tour": "ATP", "tournament": "Rome",
                           "category": "Masters 1000", "draw_size": 96}])
    matches = pd.DataFrame([{"season": 2025, "tour": "ATP", "tournament": "Rome",
                             "category": "250", "draw_size": float("nan")}])
    resolved = calendar.resolve(matches, table)
    assert resolved.iloc[0]["category"] == "Masters 1000"
    assert resolved.iloc[0]["category_source"] == "calendar-other-season"


def test_an_unknown_tournament_keeps_the_feeds_answer_and_is_reported():
    table = pd.DataFrame([{"season": 2025, "tour": "ATP", "tournament": "Rome",
                           "category": "Masters 1000", "draw_size": 96}])
    matches = pd.DataFrame([{"season": 2025, "tour": "ATP", "tournament": "Basel",
                             "category": "250", "draw_size": float("nan")}])
    resolved = calendar.resolve(matches, table)
    assert resolved.iloc[0]["category"] == "250"
    gaps = calendar.unresolved(matches, table)
    assert list(gaps["tournament"]) == ["Basel"]


def test_an_empty_calendar_leaves_matches_untouched():
    matches = pd.DataFrame([{"season": 2025, "tour": "ATP", "tournament": "Rome",
                             "category": "250", "draw_size": 96.0}])
    resolved = calendar.resolve(matches, pd.DataFrame(columns=["season", "tour", "tournament", "category", "draw_size"]))
    assert resolved.iloc[0]["category"] == "250"


def test_validation_flags_a_calendar_that_is_still_a_raw_seed():
    """Sackmann marks every 500 as a 250, so a calendar with no 500s in it has
    not been corrected yet and will underpay a dozen events by half."""
    seed = pd.DataFrame([{"season": 2025, "tour": "ATP", "tournament": "Basel",
                          "category": "250", "draw_size": 32}])
    assert any("no 500s" in p for p in calendar.validate(seed))


def test_validation_flags_missing_draw_sizes_and_duplicates():
    bad = pd.DataFrame([
        {"season": 2025, "tour": "ATP", "tournament": "Basel", "category": "500", "draw_size": None},
        {"season": 2025, "tour": "ATP", "tournament": "Basel", "category": "500", "draw_size": 32},
    ])
    problems = " ".join(calendar.validate(bad))
    assert "draw size" in problems
    assert "duplicate" in problems


def test_validation_flags_a_category_scoring_does_not_know():
    bad = pd.DataFrame([{"season": 2025, "tour": "ATP", "tournament": "Basel",
                         "category": "ATP 500", "draw_size": 32}])
    assert any("unknown categories" in p for p in calendar.validate(bad))


def test_a_seed_round_trips_from_sackmann_tournaments():
    parsed = sackmann._to_matches(sackmann_rows(), "ATP", 2025)
    seeded = calendar.from_sackmann(sackmann.tournaments(parsed))
    assert set(seeded.columns) == set(calendar.COLUMNS)
    assert seeded.set_index("tournament").loc["Rome", "draw_size"] == 96


# --- tour schedule scraping ------------------------------------------------

def test_every_category_a_tour_page_states_is_recognized():
    cases = {
        "Grand Slam": "Grand Slam",
        "ATP Masters 1000": "Masters 1000",
        "WTA 1000": "Masters 1000",
        "Premier Mandatory": "Masters 1000",
        "ATP 500": "500",
        "ATP 250": "250",
        "WTA International": "250",
        "Nitto ATP Finals": "Tour Finals",
        "Davis Cup": "International",
        "United Cup": "International",
    }
    for text, expected in cases.items():
        assert schedule.classify_category(text) == expected, text


def test_team_events_are_classified_before_anything_else():
    """'United Cup' carries no level number and no tour name, so a later rule
    would have to fall through to 250 and halve every win at it."""
    assert schedule.classify_category("United Cup 2026") == "International"
    assert schedule.classify_category("Billie Jean King Cup Finals") == "International"


def test_an_unlabelled_event_is_left_unclassified():
    """Better no answer than a guessed one -- the caller can fall back."""
    assert schedule.classify_category("Wimbledon") is None
    assert schedule.classify_category("") is None
    assert schedule.classify_category(None) is None


def test_only_real_draw_sizes_are_accepted():
    """A page is full of numbers. Prize money and years read as draws unless
    the value is checked against the sizes a tour event actually uses."""
    assert schedule.parse_draw_size("Draw: 96") == 96
    assert schedule.parse_draw_size("128 player draw") == 128
    assert schedule.parse_draw_size("Prize money $9,600,000") is None
    assert schedule.parse_draw_size("draw 100") is None


def test_tournaments_are_pulled_out_of_embedded_json():
    """Tour sites ship the data as JSON well before they render it, and the
    payload sits a dozen levels down inside framework state."""
    page = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": {"data": {"tournaments": [
            {"name": "Indian Wells", "category": "ATP Masters 1000", "drawSize": 96},
            {"name": "Basel", "level": "ATP 500", "drawSize": 32},
        ]}}}})
        + "</script>"
    )
    rows = schedule.extract_from_json(page, "ATP", 2026)
    by_name = {r["tournament"]: r for r in rows}
    assert by_name["Indian Wells"]["category"] == "Masters 1000"
    assert by_name["Indian Wells"]["draw_size"] == 96
    assert by_name["Basel"]["category"] == "500"


def test_json_records_without_a_category_are_not_tournaments():
    """Navigation entries and ad slots have names too."""
    page = ('<script type="application/json">'
            + json.dumps({"items": [{"name": "Rankings"}, {"name": "News"}]})
            + "</script>")
    assert schedule.extract_from_json(page, "ATP", 2026) == []


def test_malformed_json_does_not_stop_the_other_blobs():
    page = (
        '<script type="application/json">{ this is not json </script>'
        '<script type="application/json">'
        + json.dumps({"t": [{"name": "Rome", "category": "ATP Masters 1000"}]})
        + "</script>"
    )
    rows = schedule.extract_from_json(page, "ATP", 2026)
    assert [r["tournament"] for r in rows] == ["Rome"]


def test_the_dom_is_the_backstop_when_there_is_no_json():
    page = """
      <ul><li class="tourney-result">
        <a href="/en/tournaments/rome/416/overview">Rome</a>
        <span class="badge">ATP Masters 1000</span>
        <span>Draw: 96</span>
      </li></ul>
    """
    rows = schedule.extract_from_dom(page, "ATP", 2026)
    assert rows[0]["tournament"] == "Rome"
    assert rows[0]["category"] == "Masters 1000"
    assert rows[0]["draw_size"] == 96


def test_a_merge_reports_a_category_change_rather_than_applying_it_silently():
    """An event moving between categories restates its points, so the diff is
    the deliverable -- it is meant to be read before the result is saved."""
    existing = pd.DataFrame([{"season": 2026, "tour": "ATP", "tournament": "Dallas",
                              "category": "250", "draw_size": 32}])
    scraped = pd.DataFrame([{"season": 2026, "tour": "ATP", "tournament": "Dallas",
                             "category": "500", "draw_size": 32}])
    merged, changes = schedule.merge(existing, scraped)
    assert merged.iloc[0]["category"] == "500"
    assert "250 -> 500" in changes.iloc[0]["change"]


def test_a_merge_names_events_the_scrape_did_not_find():
    """A tournament dropping out of the scrape is usually a rename, not a
    cancellation, and either way it needs a person to look."""
    existing = pd.DataFrame([{"season": 2026, "tour": "ATP", "tournament": "Basel",
                              "category": "500", "draw_size": 32}])
    scraped = pd.DataFrame([{"season": 2026, "tour": "ATP", "tournament": "Vienna",
                             "category": "500", "draw_size": 32}])
    _, changes = schedule.merge(existing, scraped)
    kinds = set(changes["change"])
    assert "added" in kinds
    assert "missing from scrape" in kinds


def test_a_scrape_that_omits_the_draw_does_not_erase_a_known_one():
    """The page not stating a draw means it did not say, not that there is
    none -- overwriting with NaN would move the event to the default bracket."""
    existing = pd.DataFrame([{"season": 2026, "tour": "ATP", "tournament": "Rome",
                              "category": "Masters 1000", "draw_size": 96.0}])
    scraped = pd.DataFrame([{"season": 2026, "tour": "ATP", "tournament": "Rome",
                             "category": "Masters 1000", "draw_size": None}])
    merged, _ = schedule.merge(existing, scraped)
    assert merged.iloc[0]["draw_size"] == 96.0


def test_merging_into_an_empty_calendar_takes_everything():
    scraped = pd.DataFrame([{"season": 2026, "tour": "ATP", "tournament": "Rome",
                             "category": "Masters 1000", "draw_size": 96}])
    merged, changes = schedule.merge(pd.DataFrame(), scraped)
    assert len(merged) == 1
    assert list(changes["change"]) == ["added"]


# --- the checked-in 2026 calendar ------------------------------------------

def test_the_shipped_calendar_is_valid():
    """The file the live season is scored against, checked on every run."""
    shipped = calendar.load()
    assert not shipped.empty, "data/tennis/calendar.csv is missing"
    assert calendar.validate(shipped) == []


def test_the_shipped_calendar_covers_both_tours_and_every_category():
    shipped = calendar.load()
    assert set(shipped["tour"]) == {"ATP", "WTA"}
    # Every category scoring knows about should appear; one missing means a
    # whole class of event is being scored on a fallback.
    from whul.scoring.tennis import CATEGORIES

    assert set(shipped["category"]) == set(CATEGORIES)


def test_every_shipped_entry_resolves_to_a_real_tier():
    """A row whose category scoring does not recognize would silently pay from
    the 250 table."""
    from whul.scoring.tennis import scoring_tier

    shipped = calendar.load()
    tiers = {
        scoring_tier(row.category, row.draw_size) for row in shipped.itertuples()
    }
    assert "A250_32" in tiers and "GS" in tiers and "M1000_128" in tiers
    assert "M1000_64" in tiers, "the 56-draw Masters events should reach the small table"
