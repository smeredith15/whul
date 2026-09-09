"""The scoring rules, in words, for the Scoring page.

Every number on that page is read out of the scorer that uses it. Nothing here
is typed twice: change a weight in ``whul.scoring`` and the page changes with
it. A hand-copied rules table is a table that is wrong by the second season,
and the one thing a rules page cannot be is out of date.

The labels are the part that is written here, and they are written for someone
who has never seen the code, the feeds or the spreadsheets -- "Catch", not
``receptions``; "Win by 9 or more", not ``reg_big_wins``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from whul.scoring import golf, mlb, motorsport, nba, ncaa, nfl, nhl, soccer, tennis
from whul.scoring.competition import (
    LEAGUE_WIN, OUTCOME_SHARE, CONTINENTAL_ENTRY_POINTS, CONTINENTAL_QUALIFYING_DISCOUNT,
    WIN_POINTS, Outcome, Tier,
)
from whul.scoring.intl_soccer import BEYOND_BEST_SHARE, MATCH_MAX, RUNG, STAGE
from whul.scoring.postseason import DEFAULT_BONUS_SHARE

MINUS = "−"


def num(value: float) -> str:
    """A number as a reader would write it: no trailing zero, a real minus."""
    number = float(value)
    text = str(int(number)) if number == int(number) else f"{number:g}"
    return text.replace("-", MINUS)


def ordinal(place: int) -> str:
    if 10 <= place % 100 <= 20:
        return f"{place}th"
    return f"{place}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(place % 10, 'th') }"


def item(label: str, value: float, suffix: str = "") -> str:
    return f"{label} — {num(value)}{suffix}"


def weights(table: dict, labels: dict[str, str]) -> list[str]:
    """One line per weight, in the order the labels are written."""
    return [item(labels[key], table[key]) for key in labels if key in table]


@dataclass(frozen=True)
class Heading:
    """A sub-heading inside one section's list of scoring items.

    A marker class rather than a string with markup in it, because everything
    here reaches the page through ``escape`` -- an ``<em>`` written into a line
    would be shown to the reader as ``<em>``.
    """

    text: str


@dataclass(frozen=True)
class Rules:
    """One asset type's rules: what it is, then what scores."""

    slug: str
    title: str
    intro: str
    lines: list[str | Heading]
    notes: list[str] = field(default_factory=list)


# --- club soccer ----------------------------------------------------------

TIER_NAMES = {
    Tier.CHAMPIONS_LEAGUE: "Champions League",
    Tier.DOMESTIC_POSTSEASON: "MLS / NWSL playoffs",
    Tier.EUROPA: "Europa League",
    Tier.CONFERENCE: "Conference League",
    Tier.DOMESTIC_CUP: "domestic cup (FA Cup, Copa del Rey, …)",
    Tier.LEAGUE: "domestic league",
}

OUTCOME_NAMES = {
    Outcome.WIN: "Win",
    Outcome.SHOOTOUT_WIN: "Win on penalties",
    Outcome.DRAW: "Draw",
    Outcome.SHOOTOUT_LOSS: "Loss on penalties",
    Outcome.LOSS: "Loss",
}


