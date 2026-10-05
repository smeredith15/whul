"""NHL scoring -- port of NHL_Teams_Players.R.

**Skaters only.** Goalies are scored here for completeness but excluded from
normalization, matching ``All_Analysis.R``: goalie and skater distributions are
not comparable and the league abandoned goalie slots.

**84-game season.** The NHL expands from 82 games in 2026-27, so historical
benchmarks describe a shorter season than the one being scored. The bar is
lifted to 84 games and live points are scored as played -- see
``whul.scoring.schedule``. For skaters the benchmark is lifted whole. For clubs
only the regular-season terms of the 82-game history are lifted, as they are
scored for the pool, because a division title or a playoff run does not grow
with the schedule. A 2026-27 club has played 84 games and is scored on them.

**No byes.** All sixteen qualifiers play a first round, so the rule that a bye
scores as a swept round does not arise here.

**Division titles need the standings.** A season summary says how a club did,
not who it was competing with, so ``score_teams`` takes a division map and
awards nothing without one. It also awards nothing until every club in the
division has played its schedule: a title is an outcome, not a rate, and one
handed out in November belongs to whoever started well.
"""

from __future__ import annotations

import pandas as pd

from whul.scoring.base import resolve_num, resolve_str
from whul.scoring.schedule import factor_for, lift_for, scheduled_games

# --- teams ----------------------------------------------------------------
PTS_WIN = 2.0
PTS_OTL = 1.0
PTS_GOAL_DIFF = 0.1
PTS_DIV_CHAMP = 10.0
PTS_PLAYOFF_APP = 5.0
PTS_PLAYOFF_WIN = 1.0
PTS_SERIES_WIN = 5.0
WINS_PER_SERIES = 4

#: Standings points, which is what a division is won on. Computed from wins and
#: overtime losses rather than read from the feed's own ``points`` column: those
#: two are already resolved by name and checked by the probe, and a column that
#: turns out to be absent resolves to zero for every club -- which would not
#: fail, it would make every division a five-way tie.
STANDINGS_WIN = 2
STANDINGS_OTL = 1

#: The NHL's tiebreakers, in the order it applies them, minus the ones a season
#: summary cannot answer. Games played never separates anyone here because a
#: title is only awarded once every club has played its full schedule, and
#: head-to-head points need a schedule this scorer is not given. Regulation wins
#: are used when the feed carries them and skipped when it does not, which is
#: why goal differential is kept as the last real step.
TIEBREAKERS = ("regulation wins", "goal differential")

# --- skaters --------------------------------------------------------------
PTS_GOAL = 3.0
PTS_ASSIST = 2.0
PTS_SHOT = 0.5
PTS_PLUS_MINUS = 1.0

# --- goalies (scored, but not normalized) ---------------------------------
PTS_GOALIE_WIN = 4.0
PTS_SHUTOUT = 3.0
PTS_SAVE = 0.1
PTS_GOAL_AGAINST = -1.0

SKATER_ROLE = "Skater"
GOALIE_ROLE = "Goalie"


