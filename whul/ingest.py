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
from datetime import date

import pandas as pd

from whul import resolve as resolver
from whul.normalize import apply_benchmarks
from whul.pipeline import write_daily_scores
from whul.store import benchmarks as store_benchmarks
from whul.store.db import Store


@dataclass
class IngestReport:
    """What one league's run did, in a shape a person can check."""

    league: str
    asset_type: str = "Player"
    pulled: int = 0
    matched: int = 0
    scored: int = 0
    recorded: int = 0
    version: str = ""
    resolution: resolver.Resolution | None = None
    problems: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"{self.league} {self.asset_type.lower()}s: {self.pulled:,} feed rows, "
            f"{self.matched} rostered matched, {self.scored} scored"
        ]
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
    try:
        scored = _pull(
            source, as_of, verbose, names=list(assets["display_name"]), notes=notes
        )
    except Exception as exc:  # noqa: BLE001 -- one league must not stop the rest
        report.problems.append(f"could not pull: {type(exc).__name__}: {exc}")
        return report
    if scored is None or scored.empty:
        report.problems.append(
            notes[0] if notes else "the source has no results yet for this season"
        )
        return report
    report.pulled = len(scored)

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
        return report
    resolver.save_aliases(store, source.key, resolution.matched)

    if getattr(source, "cumulative", False):
        mine = _against_the_league_year(store, mine, source, season, as_of, report)

    _report_shrinkage(store, mine, source, season, as_of, report)

    # Raw first, and unconditionally. A benchmark can be computed next week;
    # a rolling feed's earlier weeks cannot be fetched back.
    report.recorded = store.record_stats(
        mine.to_dict("records"), source=source.key, season=season,
        as_of=as_of, league=source.league,
    )

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


#: A windowed total below the last one by more than this is reported. A small
#: dip is possible where a scorer's inputs are revised; a large one is a feed
#: that has stopped reaching as far back as it did.
SHRINKAGE_TOLERANCE = 0.005


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

    return baseline_store.combine_seasons(mine, ["asset_id", "role"])


def _replace_block(frame: pd.DataFrame, feed_season, replacement: pd.DataFrame):
    """Swap one feed season's rows for their differenced selves."""
    rest = frame[frame["season"] != feed_season]
    return pd.concat([rest, replacement], ignore_index=True)


def _why_nothing_scored(raw: pd.DataFrame, kept: pd.DataFrame) -> str:
    """What arrived, when a full fetch scored nothing.

    Three things look identical from the outside -- a feed with nothing in it,
    a feed whose rows all fall before the league year opened, and a schedule of
    fixtures nobody has played. Only the last is normal, and it is the one that
    reads most like a broken adapter.
    """
    if kept.empty:
        return (
            f"the feed returned {len(raw):,} row(s), but all of them fall "
            f"before this league's results start counting"
        )

    played = kept
    if "completed" in kept.columns:
        played = kept[kept["completed"].fillna(False).astype(bool)]
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


#: Columns a raw feed puts an event's date in, in the order worth trying.
DATE_COLUMNS = ("date", "game_date", "event_date", "match_date", "start_date")


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


def _pull(
    source, as_of: date, verbose: bool, names: list[str] | None = None,
    notes: list[str] | None = None,
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
        return pd.DataFrame()
    # A roster-scoped loader is asked only for what the roster holds, which for
    # a team league is eight requests rather than a season of dates.
    fetch = (
        (lambda years: load(years, names or []))
        if live and source.roster_scoped
        else load
    )
    if not source.windowed:
        raw = fetch(seasons)
        if raw is None or raw.empty:
            return pd.DataFrame()
        kept = _from_season_start(raw, source.league)
        scored = score(kept)
        if (scored is None or scored.empty) and notes is not None:
            notes.append(_why_nothing_scored(raw, kept))
        if not getattr(source, "cumulative", False):
            scored = _across_feed_seasons(scored)
        return scored

    # A continuously running sport accrues over the league year, not the
    # calendar one, so its live total is summed over the same window its
    # benchmark was drawn over -- and over each produced league's own window,
    # since two series sharing a pull need not start on the same day.
    years = sorted({season_start(source.league).year, as_of.year})
    events = score(fetch(years))
    if events is None or events.empty:
        return pd.DataFrame()

    frames = []
    for name in source.produces or (source.league,):
        rows = events[events["league"].astype(str) == name] \
            if "league" in events.columns else events
        if rows.empty:
            continue
        current = window.season_windows(0, start=season_start(name))[-1]
        totals = window.window_totals(rows, [current])
        frames.append(_with_finishes(totals.assign(season=current.label), rows, current))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


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
    return out
