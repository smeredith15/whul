"""Pulling a live league and turning it into today's standings.

The benchmark work answers "what does 100 mean". This answers "what has
everyone done so far", which is the other half and the one with a deadline: a
feed that only serves a rolling window forgets the start of the season if
nobody writes it down.

So this always records the raw figures, whether or not a benchmark exists to
scale them by. Raw stats can be scored later; a fortnight of tennis that
nobody captured cannot be recovered at all.

The steps, in order, are the same for every league:

    pull -> score -> normalize -> match to a roster slot -> record

Only the last two are new here. ``whul.resolve`` does the matching, and it is
the step that fails quietly if it is allowed to: an unmatched asset scores
nothing and the standings say nothing about it, so every run reports what it
could not match, by name.
"""

from __future__ import annotations

import json

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from whul import fixtures
from whul import resolve as resolver
from whul.config.league import covered_by
from whul.normalize import apply_benchmarks
from whul.pipeline import write_daily_scores
from whul.store import benchmarks as store_benchmarks
from whul.store.db import Store, _now


@dataclass
class IngestReport:
    """What one league's run did, in a shape a person can check."""

    league: str
    asset_type: str = "Player"
    pulled: int = 0
    matched: int = 0
    scored: int = 0
    recorded: int = 0
    fixtures: int = 0
    version: str = ""
    resolution: resolver.Resolution | None = None
    problems: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"{self.league} {self.asset_type.lower()}s: {self.pulled:,} feed rows, "
            f"{self.matched} rostered matched, {self.scored} scored"
        ]
        if self.fixtures:
            lines.append(f"  {self.fixtures} upcoming fixture row(s) recorded")
        if self.resolution is not None:
            lines += [
                line for line in str(self.resolution).splitlines()[1:]
            ]
        lines += [f"  ! {p}" for p in self.problems]
        return "\n".join(lines)


def ingest(
    store: Store,
    source,
    season: str,
    as_of: date,
    verbose: bool = True,
) -> IngestReport:
    """Pull one league up to ``as_of`` and record it against the roster."""
    report = IngestReport(league=source.league, asset_type=source.asset_type)

    assets = resolver.rostered_assets(store, season, source.asset_type)
    wanted = _leagues_of(source)
    assets = assets[assets["league"].isin(wanted)]
    if assets.empty:
        report.problems.append("nothing rostered in this league; skipped")
        return report

    notes: list[str] = []
    upcoming: list[pd.DataFrame] = []
    try:
        scored = _pull(
            source, as_of, verbose, names=list(assets["display_name"]),
            notes=notes, upcoming=upcoming, store=store,
        )
    except Exception as exc:  # noqa: BLE001 -- one league must not stop the rest
        report.problems.append(f"could not pull: {type(exc).__name__}: {exc}")
        _record_nothing(store, source, as_of, report)
        return report

    # Before the early return below. A league that has not kicked off scores
    # nothing and is precisely when its next fixture is the only thing the
    # page can say about it.
    report.fixtures = _record_fixtures(store, source, season, as_of, upcoming)

    if scored is None or scored.empty:
        report.problems.append(
            notes[0] if notes else "the source has no results yet for this season"
        )
        _record_nothing(store, source, as_of, report)
        return report
    report.pulled = len(scored)
    # Before the roster narrows it. Every club in the league is in this frame
    # and only some of them were drafted, and a player's club need not be one
    # of the drafted ones.
    _record_club_games(store, scored, source, season, as_of)
    # Anything the pull wanted seen. The notes above this line all belong to a
    # pull that returned nothing and has already gone home; these are the ones
    # raised by a pull that worked, and without this they went nowhere.
    report.problems.extend(notes)

    mine, resolution = resolver.resolve(
        scored, assets, source.asset_type,
        aliases=resolver.load_aliases(store, source.key),
        league=source.league,
        many_per_asset=getattr(source, "post_normalize", None) is not None,
    )
    report.resolution = resolution
    report.matched = len(resolution.matched)
    if mine.empty:
        report.problems.append("no rostered asset matched a feed row")
        # A feed that answered and named nobody we hold is a failure, and
        # without this the status row keeps yesterday's `ok`, which is the
        # same trap `_record_nothing` was written for one branch earlier.
        _record_nothing(store, source, as_of, report)
        return report
    resolver.save_aliases(store, source.key, resolution.matched)

    if getattr(source, "cumulative", False):
        mine = _against_the_league_year(store, mine, source, season, as_of, report)

    _check_against_the_club(store, mine, source, season, as_of, report)
    _settle_umbrella_league(store, mine, report)
    _report_shrinkage(store, mine, source, season, as_of, report)
    mine = _keep_competitions_the_pull_missed(
        store, mine, source, season, as_of, report)

    # Raw first, and unconditionally. A benchmark can be computed next week;
    # a rolling feed's earlier weeks cannot be fetched back.
    report.recorded = store.record_stats(
        mine.to_dict("records"), source=source.key, season=season,
        as_of=as_of, league=source.league,
    )
    _note_the_unmatched(store, source, as_of, report)

    version = store_benchmarks.active_version(store, season)
    if version is None:
        report.problems.append(
            "no frozen benchmark for this season, so the raw figures were "
            "recorded but not scaled"
        )
        return report
    report.version = version.version

    bench = store_benchmarks.load(store, version.version)
    placed = apply_benchmarks(mine, bench, source.asset_type, strict=False)
    unscaled = placed[placed["scaled_score"].isna()]
    if not unscaled.empty:
        groups = sorted(set(unscaled["norm_key"].astype(str)))
        report.problems.append(
            f"{len(unscaled)} asset(s) have no benchmark in {version.version} "
            f"(groups {', '.join(groups)}); they are recorded but unscored"
        )
    placed = placed[placed["scaled_score"].notna()]
    if placed.empty:
        return report

    # A scorer that emits several rows per asset folds them here, after each has
    # been scaled by its own benchmark. Recording them unfolded would give a
    # two-way player two rows in one slot and double-count him.
    fold = getattr(source, "post_normalize", None)
    if fold is not None:
        placed = fold(placed)

    report.scored = write_daily_scores(
        store, placed, season, as_of, version.version
    )
    return report


#: What a player's own game count is called, in order of preference.
PLAYER_GAMES_COLUMNS = ("regular_games", "games_played", "games", "matches")


