"""What each rostered team plays next.

Nothing here fetches anything. Every schedule the pipeline downloads for
scoring already carries the games that have *not* been played -- nflverse ships
the whole NFL season in September, and a team's own ESPN schedule is its whole
season whatever the date -- and the scorers drop those rows because a game with
no score cannot be scored. This reads the same frames on the way past and keeps
the half that was being thrown away, so the column costs no requests at all.

That is not the whole of it. A league whose live feed walks a scoreboard one
past date at a time has no fixtures in hand, and a league that reports season
totals rather than games (the NHL) has no schedule at all -- those take theirs
from the Flashscore feed instead, which carries a week of every sport at once.
A third shape has no fixture in any feed: a golfer's next start and a driver's
are an event with a field rather than a game against somebody, and those are
answered with the tour's own calendar. ``coverage`` says which league is on
which footing rather than leaving a reader to infer it from a column of
blanks.

Fixtures are keyed by team, not by asset. A club and the players who play for
it share one row, because a player's next fixture is their club's -- joined
through the club the spreadsheet records against them.
"""

from __future__ import annotations

import re
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
    "competition", "round_name", "fetched_at",
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
            "round_name": (
                work["round"].astype(str) if "round" in work.columns else ""
            ),
            "fetched_at": fetched_at,
        })
        rows.append(block)
    out = pd.concat(rows, ignore_index=True)
    out = out[out["team_key"].astype(str).str.strip() != ""]
    return out.drop_duplicates(
        subset=["season", "league", "team_key", "fixture_date", "opponent"]
    ).reset_index(drop=True)


