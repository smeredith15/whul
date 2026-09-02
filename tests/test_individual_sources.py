"""Source adapters for the individual sports.

The feeds themselves cannot be reached from here, so these exercise the parsing
against recorded response shapes -- which is where the adapters have actually
broken before (a value nested one level deeper than the code looked).
"""

import json
import pathlib

import pandas as pd
import pytest
import requests

from whul.sources import espn_individual as espn_ind
from whul.sources import flashscore, jolpica, snapshot
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
    merged, changes = schedule.merge(existing, scraped, require_complete=False)
    assert merged.iloc[0]["category"] == "500"
    assert "250 -> 500" in changes.iloc[0]["change"]


def test_a_merge_names_events_the_scrape_did_not_find():
    """A tournament dropping out of the scrape is usually a rename, not a
    cancellation, and either way it needs a person to look."""
    existing = pd.DataFrame([{"season": 2026, "tour": "ATP", "tournament": "Basel",
                              "category": "500", "draw_size": 32}])
    scraped = pd.DataFrame([{"season": 2026, "tour": "ATP", "tournament": "Vienna",
                             "category": "500", "draw_size": 32}])
    _, changes = schedule.merge(existing, scraped, require_complete=False)
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
    merged, _ = schedule.merge(existing, scraped, require_complete=False)
    assert merged.iloc[0]["draw_size"] == 96.0


def test_merging_into_an_empty_calendar_takes_everything():
    scraped = pd.DataFrame([{"season": 2026, "tour": "ATP", "tournament": "Rome",
                             "category": "Masters 1000", "draw_size": 96}])
    merged, changes = schedule.merge(pd.DataFrame(), scraped, require_complete=False)
    assert len(merged) == 1
    assert list(changes["change"]) == ["added"]


# --- the checked-in 2026 calendar ------------------------------------------

def test_the_shipped_calendar_is_valid():
    """The file the live season is scored against, checked on every run."""
    shipped = calendar.load()
    assert not shipped.empty, "whul/data/tennis_calendar.csv is missing"
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


def full_season(tour="WTA", n=40, season=2026):
    return pd.DataFrame([
        {"season": season, "tour": tour, "tournament": f"Event {i}",
         "category": "250", "draw_size": 32}
        for i in range(n)
    ])


def test_a_partial_scrape_is_refused_rather_than_merged():
    """The WTA page yielded six events out of about sixty. Merging that adds a
    handful of rows and removes none, so it reads as a success while leaving
    every event it missed on whatever it had -- and reports the other fifty as
    'missing from scrape', as though the tour cancelled them."""
    existing = full_season("ATP", 60)
    merged, changes = schedule.merge(existing, full_season("WTA", 6))
    assert len(merged) == 60, "the calendar must be left alone"
    assert "refused" in changes.iloc[0]["change"]


def test_a_full_scrape_is_accepted():
    merged, _ = schedule.merge(full_season("WTA", 6), full_season("WTA", 40))
    assert len(merged) == 40


def test_plausibility_is_judged_per_tour():
    """A run that gets the ATP right and the WTA wrong is still wrong."""
    mixed = pd.concat([full_season("ATP", 60), full_season("WTA", 5)], ignore_index=True)
    assert schedule.is_plausible(mixed) is False
    assert schedule.completeness(mixed) == {"ATP": 60, "WTA": 5}


def test_governing_bodies_are_not_tournaments():
    """'International Tennis Federation' sits in the WTA page's JSON next to
    real events and carries enough text to classify as a 250."""
    page = ('<script type="application/json">' + json.dumps({"t": [
        {"name": "International Tennis Federation", "category": "WTA International"},
        {"name": "Brisbane International", "category": "WTA International"},
    ]}) + "</script>")
    rows = schedule.extract_from_json(page, "WTA", 2026)
    assert [r["tournament"] for r in rows] == ["Brisbane International"]


def test_framework_state_outside_a_json_script_tag_is_read():
    """The WTA page has no __NEXT_DATA__, so a JSON-script-only sweep cannot
    see its payload at all."""
    page = ('<script>window.__NUXT__ = '
            + json.dumps({"data": [{"name": "Rome", "category": "WTA 1000"}]})
            + ';</script>')
    rows = schedule.extract_from_json(page, "WTA", 2026)
    assert rows[0]["tournament"] == "Rome"
    assert rows[0]["category"] == "Masters 1000"


def test_tournament_links_are_the_durable_fallback():
    """Links have to keep working across a redesign that renames every class."""
    page = """
      <div class="whatever-new-name">
        <a href="/tournaments/901/brisbane/">Brisbane International</a>
        <span>WTA 500 · Draw: 32</span>
      </div>
    """
    rows = schedule.extract_from_links(page, "WTA", 2026)
    assert rows[0]["tournament"] == "Brisbane International"
    assert rows[0]["category"] == "500"
    assert rows[0]["draw_size"] == 32