def _check_against_the_club(store: Store, mine: pd.DataFrame, source,
                            season: str, as_of: date, report: IngestReport) -> None:
    """Nobody plays more games than his club did.

    The one arithmetic a reader can do in their head, and the figures failed
    it: twelve of twenty baseball players were recorded with more games than
    their club had played, by half again. Both numbers looked reasonable on
    their own -- twenty-eight games, eighteen games -- and the pair is what
    says one of them is wrong.

    Reported rather than refused, and deliberately not corrected: which figure
    is the wrong one is a question about the feeds, and clamping the player to
    his club would hide a club count that is short.
    """
    if getattr(source, "asset_type", "") != "Player" or mine is None or mine.empty:
        return
    if store is None or "asset_id" not in mine.columns:
        return
    column = next((c for c in PLAYER_GAMES_COLUMNS if c in mine.columns), None)
    if column is None:
        return
    club_games = store.read_club_games(season, as_of)
    if not club_games:
        return

    ids = [str(a) for a in mine["asset_id"] if str(a)]
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    clubs = dict(store.query(
        f"SELECT asset_id, affiliation FROM assets WHERE asset_id IN ({marks})",
        tuple(ids),
    ).itertuples(index=False, name=None))

    impossible = []
    played = pd.to_numeric(mine[column], errors="coerce")
    for asset_id, his in zip(mine["asset_id"], played):
        club = str(clubs.get(str(asset_id)) or "").strip()
        theirs = club_games.get(club)
        if theirs is None or pd.isna(his) or his <= theirs:
            continue
        impossible.append((club, float(his), float(theirs)))
    if not impossible:
        return

    worst = sorted(impossible, key=lambda row: row[2] - row[1])[:4]
    detail = "; ".join(f"{club} {his:,.0f} of {theirs:,.0f}"
                       for club, his, theirs in worst)
    report.problems.append(
        f"{len(impossible)} player(s) are recorded with more games than their "
        f"club has played, which cannot be: {detail}"
        + (f" (and {len(impossible) - len(worst)} more)"
           if len(impossible) > len(worst) else "")
        + ". One of the two feeds is wrong about its window and the pair is "
          "what says so; neither figure is altered here"
    )


#: How many games a club has played, under the names a scorer gives it. Every
#: competition, because the heading it feeds counts every competition on the
#: player's side too: a club figure that stopped where his total does, under a
#: panel that went on to show a Champions League section, was two numbers
#: disagreeing about the same season.
CLUB_GAMES_COLUMNS = ("matches_played", "games_played", "team_games")


def _record_club_games(store: Store, scored: pd.DataFrame, source,
                       season: str, as_of: date) -> int:
    """Write down every club's game count, drafted or not.

    A player's games-played figure says nothing on its own -- four matches is a
    season interrupted or the league in September -- and only his club's count
    tells them apart. Read off the club's own row it is there for the clubs
    somebody drafted and missing for the rest, which is why Eintracht
    Frankfurt's player had no second number at all and why twenty of the thirty
    MLB clubs could not have given one either.

    Taken from the team pull, which reads a whole league and is narrowed to the
    roster afterwards, so this is simply the figure before the narrowing.
    """
    if store is None or getattr(source, "asset_type", "") != "Team":
        return 0
    if scored is None or scored.empty or "team" not in scored.columns:
        return 0
    column = next((c for c in CLUB_GAMES_COLUMNS if c in scored.columns), None)
    if column is None:
        return 0
    played = pd.to_numeric(scored[column], errors="coerce")
    # A source that produces several leagues says which on the row; one that
    # produces one does not need to.
    leagues = (scored["league"].astype(str)
               if "league" in scored.columns
               else pd.Series(source.league, index=scored.index))
    written = 0
    for league, block in scored.assign(_g=played, _l=leagues).groupby("_l"):
        written += store.record_club_games(
            dict(zip(block["team"].astype(str), block["_g"])),
            season, as_of, str(league),
        )
    return written


#: A windowed total below the last one by more than this is reported. A small
#: dip is possible where a scorer's inputs are revised; a large one is a feed
#: that has stopped reaching as far back as it did.
SHRINKAGE_TOLERANCE = 0.005


#: How far back to look for a competition a pull missed. Long enough to ride
#: out a feed being unreachable for a couple of days, short enough that a
#: competition genuinely finished stops being carried within the week.
CARRY_FORWARD_DAYS = 7


def _keep_competitions_the_pull_missed(
    store: Store, mine: pd.DataFrame, source, season: str, as_of: date,
    report: IngestReport,
) -> pd.DataFrame:
    """Do not let a failed competition pull erase one that succeeded earlier.

    Every run re-reads the whole season and overwrites the day's row, which is
    what lets a figure published late be picked up without anything being
    re-run by hand. It also means a run that fetches *less* replaces one that
    fetched more.

    That happened. A nightly run stored 27 Champions League lines, 6 Europa and
    1 Conference; a manual publish eleven hours later could not reach any of the
    three, and wrote silence over all of them. Nothing was wrong with the data
    and nothing said it had gone.

    A competition missing from the pull entirely is "we did not ask, or were
    refused" -- not "nobody played in it". One that answers with zeroes has
    said something, and its zeroes stand. So only the absent ones are carried
    forward, and the totals they feed are recomputed from the merged breakdown.
    """
    from whul.scoring.postseason import (
        DETAIL_COLUMN, credited_bonus, pending_bonus,
    )

    if mine.empty or DETAIL_COLUMN not in mine.columns:
        return mine
    if "asset_id" not in mine.columns or "regular_points" not in mine.columns:
        return mine

    # The most recent day that *has* a breakdown, not simply the most recent
    # day. A failed pull that already overwrote today's row would otherwise be
    # its own precedent -- it would find its own silence, carry nothing
    # forward, and the loss would be permanent from the next run on.
    previous = store.query(
        "SELECT asset_id, as_of, stats FROM raw_stats WHERE league = ? "
        "AND source = ? AND season = ? AND as_of >= ? AND as_of <= ? "
        "ORDER BY as_of DESC",
        (source.league, source.key, season,
         (as_of - timedelta(days=CARRY_FORWARD_DAYS)).isoformat(),
         as_of.isoformat()),
    )
    if previous.empty:
        return mine

    before: dict[str, list] = {}
    for row in previous.itertuples():
        asset_id = str(row.asset_id)
        if asset_id in before:
            continue  # a newer day already answered for this asset
        try:
            figures = json.loads(row.stats)
        except (TypeError, ValueError):
            continue
        detail = figures.get(DETAIL_COLUMN)
        if isinstance(detail, list) and detail:
            before[asset_id] = detail

    if not before:
        return mine

    out = mine.copy()
    details, kept = [], {}
    for row in out.itertuples():
        now = getattr(row, DETAIL_COLUMN, None)
        now = list(now) if isinstance(now, list) else []
        was = before.get(str(getattr(row, "asset_id", "")), [])
        have = {entry.get("competition") for entry in now}
        missing = [entry for entry in was
                   if entry.get("competition") not in have]
        for entry in missing:
            kept[str(entry.get("competition"))] = kept.get(
                str(entry.get("competition")), 0) + 1
        details.append(now + missing)

    if not kept:
        return mine

    out[DETAIL_COLUMN] = details
    out["postseason_bonus"] = [credited_bonus(d) for d in details]
    out["postseason_pending"] = [pending_bonus(d) for d in details]
    out["total_points"] = out["regular_points"] + out["postseason_bonus"]
    named = ", ".join(f"{name} ({count})" for name, count in sorted(kept.items()))
    report.problems.append(
        f"this pull returned nothing for {named}, which an earlier run did "
        f"reach. Those figures are carried forward rather than overwritten "
        f"with silence; a later run that reaches the feed will replace them"
    )
    return out