def score_skaters(df: pd.DataFrame,
                  standings: pd.DataFrame | None = None,
                  pool: bool = True) -> pd.DataFrame:
    """Season points per skater. All components are counting stats.

    ``standings`` is optional and changes no score. It carries how many games
    each club has played, which is what turns a games-played figure into a
    fact: fifty of eighty-two is a season interrupted and fifty of fifty is the
    league in January, and the first number alone cannot tell them apart.

    ``pool`` keeps only positive totals, as the benchmark pool does. Live
    scoring passes ``False``: Auston Matthews's first two games netted 0.0, he
    was dropped, and his slot went on counting the 0.1 his first game had made.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "team": resolve_str(df, ["teamAbbrevs", "team_abbrevs", "team"]),
            "season": resolve_num(df, ["season", "season_id", "seasonId"], required=True).astype(int),
            "player": resolve_str(
                df, ["player", "skater_full_name", "skaterFullName", "playerName"], required=True
            ),
            "player_id": resolve_str(df, ["player_id", "playerId"]),
            "games_played": resolve_num(df, ["games_played", "gamesPlayed"]),
            "goals": resolve_num(df, ["goals"]),
            "assists": resolve_num(df, ["assists"]),
            "shots": resolve_num(df, ["shots"]),
            "plus_minus": resolve_num(df, ["plus_minus", "plusMinus"]),
        }
    )
    work["total_points"] = (
        work["goals"] * PTS_GOAL
        + work["assists"] * PTS_ASSIST
        + work["shots"] * PTS_SHOT
        + work["plus_minus"] * PTS_PLUS_MINUS
    )
    work["league"] = "NHL"
    work["role"] = SKATER_ROLE
    work["team_games"] = _their_clubs_games(work, standings)
    # The caller's phase label, carried rather than matched back afterwards.
    # The frame is filtered and renumbered on the next line, so a caller
    # reindexing `_phase` onto the result would take the wrong rows the moment
    # one skater scores nothing -- and every row after him would have his
    # neighbour's phase, counting playoff production as regular season.
    if "_phase" in df.columns:
        work["_phase"] = df["_phase"].to_numpy()
    if not pool:
        # A playoff line with no games in it is no line at all.
        played = work["games_played"].fillna(0) > 0
        return work[played].reset_index(drop=True)
    return work[work["total_points"] > 0].reset_index(drop=True)


#: What a skater is scored on, and so what his playoffs must also show. Games
#: are deliberately absent: `split_phases` already reports them per phase, and
#: a second column counting the same thing would be one more figure to keep in
#: step.
SKATER_COUNTED = ["goals", "assists", "shots", "plus_minus"]


def score_skater_phases(raw: pd.DataFrame, standings: pd.DataFrame | None = None,
                        postseason: bool = True, pool: bool = True) -> pd.DataFrame:
    """Season totals per skater with the playoffs paid as a bonus.

    ``raw`` is the regular-season and playoff skater pulls together, each row
    labelled ``_phase`` "reg" or "post"; a frame with no label is all regular
    season. The playoffs are credited as a rate at the NHL's share of a season
    (``whul.scoring.postseason``), over the games each skater's club played.
    ``postseason=False`` scores the regular season alone, which is what a
    benchmark is built from.
    """
    from whul.scoring.postseason import (
        POSTSEASON, REGULAR, RULES, apply_bonus, phase_totals, regular_totals,
        split_phases,
    )

    scored = score_skaters(raw, standings, pool=pool)
    if scored.empty:
        return scored
    # From the scored frame, not reindexed off `raw`: the scorer drops skaters
    # who earned nothing and renumbers, so matching by position afterwards
    # mislabelled the phase of every row after the first such skater.
    phase = scored["_phase"] if "_phase" in scored.columns else None
    scored["phase"] = (
        phase.map({"reg": REGULAR, "post": POSTSEASON}).fillna(REGULAR)
        if phase is not None else REGULAR
    )
    keys = ["season", "player"]
    phases = split_phases(
        scored, keys, "total_points", "games_played", scored["phase"])
    # April's figures, kept apart and labelled as such. The playoff request is
    # its own call, and the rows were once reduced to points and games one
    # line later, leaving the profile's playoff boxes with nothing to hold.
    counting = regular_totals(scored, keys, SKATER_COUNTED, scored["phase"])
    post_counting = phase_totals(
        scored, keys, SKATER_COUNTED, scored["phase"], POSTSEASON, prefix="post_")
    # Facts about him and his club, identical on all his rows, carried through
    # the groupby rather than left behind by it: a column the aggregate never
    # mentions is a column the page reads as unknown. The club's games and his
    # club are his regular season's, which is what the heading they feed is
    # about.
    carried = {c: "max" for c in ("team_games",) if c in scored.columns}
    regular = scored[scored["phase"] == REGULAR]
    carried.update({c: "last" for c in ("team", "player_id")
                    if c in scored.columns})
    facts = ((regular if not regular.empty else scored)
             .groupby(keys, as_index=False).agg(carried)) if carried else None
    out = phases.merge(counting, on=keys, how="left").merge(
        post_counting, on=keys, how="left")
    if facts is not None:
        out = out.merge(facts, on=keys, how="left")
    out = out.merge(club_playoff_games(scored, keys), on=keys, how="left")
    out = apply_bonus(out, RULES["NHL"] if postseason else None)
    for column in SKATER_COUNTED + [f"post_{c}" for c in SKATER_COUNTED]:
        if column in out.columns:
            out[column] = out[column].fillna(0)
    out["league"] = "NHL"
    out["role"] = SKATER_ROLE
    return out


def club_playoff_games(scored: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """How many playoff games each skater's club played, per skater.

    A playoff rate is taken over his club's games rather than his own
    appearances (``whul.scoring.postseason``). The playoff skater pull is the
    whole league's, so the count is read off it: the most games any skater of
    his club played. Somebody on every roster plays every game, so this is
    the club's count without a second request; blank where the row names no
    club, and his own games stand in.

    ``scored`` is ``score_skaters`` output with the caller's ``_phase``.
    """
    from whul.scoring.postseason import TEAM_GAMES_COLUMN

    if (scored is None or scored.empty or "_phase" not in scored.columns
            or "team" not in scored.columns):
        return pd.DataFrame(columns=keys + [TEAM_GAMES_COLUMN])
    post = scored[scored["_phase"] == "post"]
    post = post[post["team"].fillna("").astype(str).str.strip() != ""]
    if post.empty:
        return pd.DataFrame(columns=keys + [TEAM_GAMES_COLUMN])
    most = post.groupby(["season", "team"])["games_played"].max()
    counted = post.assign(**{TEAM_GAMES_COLUMN: [
        most.get((season, team)) for season, team in zip(post["season"], post["team"])
    ]})
    return counted.groupby(keys, as_index=False)[TEAM_GAMES_COLUMN].max()


def _their_clubs_games(work: pd.DataFrame,
                       standings: pd.DataFrame | None) -> pd.Series:
    """How many games each skater's club has played.

    The skater endpoint names his club as "WPG" and the team endpoint calls it
    "Winnipeg Jets" and carries no abbreviation, so those two cannot be joined
    at all. The standings payload carries both spellings and the games played
    on one row, and is fetched every run already.

    A traded skater's field holds every club he played for -- "COL,CAR,DAL" --
    and takes the largest of their counts rather than their sum. Summing says a
    player traded in October had a hundred and sixty games available to him;
    the largest is what the season had reached wherever he was, which is the
    denominator the figure is for. It is still an approximation, and the only
    one here: nothing in either feed says on which date he moved.
    """
    blank = pd.Series(float("nan"), index=work.index)
    if standings is None or standings.empty:
        return blank
    if not {"season", "abbrev", "team_games"} <= set(standings.columns):
        return blank
    played = {
        (int(row.season), str(row.abbrev)): float(row.team_games)
        for row in standings.itertuples()
        if str(row.abbrev) and row.team_games == row.team_games
    }
    if "team" not in work.columns:
        return blank

    def most(season, named) -> float:
        clubs = [c.strip() for c in str(named or "").replace("/", ",").split(",")]
        counts = [played[(int(season), c)] for c in clubs
                  if (int(season), c) in played]
        return max(counts) if counts else float("nan")

    return pd.Series(
        [most(s, t) for s, t in zip(work["season"], work["team"])],
        index=work.index,
    )


def score_goalies(df: pd.DataFrame) -> pd.DataFrame:
    """Season points per goalie.

    Retained because the R script computes it, but goalies hold no roster slots
    and are excluded from normalization, so this feeds nothing downstream.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    work = pd.DataFrame(
        {
            "season": resolve_num(df, ["season", "season_id", "seasonId"], required=True).astype(int),
            "player": resolve_str(
                df, ["player", "goalie_full_name", "goalieFullName", "playerName"], required=True
            ),
            "games_played": resolve_num(df, ["games_played", "gamesPlayed"]),
            "wins": resolve_num(df, ["wins"]),
            "shutouts": resolve_num(df, ["shutouts"]),
            "saves": resolve_num(df, ["saves"]),
            "goals_against": resolve_num(df, ["goals_against", "goalsAgainst"]),
        }
    )
    work["total_points"] = (
        work["wins"] * PTS_GOALIE_WIN
        + work["shutouts"] * PTS_SHUTOUT
        + work["saves"] * PTS_SAVE
        + work["goals_against"] * PTS_GOAL_AGAINST
    )
    work["league"] = "NHL"
    work["role"] = GOALIE_ROLE
    return work[work["total_points"] > 0].reset_index(drop=True)