def _club_soccer_teams() -> Rules:
    tiers = [
        item(f"A win in the {TIER_NAMES[tier]}", WIN_POINTS[tier])
        for tier in TIER_NAMES
    ]
    endings = [
        item(OUTCOME_NAMES[outcome], share)
        for outcome, share in OUTCOME_SHARE.items()
    ]
    europe = [
        item(f"A place in the {name} proper", points)
        for name, points in CONTINENTAL_ENTRY_POINTS.items()
    ]
    unhalved = [n for n, d in CONTINENTAL_QUALIFYING_DISCOUNT.items() if d >= 1]
    halved = [n for n, d in CONTINENTAL_QUALIFYING_DISCOUNT.items() if d < 1]
    return Rules(
        "club-soccer-teams",
        "Club soccer — teams",
        "A club is paid per match, and a match is worth more the bigger the "
        "competition it was played in. The figures below are what a win is "
        "worth in each; a lesser result is a share of it.",
        tiers
        + [
            item("Winning by 2 goals or more, on top of the win",
                 soccer.PTS_BIG_MARGIN),
            item("Keeping a clean sheet (conceding nothing)",
                 soccer.PTS_CLEAN_SHEET),
        ]
        + [Heading(f"Each result, out of the {num(LEAGUE_WIN)} a league win pays")]
        + endings
        + [Heading("Reaching a continental competition, earned by last "
                   "season's league finish")]
        + europe,
        [
            "Qualifying rounds score nothing. Only the competition proper "
            "counts — for the Champions League that means the league phase "
            "onward.",
            "Losing on penalties pays the same as a draw, because the match "
            "itself was drawn.",
            "A continental place is halved if the club still has to come "
            f"through a qualifying tie ({', '.join(halved)}). It is not halved "
            f"for the others ({', '.join(unhalved)}): a top-five-league club is "
            "expected to win a Conference League play-off comfortably, and the "
            "Champions Cup has no league phase to be outside of — an MLS club "
            "is in the competition proper whatever round it enters at, with no "
            "lower competition to drop into.",
            "MLS clubs earn their place in the CONCACAF Champions Cup, which "
            "pays what a Europa League place pays. Reaching one is the same "
            "size of achievement inside its own league, and every club is "
            "measured against its own league.",
            "Skipping a round by finishing high enough is paid as though the club "
            "had played it and won.",
        ],
    )


def _club_soccer_players() -> Rules:
    goals = [
        item(f"Goal by a {position}", points)
        for position, points in soccer.GOAL_POINTS_BY_POSITION.items()
    ]
    return Rules(
        "club-soccer-players",
        "Club soccer — players",
        "Players are paid per match for showing up and for what they did in it. "
        "Goalkeepers are not rostered.",
        [
            item(f"Playing {soccer.FULL_APPEARANCE_MINUTES} minutes or more",
                 soccer.PTS_FULL_APPEARANCE),
            item("A shorter appearance off the bench", soccer.PTS_SHORT_APPEARANCE),
        ]
        + goals
        + [
            item("Assist", soccer.PTS_ASSIST),
            item("Yellow card", soccer.PTS_YELLOW),
            item("Red card", soccer.PTS_RED),
        ],
        [
            "Goals are worth more the further back the scorer plays, so a "
            "defender's goal beats a striker's.",
            "European competition is credited as a bonus rather than as extra "
            "matches — see “What a cup run is worth” below.",
        ],
    )


# --- NFL ------------------------------------------------------------------

NFL_PLAYER_LABELS = {
    "passing_yards": "Passing yard",
    "passing_tds": "Passing touchdown",
    "interceptions": "Interception thrown",
    "rushing_yards": "Rushing yard",
    "rushing_tds": "Rushing touchdown",
    "receptions": "Catch",
    "receiving_yards": "Receiving yard",
    "receiving_tds": "Receiving touchdown",
    "fumbles_lost": "Fumble lost",
}

NFL_TEAM_LABELS = {
    "reg_wins": "Regular-season win",
    "reg_big_wins": f"Winning by {nfl.BIG_WIN_MARGIN} points or more",
    "reg_shutouts": "Shutout win",
    "div_wins": "Beating a division rival, on top of the win",
    "div_champ": "Winning the division",
    "playoff_appearance": "Reaching the playoffs",
    "playoff_wins": "Playoff win",
    "point_diff": "Each point of regular-season point differential",
}


def _nfl_players() -> Rules:
    return Rules(
        "nfl-players", "NFL — players",
        "Half-PPR: the ordinary fantasy football scale, with half a point a catch. "
        "Only quarterbacks, running backs, receivers and tight ends score.",
        weights(nfl.PLAYER_WEIGHTS, NFL_PLAYER_LABELS),
    )


def _nfl_teams() -> Rules:
    return Rules(
        "nfl-teams", "NFL — teams",
        "A team is paid for winning, for winning big, and for how far it goes in "
        "January.",
        weights(nfl.TEAM_WEIGHTS, NFL_TEAM_LABELS),
    )


# --- NBA ------------------------------------------------------------------

NBA_PLAYER_LABELS = {
    "points": "Point scored",
    "rebounds": "Rebound",
    "assists": "Assist",
    "steals": "Steal",
    "blocks": "Block",
    "three_pt_made": "Three-pointer made, on top of the points",
    "turnovers": "Turnover",
}