def _report_shrinkage(
    store: Store, mine: pd.DataFrame, source, season: str, as_of: date,
    report: IngestReport,
) -> None:
    """Say when an asset's season-to-date total came back smaller than before.

    These totals accumulate over a league year, so within one they can only
    grow. A drop is not a bad week -- it is the feed no longer reaching as far
    back as it did, and the score simply gets smaller with nothing raised.

    Tennis is the standing example. It is assembled from three vintages, and
    the middle one closes the gap between an archive that ends in February and
    a feed that serves seven days. Lose it and the totals quietly become the
    last week of the season: a player who won a Masters a fortnight ago is
    suddenly on nothing, and the standings look like a slump.

    Reported rather than refused. The smaller figure may be the correct one
    after a correction upstream, and a pipeline that will not record today
    because yesterday was bigger would be worse than one that says so.
    """
    if mine.empty or "total_points" not in mine.columns or "asset_id" not in mine.columns:
        return

    previous = store.query(
        "SELECT asset_id, stats FROM raw_stats WHERE league = ? AND source = ? "
        "AND season = ? AND as_of = (SELECT MAX(as_of) FROM raw_stats "
        "  WHERE league = ? AND source = ? AND season = ? AND as_of < ?)",
        (source.league, source.key, season, source.league, source.key, season,
         as_of.isoformat()),
    )
    if previous.empty:
        return

    was = {}
    for row in previous.itertuples():
        try:
            figures = json.loads(row.stats)
        except (TypeError, ValueError):
            continue
        value = pd.to_numeric(pd.Series([figures.get("total_points")]),
                              errors="coerce").iloc[0]
        if pd.notna(value):
            was[str(row.asset_id)] = float(value)

    shrunk = []
    for row in mine.itertuples():
        asset_id = str(getattr(row, "asset_id", ""))
        before = was.get(asset_id)
        now = pd.to_numeric(pd.Series([getattr(row, "total_points", None)]),
                            errors="coerce").iloc[0]
        if before is None or pd.isna(now):
            continue
        if now < before - abs(before) * SHRINKAGE_TOLERANCE:
            shrunk.append((asset_id, before, float(now)))

    if not shrunk:
        return
    names = _names_for(store, [a for a, _, _ in shrunk])
    worst = sorted(shrunk, key=lambda s: s[2] - s[1])[:5]
    detail = "; ".join(
        f"{names.get(a, a)} {b:,.1f} -> {n:,.1f}" for a, b, n in worst
    )
    report.problems.append(
        f"{len(shrunk)} asset(s) came back with a smaller season-to-date total "
        f"than the last pull, which within a league year should only grow. This "
        f"is what a feed losing its earlier weeks looks like: {detail}"
        + (f" (and {len(shrunk) - len(worst)} more)" if len(shrunk) > len(worst) else "")
    )


def _names_for(store: Store, asset_ids: list[str]) -> dict[str, str]:
    if not asset_ids:
        return {}
    marks = ",".join("?" for _ in asset_ids)
    rows = store.query(
        f"SELECT asset_id, display_name FROM assets WHERE asset_id IN ({marks})",
        tuple(asset_ids),
    )
    return {str(r.asset_id): str(r.display_name) for r in rows.itertuples()}


#: How late a baseline may be taken and still be treated as the league year's
#: opening state. A night's lag is a cron that ran after midnight; three weeks
#: is a feature added mid-season, and subtracting that would credit a manager
#: with none of what their player did in the meantime.
BASELINE_GRACE_DAYS = 2


def _against_the_league_year(
    store: Store, mine: pd.DataFrame, source, season: str, as_of: date,
    report: IngestReport,
) -> pd.DataFrame:
    """Turn season-to-date figures into what was earned inside the league year.

    Recorded once per asset per feed season, then subtracted from every pull
    after -- which is exact, and needs nothing from a feed that will not serve
    a date range. The calendar seasons a league year spans are then summed, so
    a player held across the turn of the year is scored once rather than twice
    against a benchmark drawn from whole years.

    A baseline taken late is kept but not used. It is still the honest record
    of when the differencing became possible, and using it would quietly credit
    a manager with nothing their player did before it was taken.
    """
    from whul.config.league import season_start
    from whul.store import baselines as baseline_store

    if mine.empty or "season" not in mine.columns:
        return mine

    opens = season_start(source.league)
    late = 0
    for feed_season, block in mine.groupby("season"):
        for row in block.to_dict("records"):
            asset_id = str(row.get("asset_id", ""))
            if not asset_id:
                continue
            figures = {k: v for k, v in row.items() if k != "asset_id"}
            baseline_store.record(
                store, asset_id, season, source.key, int(feed_season),
                figures, opens.isoformat(),
            )

        held = baseline_store.usable(
            store, season, source.key, int(feed_season), opens,
            grace_days=BASELINE_GRACE_DAYS,
        )
        if held:
            mine = _replace_block(
                mine, feed_season, baseline_store.subtract(block, held)
            )
        elif baseline_store.load(store, season, source.key, int(feed_season)):
            late += 1

    if late:
        report.problems.append(
            f"the {source.league} baseline was taken after this league year "
            f"opened on {opens}, so it is recorded but not subtracted; the "
            f"figures are still season-to-date for that stretch"
        )

    return baseline_store.combine_seasons(
        _kept_season_by_season(mine), ["asset_id", "role"])


#: Columns that name the row rather than measure it, so a per-season line does
#: not repeat the player's name back at itself four times.
SEASON_LINE_SKIP = frozenset({
    "asset_id", "league", "as_of", "source", "phase", "fetched_at",
    "player", "player_id", "team", "display_name", "role", "norm_key",
    "asset_type", "season_lines",
})


def _kept_season_by_season(mine: pd.DataFrame) -> pd.DataFrame:
    """Each feed season's own figures, kept before the seasons are added up.

    A league year opening in August spans two calendar seasons, and they are
    summed so a player held across the turn of the year is scored once. The sum
    is the right number to score and the wrong one to read: from April a
    profile would show one line that is neither season, and no way to ask which
    half of it was last year.

    Attached after the differencing, so each line is what was earned inside the
    league year rather than the feed's season to date. A list, because
    ``combine_seasons`` joins those and sums everything else -- the same shape
    every other nested figure here already uses, and for the same reason.
    """
    if mine.empty or "season" not in mine.columns:
        return mine
    wanted = [
        column for column in mine.columns
        if column not in SEASON_LINE_SKIP
        and not _holds_a_list(mine[column])
    ]
    out = mine.copy()
    out["season_lines"] = [
        [{
            column: (None if value != value else
                     value.item() if hasattr(value, "item") else value)
            for column, value in row.items() if column in wanted
        }]
        for row in out[wanted].to_dict("records")
    ]
    return out


def _holds_a_list(values) -> bool:
    return any(isinstance(value, list) for value in values)


def _replace_block(frame: pd.DataFrame, feed_season, replacement: pd.DataFrame):
    """Swap one feed season's rows for their differenced selves."""
    rest = frame[frame["season"] != feed_season]
    return pd.concat([rest, replacement], ignore_index=True)


