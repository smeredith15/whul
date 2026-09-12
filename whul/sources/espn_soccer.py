"""Club soccer player stats, from ESPN's team rosters.

Twenty-eight rostered players had never been scored, because there was no
source. FBref -- whose column names the scorer was written against -- answers
403 to a datacenter address *and* to a laptop, so it is out rather than
pending, and the nightly pull runs on GitHub Actions anyway.

ESPN is the one host already known to answer from both. Three of its shapes
were probed:

    league statistics   1 request a league-season, missing cards and starts
    team roster        20 requests, missing only minutes
    match summary     380 requests, missing nothing

The roster wins. Minutes are wanted rather than needed: the scorer takes
per-match minutes where it has them and otherwise reads starts and
appearances, which the roster carries -- as ``appearances`` and ``subIns``,
so a start is the difference between them.

Twenty requests a league-season is about four minutes for a five-season,
six-league benchmark, against seventy-six for the same thing out of match
summaries. Match summaries remain the upgrade if the appearance approximation
ever looks wrong; the scorer reads exact minutes without being asked twice.

The payload shape, which was found rather than assumed::

    athlete.position.abbreviation                        "D"
    athlete.statistics.splits.categories[N].name         "general" | "offensive"
    athlete.statistics.splits.categories[N].stats[M].name   "appearances"
    athlete.statistics.splits.categories[N].stats[M].value   30.0
"""

from __future__ import annotations

import time

import pandas as pd
import requests

from whul.sources.espn import (
    BASE, LEAGUE_PATHS, REQUEST_PAUSE, SEASON_WINDOWS, TIMEOUT,
)

#: Where each figure the scorer needs lives, as ``category.stat``. Read by the
#: stat's own name rather than its position: the order of both the categories
#: and the stats within them varies by player -- a goalkeeper has a goalKeeping
#: category that an outfielder does not -- so an index would read the wrong
#: number for half a squad.
STAT_PATHS = {
    "appearances": ("general", "appearances"),
    "sub_ins": ("general", "subIns"),
    "goals": ("offensive", "totalGoals"),
    "assists": ("offensive", "goalAssists"),
    "yellow": ("general", "yellowCards"),
    "red": ("general", "redCards"),
}

#: ESPN's own goals stat is in the same category as the real ones and is named
#: similarly enough to be picked up by a looser match. Conceding one must never
#: be paid as scoring one.
NOT_A_GOAL = "ownGoals"


def roster_season(league: str, season: int) -> int:
    """Our season label, in ESPN's numbering.

    ESPN names a soccer season for the year it starts; we name it for the year
    it ends. A live run confirmed it: asked for 2021, the feed answered
    "2021-22 English Premier League". So every European league is one year
    apart from us, and MLS -- which runs inside a calendar year and answered
    "2021 MLS" -- is not.

    That distinction is already in ``SEASON_WINDOWS`` as "ends" against
    "within", so it is derived rather than listed and a league added later is
    right without anyone remembering this.

    The cost of getting it wrong is not the benchmark, where five consecutive
    seasons are five consecutive seasons. It is the live pull: our 2026-27 is
    2027, and asking ESPN for 2027 returns 2027-28, a season nobody has played.
    Every rostered player would score zero and the run would look like it had
    worked.
    """
    numbering = SEASON_WINDOWS.get(league, ((), (), "within"))[2]
    return season - 1 if numbering == "ends" else season


def season_matches(league: str, season: int, said: str) -> bool:
    """Does the label the feed returned describe the season we meant?

    Deliberately strict about which year it must start with. The first version
    of this check accepted a label beginning with either the year asked for or
    the year before, which is to say it accepted both conventions and so
    detected neither -- it passed on the very shift it was written to find.
    """
    if not said:
        return True
    return said.strip().startswith(str(roster_season(league, season)))


