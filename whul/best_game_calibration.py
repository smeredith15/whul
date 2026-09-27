"""What k makes a best-game slot worth the same in MLB as in the NFL.

The slot is proposed at face value -- a game's points over the frozen season
divisor, the units a season is in -- with k set per sport so that the average
draft-caliber player's k best games come to the same figure everywhere. The
other sports were measured from their own archives; MLB needed a game log,
which the Stats API serves and ``probe mlb-gamelog`` confirmed is complete.

This measures MLB's three roles -- batters, starts and relief appearances --
against the NFL in one run, with the NFL recomputed here rather than typed in,
so both sides of the comparison come from the same method and the same frozen
divisors. Games are scored through ``whul.scoring.best_game``, so what is
calibrated is exactly the rule as agreed:

* counting stats only, at face value, no year multiplier;
* a two-way player's game split 1x / 0.5x by which role led that game;
* starts and relief appearances segregated, a pitcher's slot being whichever
  of his best n starts or best m appearances is larger.

Population: per season and group, the draft-caliber players -- the top N by
points per game among those past a minimum, N being the frozen benchmark
pool's size per season. Ranked by rate rather than total so an injured good
player is in it, which is the case the slot exists for. Starters and relievers
are ranked separately, each against its own kind: ranked together by points
per game, short outings would push nearly every reliever out, elite closers
included.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from whul.scoring import best_game as rules
from whul.scoring import mlb as mlb_scoring
from whul.scoring import nfl as nfl_scoring
from whul.scoring.base import resolve_num, resolve_str

KS = (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40)
SEASONS = (2021, 2022, 2023, 2024, 2025)

#: Games before a rate means anything. Three NFL weeks is the floor the
#: cross-sport table used; the MLB floors are the same idea at baseball's scale.
MIN_GAMES = {"NFL": 3, "Batter": 20, "Starter": 5, "Reliever": 15}

#: Who counts as two-way in a season: the probe's threshold.
TWO_WAY_PA, TWO_WAY_IP = 100, 20.0

MLB_GROUPS = ("Batter", "Starter", "Reliever")


@dataclass
class Scale:
    """The frozen divisors, and how many players a season's pool holds."""

    divisor: dict[str, float]
    per_season: dict[str, int]


def frozen_scale(store, season_label: str) -> Scale:
    from whul.store import benchmarks as bm

    version = bm.active_version(store, season_label)
    if version is None:
        raise RuntimeError(f"no frozen benchmark for {season_label}")
    frame = bm.load(store, version.version)
    frame = frame[frame["asset_type"] == "Player"]
    divisor, per_season = {}, {}
    for row in frame.itertuples():
        seasons = [s for s in str(row.seasons).split(",") if s]
        divisor[row.norm_key] = float(row.benchmark)
        per_season[row.norm_key] = int(round(row.pool_size / max(len(seasons), 1)))
    return Scale(divisor, per_season)


def prefix(scores, ks=KS) -> dict[int, float]:
    """``best_k`` at every k at once."""
    return {k: rules.best_k(scores, k) for k in ks}


def equalising_k(means: dict[int, float], target: float) -> float:
    """The k at which a group's mean best-k reaches ``target``, interpolated.

    NaN if even the largest k measured falls short, which is itself the
    finding: that group's slot cannot be made worth the target at any k tried.
    """
    ks = sorted(means)
    if means[ks[0]] >= target:
        return ks[0] * target / means[ks[0]] if means[ks[0]] else math.nan
    for lo, hi in zip(ks, ks[1:]):
        if means[hi] >= target:
            return lo + (target - means[lo]) * (hi - lo) / (means[hi] - means[lo])
    return math.nan


# --- the anchor ---------------------------------------------------------------