NBA_TEAM_LABELS = {
    "reg_wins": "Regular-season win",
    "reg_big_wins": f"Winning by {nba.BIG_WIN_MARGIN} points or more",
    "playin_only": "Reaching the Play-In but not the playoffs",
    "playoff_appearance": "Reaching the playoffs",
    "playoff_wins": "Playoff win",
    "playoff_series_wins": "Winning a playoff series, on top of the wins",
    "ist_wins": "NBA Cup win",
    "ist_champ": "Winning the NBA Cup",
    "point_diff": "Each point of regular-season point differential",
}


def _nba_players() -> Rules:
    return Rules(
        "nba-players", "NBA — players",
        "The usual box score, plus a bonus for filling more than one column.",
        weights(nba.BOX_WEIGHTS, NBA_PLAYER_LABELS)
        + [
            item("Double-double", nba.DOUBLE_DOUBLE_BONUS),
            item("Triple-double (on top of the double-double)",
                 nba.TRIPLE_DOUBLE_BONUS),
            item("Each point of plus-minus in the game", nba.PLUS_MINUS_WEIGHT),
        ],
        [
            f"A season needs at least {nba.MIN_GAMES} games to count as a season.",
        ],
    )


def _nba_teams() -> Rules:
    return Rules(
        "nba-teams", "NBA — teams",
        "Wins, the NBA Cup, and the playoffs. The Play-In pays only as "
        "consolation: it is dropped the moment the team reaches the playoffs "
        "proper.",
        weights(nba.TEAM_WEIGHTS, NBA_TEAM_LABELS),
    )


# --- MLB ------------------------------------------------------------------

MLB_BATTER_LABELS = {
    "h": "Hit",
    "doubles": "Double, on top of the hit",
    "triples": "Triple, on top of the hit",
    "hr": "Home run, on top of the hit",
    "bb": "Walk",
    "hbp": "Hit by pitch",
    "sb": "Stolen base",
    "cs": "Caught stealing",
    "ab": "Each at-bat",
}

MLB_PITCHER_LABELS = {
    "ip": "Inning pitched",
    "so": "Strikeout",
    "sv": "Save",
    "hld": "Hold",
    "h": "Hit allowed",
    "bb": "Walk allowed",
    "hbp": "Batter hit",
    "hr": "Home run allowed",
}

MLB_TEAM_LINES = {
    "Regular-season win": mlb.BASE_REG_WIN,
    "Winning by 5 runs or more": mlb.PTS_BIG_WIN,
    "Shutout win": mlb.PTS_SHUTOUT,
    "Each run of run differential": mlb.PTS_RUN_DIFF,
    "Winning the division": mlb.PTS_DIV_CHAMP,
    "Playoff win": mlb.BASE_PLAYOFF_WIN,
    "Winning the Wild Card round": mlb.PTS_SERIES["wc"],
    "Winning a Division Series": mlb.PTS_SERIES["lds"],
    "Winning a Championship Series": mlb.PTS_SERIES["lcs"],
    "Winning the World Series": mlb.PTS_SERIES["ws"],
}


def _mlb_players() -> Rules:
    return Rules(
        "mlb-players", "MLB — batters and pitchers",
        "Batting and pitching are scored on separate scales, because they are "
        "not the same currency. Note that an at-bat costs a point: the scale "
        "rewards reaching base, not swinging.",
        [Heading("Batting")]
        + weights(mlb.BATTER_WEIGHTS, MLB_BATTER_LABELS)
        + [Heading("Pitching")]
        + weights(mlb.PITCHER_WEIGHTS, MLB_PITCHER_LABELS),
        [
            "A player who both bats and pitches is scored at their better role in "
            f"full plus {num(mlb.SECONDARY_ROLE_WEIGHT * 100)}% of the other one.",
            "Fielding and overall value are folded in from FanGraphs' run "
            "estimates, which are worth roughly 1–5% of a score — most to a "
            "glove-first player, least to a slugger.",
            "Baseball straddles the draft, so a season is valued across the "
            "twelve months either side of the All-Star break rather than by "
            "calendar year.",
        ],
    )


def _mlb_teams() -> Rules:
    return Rules(
        "mlb-teams", "MLB — teams",
        "162 games make wins cheap, so October is where the points are.",
        [item(label, value) for label, value in MLB_TEAM_LINES.items()],
        [
            "A bye past the Wild Card round is paid as though the team had "
            "played it and swept it.",
            "A division title is only awarded once the season is over. Nobody "
            "has won one in April.",
        ],
    )


