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

CREATE TABLE IF NOT EXISTS instrument_tags (
    instrument_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    position INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, tag),
    UNIQUE (instrument_id, position),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_instrument_tags_tag
ON instrument_tags(tag, instrument_id);

CREATE TABLE IF NOT EXISTS instrument_board_tags (
    instrument_id INTEGER NOT NULL,
    board_instrument_id INTEGER NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('industry', 'concept')),
    position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 2),
    source_system TEXT NOT NULL,
    selection_score REAL NOT NULL,
    selection_reason TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    generated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, position),
    UNIQUE (instrument_id, board_instrument_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (board_instrument_id) REFERENCES instruments(instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_instrument_board_tags_board
ON instrument_board_tags(board_instrument_id, instrument_id);

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
    role TEXT NOT NULL DEFAULT '',
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
    close REAL,
    volume INTEGER,
    amount REAL,
    source_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, trade_date),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS market_snapshots_latest
ON market_snapshots(instrument_id, trade_date DESC);

CREATE TABLE IF NOT EXISTS active_market_value_features (
    instrument_id INTEGER NOT NULL,
    trade_date INTEGER NOT NULL,
    turnover_rate_f REAL NOT NULL CHECK (turnover_rate_f >= 0),
    free_share REAL NOT NULL CHECK (free_share > 0),
    circ_market_value REAL NOT NULL CHECK (circ_market_value >= 0),
    total_market_value REAL NOT NULL CHECK (total_market_value >= 0),
    close REAL NOT NULL CHECK (close > 0),
    source_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, trade_date),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS active_market_value_features_date
ON active_market_value_features(trade_date, instrument_id);

CREATE TABLE IF NOT EXISTS active_market_value_feature_receipts (
    source_id INTEGER NOT NULL,
    trade_date INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    skipped_count INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'empty')),
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, trade_date),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS active_market_value_definitions (
    definition_id TEXT PRIMARY KEY,
    instrument_id INTEGER NOT NULL UNIQUE,
    algorithm_version TEXT NOT NULL,
    smoothing_period INTEGER NOT NULL CHECK (smoothing_period > 0),
    scale_k REAL NOT NULL CHECK (scale_k > 0),
    turnover_cap REAL NOT NULL CHECK (turnover_cap > 0),
    base_value REAL NOT NULL CHECK (base_value > 0),
    base_date INTEGER,
    status TEXT NOT NULL CHECK (status IN ('pending', 'building', 'ready', 'error')),
    last_error TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);

