"""SQLite hot store optimized for daily batch updates and symbol-range reads."""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
import json
from contextlib import AbstractContextManager
from hashlib import blake2b
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from stock_harness.models import (
    BoardMembership,
    CatalogEntry,
    CoverageGap,
    DailyBar,
    Instrument,
    InstrumentCoverage,
    InstrumentKind,
    EtfHolding,
    MarketSnapshot,
    ProviderIncident,
    RepairJob,
    StoredDailyBar,
    SymbolSyncState,
    ValidationResult,
    WriteStats,
)
from stock_harness.search_terms import matches_name_or_pinyin, pinyin_search_aliases


_SCHEMA = """
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


class SQLiteMarketDataStore:
    """Owns one local SQLite connection and serializes its short write transactions."""

    def __init__(
        self,
        path: Path | str,
        *,
        cache_size_kib: int = 32_768,
        mmap_size_mib: int = 256,
        temp_store: str = "MEMORY",
        busy_timeout_ms: int = 120_000,
    ) -> None:
        if cache_size_kib <= 0:
            raise ValueError("cache_size_kib must be positive")
        if mmap_size_mib < 0:
            raise ValueError("mmap_size_mib must be non-negative")
        temp_store = temp_store.upper()
        if temp_store not in {"MEMORY", "FILE"}:
            raise ValueError("temp_store must be MEMORY or FILE")
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        path_text = str(path)
        if path_text != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.cache_size_kib = cache_size_kib
        self.mmap_size_mib = mmap_size_mib
        self.temp_store = temp_store
        self.busy_timeout_ms = busy_timeout_ms
        self._writer_lock: AbstractContextManager[None]
        if path_text == ":memory:":
            self._writer_lock = _ThreadOnlyWriterLock()
        else:
            self._writer_lock = _InterprocessWriterLock(
                Path(path_text + ".writer.lock"), busy_timeout_ms / 1000
            )
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            path_text,
            isolation_level=None,
            check_same_thread=False,
            timeout=busy_timeout_ms / 1000,
        )
        self._connection.row_factory = sqlite3.Row
        self._configure()
        with self._writer_lock:
            self._connection.executescript(_SCHEMA)
        self._backfill_pinyin_aliases()

    def __enter__(self) -> SQLiteMarketDataStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def checkpoint(self, mode: str = "PASSIVE") -> tuple[int, int, int]:
        mode = mode.upper()
        if mode not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            raise ValueError(f"unsupported checkpoint mode: {mode}")
        with self._lock, self._writer_lock:
            row = self._connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return int(row[0]), int(row[1]), int(row[2])

    def upsert_trading_dates(self, source: str, trading_dates: Sequence[date]) -> int:
        dates = sorted(set(trading_dates))
        if not dates:
            return 0
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            self._connection.executemany(
                """
                INSERT INTO trading_calendar(source_id, trade_date, updated_at_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(source_id, trade_date) DO UPDATE SET
                    updated_at_ms = excluded.updated_at_ms
                """,
                ((source_id, _date_key(item), now_ms) for item in dates),
            )
        return len(dates)

    def list_trading_dates(
        self, source: str, start_date: date, end_date: date
    ) -> list[date]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT calendar.trade_date
                FROM trading_calendar AS calendar
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND calendar.trade_date BETWEEN ? AND ?
                ORDER BY calendar.trade_date
                """,
                (source, _date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return [_date_from_key(int(row[0])) for row in rows]

    def _configure(self) -> None:
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        self._connection.execute(f"PRAGMA temp_store={self.temp_store}")
        self._connection.execute(f"PRAGMA cache_size=-{self.cache_size_kib}")
        self._connection.execute(f"PRAGMA mmap_size={self.mmap_size_mib * 1024 * 1024}")

    def upsert_instruments(self, instruments: Sequence[Instrument]) -> int:
        rows = [
            (item.symbol, item.name, item.kind.value, item.exchange, int(item.active))
            for item in instruments
        ]
        if not rows:
            return 0
        with self._lock, self._transaction():
            existing_names = self._instrument_names({item.symbol for item in instruments})
            self._connection.executemany(
                """
                INSERT INTO instruments(symbol, name, kind, exchange, active)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    exchange = excluded.exchange,
                    active = excluded.active
                WHERE name IS NOT excluded.name
                   OR kind IS NOT excluded.kind
                   OR exchange IS NOT excluded.exchange
                   OR active IS NOT excluded.active
                """,
                rows,
            )
            changed = [item for item in instruments if existing_names.get(item.symbol) != item.name]
            self._replace_pinyin_aliases_locked(changed)
        return len(rows)

    def upsert_catalog_entries(self, entries: Sequence[CatalogEntry]) -> int:
        if not entries:
            return 0
        self.upsert_instruments([entry.instrument for entry in entries])
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids(
                {entry.instrument.symbol for entry in entries}
            )
            for entry in entries:
                source_id = self._source_id(
                    entry.catalog_source,
                    acquired_via="tushare" if entry.catalog_source.startswith("tushare") else entry.catalog_source,
                    source_system=entry.source_system,
                )
                instrument_id = instrument_ids[entry.instrument.symbol]
                self._connection.execute(
                    """
                    INSERT INTO instrument_catalog_entries(
                        catalog_source_id, provider_symbol, instrument_id,
                        source_system, family, category, observed_on, listed_on,
                        delisted_on, constituent_count, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(catalog_source_id, provider_symbol) DO UPDATE SET
                        instrument_id = excluded.instrument_id,
                        source_system = excluded.source_system,
                        family = excluded.family,
                        category = excluded.category,
                        observed_on = excluded.observed_on,
                        listed_on = excluded.listed_on,
                        delisted_on = excluded.delisted_on,
                        constituent_count = excluded.constituent_count,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        source_id, entry.provider_symbol, instrument_id,
                        entry.source_system, entry.family, entry.category,
                        _date_key(entry.observed_on),
                        _date_key(entry.listed_on) if entry.listed_on else None,
                        _date_key(entry.delisted_on) if entry.delisted_on else None,
                        entry.constituent_count, now_ms,
                    ),
                )
                aliases = {entry.instrument.name, *entry.aliases}
                search_aliases = [
                    (alias.strip(), "display_name")
                    for alias in aliases if alias.strip()
                ]
                for alias in aliases:
                    search_aliases.extend(pinyin_search_aliases(alias))
                self._connection.executemany(
                    """
                    INSERT INTO instrument_aliases(
                        catalog_source_id, instrument_id, alias, alias_type, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(catalog_source_id, instrument_id, alias) DO UPDATE SET
                        alias_type = excluded.alias_type,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        (source_id, instrument_id, alias, alias_type, now_ms)
                        for alias, alias_type in search_aliases
                    ),
                )
        return len(entries)

    def replace_board_memberships(
        self,
        source: str,
        board_symbol: str,
        observed_on: date,
        memberships: Sequence[BoardMembership],
    ) -> int:
        for membership in memberships:
            if membership.source != source or membership.board_symbol != board_symbol:
                raise ValueError("board memberships must share source and board symbol")
            if membership.observed_on != observed_on:
                raise ValueError("board memberships must share the observation date")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            instrument_ids = self._instrument_ids({board_symbol})
            if board_symbol not in instrument_ids:
                raise ValueError(f"unknown board instrument: {board_symbol}")
            board_id = instrument_ids[board_symbol]
            self._connection.execute(
                """
                UPDATE board_memberships SET active = 0, updated_at_ms = ?
                WHERE source_id = ? AND board_instrument_id = ? AND active = 1
                """,
                (now_ms, source_id, board_id),
            )
            self._connection.executemany(
                """
                INSERT INTO board_memberships(
                    source_id, board_instrument_id, member_symbol, member_name,
                    active, first_seen_on, last_seen_on, updated_at_ms
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(source_id, board_instrument_id, member_symbol) DO UPDATE SET
                    member_name = excluded.member_name,
                    active = 1,
                    last_seen_on = excluded.last_seen_on,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    (
                        source_id, board_id, item.member_symbol, item.member_name,
                        _date_key(observed_on), _date_key(observed_on), now_ms,
                    )
                    for item in memberships
                ),
            )
        return len(memberships)

    def derive_market_snapshots(self, trade_date: date) -> int:
        """Materialize latest-day changes from canonical bars without extra Provider calls."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        trade_key = _date_key(trade_date)
        with self._lock, self._transaction():
            cursor = self._connection.execute(
                """
                INSERT INTO market_snapshots(
                    instrument_id, trade_date, change_percent, total_market_cap,
                    source_id, updated_at_ms
                )
                SELECT current.instrument_id, current.trade_date,
                       (current.close / previous.close - 1.0) * 100.0,
                       NULL, current.source_id, ?
                FROM daily_bars AS current
                JOIN daily_bars AS previous
                  ON previous.instrument_id = current.instrument_id
                 AND previous.trade_date = (
                     SELECT max(candidate.trade_date)
                     FROM daily_bars AS candidate
                     WHERE candidate.instrument_id = current.instrument_id
                       AND candidate.trade_date < current.trade_date
                 )
                WHERE current.trade_date = ? AND previous.close <> 0
                ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                    change_percent = excluded.change_percent,
                    source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (now_ms, trade_key),
            )
        return max(0, cursor.rowcount)

    def upsert_market_snapshots(
        self, source: str, snapshots: Sequence[MarketSnapshot]
    ) -> int:
        if not snapshots:
            return 0
        symbols = {item.symbol for item in snapshots}
        if len(symbols) != len(snapshots):
            raise ValueError("market snapshots must contain unique symbols")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids(symbols)
            missing = symbols - instrument_ids.keys()
            if missing:
                raise ValueError(f"unknown instruments: {', '.join(sorted(missing))}")
            source_id = self._source_id(source)
            self._connection.executemany(
                """
                INSERT INTO market_snapshots(
                    instrument_id, trade_date, change_percent, total_market_cap,
                    source_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                    change_percent = excluded.change_percent,
                    total_market_cap = excluded.total_market_cap,
                    source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    (
                        instrument_ids[item.symbol], _date_key(item.trade_date),
                        item.change_percent, item.total_market_cap, source_id, now_ms,
                    )
                    for item in snapshots
                ),
            )
        return len(snapshots)

    def list_market_snapshots(self, symbols: Sequence[str]) -> list[dict[str, object]]:
        ordered = list(dict.fromkeys(item.upper() for item in symbols if item))
        if len(ordered) > 500:
            raise ValueError("market snapshot query exceeds 500 symbols")
        if not ordered:
            return []
        rows: list[sqlite3.Row] = []
        with self._lock:
            for offset in range(0, len(ordered), 400):
                chunk = ordered[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(self._connection.execute(
                    f"""
                    SELECT instrument.symbol, instrument.name, instrument.kind,
                           instrument.exchange, snapshot.trade_date,
                           snapshot.change_percent, snapshot.total_market_cap,
                           source.code, snapshot.updated_at_ms
                    FROM instruments AS instrument
                    JOIN market_snapshots AS snapshot USING (instrument_id)
                    JOIN sources AS source USING (source_id)
                    WHERE instrument.symbol IN ({placeholders})
                      AND snapshot.trade_date = (
                          SELECT max(latest.trade_date)
                          FROM market_snapshots AS latest
                          WHERE latest.instrument_id = instrument.instrument_id
                      )
                    """,
                    chunk,
                ).fetchall())
        by_symbol = {
            str(row[0]): {
                "symbol": str(row[0]), "name": str(row[1]),
                "kind": str(row[2]), "exchange": str(row[3]),
                "trade_date": _date_from_key(int(row[4])),
                "change_percent": float(row[5]),
                "total_market_cap": float(row[6]) if row[6] is not None else None,
                "source": str(row[7]), "updated_at_ms": int(row[8]),
            }
            for row in rows
        }
        return [by_symbol[symbol] for symbol in ordered if symbol in by_symbol]

    def replace_etf_holdings(
        self,
        source: str,
        etf_symbol: str,
        as_of_date: date,
        holdings: Sequence[EtfHolding],
    ) -> int:
        for item in holdings:
            if item.etf_symbol != etf_symbol or item.as_of_date != as_of_date:
                raise ValueError("ETF holdings must share ETF symbol and as-of date")
        if len({item.holding_symbol for item in holdings}) != len(holdings):
            raise ValueError("ETF holdings must contain unique symbols")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            etf_row = self._connection.execute(
                "SELECT instrument_id, kind FROM instruments WHERE symbol = ?", (etf_symbol,)
            ).fetchone()
            if etf_row is None or str(etf_row[1]) != InstrumentKind.ETF.value:
                raise ValueError(f"unknown ETF instrument: {etf_symbol}")
            source_id = self._source_id(source)
            etf_id = int(etf_row[0])
            as_of_key = _date_key(as_of_date)
            self._connection.execute(
                "DELETE FROM etf_holdings WHERE source_id = ? AND etf_instrument_id = ? AND as_of_date = ?",
                (source_id, etf_id, as_of_key),
            )
            self._connection.executemany(
                """
                INSERT INTO etf_holdings(
                    source_id, etf_instrument_id, as_of_date, holding_symbol,
                    holding_name, quantity, weight_percent, market_value,
                    holding_rank, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        source_id, etf_id, as_of_key, item.holding_symbol,
                        item.holding_name, item.quantity, item.weight_percent,
                        item.market_value, item.rank, now_ms,
                    )
                    for item in holdings
                ),
            )
        return len(holdings)

    def record_etf_holding_receipt(
        self,
        source: str,
        etf_symbol: str,
        requested_date: date,
        as_of_date: date | None,
        row_count: int,
    ) -> None:
        if row_count < 0:
            raise ValueError("ETF holding receipt row count must be non-negative")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            row = self._connection.execute(
                "SELECT instrument_id, kind FROM instruments WHERE symbol = ?", (etf_symbol,)
            ).fetchone()
            if row is None or str(row[1]) != InstrumentKind.ETF.value:
                raise ValueError(f"unknown ETF instrument: {etf_symbol}")
            source_id = self._source_id(source)
            self._connection.execute(
                """
                INSERT INTO etf_holding_receipts(
                    source_id, etf_instrument_id, requested_date, as_of_date,
                    row_count, status, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, etf_instrument_id, requested_date) DO UPDATE SET
                    as_of_date = excluded.as_of_date,
                    row_count = excluded.row_count,
                    status = excluded.status,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    source_id, int(row[0]), _date_key(requested_date),
                    _date_key(as_of_date) if as_of_date else None, row_count,
                    "complete" if row_count else "empty", now_ms,
                ),
            )

    def list_etfs_needing_holding_refresh(
        self,
        source: str,
        requested_date: date,
        limit: int,
        preferred_symbols: Sequence[str] = (),
    ) -> list[str]:
        if limit <= 0:
            return []
        preferred = list(dict.fromkeys(item.upper() for item in preferred_symbols))
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            if preferred:
                placeholders = ",".join("?" for _ in preferred)
                priority = f"CASE WHEN instrument.symbol IN ({placeholders}) THEN 0 ELSE 1 END"
                params: list[object] = [source_id, _date_key(requested_date), *preferred, limit]
            else:
                priority = "1"
                params = [source_id, _date_key(requested_date), limit]
            rows = self._connection.execute(
                f"""
                SELECT instrument.symbol
                FROM instruments AS instrument
                LEFT JOIN etf_holding_receipts AS receipt
                  ON receipt.etf_instrument_id = instrument.instrument_id
                 AND receipt.source_id = ? AND receipt.requested_date = ?
                WHERE instrument.kind = 'etf' AND instrument.active = 1
                  AND receipt.etf_instrument_id IS NULL
                ORDER BY {priority}, instrument.symbol
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [str(row[0]) for row in rows]

    def list_etf_holdings(
        self, etf_symbol: str, limit: int = 500, offset: int = 0
    ) -> dict[str, object] | None:
        if not 1 <= limit <= 5000 or offset < 0:
            raise ValueError("invalid ETF holding pagination")
        with self._lock:
            meta = self._connection.execute(
                """
                SELECT holding.as_of_date, source.code, count(*)
                FROM etf_holdings AS holding
                JOIN instruments AS etf ON etf.instrument_id = holding.etf_instrument_id
                JOIN sources AS source USING (source_id)
                WHERE etf.symbol = ?
                GROUP BY holding.as_of_date, source.code
                ORDER BY holding.as_of_date DESC,
                         CASE source.code WHEN 'tushare_etf_pcf' THEN 0 ELSE 1 END,
                         source.code
                LIMIT 1
                """,
                (etf_symbol,),
            ).fetchone()
            if meta is None:
                return None
            rows = self._connection.execute(
                """
                SELECT holding.holding_symbol, holding.holding_name,
                       holding.quantity, holding.weight_percent,
                       holding.market_value, holding.holding_rank,
                       instrument.kind, instrument.exchange, instrument.active
                FROM etf_holdings AS holding
                JOIN instruments AS etf ON etf.instrument_id = holding.etf_instrument_id
                JOIN sources AS source USING (source_id)
                LEFT JOIN instruments AS instrument
                  ON instrument.symbol = holding.holding_symbol
                WHERE etf.symbol = ? AND holding.as_of_date = ? AND source.code = ?
                ORDER BY coalesce(holding.holding_rank, 2147483647), holding.holding_symbol
                LIMIT ? OFFSET ?
                """,
                (etf_symbol, int(meta[0]), str(meta[1]), limit, offset),
            ).fetchall()
        return {
            "symbol": etf_symbol,
            "as_of_date": _date_from_key(int(meta[0])),
            "source": str(meta[1]),
            "total": int(meta[2]),
            "items": [
                {
                    "symbol": str(row[0]), "name": str(row[1]),
                    "quantity": float(row[2]) if row[2] is not None else None,
                    "weight_percent": float(row[3]) if row[3] is not None else None,
                    "market_value": float(row[4]) if row[4] is not None else None,
                    "rank": int(row[5]) if row[5] is not None else None,
                    "kind": str(row[6]) if row[6] is not None else None,
                    "exchange": str(row[7]) if row[7] is not None else None,
                    "available": row[6] is not None and bool(row[8]),
                }
                for row in rows
            ],
        }

    def ensure_instruments(self, instruments: Sequence[Instrument]) -> int:
        rows = [
            (item.symbol, item.name, item.kind.value, item.exchange, int(item.active))
            for item in instruments
        ]
        if not rows:
            return 0
        with self._lock, self._transaction():
            before = self._connection.total_changes
            self._connection.executemany(
                """
                INSERT OR IGNORE INTO instruments(symbol, name, kind, exchange, active)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            return self._connection.total_changes - before

    def upsert_daily_bars(self, source: str, bars: Sequence[DailyBar]) -> WriteStats:
        started = time.perf_counter()
        if not bars:
            return WriteStats(0, 0, 0, (time.perf_counter() - started) * 1000)
        for bar in bars:
            bar.validate()

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            changed = self._upsert_daily_bars_locked(source, bars, now_ms)

        elapsed_ms = (time.perf_counter() - started) * 1000
        return WriteStats(len(bars), changed, len(bars) - changed, elapsed_ms)

    def apply_volume_scale_migration(
        self, migration_id: str, source: str, multiplier: int
    ) -> int:
        if not migration_id:
            raise ValueError("migration_id is required")
        if multiplier <= 0:
            raise ValueError("volume multiplier must be positive")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            existing = self._connection.execute(
                "SELECT affected_rows FROM data_migrations WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            if existing is not None:
                return int(existing[0])
            source_id = self._source_id(source)
            before = self._connection.total_changes
            self._connection.execute(
                """
                UPDATE daily_bars
                SET volume = volume * ?, updated_at_ms = ?
                WHERE source_id = ?
                """,
                (multiplier, now_ms, source_id),
            )
            affected = self._connection.total_changes - before
            self._connection.execute(
                """
                INSERT INTO data_migrations(
                    migration_id, applied_at_ms, affected_rows, details
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    migration_id,
                    now_ms,
                    affected,
                    f"scaled {source} daily volume by {multiplier}",
                ),
            )
        return affected

    def apply_catalog_name_exclusion_migration(
        self,
        migration_id: str,
        source: str,
        family: str,
        excluded_name_fragment: str,
    ) -> int:
        if not migration_id or not excluded_name_fragment:
            raise ValueError("migration ID and excluded name fragment are required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            existing = self._connection.execute(
                "SELECT affected_rows FROM data_migrations WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            if existing is not None:
                return int(existing[0])
            source_id = self._source_id(source)
            instrument_rows = self._connection.execute(
                """
                SELECT catalog.instrument_id
                FROM instrument_catalog_entries AS catalog
                JOIN instruments AS instrument USING (instrument_id)
                WHERE catalog.catalog_source_id = ? AND catalog.family = ?
                  AND instr(instrument.name, ?) > 0
                """,
                (source_id, family, excluded_name_fragment),
            ).fetchall()
            instrument_ids = [int(row[0]) for row in instrument_rows]
            self._connection.executemany(
                """
                DELETE FROM instrument_aliases
                WHERE catalog_source_id = ? AND instrument_id = ?
                """,
                ((source_id, instrument_id) for instrument_id in instrument_ids),
            )
            self._connection.executemany(
                """
                DELETE FROM instrument_catalog_entries
                WHERE catalog_source_id = ? AND instrument_id = ? AND family = ?
                """,
                (
                    (source_id, instrument_id, family)
                    for instrument_id in instrument_ids
                ),
            )
            affected = len(instrument_ids)
            self._connection.execute(
                """
                INSERT INTO data_migrations(
                    migration_id, applied_at_ms, affected_rows, details
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    migration_id,
                    now_ms,
                    affected,
                    f"excluded {source}/{family} names containing {excluded_name_fragment}",
                ),
            )
        return affected

    def upsert_daily_snapshot(
        self,
        source: str,
        scope: str,
        trade_date: date,
        bars: Sequence[DailyBar],
    ) -> WriteStats:
        started = time.perf_counter()
        if not scope:
            raise ValueError("snapshot scope is required")
        if not bars:
            return WriteStats(0, 0, 0, (time.perf_counter() - started) * 1000)
        for bar in bars:
            bar.validate()
            if bar.trade_date != trade_date:
                raise ValueError("snapshot bars must share the requested trade date")
        symbols = [bar.symbol for bar in bars]
        if len(set(symbols)) != len(symbols):
            raise ValueError("snapshot bars must contain unique symbols")
        payload_hash = _snapshot_hash(bars)
        trade_date_key = _date_key(trade_date)

        with self._lock, self._transaction():
            source_id = self._source_id(source)
            receipt = self._connection.execute(
                """
                SELECT row_count, payload_hash
                FROM daily_snapshot_receipts
                WHERE source_id = ? AND scope = ? AND trade_date = ?
                """,
                (source_id, scope, trade_date_key),
            ).fetchone()
            if receipt and int(receipt[0]) == len(bars) and bytes(receipt[1]) == payload_hash:
                elapsed_ms = (time.perf_counter() - started) * 1000
                return WriteStats(len(bars), 0, len(bars), elapsed_ms)

            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            changed = self._upsert_daily_bars_locked(source, bars, now_ms)
            self._connection.execute(
                """
                INSERT INTO daily_snapshot_receipts(
                    source_id, scope, trade_date, row_count, payload_hash, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, scope, trade_date) DO UPDATE SET
                    row_count = excluded.row_count,
                    payload_hash = excluded.payload_hash,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (source_id, scope, trade_date_key, len(bars), payload_hash, now_ms),
            )
        return WriteStats(
            len(bars), changed, len(bars) - changed,
            (time.perf_counter() - started) * 1000,
        )

    def _upsert_daily_bars_locked(
        self, source: str, bars: Sequence[DailyBar], now_ms: int
    ) -> int:
        source_id = self._source_id(source)
        canonical_source_id = self._source_id("tushare")
        symbols = {bar.symbol for bar in bars}
        symbol_ids = self._instrument_ids(symbols)
        missing = sorted(symbols - symbol_ids.keys())
        if missing:
            raise ValueError("daily bars reference unknown instruments: " + ", ".join(missing[:5]))
        before = self._connection.total_changes
        self._connection.executemany(
            """
            INSERT INTO daily_bars(
                instrument_id, trade_date, open, high, low, close,
                volume, source_id, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                source_id = excluded.source_id,
                updated_at_ms = excluded.updated_at_ms
            WHERE (daily_bars.open IS NOT excluded.open
                OR daily_bars.high IS NOT excluded.high
                OR daily_bars.low IS NOT excluded.low
                OR daily_bars.close IS NOT excluded.close
                OR daily_bars.volume IS NOT excluded.volume
                OR daily_bars.source_id IS NOT excluded.source_id)
              AND (daily_bars.source_id IS NOT ? OR excluded.source_id IS ?)
            """,
            (
                (
                    symbol_ids[bar.symbol], _date_key(bar.trade_date),
                    bar.open, bar.high, bar.low, bar.close, bar.volume,
                    source_id, now_ms, canonical_source_id, canonical_source_id,
                )
                for bar in bars
            ),
        )
        return self._connection.total_changes - before

    def get_symbol_sync_state(
        self, source: str, scope: str, symbol: str
    ) -> SymbolSyncState | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT source.code, state.scope, instrument.symbol,
                       state.covered_from, state.covered_through,
                       state.last_batch_rows, state.updated_at_ms
                FROM symbol_sync_states AS state
                JOIN sources AS source USING (source_id)
                JOIN instruments AS instrument USING (instrument_id)
                WHERE source.code = ? AND state.scope = ? AND instrument.symbol = ?
                """,
                (source, scope, symbol),
            ).fetchone()
        if row is None:
            return None
        return SymbolSyncState(
            source=str(row[0]),
            scope=str(row[1]),
            symbol=str(row[2]),
            covered_from=_date_from_key(int(row[3])),
            covered_through=_date_from_key(int(row[4])),
            last_batch_rows=int(row[5]),
            updated_at_ms=int(row[6]),
        )

    def upsert_symbol_history(
        self,
        source: str,
        scope: str,
        symbol: str,
        covered_from: date,
        covered_through: date,
        bars: Sequence[DailyBar],
    ) -> WriteStats:
        started = time.perf_counter()
        if not scope:
            raise ValueError("symbol-history scope is required")
        if covered_from > covered_through:
            raise ValueError("symbol-history coverage start must not exceed end")
        dates: set[date] = set()
        for bar in bars:
            bar.validate()
            if bar.symbol != symbol:
                raise ValueError("symbol-history bars must share the requested symbol")
            if not covered_from <= bar.trade_date <= covered_through:
                raise ValueError("symbol-history bars must be inside the covered range")
            if bar.trade_date in dates:
                raise ValueError("symbol-history bars must contain unique dates")
            dates.add(bar.trade_date)

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            instrument_ids = self._instrument_ids({symbol})
            if symbol not in instrument_ids:
                raise ValueError(f"symbol history references unknown instrument: {symbol}")
            instrument_id = instrument_ids[symbol]
            existing = self._connection.execute(
                """
                SELECT covered_from, covered_through
                FROM symbol_sync_states
                WHERE source_id = ? AND scope = ? AND instrument_id = ?
                """,
                (source_id, scope, instrument_id),
            ).fetchone()
            merged_from = min(int(existing[0]), _date_key(covered_from)) if existing else _date_key(covered_from)
            merged_through = max(int(existing[1]), _date_key(covered_through)) if existing else _date_key(covered_through)
            changed = self._upsert_daily_bars_locked(source, bars, now_ms) if bars else 0
            self._connection.execute(
                """
                INSERT INTO symbol_sync_states(
                    source_id, scope, instrument_id, covered_from, covered_through,
                    last_batch_rows, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, scope, instrument_id) DO UPDATE SET
                    covered_from = excluded.covered_from,
                    covered_through = excluded.covered_through,
                    last_batch_rows = excluded.last_batch_rows,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    source_id, scope, instrument_id, merged_from, merged_through,
                    len(bars), now_ms,
                ),
            )
        return WriteStats(
            len(bars), changed, len(bars) - changed,
            (time.perf_counter() - started) * 1000,
        )

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[StoredDailyBar]:
        clauses = ["instrument.symbol = ?"]
        parameters: list[object] = [symbol]
        if start_date is not None:
            clauses.append("bar.trade_date >= ?")
            parameters.append(_date_key(start_date))
        if end_date is not None:
            clauses.append("bar.trade_date <= ?")
            parameters.append(_date_key(end_date))
        query = f"""
            SELECT instrument.symbol, bar.trade_date, bar.open, bar.high,
                   bar.low, bar.close, bar.volume, source.code, bar.updated_at_ms
            FROM daily_bars AS bar
            JOIN instruments AS instrument USING (instrument_id)
            JOIN sources AS source USING (source_id)
            WHERE {' AND '.join(clauses)}
            ORDER BY bar.trade_date
        """
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [
            StoredDailyBar(
                symbol=row[0],
                trade_date=_date_from_key(row[1]),
                open=row[2],
                high=row[3],
                low=row[4],
                close=row[5],
                volume=row[6],
                source=row[7],
                updated_at_ms=row[8],
            )
            for row in rows
        ]

    def list_custom_groups(self, query: str = "") -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT custom.group_id, custom.name, custom.description,
                       custom.created_at_ms, custom.updated_at_ms, count(member.instrument_id)
                FROM custom_instrument_groups AS custom
                LEFT JOIN custom_instrument_group_members AS member USING (group_id)
                GROUP BY custom.group_id
                ORDER BY custom.name COLLATE NOCASE, custom.group_id
                """
            ).fetchall()
        groups = [
            {
                "id": str(row[0]), "symbol": f"CUSTOM:{row[0]}",
                "name": str(row[1]), "description": str(row[2]),
                "member_count": int(row[5]), "created_at_ms": int(row[3]),
                "updated_at_ms": int(row[4]),
            }
            for row in rows
        ]
        return groups if not query.strip() else [
            item for item in groups
            if matches_name_or_pinyin(query, str(item["name"]), str(item["description"]))
        ]

    def get_custom_group(self, group_id: str) -> dict[str, object] | None:
        with self._lock:
            group = self._connection.execute(
                """
                SELECT group_id, name, description, created_at_ms, updated_at_ms
                FROM custom_instrument_groups WHERE group_id = ?
                """,
                (group_id,),
            ).fetchone()
            if group is None:
                return None
            rows = self._connection.execute(
                """
                SELECT instrument.symbol, instrument.name, instrument.kind,
                       instrument.exchange, member.position, member.tags_json,
                       member.note, instrument.active
                FROM custom_instrument_group_members AS member
                JOIN instruments AS instrument USING (instrument_id)
                WHERE member.group_id = ?
                ORDER BY member.position, instrument.symbol
                """,
                (group_id,),
            ).fetchall()
        return {
            "id": str(group[0]), "symbol": f"CUSTOM:{group[0]}",
            "name": str(group[1]), "description": str(group[2]),
            "created_at_ms": int(group[3]), "updated_at_ms": int(group[4]),
            "members": [
                {
                    "symbol": str(row[0]), "name": str(row[1]),
                    "kind": str(row[2]), "exchange": str(row[3]),
                    "position": int(row[4]), "tags": json.loads(str(row[5])),
                    "note": str(row[6]), "available": bool(row[7]),
                }
                for row in rows
            ],
        }

    def create_custom_group(
        self,
        group_id: str,
        name: str,
        description: str = "",
        members: Sequence[dict[str, object]] = (),
    ) -> dict[str, object]:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("custom group name is required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                INSERT INTO custom_instrument_groups(
                    group_id, name, description, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (group_id, normalized_name, description.strip(), now_ms, now_ms),
            )
            self._replace_custom_group_members_locked(group_id, members, now_ms)
        result = self.get_custom_group(group_id)
        assert result is not None
        return result

    def update_custom_group(
        self,
        group_id: str,
        name: str,
        description: str = "",
        members: Sequence[dict[str, object]] = (),
    ) -> dict[str, object] | None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("custom group name is required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE custom_instrument_groups
                SET name = ?, description = ?, updated_at_ms = ?
                WHERE group_id = ?
                """,
                (normalized_name, description.strip(), now_ms, group_id),
            )
            if cursor.rowcount == 0:
                return None
            self._replace_custom_group_members_locked(group_id, members, now_ms)
        return self.get_custom_group(group_id)

    def delete_custom_group(self, group_id: str) -> bool:
        with self._lock, self._transaction():
            self._connection.execute(
                "DELETE FROM custom_instrument_group_members WHERE group_id = ?",
                (group_id,),
            )
            cursor = self._connection.execute(
                "DELETE FROM custom_instrument_groups WHERE group_id = ?",
                (group_id,),
            )
        return cursor.rowcount > 0

    def _replace_custom_group_members_locked(
        self,
        group_id: str,
        members: Sequence[dict[str, object]],
        now_ms: int,
    ) -> None:
        symbols = [str(item["symbol"]).upper() for item in members]
        if len(symbols) != len(set(symbols)):
            raise ValueError("custom group members must contain unique symbols")
        instrument_ids = self._instrument_ids(set(symbols))
        missing = set(symbols) - instrument_ids.keys()
        if missing:
            raise ValueError(f"unknown instruments: {', '.join(sorted(missing))}")
        self._connection.execute(
            "DELETE FROM custom_instrument_group_members WHERE group_id = ?",
            (group_id,),
        )
        self._connection.executemany(
            """
            INSERT INTO custom_instrument_group_members(
                group_id, instrument_id, position, tags_json, note, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    group_id, instrument_ids[symbol], position,
                    json.dumps(
                        [str(tag).strip() for tag in item.get("tags", []) if str(tag).strip()],
                        ensure_ascii=False, separators=(",", ":"),
                    ),
                    str(item.get("note", "")).strip(), now_ms,
                )
                for position, (symbol, item) in enumerate(zip(symbols, members))
            ),
        )

    def search_instruments(
        self,
        query: str = "",
        kinds: set[InstrumentKind] | None = None,
        classification: str | None = None,
        source_system: str | None = None,
        family: str | None = None,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 501 or offset < 0:
            raise ValueError("invalid instrument pagination")
        if classification not in {None, "stock", "etf", "index", "concept", "industry", "sector"}:
            raise ValueError("invalid instrument classification")
        clauses: list[str] = []
        parameters: list[object] = []
        if query.strip():
            pattern = f"%{query.strip()}%"
            clauses.append(
                "(instrument.symbol LIKE ? OR instrument.name LIKE ? OR EXISTS ("
                "SELECT 1 FROM instrument_aliases AS alias "
                "WHERE alias.instrument_id = instrument.instrument_id AND alias.alias LIKE ?))"
            )
            parameters.extend((pattern, pattern, pattern))
        if kinds:
            values = sorted(item.value for item in kinds)
            clauses.append("instrument.kind IN (" + ",".join("?" for _ in values) + ")")
            parameters.extend(values)
        if classification:
            class_clause, class_parameters = _instrument_classification_clause(classification)
            clauses.append(class_clause)
            parameters.extend(class_parameters)
        for column, value in (
            ("catalog.source_system", source_system),
            ("catalog.family", family),
            ("catalog.category", category),
        ):
            if value:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend((limit, offset))
        with self._lock:
            rows = self._connection.execute(
                f"""
                WITH selected AS (
                    SELECT instrument.instrument_id, instrument.symbol,
                           instrument.name, instrument.kind, instrument.exchange,
                           instrument.active, catalog.source_system,
                           catalog.family, catalog.category
                    FROM instruments AS instrument
                    LEFT JOIN instrument_catalog_entries AS catalog USING (instrument_id)
                    {where}
                    ORDER BY instrument.kind, instrument.name, instrument.symbol
                    LIMIT ? OFFSET ?
                )
                SELECT selected.symbol, selected.name, selected.kind,
                       selected.exchange, selected.active, selected.source_system,
                       selected.family, selected.category,
                       (SELECT min(trade_date) FROM daily_bars
                        WHERE instrument_id = selected.instrument_id),
                       (SELECT max(trade_date) FROM daily_bars
                        WHERE instrument_id = selected.instrument_id),
                       (SELECT count(*) FROM daily_bars
                        WHERE instrument_id = selected.instrument_id)
                FROM selected
                ORDER BY selected.kind, selected.name, selected.symbol
                """,
                parameters,
            ).fetchall()
        return [_instrument_row(row) for row in rows]

    def get_instrument_summary(self, symbol: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT instrument.symbol, instrument.name, instrument.kind,
                       instrument.exchange, instrument.active,
                       catalog.source_system, catalog.family, catalog.category,
                       (SELECT min(trade_date) FROM daily_bars
                        WHERE instrument_id = instrument.instrument_id),
                       (SELECT max(trade_date) FROM daily_bars
                        WHERE instrument_id = instrument.instrument_id),
                       (SELECT count(*) FROM daily_bars
                        WHERE instrument_id = instrument.instrument_id)
                FROM instruments AS instrument
                LEFT JOIN instrument_catalog_entries AS catalog USING (instrument_id)
                WHERE instrument.symbol = ?
                ORDER BY catalog.catalog_source_id
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
            if row is None:
                return None
            instrument_id = int(
                self._connection.execute(
                    "SELECT instrument_id FROM instruments WHERE symbol = ?", (symbol,)
                ).fetchone()[0]
            )
            aliases = self._connection.execute(
                """
                SELECT DISTINCT alias FROM instrument_aliases
                WHERE instrument_id = ? AND alias_type = 'display_name'
                ORDER BY alias
                """,
                (instrument_id,),
            ).fetchall()
            catalog = self._connection.execute(
                """
                SELECT source.code, entry.listed_on, entry.delisted_on
                FROM instrument_catalog_entries AS entry
                JOIN sources AS source ON source.source_id = entry.catalog_source_id
                WHERE entry.instrument_id = ?
                ORDER BY entry.catalog_source_id LIMIT 1
                """,
                (instrument_id,),
            ).fetchone()
            incidents = self._connection.execute(
                """
                SELECT source.code, incident.dataset, incident.trade_date,
                       incident.incident_type, incident.message
                FROM provider_incidents AS incident
                JOIN sources AS source USING (source_id)
                WHERE incident.status = 'open'
                  AND (incident.scope = ? OR incident.scope LIKE ?)
                ORDER BY incident.trade_date DESC, incident.incident_id DESC
                LIMIT 20
                """,
                (symbol, f"%:{symbol}"),
            ).fetchall()
        result = _instrument_row(row)
        result.update(
            {
                "aliases": [str(item[0]) for item in aliases],
                "catalog_source": str(catalog[0]) if catalog else None,
                "listed_on": (
                    _date_from_key(int(catalog[1])) if catalog and catalog[1] is not None else None
                ),
                "delisted_on": (
                    _date_from_key(int(catalog[2])) if catalog and catalog[2] is not None else None
                ),
                "open_incidents": [
                    {
                        "source": str(item[0]),
                        "dataset": str(item[1]),
                        "trade_date": _date_from_key(int(item[2])),
                        "type": str(item[3]),
                        "message": str(item[4]),
                    }
                    for item in incidents
                ],
            }
        )
        return result

    def list_board_members(
        self, board_symbol: str, limit: int = 500, offset: int = 0
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 5000 or offset < 0:
            raise ValueError("invalid membership pagination")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT member.member_symbol, member.member_name,
                       member.first_seen_on, member.last_seen_on, source.code,
                       instrument.kind, instrument.exchange, instrument.active
                FROM board_memberships AS member
                JOIN instruments AS board
                  ON board.instrument_id = member.board_instrument_id
                JOIN sources AS source USING (source_id)
                LEFT JOIN instruments AS instrument
                  ON instrument.symbol = member.member_symbol
                WHERE board.symbol = ? AND member.active = 1
                ORDER BY member.member_symbol
                LIMIT ? OFFSET ?
                """,
                (board_symbol, limit, offset),
            ).fetchall()
        return [
            {
                "symbol": str(row[0]), "name": str(row[1]),
                "first_seen_on": _date_from_key(int(row[2])),
                "last_seen_on": _date_from_key(int(row[3])), "source": str(row[4]),
                "kind": str(row[5]) if row[5] is not None else None,
                "exchange": str(row[6]) if row[6] is not None else None,
                "available": row[5] is not None and bool(row[7]),
            }
            for row in rows
        ]

    def list_symbol_boards(
        self, member_symbol: str, limit: int = 500, offset: int = 0
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 5000 or offset < 0:
            raise ValueError("invalid membership pagination")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT board.symbol, board.name, catalog.source_system,
                       catalog.family, catalog.category, source.code
                FROM board_memberships AS member
                JOIN instruments AS board
                  ON board.instrument_id = member.board_instrument_id
                JOIN sources AS source USING (source_id)
                LEFT JOIN instrument_catalog_entries AS catalog
                  ON catalog.instrument_id = board.instrument_id
                 AND catalog.catalog_source_id = member.source_id
                WHERE member.member_symbol = ? AND member.active = 1
                ORDER BY source.code, board.name, board.symbol
                LIMIT ? OFFSET ?
                """,
                (member_symbol, limit, offset),
            ).fetchall()
        return [
            {
                "symbol": str(row[0]), "name": str(row[1]),
                "source_system": row[2], "family": row[3],
                "category": row[4], "source": str(row[5]),
            }
            for row in rows
        ]

    def list_instrument_coverage(
        self, kinds: set[InstrumentKind] | None = None
    ) -> list[InstrumentCoverage]:
        parameters: list[object] = []
        where = ""
        if kinds:
            values = sorted(item.value for item in kinds)
            where = "WHERE instrument.kind IN (" + ",".join("?" for _ in values) + ")"
            parameters.extend(values)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT instrument.instrument_id, instrument.symbol, instrument.name,
                       instrument.kind, instrument.active, min(bar.trade_date),
                       max(bar.trade_date), count(bar.trade_date)
                FROM instruments AS instrument
                LEFT JOIN daily_bars AS bar USING (instrument_id)
                {where}
                GROUP BY instrument.instrument_id
                ORDER BY instrument.kind, instrument.symbol
                """,
                parameters,
            ).fetchall()
            source_rows = self._connection.execute(
                f"""
                SELECT instrument.instrument_id, source.code, count(*)
                FROM instruments AS instrument
                JOIN daily_bars AS bar USING (instrument_id)
                JOIN sources AS source USING (source_id)
                {where}
                GROUP BY instrument.instrument_id, source.code
                ORDER BY instrument.instrument_id, source.code
                """,
                parameters,
            ).fetchall()
        sources: dict[int, list[tuple[str, int]]] = {}
        for row in source_rows:
            sources.setdefault(int(row[0]), []).append((str(row[1]), int(row[2])))
        return [
            InstrumentCoverage(
                symbol=str(row[1]),
                name=str(row[2]),
                kind=InstrumentKind(str(row[3])),
                active=bool(row[4]),
                first_trade_date=_date_from_key(int(row[5])) if row[5] is not None else None,
                last_trade_date=_date_from_key(int(row[6])) if row[6] is not None else None,
                row_count=int(row[7]),
                source_rows=tuple(sources.get(int(row[0]), ())),
            )
            for row in rows
        ]

    def list_catalog_coverage_rows(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT catalog_source.code, catalog.source_system, catalog.family,
                       catalog.category, instrument.symbol, instrument.name,
                       instrument.active, catalog.observed_on, catalog.listed_on,
                       catalog.delisted_on, min(bar.trade_date), max(bar.trade_date),
                       count(bar.trade_date)
                FROM instrument_catalog_entries AS catalog
                JOIN sources AS catalog_source
                  ON catalog_source.source_id = catalog.catalog_source_id
                JOIN instruments AS instrument USING (instrument_id)
                LEFT JOIN daily_bars AS bar USING (instrument_id)
                GROUP BY catalog.catalog_source_id, catalog.provider_symbol
                ORDER BY catalog_source.code, catalog.family, catalog.category,
                         instrument.symbol
                """
            ).fetchall()
        return [
            {
                "catalog_source": str(row[0]),
                "source_system": str(row[1]),
                "family": str(row[2]),
                "category": str(row[3]),
                "symbol": str(row[4]),
                "name": str(row[5]),
                "active": bool(row[6]),
                "observed_on": _date_from_key(int(row[7])),
                "listed_on": _date_from_key(int(row[8])) if row[8] is not None else None,
                "delisted_on": _date_from_key(int(row[9])) if row[9] is not None else None,
                "first_trade_date": _date_from_key(int(row[10])) if row[10] is not None else None,
                "last_trade_date": _date_from_key(int(row[11])) if row[11] is not None else None,
                "rows": int(row[12]),
            }
            for row in rows
        ]

    def count_daily_bars(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT count(*) FROM daily_bars").fetchone()[0])

    def has_daily_snapshot(self, source: str, scope: str, trade_date: date) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1
                FROM daily_snapshot_receipts AS receipt
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND receipt.scope = ? AND receipt.trade_date = ?
                """,
                (source, scope, _date_key(trade_date)),
            ).fetchone()
        return row is not None

    def list_daily_snapshot_dates(
        self,
        source: str,
        scope: str,
        start_date: date,
        end_date: date,
    ) -> set[date]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT receipt.trade_date
                FROM daily_snapshot_receipts AS receipt
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND receipt.scope = ?
                  AND receipt.trade_date BETWEEN ? AND ?
                """,
                (source, scope, _date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return {_date_from_key(int(row[0])) for row in rows}

    def record_coverage_gap(self, source: str, scope: str, trade_date: date, reason: str) -> None:
        if not reason:
            raise ValueError("coverage gap reason is required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            self._connection.execute(
                """
                INSERT INTO coverage_gaps(source_id, scope, trade_date, reason, observed_at_ms)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, scope, trade_date) DO UPDATE SET
                    reason = excluded.reason,
                    observed_at_ms = excluded.observed_at_ms
                """,
                (source_id, scope, _date_key(trade_date), reason, now_ms),
            )

    def list_coverage_gaps(self, source: str, scope: str) -> list[CoverageGap]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT source.code, gap.scope, gap.trade_date, gap.reason, gap.observed_at_ms
                FROM coverage_gaps AS gap
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND gap.scope = ?
                ORDER BY gap.trade_date
                """,
                (source, scope),
            ).fetchall()
        return [
            CoverageGap(row[0], row[1], _date_from_key(row[2]), row[3], row[4])
            for row in rows
        ]

    def record_provider_incident(
        self,
        source: str,
        dataset: str,
        scope: str,
        trade_date: date,
        incident_type: str,
        message: str,
    ) -> int:
        if not all((dataset, scope, incident_type, message)):
            raise ValueError("provider incident fields are required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            self._connection.execute(
                """
                INSERT INTO provider_incidents(
                    source_id, dataset, scope, trade_date, incident_type, status,
                    occurrence_count, message, first_observed_at_ms, last_observed_at_ms
                ) VALUES (?, ?, ?, ?, ?, 'open', 1, ?, ?, ?)
                ON CONFLICT(source_id, dataset, scope, trade_date, incident_type) DO UPDATE SET
                    status = 'open',
                    occurrence_count = provider_incidents.occurrence_count + 1,
                    message = excluded.message,
                    last_observed_at_ms = excluded.last_observed_at_ms,
                    resolved_at_ms = NULL,
                    resolution = NULL
                """,
                (
                    source_id,
                    dataset,
                    scope,
                    _date_key(trade_date),
                    incident_type,
                    message,
                    now_ms,
                    now_ms,
                ),
            )
            row = self._connection.execute(
                """
                SELECT incident_id FROM provider_incidents
                WHERE source_id = ? AND dataset = ? AND scope = ?
                  AND trade_date = ? AND incident_type = ?
                """,
                (source_id, dataset, scope, _date_key(trade_date), incident_type),
            ).fetchone()
        return int(row[0])

    def resolve_provider_incident(
        self,
        source: str,
        dataset: str,
        scope: str,
        trade_date: date,
        incident_type: str,
        resolution: str,
    ) -> bool:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            cursor = self._connection.execute(
                """
                UPDATE provider_incidents
                SET status = 'resolved', resolved_at_ms = ?, resolution = ?
                WHERE source_id = ? AND dataset = ? AND scope = ?
                  AND trade_date = ? AND incident_type = ? AND status = 'open'
                """,
                (
                    now_ms,
                    resolution,
                    source_id,
                    dataset,
                    scope,
                    _date_key(trade_date),
                    incident_type,
                ),
            )
        return cursor.rowcount > 0

    def list_provider_incidents(self) -> list[ProviderIncident]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT incident.incident_id, source.code, incident.dataset, incident.scope,
                       incident.trade_date, incident.incident_type, incident.status,
                       incident.occurrence_count, incident.message,
                       incident.first_observed_at_ms, incident.last_observed_at_ms,
                       incident.resolved_at_ms, incident.resolution
                FROM provider_incidents AS incident
                JOIN sources AS source USING (source_id)
                ORDER BY incident.trade_date, incident.incident_id
                """
            ).fetchall()
        return [
            ProviderIncident(
                int(row[0]), str(row[1]), str(row[2]), str(row[3]), _date_from_key(row[4]),
                str(row[5]), str(row[6]), int(row[7]), str(row[8]), int(row[9]), int(row[10]),
                int(row[11]) if row[11] is not None else None,
                str(row[12]) if row[12] is not None else None,
            )
            for row in rows
        ]

    def record_validation_result(
        self,
        primary_source: str,
        validator_source: str,
        symbol: str,
        trade_date: date,
        status: str,
        message: str,
        incident_id: int | None = None,
    ) -> None:
        if status not in {"match", "mismatch", "missing", "error"}:
            raise ValueError(f"unsupported validation status: {status}")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            primary_id = self._source_id(primary_source)
            validator_id = self._source_id(validator_source)
            self._connection.execute(
                """
                INSERT INTO provider_validation_results(
                    incident_id, primary_source_id, validator_source_id, symbol,
                    trade_date, status, message, checked_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id, primary_id, validator_id, symbol, _date_key(trade_date),
                    status, message, now_ms,
                ),
            )

    def list_validation_results(self, trade_date: date) -> list[ValidationResult]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT primary_source.code, validator_source.code, result.symbol,
                       result.trade_date, result.status, result.message, result.checked_at_ms
                FROM provider_validation_results AS result
                JOIN sources AS primary_source
                  ON primary_source.source_id = result.primary_source_id
                JOIN sources AS validator_source
                  ON validator_source.source_id = result.validator_source_id
                WHERE result.trade_date = ?
                ORDER BY result.symbol, validator_source.code, result.validation_id
                """,
                (_date_key(trade_date),),
            ).fetchall()
        return [
            ValidationResult(
                str(row[0]), str(row[1]), str(row[2]), _date_from_key(row[3]),
                str(row[4]), str(row[5]), int(row[6]),
            )
            for row in rows
        ]

    def enqueue_repair_job(self, primary_source: str, scope: str, trade_date: date) -> int:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(primary_source)
            self._connection.execute(
                """
                INSERT OR IGNORE INTO repair_jobs(
                    primary_source_id, scope, trade_date, status, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (source_id, scope, _date_key(trade_date), now_ms, now_ms),
            )
            row = self._connection.execute(
                """
                SELECT job_id FROM repair_jobs
                WHERE primary_source_id = ? AND scope = ? AND trade_date = ?
                """,
                (source_id, scope, _date_key(trade_date)),
            ).fetchone()
        return int(row[0])

    def begin_repair_job(self, primary_source: str, scope: str, trade_date: date) -> int:
        job_id = self.enqueue_repair_job(primary_source, scope, trade_date)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE repair_jobs
                SET status = 'running', attempt_count = attempt_count + 1,
                    last_error = NULL, updated_at_ms = ?
                WHERE job_id = ?
                """,
                (now_ms, job_id),
            )
        return job_id

    def record_repair_items(
        self,
        job_id: int,
        repair_source: str | None,
        status: str,
        messages: dict[str, str],
    ) -> None:
        if status not in {"repaired", "unresolved"}:
            raise ValueError(f"unsupported repair item status: {status}")
        if not messages:
            return
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(repair_source) if repair_source else None
            self._connection.executemany(
                """
                INSERT INTO repair_items(
                    job_id, symbol, repair_source_id, status, message, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, symbol) DO UPDATE SET
                    repair_source_id = excluded.repair_source_id,
                    status = excluded.status,
                    message = excluded.message,
                    updated_at_ms = excluded.updated_at_ms
                """,
                [
                    (job_id, symbol, source_id, status, message, now_ms)
                    for symbol, message in messages.items()
                ],
            )

    def queue_symbol_repairs(
        self,
        primary_source: str,
        scope: str,
        trade_date: date,
        messages: dict[str, str],
    ) -> int:
        job_id = self.enqueue_repair_job(primary_source, scope, trade_date)
        self.record_repair_items(job_id, None, "unresolved", messages)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE repair_jobs
                SET status = 'queued', expected_rows = ?, repaired_rows = 0,
                    unresolved_rows = ?, last_error = NULL, updated_at_ms = ?
                WHERE job_id = ?
                """,
                (len(messages), len(messages), now_ms, job_id),
            )
        return job_id

    def list_unresolved_repair_symbols(self, job_id: int) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT symbol FROM repair_items
                WHERE job_id = ? AND status = 'unresolved'
                ORDER BY symbol
                """,
                (job_id,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def finish_repair_job(
        self,
        job_id: int,
        status: str,
        expected_rows: int,
        repaired_rows: int,
        unresolved_rows: int,
        last_error: str | None = None,
    ) -> None:
        if status not in {"completed", "partial", "failed"}:
            raise ValueError(f"unsupported repair job status: {status}")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE repair_jobs
                SET status = ?, expected_rows = ?, repaired_rows = ?,
                    unresolved_rows = ?, last_error = ?, updated_at_ms = ?
                WHERE job_id = ?
                  AND (status != 'completed' OR ? = 'completed')
                """,
                (
                    status, expected_rows, repaired_rows, unresolved_rows,
                    last_error, now_ms, job_id, status,
                ),
            )

    def list_repair_jobs(self, statuses: set[str] | None = None) -> list[RepairJob]:
        clauses = []
        parameters: list[object] = []
        if statuses:
            ordered = sorted(statuses)
            clauses.append("job.status IN (" + ",".join("?" for _ in ordered) + ")")
            parameters.extend(ordered)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT job.job_id, source.code, job.scope, job.trade_date, job.status,
                       job.attempt_count, job.expected_rows, job.repaired_rows,
                       job.unresolved_rows, job.last_error,
                       job.created_at_ms, job.updated_at_ms
                FROM repair_jobs AS job
                JOIN sources AS source ON source.source_id = job.primary_source_id
                {where}
                ORDER BY job.trade_date, job.job_id
                """,
                parameters,
            ).fetchall()
        return [
            RepairJob(
                int(row[0]), str(row[1]), str(row[2]), _date_from_key(row[3]),
                str(row[4]), int(row[5]), int(row[6]), int(row[7]), int(row[8]),
                str(row[9]) if row[9] is not None else None, int(row[10]), int(row[11]),
            )
            for row in rows
        ]

    def _source_id(
        self,
        source: str,
        acquired_via: str | None = None,
        source_system: str | None = None,
    ) -> int:
        if not source:
            raise ValueError("source is required")
        self._connection.execute("INSERT OR IGNORE INTO sources(code) VALUES (?)", (source,))
        source_id = int(self._connection.execute("SELECT source_id FROM sources WHERE code = ?", (source,)).fetchone()[0])
        default_acquired_via, default_source_system = _source_profile(source)
        self._connection.execute(
            """
            INSERT INTO source_profiles(source_id, acquired_via, source_system)
            VALUES (?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                acquired_via = excluded.acquired_via,
                source_system = excluded.source_system
            """,
            (
                source_id,
                acquired_via or default_acquired_via,
                source_system or default_source_system,
            ),
        )
        return source_id

    def _instrument_ids(self, symbols: set[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        ordered = sorted(symbols)
        for offset in range(0, len(ordered), 900):
            chunk = ordered[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._connection.execute(
                f"SELECT symbol, instrument_id FROM instruments WHERE symbol IN ({placeholders})",
                chunk,
            ).fetchall()
            result.update((str(row[0]), int(row[1])) for row in rows)
        return result

    def _instrument_names(self, symbols: set[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        ordered = sorted(symbols)
        for offset in range(0, len(ordered), 900):
            chunk = ordered[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._connection.execute(
                f"SELECT symbol, name FROM instruments WHERE symbol IN ({placeholders})",
                chunk,
            ).fetchall()
            result.update((str(row[0]), str(row[1])) for row in rows)
        return result

    def _replace_pinyin_aliases_locked(self, instruments: Sequence[Instrument]) -> int:
        if not instruments:
            return 0
        source_id = self._source_id(
            "stock_harness_search", acquired_via="derived", source_system="stock_harness"
        )
        instrument_ids = self._instrument_ids({item.symbol for item in instruments})
        ids = [instrument_ids[item.symbol] for item in instruments]
        self._connection.executemany(
            "DELETE FROM instrument_aliases WHERE catalog_source_id = ? AND instrument_id = ?",
            ((source_id, instrument_id) for instrument_id in ids),
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        rows = [
            (source_id, instrument_ids[item.symbol], alias, alias_type, now_ms)
            for item in instruments
            for alias, alias_type in pinyin_search_aliases(item.name)
        ]
        self._connection.executemany(
            """
            INSERT INTO instrument_aliases(
                catalog_source_id, instrument_id, alias, alias_type, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        return len(rows)

    def _backfill_pinyin_aliases(self) -> int:
        migration_id = "2026-08-04-instrument-pinyin-aliases-v1"
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            existing = self._connection.execute(
                "SELECT affected_rows FROM data_migrations WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            if existing is not None:
                return int(existing[0])
            rows = self._connection.execute(
                "SELECT symbol, name, kind, exchange, active FROM instruments"
            ).fetchall()
            instruments = [
                Instrument(str(row[0]), str(row[1]), InstrumentKind(str(row[2])), str(row[3]), bool(row[4]))
                for row in rows
            ]
            affected = self._replace_pinyin_aliases_locked(instruments)
            self._connection.execute(
                """
                INSERT INTO data_migrations(migration_id, applied_at_ms, affected_rows, details)
                VALUES (?, ?, ?, ?)
                """,
                (migration_id, now_ms, affected, "backfilled full and initial pinyin aliases"),
            )
        return affected

    def _transaction(self):
        return _Transaction(self._connection, self._writer_lock)


class _Transaction:
    def __init__(
        self,
        connection: sqlite3.Connection,
        writer_lock: AbstractContextManager[None],
    ) -> None:
        self.connection = connection
        self.writer_lock = writer_lock

    def __enter__(self) -> None:
        self.writer_lock.__enter__()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self.writer_lock.__exit__(*sys.exc_info())
            raise

    def __exit__(self, exc_type, _exc, _traceback) -> None:
        try:
            self.connection.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.writer_lock.__exit__(exc_type, _exc, _traceback)


class _ThreadOnlyWriterLock(AbstractContextManager[None]):
    def __init__(self) -> None:
        self._lock = threading.RLock()

    def __enter__(self) -> None:
        self._lock.acquire()

    def __exit__(self, *_args: object) -> None:
        self._lock.release()


class _InterprocessWriterLock(AbstractContextManager[None]):
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle = None

    def __enter__(self) -> None:
        import msvcrt

        started = time.monotonic()
        handle = self.path.open("a+b")
        if handle.seek(0, 2) == 0:
            handle.write(b"\0")
            handle.flush()
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                self._handle = handle
                return
            except OSError:
                if time.monotonic() - started >= self.timeout_seconds:
                    handle.close()
                    raise TimeoutError(f"timed out waiting for SQLite writer lock: {self.path}")
                time.sleep(0.05)

    def __exit__(self, *_args: object) -> None:
        import msvcrt

        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._handle.close()
            self._handle = None


def _date_key(value: date) -> int:
    return value.year * 10_000 + value.month * 100 + value.day


def _date_from_key(value: int) -> date:
    return date(value // 10_000, value // 100 % 100, value % 100)


def _source_profile(source: str) -> tuple[str, str]:
    if source == "tushare_dc":
        return "tushare", "eastmoney"
    if source == "tushare_ths":
        return "tushare", "ths"
    if source.startswith("akshare_eastmoney"):
        return "akshare", "eastmoney"
    if source.startswith("akshare_ths"):
        return "akshare", "ths"
    return source, source


def _instrument_row(row: sqlite3.Row) -> dict[str, object]:
    classification = _instrument_classification(
        str(row[2]), str(row[3]), row[5], row[7]
    )
    return {
        "symbol": str(row[0]), "name": str(row[1]), "kind": str(row[2]),
        "exchange": str(row[3]), "active": bool(row[4]),
        "source_system": row[5], "family": row[6], "category": row[7],
        "classification": classification,
        "classification_label": {
            "stock": "个股", "etf": "ETF", "index": "指数",
            "concept": "概念板块", "industry": "行业板块", "sector": "其他板块",
        }[classification],
        "source_label": _instrument_source_label(
            classification, str(row[3]), row[5]
        ),
        "first_trade_date": _date_from_key(int(row[8])) if row[8] is not None else None,
        "last_trade_date": _date_from_key(int(row[9])) if row[9] is not None else None,
        "rows": int(row[10]),
    }


def _instrument_classification_clause(classification: str) -> tuple[str, list[object]]:
    if classification in {"stock", "etf", "index"}:
        return "instrument.kind = ?", [classification]
    concept = (
        "(instrument.kind = 'sector' AND ("
        "catalog.category IN ('概念板块', 'concept') OR "
        "(catalog.source_system = 'ths' AND catalog.category = 'N')))"
    )
    industry = (
        "(instrument.kind = 'sector' AND (instrument.exchange = 'SI' OR "
        "catalog.category IN ('行业板块', 'industry') OR "
        "(catalog.source_system = 'ths' AND catalog.category = 'I')))"
    )
    if classification == "concept":
        return concept, []
    if classification == "industry":
        return industry, []
    return f"(instrument.kind = 'sector' AND NOT {concept} AND NOT {industry})", []


def _instrument_classification(
    kind: str,
    exchange: str,
    source_system: object,
    category: object,
) -> str:
    if kind != "sector":
        return kind
    category_value = str(category or "")
    source_value = str(source_system or "")
    if category_value in {"概念板块", "concept"} or (
        source_value == "ths" and category_value == "N"
    ):
        return "concept"
    if exchange == "SI" or category_value in {"行业板块", "industry"} or (
        source_value == "ths" and category_value == "I"
    ):
        return "industry"
    return "sector"


def _instrument_source_label(
    classification: str, exchange: str, source_system: object
) -> str:
    source_value = str(source_system or "")
    if source_value == "eastmoney":
        return "东财"
    if source_value == "ths":
        return "同花顺"
    if exchange == "SI":
        return "申万"
    if classification in {"etf", "stock"}:
        return {"SH": "上交所", "SZ": "深交所", "BJ": "北交所"}.get(
            exchange, exchange
        )
    if classification == "index":
        return {"CSI": "中证", "SH": "上证", "SZ": "深证"}.get(
            exchange, "主要指数"
        )
    return "其他"


def _snapshot_hash(bars: Sequence[DailyBar]) -> bytes:
    digest = blake2b(digest_size=16)
    for bar in sorted(bars, key=lambda item: item.symbol):
        digest.update(
            f"{bar.symbol}|{_date_key(bar.trade_date)}|{bar.open:.12g}|{bar.high:.12g}|"
            f"{bar.low:.12g}|{bar.close:.12g}|{bar.volume}\n".encode("ascii")
        )
    return digest.digest()
