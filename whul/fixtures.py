"""What each rostered team plays next.

Nothing here fetches anything. Every schedule the pipeline downloads for
scoring already carries the games that have *not* been played -- nflverse ships
the whole NFL season in September, and a team's own ESPN schedule is its whole
season whatever the date -- and the scorers drop those rows because a game with
no score cannot be scored. This reads the same frames on the way past and keeps
the half that was being thrown away, so the column costs no requests at all.

That is also the limit of it. A league whose live feed walks a scoreboard one
past date at a time has no fixtures in hand and gets none here; a league that
reports season totals rather than games (the NHL) has no schedule at all.
``coverage`` says which is which rather than leaving a reader to infer it from
a column of blanks.

Fixtures are keyed by team, not by asset. A club and the players who play for
it share one row, because a player's next fixture is their club's -- joined
through the club the spreadsheet records against them.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from whul.resolve import normalize_team

#: Column names a schedule frame might use, in the order they are tried. The
#: feeds disagree about all three, which is why `whul.scoring.base` exists.
DATE_COLUMNS = ("game_date", "gameday", "fixture_date", "date")
HOME_COLUMNS = ("home_team", "home")
AWAY_COLUMNS = ("away_team", "away")
SCORE_COLUMNS = (("home_score", "away_score"), ("points_for", "points_against"))
COMPETITION_COLUMNS = ("competition", "notes", "game_type", "season_type")

FIXTURE_COLUMNS = (
    "season", "league", "team_key", "fixture_date", "opponent", "home",
    "competition", "fetched_at",
)


def _first(frame: pd.DataFrame, candidates) -> str | None:
    for name in candidates:
        if name in frame.columns:
            return name
    return None


def _unplayed(frame: pd.DataFrame) -> pd.Series | None:
    """Which rows are fixtures rather than results.

    A pair of score columns where both are null is the only reliable answer.
    ``completed`` is not: nflverse does not carry it, and a frame without it
    once made every unplayed fixture read as a finished game -- the bug this
    project keeps rediscovering. So the scores decide, and a frame whose scores
    cannot be found yields nothing rather than everything.
    """
    for home, away in SCORE_COLUMNS:
        if home in frame.columns and away in frame.columns:
            return frame[home].isna() & frame[away].isna()
    return None


def harvest(league: str, season: str, frame: pd.DataFrame,
            after: date, fetched_at: str,
            rename: dict[str, str] | None = None) -> pd.DataFrame:
    """Every unplayed game in a schedule frame, one row per side.

    ``after`` drops anything already in the past: a schedule downloaded in
    September still lists August's postponed fixtures as unplayed, and a
    "next fixture" in the past is worse than none.

    ``rename`` maps whatever the feed calls a team to the name the league
    drafted. nflverse says ``SEA`` and the roster says Seattle Seahawks, and
    the key everything joins on is built from the roster's spelling -- so
    without this the NFL matches nothing at all while looking like it worked.
    """
    empty = pd.DataFrame({c: pd.Series(dtype="object") for c in FIXTURE_COLUMNS})
    if frame is None or frame.empty:
        return empty

    when = _first(frame, DATE_COLUMNS)
    home_col = _first(frame, HOME_COLUMNS)
    away_col = _first(frame, AWAY_COLUMNS)
    unplayed = _unplayed(frame)
    if not (when and home_col and away_col) or unplayed is None:
        return empty

    work = frame[unplayed].copy()
    if work.empty:
        return empty
    work["_when"] = pd.to_datetime(work[when], errors="coerce").dt.date
    work = work[work["_when"].notna() & (work["_when"] >= after)]
    if work.empty:
        return empty

    competition = _first(work, COMPETITION_COLUMNS)
    spell = (lambda s: rename.get(s, s)) if rename else (lambda s: s)
    rows = []
    for side, other, home in ((home_col, away_col, 1), (away_col, home_col, 0)):
        named = work[side].astype(str).map(spell)
        block = pd.DataFrame({
            "season": season,
            "league": league,
            "team_key": named.map(normalize_team),
            "fixture_date": work["_when"].map(str),
            "opponent": work[other].astype(str).map(spell),
            "home": home,
            "competition": (
                work[competition].astype(str) if competition else ""
            ),
            "fetched_at": fetched_at,
        })
        rows.append(block)
    out = pd.concat(rows, ignore_index=True)
    out = out[out["team_key"].astype(str).str.strip() != ""]
    return out.drop_duplicates(
        subset=["season", "league", "team_key", "fixture_date", "opponent"]
    ).reset_index(drop=True)


def replace(store, season: str, league: str, rows: pd.DataFrame) -> int:
    """Swap in a league's fixtures, dropping whatever it had before.

    Wholesale rather than an upsert, because the interesting change is a
    fixture that has *gone* -- postponed, or played early. An upsert would
    leave it in the table for ever, and a date that never arrives is the one
    kind of wrong this column can be without anyone noticing.
    """
    with store.transaction():
        store.conn.execute(
            "DELETE FROM fixtures WHERE season = ? AND league = ?", (season, league)
        )
    if rows is None or rows.empty:
        return 0
    return store.insert_frame(
        "fixtures", rows[list(FIXTURE_COLUMNS)],
        ["season", "league", "team_key", "fixture_date", "opponent"],
    )


def next_by_team(store, season: str, as_of: date | str) -> dict[str, dict]:
    """The soonest fixture for each team, keyed by its normalized name."""
    rows = store.query(
        "SELECT team_key, fixture_date, opponent, home, competition, league "
        "FROM fixtures WHERE season = ? AND fixture_date >= ? "
        "ORDER BY fixture_date",
        (season, str(as_of)),
    )
    out: dict[str, dict] = {}
    for row in rows.itertuples():
        # Ordered by date, so the first row seen for a team is its next game.
        out.setdefault(str(row.team_key), {
            "date": str(row.fixture_date),
            "opponent": str(row.opponent),
            "home": bool(row.home),
            "competition": str(row.competition or ""),
            "league": str(row.league),
        })
    return out


def coverage(store, season: str) -> pd.DataFrame:
    """Which leagues have fixtures in hand, and how many teams they cover."""
    return store.query(
        "SELECT league, COUNT(DISTINCT team_key) AS teams, COUNT(*) AS fixtures, "
        "MIN(fixture_date) AS first, MAX(fixture_date) AS last "
        "FROM fixtures WHERE season = ? GROUP BY league ORDER BY league",
        (season,),
    )


#: Feeds whose team names are not the names the league drafted. Everything
#: else already speaks in display names, so the map is empty and the harvest
#: uses the feed's own spelling.
def spelling_for(source_key: str, seasons: list[int]) -> dict[str, str]:
    """``{what the feed calls a team: what the roster calls it}``."""
    if source_key == "nfl-teams":
        from whul.sources import nflverse

        teams = nflverse.load_teams(seasons)
        if teams.empty or "team_name" not in teams.columns:
            return {}
        return dict(zip(teams["team_abbr"].astype(str),
                        teams["team_name"].astype(str)))
    return {}


def by_asset(store, season: str, as_of: date | str) -> dict[str, dict]:
    """Each rostered asset's next fixture, keyed by asset id.

    A team is matched on its own name and a player on the club the spreadsheet
    records against them, which is the same join the corner badge uses. A
    player whose affiliation is a country rather than a club -- a driver, a
    golfer -- matches nothing, which is correct: their next event is a
    tournament and no fixture here describes one.
    """
    upcoming = next_by_team(store, season, as_of)
    if not upcoming:
        return {}
    assets = store.query(
        "SELECT DISTINCT a.asset_id, a.asset_type, a.display_name, a.affiliation "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id WHERE r.season = ?",
        (season,),
    )
    out: dict[str, dict] = {}
    for row in assets.itertuples():
        name = (str(row.display_name) if row.asset_type == "Team"
                else str(row.affiliation or ""))
        if not name.strip():
            continue
        found = upcoming.get(normalize_team(name))
        if found:
            out[str(row.asset_id)] = found
    return out


# --- clubs whose fixtures come from Flashscore ------------------------------

#: Shortest feed word that may stand in for a longer roster word. Three, so
#: "Man" reaches Manchester and "Inter" reaches Internazionale, while a
#: two-letter fragment cannot quietly attach itself to half the division.
ABBREVIATION_FLOOR = 3

#: Contractions a prefix rule cannot reach, because the short form is not the
#: start of the long one. Deliberately a short list of the standard ones rather
#: than a club-by-club table: anything not here shows up in the "no fixture
#: found" line, which is where a missing name is supposed to surface.
WORD_ALIASES = {
    "utd": "united", "weds": "wednesday", "nott'm": "nottingham",
    "nottm": "nottingham", "sheff": "sheffield", "wolves": "wolverhampton",
    "spurs": "tottenham", "boro": "middlesbrough", "gunners": "arsenal",
    "st": "saint", "st.": "saint",
}


#: Where a league's clubs may legitimately be playing, as Flashscore writes
#: the country in its competition headers. This is the guard against a name
#: that means two clubs in two countries: the feed carries the whole world at
#: once, Brazil's Serie B has an Athletic Club and so does Bilbao, and without
#: this one of them gets the other's fixtures while the page looks right.
#:
#: Continental and world competitions are listed alongside the domestic
#: country because a club's next game is often one of those. Anything not
#: listed is refused rather than guessed, and the run says which clubs found
#: nothing -- a missing fixture surfaces, a wrong one would not.
COUNTRIES: dict[str, set[str]] = {
    "Premier League": {"ENGLAND", "EUROPE", "WORLD"},
    "La Liga": {"SPAIN", "EUROPE", "WORLD"},
    "Serie A": {"ITALY", "EUROPE", "WORLD"},
    "Bundesliga": {"GERMANY", "EUROPE", "WORLD"},
    "Ligue 1": {"FRANCE", "EUROPE", "WORLD"},
    "MLS": {"USA", "NORTH & CENTRAL AMERICA", "NORTH AMERICA", "WORLD"},
    "NWSL": {"USA", "NORTH & CENTRAL AMERICA", "NORTH AMERICA", "WORLD"},
    "MLB": {"USA"},
    "NBA": {"USA"},
}

#: A league with no entry above may play anywhere. Used for the leagues whose
#: fixtures do not come from this feed at all, so it never actually widens
#: anything -- it is here so an unlisted league degrades to the old behaviour
#: rather than silently matching nothing.
ANYWHERE: set[str] = set()


def allowed_countries(league: str) -> set[str]:
    return COUNTRIES.get(str(league), ANYWHERE)


def wanted_teams(store, season: str) -> dict[str, tuple[str, str]]:
    """``{normalized: (the roster's spelling, the league it plays in)}``.

    Both the clubs somebody rosters *and* the clubs rostered players play for.
    The second is not a nicety: a manager holding four Bayern players and no
    Bayern is the ordinary case, and matching only team assets would leave
    every one of those four blank while looking like it worked.
    """
    rows = store.query(
        "SELECT DISTINCT a.asset_type, a.display_name, a.affiliation, a.league "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id WHERE r.season = ?",
        (season,),
    )
    out: dict[str, tuple[str, str]] = {}
    for row in rows.itertuples():
        name = (str(row.display_name) if row.asset_type == "Team"
                else str(row.affiliation or ""))
        if name.strip():
            out.setdefault(normalize_team(name), (name, str(row.league or "")))
    return out


def _abbreviates(feed_words: list[str], roster_words: list[str]) -> bool:
    """Is the feed's name a shortening of the roster's?

    Flashscore writes "Man City" where a roster says "Manchester City", and
    "Inter" where it says Internazionale. Each feed word must open a roster
    word, in order, and nothing may be left over on the feed's side. Order
    matters: without it "City Man" would match, and so would half the clubs
    whose names share a word.
    """
    if not feed_words or len(feed_words) > len(roster_words):
        return False
    remaining = list(roster_words)
    for word in feed_words:
        if len(word) < ABBREVIATION_FLOOR:
            return False
        for position, candidate in enumerate(remaining):
            if candidate.startswith(word):
                remaining = remaining[position + 1:]
                break
        else:
            return False
    return True


def _initials(words: list[str]) -> str:
    return "".join(w[0] for w in words if w)


def match_team(name: str, wanted: dict[str, tuple[str, str]],
               country: str = "") -> str | None:
    """The roster's spelling of a club the feed named, or None.

    Three stages, each requiring a *unique* answer: exact, then an
    abbreviation ("Man City", "Inter", "Dortmund"), then an initialism ("PSG").
    Uniqueness is the whole guard. Two roster clubs a feed name could equally
    mean is not a near miss to be broken by a further rule, it is a name this
    cannot read -- and guessing would put a Manchester United fixture beside a
    Manchester City badge, which is worse than a blank cell by some distance.

    ``country`` narrows the field to clubs that could be playing there before
    any of that runs. The feed is the whole world in one payload, so an exact
    name match is not enough on its own: Brazil's Serie B has an Athletic Club
    and so does Bilbao, and the first three days of a real payload contained
    the Brazilian one. Passing no country keeps the old behaviour, which is
    right for a feed that is not global.
    """
    if country:
        wanted = {
            key: value for key, value in wanted.items()
            if not allowed_countries(value[1])
            or country.upper() in allowed_countries(value[1])
        }
    if not wanted:
        return None

    key = normalize_team(name)
    if key in wanted:
        return wanted[key][0]
    words = [WORD_ALIASES.get(w, w) for w in key.split()]
    expanded = " ".join(words)
    if expanded in wanted:
        return wanted[expanded][0]

    hits = [
        value[0] for candidate, value in wanted.items()
        if _abbreviates(words, candidate.split())
    ]
    if len(hits) == 1:
        return hits[0]
    if len(words) == 1:
        letters = words[0]
        initialled = [
            value[0] for candidate, value in wanted.items()
            if _initials(candidate.split()) == letters and len(letters) > 1
        ]
        if len(initialled) == 1:
            return initialled[0]
    return None


def from_flashscore(store, season: str, as_of: date, leagues=None,
                    verbose: bool = True) -> dict[str, int]:
    """Fetch and record fixtures for the leagues Flashscore covers.

    Returns ``{league: rows recorded}``. One request per day of the window per
    *sport*, not per league: the soccer feed carries every competition at once,
    so seven requests cover all nine club-soccer categories.
    """
    from whul.sources import flashscore_fixtures as feed

    wanted = wanted_teams(store, season)
    if not wanted:
        if verbose:
            print("  nothing rostered, so no club to look for", flush=True)
        return {}

    leagues = list(leagues) if leagues else sorted(feed.SPORTS)
    sports = sorted({feed.SPORTS[l] for l in leagues if l in feed.SPORTS})
    recorded: dict[str, int] = {}
    seen: set[str] = set()

    for sport in sports:
        try:
            upcoming = feed.load_upcoming(sport, verbose=verbose)
        except Exception as exc:  # noqa: BLE001 -- one sport must not lose the rest
            print(f"  flashscore sport {sport}: {type(exc).__name__}: {exc}",
                  flush=True)
            continue
        if upcoming.empty:
            continue

        # Matched row by row, not name by name, because the country is what
        # decides. "Athletic Club" is Bilbao under SPAIN and a Serie B side
        # under BRAZIL; a name-keyed filter would keep the Brazilian fixture
        # on the strength of the Spanish match.
        rename: dict[str, str] = {}
        keep: list = []
        held: set[str] = set()
        for row in upcoming.itertuples():
            country = str(getattr(row, "country", "") or "")
            sides = [
                (str(row.home_team), match_team(str(row.home_team), wanted, country)),
                (str(row.away_team), match_team(str(row.away_team), wanted, country)),
            ]
            if not any(found for _, found in sides):
                continue
            for spelling, found in sides:
                if found:
                    rename[spelling] = found
                    held.add(normalize_team(found))
            keep.append(row.Index)
        if not keep:
            continue
        mine = upcoming.loc[keep]

        rows = harvest(
            f"flashscore-{sport}", season, mine, as_of, _timestamp(), rename=rename
        )
        # Only the clubs somebody holds. The other side of the tie was
        # translated for display and must not become a row of its own.
        rows = rows[rows["team_key"].isin(held)]
        if rows.empty:
            continue
        rows = rows.copy()
        rows["league"] = f"Flashscore/{sport}"
        recorded[f"Flashscore/{sport}"] = replace(
            store, season, f"Flashscore/{sport}", rows
        )
        seen |= set(rows["team_key"])

    if verbose:
        missing = sorted(
            value[0] for key, value in wanted.items() if key not in seen
        )
        print(f"  {len(seen)} of {len(wanted)} rostered club(s) have a fixture "
              f"in the next week.", flush=True)
        if missing:
            print("  No fixture found for: " + ", ".join(missing[:20]), flush=True)
            print("  (out of season, between competitions, or a name this "
                  "could not read)", flush=True)
    return recorded


def _timestamp() -> str:
    from whul.store.db import _now

    return _now()