CREATE TABLE IF NOT EXISTS active_market_value_daily_bars (
    definition_id TEXT NOT NULL,
    trade_date INTEGER NOT NULL,
    absolute_open REAL NOT NULL,
    absolute_high REAL NOT NULL,
    absolute_low REAL NOT NULL,
    absolute_close REAL NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    eligible_count INTEGER NOT NULL,
    total_count INTEGER NOT NULL,
    coverage_ratio REAL NOT NULL CHECK (coverage_ratio BETWEEN 0 AND 1),
    input_digest TEXT NOT NULL DEFAULT '',
    contribution_total REAL NOT NULL DEFAULT 0,
    algorithm_version TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (definition_id, trade_date),
    FOREIGN KEY (definition_id) REFERENCES active_market_value_definitions(definition_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS active_market_value_daily_bars_date
ON active_market_value_daily_bars(trade_date, definition_id);

CREATE TABLE IF NOT EXISTS active_market_value_build_runs (
    run_id INTEGER PRIMARY KEY,
    definition_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('backfill', 'incremental', 'correction')),
    from_date INTEGER,
    through_date INTEGER,
    row_count INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    message TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    FOREIGN KEY (definition_id) REFERENCES active_market_value_definitions(definition_id)
);

CREATE TABLE IF NOT EXISTS active_market_value_stock_states (
    definition_id TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    as_of_date INTEGER NOT NULL,
    smoothed_turnover REAL NOT NULL CHECK (smoothed_turnover >= 0),
    active_close REAL NOT NULL DEFAULT 0 CHECK (active_close >= 0),
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (definition_id, instrument_id),
    FOREIGN KEY (definition_id) REFERENCES active_market_value_definitions(definition_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS active_market_value_daily_contributions (
    definition_id TEXT NOT NULL,
    trade_date INTEGER NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('positive', 'negative')),
    contribution_rank INTEGER NOT NULL CHECK (contribution_rank BETWEEN 1 AND 10),
    instrument_id INTEGER NOT NULL,
    active_close REAL NOT NULL CHECK (active_close >= 0),
    change_contribution REAL NOT NULL,
    algorithm_version TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (definition_id, trade_date, direction, contribution_rank),
    FOREIGN KEY (definition_id) REFERENCES active_market_value_definitions(definition_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS active_market_value_contributions_instrument
ON active_market_value_daily_contributions(instrument_id, trade_date DESC);

CREATE TABLE IF NOT EXISTS active_market_value_dirty_ranges (
    definition_id TEXT PRIMARY KEY,
    dirty_from INTEGER NOT NULL,
    dirty_through INTEGER NOT NULL,
    reason TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (definition_id) REFERENCES active_market_value_definitions(definition_id)
) WITHOUT ROWID;

CREATE TRIGGER IF NOT EXISTS active_market_value_feature_insert_marks_dirty
AFTER INSERT ON active_market_value_features
WHEN NOT EXISTS (
    SELECT 1 FROM active_market_value_dirty_ranges
    WHERE dirty_from <= NEW.trade_date AND dirty_through >= NEW.trade_date
      AND updated_at_ms >= NEW.updated_at_ms
)
BEGIN
    INSERT INTO active_market_value_dirty_ranges(
        definition_id, dirty_from, dirty_through, reason, updated_at_ms
    )
    SELECT definition_id, NEW.trade_date, NEW.trade_date,
           'feature_inserted', NEW.updated_at_ms
    FROM active_market_value_definitions WHERE 1
    ON CONFLICT(definition_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        dirty_through = max(dirty_through, excluded.dirty_through),
        reason = excluded.reason,
        updated_at_ms = max(updated_at_ms, excluded.updated_at_ms);
END;

CREATE TRIGGER IF NOT EXISTS active_market_value_feature_update_marks_dirty
AFTER UPDATE ON active_market_value_features
WHEN (
    OLD.turnover_rate_f IS NOT NEW.turnover_rate_f
    OR OLD.free_share IS NOT NEW.free_share
    OR OLD.close IS NOT NEW.close
)
AND NOT EXISTS (
    SELECT 1 FROM active_market_value_dirty_ranges
    WHERE dirty_from <= NEW.trade_date AND dirty_through >= NEW.trade_date
      AND updated_at_ms >= NEW.updated_at_ms
)
BEGIN
    INSERT INTO active_market_value_dirty_ranges(
        definition_id, dirty_from, dirty_through, reason, updated_at_ms
    )
    SELECT definition_id, NEW.trade_date, NEW.trade_date,
           'feature_corrected', NEW.updated_at_ms
    FROM active_market_value_definitions WHERE 1
    ON CONFLICT(definition_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        dirty_through = max(dirty_through, excluded.dirty_through),
        reason = excluded.reason,
        updated_at_ms = max(updated_at_ms, excluded.updated_at_ms);
END;

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

CREATE TRIGGER IF NOT EXISTS active_market_value_stock_bar_insert_marks_dirty
AFTER INSERT ON daily_bars
WHEN EXISTS (
    SELECT 1 FROM instruments
    WHERE instrument_id = NEW.instrument_id AND kind = 'stock'
)
AND NOT EXISTS (
    SELECT 1 FROM active_market_value_dirty_ranges
    WHERE dirty_from <= NEW.trade_date AND dirty_through >= NEW.trade_date
      AND updated_at_ms >= NEW.updated_at_ms
)
BEGIN
    INSERT INTO active_market_value_dirty_ranges(
        definition_id, dirty_from, dirty_through, reason, updated_at_ms
    )
    SELECT definition_id, NEW.trade_date, NEW.trade_date,
           'stock_bar_inserted', NEW.updated_at_ms
    FROM active_market_value_definitions WHERE 1
    ON CONFLICT(definition_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        dirty_through = max(dirty_through, excluded.dirty_through),
        reason = excluded.reason,
        updated_at_ms = max(updated_at_ms, excluded.updated_at_ms);
END;

CREATE TRIGGER IF NOT EXISTS active_market_value_stock_bar_update_marks_dirty
AFTER UPDATE ON daily_bars
WHEN EXISTS (
    SELECT 1 FROM instruments
    WHERE instrument_id = NEW.instrument_id AND kind = 'stock'
)
AND (
    OLD.open IS NOT NEW.open OR OLD.high IS NOT NEW.high
    OR OLD.low IS NOT NEW.low OR OLD.close IS NOT NEW.close
)
AND NOT EXISTS (
    SELECT 1 FROM active_market_value_dirty_ranges
    WHERE dirty_from <= NEW.trade_date AND dirty_through >= NEW.trade_date
      AND updated_at_ms >= NEW.updated_at_ms
)
BEGIN
    INSERT INTO active_market_value_dirty_ranges(
        definition_id, dirty_from, dirty_through, reason, updated_at_ms
    )
    SELECT definition_id, NEW.trade_date, NEW.trade_date,
           'stock_bar_corrected', NEW.updated_at_ms
    FROM active_market_value_definitions WHERE 1
    ON CONFLICT(definition_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        dirty_through = max(dirty_through, excluded.dirty_through),
        reason = excluded.reason,
        updated_at_ms = max(updated_at_ms, excluded.updated_at_ms);
END;

CREATE TABLE IF NOT EXISTS stock_adjustment_factors (
    instrument_id INTEGER NOT NULL,
    trade_date INTEGER NOT NULL,
    factor REAL NOT NULL CHECK (factor > 0),
    source_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, trade_date),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS stock_trade_status (
    instrument_id INTEGER NOT NULL,
    trade_date INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('listed', 'trading', 'suspended', 'delisted')),
    source_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, trade_date),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS custom_indices (
    index_id TEXT PRIMARY KEY,
    instrument_id INTEGER NOT NULL UNIQUE,
    description TEXT NOT NULL,
    base_date INTEGER NOT NULL,
    base_value REAL NOT NULL CHECK (base_value > 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'building', 'ready', 'error')),
    calculation_version TEXT NOT NULL,
    last_error TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);

CREATE TABLE IF NOT EXISTS custom_index_revisions (
    revision_id INTEGER PRIMARY KEY,
    index_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    effective_from INTEGER NOT NULL,
    weighting_method TEXT NOT NULL CHECK (weighting_method IN ('equal', 'manual')),
    created_at_ms INTEGER NOT NULL,
    UNIQUE (index_id, revision_number),
    FOREIGN KEY (index_id) REFERENCES custom_indices(index_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS custom_index_revision_members (
    revision_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    raw_weight REAL NOT NULL CHECK (raw_weight > 0),
    normalized_weight REAL NOT NULL CHECK (normalized_weight > 0),
    PRIMARY KEY (revision_id, instrument_id),
    UNIQUE (revision_id, position),
    FOREIGN KEY (revision_id) REFERENCES custom_index_revisions(revision_id) ON DELETE CASCADE,
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS custom_index_daily_bars (
    index_id TEXT NOT NULL,
    trade_date INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL DEFAULT 0,
    daily_return REAL NOT NULL,
    eligible_count INTEGER NOT NULL,
    total_count INTEGER NOT NULL,
    quality_status TEXT NOT NULL CHECK (quality_status IN ('complete', 'inferred_suspension')),
    input_hash BLOB NOT NULL,
    calculation_version TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (index_id, trade_date),
    FOREIGN KEY (index_id) REFERENCES custom_indices(index_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS custom_index_daily_bars_date
ON custom_index_daily_bars(trade_date, index_id);

CREATE TABLE IF NOT EXISTS custom_index_calculation_runs (
    run_id INTEGER PRIMARY KEY,
    index_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('backfill', 'incremental', 'correction')),
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    from_date INTEGER,
    through_date INTEGER,
    row_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    message TEXT NOT NULL,
    FOREIGN KEY (index_id) REFERENCES custom_indices(index_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS custom_index_dirty_dates (
    index_id TEXT PRIMARY KEY,
    dirty_from INTEGER NOT NULL,
    reason TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (index_id) REFERENCES custom_indices(index_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TRIGGER IF NOT EXISTS custom_index_dirty_after_daily_bar_update
AFTER UPDATE OF open, high, low, close ON daily_bars
WHEN OLD.open IS NOT NEW.open OR OLD.high IS NOT NEW.high
  OR OLD.low IS NOT NEW.low OR OLD.close IS NOT NEW.close
BEGIN
    INSERT INTO custom_index_dirty_dates(index_id, dirty_from, reason, updated_at_ms)
    SELECT DISTINCT revision.index_id, NEW.trade_date, 'constituent_bar_corrected', NEW.updated_at_ms
    FROM custom_index_revision_members AS member
    JOIN custom_index_revisions AS revision USING (revision_id)
    WHERE member.instrument_id = NEW.instrument_id
    ON CONFLICT(index_id) DO UPDATE SET
        dirty_from = min(custom_index_dirty_dates.dirty_from, excluded.dirty_from),
        reason = excluded.reason,
        updated_at_ms = excluded.updated_at_ms;
END;

CREATE TRIGGER IF NOT EXISTS custom_index_dirty_after_daily_bar_volume_update
AFTER UPDATE OF volume ON daily_bars
WHEN OLD.volume IS NOT NEW.volume
BEGIN
    INSERT INTO custom_index_dirty_dates(index_id, dirty_from, reason, updated_at_ms)
    SELECT DISTINCT revision.index_id, NEW.trade_date, 'constituent_volume_corrected', NEW.updated_at_ms
    FROM custom_index_revision_members AS member
    JOIN custom_index_revisions AS revision USING (revision_id)
    WHERE member.instrument_id = NEW.instrument_id
    ON CONFLICT(index_id) DO UPDATE SET
        dirty_from = min(custom_index_dirty_dates.dirty_from, excluded.dirty_from),
        reason = excluded.reason,
        updated_at_ms = excluded.updated_at_ms;
END;

CREATE TRIGGER IF NOT EXISTS custom_index_dirty_after_adjustment_update
AFTER UPDATE OF factor ON stock_adjustment_factors
WHEN OLD.factor IS NOT NEW.factor
BEGIN
    INSERT INTO custom_index_dirty_dates(index_id, dirty_from, reason, updated_at_ms)
    SELECT DISTINCT revision.index_id, NEW.trade_date, 'adjustment_factor_corrected', NEW.updated_at_ms
    FROM custom_index_revision_members AS member
    JOIN custom_index_revisions AS revision USING (revision_id)
    WHERE member.instrument_id = NEW.instrument_id
    ON CONFLICT(index_id) DO UPDATE SET
        dirty_from = min(custom_index_dirty_dates.dirty_from, excluded.dirty_from),
        reason = excluded.reason,
        updated_at_ms = excluded.updated_at_ms;
END;

CREATE TABLE IF NOT EXISTS generated_analysis_runs (
    run_id TEXT PRIMARY KEY,
    system_id TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    timeframe TEXT NOT NULL,
    namespace TEXT NOT NULL CHECK (namespace IN ('official', 'preview')),
    as_of_date INTEGER NOT NULL,
    input_start_date INTEGER NOT NULL,
    input_end_date INTEGER NOT NULL,
    input_digest BLOB NOT NULL,
    algorithm_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    completion_state TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    duration_ms REAL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    failure_details TEXT,
    source_observed_at_ms INTEGER,
    expires_at_ms INTEGER,
    supersedes_run_id TEXT,
    created_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (supersedes_run_id) REFERENCES generated_analysis_runs(run_id)
);

CREATE INDEX IF NOT EXISTS generated_analysis_run_latest
ON generated_analysis_runs(
    instrument_id, system_id, timeframe, namespace, as_of_date DESC, created_at_ms DESC
);

CREATE INDEX IF NOT EXISTS generated_analysis_run_identity
ON generated_analysis_runs(
    instrument_id, system_id, timeframe, namespace, as_of_date,
    input_digest, algorithm_version, config_version, status
);

CREATE TABLE IF NOT EXISTS generated_analysis_items (
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_type TEXT NOT NULL CHECK (
        item_type IN ('anchor', 'line', 'zone', 'pattern', 'transition', 'evidence')
    ),
    parent_item_id TEXT,
    sequence INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, item_id),
    UNIQUE (run_id, sequence),
    FOREIGN KEY (run_id) REFERENCES generated_analysis_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, parent_item_id)
        REFERENCES generated_analysis_items(run_id, item_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS generated_analysis_items_type
ON generated_analysis_items(run_id, item_type, sequence);

CREATE TABLE IF NOT EXISTS ai_analysis_reports (
    report_id TEXT PRIMARY KEY,
    instrument_id INTEGER NOT NULL,
    timeframe TEXT NOT NULL CHECK (timeframe IN ('daily', 'weekly', 'monthly')),
    as_of_date INTEGER NOT NULL,
    source_run_id TEXT,
    title TEXT NOT NULL,
    conclusion_markdown TEXT NOT NULL,
    framework_json TEXT NOT NULL,
    references_json TEXT NOT NULL,
    author TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    created_at_ms INTEGER NOT NULL,
    UNIQUE (instrument_id, timeframe, revision),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (source_run_id) REFERENCES generated_analysis_runs(run_id)
);

CREATE INDEX IF NOT EXISTS ai_analysis_reports_latest
ON ai_analysis_reports(instrument_id, timeframe, revision DESC);

CREATE TABLE IF NOT EXISTS ai_chat_conversations (
    conversation_id TEXT PRIMARY KEY,
    context_kind TEXT NOT NULL DEFAULT 'trend_analysis' CHECK (
        context_kind IN ('trend_analysis', 'signal_run')
    ),
    context_id TEXT NOT NULL,
    instrument_id INTEGER,
    timeframe TEXT CHECK (timeframe IN ('daily', 'weekly', 'monthly')),
    source_run_id TEXT,
    title TEXT NOT NULL,
    codex_thread_id TEXT,
    codex_policy_version TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);

CREATE INDEX IF NOT EXISTS ai_chat_conversations_latest
ON ai_chat_conversations(instrument_id, timeframe, updated_at_ms DESC);

CREATE TABLE IF NOT EXISTS ai_chat_turns (
    turn_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    codex_turn_id TEXT,
    template_id TEXT,
    template_version TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'cancelled')
    ),
    error TEXT,
    created_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    FOREIGN KEY (conversation_id)
        REFERENCES ai_chat_conversations(conversation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ai_chat_turns_conversation
ON ai_chat_turns(conversation_id, created_at_ms, turn_id);

CREATE TABLE IF NOT EXISTS ai_chat_messages (
    message_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    content TEXT NOT NULL,
    incomplete INTEGER NOT NULL DEFAULT 0 CHECK (incomplete IN (0, 1)),
    created_at_ms INTEGER NOT NULL,
    UNIQUE (turn_id, sequence),
    FOREIGN KEY (turn_id) REFERENCES ai_chat_turns(turn_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_chat_turn_contexts (
    turn_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    context_kind TEXT NOT NULL DEFAULT 'trend_analysis' CHECK (
        context_kind IN ('trend_analysis', 'signal_run')
    ),
    context_id TEXT NOT NULL,
    source_run_id TEXT,
    as_of_date INTEGER NOT NULL,
    input_digest TEXT NOT NULL,
    context_json TEXT NOT NULL,
    FOREIGN KEY (turn_id) REFERENCES ai_chat_turns(turn_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS ai_chat_message_references (
    message_id TEXT NOT NULL,
    code TEXT NOT NULL,
    analysis_item_id TEXT NOT NULL,
    PRIMARY KEY (message_id, code),
    FOREIGN KEY (message_id) REFERENCES ai_chat_messages(message_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS ai_chat_stream_events (
    turn_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    event_type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (turn_id, sequence),
    FOREIGN KEY (turn_id) REFERENCES ai_chat_turns(turn_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS screener_runs (
    run_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    as_of_date INTEGER NOT NULL,
    parameters_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    universe_count INTEGER NOT NULL DEFAULT 0,
    scanned_count INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER
);

CREATE INDEX IF NOT EXISTS screener_runs_latest
ON screener_runs(started_at_ms DESC);

CREATE TABLE IF NOT EXISTS screener_candidates (
    run_id TEXT NOT NULL,
    rank INTEGER NOT NULL CHECK (rank > 0),
    instrument_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('critical-breakout', 'breakout-retest', 'broken-out')
    ),
    score REAL NOT NULL,
    line_item_id TEXT NOT NULL,
    line_code TEXT NOT NULL,
    analysis_run_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (run_id, instrument_id),
    UNIQUE (run_id, rank),
    FOREIGN KEY (run_id) REFERENCES screener_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (analysis_run_id) REFERENCES generated_analysis_runs(run_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS screener_candidates_rank
ON screener_candidates(run_id, rank);

CREATE TABLE IF NOT EXISTS signal_review_runs (
    run_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    definition_version TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    cadence TEXT NOT NULL,
    effective_date INTEGER NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    prior_run_id TEXT,
    parameters_json TEXT NOT NULL,
    input_digest TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    phase TEXT NOT NULL,
    work_total INTEGER NOT NULL DEFAULT 0,
    work_done INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    added_count INTEGER NOT NULL DEFAULT 0,
    retained_count INTEGER NOT NULL DEFAULT 0,
    removed_count INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    UNIQUE (signal_id, effective_date, revision),
    FOREIGN KEY (prior_run_id) REFERENCES signal_review_runs(run_id)
);

CREATE INDEX IF NOT EXISTS signal_review_runs_latest
ON signal_review_runs(signal_id, started_at_ms DESC);

CREATE TABLE IF NOT EXISTS signal_review_items (
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    rank INTEGER NOT NULL CHECK (rank > 0),
    instrument_id INTEGER NOT NULL,
    profile TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK (change_type IN ('added', 'retained', 'removed')),
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    score REAL NOT NULL,
    confidence REAL NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, item_id),
    UNIQUE (run_id, item_key),
    FOREIGN KEY (run_id) REFERENCES signal_review_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS signal_review_items_rank
ON signal_review_items(run_id, profile, active DESC, rank);

CREATE TABLE IF NOT EXISTS signal_review_evidence (
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    source_run_id TEXT,
    source_item_id TEXT,
    payload_json TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position > 0),
    PRIMARY KEY (run_id, item_id, evidence_id),
    UNIQUE (run_id, item_id, alias),
    FOREIGN KEY (run_id, item_id) REFERENCES signal_review_items(run_id, item_id)
        ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS generated_analysis_targets (
    target_id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL,
    system_id TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (instrument_id, system_id, timeframe),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);

CREATE TABLE IF NOT EXISTS generated_analysis_dirty_targets (
    target_id INTEGER PRIMARY KEY,
    dirty_from INTEGER NOT NULL,
    dirty_through INTEGER NOT NULL,
    reason TEXT NOT NULL,
    generation INTEGER NOT NULL,
    queued_at_ms INTEGER NOT NULL,
    claimed_at_ms INTEGER,
    lease_until_ms INTEGER,
    last_error TEXT,
    FOREIGN KEY (target_id) REFERENCES generated_analysis_targets(target_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trend_review_cases (
    review_id TEXT PRIMARY KEY,
    instrument_id INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    timeframe TEXT NOT NULL CHECK (timeframe = 'daily'),
    horizon TEXT NOT NULL CHECK (horizon IN ('short', 'long')),
    interval_start INTEGER NOT NULL,
    interval_end INTEGER NOT NULL,
    as_of_date INTEGER NOT NULL,
    input_digest BLOB NOT NULL,
    algorithm_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    classification TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK (
        review_status IN ('proposed', 'ambiguous', 'confirmed', 'rejected')
    ),
    tags_json TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);

CREATE INDEX IF NOT EXISTS trend_review_case_lookup
ON trend_review_cases(instrument_id, as_of_date DESC, updated_at_ms DESC);

CREATE INDEX IF NOT EXISTS trend_review_case_status
ON trend_review_cases(review_status, updated_at_ms DESC);

CREATE INDEX IF NOT EXISTS generated_analysis_dirty_claim
ON generated_analysis_dirty_targets(lease_until_ms, queued_at_ms, target_id);

CREATE TRIGGER IF NOT EXISTS daily_bar_queues_generated_analysis
AFTER INSERT ON daily_bars
BEGIN
    INSERT INTO generated_analysis_dirty_targets(
        target_id, dirty_from, dirty_through, reason, generation, queued_at_ms
    )
    SELECT target_id, NEW.trade_date, NEW.trade_date, 'canonical_bar_changed', 1,
           NEW.updated_at_ms
    FROM generated_analysis_targets
    WHERE instrument_id = NEW.instrument_id AND enabled = 1
    ON CONFLICT(target_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        dirty_through = max(dirty_through, excluded.dirty_through),
        reason = excluded.reason,
        generation = generation + 1,
        queued_at_ms = excluded.queued_at_ms,
        claimed_at_ms = NULL,
        lease_until_ms = NULL;
END;

CREATE TRIGGER IF NOT EXISTS daily_bar_correction_queues_generated_analysis
AFTER UPDATE OF open, high, low, close, volume, source_id ON daily_bars
BEGIN
    INSERT INTO generated_analysis_dirty_targets(
        target_id, dirty_from, dirty_through, reason, generation, queued_at_ms
    )
    SELECT target_id, NEW.trade_date, NEW.trade_date, 'canonical_bar_corrected', 1,
           NEW.updated_at_ms
    FROM generated_analysis_targets
    WHERE instrument_id = NEW.instrument_id AND enabled = 1
    ON CONFLICT(target_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        dirty_through = max(dirty_through, excluded.dirty_through),
        reason = excluded.reason,
        generation = generation + 1,
        queued_at_ms = excluded.queued_at_ms,
        claimed_at_ms = NULL,
        lease_until_ms = NULL;
END;

CREATE TRIGGER IF NOT EXISTS custom_index_bar_queues_generated_analysis
AFTER INSERT ON custom_index_daily_bars
BEGIN
    INSERT INTO generated_analysis_dirty_targets(
        target_id, dirty_from, dirty_through, reason, generation, queued_at_ms
    )
    SELECT target.target_id, NEW.trade_date, NEW.trade_date,
           'custom_index_bar_changed', 1, NEW.updated_at_ms
    FROM custom_indices AS custom
    JOIN generated_analysis_targets AS target
      ON target.instrument_id = custom.instrument_id
    WHERE custom.index_id = NEW.index_id AND target.enabled = 1
    ON CONFLICT(target_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        dirty_through = max(dirty_through, excluded.dirty_through),
        reason = excluded.reason,
        generation = generation + 1,
        queued_at_ms = excluded.queued_at_ms,
        claimed_at_ms = NULL,
        lease_until_ms = NULL;
END;

CREATE TABLE IF NOT EXISTS intraday_daily_bars (
    symbol TEXT NOT NULL,
    trade_date INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    amount REAL NOT NULL,
    previous_close REAL NOT NULL,
    change_percent REAL NOT NULL,
    source TEXT NOT NULL,
    provider_time TEXT NOT NULL,
    received_at TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (symbol, trade_date)
) WITHOUT ROWID;

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

FUTURES_SCHEMA_VERSION = 3

FUTURES_SCHEMA = """
CREATE TABLE IF NOT EXISTS futures_products (
    instrument_id INTEGER PRIMARY KEY,
    product_code TEXT NOT NULL,
    exchange TEXT NOT NULL,
    multiplier REAL,
    per_unit REAL,
    trading_unit TEXT NOT NULL,
    quote_unit TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (exchange, product_code),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE TABLE IF NOT EXISTS futures_contracts (
    instrument_id INTEGER PRIMARY KEY,
    product_instrument_id INTEGER NOT NULL,
    provider_symbol TEXT NOT NULL,
    contract_month TEXT NOT NULL,
    listed_on INTEGER NOT NULL,
    last_trading_date INTEGER NOT NULL,
    delivery_date INTEGER,
    multiplier REAL,
    per_unit REAL,
    trading_unit TEXT NOT NULL,
    quote_unit TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK (
        lifecycle_status IN ('pending', 'listed', 'trading', 'expired', 'delivered', 'delisted')
    ),
    source_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (source_id, provider_symbol),
    UNIQUE (product_instrument_id, contract_month),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (product_instrument_id) REFERENCES futures_products(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE INDEX IF NOT EXISTS futures_contracts_lifecycle
ON futures_contracts(lifecycle_status, last_trading_date, product_instrument_id);

CREATE TABLE IF NOT EXISTS futures_continuous_series (
    instrument_id INTEGER PRIMARY KEY,
    product_instrument_id INTEGER NOT NULL,
    provider_symbol TEXT NOT NULL,
    series_kind TEXT NOT NULL CHECK (series_kind IN ('main', 'continuous')),
    series_variant TEXT NOT NULL,
    price_basis TEXT NOT NULL CHECK (
        price_basis IN ('raw', 'backward-ratio', 'backward-additive')
    ),
    rule_version TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (source_id, provider_symbol, price_basis),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (product_instrument_id) REFERENCES futures_products(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE TABLE IF NOT EXISTS futures_daily_bars (
    instrument_id INTEGER NOT NULL,
    trading_day INTEGER NOT NULL,
    provider_date INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    previous_close REAL,
    settlement REAL,
    previous_settlement REAL,
    volume_contracts INTEGER NOT NULL,
    amount_cny REAL,
    open_interest_contracts REAL,
    open_interest_change_contracts REAL,
    delivery_settlement REAL,
    mapped_contract_instrument_id INTEGER,
    roll_event INTEGER NOT NULL DEFAULT 0 CHECK (roll_event IN (0, 1)),
    source_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, trading_day),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (mapped_contract_instrument_id) REFERENCES futures_contracts(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS futures_daily_bars_date
ON futures_daily_bars(trading_day, instrument_id);

CREATE TRIGGER IF NOT EXISTS futures_bar_queues_generated_analysis
AFTER INSERT ON futures_daily_bars
BEGIN
    INSERT INTO generated_analysis_dirty_targets(
        target_id, dirty_from, dirty_through, reason, generation, queued_at_ms
    )
    SELECT target_id, NEW.trading_day, NEW.trading_day,
           'futures_canonical_bar_changed', 1, NEW.updated_at_ms
    FROM generated_analysis_targets
    WHERE instrument_id = NEW.instrument_id AND enabled = 1
    ON CONFLICT(target_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        dirty_through = max(dirty_through, excluded.dirty_through),
        reason = excluded.reason,
        generation = generation + 1,
        queued_at_ms = excluded.queued_at_ms,
        claimed_at_ms = NULL,
        lease_until_ms = NULL;
END;

CREATE TRIGGER IF NOT EXISTS futures_bar_correction_queues_generated_analysis
AFTER UPDATE ON futures_daily_bars
WHEN OLD.provider_date IS NOT NEW.provider_date
  OR OLD.open IS NOT NEW.open OR OLD.high IS NOT NEW.high
  OR OLD.low IS NOT NEW.low OR OLD.close IS NOT NEW.close
  OR OLD.previous_close IS NOT NEW.previous_close
  OR OLD.settlement IS NOT NEW.settlement
  OR OLD.previous_settlement IS NOT NEW.previous_settlement
  OR OLD.volume_contracts IS NOT NEW.volume_contracts
  OR OLD.amount_cny IS NOT NEW.amount_cny
  OR OLD.open_interest_contracts IS NOT NEW.open_interest_contracts
  OR OLD.open_interest_change_contracts IS NOT NEW.open_interest_change_contracts
  OR OLD.delivery_settlement IS NOT NEW.delivery_settlement
  OR OLD.mapped_contract_instrument_id IS NOT NEW.mapped_contract_instrument_id
  OR OLD.roll_event IS NOT NEW.roll_event
  OR OLD.source_id IS NOT NEW.source_id
BEGIN
    INSERT INTO generated_analysis_dirty_targets(
        target_id, dirty_from, dirty_through, reason, generation, queued_at_ms
    )
    SELECT target_id, NEW.trading_day, NEW.trading_day,
           'futures_canonical_bar_corrected', 1, NEW.updated_at_ms
    FROM generated_analysis_targets
    WHERE instrument_id = NEW.instrument_id AND enabled = 1
    ON CONFLICT(target_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        dirty_through = max(dirty_through, excluded.dirty_through),
        reason = excluded.reason,
        generation = generation + 1,
        queued_at_ms = excluded.queued_at_ms,
        claimed_at_ms = NULL,
        lease_until_ms = NULL;
END;

CREATE TABLE IF NOT EXISTS futures_exchange_calendar (
    source_id INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    calendar_date INTEGER NOT NULL,
    is_open INTEGER NOT NULL CHECK (is_open IN (0, 1)),
    previous_trading_day INTEGER,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, exchange, calendar_date),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS futures_roll_mappings (
    source_id INTEGER NOT NULL,
    series_instrument_id INTEGER NOT NULL,
    effective_from INTEGER NOT NULL,
    contract_instrument_id INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, series_instrument_id, effective_from),
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (series_instrument_id) REFERENCES futures_continuous_series(instrument_id),
    FOREIGN KEY (contract_instrument_id) REFERENCES futures_contracts(instrument_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS futures_roll_mapping_contract
ON futures_roll_mappings(contract_instrument_id, effective_from);

CREATE INDEX IF NOT EXISTS futures_roll_mapping_series_date
ON futures_roll_mappings(series_instrument_id, effective_from);

CREATE TABLE IF NOT EXISTS futures_provisional_daily_bars (
    instrument_id INTEGER NOT NULL,
    trading_day INTEGER NOT NULL,
    provider_date INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    previous_close REAL,
    previous_settlement REAL,
    volume_contracts INTEGER NOT NULL,
    open_interest_contracts REAL,
    source_id INTEGER NOT NULL,
    provider_time TEXT NOT NULL,
    received_at TEXT NOT NULL,
    takeover_state TEXT NOT NULL CHECK (
        takeover_state IN ('active', 'canonical-taken-over')
    ),
    stale INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, trading_day, source_id),
    FOREIGN KEY (instrument_id) REFERENCES futures_contracts(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS futures_sync_states (
    source_id INTEGER NOT NULL,
    dataset TEXT NOT NULL,
    scope TEXT NOT NULL,
    identity TEXT NOT NULL,
    covered_from INTEGER NOT NULL,
    covered_through INTEGER NOT NULL,
    last_batch_rows INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, dataset, scope, identity),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS futures_sync_states_coverage
ON futures_sync_states(dataset, scope, covered_through, identity);

CREATE TABLE IF NOT EXISTS futures_update_receipts (
    source_id INTEGER NOT NULL,
    dataset TEXT NOT NULL,
    scope TEXT NOT NULL,
    effective_date INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    payload_hash BLOB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'empty', 'partial', 'rejected')),
    message TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, dataset, scope, effective_date),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS futures_continuous_builds (
    series_instrument_id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    price_basis TEXT NOT NULL CHECK (
        price_basis IN ('raw', 'backward-ratio', 'backward-additive')
    ),
    rule_version TEXT NOT NULL,
    input_digest BLOB NOT NULL,
    rebuilt_from INTEGER,
    rebuilt_through INTEGER,
    row_count INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'partial', 'failed')),
    warnings_json TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (series_instrument_id) REFERENCES futures_continuous_series(instrument_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS futures_continuous_roll_events (
    series_instrument_id INTEGER NOT NULL,
    effective_from INTEGER NOT NULL,
    first_output_day INTEGER NOT NULL,
    outgoing_contract_instrument_id INTEGER NOT NULL,
    incoming_contract_instrument_id INTEGER NOT NULL,
    outgoing_close REAL,
    incoming_close REAL,
    adjustment_factor REAL,
    adjustment_offset REAL,
    rule_version TEXT NOT NULL,
    input_digest BLOB NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (series_instrument_id, effective_from),
    FOREIGN KEY (series_instrument_id) REFERENCES futures_continuous_series(instrument_id),
    FOREIGN KEY (outgoing_contract_instrument_id) REFERENCES futures_contracts(instrument_id),
    FOREIGN KEY (incoming_contract_instrument_id) REFERENCES futures_contracts(instrument_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS futures_continuous_dirty_series (
    series_instrument_id INTEGER PRIMARY KEY,
    dirty_from INTEGER NOT NULL,
    reason TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (series_instrument_id) REFERENCES futures_continuous_series(instrument_id)
) WITHOUT ROWID;

CREATE TRIGGER IF NOT EXISTS futures_continuous_dirty_after_contract_insert
AFTER INSERT ON futures_daily_bars
BEGIN
    INSERT INTO futures_continuous_dirty_series(
        series_instrument_id, dirty_from, reason, updated_at_ms
    )
    SELECT mapping.series_instrument_id, NEW.trading_day,
           'mapped-contract-bar-inserted', NEW.updated_at_ms
    FROM futures_roll_mappings AS mapping
    WHERE mapping.contract_instrument_id = NEW.instrument_id
      AND mapping.effective_from = (
          SELECT max(active.effective_from)
          FROM futures_roll_mappings AS active
          WHERE active.source_id = mapping.source_id
            AND active.series_instrument_id = mapping.series_instrument_id
            AND active.effective_from <= NEW.trading_day
      )
    ON CONFLICT(series_instrument_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        reason = excluded.reason,
        updated_at_ms = excluded.updated_at_ms;
END;

CREATE TRIGGER IF NOT EXISTS futures_continuous_dirty_after_contract_update
AFTER UPDATE ON futures_daily_bars
WHEN OLD.open IS NOT NEW.open OR OLD.high IS NOT NEW.high
  OR OLD.low IS NOT NEW.low OR OLD.close IS NOT NEW.close
  OR OLD.provider_date IS NOT NEW.provider_date
  OR OLD.previous_close IS NOT NEW.previous_close
  OR OLD.settlement IS NOT NEW.settlement
  OR OLD.previous_settlement IS NOT NEW.previous_settlement
  OR OLD.volume_contracts IS NOT NEW.volume_contracts
  OR OLD.amount_cny IS NOT NEW.amount_cny
  OR OLD.open_interest_contracts IS NOT NEW.open_interest_contracts
  OR OLD.open_interest_change_contracts IS NOT NEW.open_interest_change_contracts
  OR OLD.delivery_settlement IS NOT NEW.delivery_settlement
BEGIN
    INSERT INTO futures_continuous_dirty_series(
        series_instrument_id, dirty_from, reason, updated_at_ms
    )
    SELECT mapping.series_instrument_id, NEW.trading_day,
           'mapped-contract-bar-corrected', NEW.updated_at_ms
    FROM futures_roll_mappings AS mapping
    WHERE mapping.contract_instrument_id = NEW.instrument_id
      AND mapping.effective_from = (
          SELECT max(active.effective_from)
          FROM futures_roll_mappings AS active
          WHERE active.source_id = mapping.source_id
            AND active.series_instrument_id = mapping.series_instrument_id
            AND active.effective_from <= NEW.trading_day
      )
    ON CONFLICT(series_instrument_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        reason = excluded.reason,
        updated_at_ms = excluded.updated_at_ms;
END;

CREATE TRIGGER IF NOT EXISTS futures_continuous_dirty_after_mapping_insert
AFTER INSERT ON futures_roll_mappings
BEGIN
    INSERT INTO futures_continuous_dirty_series(
        series_instrument_id, dirty_from, reason, updated_at_ms
    ) VALUES (
        NEW.series_instrument_id, NEW.effective_from,
        'roll-mapping-inserted', NEW.updated_at_ms
    )
    ON CONFLICT(series_instrument_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        reason = excluded.reason,
        updated_at_ms = excluded.updated_at_ms;
END;

CREATE TRIGGER IF NOT EXISTS futures_continuous_dirty_after_mapping_update
AFTER UPDATE OF contract_instrument_id ON futures_roll_mappings
WHEN OLD.contract_instrument_id IS NOT NEW.contract_instrument_id
BEGIN
    INSERT INTO futures_continuous_dirty_series(
        series_instrument_id, dirty_from, reason, updated_at_ms
    ) VALUES (
        NEW.series_instrument_id, NEW.effective_from,
        'roll-mapping-corrected', NEW.updated_at_ms
    )
    ON CONFLICT(series_instrument_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        reason = excluded.reason,
        updated_at_ms = excluded.updated_at_ms;
END;

CREATE TRIGGER IF NOT EXISTS futures_continuous_dirty_after_mapping_delete
AFTER DELETE ON futures_roll_mappings
BEGIN
    INSERT INTO futures_continuous_dirty_series(
        series_instrument_id, dirty_from, reason, updated_at_ms
    ) VALUES (
        OLD.series_instrument_id, OLD.effective_from,
        'roll-mapping-removed', OLD.updated_at_ms
    )
    ON CONFLICT(series_instrument_id) DO UPDATE SET
        dirty_from = min(dirty_from, excluded.dirty_from),
        reason = excluded.reason,
        updated_at_ms = excluded.updated_at_ms;
END;

CREATE TABLE IF NOT EXISTS futures_schema_metadata (
    schema_version INTEGER PRIMARY KEY,
    state TEXT NOT NULL CHECK (state = 'ready'),
    applied_at_ms INTEGER NOT NULL
);
"""
