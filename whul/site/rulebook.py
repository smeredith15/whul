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
from whul.scoring.postseason import DEFAULT_BONUS_SHARE, RULES

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
    "reg_ties": "Regular-season tie, at half a win",
    "reg_big_wins": f"Winning by {nfl.BIG_WIN_MARGIN} points or more",
    "reg_shutouts": "Shutout win",
    "div_wins": "Beating a division rival, on top of the win",
    "div_ties": "Tying a division rival, on top of the tie",
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
        # The run values, itemised. They were described in a note below and
        # left out of the table, so a reader totting up the scored terms found
        # every one except the two that are not counting stats -- and those are
        # the ones nobody would guess at.
        + [item("Each run of FanGraphs Offense", mlb.OFFENSE_FACTOR),
           item("Each run of FanGraphs Defense", mlb.DEFENSE_FACTOR)]
        + [Heading("Pitching")]
        + weights(mlb.PITCHER_WEIGHTS, MLB_PITCHER_LABELS)
        + [item("Each win above replacement (FanGraphs)", mlb.WAR_FACTOR)],
        [
            "A player who both bats and pitches is scored at their better role in "
            f"full plus {num(mlb.SECONDARY_ROLE_WEIGHT * 100)}% of the other one.",
            "Fielding and overall value are folded in from FanGraphs' run "
            "estimates, which are worth roughly 1–5% of a score — most to a "
            "glove-first player, least to a slugger.",
            "Those three are a share of a whole season, apportioned by the "
            "games played since the draft, so they will not match the figure "
            "FanGraphs shows for the year. The counting stats above are the "
            "window's own.",
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

#: How a competition is named on the Scoring page, where its rule's key is not
#: what a manager would call it.
BONUS_NAMES = {
    "UCL": "the Champions League",
    "Europa League": "the Europa League",
    "Europa Conference League": "the Conference League",
    "MLS": "the MLS Cup playoffs",
    "NWSL": "the NWSL playoffs",
}

#: Priced but not paid, because no feed reachable from here carries a player
#: line for it. Listed so the page does not promise a bonus that is always zero.
BONUS_UNPAID = {"CONCACAF Champions Cup"}


def _bonus_shares() -> tuple[list[str], list[float]]:
    """One line a share, naming every competition that pays it."""
    by_share: dict[float, list[str]] = {}
    for key, rule in RULES.items():
        if key in BONUS_UNPAID:
            continue
        by_share.setdefault(rule.bonus_share, []).append(
            BONUS_NAMES.get(key, key))
    lines = []
    for share in sorted(by_share, reverse=True):
        where = ", ".join(sorted(by_share[share]))
        lines.append(f"{where[:1].upper()}{where[1:]} — {num(share * 100)}% of a "
                     f"regular season, for a postseason played exactly as well "
                     f"as the regular one")
    return lines, sorted(by_share, reverse=True)


def _postseason() -> Rules:
    lines, shares = _bonus_shares()
    top, bottom = num(max(shares) * 100), num(min(shares) * 100)
    return Rules(
        "postseason-bonus", "What a cup run is worth (all players)",
        "Playoff production is credited as a bonus rather than as extra games. "
        "The size of the bonus depends on how much was still unknown when you "
        "drafted: a competition whose field nobody could predict pays more than "
        "one whose field was already settled.",
        lines + [
            f"A postseason played twice as well as the regular season — double "
            f"its share, so {num(max(shares) * 200)}% at the top of the list "
            f"and {num(min(shares) * 200)}% at the bottom",
            "A team that missed the postseason — 0",
        ],
        [
            "The bonus is a rate, not a tally, so one brilliant playoff game is "
            "worth as much per game as a long run of them. What it cannot do is "
            "let a short postseason outweigh a whole season.",
            f"Why the spread. At {top}% the field is genuinely unknown at the "
            "draft. The mid-season leagues are drafted in July, when a manager "
            "can already see who is heading for the playoffs, so a run there is "
            "less of a discovery. European places are settled before the "
            "previous season ends, so there is nothing left to find out at all.",
            "MLS is on the mid-season leagues' calendar but not on their draft: "
            "its season opens in February, inside the league year, so an MLS "
            f"club is drafted before a ball is kicked and pays the full {top}%.",
            "For club soccer, European competition plays the part the playoffs "
            "play elsewhere. Domestic cups are different — the FA Cup, the Copa "
            "del Rey and the US Open Cup count in full, like league matches.",
            "This is for players only. Teams are paid for the postseason "
            "directly — appearances, wins and series, listed above.",
            "The NBA Play-In and European qualifying rounds count as neither a "
            "regular-season game nor a postseason one, and are thrown away.",
        ],
    )


# --- shapes for a bigger league -------------------------------------------
#
# None of this is in force. It is written down because it was worked out
# carefully, and a decision worked out carefully and then remembered from a
# conversation is a decision that gets re-litigated every August.

#: What a seat in the top flight is set to return, in expectation, when the
#: league runs two tiers at different buy-ins. Everything else in that
#: structure falls out of it, which is the point: fixed percentages only
#: balance at one ratio of tier sizes, and the sizes are not known in advance.
TOP_FLIGHT_PREMIUM = 0.06

#: Champion / runner-up / third, as shares of their own flight's prize pool.
TOP_FLIGHT_PLACES = (0.72, 0.21, 0.07)
BOTTOM_FLIGHT_PLACES = (0.63, 0.37)

#: A quarter's prize, in every structure. Held constant on purpose: it is what
#: makes the quarters mean the same thing however the league is organised, and
#: it is always league-wide, never inside a division.
QUARTER_SHARE = 0.05


def _two_tier_shares(n_top: int, n_bot: int,
                     top_buy: float = 100.0, bot_buy: float = 50.0):
    """Shares of the whole pot for a two-tier league of these sizes.

    Derived rather than declared. A fixed table balances at one ratio and
    inverts either side of it: 44/13/4 to the top and 12/7 to the bottom is
    right at six and five, and at seven and four the same numbers hand the
    bottom flight a better return than the top, because nineteen per cent of
    the pot split four ways beats it split five ways.
    """
    pot = n_top * top_buy + n_bot * bot_buy
    quarters = 4 * QUARTER_SHARE
    # The top flight is assumed to take about seven quarters in ten, being the
    # stronger field. It is an assumption and it only moves the split a little.
    top_pool = (n_top * top_buy * (1 + TOP_FLIGHT_PREMIUM)
                - pot * quarters * 0.70) / pot
    return top_pool, 1 - quarters - top_pool


def _structures() -> Rules:
    top, bottom = _two_tier_shares(6, 5)
    t = [top * share for share in TOP_FLIGHT_PLACES]
    b = [bottom * share for share in BOTTOM_FLIGHT_PLACES]
    return Rules(
        "structures", "If the league ever plays for money",
        "Nothing here is in force and nothing was played for this season. It "
        "is written down because it was worked out rather than guessed, and a "
        "decision remembered from a conversation is a decision argued again "
        "every August. Which shape applies depends on how many managers turn "
        "up, which is not knowable until they do.",
        [
            Heading("In every shape"),
            f"Each quarter — {num(QUARTER_SHARE * 100)}% of the pot, "
            f"{num(QUARTER_SHARE * 400)}% across the four",
            "Always league-wide, never inside a division: the quarters are "
            "what make two divisions one league",
            Heading("One division (eight managers or fewer)"),
            "Everyone pays the same, so every split returns the buy-in on "
            "average — the shape is only about how many people still have "
            "something to play for in April",
            "Under seven managers — champion 58%, runner-up 22%",
            "Seven or more — champion 50%, runner-up 18%, third 12%",
            "Two places under seven because three of five is most of the "
            "field, and a third place that pays back most of the buy-in is a "
            "refund rather than a prize",
            Heading("Two tiers, promotion and relegation (nine or more)"),
            "$100 in the top flight, $50 in the bottom",
            f"A top-flight seat is set to return {num(TOP_FLIGHT_PREMIUM * 100)}% "
            "in expectation; the bottom flight takes the rest, which lands "
            "between −12% and −21% depending on the sizes",
            f"At six and five that is champion {round(t[0] * 100)}%, runner-up "
            f"{round(t[1] * 100)}%, third {round(t[2] * 100)}% in the top "
            f"flight, and champion {round(b[0] * 100)}%, runner-up "
            f"{round(b[1] * 100)}% in the bottom",
            "The percentages are derived from the sizes each year, not fixed",
            Heading("Two parallel divisions of equal standing"),
            "Everyone pays $100; the divisions exist to keep each one small, "
            "not to rank them",
            "6% to the best total score in the league, either division",
            "The remaining 74% split between divisions in proportion to what "
            "each put in, then 76% to that division's champion and 24% to its "
            "runner-up",
            "Proportional because the bigger division's title is both worth "
            "more and harder to win, and those cancel exactly — so division "
            "size changes nobody's expected return",
        ],
        [
            "Why the quarters are the same everywhere. They are the one part "
            "of the pot a manager out of the title race is still playing for, "
            "and in a two-tier league they are the only prize the bottom "
            "flight competes for on level terms. Prorating them by buy-in was "
            "considered and rejected: it takes from the flight paying half and "
            "gives to the flight paying full, which is the opposite of what it "
            "looks like it does.",
            "Why the bottom flight still loses money. It puts in about 29% of "
            "the pot and can reach at most 30% of it, so a bottom-flight "
            "season is a bad bet in cash terms. That is deliberate — it is "
            "what relegation costs — and the real prize there is promotion. "
            "Making it an even bet would need a buy-in nearer $23 than $50.",
            "Why a third place at 4% is only just worth having. It is about "
            "$34 against a $100 buy-in. Folding it into the runner-up would "
            "pay two places out of six rather than three, which may read "
            "better.",
            "The quarters are already computed and shown on the standings "
            "page. Nothing on that page mentions money, because none of this "
            "is in force.",
        ],
    )


def structures() -> list[Rules]:
    """League shapes and prize splits, written down but not in force."""
    return [_structures()]


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
