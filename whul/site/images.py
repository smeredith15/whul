"""Profile photos, team badges and their fallback.

Images are files the league supplies, not something the app can fetch: player
photographs and club badges are owned by someone, and quietly hotlinking them
would be both fragile and rude. So the site reads from a directory the league
fills in at its own pace, and renders a monogram wherever a file is missing.

    assets/img/manager/<manager-id>.<ext>   a manager's team photo
    assets/img/asset/<asset-id>.<ext>       a player or team photo
    assets/img/badge/<league>.<ext>         a competition or club badge

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
from html import escape
from pathlib import Path

#: Where the league drops images. Versioned with the repo so the site rebuilds
#: identically anywhere.
SOURCE_DIR = Path("assets/img")
#: Where they are published to, relative to the site root.
OUT_DIR = "img"
EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg")

KINDS = ("manager", "asset", "badge")


def find(kind: str, key: str, source: Path | None = None) -> Path | None:
    """The image file for an id, or None when the league has not supplied one."""
    base = (source or SOURCE_DIR) / kind
    for extension in EXTENSIONS:
        candidate = base / f"{key}{extension}"
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


def avatar(
    kind: str,
    key: str,
    name: str,
    size: int = 40,
    slot: int | None = None,
    depth: int = 0,
    source: Path | None = None,
    initials: str | None = None,
) -> str:
    """An image if one exists, a monogram if not.

    ``slot`` tints the monogram with a manager's series colour, so identity
    still carries where a photograph does not. ``initials`` overrides what the
    monogram says -- a manager's id already *is* their initials, so deriving
    them from the display name again would turn TG into T rather than leaving
    it as the badge the league uses.
    """
    up = "../" * depth
    file = find(kind, key, source)
    style = f"width:{size}px;height:{size}px"
    if file is not None:
        return (
            f'<img class="avatar" src="{up}{OUT_DIR}/{kind}/{escape(file.name)}" '
            f'alt="" loading="lazy" style="{style}">'
        )
    tint = (
        f"background: color-mix(in srgb, var(--series-{slot}) 22%, transparent); "
        f"color: var(--series-{slot})"
        if slot
        else "background: var(--grid); color: var(--text-secondary)"
    )
    return (
        f'<span class="avatar mono" aria-hidden="true" '
        f'style="{style};font-size:{max(10, int(size * 0.36))}px;{tint}">'
        f"{escape(initials or _initials(name))}</span>"
    )
