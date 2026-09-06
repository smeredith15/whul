"""The generated site."""

import json
import re
from datetime import date
from pathlib import Path

import pytest

from whul import simulate
from whul.site import charts, images, theme
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


def test_the_about_page_explains_how_a_score_is_reached(site):
    out, _ = site
    html = (out / "about.html").read_text()
    for step in ("League points", "Normalize", "Accrue by owner", "Best ball"):
        assert step in html


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
