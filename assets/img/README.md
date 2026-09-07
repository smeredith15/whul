# Images

Drop files here and the next site build picks them up. Nothing else to update —
no manifest, no config. A missing image renders as a monogram in the manager's
colour, which is a finished-looking answer rather than a placeholder, so these
can be filled in at whatever pace suits and in any order.

## Don't work from this file. Work from the list.

```
python -m whul.cli images-needed
```

**Run it from the repository root.** `assets/img` is a relative path, so from
anywhere else it resolves to nothing and every file reads as missing -- which
looks exactly like having fetched none of them. The command prints the
directory it looked in for that reason; `--images <path>` points it elsewhere.
And pull first: a clone that has not seen the last merge has none of the files
either.

That prints every image the site wants **that is not already here**, grouped by
directory, with the exact filename and what belongs in it:

```
  --- assets/img/asset/
    player-la-liga-kylian-mbappe.png    Kylian Mbappé -- headshot
    team-bundesliga-freiburg.png        Freiburg -- team logo
  --- assets/img/club/
    arsenal.png                         Arsenal -- club crest
```

The naming rule is short enough to state in a sentence and long enough to get
wrong eighty times running. The list states it once per file, so there is
nothing to remember and nothing to derive.

## What goes where

```
manager/<manager-id>.png     a manager's photo or crest
asset/<asset-id>.png         THE MAIN PICTURE: a player's headshot, or a team's logo
badge/<league-slug>.png      a competition's mark      -> corner of a team
club/<club-slug>.png         a club's crest            -> corner of a team-sport player
flag/<country-slug>.png      a country's flag          -> corner of an individual athlete
shield/<name-slug>.png       a confederation's shield  -> an international side
                             (and `olympics.png` for the rings)
```

Every asset gets a main picture from `asset/`, and one corner badge from
whichever of the other three fits what it is. Which one that is follows from the
roster category, so there is nothing to choose:

| the asset | main picture | bottom-right corner |
|---|---|---|
| a footballer, an NFL back, a shortstop | headshot | their club's crest |
| a golfer, a tennis player, a driver | headshot | their country's flag |
| a club or a college programme | team logo | its league's logo |
| an international side | the national crest | its confederation's shield |
| an Olympic entry | the country's flag | the Olympic rings |

`club/`, `flag/` and `shield/` are kept apart rather than pooled into one
directory because "England" is a country *and* an international side, and a
single `england.png` would be handed to whichever asked first.

## Naming

`.png`, `.jpg`, `.jpeg`, `.webp` and `.svg` all work. Lower case, hyphens for
spaces, no other punctuation.

**Accents can be dropped.** The asset id keeps the spelling the league drafted —
`player-la-liga-kylian-mbappé` — because that is what the rest of the engine
speaks, but `player-la-liga-kylian-mbappe.png` is found just the same. Type the
plain one; `images-needed` prints the plain one for exactly this reason. A file
that looks right and is never found is the failure worth designing out.

## Getting them onto the site

Three steps, and the middle one is the only one with a wrong answer.

1. **Put the files in.** `assets/img/<kind>/<name>.png`, per the list.
2. **Commit and push them to `main`.** Through the GitHub web UI: open
   `assets/img/<kind>/`, *Add file → Upload files*, drag them in, *Commit
   changes*. They must land on `main` — the nightly job checks out the default
   branch, so a file on any other branch is not there as far as the site is
   concerned.
3. **Run the workflow.** *Actions → Publish standings → Run workflow*, from
   `main`. The build copies whatever is here into the site and prints a count
   per directory, so a filename typo shows up as a count that did not go up.

There is no separate "publish images" step and no cache to bust. The images are
part of the site the same way the HTML is.

## After correcting a name in the spreadsheet

The feed's spelling of a club wins over the sheet's, so most of these correct
themselves: the feed notices a transfer, and its name is ESPN's own, which
means a crest looked up under it matches by construction.

The sheet is the answer only where the feed has none -- every athlete's
country, and every league that has not kicked off. That is where a difference
still bites: the sheet said "Los Angeles Clippers", ESPN files them as "LA
Clippers", and a perfectly correct name found nothing. Fix the spelling in the
sheet, then:

1. Upload the sheet to `main`.
2. *Actions -> Probe images -> Run workflow*, from `main`, with **Fetch**
   ticked. It re-imports the sheet before it probes, so the correction is live
   for that run; files already present are left alone, so only what is still
   missing is fetched.
3. Open the compare link it prints, and merge.
4. *Actions -> Publish standings*, from `main`, to put the new files on the site.

Step 2 is where the refresh happens. There is no separate re-import to
remember: the probe reads the sheet every time, for the same reason the nightly
job does.

## A note on what to spend an evening on

Most of these are fetchable from ESPN and will be filled in automatically —
`scripts/probe-images.py` establishes which. What is genuinely by hand is the
short list: the confederation shields, the Olympic rings, and the players ESPN
has no photograph of. `images-needed` will shrink as the automatic ones land, so
running it again before starting is worth the ten seconds.

Only put images here that the league has the right to publish — the site is
public. Images are served from the site itself rather than hotlinked, so nothing
breaks when a source moves and nobody else's bandwidth is being spent.
