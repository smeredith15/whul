"""The generated site."""

import json
import re
from html import escape
from datetime import date
from pathlib import Path

import pytest

from whul import simulate
from whul.site import charts, images, rulebook, theme
from whul.site.build import build
from whul.store import open_store

END = date(2026, 10, 31)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    store = open_store(":memory:")
    simulate.generate(store, seed=2026, end=END, verbose=False)
    out = tmp_path_factory.mktemp("site")
    result = build(store, simulate.SIM_SEASON, out)
    return out, result


# --- the palette -----------------------------------------------------------

def test_a_manager_keeps_one_colour_everywhere():
    """Colour follows the entity, never its rank -- a change in the standings
    must not repaint anything."""
    managers = ["SS", "TG", "JM"]
    first = {m: theme.series_index(managers, m) for m in managers}
    reordered = {m: theme.series_index(list(reversed(managers)), m) for m in managers}
    assert first == reordered


def test_hues_are_never_cycled_or_generated():
    """Past eight, a league needs a different encoding, not an invented hue."""
    assert len(theme.SERIES_LIGHT) == len(theme.SERIES_DARK) == theme.MAX_SERIES == 8


def test_dark_mode_is_its_own_palette_not_a_flip():
    assert theme.SERIES_LIGHT != theme.SERIES_DARK
    for light, dark in zip(theme.SERIES_LIGHT, theme.SERIES_DARK):
        assert light.startswith("#") and dark.startswith("#")


def test_dark_values_are_declared_for_both_the_toggle_and_the_os_setting():
    assert "prefers-color-scheme: dark" in theme.STYLESHEET
    assert '[data-theme="dark"]' in theme.STYLESHEET


# --- chart construction ----------------------------------------------------

def test_zero_reads_as_zero_not_as_a_decimal():
    assert charts._fmt(0) == "0"
    assert charts._fmt(3253.04) == "3,253"


def test_a_line_chart_carries_its_data_for_the_hover_layer():
    days = [date(2026, 9, 1), date(2026, 9, 2)]
    svg = charts.progression_chart(days, [charts.Series("Avery", 1, [1.0, 2.0])])
    assert 'class="chartdata"' in svg
    assert "2026-09-01" in svg


def test_end_labels_stay_in_value_order():
    """A label nudged past its neighbour would point at the wrong line."""
    days = [date(2026, 9, 1), date(2026, 9, 2)]
    series = [
        charts.Series("A", 1, [0.0, 100.0]),
        charts.Series("B", 2, [0.0, 99.5]),
        charts.Series("C", 3, [0.0, 99.0]),
    ]
    svg = charts.progression_chart(days, series)
    order = [name for name in ("A", "B", "C") if f">{name} " in svg]
    assert order == ["A", "B", "C"]
    assert svg.index(">A ") < svg.index(">B ") < svg.index(">C ")


def test_a_chart_with_no_data_says_so_rather_than_drawing_an_empty_frame():
    assert "No scores" in charts.progression_chart([], [])
    assert "Nothing scored" in charts.contribution_chart([], [], {})


SLOT_ROWS = [("NFL", "NFL 1", "#1"), ("NFL", "NFL 2", "#2")]
SLOT_VALUES = {
    ("Avery", "NFL 1"): (100.0, "a1", "P. Vance", "Player"),
    ("Avery", "NFL 2"): (60.0, "a2", "R. Lockwood", "Team"),
}


def test_bars_are_capped_and_rounded_at_the_data_end():
    svg = charts.contribution_chart(SLOT_ROWS, [("Avery", 1)], SLOT_VALUES)
    assert f'height="{charts.SLOT_BAR_THICKNESS}"' in svg
    assert charts.SLOT_BAR_THICKNESS <= 24
    assert 'rx="4' in svg


def test_every_bar_carries_a_native_title_for_keyboard_and_screen_readers():
    """Named first. A hover and a screen reader both got "NFL 1" before the
    name, which answers neither who this is nor how they are doing."""
    svg = charts.contribution_chart(SLOT_ROWS, [("Avery", 1)], SLOT_VALUES)
    assert "<title>P. Vance — Player · NFL #1 · Avery · 100</title>" in svg


def test_a_bar_with_no_asset_still_titles_itself():
    """An empty slot is a real state, and a blank title reads as a broken one."""
    svg = charts.contribution_chart(
        [("NFL", "NFL 1", "#1")], [("Avery", 1)], {})
    assert "(empty)" in svg


def test_a_three_entry_value_still_works():
    """The kind is a later addition; a caller that does not carry it should
    lose the word, not the bar."""
    svg = charts.contribution_chart(
        SLOT_ROWS, [("Avery", 1)], {("Avery", "NFL 1"): (100.0, "a1", "P. Vance")})
    assert "P. Vance" in svg and 'class="bar"' in svg


def test_players_and_teams_are_drawn_at_different_strengths():
    """One category can hold both, and a run of identical bars does not say
    which is which. The label carries the word too -- alpha on its own is a
    weak signal and no help at all to a screen reader."""
    svg = charts.contribution_chart(SLOT_ROWS, [("Avery", 1)], SLOT_VALUES)
    assert f'fill-opacity="{charts.PLAYER_ALPHA}"' in svg
    assert svg.count("fill-opacity") == 1   # the team bar is drawn solid
    assert "Player</tspan>" in svg and "Team</tspan>" in svg


def test_every_bar_is_one_slot_so_any_two_are_comparable():
    """A category with four slots used to dwarf one with a single slot simply
    by having more of them; now every bar is one normalized score."""
    svg = charts.contribution_chart(SLOT_ROWS, [("Avery", 1)], SLOT_VALUES)
    assert svg.count('class="bar"') == 2


def test_a_bar_knows_which_asset_it_stands_for():
    """So clicking it can open that player's profile."""
    svg = charts.contribution_chart(SLOT_ROWS, [("Avery", 1)], SLOT_VALUES)
    assert 'data-asset="a1"' in svg


def test_a_category_is_written_once_per_run_of_its_slots():
    """Four rows of the same word is four lines of noise."""
    svg = charts.contribution_chart(SLOT_ROWS, [("Avery", 1)], SLOT_VALUES)
    assert svg.count(">NFL<") == 1


def test_a_bar_is_labelled_with_what_it_is_of():
    """The rank says where a holding sits and the colour says whose it is.
    Neither says the manager holds P. Vance."""
    svg = charts.contribution_chart(SLOT_ROWS, [("Avery", 1)], SLOT_VALUES)
    assert "#1 P. Vance" in svg
    assert "#2 R. Lockwood" in svg


def test_a_long_name_is_cut_rather_than_overrunning_the_column():
    values = {("Avery", "NFL 1"): (10.0, "a1", "Andrea Kimi Antonelli Jr", "Player")}
    svg = charts.contribution_chart([("NFL", "NFL 1", "#1")], [("Avery", 1)], values)
    # The label is cut; the title still carries the whole name, because a
    # hover has room where a 250px column does not.
    assert "Andrea Kimi\u2026" in svg or "Andrea Kimi Antonelli\u2026" in svg
    assert "<title>Andrea Kimi Antonelli Jr" in svg


def test_a_legend_is_present_for_more_than_one_series():
    """Identity is never colour alone."""
    assert "Avery" in charts.legend([("Avery", 1), ("Blake", 2)])


# --- the built site --------------------------------------------------------

def test_every_page_is_written(site):
    out, result = site
    assert (out / "index.html").exists()
    assert (out / "about.html").exists()
    assert (out / "style.css").exists()
    assert (out / "app.js").exists()
    for manager in simulate.MANAGERS:
        assert (out / "team" / f"{manager.lower()}.html").exists()
    assert result["pages"] == 2 + len(simulate.MANAGERS)


def test_simulated_data_is_labelled_on_every_page(site):
    """Nobody should be able to mistake a placeholder for a real result."""
    out, _ = site
    for page in out.rglob("*.html"):
        assert "Simulated data" in page.read_text(), page.name


def test_the_banner_says_what_is_actually_invented(tmp_path):
    """Once the rosters are real, calling the players placeholders is false on
    every page -- and a banner nobody can trust is worse than no banner."""
    from whul.site.build import BANNERS

    assert "rosters and players are invented" in BANNERS["everything"]
    assert "rosters, players and prices are real" in BANNERS["scores_only"]


def test_the_standings_show_a_best_performer_not_a_slot_count(site):
    """A count of counting slots is the same number for everyone; the best
    performer is the thing worth looking at."""
    out, _ = site
    html = (out / "index.html").read_text()
    assert "Best performer" in html
    assert "Counting slots" not in html


def test_the_headline_tiles_say_something_that_changes(site):
    """The ceiling was a constant nobody approaches."""
    out, _ = site
    html = (out / "index.html").read_text()
    assert "Biggest riser this week" in html
    assert "Ceiling" not in html


def test_the_standings_table_is_the_default_view(site):
    """Not a tab or a toggle: the light-mode palette's contrast warning makes
    a readable table mandatory, and it is also just the thing people want."""
    out, _ = site
    html = (out / "index.html").read_text()
    assert html.index("Standings</h2>") < html.index("Progression</h2>")


def test_every_chart_ships_a_table_view(site):
    """The relief the light-mode contrast warning requires, and what makes a
    value readable without a hover. The two charts now sit on two pages -- the
    line on the standings, both on the results -- so the count is per page."""
    out, _ = site
    assert (out / "index.html").read_text().count("Show as a table") == 1
    assert (out / "results.html").read_text().count("Show as a table") == 2


def test_the_results_page_carries_both_charts_and_the_table(site):
    """The bars move here and the line is copied: the standings page is opened
    to see who is winning, and the line is the shape of that."""
    out, _ = site
    html = (out / "results.html").read_text()
    assert 'id="progression"' in html
    assert 'id="slots"' in html
    assert 'id="everyone"' in html
    assert "linechart" in html and "barchart" in html


def test_the_standings_page_keeps_the_line_and_loses_the_bars(site):
    out, _ = site
    html = (out / "index.html").read_text()
    assert "linechart" in html
    assert "barchart" not in html


def test_a_team_page_shows_the_normalized_score(site):
    out, _ = site
    html = (out / "team" / "tg.html").read_text()
    assert "Normalized" in html
    assert "Slot score" not in html, "dropped: raw stats live in the profile window"


def test_a_benched_score_is_struck_through_not_hidden(site):
    """It is what the slot would be worth, and seeing it is how a manager knows
    how close the bench is to the cut."""
    out, _ = site
    html = (out / "team" / "tg.html").read_text()
    assert "class='bench'" in html
    assert 'class="struck"' in html


# --- the profile window ----------------------------------------------------

def test_every_rostered_asset_ships_its_profile_with_the_page(site):
    """A static site has nothing to fetch from, so a profile has to already be
    there when it is clicked."""
    out, result = site
    html = (out / "index.html").read_text()
    assert 'id="assetdata"' in html
    assert '<dialog class="profile"' in html
    assert result["profiles"] > 100


def test_a_profile_carries_the_stats_behind_the_raw_score(site):
    out, _ = site
    payload = json.loads(
        re.search(r'id="assetdata">(.*?)</script>',
                  (out / "index.html").read_text(), re.S).group(1)
    )
    profile = next(p for p in payload.values() if p["stats"])
    assert profile["raw"] != "—"
    assert profile["scaled"] != "—"
    assert len(profile["stats"]) >= 3


def test_names_in_tables_open_their_profile(site):
    out, _ = site
    for page in ("index.html", "team/tg.html"):
        assert 'class="assetlink"' in (out / page).read_text()


# --- images ----------------------------------------------------------------

def test_a_missing_photo_falls_back_to_a_monogram(site):
    """Not a placeholder to be replaced later: a page with three photos and
    forty-five monograms should still look deliberate."""
    out, _ = site
    html = (out / "index.html").read_text()
    assert 'class="avatar mono"' in html


def test_a_monogram_uses_initials():
    assert images._initials("L. Delgado") == "LD"
    assert images._initials("Oakhurst Rovers") == "OR"
    assert images._initials("avery") == "AV"
    assert images._initials("") == "?"


def test_a_disambiguating_marker_is_not_part_of_the_monogram():
    """An international side carries "(M)" or "(W)" to tell it from its
    opposite number. A monogram reading "E(" is worse than no marker."""
    assert images._initials("England (W)") == "EN"
    assert images._initials("Oakhurst Rovers (M)") == "OR"


def test_the_two_international_categories_are_told_apart_by_name():
    """England is rostered in both, under the same name. Two rows reading
    "England", one above the other, with nothing to say which is which."""
    from whul.site.build import marked_name

    assert marked_name("England", "Men's Intl Soccer") == "England (M)"
    assert marked_name("England", "Women's Intl Soccer") == "England (W)"
    # Everywhere else the league is already the row's own column.
    assert marked_name("Arsenal", "Premier League") == "Arsenal"
    assert marked_name("Rory McIlroy", "PGA") == "Rory McIlroy"


