"""NCAA team scoring -- ports of the five NCAA R scripts.

All five NCAA categories are **team slots only**; there are no NCAA player slots,
so nothing here needs box scores. Game results plus conference affiliation are
enough, which is why these leagues are cheap to scrape: one scoreboard request
per date rather than one per game.

Three shapes cover the five leagues:

* **Football** -- wins, blowouts, conference record and title, CFP appearance.
* **Basketball** (men's and women's, identical but for the minimum-games filter)
  -- conference tournament and March Madness on top of the regular season.
* **Diamond** (baseball and softball) -- wins, run differential, and flat
  postseason series milestones.

Conference affiliation is load-bearing in football and basketball: conference
wins are scored directly, and the regular-season title is split among
co-champions. A feed that omits conference data cannot score these leagues.

Every scorer takes an optional ``eligible`` set of team names. A scoreboard
request returns games *involving* a listed team, so the opponent may be from a
lower division -- those teams would otherwise enter the pool with one or two
games apiece and drag the benchmark down. The R scripts approximated this with a
minimum-games filter; naming the division's members is exact, and leaves genuine
short seasons intact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from whul.scoring.base import DATE_COLUMNS, resolve_num, resolve_str, settled_seasons

# --- football -------------------------------------------------------------
#: A blowout is harder to achieve against a conference opponent or in the
#: postseason, where the field is stronger, so the bar is lower there.
FB_BIG_WIN_CONF = 13
FB_BIG_WIN_NONCONF = 20
FB_WEIGHTS = {
    "wins": 10.0, "big_wins": 2.0, "conf_wins": 2.0,
    "conf_title_win": 6.0, "playoff_app": 10.0, "playoff_wins": 15.0,
    "point_diff": 0.05,
}
FB_REG_CHAMP_POOL = 6.0  # split evenly among co-champions
FB_PLAYOFF_PATTERN = r"Playoff|CFP|Rose|Sugar|Orange|Cotton|Fiesta|Peach"
FB_TITLE_PATTERN = r"Championship"

# --- basketball -----------------------------------------------------------
BB_BIG_WIN_NONCONF = 25
BB_BIG_WIN_CONF = 15
BB_WEIGHTS = {
    "reg_wins": 2.0, "big_wins": 1.5, "conf_wins": 1.0,
    "conf_tourney_wins": 2.0, "conf_tourney_champ": 6.0,
    "mm_appearance": 8.0, "mm_wins": 5.0, "point_diff": 0.03,
}
BB_REG_CHAMP_POOL = 8.0
MM_PATTERN = (
    r"NCAA Tournament|March Madness|First Four|First Round|Second Round|"
    r"Sweet 16|Elite Eight|Final Four|National Championship"
)
#: The names only the national tournament uses. "First Round" is also every
#: November invitational's, so it counts only in the postseason months.
MM_OWN_PATTERN = (
    r"NCAA|March Madness|First Four|Sweet 16|Elite Eight|Final Four|"
    r"Basketball Championship"
)
MM_MONTHS = (3, 4)
CONF_TOURNEY_PATTERN = r"Tournament"
NOT_MM_PATTERN = r"NCAA|March Madness|Basketball Championship"
#: The other postseason tournaments. Neither a conference tournament -- "National
#: Invitation Tournament" has the word in it -- nor the NCAA tournament, which
#: every postseason game not otherwise named was taken to be. Not scored.
OTHER_POSTSEASON_PATTERN = (
    r"\bW?NIT\b|National Invitation|\bW?BIT\b|Basketball Invitation|"
    r"\bCBI\b|\bCIT\b|Basketball Crown|Basketball Classic"
)

#: A round's name, and where it sits: counted from the first round, or back from
#: the final. Quarterfinal and semifinal are tested before the final, whose
#: name they contain.
ROUND_NAMES: tuple[tuple[str, str, int], ...] = (
    (r"quarter", "end", 2),
    (r"semi", "end", 1),
    (r"championship|\bfinals?\b|title game", "end", 0),
    (r"\b(?:opening|first|1st)\s+round|play-?in", "start", 1),
    (r"\b(?:second|2nd)\s+round", "start", 2),
    (r"\b(?:third|3rd)\s+round", "start", 3),
    (r"\b(?:fourth|4th)\s+round", "start", 4),
)

#: Each bracket's shape, as the league admin states it -- the conference
#: tournaments and the College Football Playoff: how many rounds it has, and
#: where its rounds are not called the usual names, what they are called. See
#: the file's own header.
CONF_TOURNEY_FORMATS = (
    Path(__file__).resolve().parent.parent / "data" / "ncaa_conf_tournaments.csv")

SEASON_TYPE_REGULAR = 2
SEASON_TYPE_POST = 3

# --- diamond --------------------------------------------------------------
DIAMOND_REG_WIN = 2.0
DIAMOND_RUN_DIFF = 0.05
PTS_SERIES_REGIONAL = 5.0
PTS_SERIES_SUPER = 6.0
PTS_SERIES_CWS = 8.0
REGIONAL_PATTERN = r"Regional"
SUPER_PATTERN = r"Super Regional"
CWS_PATTERN = r"College World Series|Women's College World Series|WCWS|CWS"


@dataclass(frozen=True)
class DiamondRules:
    """Baseball and softball differ only in the College World Series threshold."""

    league: str
    cws_wins_for_title: int


DIAMOND = {
    "NCAA Baseball": DiamondRules("NCAA Baseball", 4),
    "NCAA Softball": DiamondRules("NCAA Softball", 5),
}


def _restrict(games: pd.DataFrame, eligible: set[str] | None) -> pd.DataFrame:
    """Keep only the division's own teams, when the caller knows who they are."""
    if not eligible:
        return games
    return games[games["team"].isin(eligible)]


