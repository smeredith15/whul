"""Whether a competition's championship has been decided, read off the pulls.

A postseason bonus is held until its competition is over
(``whul.scoring.completion``), and over was a date written down with a margin:
the World Series by 20 November, the Finals and the Stanley Cup by 15 July. A
generous date is the safe one, and it held every bonus for a fortnight or a
month after the trophy was lifted.

Several feeds already say when it was. The team pulls track every series,
because a series win is worth points: a club with four World Series wins, or
four playoff series won in the NBA or the NHL, is the champion, and nothing in
that postseason can move again. The NFL's schedule holds the Super Bowl with
its score. FotMob names the round of every European match it reads, so a
final with a result in it is the end of that competition.

Each reader answers with the (competition, season) pairs it can see decided,
in ``whul.scoring.completion``'s keys and season labels. Seeing nothing is the
ordinary answer and costs only the wait for the declared date, which stays the
fallback.
"""

from __future__ import annotations

import pandas as pd

#: Playoff series a champion wins in the NBA and the NHL: four rounds of
#: best-of-seven, for every club in the field. The NBA Play-In is not a series
#: and is not counted as one by the scorer.
TITLE_SERIES = 4

#: World Series wins that take it.
WORLD_SERIES_WON_AT = 4

#: What a round is called when it is the final, after ``_plain``. Exact, so a
#: semi-final or a quarter-final can never read as one.
FINAL_ROUNDS = frozenset({"final", "the final"})


def _plain(text) -> str:
    return " ".join(str(text or "").replace("-", " ").lower().split())


def _seasons_where(frame: pd.DataFrame, column: str, at_least: float) -> set[int]:
    """Seasons in which some club's ``column`` reached ``at_least``."""
    if frame is None or frame.empty or column not in frame.columns \
            or "season" not in frame.columns:
        return set()
    reached = pd.to_numeric(frame[column], errors="coerce") >= at_least
    seasons = pd.to_numeric(frame.loc[reached, "season"], errors="coerce").dropna()
    return {int(s) for s in seasons}


def _nfl(raw=None, scored=None) -> list[tuple[str, int]]:
    """A Super Bowl with both scores in it."""
    if raw is None or raw.empty or "game_type" not in raw.columns:
        return []
    played = raw[raw["game_type"].astype(str) == "SB"]
    for side in ("home_score", "away_score"):
        if side not in played.columns:
            return []
        played = played[pd.to_numeric(played[side], errors="coerce").notna()]
    return [("NFL", int(s)) for s in sorted(set(played["season"].astype(int)))]


def _mlb(raw=None, scored=None) -> list[tuple[str, int]]:
    """A club with four World Series wins."""
    if raw is None or raw.empty:
        return []
    from whul.scoring import mlb

    games = mlb._team_games(raw)
    if games.empty:
        return []
    won = games[games["is_win"] & (games["game_type"] == mlb.GAME_TYPE_WS)]
    wins = won.groupby(["season", "team"]).size().rename("ws_wins").reset_index()
    return [("MLB", s) for s in sorted(
        _seasons_where(wins, "ws_wins", WORLD_SERIES_WON_AT))]


def _series(competition: str, column: str):
    def seen(raw=None, scored=None) -> list[tuple[str, int]]:
        return [(competition, s) for s in sorted(
            _seasons_where(scored, column, TITLE_SERIES))]
    return seen


def _european(raw=None, scored=None) -> list[tuple[str, int]]:
    """A final with a result, in a competition whose bonus is held.

    Read off the match lines, which are written only for a finished match, so
    a final that has a line has been played. Classified the way the scorer
    classifies the same line, so the competition named here is the one whose
    bonus it releases. An MLS or NWSL playoff final releases its own.
    """
    if raw is None or raw.empty or "round" not in raw.columns:
        return []
    from whul.scoring.competition import classify, classify_key
    from whul.scoring.postseason import rule_for

    finals = raw[[_plain(r) in FINAL_ROUNDS for r in raw["round"]]]
    if finals.empty:
        return []
    keys = (finals["competition_key"] if "competition_key" in finals.columns
            else pd.Series("", index=finals.index)).fillna("").astype(str)
    labels = (finals["competition"] if "competition" in finals.columns
              else pd.Series("", index=finals.index)).fillna("").astype(str)
    leagues = (finals["league"] if "league" in finals.columns
               else pd.Series("", index=finals.index)).fillna("").astype(str)
    out = set()
    for key, label, league, season in zip(keys, labels, leagues, finals["season"]):
        tier = (classify_key(key, label) if key else classify(label)).tier.value
        rule = rule_for(tier, league)
        if rule is not None and pd.notna(season):
            out.add((rule.competition, int(season)))
    return sorted(out)


#: Which reader each pull carries, by source key. ``raw`` is the feed's frame
#: cut to the day being scored and ``scored`` what the scorer made of it, so a
#: reader sees nothing played after that day.
READERS = {
    "nfl-teams": _nfl,
    "mlb-teams": _mlb,
    "nba-teams": _series("NBA", "playoff_series_wins"),
    "nhl-teams": _series("NHL", "series_wins"),
    "soccer-players": _european,
}


def seen(source_key: str, raw: pd.DataFrame | None = None,
         scored: pd.DataFrame | None = None) -> list[tuple[str, int]]:
    """The championships this pull shows decided, as (competition, season).

    Never raises: a reader that cannot make sense of a frame leaves the
    declared date to do its job, which is what it did before any of this.
    """
    reader = READERS.get(str(source_key))
    if reader is None:
        return []
    try:
        return list(reader(raw=raw, scored=scored))
    except Exception:  # noqa: BLE001 -- a sighting is never worth a pull
        return []