#: Column pairs that say a row has a result, for the feeds carrying no
#: ``completed`` flag.
#:
#: The flag is what the ESPN adapters set and is the easy case. nflverse has
#: none: ``games.csv`` holds one row a fixture from the day the schedule is
#: published, and the scores appear in it as the season is played. Reading only
#: the flag counted all 272 fixtures of an unplayed NFL season as completed
#: games and reported the week before kickoff as a scoring bug -- pointing at
#: the scorer, which is the one place there was nothing to find.
RESULT_COLUMNS = (
    ("home_score", "away_score"),
    ("points_for", "points_against"),
)


def _played_rows(frame: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """The rows carrying a result, and whether that could be told at all.

    The second half matters as much as the first. A feed that says neither
    whether a game is finished nor what it finished is one this cannot read,
    and guessing there is what produced a confident wrong answer.
    """
    if "completed" in frame.columns:
        return frame[frame["completed"].fillna(False).astype(bool)], True
    for left, right in RESULT_COLUMNS:
        if left in frame.columns and right in frame.columns:
            return frame[frame[left].notna() & frame[right].notna()], True
    return frame, False


def _eaten_by_the_start_date(raw: pd.DataFrame, kept: pd.DataFrame,
                             league: str) -> str:
    """Whether the league's start date is what emptied the frame.

    The one case the report below cannot see, because it describes what
    survived the cut rather than what the cut removed. The 2026 NFL season
    opened on a Wednesday; the start date said Thursday; the opener was dropped
    and the run said "271 fixture(s) scheduled, none played yet" about a game
    both of whose teams somebody had drafted.

    An off-by-one in a boundary date does not look like an error anywhere --
    which is why it gets its own sentence, naming the boundary and the date it
    refused, rather than being left to be inferred from a count.
    """
    from whul.config.league import season_start

    if raw is None or raw.empty or len(kept) == len(raw) or not league:
        return ""
    played, knowable = _played_rows(raw)
    if not knowable or played.empty:
        return ""
    column = next((c for c in DATE_COLUMNS if c in played.columns), None)
    if column is None:
        return ""
    days = pd.to_datetime(played[column], errors="coerce").dt.date
    start = season_start(league)
    before = sorted(d for d in days.dropna() if d < start)
    if not before:
        return ""
    return (
        f"{len(before):,} played row(s) were dropped for falling before "
        f"{start}, the day {league} results start counting -- the earliest is "
        f"{before[0]}. A game that has been played is not a season that has "
        f"not started: check the start date in `whul.config.league` before "
        f"looking anywhere else."
    )


def _why_nothing_scored(raw: pd.DataFrame, kept: pd.DataFrame,
                        league: str = "") -> str:
    """What arrived, when a full fetch scored nothing.

    Three things look identical from the outside -- a feed with nothing in it,
    a feed whose rows all fall before the league year opened, and a schedule of
    fixtures nobody has played. Only the last is normal, and it is the one that
    reads most like a broken adapter.

    A fourth is not knowing which of them it is, and that gets said rather than
    resolved by assumption. Blaming the scorer for a season that has not started
    sends someone to read arithmetic that is working.
    """
    eaten = _eaten_by_the_start_date(raw, kept, league)
    if eaten:
        return eaten
    if kept.empty:
        return (
            f"the feed returned {len(raw):,} row(s), but all of them fall "
            f"before this league's results start counting"
        )

    # A source that carries more than it was asked for marks the rows it was.
    # International soccer returns its whole history so each tournament's shape
    # can be read off an edition that was played, and diagnosing on all of it
    # said "25,929 completed rows arrived but none of them scored, which is the
    # scorer's to explain" -- an accusation, about a season nobody has played.
    if "wanted" in kept.columns:
        asked = kept[kept["wanted"].astype(bool)]
        if asked.empty:
            return (
                f"nothing that counts has been played in this league year yet. "
                f"The feed holds {len(kept):,} row(s) of earlier seasons, which "
                f"this source carries on purpose"
            )
        kept = asked

    played, knowable = _played_rows(kept)
    if played.empty:
        upcoming = ""
        column = next((c for c in DATE_COLUMNS if c in kept.columns), None)
        if column is not None:
            days = pd.to_datetime(kept[column], errors="coerce")
            if days.notna().any():
                upcoming = f"; the first is {days.min().date()}"
        return (
            f"{len(kept):,} fixture(s) scheduled, none played yet{upcoming}. "
            f"This is a season that has not started, not a feed that is broken."
        )
    if not knowable:
        return (
            f"{len(kept):,} row(s) arrived and none scored, and this feed says "
            f"neither whether a game is finished nor what it finished -- so a "
            f"season that has not started cannot be told apart from a scorer "
            f"that is not working"
        )
    return (
        f"{len(played):,} completed row(s) arrived but none of them scored, "
        f"which is the scorer's to explain rather than the feed's"
    )


def _leagues_of(source) -> set[str]:
    """Roster league labels a source can score.

    A roster records the category a pick was drafted into, which is sometimes
    broader than the competition -- "Tennis" for an ATP player. Both spellings
    have to reach the source that scores them.
    """
    from whul.config.league import CATEGORY_COMPETITIONS

    produced = set(source.produces or (source.league,))
    categories = {
        category for category, members in CATEGORY_COMPETITIONS.items()
        if produced & set(members)
    }
    return produced | categories | {source.league}


def uncovered(store: Store, season: str, sources) -> pd.DataFrame:
    """Rostered assets no source in this run could have scored.

    ``cmd_ingest`` already names the assets that were asked for and did not
    match. This is the failure one level up: a league nobody asked for. Every
    source it was asked to run can succeed, every asset those sources cover
    can match, and a whole league still scores nothing -- because it was never
    in the list. That is how thirty-two club soccer players sat at zero
    through a run that reported no problems at all.

    Groups rather than lists, because the list is the roster and the point is
    the league. Each group carries the source that would have covered it, so a
    league left out of tonight's list reads differently from a league nothing
    can score yet -- the first is a one-word fix, the second is a known gap.
    """
    from whul.benchmark_sources import resolve

    rostered = resolver.rostered_assets(store, season)
    if rostered.empty:
        return rostered

    covered = {
        (source.asset_type, league)
        for source in sources
        for league in _leagues_of(source)
    }
    exists = {
        (source.asset_type, league): source.key
        for source in resolve(None)
        for league in _leagues_of(source)
    }
    missed = rostered[[
        (row.asset_type, row.league) not in covered
        for row in rostered.itertuples()
    ]]
    if missed.empty:
        return missed
    grouped = (
        missed.groupby(["league", "asset_type"], as_index=False)
        .agg(assets=("display_name", "size"),
             names=("display_name", lambda names: ", ".join(sorted(names)[:3])))
        .sort_values(["league", "asset_type"])
    )
    grouped["source"] = [
        exists.get((row.asset_type, row.league), "")
        for row in grouped.itertuples()
    ]
    return grouped


#: Columns a raw feed puts an event's date in, in the order worth trying.
#: ``gameday`` is nflverse's spelling, and without it the NFL's whole schedule
#: went unfiltered by the league start date and its "not played yet" message
#: could not say when the first game is.
DATE_COLUMNS = ("date", "game_date", "event_date", "match_date", "start_date",
                "gameday")


def _from_season_start(raw: pd.DataFrame, league: str) -> pd.DataFrame:
    """Drop rows from before the league's results start counting.

    A league's own season rarely opens on the day the fantasy year does. Without
    this, the Premier League's first two matchweeks -- played the week before --
    would be scored as part of this league year, and tennis would count the
    Cincinnati final twice.
    """
    from whul.config.league import season_start

    column = next((c for c in DATE_COLUMNS if c in raw.columns), None)
    if column is None:
        return raw
    days = pd.to_datetime(raw[column], errors="coerce", utc=True).dt.tz_localize(None)
    # A row whose date will not parse is kept: dropping it would lose a result
    # silently, and the scorer is the better place to notice a broken row.
    return raw[days.isna() | (days.dt.date >= season_start(league))]


def _accumulating(fetch, source, store: Store, verbose: bool = True,
                  notes: list[str] | None = None):
    """``fetch``, but keeping what the feed forgets.

    Flashscore's tennis feed serves seven days either side of today and has no
    more to give, so a season total recomputed from it each night is a rolling
    one-week figure wearing a season's name. Taylor Fritz held 150 points for
    six days and then zero, having done nothing, because the tournament he won
    aged out of the window -- and the standings ledger, which differences
    consecutive days, read that as a loss.

    A wider request cannot fix it. Only a record of what the feed said while it
    was still saying it, which is what this keeps.

    A feed asked for the whole season forgets too, and more quietly. The club
    soccer walk reads a competition one date at a time, and a date that will
    not read is a day of matches missing from the pull rather than a day
    nobody played -- so a club that was on six matches is on five, and its
    score falls by a win it did play. The same ledger answers it, and for a
    season-wide feed anything held that tonight's pull did not show is named
    rather than counted, because for that kind of feed it is a fault.
    """
    from whul.store import feed_ledger

    def fetch_and_keep(years):
        # The committed history first, every time. It lives in the repository
        # and the ledger lives in a database that three workflows rebuild and
        # force-push, so a history that had to be restored by remembering to
        # restore it is one that eventually is not.
        seeded = feed_ledger.apply_seed(store, source.key, source.accumulates)
        window = fetch(years)
        try:
            held = feed_ledger.merge(store, source.key, window, source.accumulates)
        except KeyError as exc:
            # A key naming a column the source does not produce. Loud, and not
            # fatal: this is a guard against a feed forgetting, and losing the
            # guard is worth one league's pull, which is what happened -- every
            # club-soccer source failed outright for two days over a column
            # name.
            said = (f"{source.key} cannot be accumulated: {exc}. Tonight's "
                    f"pull is scored as it came, so a match the feed has "
                    f"stopped returning is lost until this is fixed")
            if notes is not None:
                notes.append(said)
            print(f"  {said}", flush=True)
            return window if window is not None else pd.DataFrame()
        if verbose:
            fresh = 0 if window is None or window.empty else len(window)
            print(f"  {source.key}: {fresh} row(s) in the feed's window, "
                  f"{seeded} from the committed history, "
                  f"{len(held)} kept in all", flush=True)
        _report_forgotten(source, window, held, verbose, notes)
        return held

    return fetch_and_keep


#: How many forgotten rows to name before the count stands in for the rest.
FORGOTTEN_SHOWN = 6


def _report_forgotten(source, window, held, verbose: bool, notes) -> None:
    """Rows the feed has shown before and did not show tonight.

    Expected of a windowed feed: a seven-day window forgetting last month is
    what a seven-day window is. It is a fault in a feed asked for the whole
    season, and the kind that costs points with nothing raised -- so it is
    named here, restored by the ledger either way.

    Only rows inside this league year count. The ledger keeps every season it
    has ever seen, and last August's matches are not missing from tonight's
    pull, they are simply not what was asked for.
    """
    if getattr(source, "windowed", False) or not getattr(source, "accumulates", ()):
        return
    if window is None or window.empty or held is None or held.empty:
        return

    from whul.store.feed_ledger import row_key

    keys = source.accumulates
    tonight = {row_key(row, keys) for row in window.to_dict("records")}
    inside = _from_season_start(held, source.league)
    lost = [row for row in inside.to_dict("records")
            if row_key(row, keys) not in tonight]
    if not lost:
        return

    shown = "; ".join(
        " ".join(str(row.get(part) or "") for part in
                 ("date", "competition_key", "team", "opponent")).strip()
        or row_key(row, keys)
        for row in lost[:FORGOTTEN_SHOWN]
    )
    said = (
        f"the {source.league} feed is asked for the whole season and did not "
        f"return {len(lost)} row(s) it has returned before; they are scored "
        f"from the ledger rather than lost, and a feed dropping what it "
        f"already showed is worth looking at: {shown}"
        + (f" (and {len(lost) - FORGOTTEN_SHOWN} more)"
           if len(lost) > FORGOTTEN_SHOWN else "")
    )
    if notes is not None:
        notes.append(said)
    if verbose:
        print(f"  {source.key}: {said}", flush=True)


def _drain_findings(notes) -> None:
    """Carry a loader's findings into the run's report.

    A loader is handed a list of seasons and returns a frame, so anything it
    notices about the *shape* of what came back could only ever be printed.
    That is where the domestic cups went: every night's log said the League Cup
    had returned squads with no appearances in them, ten thousand lines in, and
    a log is not read on the nights it says nothing is wrong.
    """
    from whul import benchmark_sources
    from whul.scoring import tennis

    said = benchmark_sources.take_findings() + tennis.take_collisions()
    if notes is not None:
        notes.extend(said)


def _scored_on(score, kept, as_of: date):
    """Score a day, telling the scorer which day it is.

    Whether a competition has finished is a question about a date, and the
    scorers that ask it -- the European bonus, which is held until its
    competition ends, and the domestic league title, which nobody holds in
    September -- were never told which one. They defaulted to today, so a day
    was always scored against the calendar of the run rather than of the day.
    For the nightly job those are the same date. For a backfill they are not: a
    day in September, rescored in June, would have every held bonus released
    into it and a title awarded on it.

    Passed only to scorers that take it, because most do not need it and a
    scorer's signature is the honest declaration of whether a date changes its
    answer.
    """
    import inspect

    try:
        takes = "as_of" in inspect.signature(score).parameters
    except (TypeError, ValueError):  # a builtin or a C callable
        takes = False
    return score(kept, as_of=as_of) if takes else score(kept)


def _pull(
    source, as_of: date, verbose: bool, names: list[str] | None = None,
    notes: list[str] | None = None, upcoming: list | None = None,
    store: Store | None = None,
) -> pd.DataFrame:
    """Season-to-date totals for one league, however that league counts them.

    ``notes`` collects anything the caller should say about an empty result. A
    feed that returned a full fixture list none of which has been played is not
    the same as a feed that returned nothing, and reporting both as "no results
    yet" has twice sent someone looking for a bug in a league that simply has
    not kicked off.
    """
    from whul.config.league import season_start
    from whul.scoring import window

    live = source.live is not None
    load, score = (source.live or source.build)()
    seasons = source.seasons_for(as_of) if source.seasons_for else [as_of.year]
    if source.seasons_for and not seasons:
        # No season of this league falls inside the league year so far. Asking
        # the feed anyway would get an empty answer that reads exactly like a
        # broken adapter, so say the true thing instead of fetching nothing.
        if notes is not None:
            notes.append(
                f"no {source.league} season has been played inside this "
                f"league year yet (it opened {season_start(source.league)}); "
                f"nothing to pull"
            )
        # Nothing to score, but this is exactly when the next fixture is the
        # only thing the page can say about the league -- and the schedule is
        # published weeks before the first game. The NFL's opens on a Thursday
        # and this ran on the Tuesday: 272 fixtures sat in a file nobody asked
        # for, and every NFL row on every roster was blank.
        _harvest_ahead(source, as_of, verbose, names, upcoming)
        return pd.DataFrame()
    # A roster-scoped loader is asked only for what the roster holds, which for
    # a team league is eight requests rather than a season of dates.
    fetch = (
        (lambda years: load(years, names or []))
        if live and source.roster_scoped
        else load
    )
    # A feed that only serves a window is asked for the window and answered for
    # the season: what it shows is written down on the way past, and what comes
    # back is everything it has ever shown. Wrapped around `fetch` rather than
    # placed at either use of it, because the windowed and whole-season paths
    # below both need it and only one of them is tennis today.
    # getattr, as `cumulative` below: a Source is duck-typed at this boundary
    # and a test double need not carry every field to be pulled from.
    if getattr(source, "accumulates", ()) and store is not None:
        fetch = _accumulating(fetch, source, store, verbose, notes)
    if not source.windowed:
        raw = fetch(seasons)
        _drain_findings(notes)
        if raw is None or raw.empty:
            # A feed that returns nothing said nothing about why, and an empty
            # frame reaching the report as a bare zero is the shape of fault
            # this whole module exists to prevent. International soccer found
            # it: its first pull of a league year returns nothing, correctly --
            # the next international window is weeks away -- and the run said
            # so in no way at all.
            if notes is not None:
                notes.append(
                    f"the {source.league} feed returned nothing for season(s) "
                    f"{', '.join(str(s) for s in seasons)}. Nothing that counts "
                    f"has been played, or the feed is empty; the adapter's own "
                    f"output says which"
                )
            return pd.DataFrame()
        # A source that has already decided which league year each row belongs
        # to is not filtered again. International soccer assigns a whole
        # tournament to the year it began in and returns the history its
        # tournament shapes are read from; a date cutoff strips that history
        # and would cut a World Cup off at the year's end.
        _harvest(source, raw, as_of, seasons, upcoming)
        kept = raw if source.dated_by_source else _from_season_start(raw, source.league)
        scored = _scored_on(score, kept, as_of)
        if (scored is None or scored.empty) and notes is not None:
            notes.append(_why_nothing_scored(raw, kept, source.league))
        if not getattr(source, "cumulative", False):
            scored = _across_feed_seasons(scored)
        return _carry_identity(scored, kept, source.asset_type)

    # A continuously running sport accrues over the league year, not the
    # calendar one, so its live total is summed over the same window its
    # benchmark was drawn over -- and over each produced league's own window,
    # since two series sharing a pull need not start on the same day.
    years = sorted({season_start(source.league).year, as_of.year})
    fetched = fetch(years)
    _drain_findings(notes)
    events = _carry_identity(
        _scored_on(score, fetched, as_of), fetched, source.asset_type)
    if events is None or events.empty:
        return pd.DataFrame()
    # Read off the scored events, because that frame is the only place that
    # says which series an athlete actually runs in. Before the window totals,
    # so a driver whose league year has produced nothing yet still gets one.
    _harvest_tour(source, events, as_of, years, names, upcoming, verbose)

    frames = []
    for name in source.produces or (source.league,):
        rows = events[events["league"].astype(str) == name] \
            if "league" in events.columns else events
        if rows.empty:
            continue
        current = window.season_windows(0, start=season_start(name))[-1]
        totals = _carry_identity(
            window.window_totals(rows, [current]), rows, source.asset_type
        )
        frames.append(_with_finishes(totals.assign(season=current.label), rows, current))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


#: What a player is, beyond a name and a total. Carried through from the feed
#: because a scorer has no use for it and drops it: every one of them builds a
#: fresh frame of exactly the columns its arithmetic needs, and identity is not
#: one of them.
#:
#: The site wants it. A roster of sixty names is unreadable without saying which
#: of them is a striker at Villa and which a shortstop for the Athletics -- and
#: two players sharing a surname are told apart by their club, not by their
#: goals. It is recorded rather than looked up because there is nowhere to look
#: it up from: `assets` holds the drafted name and nothing else, and a static
#: site cannot ask a feed at the moment someone clicks.
#: Named CARRIED_ rather than IDENTITY_: this module already has an
#: IDENTITY_COLUMNS, for the keys a multi-season sum groups by, and the two are
#: different lists for different jobs. Defined under one name they collided
#: silently -- the later definition won, this function read the group-by keys
#: instead, and a position went missing with nothing raised anywhere.
CARRIED_IDENTITY = ("team", "team_name", "position", "role", "car_number")


def _harvest_ahead(source, as_of: date, verbose: bool, names, upcoming) -> None:
    """Fetch a not-yet-started season's schedule, for its fixtures alone.

    Only for the leagues that carry a schedule and only for their fixtures:
    nothing here is scored, because nothing has been played. The season asked
    for is the one the *whole* league year touches rather than the part of it
    already gone, which is the difference between "no season yet" and "the
    season that starts on Thursday".

    Never fatal, and quiet about it. This is a pull nobody asked for, made on
    the chance that a schedule exists; a league whose feed has not published
    one yet is the ordinary case, not a fault.
    """
    if upcoming is None or source.asset_type != "Team":
        return
    from whul.config.league import SEASON

    try:
        ahead = source.seasons_for(SEASON.end) if source.seasons_for else []
        if not ahead:
            return
        load, _ = (source.live or source.build)()
        fetch = (
            (lambda years: load(years, names or []))
            if source.live is not None and source.roster_scoped
            else load
        )
        raw = fetch(ahead)
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  {source.league}: no schedule published yet for the coming "
                  f"season ({type(exc).__name__})", flush=True)
        return
    if raw is None or raw.empty:
        return
    _harvest(source, raw, as_of, ahead, upcoming)
    if verbose and upcoming:
        print(f"  {source.league}: season not started; kept "
              f"{len(upcoming[-1])} upcoming fixture row(s)", flush=True)