def _get(url: str, params: dict, session=None) -> dict:
    """Fetch, with the request shape that is known to work unattended.

    No custom User-Agent. ``whul.sources.espn`` sends none and is pulled from
    GitHub Actions every night without trouble; this module sent one and drew a
    403 from the same runner on the same host. That is one difference fewer,
    not a proven cause -- but the module that works is the one worth copying.
    """
    getter = session.get if session is not None else requests.get
    response = getter(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    time.sleep(REQUEST_PAUSE)
    return payload


def _athletes(payload: dict) -> list[dict]:
    """Every athlete, however the payload groups them.

    ESPN groups a soccer roster by position, so the athletes are one level
    further in than for the sports that do not.
    """
    found = []
    for entry in payload.get("athletes") or []:
        if isinstance(entry, dict) and isinstance(entry.get("items"), list):
            found += [a for a in entry["items"] if isinstance(a, dict)]
        elif isinstance(entry, dict):
            found.append(entry)
    return found


def _stats(athlete: dict) -> dict[str, float]:
    """``{category.stat: value}`` for one athlete.

    Empty where the athlete has no statistics block at all, which is ordinary:
    a squad player who has not appeared has nothing to report, and reading that
    as zeroes rather than as absence is the same answer here.
    """
    stats = athlete.get("statistics")
    if isinstance(stats, list):
        stats = stats[0] if stats else None
    if not isinstance(stats, dict):
        return {}
    splits = stats.get("splits")
    if isinstance(splits, list):
        splits = splits[0] if splits else None
    if not isinstance(splits, dict):
        return {}

    out: dict[str, float] = {}
    for category in splits.get("categories") or []:
        if not isinstance(category, dict):
            continue
        group = str(category.get("name") or "")
        for stat in category.get("stats") or []:
            if not isinstance(stat, dict):
                continue
            name = str(stat.get("name") or "")
            if not name:
                continue
            try:
                out[f"{group}.{name}"] = float(stat.get("value"))
            except (TypeError, ValueError):
                continue
    return out


def season_label(payload: dict) -> str:
    """What season the feed says it answered with, if it says.

    Asked for rather than deduced. ESPN's own numbering could name a season for
    the year it starts where ours names it for the year it ends, and a
    one-year shift would fill every benchmark season with the wrong year's
    football -- every figure still a real footballer's real season, and nothing
    anywhere reading as wrong.
    """
    for holder in (payload, payload.get("team") or {}):
        block = holder.get("season")
        if isinstance(block, dict):
            for key in ("displayName", "name", "year", "id"):
                if block.get(key):
                    return str(block[key])
        if isinstance(block, (str, int)):
            return str(block)
    return ""


def load_squad(
    league: str, team_id: str, season: int, session=None
) -> pd.DataFrame:
    """One club's players for one season, in the shape the scorer reads.

    ``season`` is our label throughout -- 2027 is 2026-27 -- and the
    translation into ESPN's numbering happens at the request. The rows come
    back tagged with ours, so nothing downstream has to know the difference.
    """
    sport, path = LEAGUE_PATHS[league]
    payload = _get(
        f"{BASE}/{sport}/{path}/teams/{team_id}/roster",
        {"season": roster_season(league, season)}, session,
    )
    club = str((payload.get("team") or {}).get("displayName", ""))
    said = season_label(payload)

    rows = []
    for athlete in _athletes(payload):
        stats = _stats(athlete)
        # A squad player with no statistics block has not appeared. He is still
        # emitted, as zeroes, because "in the squad and yet to play" and "the
        # feed does not know this name" are different problems with different
        # fixes -- and dropping him makes them read identically. He scores
        # nothing either way, and sorts below everyone in a benchmark pool.
        appearances = stats.get(".".join(STAT_PATHS["appearances"]), 0.0)
        sub_ins = stats.get(".".join(STAT_PATHS["sub_ins"]), 0.0)
        rows.append({
            "player": str(athlete.get("displayName") or athlete.get("fullName") or ""),
            "player_id": str(athlete.get("id") or ""),
            "team": club,
            # ESPN's club id is global, so the same club carries it in its
            # league's request and in the Champions League's. That is what lets
            # a shared competition be attributed to the club's own league
            # rather than to whichever league happened to ask for it first.
            "team_id": str(team_id),
            "season": int(season),
            "season_said": said,
            "position": str((athlete.get("position") or {}).get("abbreviation") or ""),
            "matches": appearances,
            # A start is an appearance that did not begin on the bench. ESPN
            # gives the substitute count, never the starts, so this is the
            # subtraction the scorer's season path wants.
            "starts": max(appearances - sub_ins, 0.0),
            "goals": stats.get(".".join(STAT_PATHS["goals"]), 0.0),
            "assists": stats.get(".".join(STAT_PATHS["assists"]), 0.0),
            "yellow": stats.get(".".join(STAT_PATHS["yellow"]), 0.0),
            "red": stats.get(".".join(STAT_PATHS["red"]), 0.0),
        })
    return pd.DataFrame(rows)


def _clubs_in(payload: dict) -> dict[str, str]:
    """``{club: espn id}`` out of a teams payload."""
    out = {}
    try:
        entries = payload["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError, TypeError):
        return out
    for entry in entries:
        team = (entry or {}).get("team") or {}
        if team.get("id") and team.get("displayName"):
            out[str(team["displayName"])] = str(team["id"])
    return out


def team_ids(
    league: str, season: int, session=None, note: list | None = None
) -> dict[str, str]:
    """``{club: espn id}`` for one league and season.

    Never raises. A league whose club list cannot be fetched is one league that
    scores nothing, and the five others in the same run should not go with it --
    which is exactly what happened when a 403 on the Premier League's club list
    took every club soccer player down with it.

    Two request shapes, because a 403 does not say what it objects to. If the
    seasoned request is refused, the bare one is tried: it answers with the
    *current* club list, which for the season in progress is the same list and
    for a past season is not. That substitution is reported rather than made
    quietly -- promotion and relegation mean a bare list would attribute the
    wrong clubs to an older season, which matters for a benchmark and not at
    all for tonight's results.
    """
    sport, path = LEAGUE_PATHS[league]
    url = f"{BASE}/{sport}/{path}/teams"
    asked = roster_season(league, season)
    said: list[str] = []
    for params in ({"season": asked}, {}):
        try:
            clubs = _clubs_in(_get(url, params, session))
        except Exception as exc:  # noqa: BLE001 -- one league, not the run
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            shape = f"season={asked}" if params else "no season"
            said.append(f"{shape}: {type(exc).__name__} {status}")
            continue
        if not clubs:
            said.append(f"{'season=' + str(asked) if params else 'no season'}: "
                        f"answered, but listed no clubs")
            continue
        if not params:
            said.append(
                f"the {asked} club list was refused, so this is the current "
                f"one -- right for a season in progress, and wrong by however "
                f"many clubs were promoted or relegated for an older one"
            )
        if note is not None:
            note.extend(said)
        return clubs
    if note is not None:
        note.extend(said)
    return {}


def load_players(
    league: str, seasons: list[int], verbose: bool = True, session=None
) -> pd.DataFrame:
    """Every player in a league, for each season given.

    One request for the club list and one per club, so a league-season is
    twenty-one requests and a five-season benchmark about a hundred.

    A club that fails is reported and skipped rather than taking the league
    down with it: nineteen clubs' players are worth more than none, and the
    league that lost one says so.
    """
    session = session or requests.Session()
    frames = []
    for season in seasons:
        why: list[str] = []
        clubs = team_ids(league, season, session, note=why)
        if verbose:
            for line in why:
                print(f"    {league} {season}: {line}", flush=True)
        if not clubs:
            if verbose:
                print(f"  {league} {season}: no clubs listed, so no players",
                      flush=True)
            continue
        if verbose:
            print(f"  {league} {season}: {len(clubs)} club(s) ...", flush=True)
        failed = []
        for club, team_id in clubs.items():
            try:
                frames.append(load_squad(league, team_id, season, session))
            except Exception as exc:  # noqa: BLE001 -- one club, not the league
                failed.append((club, type(exc).__name__))
        if not verbose or not failed:
            continue
        # Every club failing is one fact, not thirty. Thirty lines of HTTPError
        # read like a broken adapter; what they actually mean is that a season
        # has not been played, and the endpoint says so by 404ing every roster
        # in it while still listing the clubs.
        if len(failed) == len(clubs):
            print(f"    every club failed ({failed[0][1]}). ESPN lists the clubs "
                  f"for {roster_season(league, season)} but has no roster in it, "
                  f"which is what a season nobody has played looks like.",
                  flush=True)
        else:
            for club, kind in failed[:8]:
                print(f"    {club} failed: {kind}", flush=True)
            if len(failed) > 8:
                print(f"    ... and {len(failed) - 8} more", flush=True)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


#: Where an athlete's own record might live. ESPN's player pages show "stats by
#: competition", so the data exists somewhere; which endpoint serves it, and
#: under what shape, is what this probe is for. UNVERIFIED -- none of these is
#: reachable from the environment this was written in.
#:
#: ``{name: (host, path suffix)}``. The common/v3 host is the one ESPN's own
#: player pages call; the site/v2 host is the one the rest of this project
#: uses, and is tried in case v3 is refused.
ATHLETE_SHAPES = {
    "overview": ("https://site.api.espn.com/apis/common/v3", ""),
    "stats": ("https://site.api.espn.com/apis/common/v3", "/stats"),
    "splits": ("https://site.api.espn.com/apis/common/v3", "/splits"),
    "gamelog": ("https://site.web.api.espn.com/apis/common/v3", "/gamelog"),
    "site overview": ("https://site.api.espn.com/apis/site/v2", ""),
}


def _competition_names(node, found: set, depth: int = 0) -> set:
    """Every competition-ish name anywhere in a payload.

    Deliberately shape-blind. The question this answers is "does this response
    know which competition a number belongs to", and a probe that only looked
    where the answer was expected would report absence for a payload that had
    it somewhere else.
    """
    if depth > 8:
        return found
    if isinstance(node, dict):
        for key in ("league", "competition", "displayName", "name", "abbreviation"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                found.add(value.strip())
            elif isinstance(value, dict):
                _competition_names(value, found, depth + 1)
        for value in node.values():
            if isinstance(value, (dict, list)):
                _competition_names(value, found, depth + 1)
    elif isinstance(node, list):
        for value in node[:40]:
            _competition_names(value, found, depth + 1)
    return found


def probe_athlete(
    league: str, athlete_id: str | None = None, season: int | None = None,
    club: str | None = None, session=None,
) -> dict:
    """Whether an athlete's own record says which competition a figure is from.

    The roster gives a player one statistics block for a season, and it is not
    always the competition that was asked for: Bayern's Bundesliga roster
    briefly returned Harry Kane with the Champions League match in his
    appearances, so it was counted as domestic football *and* held as a European
    bonus, and the next night it was not. Nothing in that payload could have
    told the two apart, because the payload has no competitions in it.

    An athlete endpoint that splits by competition would end that: a figure
    would carry its own competition rather than inheriting whichever request
    fetched it. This asks the shapes ESPN's own player pages are built from and
    reports what each returned -- the keys near the top, whether anything in it
    names more than one competition, and which names those are.

    Nothing here is wired into scoring. It is a question, and the answer
    decides what to build.
    """
    session = session or requests.Session()
    sport, path = LEAGUE_PATHS[league]
    if athlete_id is None:
        athlete_id, club = _some_athlete(league, season, club, session)
    out: dict = {
        "league": league, "path": path, "athlete_id": athlete_id,
        "club": club, "season": season, "shapes": {},
    }
    if athlete_id is None:
        out["problem"] = ("no athlete id: the club list or the roster could not "
                          "be read, so there was nobody to ask about")
        return out

    for label, (host, suffix) in ATHLETE_SHAPES.items():
        url = f"{host}/sports/{sport}/{path}/athletes/{athlete_id}{suffix}"
        params = {"season": roster_season(league, season)} if season else {}
        entry: dict = {"url": url, "params": dict(params)}
        try:
            payload = _get(url, params, session)
        except Exception as exc:  # noqa: BLE001 -- one shape, not the probe
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            entry["error"] = f"{type(exc).__name__} {status}"
            out["shapes"][label] = entry
            continue
        entry["keys"] = sorted(payload)[:20]
        names = sorted(_competition_names(payload, set()))
        entry["names"] = names[:30]
        entry["competitions"] = sorted(
            n for n in names
            if any(word in n.lower() for word in
                   ("league", "cup", "liga", "serie", "bundesliga", "ligue",
                    "uefa", "champions", "europa", "conference", "pokal",
                    "coppa", "copa", "coupe", "fa "))
        )[:20]
        entry["splits_by"] = _splits_shape(payload)
        if label == "overview":
            entry["overview"] = overview_report(payload)
        # The gamelog is the shape that could attribute a match, so it is the
        # one worth reading properly rather than counting names in.
        if label == "gamelog":
            entry["gamelog"] = gamelog_report(payload)
            # The filter names the competitions this player has appeared in,
            # which is exactly the list to ask for one at a time.
            # The `league` parameter does not filter: eight different values
            # each returned the same single Champions League match. So the
            # competition is varied in the path instead, which is where the
            # rest of ESPN's soccer API puts it.
            out["by_path"] = probe_gamelog_paths(athlete_id, season,
                                                 session=session)
        out["shapes"][label] = entry
    return out


def _splits_shape(payload: dict) -> list[str]:
    """How many statistics blocks the payload holds, and what labels them.

    One block is a season aggregate and cannot answer the question. Several,
    each with a name, is the competition breakdown this is looking for.
    """
    labels: list[str] = []
    for key in ("statistics", "splits", "seasonTypes", "categories", "entries"):
        node = payload.get(key)
        if isinstance(node, list) and node:
            for item in node[:12]:
                if isinstance(item, dict):
                    for naming in ("displayName", "name", "abbreviation", "type"):
                        if isinstance(item.get(naming), str):
                            labels.append(f"{key}[].{naming}={item[naming]}")
                            break
        elif isinstance(node, dict):
            inner = node.get("splits")
            if isinstance(inner, list):
                labels.append(f"{key}.splits is a list of {len(inner)}")
            elif isinstance(inner, dict):
                labels.append(f"{key}.splits is one block")
    return labels[:20]


def _some_athlete(league, season, club, session):
    """Any athlete in the league, to ask the question about."""
    try:
        clubs = team_ids(league, season or 0, session)
    except Exception:  # noqa: BLE001
        return None, None
    if not clubs:
        return None, None
    name, team_id = next(
        ((n, i) for n, i in clubs.items() if club and club.lower() in n.lower()),
        next(iter(clubs.items())),
    )
    sport, path = LEAGUE_PATHS[league]
    try:
        payload = _get(f"{BASE}/{sport}/{path}/teams/{team_id}/roster",
                       {"season": roster_season(league, season)} if season else {},
                       session)
    except Exception:  # noqa: BLE001
        return None, name
    for athlete in _athletes(payload):
        if athlete.get("id"):
            return str(athlete["id"]), name
    return None, name


def gamelog_report(payload: dict) -> dict:
    """What a gamelog actually holds, in the terms a loader would need.

    The first probe established that this shape exists and names more than one
    competition. What it could not say is whether an *event* carries its own
    competition, which is the only thing that matters: a payload that groups by
    competition at the top and then hands back a flat list of matches is no
    better than the roster.

    So this reads the parts a loader would read -- the filters the endpoint
    accepts, how the season types are grouped and how many matches each holds,
    the stat labels in order, and a handful of whole events with the keys they
    carry.
    """
    out: dict = {}

    filters = payload.get("filters")
    if isinstance(filters, list):
        out["filters"] = [
            {
                "name": f.get("name"),
                "value": f.get("value"),
                "options": [
                    {"value": o.get("value"), "label": o.get("displayName") or o.get("label")}
                    for o in (f.get("options") or [])[:25]
                    if isinstance(o, dict)
                ],
            }
            for f in filters[:8] if isinstance(f, dict)
        ]

    # The stat column headers, in the order the numbers arrive in.
    for key in ("labels", "names", "displayNames"):
        value = payload.get(key)
        if isinstance(value, list):
            out[key] = [str(v) for v in value[:30]]

    types = payload.get("seasonTypes")
    if isinstance(types, list):
        out["seasonTypes"] = []
        for entry in types[:12]:
            if not isinstance(entry, dict):
                continue
            groups = entry.get("categories") or entry.get("events") or []
            counted = 0
            if isinstance(groups, list):
                for group in groups:
                    inner = (group.get("events") if isinstance(group, dict) else None)
                    counted += len(inner) if isinstance(inner, list) else 1
            out["seasonTypes"].append({
                "displayName": entry.get("displayName"),
                "name": entry.get("name"),
                "keys": sorted(k for k in entry if k not in ("categories", "events")),
                "matches": counted,
            })

    # Where the numbers actually live. `events` is match metadata keyed by id;
    # the stat rows sit under the season types, referencing an event by id.
    # A row is what a loader would read, so one is carried out whole.
    for entry in (payload.get("seasonTypes") or [])[:4]:
        if not isinstance(entry, dict):
            continue
        for group in (entry.get("categories") or [])[:4]:
            rows = (group.get("events") if isinstance(group, dict) else None) or []
            if rows and isinstance(rows[0], dict):
                out["stat_row"] = rows[0]
                out["stat_row_keys"] = sorted(rows[0])
                break
        if out.get("stat_row"):
            break

    events = payload.get("events")
    if isinstance(events, dict):
        out["events_shape"] = f"dict keyed by id, {len(events)} entries"
        sample = list(events.values())[:4]
    elif isinstance(events, list):
        out["events_shape"] = f"list of {len(events)}"
        sample = events[:4]
    else:
        sample = []
    out["event_keys"] = sorted({k for e in sample if isinstance(e, dict) for k in e})
    out["events"] = [
        {k: (v if not isinstance(v, (dict, list)) else
             {kk: vv for kk, vv in list(v.items())[:6]} if isinstance(v, dict)
             else v[:8])
         for k, v in e.items()}
        for e in sample if isinstance(e, dict)
    ]
    return out


def competition_of(espn_key: str) -> str | None:
    """ESPN's own key for a competition, as the key this project scores it by.

    The gamelog names the competition on every event -- `ger.dfb_pokal`,
    `uefa.champions` -- and those are ESPN's spellings, not ours. Feeding them
    to the classifier unmapped is not a near miss: every unknown key falls
    through to a *league* match worth three points and counted in the base
    score, so a Champions League night would be paid as a league win and folded
    into the total the benchmark measures, which is the fault the gamelog is
    being read to fix, made worse.

    Inverted from ``LEAGUE_PATHS`` rather than written out again. That table
    already says which ESPN path each competition is fetched from; a second
    copy of it would be right on the day it was written and wrong on the day a
    competition was added to one of them.
    """
    for key, (_, path) in LEAGUE_PATHS.items():
        if path == espn_key:
            return key
    return None


#: Competitions the gamelog will name that this project does not score.
#: Friendlies are not competitive football. The rest are one-off finals that
#: are neither the domestic league, nor a domestic cup, nor one of UEFA's three
#: -- and what they are worth is a rules question rather than a mapping, so
#: they are left out and reported rather than guessed at.
#:
#: Anything here scores nothing. Anything *not* here and not in LEAGUE_PATHS is
#: reported too, because a competition nobody has decided about must not be
#: quietly paid as a league match.
UNSCORED_COMPETITIONS = {
    "club.friendly": "a friendly",
    "ger.super_cup": "a domestic super cup",
    "esp.super_cup": "a domestic super cup",
    "ita.super_cup": "a domestic super cup",
    "fra.super_cup": "a domestic super cup",
    "eng.charity": "a domestic super cup",
    "uefa.super_cup": "the UEFA Super Cup",
    "fifa.cwc": "the Club World Cup",
    "global.champs_cup": "a cross-confederation cup",
    "concacaf.leagues.cup": "a cross-confederation cup",
}


def classify_gamelog_league(espn_key: str) -> tuple[str | None, str]:
    """``(our key, why)`` for a competition the gamelog named.

    A key this project scores returns its own name. One deliberately left out
    returns None and says what it is. One nobody has seen before returns None
    too -- the safe direction, because the unsafe one pays it as a league match
    and nothing says so.
    """
    ours = competition_of(espn_key)
    if ours:
        return ours, "scored"
    if espn_key in UNSCORED_COMPETITIONS:
        return None, f"not scored: {UNSCORED_COMPETITIONS[espn_key]}"
    return None, ("not scored: this project has never seen this competition, and "
                  "an unmapped key would otherwise be paid as a league win")


def probe_gamelog_by_competition(
    league: str, athlete_id: str, season: int | None = None,
    keys: list[str] | None = None, session=None,
) -> dict:
    """Ask the gamelog for one competition at a time.

    A bare request returned a single match -- the Champions League tie -- for a
    player who had also played twice in the Bundesliga and once in the cup, so
    whatever a bare request means, it is not "everything". The filter lists the
    competitions the player has appeared in, which is exactly the list to ask
    for one by one.

    Reports, per competition: how many matches came back, which competitions
    the returned events actually name -- a filter that does not filter would
    show the others here -- and whether the stat rows carry appearances or
    starts, which the scorer needs and the labels so far have not offered.
    """
    session = session or requests.Session()
    sport, path = LEAGUE_PATHS[league]
    url = (f"https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{path}"
           f"/athletes/{athlete_id}/gamelog")
    out: dict = {"url": url, "asked": {}}
    for key in keys or []:
        params: dict = {"league": key}
        if season:
            params["season"] = roster_season(league, season)
        entry: dict = {"params": dict(params), "our_key": competition_of(key)}
        entry["scored"] = classify_gamelog_league(key)[1]
        try:
            payload = _get(url, params, session)
        except Exception as exc:  # noqa: BLE001 -- one competition, not the probe
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            entry["error"] = f"{type(exc).__name__} {status}"
            out["asked"][key] = entry
            continue
        report = gamelog_report(payload)
        events = payload.get("events")
        found = events.values() if isinstance(events, dict) else (events or [])
        entry["matches"] = len(list(found))
        entry["named"] = sorted({
            str(e.get("leagueName") or e.get("leagueShortName") or "")
            for e in found if isinstance(e, dict)
        })
        entry["labels"] = report.get("labels") or []
        entry["names"] = report.get("names") or []
        entry["stat_row_keys"] = report.get("stat_row_keys") or []
        entry["seasonTypes"] = [
            f"{t.get('displayName')} ({t.get('matches')})"
            for t in report.get("seasonTypes") or []
        ]
        out["asked"][key] = entry
    return out


#: What the scorer needs from a player and cannot yet see in a gamelog label.
#: An appearance is implied -- one event is one match -- but a *start* is not,
#: and appearance points are 2 for a start and 1 for coming off the bench. The
#: roster derives it as appearances minus substitute appearances; if no gamelog
#: field carries it, the roster stays the source for that one figure.
NEEDED_FROM_A_MATCH = ("appearances", "subIns", "substitute", "starts", "minutes")


def overview_report(payload: dict) -> dict:
    """Every statistics block in an athlete overview, and what names it.

    The overview named "2026-27 Bundesliga Stats" among its competitions, which
    is the shape a per-competition season total would wear. If it holds one
    block per competition then a player costs one request and every figure
    arrives already attributed -- which is what the roster cannot do and the
    gamelog, having ignored its own league filter, cannot either.

    Walks for anything holding a list of named stat blocks rather than reading
    a path, because the path is what is being discovered.
    """
    out: dict = {"blocks": []}

    def walk(node, trail, depth=0):
        if depth > 7 or len(out["blocks"]) > 30:
            return
        if isinstance(node, dict):
            names = node.get("names") or node.get("labels")
            stats = node.get("stats") or node.get("splits")
            title = (node.get("displayName") or node.get("name")
                     or node.get("shortDisplayName"))
            if title and (names or stats):
                entry = {"at": ".".join(trail)[:60], "title": str(title)}
                if isinstance(names, list):
                    entry["labels"] = [str(n) for n in names[:14]]
                if isinstance(stats, list) and stats:
                    entry["values"] = [str(v)[:14] for v in stats[:14]
                                       if not isinstance(v, (dict, list))]
                    entry["stat_entries"] = len(stats)
                out["blocks"].append(entry)
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    walk(value, trail + [key], depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node[:12]):
                walk(value, trail + [f"[{index}]"], depth + 1)

    walk(payload, [])
    return out


#: Where the competition might live for a gamelog, since the `league` query
#: parameter demonstrably does not filter: every one of eight values returned
#: the same single Champions League match. The rest of ESPN's soccer API puts
#: the competition in the *path* -- `/soccer/ger.dfb_pokal/teams/...` -- so the
#: path is the next thing to vary, and it is varied against no season, our
#: season, and the season with a league parameter, because which of the three
#: the endpoint wants is exactly what is unknown.
GAMELOG_TRIALS = ("ger.1", "ger.dfb_pokal", "uefa.champions")


def probe_gamelog_paths(
    athlete_id: str, season: int | None = None, paths: tuple = GAMELOG_TRIALS,
    session=None,
) -> dict:
    """Vary the competition in the path rather than in a parameter."""
    session = session or requests.Session()
    out: dict = {"tried": []}
    for path in paths:
        for label, params in (
            ("no season", {}),
            ("season", {"season": season} if season else {}),
            ("season+league", {"season": season, "league": path} if season else {}),
        ):
            url = (f"https://site.web.api.espn.com/apis/common/v3/sports/soccer/"
                   f"{path}/athletes/{athlete_id}/gamelog")
            entry = {"path": path, "shape": label, "params": dict(params)}
            try:
                payload = _get(url, params, session)
            except Exception as exc:  # noqa: BLE001 -- one trial, not the probe
                status = getattr(getattr(exc, "response", None), "status_code", "?")
                entry["error"] = f"{type(exc).__name__} {status}"
                out["tried"].append(entry)
                continue
            events = payload.get("events")
            found = list(events.values()) if isinstance(events, dict) else (events or [])
            entry["matches"] = len(found)
            entry["named"] = sorted({
                str(e.get("leagueName") or "") for e in found if isinstance(e, dict)
            } - {""})
            entry["labels"] = [str(n) for n in (payload.get("names") or [])[:12]]
            out["tried"].append(entry)
    return out