def _team_games(schedule: pd.DataFrame) -> pd.DataFrame:
    """One row per team per completed game, with the opponent's conference."""
    if schedule is None or schedule.empty:
        return pd.DataFrame()

    base = pd.DataFrame(
        {
            "season": resolve_num(schedule, ["season"], required=True).astype(int),
            "season_type": resolve_num(schedule, ["season_type"], default=SEASON_TYPE_REGULAR),
            "notes": resolve_str(schedule, ["notes", "notes_headline"]).fillna(""),
            "home_team": resolve_str(schedule, ["home_team"], required=True),
            "away_team": resolve_str(schedule, ["away_team"], required=True),
            "home_conference": resolve_str(schedule, ["home_conference"]),
            "away_conference": resolve_str(schedule, ["away_conference"]),
            "home_score": resolve_num(schedule, ["home_score"], default=float("nan")),
            "away_score": resolve_num(schedule, ["away_score"], default=float("nan")),
            "game_date": resolve_str(schedule, list(DATE_COLUMNS)),
            # A bracket game's round where the note could not say it, worked
            # out from the bracket by `whul.brackets.place_rounds`.
            "bracket_round": resolve_num(schedule, ["bracket_round"],
                                         default=float("nan")),
        }
    )
    if "completed" in schedule.columns:
        base = base[schedule["completed"].fillna(False).astype(bool).to_numpy()]
    base = base[base["home_score"].notna() & base["away_score"].notna()]

    sides = []
    for side, other in (("home", "away"), ("away", "home")):
        sides.append(
            pd.DataFrame(
                {
                    "season": base["season"],
                    "season_type": base["season_type"],
                    "notes": base["notes"],
                    "team": base[f"{side}_team"],
                    "opp_team": base[f"{other}_team"],
                    "conference": base[f"{side}_conference"],
                    "opp_conference": base[f"{other}_conference"],
                    "points_for": base[f"{side}_score"],
                    "points_against": base[f"{other}_score"],
                    "game_date": base["game_date"],
                    "bracket_round": base["bracket_round"],
                }
            )
        )
    games = pd.concat(sides, ignore_index=True)
    # Before `is_conf_game` is decided, since that is what the override is for.
    games = _apply_overrides(games)
    games["margin"] = games["points_for"] - games["points_against"]
    games["is_win"] = games["margin"] > 0
    games["is_reg"] = games["season_type"] == SEASON_TYPE_REGULAR
    games["is_post"] = games["season_type"] == SEASON_TYPE_POST
    has_both = games["conference"].notna() & games["opp_conference"].notna()
    has_both &= (games["conference"] != "") & (games["opp_conference"] != "")
    games["is_conf_game"] = has_both & (games["conference"] == games["opp_conference"])
    return games


