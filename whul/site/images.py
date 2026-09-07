"""Profile photos, team badges and their fallback.

Images are files the league supplies, not something the app can fetch: player
photographs and club badges are owned by someone, and quietly hotlinking them
would be both fragile and rude. So the site reads from a directory the league
fills in at its own pace, and renders a monogram wherever a file is missing.

    assets/img/manager/<manager-id>.<ext>   a manager's team photo
    assets/img/asset/<asset-id>.<ext>       a player's headshot or a team logo
    assets/img/badge/<league-slug>.<ext>    a competition's mark
    assets/img/club/<club-slug>.<ext>       a club's crest, for a player's corner
    assets/img/flag/<country-slug>.<ext>    a country's flag, for an athlete's
    assets/img/shield/<name-slug>.<ext>     a confederation's shield, Olympic rings

Any of jpg, jpeg, png, webp or svg. Names are matched exactly on the id the
store uses, so adding a photo is dropping in a file -- no manifest to update
and nothing to rebuild but the site.

**The fallback is not a placeholder to be replaced later.** A monogram in the
manager's own colour is a real answer: it identifies the asset, it is the right
shape, and a page with three photos and forty-five monograms should still look
deliberate rather than half-finished.
"""

from __future__ import annotations

import shutil
import unicodedata
from html import escape
from pathlib import Path

#: Where the league drops images. Versioned with the repo so the site rebuilds
#: identically anywhere.
SOURCE_DIR = Path("assets/img")
#: Where they are published to, relative to the site root.
OUT_DIR = "img"
EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg")

#: Every directory the site reads. `asset` holds the main picture -- a
#: headshot or a team logo -- and the other three hold whatever goes in its
#: bottom-right corner, kept apart rather than pooled: "England" is a country
#: and an international side and a `flag/england.png` that turned out to be a
#: crest would be found by whichever asked first.
KINDS = ("manager", "asset", "badge", "club", "flag", "shield")


def plain(key: str) -> str:
    """A key with its accents folded away, for people naming files by hand.

    Asset ids keep the spelling the league drafted -- `kylian-mbappé`, and the
    id is what the rest of the engine speaks, so it stays. But asking someone to
    type that as a filename eighty times is asking for a file that looks right
    and is never found, and the ways to get it wrong (a precomposed é, a
    combining accent, whatever the file manager does on the way through) all
    fail identically and silently.
    """
    folded = unicodedata.normalize("NFKD", str(key))
    return "".join(c for c in folded if not unicodedata.combining(c))


def find(kind: str, key: str, source: Path | None = None) -> Path | None:
    """The image file for an id, or None when the league has not supplied one.

    The accent-folded spelling is accepted as well as the exact one, so a file
    named `kylian-mbappe.png` is found for `kylian-mbappé`. The exact spelling
    wins where both exist, since that one was deliberate.
    """
    base = (source or SOURCE_DIR) / kind
    for name in dict.fromkeys((key, plain(key))):
        for extension in EXTENSIONS:
            candidate = base / f"{name}{extension}"
            if candidate.exists():
                return candidate
    return None


def copy_all(out: Path, source: Path | None = None) -> dict[str, int]:
    """Publish whatever images exist into the site.

    Returns a count per kind, so a build can report how much of the league has
    photographs yet rather than leaving it to be discovered by looking.
    """
    source = source or SOURCE_DIR
    counts = {kind: 0 for kind in KINDS}
    for kind in KINDS:
        src = source / kind
        if not src.is_dir():
            continue
        dest = out / OUT_DIR / kind
        dest.mkdir(parents=True, exist_ok=True)
        for file in src.iterdir():
            if file.suffix.lower() in EXTENSIONS:
                shutil.copy2(file, dest / file.name)
                counts[kind] += 1
    return counts


def _initials(name: str) -> str:
    parts = [p for p in str(name).replace(".", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


#: The corner badge, as a fraction of the picture it sits on. Small enough to
#: read as a mark on a photograph rather than a second photograph, large enough
#: to tell one crest from another at 26px.
BADGE_SCALE = 0.46

#: Below this the corner badge is dropped entirely. A crest rendered at nine
#: pixels is a smudge that costs a request and says nothing, and the row it
#: would sit in has a club written next to the name anyway.
BADGE_FLOOR = 24


def avatar(
    kind: str,
    key: str,
    name: str,
    size: int = 40,
    slot: int | None = None,
    depth: int = 0,
    source: Path | None = None,
    initials: str | None = None,
    badge: tuple[str, str] | None = None,
    logo: bool = False,
) -> str:
    """An image if one exists, a monogram if not, with an optional corner mark.

    ``slot`` tints the monogram with a manager's series colour, so identity
    still carries where a photograph does not. ``initials`` overrides what the
    monogram says -- a manager's id already *is* their initials, so deriving
    them from the display name again would turn TG into T rather than leaving
    it as the badge the league uses.

    ``badge`` is ``(directory, filename stem)`` for the mark in the bottom
    right: a club's crest on a player, a league's on a team, a flag on an
    individual athlete. It is drawn over whatever the main picture turned out
    to be, monogram included -- a player with no photograph still plays for
    somebody, and the crest is the more useful half of that pair anyway.

    ``logo`` fits the picture inside the circle instead of filling it. A
    photograph cropped to a circle loses a corner of the background and is
    better for it; a crest cropped to a circle loses the top of the shield and
    the bottom of the scroll, which is most of what makes it that club's. It
    was Arsenal's cannon coming out clipped at both ends that made this a
    parameter rather than one rule for everything.
    """
    up = "../" * depth
    file = find(kind, key, source)
    style = f"width:{size}px;height:{size}px"
    if file is not None:
        main = (
            f'<img class="avatar{" fitted" if logo else ""}" '
            f'src="{up}{OUT_DIR}/{kind}/{escape(file.name)}" '
            f'alt="" loading="lazy" style="{style}">'
        )
    else:
        tint = (
            f"background: color-mix(in srgb, var(--series-{slot}) 22%, transparent); "
            f"color: var(--series-{slot})"
            if slot
            else "background: var(--grid); color: var(--text-secondary)"
        )
        main = (
            f'<span class="avatar mono" aria-hidden="true" '
            f'style="{style};font-size:{max(10, int(size * 0.36))}px;{tint}">'
            f"{escape(initials or _initials(name))}</span>"
        )

    corner = find(badge[0], badge[1], source) if badge else None
    if corner is None or size < BADGE_FLOOR:
        return main
    pip = max(10, round(size * BADGE_SCALE))
    return (
        f'<span class="badged" style="{style}">{main}'
        f'<img class="pip" src="{up}{OUT_DIR}/{badge[0]}/{escape(corner.name)}" '
        f'alt="" loading="lazy" style="width:{pip}px;height:{pip}px"></span>'
    )
