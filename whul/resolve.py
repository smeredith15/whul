"""Matching a feed's name to the asset a manager drafted.

Every other part of the engine works on ids. The feeds do not have ours: a
scored row says "Ja'Marr Chase" or "Nottingham Forest", and something has to
decide which roster slot that is. This is that something.

Two things make the job smaller than general entity resolution. Only rostered
assets matter -- around two hundred of them, not the tens of thousands a feed
carries -- and every one of them is known in advance, so an asset that fails to
match can be *named*. A silent failure here is the expensive kind: the manager
simply scores nothing, and nothing in the standings says why.

So the rules are deliberately strict:

* An exact match on the normalized name links, and the link is stored, so a
  later manual correction sticks and the next run is a lookup.
* Two feed rows that normalize the same way link neither. A coin flip between
  two players with one name is the failure that looks like success.
* Everything unmatched is reported, by name, every run.

Nothing here guesses. Fuzzy matching belongs behind a person's review, which is
what ``needs_review`` on the alias table is for.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

from whul.config.league import competitions_for
from whul.store.db import Store, _now

#: Suffixes that a feed may carry and a roster sheet may not, or the reverse.
SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")

#: Club words that differ between feeds for the same team -- ESPN's "Nottingham
#: Forest" against a sheet's "Nott'm Forest", "Inter Milan" against
#: "Internazionale". Only the noise words are dropped; the distinguishing part
#: of the name is never touched, so "Manchester United" and "Manchester City"
#: stay different.
CLUB_NOISE = ("fc", "cf", "afc", "sc", "ac", "as", "ss", "ssc", "us", "sv",
              "rc", "cd", "ud", "club", "de", "futbol", "football")


def normalize_name(name: str) -> str:
    """A name reduced to what two feeds are likely to agree on.

    Accents, punctuation and case go; word order and the words themselves stay.
    Anything more aggressive starts merging people who are actually different.
    """
    return split_name(name)[0]


def split_name(name: str) -> tuple[str, str]:
    """A name as ``(base, generational suffix)``.

    The suffix is kept apart rather than thrown away, because it is the only
    thing distinguishing two people who are otherwise identical. John Daly II
    is on a roster here and John Daly plays the same tour; folding them
    together would credit one manager with the other man's score, and nothing
    downstream could tell.
    """
    if not name:
        return "", ""
    folded = unicodedata.normalize("NFKD", str(name))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.lower().replace("&", " and ")
    # An apostrophe never separates two words in a name, and feeds disagree
    # about keeping it: spacing it turns "Ja'Marr" into two tokens that
    # "JaMarr" can never match.
    folded = folded.replace("'", "").replace("\u2019", "")
    folded = re.sub(r"[^a-z0-9\s]", " ", folded)
    words = _join_initials(folded.split())

    suffix = ""
    while len(words) > 1 and words[-1] in SUFFIXES:
        suffix = words[-1]
        words = words[:-1]
    return " ".join(words), suffix


def _join_initials(words: list[str]) -> list[str]:
    """Run consecutive single letters together, so "A.J." reads as "AJ".

    A period does separate words -- "St. Louis" is two -- so it cannot simply be
    deleted like an apostrophe. Rejoining the initials afterwards gets both
    cases: "a j brown" becomes "aj brown", and "st louis" is left alone.
    """
    out: list[str] = []
    for word in words:
        if len(word) == 1 and out and len(out[-1]) <= 2 and out[-1].isalpha() \
                and len(out[-1]) == 1:
            out[-1] += word
        else:
            out.append(word)
    return out


def normalize_team(name: str) -> str:
    """A club name reduced further, since club naming is the noisier case."""
    words = [w for w in normalize_name(name).split() if w not in CLUB_NOISE]
    return " ".join(words) or normalize_name(name)


@dataclass
class Match:
    """One rostered asset and the feed row it was matched to."""

    asset_id: str
    display_name: str
    league: str
    feed_name: str
    how: str  # "alias" | "name"


@dataclass
class Resolution:
    """What one league's matching found, in a shape a report can print."""

    league: str
    asset_type: str
    matched: list[Match] = field(default_factory=list)
    unmatched: list[tuple[str, str]] = field(default_factory=list)
    ambiguous: list[tuple[str, str, int]] = field(default_factory=list)
    #: Roster name, league, and the feed name it nearly matched. Held back
    #: rather than linked, because the suffix is the whole difference.
    suffix_mismatch: list[tuple[str, str, str]] = field(default_factory=list)
    #: Roster name and the longer feed name it was linked to. Reported because
    #: it was inferred rather than read.
    extra_names: list[tuple[str, str]] = field(default_factory=list)

    @property
    def rostered(self) -> int:
        return (
            len(self.matched) + len(self.unmatched) + len(self.ambiguous)
            + len(self.suffix_mismatch)
        )

    def __str__(self) -> str:
        lines = [
            f"{self.league} {self.asset_type.lower()}s: "
            f"{len(self.matched)} of {self.rostered} rostered assets matched"
        ]
        for name, league in self.unmatched:
            lines.append(f"  no feed row for {name} ({league}) -- it will score nothing")
        for name, league, count in self.ambiguous:
            lines.append(
                f"  {name} ({league}) matches {count} feed rows -- left unlinked "
                f"rather than guessed"
            )
        for name, feed_name in self.extra_names:
            lines.append(
                f"  {name} matched {feed_name} on the names they share -- the "
                f"feed carries one this roster does not"
            )
        for name, league, feed_name in self.suffix_mismatch:
            lines.append(
                f"  {name} ({league}) is not the {feed_name} the feed lists -- "
                f"one carries a generational suffix and the other does not. Link "
                f"them with `whul.cli alias` if they are the same person"
            )
        return "\n".join(lines)