#: Teams the league scores as a member of a conference they do not belong to.
#:
#: Notre Dame plays football as an independent. ESPN reports them that way, and
#: an independent has no conference games, so the conference-wins term is zero
#: for them no matter how the season goes -- which scores a genuinely strong
#: programme short against a field where everyone else can earn it. The league's
#: rule is to treat them as ACC, which is where their other sports play and
#: where their football scheduling agreement points.
#:
#: Keyed on the conference *value the feed uses*, resolved from the data rather
#: than written down: the id is ESPN's and could change, and a wrong one would
#: group Notre Dame with some other conference silently. `_apply_overrides`
#: takes the conference of the named exemplar wherever it appears in the same
#: frame, so the mapping is only ever as right as the feed itself.
CONFERENCE_OVERRIDES = {
    "Notre Dame Fighting Irish": "Miami Hurricanes",
}

#: ...and are never credited with winning it.
#:
#: An independent scored as a conference member could top that conference's
#: table on a technicality and take a title it is not eligible for. The league's
#: rule is explicit: treat them as ACC for scoring, award no conference title.
#: Kept as its own set rather than inferred from the override, because the two
#: are different decisions -- a future override might be a real member.
NO_CONFERENCE_TITLE = frozenset({"Notre Dame Fighting Irish"})


def _apply_overrides(games: pd.DataFrame) -> pd.DataFrame:
    """Put an independent into the conference the league scores it in.

    Both its own rows and its opponents' view of it: a conference game is a
    game whose two sides share a conference, so moving one side without the
    other would leave the ACC's games against Notre Dame counting for Notre
    Dame and not for the ACC team, which is a table that does not add up.
    """
    if games.empty or "team" not in games.columns:
        return games
    for team, exemplar in CONFERENCE_OVERRIDES.items():
        rows = games["team"] == team
        if not rows.any():
            continue
        borrowed = games.loc[games["team"] == exemplar, "conference"]
        borrowed = borrowed[borrowed.astype(str) != ""]
        if borrowed.empty:
            # Nothing to copy from, so leave it alone rather than invent a
            # conference. Scoring short is recoverable; a wrong conference is
            # a title awarded to the wrong programme.
            continue
        value = borrowed.iloc[0]
        games.loc[rows, "conference"] = value
        games.loc[games["opp_team"] == team, "opp_conference"] = value
    return games


def _matches(series: pd.Series, pattern: str) -> pd.Series:
    return series.str.contains(pattern, case=False, regex=True, na=False)


def _split_conference_title(summary: pd.DataFrame, pool: float,
                            settled: set[int] | None = None) -> pd.Series:
    """Points for the regular-season conference title, split among co-champions.

    A shared title is worth proportionally less to each holder, as the R scripts
    have it -- two co-champions take half the pool each.

    ``settled`` is the seasons whose games have all been played. A title is a
    season outcome and nobody holds one in September: without this, whoever won
    the first conference game of the year took the whole pool, and Miami was
    carrying six points for an ACC title on a 1-0 conference record in week
    two. A season not in the set scores nothing here and is credited in full
    once its last game is played.

    Passing nothing settles nothing, which is the safe direction: withholding a
    title that was won is a visible undercount and awarding one that was not is
    the invisible kind.
    """
    # Conference *record*, not conference wins alone. A conference schedule is
    # not balanced -- teams play different numbers of conference games -- so a
    # team at 8-0 and a team at 8-1 both have eight conference wins, and both
    # were being crowned and splitting the pool.
    #
    # Most wins, then fewest losses among those tied, which is how a standings
    # table reads. Not a win *rate*: a rate rewards a short schedule, and a
    # team misfiled into a conference with one game in it would go 1-0 and tie
    # a champion at 8-0 for the top of the table.
    #
    # Not the official standing either: a conference breaks a genuine tie by
    # its own rules, and this shares the title instead. Sharing a title that
    # was won outright costs the winner half a pool; picking the wrong team
    # costs it the lot and pays somebody who won nothing.
    wins = pd.to_numeric(summary["conf_wins"], errors="coerce").fillna(0)
    played = pd.to_numeric(
        summary["conf_games"], errors="coerce"
    ).fillna(wins) if "conf_games" in summary.columns else wins
    losses = (played - wins).clip(lower=0)
    by = [summary["season"], summary["conference"]]
    most = wins.groupby(by).transform("max")
    fewest = losses.where(wins == most).groupby(by).transform("min")
    is_champ = (wins == most) & (losses == fewest) & (wins > 0)
    is_champ &= summary["season"].astype(int).isin(settled or set())
    # An independent scored as a conference member could top that conference's
    # table on a technicality and take a title it is not eligible for. Excluded
    # *after* the maximum is taken, not before: a team that is not eligible for
    # the title still played the games, and dropping it earlier would hand the
    # title to whoever finished behind it rather than leaving it unwon.
    if "team" in summary.columns:
        is_champ &= ~summary["team"].isin(NO_CONFERENCE_TITLE)
    ties = is_champ.groupby([summary["season"], summary["conference"]]).transform("sum")
    return (is_champ.astype(float) * pool / ties.where(ties > 0, 1)).fillna(0.0)