def test_a_supplied_photo_is_used_and_published(tmp_path):
    source = tmp_path / "img"
    (source / "asset").mkdir(parents=True)
    (source / "asset" / "sim-x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert images.find("asset", "sim-x", source) is not None
    html = images.avatar("asset", "sim-x", "X Y", source=source)
    assert "<img" in html and "sim-x.png" in html

    out = tmp_path / "site"
    out.mkdir()
    counts = images.copy_all(out, source)
    assert counts["asset"] == 1
    assert (out / "img" / "asset" / "sim-x.png").exists()


def test_photo_lookup_is_by_the_id_the_store_uses(tmp_path):
    """Adding a photo should be dropping in a file -- no manifest to update."""
    source = tmp_path / "img"
    (source / "manager").mkdir(parents=True)
    (source / "manager" / "avery.webp").write_bytes(b"RIFF")
    assert images.find("manager", "avery", source).name == "avery.webp"
    assert images.find("manager", "blake", source) is None


def test_every_team_is_reachable_from_every_page(site):
    out, _ = site
    for page in out.rglob("*.html"):
        html = page.read_text()
        for manager in simulate.MANAGERS:
            assert f"{manager.lower()}.html" in html, f"{manager} missing from {page.name}"


def test_the_scoring_page_explains_how_a_score_is_reached(site):
    out, _ = site
    html = (out / "about.html").read_text()
    for step in ("Count what happened", "same scale", "What 100 means",
                 "owned the slot", "Best ball"):
        assert step in html


def test_the_scoring_tab_is_called_scoring(site):
    """The old label described the page; the tab should name it."""
    out, _ = site
    for page in out.rglob("*.html"):
        html = page.read_text()
        assert "How scoring works" not in html, page.name
        assert '>Scoring</a>' in html, page.name


def test_the_scoring_page_carries_every_asset_type(site):
    out, _ = site
    html = (out / "about.html").read_text()
    for rules in rulebook.sections():
        assert escape(rules.title) in html, rules.title


def test_the_scoring_page_explains_itself_without_naming_the_plumbing(site):
    """It is read by five people who have never opened the repository.

    A rules page that says "per NFL_Players.R" tells a reader to go and find a
    file they do not have. Every one of these words appeared on the old page.
    """
    out, _ = site
    html = (out / "about.html").read_text()
    for jargon in ("R script", ".R", "99th percentile", "benchmark_rate",
                   "half-PPR"):
        assert jargon not in html, jargon


def test_building_without_standings_says_what_to_run():
    store = open_store(":memory:")
    with pytest.raises(ValueError, match="simulate"):
        build(store, "2026-27", Path("/tmp/whul-site-test"))


def test_the_pages_are_self_contained(site):
    """No CDN, no framework: the site has to work offline and keep working
    when something upstream changes."""
    out, _ = site
    for page in out.rglob("*.html"):
        html = page.read_text()
        assert "http://" not in html
        assert "cdn" not in html.lower()


# --- managers and empty slots ----------------------------------------------

def test_pages_use_the_name_where_there_is_room(site):
    out, _ = site
    html = (out / "index.html").read_text()
    assert "Tyler" in html and "Shelby" in html


def test_a_badge_without_a_photo_shows_the_id_not_the_name(tmp_path):
    """A manager's id already is their initials, so deriving them from the
    display name would turn TG into T."""
    badge = images.avatar(
        "manager", "TG", "Tyler", slot=1, initials="TG", source=tmp_path
    )
    assert ">TG<" in badge


def test_a_supplied_manager_photo_replaces_the_badge(site):
    """The league has now supplied all five."""
    out, result = site
    assert result["photos"]["manager"] == 5
    assert 'img/manager/' in (out / "index.html").read_text()


def test_a_team_page_is_titled_with_the_managers_name(site):
    out, _ = site
    assert "<title>WHUL — Tyler</title>" in (out / "team" / "tg.html").read_text()


def test_an_unknown_manager_falls_back_to_their_id():
    """A roster file can name someone the config has not been told about; an id
    is better than a blank or a refused build."""
    from whul.config.league import manager_name

    assert manager_name("TG") == "Tyler"
    assert manager_name("ZZ") == "ZZ"


def test_an_undrafted_slot_is_shown_not_skipped(site):
    """It is a slot the manager still has to fill; hiding it would make a
    roster with a hole look complete."""
    out, _ = site
    pages = [(out / "team" / f"{m.lower()}.html").read_text() for m in simulate.MANAGERS]
    assert any("Undrafted" in page for page in pages)
    assert any("still to draft" in page for page in pages)


def test_an_undrafted_slot_never_becomes_a_player_called_nan(site):
    """SQL NULL reaches pandas as NaN, which is truthy -- so an empty slot used
    to pass the "is there anybody here" test and render as 'nan'."""
    out, _ = site
    for page in out.rglob("*.html"):
        assert ">nan<" not in page.read_text().lower(), page.name


def test_the_standings_total_ignores_empty_slots(site):
    """An empty slot scores nothing, and nothing is not a number to add.

    Checked as a whole cell rather than a substring -- a surname like Brennan
    contains the letters, and a test that fails on a real player is worse than
    no test."""
    out, _ = site
    html = (out / "index.html").read_text()
    assert ">nan<" not in html.lower()
    assert ">NaN<" not in html
    for row in re.findall(r"<td class='num'>([^<]*)</td>", html):
        assert row.strip().lower() != "nan"


def test_the_progression_chart_survives_the_seasons_first_day():
    """One day of scores is what the first live run produces, and it used to
    raise: the x-scale is floored at one step, so the axis asked for a second
    day that does not exist."""
    from datetime import date

    from whul.site import charts

    svg = charts.progression_chart(
        [date(2026, 8, 21)], [charts.Series("Tyler", 1, [12.5])]
    )
    assert "21 Aug 2026" in svg
    assert svg.count("21 Aug 2026") == 1, "one day is one label, not three"


def test_the_progression_chart_labels_both_ends_of_two_days():
    from datetime import date

    from whul.site import charts

    svg = charts.progression_chart(
        [date(2026, 8, 21), date(2026, 8, 22)],
        [charts.Series("Tyler", 1, [12.5, 20.0])],
    )
    assert "21 Aug 2026" in svg and "22 Aug 2026" in svg


# --- a build that cannot run --------------------------------------------

def test_a_failed_build_leaves_no_directory_behind(tmp_path):
    """`site/team/` was created before anything was checked, so a failed build
    left it there -- and a local server serves that as a directory listing: a
    site that looks built and is not."""
    from whul.site.build import build
    from whul.store import open_store

    out = tmp_path / "site"
    with pytest.raises(ValueError):
        build(open_store(":memory:"), "2026-27", out)
    assert not out.exists()


def test_an_empty_season_says_which_link_is_missing(tmp_path):
    """Every step depends on the one before, so "no standings" is the symptom
    of four different problems."""
    import pandas as pd

    from whul.site.build import build
    from whul.store import benchmarks as bm
    from whul.store import open_store, rosters

    store = open_store(":memory:")

    with pytest.raises(ValueError, match="Nothing is rostered"):
        build(store, "2026-27", tmp_path / "s")

    rosters.add_manager(store, "TG")
    rosters.create_slots(store, "TG", "2026-27")
    store.upsert("assets", [{
        "asset_id": "a", "asset_type": "Player", "display_name": "Someone",
        "league": "NFL", "role": "", "norm_key": "NFL", "active": 1,
        "created_at": "2026-08-21",
    }], keys=("asset_id",))
    slot = store.query(
        "SELECT slot_id FROM roster_slots WHERE season = ? LIMIT 1", ("2026-27",)
    ).loc[0, "slot_id"]
    rosters.assign(store, slot, "a", "2026-08-21")

    with pytest.raises(ValueError, match="no benchmark version is frozen"):
        build(store, "2026-27", tmp_path / "s")

    version = bm.save(store, pd.DataFrame([{
        "asset_type": "Player", "norm_key": "NFL", "benchmark": 100.0,
        "pool_size": 300, "seasons": "2025",
    }]), "2026-27")
    bm.freeze(store, version)

    with pytest.raises(ValueError, match="no results have been recorded"):
        build(store, "2026-27", tmp_path / "s")


# --- saying what the standings cannot yet speak for --------------------------

def test_the_about_page_names_what_is_not_yet_scored():
    """A standing built while a league has no benchmark is not wrong, but it is
    partial -- and it looks exactly like a complete one, because the managers
    holding those picks are simply lower. Saying so is the difference between a
    season in progress and a season being misreported."""
    from whul.site.build import _uncovered_note

    note = _uncovered_note([("Premier League", "Player", 10),
                            ("Men's Intl Soccer", "Team", 2)])
    assert "12 rostered pick" in note
    assert "Premier League" in note
    assert "shown lower than they will finish" in note


def test_a_fully_covered_version_says_nothing():
    """The note is for a real gap. Printed when there is none, it would teach
    everyone to ignore it."""
    from whul.site.build import _uncovered_note

    assert _uncovered_note([]) == ""


def test_the_note_cannot_fail_the_build(monkeypatch):
    """A page annotation is worth less than the site it annotates."""
    from whul import benchmarks as benchmark_method
    from whul.site import build as site_build

    monkeypatch.setattr(
        benchmark_method, "coverage",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    class Version:
        version = "v1"

    assert site_build._uncovered(None, "2026-27", Version()) == []


# --- a category reads as a block ---------------------------------------------

def test_a_managers_slots_in_a_category_sit_together():
    """NFL 1 and NFL 2 adjacent in that manager's colour, then the next
    manager's two. Ordered the other way -- every manager's first slot, then
    every manager's second -- a category cannot be read as a block, because
    each manager's holding is split across the width of the chart."""
    import re

    from whul.site import charts

    values = {
        ("Avery", "NFL 1"): (100.0, "a1", "P. Vance"),
        ("Avery", "NFL 2"): (80.0, "a2", "R. Ellis"),
        ("Blake", "NFL 1"): (90.0, "b1", "K. Shaw"),
        ("Blake", "NFL 2"): (70.0, "b2", "T. Moss"),
    }
    rows = [("NFL", "NFL 1", "#1"), ("NFL", "NFL 2", "#2")]
    svg = charts.contribution_chart(rows, [("Avery", 1), ("Blake", 2)], values,
                                    depth={"NFL": 2})

    order = re.findall(r'data-manager="([^"]+)" data-category="([^"]+)"', svg)
    assert order == [
        ("Avery", "NFL 1"), ("Avery", "NFL 2"),
        ("Blake", "NFL 1"), ("Blake", "NFL 2"),
    ]


def test_a_managers_slots_share_one_colour():
    from whul.site import charts

    values = {("Avery", "NFL 1"): (100.0, "a1", "x"),
              ("Avery", "NFL 2"): (80.0, "a2", "y")}
    svg = charts.contribution_chart(
        [("NFL", "NFL 1", "#1"), ("NFL", "NFL 2", "#2")],
        [("Avery", 1)], values, depth={"NFL": 2})
    assert svg.count("var(--series-1)") == 2


def test_the_rank_is_labelled_so_a_bar_says_which_slot_it_is():
    """Colour says whose the bar is; without this nothing says whether it is
    their best holding in the category or their fourth."""
    from whul.site import charts

    values = {("Avery", "NFL 1"): (100.0, "a1", "x"),
              ("Avery", "NFL 2"): (80.0, "a2", "y")}
    svg = charts.contribution_chart(
        [("NFL", "NFL 1", "#1"), ("NFL", "NFL 2", "#2")],
        [("Avery", 1)], values, depth={"NFL": 2})
    assert ">#1 x<" in svg and ">#2 y<" in svg


def test_a_single_slot_category_is_not_labelled_with_a_rank():
    """"#1" under a category with one slot is a column of ones."""
    from whul.site import charts

    svg = charts.contribution_chart(
        [("PGA", "PGA 1", "#1")], [("Avery", 1)],
        {("Avery", "PGA 1"): (50.0, "p1", "x")}, depth={"PGA": 1})
    assert ">#1<" not in svg


def test_a_benched_slot_strikes_the_score_and_not_the_name():
    """The player is not crossed out, their contribution is -- and a struck
    name reads like a player who has been dropped rather than one whose week
    was someone else's."""
    from whul.site.build import _asset_button

    assert "struck" not in _asset_button("a1", "P. Vance", counts=False)


def test_the_header_carries_the_leagues_full_name():
    """"WHUL" is unreadable to anyone outside the league.

    Asserted on the rendered page rather than on the constant. The constant was
    right the whole time the header was wrong: the name was in the markup and
    then hidden by CSS below 640px, so every phone showed the initials and this
    test passed anyway.
    """
    from whul.config.league import LEAGUE_ABBR, LEAGUE_NAME
    from whul.site.build import _page

    assert LEAGUE_NAME == "Wolf Hill Uber League"
    assert LEAGUE_ABBR == "WHUL"

    page = _page("Standings", "<p>body</p>", "Standings", [])
    header = page[page.index("<h1>"):page.index("</h1>")]
    assert LEAGUE_NAME in header
    assert LEAGUE_ABBR not in header


def test_no_width_hides_the_leagues_name():
    """The name is 207px at the masthead's 20px, and the narrowest phone still
    in use leaves 280px inside the wrapper -- so there is no width at which the
    initials are needed, and no rule may hide it at one."""
    from whul.site.theme import STYLESHEET

    assert ".masthead h1 .short" not in STYLESHEET
    assert ".masthead h1 .full" not in STYLESHEET


# --- what the profile window says --------------------------------------------

def test_a_stat_is_labelled_in_words():
    """`games_played` reads as debug output."""
    from whul.site.build import _label_for

    assert _label_for("games_played") == "Games played"
    assert _label_for("reg_big_wins") == "Big wins"
    assert _label_for("point_diff") == "Point differential"
    # An unmapped column still reads as English rather than as a column name.
    assert _label_for("some_new_thing") == "Some new thing"


def test_every_figure_behind_the_score_is_shown():
    """The window used to carry five columns because that is what the aggregate
    kept. What it should carry is everything that went into the raw score."""
    from whul.site.build import _stat_lines

    row = {
        "asset_id": "a1", "league": "NFL", "season": "2026-27", "source": "nfl",
        "total_points": 210.0, "reg_wins": 11, "reg_big_wins": 4,
        "div_wins": 5, "point_diff": 88.0, "playoff_wins": 2,
    }
    labels = dict(_stat_lines(row))
    assert "Regular-season wins" in labels and labels["Regular-season wins"] == "11"
    assert "Point differential" in labels
    assert "Playoff wins" in labels
    # Identity columns are not statistics.
    assert "Asset id" not in labels and "League" not in labels


def test_a_prorated_score_says_so():
    """A prorated figure looks like an ordinary one, and a manager checking it
    against a box score would find it does not reconcile."""
    from whul.site.build import _scaling_notes

    notes = _scaling_notes({"proration_factor": 1.218})
    assert len(notes) == 1
    assert "1.218" in notes[0]
    assert "One-off achievements are not scaled" in notes[0]


def test_an_unscaled_score_says_nothing():
    """A note printed when nothing was scaled teaches everyone to ignore it."""
    from whul.site.build import _scaling_notes

    assert _scaling_notes({"proration_factor": 1.0}) == []
    assert _scaling_notes({"total_points": 5.0}) == []


def test_a_schedule_scaled_benchmark_says_so():
    from whul.site.build import _scaling_notes

    notes = _scaling_notes({"schedule_factor": 1.024})
    assert notes and "1.024" in notes[0]


def test_the_finish_list_survives_the_round_trip_through_the_database():
    """The stats payload is stored as JSON, so a list comes back as a string."""
    import json

    from whul.site.build import _finish_list

    finishes = [{"label": "ATP Winston Salem 250 F", "points": 150.0,
                 "date": "2026-08-22"}]
    assert _finish_list({"finishes": finishes}) == finishes
    assert _finish_list({"finishes": json.dumps(finishes)}) == finishes
    assert _finish_list({}) == []
    assert _finish_list({"finishes": "not json"}) == []


# --- collapsing and filtering -------------------------------------------------

SECTION_ROWS = [("NFL", "NFL 1", "#1"), ("NFL", "NFL 2", "#2"),
                ("PGA", "PGA 1", "#1")]
SECTION_VALUES = {
    ("Avery", "NFL 1"): (100.0, "a1", "P. Vance"),
    ("Avery", "NFL 2"): (60.0, "a2", "R. Ellis"),
    ("Avery", "PGA 1"): (40.0, "a3", "S. Kerr"),
}


def test_each_league_is_its_own_collapsible_section():
    """Twenty leagues open at once is a page that is long before it is
    informative."""
    from whul.site import charts

    html = charts.slot_sections(SECTION_ROWS, [("Avery", 1)], SECTION_VALUES,
                                depth={"NFL": 2, "PGA": 1})
    assert html.count("<details class=\"leaguebox\"") == 2
    assert 'data-league="NFL"' in html and 'data-league="PGA"' in html


def test_sections_open_by_default_and_work_without_script():
    """`<details>` collapses with no JavaScript, which is what a static site
    wants; the script only remembers what a reader left closed."""
    from whul.site import charts

    html = charts.slot_sections(SECTION_ROWS, [("Avery", 1)], SECTION_VALUES)
    assert " open>" in html


def test_every_section_shares_one_ceiling():
    """A per-section ceiling would make two sections' bars look alike at
    different scores, which is the one thing a common scale is for."""
    from whul.site import charts

    html = charts.slot_sections(SECTION_ROWS, [("Avery", 1)], SECTION_VALUES,
                                depth={"NFL": 2, "PGA": 1})
    # The 100-point bar is full width in its section; the 40-point one is not.
    full = 1000 - charts.SLOT_LABEL_WIDTH - 56
    assert html.count(f'width="{full:.1f}"') == 1


def test_a_filterable_legend_is_made_of_buttons():
    """So the filter is reachable from a keyboard."""
    from whul.site import charts

    html = charts.legend([("Avery", 1)], filterable=True)
    assert "<button" in html and 'data-manager="Avery"' in html
    assert 'aria-pressed="false"' in html


def test_a_plain_legend_is_still_just_a_key():
    from whul.site import charts

    assert "<button" not in charts.legend([("Avery", 1)])


def test_a_progression_line_is_addressable_by_manager():
    """Hiding a manager has to reach the line, its end marker and its label --
    a hidden line with a floating label reads as a bug."""
    from datetime import date

    from whul.site import charts

    svg = charts.progression_chart(
        [date(2026, 8, 21), date(2026, 8, 22)],
        [charts.Series("Avery", 1, [1.0, 2.0])],
    )
    assert svg.count('data-manager="Avery"') >= 3


def test_a_shared_out_run_value_says_so():
    """A run value that looks measured and is not is the kind of number
    somebody checks against Baseball Reference and cannot find."""
    from whul.site.build import _scaling_notes, _stat_lines

    notes = _scaling_notes({"advanced_share": 0.13})
    assert len(notes) == 1
    assert "13%" in notes[0]
    assert "shared out" in notes[0]
    # And the share itself is not a statistic in the table.
    assert "Advanced share" not in dict(_stat_lines({"advanced_share": 0.13}))


def test_a_full_season_share_says_nothing():
    from whul.site.build import _scaling_notes

    assert _scaling_notes({"advanced_share": 1.0}) == []


# --- the results page ---------------------------------------------------------

import pandas as pd

RESULT_BARS = pd.DataFrame([
    {"manager_id": "AV", "asset_id": "a1", "score": 40.0},
    {"manager_id": "BL", "asset_id": "a2", "score": 90.0},
    {"manager_id": "AV", "asset_id": "a3", "score": 0.0},
])
RESULT_PROFILES = {
    "a1": {"name": "P. Vance", "league": "NFL", "kind": "Player"},
    "a2": {"name": "Arsenal", "league": "Premier League", "kind": "Team"},
    "a3": {"name": "Q. Idle", "league": "NBA", "kind": "Player"},
}


def test_the_results_table_leads_with_the_best_score():
    """The bar chart answers "how is my roster doing"; this answers "who is
    doing well", which is a different question and had no page."""
    from whul.site.build import _results_table

    html = _results_table(RESULT_BARS, RESULT_PROFILES, ["AV", "BL"])
    assert html.index("Arsenal") < html.index("P. Vance") < html.index("Q. Idle")


def test_every_row_carries_what_it_can_be_filtered_by():
    from whul.site.build import _results_table

    html = _results_table(RESULT_BARS, RESULT_PROFILES, ["AV", "BL"])
    assert 'data-league="Premier League" data-kind="Team"' in html
    assert 'data-filter="kind" data-value="Player"' in html
    assert 'data-filter="league" data-value="NFL"' in html


def test_an_asset_on_nothing_yet_is_listed_rather_than_dropped():
    """Two thirds of the roster is on nothing in September, when most leagues
    have not started. Listing them is the honest default; the toggle is what
    makes the table useful anyway."""
    from whul.site.build import _results_table

    html = _results_table(RESULT_BARS, RESULT_PROFILES, ["AV", "BL"])
    assert "Q. Idle" in html
    assert 'data-filter="scoring"' in html
    assert 'data-score="0.0000"' in html


def test_a_name_in_the_table_opens_its_profile():
    """The same hook the bars use, so one handler serves both."""
    from whul.site.build import _results_table

    html = _results_table(RESULT_BARS, RESULT_PROFILES, ["AV", "BL"])
    assert 'class="assetlink" data-asset="a2"' in html


def test_a_figure_is_collapsible_and_addressable():
    """Three figures on one page is long, and someone who wants the table
    should not scroll past two charts to reach it."""
    from whul.site.build import _figure, _figure_index

    html = _figure("everyone", "Every scored asset", "Best first.", "<p>x</p>")
    assert 'id="everyone"' in html and 'data-figure="everyone"' in html
    assert html.startswith("\n<details") and " open>" in html
    assert "#everyone" in _figure_index([("everyone", "Every scored asset")])


# --- the counting mix ---------------------------------------------------------

MIX_BARS = pd.DataFrame([
    {"manager_id": "AV", "asset_id": f"a{i}", "score": float(s),
     "category": c, "counts": True}
    for i, (c, s) in enumerate([
        ("NFL QB", 90), ("NFL RB", 80), ("NBA Guard", 70), ("MLB SP", 60),
        ("NHL C", 50), ("Golf", 40), ("Tennis", 30), ("Club Soccer", 20),
    ])
])
MIX_PROFILES = {f"a{i}": {"name": f"Name {i}"} for i in range(8)}


def test_a_benched_or_scoreless_slot_is_not_part_of_the_mix():
    """The ring has to add up to the counting total printed beside it. A
    benched slot is not in that total, so a wedge for it would make the ring
    say something the number does not."""
    from whul.site.build import _counting_mix

    bars = MIX_BARS.copy()
    bars.loc[0, "counts"] = False
    bars.loc[1, "score"] = 0.0
    mix = _counting_mix(bars, MIX_PROFILES)
    assert "NFL QB" not in dict(mix)
    assert "NFL RB" not in dict(mix)


def test_the_mix_never_exceeds_six_segments():
    """Past six, angles stop being comparable and adjacent hues blur. The tail
    folds into one "Other" rather than growing a seventh hue -- a manager has
    nine contributing categories now and twice that once every league is in."""
    from whul.site.build import _counting_mix
    from whul.site import charts

    mix = _counting_mix(MIX_BARS, MIX_PROFILES)
    assert len(mix) == charts.DONUT_SEGMENTS
    assert mix[-1][0] == charts.DONUT_OTHER
    # Nothing is dropped on the way into "Other".
    assert sum(len(holdings) for _, holdings in mix) == len(MIX_BARS)


def test_the_mix_is_ordered_by_what_each_category_contributes():
    """Named categories descend. "Other" stays last however big it grows: it is
    a remainder rather than a category, and sorting it into the middle would
    put a bucket of eight leagues between two single ones."""
    from whul.site.build import _counting_mix
    from whul.site import charts

    mix = _counting_mix(MIX_BARS, MIX_PROFILES)
    named = [sum(s for _, s, _ in holdings) for name, holdings in mix
             if name != charts.DONUT_OTHER]
    assert named == sorted(named, reverse=True)
    assert mix[-1][0] == charts.DONUT_OTHER


def test_a_category_is_one_hue_and_its_holdings_step_down_in_shade():
    """Alpha within a hue is what says "these three are all NFL QBs" without
    spending three hues on it. It is not readable as a quantity, which is why
    the wedge names itself on hover and the table carries the numbers."""
    from whul.site import charts

    html = charts.donut_chart(
        [("NFL QB", [("A", 60.0, "a1"), ("B", 30.0, "a2")]),
         ("Golf", [("C", 10.0, "a3")])],
        100.0,
    )
    assert html.count('fill="var(--series-1)"') == 2
    assert html.count('fill="var(--series-2)"') == 1
    alphas = re.findall(r'fill="var\(--series-1\)" fill-opacity="([\d.]+)"', html)
    assert float(alphas[0]) > float(alphas[1]) >= charts.DONUT_MIN_ALPHA


def test_a_lone_holding_is_not_faded():
    """A category of one has no ordering to convey, and fading it would read as
    a category that is somehow half-present."""
    from whul.site import charts

    html = charts.donut_chart([("Golf", [("C", 10.0, "a3")])], 10.0)
    assert 'fill-opacity="1.00"' in html


def test_every_wedge_says_who_it_is_and_opens_its_profile():
    from whul.site import charts

    html = charts.donut_chart([("NFL QB", [("P. Vance", 60.0, "a1")])], 60.0)
    assert 'data-asset="a1"' in html
    assert 'data-name="P. Vance"' in html
    assert 'data-category="NFL QB"' in html


def test_the_categories_are_named_on_the_figure_and_in_a_table():
    """The palette check warns on contrast at this surface, which obligates
    visible labels or a table view. This ships both: colour is never the only
    thing carrying an identity."""
    from whul.site import charts

    html = charts.donut_chart(
        [("NFL QB", [("A", 60.0, "a1")]), ("Golf", [("C", 40.0, "a3")])], 100.0)
    assert html.count("NFL QB") >= 2 and html.count("Golf") >= 2
    assert 'class="mixtable"' in html
    assert "60%" in html and "40%" in html


def test_a_roster_on_nothing_yet_says_so_rather_than_drawing_an_empty_ring():
    from whul.site import charts

    assert "Nothing counting yet" in charts.donut_chart([], 0.0)
    assert "Nothing counting yet" in charts.donut_chart(
        [("Golf", [("C", 0.0, "a3")])], 0.0)


def test_the_remainder_is_neutral_rather_than_a_sixth_hue():
    """Two reasons that agree. "Other" is a bucket, not a category, so it
    should recede next to the five things being compared -- and the palette
    will not carry six hues in a ring where any wedge may be matched against
    any other. Validated all-pairs, the sixth hue is 3.2 from the second for a
    protanope. Five hues plus a neutral passes."""
    from whul.site import charts

    html = charts.donut_chart(
        [("NFL QB", [("A", 60.0, "a1")]),
         (charts.DONUT_OTHER, [("C", 40.0, "a3")])],
        100.0,
    )
    assert f'fill="{charts.DONUT_OTHER_FILL}"' in html
    assert "--series-2" not in html


def test_the_share_table_reads_in_the_order_the_ring_is_drawn():
    """So a row is found by position rather than by matching a swatch to a
    hue. Nobody should have to tell the pink from the orange to read this."""
    from whul.site import charts

    parts = [("NFL QB", [("A", 60.0, "a1")]), ("Golf", [("C", 30.0, "a3")]),
             ("MLB SP", [("D", 10.0, "a4")])]
    html = charts.donut_chart(parts, 100.0)
    table = html[html.index('class="mixtable"'):]
    assert table.index("NFL QB") < table.index("Golf") < table.index("MLB SP")


# --- how much of a roster is in season ----------------------------------------

SEASON_BARS = pd.DataFrame([
    # An injured NFL back: the league has started and the slot is in it.
    {"asset_id": "n1", "score": 0.0, "counts": True},
    {"asset_id": "n2", "score": 30.0, "counts": True},
    # The NBA has not tipped off.
    {"asset_id": "b1", "score": 0.0, "counts": True},
    # An international squad, whose tournament has not come round.
    {"asset_id": "i1", "score": 0.0, "counts": True},
    # A bench slot, and an undrafted one. Neither is in the counting total.
    {"asset_id": "n3", "score": 90.0, "counts": False},
    {"asset_id": "", "score": 0.0, "counts": True},
])
SEASON_PROFILES = {
    "n1": {"name": "A", "league": "NFL"}, "n2": {"name": "B", "league": "NFL"},
    "b1": {"name": "C", "league": "NBA"}, "i1": {"name": "D", "league": "Men's Intl Soccer"},
    "n3": {"name": "E", "league": "NFL"},
}


def test_an_injured_player_is_still_in_season():
    """The badge is asked of the calendar, not of the scores. Counting non-zero
    scores would file an injury, a benching and a quiet week under "not
    started", so a manager whose back is hurt would look like one who never
    drafted a back."""
    from whul.site.build import _in_season

    live, filled = _in_season(SEASON_BARS, SEASON_PROFILES, date(2026, 9, 15))
    assert (live, filled) == (2, 4)   # both NFL slots; not the NBA or the squad


def test_a_league_that_has_not_started_is_not_counted():
    from whul.site.build import _in_season

    assert _in_season(SEASON_BARS, SEASON_PROFILES, date(2026, 9, 1))[0] == 0
    assert _in_season(SEASON_BARS, SEASON_PROFILES, date(2026, 11, 1))[0] == 3


def test_a_squad_with_no_start_date_is_read_from_its_score():
    """Each international squad plays in a different competition on a different
    calendar, so there is no date the slot begins on. A score is the only
    evidence there is."""
    from whul.site.build import _in_season

    bars = SEASON_BARS.copy()
    bars.loc[bars["asset_id"] == "i1", "score"] = 12.0
    live, _ = _in_season(bars, SEASON_PROFILES, date(2026, 9, 15))
    assert live == 3


def test_a_bench_or_undrafted_slot_is_not_part_of_the_count():
    """The badge explains the counting total, so it counts what the total is
    made of."""
    from whul.site.build import _in_season

    assert _in_season(SEASON_BARS, SEASON_PROFILES, date(2027, 3, 1))[1] == 4


def test_the_note_says_what_the_total_is_missing():
    from whul.site.build import _season_note

    assert _season_note(2, 4) == "2 of 4 slots in season"
    assert _season_note(4, 4) == "every slot in season"
    assert _season_note(0, 0) == "nothing drafted yet"


def test_no_css_escape_is_eaten_by_python_first():
    """This stylesheet is a Python string, and Python reads a backslash in one
    long before CSS does. `content: "\\25be"` -- a perfectly good CSS escape for
    the disclosure triangle -- arrived at the browser as control character 0x15
    followed by the letters "be", and every figure's arrow read "?be" on the
    live site for as long as the figures existed."""
    from whul.site import theme

    for bad in range(0x00, 0x20):
        if chr(bad) in ("\n", "\t"):
            continue
        assert chr(bad) not in theme.STYLESHEET, f"control character {bad:#04x}"


# --- what a name is -----------------------------------------------------------

def test_a_position_is_read_from_whichever_word_the_feed_uses():
    """`position` where a sport has them, `role` where the distinction is the
    sport itself. Both answer the question a reader is asking."""
    from whul.site.build import _identity

    assert _identity({"position": "F"}, "Premier League", "")["position"] == "F"
    assert _identity({"role": "Batter"}, "MLB", "")["position"] == "Batter"
    # A position the scorer computed wins over a role that merely names the feed.
    assert _identity({"position": "M", "role": "Outfield"}, "", "")["position"] == "M"


def test_a_missing_figure_is_absent_rather_than_the_word_nan():
    """Most of these are empty most of the season, and a page of names each
    followed by "nan" reads as broken rather than early."""
    from whul.site.build import _identity

    who = _identity({"position": float("nan"), "team": "None"}, "NFL", "")
    assert who["position"] == "" and who["team"] == ""


def test_the_group_line_is_dropped_when_it_only_repeats_the_league():
    """An italic line reading "Premier League" under a line reading "Premier
    League" is furniture. A soccer player's benchmark group is his league."""
    from whul.site.build import _identity

    assert _identity({}, "Premier League", "Premier League")["group"] == ""
    assert _identity({}, "NFL", "NFL_QB")["group"] == "NFL QB"


def test_a_name_with_nothing_known_about_it_is_just_a_name():
    """No stray separators for a league that has not started."""
    from whul.site.build import _identity_lines

    assert _identity_lines(None) == ""
    assert _identity_lines({"position": "", "team": "", "group": ""}) == ""


def test_the_lines_run_position_then_club_then_whatever_was_asked_for():
    from whul.site.build import _identity_lines

    html = _identity_lines(
        {"position": "F", "team": "Arsenal", "group": "NFL QB"}, "Premier League")
    assert '<span class="idl">F · Arsenal · Premier League</span>' in html
    assert '<em class="grp">NFL QB</em>' in html


def test_a_roster_row_carries_the_position_without_a_click():
    """Sixty names on a page, and reading it should not need sixty clicks."""
    from whul.site.build import _asset_button

    html = _asset_button("a1", "Bukayo Saka", profile={
        "position": "F", "team": "Arsenal", "group": ""})
    assert "Bukayo Saka" in html and "F · Arsenal" in html


def test_the_donut_pushes_two_labels_off_each_other():
    """Two small categories side by side put their labels at nearly the same
    angle. "MotorsportsTennis" was the first pair to do it."""
    from whul.site import charts

    parts = [("Big", [("A", 94.0, "a1")]),
             ("Motorsports", [("B", 3.0, "a2")]),
             ("Tennis", [("C", 3.0, "a3")])]
    ys = [float(m) for m in re.findall(
        r'<text x="[-\d.]+" y="([-\d.]+)"[^>]*font-size="10"',
        charts.donut_chart(parts, 100.0))]
    assert all(abs(a - b) >= charts.DONUT_LABEL_GAP - 0.01
               for i, a in enumerate(ys) for b in ys[i + 1:])


# --- naming images by hand ----------------------------------------------------

def test_an_accented_id_is_found_under_its_plain_spelling(tmp_path):
    """Asset ids keep the spelling the league drafted, and `kylian-mbappé` is
    what the rest of the engine speaks. Asking someone to type that as a
    filename eighty times is asking for a file that looks right and is never
    found: a precomposed é, a combining accent and whatever the file manager
    did on the way through all fail identically and silently."""
    from whul.site import images

    folder = tmp_path / "asset"
    folder.mkdir()
    (folder / "player-la-liga-kylian-mbappe.png").write_bytes(b"x")

    found = images.find("asset", "player-la-liga-kylian-mbappé", source=tmp_path)
    assert found is not None and found.name.endswith("mbappe.png")


def test_the_exact_spelling_wins_where_both_exist():
    """That one was deliberate."""
    from whul.site import images

    assert images.plain("kylian-mbappé") == "kylian-mbappe"
    assert images.plain("arsenal") == "arsenal"


def test_a_corner_badge_has_a_directory_of_its_own():
    """"England" is a country and an international side. A shared directory
    would hand whichever asked first a file meant for the other."""
    from whul.site import images

    for kind in ("club", "flag", "shield"):
        assert kind in images.KINDS


def test_a_missing_image_directory_says_so_rather_than_reporting_nothing_found(tmp_path):
    """`assets/img` is a relative path. Run from anywhere but the repository
    root it resolves to nothing and all four hundred files read as missing,
    which is indistinguishable from having fetched none of them -- and was read
    that way. The directory it looked in is now always printed, and its absence
    is called out rather than left to be inferred from a round number."""
    import contextlib
    import io
    from types import SimpleNamespace

    from whul import simulate
    from whul.cli import cmd_images_needed
    from whul.store import open_store

    db = tmp_path / "whul.sqlite3"
    store = open_store(str(db))
    simulate.generate(store, seed=1, verbose=False)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cmd_images_needed(SimpleNamespace(
            db=str(db), season=simulate.SIM_SEASON, images=str(tmp_path / "nowhere")))
    said = out.getvalue()
    assert "nowhere" in said
    assert "does not exist" in said


# --- which name a crest is filed under ----------------------------------------

def _badge_store(tmp_path, affiliation, feed_team=None):
    from datetime import date

    from whul.store import open_store, rosters

    store = open_store(str(tmp_path / "b.sqlite3"))
    rosters.add_manager(store, "JM")
    rosters.create_slots(store, "JM", "2026-27")
    store.upsert("assets", [{
        "asset_id": "p1", "asset_type": "Player", "display_name": "A Player",
        "league": "Premier League", "role": "", "norm_key": "Premier League",
        "affiliation": affiliation, "active": 1, "created_at": "2026-08-21",
    }], keys=("asset_id",))
    slot = store.query(
        "SELECT slot_id FROM roster_slots WHERE season = ? AND category = ? "
        "AND asset_type = 'Player'", ("2026-27", "Club Soccer Top 3"))
    rosters.assign(store, slot.loc[0, "slot_id"], "p1", "2026-08-21")
    if feed_team is not None:
        store.record_stats(
            [{"asset_id": "p1", "team": feed_team, "total_points": 1.0}],
            source="s", season="2026-27", as_of=date(2026, 9, 6),
            league="Premier League",
        )
    return store


def test_the_feeds_spelling_of_a_club_beats_the_sheets(tmp_path):
    """The feed notices a transfer, and its name is ESPN's own -- so a crest
    looked up under it matches by construction. The sheet said "Los Angeles
    Clippers" and ESPN files them as "LA Clippers": a perfectly correct name
    that found nothing at all."""
    from whul.site.build import badge_names

    store = _badge_store(tmp_path, "Rennes", feed_team="Stade Rennais")
    assert badge_names(store, "2026-27")["p1"] == "Stade Rennais"


def test_the_sheet_answers_where_the_feed_has_nothing(tmp_path):
    """Every golfer's country, and every league that has not kicked off."""
    from whul.site.build import badge_names

    store = _badge_store(tmp_path, "Los Angeles Clippers")
    assert badge_names(store, "2026-27")["p1"] == "Los Angeles Clippers"


def test_an_asset_neither_knows_about_is_left_out(tmp_path):
    """Not badged, rather than badged with an empty string -- which would ask
    for a file called `.png`."""
    from whul.site.build import badge_names

    store = _badge_store(tmp_path, "")
    assert "p1" not in badge_names(store, "2026-27")


# --- the corner badge ---------------------------------------------------------

def test_each_kind_of_asset_takes_the_badge_its_spec_says():
    """Which directory the corner comes from follows from what the asset is, so
    there is nothing to choose and nothing to keep in step."""
    from whul.site.build import corner_badge

    assert corner_badge("Player", "Club Soccer Top 3", "Premier League",
                        "Arsenal") == ("club", "arsenal")
    assert corner_badge("Player", "PGA", "PGA", "Spain") == ("flag", "spain")
    assert corner_badge("Team", "NFL", "NFL", "") == ("badge", "nfl")
    assert corner_badge("Team", "Intl Soccer", "Men's Intl Soccer",
                        "Brazil") == ("shield", "conmebol")


def test_a_country_with_no_confederation_gets_no_shield():
    """A wrong shield is worse than none, and the table is not exhaustive."""
    from whul.site.build import corner_badge

    assert corner_badge("Team", "Intl Soccer", "Men's Intl Soccer", "Atlantis") is None
    assert corner_badge("Player", "NBA", "NBA", "") is None


def test_the_badge_is_drawn_on_a_monogram_too(tmp_path):
    """A player with no photograph still plays for somebody, and of that pair
    the crest is the more useful half."""
    from whul.site import images

    (tmp_path / "club").mkdir()
    (tmp_path / "club" / "arsenal.png").write_bytes(b"x")
    html = images.avatar("asset", "nobody", "A Player", size=26,
                         source=tmp_path, badge=("club", "arsenal"))
    assert "avatar mono" in html and 'class="pip"' in html


def test_a_badge_nobody_supplied_leaves_the_picture_alone(tmp_path):
    from whul.site import images

    html = images.avatar("asset", "nobody", "A Player", size=26,
                         source=tmp_path, badge=("club", "arsenal"))
    assert "pip" not in html


def test_a_badge_too_small_to_read_is_not_drawn(tmp_path):
    """A crest at nine pixels is a smudge that costs a request and says
    nothing, and the row it would sit in names the club anyway."""
    from whul.site import images

    (tmp_path / "club").mkdir()
    (tmp_path / "club" / "arsenal.png").write_bytes(b"x")
    small = images.avatar("asset", "x", "A", size=images.BADGE_FLOOR - 1,
                          source=tmp_path, badge=("club", "arsenal"))
    big = images.avatar("asset", "x", "A", size=images.BADGE_FLOOR,
                        source=tmp_path, badge=("club", "arsenal"))
    assert "pip" not in small and "pip" in big


def test_a_finishing_position_is_not_printed_where_a_role_belongs():
    """Golf files a leaderboard place under the same key a soccer feed uses for
    "F". Rory McIlroy read "14.0 · Northern Ireland" on the live site."""
    from whul.site.build import _identity

    assert _identity({"position": 14.0, "role": "Golfer"}, "PGA", "")["position"] \
        == "Golfer"
    assert _identity({"position": "F"}, "Premier League", "")["position"] == "F"


# --- what a day was made of -------------------------------------------------

def test_a_day_line_is_the_day_and_not_the_season():
    """The feeds report season to date. Shown cumulatively the line said
    "Hits 25, Home runs 13" under a score that moved by one -- true, and not an
    answer to what happened."""
    from whul.site.build import _day_line

    now = {"h": 25.0, "ab": 88.0, "hr": 13.0, "games": 40.0}
    before = {"h": 23.0, "ab": 84.0, "hr": 12.0, "games": 39.0}
    assert _day_line(now, before) == ["2-for-4", "1 HR", "Games 1"]


def test_a_batter_reads_as_a_batter_and_a_pitcher_as_a_pitcher():
    """Baseball has its own shorthand and it is shorter than the words. `h` is
    hits allowed in a pitching line, so put anywhere but after the innings it
    reads as four hits made rather than four given up."""
    from whul.site.build import _day_line

    batter = _day_line({"h": 2.0, "ab": 4.0, "bb": 1.0}, {})
    assert batter[0] == "2-for-4" and "1 BB" in batter

    pitcher = _day_line({"ip": 5.33, "h": 4.0, "so": 2.0}, {})
    assert pitcher[0] == "5.1 IP" and pitcher[1] == "4 H" and "2 K" in pitcher


def test_innings_are_thirds_not_decimals():
    """The feed stores thirds as decimals, so differencing them is arithmetic
    and correct -- and printing the result is not. A day's 5.33 rounds to
    "5.3", and baseball has no such figure."""
    from whul.site.build import _innings

    assert _innings(5.33) == "5.1"
    assert _innings(5.67) == "5.2"
    assert _innings(6.0) == "6.0"


def test_a_rate_is_not_differenced():
    """Yesterday's subtracted from today's is not what happened today -- it is
    noise with a plausible magnitude, which is worse than nothing."""
    from whul.site.build import _day_line

    line = _day_line({"offense": 1.26, "war": 3.1, "hr": 2.0},
                     {"offense": 1.10, "war": 2.9, "hr": 1.0})
    assert line == ["1 HR"]


def test_a_day_with_no_previous_stats_says_nothing_rather_than_a_season():
    """`raw_stats` reaches back only as far as the first nightly run while
    `slot_scores` were backfilled, so the earliest listed days have a score to
    compare and no stats to compare. Subtracting nothing from a season printed
    a whole year as one day."""
    from whul.site.build import _day_line

    season = {"ip": 19.0, "h": 15.0, "so": 9.0}
    assert _day_line(season, {}, comparable=False) == []
    # ...but an asset that had no score at all before is genuinely new, and
    # then the total really is the day.
    assert _day_line(season, {}, comparable=True)[0] == "19.0 IP"


def test_a_conference_id_is_not_a_statistic():
    """ESPN's conference is a number, so a college team's line read
    "Conference 5" beside its wins -- neither a figure anyone can check nor one
    that went into the score."""
    from whul.site.build import _stat_lines

    labels = [label for label, _ in _stat_lines({"conference": "5", "wins": 1.0})]
    assert labels == ["Wins"]


def test_a_table_cell_is_only_clickable_where_something_moved():
    """Marking every cell promises a breakdown for days nothing happened on,
    which is a click that opens an empty panel."""
    from whul.site.build import _table_view

    html = _table_view(
        "Show as a table", ["Date", "TG"], [["2026-09-04", "10.0"],
                                            ["2026-09-05", "10.0"]],
        columns=["TG"], breakdown=[["TG|2026-09-04"], [""]],
    )
    assert 'data-day="TG|2026-09-04"' in html
    assert html.count("daycell") == 1
    assert "<td class='num'>10.0</td>" in html, "the quiet day stays a plain cell"


def test_a_day_breakdown_names_what_moved_and_by_how_much(tmp_path):
    """The progression table gives a total by date and says nothing about how
    it got there. This is the answer to "what happened on the 6th"."""
    import json

    from whul.site.build import _day_breakdown
    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("managers", [{"manager_id": "TG", "display_name": "TG",
                               "active": 1}], ["manager_id"])
    store.upsert("roster_slots", [
        {"slot_id": "s1", "manager_id": "TG", "season": "2026-27",
         "category": "NFL", "asset_type": "Team", "slot_index": 1},
        {"slot_id": "s2", "manager_id": "TG", "season": "2026-27",
         "category": "NFL", "asset_type": "Team", "slot_index": 2},
    ], ["slot_id"])
    store.upsert("slot_scores", [
        {"slot_id": "s1", "season": "2026-27", "as_of": "2026-09-05",
         "asset_id": "a1", "score": 10.0, "counts": 1},
        {"slot_id": "s2", "season": "2026-27", "as_of": "2026-09-05",
         "asset_id": "a2", "score": 5.0, "counts": 1},
        {"slot_id": "s1", "season": "2026-27", "as_of": "2026-09-06",
         "asset_id": "a1", "score": 18.0, "counts": 1},
        {"slot_id": "s2", "season": "2026-27", "as_of": "2026-09-06",
         "asset_id": "a2", "score": 5.0, "counts": 1},
    ], ["slot_id", "as_of"])
    store.upsert("assets", [
        {"asset_id": "a1", "asset_type": "Team", "display_name": "Bills",
         "league": "NFL", "norm_key": "NFL", "created_at": "2026-08-21"},
        {"asset_id": "a2", "asset_type": "Team", "display_name": "Jets",
         "league": "NFL", "norm_key": "NFL", "created_at": "2026-08-21"},
    ], ["asset_id"])
    store.upsert("raw_stats", [{
        "asset_id": "a1", "league": "NFL", "season": "2026-27",
        "as_of": "2026-09-06", "source": "test", "phase": "regular",
        "stats": json.dumps({"wins": 2.0, "reg_big_wins": 1.0}),
        "fetched_at": "2026-09-06T09:00:00Z",
    }], ["asset_id", "season", "as_of", "source", "phase"])

    out = _day_breakdown(store, "2026-27", ["2026-09-05", "2026-09-06"], ["TG"])
    day = out["TG|2026-09-06"]
    assert day["total"] == 23.0 and day["delta"] == 8.0
    assert day["since"] == "2026-09-05"
    # Only what moved: the slot that stayed on five is not a mover.
    assert [m["asset"] for m in day["movers"]] == ["a1"]
    assert day["movers"][0]["delta"] == 8.0
    # No stats row on the previous listed day, so the day cannot be
    # differenced and the line says nothing rather than a season.
    assert day["movers"][0]["line"] == []


def test_a_day_nothing_moved_on_gets_no_panel(tmp_path):
    """An entry with no movers is a panel that would open empty, and a cell
    marked clickable that opens nothing is worse than one that is not.

    The first listed day is the exception and not a special case: it has
    nothing before it, so everything on the board arrived since nothing."""
    import json

    from whul.site.build import _day_breakdown
    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("managers", [{"manager_id": "TG", "display_name": "TG",
                               "active": 1}], ["manager_id"])
    store.upsert("roster_slots", [
        {"slot_id": "s1", "manager_id": "TG", "season": "2026-27",
         "category": "NFL", "asset_type": "Team", "slot_index": 1},
    ], ["slot_id"])
    store.upsert("slot_scores", [
        {"slot_id": "s1", "season": "2026-27", "as_of": d, "asset_id": "a1",
         "score": 10.0, "counts": 1}
        for d in ("2026-09-05", "2026-09-06")
    ], ["slot_id", "as_of"])
    out = _day_breakdown(store, "2026-27", ["2026-09-05", "2026-09-06"], ["TG"])
    assert "TG|2026-09-06" not in out, "a still day has nothing to open"
    # The first listed day has nothing before it, so what is on the board is
    # the movement. It carries no "since", which is what the panel reads off.
    assert out["TG|2026-09-05"]["delta"] is None
    assert out["TG|2026-09-05"]["movers"][0]["delta"] == 10.0


def test_the_progression_table_reads_newest_first():
    """A line has to be drawn left to right, but a table is read from the top,
    and what a reader wants first is what happened last."""
    import re

    from whul.site.build import _table_view

    html = _table_view("Show as a table", ["Date", "TG"],
                       [["2026-09-07", "3.0"], ["2026-09-06", "2.0"]],
                       columns=["TG"])
    dates = re.findall(r"<td>(\d{4}-\d\d-\d\d)</td>", html)
    assert dates == sorted(dates, reverse=True)


def test_reversing_the_rows_does_not_reverse_the_deltas(tmp_path):
    """The table is newest first and the deltas are computed forwards. Read the
    wrong way round, "since the 5th" would become "since the 7th" and every
    change would carry the wrong sign."""
    from whul.site.build import _day_breakdown
    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("managers", [{"manager_id": "TG", "display_name": "TG",
                               "active": 1}], ["manager_id"])
    store.upsert("roster_slots", [
        {"slot_id": "s1", "manager_id": "TG", "season": "2026-27",
         "category": "NFL", "asset_type": "Team", "slot_index": 1},
    ], ["slot_id"])
    store.upsert("slot_scores", [
        {"slot_id": "s1", "season": "2026-27", "as_of": "2026-09-05",
         "asset_id": "a1", "score": 4.0, "counts": 1},
        {"slot_id": "s1", "season": "2026-27", "as_of": "2026-09-07",
         "asset_id": "a1", "score": 9.0, "counts": 1},
    ], ["slot_id", "as_of"])

    out = _day_breakdown(store, "2026-27", ["2026-09-05", "2026-09-07"], ["TG"])
    assert out["TG|2026-09-07"]["delta"] == 5.0
    assert out["TG|2026-09-07"]["since"] == "2026-09-05"


# --- how an individual athlete is labelled ----------------------------------

def test_a_tennis_player_is_labelled_by_tour_not_by_singles():
    """Every tennis player's role is "Singles", so it distinguishes nobody.
    The tour does."""
    from whul.site.build import _identity

    assert _identity({"role": "Singles"}, "WTA", "WTA", "Poland")["position"] == "WTA"
    assert _identity({"role": "Singles"}, "ATP", "ATP", "Serbia")["position"] == "ATP"
    # ...and the country still goes where a club would.
    assert _identity({"role": "Singles"}, "WTA", "WTA", "Poland")["team"] == "Poland"


def test_a_driver_carries_the_number_on_the_car():
    """A driver has no club, so the line that carries one for a footballer can
    carry his number. The hash keeps it from reading as a finishing place,
    which is the other number a motorsport row is full of."""
    from whul.site.build import _identity

    who = _identity({"role": "Driver", "car_number": "1"}, "F1", "F1", "Netherlands")
    assert who["position"] == "Driver" and who["team"] == "#1"


def test_a_driver_without_a_number_keeps_his_country():
    """Not every racing feed reports one, and a country is what the line showed
    before -- less useful, and not wrong."""
    from whul.site.build import _identity

    who = _identity({"role": "Driver"}, "NASCAR", "NASCAR", "United States")
    assert who["team"] == "United States"


def test_a_finishing_place_is_never_read_as_a_car_number():
    """Fifth place is not car #5, and the mistake would look right."""
    from whul.site.build import _identity

    who = _identity({"role": "Driver", "position": "5"}, "NASCAR", "NASCAR", "USA")
    assert who["team"] == "USA"


def test_a_footballer_still_shows_a_club():
    from whul.site.build import _identity

    who = _identity({"position": "F", "team": "Arsenal"}, "Premier League",
                    "Premier League", "")
    assert who == {"position": "F", "team": "Arsenal", "group": ""}


# --- where the numbers came from --------------------------------------------

def test_a_quiet_season_and_a_broken_feed_are_told_apart():
    """Both look identical in the standings -- a column of zeroes -- and they
    need opposite responses. Whether the season has opened is what separates
    them, so it is asked rather than read out of the message text, which
    changes."""
    from whul.site.build import _feed_state

    silent = {"last_run_at": "2026-09-07T09:00Z", "last_ok": 0, "message": ""}
    assert _feed_state(silent, in_season=True)[0] == "failing"
    assert _feed_state(silent, in_season=False)[0] == "waiting"
    # An international side has no start date -- each plays a different
    # competition on a different calendar -- so only a result can say. Calling
    # that a failure would cry wolf all year.
    assert _feed_state(silent, in_season=None)[0] == "waiting"


def test_a_feed_that_has_never_run_is_the_loud_case():
    """It is invisible by construction: no row, no message, no zero to notice.
    A league left out of the nightly list looks exactly like one nobody has
    drafted."""
    from whul.site.build import _feed_state

    state, note = _feed_state({}, in_season=True)
    assert state == "never pulled"
    assert "nightly list" in note


def test_a_scoring_feed_says_so():
    from whul.site.build import _feed_state

    assert _feed_state(
        {"last_run_at": "2026-09-07T09:00Z", "last_ok": 1}, True)[0] == "scoring"


def _rostered_store(tmp_path, leagues):
    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("managers", [{"manager_id": "TG", "display_name": "TG",
                               "active": 1}], ["manager_id"])
    store.upsert("assets", [
        {"asset_id": f"a{i}", "asset_type": "Team", "display_name": f"T{i}",
         "league": lg, "norm_key": lg, "created_at": "2026-08-21"}
        for i, lg in enumerate(leagues)
    ], ["asset_id"])
    store.upsert("roster_slots", [
        {"slot_id": f"s{i}", "manager_id": "TG", "season": "2026-27",
         "category": "NFL", "asset_type": "Team", "slot_index": i}
        for i, _ in enumerate(leagues)
    ], ["slot_id"])
    store.upsert("slot_occupancy", [
        {"slot_id": f"s{i}", "asset_id": f"a{i}", "start_date": "2026-08-21",
         "end_date": None, "cost": 0.0, "note": ""}
        for i, _ in enumerate(leagues)
    ], ["slot_id", "start_date"])
    return store


def test_a_source_keyed_on_its_category_is_not_reported_as_never_run(tmp_path):
    """`source_status` keeps one row per source, keyed on the source's own
    league -- which for one serving six competitions is the category rather
    than any of them. Looking it up per competition produced twelve rows
    reading "never pulled" about feeds that had run an hour earlier."""
    from whul.site.build import _feed_rows

    store = _rostered_store(tmp_path, ["Premier League", "La Liga"])
    store.upsert("source_status", [{
        "source": "soccer-players", "league": "Club Soccer",
        "last_run_at": "2026-09-07T09:00:00Z", "last_data_date": "2026-09-07",
        "last_ok": 1, "rows_last_run": 39, "message": "",
    }], ["source", "league"])

    rows = {r["source"]: r for r in _feed_rows(store, "2026-27", "2026-09-07")}
    assert rows["soccer-players"]["state"] == "scoring"
    assert rows["soccer-players"]["rows"] == 39
    # One row for the source, listing what it covers -- not one per league.
    assert rows["soccer-players"]["covers"] == ["Premier League", "La Liga"]


def test_only_sources_that_feed_the_roster_are_listed(tmp_path):
    """Twenty-four feeds is a page. Every source in the table would be a
    catalogue, and the leagues nobody drafted are not the reader's problem."""
    from whul.site.build import _feed_rows

    store = _rostered_store(tmp_path, ["NCAAF"])
    covered = {lg for row in _feed_rows(store, "2026-27", "2026-09-07")
               for lg in row["covers"]}
    assert covered == {"NCAAF"}


def test_the_worst_feeds_are_listed_first(tmp_path):
    """The order is the order to look in."""
    from whul.site.build import FEED_STATES, _feed_rows

    store = _rostered_store(tmp_path, ["NCAAF", "MLB"])
    store.upsert("source_status", [{
        "source": "mlb", "league": "MLB", "last_run_at": "2026-09-07T09:00:00Z",
        "last_data_date": "2026-09-07", "last_ok": 1, "rows_last_run": 19,
        "message": "",
    }], ["source", "league"])
    states = [r["state"] for r in _feed_rows(store, "2026-27", "2026-09-07")]
    assert states == sorted(states, key=FEED_STATES.index)


# --- a file that is present and unreadable ---------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\0" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'


def test_a_file_that_is_what_it_says_is_not_reported(tmp_path):
    (tmp_path / "flag").mkdir()
    (tmp_path / "flag" / "italy.png").write_bytes(PNG)
    (tmp_path / "flag" / "spain.webp").write_bytes(WEBP)
    (tmp_path / "flag" / "france.svg").write_bytes(SVG)
    assert images.mislabelled(tmp_path) == []


def test_an_svg_named_png_is_caught(tmp_path):
    """The one that actually breaks: served as image/png, the browser refuses
    it, and it renders as the same monogram a missing file renders as."""
    (tmp_path / "shield").mkdir()
    (tmp_path / "shield" / "ofc.png").write_bytes(SVG)
    caught = images.mislabelled(tmp_path)
    assert [(p.name, claimed, actual) for p, claimed, actual in caught] == [
        ("ofc.png", "png", "svg")
    ]


def test_a_webp_named_png_is_caught_too(tmp_path):
    """It usually survives on browser sniffing, which is luck, not design."""
    (tmp_path / "flag").mkdir()
    (tmp_path / "flag" / "italy.png").write_bytes(WEBP)
    assert [a for _, _, a in images.mislabelled(tmp_path)] == ["webp"]


def test_jpg_and_jpeg_are_the_same_format(tmp_path):
    """Two spellings of one format is not a mismatch."""
    (tmp_path / "asset").mkdir()
    (tmp_path / "asset" / "a.jpg").write_bytes(b"\xff\xd8\xff" + b"\0" * 32)
    (tmp_path / "asset" / "b.jpeg").write_bytes(b"\xff\xd8\xff" + b"\0" * 32)
    assert images.mislabelled(tmp_path) == []


def test_a_format_nobody_recognises_is_left_alone(tmp_path):
    """This catches files that are provably something else. It does not
    police the format list, and a false alarm on a working picture would
    teach the reader to ignore the whole section."""
    (tmp_path / "asset").mkdir()
    (tmp_path / "asset" / "odd.png").write_bytes(b"\x00\x01\x02\x03" * 8)
    assert images.mislabelled(tmp_path) == []


def test_every_image_in_the_repository_is_what_its_name_says():
    """The real tree, because this is how the twenty that were wrong got in:
    exported "for the web", saved with the extension the list asked for."""
    wrong = images.mislabelled()
    assert wrong == [], "\n".join(
        f"{p}: named .{claimed}, actually {actual}" for p, claimed, actual in wrong
    )


# --- the next-fixture column ------------------------------------------------

def test_the_roster_table_has_a_next_column_between_name_and_score(site):
    out, _ = site
    html = (out / "team" / "ss.html").read_text()
    assert "<th class='fixture'>Next</th>" in html
    # Order matters: the user asked for it between the asset and the score.
    header = html[html.index("<th>Asset</th>"):]
    assert header.index("class='fixture'") < header.index("Normalized")


def test_an_asset_with_no_fixture_gets_an_empty_cell_not_a_guess(site):
    """The simulated league has no fixtures at all, so every cell is blank --
    and blank is the answer, not a dash that reads as a known absence."""
    out, _ = site
    html = (out / "team" / "ss.html").read_text()
    assert "<td class='fixture'></td>" in html


def test_a_fixture_reads_as_a_date_and_an_opponent():
    from whul.site.build import _fixture_cell

    cell = _fixture_cell({"date": "2026-09-20", "opponent": "New Orleans Saints",
                          "home": True, "competition": "REG"})
    assert "Sep 20" in cell and "vs" in cell and "New Orleans Saints" in cell
    away = _fixture_cell({"date": "2026-09-20", "opponent": "Houston Texans",
                          "home": False, "competition": "REG"})
    assert "at" in away and "vs" not in away


def test_an_unreadable_fixture_date_is_shown_as_it_came():
    """Better a raw string than a crash on the page, and better than dropping
    the opponent along with it."""
    from whul.site.build import _fixture_cell

    cell = _fixture_cell({"date": "week 3", "opponent": "Alpha", "home": True})
    assert "week 3" in cell and "Alpha" in cell


# --- the calculator ---------------------------------------------------------

def test_the_scoring_page_carries_the_calculator_and_its_rules(site):
    out, _ = site
    html = (out / "about.html").read_text()
    assert 'id="calcpanel"' in html
    assert 'id="calcdata"' in html
    spec = json.loads(
        re.search(r'id="calcdata">(.*?)</script>', html, re.S).group(1)
    )
    assert spec["calcs"], "no calculators reached the page"
    assert spec["benchmarks"], "nothing to divide by, so nothing normalizes"


def test_a_group_with_no_benchmark_is_shipped_anyway_and_says_so(site):
    """The simulated league benchmarks by roster category, not by the keys a
    real season uses, so most calculator groups have nothing to divide by
    there. That is the honest case to build for: the panel still renders and
    reports no scale, rather than hiding the calculator or inventing one.

    The strict check -- every group has a benchmark in the *real* database --
    is in tests/test_calculator.py, where the real database is.
    """
    out, _ = site
    html = (out / "about.html").read_text()
    spec = json.loads(
        re.search(r'id="calcdata">(.*?)</script>', html, re.S).group(1)
    )
    keys = {key for calc in spec["calcs"] for _, key in calc["groups"]}
    assert keys, "no group names a benchmark at all"
    # Whatever it can normalize, it normalizes; the rest read as unscaled.
    assert "No frozen benchmark for this group" in charts.SCRIPT


def test_no_two_elements_on_a_page_share_an_id(site):
    """`getElementById` returns the first, so a duplicate is a script wired to
    the wrong element -- which is how the calculator first rendered into its
    own collapsed wrapper and did nothing at all."""
    out, _ = site
    for page in out.rglob("*.html"):
        ids = re.findall(r'\bid="([^"]+)"', page.read_text())
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, f"{page.name}: {sorted(duplicates)}"


# --- the playoffs and Europe section -----------------------------------------


def test_the_profile_carries_each_competition_paying_a_bonus():
    from whul.site.build import _bonus_list

    detail = [
        {"competition": "UEFA Champions League", "games": 6.0, "points": 33.0,
         "share": 0.05, "scalar": 1.9, "adds": 10.45},
        {"competition": "MLS Cup Playoffs", "games": 0.0, "points": 0.0,
         "share": 0.10, "scalar": 3.4, "adds": 0.0},
    ]
    kept = _bonus_list({"bonus_detail": detail})
    assert [d["competition"] for d in kept] == ["UEFA Champions League"], \
        "a competition nobody played in is not a row"


def test_the_profile_reads_the_breakdown_back_out_of_json():
    """raw_stats keeps every column as JSON, so it arrives as a string."""
    import json

    from whul.site.build import _bonus_list

    detail = [{"competition": "Europa League", "games": 4.0, "points": 20.0,
               "share": 0.05, "scalar": 1.9, "adds": 9.5}]
    assert _bonus_list({"bonus_detail": json.dumps(detail)}) == detail
    assert _bonus_list({"bonus_detail": "not json"}) == []
    assert _bonus_list({}) == []


def test_the_postseason_figures_stay_out_of_the_season_totals():
    """A row reading "Postseason bonus 11.2" says nothing about what it was
    11.2 *of*, and the shares are no longer one number."""
    from whul.site.build import _stat_lines

    lines = dict(_stat_lines({
        "goals": 4.0, "regular_points": 155.0, "postseason_bonus": 11.2,
        "bonus_matches": 8.0, "bonus_points": 47.0,
    }))
    assert "Goals" in lines
    assert not [name for name in lines if "ostseason" in name or "onus" in name]


def test_points_columns_are_not_labelled_as_counts():
    """"Goals 2.0" directly above "Goals 8.0" invites the reader to decide
    which of the two is wrong."""
    from whul.site.build import _stat_lines

    lines = dict(_stat_lines({"goals": 2.0, "goal_points": 8.0,
                              "appearance_points": 6.0}))
    assert lines["Goals"] == "2.0"
    assert lines["Points for goals"] == "8.0"
    assert lines["Points for appearances"] == "6.0"


# --- a day the scale moved ---------------------------------------------------


def rescale_store(tmp_path, versions: dict[str, str]):
    """Two days of one slot, scored against the versions given."""
    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("managers", [{"manager_id": "TG", "display_name": "TG",
                               "active": 1}], ["manager_id"])
    store.upsert("roster_slots", [
        {"slot_id": "s1", "manager_id": "TG", "season": "2026-27",
         "category": "La Liga", "asset_type": "Player", "slot_index": 1},
    ], ["slot_id"])
    store.upsert("assets", [
        {"asset_id": "a1", "asset_type": "Player", "display_name": "Yamal",
         "league": "La Liga", "norm_key": "La Liga", "created_at": "2026-08-21"},
    ], ["asset_id"])
    # The raw total does not move; only the divisor does.
    store.upsert("slot_scores", [
        {"slot_id": "s1", "season": "2026-27", "as_of": "2026-09-09",
         "asset_id": "a1", "score": 14.7, "counts": 1},
        {"slot_id": "s1", "season": "2026-27", "as_of": "2026-09-10",
         "asset_id": "a1", "score": 13.7, "counts": 1},
    ], ["slot_id", "as_of"])
    store.upsert("benchmark_versions", [
        {"version": v, "season": "2026-27", "quantile": 0.99, "managers": 5,
         "computed_at": "2026-09-01T00:00:00Z"}
        for v in sorted(set(versions.values()))
    ], ["version"])
    store.upsert("daily_scores", [
        {"asset_id": "a1", "season": "2026-27", "as_of": day,
         "league_points": 24.0, "scaled_score": score,
         "benchmark_version": versions[day], "computed_at": f"{day}T09:00:00Z"}
        for day, score in (("2026-09-09", 14.7), ("2026-09-10", 13.7))
    ], ["asset_id", "season", "as_of"])
    return store


def test_a_day_the_scale_was_refrozen_says_so(tmp_path):
    """Three players were about a point down on the day the September scale was
    adopted, none of them having kicked a ball: identical raw totals, a bigger
    divisor. Differenced against the day before, a rescale reads as a bad day."""
    from whul.site.build import _day_breakdown

    store = rescale_store(tmp_path, {"2026-09-09": "2026-27-20260907-182143",
                                     "2026-09-10": "2026-27-20260909-224543"})
    out = _day_breakdown(store, "2026-27", ["2026-09-09", "2026-09-10"], ["TG"])
    assert out["TG|2026-09-10"]["rescaled"] is True


def test_an_ordinary_day_is_not_marked(tmp_path):
    """The note has to mean something, so it must not appear on a day the scale
    held and a player simply lost points to a card."""
    from whul.site.build import _day_breakdown

    store = rescale_store(tmp_path, {"2026-09-09": "2026-27-20260907-182143",
                                     "2026-09-10": "2026-27-20260907-182143"})
    out = _day_breakdown(store, "2026-27", ["2026-09-09", "2026-09-10"], ["TG"])
    assert out["TG|2026-09-10"]["rescaled"] is False


def test_the_first_listed_day_is_never_marked(tmp_path):
    """There is nothing before it to have been scored differently."""
    from whul.site.build import _day_breakdown

    store = rescale_store(tmp_path, {"2026-09-09": "2026-27-20260907-182143",
                                     "2026-09-10": "2026-27-20260909-224543"})
    out = _day_breakdown(store, "2026-27", ["2026-09-09", "2026-09-10"], ["TG"])
    assert "TG|2026-09-09" not in out or not out["TG|2026-09-09"]["rescaled"]


# --- a slot moving between the bench and the counting set --------------------


def bench_store(tmp_path, counts_before: int, counts_now: int):
    """One asset, benched or counting on each of two days."""
    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("managers", [{"manager_id": "SM", "display_name": "SM",
                               "active": 1}], ["manager_id"])
    store.upsert("roster_slots", [
        {"slot_id": "s1", "manager_id": "SM", "season": "2026-27",
         "category": "Club Soccer", "asset_type": "Player", "slot_index": 1},
        # A second slot that counts on both days, so the panel has an entry to
        # attach movers to even when the first is benched throughout.
        {"slot_id": "s2", "manager_id": "SM", "season": "2026-27",
         "category": "Club Soccer", "asset_type": "Player", "slot_index": 2},
    ], ["slot_id"])
    store.upsert("assets", [
        {"asset_id": "a1", "asset_type": "Player", "display_name": "Haaland",
         "league": "Premier League", "norm_key": "Premier League",
         "created_at": "2026-08-21"},
        {"asset_id": "a2", "asset_type": "Player", "display_name": "Someone",
         "league": "Premier League", "norm_key": "Premier League",
         "created_at": "2026-08-21"},
    ], ["asset_id"])
    store.upsert("slot_scores", [
        {"slot_id": "s1", "season": "2026-27", "as_of": "2026-09-09",
         "asset_id": "a1", "score": 9.7, "counts": counts_before},
        {"slot_id": "s1", "season": "2026-27", "as_of": "2026-09-10",
         "asset_id": "a1", "score": 17.0, "counts": counts_now},
        {"slot_id": "s2", "season": "2026-27", "as_of": "2026-09-09",
         "asset_id": "a2", "score": 5.0, "counts": 1},
        {"slot_id": "s2", "season": "2026-27", "as_of": "2026-09-10",
         "asset_id": "a2", "score": 6.0, "counts": 1},
    ], ["slot_id", "as_of"])
    store.conn.commit()
    return store


def movers_for(store, asset="a1"):
    from whul.site.build import _day_breakdown

    out = _day_breakdown(store, "2026-27",
                         ["2026-09-09", "2026-09-10"], ["SM"])
    day = out.get("SM|2026-09-10", {"movers": []})
    return next((m for m in day["movers"] if m["asset"] == asset), None)


def test_a_days_change_is_what_the_asset_did_not_what_it_contributed(tmp_path):
    """Haaland showed +17.0 -- a whole season -- for having been benched on the
    9th at 9.69 and counted on the 10th at 17.04. This panel is read to see how
    a day went, not to account for the total, so whether a slot was counting
    makes no difference to the figure against its name."""
    mover = movers_for(bench_store(tmp_path, counts_before=0, counts_now=1))
    assert mover["delta"] == 7.3
    assert mover["points"] == 17.0


def test_a_benched_slot_is_shown_rather_than_left_out(tmp_path):
    """A benched player having a big day is worth seeing. It is marked so, and
    quieter on the page, because it did not count towards the total above it."""
    mover = movers_for(bench_store(tmp_path, counts_before=1, counts_now=0))
    assert mover is not None
    assert mover["counts"] is False
    assert mover["delta"] == 7.3, "what it did, bench or not"


def test_a_counting_slot_says_so(tmp_path):
    mover = movers_for(bench_store(tmp_path, counts_before=1, counts_now=1))
    assert mover["counts"] is True and mover["delta"] == 7.3


def test_a_day_only_a_benched_slot_moved_on_is_still_worth_opening(tmp_path):
    """The counting total does not move, so the cell would not have been
    clickable -- and the one thing that happened would have been unreachable."""
    from whul.site.build import _day_breakdown
    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("managers", [{"manager_id": "SM", "display_name": "SM",
                               "active": 1}], ["manager_id"])
    store.upsert("roster_slots", [
        {"slot_id": "s1", "manager_id": "SM", "season": "2026-27",
         "category": "Club Soccer", "asset_type": "Player", "slot_index": 1},
    ], ["slot_id"])
    store.upsert("assets", [
        {"asset_id": "a1", "asset_type": "Player", "display_name": "Haaland",
         "league": "Premier League", "norm_key": "Premier League",
         "created_at": "2026-08-21"},
    ], ["asset_id"])
    store.upsert("slot_scores", [
        {"slot_id": "s1", "season": "2026-27", "as_of": "2026-09-09",
         "asset_id": "a1", "score": 9.7, "counts": 0},
        {"slot_id": "s1", "season": "2026-27", "as_of": "2026-09-10",
         "asset_id": "a1", "score": 17.0, "counts": 0},
    ], ["slot_id", "as_of"])
    store.conn.commit()

    out = _day_breakdown(store, "2026-27", ["2026-09-09", "2026-09-10"], ["SM"])
    day = out["SM|2026-09-10"]
    assert day["delta"] == 0.0, "the counting total did not move"
    assert [m["asset"] for m in day["movers"]] == ["a1"]
    assert day["movers"][0]["counts"] is False


def test_an_asset_traded_away_is_not_a_loss(tmp_path):
    """It is no longer this manager's, and differencing it against nothing
    would show its whole score as a fall."""
    from whul.site.build import _day_breakdown
    from whul.store import open_store

    store = bench_store(tmp_path, counts_before=1, counts_now=1)
    store.conn.execute(
        "DELETE FROM slot_scores WHERE as_of = '2026-09-10' AND asset_id = 'a1'")
    store.conn.commit()

    out = _day_breakdown(open_store(str(tmp_path / "whul.sqlite3")), "2026-27",
                         ["2026-09-09", "2026-09-10"], ["SM"])
    moved = [m["asset"] for m in out.get("SM|2026-09-10", {"movers": []})["movers"]]
    assert "a1" not in moved


# --- a match whose points are held -------------------------------------------


def held_store(tmp_path):
    """A player who played a Champions League match on the second day."""
    import json

    from whul.store import open_store

    store = open_store(str(tmp_path / "whul.sqlite3"))
    store.upsert("managers", [{"manager_id": "SM", "display_name": "SM",
                               "active": 1}], ["manager_id"])
    store.upsert("roster_slots", [
        {"slot_id": "s1", "manager_id": "SM", "season": "2026-27",
         "category": "Club Soccer", "asset_type": "Player", "slot_index": 1},
    ], ["slot_id"])
    store.upsert("assets", [
        {"asset_id": "a1", "asset_type": "Player", "display_name": "Mbappé",
         "league": "La Liga", "norm_key": "La Liga", "created_at": "2026-08-21"},
    ], ["asset_id"])
    # The score does not move: the bonus the match feeds is held.
    store.upsert("slot_scores", [
        {"slot_id": "s1", "season": "2026-27", "as_of": day,
         "asset_id": "a1", "score": 11.8, "counts": 1}
        for day in ("2026-09-09", "2026-09-10")
    ], ["slot_id", "as_of"])
    for day, detail in (
        ("2026-09-09", []),
        ("2026-09-10", [{"competition": "UEFA Champions League", "games": 1.0,
                         "points": 6.0, "share": 0.05, "scalar": 1.9,
                         "adds": 11.4, "credited": False,
                         "finishes": "2027-06-30"}]),
    ):
        store.upsert("raw_stats", [{
            "asset_id": "a1", "league": "Club Soccer", "season": "2026-27",
            "as_of": day, "source": "soccer-players", "phase": "regular",
            "stats": json.dumps({"league": "La Liga", "total_points": 24.0,
                                 "bonus_detail": detail}),
            "fetched_at": f"{day}T09:00:00Z",
        }], ["asset_id", "season", "as_of", "source", "phase"])
    store.conn.commit()
    return store


def test_a_european_night_shows_even_though_the_score_did_not_move(tmp_path):
    """The bonus it feeds is held until the competition finishes, so the day it
    was played would otherwise read as a day off."""
    from whul.site.build import _day_breakdown

    day = _day_breakdown(held_store(tmp_path), "2026-27",
                         ["2026-09-09", "2026-09-10"], ["SM"])["SM|2026-09-10"]
    mover = day["movers"][0]
    assert mover["delta"] == 0.0, "the score really did not move"
    assert mover["pending"] == 11.4
    assert mover["pending_line"] == ["UEFA Champions League 1 game · 6.0"]


def test_a_day_with_no_match_at_all_is_still_left_out(tmp_path):
    """The panel must not fill with rows for competitions nobody played in."""
    from whul.site.build import _day_breakdown

    store = held_store(tmp_path)
    store.conn.execute(
        "UPDATE raw_stats SET stats = ? WHERE as_of = '2026-09-10'",
        ('{"league": "La Liga", "total_points": 24.0, "bonus_detail": []}',))
    store.conn.commit()
    out = _day_breakdown(store, "2026-27", ["2026-09-09", "2026-09-10"], ["SM"])
    assert "SM|2026-09-10" not in out


def test_the_season_totals_add_up_to_the_total(tmp_path):
    """Mbappe's line read 8 + 16 = 24 above a total of 35.4, the missing 11.4
    being a bonus that lived only in the section below it."""
    from whul.site.build import _stat_lines

    lines = dict(_stat_lines({
        "appearance_points": 8.0, "goal_points": 16.0,
        "regular_points": 24.0, "postseason_bonus": 11.4,
        "postseason_pending": 0.0, "total_points": 35.4,
    }))
    assert lines["Points from league and cups"] == "24.0"
    assert "Postseason bonus" not in lines


def test_a_feed_identifier_is_not_shown_as_a_statistic():
    """"Player id 00-0038543" sat in a column of yards and touchdowns as though
    it were one of them."""
    from whul.site.build import _stat_lines

    lines = dict(_stat_lines({
        "player_id": "00-0038543", "athlete_id": "4361741", "team_id": "2",
        "passing_yards": 550.0,
    }))
    assert lines == {"Passing yards": "550.0"}


def test_typing_in_the_calculator_does_not_rebuild_the_form():
    """Every keystroke used to call show(), which empties the host and rebuilds
    every control -- so the input being typed into was destroyed mid-keystroke
    and focus landed on whatever came next. One digit a box, and a multi-digit
    stat effectively untypeable."""
    from whul.site import charts

    js = charts.SCRIPT
    handler = js[js.index("input.addEventListener('input'"):]
    handler = handler[:handler.index("});")]
    assert "showResult()" in handler
    assert "show()" not in handler.replace("showResult()", "")

    # And the lighter redraw must not be the heavy one under another name.
    lighter = js[js.index("function showResult()"):]
    lighter = lighter[:lighter.index("\n  }\n")]
    assert "host.innerHTML" not in lighter


# --- the grey line under a name --------------------------------------------

def test_a_clubs_line_is_never_its_own_name_again():
    """Arsenal plays for Arsenal, so the line that carries a player's position
    and club carried "Arsenal" under "Arsenal" for ninety team assets."""
    from whul.site.build import _under_a_team

    assert _under_a_team(
        "Team", "Premier League", {"continental": "Champions League"},
        ("", "Arsenal"),
    ) == ("Premier League", "Champions League")


def test_a_soccer_club_out_of_europe_still_names_its_league():
    """Which the roster's own column does not say: a slot reads "Club Soccer
    Top 3"."""
    from whul.site.build import _under_a_team

    assert _under_a_team("Team", "Serie A", {"continental": ""},
                         ("", "Como")) == ("Serie A", "")


def test_every_other_team_gets_no_line_at_all():
    """They are drafted one league at a time and the category beside them
    already says which."""
    from whul.site.build import _under_a_team

    for league in ("NHL", "NFL", "MLB", "Men's Intl Soccer",
                   "Women's Intl Soccer"):
        assert _under_a_team("Team", league, {}, ("", "Whoever")) == ("", ""), league


def test_a_players_line_is_untouched():
    """A player's position and club are the two things a roster of sixty names
    is read with."""
    from whul.site.build import _under_a_team

    assert _under_a_team("Player", "Premier League", {},
                         ("F", "Arsenal")) == ("F", "Arsenal")


def test_a_line_never_says_the_same_thing_twice():
    """The results table appends the league, and a club's own line now names
    it: "Premier League - Champions League - Premier League - Team"."""
    from whul.site.build import _identity_lines

    got = _identity_lines(
        {"position": "Premier League", "team": "Champions League"},
        "Premier League", "Team")
    assert got.count("Premier League") == 1
    assert "Champions League" in got and "Team" in got


def test_the_corner_badge_still_reads_the_clubs_own_name():
    """The line stopped carrying it; the crest lookup must not. An
    international side's shield is found by the country, and a player's crest
    by the club -- both through the same field."""
    from whul.site.build import corner_badge

    assert corner_badge("Team", "Intl Soccer", "Women's Intl Soccer",
                        "England") == ("shield", "uefa")
    assert corner_badge("Player", "Club Soccer Top 3", "Premier League",
                        "Arsenal") == ("club", "arsenal")


def test_a_figure_the_scorer_had_nothing_for_is_not_a_row():
    """Not the same as a zero, and reads as neither: "Continental entry"
    followed by nothing at all."""
    from whul.site.build import _stat_lines

    got = dict(_stat_lines({"continental_entry": "", "wins": 3.0}))
    assert "Continental entry" not in got
    assert got["Wins"] == "3.0"


def test_the_continental_competition_is_identity_not_a_statistic():
    """It is on the club's own line above the figures; a second copy in the
    column of wins reads as one of them."""
    from whul.site.build import _stat_lines

    assert "Continental" not in dict(
        _stat_lines({"continental": "Champions League", "wins": 3.0}))


# --- the two figures that are not the total ---------------------------------

def _standings_frame():
    return pd.DataFrame([
        {"manager_id": "SS", "rank": 1, "total": 219.3},
        {"manager_id": "TG", "rank": 2, "total": 181.2},
    ])


def test_held_points_ride_on_the_total_as_a_superscript():
    """Not a column: they are the same score, on the same scale, waiting on a
    competition to finish. The order is still by what has been awarded."""
    from whul.site.build import _standings_table

    html = _standings_table(_standings_frame(), {}, ["SS", "TG"],
                            bench={"SS": 30.0}, held={"SS": 13.6})
    assert "219.3<sup" in html
    assert "+13.6" in html
    # And nothing where nothing is held, rather than a "+0.0".
    assert "+0.0" not in html


def test_the_bench_is_its_own_column_and_reads_as_one():
    """Grey and italic because it is not a smaller total -- it is a different
    thing: points best ball is not counting and will not count."""
    from whul.site import theme
    from whul.site.build import _standings_table

    html = _standings_table(_standings_frame(), {}, ["SS", "TG"],
                            bench={"SS": 30.0, "TG": 16.2}, held={})
    assert "<td class='num benched'>30.0</td>" in html
    assert "td.benched { color: var(--muted); font-style: italic; }" in theme.STYLESHEET


def test_a_standings_table_with_neither_figure_still_renders():
    from whul.site.build import _standings_table

    html = _standings_table(_standings_frame(), {}, ["SS", "TG"])
    assert "219.3" in html and "<sup" not in html


# --- the totals above the filters -------------------------------------------

def _results_frame():
    return pd.DataFrame([
        {"asset_id": "a1", "manager_id": "SS", "score": 20.0, "counts": 1},
        {"asset_id": "a2", "manager_id": "SS", "score": 5.0, "counts": 0},
        {"asset_id": "a3", "manager_id": "TG", "score": 12.0, "counts": 1},
    ])


def _results_profiles():
    return {a: {"name": a, "league": "MLB", "kind": "Player"}
            for a in ("a1", "a2", "a3")}


def test_every_results_row_says_who_holds_it_and_whether_it_counts():
    """What the totals above the table are summed from. Without these the
    small table can only add up everything, which is not the standings."""
    from whul.site.build import _results_table

    html = _results_table(_results_frame(), _results_profiles(), ["SS", "TG"])
    assert 'data-manager="SS" data-counts="1"' in html
    assert 'data-manager="SS" data-counts="0"' in html


def test_a_row_counts_where_the_frame_does_not_say():
    """`contributions` always carries it; a frame assembled by hand may not,
    and a missing column should leave every row counting rather than none."""
    from whul.site.build import _results_table

    frame = _results_frame().drop(columns=["counts"])
    html = _results_table(frame, _results_profiles(), ["SS", "TG"])
    assert 'data-counts="0"' not in html


def test_the_totals_table_has_a_row_per_manager():
    from whul.site.build import _results_table

    html = _results_table(_results_frame(), _results_profiles(), ["SS", "TG"])
    assert 'id="filtertotals"' in html
    for manager in ("SS", "TG"):
        assert f'<tr data-manager="{manager}">' in html


def test_the_totals_round_the_way_the_standings_do():
    """Straight to one place disagrees with them by a tenth on an exact half --
    Scott's 207.5499999 is 207.6 there and would be 207.5 here -- and two
    tables on one site differing by a tenth is a reason to trust neither."""
    from whul.site import charts

    assert "Math.round((value || 0) * 100) / 100" in charts.SCRIPT


def test_a_filter_chip_is_not_escaped_twice():
    """A chip once read "Men&#x27;s Intl Soccer", and the filter went on
    working because the row's own attribute was wrong the same way. It was
    fixed by dropping the escape here, on the reasoning that the profiles hold
    HTML -- which made every other reader of them wrong instead, and put an
    entity in the middle of a chart label. The profiles hold text now, so this
    escapes, and once."""
    from whul.site.build import _results_table

    frame = pd.DataFrame([
        {"asset_id": "a1", "manager_id": "SS", "score": 1.0, "counts": 1},
    ])
    profiles = {"a1": {"name": "a1", "league": "Men's Intl Soccer",
                       "kind": "Team"}}
    html = _results_table(frame, profiles, ["SS"])
    assert "&amp;#x27;" not in html
    assert ">Men&#x27;s Intl Soccer</button>" in html
    assert 'data-league="Men&#x27;s Intl Soccer"' in html


# --- an umbrella is not a league --------------------------------------------

def test_an_umbrella_chip_stands_beside_the_leagues_it_covers():
    """All three buttons stay: Tennis, ATP and WTA. Tennis is the slot a
    manager drafts into and the other two are what people play in."""
    from whul.site.build import _results_table

    frame = pd.DataFrame([
        {"asset_id": "a1", "manager_id": "SS", "score": 1.0, "counts": 1},
        {"asset_id": "a2", "manager_id": "SS", "score": 1.0, "counts": 1},
    ])
    profiles = {"a1": {"name": "a1", "league": "ATP", "kind": "Player"},
                "a2": {"name": "a2", "league": "WTA", "kind": "Player"}}
    html = _results_table(frame, profiles, ["SS"])
    for label in ("ATP", "WTA", "Tennis"):
        assert f'data-value="{label}"' in html, label
    assert 'class="chip umbrella" data-filter="league" data-value="Tennis"' in html


def test_a_row_carries_the_umbrella_it_sits_under():
    """What lets one chip match several leagues without the page holding a
    second copy of the membership."""
    from whul.site.build import _results_table

    frame = pd.DataFrame([
        {"asset_id": "a1", "manager_id": "SS", "score": 1.0, "counts": 1},
    ])
    profiles = {"a1": {"name": "a1", "league": "ATP", "kind": "Player"}}
    html = _results_table(frame, profiles, ["SS"])
    assert 'data-group="Tennis"' in html


def test_an_umbrella_nobody_is_in_gets_no_chip():
    """A filter that can only empty the table."""
    from whul.site.build import _results_table

    frame = pd.DataFrame([
        {"asset_id": "a1", "manager_id": "SS", "score": 1.0, "counts": 1},
    ])
    profiles = {"a1": {"name": "a1", "league": "NFL", "kind": "Player"}}
    html = _results_table(frame, profiles, ["SS"])
    assert 'data-value="Tennis"' not in html
    assert 'data-value="Motorsports"' not in html


def test_a_league_chip_still_means_that_league_alone():
    from whul.site import charts

    assert "picked.league[row.dataset.league]" in charts.SCRIPT
    assert "picked.league[row.dataset.group]" in charts.SCRIPT


def test_a_drivers_corner_is_his_flag_and_not_his_car_number():
    """`_identity` puts the car number where a footballer's club goes, which is
    right on the line and wrong for the badge: `_slug("#1")` was looked up in
    the flag directory, so every driver lost his flag to a file called "-1"."""
    from whul.site.build import asset_profiles

    store = open_store(":memory:")
    store.upsert("managers", [{"manager_id": "SS", "display_name": "Scott"}],
                 ["manager_id"])
    store.upsert("assets", [{
        "asset_id": "player-motorsports-lando-norris", "asset_type": "Player",
        "display_name": "Lando Norris", "league": "F1", "role": "Driver",
        "norm_key": "F1", "affiliation": "Great Britain", "active": 1,
        "created_at": "2026-08-21",
    }], keys=("asset_id",))
    store.upsert("roster_slots", [{
        "slot_id": "s1", "season": "2026-27", "manager_id": "SS",
        "category": "Motorsports", "asset_type": "Player", "slot_index": 1,
    }], ["slot_id"])
    store.upsert("slot_occupancy", [{
        "slot_id": "s1", "asset_id": "player-motorsports-lando-norris",
        "start_date": "2026-08-21", "end_date": None,
    }], ["slot_id", "start_date"])
    store.record_stats(
        [{"player": "Lando Norris", "league": "F1", "role": "Driver",
          "car_number": "1", "total_points": 31.0,
          "asset_id": "player-motorsports-lando-norris"}],
        source="motorsports", season="2026-27", as_of=date(2026, 9, 10),
        league="Motorsports",
    )
    got = asset_profiles(store, "2026-27", date(2026, 9, 10),
                         {"player-motorsports-lando-norris"})
    profile = got["player-motorsports-lando-norris"]
    # The car number is on the line, where it is the useful thing.
    assert profile["team"] == "#1"
    # The flag is in the corner, where the car number never was one.
    assert profile["corner"] == ["flag", "great-britain"]


# --- the NFL stat panel ---------------------------------------------------

from whul.site import build as site_build

def _nfl_row(**over):
    row = {
        "league": "NFL", "role": "QB", "position": "QB",
        "passing_yards": 0, "passing_tds": 0, "interceptions": 0,
        "rushing_yards": 0, "rushing_tds": 0, "receptions": 0,
        "receiving_yards": 0, "receiving_tds": 0, "fumbles_lost": 0,
    }
    row.update(over)
    return row


def test_the_boxes_add_up_to_the_score_they_sit_above():
    """The reason to pair a count with its points rather than list both: the
    profile can be checked against itself, and one that does not reconcile is
    visible rather than plausible."""
    row = _nfl_row(passing_yards=334, passing_tds=2, rushing_yards=23,
                   rushing_tds=2)
    panel = site_build._nfl_boxes(row)

    total = sum(b["points"] for b in panel["top"] + panel["secondary"])
    # 334*0.04 + 2*4 + 23*0.1 + 2*6 = 35.7
    assert round(total, 1) == 35.7


def test_the_top_row_is_the_role_and_keeps_its_zeroes():
    """It is the line that makes one profile comparable with the next."""
    qb = site_build._nfl_boxes(_nfl_row(passing_yards=300))
    assert [b["label"] for b in qb["top"]] == [
        "Pass yds", "Pass TD", "INT", "Rush yds"]

    # A receiver leads with receiving and a back with rushing, each in the
    # order the role is actually used in.
    wr = site_build._nfl_boxes(_nfl_row(role="WR", receptions=10, receiving_yards=67))
    assert [b["label"] for b in wr["top"]] == [
        "Rec", "Rec yds", "Rush yds", "Rec / Rush TD"]

    rb = site_build._nfl_boxes(_nfl_row(role="RB", rushing_yards=90, receptions=4))
    assert [b["label"] for b in rb["top"]] == [
        "Rush yds", "Rec", "Rec yds", "Rush / Rec TD"]


def test_the_second_row_drops_what_is_empty_but_keeps_the_two_worth_zero():
    """A quarterback has no receptions and saying so four times is noise. A
    fumble count of zero is the good answer and its absence reads as missing."""
    boxes = site_build._nfl_boxes(_nfl_row(passing_yards=300))
    labels = [b["label"] for b in boxes["secondary"]]

    assert "Rec" not in labels and "Rec yds" not in labels
    # No touchdown box either: he ran for none and caught none, and a box
    # reading "0 / 0" is the noise this rule exists to remove.
    assert labels == ["Fumbles"]

    ran = site_build._nfl_boxes(_nfl_row(passing_yards=300, rushing_tds=1))
    assert [b["label"] for b in ran["secondary"]] == ["Rush / Rec TD", "Fumbles"]


def test_touchdowns_share_a_box_only_where_they_share_a_price():
    """Rushing and receiving are both six, so one strip under two figures is
    exact. Passing touchdowns are four, which is why they lead the row alone."""
    wr = site_build._nfl_boxes(_nfl_row(role="WR", rushing_tds=1, receiving_tds=2))
    td = next(b for b in wr["top"] if b["label"].endswith("TD"))

    # The label names the order rather than leaving two figures either side of
    # a slash for the reader to assign, and a receiver's receiving comes first.
    assert td["label"] == "Rec / Rush TD"
    assert td["value"] == "2 / 1"
    assert td["points"] == 18.0

    rb = site_build._nfl_boxes(_nfl_row(role="RB", rushing_tds=1, receiving_tds=2))
    td = next(b for b in rb["top"] if b["label"].endswith("TD"))
    assert td["label"] == "Rush / Rec TD"
    assert td["value"] == "1 / 2"


def test_a_zero_against_a_negative_weight_is_not_printed_as_minus_zero():
    boxes = site_build._nfl_boxes(_nfl_row(passing_yards=300))
    ints = next(b for b in boxes["top"] if b["label"] == "INT")

    assert ints["points"] == 0.0
    assert str(ints["points"]) == "0.0"


def test_the_playoff_boxes_read_january_and_not_the_season():
    row = _nfl_row(passing_yards=334, postseason_games=3, team_games=17,
                   regular_games=16, post_passing_yards=812, post_passing_tds=7,
                   post_interceptions=2, post_rushing_yards=96,
                   post_rushing_tds=2, post_receptions=0, post_receiving_yards=0,
                   post_receiving_tds=0, post_fumbles_lost=1)
    panel = site_build._nfl_panel(row)

    assert panel["head"] == [["Games played", "16"], ["Team games", "17"]]
    assert panel["post"]["games"] == "3"
    assert [b["value"] for b in panel["post"]["top"]] == ["812", "7", "2", "96"]
    # The season's own row is untouched by January.
    assert [b["value"] for b in panel["season"]["top"]] == ["334", "0", "0", "0"]


def test_no_playoff_section_until_there_is_a_playoff():
    """Four months of the year, an empty table saying nothing."""
    assert "post" not in site_build._nfl_panel(_nfl_row(passing_yards=300))


def test_a_player_with_figures_and_no_known_position_gets_no_line():
    """The dash line is for a player nobody has a position for *and* no figures
    for. One who has played and whose position we cannot read is a fault, and
    drawing him a quarterback's boxes would hide it."""
    assert site_build._nfl_boxes(
        {"league": "NFL", "role": "K", "rushing_yards": 12}) is None


def test_an_unplayed_player_gets_the_boxes_rather_than_an_empty_table():
    """Brock Bowers, injured, four weeks into a season: the profile a manager
    opens to ask exactly that read "No stat lines recorded for this day yet".
    The roster records a name and a league and not what he plays, so until he
    is scored once there is no position to pick a line with -- and these four
    name every way an NFL player scores rather than guessing at one."""
    panel = site_build._panel_before_a_season("NFL", "Player", "")

    assert [b["label"] for b in panel["season"]["top"]] == [
        "Pass yds", "Rush yds", "Rec yds", "Rush / Rec TD"]
    assert [b["value"] for b in panel["season"]["top"]] == ["—"] * 4
    # A dash, not a zero: zero says he played and did none of it.
    assert all(b["points"] is None for b in panel["season"]["top"])
    assert panel["head"] == [["Games played", "—"], ["Team games", "—"]]


def test_a_position_is_kept_once_a_scored_row_carries_one():
    """And then the line is his own rather than the neutral one."""
    panel = site_build._panel_before_a_season("NFL", "Player", "TE")
    assert [b["label"] for b in panel["season"]["top"]][0] == "Rec"


# --- NFL teams, and the outcome boxes both panels share ---------------------

def _nfl_team(**over):
    row = {"league": "NFL", "team": "SEA", "team_division": "NFC West",
           "div_rank": 1, "reg_wins": 11.0, "reg_losses": 5.0, "reg_ties": 1.0,
           "reg_big_wins": 4.0, "reg_shutouts": 1.0, "div_wins": 5.0,
           "div_ties": 1.0, "point_diff": 84.0, "playoff_appearance": 1.0,
           "playoff_wins": 2.0, "div_champ": 1, "season_settled": True}
    row.update(over)
    return row


def test_every_weighted_column_reaches_a_box_and_they_sum_to_the_score():
    """Every column the scorer multiplies is one the page shows, off the same
    table, so the panel can be checked against itself."""
    from whul.scoring.nfl import TEAM_WEIGHTS

    row = _nfl_team()
    panel = site_build._nfl_team_panel(row)
    boxes = (panel["top"] + panel["secondary"] + panel["outcomes"]
             + panel["post"]["top"])

    assert round(sum(b["points"] for b in boxes), 1) == round(
        sum(row[c] * w for c, w in TEAM_WEIGHTS.items()), 1)


def test_a_season_outcome_carries_its_points_rather_than_a_bare_yes():
    """As chips reading "Yes" these were fifteen and ten points in the score
    and nowhere on the page, and the panel silently stopped adding up."""
    panel = site_build._nfl_team_panel(_nfl_team())
    title = next(b for b in panel["outcomes"] if b["label"] == "Division title")

    assert title["value"] == "Yes"
    assert title["points"] == 15.0


def test_an_unsettled_season_says_neither_won_nor_lost():
    """A club that has not won its division in September has not lost it."""
    panel = site_build._nfl_team_panel(
        _nfl_team(div_champ=0, playoff_appearance=0, season_settled=False))

    assert [b["value"] for b in panel["outcomes"]] == ["—", "—"]

    over = site_build._nfl_team_panel(
        _nfl_team(div_champ=0, playoff_appearance=0, season_settled=True))
    assert [b["value"] for b in over["outcomes"]] == ["No", "No"]


def test_a_division_standing_reads_as_a_place():
    panel = site_build._nfl_team_panel(_nfl_team(div_rank=3))
    assert panel["head"] == [["Record", "11\u20135\u20131"],
                             ["Division", "3rd in NFC West"]]

    assert site_build._ordinal(1) == "1st"
    assert site_build._ordinal(2) == "2nd"
    assert site_build._ordinal(4) == "4th"
    # The teens, which the last-digit rule gets wrong.
    assert site_build._ordinal(11) == "11th"
    assert site_build._ordinal(12) == "12th"


def test_no_playoff_section_for_a_club_that_won_none():
    assert "post" not in site_build._nfl_team_panel(_nfl_team(playoff_wins=0))


def test_a_soccer_leagues_title_and_europe_are_boxes_that_carry_points():
    """Same fault as the NFL outcomes: Arsenal's sections summed to 29.7
    against a total of 39.7 the moment a league title landed."""
    row = {"league_champion": 1, "pts_league_title": 10.0,
           "continental_entry": "Champions League",
           "pts_continental_entry": 12.0, "league_settled": True}
    outcomes = site_build._soccer_outcomes(row)

    assert [(b["label"], b["value"], b["points"]) for b in outcomes] == [
        ("League title", "Yes", 10.0),
        ("Europe next year", "Champions League", 12.0),
    ]


# --- basketball as rates, hockey as a tally --------------------------------

def _nba_row(**over):
    row = {"league": "NBA", "role": "C", "regular_games": 50, "team_games": 52,
           "points": 1215, "rebounds": 610, "assists": 510, "steals": 62,
           "blocks": 38, "turnovers": 151, "three_pt_made": 90,
           "plus_minus": 212, "double_doubles": 41, "triple_doubles": 14}
    row.update(over)
    return row


def test_basketball_is_shown_as_the_sport_reports_it():
    panel = site_build._nba_panel(_nba_row())

    assert [(b["label"], b["value"]) for b in panel["top"]] == [
        ("PPG", "24.3"), ("RPG", "12.2"), ("APG", "10.2")]
    # The strip is still the season's total times its weight, so the big
    # figure and the points are the same season said twice.
    assert panel["top"][0]["points"] == 1215.0


def test_an_nba_panel_adds_up_to_the_score():
    from whul.scoring.nba import (
        BOX_WEIGHTS, DOUBLE_DOUBLE_BONUS, PLUS_MINUS_WEIGHT,
        TRIPLE_DOUBLE_BONUS,
    )

    row = _nba_row()
    panel = site_build._nba_panel(row)
    shown = sum(b["points"] for b in panel["top"] + panel["secondary"])
    scored = (sum(row[c] * w for c, w in BOX_WEIGHTS.items())
              + row["double_doubles"] * DOUBLE_DOUBLE_BONUS
              + row["triple_doubles"] * TRIPLE_DOUBLE_BONUS
              + row["plus_minus"] * PLUS_MINUS_WEIGHT)

    assert round(shown, 1) == round(scored, 1)


def test_a_triple_double_counts_in_both_boxes_because_it_earns_both():
    """`(doubles >= 2) * 1.5 + (doubles >= 3) * 3.0` -- a triple-double is
    worth 4.5, so counting it in both is what makes the boxes match."""
    panel = site_build._nba_panel(_nba_row(double_doubles=41, triple_doubles=14))
    by = {b["label"]: b for b in panel["secondary"]}

    assert by["Double-doubles"]["points"] == 41 * 1.5
    assert by["Triple-doubles"]["points"] == 14 * 3.0


# --- a playoff run, in whichever sport ran it -------------------------------

def _run(**over):
    """One postseason in the breakdown, as `apply_bonus` writes it."""
    entry = {"competition": "NBA", "games": 12.0, "points": 480.0,
             "share": 0.10, "scalar": 8.2, "adds": 328.0, "credited": False,
             "finishes": "2027-06-20"}
    entry.update(over)
    return entry


def _nhl_row(**over):
    row = {"league": "NHL", "role": "Skater", "games_played": 70, "goals": 30,
           "assists": 45, "shots": 210, "plus_minus": 12}
    row.update(over)
    return row


def _nba_playoffs(**over):
    row = _nba_row()
    row.update({f"post_{c}": v / 5.0 for c, v in row.items()
                if isinstance(v, (int, float)) and not c.startswith("post_")})
    row.update({"postseason_games": 12, "bonus_detail": [_run()]}, **over)
    return row


def test_a_playoff_section_is_the_same_boxes_as_the_season():
    """The figures were collected and dropped in aggregation, so these boxes
    had nothing to hold. They are built by the same call now, so a box that
    appears in one appears in the other."""
    panel = site_build._nba_panel(_nba_playoffs())
    season = [b["label"] for b in panel["top"] + panel["secondary"]]
    post = [b["label"] for b in
            panel["posts"][0]["top"] + panel["posts"][0]["secondary"]]

    assert season == post
    assert panel["posts"][0]["games"] == "12"


def test_a_playoff_rate_is_over_the_playoff_games():
    """Dividing April's figures by the regular season's game count would
    report a run at a twentieth of what was actually averaged."""
    row = _nba_playoffs()
    panel = site_build._nba_panel(row)
    ppg = next(b for b in panel["posts"][0]["top"] if b["label"] == "PPG")

    assert ppg["value"] == f"{row['post_points'] / 12:,.1f}"


def test_a_playoff_section_carries_what_the_run_pays():
    """Its boxes sum to what the run scored; this is what it is worth, which
    is a different number -- the rate credited over a share of a season. Held,
    and greyed, until the competition stops moving."""
    panel = site_build._nba_panel(_nba_playoffs())
    total = panel["posts"][0]["total"]

    assert total["points"] == 328.0
    assert total["value"] == "\u00d78.2"
    assert total["muted"] is True
    assert "held to 2027-06-20" in total["aside"]
    assert "not a tally" in panel["posts"][0]["note"]


def test_a_finished_competition_is_no_longer_greyed():
    panel = site_build._nba_panel(
        _nba_playoffs(bonus_detail=[_run(credited=True)]))

    assert panel["posts"][0]["total"]["muted"] is False


def test_no_playoff_games_means_no_playoff_section():
    """Everyone carries a breakdown entry. A section for a run nobody made
    would read as a player who turned out and did nothing."""
    assert "posts" not in site_build._nba_panel(_nba_row())
    assert "posts" not in site_build._nhl_panel(_nhl_row())


def test_hockey_gets_the_same_treatment():
    row = _nhl_row()
    row.update({"post_goals": 8, "post_assists": 9, "post_shots": 50,
                "post_plus_minus": 4, "postseason_games": 14,
                "bonus_detail": [_run(competition="NHL", games=14.0,
                                      scalar=8.4, adds=72.0)]})
    panel = site_build._nhl_panel(row)

    assert [b["label"] for b in panel["top"]] == \
        [b["label"] for b in panel["posts"][0]["top"]]
    assert [b["value"] for b in panel["posts"][0]["top"]] == \
        ["8", "9", "50", "4"]


def test_a_playoff_section_is_the_same_shape_even_where_one_phase_is_empty():
    """The secondary row drops its empty boxes, which it should -- but making
    that call twice, once per phase, gave the two sections different shapes and
    left the reader checking which boxes were present before reading any
    figure. A box now appears in both or in neither."""
    row = {"league": "NFL", "role": "QB", "regular_games": 16,
           "team_games": 17, "postseason_games": 3,
           "passing_yards": 4200, "passing_tds": 32, "interceptions": 9,
           "rushing_yards": 310, "rushing_tds": 0, "fumbles_lost": 4,
           "receptions": 0, "receiving_yards": 0, "receiving_tds": 0,
           # He scored on the ground in January and not before it.
           "post_passing_yards": 820, "post_passing_tds": 7,
           "post_interceptions": 1, "post_rushing_yards": 60,
           "post_rushing_tds": 1, "post_fumbles_lost": 0,
           "post_receptions": 0, "post_receiving_yards": 0,
           "post_receiving_tds": 0}
    panel = site_build._nfl_panel(row)
    labels = lambda part: [b["label"] for b in part["top"] + part["secondary"]]

    assert labels(panel["season"]) == labels(panel["post"])
    season_td = next(b for b in panel["season"]["secondary"]
                     if b["label"] == "Rush / Rec TD")
    assert season_td["value"] == "0 / 0"


def test_hockey_is_shown_as_a_tally_and_adds_up():
    from whul.scoring.nhl import (
        PTS_ASSIST, PTS_GOAL, PTS_PLUS_MINUS, PTS_SHOT,
    )

    row = {"league": "NHL", "role": "Skater", "games_played": 70, "goals": 30,
           "assists": 45, "shots": 210, "plus_minus": 12}
    panel = site_build._nhl_panel(row)

    assert [b["label"] for b in panel["top"]] == [
        "Goals", "Assists", "Shots", "+/-"]
    assert round(sum(b["points"] for b in panel["top"]), 1) == round(
        30 * PTS_GOAL + 45 * PTS_ASSIST + 210 * PTS_SHOT + 12 * PTS_PLUS_MINUS, 1)


def test_nothing_played_reads_as_nothing_rather_than_as_zero():
    """A club that has not played reports nothing; a player who took no shots
    reports none. They are different answers and the page says so."""
    panel = site_build._nba_panel({"league": "NBA", "role": "C"})

    assert [b["value"] for b in panel["top"]] == ["—"] * 3
    assert panel["head"] == [["Games played", "—"],
                             ["Team games", "—"]]
    # And the strip beneath is blank too. "0.0" under a dash says the player
    # earned none, which is a claim; nothing has been played.
    assert [b["points"] for b in panel["top"]] == [None] * 3

    played = site_build._nba_panel(_nba_row(blocks=0))
    assert next(b for b in played["secondary"] if b["label"] == "BPG")["value"] == "0.0"


def test_hockey_says_it_does_not_know_rather_than_hiding_the_row():
    """A club the standings do not name leaves the figure blank. The row stays,
    because "we do not know" and "there is nothing to know" are different and
    an omitted row says the second."""
    panel = site_build._nhl_panel(
        {"league": "NHL", "role": "Skater", "games_played": 70})

    assert panel["head"] == [["Games played", "70"], ["Team games", "—"]]

    known = site_build._nhl_panel(
        {"league": "NHL", "role": "Skater", "games_played": 50,
         "team_games": 62})
    assert known["head"] == [["Games played", "50"], ["Team games", "62"]]


def test_a_league_that_has_not_played_still_gets_its_panel():
    """A club drafted in August into a league that opens in October has a
    profile from the day it is drafted. Before this it fell back to a table
    reading "No stat lines recorded for this day yet"."""
    panel = site_build._panel_before_a_season("NBA", "Player", "C")

    assert [b["label"] for b in panel["top"]] == ["PPG", "RPG", "APG"]
    assert [b["value"] for b in panel["top"]] == ["—"] * 3

    assert site_build._panel_before_a_season("NHL", "Player", "Skater")
    assert site_build._panel_before_a_season("PGA", "Player", "")
    assert site_build._panel_before_a_season("Tennis", "Player", "") is not None


def test_a_soccer_section_always_shows_big_wins_and_clean_sheets():
    """Two of the four things a club is scored on. A competition where it kept
    none is a fact about the season, not a box with nothing to say."""
    part = {"wins": 2, "draws": 0, "losses": 0, "shootout_wins": 0,
            "shootout_losses": 0, "big_margins": 0, "clean_sheets": 0,
            "pts_wins": 6.0, "pts_draws": 0.0, "pts_losses": 0.0,
            "pts_shootout_wins": 0.0, "pts_shootout_losses": 0.0,
            "pts_big_margins": 0.0, "pts_clean_sheets": 0.0}
    block = site_build._soccer_block(part, "round-robin")

    assert [b["label"] for b in block["secondary"]] == ["Big wins", "Clean sheets"]


# --- the footballer, as against his club ------------------------------------

def _footballer(**over):
    """Arda Guler's real line, which is the one the panel was checked against."""
    row = {"league": "Club Soccer", "player": "Arda Guler", "team": "Real Madrid",
           "position": "M", "matches": 5.0, "starts": 3.0, "goals": 1.0,
           "assists": 2.0, "yellow": 1.0, "red": 0.0,
           "appearance_points": 8.0, "goal_points": 5.0, "regular_points": 18.0}
    row.update(over)
    return row


def test_a_footballers_boxes_add_up_to_his_domestic_points():
    """Europe is not in them, and is not in `regular_points` either -- it is
    paid as a rate in its own section. These two agree or the panel is lying
    about the half of the season it does show."""
    from whul.scoring.soccer import PTS_ASSIST, PTS_RED, PTS_YELLOW

    row = _footballer()
    panel = site_build._soccer_player_panel(row)
    boxes = panel["top"] + panel["secondary"]

    assert round(sum(b["points"] for b in boxes), 1) == row["regular_points"]
    assert [b["label"] for b in boxes] == [
        "Apps / Starts", "Goals", "Assists", "Yellow / Red"]
    assert [b["value"] for b in boxes] == ["5 / 3", "1", "2", "1 / 0"]
    # The discipline box carries both cards' points, since it shows both counts.
    assert boxes[-1]["points"] == round(
        1.0 * PTS_YELLOW + 0.0 * PTS_RED, 1)
    assert boxes[2]["points"] == round(2.0 * PTS_ASSIST, 1)


def test_a_footballer_with_no_line_reads_as_dashes_not_zeroes():
    """Eleven MLS players have no feed rows at all. A panel of zeroes would say
    they turned out and did nothing, which is a claim about matches nobody has
    played."""
    panel = site_build._soccer_player_panel(
        {"league": "Club Soccer", "player": "Nobody", "team": "FC Dallas"})
    boxes = panel["top"] + panel["secondary"]

    assert [b["value"] for b in boxes] == ["\u2014"] * 4
    assert all(b["points"] is None for b in boxes)
    assert panel["head"] == [["Games played", "\u2014"],
                            ["Team games", "\u2014"]]


def test_a_footballers_team_games_come_from_his_club():
    """Played and out of how many: "4 played" alone cannot say whether the rest
    were missed or not yet played."""
    games = {"Real Madrid": 6.0}
    panel = site_build._soccer_player_panel(_footballer(), games)

    assert panel["head"] == [["Games played", "5"], ["Team games", "6"]]


def test_a_club_we_do_not_carry_leaves_team_games_blank():
    """Six of the thirty-nine play for clubs that are nobody's asset, so the
    figure is nowhere in the store. Falling back to his own appearances would
    print every one of them as a player who never missed a match."""
    panel = site_build._soccer_player_panel(_footballer(), {"Chelsea": 6.0})

    assert panel["head"][1] == ["Team games", "\u2014"]


def test_a_club_row_is_not_mistaken_for_a_footballer():
    """A club's `player` arrives as NaN and NaN is truthy -- the mistake that
    sent every NFL club down the player path and killed a baseball build."""
    assert not site_build._is_a_club_soccer_player(
        {"league": "Premier League", "player": float("nan")})
    assert not site_build._is_a_club_soccer_player(
        {"league": "Club Soccer", "player": float("nan")})
    assert site_build._is_a_club_soccer_player(_footballer())


def test_the_club_lookup_reads_every_clubs_matches_played():
    import pandas as pd

    frame = pd.DataFrame([
        {"team": "Real Madrid", "matches_played": 6.0},
        {"team": "Liverpool", "matches_played": 5.0},
        {"team": "Arda Guler", "matches_played": None},
    ])

    assert site_build._club_games(frame) == {"Real Madrid": 6.0, "Liverpool": 5.0}
    assert site_build._club_games(pd.DataFrame()) == {}


def test_a_footballers_domestic_cups_get_their_own_section():
    """The cups count in full and were summed into one section named for the
    league, so four league matches and two League Cup ties read as six
    Premier League appearances -- on a page whose whole point is that a figure
    says which competition it is from."""
    row = _footballer(domestic_detail=[
        {"competition": "Premier League", "games": 4.0, "points": 24.0,
         "starts": 4.0, "goals": 2.0, "assists": 2.0, "yellow": 0.0,
         "red": 0.0, "appearance_points": 8.0, "goal_points": 10.0},
        {"competition": "EFL Cup", "games": 2.0, "points": 7.0, "starts": 1.0,
         "goals": 1.0, "assists": 0.0, "yellow": 1.0, "red": 0.0,
         "appearance_points": 3.0, "goal_points": 5.0},
    ])
    panel = site_build._soccer_player_panel(row, {}, "Premier League")

    assert [s["name"] for s in panel["sections"]] == ["Premier League", "EFL Cup"]
    # The same boxes in each, as the league and the cups are the same football.
    assert [b["label"] for b in panel["sections"][0]["top"]] == \
        [b["label"] for b in panel["sections"][1]["top"]]
    cup = panel["sections"][1]
    assert round(sum(b["points"] for b in cup["top"] + cup["secondary"]), 1) == 7.0


def test_his_own_league_leads_the_cups():
    panel = site_build._soccer_player_panel(_footballer(domestic_detail=[
        {"competition": "EFL Cup", "games": 1.0, "points": 2.0},
        {"competition": "FA Cup", "games": 1.0, "points": 2.0},
        {"competition": "Premier League", "games": 4.0, "points": 24.0},
    ]), {}, "Premier League")

    assert [s["name"] for s in panel["sections"]] == [
        "Premier League", "EFL Cup", "FA Cup"]


def test_a_row_stored_before_the_breakdown_still_draws():
    """Every row in the store predates it, and they keep the single set of
    boxes rather than a section named for a league that may not be all of it."""
    panel = site_build._soccer_player_panel(_footballer(), {}, "Premier League")

    assert "sections" not in panel
    assert panel["title"] == "Premier League"
    assert [b["label"] for b in panel["top"]] == [
        "Apps / Starts", "Goals", "Assists"]


def test_a_match_is_not_pluralised_as_matchs():
    note = site_build._campaign_note(
        {"competition": "UEFA Champions League", "games": 1.0, "points": 7.0,
         "share": 0.05, "scalar": 1.9, "credited": False,
         "finishes": "2027-06-30"}, "match")

    assert "one or two matches" in note
    assert "matchs" not in note


def _euro(**over):
    entry = {"competition": "UEFA Champions League", "games": 1.0,
             "points": 9.0, "share": 0.05, "scalar": 1.9, "adds": 17.1,
             "credited": False, "finishes": "2027-06-30", "starts": 1.0,
             "goals": 1.0, "assists": 1.0, "yellow": 1.0, "red": 0.0,
             "appearance_points": 2.0, "goal_points": 5.0}
    entry.update(over)
    return entry


def test_a_european_section_is_the_same_boxes_as_the_league():
    """Not similar -- the same. Both are built by one function, because a
    second builder for the second competition is how the two drift apart."""
    panel = site_build._soccer_player_panel(_footballer(bonus_detail=[_euro()]))
    league = [b["label"] for b in panel["top"] + panel["secondary"]]
    europe = [b["label"] for b in
              panel["posts"][0]["top"] + panel["posts"][0]["secondary"]]

    assert league == europe
    assert panel["posts"][0]["name"] == "UEFA Champions League"
    assert panel["posts"][0]["games"] == "1"


def test_a_european_sections_boxes_add_up_to_what_the_run_scored():
    """Its own figures, on its own terms. What the run *pays* is a different
    number and lives in its own box -- see the next test."""
    panel = site_build._soccer_player_panel(_footballer(bonus_detail=[_euro()]))
    post = panel["posts"][0]

    assert round(sum(b["points"] for b in post["top"] + post["secondary"]), 1) \
        == _euro()["points"]


def test_a_european_sections_total_is_held_until_the_competition_ends():
    """A rate off one match projects most of a share of a season and falls on
    the next one, so it is greyed and dated rather than shown as earned."""
    pending = site_build._soccer_player_panel(
        _footballer(bonus_detail=[_euro()]))["posts"][0]["total"]

    assert pending["points"] == 17.1
    assert pending["muted"] is True
    assert "held to 2027-06-30" in pending["aside"]

    done = site_build._soccer_player_panel(
        _footballer(bonus_detail=[_euro(credited=True)]))["posts"][0]["total"]
    assert done["muted"] is False
    assert "held" not in (done.get("aside") or "")


def test_a_footballer_in_two_competitions_gets_a_section_each():
    """A Champions League run and a domestic cup pay different shares, so one
    section carrying both would have no single total to show."""
    panel = site_build._soccer_player_panel(_footballer(bonus_detail=[
        _euro(), _euro(competition="MLS Cup Playoffs", share=0.10, scalar=3.4)]))

    assert [s["name"] for s in panel["posts"]] == [
        "UEFA Champions League", "MLS Cup Playoffs"]


def test_a_competition_nobody_played_gets_no_section():
    """Every La Liga player carries a Champions League entry reading zero
    games. A section for it would say he turned out and did nothing."""
    panel = site_build._soccer_player_panel(
        _footballer(bonus_detail=[_euro(games=0.0, points=0.0, adds=0.0)]))

    assert "posts" not in panel


# --- baseball, at whichever of the two jobs he does -------------------------

def _batter(**over):
    # A calendar year, because the boxes are priced at what the benchmark says
    # that year of a contract is worth -- 0.75 for the season a league year
    # opens inside -- and a row without one could be either.
    row = {"league": "MLB", "role": "Batter", "season": 2026, "h": 150,
           "ab": 480, "hr": 44,
           "doubles": 26, "triples": 6, "bb": 72, "hbp": 4, "sb": 18, "cs": 4,
           "offense": 38.2, "defense": -6.1, "proration_factor": 1.0}
    row.update(over)
    return row


def _pitcher_line(**over):
    row = {"role": "Pitcher", "scaled_score": 41.0, "role_points": 812.0,
           "ip": 92.0, "so": 124, "h": 62, "bb": 26, "hbp": 3, "hr": 10,
           "sv": 0, "hld": 0, "war": 3.1}
    row.update(over)
    return row


def test_a_batters_boxes_add_up_to_what_the_scorer_gave_him():
    from whul.scoring.mlb import (
        BATTER_WEIGHTS, DEFENSE_FACTOR, MULT_YEAR_N, OFFENSE_FACTOR,
    )

    row = _batter()
    section = site_build._mlb_panel(row)["sections"][0]
    shown = sum(b["points"] or 0 for b in section["top"] + section["secondary"])
    # At the scorer's own price for the year, which is what the score beneath
    # the boxes is: counting production in the season a league year opens
    # inside is worth 0.75 of the base.
    scored = (sum(row[c] * w for c, w in BATTER_WEIGHTS.items())
              + row["offense"] * OFFENSE_FACTOR
              + row["defense"] * DEFENSE_FACTOR) * MULT_YEAR_N

    # To a tenth per box: each is rounded for display before it is summed, and
    # ten of them can drift half a point off the figure printed underneath.
    assert shown == pytest.approx(scored, abs=0.5)


def test_the_average_carries_the_points_of_what_is_behind_it():
    """The figure a hitter is known by is not scored and the five things behind
    it are, so it carries theirs and lists them down its side."""
    from whul.scoring.mlb import BATTER_WEIGHTS, MULT_YEAR_N

    box = site_build._mlb_panel(_batter())["sections"][0]["top"][0]

    assert box["label"] == "AVG"
    assert box["value"] == ".312"
    assert box["aside"] == "150/480\n26 2B\n6 3B\n44 HR"
    assert box["points"] == round(
        (150 * BATTER_WEIGHTS["h"] + 480 * BATTER_WEIGHTS["ab"]
         + 26 * BATTER_WEIGHTS["doubles"] + 6 * BATTER_WEIGHTS["triples"]
         + 44 * BATTER_WEIGHTS["hr"]) * MULT_YEAR_N, 1)


def test_home_runs_are_shown_twice_and_counted_once():
    """It is the figure every reader looks for, so it gets a box -- but its
    points are in the average above and the strip says so."""
    top = site_build._mlb_panel(_batter())["sections"][0]["top"]
    box = next(b for b in top if b["label"] == "HR")

    assert box["value"] == "44"
    assert box["points"] is None
    assert box["note"] == "in AVG"


def test_a_steal_is_shown_against_what_it_cost():
    from whul.scoring.mlb import BATTER_WEIGHTS, MULT_YEAR_N

    rest = site_build._mlb_panel(_batter())["sections"][0]["secondary"]
    steals = next(b for b in rest if b["label"] == "SB")

    assert steals["value"] == "18"
    assert steals["aside"] == "4 CS"
    assert steals["points"] == round(
        (18 * BATTER_WEIGHTS["sb"] + 4 * BATTER_WEIGHTS["cs"]) * MULT_YEAR_N, 1)


def test_a_league_year_spanning_two_seasons_can_be_read_a_season_at_a_time():
    """Summed, the line is neither season and there is no way to ask which
    half was last year."""
    row = _batter(season_lines=[
        {"season": 2026, "h": 40, "ab": 130, "hr": 6, "doubles": 8,
         "triples": 1, "bb": 20, "hbp": 1, "sb": 5, "cs": 1,
         "offense": 2.0, "defense": -1.0},
        {"season": 2027, "h": 110, "ab": 350, "hr": 38, "doubles": 18,
         "triples": 5, "bb": 52, "hbp": 3, "sb": 13, "cs": 3,
         "offense": 36.2, "defense": -5.1},
    ])
    panel = site_build._mlb_panel(row)

    assert [y["year"] for y in panel["years"]] == ["2026", "2027"]
    halves = [y["sections"][0]["top"][0]["points"] for y in panel["years"]]
    whole = panel["sections"][0]["top"][0]["points"]
    assert round(sum(halves), 1) == round(whole, 1)


def test_a_season_nobody_has_played_yet_is_a_tab_of_dashes():
    """The years come from the league year, not from whichever have been
    played. Gating the tab on data present would have said, all winter, that
    the question could not be asked -- when the answer is that nothing has
    happened yet, which is what every other sport's empty panel says."""
    panel = site_build._mlb_panel(
        _batter(season_lines=[{"season": 2026, "h": 150, "ab": 480}]))

    assert [y["year"] for y in panel["years"]] == ["2026", "2027"]
    ahead = next(y for y in panel["years"] if y["year"] == "2027")
    assert [box["value"] for box in ahead["sections"][0]["top"]] == ["\u2014"] * 4
    assert all(box["points"] is None for box in ahead["sections"][0]["top"])


def test_a_league_year_inside_one_calendar_year_gets_no_toggle(monkeypatch):
    """A control with one position does nothing."""
    from dataclasses import replace

    import whul.config.league as league

    monkeypatch.setattr(league, "SEASON", replace(
        league.SEASON, start=date(2027, 4, 1), end=date(2027, 10, 1)))

    assert "years" not in site_build._mlb_panel(_batter())


def test_a_baseball_playoff_section_is_the_same_boxes_and_adds_up():
    """The Scoring page has always promised MLB players 7.5% of a season for
    October, and the scorer paid zero because the postseason was never asked
    for. It is now, and the section reconciles to what the scorer priced --
    the October line at the contract multiplier, and not at the proration
    lift. A playoff run is not short of the month a league year opening in
    August is short of, so nothing is owed it."""
    from whul.scoring.mlb import MULT_YEAR_N

    priced = 142.7 * MULT_YEAR_N
    row = _batter(**{
        "post_h": 18, "post_ab": 55, "post_hr": 6, "post_doubles": 4,
        "post_triples": 0, "post_bb": 8, "post_hbp": 1, "post_sb": 1,
        "post_cs": 0, "post_games": 14,
        "bonus_detail": [{"competition": "MLB", "games": 14.0,
                          "points": priced, "share": 0.075, "scalar": 12.15,
                          "adds": 123.8, "credited": False,
                          "finishes": "2026-11-20"}],
    })
    panel = site_build._mlb_panel(row)
    season, post = panel["sections"][0], panel["posts"][0]

    assert [b["label"] for b in season["top"]] == [b["label"] for b in post["top"]]
    assert [b["label"] for b in season["secondary"]] == \
        [b["label"] for b in post["secondary"]]
    # The home-run box says its points are counted in the average beside it, so
    # it is excluded here exactly as it is in the season section.
    scored = sum(b["points"] or 0 for b in post["top"] + post["secondary"]
                 if not b.get("note"))
    assert round(scored, 1) == round(priced, 1)
    assert post["total"]["points"] == 123.8
    assert post["total"]["muted"] is True


def test_a_baseball_playoff_section_is_not_prorated():
    """The lift exists because a league year opening in August is short a
    month of the summer, and a playoff run is not short of anything. The
    scorer rebuilds the total around the regular season alone, so a section
    lifted here would stop adding up to the figure it priced October at."""
    entry = [{"competition": "MLB", "games": 14.0, "points": 142.7,
              "share": 0.075, "scalar": 12.15, "adds": 123.8,
              "credited": False, "finishes": "2026-11-20"}]
    figures = {"post_h": 18, "post_ab": 55, "post_hr": 6, "post_doubles": 4,
               "post_triples": 0, "post_bb": 8, "post_hbp": 1, "post_sb": 1,
               "post_cs": 0, "bonus_detail": entry}
    plain = site_build._mlb_panel(_batter(**figures))["posts"][0]
    scaled = site_build._mlb_panel(
        _batter(proration_factor=1.5, **figures))["posts"][0]

    assert [b["points"] for b in plain["top"]] == \
        [b["points"] for b in scaled["top"]]


def test_a_batter_with_no_october_gets_no_playoff_section():
    assert "posts" not in site_build._mlb_panel(_batter())


def test_a_year_is_priced_at_what_the_benchmark_says_that_year_is_worth():
    """Both tabs used to be priced alike, which was right only while the
    scorer priced them alike too. The season a league year opens inside is
    worth 0.75 of the base and the season it closes in 1.181, so the same
    forty hits are worth more in the second tab than the first."""
    from whul.scoring.mlb import BATTER_WEIGHTS, MULT_YEAR_N, MULT_YEAR_N1

    line = {"h": 40, "ab": 130, "hr": 6, "doubles": 8, "triples": 1,
            "bb": 20, "hbp": 1, "sb": 5, "cs": 1, "offense": 2.0,
            "defense": -1.0}
    panel = site_build._mlb_panel(_batter(season_lines=[
        {"season": 2026, **line}, {"season": 2027, **line},
    ]))
    average = {y["year"]: y["sections"][0]["top"][0]["points"]
               for y in panel["years"]}

    behind = sum(line[c] * BATTER_WEIGHTS[c]
                 for c in ("h", "ab", "doubles", "triples", "hr"))
    assert average["2026"] == round(behind * MULT_YEAR_N, 1)
    assert average["2027"] == round(behind * MULT_YEAR_N1, 1)
    # And the season above the tabs is the two of them added, not the summed
    # counts priced once at one of the two multipliers.
    assert panel["sections"][0]["top"][0]["points"] == pytest.approx(
        average["2026"] + average["2027"], abs=0.1)


def test_each_year_carries_what_it_is_worth():
    """The toggle changes the score beneath it, so each year has to know its
    own. Summed from the boxes rather than scored a second time: the live
    player path prorates by one factor and does not carry the bisection
    weights, which govern the historical team path where a whole season really
    is split into two shares. So the years add to the total."""
    row = _batter(season_lines=[
        {"season": 2026, "h": 40, "ab": 130, "hr": 6, "doubles": 8,
         "triples": 1, "bb": 20, "hbp": 1, "sb": 5, "cs": 1,
         "offense": 2.0, "defense": -1.0},
        {"season": 2027, "h": 110, "ab": 350, "hr": 38, "doubles": 18,
         "triples": 5, "bb": 52, "hbp": 3, "sb": 13, "cs": 3,
         "offense": 36.2, "defense": -5.1},
    ])
    panel = site_build._mlb_panel(row)
    whole = site_build._section_points(panel["sections"])
    years = [y["raw"] for y in panel["years"]]

    assert all(isinstance(r, float) for r in years)
    assert round(sum(years), 1) == whole


def test_a_box_counted_elsewhere_is_not_counted_again():
    """Home runs sit in the average above them and say so. Adding the strip
    that says "in AVG" would double them in the year's figure."""
    panel = site_build._mlb_panel(_batter())
    section = panel["sections"][0]
    noted = [b for b in section["top"] if b.get("note")]

    assert noted, "the fixture no longer exercises a carried box"
    assert site_build._section_points([section]) == round(sum(
        b["points"] or 0.0 for b in section["top"] + section["secondary"]
        if not b.get("note")), 1)


def test_the_counting_boxes_carry_the_proration_the_score_does():
    """Acuna's counting terms come to 167.6 and his role points to 204.2; the
    difference is the window factor, and without it the panel does not add up
    to the number printed under it."""
    plain = site_build._mlb_panel(_batter())["sections"][0]
    scaled = site_build._mlb_panel(_batter(proration_factor=1.5))["sections"][0]

    assert round(scaled["top"][0]["points"], 1) == round(
        plain["top"][0]["points"] * 1.5, 1)


def test_whip_carries_the_points_of_what_it_is_made_of():
    """It is not scored itself, and every other box is. The hits and walks are
    printed small beside it so the rate and the scoring agree in public."""
    from whul.scoring.mlb import MULT_YEAR_N, PITCHER_WEIGHTS

    panel = site_build._mlb_panel(
        {"league": "MLB", "role": "Pitcher", "season": 2026,
         "proration_factor": 1.0,
         **{k: v for k, v in _pitcher_line().items() if k != "role"}})
    whip = next(b for b in panel["sections"][0]["top"] if b["label"] == "WHIP")

    assert whip["value"] == "0.96"
    assert whip["points"] == round(
        (62 * PITCHER_WEIGHTS["h"] + 26 * PITCHER_WEIGHTS["bb"])
        * MULT_YEAR_N, 1)
    assert whip["aside"] == "62 H\n26 BB"


def test_saves_and_holds_join_the_top_row_only_where_he_has_them():
    """A reliever's season is his saves; a starter's line should not carry two
    zeroes explaining that he is not one."""
    starter = {"league": "MLB", "role": "Pitcher", "proration_factor": 1.0,
               **{k: v for k, v in _pitcher_line().items() if k != "role"}}
    labels = [b["label"] for b in site_build._mlb_panel(starter)["sections"][0]["top"]]
    assert labels == ["IP", "K", "WHIP", "HR", "HBP", "WAR"]

    closer = dict(starter, sv=38, hld=2)
    labels = [b["label"] for b in site_build._mlb_panel(closer)["sections"][0]["top"]]
    assert labels == ["IP", "K", "WHIP", "HR", "HBP", "WAR", "SV", "HLD"]


def test_a_second_role_worth_showing_gets_a_section():
    panel = site_build._mlb_panel(
        _batter(secondary_stats=[_pitcher_line(scaled_score=41.0)]))

    assert [s["label"] for s in panel["sections"]] == ["Batting", "Pitching"]
    assert panel["note"] == ""


def test_a_second_role_worth_little_gets_a_sentence():
    """A pitcher with four at-bats is not a two-way player, and a section of
    his batting would say he is."""
    panel = site_build._mlb_panel(
        _batter(secondary_stats=[_pitcher_line(scaled_score=4.0,
                                               role_points=45.0)]))

    assert [s["label"] for s in panel["sections"]] == ["Batting"]
    # The verb is not the noun plus "ed": nobody has "pitchered".
    assert panel["note"].startswith("Also pitched: 45.0 points")
    assert "2.0 after the half" in panel["note"]


def test_a_second_role_worth_nothing_is_not_mentioned():
    panel = site_build._mlb_panel(
        _batter(secondary_stats=[_pitcher_line(scaled_score=0.0,
                                               role_points=0.0)]))
    assert panel["note"] == ""


def test_a_player_with_no_second_role_does_not_break_the_build():
    """Pandas fills the missing list with NaN, and NaN is truthy -- which took
    the whole site build down with an AttributeError."""
    assert site_build._mlb_panel(_batter(secondary_stats=float("nan")))
    assert site_build._mlb_panel(_batter(secondary_stats=[]))
    assert site_build._mlb_panel(_batter(secondary_stats=None))


# --- motorsport: counted, not scored ---------------------------------------

def test_a_driver_is_shown_in_his_own_series_vocabulary():
    """NASCAR counts top fives, Formula 1 counts podiums, and neither page
    borrows the other's word for a good day."""
    nascar = site_build._motorsport_panel(
        {"league": "NASCAR", "role": "Driver", "starts": 28, "events_held": 30,
         "wins": 3, "top_fives": 11, "top_tens": 17})
    assert nascar["head"] == [["Races started", "28"], ["Races run", "30"]]
    assert [(b["label"], b["value"]) for b in nascar["top"]] == [
        ("Wins", "3"), ("Top 5", "11"), ("Top 10", "17")]

    f1 = site_build._motorsport_panel(
        {"league": "F1", "role": "Driver", "events": 17,
         "wins": 4, "podiums": 9, "top_tens": 15})
    assert [(b["label"], b["value"]) for b in f1["top"]] == [
        ("Wins", "4"), ("Podiums", "9"), ("Points finishes", "15")]


def test_a_driver_box_carries_no_points_strip():
    """A win is 55 in NASCAR and 25 in Formula 1, and a top five is worth
    whatever the five finishes in it were worth. A strip under the box would
    print either a sum that is not a category or a blank that reads as nothing
    earned, so the box says outright that it has none."""
    panel = site_build._motorsport_panel(
        {"league": "NASCAR", "role": "Driver", "events": 28,
         "wins": 3, "top_fives": 11, "top_tens": 17})

    assert all(box["bare"] for box in panel["top"])
    assert not any("points" in box for box in panel["top"])


def test_a_driver_who_has_not_raced_reads_as_unknown_not_as_zero():
    panel = site_build._motorsport_panel({"league": "NASCAR", "role": "Driver"})
    assert panel["head"] == [["Races started", "—"], ["Races run", "—"]]
    assert [b["value"] for b in panel["top"]] == ["—"] * 3


def test_a_league_with_no_driver_vocabulary_gets_no_driver_panel():
    assert site_build._motorsport_panel({"league": "PGA", "role": "Golfer"}) is None


def test_a_team_shows_what_it_lost_as_well_as_what_it_won():
    """Losses are not scored and are shown anyway: "Wins 11" says nothing about
    whether the other six were lost or have not been played, which is the first
    thing a reader wants off a club's line."""
    assert site_build._team_record(_nfl_team()) == "11\u20135\u20131"
    # A record with no ties in it is not written "11-6-0".
    assert site_build._team_record(
        _nfl_team(reg_ties=0, reg_losses=6.0)) == "11\u20136"
    # Whatever the league puts third: the NHL prints overtime losses there.
    assert site_build._team_record(
        {"reg_wins": 45, "reg_losses": 25, "reg_otl": 12}) == "45\u201325\u201312"
    # And a college team, whose scorer spells both without a prefix.
    assert site_build._team_record({"wins": 9, "losses": 3}) == "9\u20133"


def test_a_club_with_no_losses_counted_shows_no_record():
    """A row stored before losses were carried. Half a record is worse than
    none -- "11-0" for a team that lost six is a wrong number, and a missing
    line is only a missing line.

    Told apart from a club that has not played, which knows nothing about its
    record rather than half of it, and gets the line with a dash in it."""
    assert site_build._team_record({"reg_wins": 11}) == ""
    assert site_build._team_record({}) is None
    panel = site_build._nfl_team_panel(_nfl_team(reg_losses=None))
    assert panel["head"] == [["Division", "1st in NFC West"]]


def test_a_hockey_record_is_three_numbers_even_when_the_third_is_nought():
    """45-25-0 is how the NHL writes a club that never lost in overtime. The
    NFL does not write 11-6-0, so which it is comes off the column the third
    figure was found under rather than off whether it is nought."""
    assert site_build._team_record(
        {"reg_wins": 45, "reg_losses": 37, "reg_otl": 0}) == "45–37–0"
    assert site_build._team_record(
        {"reg_wins": 11, "reg_losses": 6, "reg_ties": 0}) == "11–6"


def test_every_club_leads_with_a_record_whether_or_not_it_has_played():
    """Hockey's clubs read "Games played --  Standings points --" where every
    other club's panel leads with its record, so the one heading that says what
    a club has done was the one heading a club without a season did not get."""
    for league in ("NHL", "NBA", "NCAAF", "NCAAM", "NCAA Baseball"):
        head = site_build._counted_team_panel(league, {})["head"]
        assert head[0] == ["Record", "—"], league
    for league in ("NFL", "MLB"):
        head = site_build._panel_before_a_season(league, "Team", "")["head"]
        assert head[0] == ["Record", "—"], league
    # And a club that has played leads with the real thing, in the shape its
    # own standings print.
    head = site_build._counted_team_panel("NHL", {
        "reg_wins": 45, "reg_losses": 25, "reg_otl": 12, "games_played": 82,
        "standings_points": 102,
    })["head"]
    assert head[0] == ["Record", "45–25–12"]


def test_a_driver_is_read_off_the_asset_rather_than_the_feed_that_pulled_him():
    """NASCAR and Formula 1 arrive as one feed called Motorsports and are
    stored under that name, so the stored row says "Motorsports" where every
    other sport says its own league. Reading the series off the row left every
    driver with no panel at all -- the figures reached the page and were shown
    as a table of raw column names."""
    row = {"league": "Motorsports", "role": "Driver", "starts": 17,
           "events_held": 18, "wins": 4, "podiums": 9, "top_tens": 15}

    assert site_build._motorsport_panel(row) is None
    panel = site_build._motorsport_panel(row, "F1")
    assert [(b["label"], b["value"]) for b in panel["top"]] == [
        ("Wins", "4"), ("Podiums", "9"), ("Points finishes", "15")]


def test_a_part_timer_does_not_read_as_a_regular_having_a_quiet_year():
    """The same heading every other slot carries: a start count alone cannot
    say whether the rest of the calendar was missed or has not been run."""
    panel = site_build._motorsport_panel(
        {"league": "NASCAR", "role": "Driver", "starts": 6, "events_held": 28,
         "wins": 0, "top_fives": 1, "top_tens": 2})
    assert panel["head"] == [["Races started", "6"], ["Races run", "28"]]


def test_football_says_games_played_the_same_way_every_other_slot_does():
    """It read "— team games  — played": the same pair, backwards, in words
    nothing else on the site uses."""
    row = _nfl_row(passing_yards=334, team_games=17, regular_games=16)
    assert (site_build._nfl_panel(row)["head"]
            == site_build._games_head(row)
            == [["Games played", "16"], ["Team games", "17"]])


# --- how many games his club played, for a club nobody drafted --------------

def test_a_club_count_covers_clubs_nobody_drafted():
    """Eintracht Frankfurt is on no roster, so it has no row of its own and its
    player's heading had no second number at all. The team pull reads a whole
    league and is narrowed to the roster afterwards; this is that figure."""
    recorded = {"Eintracht Frankfurt": 3.0, "Bayern Munich": 4.0}
    games = site_build._club_games(None, recorded)

    assert games["Eintracht Frankfurt"] == 3.0


def test_a_club_count_is_read_on_the_same_basis_his_own_is():
    """Both sides count every competition. Mbappé read "Games played 6, Team
    games 6" with a Champions League section under it saying he had played one
    more: two numbers disagreeing about the same season, in the same panel."""
    import pandas as pd

    stats = pd.DataFrame([{"team": "Arsenal", "matches_played": 6.0,
                           "counted_matches": 5.0}])
    assert site_build._club_games(stats)["Arsenal"] == 6.0


def test_what_the_league_pull_wrote_down_wins_over_a_club_row():
    import pandas as pd

    stats = pd.DataFrame([{"team": "Arsenal", "counted_matches": 5.0}])
    games = site_build._club_games(stats, {"Arsenal": 6.0})

    assert games["Arsenal"] == 6.0


def test_a_batter_is_shown_against_his_club_s_games():
    """Six games is a season interrupted or a club that has played six, and a
    figure on its own cannot tell them apart."""
    panel = site_build._mlb_panel(
        {"league": "MLB", "role": "Batter", "games": 6, "ab": 21, "h": 4},
        team_games=7.0)

    assert panel["head"] == [["Games played", "6"], ["Team games", "7"]]


def test_a_batter_whose_club_nobody_drafted_says_so_rather_than_guessing():
    panel = site_build._mlb_panel(
        {"league": "MLB", "role": "Batter", "games": 6}, team_games=None)
    assert panel["head"] == [["Games played", "6"], ["Team games", "—"]]


def test_a_club_with_no_match_count_gets_no_figure_rather_than_a_zero():
    """A row stored before the count was carried. Half a heading is worse than
    none: "Games played 4, Team games 0" reads as a club that has not played."""
    import pandas as pd

    stats = pd.DataFrame([
        {"team": "Arsenal", "matches_played": 6.0},
        {"team": "Bayern Munich", "matches_played": None},
    ])
    assert site_build._club_games(stats) == {"Arsenal": 6.0}


def test_a_heading_counts_every_competition_the_panel_shows():
    """Mbappé read "Games played 6, Team games 6" with a Champions League
    section under it saying he had played one more."""
    row = {"league": "La Liga", "team": "Real Madrid", "matches": 6.0,
           "starts": 6.0, "goals": 7.0, "assists": 2.0,
           "domestic_detail": [{"competition": "La Liga", "games": 6.0,
                                "starts": 6.0, "goals": 7.0, "assists": 2.0,
                                "points": 46.0}],
           "bonus_detail": [{"competition": "UEFA Champions League",
                             "games": 1.0, "starts": 1.0, "points": 2.0,
                             "share": 0.05, "scalar": 1.9, "adds": 3.8,
                             "credited": False}]}
    panel = site_build._soccer_player_panel(row, {"Real Madrid": 7.0}, "La Liga")

    assert panel["head"] == [["Games played", "7"], ["Team games", "7"]]


def test_a_competition_he_was_not_picked_for_is_not_a_section():
    """ESPN returns a cup's whole squad and fills the figures in only where
    they exist, so a club that played a tie hands back every player on its
    books at nought. Guirassy's profile carried a DFB-Pokal section reading
    "Apps / Starts 0 / 0", which says he was there and did nothing rather than
    that he was not picked."""
    row = {"league": "Bundesliga", "team": "Borussia Dortmund", "matches": 3.0,
           "domestic_detail": [
               {"competition": "Bundesliga", "games": 3.0, "starts": 3.0,
                "goals": 2.0, "assists": 2.0, "points": 20.0},
               {"competition": "DFB-Pokal", "games": 0.0, "starts": 0.0,
                "goals": 0.0, "assists": 0.0, "points": 0.0},
           ]}
    panel = site_build._soccer_player_panel(row, {}, "Bundesliga")

    assert [s["name"] for s in panel["sections"]] == ["Bundesliga"]


# --- golf: counted, not scored ---------------------------------------------

def test_a_golfer_is_shown_the_way_a_driver_is():
    panel = site_build._golf_panel(
        {"league": "PGA", "role": "Golfer", "starts": 9, "wins": 1,
         "top_fives": 3, "top_tens": 5, "made_cut": 7})

    assert panel["head"] == [["Tournaments", "9"]]
    assert [(b["label"], b["value"]) for b in panel["top"]] == [
        ("Wins", "1"), ("Top 5", "3"), ("Top 10", "5"), ("Cuts made", "7 / 9")]
    assert all(box["bare"] for box in panel["top"]), "no points strip"


def test_a_cut_is_a_pair_because_the_count_alone_says_nothing():
    """"Cuts made 7" cannot say whether he missed two or entered seven and
    made them all, and for a sport a player picks his own schedule in that is
    the whole of the figure."""
    panel = site_build._golf_panel(
        {"league": "PGA", "starts": 7, "made_cut": 7})
    assert panel["top"][-1]["value"] == "7 / 7"


def test_a_golfer_who_has_not_teed_off_reads_as_unknown_not_as_zero():
    panel = site_build._panel_before_a_season("PGA", "Player", "Golfer")
    assert [b["value"] for b in panel["top"]] == ["—"] * 4


# --- tennis: the points are the achievement --------------------------------

def _tier(label, points, straight=0.0, note="", entered=1):
    return {"label": label, "points": points, "straight": straight,
            "note": note, "entered": entered}


def test_a_tennis_season_is_shown_by_the_size_of_the_field():
    panel = site_build._tennis_panel({"league": "ATP", "tier_detail": [
        _tier("ATP 250", 150.0, 37.5, "W · R32", 2),
        _tier("ATP 500", None, 0.0, "", 0),
        _tier("ATP 1000", 550.0, 137.5, "F"),
        _tier("Grand Slam", 100.0, 50.0, "R32"),
        _tier("ATP Finals", 200.0, 50.0, "RR 1-1"),
        _tier("Team Events", 50.0, 12.5, "1-0"),
    ]})

    assert panel["head"] == [["Tournaments", "6"]]
    # Whole numbers, every one: these are the tour's own totals, and the bonus
    # that would put a half on the end of them is the superscript instead.
    assert [(b["label"], b["value"], b.get("sup"), b.get("aside"))
            for b in panel["top"]] == [
        ("ATP 250", "150", "+37.5", "W · R32"),
        ("ATP 500", "—", None, None),
        ("ATP 1000", "550", "+137.5", "F"),
        ("Grand Slam", "100", "+50", "R32"),
        ("ATP Finals", "200", "+50", "RR 1-1"),
        ("Team Events", "50", "+12.5", "1-0"),
    ]


def test_a_tennis_box_has_no_points_strip_because_it_is_one():
    """The big figure is what the tier paid. A strip beneath would print the
    same number twice."""
    panel = site_build._tennis_panel({"league": "ATP", "tier_detail": [
        _tier("ATP 250", 150.0, 37.5, "W")]})
    assert all(box["bare"] for box in panel["top"])


def test_a_womens_profile_names_the_tours_she_plays():
    """Both tours call them that, so a WTA profile says "WTA 1000"."""
    panel = site_build._panel_before_a_season("WTA", "Player", "Singles")
    assert [b["label"] for b in panel["top"]] == [
        "WTA 250", "WTA 500", "WTA 1000", "Grand Slam", "WTA Finals",
        "Team Events"]
    assert [b["value"] for b in panel["top"]] == ["—"] * 6


def test_an_apostrophe_survives_the_whole_way_to_a_chart_label():
    """St. John's Red Storm reached the results page as "St. John&#x27;s Red
    Storm" and its bar as "St. John&amp;#x27;s Red..." -- escaped once on the
    way into the profiles, escaped again by the chart that drew it, and then
    cut off mid-entity by the label that shortened it. Saint Mary's Gaels and
    Ja'Marr Chase went the same way."""
    from html import escape as html_escape

    from whul.site import charts
    from whul.site.build import _profile_payload, _results_table

    THEM = "St. John's Red Storm"
    profiles = {"a1": {"name": THEM, "league": "NCAAM", "kind": "Team"}}

    frame = pd.DataFrame([
        {"asset_id": "a1", "manager_id": "SS", "score": 1.0, "counts": 1},
    ])
    table = _results_table(frame, profiles, ["SS"])
    assert "&amp;#x27;" not in table
    assert html_escape(THEM) in table

    # The chart is handed text and escapes it itself, so the label is the whole
    # name rather than 22 characters of which five are an entity.
    bars = charts.slot_sections(
        [("NCAAM", "NCAAM 1", "#1")], [("Shelby", 1)],
        {("Shelby", "NCAAM 1"): (1.0, "a1", THEM, "Team")},
    )
    assert "&amp;#x27;" not in bars
    assert f'data-name="{html_escape(THEM)}"' in bars
    assert "Red Storm" in bars, "the name was truncated inside an entity"

    # And the payload the profile window reads is escaped exactly once, since
    # the window writes it into innerHTML.
    payload = _profile_payload(profiles)
    assert "&amp;#x27;" not in payload
    assert html_escape(THEM) in payload


# --- a national team's panel ------------------------------------------------

def test_a_national_team_is_shown_the_way_a_club_is():
    """Same panel, same boxes, same grouping by competition. What is added is
    the one thing that differs: a club's competitions add up to its season and
    a national team's do not."""
    from whul.site.build import _soccer_panel

    row = {
        "lift": 1.3333,
        "sections": [
            {"kind": "international", "name": "FIFA World Cup", "rung": "World",
             "matches": 6, "points": 120.0, "counted": 160.0,
             "wins": 4, "pts_wins": 100.0, "draws": 1, "pts_draws": 10.0,
             "losses": 1, "pts_losses": 0.0, "shootout_wins": 0,
             "pts_shootout_wins": 0.0, "shootout_losses": 0,
             "pts_shootout_losses": 0.0, "big_margins": 2, "pts_big_margins": 6.0,
             "clean_sheets": 2, "pts_clean_sheets": 4.0,
             "phases": [
                 {"label": "Group stage", "shape": "round-robin", "matches": 3,
                  "wins": 2, "draws": 1, "losses": 0, "big_margins": 1,
                  "clean_sheets": 1, "pts_wins": 50.0, "pts_draws": 10.0,
                  "pts_losses": 0.0, "pts_big_margins": 3.0,
                  "pts_clean_sheets": 2.0},
                 {"label": "Knockout", "shape": "knockout", "matches": 3,
                  "wins": 2, "losses": 1, "shootout_wins": 0,
                  "shootout_losses": 0, "big_margins": 1, "clean_sheets": 1,
                  "pts_wins": 50.0, "pts_losses": 0.0, "pts_shootout_wins": 0.0,
                  "pts_shootout_losses": 0.0, "pts_big_margins": 3.0,
                  "pts_clean_sheets": 2.0},
             ]},
        ],
    }
    panel = _soccer_panel(row)
    assert panel["kind"] == "soccer"
    section = panel["sections"][0]
    assert section["head"] == [["Matches", "6"], ["Rung", "World"]]

    # Earned big, counted as the superscript, and no points strip under it --
    # the strip would be a third number saying one of the first two again.
    assert section["total"]["value"] == "120.0"
    assert "160.0" in section["total"]["sup"]
    assert section["total"]["bare"] is True

    # A group is round-robin and a knockout tie is not, said by the phase
    # rather than inferred from its wording.
    group, knockout = section["blocks"]
    assert [b["label"] for b in group["top"]] == ["W", "D", "L"]
    assert [b["label"] for b in knockout["top"]] == ["W", "SO W", "L", "SO L"]

    # And the arithmetic between the sections and the score is on the page.
    assert "half" in panel["note"] and "1.33" in panel["note"]


def test_a_club_soccer_panel_gains_nothing_from_the_international_one():
    """The club panel is the one this was modelled on and must be untouched:
    no total box, because a club's competitions simply add up, and no note."""
    from whul.site.build import _soccer_panel

    panel = _soccer_panel({
        "sections": [{"kind": "league", "name": "La Liga", "matches": 5,
                      "wins": 2, "draws": 1, "losses": 2, "big_margins": 2,
                      "clean_sheets": 2, "pts_wins": 6.0, "pts_draws": 1.0,
                      "pts_losses": 0.0, "pts_big_margins": 2.0,
                      "pts_clean_sheets": 2.0, "position": 9, "of": 20}],
    })
    section = panel["sections"][0]
    assert "total" not in section
    assert "note" not in panel
    assert section["head"] == [["Position", "9 of 20"]]


# --- a baseball club's panel ------------------------------------------------

def _mlb_club() -> dict:
    return {
        "league": "MLB", "team": "Chicago Cubs", "games_played": 162.0,
        "reg_wins": 94.0, "reg_losses": 68.0, "reg_big_wins": 31.0,
        "shutouts": 12.0, "run_diff": 142.0, "playoff_game_wins": 7.0,
        "series_wc_or_bye": 1.0, "series_lds": 1.0, "series_lcs": 1.0,
        "series_ws": 0.0, "is_division_champ": 1.0,
        "pts_reg_wins": 188.0, "pts_big_wins": 31.0, "pts_shutouts": 24.0,
        "pts_run_diff": 7.1, "pts_div_champ": 5.0, "pts_playoff": 39.0,
        "total_points": 294.1,
        "season_lines": [
            {"season": 2026, "reg_wins": 30.0, "reg_big_wins": 10.0,
             "shutouts": 4.0, "run_diff": 44.0, "pts_reg_wins": 60.0,
             "pts_big_wins": 10.0, "pts_shutouts": 8.0, "pts_run_diff": 2.2},
            {"season": 2027, "reg_wins": 64.0, "reg_big_wins": 21.0,
             "shutouts": 8.0, "run_diff": 98.0, "pts_reg_wins": 128.0,
             "pts_big_wins": 21.0, "pts_shutouts": 16.0, "pts_run_diff": 4.9},
        ],
    }


def test_a_baseball_club_reconciles_with_its_own_score():
    """The invariant every panel here is built on: the boxes add up to the
    number printed under them. Four season boxes, the division title, and
    what October paid."""
    from whul.site.build import _mlb_team_panel

    row = _mlb_club()
    panel = _mlb_team_panel(row)
    assert [b["label"] for b in panel["top"]] == [
        "Wins", "Run diff", "Big wins", "Shutouts"]
    season = sum(b["points"] for b in panel["top"])
    october = panel["posts"][0]
    assert season + panel["outcomes"][0]["points"] + float(
        october["total"]["value"]) == pytest.approx(row["total_points"], abs=0.2)
    # And October's own boxes add up to what October paid.
    assert sum(b["points"] for b in october["top"] + october["secondary"]) \
        == pytest.approx(row["pts_playoff"], abs=0.2)
    assert [b["label"] for b in october["secondary"]] == [
        "Wild card", "Division series", "Championship series"]


def test_a_baseball_club_says_which_summer_a_figure_came_from():
    """A contract year is the tail of one summer and the front of the next, and
    a single line across both belongs to neither -- the question its own
    batters' panels already answer."""
    from whul.site.build import _mlb_team_panel

    panel = _mlb_team_panel(_mlb_club())
    assert [y["year"] for y in panel["years"]] == ["2026", "2027"]
    assert sum(y["raw"] for y in panel["years"]) == pytest.approx(
        sum(b["points"] for b in panel["top"]), abs=0.2)


def test_a_year_nobody_has_played_is_dashes_rather_than_zeroes():
    """A tab that is not there says the question cannot be asked; a zero says
    the club played and scored nothing. Neither is true of next summer."""
    from whul.site.build import _mlb_team_panel

    row = _mlb_club()
    row["season_lines"] = [row["season_lines"][0]]
    panel = _mlb_team_panel(row)
    ahead = next(y for y in panel["years"] if y["year"] == "2027")
    assert [b["value"] for b in ahead["top"]] == ["—"] * 4
    assert all(b["points"] is None for b in ahead["top"])


def test_a_clubs_nan_role_does_not_send_it_down_the_player_path():
    """NaN is truthy. `not row.get("role")` reads a club as a player, which is
    how every NFL club took the player path and how a baseball build died on
    `.get`."""
    from whul.site.build import _has_a_role

    assert _has_a_role({"role": "Batter"})
    assert not _has_a_role({"role": float("nan")})
    assert not _has_a_role({"role": ""})
    assert not _has_a_role({})


# --- the format is forced, whether or not anything has been played ----------

#: Every league a rostered asset can be filed under, and which kinds of asset
#: that league holds. Not the slot categories: an umbrella is a slot and not a
#: league, and nobody's asset is filed under "Tennis".
EVERY_SLOT = (
    ("Premier League", "Team"), ("La Liga", "Team"), ("Serie A", "Team"),
    ("Bundesliga", "Team"), ("Ligue 1", "Team"), ("MLS", "Team"),
    ("NWSL", "Team"),
    ("Premier League", "Player"), ("La Liga", "Player"), ("Serie A", "Player"),
    ("Bundesliga", "Player"), ("Ligue 1", "Player"), ("MLS", "Player"),
    ("NWSL", "Player"),
    ("NFL", "Team"), ("NFL", "Player"),
    ("NBA", "Team"), ("NBA", "Player"),
    ("MLB", "Team"), ("MLB", "Player"),
    ("NHL", "Team"), ("NHL", "Player"),
    ("NCAAF", "Team"), ("NCAAM", "Team"), ("NCAAW", "Team"),
    ("NCAA Baseball", "Team"), ("NCAA Softball", "Team"),
    ("Men's Intl Soccer", "Team"), ("Women's Intl Soccer", "Team"),
    ("ATP", "Player"), ("WTA", "Player"),
    ("F1", "Player"), ("NASCAR", "Player"),
    ("PGA", "Player"),
    # And by the umbrella, because a caller that has the slot and not the
    # league should still get an answer.
    ("Tennis", "Player"), ("Motorsports", "Player"),
    ("Club Soccer", "Player"), ("Club Soccer", "Team"),
)


@pytest.mark.parametrize("league,kind", EVERY_SLOT)
def test_every_slot_has_its_panel_before_it_has_played(league, kind):
    """The one that keeps being forgotten. Two panels shipped without anybody
    seeing them -- a national team's and a basketball club's -- because the
    leagues they belong to open in October and a club with no figures got no
    panel at all, only "No stat lines recorded for this day yet".

    A profile exists from the day the asset is drafted. What it says before the
    season is the same thing it will say during it, with dashes where the
    figures go, so a layout can be looked at in August and a reader can see
    what is coming.
    """
    panel = site_build._panel_before_a_season(league, kind, "")
    assert panel, f"{kind} in {league} has no panel before its season"
    # The panel shapes differ -- boxes at the top level, inside a `season`,
    # inside sections, inside a section's phases -- and this is deliberately
    # indifferent to which: what is being asserted is that there are boxes and
    # that they read as unplayed, not where they live.
    holders = [panel, panel.get("season") or {}]
    boxes = [
        b for holder in holders
        for key in ("top", "secondary", "outcomes")
        for b in holder.get(key, [])
    ] + [
        b for section in panel.get("sections", [])
        for block in section.get("blocks", [])
        for key in ("top", "secondary")
        for b in block.get(key, [])
    ] + [
        b for section in panel.get("sections", [])
        for key in ("top", "secondary")
        for b in section.get(key, [])
    ]
    assert boxes, f"{kind} in {league} has a panel with no boxes in it"
    # Dashes, not zeroes. "0" says the asset played and did nothing, which is
    # the opposite of what a season that has not opened means -- and it is what
    # a reader would check the standings against.
    counts = [b for b in boxes if not b.get("outcome")]
    assert any(b["value"] == "—" for b in counts), (
        f"{kind} in {league} reads as a season played and lost: "
        f"{[(b['label'], b['value']) for b in counts][:6]}"
    )


# --- the clubs scored by counting things ------------------------------------

def _panel_total(panel: dict) -> float:
    """Every box on a club's panel, added up the way a reader would."""
    got = 0.0
    for holder in [panel, *panel.get("posts", [])]:
        for key in ("top", "secondary", "outcomes"):
            for box in holder.get(key, []):
                if box.get("points") is not None and not box.get("note"):
                    got += box["points"]
    return round(got, 1)


def _ncaa_game(home, away, hs, as_, hc="ACC", ac="ACC", season_type=2, notes="",
               season=2026, day="2026-11-15"):
    return {"season": season, "season_type": season_type, "notes": notes,
            "home_team": home, "away_team": away, "home_conference": hc,
            "away_conference": ac, "home_score": hs, "away_score": as_,
            "completed": True, "game_date": day}


def test_a_college_football_club_reconciles_with_its_own_score():
    """The invariant every panel here is built on, now for the seven leagues
    that were still on the old table: the boxes add up to the number printed
    under them, against the scorer's own arithmetic rather than a copy of it."""
    from whul.scoring import ncaa

    games = pd.DataFrame(
        [_ncaa_game("A", "B", 40, 3), _ncaa_game("B", "A", 10, 31),
         _ncaa_game("A", "C", 28, 21, ac="SEC"),
         _ncaa_game("A", "B", 35, 0, season_type=3, notes="ACC Championship"),
         _ncaa_game("A", "D", 30, 10, season_type=3, notes="CFP Semifinal",
                    ac="B1G")]
        + [_ncaa_game("A", "B", 20, 17) for _ in range(6)])
    row = ncaa.score_football(games).set_index("team").loc["A"].to_dict()
    panel = site_build._counted_team_panel("NCAAF", row)
    assert _panel_total(panel) == pytest.approx(row["total_points"], abs=0.2)
    # The conference record rides on one box rather than taking two, since the
    # denominator is not scored and is most of what the numerator means.
    conf = next(b for b in panel["top"] if b["label"] == "Conference wins")
    assert conf["aside"] == f"of {row['conf_games']:,.0f}"


def test_a_college_basketball_club_reconciles_with_its_own_score():
    from whul.scoring import ncaa

    games = pd.DataFrame(
        [_ncaa_game("A", "B", 90, 60), _ncaa_game("B", "A", 70, 95),
         _ncaa_game("A", "C", 88, 55, ac="SEC"),
         _ncaa_game("A", "B", 80, 70, season_type=3,
                    notes="ACC Tournament Championship"),
         _ncaa_game("A", "D", 78, 70, season_type=3,
                    notes="NCAA Tournament Second Round", ac="B1G")]
        + [_ncaa_game("A", "B", 70, 65) for _ in range(8)])
    row = ncaa.score_basketball(games, "NCAAM").set_index("team").loc["A"].to_dict()
    panel = site_build._counted_team_panel("NCAAM", row)
    assert _panel_total(panel) == pytest.approx(row["total_points"], abs=0.2)


def test_a_college_diamond_club_reconciles_with_its_own_score():
    """And its rounds carry the wins that decided them, rather than giving each
    count a box whose points strip would have to read nought."""
    from whul.scoring import ncaa

    games = pd.DataFrame(
        [_ncaa_game("A", "B", 9, 2), _ncaa_game("B", "A", 1, 7),
         _ncaa_game("A", "C", 5, 4, ac="SEC")] * 4
        + [_ncaa_game("A", "D", 6, 3, season_type=3, notes="Regional")
           for _ in range(3)]
        + [_ncaa_game("A", "D", 5, 2, season_type=3, notes="Super Regional")
           for _ in range(2)]
        + [_ncaa_game("A", "D", 4, 1, season_type=3,
                      notes="College World Series") for _ in range(4)])
    row = ncaa.score_diamond(games, "NCAA Baseball").set_index("team").loc["A"].to_dict()
    panel = site_build._counted_team_panel("NCAA Baseball", row)
    assert _panel_total(panel) == pytest.approx(row["total_points"], abs=0.2)
    regional = next(b for b in panel["posts"][0]["top"] if b["label"] == "Regional")
    assert regional["value"] == "Yes" and regional["aside"] == "3 won"


def test_a_basketball_club_reconciles_with_its_own_score():
    from whul.scoring import nba

    def ev(home, away, hs, as_, stype=2, notes=""):
        return {"season": 2026, "season_type": stype, "notes": notes,
                "home_team": home, "away_team": away, "home_score": hs,
                "away_score": as_, "completed": True, "game_date": "2026-12-01"}

    rows = ([ev("A", "B", 120, 95) for _ in range(30)]
            + [ev("B", "A", 110, 90) for _ in range(20)]
            + [ev("A", "C", 118, 100, stype=3) for _ in range(9)]
            + [ev("A", "D", 112, 105, notes="In-Season Tournament")
               for _ in range(3)])
    row = nba.score_teams(pd.DataFrame(rows)).set_index("team").loc["A"].to_dict()
    panel = site_build._counted_team_panel("NBA", row)
    assert _panel_total(panel) == pytest.approx(row["total_points"], abs=0.2)
    # No games-played column exists in this sport, and the record says it
    # anyway -- so the heading does not print a dash that reads as a fault.
    assert panel["head"] == [["Record", f"{row['reg_wins']:,.0f}–{row['reg_losses']:,.0f}"]]


def test_a_hockey_club_carries_the_lift_its_points_were_given():
    """An 82-game history scored against an 84-game season: the points are
    lifted and the counts are not, so a page that multiplied a count by a
    weight would print a figure the score does not contain."""
    from whul.scoring import nhl

    regular = pd.DataFrame([{
        "season": 20262027, "team": "A", "games_played": 82, "wins": 50,
        "losses": 22, "ot_losses": 10, "regulation_wins": 42,
        "goals_for": 280, "goals_against": 220, "points": 110,
    }])
    post = pd.DataFrame([{"season": 20262027, "team": "A",
                          "games_played": 20, "wins": 12}])
    row = nhl.score_teams(regular, post).set_index("team").loc["A"].to_dict()
    panel = site_build._counted_team_panel("NHL", row)
    assert _panel_total(panel) == pytest.approx(row["total_points"], abs=0.2)
    wins = next(b for b in panel["top"] if b["label"] == "Wins")
    assert wins["points"] > 50 * nhl.PTS_WIN, "the lift is missing"


def test_an_outcome_nobody_has_lost_yet_does_not_read_as_lost():
    """Indiana read "Conference title No / Regular-season title No / Playoff
    No" in week two of a season it was 2-0 in. A club that has not won its
    conference in September has not failed to either."""
    row = {"wins": 2.0, "losses": 0.0, "games_played": 2.0, "point_diff": 91.0,
           "big_wins": 2.0, "conf_wins": 0.0, "conf_games": 0.0,
           "conf_title_win": 0.0, "playoff_app": 0.0, "playoff_wins": 0.0,
           "pts_reg_champ": 0.0}
    panel = site_build._counted_team_panel("NCAAF", row)
    assert [b["value"] for b in panel["outcomes"]] == ["—"] * 3


def _driver_store(tmp_path, affiliation="Great Britain", car_number="44"):
    """A rostered driver, with the car number his feed row carries."""
    from datetime import date

    from whul.store import open_store, rosters

    store = open_store(str(tmp_path / "d.sqlite3"))
    rosters.add_manager(store, "JM")
    rosters.create_slots(store, "JM", "2026-27")
    store.upsert("assets", [{
        "asset_id": "d1", "asset_type": "Player", "display_name": "A Driver",
        "league": "F1", "role": "Driver", "norm_key": "F1",
        "affiliation": affiliation, "active": 1, "created_at": "2026-08-21",
    }], keys=("asset_id",))
    slot = store.query(
        "SELECT slot_id FROM roster_slots WHERE season = ? AND category = ? "
        "AND asset_type = 'Player' ORDER BY slot_index",
        ("2026-27", "Motorsports"))
    rosters.assign(store, slot.loc[0, "slot_id"], "d1", "2026-08-21")
    if car_number:
        store.record_stats(
            [{"asset_id": "d1", "car_number": car_number, "total_points": 1.0}],
            source="s", season="2026-27", as_of=date(2026, 9, 6), league="F1",
        )
    return store


def test_a_driver_is_badged_with_his_country_not_his_car(tmp_path):
    """His corner is a flag, and a flag has a country in it. `_identity` gives
    his *line* the car number, which is right there -- a driver has no club --
    and is a filename that can never exist: six drivers were being looked up as
    `flag/-44.png`, so their real flags were never fetched and never reported
    missing either."""
    from whul.site.build import badge_names

    assert badge_names(_driver_store(tmp_path), "2026-27")["d1"] == \
        "Great Britain"


def test_a_footballer_still_takes_the_feeds_club(tmp_path):
    """The rule it is an exception to, kept honest: only an individual
    athlete's badge comes off the sheet."""
    from whul.site.build import badge_names

    store = _badge_store(tmp_path, "Rennes", feed_team="Stade Rennais")
    assert badge_names(store, "2026-27")["p1"] == "Stade Rennais"


# --- what the auction bought -------------------------------------------------

def _priced_store(tmp_path, *slots):
    """A store holding priced slots: ``(slot_id, manager, category, cost)``."""
    from whul.store import open_store, rosters

    store = open_store(str(tmp_path / "c.sqlite3"))
    for manager in {m for _, m, _, _ in slots}:
        rosters.add_manager(store, manager)
    store.upsert("roster_slots", [
        {"slot_id": slot, "manager_id": manager, "season": "2026-27",
         "category": category, "asset_type": "Player", "slot_index": index}
        for index, (slot, manager, category, _) in enumerate(slots)
    ], keys=("slot_id",))
    store.upsert("assets", [
        {"asset_id": f"a-{slot}", "asset_type": "Player",
         "display_name": f"Player {slot}", "league": "NFL", "role": "",
         "norm_key": "NFL", "affiliation": "", "active": 1,
         "created_at": "2026-08-21"}
        for slot, _, _, _ in slots
    ], keys=("asset_id",))
    store.upsert("slot_occupancy", [
        {"slot_id": slot, "asset_id": f"a-{slot}", "start_date": "2026-08-21",
         "end_date": None, "cost": cost, "note": ""}
        for slot, _, _, cost in slots
    ], keys=("slot_id", "start_date"))
    store.conn.commit()
    return store


def _bars(*rows):
    """The contribution frame, as `pipeline.contributions` returns it."""
    return pd.DataFrame([
        {"manager_id": manager, "category": category, "asset_type": "Player",
         "slot_id": slot, "asset_id": f"a-{slot}", "score": score,
         "counts": counts}
        for slot, manager, category, score, counts in rows
    ])


def test_a_price_is_only_compared_inside_its_own_category(tmp_path):
    """The five of them spent far more per NFL slot than per Olympics slot, and
    the scores those slots return are not on the same footing either. Comparing
    a raw price against a raw score would rank the categories, not the
    managers."""
    store = _priced_store(tmp_path,
                          ("s1", "JM", "NFL", 100.0), ("s2", "SS", "NFL", 100.0),
                          ("s3", "JM", "Olympics", 1.0), ("s4", "SS", "Olympics", 1.0))
    priced = site_build._priced_slots(store, "2026-27", _bars(
        ("s1", "JM", "NFL", 60.0, 1), ("s2", "SS", "NFL", 40.0, 1),
        ("s3", "JM", "Olympics", 8.0, 1), ("s4", "SS", "Olympics", 2.0, 1),
    )).set_index("slot_id")

    # Half the category's money, so half its points are what the price bought.
    assert priced.loc["s1", "expected"] == pytest.approx(50.0)
    assert priced.loc["s1", "surplus"] == pytest.approx(10.0)
    # And the cheap category is judged on its own terms rather than dismissed
    # for being cheap: five points of Olympics beats its price by three.
    assert priced.loc["s3", "expected"] == pytest.approx(5.0)
    assert priced.loc["s3", "surplus"] == pytest.approx(3.0)


def test_the_surpluses_cancel_out(tmp_path):
    """A slot beats its price at another slot's expense, because the shares are
    of one pot. A league-wide total of anything but zero means the arithmetic
    is wrong, not that everybody did well."""
    store = _priced_store(tmp_path,
                          ("s1", "JM", "NFL", 150.0), ("s2", "SS", "NFL", 50.0))
    priced = site_build._priced_slots(store, "2026-27", _bars(
        ("s1", "JM", "NFL", 10.0, 1), ("s2", "SS", "NFL", 30.0, 1),
    ))

    assert priced["surplus"].sum() == pytest.approx(0.0)
    rows = site_build._cost_rows(priced, ["JM", "SS"])
    assert sum(r["surplus"] for r in rows) == pytest.approx(0.0)
    # Ranked by it, so the table opens on whoever beat the room.
    assert [r["manager"] for r in rows] == ["SS", "JM"]


def test_a_benched_slot_is_still_money_spent(tmp_path):
    """It is the whole reason to look: a manager can lead the standings and
    have a third of his auction sitting out."""
    store = _priced_store(tmp_path,
                          ("s1", "JM", "NFL", 40.0), ("s2", "JM", "NFL", 60.0))
    rows = site_build._cost_rows(site_build._priced_slots(store, "2026-27", _bars(
        ("s1", "JM", "NFL", 30.0, 1), ("s2", "JM", "NFL", 20.0, 0),
    )), ["JM"])

    assert rows[0]["spend"] == 100.0
    assert rows[0]["bench_spend"] == 60.0
    assert rows[0]["counting"] == 30.0
    assert rows[0]["score"] == 50.0


def test_a_season_with_no_prices_says_so_rather_than_breaking(tmp_path):
    """Every season before the costs were recorded, and any league that does
    not run an auction at all."""
    store = _priced_store(tmp_path, ("s1", "JM", "NFL", None))

    priced = site_build._priced_slots(store, "2026-27",
                                      _bars(("s1", "JM", "NFL", 30.0, 1)))

    assert priced.empty
    assert "No auction prices" in site_build._cost_table(
        site_build._cost_rows(priced, ["JM"]))


def test_the_cost_figure_is_closed_and_last(site):
    """An argument about the auction, not part of the standings: it opens
    closed, sits at the end of the page, and nothing above it moved to make
    room."""
    out, _ = site
    page = (out / "results.html").read_text()
    costs = page[page.index('id="costs"'):]

    assert costs.startswith('id="costs" data-figure="costs">'), "not open"
    assert 'href="#costs"' in page, "and it is in the index at the top"
    assert page.index('id="everyone"') < page.index('id="costs"')


# --- where two rosters met ----------------------------------------------------

def test_the_meetings_figure_is_closed_and_in_the_index(site):
    """A record, not part of the standings: it opens closed and sits with the
    other asides rather than above the tables people came for."""
    out, _ = site
    page = (out / "results.html").read_text()

    assert 'id="head-to-head" data-figure="head-to-head">' in page, "not open"
    assert 'href="#head-to-head"' in page
    assert page.index('id="everyone"') < page.index('id="head-to-head"')


def test_a_meeting_puts_the_winner_on_the_left():
    """The columns say "won by" and "lost by", and a table whose headings are
    right for some rows and wrong for others is worse than one with none."""
    import pandas as pd

    found = pd.DataFrame([{
        "date": "2026-09-10", "competition": "Champions League",
        "a_id": "a1", "a_name": "RB Leipzig", "a_manager": "LS",
        "a_league": "Bundesliga", "a_category": "Club Soccer Other",
        "b_id": "b1", "b_name": "Como", "b_manager": "JM",
        "b_league": "Serie A", "b_category": "Club Soccer Top 3",
        "a_score": 1.0, "b_score": 4.0, "won": "b", "detail": "",
    }])
    html = site_build._head_to_head_table(found, {}, ["LS", "JM"])

    # Como won, so Como is the first cell and the one marked.
    assert html.index("Como") < html.index("RB Leipzig")
    marked = html[html.index('class="beat"'):]
    assert marked.index("Como") < marked.index("RB Leipzig")
    # And the attribute keeps the ledger's answer, which the script tallies on.
    assert 'data-won="b"' in html
    assert 'data-one="LS"' in html and 'data-two="JM"' in html


def test_a_season_with_no_meetings_says_so(tmp_path):
    from whul.store import open_store

    store = open_store(str(tmp_path / "empty.sqlite3"))
    said = site_build._head_to_head(store, "2026-27", {}, [])

    assert "No two managers" in said
