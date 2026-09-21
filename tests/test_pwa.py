"""What makes the site installable on a phone.

A manifest, a service worker and a set of icons -- enough that a manager can
put the league on a home screen and stop keeping a tab open for it. Nothing
here is a native app: it is the same static files with three more of them.
"""

import json
import struct

from whul.site import icons, pwa


# --- the manifest -----------------------------------------------------------

def test_every_url_in_the_manifest_is_relative():
    """The site is served from a project path on GitHub Pages today and could
    be served from a domain root tomorrow. A manifest that hard-coded either
    would install an app pointing at nothing."""
    found = json.loads(pwa.manifest())

    assert found["start_url"] == "."
    assert found["scope"] == "."
    for icon in found["icons"]:
        assert not icon["src"].startswith(("/", "http"))


def test_the_manifest_asks_for_a_window_of_its_own():
    """The whole request: no browser tab."""
    found = json.loads(pwa.manifest())
    assert found["display"] == "standalone"
    assert found["short_name"] == "WHUL"


def test_the_manifest_offers_the_sizes_a_phone_asks_for():
    found = json.loads(pwa.manifest())
    sizes = {icon["sizes"] for icon in found["icons"]}

    assert {"192x192", "512x512"} <= sizes
    # One of them croppable, because some launchers cut an icon to a circle.
    assert any(icon.get("purpose") == "maskable" for icon in found["icons"])


# --- the service worker -----------------------------------------------------

def test_the_worker_goes_to_the_network_first():
    """The site's only claim is that it is current. A cache-first worker would
    serve Tuesday's standings on Thursday, which breaks the one thing it
    does."""
    code = pwa.service_worker("2026-27")

    network = code.index("fetch(request)")
    fallback = code.index("caches.match(request)")
    assert network < fallback, "the cache is consulted before the network"
    assert ".catch(" in code, "the cache is a fallback, not a first choice"


def test_the_cache_is_named_for_the_build_that_filled_it():
    """Without it a worker installed in October would still be answering from
    an October cache in March."""
    one = pwa.service_worker("2026-27 · through 2026-09-18")
    two = pwa.service_worker("2026-27 · through 2026-09-19")

    assert one != two
    assert "2026-09-18" in one and "2026-09-18" not in two


def test_an_old_cache_is_deleted_when_a_new_worker_activates():
    code = pwa.service_worker("x")
    assert "caches.delete" in code
    # And the new worker takes over rather than waiting for every window to
    # close -- a standalone app window may never be closed.
    assert "skipWaiting" in code and "clients.claim" in code


def test_the_worker_only_answers_for_this_site():
    code = pwa.service_worker("x")
    assert "self.location.origin" in code
    assert "request.method !== 'GET'" in code


def test_a_navigation_with_nothing_cached_still_gets_a_page():
    code = pwa.service_worker("x")
    assert "request.mode === 'navigate'" in code
    assert "caches.match('index.html')" in code


# --- the head ---------------------------------------------------------------

def test_the_head_points_at_everything_from_the_pages_own_depth():
    """A team page lives one directory down, and a manifest linked from there
    without the climb would be a 404."""
    deep = pwa.head("../")

    assert 'href="../manifest.webmanifest"' in deep
    assert 'href="../icon-180.png"' in deep
    # Apple needs its own tags; without them iOS opens the site in Safari
    # chrome rather than in a window of its own.
    assert "apple-mobile-web-app-capable" in deep
    assert "apple-touch-icon" in deep


def test_the_status_bar_matches_the_page_under_it():
    top = pwa.head()
    assert f'content="{pwa.THEME_LIGHT}" media="(prefers-color-scheme: light)"' in top
    assert f'content="{pwa.THEME_DARK}" media="(prefers-color-scheme: dark)"' in top


# --- the icon ---------------------------------------------------------------

def test_the_icon_is_a_png_of_the_size_it_says():
    """Written by hand: iOS will not take an SVG for a home-screen icon and
    this project has no image library."""
    for size in icons.SIZES:
        blob = icons.mark(size)
        assert blob[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", blob[16:24])
        assert (width, height) == (size, size)


def test_the_icon_is_drawn_in_the_managers_own_colours():
    """Five bars in the series palette rather than a logo nobody chose."""
    size = 192
    blob = icons.mark(size)
    assert len(blob) > 100
    # The ground is the site's dark page colour, so the corners are it.
    assert icons._rgb(icons.GROUND) == (13, 13, 13)
    assert len(icons.BARS) == len(icons.HEIGHTS) == 5


def test_every_size_the_manifest_names_is_actually_written():
    made = icons.files()
    listed = {icon["src"] for icon in json.loads(pwa.manifest())["icons"]}

    assert listed <= set(made), f"manifest names files nothing writes: {listed - set(made)}"
    assert "icon-180.png" in made, "iOS asks for 180 and the manifest does not"
