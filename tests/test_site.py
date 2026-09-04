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
    ("Avery", "NFL 1"): (100.0, "a1", "P. Vance"),
    ("Avery", "NFL 2"): (60.0, "a2", "R. Lockwood"),
}


def test_bars_are_capped_and_rounded_at_the_data_end():
    svg = charts.contribution_chart(SLOT_ROWS, [("Avery", 1)], SLOT_VALUES)
    assert f'height="{charts.SLOT_BAR_THICKNESS}"' in svg
    assert charts.SLOT_BAR_THICKNESS <= 24
    assert 'rx="4' in svg


def test_every_bar_carries_a_native_title_for_keyboard_and_screen_readers():
    svg = charts.contribution_chart(SLOT_ROWS, [("Avery", 1)], SLOT_VALUES)
    assert "<title>Avery — NFL 1: P. Vance 100</title>" in svg


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
    assert ">#1<" in svg and ">#2<" in svg


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


def test_both_charts_ship_a_table_view(site):
    out, _ = site
    html = (out / "index.html").read_text()
    assert html.count("Show as a table") == 2


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
    assert any("Still to draft" in page for page in pages)


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
    assert ">#1<" in svg and ">#2<" in svg


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
    """"WHUL" is unreadable to anyone outside the league."""
    from whul.config.league import LEAGUE_ABBR, LEAGUE_NAME

    assert LEAGUE_NAME == "Wolf Hill Uber League"
    assert LEAGUE_ABBR == "WHUL"
