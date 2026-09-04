"""SQLite hot store optimized for daily batch updates and symbol-range reads."""

from __future__ import annotations

import sqlite3
import threading
import time
import hashlib
import logging
from dataclasses import replace
from uuid import uuid4
from contextlib import AbstractContextManager
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from stock_harness.models import (
    AdjustmentFactor,
    BoardMembership,
    CatalogEntry,
    DailyBar,
    Instrument,
    InstrumentCoverage,
    InstrumentKind,
    MarketSnapshot,
    ProvisionalDailyBar,
    StoredDailyBar,
    StockTradeStatus,
    SymbolSyncState,
    WriteStats,
)
from stock_harness.search_terms import pinyin_search_aliases
from stock_harness.sqlite_mapping import (
    _date_from_key,
    _date_key,
    _instrument_classification_clause,
    _instrument_row,
    _snapshot_hash,
    _source_profile,
)
from stock_harness.sqlite_runtime import InterprocessWriterLock, ThreadOnlyWriterLock, Transaction
from stock_harness.sqlite_analysis_store import SQLiteAnalysisStoreMixin
from stock_harness.sqlite_chat_store import SQLiteChatStoreMixin
from stock_harness.sqlite_custom_group_store import SQLiteCustomGroupStoreMixin
from stock_harness.sqlite_custom_index_store import SQLiteCustomIndexStoreMixin
from stock_harness.sqlite_etf_holding_store import SQLiteEtfHoldingStoreMixin
from stock_harness.sqlite_futures_store import SQLiteFuturesStoreMixin
from stock_harness.sqlite_provider_quality_store import SQLiteProviderQualityStoreMixin
from stock_harness.sqlite_screener_store import SQLiteScreenerStoreMixin


from stock_harness.sqlite_schema import (
    FUTURES_SCHEMA as _FUTURES_SCHEMA,
    FUTURES_SCHEMA_VERSION as _FUTURES_SCHEMA_VERSION,
    SCHEMA as _SCHEMA,
)


LOGGER = logging.getLogger(__name__)


