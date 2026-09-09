"""The scoring rules as data a browser can evaluate.

The Scoring page explains what a thing is worth. This lets somebody try it:
type a statline, see the raw points and the 0-100 score it becomes. That is a
different kind of understanding, and it is the one a manager actually wants --
"is a 40-goal season good" is not answered by a table of weights.

The site is a folder of HTML with no server, so the arithmetic has to happen in
the browser. What the browser gets is *this* -- the same weight dictionaries
the scorers use, serialised -- rather than a second implementation of the rules
in JavaScript. A second implementation is a second thing to keep right, and it
would be wrong within a season.

Two shapes cover everything here:

``linear``
    A statline: each field has a per-unit value and the total is their sum.
    Every team sport works this way, and so does every player in one.

``events``
    A ladder: golf, motorsport and tennis pay for a finishing position or a
    round reached, so the input is a list of results rather than a column of
    counts.

What is deliberately absent is international soccer. Its season score is a
ceiling divided by the champion's path, plus half of everything after the best
competition -- not a sum of matches -- and a calculator that added match points
up would teach the opposite of how it works.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from whul.scoring import golf, mlb, motorsport, nba, ncaa, nfl, nhl, soccer, tennis
from whul.scoring.competition import (
    LEAGUE_WIN, OUTCOME_SHARE, WIN_POINTS, Outcome, Tier,
)
from whul.site import rulebook


@dataclass
class Field:
    """One number a reader types, and what each unit of it is worth."""

    key: str
    label: str
    points: float
    #: Multiplied by the competition premium, for the leagues that have one.
    #: A win is worth more in Europe; a clean sheet is worth one everywhere.
    scaled: bool = False
    step: float = 1


@dataclass
class Choice:
    """A pick that changes the arithmetic rather than adding to it."""

    label: str
    value: float


@dataclass
class Calc:
    slug: str
    title: str
    kind: str
    intro: str
    #: ``(label, benchmark key)`` -- what the raw total is divided by. More
    #: than one where a league is normalized per position, which is where a
    #: calculator earns its keep: the same statline is a different score for a
    #: tight end and a quarterback.
    groups: list[list] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    #: For ``linear`` leagues with a competition premium.
    scale_label: str = ""
    scales: list[Choice] = field(default_factory=list)
    #: For ``events``.
    event_label: str = ""
    options: list[Choice] = field(default_factory=list)
    multiplier_label: str = ""
    multipliers: list[Choice] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _fields(table: dict, labels: dict[str, str], step: float = 1) -> list[Field]:
    """One field per weight, in the order the labels are written."""
    return [
        Field(key, labels[key], float(table[key]), step=step)
        for key in labels if key in table
    ]


def _ordinal(place: int) -> str:
    return rulebook.ordinal(place)


# --- the leagues that are a statline ---------------------------------------

def _nfl_players() -> Calc:
    return Calc(
        "calc-nfl-players", "NFL — player", "linear",
        "Half-PPR. A quarterback and a tight end are measured against "
        "different pools, so the same line is a different score for each.",
        groups=[["Quarterback", "NFL_QB"], ["Running back", "NFL_RB"],
                ["Wide receiver", "NFL_WR"], ["Tight end", "NFL_TE"]],
        fields=_fields(nfl.PLAYER_WEIGHTS, rulebook.NFL_PLAYER_LABELS),
    )


def _nfl_teams() -> Calc:
    return Calc(
        "calc-nfl-teams", "NFL — team", "linear",
        "A season's worth of results. Seventeen games, so a win is worth a lot "
        "and January is worth more.",
        groups=[["NFL", "NFL"]],
        fields=_fields(nfl.TEAM_WEIGHTS, rulebook.NFL_TEAM_LABELS),
    )


def _nba_players() -> Calc:
    return Calc(
        "calc-nba-players", "NBA — player", "linear",
        "Per-game box scores added up. The double-double and triple-double "
        "bonuses are per game, so enter how many of each.",
        groups=[["Guard", "NBA_Backcourt"], ["Forward or centre", "NBA_Frontcourt"]],
        fields=_fields(nba.BOX_WEIGHTS, rulebook.NBA_PLAYER_LABELS) + [
            Field("dd", "Double-doubles", float(nba.DOUBLE_DOUBLE_BONUS)),
            Field("td", "Triple-doubles (on top of the double-double)",
                  float(nba.TRIPLE_DOUBLE_BONUS)),
            Field("plus_minus", "Net plus-minus across the season",
                  float(nba.PLUS_MINUS_WEIGHT)),
        ],
        notes=[f"A season needs {nba.MIN_GAMES} games to enter the pool at all."],
    )


def _nba_teams() -> Calc:
    return Calc(
        "calc-nba-teams", "NBA — team", "linear",
        "The Play-In pays only as consolation: it is dropped the moment the "
        "team reaches the playoffs proper.",
        groups=[["NBA", "NBA"]],
        fields=_fields(nba.TEAM_WEIGHTS, rulebook.NBA_TEAM_LABELS, step=1),
    )


def _mlb_batters() -> Calc:
    return Calc(
        "calc-mlb-batters", "MLB — batter", "linear",
        "Note that an at-bat costs a point: the scale pays for reaching base, "
        "not for swinging.",
        groups=[["Batter", "MLB_Batter"]],
        fields=_fields(mlb.BATTER_WEIGHTS, rulebook.MLB_BATTER_LABELS),
        notes=[
            "Fielding and overall value are folded in from FanGraphs' run "
            "estimates, which are worth roughly 1-5% of a score and are not "
            "modelled here.",
        ],
    )


def _mlb_pitchers() -> Calc:
    return Calc(
        "calc-mlb-pitchers", "MLB — pitcher", "linear",
        "Innings are the bulk of it. A home run allowed costs more than a "
        "strikeout earns, by a factor of six.",
        groups=[["Pitcher", "MLB_Pitcher"]],
        fields=_fields(mlb.PITCHER_WEIGHTS, rulebook.MLB_PITCHER_LABELS, step=0.1),
        notes=["WAR is folded in from FanGraphs and is not modelled here."],
    )


def _mlb_teams() -> Calc:
    return Calc(
        "calc-mlb-teams", "MLB — team", "linear",
        "162 games make a win cheap, so October is where the points are.",
        groups=[["MLB", "MLB"]],
        fields=[Field(key, label, float(value))
                for key, (label, value) in {
                    "reg_wins": ("Regular-season win", mlb.BASE_REG_WIN),
                    "big_wins": ("Winning by 5 runs or more", mlb.PTS_BIG_WIN),
                    "shutouts": ("Shutout win", mlb.PTS_SHUTOUT),
                    "run_diff": ("Each run of run differential", mlb.PTS_RUN_DIFF),
                    "div": ("Winning the division", mlb.PTS_DIV_CHAMP),
                    "po_wins": ("Playoff win", mlb.BASE_PLAYOFF_WIN),
                    "wc": ("Winning the Wild Card round", mlb.PTS_SERIES["wc"]),
                    "lds": ("Winning a Division Series", mlb.PTS_SERIES["lds"]),
                    "lcs": ("Winning a Championship Series", mlb.PTS_SERIES["lcs"]),
                    "ws": ("Winning the World Series", mlb.PTS_SERIES["ws"]),
                }.items()],
    )


def _nhl_players() -> Calc:
    return Calc(
        "calc-nhl-players", "NHL — skater", "linear",
        "Skaters only; the league does not roster goalies.",
        groups=[["Skater", "NHL"]],
        fields=[
            Field("goals", "Goal", float(nhl.PTS_GOAL)),
            Field("assists", "Assist", float(nhl.PTS_ASSIST)),
            Field("shots", "Shot on goal", float(nhl.PTS_SHOT)),
            Field("plus_minus", "Each point of plus-minus",
                  float(nhl.PTS_PLUS_MINUS)),
        ],
    )


def _nhl_teams() -> Calc:
    return Calc(
        "calc-nhl-teams", "NHL — team", "linear",
        "The league's own standings points, then the playoffs.",
        groups=[["NHL", "NHL"]],
        fields=[
            Field("wins", "Win", float(nhl.PTS_WIN)),
            Field("otl", "Overtime or shootout loss", float(nhl.PTS_OTL)),
            Field("gd", "Each goal of goal differential", float(nhl.PTS_GOAL_DIFF)),
            Field("div", "Winning the division", float(nhl.PTS_DIV_CHAMP)),
            Field("po_app", "Reaching the playoffs", float(nhl.PTS_PLAYOFF_APP)),
            Field("po_wins", "Playoff win", float(nhl.PTS_PLAYOFF_WIN)),
            Field("series", "Winning a playoff series", float(nhl.PTS_SERIES_WIN)),
        ],
        notes=["Regular-season terms are lifted to the 84-game 2026-27 "
               "schedule before they are compared with history."],
    )


# --- club soccer, where the competition changes the arithmetic --------------

SOCCER_GROUPS = [
    ["Premier League", "Premier League"], ["La Liga", "La Liga"],
    ["Serie A", "Serie A"], ["Bundesliga", "Bundesliga"],
    ["Ligue 1", "Ligue 1"], ["MLS", "MLS"], ["NWSL", "NWSL"],
]


def _soccer_teams() -> Calc:
    """The competition premium, as a multiplier on the outcome fields."""
    def opening_capital(name: str) -> str:
        """`str.capitalize` lowercases the rest, which turns the domestic cup
        entry into "Domestic cup (fa cup, copa del rey)". Only the first
        letter moves."""
        return name[:1].upper() + name[1:] if name else name

    scales = [
        Choice(opening_capital(rulebook.TIER_NAMES[tier]),
               WIN_POINTS[tier] / LEAGUE_WIN)
        for tier in rulebook.TIER_NAMES
    ]
    return Calc(
        "calc-soccer-teams", "Club soccer — team", "linear",
        "Results in one competition. A win is worth more the bigger the "
        "competition, so a season played across four of them is four passes "
        "through this and the totals added.",
        groups=SOCCER_GROUPS,
        scale_label="Competition",
        scales=scales,
        fields=[
            Field("win", "Win", float(OUTCOME_SHARE[Outcome.WIN]), scaled=True),
            Field("shootout_win", "Win on penalties",
                  float(OUTCOME_SHARE[Outcome.SHOOTOUT_WIN]), scaled=True),
            Field("draw", "Draw", float(OUTCOME_SHARE[Outcome.DRAW]), scaled=True),
            Field("shootout_loss", "Loss on penalties",
                  float(OUTCOME_SHARE[Outcome.SHOOTOUT_LOSS]), scaled=True),
            Field("big", "Winning by 2 goals or more",
                  float(soccer.PTS_BIG_MARGIN)),
            Field("clean", "Clean sheet", float(soccer.PTS_CLEAN_SHEET)),
        ],
        notes=[
            "The two bonuses are flat: a clean sheet is worth one wherever it "
            "happens. Only the result scales.",
            "Qualifying rounds score nothing, so leave them out.",
        ],
    )


def _soccer_players() -> Calc:
    goals = soccer.GOAL_POINTS_BY_POSITION
    return Calc(
        "calc-soccer-players", "Club soccer — player", "linear",
        "A player's points do not change with the competition -- only a "
        "club's do. Goals are worth more the further back the scorer plays, "
        "so start with the position.",
        groups=SOCCER_GROUPS,
        scale_label="Position",
        scales=[Choice(position[:1].upper() + position[1:], 1.0)
                for position in goals],
        fields=[
            Field("full", f"Appearances of {soccer.FULL_APPEARANCE_MINUTES} "
                          f"minutes or more", float(soccer.PTS_FULL_APPEARANCE)),
            Field("sub", "Shorter appearances off the bench",
                  float(soccer.PTS_SHORT_APPEARANCE)),
            Field("goals_defender", "Goals as a defender",
                  float(goals["defender"])),
            Field("goals_midfielder", "Goals as a midfielder",
                  float(goals["midfielder"])),
            Field("goals_forward", "Goals as a forward", float(goals["forward"])),
            Field("assists", "Assists", float(soccer.PTS_ASSIST)),
            Field("yellow", "Yellow cards", float(soccer.PTS_YELLOW)),
            Field("red", "Red cards", float(soccer.PTS_RED)),
        ],
        notes=[
            "Enter goals on the row matching where the player lines up; the "
            "other two stay at zero.",
            "European competition is credited as a bonus on top of this, at a "
            "rate rather than per match, and is not modelled here.",
        ],
    )


# --- college ----------------------------------------------------------------

def _ncaaf_teams() -> Calc:
    return Calc(
        "calc-ncaaf-teams", "College football — team", "linear",
        "Wins are the bulk of it, and the bar for a blowout is lower in "
        "conference because the opponent is better.",
        groups=[["NCAAF", "NCAAF"]],
        fields=_fields(ncaa.FB_WEIGHTS, rulebook.FB_LABELS) + [
            Field("reg_champ", "Finishing top of the conference",
                  float(ncaa.FB_REG_CHAMP_POOL)),
        ],
    )


def _ncaab_teams() -> Calc:
    return Calc(
        "calc-ncaab-teams", "College basketball — team", "linear",
        "Thirty-odd games make a single win worth little, so March decides it.",
        groups=[["Men's", "NCAAM"], ["Women's", "NCAAW"]],
        fields=_fields(ncaa.BB_WEIGHTS, rulebook.BB_LABELS) + [
            Field("reg_champ", "Finishing top of the conference",
                  float(ncaa.BB_REG_CHAMP_POOL)),
        ],
    )


def _ncaa_diamond() -> Calc:
    return Calc(
        "calc-ncaa-diamond", "College baseball and softball — team", "linear",
        "A long regular season that pays little, then three rounds that pay "
        "most of it.",
        groups=[["Baseball", "NCAA Baseball"], ["Softball", "NCAA Softball"]],
        fields=[
            Field("wins", "Win", float(ncaa.DIAMOND_REG_WIN)),
            Field("run_diff", "Each run of run differential",
                  float(ncaa.DIAMOND_RUN_DIFF)),
            Field("regional", "Winning a Regional",
                  float(ncaa.PTS_SERIES_REGIONAL)),
            Field("super", "Winning a Super Regional",
                  float(ncaa.PTS_SERIES_SUPER)),
            Field("cws", "Winning the College World Series",
                  float(ncaa.PTS_SERIES_CWS)),
        ],
    )


# --- the ladders ------------------------------------------------------------

def _pga() -> Calc:
    return Calc(
        "calc-pga", "Golf — player", "events",
        "Finishing position only, and nothing below "
        f"{_ordinal(golf.SCORING_POSITIONS)} scores. Add an event per start.",
        groups=[["PGA", "PGA"]],
        event_label="Finish",
        options=[Choice(_ordinal(place), float(points))
                 for place, points in enumerate(golf.FINISH_POINTS, start=1)]
                + [Choice(f"{_ordinal(golf.SCORING_POSITIONS)}+ or missed cut", 0.0)],
        multiplier_label="Event",
        multipliers=[Choice("Regular tour event", 1.0),
                     Choice("Major or the Players", float(golf.MAJOR_MULTIPLIER))],
        notes=[f"A season needs {golf.MIN_EVENTS} starts to enter the pool.",
               "A tie pays every player the full points for the place they "
               "tied at, rather than splitting them."],
    )


def _motorsports() -> Calc:
    return Calc(
        "calc-motorsports", "Motorsport — driver", "events",
        "NASCAR and Formula 1 keep their own championship points and are "
        "pooled afterwards, so pick the series first.",
        groups=[["NASCAR", "NASCAR"], ["Formula 1", "F1"]],
        event_label="Finish",
        options=(
            [Choice(f"NASCAR {_ordinal(place)}",
                    motorsport.nascar_points(place))
             for place in range(1, motorsport.NASCAR_LAST_SCORING_POSITION + 1)]
            + [Choice("NASCAR, outside the top "
                      f"{motorsport.NASCAR_LAST_SCORING_POSITION}",
                      float(motorsport.NASCAR_MINIMUM_POINTS))]
            + [Choice(f"F1 {_ordinal(place)}", float(points))
               for place, points in enumerate(motorsport.F1_POINTS, start=1)]
            + [Choice("F1, out of the points", 0.0)]
            + [Choice(f"F1 sprint {_ordinal(place)}", float(points))
               for place, points in enumerate(motorsport.F1_SPRINT_POINTS, start=1)]
        ),
        multiplier_label="Fastest lap",
        multipliers=[Choice("No", 0.0), Choice("Yes (F1, inside the top "
                     f"{motorsport.F1_FASTEST_LAP_MAX_POSITION})",
                     float(motorsport.F1_FASTEST_LAP_POINT))],
        notes=[f"A NASCAR season needs {motorsport.NASCAR_MIN_RACES} starts to "
               "enter the pool.",
               "The fastest-lap point is added, not multiplied -- pick the "
               "finish, then say whether they took it."],
    )


#: The tiers a calculator offers, and how many times a champion plays each
#: round. Only the Tour Finals repeats one.
TENNIS_TIERS = (
    ("GS", "Grand Slam", {}),
    ("M1000_128", "Masters 1000", {}),
    ("A500_32", "ATP / WTA 500", {}),
    ("A250_32", "ATP / WTA 250", {}),
    ("FINALS", "Tour Finals", {"RR": 3}),
)


def _tennis() -> Calc:
    options = []
    for key, name, repeats in TENNIS_TIERS:
        rounds = tennis.TIER_ROUNDS[key]
        running = 0.0
        for index, this_round in enumerate(rounds):
            running += tennis.ATP_WIN_POINTS[(key, this_round)] * repeats.get(
                this_round, 1)
            reached = ("won it" if index == len(rounds) - 1
                       else f"through {this_round}")
            options.append(Choice(f"{name}: {reached}", running))
        options.append(Choice(f"{name}: lost first match", 0.0))
    options.append(Choice("Davis / BJK / United Cup, per win",
                          float(tennis.INTERNATIONAL_WIN_POINTS)))
    return Calc(
        "calc-tennis", "Tennis — player", "events",
        "Ranking points, paid a round at a time. Pick how far they got and "
        "the running total for that tier is used.",
        groups=[["ATP", "ATP"], ["WTA", "WTA"]],
        event_label="Result",
        options=options,
        multiplier_label="Straight sets",
        multipliers=[Choice("Mixed", 1.0),
                     Choice("Every match in straight sets (best of 3)",
                            float(tennis.STRAIGHT_SETS_MULTIPLIER[3])),
                     Choice("Every match in straight sets (best of 5)",
                            float(tennis.STRAIGHT_SETS_MULTIPLIER[5]))],
        notes=["Qualifying scores nothing.",
               "The straight-sets bonus is per match; applying it to a whole "
               "run is the best case, not the usual one."],
    )


def calculators() -> list[Calc]:
    return [
        _soccer_teams(), _soccer_players(),
        _nfl_teams(), _nfl_players(),
        _nba_teams(), _nba_players(),
        _mlb_teams(), _mlb_batters(), _mlb_pitchers(),
        _nhl_teams(), _nhl_players(),
        _ncaaf_teams(), _ncaab_teams(), _ncaa_diamond(),
        _pga(), _tennis(), _motorsports(),
    ]


def payload(benchmarks: dict[str, float], version: str) -> dict:
    """Everything the page needs to do the arithmetic, and nothing else."""
    return {
        "version": version,
        "benchmarks": {str(k): float(v) for k, v in benchmarks.items()},
        "calcs": [asdict(calc) for calc in calculators()],
    }
