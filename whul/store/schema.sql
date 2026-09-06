-- WHUL scoring store.
--
-- Written in portable SQL: SQLite runs it today, Postgres is the deployment
-- target. Nothing here uses a dialect-only feature -- no SERIAL, no AUTOINCREMENT,
-- no JSON operators -- so the same file applies to both. Dates are ISO-8601 TEXT
-- and payloads are TEXT holding JSON, both of which every engine reads.
--
-- The shape follows one commitment: raw_stats is append-only and dated, and
-- everything else is derived from it. A formula fix is then a recompute rather
-- than a re-scrape, and the daily progression graph can be rebuilt back to the
-- season's first day instead of starting whenever the app went live.

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL
);

-- --------------------------------------------------------------------------
-- Identity
-- --------------------------------------------------------------------------

-- One row per scoreable thing: a player or a team. The id is ours, not a
-- feed's, because an asset is followed across several feeds that each name it
-- differently and none of which is authoritative for all of them.
CREATE TABLE IF NOT EXISTS assets (
    asset_id     TEXT PRIMARY KEY,
    asset_type   TEXT NOT NULL CHECK (asset_type IN ('Player', 'Team')),
    display_name TEXT NOT NULL,
    league       TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT '',
    -- The normalization group, denormalized from league and role so a scoring
    -- change can be traced to the rows it moved.
    norm_key     TEXT NOT NULL DEFAULT '',
    -- Who the asset belongs to, as the league writes it down: a club for a
    -- player in a team sport, a country for an individual athlete. One column
    -- for both because it answers one question -- what goes in the corner of
    -- this asset's picture -- and which of the two it is follows from the
    -- roster category, not from a second column somebody has to keep in step.
    affiliation  TEXT NOT NULL DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS assets_league_idx ON assets (league, asset_type);

-- How each feed names an asset. Feed-native ids are the reliable anchor;
-- names are a fallback, and a name-only match between two feeds is held for
-- review rather than trusted, because the dangerous case is not a spelling
-- variant but two different people with the same name.
CREATE TABLE IF NOT EXISTS asset_aliases (
    source       TEXT NOT NULL,
    source_key   TEXT NOT NULL,
    asset_id     TEXT NOT NULL REFERENCES assets (asset_id),
    match_kind   TEXT NOT NULL CHECK (match_kind IN ('feed_id', 'name', 'manual')),
    needs_review INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (source, source_key)
);

CREATE INDEX IF NOT EXISTS asset_aliases_asset_idx ON asset_aliases (asset_id);
CREATE INDEX IF NOT EXISTS asset_aliases_review_idx ON asset_aliases (needs_review);

-- --------------------------------------------------------------------------
-- Ingest
-- --------------------------------------------------------------------------

-- Season-to-date figures as of one day, one row per asset per day per source.
-- Cumulative rather than per-day deltas: that is what most feeds serve, and
-- differencing consecutive snapshots recovers the deltas exactly, while the
-- reverse -- rebuilding a cumulative total from deltas -- compounds any day
-- the scraper missed.
CREATE TABLE IF NOT EXISTS raw_stats (
    asset_id   TEXT NOT NULL REFERENCES assets (asset_id),
    league     TEXT NOT NULL,
    season     TEXT NOT NULL,
    as_of      TEXT NOT NULL,
    source     TEXT NOT NULL,
    phase      TEXT NOT NULL DEFAULT 'regular'
               CHECK (phase IN ('regular', 'postseason', 'excluded')),
    stats      TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (asset_id, season, as_of, source, phase)
);

CREATE INDEX IF NOT EXISTS raw_stats_day_idx ON raw_stats (season, as_of);
CREATE INDEX IF NOT EXISTS raw_stats_league_idx ON raw_stats (league, season, as_of);

-- What a cumulative feed already read before the league year opened.
--
-- Most feeds report season to date and will not serve a date range, so the
-- share of a season that belongs to this league year cannot be asked for. It
-- can be subtracted: record the figures once, on the first day an asset is
-- rostered, and every later pull minus that baseline is what was earned since.
--
-- Written once and never updated. A baseline that moved would silently rewrite
-- every score derived from it, and the whole point is that it is the fixed
-- point the subtraction is against. `captured_for` is the date the baseline is
-- meant to represent -- the league year's start -- which is not always the day
-- it was taken, and the difference is what says how much of the year the
-- subtraction cannot account for.
CREATE TABLE IF NOT EXISTS stat_baselines (
    asset_id     TEXT NOT NULL REFERENCES assets (asset_id),
    season       TEXT NOT NULL,
    source       TEXT NOT NULL,
    feed_season  INTEGER NOT NULL,
    captured_at  TEXT NOT NULL,
    captured_for TEXT NOT NULL,
    stats        TEXT NOT NULL,
    PRIMARY KEY (asset_id, season, source, feed_season)
);

-- What each source last managed to fetch. The dangerous scraper failure is not
-- a crash but a feed that quietly stops updating: the standings freeze and
-- still look plausible. This is what makes that visible.
CREATE TABLE IF NOT EXISTS source_status (
    source          TEXT NOT NULL,
    league          TEXT NOT NULL,
    last_run_at     TEXT,
    last_data_date  TEXT,
    last_ok         INTEGER NOT NULL DEFAULT 1,
    rows_last_run   INTEGER NOT NULL DEFAULT 0,
    message         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source, league)
);