class MissingConference(ValueError):
    """Completed games arrived with no conference on any of them.

    Football scoring cannot proceed without it, and the failure has to be loud:
    silently returning nothing is indistinguishable from a week with no games,
    which is how a whole league can go unscored without anyone noticing.
    """


def score_football(
    schedule: pd.DataFrame, eligible: set[str] | None = None
) -> pd.DataFrame:
    """NCAAF team scoring."""
    games = _restrict(_team_games(schedule), eligible)
    if games.empty:
        return pd.DataFrame()

    named = games[games["conference"].notna() & (games["conference"] != "")]
    if named.empty:
        # No conference data means conference wins and the title split cannot be
        # scored at all. Returning an empty frame here reads downstream as "the
        # league has not played yet", which is the opposite of what happened --
        # it played, and the feed described it without conferences. Say so.
        raise MissingConference(
            f"{len(games)} completed game(s) arrived with no conference on any "
            "team, so conference wins and the regular-season title cannot be "
            "scored. This is a feed problem, not an empty week."
        )
    games = named
    games["is_playoff"] = games["is_post"] & _matches(games["notes"], FB_PLAYOFF_PATTERN)
    tougher_field = games["is_conf_game"] | games["is_post"]
    games["is_big_win"] = games["is_win"] & (
        (tougher_field & (games["margin"] >= FB_BIG_WIN_CONF))
        | (~tougher_field & (games["margin"] >= FB_BIG_WIN_NONCONF))
    )
    # The playoff's own final is a championship too, and not a conference's.
    games["is_conf_title"] = (games["is_post"] & ~games["is_playoff"]
                              & _matches(games["notes"], FB_TITLE_PATTERN))

    summary = games.groupby(["season", "team", "conference"], as_index=False).apply(
        lambda g: pd.Series(
            {
                "games_played": len(g),
                "wins": int(g["is_win"].sum()),
                # Unscored, and carried anyway: a record is two numbers, and
                # a win count on its own cannot say whether the rest were lost
                # or have not been played.
                "losses": int((g["margin"] < 0).sum()),
                "big_wins": int(g["is_big_win"].sum()),
                "conf_wins": int((g["is_win"] & g["is_conf_game"]).sum()),
                "conf_games": int(g["is_conf_game"].sum()),
                "point_diff": float(g["margin"].sum()),
                "conf_title_win": int((g["is_win"] & g["is_conf_title"]).sum()),
                "playoff_app": int(g["is_playoff"].any()),
                "playoff_wins": int((g["is_win"] & g["is_playoff"]).sum()),
            }
        ),
        include_groups=False,
    ).reset_index(drop=True)
    if summary.empty:
        return summary

    summary["pts_reg_champ"] = _split_conference_title(
        summary, FB_REG_CHAMP_POOL, settled_seasons(schedule))
    # The twelve-team playoff's top four seeds skip its first round, and are
    # paid it as a playoff win.
    summary["playoff_byes"] = _bracket_byes(summary, games, "NCAAF", "is_playoff")
    summary["total_points"] = (
        sum(summary[c] * w for c, w in FB_WEIGHTS.items()) + summary["pts_reg_champ"]
        + summary["playoff_byes"] * FB_WEIGHTS["playoff_wins"]
    )
    summary["league"] = "NCAAF"
    return summary.sort_values(["season", "total_points"], ascending=[True, False]).reset_index(
        drop=True
    )


