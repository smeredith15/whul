#!/usr/bin/env python3
"""Download the images the probe found, into the directories the site reads.

The probe answers "does this URL work". This does the downloading, and the two
are kept apart because they fail differently and only one of them writes to the
repository. Nothing here decides *which* image an asset should have -- that was
settled upstream and arrives as a manifest.

    python scripts/probe-images.py --db data/whul.sqlite3 --json image-urls.json
    python scripts/fetch-images.py image-urls.json

The manifest is ``{directory: {filename stem: url}}``, already keyed by where
each file belongs, so this is a loop over paths rather than a second attempt at
working out what goes where. Two keyings of one fact drifting apart is how a
download succeeds and an image never appears.

Existing files are left alone. Re-running is therefore cheap and safe, and a
file added by hand is never overwritten by a worse one from a feed -- which is
the whole point of being able to add them by hand.

RUN IT WHERE ESPN IS REACHABLE, for the same reason as the probe: a filtered
sandbox refuses every request identically and the result reads as a feed with
no images at all.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

TIMEOUT = 30
PAUSE = 0.15

#: What each content type is called on disk. A URL's own extension is not
#: trusted: ESPN serves plenty of images from paths ending in nothing at all,
#: and a file saved without an extension is invisible to `images.find`.
EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/gif": ".gif",
}

#: Below this, a headshot is ESPN's generic silhouette rather than a person.
#: The probe applies the same floor; repeating it here is deliberate, since a
#: URL can start answering differently between the two runs.
TOO_SMALL = 2000

#: ...and it applies to `asset/` alone. A crest, a flag and a league mark are
#: legitimately small -- a flag is a few coloured rectangles and compresses to
#: under a kilobyte -- so the floor that catches a silhouette would throw away
#: every real flag in the manifest. The silhouette problem is a headshot
#: problem, and the floor belongs where the problem is.
FLOOR_APPLIES_TO = {"asset"}


def destination(out: Path, kind: str, stem: str, content_type: str) -> Path | None:
    """Where a downloaded image belongs, or None if it is not an image."""
    extension = EXTENSIONS.get(content_type.split(";")[0].strip().lower())
    if not extension:
        return None
    return out / kind / f"{stem}{extension}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="the probe's --json output")
    parser.add_argument("--out", default="assets/img",
                        help="where the site reads images from")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many downloads, for a quick check")
    parser.add_argument("--force", action="store_true",
                        help="overwrite files that are already there")
    args = parser.parse_args()

    path = Path(args.manifest)
    if not path.exists():
        print(f"No manifest at {path}. Run scripts/probe-images.py --json first.",
              file=sys.stderr)
        return 1
    try:
        manifest = json.loads(path.read_text())
    except ValueError as exc:
        print(f"{path} is not readable as JSON: {exc}", file=sys.stderr)
        return 1

    from whul.site import images

    out = Path(args.out)
    session = requests.Session()
    saved = skipped = failed = 0
    problems: list[str] = []

    wanted = [
        (kind, stem, url)
        for kind, entries in sorted(manifest.items())
        for stem, url in sorted(entries.items())
        if kind in images.KINDS
    ]
    unknown = sorted(set(manifest) - set(images.KINDS))
    if unknown:
        # A manifest naming a directory the site does not read would download
        # into nowhere. Said out loud rather than skipped quietly.
        problems.append(
            f"manifest names {len(unknown)} directory the site does not read: "
            f"{', '.join(unknown)}"
        )

    print(f"\n{len(wanted)} image(s) in the manifest.\n")
    for kind, stem, url in wanted:
        if args.limit and saved >= args.limit:
            break
        if not args.force and images.find(kind, stem, source=out) is not None:
            skipped += 1
            continue
        try:
            reply = session.get(url, timeout=TIMEOUT)
            reply.raise_for_status()
            body = reply.content
            target = destination(
                out, kind, stem, reply.headers.get("content-type", "")
            )
            if target is None:
                problems.append(
                    f"{kind}/{stem}: not an image "
                    f"({reply.headers.get('content-type', 'no content-type')!r})"
                )
                failed += 1
                continue
            if kind in FLOOR_APPLIES_TO and len(body) < TOO_SMALL:
                problems.append(f"{kind}/{stem}: {len(body)} bytes, a placeholder")
                failed += 1
                continue
            if not body:
                problems.append(f"{kind}/{stem}: empty")
                failed += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            saved += 1
            if saved % 25 == 0:
                print(f"    {saved} saved ...", flush=True)
        except Exception as exc:  # noqa: BLE001 -- one image must not stop the rest
            problems.append(f"{kind}/{stem}: {type(exc).__name__}: {exc}")
            failed += 1
        time.sleep(PAUSE)

    print(f"\n{'=' * 60}")
    print(f"  saved     {saved}")
    print(f"  already   {skipped}")
    print(f"  failed    {failed}")
    if problems:
        print(f"\n  {len(problems)} problem(s):")
        for line in problems[:40]:
            print(f"    {line}")
        if len(problems) > 40:
            print(f"    ... and {len(problems) - 40} more")
    print(f"\n  Files are under {out}/. `python -m whul.cli images-needed`")
    print("  now lists only what is still missing.\n")
    # A run that saved nothing and failed everything is worth a non-zero exit,
    # so a workflow step does not look green while having done nothing.
    return 1 if saved == 0 and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