# --- NHL ------------------------------------------------------------------

def _nhl_players() -> Rules:
    return Rules(
        "nhl-players", "NHL — skaters",
        "Skaters only — the league does not roster goalies.",
        [
            item("Goal", nhl.PTS_GOAL),
            item("Assist", nhl.PTS_ASSIST),
            item("Shot on goal", nhl.PTS_SHOT),
            item("Each point of plus-minus", nhl.PTS_PLUS_MINUS),
        ],
    )


def _nhl_teams() -> Rules:
    return Rules(
        "nhl-teams", "NHL — teams",
        "The league's own standings points, then the playoffs.",
        [
            item("Win", nhl.PTS_WIN),
            item("Overtime or shootout loss", nhl.PTS_OTL),
            item("Each goal of goal differential", nhl.PTS_GOAL_DIFF),
            item("Winning the division", nhl.PTS_DIV_CHAMP),
            item("Reaching the playoffs", nhl.PTS_PLAYOFF_APP),
            item("Playoff win", nhl.PTS_PLAYOFF_WIN),
            item("Winning a playoff series, on top of the wins", nhl.PTS_SERIES_WIN),
        ],
        [
            "The NHL goes to 84 games in 2026-27, so past seasons are scaled up "
            "to that length before they are compared with this one.",
            "The division goes to the club with the most standings points in "
            "it, ties broken on regulation wins and then on goal difference. "
            "It is only awarded once every club in the division has played its "
            "schedule — leading in November is not winning a division, and "
            "paying for it would mean taking the points back in March.",
        ],
    )


# --- international soccer -------------------------------------------------

RUNG_NAMES = {
    "world": "World Cup and its qualifying",
    "federation": "Euros, Copa América, AFCON, Asian Cup, Gold Cup and their qualifying",
    "nations_league": "Nations League",
}

STAGE_NAMES = {
    "qualifying": "A qualifier",
    "group": "A group-stage match at a finals",
    "knockout": "A knockout match at a finals",
}


def _intl_soccer_teams() -> Rules:
    return Rules(
        "intl-soccer-teams", "International soccer — national teams",
        "A national team plays a handful of tournaments across a four-year "
        "cycle, so it is scored by how far it went rather than by how many "
        "matches it won. Each match is scored exactly like a club match, then "
        "multiplied twice: by the stage it was played at and by the size of the "
        "competition.",
        [item("A win", OUTCOME_SHARE[Outcome.WIN]),
         item("A draw or a shootout loss", OUTCOME_SHARE[Outcome.DRAW]),
         item("A shootout win", OUTCOME_SHARE[Outcome.SHOOTOUT_WIN]),
         item("Winning by 2 goals or more", soccer.PTS_BIG_MARGIN),
         item("A clean sheet", soccer.PTS_CLEAN_SHEET)]
        + [item(STAGE_NAMES[key], value, "× multiplier")
           for key, value in STAGE.items()]
        + [item(RUNG_NAMES[key], value, "× multiplier")
           for key, value in RUNG.items()],
        [
            "Each competition pays a fixed ceiling shared out along the "
            "champion's own path, so winning the Gold Cup in six matches and "
            "AFCON in seven are worth the same, and the 2026 World Cup's extra "
            "round changes nothing about what a World Cup is worth. A perfect "
            f"match is {num(MATCH_MAX)} points.",
            "A team's year is its best competition in full plus "
            f"{num(BEYOND_BEST_SHARE * 100)}% of everything else, so winning two "
            "trophies does not simply double the score.",
            "A year with no finals in it is lifted so the best competition the "
            "team actually played still reaches a full ceiling — otherwise "
            "European teams would go quiet two years in three.",
            "Friendlies, the Olympics and invitational tournaments score nothing.",
        ],
    )


# --- college --------------------------------------------------------------

FB_LABELS = {
    "wins": "Win",
    "big_wins": "Winning big — by "
                f"{ncaa.FB_BIG_WIN_CONF} in conference or "
                f"{ncaa.FB_BIG_WIN_NONCONF} out of it",
    "conf_wins": "Beating a conference opponent, on top of the win",
    "conf_title_win": "Winning the conference championship game",
    "playoff_app": "Reaching the College Football Playoff",
    "playoff_wins": "Playoff win",
    "point_diff": "Each point of point differential",
}