def score_basketball(
    schedule: pd.DataFrame, league: str = "NCAAM", eligible: set[str] | None = None
) -> pd.DataFrame:
    """NCAAM and NCAAW team scoring -- the two are scored identically."""
    games = _restrict(_team_games(schedule), eligible)
    if games.empty:
        return pd.DataFrame()

    games["is_big_win"] = games["is_win"] & (
        ((~games["is_conf_game"]) & (games["margin"] >= BB_BIG_WIN_NONCONF))
        | (games["is_conf_game"] & (games["margin"] >= BB_BIG_WIN_CONF))
    )
    other = _matches(games["notes"], OTHER_POSTSEASON_PATTERN)
    # A conference tournament game is one between two members of the
    # conference, where the feed does not already call it postseason: not
    # every feed does, and a game in the conference tournament is not a
    # regular-season conference game however it is labelled.
    games["is_conf_tourney"] = _conf_tourney(games, other)
    # Anything in the postseason that is not a conference tournament game, nor
    # one of the other invitationals, is treated as the national tournament,
    # which catches rounds the notes do not name explicitly. A conference
    # tournament's "First Round" is not March Madness's.
    month = pd.to_datetime(games["game_date"], errors="coerce").dt.month
    in_march = month.isin(MM_MONTHS) | month.isna()
    games["is_mm"] = ~games["is_conf_tourney"] & ~other & (
        games["is_post"] | _matches(games["notes"], MM_OWN_PATTERN)
        | (_matches(games["notes"], MM_PATTERN) & in_march))
    games["is_reg"] &= ~games["is_conf_tourney"] & ~games["is_mm"] & ~other
    games["is_ct_title"] = games["is_conf_tourney"] & (
        games["notes"].map(_round_from_end) == 0)

    summary = games.groupby(["season", "team", "conference"], as_index=False).apply(
        lambda g: pd.Series(
            {
                "games_played": len(g),
                "reg_wins": int((g["is_win"] & g["is_reg"]).sum()),
                # Unscored, and carried anyway: a record is two numbers, and
                # a win count on its own cannot say whether the rest were lost
                # or have not been played.
                "reg_losses": int(((g["margin"] < 0) & g["is_reg"]).sum()),
                "big_wins": int(g["is_big_win"].sum()),
                "conf_wins": int((g["is_win"] & g["is_reg"] & g["is_conf_game"]).sum()),
                "conf_games": int((g["is_reg"] & g["is_conf_game"]).sum()),
                "point_diff": float(g.loc[g["is_reg"], "margin"].sum()),
                "conf_tourney_wins": int((g["is_win"] & g["is_conf_tourney"]).sum()),
                "conf_tourney_champ": int((g["is_win"] & g["is_ct_title"]).any()),
                "mm_appearance": int(g["is_mm"].any()),
                "mm_wins": int((g["is_win"] & g["is_mm"]).sum()),
            }
        ),
        include_groups=False,
    ).reset_index(drop=True)
    if summary.empty:
        return summary

    summary["pts_reg_champ"] = _split_conference_title(
        summary, BB_REG_CHAMP_POOL, settled_seasons(schedule))
    summary["conf_tourney_byes"] = _bracket_byes(
        summary, games, league, "is_conf_tourney")
    summary["total_points"] = (
        sum(summary[c] * w for c, w in BB_WEIGHTS.items()) + summary["pts_reg_champ"]
        + summary["conf_tourney_byes"] * BB_WEIGHTS["conf_tourney_wins"]
    )
    summary["league"] = league
    return summary.sort_values(["season", "total_points"], ascending=[True, False]).reset_index(
        drop=True
    )


def _round_from_end(note: str) -> int | None:
    """How many rounds before the final a note's round is, where its name says
    so: the final 0, a semifinal 1, a quarterfinal 2."""
    for pattern, counted, position in ROUND_NAMES:
        if re.search(pattern, str(note), re.IGNORECASE):
            return position if counted == "end" else None
    return None