#: Which feed serves each series' calendar. F1 comes from the same place its
#: results do -- ESPN's racing scoreboard has never been exercised for that
#: series here, and a schedule is not the place to start.
def _tour_calendar(series: str, seasons: list[int], as_of: date) -> list[dict]:
    """The named series' events that have not finished, soonest first."""
    if series == "F1":
        from whul.sources import jolpica

        return jolpica.races_ahead(seasons, as_of)
    from whul.sources import espn_individual

    key = {"PGA": "pga", "NASCAR": "nascar"}.get(series)
    if not key:
        return []
    return espn_individual.events_ahead(key, seasons, as_of)


def _harvest_tour(source, events: pd.DataFrame, as_of: date, seasons,
                  names, upcoming, verbose: bool = True) -> None:
    """What the tour plays next, for the leagues that have no fixture at all.

    A golfer and a driver were the two categories this column could never
    speak for: their next start is an event with a field rather than a game
    against somebody, and `by_asset` correctly matched them to nothing. The
    answer it can give is the tour's -- the next tournament or race on the
    calendar -- which is the same for everyone in the series and is what a
    manager checking the page actually wants to know.

    Never fatal, and never at the cost of the pull: a series whose calendar
    cannot be read contributes no rows and says so.
    """
    if upcoming is None or source.league not in fixtures.TOUR:
        return
    if events is None or events.empty or "player" not in events.columns:
        return
    series_of = (
        events["league"].astype(str) if "league" in events.columns
        else pd.Series(source.league, index=events.index)
    )
    wanted = {_normalized(n): n for n in (names or [])}
    entrants: dict[str, str] = {}
    for athlete, series in zip(events["player"].astype(str), series_of):
        drafted = wanted.get(_normalized(athlete))
        if drafted and drafted not in entrants:
            # The roster's spelling, since that is the key `by_asset` looks up.
            entrants[drafted] = series
    if not entrants:
        return

    ahead: dict[str, list[dict]] = {}
    for series in sorted(set(entrants.values())):
        try:
            ahead[series] = _tour_calendar(series, list(seasons), as_of)
        except Exception as exc:  # noqa: BLE001 -- a fixture is never worth a pull
            print(f"  {source.league}: no {series} calendar "
                  f"({type(exc).__name__}: {exc})", flush=True)
            ahead[series] = []
        if verbose and not ahead[series]:
            print(f"  {source.league}: {series} has no event left on the "
                  f"calendar, so those cells stay blank", flush=True)

    rows = fixtures.tour_rows(source.league, "", entrants, ahead, "")
    if not rows.empty:
        upcoming.append(rows)