BB_LABELS = {
    "reg_wins": "Regular-season win",
    "big_wins": "Winning big — by "
                f"{ncaa.BB_BIG_WIN_CONF} in conference or "
                f"{ncaa.BB_BIG_WIN_NONCONF} out of it",
    "conf_wins": "Beating a conference opponent, on top of the win",
    "conf_tourney_wins": "Conference tournament win",
    "conf_tourney_champ": "Winning the conference tournament",
    "mm_appearance": "Reaching the NCAA tournament",
    "mm_wins": "NCAA tournament win",
    "point_diff": "Each point of point differential",
}


def _ncaaf_teams() -> Rules:
    return Rules(
        "ncaaf-teams", "College football — teams",
        "Wins are the bulk of it, and the bar for a blowout is lower against a "
        "conference opponent because the opponent is better.",
        weights(ncaa.FB_WEIGHTS, FB_LABELS)
        + [item("Finishing top of the conference in the regular season",
                ncaa.FB_REG_CHAMP_POOL, " shared among co-champions")],
    )


def _ncaab_teams() -> Rules:
    return Rules(
        "ncaab-teams", "College basketball — teams (men's and women's)",
        "Thirty-odd games make a single win worth little, so March is where a "
        "season is decided.",
        weights(ncaa.BB_WEIGHTS, BB_LABELS)
        + [item("Finishing top of the conference in the regular season",
                ncaa.BB_REG_CHAMP_POOL, " shared among co-champions")],
    )


def _ncaa_diamond_teams() -> Rules:
    return Rules(
        "ncaa-diamond-teams", "College baseball and softball — teams",
        "The regular season is a long grind that pays little; the postseason is "
        "three rounds and pays most of the score.",
        [
            item("Win", ncaa.DIAMOND_REG_WIN),
            item("Each run of run differential", ncaa.DIAMOND_RUN_DIFF),
            item("Winning a Regional", ncaa.PTS_SERIES_REGIONAL),
            item("Winning a Super Regional", ncaa.PTS_SERIES_SUPER),
            item("Winning the College World Series", ncaa.PTS_SERIES_CWS),
        ],
    )


# --- golf, tennis, motorsport --------------------------------------------

def _pga_players() -> Rules:
    top = golf.FINISH_POINTS
    ladder = ", ".join(
        f"{ordinal(place)} {num(top[place - 1])}"
        for place in (1, 2, 3, 5, 10, 20, 30)
    )
    return Rules(
        "pga-players", "Golf — players",
        "Finishing position only. Nothing else about a round matters, and "
        f"nothing below {ordinal(golf.SCORING_POSITIONS)} place scores at all.",
        [
            f"Points by finish — {ladder}",
            item("Majors and the Players Championship", golf.MAJOR_MULTIPLIER,
                 "× multiplier"),
        ],
        [
            "A tie pays every player the full points for the place they tied at, "
            "rather than splitting them.",
            f"A season needs at least {golf.MIN_EVENTS} starts to count.",
        ],
    )


#: The tiers worth showing, and how many matches a champion plays at each.
#: Three round-robin matches at the Tour Finals are all worth the same, so a
#: champion's total is not simply the sum of the column.
TENNIS_TIERS = (
    ("GS", "Grand Slam", {}),
    ("M1000_128", "Masters 1000", {}),
    ("A500_32", "ATP/WTA 500", {}),
    ("A250_32", "ATP/WTA 250", {}),
    ("FINALS", "ATP / WTA Tour Finals", {"RR": 3}),
)