@dataclass(frozen=True)
class ConferenceTournament:
    """One conference tournament's bracket, as stated."""

    league: str
    season: int
    names: tuple[str, ...]    # what the conference is called in a note
    rounds: int               # rounds in the bracket, the final included
    round_names: tuple[str, ...] = ()   # in order, where not the usual ones

    def called(self, note: str, conference: str) -> bool:
        text = f" {note} "
        return any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text, re.IGNORECASE)
                   or n.casefold() == str(conference).casefold()
                   for n in self.names)

    def round_of(self, note: str) -> int | None:
        """Which round a game was, from 1; None where the note does not say."""
        # The longest first, so a stated "Final" does not claim a semifinal.
        for name in sorted(self.round_names, key=len, reverse=True):
            if name.casefold() in str(note).casefold():
                return self.round_names.index(name) + 1
        return round_number(note, self.rounds)


def round_number(note: str, rounds: int | None) -> int | None:
    """A round's number from its name, the first round being 1.

    A round counted from the start ("Second Round") needs nothing else. One
    counted back from the final ("Quarterfinal") needs to know how many rounds
    there are, and without that is None -- not a guess, because a quarterfinal
    is the second round of an eleven-team bracket and the third of a fifteen.
    """
    for pattern, counted, position in ROUND_NAMES:
        if re.search(pattern, str(note), re.IGNORECASE):
            if counted == "start":
                return position
            return rounds - position if rounds else None
    return None


def conference_tournaments(path: Path | None = None) -> list[ConferenceTournament]:
    """The stated brackets, from ``whul/data/ncaa_conf_tournaments.csv``."""
    path = path or CONF_TOURNEY_FORMATS
    if not path.exists():
        return []
    table = pd.read_csv(path, comment="#", dtype=str).fillna("")
    out = []
    for row in table.itertuples(index=False):
        try:
            rounds = int(row.rounds)
        except ValueError:
            continue
        out.append(ConferenceTournament(
            league=row.league.strip(), season=int(row.season),
            names=tuple(n.strip() for n in row.conference.split("|") if n.strip()),
            rounds=rounds,
            round_names=tuple(n.strip() for n in row.round_names.split("|") if n.strip()),
        ))
    return out


def _bracket_byes(summary: pd.DataFrame, games: pd.DataFrame, league: str,
                  column: str) -> pd.Series:
    """Rounds of a bracket a team skipped by its seed.

    A bye scores as though the team swept the round it skipped, as it does in
    every sport here (``whul.scoring.postseason.BYE_COUNTS_AS_SWEEP``): each
    round skipped is paid as a win in that bracket. A team's byes are the rounds
    before the first one it played, read off the round's name in the game's
    note and, where the name counts back from the final, the number of rounds
    the bracket has (``CONF_TOURNEY_FORMATS``). ``column`` flags the bracket's
    games.

    Paid once the team has played its first game, which is when the feed first
    says it is in the bracket, and whether or not it won it.

    Not derivable from the games alone: the live feed is the rostered teams' own
    schedules, so the rest of a bracket is never in it, and brackets do not
    follow from the number of teams -- fifteen teams play four rounds in one
    conference and five in another.
    """
    zero = pd.Series(0, index=summary.index, dtype=int)
    played = games[games[column]]
    if played.empty:
        return zero
    stated = [t for t in conference_tournaments() if t.league == league]
    first: dict[tuple[int, str], int] = {}
    unread: set[tuple[int, str]] = set()
    for row in played.itertuples(index=False):
        bracket = next((t for t in stated if t.season == int(row.season)
                        and t.called(row.notes, row.conference)), None)
        number = (bracket.round_of(row.notes) if bracket
                  else round_number(row.notes, None))
        if number is None and pd.notna(row.bracket_round):
            number = int(row.bracket_round)
        key = (int(row.season), str(row.team))
        if number is None:
            # Any game whose round cannot be read might be the first one, and
            # a later round read as the first would pay rounds that were
            # played. Nothing, rather than a guess.
            unread.add(key)
            continue
        first[key] = min(first.get(key, number), number)
    return pd.Series(
        [0 if (int(s), str(t)) in unread
         else max(first.get((int(s), str(t)), 1) - 1, 0)
         for s, t in zip(summary["season"], summary["team"])],
        index=summary.index, dtype=int)


