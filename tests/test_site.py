"""The generated site."""

from datetime import date
from pathlib import Path

import pytest

from whul import simulate
from whul.site import charts, theme
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
    managers = ["emery", "avery", "casey"]
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


def test_bars_are_capped_and_rounded_at_the_data_end():
    svg = charts.contribution_chart(["NFL"], [("Avery", 1)], {("Avery", "NFL"): 100.0})
    assert f'height="{charts.BAR_MAX_THICKNESS}"' in svg
    assert charts.BAR_MAX_THICKNESS <= 24
    assert 'rx="4' in svg


def test_every_bar_carries_a_native_title_for_keyboard_and_screen_readers():
    svg = charts.contribution_chart(["NFL"], [("Avery", 1)], {("Avery", "NFL"): 100.0})
    assert "<title>Avery — NFL: 100</title>" in svg


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
        assert (out / "team" / f"{manager}.html").exists()
    assert result["pages"] == 2 + len(simulate.MANAGERS)


def test_simulated_data_is_labelled_on_every_page(site):
    """Nobody should be able to mistake a placeholder for a real result."""
    out, _ = site
    for page in out.rglob("*.html"):
        assert "Simulated data" in page.read_text(), page.name


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


def test_a_team_page_shows_raw_and_normalized_side_by_side(site):
    """What the league asked for: raw stats and normalized scores per slot."""
    out, _ = site
    html = (out / "team" / "avery.html").read_text()
    assert "Raw" in html and "Normalized" in html and "Slot score" in html


def test_a_team_page_marks_the_bench(site):
    """Best ball selects on its own, so which slots are counting is the whole
    story of a roster."""
    out, _ = site
    assert "class='bench'" in (out / "team" / "avery.html").read_text()


def test_every_team_is_reachable_from_every_page(site):
    out, _ = site
    for page in out.rglob("*.html"):
        html = page.read_text()
        for manager in simulate.MANAGERS:
            assert f"{manager}.html" in html, f"{manager} missing from {page.name}"


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
