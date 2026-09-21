"""The app icon, drawn here rather than shipped as a file.

A home-screen icon has to be a PNG -- iOS will not take an SVG for one -- and
this project has no image library and deliberately fetches no images it does
not own. So the few hundred bytes of PNG are written by hand: a signature,
three chunks and zlib, all of which are in the standard library.

The mark is five bars in the managers' own series colours, rising. It is the
standings at the size a phone shows an icon, it uses the palette the site
already has rather than inventing a logo, and it is made of rectangles, which
is the one thing that can be rasterised honestly without a font.
"""

from __future__ import annotations

import struct
import zlib

#: The bars, in the order they climb. The series palette's first five, which is
#: also one per manager in a five-manager league.
BARS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")

#: The ground. The site's dark page colour, so the icon reads on a light home
#: screen and disappears into a dark one the way an app's should.
GROUND = "#0d0d0d"

#: How tall each bar stands, as a share of the drawable area. Rising, because a
#: flat row of blocks is a keypad and a rising one is a league table.
HEIGHTS = (0.34, 0.50, 1.00, 0.72, 0.58)

#: The margin around the bars, as a share of the icon. Generous, because a
#: maskable icon is cropped to a circle on some launchers and anything near the
#: corner is the part that goes.
PAD = 0.22


def _rgb(colour: str) -> tuple[int, int, int]:
    text = colour.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def png(width: int, height: int, pixels: bytearray) -> bytes:
    """An 8-bit RGB PNG from a row-major buffer of ``width * height * 3``.

    No filtering -- every scanline is written with filter type 0. A flat
    drawing of rectangles compresses to almost nothing anyway, and a filter
    would be a second thing to get right for no gain.
    """
    raw = bytearray()
    stride = width * 3
    for row in range(height):
        raw.append(0)
        raw += pixels[row * stride:(row + 1) * stride]
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _chunk(b"IEND", b""))


def mark(size: int) -> bytes:
    """The league's icon at one size, as PNG bytes."""
    ground = _rgb(GROUND)
    pixels = bytearray(bytes(ground) * (size * size))

    inset = int(size * PAD)
    field = size - 2 * inset
    if field <= 0:
        return png(size, size, pixels)
    # Four gaps between five bars, each a third of a bar wide.
    bar = field / (len(BARS) + (len(BARS) - 1) / 3)
    gap = bar / 3

    for index, (colour, share) in enumerate(zip(BARS, HEIGHTS)):
        red, green, blue = _rgb(colour)
        left = int(inset + index * (bar + gap))
        right = min(size, int(left + bar))
        tall = int(field * share)
        top = inset + field - tall
        for row in range(max(0, top), min(size, inset + field)):
            start = (row * size + left) * 3
            for column in range(right - left):
                at = start + column * 3
                pixels[at] = red
                pixels[at + 1] = green
                pixels[at + 2] = blue
    return png(size, size, pixels)


#: The sizes a manifest and an iOS home screen ask for. 180 is Apple's
#: apple-touch-icon; 192 and 512 are what Chrome wants before it will offer to
#: install anything.
SIZES = (180, 192, 512)


def files() -> dict[str, bytes]:
    """``{filename: png bytes}`` for every size the site ships."""
    return {f"icon-{size}.png": mark(size) for size in SIZES}