def _normalized(name: str) -> str:
    from whul.resolve import normalize_team

    return normalize_team(str(name))


def _settle_umbrella_league(store: Store, mine: pd.DataFrame, report) -> None:
    """Move an asset filed under a roster category onto the league it plays in.

    The spreadsheet's league column stands in with the category when it is
    blank, so six players arrived filed as "Tennis" and "Motorsports" -- which
    are what a manager drafts into, not what anybody competes in. Their scores
    were never wrong: the scorer reads the feed's own league and normalizes ATP
    against ATP. Everything that reads the asset record was: the wrong badge,
    the wrong line under the name, and a filter chip for a league nobody is in.

    Corrected from the feed, which is the same precedence the profile line
    already uses -- the feed is the thing that knows, the sheet is the thing
    somebody typed. Only ever from an umbrella onto one of its own members, so
    a feed naming something unexpected cannot reclassify anybody: a Premier
    League player is not moved to La Liga by this, whatever the feed says.

    The asset id is left alone. It was derived from the league at import and so
    still says `player-tennis-taylor-fritz`, which is ugly and is not worth the
    cost of changing: every score, every stat row and every photograph is
    keyed on it.
    """
    if mine is None or mine.empty or "league" not in mine.columns:
        return
    if "asset_id" not in mine.columns:
        return
    named = {
        str(row.asset_id): str(row.league)
        for row in mine.itertuples()
        if str(getattr(row, "asset_id", "")) and str(getattr(row, "league", ""))
    }
    if not named:
        return
    held = store.query(
        "SELECT asset_id, league, norm_key FROM assets WHERE asset_id IN "
        f"({','.join('?' * len(named))})",
        tuple(named),
    )
    moves = []
    for row in held.itertuples():
        was, now = str(row.league), named.get(str(row.asset_id), "")
        if was == now or now not in covered_by(was):
            continue
        moves.append({
            "asset_id": str(row.asset_id), "league": now,
            # The norm_key follows unless somebody has set it to something
            # else: it is the league for every group that is not split by
            # position, and those are spelled "NFL_QB" rather than "NFL".
            "norm_key": now if str(row.norm_key) == was else str(row.norm_key),
        })
    if not moves:
        return
    # An UPDATE, not an upsert: an upsert is an INSERT with a conflict clause
    # and would have to supply every NOT NULL column of a row that already
    # exists and is otherwise correct.
    with store.transaction():
        store.conn.executemany(
            "UPDATE assets SET league = :league, norm_key = :norm_key "
            "WHERE asset_id = :asset_id",
            moves,
        )
    named_moves = ", ".join(f"{m['asset_id']} -> {m['league']}" for m in moves[:6])
    report.problems.append(
        f"{len(moves)} asset(s) were filed under a roster category rather than "
        f"the league they play in, and have been moved: {named_moves}"
        + (" ..." if len(moves) > 6 else "")
    )