-- --------------------------------------------------------------------------
-- Scoring
-- --------------------------------------------------------------------------

-- A frozen benchmark set. Versioned rather than recomputed on the fly, so a
-- mid-season formula fix can be replayed without silently moving the scale
-- under the standings that were already published.
CREATE TABLE IF NOT EXISTS benchmark_versions (
    version      TEXT PRIMARY KEY,
    season       TEXT NOT NULL,
    quantile     REAL NOT NULL,
    managers     INTEGER NOT NULL,
    computed_at  TEXT NOT NULL,
    -- Set when the version becomes the one standings are scored against.
    -- A frozen version is never edited; superseding it means a new version.
    frozen_at    TEXT,
    notes        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS benchmarks (
    version    TEXT NOT NULL REFERENCES benchmark_versions (version),
    asset_type TEXT NOT NULL,
    norm_key   TEXT NOT NULL,
    benchmark  REAL NOT NULL,
    pool_size  INTEGER NOT NULL,
    seasons    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (version, asset_type, norm_key)
);

-- Season-to-date score per asset per day: the league formula's output and the
-- same figure on the 0-100 scale. The benchmark version is recorded on every
-- row, so a score can always be explained by the scale it was measured against.
CREATE TABLE IF NOT EXISTS daily_scores (
    asset_id          TEXT NOT NULL REFERENCES assets (asset_id),
    season            TEXT NOT NULL,
    as_of             TEXT NOT NULL,
    league_points     REAL NOT NULL,
    postseason_bonus  REAL NOT NULL DEFAULT 0,
    scaled_score      REAL NOT NULL,
    benchmark_version TEXT NOT NULL REFERENCES benchmark_versions (version),
    computed_at       TEXT NOT NULL,
    PRIMARY KEY (asset_id, season, as_of)
);

CREATE INDEX IF NOT EXISTS daily_scores_day_idx ON daily_scores (season, as_of);

-- --------------------------------------------------------------------------
-- Rosters
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS managers (
    manager_id   TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);

-- A slot is a persistent container owned by a manager. It is the scoring unit,
-- not the asset: a trade changes who occupies a slot, and the points earned
-- before the trade stay with the manager who earned them.
CREATE TABLE IF NOT EXISTS roster_slots (
    slot_id     TEXT PRIMARY KEY,
    manager_id  TEXT NOT NULL REFERENCES managers (manager_id),
    season      TEXT NOT NULL,
    category    TEXT NOT NULL,
    asset_type  TEXT NOT NULL CHECK (asset_type IN ('Player', 'Team')),
    slot_index  INTEGER NOT NULL,
    UNIQUE (manager_id, season, category, asset_type, slot_index)
);

-- Who sat in a slot and when. end_date NULL means still there; the date is
-- inclusive of the occupant's last accruing day.
CREATE TABLE IF NOT EXISTS slot_occupancy (
    slot_id     TEXT NOT NULL REFERENCES roster_slots (slot_id),
    asset_id    TEXT NOT NULL REFERENCES assets (asset_id),
    start_date  TEXT NOT NULL,
    end_date    TEXT,
    -- What the manager paid at auction. On the occupancy rather than the asset
    -- because it is a price for an acquisition: a player traded on is worth
    -- what the next manager gave up, not what the first one bid.
    cost        REAL,
    note        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (slot_id, start_date)
);

CREATE INDEX IF NOT EXISTS slot_occupancy_asset_idx ON slot_occupancy (asset_id);

-- --------------------------------------------------------------------------
-- Standings
-- --------------------------------------------------------------------------

-- One row per slot per day: the score and whether it counted that day. The
-- contribution bar chart reads the latest day; the progression line reads the
-- series. Storing it per day rather than deriving on request means the chart
-- shows what the standings actually said at the time.
CREATE TABLE IF NOT EXISTS slot_scores (
    slot_id   TEXT NOT NULL REFERENCES roster_slots (slot_id),
    season    TEXT NOT NULL,
    as_of     TEXT NOT NULL,
    asset_id  TEXT,
    score     REAL NOT NULL,
    counts    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (slot_id, as_of)
);

CREATE INDEX IF NOT EXISTS slot_scores_day_idx ON slot_scores (season, as_of);

CREATE TABLE IF NOT EXISTS standings_snapshots (
    manager_id TEXT NOT NULL REFERENCES managers (manager_id),
    season     TEXT NOT NULL,
    as_of      TEXT NOT NULL,
    total      REAL NOT NULL,
    rank       INTEGER NOT NULL,
    PRIMARY KEY (manager_id, season, as_of)
);

CREATE INDEX IF NOT EXISTS standings_day_idx ON standings_snapshots (season, as_of);

-- --------------------------------------------------------------------------
-- Admin
-- --------------------------------------------------------------------------

-- Admin-entered values the pipeline reads: a shortened season's expected game
-- count, a schedule change, a manual correction. Kept as rows rather than
-- configuration so a change is dated and attributable.
CREATE TABLE IF NOT EXISTS admin_overrides (
    scope      TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    season     TEXT NOT NULL,
    set_by     TEXT NOT NULL DEFAULT '',
    set_at     TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (scope, key, season)
);