def score_players(skaters: pd.DataFrame, goalies: pd.DataFrame | None = None) -> pd.DataFrame:
    """Skaters only -- the league does not roster goalies."""
    return score_skaters(skaters)


def _division_champs(
    work: pd.DataFrame, divisions: pd.DataFrame | None
) -> pd.Series:
    """The club atop each division, per season, once the season has finished.

    Most standings points in the division, ties broken on regulation wins and
    then goal differential -- the NHL's own order, minus the steps a season
    summary cannot answer. Clubs level after both share the title: they were
    not separated by anything that happened on the ice, and picking one would
    be inventing a result rather than reading one.

    Two things return zeroes rather than a guess, and both matter more than the
    title does:

    * **No divisions.** A club's own totals never say who it was racing.
    * **An unfinished season.** A standing is only a final standing once every
      club in the division has played its schedule. Awarding on games in hand
      would pay ten points in November to whoever started well and take them
      back in March, which is the bug this scorer had in the other direction
      for a year -- and the same one ``whul.scoring.mlb`` fixed by refusing to
      award a title mid-season.
    """
    zero = pd.Series(0, index=work.index, dtype=int)
    if divisions is None or divisions.empty:
        return zero
    if not {"season", "team", "division"} <= set(divisions.columns):
        return zero

    lookup = divisions.assign(
        season=pd.to_numeric(divisions["season"], errors="coerce"),
        team=divisions["team"].astype(str),
        division=divisions["division"].astype(str),
    ).dropna(subset=["season"])
    lookup["season"] = lookup["season"].astype(int)

    placed = work[["season", "team"]].copy()
    placed["team"] = placed["team"].astype(str)
    placed = placed.merge(
        lookup[["season", "team", "division"]].drop_duplicates(["season", "team"]),
        on=["season", "team"],
        how="left",
    )
    for column in ("standings_points", "regulation_wins", "goal_diff",
                   "games_played"):
        placed[column] = pd.to_numeric(work[column], errors="coerce").to_numpy()
    placed.index = work.index

    champ = zero.copy()
    for (season, division), block in placed.dropna(subset=["division"]).groupby(
        ["season", "division"]
    ):
        full = scheduled_games("NHL", int(season))
        if full is None or (block["games_played"] < full).any():
            continue
        leaders = block.index
        for column in ("standings_points", "regulation_wins", "goal_diff"):
            best = block.loc[leaders, column].max()
            leaders = leaders[block.loc[leaders, column] == best]
            if len(leaders) == 1:
                break
        champ.loc[leaders] = 1
    return champ