def tour_rows(league: str, season: str, entrants: dict[str, str],
              ahead: dict[str, list[dict]], fetched_at: str) -> pd.DataFrame:
    """One row per athlete naming their series' next event.

    ``entrants`` is ``{athlete: series}`` -- read off the results the pull just
    scored, not off the roster's league label, because the spreadsheet files
    the same F1 driver under "F1" and under "Motorsports" and only the results
    say which car anyone is in. ``ahead`` is ``{series: events}``, soonest
    first.

    The row carries no opponent, which is the honest shape: a tournament has a
    field, not a fixture, and everyone in the series shares the answer. That
    also means this says what the tour plays next and not that this athlete has
    entered it -- a distinction worth keeping, because no season schedule
    anywhere carries a field before the event.
    """
    empty = pd.DataFrame({c: pd.Series(dtype="object") for c in FIXTURE_COLUMNS})
    rows = []
    for athlete, series in entrants.items():
        events = ahead.get(str(series)) or []
        if not events or not str(athlete).strip():
            continue
        event = events[0]
        if not str(event.get("name") or "").strip():
            continue
        rows.append({
            "season": season,
            "league": league,
            "team_key": normalize_team(str(athlete)),
            "fixture_date": str(event["date"]),
            "opponent": "",
            "home": 1,
            "competition": str(event["name"]),
            "round_name": "",
            "fetched_at": fetched_at,
        })
    if not rows:
        return empty
    return pd.DataFrame(rows, columns=list(FIXTURE_COLUMNS)).drop_duplicates(
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
    ready = rows.copy()
    # A caller that predates a column should not have to know about it. Only
    # the optional ones are filled: a row missing a key still fails, loudly.
    for column in ("competition", "round_name"):
        if column not in ready.columns:
            ready[column] = ""
    return store.insert_frame(
        "fixtures", ready[list(FIXTURE_COLUMNS)],
        ["season", "league", "team_key", "fixture_date", "opponent"],
    )


#: Which feed may speak for which league. A fixture recorded under any other
#: feed is not that league's, whatever team it names.
#:
#: This is the second guard, and it exists because the first one failed
#: silently: a soccer match reached three NFL rosters, and because both sides
#: had been translated into the roster's spelling it read as a plausible NFL
#: game -- "New England Patriots vs Buffalo Bills", on a Wednesday, in
#: September. Nothing about the row looked wrong. So rather than trust the
#: matching alone, a fixture now has to have come from a feed that covers the
#: asset's league before it can be shown.
FEEDS: dict[str, set[str]] = {
    "MLB": {"Flashscore/6"},
    "NBA": {"Flashscore/3"},
    "NHL": {"Flashscore/4"},
    "ATP": {"Flashscore/2"},
    "WTA": {"Flashscore/2"},
    "Tennis": {"Flashscore/2"},
    "Premier League": {"Flashscore/1"},
    "La Liga": {"Flashscore/1"},
    "Serie A": {"Flashscore/1"},
    "Bundesliga": {"Flashscore/1"},
    "Ligue 1": {"Flashscore/1"},
    "MLS": {"Flashscore/1"},
    # Both, because the feed's women's marker is what sorts a match into one
    # bucket or the other and an NWSL club could be written either way.
    "NWSL": {"Flashscore/1", "Flashscore/1W"},
    # The one place in this project where two rostered assets share a display
    # name: England, France and Spain are held in both categories. Nothing in
    # the name can tell them apart, so the *bucket* does -- the soccer feed is
    # split in two on the way in, and this is the half each may read.
    "Men's Intl Soccer": {"Flashscore/1"},
    "Women's Intl Soccer": {"Flashscore/1W"},
    # A tour's next event, recorded under the source that pulled it. The
    # roster files F1 drivers under two different league labels, so both --
    # and NASCAR -- point at the one source that fetches them.
    "PGA": {"PGA"},
    "Motorsports": {"Motorsports"},
    "F1": {"Motorsports"},
    "NASCAR": {"Motorsports"},
}


#: Leagues whose "next" is an event with a field rather than a fixture with an
#: opponent. A golfer's is the tour's next tournament and a driver's is the
#: next race: the same answer for everyone in the series, which is why the row
#: carries an event name and no opponent, and why the cell reads
#: "Sep 17 - Procore Championship" rather than "vs" anybody.
#:
#: This is not the same claim as an entry list. It says what the tour plays
#: next, not that this athlete is in the field -- which is the honest limit of
#: what a season schedule can support, and is what the column meant for every
#: other league anyway.
TOUR: frozenset[str] = frozenset({"PGA", "NASCAR", "F1", "Motorsports"})


#: Leagues whose fixtures ride along with their own scoring pull, because
#: their feed hands over a whole schedule and the scorer discards the half
#: nobody has played. These arrive with `ingest`, not with `fixtures --fetch`.
HARVESTED: frozenset[str] = frozenset({
    "NFL", "NCAAF", "NCAAM", "NCAAW", "NCAA Baseball", "NCAA Softball",
})


#: Roster categories whose fixtures are worth labelling with the competition.
#: A club plays in four or five of them in a season and which one it is changes
#: what the fixture means -- a Tuesday in Europe is not a Saturday in the
#: league. Everywhere else the competition is the league, so printing it would
#: repeat the row above.
#: Leagues whose assets are matched on their own name rather than on a club.
#: A tennis player has no affiliation to join through -- their next fixture is
#: their own match.
INDIVIDUAL: frozenset[str] = frozenset({
    "ATP", "WTA", "Tennis", "PGA", "NASCAR", "F1", "Motorsports",
})

SOCCER: frozenset[str] = frozenset({
    "Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1",
    "MLS", "NWSL", "EPL", "Club Soccer",
})

#: How a competition is written in a column this narrow. Anything not listed
#: falls back to its own initials, which is right far more often than not --
#: "Coppa Italia" becomes CI, "Copa del Rey" CDR -- and is never a guess about
#: what the competition *is*.
COMPETITION_SHORT: dict[str, str] = {
    "premier league": "PL", "laliga": "LL", "la liga": "LL",
    "serie a": "SA", "bundesliga": "BUN", "ligue 1": "L1",
    "mls": "MLS", "nwsl": "NWSL",
    "champions league": "UCL", "uefa champions league": "UCL",
    "europa league": "UEL", "uefa europa league": "UEL",
    "europa conference league": "UECL", "conference league": "UECL",
    "fa cup": "FA", "efl cup": "EFL", "league cup": "EFL", "carabao cup": "EFL",
    "copa del rey": "CDR", "dfb pokal": "DFB", "dfb-pokal": "DFB",
    "coppa italia": "CI", "coupe de france": "CDF",
    "club world cup": "CWC", "fifa club world cup": "CWC",
    "supercopa": "SCP", "super cup": "SC", "community shield": "CS",
    "us open cup": "USOC", "leagues cup": "LC",
    "concacaf champions cup": "CCC", "champions cup": "CCC",
    "mls cup": "MLS", "playoffs": "PO",
}

#: Words that carry no information in an initialism.
_SKIP_WORDS = frozenset({
    "of", "the", "and", "de", "del", "di", "du", "da", "la", "le", "les",
    "el", "il", "der", "des",
})


def short_competition(name: str) -> str:
    """A competition in three or four characters, for a column this narrow."""
    text = " ".join(str(name or "").split()).strip()
    if not text:
        return ""
    known = COMPETITION_SHORT.get(text.lower())
    if known:
        return known
    words = [w for w in re.split(r"[\s\-]+", text) if w and w.lower() not in _SKIP_WORDS]
    if len(words) == 1:
        return words[0][:4].upper()
    return "".join(w[0] for w in words[:4]).upper()


def feeds_for(league: str) -> set[str]:
    """The feeds allowed to supply this league's fixtures.

    A league not listed above takes its fixtures from its own scoring feed,
    recorded under its own name -- which is every league whose schedule is
    harvested on the way past.
    """
    return FEEDS.get(str(league), {str(league)})


def next_by_team(store, season: str, as_of: date | str) -> dict[str, list[dict]]:
    """Every upcoming fixture for each team, soonest first, by normalized name.

    A list rather than one row, because which of them is *this asset's* next
    fixture depends on the asset: two clubs in different sports can share a
    normalized name, and the caller settles it by feed.
    """
    rows = store.query(
        "SELECT team_key, fixture_date, opponent, home, competition, "
        "       round_name, league "
        "FROM fixtures WHERE season = ? AND fixture_date >= ? "
        "ORDER BY fixture_date",
        (season, str(as_of)),
    )
    out: dict[str, list[dict]] = {}
    for row in rows.itertuples():
        out.setdefault(str(row.team_key), []).append({
            "date": str(row.fixture_date),
            "opponent": str(row.opponent),
            "home": bool(row.home),
            "competition": str(row.competition or ""),
            "round": str(getattr(row, "round_name", "") or ""),
            "league": str(row.league),
        })
    return out


def _owned(store, season: str) -> tuple[dict, dict, dict]:
    """Everything the board needs to know about who owns what.

    ``by_name``   normalized side -> [(asset_id, league)], every rostered asset
                  that plays for that side. A club and the four players who
                  play for it all hang off one name, which is what puts them
                  under the same heading.
    ``owner``     asset_id -> manager_id.
    ``spelling``  normalized side -> the roster's spelling of it, for a side
                  whose own row is all this has: ``team_key`` is normalized and
                  the display name only survives on the *other* row of the tie,
                  which for a one-sided fixture does not exist.
    """
    rows = store.query(
        "SELECT DISTINCT a.asset_id, a.asset_type, a.display_name, "
        "       a.affiliation, a.league, r.manager_id "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id WHERE r.season = ?",
        (season,),
    )
    by_name: dict[str, list[tuple[str, str]]] = {}
    owner: dict[str, str] = {}
    spelling: dict[str, str] = {}
    for row in rows.itertuples():
        league = str(row.league or "")
        name = (str(row.display_name) if row.asset_type == "Team"
                or league in INDIVIDUAL else str(row.affiliation or ""))
        if not name.strip():
            continue
        key = normalize_team(name)
        by_name.setdefault(key, []).append((str(row.asset_id), league))
        owner[str(row.asset_id)] = str(row.manager_id or "")
        # A team's own name wins over a player's affiliation: both spell the
        # club, and the one somebody drafted is the one the league calls it.
        if row.asset_type == "Team" or key not in spelling:
            spelling[key] = name
    return by_name, owner, spelling


def owners(store, season: str) -> dict[str, str]:
    """``asset_id -> manager_id`` for everything currently rostered."""
    return _owned(store, season)[1]


def board(store, season: str, as_of: date | str) -> list[dict]:
    """Every upcoming fixture an owned asset is in, with both sides named.

    The roster pages answer "what does this asset play next"; this answers the
    other half of the same question -- who is playing whom, and which of the
    league's assets are on each side of it. A tie between two drafted clubs is
    invisible on a roster page and is the most interesting row here.

    One entry per match rather than the two rows the table holds. The table is
    keyed by side because that is what a lookup wants; a fixture list wants the
    fixture, and the two rows of a tie are folded back together on the pair of
    names -- which works whether the table holds both (a harvested league keeps
    every side) or only one (Flashscore keeps the sides somebody owns).

    A tour event has one side and no opponent: a field is not a fixture, and
    everyone in the series shares the heading.
    """
    rows = store.query(
        "SELECT league, team_key, fixture_date, opponent, home, competition "
        "FROM fixtures WHERE season = ? AND fixture_date >= ? "
        "ORDER BY fixture_date",
        (season, str(as_of)),
    )
    if rows.empty:
        return []
    by_name, owner, spelling = _owned(store, season)

    matches: dict[tuple, dict] = {}
    for row in rows.itertuples():
        when = str(row.fixture_date or "").strip()
        if not when:
            # A fixture with no date cannot be placed on a list ordered by
            # date, and a blank at the top reads as "today".
            continue
        feed = str(row.league)
        mine = normalize_team(str(row.team_key))
        against = normalize_team(str(row.opponent or ""))
        competition = str(row.competition or "")
        if not against:
            # A tour event. Everyone in the series is under one heading, so the
            # event itself is the key -- not the athlete, who would otherwise
            # get an entry of his own with a field of one.
            key = (when, feed, competition, "")
            entry = matches.setdefault(key, {
                "date": when, "feed": feed, "competition": competition,
                "event": True, "sides": [{"name": competition, "keys": []}],
            })
            if mine not in entry["sides"][0]["keys"]:
                entry["sides"][0]["keys"].append(mine)
            continue
        pair = tuple(sorted((mine, against)))
        key = (when, feed, competition, pair)
        home = mine if row.home else against
        away = against if row.home else mine
        entry = matches.setdefault(key, {
            "date": when, "feed": feed, "competition": competition,
            "event": False,
            "sides": [{"name": home, "keys": [home]},
                      {"name": away, "keys": [away]}],
        })
        # The display spelling, which only the *other* row of a tie carries:
        # `team_key` is normalized and `opponent` is not.
        spelling.setdefault(against, str(row.opponent))

    out = []
    for entry in matches.values():
        sides = []
        for side in entry["sides"]:
            assets = [
                asset for key in side["keys"]
                for asset, league in by_name.get(key, ())
                if entry["feed"] in feeds_for(league)
            ]
            sides.append({
                "name": side["name"] if entry["event"]
                        else spelling.get(side["name"], _titled(side["name"])),
                "assets": assets,
                "owners": sorted({owner.get(a, "") for a in assets} - {""}),
            })
        if not any(side["assets"] for side in sides):
            # Every row here reached the table because somebody owns a side,
            # but the feed guard can still rule all of them out -- and a
            # fixture with nobody in it is a fixture this page is not about.
            continue
        out.append({**entry, "sides": sides,
                    "owners": sorted({o for s in sides for o in s["owners"]})})
    return sorted(out, key=lambda e: (e["date"], e["competition"],
                                      e["sides"][0]["name"]))


def _titled(key: str) -> str:
    """A normalized name with nothing better to show. Never blank."""
    return " ".join(word.capitalize() for word in str(key).split()) or "—"


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
        "SELECT DISTINCT a.asset_id, a.asset_type, a.display_name, a.affiliation, "
        "       a.league "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id WHERE r.season = ?",
        (season,),
    )
    out: dict[str, dict] = {}
    for row in assets.itertuples():
        league_of = str(row.league or "")
        name = (
            str(row.display_name)
            if row.asset_type == "Team" or league_of in INDIVIDUAL
            else str(row.affiliation or "")
        )
        if not name.strip():
            continue
        league = league_of
        allowed = feeds_for(league)
        for fixture in upcoming.get(normalize_team(name), []):
            if fixture["league"] in allowed:
                found = dict(fixture)
                # A tennis match has no home side. Both players are listed
                # because the feed lists them in an order, not because one of
                # them is at home, and "at Carlos Alcaraz" reads as a venue.
                if league in INDIVIDUAL:
                    found["home"] = True
                # Only the clubs. Elsewhere the competition *is* the league,
                # and printing it would repeat the category beside it.
                # A club's competition; a tennis player's round. Both answer
                # the same question -- what *kind* of match is this -- and a
                # quarter-final is no more the same as a first round than a
                # European night is the same as a league Saturday. Nowhere
                # else, because everywhere else the competition is the league.
                found["badge"] = (
                    short_competition(found.get("competition", ""))
                    if league in SOCCER
                    else str(found.get("round", "") or "") if league in INDIVIDUAL
                    else ""
                )
                out[str(row.asset_id)] = found
                break
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
#: Where an international match is filed. The feed heads these with the
#: confederation or with WORLD rather than with a country, since neither side
#: is at home in the sense a club is.
_CONTINENTS: set[str] = {
    "WORLD", "EUROPE", "AFRICA", "ASIA", "OCEANIA", "SOUTH AMERICA",
    "NORTH & CENTRAL AMERICA", "NORTH AMERICA",
}

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
    # Half the league is Canadian and the feed files the competition under one
    # country, not two -- which one is not knowable from here, so both are
    # allowed. This is a second guard rather than the only one: the payload is
    # narrowed to the sport that serves the league before a name is read at
    # all, so an NHL club is never offered to the soccer feed.
    "NHL": {"USA", "CANADA"},
    # A national side plays under a continental or world header, never under
    # its own country's -- that heading is where its clubs are. Listing the
    # confederations is what keeps "England" the national team rather than a
    # club somebody spells the same way.
    "Men's Intl Soccer": _CONTINENTS,
    "Women's Intl Soccer": _CONTINENTS,
}