def rostered_assets(store: Store, season: str, asset_type: str | None = None) -> pd.DataFrame:
    """Every asset currently in a slot, with the league it was drafted into."""
    sql = (
        "SELECT DISTINCT a.asset_id, a.display_name, a.asset_type, a.league "
        "FROM slot_occupancy o "
        "JOIN roster_slots s ON s.slot_id = o.slot_id "
        "JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE s.season = ? AND o.end_date IS NULL"
    )
    params: list = [season]
    if asset_type:
        sql += " AND a.asset_type = ?"
        params.append(asset_type)
    return store.query(sql + " ORDER BY a.league, a.display_name", params)


def load_aliases(store: Store, source: str) -> dict[str, str]:
    """``{feed name: asset_id}`` for one source, including manual corrections."""
    rows = store.query(
        "SELECT source_key, asset_id FROM asset_aliases "
        "WHERE source = ? AND needs_review = 0",
        (source,),
    )
    return {r.source_key: r.asset_id for r in rows.itertuples()}


def save_aliases(store: Store, source: str, matches: list[Match]) -> int:
    """Record what matched, so the next run is a lookup and a fix stays fixed."""
    if not matches:
        return 0
    return store.upsert(
        "asset_aliases",
        [{
            "source": source, "source_key": m.feed_name, "asset_id": m.asset_id,
            "match_kind": "name", "needs_review": 0, "created_at": _now(),
        } for m in matches if m.how == "name"],
        keys=("source", "source_key"),
    )