def nfl_rows(scale: Scale, seasons=SEASONS, loader=None) -> pd.DataFrame:
    """Draft-caliber NFL player-seasons, with best-k on the league scale."""
    if loader is None:
        from whul.sources.nflverse import load_player_stats as loader
    raw = loader(list(seasons))
    raw = raw[resolve_str(raw, ["season_type"], default="REG") == "REG"]
    work = pd.DataFrame({
        "season": resolve_num(raw, ["season"]).astype(int),
        "player": resolve_str(raw, ["player_id", "gsis_id"]),
        "name": resolve_str(raw, ["player_display_name", "player_name"]),
        "position": resolve_str(raw, ["position", "position_group"]),
        "passing_yards": resolve_num(raw, ["passing_yards"]),
        "passing_tds": resolve_num(raw, ["passing_tds"]),
        "interceptions": resolve_num(raw, ["passing_interceptions", "interceptions"]),
        "rushing_yards": resolve_num(raw, ["rushing_yards"]),
        "rushing_tds": resolve_num(raw, ["rushing_tds"]),
        "receptions": resolve_num(raw, ["receptions"]),
        "receiving_yards": resolve_num(raw, ["receiving_yards"]),
        "receiving_tds": resolve_num(raw, ["receiving_tds"]),
        "fumbles_lost": (resolve_num(raw, ["sack_fumbles_lost"])
                         + resolve_num(raw, ["rushing_fumbles_lost"])
                         + resolve_num(raw, ["receiving_fumbles_lost"])),
    })
    work = work[work["position"].isin(nfl_scoring.SCORING_POSITIONS)]
    work["points"] = sum(work[c] * w for c, w in nfl_scoring.PLAYER_WEIGHTS.items())
    work["key"] = "NFL_" + work["position"]

    rows = []
    for (season, key), block in work.groupby(["season", "key"]):
        divisor = scale.divisor[key]
        games = block.groupby("player")["points"].agg(["size", "mean"])
        games = games[games["size"] >= MIN_GAMES["NFL"]]
        chosen = games.sort_values("mean", ascending=False).head(scale.per_season[key])
        for player in chosen.index:
            mine = 100 * block.loc[block["player"] == player, "points"] / divisor
            rows.append({"season": season, "group": "NFL", "key": key,
                         "player": player, "games": len(mine),
                         **{f"best{k}": v for k, v in prefix(mine).items()}})
    return pd.DataFrame(rows)


# --- MLB ----------------------------------------------------------------------

@dataclass
class Subject:
    player_id: str
    name: str
    season: int
    group: str
    two_way: bool = False


def _line_points(lines: pd.DataFrame, group: str) -> dict[str, float]:
    """Season counting points per player id, from the league's own scorer."""
    from whul.sources import mlb

    if lines.empty:
        return {}
    frame = lines.copy()
    if group == "pitching":
        frame["IP"] = frame["inningsPitched"].map(mlb.innings_to_float)
        frame = frame.rename(columns=mlb.PITCHER_COLUMNS)
        score = mlb_scoring.score_pitchers
    else:
        frame = frame.rename(columns=mlb.BATTER_COLUMNS)
        score = mlb_scoring.score_batters
    # The scorer keys its rows by the name it is given and drops the ones that
    # did nothing, so the id rides in the name to come back attached.
    frame["PlayerName"] = frame["player_id"].astype(str)
    scored = score(frame, include_advanced=False)
    return dict(zip(scored["player"].astype(str), scored["role_points"]))


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame.get(column, pd.Series(0, index=frame.index)),
                         errors="coerce").fillna(0)


def mlb_population(season: int, scale: Scale, loader=None) -> list[Subject]:
    """Draft-caliber batters, starters and relievers for one season."""
    from whul.sources import mlb

    loader = loader or mlb.load_stats_api_players
    hitting = loader(season, "hitting")
    pitching = loader(season, "pitching")
    if hitting.empty or pitching.empty:
        return []

    innings = pitching.get("inningsPitched", pd.Series(0, index=pitching.index)
                           ).map(mlb.innings_to_float)
    batted = set(hitting.loc[_num(hitting, "plateAppearances") >= TWO_WAY_PA,
                             "player_id"].astype(str))
    pitched = set(pitching.loc[innings >= TWO_WAY_IP, "player_id"].astype(str))
    two_way = batted & pitched

    names = {**dict(zip(hitting["player_id"].astype(str), hitting["player"])),
             **dict(zip(pitching["player_id"].astype(str), pitching["player"]))}
    bat_games = dict(zip(hitting["player_id"].astype(str), _num(hitting, "gamesPlayed")))
    pit_games = dict(zip(pitching["player_id"].astype(str), _num(pitching, "gamesPitched")))

    # A two-way player is calibrated once, in the group where most of his games
    # were: counting him in two groups would put his combined games in both.
    majority = {pid: ("Batter" if bat_games.get(pid, 0) >= pit_games.get(pid, 0)
                      else "Pitcher") for pid in two_way}

    subjects: list[Subject] = []

    bat_points = _line_points(hitting, "hitting")
    batters = pd.DataFrame({
        "pid": hitting["player_id"].astype(str),
        "games": _num(hitting, "gamesPlayed"),
    })
    batters["rate"] = (batters["pid"].map(lambda p: bat_points.get(p, 0.0))
                       / batters["games"].clip(lower=1))
    batters = batters[(batters["games"] >= MIN_GAMES["Batter"])
                      & ~batters["pid"].map(lambda p: majority.get(p) == "Pitcher")]
    for pid in batters.sort_values("rate", ascending=False).head(
            scale.per_season["MLB_Batter"])["pid"]:
        subjects.append(Subject(pid, names.get(pid, pid), season, "Batter", pid in two_way))

    pit_points = _line_points(pitching, "pitching")
    arms = pd.DataFrame({
        "pid": pitching["player_id"].astype(str),
        "games": _num(pitching, "gamesPitched"),
        "starts": _num(pitching, "gamesStarted"),
    })
    arms["rate"] = (arms["pid"].map(lambda p: pit_points.get(p, 0.0))
                    / arms["games"].clip(lower=1))
    arms["role"] = ["Starter" if 2 * s >= g and s > 0 else "Reliever"
                    for s, g in zip(arms["starts"], arms["games"])]
    arms = arms[~arms["pid"].map(lambda p: majority.get(p) == "Batter")]
    for role in ("Starter", "Reliever"):
        pool = arms[arms["role"] == role]
        floor = pool["starts"] if role == "Starter" else pool["games"]
        pool = pool[floor >= MIN_GAMES[role]]
        for pid in pool.sort_values("rate", ascending=False).head(
                scale.per_season["MLB_Pitcher"])["pid"]:
            subjects.append(Subject(pid, names.get(pid, pid), season, role, pid in two_way))
    return subjects