def _conf_tourney(games: pd.DataFrame, other: pd.Series) -> pd.Series:
    """A conference tournament game: two members of the conference, a note
    that says tournament, and not one of the national ones.

    Not gated on the feed calling it postseason: not every feed does, and a
    game in the conference tournament is not a regular-season conference game
    however it is labelled.
    """
    return (
        (games["is_post"] | games["is_conf_game"])
        & _matches(games["notes"], CONF_TOURNEY_PATTERN)
        & ~_matches(games["notes"], NOT_MM_PATTERN)
        & ~other
    )


def score_diamond(
    schedule: pd.DataFrame,
    league: str = "NCAA Baseball",
    eligible: set[str] | None = None,
) -> pd.DataFrame:
    """NCAA Baseball and Softball team scoring."""
    rules = DIAMOND[league]
    games = _restrict(_team_games(schedule), eligible)
    if games.empty:
        return pd.DataFrame()

    # Super Regional must be tested before Regional, since it contains the word.
    games["is_super"] = _matches(games["notes"], SUPER_PATTERN)
    games["is_cws"] = _matches(games["notes"], CWS_PATTERN)
    games["is_regional"] = (
        _matches(games["notes"], REGIONAL_PATTERN) & ~games["is_super"] & ~games["is_cws"]
    )
    # A conference tournament is played for wins like the rest of the season:
    # the benchmark's feed calls every game regular season and counted them,
    # so a live feed that calls them postseason must not drop them.
    games["is_conf_tourney"] = _conf_tourney(
        games, _matches(games["notes"], OTHER_POSTSEASON_PATTERN)
        | games["is_regional"] | games["is_super"] | games["is_cws"])
    games["is_postseason"] = (
        (games["is_post"] & ~games["is_conf_tourney"])
        | games["is_regional"] | games["is_super"] | games["is_cws"])

    summary = games.groupby(["season", "team"], as_index=False).apply(
        lambda g: pd.Series(
            {
                "games_played": len(g),
                "reg_wins": int((g["is_win"] & ~g["is_postseason"]).sum()),
                # Unscored, and carried anyway: a record is two numbers, and
                # a win count on its own cannot say whether the rest were lost
                # or have not been played.
                "reg_losses": int(((g["margin"] < 0) & ~g["is_postseason"]).sum()),
                "run_diff": float(g.loc[~g["is_postseason"], "margin"].sum()),
                "regional_wins": int((g["is_win"] & g["is_regional"]).sum()),
                "super_wins": int((g["is_win"] & g["is_super"]).sum()),
                "cws_wins": int((g["is_win"] & g["is_cws"]).sum()),
            }
        ),
        include_groups=False,
    ).reset_index(drop=True)
    if summary.empty:
        return summary

    # The NCAA tournament has no byes -- every team plays a Regional -- but a
    # conference tournament does, and its rounds are paid as wins.
    summary["conf_tourney_byes"] = _bracket_byes(summary, games, league, "is_conf_tourney")
    summary["series_regional"] = (summary["regional_wins"] >= 3).astype(int)
    summary["series_super"] = (summary["super_wins"] >= 2).astype(int)
    summary["series_cws_champ"] = (summary["cws_wins"] >= rules.cws_wins_for_title).astype(int)

    summary["total_points"] = (
        (summary["reg_wins"] + summary["conf_tourney_byes"]) * DIAMOND_REG_WIN
        + summary["run_diff"] * DIAMOND_RUN_DIFF
        + summary["series_regional"] * PTS_SERIES_REGIONAL
        + summary["series_super"] * PTS_SERIES_SUPER
        + summary["series_cws_champ"] * PTS_SERIES_CWS
    )
    summary["league"] = league
    return summary.sort_values(["season", "total_points"], ascending=[True, False]).reset_index(
        drop=True
    )


SCORERS = {
    "NCAAF": score_football,
    "NCAAM": lambda s, eligible=None: score_basketball(s, "NCAAM", eligible),
    "NCAAW": lambda s, eligible=None: score_basketball(s, "NCAAW", eligible),
    "NCAA Baseball": lambda s, eligible=None: score_diamond(s, "NCAA Baseball", eligible),
    "NCAA Softball": lambda s, eligible=None: score_diamond(s, "NCAA Softball", eligible),
}
