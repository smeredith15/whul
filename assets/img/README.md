# Images

Drop files here and the next site build picks them up. Nothing else to update —
no manifest, no config. A missing image renders as a monogram in the manager's
colour, which is a finished-looking answer rather than a placeholder, so the
league can fill these in at whatever pace it likes.

```
manager/<manager-id>.png     a manager's team photo or crest
asset/<asset-id>.png         a player or team photograph
badge/<league-slug>.png      a competition or club badge
```

`.png`, `.jpg`, `.jpeg`, `.webp` and `.svg` all work. The name must match the id
the store uses exactly — `python -m whul.cli site` prints how many of each kind
it found, so a typo shows up as a count that did not go up.

Manager ids are what the roster import sets (`avery`, `blake`, …). Asset ids
come from the asset table. League slugs are the category lowercased with
non-alphanumerics turned into hyphens: `nfl`, `club-soccer-top-3`.

Images are served from the site itself rather than hotlinked, so nothing breaks
when a source moves and nobody else's bandwidth is being spent. Only put images
here that the league has the right to publish — the site is public.