def test_non_tournament_links_are_ignored():
    page = '<div><a href="/rankings/singles">Rankings</a><span>WTA 500</span></div>'
    assert schedule.extract_from_links(page, "WTA", 2026) == []


# --- Flashscore rounds -----------------------------------------------------

TOURNAMENT_PAGE = (
    "AA÷abc123¬ER÷Quarterfinal¬AD÷1770000000¬~"
    "AA÷def456¬ER÷Semi-finals¬AD÷1770100000¬~"
)


def test_the_slug_is_kept_because_the_round_depends_on_it():
    """The daily feed carries no per-match round -- a slam's header ends with
    the country, not a round -- so the tournament page has to be fetched, and
    the slug is the only thing that says which page."""
    header = flashscore.parse_tournament_header(
        "ZA÷ATP - SINGLES: US Open (USA), hard¬ZL÷/tennis/atp-singles/us-open/¬"
    )
    assert header["slug"] == "/tennis/atp-singles/us-open/"
    assert header["round"] == ""


def test_the_round_map_is_read_from_a_tournament_page(monkeypatch):
    class Response:
        status_code = 200
        text = TOURNAMENT_PAGE

    class Session:
        def get(self, url, **kwargs):
            return Response()

    rounds = flashscore.fetch_round_map("/tennis/atp-singles/us-open/", Session())
    assert rounds["abc123"] == "Quarterfinal"
    assert rounds["def456"] == "Semi-finals"


def test_rounds_from_the_tournament_page_are_normalized_onto_matches(monkeypatch):
    monkeypatch.setattr(
        flashscore, "fetch_round_map",
        lambda slug, session=None: {"abc123": "Quarterfinal"},
    )
    matches = [{"match_uid": "abc123", "slug": "/x/", "round": "",
                "tournament": "US Open"}]
    assert flashscore.apply_rounds(matches)[0]["round"] == "QF"


def test_a_round_the_feed_already_gave_is_not_refetched(monkeypatch):
    """One request pair per tournament, and only for tournaments that need it."""
    calls = []
    monkeypatch.setattr(
        flashscore, "fetch_round_map",
        lambda slug, session=None: calls.append(slug) or {},
    )
    matches = [{"match_uid": "abc", "slug": "/x/", "round": "QF", "tournament": "Rome"}]
    flashscore.apply_rounds(matches)
    assert calls == []


def test_an_unreachable_tournament_page_loses_only_its_rounds(monkeypatch):
    class Session:
        def get(self, url, **kwargs):
            raise requests.RequestException("boom")

    assert flashscore.fetch_round_map("/x/", Session()) == {}


def test_a_page_that_answers_non_200_is_skipped():
    class Response:
        status_code = 404
        text = TOURNAMENT_PAGE

    class Session:
        def get(self, url, **kwargs):
            return Response()

    assert flashscore.fetch_round_map("/x/", Session()) == {}



# --- failing loudly on a missing parser ------------------------------------