def team_game_points(games: pd.DataFrame) -> pd.Series:
    """What each club game earned on its own, from ``load_team_games`` rows.

    A regular-season win, an overtime loss and the goal margin, and a playoff
    win, at ``score_teams``' weights. A playoff berth, the series and a
    division title are the season's and left to the panel. Unlifted: the
    84-game season is carried by the benchmark (``whul.scoring.schedule``).
    """
    if games is None or games.empty:
        return pd.Series(dtype=float)
    wins = resolve_num(games, ["wins"])
    otl = resolve_num(games, ["otLosses", "ot_losses"])
    margin = (resolve_num(games, ["goalsFor", "goals_for"])
              - resolve_num(games, ["goalsAgainst", "goals_against"]))
    kind = resolve_num(games, ["game_type", "gameTypeId"], default=2)
    regular = wins * PTS_WIN + otl * PTS_OTL + margin * PTS_GOAL_DIFF
    playoff = wins * PTS_PLAYOFF_WIN
    return regular.where(kind != 3, playoff).astype(float)


def score_teams(
    regular: pd.DataFrame,
    playoffs: pd.DataFrame | None = None,
    scale_regular_season: bool | None = None,
    divisions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Season points per team, blending regular-season and playoff summaries.

    ``scale_regular_season`` lifts wins, overtime losses and goal differential to
    the current schedule length, leaving the playoff and division terms alone.
    By default each season is lifted by what its own length needs
    (``schedule.lift_for``): the 82-game history the benchmark is drawn from
    reaches 84 games, and a season already played at 84 is scored as played.
    It used to lift every season, which inflated each 2026-27 club by the two
    games it had actually played. ``True`` lifts every season and ``False``
    none, for tests that need one or the other.

    ``divisions`` maps a team to its division for a season -- columns
    ``season``, ``team``, ``division``. Without it no division title is awarded,
    which is the honest answer: a season summary says how a club did, not who
    it was competing with.
    """
    if regular is None or regular.empty:
        return pd.DataFrame()


    work = pd.DataFrame(
        {
            "season": resolve_num(regular, ["season", "season_id", "seasonId"], required=True).astype(int),
            "team": resolve_str(regular, ["team", "team_full_name", "teamFullName"], required=True),
            "team_id": resolve_str(regular, ["team_id", "teamId"]),
            "games_played": resolve_num(regular, ["games_played", "gamesPlayed"]),
            "reg_wins": resolve_num(regular, ["wins"]),
            "reg_otl": resolve_num(regular, ["ot_losses", "otLosses"]),
            # A tiebreaker, and the feed may not carry it. Absent, it resolves
            # to zero for every club and simply does not separate anyone, which
            # is why goal differential follows it.
            "regulation_wins": resolve_num(
                regular, ["regulation_wins", "regulationWins"]
            ),
            "goals_for": resolve_num(regular, ["goals_for", "goalsFor"]),
            "goals_against": resolve_num(regular, ["goals_against", "goalsAgainst"]),
        }
    )
    work["goal_diff"] = work["goals_for"] - work["goals_against"]
    if scale_regular_season is None:
        factor = work["season"].map(lambda season: lift_for("NHL", season))
    else:
        factor = factor_for("NHL") if scale_regular_season else 1.0
    # Unscored, and carried anyway: a record is the three numbers the sport
    # prints, and "Wins 45" on its own cannot say whether the other thirty-seven
    # were lost or have not been played. Regulation losses are what is left --
    # the feed serves wins and overtime losses, and the standings line is
    # wins, losses, overtime losses.
    work["reg_losses"] = (
        work["games_played"] - work["reg_wins"] - work["reg_otl"]
    ).clip(lower=0)

    if playoffs is not None and not playoffs.empty:
        post = pd.DataFrame(
            {
                "season": resolve_num(playoffs, ["season", "season_id", "seasonId"], required=True).astype(int),
                "team": resolve_str(playoffs, ["team", "team_full_name", "teamFullName"], required=True),
                "playoff_games": resolve_num(playoffs, ["games_played", "gamesPlayed"]),
                "playoff_wins": resolve_num(playoffs, ["wins"]),
            }
        )
        work = work.merge(post, on=["season", "team"], how="left")
    else:
        work["playoff_games"] = 0.0
        work["playoff_wins"] = 0.0

    work[["playoff_games", "playoff_wins"]] = work[["playoff_games", "playoff_wins"]].fillna(0.0)
    work["made_playoffs"] = (work["playoff_games"] > 0).astype(int)
    work["series_wins"] = (work["playoff_wins"] // WINS_PER_SERIES).astype(int)
    work["standings_points"] = (
        work["reg_wins"] * STANDINGS_WIN + work["reg_otl"] * STANDINGS_OTL
    )
    work["is_division_champ"] = _division_champs(work, divisions)

    work["total_points"] = (
        work["reg_wins"] * PTS_WIN * factor
        + work["reg_otl"] * PTS_OTL * factor
        + work["goal_diff"] * PTS_GOAL_DIFF * factor
        + work["made_playoffs"] * PTS_PLAYOFF_APP
        + work["playoff_wins"] * PTS_PLAYOFF_WIN
        + work["series_wins"] * PTS_SERIES_WIN
        + work["is_division_champ"] * PTS_DIV_CHAMP
    )
    work["league"] = "NHL"
    work["schedule_factor"] = factor
    return work.sort_values(["season", "total_points"], ascending=[True, False]).reset_index(
        drop=True
    )
