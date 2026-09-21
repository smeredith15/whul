"""The app icon: the league's crest, at the sizes a phone asks for.

The crest is the league's own -- a gold octopus holding a ball from five of the
sports it scores, on the blue plate -- and it is a drawing, not something this
module can invent. So the icon is a file rather than a procedure, and this
module is only the part that finds it and hands it to the build.

There is one file per size instead of one master resized at build time. The
resize wants an image library, this project has none and does not want one for
three files that change about as often as the league's name does. The PNGs in
``icon/`` are therefore the source of truth: they were cut from the crest once,
they are versioned with the repo, and the build copies bytes.

The sizes are what the platforms ask for and nothing else. 180 is Apple's
``apple-touch-icon``; 192 and 512 are what Chrome wants before it will offer to
install anything. Each is square and full-bleed -- the plate reaches every
corner -- because iOS and Android both apply a mask of their own, and an icon
that brought its own rounded corners would get them rounded twice.
"""

from __future__ import annotations

from pathlib import Path

#: Where the cut files live. Beside this module and inside the package, so they
#: are found whether the repo is checked out or the package is installed.
SOURCE_DIR = Path(__file__).with_name("icon")

#: The sizes the site ships, named rather than discovered. A directory listing
#: would quietly ship four icons when someone adds a file and three when a file
#: goes missing, and a missing icon is exactly the kind of thing nobody notices
#: from a home screen that still has yesterday's.
SIZES = (180, 192, 512)

#: The one an Android launcher is allowed to crop to whatever shape it likes.
#: It is the same crest with the plate at 80% of the square, because the safe
#: zone a maskable icon promises is only the middle 80% and the crest's outer
#: ring sits at 91% -- shipped at full bleed it would have its ring shaved off
#: by a circular mask, which is the one part of the drawing that has to close.
MASKABLE = "icon-maskable-512.png"


def _read(name: str) -> bytes:
    path = SOURCE_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. The icons are files in the repo, not something "
            f"the build draws -- cut one from the crest and commit it."
        )
    return path.read_bytes()


def mark(size: int) -> bytes:
    """The crest at one size, as PNG bytes."""
    return _read(f"icon-{size}.png")


def files() -> dict[str, bytes]:
    """``{filename: png bytes}`` for every icon the site ships."""
    made = {f"icon-{size}.png": mark(size) for size in SIZES}
    made[MASKABLE] = _read(MASKABLE)
    return made