def mlb_games(subject: Subject, scale: Scale, log_loader=None) -> pd.DataFrame:
    """One row per game: its role -- bat, start or relief -- and its score.

    A two-way player's game has both lines, split 1x / 0.5x by which led that
    game. Anyone else is read for the role he was chosen in.
    """
    from whul.sources import mlb

    log_loader = log_loader or mlb.load_game_log
    wants_bat = subject.group == "Batter" or subject.two_way
    wants_arm = subject.group != "Batter" or subject.two_way

    batting: dict = {}
    if wants_bat:
        log = log_loader(subject.player_id, subject.season, "hitting")
        for row in mlb.game_points(log, "hitting").itertuples():
            batting[row.game_pk] = (row.date, 100 * row.points / scale.divisor["MLB_Batter"])

    pitching: dict = {}
    if wants_arm:
        log = log_loader(subject.player_id, subject.season, "pitching")
        started = dict(zip(log["game_pk"], _num(log, "gamesStarted"))) if len(log) else {}
        for row in mlb.game_points(log, "pitching").itertuples():
            role = "start" if started.get(row.game_pk, 0) >= 1 else "relief"
            pitching[row.game_pk] = (row.date, role,
                                     100 * row.points / scale.divisor["MLB_Pitcher"])

    rows = []
    for pk in dict.fromkeys([*batting, *pitching]):
        bat = batting.get(pk)
        arm = pitching.get(pk)
        rows.append({
            "game_pk": pk,
            "date": (arm or bat)[0],
            "role": arm[1] if arm else "bat",
            "score": rules.two_way_game(
                batting=bat[1] if bat else None,
                pitching=arm[2] if arm else None),
        })
    return pd.DataFrame(rows, columns=["game_pk", "date", "role", "score"])


@dataclass
class Report:
    seasons: tuple[int, ...]
    nfl: pd.DataFrame
    mlb: pd.DataFrame
    swing: pd.DataFrame
    failures: list[str] = field(default_factory=list)
    two_way: list[str] = field(default_factory=list)