def resolve(
    scored: pd.DataFrame,
    assets: pd.DataFrame,
    asset_type: str,
    aliases: dict[str, str] | None = None,
    league: str = "",
) -> tuple[pd.DataFrame, Resolution]:
    """Attach ``asset_id`` to the scored rows a roster actually holds.

    Returns only the matched rows: a feed carries every professional in the
    league, and the standings care about two hundred of them.
    """
    report = Resolution(league=league or "", asset_type=asset_type)
    name_col, also = _name_columns(scored, asset_type)
    if scored is None or scored.empty or assets.empty:
        report.unmatched = [
            (r.display_name, r.league) for r in assets.itertuples()
        ] if not assets.empty else []
        return pd.DataFrame(), report

    teams = asset_type == "Team"
    shape = normalize_team if teams else normalize_name

    def key_of(name) -> tuple[str, str]:
        # A club has no generational suffix to disagree about.
        return (normalize_team(name), "") if teams else split_name(name)

    feed = scored.copy()
    feed["_league"] = feed["league"].astype(str) if "league" in feed else ""

    aliases = aliases or {}
    by_key: dict[tuple[str, str], set[int]] = {}
    by_alias: dict[str, list[int]] = {}
    # Every name the feed offers is indexed, not just the primary one. A feed
    # that calls a team "SEA" in one column and "Seattle Seahawks" in another
    # is the common case, and a roster may use either.
    suffixes: dict[int, str] = {}
    # Feed names indexed by their leading words as well as in full, so a name
    # carrying a surname the roster does not can still be found. Spanish and
    # Portuguese players routinely appear under both surnames in one feed and
    # one in another -- Flashscore lists "Carlos Alcaraz Garfia".
    by_prefix: dict[tuple[str, str], set[int]] = {}
    for column in (name_col, *also):
        for position, name in enumerate(feed[column]):
            base, suffix = key_of(name)
            if base:
                league_of = feed["_league"].iat[position]
                by_key.setdefault((league_of, base), set()).add(position)
                suffixes.setdefault(position, suffix)
                words = base.split()
                for take in range(2, len(words)):
                    by_prefix.setdefault(
                        (league_of, " ".join(words[:take])), set()
                    ).add(position)
            if str(name) in aliases:
                by_alias.setdefault(aliases[str(name)], []).append(position)

    picked: dict[int, str] = {}
    for asset in assets.itertuples():
        wanted = competitions_for(asset.league)
        # An alias is a decision already made -- by an earlier run or by hand --
        # so it wins over re-deriving the match.
        if asset.asset_id in by_alias:
            position = by_alias[asset.asset_id][0]
            picked[position] = asset.asset_id
            report.matched.append(Match(
                asset.asset_id, asset.display_name, asset.league,
                feed.iloc[position][name_col], "alias",
            ))
            continue

        key, suffix = key_of(asset.display_name)
        hits = sorted({i for c in wanted for i in by_key.get((c, key), set())})
        if not hits and not feed["_league"].isin(wanted).any():
            # The frame is single-league and carries no league column to match
            # on; fall back to the name alone rather than reporting every asset
            # as missing.
            hits = sorted(by_key.get(("", key), set()))

        # The suffix is part of the identity, not noise to be normalized away.
        # A near-match on the base alone is reported rather than linked: it is
        # as likely to be someone's father as a feed dropping a "Jr.", and an
        # alias settles it in one command.
        if not hits and len(key.split()) >= 2:
            # Only when nothing matched outright, and only when exactly one
            # feed name extends this one: a unique longer name is an extra
            # surname, while two of them is a guess between two people.
            extended = sorted({i for c in wanted for i in by_prefix.get((c, key), set())})
            if not extended and not feed["_league"].isin(wanted).any():
                extended = sorted(by_prefix.get(("", key), set()))
            if len(extended) == 1:
                hits = extended
                report.extra_names.append(
                    (asset.display_name, str(feed.iloc[extended[0]][name_col]))
                )

        agreed = [i for i in hits if suffixes.get(i, "") == suffix]
        if hits and not agreed:
            report.suffix_mismatch.append(
                (asset.display_name, asset.league, str(feed.iloc[hits[0]][name_col]))
            )
            continue
        hits = agreed
        if len(hits) == 1:
            picked[hits[0]] = asset.asset_id
            report.matched.append(Match(
                asset.asset_id, asset.display_name, asset.league,
                feed.iloc[hits[0]][name_col], "name",
            ))
        elif len(hits) > 1:
            report.ambiguous.append((asset.display_name, asset.league, len(hits)))
        else:
            report.unmatched.append((asset.display_name, asset.league))

    if not picked:
        return pd.DataFrame(), report
    out = feed.iloc[sorted(picked)].copy()
    out["asset_id"] = [picked[i] for i in sorted(picked)]
    return out.drop(columns=["_league"]).reset_index(drop=True), report


#: Columns a feed may put a name in, best first. A scorer names its subject
#: ``player`` or ``team``; the display name, where a feed carries one, is what a
#: roster is more likely to have been written from.
NAME_COLUMNS = {
    "Team": ("team_name", "team", "display_name", "club"),
    "Player": ("player", "display_name", "athlete", "driver"),
}


def _name_columns(scored: pd.DataFrame, asset_type: str) -> tuple[str, tuple[str, ...]]:
    """The primary name column and every other one worth matching against."""
    present = [
        c for c in NAME_COLUMNS.get(asset_type, NAME_COLUMNS["Player"])
        if scored is not None and c in scored.columns
    ]
    default = "team" if asset_type == "Team" else "player"
    if not present:
        return default, ()
    # The scorer's own column stays primary, so the recorded feed_name is the
    # one the rest of the engine already speaks.
    primary = default if default in present else present[0]
    return primary, tuple(c for c in present if c != primary)