def _tennis_players() -> Rules:
    lines = []
    for key, name, repeats in TENNIS_TIERS:
        rounds = tennis.TIER_ROUNDS[key]
        total = sum(
            tennis.ATP_WIN_POINTS[(key, r)] * repeats.get(r, 1) for r in rounds
        )
        detail = ", ".join(
            f"{r} {num(tennis.ATP_WIN_POINTS[(key, r)])}"
            + (f" (×{repeats[r]})" if r in repeats else "")
            for r in rounds
        )
        whose = "an undefeated champion" if repeats else "the champion"
        lines.append(f"{name} — {num(total)} to {whose} ({detail})")
    lines.append(item("A Davis Cup, Billie Jean King Cup or United Cup win",
                      tennis.INTERNATIONAL_WIN_POINTS))
    for best_of, multiplier in sorted(tennis.STRAIGHT_SETS_MULTIPLIER.items()):
        lines.append(item(f"Winning a best-of-{best_of} match in straight sets",
                          multiplier, "× multiplier"))
    return Rules(
        "tennis-players", "Tennis — players",
        "The tours' own ranking points, paid a round at a time: winning a match "
        "is worth what reaching the next round adds. The figures below are the "
        "increments, and they add up to the event's face value.",
        lines,
        [
            "Qualifying rounds score nothing.",
            "A seed's first-round bye pays only if they win their next match.",
            "Only men's Grand Slam matches are best-of-five.",
            "The Tour Finals is the one event whose column does not add up to "
            "what a champion collects: the round robin is played three times, "
            "so an unbeaten run is 1500 and a champion who dropped a group "
            "match earns less.",
        ],
    )


def _motorsports_players() -> Rules:
    f1 = ", ".join(
        f"{ordinal(place)} {num(points)}"
        for place, points in enumerate(motorsport.F1_POINTS, start=1)
    )
    sprint = ", ".join(
        f"{ordinal(place)} {num(points)}"
        for place, points in enumerate(motorsport.F1_SPRINT_POINTS, start=1)
    )
    return Rules(
        "motorsports-players", "Motorsport — drivers",
        "NASCAR and Formula 1 share one roster slot and one scale, so each "
        "series keeps its own championship points and the two are pooled.",
        [
            f"NASCAR win — {num(motorsport.NASCAR_WIN_POINTS)}",
            f"NASCAR 2nd — {num(motorsport.NASCAR_SECOND_POINTS)}, then one "
            f"fewer per place down to "
            f"{ordinal(motorsport.NASCAR_LAST_SCORING_POSITION)}",
            f"NASCAR, anything worse — {num(motorsport.NASCAR_MINIMUM_POINTS)}",
            f"Formula 1 by finish — {f1}",
            f"Formula 1 sprint by finish — {sprint}",
            item("Formula 1 fastest lap, inside the top "
                 f"{motorsport.F1_FASTEST_LAP_MAX_POSITION}",
                 motorsport.F1_FASTEST_LAP_POINT),
        ],
        [
            f"A NASCAR season needs at least {motorsport.NASCAR_MIN_RACES} starts "
            "to count, which keeps one-off substitutes out.",
            "NASCAR's current scale is applied to past seasons too, so eras are "
            "comparable.",
        ],
    )


# --- the postseason bonus, which is shared ---------------------------------

def _postseason() -> Rules:
    return Rules(
        "postseason-bonus", "What a cup run is worth (all players)",
        "Playoff production is credited as a bonus rather than as extra games, "
        "and the bonus is the same size in every sport.",
        [
            "A postseason played exactly as well as the regular season — "
            f"{num(DEFAULT_BONUS_SHARE * 100)}% of a regular season, on top of it",
            f"A postseason played twice as well — "
            f"{num(DEFAULT_BONUS_SHARE * 200)}% of a regular season, and so on",
            "A team that missed the postseason — 0",
        ],
        [
            "The bonus is a rate, not a tally, so one brilliant playoff game is "
            "worth as much per game as a long run of them. What it cannot do is "
            "let a short postseason outweigh a whole season.",
            "For club soccer, European competition plays the part the playoffs "
            "play elsewhere.",
            "This is for players only. Teams are paid for the postseason "
            "directly — appearances, wins and series, listed above.",
            "The NBA Play-In and European qualifying rounds count as neither a "
            "regular-season game nor a postseason one, and are thrown away.",
        ],
    )


def sections() -> list[Rules]:
    """Every asset type, in roster order."""
    return [
        _club_soccer_teams(),
        _club_soccer_players(),
        _nfl_teams(),
        _nfl_players(),
        _nba_teams(),
        _nba_players(),
        _mlb_teams(),
        _mlb_players(),
        _nhl_teams(),
        _nhl_players(),
        _ncaaf_teams(),
        _ncaab_teams(),
        _ncaa_diamond_teams(),
        _intl_soccer_teams(),
        _pga_players(),
        _tennis_players(),
        _motorsports_players(),
        _postseason(),
    ]