def _harvest(source, raw: pd.DataFrame, as_of: date, seasons, upcoming) -> None:
    """Keep the unplayed half of a schedule the scorer is about to discard.

    Team sources only. A player's next fixture is their club's, joined through
    the club the spreadsheet records against them, so harvesting the player
    feed as well would either duplicate the team rows or -- worse, since a
    player feed carries no fixtures at all -- quietly overwrite them with
    nothing.
    """
    if upcoming is None or source.asset_type != "Team":
        return
    try:
        rows = fixtures.harvest(
            source.league, "", raw, as_of, "",
            rename=fixtures.spelling_for(source.key, seasons),
        )
    except Exception as exc:  # noqa: BLE001 -- a fixture is never worth a pull
        print(f"  {source.league}: no fixtures harvested "
              f"({type(exc).__name__}: {exc})", flush=True)
        return
    if not rows.empty:
        upcoming.append(rows)


def _carry_identity(scored: pd.DataFrame, feed: pd.DataFrame,
                    asset_type: str) -> pd.DataFrame:
    """Put the feed's identity columns back on the scored rows.

    Joined on the name, which is the same key the resolution downstream matches
    on -- so a row this cannot place is a row nothing else could have placed
    either, and it is left without a team rather than given a wrong one.

    A name appearing twice in the feed takes the first, because the duplicate is
    a two-way player filed once as a batter and once as a pitcher: same person,
    same club, and the scorer folds the two rows back together later anyway.
    A column the scorer already produced is never overwritten -- if it did the
    arithmetic on a position, its answer is the considered one.
    """
    from whul.resolve import _name_columns

    if scored is None or scored.empty or feed is None or feed.empty:
        return scored
    key, _ = _name_columns(scored, asset_type)
    if key not in scored.columns or key not in feed.columns:
        return scored

    wanted = [
        c for c in CARRIED_IDENTITY
        if c in feed.columns and c not in scored.columns and c != key
    ]
    if not wanted:
        return scored
    lookup = feed[[key, *wanted]].drop_duplicates(subset=[key], keep="first")
    merged = scored.merge(lookup, on=key, how="left")
    # A merge that changed the row count has matched one scored row to several
    # feed rows, which would silently double a total downstream. Nothing is
    # worth that, so the identity is dropped and the figures stand.
    if len(merged) != len(scored):
        return scored
    return merged