def calibrate(store, seasons=SEASONS, season_label: str | None = None,
              verbose: bool = True, nfl_loader=None, line_loader=None,
              log_loader=None) -> Report:
    from whul.config.league import SEASON

    scale = frozen_scale(store, season_label or SEASON.label)
    if verbose:
        print(f"  NFL anchor, {seasons[0]}-{seasons[-1]} ...", flush=True)
    nfl = nfl_rows(scale, seasons, loader=nfl_loader)

    rows, swing, failures, two_way = [], [], [], []
    for season in seasons:
        subjects = mlb_population(season, scale, loader=line_loader)
        if verbose:
            counts = pd.Series([s.group for s in subjects]).value_counts().to_dict()
            print(f"  MLB {season}: {counts}", flush=True)
        for subject in subjects:
            try:
                games = mlb_games(subject, scale, log_loader=log_loader)
            except Exception as exc:  # noqa: BLE001 -- one player, not the run
                failures.append(f"{subject.name} {season} ({subject.group}): "
                                f"{type(exc).__name__}: {exc}")
                continue
            if subject.two_way:
                two_way.append(f"{subject.name} {season}, calibrated as a "
                               f"{subject.group.lower()}")
            if subject.group == "Batter":
                mine = games["score"]
            else:
                wanted = "start" if subject.group == "Starter" else "relief"
                mine = games.loc[games["role"] == wanted, "score"]
                starts = games.loc[games["role"] == "start", "score"].tolist()
                reliefs = games.loc[games["role"] == "relief", "score"].tolist()
                if starts and reliefs:
                    swing.append({"season": season, "name": subject.name,
                                  "group": subject.group, "starts": starts,
                                  "reliefs": reliefs})
            rows.append({"season": season, "group": subject.group,
                         "name": subject.name, "games": len(mine),
                         "two_way": subject.two_way,
                         **{f"best{k}": v for k, v in prefix(mine).items()}})
    return Report(tuple(seasons), nfl, pd.DataFrame(rows), pd.DataFrame(swing),
                  failures, two_way)


# --- the report ---------------------------------------------------------------

def _means(frame: pd.DataFrame) -> dict[int, float]:
    return {k: float(frame[f"best{k}"].mean()) for k in KS}


def _k_cell(value: float) -> str:
    return f"beyond k={KS[-1]}" if math.isnan(value) else f"{value:.1f}"


def render(report: Report) -> str:
    out = []
    say = out.append
    first, last = report.seasons[0], report.seasons[-1]
    span = str(first) if first == last else f"{first}-{last}"
    say(f"\nBest-game calibration, {span}, on the frozen scale "
        f"(a 99th-percentile season = 100)\n")

    nfl = _means(report.nfl)
    anchors = {"best week": nfl[1], "best three weeks": nfl[3]}
    say(f"  NFL anchor ({len(report.nfl):,} player-seasons): best week "
        f"{nfl[1]:.1f}, best three weeks {nfl[3]:.1f}\n")

    shown = [1, 2, 3, 5, 8, 12, 15, 20, 25, 30, 40]
    say("  Mean best-k:")
    say("  " + f"{'':10s}{'n':>6s}" + "".join(f"{'k=' + str(k):>7s}" for k in shown))
    means = {}
    for group in MLB_GROUPS:
        block = report.mlb[report.mlb["group"] == group] if len(report.mlb) else report.mlb
        if block.empty:
            say(f"  {group:10s}{0:>6d}   (none)")
            continue
        means[group] = _means(block)
        say("  " + f"{group:10s}{len(block):>6d}"
            + "".join(f"{means[group][k]:>7.1f}" for k in shown))

    say("\n  k at which each matches the NFL:")
    say("  " + f"{'':10s}" + "".join(f"{name:>18s}" for name in anchors))
    ks = {}
    for group, m in means.items():
        ks[group] = {name: equalising_k(m, target) for name, target in anchors.items()}
        say(f"  {group:10s}" + "".join(f"{_k_cell(v):>18s}" for v in ks[group].values()))

    swing = report.swing
    say(f"\n  Swingmen -- started and relieved in the same season: {len(swing)}")
    if len(swing) and {"Starter", "Reliever"} <= set(ks):
        for name in anchors:
            n = ks["Starter"][name]
            m = ks["Reliever"][name]
            if math.isnan(n) or math.isnan(m):
                continue
            n, m = max(1, round(n)), max(1, round(m))
            seg = [rules.pitcher_best(r.starts, r.reliefs, n, m) for r in swing.itertuples()]
            mix = [rules.pitcher_best(r.starts, r.reliefs, n, m, mix=True)
                   for r in swing.itertuples()]
            gains = [b - a for a, b in zip(seg, mix)]
            helped = sum(g > 1e-9 for g in gains)
            say(f"    at the {name} anchor (n={n} starts, m={m} appearances): "
                f"agreed rule {sum(seg) / len(seg):.1f} on average, mixed "
                f"{sum(mix) / len(mix):.1f}; {helped} of {len(swing)} gain, "
                f"by up to {max(gains):.1f}")

    say(f"\n  Two-way seasons, scored game by game: "
        f"{', '.join(report.two_way) if report.two_way else 'none'}")
    if report.failures:
        say(f"\n  {len(report.failures)} player-season(s) could not be read:")
        for line in report.failures[:20]:
            say(f"    {line}")
    say("")
    return "\n".join(out)