class SQLiteMarketDataStore(
    SQLiteFuturesStoreMixin,
    SQLiteChatStoreMixin,
    SQLiteAnalysisStoreMixin,
    SQLiteCustomGroupStoreMixin,
    SQLiteCustomIndexStoreMixin,
    SQLiteEtfHoldingStoreMixin,
    SQLiteProviderQualityStoreMixin,
    SQLiteScreenerStoreMixin,
):
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
            self._writer_lock = ThreadOnlyWriterLock()
        else:
            self._writer_lock = InterprocessWriterLock(
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
        self._futures_storage_ready = False
        self._futures_storage_error: str | None = None
        self._ensure_futures_schema()
        self._ensure_custom_index_volume()
        self._ensure_custom_group_member_roles()
        self._ensure_market_snapshot_metrics()
        self._ensure_generated_analysis_target_settings()
        self._backfill_pinyin_aliases()

    def __enter__(self) -> SQLiteMarketDataStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def futures_storage_status(self) -> dict[str, object]:
        return {
            "ready": self._futures_storage_ready,
            "schema_version": (
                _FUTURES_SCHEMA_VERSION if self._futures_storage_ready else None
            ),
            "error": self._futures_storage_error,
        }

    def _ensure_futures_schema(self) -> None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._writer_lock:
            try:
                self._connection.executescript("BEGIN IMMEDIATE;\n" + _FUTURES_SCHEMA)
                newest = self._connection.execute(
                    "SELECT max(schema_version) FROM futures_schema_metadata"
                ).fetchone()[0]
                if newest is not None and int(newest) > _FUTURES_SCHEMA_VERSION:
                    raise RuntimeError(
                        "futures schema is newer than this application: "
                        f"{newest} > {_FUTURES_SCHEMA_VERSION}"
                    )
                self._connection.execute(
                    """
                    INSERT INTO futures_schema_metadata(schema_version, state, applied_at_ms)
                    VALUES (?, 'ready', ?)
                    ON CONFLICT(schema_version) DO NOTHING
                    """,
                    (_FUTURES_SCHEMA_VERSION, now_ms),
                )
                self._connection.execute("COMMIT")
                self._futures_storage_ready = True
                self._futures_storage_error = None
            except Exception as error:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                self._futures_storage_ready = False
                self._futures_storage_error = " ".join(str(error).split())[:500]
                LOGGER.error(
                    "futures_schema_migration_failed version=%s error_type=%s",
                    _FUTURES_SCHEMA_VERSION,
                    type(error).__name__,
                )

    def _require_futures_storage(self) -> None:
        if not self._futures_storage_ready:
            raise RuntimeError(
                "futures storage is unavailable: "
                + (self._futures_storage_error or "schema is not ready")
            )

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
                    close, volume, amount, source_id, updated_at_ms
                )
                SELECT current.instrument_id, current.trade_date,
                       (current.close / previous.close - 1.0) * 100.0,
                       NULL, current.close, current.volume, NULL, current.source_id, ?
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
                    close = excluded.close,
                    volume = excluded.volume,
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
                    close, volume, amount, source_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                    change_percent = excluded.change_percent,
                    total_market_cap = COALESCE(excluded.total_market_cap, market_snapshots.total_market_cap),
                    close = COALESCE(excluded.close, market_snapshots.close),
                    volume = COALESCE(excluded.volume, market_snapshots.volume),
                    amount = COALESCE(excluded.amount, market_snapshots.amount),
                    source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    (
                        instrument_ids[item.symbol], _date_key(item.trade_date),
                        item.change_percent, item.total_market_cap, item.close,
                        item.volume, item.amount, source_id, now_ms,
                    )
                    for item in snapshots
                ),
            )
        return len(snapshots)

    def list_market_snapshots(self, symbols: Sequence[str]) -> list[dict[str, object]]:
        ordered = list(dict.fromkeys(
            item.strip() if item.strip().upper().startswith("FUT") else item.strip().upper()
            for item in symbols if item.strip()
        ))
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
                           snapshot.close, snapshot.volume, snapshot.amount,
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
                "close": float(row[7]) if row[7] is not None else None,
                "volume": int(row[8]) if row[8] is not None else None,
                "amount": float(row[9]) if row[9] is not None else None,
                "source": str(row[10]), "updated_at_ms": int(row[11]),
            }
            for row in rows
        }
        by_symbol.update(self._list_futures_market_snapshots(ordered))
        return [by_symbol[symbol] for symbol in ordered if symbol in by_symbol]

    def _list_futures_market_snapshots(
        self,
        symbols: Sequence[str],
    ) -> dict[str, dict[str, object]]:
        futures_symbols = [item for item in symbols if item.upper().startswith("FUT")]
        if not futures_symbols or not self.futures_storage_status()["ready"]:
            return {}
        final_rows: list[sqlite3.Row] = []
        provisional_rows: list[sqlite3.Row] = []
        with self._lock:
            for offset in range(0, len(futures_symbols), 400):
                chunk = futures_symbols[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                final_rows.extend(self._connection.execute(
                    f"""
                    SELECT instrument.symbol, instrument.name, instrument.kind,
                           instrument.exchange, bar.trading_day, bar.close,
                           bar.previous_settlement, bar.volume_contracts,
                           bar.amount_cny, bar.open_interest_contracts,
                           bar.open_interest_change_contracts, source.code,
                           COALESCE(contract.contract_month, mapped.contract_month),
                           COALESCE(contract.last_trading_date, mapped.last_trading_date),
                           bar.updated_at_ms
                    FROM instruments AS instrument
                    JOIN futures_daily_bars AS bar USING (instrument_id)
                    JOIN sources AS source USING (source_id)
                    LEFT JOIN futures_contracts AS contract USING (instrument_id)
                    LEFT JOIN futures_contracts AS mapped
                      ON mapped.instrument_id = bar.mapped_contract_instrument_id
                    WHERE instrument.symbol IN ({placeholders})
                      AND bar.trading_day = (
                          SELECT max(latest.trading_day)
                          FROM futures_daily_bars AS latest
                          WHERE latest.instrument_id = instrument.instrument_id
                      )
                    """,
                    chunk,
                ).fetchall())
                provisional_rows.extend(self._connection.execute(
                    f"""
                    SELECT instrument.symbol, instrument.name, instrument.kind,
                           instrument.exchange, bar.trading_day, bar.close,
                           bar.previous_settlement, bar.volume_contracts,
                           NULL, bar.open_interest_contracts, NULL, source.code,
                           contract.contract_month, contract.last_trading_date,
                           bar.updated_at_ms, bar.stale
                    FROM instruments AS instrument
                    JOIN futures_provisional_daily_bars AS bar USING (instrument_id)
                    JOIN sources AS source USING (source_id)
                    JOIN futures_contracts AS contract USING (instrument_id)
                    WHERE instrument.symbol IN ({placeholders})
                      AND bar.takeover_state = 'active'
                      AND bar.trading_day = (
                          SELECT max(latest.trading_day)
                          FROM futures_provisional_daily_bars AS latest
                          WHERE latest.instrument_id = instrument.instrument_id
                            AND latest.takeover_state = 'active'
                      )
                    ORDER BY bar.provider_time DESC
                    """,
                    chunk,
                ).fetchall())

        snapshots = {
            str(row[0]): self._futures_snapshot_row(row, "final")
            for row in final_rows
        }
        for row in provisional_rows:
            symbol = str(row[0])
            existing = snapshots.get(symbol)
            provisional_day = _date_from_key(int(row[4]))
            if existing is not None and existing["trade_date"] >= provisional_day:
                continue
            snapshots[symbol] = self._futures_snapshot_row(
                row, "provisional-stale" if bool(row[15]) else "provisional"
            )
        return snapshots

    @staticmethod
    def _futures_snapshot_row(row: sqlite3.Row, source_state: str) -> dict[str, object]:
        previous_settlement = float(row[6]) if row[6] is not None else None
        close = float(row[5])
        settlement_change = (
            (close / previous_settlement - 1) * 100
            if previous_settlement not in (None, 0) else None
        )
        return {
            "symbol": str(row[0]), "name": str(row[1]),
            "kind": str(row[2]), "exchange": str(row[3]),
            "trade_date": _date_from_key(int(row[4])), "close": close,
            "change_percent": settlement_change,
            "settlement_change_percent": settlement_change,
            "volume": int(row[7]),
            "amount": float(row[8]) if row[8] is not None else None,
            "open_interest": float(row[9]) if row[9] is not None else None,
            "open_interest_change": float(row[10]) if row[10] is not None else None,
            "source": str(row[11]), "source_state": source_state,
            "contract_month": str(row[12]) if row[12] is not None else None,
            "last_trading_date": (
                _date_from_key(int(row[13])) if row[13] is not None else None
            ),
            "updated_at_ms": int(row[14]),
            "total_market_cap": None,
        }

    def _ensure_market_snapshot_metrics(self) -> None:
        columns = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(market_snapshots)")
        }
        additions = {
            "close": "REAL",
            "volume": "INTEGER",
            "amount": "REAL",
        }
        missing = [(name, sql_type) for name, sql_type in additions.items() if name not in columns]
        if not missing:
            return
        with self._lock, self._transaction():
            for name, sql_type in missing:
                self._connection.execute(
                    f"ALTER TABLE market_snapshots ADD COLUMN {name} {sql_type}"
                )
            self._connection.execute(
                """
                UPDATE market_snapshots
                SET close = (
                        SELECT bar.close FROM daily_bars AS bar
                        WHERE bar.instrument_id = market_snapshots.instrument_id
                          AND bar.trade_date = market_snapshots.trade_date
                    ),
                    volume = (
                        SELECT bar.volume FROM daily_bars AS bar
                        WHERE bar.instrument_id = market_snapshots.instrument_id
                          AND bar.trade_date = market_snapshots.trade_date
                    )
                WHERE close IS NULL OR volume IS NULL
                """
            )

    def _ensure_custom_index_volume(self) -> None:
        columns = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(custom_index_daily_bars)")
        }
        if "volume" in columns:
            return
        with self._lock, self._transaction():
            self._connection.execute(
                "ALTER TABLE custom_index_daily_bars "
                "ADD COLUMN volume INTEGER NOT NULL DEFAULT 0"
            )

    def _ensure_custom_group_member_roles(self) -> None:
        columns = {
            str(row[1])
            for row in self._connection.execute(
                "PRAGMA table_info(custom_instrument_group_members)"
            )
        }
        if "role" in columns:
            return
        with self._lock, self._transaction():
            self._connection.execute(
                "ALTER TABLE custom_instrument_group_members "
                "ADD COLUMN role TEXT NOT NULL DEFAULT ''"
            )

    def _ensure_generated_analysis_target_settings(self) -> None:
        columns = {
            str(row[1])
            for row in self._connection.execute(
                "PRAGMA table_info(generated_analysis_targets)"
            )
        }
        if "settings_json" in columns:
            return
        with self._lock, self._transaction():
            self._connection.execute(
                "ALTER TABLE generated_analysis_targets "
                "ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'"
            )

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
        with self._lock:
            instrument = self._connection.execute(
                "SELECT symbol, instrument_id, kind FROM instruments "
                "WHERE symbol = ? COLLATE NOCASE",
                (symbol.upper(),),
            ).fetchone()
        if instrument is None:
            return []
        canonical_symbol = str(instrument[0])
        instrument_id = int(instrument[1])
        if str(instrument[2]) == InstrumentKind.CUSTOM_INDEX.value:
            clauses = ["custom.instrument_id = ?"]
            parameters: list[object] = [instrument_id]
            if start_date is not None:
                clauses.append("bar.trade_date >= ?")
                parameters.append(_date_key(start_date))
            if end_date is not None:
                clauses.append("bar.trade_date <= ?")
                parameters.append(_date_key(end_date))
            query = f"""
                SELECT bar.trade_date, bar.open, bar.high,
                       bar.low, bar.close, bar.volume, bar.updated_at_ms
                FROM custom_index_daily_bars AS bar
                JOIN custom_indices AS custom USING (index_id)
                WHERE {' AND '.join(clauses)}
                ORDER BY bar.trade_date
            """
            with self._lock:
                rows = self._connection.execute(query, parameters).fetchall()
            return [
                StoredDailyBar(
                    symbol=canonical_symbol, trade_date=_date_from_key(int(row[0])),
                    open=float(row[1]), high=float(row[2]), low=float(row[3]),
                    close=float(row[4]), volume=int(row[5]), source="local_custom_index",
                    updated_at_ms=int(row[6]),
                )
                for row in rows
            ]
        clauses = ["bar.instrument_id = ?"]
        parameters = [instrument_id]
        if start_date is not None:
            clauses.append("bar.trade_date >= ?")
            parameters.append(_date_key(start_date))
        if end_date is not None:
            clauses.append("bar.trade_date <= ?")
            parameters.append(_date_key(end_date))
        query = f"""
            SELECT bar.trade_date, bar.open, bar.high,
                   bar.low, bar.close, bar.volume, source.code, bar.updated_at_ms
            FROM daily_bars AS bar
            JOIN sources AS source USING (source_id)
            WHERE {' AND '.join(clauses)}
            ORDER BY bar.trade_date
        """
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [
            StoredDailyBar(
                symbol=canonical_symbol,
                trade_date=_date_from_key(row[0]),
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
                source=row[6],
                updated_at_ms=row[7],
            )
            for row in rows
        ]

    def get_instrument_kind(self, symbol: str) -> InstrumentKind | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT kind FROM instruments WHERE symbol = ? COLLATE NOCASE",
                (symbol.upper(),),
            ).fetchone()
        return InstrumentKind(str(row[0])) if row is not None else None

    def get_instrument_lifecycle(
        self, symbol: str
    ) -> tuple[date | None, date | None]:
        if self.get_instrument_kind(symbol) is InstrumentKind.FUTURES_CONTRACT:
            with self._lock:
                row = self._connection.execute(
                    """
                    SELECT contract.listed_on, contract.last_trading_date
                    FROM futures_contracts AS contract
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE instrument.symbol = ? COLLATE NOCASE
                    """,
                    (symbol,),
                ).fetchone()
            if row is None:
                return None, None
            return _date_from_key(int(row[0])), _date_from_key(int(row[1]))
        with self._lock:
            row = self._connection.execute(
                """
                SELECT instrument.active, min(catalog.listed_on), max(catalog.delisted_on)
                FROM instruments AS instrument
                LEFT JOIN instrument_catalog_entries AS catalog USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                GROUP BY instrument.instrument_id
                """,
                (symbol.upper(),),
            ).fetchone()
        if row is None:
            return None, None
        listed_on = _date_from_key(int(row[1])) if row[1] is not None else None
        delisted_on = (
            _date_from_key(int(row[2]))
            if not bool(row[0]) and row[2] is not None
            else None
        )
        return listed_on, delisted_on

    def get_recent_daily_bars(
        self, symbol: str, end_date: date, limit: int
    ) -> list[StoredDailyBar]:
        if limit <= 0:
            raise ValueError("daily bar limit must be positive")
        normalized = symbol.upper()
        with self._lock:
            instrument = self._connection.execute(
                "SELECT symbol, instrument_id, kind FROM instruments "
                "WHERE symbol = ? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
        if instrument is None:
            return []
        canonical_symbol = str(instrument[0])
        instrument_id = int(instrument[1])
        kind = InstrumentKind(str(instrument[2]))
        if kind is InstrumentKind.CUSTOM_INDEX:
            query = """
                SELECT bar.trade_date, bar.open, bar.high,
                       bar.low, bar.close, bar.volume, bar.updated_at_ms
                FROM custom_index_daily_bars AS bar
                JOIN custom_indices AS custom USING (index_id)
                WHERE custom.instrument_id = ? AND bar.trade_date <= ?
                ORDER BY bar.trade_date DESC LIMIT ?
            """
            with self._lock:
                rows = self._connection.execute(
                    query, (instrument_id, _date_key(end_date), limit)
                ).fetchall()
            result = [
                StoredDailyBar(
                    symbol=canonical_symbol, trade_date=_date_from_key(int(row[0])),
                    open=float(row[1]), high=float(row[2]), low=float(row[3]),
                    close=float(row[4]), volume=int(row[5]),
                    source="local_custom_index", updated_at_ms=int(row[6]),
                )
                for row in rows
            ]
        else:
            query = """
                SELECT bar.trade_date, bar.open, bar.high,
                       bar.low, bar.close, bar.volume, source.code, bar.updated_at_ms
                FROM daily_bars AS bar
                JOIN sources AS source USING (source_id)
                WHERE bar.instrument_id = ? AND bar.trade_date <= ?
                ORDER BY bar.trade_date DESC LIMIT ?
            """
            with self._lock:
                rows = self._connection.execute(
                    query, (instrument_id, _date_key(end_date), limit)
                ).fetchall()
            result = [
                StoredDailyBar(
                    symbol=canonical_symbol, trade_date=_date_from_key(int(row[0])),
                    open=float(row[1]), high=float(row[2]), low=float(row[3]),
                    close=float(row[4]), volume=int(row[5]), source=str(row[6]),
                    updated_at_ms=int(row[7]),
                )
                for row in rows
            ]
        result.reverse()
        return result

    def get_adjustment_factors(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[AdjustmentFactor]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT instrument.symbol, factor.trade_date, factor.factor
                FROM stock_adjustment_factors AS factor
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                  AND factor.trade_date BETWEEN ? AND ?
                ORDER BY factor.trade_date
                """,
                (symbol.upper(), _date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return [
            AdjustmentFactor(str(row[0]), _date_from_key(int(row[1])), float(row[2]))
            for row in rows
        ]

    def get_stock_trade_statuses(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[StockTradeStatus]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT instrument.symbol, trade.trade_date, trade.status
                FROM stock_trade_status AS trade
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                  AND trade.trade_date BETWEEN ? AND ?
                ORDER BY trade.trade_date
                """,
                (symbol.upper(), _date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return [
            StockTradeStatus(str(row[0]), _date_from_key(int(row[1])), str(row[2]))
            for row in rows
        ]

    def get_latest_daily_bar_date(self, symbol: str) -> date | None:
        kind = self.get_instrument_kind(symbol)
        if kind in {
            InstrumentKind.FUTURES_CONTRACT,
            InstrumentKind.FUTURES_CONTINUOUS,
        }:
            with self._lock:
                row = self._connection.execute(
                    """
                    SELECT max(bar.trading_day)
                    FROM futures_daily_bars AS bar
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE instrument.symbol = ? COLLATE NOCASE
                    """,
                    (symbol.upper(),),
                ).fetchone()
            return _date_from_key(int(row[0])) if row and row[0] is not None else None
        if kind is InstrumentKind.CUSTOM_INDEX:
            with self._lock:
                row = self._connection.execute(
                    """
                    SELECT max(bar.trade_date)
                    FROM custom_index_daily_bars AS bar
                    JOIN custom_indices AS custom USING (index_id)
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE instrument.symbol = ? COLLATE NOCASE
                    """,
                    (symbol.upper(),),
                ).fetchone()
            return _date_from_key(int(row[0])) if row and row[0] is not None else None
        with self._lock:
            row = self._connection.execute(
                """
                SELECT max(bar.trade_date)
                FROM daily_bars AS bar
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                """,
                (symbol.upper(),),
            ).fetchone()
        return _date_from_key(int(row[0])) if row and row[0] is not None else None

    def upsert_provisional_daily_bars(
        self, bars: Sequence[ProvisionalDailyBar]
    ) -> int:
        if not bars:
            return 0
        for bar in bars:
            DailyBar(
                bar.symbol, bar.trade_date, bar.open, bar.high, bar.low, bar.close, bar.volume
            ).validate()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        rows = [
            (
                bar.symbol.upper(), _date_key(bar.trade_date), bar.open, bar.high, bar.low,
                bar.close, bar.volume, bar.amount, bar.previous_close, bar.change_percent,
                bar.source, bar.provider_time.isoformat(), bar.received_at.isoformat(), now_ms,
            )
            for bar in bars
        ]
        with self._lock, self._transaction():
            self._connection.executemany(
                """
                INSERT INTO intraday_daily_bars(
                    symbol, trade_date, open, high, low, close, volume, amount,
                    previous_close, change_percent, source, provider_time,
                    received_at, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, trade_date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    amount = excluded.amount,
                    previous_close = excluded.previous_close,
                    change_percent = excluded.change_percent,
                    source = excluded.source,
                    provider_time = excluded.provider_time,
                    received_at = excluded.received_at,
                    updated_at_ms = excluded.updated_at_ms
                """,
                rows,
            )
        return len(rows)

    def get_latest_provisional_daily_bar(
        self, symbol: str
    ) -> ProvisionalDailyBar | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT symbol, trade_date, open, high, low, close, volume, amount,
                       previous_close, change_percent, source, provider_time, received_at
                FROM intraday_daily_bars
                WHERE symbol = ?
                ORDER BY trade_date DESC
                LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchone()
        if row is None:
            return None
        return ProvisionalDailyBar(
            symbol=str(row[0]),
            trade_date=_date_from_key(int(row[1])),
            open=float(row[2]),
            high=float(row[3]),
            low=float(row[4]),
            close=float(row[5]),
            volume=int(row[6]),
            amount=float(row[7]),
            previous_close=float(row[8]),
            change_percent=float(row[9]),
            source=str(row[10]),
            provider_time=datetime.fromisoformat(str(row[11])),
            received_at=datetime.fromisoformat(str(row[12])),
        )

    def upsert_adjustment_factors(
        self, source: str, factors: Sequence[AdjustmentFactor]
    ) -> int:
        if not factors:
            return 0
        for factor in factors:
            factor.validate()
        symbols = {item.symbol for item in factors}
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids(symbols)
            missing = symbols - instrument_ids.keys()
            if missing:
                raise ValueError("adjustment factors reference unknown instruments: " + ", ".join(sorted(missing)))
            source_id = self._source_id(source)
            before = self._connection.total_changes
            self._connection.executemany(
                """
                INSERT INTO stock_adjustment_factors(
                    instrument_id, trade_date, factor, source_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                    factor = excluded.factor,
                    source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                WHERE stock_adjustment_factors.factor IS NOT excluded.factor
                   OR stock_adjustment_factors.source_id IS NOT excluded.source_id
                """,
                (
                    (instrument_ids[item.symbol], _date_key(item.trade_date), item.factor, source_id, now_ms)
                    for item in factors
                ),
            )
            return self._connection.total_changes - before

    def upsert_stock_trade_statuses(
        self, source: str, statuses: Sequence[StockTradeStatus]
    ) -> int:
        if not statuses:
            return 0
        for item in statuses:
            item.validate()
        symbols = {item.symbol for item in statuses}
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids(symbols)
            missing = symbols - instrument_ids.keys()
            if missing:
                raise ValueError(
                    "stock trade statuses reference unknown instruments: "
                    + ", ".join(sorted(missing))
                )
            source_id = self._source_id(source)
            before = self._connection.total_changes
            self._connection.executemany(
                """
                INSERT INTO stock_trade_status(
                    instrument_id, trade_date, status, source_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                    status = excluded.status, source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                WHERE stock_trade_status.status IS NOT excluded.status
                   OR stock_trade_status.source_id IS NOT excluded.source_id
                """,
                (
                    (
                        instrument_ids[item.symbol], _date_key(item.trade_date),
                        item.status, source_id, now_ms,
                    )
                    for item in statuses
                ),
            )
            return self._connection.total_changes - before

    def search_instruments(
        self,
        query: str = "",
        kinds: set[InstrumentKind] | None = None,
        classification: str | None = None,
        source_system: str | None = None,
        family: str | None = None,
        category: str | None = None,
        exchange: str | None = None,
        futures_product: str | None = None,
        futures_lifecycle: str | None = None,
        futures_series_kind: str | None = None,
        active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 501 or offset < 0:
            raise ValueError("invalid instrument pagination")
        if classification not in {
            None, "stock", "etf", "index", "custom-index", "concept", "industry",
            "sector", "futures", "futures-product", "futures-contract",
            "futures-continuous",
        }:
            raise ValueError("invalid instrument classification")
        if futures_lifecycle and futures_lifecycle not in {
            "pending", "listed", "trading", "expired", "delivered", "delisted",
        }:
            raise ValueError("invalid futures lifecycle status")
        if futures_series_kind and futures_series_kind not in {"main", "continuous"}:
            raise ValueError("invalid futures series kind")
        futures_requested = (
            classification is not None and classification.startswith("futures")
        ) or any((futures_product, futures_lifecycle, futures_series_kind))
        if futures_requested and not self._futures_storage_ready:
            return []
        futures_min_case = (
            "WHEN selected.kind IN ('futures-contract', 'futures-continuous') THEN "
            "(SELECT min(bar.trading_day) FROM futures_daily_bars AS bar "
            "WHERE bar.instrument_id = selected.instrument_id)"
            if self._futures_storage_ready else ""
        )
        futures_max_case = (
            "WHEN selected.kind IN ('futures-contract', 'futures-continuous') THEN "
            "(SELECT max(bar.trading_day) FROM futures_daily_bars AS bar "
            "WHERE bar.instrument_id = selected.instrument_id)"
            if self._futures_storage_ready else ""
        )
        futures_count_case = (
            "WHEN selected.kind IN ('futures-contract', 'futures-continuous') THEN "
            "(SELECT count(*) FROM futures_daily_bars AS bar "
            "WHERE bar.instrument_id = selected.instrument_id)"
            if self._futures_storage_ready else ""
        )
        futures_metadata = (
            "COALESCE((SELECT product_code FROM futures_products WHERE instrument_id = selected.instrument_id), "
            "(SELECT product.product_code FROM futures_contracts AS contract JOIN futures_products AS product "
            "ON product.instrument_id = contract.product_instrument_id WHERE contract.instrument_id = selected.instrument_id), "
            "(SELECT product.product_code FROM futures_continuous_series AS series JOIN futures_products AS product "
            "ON product.instrument_id = series.product_instrument_id WHERE series.instrument_id = selected.instrument_id)), "
            "(SELECT lifecycle_status FROM futures_contracts WHERE instrument_id = selected.instrument_id), "
            "(SELECT contract_month FROM futures_contracts WHERE instrument_id = selected.instrument_id), "
            "(SELECT series_kind FROM futures_continuous_series WHERE instrument_id = selected.instrument_id), "
            "(SELECT series_variant FROM futures_continuous_series WHERE instrument_id = selected.instrument_id), "
            "(SELECT price_basis FROM futures_continuous_series WHERE instrument_id = selected.instrument_id), "
            "(SELECT rule_version FROM futures_continuous_series WHERE instrument_id = selected.instrument_id), "
            "COALESCE((SELECT multiplier FROM futures_contracts WHERE instrument_id = selected.instrument_id), "
            "(SELECT multiplier FROM futures_products WHERE instrument_id = selected.instrument_id), "
            "(SELECT product.multiplier FROM futures_continuous_series AS series JOIN futures_products AS product "
            "ON product.instrument_id = series.product_instrument_id WHERE series.instrument_id = selected.instrument_id)), "
            "COALESCE((SELECT per_unit FROM futures_contracts WHERE instrument_id = selected.instrument_id), "
            "(SELECT per_unit FROM futures_products WHERE instrument_id = selected.instrument_id), "
            "(SELECT product.per_unit FROM futures_continuous_series AS series JOIN futures_products AS product "
            "ON product.instrument_id = series.product_instrument_id WHERE series.instrument_id = selected.instrument_id)), "
            "COALESCE((SELECT trading_unit FROM futures_contracts WHERE instrument_id = selected.instrument_id), "
            "(SELECT trading_unit FROM futures_products WHERE instrument_id = selected.instrument_id), "
            "(SELECT product.trading_unit FROM futures_continuous_series AS series JOIN futures_products AS product "
            "ON product.instrument_id = series.product_instrument_id WHERE series.instrument_id = selected.instrument_id)), "
            "COALESCE((SELECT quote_unit FROM futures_contracts WHERE instrument_id = selected.instrument_id), "
            "(SELECT quote_unit FROM futures_products WHERE instrument_id = selected.instrument_id), "
            "(SELECT product.quote_unit FROM futures_continuous_series AS series JOIN futures_products AS product "
            "ON product.instrument_id = series.product_instrument_id WHERE series.instrument_id = selected.instrument_id))"
            if self._futures_storage_ready else "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL"
        )
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
        if exchange:
            clauses.append("instrument.exchange = ?")
            parameters.append(exchange.upper())
        if active is not None:
            clauses.append("instrument.active = ?")
            parameters.append(int(active))
        if futures_product:
            product = futures_product.strip().upper()
            clauses.append(
                "(EXISTS (SELECT 1 FROM futures_products AS product "
                "WHERE product.instrument_id = instrument.instrument_id "
                "AND product.product_code = ?) OR EXISTS ("
                "SELECT 1 FROM futures_contracts AS contract "
                "JOIN futures_products AS product "
                "ON product.instrument_id = contract.product_instrument_id "
                "WHERE contract.instrument_id = instrument.instrument_id "
                "AND product.product_code = ?) OR EXISTS ("
                "SELECT 1 FROM futures_continuous_series AS series "
                "JOIN futures_products AS product "
                "ON product.instrument_id = series.product_instrument_id "
                "WHERE series.instrument_id = instrument.instrument_id "
                "AND product.product_code = ?))"
            )
            parameters.extend((product, product, product))
        if futures_lifecycle:
            clauses.append(
                "EXISTS (SELECT 1 FROM futures_contracts AS contract "
                "WHERE contract.instrument_id = instrument.instrument_id "
                "AND contract.lifecycle_status = ?)"
            )
            parameters.append(futures_lifecycle)
        if futures_series_kind:
            clauses.append(
                "EXISTS (SELECT 1 FROM futures_continuous_series AS series "
                "WHERE series.instrument_id = instrument.instrument_id "
                "AND series.series_kind = ?)"
            )
            parameters.append(futures_series_kind)
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
                       CASE WHEN selected.kind = 'custom-index' THEN
                           (SELECT min(bar.trade_date) FROM custom_index_daily_bars AS bar
                            JOIN custom_indices AS custom USING (index_id)
                            WHERE custom.instrument_id = selected.instrument_id)
                       {futures_min_case}
                       ELSE (SELECT min(trade_date) FROM daily_bars
                             WHERE instrument_id = selected.instrument_id) END,
                       CASE WHEN selected.kind = 'custom-index' THEN
                           (SELECT max(bar.trade_date) FROM custom_index_daily_bars AS bar
                            JOIN custom_indices AS custom USING (index_id)
                            WHERE custom.instrument_id = selected.instrument_id)
                       {futures_max_case}
                       ELSE (SELECT max(trade_date) FROM daily_bars
                             WHERE instrument_id = selected.instrument_id) END,
                       CASE WHEN selected.kind = 'custom-index' THEN
                           (SELECT count(*) FROM custom_index_daily_bars AS bar
                            JOIN custom_indices AS custom USING (index_id)
                            WHERE custom.instrument_id = selected.instrument_id)
                       {futures_count_case}
                       ELSE (SELECT count(*) FROM daily_bars
                             WHERE instrument_id = selected.instrument_id) END,
                       {futures_metadata}
                FROM selected
                ORDER BY selected.kind, selected.name, selected.symbol
                """,
                parameters,
            ).fetchall()
        return [_instrument_row(row) for row in rows]

    def get_instrument_summary(self, symbol: str) -> dict[str, object] | None:
        futures_min_case = (
            "WHEN instrument.kind IN ('futures-contract', 'futures-continuous') THEN "
            "(SELECT min(bar.trading_day) FROM futures_daily_bars AS bar "
            "WHERE bar.instrument_id = instrument.instrument_id)"
            if self._futures_storage_ready else ""
        )
        futures_max_case = (
            "WHEN instrument.kind IN ('futures-contract', 'futures-continuous') THEN "
            "(SELECT max(bar.trading_day) FROM futures_daily_bars AS bar "
            "WHERE bar.instrument_id = instrument.instrument_id)"
            if self._futures_storage_ready else ""
        )
        futures_count_case = (
            "WHEN instrument.kind IN ('futures-contract', 'futures-continuous') THEN "
            "(SELECT count(*) FROM futures_daily_bars AS bar "
            "WHERE bar.instrument_id = instrument.instrument_id)"
            if self._futures_storage_ready else ""
        )
        futures_metadata = (
            "COALESCE((SELECT product_code FROM futures_products WHERE instrument_id = instrument.instrument_id), "
            "(SELECT product.product_code FROM futures_contracts AS contract JOIN futures_products AS product "
            "ON product.instrument_id = contract.product_instrument_id WHERE contract.instrument_id = instrument.instrument_id), "
            "(SELECT product.product_code FROM futures_continuous_series AS series JOIN futures_products AS product "
            "ON product.instrument_id = series.product_instrument_id WHERE series.instrument_id = instrument.instrument_id)), "
            "(SELECT lifecycle_status FROM futures_contracts WHERE instrument_id = instrument.instrument_id), "
            "(SELECT contract_month FROM futures_contracts WHERE instrument_id = instrument.instrument_id), "
            "(SELECT series_kind FROM futures_continuous_series WHERE instrument_id = instrument.instrument_id), "
            "(SELECT series_variant FROM futures_continuous_series WHERE instrument_id = instrument.instrument_id), "
            "(SELECT price_basis FROM futures_continuous_series WHERE instrument_id = instrument.instrument_id), "
            "(SELECT rule_version FROM futures_continuous_series WHERE instrument_id = instrument.instrument_id), "
            "COALESCE((SELECT multiplier FROM futures_contracts WHERE instrument_id = instrument.instrument_id), "
            "(SELECT multiplier FROM futures_products WHERE instrument_id = instrument.instrument_id), "
            "(SELECT product.multiplier FROM futures_continuous_series AS series JOIN futures_products AS product "
            "ON product.instrument_id = series.product_instrument_id WHERE series.instrument_id = instrument.instrument_id)), "
            "COALESCE((SELECT per_unit FROM futures_contracts WHERE instrument_id = instrument.instrument_id), "
            "(SELECT per_unit FROM futures_products WHERE instrument_id = instrument.instrument_id), "
            "(SELECT product.per_unit FROM futures_continuous_series AS series JOIN futures_products AS product "
            "ON product.instrument_id = series.product_instrument_id WHERE series.instrument_id = instrument.instrument_id)), "
            "COALESCE((SELECT trading_unit FROM futures_contracts WHERE instrument_id = instrument.instrument_id), "
            "(SELECT trading_unit FROM futures_products WHERE instrument_id = instrument.instrument_id), "
            "(SELECT product.trading_unit FROM futures_continuous_series AS series JOIN futures_products AS product "
            "ON product.instrument_id = series.product_instrument_id WHERE series.instrument_id = instrument.instrument_id)), "
            "COALESCE((SELECT quote_unit FROM futures_contracts WHERE instrument_id = instrument.instrument_id), "
            "(SELECT quote_unit FROM futures_products WHERE instrument_id = instrument.instrument_id), "
            "(SELECT product.quote_unit FROM futures_continuous_series AS series JOIN futures_products AS product "
            "ON product.instrument_id = series.product_instrument_id WHERE series.instrument_id = instrument.instrument_id))"
            if self._futures_storage_ready else "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL"
        )
        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT instrument.symbol, instrument.name, instrument.kind,
                       instrument.exchange, instrument.active,
                       catalog.source_system, catalog.family, catalog.category,
                       CASE WHEN instrument.kind = 'custom-index' THEN
                           (SELECT min(bar.trade_date) FROM custom_index_daily_bars AS bar
                            JOIN custom_indices AS custom USING (index_id)
                            WHERE custom.instrument_id = instrument.instrument_id)
                       {futures_min_case}
                       ELSE (SELECT min(trade_date) FROM daily_bars
                             WHERE instrument_id = instrument.instrument_id) END,
                       CASE WHEN instrument.kind = 'custom-index' THEN
                           (SELECT max(bar.trade_date) FROM custom_index_daily_bars AS bar
                            JOIN custom_indices AS custom USING (index_id)
                            WHERE custom.instrument_id = instrument.instrument_id)
                       {futures_max_case}
                       ELSE (SELECT max(trade_date) FROM daily_bars
                             WHERE instrument_id = instrument.instrument_id) END,
                       CASE WHEN instrument.kind = 'custom-index' THEN
                           (SELECT count(*) FROM custom_index_daily_bars AS bar
                            JOIN custom_indices AS custom USING (index_id)
                            WHERE custom.instrument_id = instrument.instrument_id)
                       {futures_count_case}
                       ELSE (SELECT count(*) FROM daily_bars
                             WHERE instrument_id = instrument.instrument_id) END,
                       {futures_metadata}
                FROM instruments AS instrument
                LEFT JOIN instrument_catalog_entries AS catalog USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                ORDER BY catalog.catalog_source_id
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
            if row is None:
                return None
            instrument_id = int(
                self._connection.execute(
                    "SELECT instrument_id FROM instruments WHERE symbol = ? COLLATE NOCASE",
                    (symbol,),
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

    def list_symbol_analysis_contexts(
        self, member_symbol: str, limit: int = 12
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid analysis-context limit")
        boards = [
            {**item, "kind": "board"}
            for item in self.list_symbol_boards(member_symbol, limit=limit)
        ]
        with self._lock:
            custom_rows = self._connection.execute(
                """
                SELECT context.symbol, context.name, revision.effective_from,
                       custom.status
                FROM custom_index_revision_members AS member
                JOIN instruments AS selected
                  ON selected.instrument_id = member.instrument_id
                JOIN custom_index_revisions AS revision
                  ON revision.revision_id = member.revision_id
                JOIN custom_indices AS custom
                  ON custom.index_id = revision.index_id
                JOIN instruments AS context
                  ON context.instrument_id = custom.instrument_id
                WHERE selected.symbol = ? COLLATE NOCASE
                  AND revision.revision_number = (
                      SELECT max(latest.revision_number)
                      FROM custom_index_revisions AS latest
                      WHERE latest.index_id = revision.index_id
                  )
                ORDER BY context.name, context.symbol
                LIMIT ?
                """,
                (member_symbol, limit),
            ).fetchall()
        custom_indexes = [
            {
                "symbol": str(row[0]),
                "name": str(row[1]),
                "source_system": "local_custom_index",
                "family": "custom-index",
                "category": "custom-index",
                "source": "local_custom_index",
                "kind": "custom-index",
                "effective_from": _date_from_key(int(row[2])),
                "status": str(row[3]),
            }
            for row in custom_rows
        ]
        combined = [*boards, *custom_indexes]
        seen: set[str] = set()
        result: list[dict[str, object]] = []
        for item in combined:
            symbol = str(item["symbol"])
            if symbol in seen:
                continue
            seen.add(symbol)
            result.append(item)
            if len(result) >= limit:
                break
        return result

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

    def _canonical_instrument_identity(self, symbol: str) -> tuple[str, int] | None:
        row = self._connection.execute(
            "SELECT symbol, instrument_id FROM instruments WHERE symbol = ? COLLATE NOCASE",
            (symbol.strip(),),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), int(row[1])

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
        return Transaction(self._connection, self._writer_lock)