def _record_fixtures(store: Store, source, season: str, as_of: date,
                     upcoming: list) -> int:
    """Swap in what this league plays next. Never fatal.

    A fixture is a convenience on a page; a pull is the season's record. If
    this raises, the pull it rode in on must still land.
    """
    if not upcoming:
        return 0
    try:
        rows = pd.concat(upcoming, ignore_index=True)
        rows["season"] = season
        rows["fetched_at"] = _now()
        return fixtures.replace(store, season, source.league, rows)
    except Exception as exc:  # noqa: BLE001
        print(f"  {source.league}: fixtures not recorded "
              f"({type(exc).__name__}: {exc})", flush=True)
        return 0


def _note_the_unmatched(store: Store, source, as_of: date,
                        report: IngestReport) -> None:
    """Say on the run's own record that somebody rostered went unseen.

    ``record_stats`` writes the source's status from inside the write, where
    the only thing it can know is how many rows landed. So a feed that answers
    fully and healthily, and still accounts for nobody we hold, is recorded as
    `ok=1, rows=14, message=''` -- indistinguishable from a run that saw
    everybody. The roster held fifteen tennis players and the feed named
    fourteen for twenty-five days, and the only place that appeared was a line
    in a seventeen-hundred-line log.

    This is that line, put somewhere a query can reach. It is a note and not a
    failure: a player who misses a tournament injured is *correctly* scoring
    nothing, and neither this nor the feed can tell that apart from a name
    spelled differently. What it can do is stop the two looking identical to
    anybody reading the database afterwards.

    Every reason, not just the missing ones. A rostered player can also go
    unscored because the feed spells him with a generational suffix and the
    roster does not, or because two feed rows answer to his name -- and both of
    those were reaching the log and nothing else, so the database showed a
    league where everybody matched. Those two are the cases a person can
    actually settle, which makes them the ones worth surfacing: the resolver
    holds them back deliberately rather than guessing, and says so here with
    the command that settles it.
    """
    found = report.resolution
    if found is None:
        return
    parts = _why_unscored(found)
    if not parts:
        return
    store.record_source_status(
        source.key, source.league, ok=True, rows=report.recorded,
        last_data_date=as_of.isoformat(),
        message=(
            f"{len(found.matched)} of {found.rostered} rostered matched; "
            + "; ".join(parts)
        ),
    )


def _why_unscored(found) -> list[str]:
    """Every reason a rostered asset came out of this run with no row.

    Three, and they are not the same kind of thing. A feed that never named him
    may simply be right -- an injured player scores nothing, correctly, and no
    feed can say whether the silence is that or a spelling. The other two are
    decisions the resolver deliberately declined to make, which means a person
    can make them, which is what makes them worth carrying out of the log.
    """
    parts = []
    if found.unmatched:
        parts.append("no feed row for " + _first_few(
            f"{name} ({league})" for name, league in found.unmatched))
    if found.suffix_mismatch:
        # The remedy is named because there is one. Still not linked: the
        # suffix is the whole difference between a man and his father, and a
        # resolver that guessed would credit one manager with the other's
        # score with nothing downstream able to tell.
        parts.append(
            _first_few(f"{name} ({league}) is listed as {feed!r}"
                       for name, league, feed in found.suffix_mismatch)
            + " -- link with `whul alias` if they are the same person")
    if found.ambiguous:
        parts.append(_first_few(
            f"{name} ({league}) matches {count} feed rows"
            for name, league, count in found.ambiguous)
            + " -- left unlinked rather than guessed")
    return parts


def _first_few(items, limit: int = 8) -> str:
    """The first few, and how many were not named."""
    listed = list(items)
    named = ", ".join(listed[:limit])
    if len(listed) > limit:
        named += f", and {len(listed) - limit} more"
    return named


def _record_nothing(store: Store, source, as_of: date, report: IngestReport) -> None:
    """Leave a trace for a run that recorded no rows.

    ``source_status`` exists because the dangerous scraper failure is not a
    crash but a feed that quietly stops updating. It was only written on a
    successful record, so the one case it was built for -- a source that ran and
    came back with nothing -- left no row at all, and "ran and found nothing"
    could not be told from "never ran". Eight NCAAF teams sat on zero for a
    fortnight with nothing in the database to say the league had been tried.

    Carries the resolver's reasons where there are any. "No rostered asset
    matched a feed row" is the least informative true sentence available about
    a run where the feed answered and the names did not line up, and that is
    exactly the run where the reason is worth having.
    """
    problems = list(report.problems)
    if report.resolution is not None:
        problems += _why_unscored(report.resolution)
    store.record_source_status(
        source.key, source.league, ok=False, rows=0,
        message="; ".join(problems)[:500],
    )


#: Columns that say *which* asset a row is about, as the several scorers name
#: them. Anything else numeric is a quantity, and quantities are what add.
IDENTITY_COLUMNS = (
    "league", "team", "team_name", "club", "player", "player_id", "role",
    "_phase",
)


def _across_feed_seasons(scored: pd.DataFrame) -> pd.DataFrame:
    """Sum the feed's seasons into the one league year that spans them.

    A league year opening in August catches the tail of an MLS season and the
    front of the next, and the same is true of any league in the year's closing
    weeks. The feed reports those halves separately, and the manager held the
    club through both, so they are added.

    Before resolution rather than after, because a club that arrives as two
    rows is *ambiguous* to the resolver -- two feed rows for one roster slot --
    and would be held back and score nothing at all. The season split is an
    artefact of how the feed files results, not two different clubs.

    Cumulative sources are the exception and are summed later: their halves
    have to be differenced against a baseline first, which needs the asset id
    resolution has not attached yet.
    """
    from whul.store import baselines as baseline_store

    if scored is None or scored.empty or "season" not in scored.columns:
        return scored
    if scored["season"].nunique() <= 1:
        return scored
    keys = [c for c in IDENTITY_COLUMNS if c in scored.columns]
    if not keys:
        return scored
    return baseline_store.combine_seasons(scored, keys)


def _with_finishes(totals: pd.DataFrame, events: pd.DataFrame, current) -> pd.DataFrame:
    """Carry each athlete's actual finishes alongside their total.

    The window machinery sums these rows and drops the detail, which is the
    right answer for a benchmark and the wrong one for a profile: a total says
    how much, and "Winston Salem 250 F" says what happened. Attached here
    rather than recomputed later because this is the only place both the events
    and the window they belong to are in hand.
    """
    from whul.scoring import finishes as finish_summary
    from whul.scoring.window import assign_windows

    if totals.empty or events is None or events.empty:
        return totals
    inside = assign_windows(events, [current])
    records = finish_summary.summarize(inside)
    if not records:
        return totals
    id_col = "player" if "player" in totals.columns else totals.columns[0]
    out = totals.copy()
    out["finishes"] = [records.get(str(name), []) for name in out[id_col]]
    # Tennis, additionally, by the size of the field. Fifty tournaments a year
    # and seven matches in a good week make one list unreadable, and the thing
    # a reader wants -- how he did against fields of each size -- is exactly
    # what a flat list buries: a first-round loss at a 250 and one at a slam
    # are the same line and not remotely the same result.
    if "round" in inside.columns:
        tiers = finish_summary.tier_summary(inside)
        if tiers:
            out["tier_detail"] = [tiers.get(str(name), []) for name in out[id_col]]
    return out