def test_a_missing_html_parser_is_an_error_not_an_empty_result(monkeypatch):
    """Returning [] is indistinguishable from a page with no tournaments in
    it, which sends the diagnosis after the markup instead of the install."""
    import builtins

    real_import = builtins.__import__

    def no_bs4(name, *args, **kwargs):
        if name == "bs4":
            raise ImportError("No module named 'bs4'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_bs4)
    with pytest.raises(ImportError):
        schedule.extract_from_dom("<html></html>", "WTA", 2026)


# --- the historical snapshot -----------------------------------------------

def snapshot_rows():
    return pd.DataFrame({
        "date": pd.to_datetime(["2025-01-20", "2025-01-21", "2025-06-02", "2025-03-01"]),
        "tour": ["ATP", "ATP", "WTA", "WTA"],
        "tourney_name": ["Australian Open", "Australian Open", "Roland Garros", "W15 Monastir"],
        "tourney_level": ["Grand Slam", "Grand Slam", "Grand Slam", "MainTour"],
        "round": ["R128", "Q1", "F", "F"],
        "score": ["6-3 6-4 6-4", "6-1 6-1", "6-2 6-3", "6-0 6-0"],
        "surface": ["Hard", "Hard", "Clay", "Hard"],
        "best_of": [5, 5, 3, 3],
        "winner_id": ["atp-j-sinner", "atp-x-nobody", "wta-c-gauff", "wta-a-nobody"],
        "loser_id": ["atp-a-zverev", "atp-y-nobody", "wta-a-sabalenka", "wta-b-nobody"],
    })


def test_player_ids_resolve_despite_the_case_mismatch():
    """The mapping keys are cased (atp-J-Sinner) and the snapshot's ids are
    not (atp-j-sinner). An exact match resolves nothing at all."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write("canonical_player_id,player_name_alias,has_stats\n"
                 "atp-J-Sinner,Jannik Sinner,TRUE\n")
        path = pathlib.Path(fh.name)
    names = snapshot.player_names(path)
    assert names["atp-j-sinner"] == "Jannik Sinner"

    parsed = snapshot.to_matches(snapshot_rows(), names)
    assert "Jannik Sinner" in set(parsed["winner"])


def test_an_unmapped_id_keeps_its_id_rather_than_vanishing():
    parsed = snapshot.to_matches(snapshot_rows(), {})
    assert "atp-j-sinner" in set(parsed["winner"])


def test_qualifying_rounds_are_excluded_before_anything_else():
    """Dropping them later would let a missing qualifying result read as a
    main-draw bye."""
    parsed = snapshot.to_matches(snapshot_rows(), {})
    assert "Q1" not in set(parsed["round"])
    assert len(parsed) == 3


def test_the_season_comes_from_the_match_date():
    parsed = snapshot.to_matches(snapshot_rows(), {})
    assert set(parsed["season"]) == {2025}


def test_the_calendar_gates_what_counts_not_the_level_column():
    """This file marks ITF W15 draws as 'MainTour', so trusting its own level
    would score a $15,000 Monastir final as a tour 250."""
    parsed = snapshot.to_matches(snapshot_rows(), {})
    assert "W15 Monastir" in set(parsed["tournament"]), "it is present in the file"

    table = pd.DataFrame([
        {"season": 2025, "tour": "ATP", "tournament": "Australian Open",
         "category": "Grand Slam", "draw_size": 128},
        {"season": 2025, "tour": "WTA", "tournament": "Roland Garros",
         "category": "Grand Slam", "draw_size": 128},
    ])
    resolved = calendar.resolve(parsed, table)
    scoreable = resolved[resolved["category_source"] != "feed"]
    assert "W15 Monastir" not in set(scoreable["tournament"])
    assert calendar.unresolved(parsed, table).iloc[0]["tournament"] == "W15 Monastir"


def test_an_inferred_draw_still_lands_on_the_right_bracket():
    """Inference under-counts -- a walkover leaves a player with no match row,
    so a 128-draw shows about 125 -- but the bracket is the next power of two
    up, and 125 and 128 are both 128."""
    from whul.scoring.tennis import effective_bracket

    for counted, real in ((125, 128), (122, 128), (91, 128), (53, 64), (31, 32)):
        assert effective_bracket(counted) == effective_bracket(real)


def test_draw_sizes_are_counted_per_event():
    parsed = snapshot.to_matches(snapshot_rows(), {})
    sizes = snapshot.infer_draw_sizes(parsed).set_index("tournament")
    assert sizes.loc["Australian Open", "draw_size"] == 2


def test_a_missing_snapshot_says_it_cannot_be_re_downloaded():
    """The Sackmann repository is gone, so this file is the only copy -- an
    error that reads like a transient fetch failure would be misleading."""
    with pytest.raises(FileNotFoundError, match="only copy"):
        snapshot.load_matches([2025], root=pathlib.Path("/nonexistent"))


# --- endpoint discovery ----------------------------------------------------

JS_RENDERED_PAGE = """
  <div class="tournament-list js-tournaments-list"
       data-url="/api/tournaments/calendar" data-year="2026"></div>
  <link rel="preload" href="/dist/tournaments.js">
  <script>
    const FEED = "https://api.wtatennis.com/tennis/tournaments/?page=0";
    const LOGO = "/img/tournament-logo.png";
  </script>
"""


def test_the_data_endpoint_is_found_when_the_rows_are_not_in_the_markup():
    """The WTA page ships an empty js-tournaments-list and fills it
    client-side, so no selector will ever find the rows. The endpoint is the
    only way in."""
    found = schedule.discover_endpoints(JS_RENDERED_PAGE)
    assert "/api/tournaments/calendar" in found["urls"]
    assert "https://api.wtatennis.com/tennis/tournaments/?page=0" in found["urls"]


def test_static_assets_are_not_reported_as_endpoints():
    """A page is full of URLs. An image with 'tournament' in its name matches
    every keyword an endpoint would."""
    found = schedule.discover_endpoints(JS_RENDERED_PAGE)
    assert not any(u.endswith((".js", ".png")) for u in found["urls"])


def test_data_attributes_on_the_container_are_reported():
    found = schedule.discover_endpoints(JS_RENDERED_PAGE)
    attrs = found["data_attributes"][0]
    assert attrs["data-url"] == "/api/tournaments/calendar"
    assert attrs["data-year"] == "2026"


def test_a_page_with_nothing_to_find_reports_empty_rather_than_raising():
    found = schedule.discover_endpoints("<html><body>nothing here</body></html>")
    assert found == {"urls": [], "data_attributes": [], "preloads": []}
