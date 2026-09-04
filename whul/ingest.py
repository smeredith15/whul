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

    try:
        scored = _pull(source, as_of, verbose, names=list(assets["display_name"]))
    except Exception as exc:  # noqa: BLE001 -- one league must not stop the rest
        report.problems.append(f"could not pull: {type(exc).__name__}: {exc}")
        return report
    if scored is None or scored.empty:
        report.problems.append("the source has no results yet for this season")
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
    source, as_of: date, verbose: bool, names: list[str] | None = None
) -> pd.DataFrame:
    """Season-to-date totals for one league, however that league counts them."""
    from whul.config.league import season_start
    from whul.scoring import window

    live = source.live is not None
    load, score = (source.live or source.build)()
    seasons = source.seasons_for(as_of) if source.seasons_for else [as_of.year]
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
        return score(_from_season_start(raw, source.league))

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
        frames.append(totals.assign(season=current.label))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