#: A league with no entry above may play anywhere. Used for the leagues whose
#: fixtures do not come from this feed at all, so it never actually widens
#: anything -- it is here so an unlisted league degrades to the old behaviour
#: rather than silently matching nothing.
ANYWHERE: set[str] = set()


def allowed_countries(league: str) -> set[str]:
    return COUNTRIES.get(str(league), ANYWHERE)


def wanted_teams(store, season: str,
                 leagues: set[str] | None = None) -> dict[str, tuple[str, str]]:
    """``{normalized: (the roster's spelling, the league it plays in)}``.

    Both the clubs somebody rosters *and* the clubs rostered players play for.
    The second is not a nicety: a manager holding four Bayern players and no
    Bayern is the ordinary case, and matching only team assets would leave
    every one of those four blank while looking like it worked.

    ``leagues`` narrows it to the leagues the caller can actually serve, and
    is the difference between a search and a hazard. Unscoped, this offered
    the Flashscore matcher all 176 clubs on the roster -- the NFL, the NHL,
    college football, golfers -- and a soccer feed carrying a club Flashscore
    writes as "Buffalo" or "New England" then reached the Bills and the
    Patriots through the abbreviation rule. The country guard did not stop it:
    those leagues have no country list, and "no list" was being read as
    "anywhere" rather than as "not from this feed".
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
        league = str(row.league or "")
        if leagues is not None and league not in leagues:
            continue
        name = (
            str(row.display_name)
            if row.asset_type == "Team" or league in INDIVIDUAL
            else str(row.affiliation or "")
        )
        if name.strip():
            out.setdefault(normalize_team(name), (name, league))
    return out


def rostered_leagues(store, season: str,
                     leagues: set[str] | None = None) -> dict[str, set[str]]:
    """``{normalized name: every league that name is rostered in}``.

    ``wanted_teams`` keeps one league per name, which is all a lookup needs and
    is not enough to say whether a name was covered: England is held in both
    international categories, and reporting against whichever of them was
    inserted first would call a covered club missing -- or, worse, call a
    missing one covered.
    """
    out: dict[str, set[str]] = {}
    for key, (_, league) in wanted_teams(store, season, leagues).items():
        out.setdefault(key, set()).add(league)
    for row in store.query(
        "SELECT DISTINCT a.asset_type, a.display_name, a.affiliation, a.league "
        "FROM roster_slots r "
        "JOIN slot_occupancy o ON o.slot_id = r.slot_id AND o.end_date IS NULL "
        "JOIN assets a ON a.asset_id = o.asset_id WHERE r.season = ?",
        (season,),
    ).itertuples():
        league = str(row.league or "")
        if leagues is not None and league not in leagues:
            continue
        name = (str(row.display_name) if row.asset_type == "Team"
                or league in INDIVIDUAL else str(row.affiliation or ""))
        if name.strip():
            out.setdefault(normalize_team(name), set()).add(league)
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
    so seven requests cover all nine club-soccer categories and both
    international ones.

    That window is a week, which is the right shape for a league in season and
    the wrong one for a league that is not. A league with a season page of its
    own is also asked for that, and the two are merged.
    """
    from whul.sources import flashscore_fixtures as feed

    leagues = list(leagues) if leagues else sorted(feed.SPORTS)
    wanted = wanted_teams(store, season, leagues=set(leagues))
    if not wanted:
        if verbose:
            print("  nothing rostered, so no club to look for", flush=True)
        return {}

    sports = sorted({feed.SPORTS[l] for l in leagues if l in feed.SPORTS})
    recorded: dict[str, int] = {}
    #: Which bucket each rostered club was found in, so the summary below can
    #: say a club has a fixture only where the fixture is one it may read.
    seen: dict[str, set[str]] = {}

    for sport in sports:
        # Narrowed to the leagues this sport actually serves before a single
        # name is read. The country guard is the second line and not the first:
        # an NHL club and an MLS club can both be legitimately playing in the
        # USA, and "New York" abbreviates into either. Offering only the
        # leagues the payload could contain removes the question.
        here = {k: v for k, v in wanted.items()
                if feed.SPORTS.get(v[1]) == sport}
        if not here:
            continue
        try:
            upcoming = feed.load_upcoming(sport, verbose=verbose)
        except Exception as exc:  # noqa: BLE001 -- one sport must not lose the rest
            print(f"  flashscore sport {sport}: {type(exc).__name__}: {exc}",
                  flush=True)
            upcoming = pd.DataFrame()
        # The day feed is a week wide, which is no window at all for a league
        # that opens in six weeks: the NHL and the NBA were blank through
        # September while the feed worked perfectly. Their own season pages
        # carry the schedule rather than the week, and the two are merged --
        # the same match has the same id in both, so the overlap collapses.
        seasonal = [lg for lg in leagues
                    if feed.SEASON_PAGES.get(lg, (None,))[0] == sport]
        for league in seasonal:
            try:
                found = feed.load_season(league, verbose=verbose)
            except Exception as exc:  # noqa: BLE001 -- one page, not the run
                print(f"  {league}: season page not read "
                      f"({type(exc).__name__}: {exc})", flush=True)
                continue
            if not found.empty:
                upcoming = pd.concat([upcoming, found], ignore_index=True)
        if upcoming.empty:
            continue
        upcoming = upcoming.drop_duplicates(
            subset=["match_uid"]).reset_index(drop=True)

        # Matched row by row, not name by name, because the country is what
        # decides. "Athletic Club" is Bilbao under SPAIN and a Serie B side
        # under BRAZIL; a name-keyed filter would keep the Brazilian fixture
        # on the strength of the Spanish match.
        #
        # One bucket per feed league rather than one per sport. The soccer
        # payload carries the men's game and the women's in the same request
        # and this roster holds England in both, so the marker on the team
        # name sorts each match into its own bucket and `FEEDS` decides which
        # bucket an asset may read. Without that, a women's qualifier lands on
        # the men's card looking entirely ordinary.
        buckets: dict[str, dict] = {}
        for row in upcoming.itertuples():
            country = str(getattr(row, "country", "") or "")
            home, away = str(row.home_team), str(row.away_team)
            womens = feed.is_womens(home), feed.is_womens(away)
            if womens[0] != womens[1]:
                # Not a fixture in any competition this reads. Dropped rather
                # than assigned, because either bucket would be a guess.
                continue
            league_key = f"Flashscore/{sport}" + ("W" if womens[0] else "")
            sides = [
                (home, match_team(feed.strip_womens(home) if womens[0] else home,
                                  here, country)),
                (away, match_team(feed.strip_womens(away) if womens[1] else away,
                                  here, country)),
            ]
            if not any(found for _, found in sides):
                continue
            bucket = buckets.setdefault(
                league_key, {"rename": {}, "held": set(), "keep": []})
            for spelling, found in sides:
                if found:
                    bucket["rename"][spelling] = found
                    bucket["held"].add(normalize_team(found))
                elif womens[0]:
                    # The other side of a women's tie, which nobody rosters and
                    # so keeps the feed's spelling. The marker comes off it
                    # anyway: the bucket already says whose game this is, and
                    # "vs Norway W" on a women's card is the feed showing
                    # through.
                    bucket["rename"][spelling] = feed.strip_womens(spelling)
            bucket["keep"].append(row.Index)

        for league_key, bucket in buckets.items():
            rows = harvest(
                f"flashscore-{sport}", season, upcoming.loc[bucket["keep"]],
                as_of, _timestamp(), rename=bucket["rename"],
            )
            # Only the clubs somebody holds. The other side of the tie was
            # translated for display and must not become a row of its own.
            rows = rows[rows["team_key"].isin(bucket["held"])]
            if rows.empty:
                continue
            rows = rows.copy()
            rows["league"] = league_key
            recorded[league_key] = replace(store, season, league_key, rows)
            seen.setdefault(league_key, set()).update(rows["team_key"])

    if verbose:
        held_in = rostered_leagues(store, season, set(leagues))

        def found(key: str) -> bool:
            return any(key in seen.get(bucket, ())
                       for league in held_in.get(key, ())
                       for bucket in feeds_for(league))

        matched = [k for k in wanted if found(k)]
        missing = sorted(v[0] for k, v in wanted.items() if not found(k))
        print(f"  {len(matched)} of {len(wanted)} rostered club(s) have a fixture "
              f"in the next week.", flush=True)
        if missing:
            print("  No fixture found for: " + ", ".join(missing[:20]), flush=True)
            print("  (out of season, between competitions, or a name this "
                  "could not read)", flush=True)
    return recorded


def _timestamp() -> str:
    from whul.store.db import _now

    return _now()
