"""Canonical SQLite schema for the local market-data store."""

from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    exchange TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS instruments_kind_active
ON instruments(kind, active, symbol);

CREATE TABLE IF NOT EXISTS custom_instrument_groups (
    group_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_instrument_group_members (
    group_id TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    tags_json TEXT NOT NULL,
    note TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (group_id, instrument_id),
    UNIQUE (group_id, position),
    FOREIGN KEY (group_id) REFERENCES custom_instrument_groups(group_id) ON DELETE CASCADE,
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS custom_group_members_order
ON custom_instrument_group_members(group_id, position);

CREATE TABLE IF NOT EXISTS source_profiles (
    source_id INTEGER PRIMARY KEY,
    acquired_via TEXT NOT NULL,
    source_system TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE TABLE IF NOT EXISTS data_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at_ms INTEGER NOT NULL,
    affected_rows INTEGER NOT NULL,
    details TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instrument_catalog_entries (
    catalog_source_id INTEGER NOT NULL,
    provider_symbol TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    source_system TEXT NOT NULL,
    family TEXT NOT NULL,
    category TEXT NOT NULL,
    observed_on INTEGER NOT NULL,
    listed_on INTEGER,
    delisted_on INTEGER,
    constituent_count INTEGER,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (catalog_source_id, provider_symbol),
    FOREIGN KEY (catalog_source_id) REFERENCES sources(source_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS instrument_catalog_family
ON instrument_catalog_entries(source_system, family, category, provider_symbol);

CREATE TABLE IF NOT EXISTS instrument_aliases (
    catalog_source_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (catalog_source_id, instrument_id, alias),
    FOREIGN KEY (catalog_source_id) REFERENCES sources(source_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS instrument_aliases_instrument
ON instrument_aliases(instrument_id, alias);

CREATE TABLE IF NOT EXISTS board_memberships (
    source_id INTEGER NOT NULL,
    board_instrument_id INTEGER NOT NULL,
    member_symbol TEXT NOT NULL,
    member_name TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    first_seen_on INTEGER NOT NULL,
    last_seen_on INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, board_instrument_id, member_symbol),
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (board_instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS board_memberships_member
ON board_memberships(member_symbol, active, board_instrument_id);

CREATE INDEX IF NOT EXISTS board_memberships_board
ON board_memberships(board_instrument_id, active, member_symbol);

CREATE TABLE IF NOT EXISTS market_snapshots (
    instrument_id INTEGER NOT NULL,
    trade_date INTEGER NOT NULL,
    change_percent REAL NOT NULL,
    total_market_cap REAL,
    source_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, trade_date),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS market_snapshots_latest
ON market_snapshots(instrument_id, trade_date DESC);

CREATE TABLE IF NOT EXISTS etf_holdings (
    source_id INTEGER NOT NULL,
    etf_instrument_id INTEGER NOT NULL,
    as_of_date INTEGER NOT NULL,
    holding_symbol TEXT NOT NULL,
    holding_name TEXT NOT NULL,
    quantity REAL,
    weight_percent REAL,
    market_value REAL,
    holding_rank INTEGER,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, etf_instrument_id, as_of_date, holding_symbol),
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (etf_instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS etf_holdings_latest
ON etf_holdings(etf_instrument_id, as_of_date DESC, holding_rank, holding_symbol);

CREATE TABLE IF NOT EXISTS etf_holding_receipts (
    source_id INTEGER NOT NULL,
    etf_instrument_id INTEGER NOT NULL,
    requested_date INTEGER NOT NULL,
    as_of_date INTEGER,
    row_count INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'empty')),
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, etf_instrument_id, requested_date),
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (etf_instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS instrument_mappings (
    left_instrument_id INTEGER NOT NULL,
    right_instrument_id INTEGER NOT NULL,
    relation TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    evidence TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (left_instrument_id, right_instrument_id, relation),
    FOREIGN KEY (left_instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (right_instrument_id) REFERENCES instruments(instrument_id),
    CHECK (left_instrument_id <> right_instrument_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS daily_bars (
    instrument_id INTEGER NOT NULL,
    trade_date INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, trade_date),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS daily_bars_trade_date
ON daily_bars(trade_date, instrument_id);

CREATE TABLE IF NOT EXISTS sync_cursors (
    source_id INTEGER NOT NULL,
    instrument_kind TEXT NOT NULL,
    last_trade_date INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, instrument_kind),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS symbol_sync_states (
    source_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    covered_from INTEGER NOT NULL,
    covered_through INTEGER NOT NULL,
    last_batch_rows INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, scope, instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS symbol_sync_states_scope
ON symbol_sync_states(scope, covered_through, instrument_id);

CREATE TABLE IF NOT EXISTS daily_snapshot_receipts (
    source_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    trade_date INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    payload_hash BLOB NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, scope, trade_date),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS trading_calendar (
    source_id INTEGER NOT NULL,
    trade_date INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, trade_date),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS coverage_gaps (
    source_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    trade_date INTEGER NOT NULL,
    reason TEXT NOT NULL,
    observed_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, scope, trade_date),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS provider_incidents (
    incident_id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    dataset TEXT NOT NULL,
    scope TEXT NOT NULL,
    trade_date INTEGER NOT NULL,
    incident_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
    occurrence_count INTEGER NOT NULL,
    message TEXT NOT NULL,
    first_observed_at_ms INTEGER NOT NULL,
    last_observed_at_ms INTEGER NOT NULL,
    resolved_at_ms INTEGER,
    resolution TEXT,
    UNIQUE (source_id, dataset, scope, trade_date, incident_type),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE INDEX IF NOT EXISTS provider_incidents_status
ON provider_incidents(status, trade_date);

CREATE TABLE IF NOT EXISTS provider_validation_results (
    validation_id INTEGER PRIMARY KEY,
    incident_id INTEGER,
    primary_source_id INTEGER NOT NULL,
    validator_source_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    trade_date INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('match', 'mismatch', 'missing', 'error')),
    message TEXT NOT NULL,
    checked_at_ms INTEGER NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES provider_incidents(incident_id),
    FOREIGN KEY (primary_source_id) REFERENCES sources(source_id),
    FOREIGN KEY (validator_source_id) REFERENCES sources(source_id)
);

CREATE INDEX IF NOT EXISTS provider_validation_results_lookup
ON provider_validation_results(trade_date, symbol, validator_source_id);

CREATE TABLE IF NOT EXISTS repair_jobs (
    job_id INTEGER PRIMARY KEY,
    primary_source_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    trade_date INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'partial', 'failed')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    expected_rows INTEGER NOT NULL DEFAULT 0,
    repaired_rows INTEGER NOT NULL DEFAULT 0,
    unresolved_rows INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (primary_source_id, scope, trade_date),
    FOREIGN KEY (primary_source_id) REFERENCES sources(source_id)
);

CREATE INDEX IF NOT EXISTS repair_jobs_status_date
ON repair_jobs(status, trade_date);

CREATE TABLE IF NOT EXISTS repair_items (
    job_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    repair_source_id INTEGER,
    status TEXT NOT NULL CHECK (status IN ('repaired', 'unresolved')),
    message TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (job_id, symbol),
    FOREIGN KEY (job_id) REFERENCES repair_jobs(job_id),
    FOREIGN KEY (repair_source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TRIGGER IF NOT EXISTS coverage_gap_opens_provider_incident
AFTER INSERT ON coverage_gaps
BEGIN
    INSERT OR IGNORE INTO provider_incidents(
        source_id, dataset, scope, trade_date, incident_type, status,
        occurrence_count, message, first_observed_at_ms, last_observed_at_ms
    ) VALUES (
        NEW.source_id, 'daily_ohlcv', NEW.scope, NEW.trade_date,
        'empty_open_trade_date', 'open', 1, NEW.reason,
        NEW.observed_at_ms, NEW.observed_at_ms
    );
END;

CREATE TRIGGER IF NOT EXISTS daily_snapshot_resolves_empty_incident
AFTER INSERT ON daily_snapshot_receipts
BEGIN
    UPDATE provider_incidents
    SET status = 'resolved',
        resolved_at_ms = NEW.updated_at_ms,
        resolution = 'daily snapshot stored after provider retry'
    WHERE source_id = NEW.source_id
      AND scope = NEW.scope
      AND trade_date = NEW.trade_date
      AND incident_type = 'empty_open_trade_date'
      AND status = 'open';
END;

CREATE TRIGGER IF NOT EXISTS coverage_gap_queues_repair_job
AFTER INSERT ON coverage_gaps
BEGIN
    INSERT OR IGNORE INTO repair_jobs(
        primary_source_id, scope, trade_date, status, created_at_ms, updated_at_ms
    ) VALUES (
        NEW.source_id, NEW.scope, NEW.trade_date, 'queued',
        NEW.observed_at_ms, NEW.observed_at_ms
    );
END;

CREATE TRIGGER IF NOT EXISTS primary_snapshot_completes_repair_job
AFTER INSERT ON daily_snapshot_receipts
BEGIN
    UPDATE repair_jobs
    SET status = 'completed',
        unresolved_rows = 0,
        last_error = NULL,
        updated_at_ms = NEW.updated_at_ms
    WHERE primary_source_id = NEW.source_id
      AND scope = NEW.scope
      AND trade_date = NEW.trade_date
      AND status IN ('queued', 'running', 'partial', 'failed');
END;
"""

