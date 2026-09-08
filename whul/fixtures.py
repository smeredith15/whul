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
