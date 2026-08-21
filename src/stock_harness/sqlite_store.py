"""SQLite hot store optimized for daily batch updates and symbol-range reads."""

from __future__ import annotations

import sqlite3
import threading
import time
import json
import logging
import hashlib
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
    CoverageGap,
    DailyBar,
    Instrument,
    InstrumentCoverage,
    InstrumentKind,
    EtfHolding,
    FuturesContinuousSeries,
    FuturesBarState,
    FuturesCalendarDay,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesPriceBasis,
    FuturesProduct,
    FuturesRollMapping,
    FuturesSeriesKind,
    FuturesSyncState,
    FuturesUpdateReceipt,
    MarketSnapshot,
    ProviderIncident,
    ProvisionalDailyBar,
    RepairJob,
    StoredDailyBar,
    StockTradeStatus,
    SymbolSyncState,
    ValidationResult,
    WriteStats,
)
from stock_harness.custom_index import (
    CALCULATION_VERSION,
    ConstituentInput,
    base_bar,
    calculate_bar,
    normalize_members,
)
from stock_harness.futures_continuous import FuturesContinuousBuild
from stock_harness.analysis_results import (
    AnalysisNamespace,
    AnalysisRunRecord,
    AnalysisRunSpec,
    AnalysisRunStatus,
    ClaimedAnalysisTarget,
    GeneratedAnalysisItem,
    GeneratedAnalysisTarget,
    validate_items,
)
from stock_harness.search_terms import matches_name_or_pinyin, pinyin_search_aliases
from stock_harness.trend_reviews import TrendReviewDraftSpec, TrendReviewLabel, TrendReviewStatus
from stock_harness.sqlite_mapping import (
    _date_from_key,
    _date_key,
    _instrument_classification_clause,
    _instrument_row,
    _snapshot_hash,
    _source_profile,
)
from stock_harness.sqlite_runtime import InterprocessWriterLock, ThreadOnlyWriterLock, Transaction


from stock_harness.sqlite_schema import (
    FUTURES_SCHEMA as _FUTURES_SCHEMA,
    FUTURES_SCHEMA_VERSION as _FUTURES_SCHEMA_VERSION,
    SCHEMA as _SCHEMA,
)


LOGGER = logging.getLogger(__name__)

_CUSTOM_GROUP_ROLES = {
    "", "sentiment_anchor", "liquidity_anchor", "bellwether",
    "core_identity", "lagging_expansion",
}

_TREND_REVIEW_SELECT = """
SELECT review.review_id, instrument.symbol, review.schema_version,
       review.dataset_version, review.timeframe, review.horizon,
       review.interval_start, review.interval_end, review.as_of_date,
       review.input_digest, review.algorithm_version, review.config_version,
       review.settings_json, review.classification, review.review_status, review.tags_json,
       review.labels_json, review.expected_json, review.rationale,
       review.sources_json, review.revision,
       review.created_at_ms, review.updated_at_ms
FROM trend_review_cases AS review
JOIN instruments AS instrument USING (instrument_id)
"""


def _custom_group_role(value: object) -> str:
    role = str(value).strip()
    if role not in _CUSTOM_GROUP_ROLES:
        raise ValueError(f"invalid custom group member role: {role}")
    return role


def _review_labels_json(labels: Sequence[TrendReviewLabel]) -> str:
    return json.dumps([
        {
            "item_id": label.item_id,
            "item_type": label.item_type,
            "decision": label.decision.value,
            "payload": label.payload,
            "rationale": label.rationale,
        }
        for label in labels
    ], ensure_ascii=False, sort_keys=True)


def _trend_review_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "review_id": str(row[0]),
        "symbol": str(row[1]),
        "schema_version": str(row[2]),
        "dataset_version": str(row[3]),
        "timeframe": str(row[4]),
        "horizon": str(row[5]),
        "interval_start": _date_from_key(int(row[6])),
        "interval_end": _date_from_key(int(row[7])),
        "as_of_date": _date_from_key(int(row[8])),
        "input_digest": bytes(row[9]),
        "algorithm_version": str(row[10]),
        "config_version": str(row[11]),
        "settings": json.loads(str(row[12])),
        "classification": str(row[13]),
        "review_status": str(row[14]),
        "tags": json.loads(str(row[15])),
        "labels": json.loads(str(row[16])),
        "expected": json.loads(str(row[17])),
        "rationale": str(row[18]),
        "sources": json.loads(str(row[19])),
        "revision": int(row[20]),
        "created_at_ms": int(row[21]),
        "updated_at_ms": int(row[22]),
    }


def _futures_daily_row(
    row: sqlite3.Row,
    state: FuturesBarState,
) -> FuturesDailyBar:
    return FuturesDailyBar(
        symbol=str(row[0]),
        trading_day=_date_from_key(int(row[1])),
        provider_date=_date_from_key(int(row[2])),
        open=float(row[3]), high=float(row[4]), low=float(row[5]), close=float(row[6]),
        previous_close=float(row[7]) if row[7] is not None else None,
        settlement=float(row[8]) if row[8] is not None else None,
        previous_settlement=float(row[9]) if row[9] is not None else None,
        volume_contracts=int(row[10]),
        amount=float(row[11]) if row[11] is not None else None,
        open_interest_contracts=float(row[12]) if row[12] is not None else None,
        open_interest_change_contracts=(
            float(row[13]) if row[13] is not None else None
        ),
        delivery_settlement=float(row[14]) if row[14] is not None else None,
        source=str(row[15]),
        state=state,
        provider_time=(
            datetime.fromisoformat(str(row[18]))
            if state is FuturesBarState.PROVISIONAL else None
        ),
        mapped_contract_symbol=str(row[16]) if row[16] is not None else None,
        roll_event=bool(row[17]),
        stale=bool(row[19]) if len(row) > 19 else False,
    )


def _futures_sync_row(row: sqlite3.Row) -> FuturesSyncState:
    return FuturesSyncState(
        source=str(row[0]), dataset=str(row[1]), scope=str(row[2]),
        identity=str(row[3]), covered_from=_date_from_key(int(row[4])),
        covered_through=_date_from_key(int(row[5])), last_batch_rows=int(row[6]),
        updated_at_ms=int(row[7]),
    )


def _futures_receipt_row(row: sqlite3.Row) -> FuturesUpdateReceipt:
    return FuturesUpdateReceipt(
        source=str(row[0]), dataset=str(row[1]), scope=str(row[2]),
        effective_date=_date_from_key(int(row[3])), row_count=int(row[4]),
        payload_hash=bytes(row[5]), status=str(row[6]), message=str(row[7]),
        updated_at_ms=int(row[8]),
    )


def _futures_mapping_hash(mappings: Sequence[FuturesRollMapping]) -> bytes:
    payload = [
        {
            "series": item.series_symbol,
            "effective_from": item.effective_from.isoformat(),
            "contract": item.contract_symbol,
        }
        for item in sorted(mappings, key=lambda value: value.effective_from)
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).digest()


def _require_sync_text(value: str, label: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"futures sync {label} is required")


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
                    "futures_schema_migration_failed version=%s error=%s",
                    _FUTURES_SCHEMA_VERSION,
                    self._futures_storage_error,
                    exc_info=True,
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

    def upsert_futures_catalog(
        self,
        source: str,
        products: Sequence[FuturesProduct],
        contracts: Sequence[FuturesContract],
        continuous_series: Sequence[FuturesContinuousSeries],
    ) -> dict[str, int]:
        self._require_futures_storage()
        for item in products:
            item.validate()
        for item in contracts:
            item.validate()
        for item in continuous_series:
            item.validate()
        product_symbols = {item.symbol for item in products}
        referenced_products = {
            item.product_symbol for item in (*contracts, *continuous_series)
        }
        missing_products = referenced_products - product_symbols
        if missing_products:
            raise ValueError(
                "futures catalog has missing products: "
                + ", ".join(sorted(missing_products))
            )
        instruments = [
            *(
                Instrument(
                    item.symbol, item.display_name, InstrumentKind.FUTURES_PRODUCT,
                    item.exchange.value, item.active,
                )
                for item in products
            ),
            *(
                Instrument(
                    item.symbol, item.display_name, InstrumentKind.FUTURES_CONTRACT,
                    item.exchange.value,
                    item.lifecycle_status in {
                        FuturesLifecycleStatus.PENDING,
                        FuturesLifecycleStatus.LISTED,
                        FuturesLifecycleStatus.TRADING,
                    },
                )
                for item in contracts
            ),
            *(
                Instrument(
                    item.symbol, item.display_name, InstrumentKind.FUTURES_CONTINUOUS,
                    item.exchange.value, item.active,
                )
                for item in continuous_series
            ),
        ]
        if len({item.symbol for item in instruments}) != len(instruments):
            raise ValueError("futures catalog contains duplicate canonical symbols")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
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
                """,
                (
                    (item.symbol, item.name, item.kind.value, item.exchange, int(item.active))
                    for item in instruments
                ),
            )
            changed = [
                item for item in instruments
                if existing_names.get(item.symbol) != item.name
            ]
            self._replace_pinyin_aliases_locked(changed)
            instrument_ids = self._instrument_ids({item.symbol for item in instruments})
            self._connection.executemany(
                """
                INSERT INTO futures_products(
                    instrument_id, product_code, exchange, multiplier, per_unit,
                    trading_unit, quote_unit, source_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id) DO UPDATE SET
                    product_code = excluded.product_code,
                    exchange = excluded.exchange,
                    multiplier = excluded.multiplier,
                    per_unit = excluded.per_unit,
                    trading_unit = excluded.trading_unit,
                    quote_unit = excluded.quote_unit,
                    source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    (
                        instrument_ids[item.symbol], item.product_code,
                        item.exchange.value, item.multiplier, item.per_unit,
                        item.trading_unit, item.quote_unit, source_id, now_ms,
                    )
                    for item in products
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO futures_contracts(
                    instrument_id, product_instrument_id, provider_symbol,
                    contract_month, listed_on, last_trading_date, delivery_date,
                    multiplier, per_unit, trading_unit, quote_unit,
                    lifecycle_status, source_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id) DO UPDATE SET
                    product_instrument_id = excluded.product_instrument_id,
                    provider_symbol = excluded.provider_symbol,
                    contract_month = excluded.contract_month,
                    listed_on = excluded.listed_on,
                    last_trading_date = excluded.last_trading_date,
                    delivery_date = excluded.delivery_date,
                    multiplier = excluded.multiplier,
                    per_unit = excluded.per_unit,
                    trading_unit = excluded.trading_unit,
                    quote_unit = excluded.quote_unit,
                    lifecycle_status = excluded.lifecycle_status,
                    source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    (
                        instrument_ids[item.symbol], instrument_ids[item.product_symbol],
                        item.provider_symbol, item.contract_month,
                        _date_key(item.listed_on), _date_key(item.last_trading_date),
                        _date_key(item.delivery_date) if item.delivery_date else None,
                        item.multiplier, item.per_unit, item.trading_unit,
                        item.quote_unit, item.lifecycle_status.value, source_id, now_ms,
                    )
                    for item in contracts
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO futures_continuous_series(
                    instrument_id, product_instrument_id, provider_symbol,
                    series_kind, series_variant, price_basis, rule_version,
                    source_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id) DO UPDATE SET
                    product_instrument_id = excluded.product_instrument_id,
                    provider_symbol = excluded.provider_symbol,
                    series_kind = excluded.series_kind,
                    series_variant = excluded.series_variant,
                    price_basis = excluded.price_basis,
                    rule_version = excluded.rule_version,
                    source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    (
                        instrument_ids[item.symbol], instrument_ids[item.product_symbol],
                        item.provider_symbol, item.series_kind.value,
                        item.series_variant, item.price_basis.value,
                        item.rule_version, source_id, now_ms,
                    )
                    for item in continuous_series
                ),
            )
        return {
            "products": len(products),
            "contracts": len(contracts),
            "continuous_series": len(continuous_series),
        }

    def list_futures_products(self) -> list[FuturesProduct]:
        self._require_futures_storage()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT instrument.symbol, product.product_code, instrument.name,
                       product.exchange, product.multiplier, product.per_unit,
                       product.trading_unit, product.quote_unit, instrument.active
                FROM futures_products AS product
                JOIN instruments AS instrument USING (instrument_id)
                ORDER BY product.exchange, product.product_code
                """
            ).fetchall()
        return [
            FuturesProduct(
                symbol=str(row[0]), product_code=str(row[1]),
                display_name=str(row[2]), exchange=FuturesExchange(str(row[3])),
                multiplier=float(row[4]) if row[4] is not None else None,
                per_unit=float(row[5]) if row[5] is not None else None,
                trading_unit=str(row[6]), quote_unit=str(row[7]), active=bool(row[8]),
            )
            for row in rows
        ]

    def list_futures_contracts(self) -> list[FuturesContract]:
        self._require_futures_storage()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT instrument.symbol, contract.provider_symbol,
                       product_instrument.symbol, instrument.name, contract.contract_month,
                       contract.listed_on, contract.last_trading_date,
                       contract.delivery_date, contract.multiplier, contract.per_unit,
                       contract.trading_unit, contract.quote_unit,
                       contract.lifecycle_status, instrument.exchange
                FROM futures_contracts AS contract
                JOIN instruments AS instrument USING (instrument_id)
                JOIN instruments AS product_instrument
                  ON product_instrument.instrument_id = contract.product_instrument_id
                ORDER BY instrument.symbol
                """
            ).fetchall()
        return [
            FuturesContract(
                symbol=str(row[0]), provider_symbol=str(row[1]),
                product_symbol=str(row[2]), display_name=str(row[3]),
                exchange=FuturesExchange(str(row[13])), contract_month=str(row[4]),
                listed_on=_date_from_key(int(row[5])),
                last_trading_date=_date_from_key(int(row[6])),
                delivery_date=(
                    _date_from_key(int(row[7])) if row[7] is not None else None
                ),
                multiplier=float(row[8]) if row[8] is not None else None,
                per_unit=float(row[9]) if row[9] is not None else None,
                trading_unit=str(row[10]), quote_unit=str(row[11]),
                lifecycle_status=FuturesLifecycleStatus(str(row[12])),
            )
            for row in rows
        ]

    def get_futures_contracts(
        self, symbols: Sequence[str]
    ) -> list[FuturesContract]:
        self._require_futures_storage()
        normalized = tuple(sorted({item.strip().upper() for item in symbols if item.strip()}))
        if not normalized:
            return []
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT instrument.symbol, contract.provider_symbol,
                       product_instrument.symbol, instrument.name,
                       contract.contract_month, contract.listed_on,
                       contract.last_trading_date, contract.delivery_date,
                       contract.multiplier, contract.per_unit,
                       contract.trading_unit, contract.quote_unit,
                       contract.lifecycle_status, instrument.exchange
                FROM futures_contracts AS contract
                JOIN instruments AS instrument USING (instrument_id)
                JOIN instruments AS product_instrument
                  ON product_instrument.instrument_id = contract.product_instrument_id
                WHERE instrument.symbol IN (
                """ + ",".join("?" for _ in normalized) + ") ORDER BY instrument.symbol",
                normalized,
            ).fetchall()
        return [
            FuturesContract(
                symbol=str(row[0]), provider_symbol=str(row[1]),
                product_symbol=str(row[2]), display_name=str(row[3]),
                exchange=FuturesExchange(str(row[13])), contract_month=str(row[4]),
                listed_on=_date_from_key(int(row[5])),
                last_trading_date=_date_from_key(int(row[6])),
                delivery_date=(
                    _date_from_key(int(row[7])) if row[7] is not None else None
                ),
                multiplier=float(row[8]) if row[8] is not None else None,
                per_unit=float(row[9]) if row[9] is not None else None,
                trading_unit=str(row[10]), quote_unit=str(row[11]),
                lifecycle_status=FuturesLifecycleStatus(str(row[12])),
            )
            for row in rows
        ]

    def list_futures_continuous_series(self) -> list[FuturesContinuousSeries]:
        self._require_futures_storage()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT instrument.symbol, series.provider_symbol,
                       product_instrument.symbol, instrument.name,
                       instrument.exchange, series.series_kind,
                       series.series_variant, series.price_basis,
                       series.rule_version
                FROM futures_continuous_series AS series
                JOIN instruments AS instrument USING (instrument_id)
                JOIN instruments AS product_instrument
                  ON product_instrument.instrument_id = series.product_instrument_id
                ORDER BY instrument.exchange, instrument.symbol
                """
            ).fetchall()
        return [
            FuturesContinuousSeries(
                symbol=str(row[0]), provider_symbol=str(row[1]),
                product_symbol=str(row[2]), display_name=str(row[3]),
                exchange=FuturesExchange(str(row[4])),
                series_kind=FuturesSeriesKind(str(row[5])),
                series_variant=str(row[6]),
                price_basis=FuturesPriceBasis(str(row[7])),
                rule_version=str(row[8]),
            )
            for row in rows
        ]

    def resolve_futures_contract_references(
        self,
        source: str,
        references: Sequence[str],
        trading_day: date,
    ) -> dict[str, str | None]:
        self._require_futures_storage()
        normalized = tuple(sorted({item.strip() for item in references if item.strip()}))
        if not normalized:
            return {}
        with self._lock:
            kinds = {
                str(row[0]): str(row[1])
                for row in self._connection.execute(
                    "SELECT symbol, kind FROM instruments WHERE symbol IN ("
                    + ",".join("?" for _ in normalized) + ")",
                    normalized,
                )
            }
            result: dict[str, str | None] = {}
            for symbol in normalized:
                kind = kinds.get(symbol)
                if kind == InstrumentKind.FUTURES_CONTRACT.value:
                    result[symbol] = symbol
                    continue
                if kind != InstrumentKind.FUTURES_CONTINUOUS.value:
                    result[symbol] = None
                    continue
                row = self._connection.execute(
                    """
                    SELECT contract.symbol
                    FROM futures_roll_mappings AS mapping
                    JOIN sources AS source USING (source_id)
                    JOIN instruments AS series
                      ON series.instrument_id = mapping.series_instrument_id
                    JOIN instruments AS contract
                      ON contract.instrument_id = mapping.contract_instrument_id
                    WHERE source.code = ? AND series.symbol = ?
                      AND mapping.effective_from <= ?
                    ORDER BY mapping.effective_from DESC LIMIT 1
                    """,
                    (source, symbol, _date_key(trading_day)),
                ).fetchone()
                result[symbol] = str(row[0]) if row is not None else None
        return result

    def next_futures_open_day(
        self,
        source: str,
        exchange: FuturesExchange,
        on_or_after: date,
    ) -> date | None:
        self._require_futures_storage()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT calendar.calendar_date
                FROM futures_exchange_calendar AS calendar
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND calendar.exchange = ?
                  AND calendar.is_open = 1 AND calendar.calendar_date >= ?
                ORDER BY calendar.calendar_date LIMIT 1
                """,
                (source, exchange.value, _date_key(on_or_after)),
            ).fetchone()
        return _date_from_key(int(row[0])) if row is not None else None

    def upsert_futures_daily_bars(
        self,
        source: str,
        bars: Sequence[FuturesDailyBar],
    ) -> WriteStats:
        self._require_futures_storage()
        started = time.perf_counter()
        if not bars:
            return WriteStats(0, 0, 0, 0.0)
        if len(bars) > 2_000:
            raise ValueError("futures daily batch exceeds 2000 rows")
        identities = {(item.symbol, item.trading_day) for item in bars}
        if len(identities) != len(bars):
            raise ValueError("futures daily batch contains duplicate symbol/date rows")
        for item in bars:
            item.validate()
            if item.state is not FuturesBarState.FINAL:
                raise ValueError("canonical futures daily storage accepts final bars only")
            if item.source != source:
                raise ValueError("futures daily bars must share the requested source")
        symbols = {item.symbol for item in bars}
        symbols.update(
            item.mapped_contract_symbol for item in bars
            if item.mapped_contract_symbol is not None
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids(symbols)
            missing = symbols - instrument_ids.keys()
            if missing:
                raise ValueError(
                    "unknown futures daily instruments: " + ", ".join(sorted(missing))
                )
            kinds = {
                str(row[0]): str(row[1])
                for row in self._connection.execute(
                    "SELECT symbol, kind FROM instruments WHERE symbol IN ("
                    + ",".join("?" for _ in symbols)
                    + ")",
                    sorted(symbols),
                )
            }
            invalid_targets = [
                item.symbol for item in bars
                if kinds[item.symbol] not in {
                    InstrumentKind.FUTURES_CONTRACT.value,
                    InstrumentKind.FUTURES_CONTINUOUS.value,
                }
            ]
            invalid_mapped = [
                item.mapped_contract_symbol for item in bars
                if item.mapped_contract_symbol is not None
                and kinds[item.mapped_contract_symbol]
                != InstrumentKind.FUTURES_CONTRACT.value
            ]
            if invalid_targets or invalid_mapped:
                raise ValueError("futures daily storage received invalid instrument kinds")
            source_id = self._source_id(source)
            before = self._connection.total_changes
            self._connection.executemany(
                """
                INSERT INTO futures_daily_bars(
                    instrument_id, trading_day, provider_date, open, high, low,
                    close, previous_close, settlement, previous_settlement,
                    volume_contracts, amount_cny, open_interest_contracts,
                    open_interest_change_contracts, delivery_settlement,
                    mapped_contract_instrument_id, roll_event, source_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, trading_day) DO UPDATE SET
                    provider_date = excluded.provider_date,
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    previous_close = excluded.previous_close,
                    settlement = excluded.settlement,
                    previous_settlement = excluded.previous_settlement,
                    volume_contracts = excluded.volume_contracts,
                    amount_cny = excluded.amount_cny,
                    open_interest_contracts = excluded.open_interest_contracts,
                    open_interest_change_contracts = excluded.open_interest_change_contracts,
                    delivery_settlement = excluded.delivery_settlement,
                    mapped_contract_instrument_id = excluded.mapped_contract_instrument_id,
                    roll_event = excluded.roll_event,
                    source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                WHERE provider_date IS NOT excluded.provider_date
                   OR open IS NOT excluded.open OR high IS NOT excluded.high
                   OR low IS NOT excluded.low OR close IS NOT excluded.close
                   OR previous_close IS NOT excluded.previous_close
                   OR settlement IS NOT excluded.settlement
                   OR previous_settlement IS NOT excluded.previous_settlement
                   OR volume_contracts IS NOT excluded.volume_contracts
                   OR amount_cny IS NOT excluded.amount_cny
                   OR open_interest_contracts IS NOT excluded.open_interest_contracts
                   OR open_interest_change_contracts IS NOT excluded.open_interest_change_contracts
                   OR delivery_settlement IS NOT excluded.delivery_settlement
                   OR mapped_contract_instrument_id IS NOT excluded.mapped_contract_instrument_id
                   OR roll_event IS NOT excluded.roll_event
                   OR source_id IS NOT excluded.source_id
                """,
                (
                    (
                        instrument_ids[item.symbol], _date_key(item.trading_day),
                        _date_key(item.provider_date), item.open, item.high, item.low,
                        item.close, item.previous_close, item.settlement,
                        item.previous_settlement, item.volume_contracts, item.amount,
                        item.open_interest_contracts,
                        item.open_interest_change_contracts,
                        item.delivery_settlement,
                        (
                            instrument_ids[item.mapped_contract_symbol]
                            if item.mapped_contract_symbol else None
                        ),
                        int(item.roll_event), source_id, now_ms,
                    )
                    for item in bars
                ),
            )
            changed = self._connection.total_changes - before
            self._connection.execute(
                """
                UPDATE futures_provisional_daily_bars
                SET takeover_state = 'canonical-taken-over', updated_at_ms = ?
                WHERE takeover_state = 'active'
                  AND EXISTS (
                      SELECT 1 FROM futures_daily_bars AS final
                      WHERE final.instrument_id = futures_provisional_daily_bars.instrument_id
                        AND final.trading_day = futures_provisional_daily_bars.trading_day
                  )
                """,
                (now_ms,),
            )
        elapsed_ms = (time.perf_counter() - started) * 1000
        return WriteStats(len(bars), changed, len(bars) - changed, elapsed_ms)

    def list_futures_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[FuturesDailyBar]:
        self._require_futures_storage()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT instrument.symbol, bar.trading_day, bar.provider_date,
                       bar.open, bar.high, bar.low, bar.close, bar.previous_close,
                       bar.settlement, bar.previous_settlement, bar.volume_contracts,
                       bar.amount_cny, bar.open_interest_contracts,
                       bar.open_interest_change_contracts, bar.delivery_settlement,
                       source.code, mapped.symbol, bar.roll_event
                FROM futures_daily_bars AS bar
                JOIN instruments AS instrument USING (instrument_id)
                JOIN sources AS source USING (source_id)
                LEFT JOIN instruments AS mapped
                  ON mapped.instrument_id = bar.mapped_contract_instrument_id
                WHERE instrument.symbol = ?
                  AND bar.trading_day BETWEEN ? AND ?
                ORDER BY bar.trading_day
                """,
                (symbol, _date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return [_futures_daily_row(row, FuturesBarState.FINAL) for row in rows]

    def upsert_futures_calendar(
        self,
        source: str,
        days: Sequence[FuturesCalendarDay],
    ) -> int:
        self._require_futures_storage()
        for item in days:
            item.validate()
        identities = {(item.exchange, item.calendar_date) for item in days}
        if len(identities) != len(days):
            raise ValueError("futures calendar batch contains duplicate exchange/date rows")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            self._connection.executemany(
                """
                INSERT INTO futures_exchange_calendar(
                    source_id, exchange, calendar_date, is_open,
                    previous_trading_day, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, exchange, calendar_date) DO UPDATE SET
                    is_open = excluded.is_open,
                    previous_trading_day = excluded.previous_trading_day,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    (
                        source_id, item.exchange.value, _date_key(item.calendar_date),
                        int(item.is_open),
                        (
                            _date_key(item.previous_trading_day)
                            if item.previous_trading_day else None
                        ),
                        now_ms,
                    )
                    for item in days
                ),
            )
        return len(days)

    def list_futures_calendar(
        self,
        source: str,
        exchange: FuturesExchange,
        start_date: date,
        end_date: date,
    ) -> list[FuturesCalendarDay]:
        self._require_futures_storage()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT calendar.calendar_date, calendar.is_open,
                       calendar.previous_trading_day
                FROM futures_exchange_calendar AS calendar
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND calendar.exchange = ?
                  AND calendar.calendar_date BETWEEN ? AND ?
                ORDER BY calendar.calendar_date
                """,
                (source, exchange.value, _date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return [
            FuturesCalendarDay(
                exchange=exchange,
                calendar_date=_date_from_key(int(row[0])),
                is_open=bool(row[1]),
                previous_trading_day=(
                    _date_from_key(int(row[2])) if row[2] is not None else None
                ),
            )
            for row in rows
        ]

    def upsert_futures_roll_mappings(
        self,
        source: str,
        mappings: Sequence[FuturesRollMapping],
    ) -> int:
        self._require_futures_storage()
        if len(mappings) > 2_000:
            raise ValueError("futures mapping batch exceeds 2000 rows")
        for item in mappings:
            item.validate()
        identities = {(item.series_symbol, item.effective_from) for item in mappings}
        if len(identities) != len(mappings):
            raise ValueError("futures mapping batch contains duplicate series/date rows")
        symbols = {
            value
            for item in mappings
            for value in (item.series_symbol, item.contract_symbol)
        }
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids(symbols)
            missing = symbols - instrument_ids.keys()
            if missing:
                raise ValueError("unknown futures mapping instruments: " + ", ".join(sorted(missing)))
            series_provider_symbols = {
                str(row[0]): str(row[1])
                for row in self._connection.execute(
                    """
                    SELECT instrument.symbol, series.provider_symbol
                    FROM futures_continuous_series AS series
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE instrument.symbol IN (
                    """ + ",".join("?" for _ in symbols) + ")",
                    sorted(symbols),
                )
            }
            contract_provider_symbols = {
                str(row[0]): str(row[1])
                for row in self._connection.execute(
                    """
                    SELECT instrument.symbol, contract.provider_symbol
                    FROM futures_contracts AS contract
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE instrument.symbol IN (
                    """ + ",".join("?" for _ in symbols) + ")",
                    sorted(symbols),
                )
            }
            if any(
                series_provider_symbols.get(item.series_symbol)
                != item.series_provider_symbol
                or contract_provider_symbols.get(item.contract_symbol)
                != item.contract_provider_symbol
                for item in mappings
            ):
                raise ValueError("futures mapping Provider identity mismatch")
            source_id = self._source_id(source)
            self._connection.executemany(
                """
                INSERT INTO futures_roll_mappings(
                    source_id, series_instrument_id, effective_from,
                    contract_instrument_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, series_instrument_id, effective_from) DO UPDATE SET
                    contract_instrument_id = excluded.contract_instrument_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    (
                        source_id, instrument_ids[item.series_symbol],
                        _date_key(item.effective_from),
                        instrument_ids[item.contract_symbol], now_ms,
                    )
                    for item in mappings
                ),
            )
        return len(mappings)

    def list_futures_roll_mappings(
        self,
        series_symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[FuturesRollMapping]:
        self._require_futures_storage()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT series.symbol, series_meta.provider_symbol,
                       mapping.effective_from, contract.symbol,
                       contract_meta.provider_symbol
                FROM futures_roll_mappings AS mapping
                JOIN instruments AS series
                  ON series.instrument_id = mapping.series_instrument_id
                JOIN futures_continuous_series AS series_meta
                  ON series_meta.instrument_id = series.instrument_id
                JOIN instruments AS contract
                  ON contract.instrument_id = mapping.contract_instrument_id
                JOIN futures_contracts AS contract_meta
                  ON contract_meta.instrument_id = contract.instrument_id
                WHERE series.symbol = ? AND mapping.effective_from BETWEEN ? AND ?
                ORDER BY mapping.effective_from
                """,
                (series_symbol, _date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return [
            FuturesRollMapping(
                series_symbol=str(row[0]), series_provider_symbol=str(row[1]),
                effective_from=_date_from_key(int(row[2])),
                contract_symbol=str(row[3]), contract_provider_symbol=str(row[4]),
            )
            for row in rows
        ]

    def upsert_futures_provisional_daily_bars(
        self,
        source: str,
        bars: Sequence[FuturesDailyBar],
        received_at: datetime,
        stale_symbols: Sequence[str] = (),
    ) -> int:
        self._require_futures_storage()
        identities = {(item.symbol, item.trading_day) for item in bars}
        if len(identities) != len(bars):
            raise ValueError("provisional futures batch contains duplicate symbol/date rows")
        for item in bars:
            item.validate()
            if item.state is not FuturesBarState.PROVISIONAL:
                raise ValueError("provisional futures storage accepts provisional bars only")
            if item.source != source:
                raise ValueError("provisional futures bars must share the requested source")
        stale = {item.upper() for item in stale_symbols}
        symbols = {item.symbol for item in bars}
        if not stale <= symbols:
            raise ValueError("stale futures symbols must belong to the provisional batch")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids(symbols)
            missing = symbols - instrument_ids.keys()
            if missing:
                raise ValueError(
                    "unknown provisional futures contracts: " + ", ".join(sorted(missing))
                )
            if symbols:
                placeholders = ",".join("?" for _ in symbols)
                real_ids = {
                    int(row[0]) for row in self._connection.execute(
                        f"SELECT instrument_id FROM futures_contracts "
                        f"WHERE instrument_id IN ({placeholders})",
                        [instrument_ids[item] for item in sorted(symbols)],
                    )
                }
                if real_ids != set(instrument_ids.values()):
                    raise ValueError("provisional futures storage accepts real contracts only")
            source_id = self._source_id(source)
            self._connection.executemany(
                """
                INSERT INTO futures_provisional_daily_bars(
                    instrument_id, trading_day, provider_date, open, high, low,
                    close, previous_close, previous_settlement, volume_contracts,
                    open_interest_contracts, source_id, provider_time, received_at,
                    takeover_state, stale, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM futures_daily_bars AS final
                        WHERE final.instrument_id = ? AND final.trading_day = ?
                    ) THEN 'canonical-taken-over' ELSE 'active' END,
                    ?, ?)
                ON CONFLICT(instrument_id, trading_day, source_id) DO UPDATE SET
                    provider_date = excluded.provider_date,
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    previous_close = excluded.previous_close,
                    previous_settlement = excluded.previous_settlement,
                    volume_contracts = excluded.volume_contracts,
                    open_interest_contracts = excluded.open_interest_contracts,
                    provider_time = excluded.provider_time,
                    received_at = excluded.received_at,
                    takeover_state = excluded.takeover_state,
                    stale = excluded.stale,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    (
                        instrument_ids[item.symbol], _date_key(item.trading_day),
                        _date_key(item.provider_date), item.open, item.high, item.low,
                        item.close, item.previous_close, item.previous_settlement,
                        item.volume_contracts, item.open_interest_contracts,
                        source_id, item.provider_time.isoformat(),
                        received_at.isoformat(), instrument_ids[item.symbol],
                        _date_key(item.trading_day), int(item.symbol.upper() in stale), now_ms,
                    )
                    for item in bars
                ),
            )
        return len(bars)

    def list_fused_futures_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[FuturesDailyBar]:
        final = self.list_futures_daily_bars(symbol, start_date, end_date)
        latest_final = final[-1].trading_day if final else None
        instrument = self.get_instrument_summary(symbol)
        if instrument and instrument["kind"] == InstrumentKind.FUTURES_CONTINUOUS.value:
            with self._lock:
                mapping = self._connection.execute(
                    """
                    SELECT contract.symbol
                    FROM futures_roll_mappings AS roll
                    JOIN instruments AS series
                      ON series.instrument_id = roll.series_instrument_id
                    JOIN instruments AS contract
                      ON contract.instrument_id = roll.contract_instrument_id
                    WHERE series.symbol = ? AND roll.effective_from <= ?
                    ORDER BY roll.effective_from DESC, roll.updated_at_ms DESC
                    LIMIT 1
                    """,
                    (symbol, _date_key(end_date)),
                ).fetchone()
            if mapping is None:
                return final
            mapped_symbol = str(mapping[0])
            mapped_rows = self.list_fused_futures_daily_bars(
                mapped_symbol, start_date, end_date
            )
            provisional = next(
                (
                    item for item in reversed(mapped_rows)
                    if item.state is FuturesBarState.PROVISIONAL
                    and (latest_final is None or item.trading_day > latest_final)
                ),
                None,
            )
            if provisional is not None:
                final.append(replace(
                    provisional,
                    symbol=symbol,
                    mapped_contract_symbol=mapped_symbol,
                    roll_event=False,
                ))
            return final
        with self._lock:
            row = self._connection.execute(
                """
                SELECT instrument.symbol, bar.trading_day, bar.provider_date,
                       bar.open, bar.high, bar.low, bar.close, bar.previous_close,
                       NULL, bar.previous_settlement, bar.volume_contracts,
                       NULL, bar.open_interest_contracts, NULL, NULL,
                       source.code, NULL, 0, bar.provider_time, bar.stale
                FROM futures_provisional_daily_bars AS bar
                JOIN instruments AS instrument USING (instrument_id)
                JOIN sources AS source USING (source_id)
                WHERE instrument.symbol = ?
                  AND bar.trading_day BETWEEN ? AND ?
                  AND bar.takeover_state = 'active'
                  AND (? IS NULL OR bar.trading_day > ?)
                ORDER BY bar.trading_day DESC, bar.provider_time DESC
                LIMIT 1
                """,
                (
                    symbol, _date_key(start_date), _date_key(end_date),
                    _date_key(latest_final) if latest_final else None,
                    _date_key(latest_final) if latest_final else None,
                ),
            ).fetchone()
        if row is not None:
            final.append(_futures_daily_row(row, FuturesBarState.PROVISIONAL))
        return final

    def mark_futures_provisional_stale(
        self,
        source: str,
        symbols: Sequence[str],
    ) -> int:
        self._require_futures_storage()
        normalized = tuple(sorted({item.strip() for item in symbols if item.strip()}))
        if not normalized:
            return 0
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            instrument_ids = self._instrument_ids(normalized)
            missing = set(normalized) - instrument_ids.keys()
            if missing:
                raise ValueError(
                    "unknown provisional futures contracts: "
                    + ", ".join(sorted(missing))
                )
            before = self._connection.total_changes
            self._connection.execute(
                """
                UPDATE futures_provisional_daily_bars
                SET stale = 1, updated_at_ms = ?
                WHERE source_id = ? AND takeover_state = 'active' AND stale = 0
                  AND instrument_id IN (
                """ + ",".join("?" for _ in instrument_ids) + ")",
                (now_ms, source_id, *instrument_ids.values()),
            )
            changed = self._connection.total_changes - before
        return changed

    def list_futures_provisional_audit(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
        *,
        limit: int = 10_000,
    ) -> list[dict[str, object]]:
        self._require_futures_storage()
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("futures provisional audit start must not exceed end")
        if not 1 <= limit <= 10_000:
            raise ValueError("invalid futures provisional audit limit")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT bar.trading_day, bar.provider_date, source.code,
                       bar.provider_time, bar.received_at, bar.takeover_state,
                       bar.stale, bar.open, bar.high, bar.low, bar.close,
                       bar.previous_close, bar.previous_settlement,
                       bar.volume_contracts, bar.open_interest_contracts
                FROM futures_provisional_daily_bars AS bar
                JOIN instruments AS instrument USING (instrument_id)
                JOIN sources AS source USING (source_id)
                WHERE instrument.symbol = ?
                  AND (? IS NULL OR bar.trading_day >= ?)
                  AND (? IS NULL OR bar.trading_day <= ?)
                ORDER BY bar.trading_day, source.code
                LIMIT ?
                """,
                (
                    symbol,
                    _date_key(start_date) if start_date else None,
                    _date_key(start_date) if start_date else None,
                    _date_key(end_date) if end_date else None,
                    _date_key(end_date) if end_date else None,
                    limit,
                ),
            ).fetchall()
        return [
            {
                "trading_day": _date_from_key(int(row[0])),
                "provider_date": _date_from_key(int(row[1])),
                "source": str(row[2]),
                "provider_time": datetime.fromisoformat(str(row[3])),
                "received_at": datetime.fromisoformat(str(row[4])),
                "takeover_state": str(row[5]),
                "stale": bool(row[6]),
                "open": float(row[7]),
                "high": float(row[8]),
                "low": float(row[9]),
                "close": float(row[10]),
                "previous_close": float(row[11]) if row[11] is not None else None,
                "previous_settlement": (
                    float(row[12]) if row[12] is not None else None
                ),
                "volume_contracts": int(row[13]),
                "open_interest_contracts": (
                    float(row[14]) if row[14] is not None else None
                ),
            }
            for row in rows
        ]

    def checkpoint_futures_sync(
        self,
        source: str,
        dataset: str,
        scope: str,
        identity: str,
        covered_from: date,
        covered_through: date,
        last_batch_rows: int,
    ) -> FuturesSyncState:
        self._require_futures_storage()
        if covered_from > covered_through:
            raise ValueError("futures sync coverage start must not exceed end")
        if last_batch_rows < 0:
            raise ValueError("futures sync row count must be non-negative")
        _require_sync_text(dataset, "dataset")
        _require_sync_text(scope, "scope")
        _require_sync_text(identity, "identity")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            self._connection.execute(
                """
                INSERT INTO futures_sync_states(
                    source_id, dataset, scope, identity, covered_from,
                    covered_through, last_batch_rows, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, dataset, scope, identity) DO UPDATE SET
                    covered_from = min(covered_from, excluded.covered_from),
                    covered_through = max(covered_through, excluded.covered_through),
                    last_batch_rows = excluded.last_batch_rows,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    source_id, dataset, scope, identity, _date_key(covered_from),
                    _date_key(covered_through), last_batch_rows, now_ms,
                ),
            )
        state = self.get_futures_sync_state(source, dataset, scope, identity)
        if state is None:
            raise RuntimeError("futures sync checkpoint was not persisted")
        return state

    def get_futures_sync_state(
        self,
        source: str,
        dataset: str,
        scope: str,
        identity: str,
    ) -> FuturesSyncState | None:
        self._require_futures_storage()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT source.code, state.dataset, state.scope, state.identity,
                       state.covered_from, state.covered_through,
                       state.last_batch_rows, state.updated_at_ms
                FROM futures_sync_states AS state
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND state.dataset = ?
                  AND state.scope = ? AND state.identity = ?
                """,
                (source, dataset, scope, identity),
            ).fetchone()
        return _futures_sync_row(row) if row is not None else None

    def record_futures_update_receipt(
        self,
        source: str,
        dataset: str,
        scope: str,
        effective_date: date,
        row_count: int,
        payload_hash: bytes,
        status: str,
        message: str = "",
    ) -> FuturesUpdateReceipt:
        self._require_futures_storage()
        if row_count < 0:
            raise ValueError("futures receipt row count must be non-negative")
        if not isinstance(payload_hash, bytes) or not payload_hash:
            raise ValueError("futures receipt payload hash is required")
        if status not in {"complete", "empty", "partial", "rejected"}:
            raise ValueError(f"invalid futures receipt status: {status}")
        _require_sync_text(dataset, "dataset")
        _require_sync_text(scope, "scope")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            self._upsert_futures_receipt_locked(
                source_id, dataset, scope, effective_date, row_count,
                payload_hash, status, message, now_ms,
            )
        receipt = self.get_futures_update_receipt(
            source, dataset, scope, effective_date
        )
        if receipt is None:
            raise RuntimeError("futures update receipt was not persisted")
        return receipt

    def get_futures_update_receipt(
        self,
        source: str,
        dataset: str,
        scope: str,
        effective_date: date,
    ) -> FuturesUpdateReceipt | None:
        self._require_futures_storage()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT source.code, receipt.dataset, receipt.scope,
                       receipt.effective_date, receipt.row_count,
                       receipt.payload_hash, receipt.status, receipt.message,
                       receipt.updated_at_ms
                FROM futures_update_receipts AS receipt
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND receipt.dataset = ?
                  AND receipt.scope = ? AND receipt.effective_date = ?
                """,
                (source, dataset, scope, _date_key(effective_date)),
            ).fetchone()
        return _futures_receipt_row(row) if row is not None else None

    def replace_futures_roll_mapping_window(
        self,
        source: str,
        scope: str,
        series_symbol: str,
        start_date: date,
        end_date: date,
        mappings: Sequence[FuturesRollMapping],
    ) -> tuple[FuturesSyncState, FuturesUpdateReceipt]:
        self._require_futures_storage()
        _require_sync_text(scope, "scope")
        if start_date > end_date:
            raise ValueError("futures mapping window start must not exceed end")
        if len(mappings) > 20_000:
            raise ValueError("futures mapping window exceeds 20000 rows")
        if any(
            item.series_symbol != series_symbol
            or not start_date <= item.effective_from <= end_date
            for item in mappings
        ):
            raise ValueError("futures mappings must belong to the replacement window")
        if len({item.effective_from for item in mappings}) != len(mappings):
            raise ValueError("futures mapping window contains duplicate dates")
        for item in mappings:
            item.validate()
        payload_hash = _futures_mapping_hash(mappings)
        status = "complete" if mappings else "empty"
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            ids = self._instrument_ids({
                series_symbol,
                *(item.contract_symbol for item in mappings),
            })
            if series_symbol not in ids:
                raise ValueError(f"unknown futures series: {series_symbol}")
            missing_contracts = {
                item.contract_symbol for item in mappings
                if item.contract_symbol not in ids
            }
            if missing_contracts:
                raise ValueError(
                    "unknown futures mapping contracts: "
                    + ", ".join(sorted(missing_contracts))
                )
            series_row = self._connection.execute(
                "SELECT provider_symbol FROM futures_continuous_series WHERE instrument_id = ?",
                (ids[series_symbol],),
            ).fetchone()
            if series_row is None:
                raise ValueError(f"not a futures continuous series: {series_symbol}")
            mapping_contract_symbols = sorted({
                item.contract_symbol for item in mappings
            })
            contract_provider_symbols = {
                str(row[0]): str(row[1])
                for row in self._connection.execute(
                    """
                    SELECT instrument.symbol, contract.provider_symbol
                    FROM futures_contracts AS contract
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE contract.instrument_id IN (
                    """
                    + ",".join("?" for _ in mapping_contract_symbols)
                    + ")",
                    [ids[item] for item in mapping_contract_symbols],
                )
            } if mappings else {}
            if any(
                str(series_row[0]) != item.series_provider_symbol
                or contract_provider_symbols.get(item.contract_symbol)
                != item.contract_provider_symbol
                for item in mappings
            ):
                raise ValueError("futures mapping Provider identity mismatch")
            self._connection.execute(
                """
                DELETE FROM futures_roll_mappings
                WHERE source_id = ? AND series_instrument_id = ?
                  AND effective_from BETWEEN ? AND ?
                """,
                (
                    source_id, ids[series_symbol],
                    _date_key(start_date), _date_key(end_date),
                ),
            )
            for offset in range(0, len(mappings), 2_000):
                self._connection.executemany(
                    """
                    INSERT INTO futures_roll_mappings(
                        source_id, series_instrument_id, effective_from,
                        contract_instrument_id, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            source_id, ids[series_symbol],
                            _date_key(item.effective_from),
                            ids[item.contract_symbol], now_ms,
                        )
                        for item in mappings[offset:offset + 2_000]
                    ),
                )
            self._connection.execute(
                """
                INSERT INTO futures_sync_states(
                    source_id, dataset, scope, identity, covered_from,
                    covered_through, last_batch_rows, updated_at_ms
                ) VALUES (?, 'roll-mapping', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, dataset, scope, identity) DO UPDATE SET
                    covered_from = min(covered_from, excluded.covered_from),
                    covered_through = max(covered_through, excluded.covered_through),
                    last_batch_rows = excluded.last_batch_rows,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    source_id, scope, series_symbol, _date_key(start_date),
                    _date_key(end_date), len(mappings), now_ms,
                ),
            )
            self._upsert_futures_receipt_locked(
                source_id, "roll-mapping", series_symbol, end_date,
                len(mappings), payload_hash, status, "", now_ms,
            )
        state = self.get_futures_sync_state(
            source, "roll-mapping", scope, series_symbol
        )
        receipt = self.get_futures_update_receipt(
            source, "roll-mapping", series_symbol, end_date
        )
        if state is None or receipt is None:
            raise RuntimeError("futures mapping checkpoint transaction was incomplete")
        return state, receipt

    def list_futures_coverage(
        self,
        *,
        kind: InstrumentKind | None = InstrumentKind.FUTURES_CONTRACT,
        limit: int = 5_000,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        self._require_futures_storage()
        if kind not in {
            None,
            InstrumentKind.FUTURES_CONTRACT,
            InstrumentKind.FUTURES_CONTINUOUS,
        }:
            raise ValueError("futures coverage kind must be contract or continuous")
        if not 1 <= limit <= 5_000 or offset < 0:
            raise ValueError("invalid futures coverage pagination")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM (
                    SELECT instrument.symbol, instrument.name, instrument.exchange,
                           product.product_code, 'futures-contract' AS kind,
                           contract.lifecycle_status, contract.contract_month,
                           NULL AS price_basis, NULL AS rule_version,
                           min(bar.trading_day) AS first_day,
                           max(bar.trading_day) AS last_day,
                           count(bar.trading_day) AS rows,
                           count(bar.trading_day) FILTER (
                               WHERE bar.settlement IS NULL
                           ) AS missing_settlement_rows,
                           count(bar.trading_day) FILTER (
                               WHERE bar.open_interest_contracts IS NULL
                           ) AS missing_open_interest_rows,
                           0 AS mapped_rows, 0 AS roll_event_rows
                    FROM futures_contracts AS contract
                    JOIN instruments AS instrument USING (instrument_id)
                    JOIN futures_products AS product
                      ON product.instrument_id = contract.product_instrument_id
                    LEFT JOIN futures_daily_bars AS bar USING (instrument_id)
                    GROUP BY contract.instrument_id
                    UNION ALL
                    SELECT instrument.symbol, instrument.name, instrument.exchange,
                           product.product_code, 'futures-continuous' AS kind,
                           NULL AS lifecycle_status, NULL AS contract_month,
                           series.price_basis, series.rule_version,
                           min(bar.trading_day), max(bar.trading_day),
                           count(bar.trading_day),
                           count(bar.trading_day) FILTER (
                               WHERE bar.settlement IS NULL
                           ),
                           count(bar.trading_day) FILTER (
                               WHERE bar.open_interest_contracts IS NULL
                           ),
                           count(bar.trading_day) FILTER (
                               WHERE bar.mapped_contract_instrument_id IS NOT NULL
                           ),
                           count(bar.trading_day) FILTER (WHERE bar.roll_event = 1)
                    FROM futures_continuous_series AS series
                    JOIN instruments AS instrument USING (instrument_id)
                    JOIN futures_products AS product
                      ON product.instrument_id = series.product_instrument_id
                    LEFT JOIN futures_daily_bars AS bar USING (instrument_id)
                    GROUP BY series.instrument_id
                ) AS coverage
                WHERE (? IS NULL OR coverage.kind = ?)
                ORDER BY exchange, product_code, kind, symbol
                LIMIT ? OFFSET ?
                """,
                (
                    kind.value if kind is not None else None,
                    kind.value if kind is not None else None,
                    limit,
                    offset,
                ),
            ).fetchall()
        return [
            {
                "symbol": str(row[0]), "name": str(row[1]),
                "exchange": str(row[2]), "product_code": str(row[3]),
                "kind": str(row[4]),
                "lifecycle_status": str(row[5]) if row[5] is not None else None,
                "contract_month": str(row[6]) if row[6] is not None else None,
                "price_basis": str(row[7]) if row[7] is not None else None,
                "rule_version": str(row[8]) if row[8] is not None else None,
                "first_trading_day": (
                    _date_from_key(int(row[9])) if row[9] is not None else None
                ),
                "last_trading_day": (
                    _date_from_key(int(row[10])) if row[10] is not None else None
                ),
                "rows": int(row[11]),
                "missing_settlement_rows": int(row[12]),
                "missing_open_interest_rows": int(row[13]),
                "mapped_rows": int(row[14]),
                "roll_event_rows": int(row[15]),
            }
            for row in rows
        ]

    def audit_futures_integrity(self) -> dict[str, object]:
        """Return aggregate futures integrity evidence without scanning rows in Python."""
        self._require_futures_storage()
        with self._lock:
            product_rows = self._connection.execute(
                """
                SELECT product.exchange, product.product_code,
                       count(DISTINCT contract.instrument_id) AS contracts,
                       count(DISTINCT CASE WHEN bar.trading_day IS NOT NULL
                                          THEN contract.instrument_id END) AS covered_contracts,
                       count(bar.trading_day) AS daily_rows,
                       min(bar.trading_day), max(bar.trading_day),
                       count(bar.trading_day) FILTER (WHERE bar.settlement IS NULL),
                       count(bar.trading_day) FILTER (
                           WHERE bar.open_interest_contracts IS NULL
                       ),
                       count(bar.trading_day) FILTER (WHERE bar.volume_contracts = 0),
                       count(bar.trading_day) FILTER (
                           WHERE bar.volume_contracts < 0
                              OR bar.open < 0 OR bar.high < 0 OR bar.low < 0 OR bar.close < 0
                              OR bar.high < max(bar.open, bar.close, bar.low)
                              OR bar.low > min(bar.open, bar.close, bar.high)
                       ),
                       count(DISTINCT contract.instrument_id) FILTER (
                           WHERE contract.trading_unit != product.trading_unit
                              OR contract.quote_unit != product.quote_unit
                              OR contract.multiplier IS NOT product.multiplier
                              OR contract.per_unit IS NOT product.per_unit
                       )
                FROM futures_products AS product
                JOIN futures_contracts AS contract
                  ON contract.product_instrument_id = product.instrument_id
                LEFT JOIN futures_daily_bars AS bar
                  ON bar.instrument_id = contract.instrument_id
                GROUP BY product.exchange, product.product_code
                ORDER BY product.exchange, product.product_code
                """
            ).fetchall()
            missing_rows = self._connection.execute(
                """
                SELECT product.exchange, product.product_code, count(*)
                FROM futures_sync_states AS state
                JOIN instruments AS instrument ON instrument.symbol = state.identity
                JOIN futures_contracts AS contract
                  ON contract.instrument_id = instrument.instrument_id
                JOIN futures_products AS product
                  ON product.instrument_id = contract.product_instrument_id
                JOIN futures_exchange_calendar AS calendar
                  ON calendar.source_id = state.source_id
                 AND calendar.exchange = product.exchange
                 AND calendar.is_open = 1
                 AND calendar.calendar_date BETWEEN max(state.covered_from, contract.listed_on)
                                                AND min(state.covered_through, contract.last_trading_date)
                LEFT JOIN futures_daily_bars AS bar
                  ON bar.instrument_id = contract.instrument_id
                 AND bar.trading_day = calendar.calendar_date
                WHERE state.dataset = 'daily' AND bar.trading_day IS NULL
                GROUP BY product.exchange, product.product_code
                """
            ).fetchall()
            duplicate_rows = int(self._connection.execute(
                """
                SELECT count(*) FROM (
                    SELECT instrument_id, trading_day
                    FROM futures_daily_bars
                    GROUP BY instrument_id, trading_day HAVING count(*) > 1
                )
                """
            ).fetchone()[0])
            continuous = self._connection.execute(
                """
                SELECT count(*),
                       count(*) FILTER (WHERE EXISTS (
                           SELECT 1 FROM futures_roll_mappings AS mapping
                           WHERE mapping.series_instrument_id = series.instrument_id
                       )),
                       count(*) FILTER (WHERE EXISTS (
                           SELECT 1 FROM futures_daily_bars AS bar
                           WHERE bar.instrument_id = series.instrument_id
                       )),
                       (SELECT count(*) FROM futures_roll_mappings),
                       count(*) FILTER (WHERE EXISTS (
                           SELECT 1 FROM futures_continuous_dirty_series AS dirty
                           WHERE dirty.series_instrument_id = series.instrument_id
                       )),
                       count(*) FILTER (WHERE EXISTS (
                           SELECT 1
                           FROM futures_roll_mappings AS mapping
                           JOIN futures_contracts AS contract
                             ON contract.instrument_id = mapping.contract_instrument_id
                           WHERE mapping.series_instrument_id = series.instrument_id
                             AND (mapping.effective_from < contract.listed_on
                               OR mapping.effective_from > contract.last_trading_date)
                       )),
                       count(*) FILTER (WHERE EXISTS (
                           SELECT 1 FROM futures_daily_bars AS bar
                           WHERE bar.instrument_id = series.instrument_id
                             AND bar.mapped_contract_instrument_id IS NULL
                       )),
                       (SELECT count(*)
                        FROM futures_daily_bars AS bar
                        JOIN futures_continuous_series AS selected
                          ON selected.instrument_id = bar.instrument_id
                        WHERE bar.roll_event = 1)
                FROM futures_continuous_series AS series
                """
            ).fetchone()
            latest_mapping = self._connection.execute(
                """
                SELECT series_symbol.exchange, count(*)
                FROM (
                    SELECT series.instrument_id, product.exchange,
                           max(mapping.effective_from) AS mapping_day,
                           (SELECT max(calendar.calendar_date)
                            FROM futures_exchange_calendar AS calendar
                            WHERE calendar.exchange = product.exchange
                              AND calendar.is_open = 1) AS latest_open_day
                    FROM futures_continuous_series AS series
                    JOIN futures_products AS product
                      ON product.instrument_id = series.product_instrument_id
                    LEFT JOIN futures_roll_mappings AS mapping
                      ON mapping.series_instrument_id = series.instrument_id
                    GROUP BY series.instrument_id
                ) AS series_symbol
                WHERE mapping_day IS NOT NULL AND mapping_day < latest_open_day
                GROUP BY series_symbol.exchange
                """
            ).fetchall()
            roll_jump = self._connection.execute(
                """
                WITH ordered AS (
                    SELECT bar.instrument_id, bar.trading_day, bar.close, bar.roll_event,
                           lag(bar.close) OVER (
                               PARTITION BY bar.instrument_id ORDER BY bar.trading_day
                           ) AS prior_close
                    FROM futures_daily_bars AS bar
                    JOIN futures_continuous_series AS series
                      ON series.instrument_id = bar.instrument_id
                )
                SELECT count(*) FILTER (WHERE roll_event = 1),
                       max(CASE WHEN roll_event = 1 AND prior_close > 0
                                THEN abs(close / prior_close - 1.0) END)
                FROM ordered
                """
            ).fetchone()
            receipt_rows = self._connection.execute(
                """
                SELECT status, count(*) FROM futures_update_receipts
                GROUP BY status ORDER BY status
                """
            ).fetchall()
        missing_by_product = {
            (str(row[0]), str(row[1])): int(row[2]) for row in missing_rows
        }
        products = [
            {
                "exchange": str(row[0]),
                "product_code": str(row[1]),
                "contracts": int(row[2]),
                "contracts_with_rows": int(row[3]),
                "contracts_without_rows": int(row[2]) - int(row[3]),
                "daily_rows": int(row[4]),
                "first_trading_day": _date_from_key(int(row[5])) if row[5] else None,
                "last_trading_day": _date_from_key(int(row[6])) if row[6] else None,
                "missing_settlement_rows": int(row[7]),
                "missing_open_interest_rows": int(row[8]),
                "zero_volume_rows": int(row[9]),
                "invalid_bar_rows": int(row[10]),
                "unit_mismatch_contracts": int(row[11]),
                "missing_covered_open_days": missing_by_product.get(
                    (str(row[0]), str(row[1])), 0
                ),
            }
            for row in product_rows
        ]
        structural_errors = (
            duplicate_rows
            + sum(
                item["invalid_bar_rows"]
                + item["unit_mismatch_contracts"]
                for item in products
            )
            + int(continuous[5] or 0)
            + int(continuous[6] or 0)
        )
        return {
            "schema_version": "futures-integrity-v1",
            "summary": {
                "products": len(products),
                "contracts": sum(item["contracts"] for item in products),
                "contracts_with_rows": sum(item["contracts_with_rows"] for item in products),
                "daily_rows": sum(item["daily_rows"] for item in products),
                "duplicate_daily_keys": duplicate_rows,
                "missing_covered_open_days": sum(
                    item["missing_covered_open_days"] for item in products
                ),
                "structural_errors": structural_errors,
            },
            "products": products,
            "continuous": {
                "series": int(continuous[0] or 0),
                "series_with_mappings": int(continuous[1] or 0),
                "series_without_mappings": int(continuous[0] or 0) - int(continuous[1] or 0),
                "series_with_rows": int(continuous[2] or 0),
                "mapping_rows": int(continuous[3] or 0),
                "dirty_series": int(continuous[4] or 0),
                "mapping_lifecycle_violations": int(continuous[5] or 0),
                "continuous_series_with_unmapped_rows": int(continuous[6] or 0),
                "roll_event_rows": int(continuous[7] or 0),
                "stale_mapping_series_by_exchange": {
                    str(row[0]): int(row[1]) for row in latest_mapping
                },
                "roll_jumps": int(roll_jump[0] or 0),
                "maximum_absolute_roll_return": (
                    float(roll_jump[1]) if roll_jump[1] is not None else None
                ),
            },
            "receipt_status_counts": {
                str(row[0]): int(row[1]) for row in receipt_rows
            },
        }

    def persist_futures_continuous_build(
        self,
        source: str,
        build: FuturesContinuousBuild,
        *,
        rebuilt_from: date | None = None,
    ) -> dict[str, object]:
        self._require_futures_storage()
        if len(build.input_digest) != 32:
            raise ValueError("continuous build requires a SHA-256 input digest")
        if any(
            item.symbol != build.series_symbol
            or item.state is not FuturesBarState.FINAL
            or item.source != source
            or item.mapped_contract_symbol is None
            for item in build.bars
        ):
            raise ValueError("continuous build contains incompatible output bars")
        if rebuilt_from is not None and any(
            item.trading_day < rebuilt_from for item in build.bars
        ):
            raise ValueError("continuous suffix contains bars before rebuilt_from")
        if (
            rebuilt_from is not None
            and build.price_basis is not FuturesPriceBasis.RAW
        ):
            raise ValueError(
                "backward-adjusted continuous history requires a full rebuild"
            )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            series_row = self._connection.execute(
                """
                SELECT instrument.instrument_id, series.price_basis,
                       series.rule_version
                FROM futures_continuous_series AS series
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ?
                """,
                (build.series_symbol,),
            ).fetchone()
            if series_row is None:
                raise ValueError(f"unknown futures continuous series: {build.series_symbol}")
            series_id = int(series_row[0])
            if str(series_row[1]) != build.price_basis.value:
                raise ValueError("continuous build price basis does not match catalog")
            if str(series_row[2]) != build.rule_version:
                raise ValueError("continuous build rule version does not match catalog")

            mapped_symbols = {
                item.mapped_contract_symbol for item in build.bars
                if item.mapped_contract_symbol is not None
            }
            mapped_symbols.update(
                value for item in build.rolls for value in (
                    item.outgoing_contract_symbol, item.incoming_contract_symbol,
                )
            )
            instrument_ids = self._instrument_ids(mapped_symbols)
            missing = mapped_symbols - instrument_ids.keys()
            if missing:
                raise ValueError(
                    "continuous build references unknown contracts: "
                    + ", ".join(sorted(missing))
                )

            if rebuilt_from is None:
                self._connection.execute(
                    "DELETE FROM futures_daily_bars WHERE instrument_id = ?",
                    (series_id,),
                )
                self._connection.execute(
                    "DELETE FROM futures_continuous_roll_events "
                    "WHERE series_instrument_id = ?",
                    (series_id,),
                )
            else:
                self._connection.execute(
                    "DELETE FROM futures_daily_bars "
                    "WHERE instrument_id = ? AND trading_day >= ?",
                    (series_id, _date_key(rebuilt_from)),
                )
                self._connection.execute(
                    "DELETE FROM futures_continuous_roll_events "
                    "WHERE series_instrument_id = ? AND effective_from >= ?",
                    (series_id, _date_key(rebuilt_from)),
                )

            for offset in range(0, len(build.bars), 2_000):
                self._connection.executemany(
                    """
                    INSERT INTO futures_daily_bars(
                        instrument_id, trading_day, provider_date, open, high, low,
                        close, previous_close, settlement, previous_settlement,
                        volume_contracts, amount_cny, open_interest_contracts,
                        open_interest_change_contracts, delivery_settlement,
                        mapped_contract_instrument_id, roll_event, source_id, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            series_id, _date_key(item.trading_day),
                            _date_key(item.provider_date), item.open, item.high,
                            item.low, item.close, item.previous_close,
                            item.settlement, item.previous_settlement,
                            item.volume_contracts, item.amount,
                            item.open_interest_contracts,
                            item.open_interest_change_contracts,
                            item.delivery_settlement,
                            instrument_ids[item.mapped_contract_symbol],
                            int(item.roll_event), source_id, now_ms,
                        )
                        for item in build.bars[offset:offset + 2_000]
                    ),
                )
            self._connection.executemany(
                """
                INSERT INTO futures_continuous_roll_events(
                    series_instrument_id, effective_from, first_output_day,
                    outgoing_contract_instrument_id, incoming_contract_instrument_id,
                    outgoing_close, incoming_close, adjustment_factor,
                    adjustment_offset, rule_version, input_digest, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        series_id, _date_key(item.effective_from),
                        _date_key(item.first_output_day),
                        instrument_ids[item.outgoing_contract_symbol],
                        instrument_ids[item.incoming_contract_symbol],
                        item.outgoing_close, item.incoming_close,
                        item.adjustment_factor, item.adjustment_offset,
                        build.rule_version, build.input_digest, now_ms,
                    )
                    for item in build.rolls
                ),
            )
            warnings_json = json.dumps(
                [
                    {
                        "code": item.code,
                        "trading_day": item.trading_day.isoformat(),
                        "contract_symbol": item.contract_symbol,
                        "message": item.message,
                    }
                    for item in build.warnings
                ],
                separators=(",", ":"),
            )
            first_day = min((item.trading_day for item in build.bars), default=None)
            last_day = max((item.trading_day for item in build.bars), default=None)
            status = "partial" if build.warnings else "complete"
            self._connection.execute(
                """
                INSERT INTO futures_continuous_builds(
                    series_instrument_id, source_id, price_basis, rule_version,
                    input_digest, rebuilt_from, rebuilt_through, row_count,
                    status, warnings_json, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(series_instrument_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    price_basis = excluded.price_basis,
                    rule_version = excluded.rule_version,
                    input_digest = excluded.input_digest,
                    rebuilt_from = excluded.rebuilt_from,
                    rebuilt_through = excluded.rebuilt_through,
                    row_count = excluded.row_count,
                    status = excluded.status,
                    warnings_json = excluded.warnings_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    series_id, source_id, build.price_basis.value,
                    build.rule_version, build.input_digest,
                    _date_key(rebuilt_from or first_day) if (rebuilt_from or first_day) else None,
                    _date_key(last_day) if last_day else None,
                    len(build.bars), status, warnings_json, now_ms,
                ),
            )
            if rebuilt_from is None:
                self._connection.execute(
                    "DELETE FROM futures_continuous_dirty_series "
                    "WHERE series_instrument_id = ?",
                    (series_id,),
                )
            else:
                self._connection.execute(
                    "DELETE FROM futures_continuous_dirty_series "
                    "WHERE series_instrument_id = ? AND dirty_from >= ?",
                    (series_id, _date_key(rebuilt_from)),
                )
        return {
            "series_symbol": build.series_symbol,
            "price_basis": build.price_basis.value,
            "rule_version": build.rule_version,
            "rebuilt_from": rebuilt_from or first_day,
            "rebuilt_through": last_day,
            "rows": len(build.bars), "rolls": len(build.rolls),
            "status": status, "warnings": len(build.warnings),
            "input_digest": build.input_digest,
        }

    def get_futures_continuous_build_status(
        self, series_symbol: str
    ) -> dict[str, object] | None:
        self._require_futures_storage()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT source.code, series.price_basis, series.rule_version,
                       build.input_digest, build.rebuilt_from,
                       build.rebuilt_through, build.row_count, build.status,
                       build.warnings_json, build.updated_at_ms
                FROM futures_continuous_builds AS build
                JOIN futures_continuous_series AS series
                  ON series.instrument_id = build.series_instrument_id
                JOIN instruments AS instrument
                  ON instrument.instrument_id = build.series_instrument_id
                JOIN sources AS source ON source.source_id = build.source_id
                WHERE instrument.symbol = ?
                """,
                (series_symbol,),
            ).fetchone()
        if row is None:
            return None
        return {
            "source": str(row[0]), "price_basis": str(row[1]),
            "rule_version": str(row[2]), "input_digest": bytes(row[3]),
            "rebuilt_from": _date_from_key(int(row[4])) if row[4] is not None else None,
            "rebuilt_through": _date_from_key(int(row[5])) if row[5] is not None else None,
            "rows": int(row[6]), "status": str(row[7]),
            "warnings": json.loads(str(row[8])), "updated_at_ms": int(row[9]),
        }

    def get_futures_continuous_dirty_state(
        self, series_symbol: str
    ) -> dict[str, object] | None:
        self._require_futures_storage()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT dirty.dirty_from, dirty.reason, dirty.updated_at_ms
                FROM futures_continuous_dirty_series AS dirty
                JOIN instruments AS instrument
                  ON instrument.instrument_id = dirty.series_instrument_id
                WHERE instrument.symbol = ?
                """,
                (series_symbol,),
            ).fetchone()
        return None if row is None else {
            "dirty_from": _date_from_key(int(row[0])),
            "reason": str(row[1]), "updated_at_ms": int(row[2]),
        }

    def list_futures_continuous_roll_events(
        self, series_symbol: str
    ) -> list[dict[str, object]]:
        self._require_futures_storage()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event.effective_from, event.first_output_day,
                       outgoing.symbol, incoming.symbol,
                       event.outgoing_close, event.incoming_close,
                       event.adjustment_factor, event.adjustment_offset,
                       event.rule_version, event.input_digest
                FROM futures_continuous_roll_events AS event
                JOIN instruments AS series
                  ON series.instrument_id = event.series_instrument_id
                JOIN instruments AS outgoing
                  ON outgoing.instrument_id = event.outgoing_contract_instrument_id
                JOIN instruments AS incoming
                  ON incoming.instrument_id = event.incoming_contract_instrument_id
                WHERE series.symbol = ?
                ORDER BY event.effective_from
                """,
                (series_symbol,),
            ).fetchall()
        return [
            {
                "effective_from": _date_from_key(int(row[0])),
                "first_output_day": _date_from_key(int(row[1])),
                "outgoing_contract_symbol": str(row[2]),
                "incoming_contract_symbol": str(row[3]),
                "outgoing_close": float(row[4]) if row[4] is not None else None,
                "incoming_close": float(row[5]) if row[5] is not None else None,
                "adjustment_factor": float(row[6]) if row[6] is not None else None,
                "adjustment_offset": float(row[7]) if row[7] is not None else None,
                "rule_version": str(row[8]), "input_digest": bytes(row[9]),
            }
            for row in rows
        ]

    def _upsert_futures_receipt_locked(
        self,
        source_id: int,
        dataset: str,
        scope: str,
        effective_date: date,
        row_count: int,
        payload_hash: bytes,
        status: str,
        message: str,
        now_ms: int,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO futures_update_receipts(
                source_id, dataset, scope, effective_date, row_count,
                payload_hash, status, message, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, dataset, scope, effective_date) DO UPDATE SET
                row_count = excluded.row_count,
                payload_hash = excluded.payload_hash,
                status = excluded.status,
                message = excluded.message,
                updated_at_ms = excluded.updated_at_ms
            """,
            (
                source_id, dataset, scope, _date_key(effective_date), row_count,
                payload_hash, status, message[:500], now_ms,
            ),
        )

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
        with self._lock:
            instrument = self._connection.execute(
                "SELECT kind FROM instruments WHERE symbol = ? COLLATE NOCASE",
                (symbol.upper(),),
            ).fetchone()
        if instrument is not None and str(instrument[0]) == InstrumentKind.CUSTOM_INDEX.value:
            clauses = ["instrument.symbol = ? COLLATE NOCASE"]
            parameters: list[object] = [symbol.upper()]
            if start_date is not None:
                clauses.append("bar.trade_date >= ?")
                parameters.append(_date_key(start_date))
            if end_date is not None:
                clauses.append("bar.trade_date <= ?")
                parameters.append(_date_key(end_date))
            query = f"""
                SELECT instrument.symbol, bar.trade_date, bar.open, bar.high,
                       bar.low, bar.close, bar.volume, bar.updated_at_ms
                FROM custom_index_daily_bars AS bar
                JOIN custom_indices AS custom USING (index_id)
                JOIN instruments AS instrument USING (instrument_id)
                WHERE {' AND '.join(clauses)}
                ORDER BY bar.trade_date
            """
            with self._lock:
                rows = self._connection.execute(query, parameters).fetchall()
            return [
                StoredDailyBar(
                    symbol=str(row[0]), trade_date=_date_from_key(int(row[1])),
                    open=float(row[2]), high=float(row[3]), low=float(row[4]),
                    close=float(row[5]), volume=int(row[6]), source="local_custom_index",
                    updated_at_ms=int(row[7]),
                )
                for row in rows
            ]
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
        kind = self.get_instrument_kind(normalized)
        if kind is InstrumentKind.CUSTOM_INDEX:
            query = """
                SELECT instrument.symbol, bar.trade_date, bar.open, bar.high,
                       bar.low, bar.close, bar.volume, bar.updated_at_ms
                FROM custom_index_daily_bars AS bar
                JOIN custom_indices AS custom USING (index_id)
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE AND bar.trade_date <= ?
                ORDER BY bar.trade_date DESC LIMIT ?
            """
            with self._lock:
                rows = self._connection.execute(
                    query, (normalized, _date_key(end_date), limit)
                ).fetchall()
            result = [
                StoredDailyBar(
                    symbol=str(row[0]), trade_date=_date_from_key(int(row[1])),
                    open=float(row[2]), high=float(row[3]), low=float(row[4]),
                    close=float(row[5]), volume=int(row[6]),
                    source="local_custom_index", updated_at_ms=int(row[7]),
                )
                for row in rows
            ]
        else:
            query = """
                SELECT instrument.symbol, bar.trade_date, bar.open, bar.high,
                       bar.low, bar.close, bar.volume, source.code, bar.updated_at_ms
                FROM daily_bars AS bar
                JOIN instruments AS instrument USING (instrument_id)
                JOIN sources AS source USING (source_id)
                WHERE instrument.symbol = ? COLLATE NOCASE AND bar.trade_date <= ?
                ORDER BY bar.trade_date DESC LIMIT ?
            """
            with self._lock:
                rows = self._connection.execute(
                    query, (normalized, _date_key(end_date), limit)
                ).fetchall()
            result = [
                StoredDailyBar(
                    symbol=str(row[0]), trade_date=_date_from_key(int(row[1])),
                    open=float(row[2]), high=float(row[3]), low=float(row[4]),
                    close=float(row[5]), volume=int(row[6]), source=str(row[7]),
                    updated_at_ms=int(row[8]),
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
            member_rows = self._connection.execute(
                """
                SELECT member.group_id, instrument.symbol
                FROM custom_instrument_group_members AS member
                JOIN instruments AS instrument USING (instrument_id)
                ORDER BY member.group_id, member.position
                """
            ).fetchall()
        member_symbols = list(dict.fromkeys(str(row[1]) for row in member_rows))
        snapshot_rows: list[dict[str, object]] = []
        for offset in range(0, len(member_symbols), 500):
            snapshot_rows.extend(self.list_market_snapshots(
                member_symbols[offset : offset + 500]
            ))
        changes = {
            str(item["symbol"]): float(item["change_percent"])
            for item in snapshot_rows if item.get("change_percent") is not None
        }
        group_changes: dict[str, list[float]] = {}
        for row in member_rows:
            change = changes.get(str(row[1]))
            if change is not None:
                group_changes.setdefault(str(row[0]), []).append(change)
        groups = [
            {
                "id": str(row[0]), "symbol": f"CUSTOM:{row[0]}",
                "name": str(row[1]), "description": str(row[2]),
                "member_count": int(row[5]), "created_at_ms": int(row[3]),
                "updated_at_ms": int(row[4]),
                "average_change_percent": (
                    sum(group_changes[str(row[0])]) / len(group_changes[str(row[0])])
                    if group_changes.get(str(row[0])) else None
                ),
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
                       instrument.exchange, member.position, member.role,
                       member.tags_json, member.note, instrument.active
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
                    "position": int(row[4]), "role": str(row[5]),
                    "tags": json.loads(str(row[6])),
                    "note": str(row[7]), "available": bool(row[8]),
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
        symbols = [
            value if value.upper().startswith("FUT") else value.upper()
            for item in members
            if (value := str(item["symbol"]).strip())
        ]
        if len(symbols) != len(members):
            raise ValueError("custom group member symbol is required")
        if len(symbols) != len(set(symbols)):
            raise ValueError("custom group members must contain unique symbols")
        instrument_ids = self._instrument_ids(set(symbols))
        missing = set(symbols) - instrument_ids.keys()
        if missing:
            raise ValueError(f"unknown instruments: {', '.join(sorted(missing))}")
        product_symbols: list[str] = []
        instrument_id_values = list(instrument_ids.values())
        for offset in range(0, len(instrument_id_values), 400):
            chunk = instrument_id_values[offset : offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            product_symbols.extend(str(row[0]) for row in self._connection.execute(
                f"SELECT symbol FROM instruments WHERE instrument_id IN ({placeholders}) "
                "AND kind = 'futures-product'",
                chunk,
            ).fetchall())
        if product_symbols:
            raise ValueError(
                "futures products are catalog nodes, not custom group members: "
                + ", ".join(sorted(product_symbols))
            )
        self._connection.execute(
            "DELETE FROM custom_instrument_group_members WHERE group_id = ?",
            (group_id,),
        )
        self._connection.executemany(
            """
            INSERT INTO custom_instrument_group_members(
                group_id, instrument_id, position, role, tags_json, note, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    group_id, instrument_ids[symbol], position,
                    _custom_group_role(item.get("role", "")),
                    json.dumps(
                        [str(tag).strip() for tag in item.get("tags", []) if str(tag).strip()],
                        ensure_ascii=False, separators=(",", ":"),
                    ),
                    str(item.get("note", "")).strip(), now_ms,
                )
                for position, (symbol, item) in enumerate(zip(symbols, members))
            ),
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

    def create_trend_review(self, spec: TrendReviewDraftSpec) -> dict[str, object]:
        spec.validate()
        review_id = str(uuid4())
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            identity = self._canonical_instrument_identity(spec.symbol)
            if identity is None:
                raise ValueError(
                    f"trend review references unknown instrument: {spec.symbol.strip()}"
                )
            symbol, instrument_id = identity
            self._connection.execute(
                """
                INSERT INTO trend_review_cases(
                    review_id, instrument_id, schema_version, dataset_version,
                    timeframe, horizon, interval_start, interval_end, as_of_date,
                    input_digest, algorithm_version, config_version, settings_json,
                    classification, review_status, tags_json, labels_json,
                    expected_json, rationale, sources_json, revision,
                    created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    review_id, instrument_id, "1.0", spec.dataset_version.strip(),
                    spec.timeframe, spec.horizon, _date_key(spec.interval_start),
                    _date_key(spec.interval_end), _date_key(spec.as_of_date),
                    spec.input_digest, spec.algorithm_version.strip(), spec.config_version.strip(),
                    json.dumps(spec.settings, ensure_ascii=False, sort_keys=True),
                    spec.classification.strip(), spec.status.value,
                    json.dumps(list(dict.fromkeys(spec.tags)), ensure_ascii=False),
                    _review_labels_json(spec.labels),
                    json.dumps(spec.expected, ensure_ascii=False, sort_keys=True),
                    spec.rationale.strip(),
                    json.dumps(list(spec.sources), ensure_ascii=False, sort_keys=True),
                    now_ms, now_ms,
                ),
            )
        created = self.get_trend_review(review_id)
        if created is None:
            raise RuntimeError("created trend review is not readable")
        return created

    def get_trend_review(self, review_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                f"{_TREND_REVIEW_SELECT} WHERE review.review_id = ?",
                (review_id,),
            ).fetchone()
        return _trend_review_row(row) if row is not None else None

    def list_trend_reviews(
        self,
        *,
        symbol: str | None = None,
        status: TrendReviewStatus | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 500:
            raise ValueError("trend review limit must be between 1 and 500")
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("instrument.symbol = ? COLLATE NOCASE")
            params.append(symbol.upper())
        if status is not None:
            clauses.append("review.review_status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"{_TREND_REVIEW_SELECT} {where} "
                "ORDER BY review.updated_at_ms DESC, review.review_id LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [_trend_review_row(row) for row in rows]

    def update_trend_review(
        self,
        review_id: str,
        *,
        expected_revision: int,
        status: TrendReviewStatus,
        labels: Sequence[TrendReviewLabel],
        expected: dict[str, object],
        rationale: str,
    ) -> dict[str, object]:
        ids = [label.item_id for label in labels]
        if len(ids) != len(set(ids)):
            raise ValueError("trend review label item IDs must be unique")
        for label in labels:
            label.validate()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            current = self._connection.execute(
                "SELECT review_status, labels_json FROM trend_review_cases WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if current is None:
                raise ValueError("trend review not found")
            if str(current[0]) == TrendReviewStatus.CONFIRMED.value:
                raise ValueError("confirmed trend review is immutable")
            original_labels = json.loads(str(current[1]))
            original_types = {
                str(item["item_id"]): str(item["item_type"])
                for item in original_labels
            }
            updated_types = {label.item_id: label.item_type for label in labels}
            if any(
                updated_types.get(item_id) != item_type
                for item_id, item_type in original_types.items()
            ):
                raise ValueError("trend review update must retain every original candidate")
            cursor = self._connection.execute(
                """
                UPDATE trend_review_cases
                SET review_status = ?, labels_json = ?, expected_json = ?,
                    rationale = ?, revision = revision + 1, updated_at_ms = ?
                WHERE review_id = ? AND revision = ?
                """,
                (
                    status.value, _review_labels_json(labels),
                    json.dumps(expected, ensure_ascii=False, sort_keys=True),
                    rationale.strip(), now_ms, review_id, expected_revision,
                ),
            )
            if cursor.rowcount == 0:
                raise RuntimeError("trend review revision conflict")
        updated = self.get_trend_review(review_id)
        if updated is None:
            raise RuntimeError("updated trend review is not readable")
        return updated

    def begin_generated_analysis_run(
        self, spec: AnalysisRunSpec
    ) -> AnalysisRunRecord:
        spec.validate()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument = self._canonical_instrument_identity(spec.symbol)
            if instrument is None:
                raise ValueError(
                    f"analysis references unknown instrument: {spec.symbol.strip()}"
                )
            normalized_symbol, instrument_id = instrument
            identity = (
                instrument_id,
                spec.system_id.strip(),
                spec.timeframe.strip(),
                spec.namespace.value,
                _date_key(spec.as_of_date),
                spec.input_digest,
                spec.algorithm_version.strip(),
                spec.config_version.strip(),
            )
            existing = self._connection.execute(
                """
                SELECT run_id, attempt, supersedes_run_id
                FROM generated_analysis_runs
                WHERE instrument_id = ? AND system_id = ? AND timeframe = ?
                  AND namespace = ? AND as_of_date = ? AND input_digest = ?
                  AND algorithm_version = ? AND config_version = ?
                  AND status = 'succeeded'
                ORDER BY completed_at_ms DESC LIMIT 1
                """,
                identity,
            ).fetchone()
            if existing is not None:
                return AnalysisRunRecord(
                    run_id=str(existing[0]),
                    status=AnalysisRunStatus.SUCCEEDED,
                    attempt=int(existing[1]),
                    supersedes_run_id=str(existing[2]) if existing[2] else None,
                    reused=True,
                )
            attempt = int(self._connection.execute(
                """
                SELECT coalesce(max(attempt), 0) + 1
                FROM generated_analysis_runs
                WHERE instrument_id = ? AND system_id = ? AND timeframe = ?
                  AND namespace = ? AND as_of_date = ? AND input_digest = ?
                  AND algorithm_version = ? AND config_version = ?
                """,
                identity,
            ).fetchone()[0])
            superseded = self._connection.execute(
                """
                SELECT run_id FROM generated_analysis_runs
                WHERE instrument_id = ? AND system_id = ? AND timeframe = ?
                  AND namespace = ? AND status = 'succeeded'
                ORDER BY as_of_date DESC, completed_at_ms DESC LIMIT 1
                """,
                identity[:4],
            ).fetchone()
            run_id = uuid4().hex
            supersedes_run_id = str(superseded[0]) if superseded else None
            self._connection.execute(
                """
                INSERT INTO generated_analysis_runs(
                    run_id, system_id, instrument_id, timeframe, namespace,
                    as_of_date, input_start_date, input_end_date, input_digest,
                    algorithm_version, config_version, completion_state, status,
                    attempt, source_observed_at_ms, expires_at_ms,
                    supersedes_run_id, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
                """,
                (
                    run_id, identity[1], instrument_id, identity[2], identity[3],
                    identity[4], _date_key(spec.input_start_date),
                    _date_key(spec.input_end_date), spec.input_digest, identity[6],
                    identity[7], spec.completion_state, attempt,
                    spec.source_observed_at_ms, spec.expires_at_ms,
                    supersedes_run_id, now_ms,
                ),
            )
        return AnalysisRunRecord(
            run_id, AnalysisRunStatus.RUNNING, attempt, supersedes_run_id
        )

    def upsert_generated_analysis_target(
        self, target: GeneratedAnalysisTarget
    ) -> int:
        target.validate()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument = self._canonical_instrument_identity(target.symbol)
            if instrument is None:
                raise ValueError(
                    f"analysis target references unknown instrument: {target.symbol.strip()}"
                )
            symbol, instrument_id = instrument
            target_id = int(self._connection.execute(
                """
                INSERT INTO generated_analysis_targets(
                    instrument_id, system_id, timeframe, algorithm_version,
                    config_version, settings_json, enabled, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, system_id, timeframe) DO UPDATE SET
                    algorithm_version = excluded.algorithm_version,
                    config_version = excluded.config_version,
                    settings_json = excluded.settings_json,
                    enabled = excluded.enabled,
                    updated_at_ms = excluded.updated_at_ms
                RETURNING target_id
                """,
                (
                    instrument_id, target.system_id.strip(),
                    target.timeframe.strip(), target.algorithm_version.strip(),
                    target.config_version.strip(),
                    json.dumps(target.settings, sort_keys=True, separators=(",", ":")),
                    int(target.enabled), now_ms,
                ),
            ).fetchone()[0])
            if not target.enabled:
                self._connection.execute(
                    "DELETE FROM generated_analysis_dirty_targets WHERE target_id = ?",
                    (target_id,),
                )
        return target_id

    def queue_generated_analysis_target(
        self,
        symbol: str,
        system_id: str,
        timeframe: str,
        dirty_from: date,
        dirty_through: date,
        reason: str,
    ) -> bool:
        if dirty_from > dirty_through:
            raise ValueError("analysis dirty range is invalid")
        if not reason.strip():
            raise ValueError("analysis dirty reason is required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            target = self._connection.execute(
                """
                SELECT target.target_id
                FROM generated_analysis_targets AS target
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                  AND target.system_id = ? AND target.timeframe = ?
                  AND target.enabled = 1
                """,
                (symbol.upper(), system_id, timeframe),
            ).fetchone()
            if target is None:
                return False
            self._connection.execute(
                """
                INSERT INTO generated_analysis_dirty_targets(
                    target_id, dirty_from, dirty_through, reason,
                    generation, queued_at_ms
                ) VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(target_id) DO UPDATE SET
                    dirty_from = min(dirty_from, excluded.dirty_from),
                    dirty_through = max(dirty_through, excluded.dirty_through),
                    reason = excluded.reason, generation = generation + 1,
                    queued_at_ms = excluded.queued_at_ms,
                    claimed_at_ms = NULL, lease_until_ms = NULL
                """,
                (
                    int(target[0]), _date_key(dirty_from), _date_key(dirty_through),
                    reason.strip(), now_ms,
                ),
            )
        return True

    def claim_generated_analysis_targets(
        self, *, limit: int = 10, lease_ms: int = 60_000
    ) -> list[ClaimedAnalysisTarget]:
        if not 1 <= limit <= 100 or lease_ms <= 0:
            raise ValueError("invalid analysis claim limit or lease")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        lease_until_ms = now_ms + lease_ms
        with self._lock, self._transaction():
            rows = self._connection.execute(
                """
                SELECT dirty.target_id, instrument.symbol, target.system_id,
                       target.timeframe, target.algorithm_version,
                       target.config_version, target.settings_json, dirty.dirty_from,
                       dirty.dirty_through, dirty.reason, dirty.generation
                FROM generated_analysis_dirty_targets AS dirty
                JOIN generated_analysis_targets AS target USING (target_id)
                JOIN instruments AS instrument USING (instrument_id)
                WHERE target.enabled = 1
                  AND (dirty.lease_until_ms IS NULL OR dirty.lease_until_ms <= ?)
                ORDER BY dirty.queued_at_ms, dirty.target_id LIMIT ?
                """,
                (now_ms, limit),
            ).fetchall()
            self._connection.executemany(
                """
                UPDATE generated_analysis_dirty_targets
                SET claimed_at_ms = ?, lease_until_ms = ?, last_error = NULL
                WHERE target_id = ? AND generation = ?
                """,
                ((now_ms, lease_until_ms, int(row[0]), int(row[10])) for row in rows),
            )
        return [
            ClaimedAnalysisTarget(
                target_id=int(row[0]), symbol=str(row[1]), system_id=str(row[2]),
                timeframe=str(row[3]), algorithm_version=str(row[4]),
                config_version=str(row[5]), settings=json.loads(str(row[6])),
                dirty_from=_date_from_key(int(row[7])),
                dirty_through=_date_from_key(int(row[8])), reason=str(row[9]),
                generation=int(row[10]),
            )
            for row in rows
        ]

    def claim_generated_analysis_target(
        self, target_id: int, *, lease_ms: int = 60_000
    ) -> ClaimedAnalysisTarget | None:
        if lease_ms <= 0:
            raise ValueError("analysis claim lease must be positive")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            row = self._connection.execute(
                """
                SELECT dirty.target_id, instrument.symbol, target.system_id,
                       target.timeframe, target.algorithm_version,
                       target.config_version, target.settings_json, dirty.dirty_from,
                       dirty.dirty_through, dirty.reason, dirty.generation
                FROM generated_analysis_dirty_targets AS dirty
                JOIN generated_analysis_targets AS target USING (target_id)
                JOIN instruments AS instrument USING (instrument_id)
                WHERE dirty.target_id = ? AND target.enabled = 1
                  AND (dirty.lease_until_ms IS NULL OR dirty.lease_until_ms <= ?)
                """,
                (target_id, now_ms),
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                """
                UPDATE generated_analysis_dirty_targets
                SET claimed_at_ms = ?, lease_until_ms = ?, last_error = NULL
                WHERE target_id = ? AND generation = ?
                """,
                (now_ms, now_ms + lease_ms, target_id, int(row[10])),
            )
        return ClaimedAnalysisTarget(
            target_id=int(row[0]), symbol=str(row[1]), system_id=str(row[2]),
            timeframe=str(row[3]), algorithm_version=str(row[4]),
            config_version=str(row[5]), settings=json.loads(str(row[6])),
            dirty_from=_date_from_key(int(row[7])),
            dirty_through=_date_from_key(int(row[8])), reason=str(row[9]),
            generation=int(row[10]),
        )

    def complete_generated_analysis_target(
        self, target_id: int, generation: int
    ) -> bool:
        with self._lock, self._transaction():
            before = self._connection.total_changes
            self._connection.execute(
                """
                DELETE FROM generated_analysis_dirty_targets
                WHERE target_id = ? AND generation = ?
                """,
                (target_id, generation),
            )
            return self._connection.total_changes > before

    def fail_generated_analysis_target(
        self, target_id: int, generation: int, error: str
    ) -> bool:
        if not error.strip():
            raise ValueError("analysis target failure is required")
        with self._lock, self._transaction():
            before = self._connection.total_changes
            self._connection.execute(
                """
                UPDATE generated_analysis_dirty_targets
                SET claimed_at_ms = NULL, lease_until_ms = NULL, last_error = ?
                WHERE target_id = ? AND generation = ?
                """,
                (error.strip(), target_id, generation),
            )
            return self._connection.total_changes > before

    def complete_generated_analysis_run(
        self,
        run_id: str,
        items: Sequence[GeneratedAnalysisItem],
        *,
        duration_ms: float,
        warnings: Sequence[dict[str, object]] = (),
    ) -> AnalysisRunRecord:
        if duration_ms < 0:
            raise ValueError("analysis duration must be non-negative")
        validate_items(items)
        serialized_items = [
            json.dumps(item.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in items
        ]
        warnings_json = json.dumps(
            list(warnings), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            row = self._connection.execute(
                """
                SELECT status, attempt, supersedes_run_id, namespace,
                       instrument_id, system_id, timeframe, as_of_date
                FROM generated_analysis_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown analysis run: {run_id}")
            if str(row[0]) != AnalysisRunStatus.RUNNING.value:
                raise ValueError("only a running analysis can be completed")
            self._connection.executemany(
                """
                INSERT INTO generated_analysis_items(
                    run_id, item_id, item_type, parent_item_id, sequence, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        run_id, item.item_id, item.item_type.value,
                        item.parent_item_id, sequence, serialized_items[sequence],
                    )
                    for sequence, item in enumerate(items)
                ),
            )
            self._connection.execute(
                """
                UPDATE generated_analysis_runs
                SET status = 'succeeded', duration_ms = ?, warnings_json = ?,
                    completed_at_ms = ?, failure_details = NULL
                WHERE run_id = ?
                """,
                (duration_ms, warnings_json, now_ms, run_id),
            )
            if str(row[3]) == AnalysisNamespace.OFFICIAL.value:
                self._connection.execute(
                    """
                    UPDATE generated_analysis_runs
                    SET expires_at_ms = CASE
                        WHEN expires_at_ms IS NULL OR expires_at_ms > ? THEN ?
                        ELSE expires_at_ms END
                    WHERE instrument_id = ? AND system_id = ? AND timeframe = ?
                      AND namespace = 'preview' AND as_of_date <= ?
                    """,
                    (now_ms, now_ms, int(row[4]), str(row[5]), str(row[6]), int(row[7])),
                )
        return AnalysisRunRecord(
            run_id, AnalysisRunStatus.SUCCEEDED, int(row[1]),
            str(row[2]) if row[2] else None,
        )

    def fail_generated_analysis_run(
        self, run_id: str, failure_details: str, *, duration_ms: float
    ) -> AnalysisRunRecord:
        if not failure_details.strip():
            raise ValueError("analysis failure details are required")
        if duration_ms < 0:
            raise ValueError("analysis duration must be non-negative")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            row = self._connection.execute(
                """
                SELECT status, attempt, supersedes_run_id
                FROM generated_analysis_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown analysis run: {run_id}")
            if str(row[0]) != AnalysisRunStatus.RUNNING.value:
                raise ValueError("only a running analysis can fail")
            self._connection.execute(
                """
                UPDATE generated_analysis_runs
                SET status = 'failed', duration_ms = ?, failure_details = ?,
                    completed_at_ms = ? WHERE run_id = ?
                """,
                (duration_ms, failure_details.strip(), now_ms, run_id),
            )
        return AnalysisRunRecord(
            run_id, AnalysisRunStatus.FAILED, int(row[1]),
            str(row[2]) if row[2] else None,
        )

    def get_latest_generated_analysis_run(
        self,
        symbol: str,
        system_id: str,
        timeframe: str,
        namespace: AnalysisNamespace = AnalysisNamespace.OFFICIAL,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT run.run_id, run.status, run.as_of_date,
                       run.input_start_date, run.input_end_date, run.input_digest,
                       run.algorithm_version, run.config_version,
                       run.completion_state, run.attempt, run.duration_ms,
                       run.warnings_json, run.failure_details,
                       run.source_observed_at_ms, run.expires_at_ms,
                       run.supersedes_run_id, run.created_at_ms, run.completed_at_ms
                FROM generated_analysis_runs AS run
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                  AND run.system_id = ? AND run.timeframe = ?
                  AND run.namespace = ? AND run.status = 'succeeded'
                ORDER BY run.as_of_date DESC, run.completed_at_ms DESC LIMIT 1
                """,
                (symbol.upper(), system_id, timeframe, namespace.value),
            ).fetchone()
            if row is None:
                return None
            items = self._connection.execute(
                """
                SELECT item_id, item_type, parent_item_id, payload_json
                FROM generated_analysis_items WHERE run_id = ? ORDER BY sequence
                """,
                (str(row[0]),),
            ).fetchall()
            lifecycle = self._connection.execute(
                """
                SELECT dirty.target_id IS NOT NULL,
                       target.algorithm_version, target.config_version,
                       EXISTS (
                           SELECT 1 FROM generated_analysis_runs AS newer
                           WHERE newer.instrument_id = target.instrument_id
                             AND newer.system_id = target.system_id
                             AND newer.timeframe = target.timeframe
                             AND newer.namespace = ?
                             AND newer.status IN ('running', 'failed')
                             AND (
                                 newer.as_of_date > ?
                                 OR (newer.as_of_date = ? AND newer.created_at_ms >= ?)
                             )
                       )
                FROM generated_analysis_targets AS target
                LEFT JOIN generated_analysis_dirty_targets AS dirty USING (target_id)
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                  AND target.system_id = ? AND target.timeframe = ?
                """,
                (
                    namespace.value, int(row[2]), int(row[2]), int(row[17]), symbol.upper(),
                    system_id, timeframe,
                ),
            ).fetchone()
        stale_reasons: list[str] = []
        if lifecycle is not None:
            if bool(lifecycle[0]):
                stale_reasons.append("canonical-input-dirty")
            if str(lifecycle[1]) != str(row[6]) or str(lifecycle[2]) != str(row[7]):
                stale_reasons.append("algorithm-or-config-upgraded")
            if bool(lifecycle[3]):
                stale_reasons.append("newer-run-incomplete-or-failed")
        return {
            "run_id": str(row[0]), "status": str(row[1]),
            "as_of_date": _date_from_key(int(row[2])),
            "input_start_date": _date_from_key(int(row[3])),
            "input_end_date": _date_from_key(int(row[4])),
            "input_digest": bytes(row[5]), "algorithm_version": str(row[6]),
            "config_version": str(row[7]), "completion_state": str(row[8]),
            "attempt": int(row[9]), "duration_ms": float(row[10]),
            "warnings": json.loads(str(row[11])), "failure_details": row[12],
            "source_observed_at_ms": row[13], "expires_at_ms": row[14],
            "supersedes_run_id": row[15], "created_at_ms": int(row[16]),
            "completed_at_ms": int(row[17]),
            "stale": bool(stale_reasons), "stale_reasons": stale_reasons,
            "items": [
                {
                    "item_id": str(item[0]), "item_type": str(item[1]),
                    "parent_item_id": item[2], "payload": json.loads(str(item[3])),
                }
                for item in items
            ],
        }

    def prune_generated_analysis_runs(
        self,
        now_ms: int,
        *,
        preview_retention_ms: int = 7 * 24 * 60 * 60 * 1000,
        failed_retention_ms: int = 30 * 24 * 60 * 60 * 1000,
    ) -> int:
        if min(now_ms, preview_retention_ms, failed_retention_ms) < 0:
            raise ValueError("analysis retention values must be non-negative")
        with self._lock, self._transaction():
            rows = self._connection.execute(
                """
                SELECT run_id FROM generated_analysis_runs
                WHERE (
                    namespace = 'preview' AND expires_at_ms IS NOT NULL
                    AND expires_at_ms <= ?
                ) OR (
                    status = 'failed' AND completed_at_ms IS NOT NULL
                    AND completed_at_ms <= ?
                )
                """,
                (now_ms - preview_retention_ms, now_ms - failed_retention_ms),
            ).fetchall()
            run_ids = [str(row[0]) for row in rows]
            if not run_ids:
                return 0
            placeholders = ",".join("?" for _ in run_ids)
            self._connection.execute(
                f"UPDATE generated_analysis_runs SET supersedes_run_id = NULL "
                f"WHERE supersedes_run_id IN ({placeholders})",
                run_ids,
            )
            self._connection.execute(
                f"DELETE FROM generated_analysis_runs WHERE run_id IN ({placeholders})",
                run_ids,
            )
        return len(run_ids)

    def create_custom_index(
        self,
        index_id: str,
        name: str,
        description: str,
        base_date: date,
        base_value: float,
        weighting_method: str,
        members: Sequence[dict[str, object]],
    ) -> dict[str, object]:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("custom index name is required")
        if base_value <= 0:
            raise ValueError("custom index base value must be positive")
        if weighting_method not in {"equal", "manual"}:
            raise ValueError("invalid custom index weighting method")
        normalized = normalize_members(
            [(str(item.get("symbol", "")), item.get("weight")) for item in members],
            weighting_method,
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        symbol = f"CINDEX:{index_id.lower()}"
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids({item.symbol for item in normalized})
            missing = {item.symbol for item in normalized} - instrument_ids.keys()
            if missing:
                raise ValueError("unknown custom index members: " + ", ".join(sorted(missing)))
            invalid = self._connection.execute(
                "SELECT symbol FROM instruments WHERE instrument_id IN ("
                + ",".join("?" for _ in instrument_ids)
                + ") AND kind <> ? LIMIT 1",
                (*instrument_ids.values(), InstrumentKind.STOCK.value),
            ).fetchone()
            if invalid is not None:
                raise ValueError(f"custom index members must be stocks: {invalid[0]}")
            self._connection.execute(
                "INSERT INTO instruments(symbol, name, kind, exchange, active) VALUES (?, ?, ?, 'LOCAL', 1)",
                (symbol, normalized_name, InstrumentKind.CUSTOM_INDEX.value),
            )
            custom_instrument_id = int(self._connection.execute(
                "SELECT instrument_id FROM instruments WHERE symbol = ?", (symbol,)
            ).fetchone()[0])
            self._connection.execute(
                """
                INSERT INTO custom_indices(
                    index_id, instrument_id, description, base_date, base_value,
                    status, calculation_version, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    index_id.lower(), custom_instrument_id, description.strip(),
                    _date_key(base_date), base_value, CALCULATION_VERSION, now_ms, now_ms,
                ),
            )
            revision = self._connection.execute(
                """
                INSERT INTO custom_index_revisions(
                    index_id, revision_number, effective_from, weighting_method, created_at_ms
                ) VALUES (?, 1, ?, ?, ?) RETURNING revision_id
                """,
                (index_id.lower(), _date_key(base_date), weighting_method, now_ms),
            ).fetchone()
            revision_id = int(revision[0])
            self._connection.executemany(
                """
                INSERT INTO custom_index_revision_members(
                    revision_id, instrument_id, position, raw_weight, normalized_weight
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        revision_id, instrument_ids[item.symbol], position,
                        item.raw_weight, item.normalized_weight,
                    )
                    for position, item in enumerate(normalized)
                ),
            )
        result = self.get_custom_index(index_id)
        assert result is not None
        return result

    def list_custom_indices(self, query: str = "") -> list[dict[str, object]]:
        pattern = f"%{query.strip()}%"
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT custom.index_id, instrument.symbol, instrument.name,
                       custom.description, custom.base_date, custom.base_value,
                       custom.status, custom.last_error, custom.updated_at_ms,
                       (SELECT count(*) FROM custom_index_revision_members AS member
                        JOIN custom_index_revisions AS revision USING (revision_id)
                        WHERE revision.index_id = custom.index_id
                          AND revision.revision_number = (
                              SELECT max(revision_number) FROM custom_index_revisions
                              WHERE index_id = custom.index_id
                          )),
                       (SELECT min(trade_date) FROM custom_index_daily_bars WHERE index_id = custom.index_id),
                       (SELECT max(trade_date) FROM custom_index_daily_bars WHERE index_id = custom.index_id),
                       (SELECT count(*) FROM custom_index_daily_bars WHERE index_id = custom.index_id),
                       (SELECT count(*) FROM custom_index_daily_bars
                        WHERE index_id = custom.index_id AND quality_status <> 'complete')
                FROM custom_indices AS custom
                JOIN instruments AS instrument USING (instrument_id)
                WHERE (? = '%%' OR instrument.symbol LIKE ? OR instrument.name LIKE ?)
                ORDER BY instrument.name, instrument.symbol
                """,
                (pattern, pattern, pattern),
            ).fetchall()
        return [self._custom_index_summary(row) for row in rows]

    def update_custom_index(
        self,
        index_id: str,
        name: str,
        description: str,
        base_date: date,
        base_value: float,
        weighting_method: str,
        members: Sequence[dict[str, object]],
        effective_from: date,
    ) -> dict[str, object] | None:
        normalized_id = index_id.lower().removeprefix("cindex:")
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("custom index name is required")
        if base_value <= 0:
            raise ValueError("custom index base value must be positive")
        if weighting_method not in {"equal", "manual"}:
            raise ValueError("invalid custom index weighting method")
        normalized_members = normalize_members(
            [(str(item.get("symbol", "")), item.get("weight")) for item in members],
            weighting_method,
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            custom = self._connection.execute(
                "SELECT instrument_id, base_date, base_value FROM custom_indices WHERE index_id = ?",
                (normalized_id,),
            ).fetchone()
            if custom is None:
                return None
            if _date_key(base_date) != int(custom[1]) or base_value != float(custom[2]):
                raise ValueError("custom index base date and value are immutable")
            if effective_from < base_date:
                raise ValueError("custom index revision cannot precede its base date")
            if effective_from > date.today():
                raise ValueError("custom index revision cannot start in the future")
            instrument_ids = self._instrument_ids({item.symbol for item in normalized_members})
            missing = {item.symbol for item in normalized_members} - instrument_ids.keys()
            if missing:
                raise ValueError("unknown custom index members: " + ", ".join(sorted(missing)))
            invalid = self._connection.execute(
                "SELECT symbol FROM instruments WHERE instrument_id IN ("
                + ",".join("?" for _ in instrument_ids)
                + ") AND kind <> ? LIMIT 1",
                (*instrument_ids.values(), InstrumentKind.STOCK.value),
            ).fetchone()
            if invalid is not None:
                raise ValueError(f"custom index members must be stocks: {invalid[0]}")
            revision_number = int(self._connection.execute(
                "SELECT coalesce(max(revision_number), 0) + 1 FROM custom_index_revisions WHERE index_id = ?",
                (normalized_id,),
            ).fetchone()[0])
            revision_id = int(self._connection.execute(
                """
                INSERT INTO custom_index_revisions(
                    index_id, revision_number, effective_from, weighting_method, created_at_ms
                ) VALUES (?, ?, ?, ?, ?) RETURNING revision_id
                """,
                (
                    normalized_id, revision_number, _date_key(effective_from),
                    weighting_method, now_ms,
                ),
            ).fetchone()[0])
            self._connection.executemany(
                """
                INSERT INTO custom_index_revision_members(
                    revision_id, instrument_id, position, raw_weight, normalized_weight
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        revision_id, instrument_ids[item.symbol], position,
                        item.raw_weight, item.normalized_weight,
                    )
                    for position, item in enumerate(normalized_members)
                ),
            )
            self._connection.execute(
                "UPDATE instruments SET name = ? WHERE instrument_id = ?",
                (normalized_name, int(custom[0])),
            )
            self._connection.execute(
                """
                UPDATE custom_indices SET description = ?, status = 'pending',
                    last_error = NULL, updated_at_ms = ?
                WHERE index_id = ?
                """,
                (
                    description.strip(), now_ms, normalized_id,
                ),
            )
        return self.get_custom_index(normalized_id)

    def get_custom_index(self, index_id: str) -> dict[str, object] | None:
        normalized = index_id.lower().removeprefix("cindex:")
        with self._lock:
            row = self._connection.execute(
                """
                SELECT custom.index_id, instrument.symbol, instrument.name,
                       custom.description, custom.base_date, custom.base_value,
                       custom.status, custom.last_error, custom.updated_at_ms,
                       revision.revision_id, revision.revision_number,
                       revision.effective_from, revision.weighting_method
                FROM custom_indices AS custom
                JOIN instruments AS instrument USING (instrument_id)
                JOIN custom_index_revisions AS revision ON revision.index_id = custom.index_id
                WHERE custom.index_id = ?
                ORDER BY revision.revision_number DESC LIMIT 1
                """,
                (normalized,),
            ).fetchone()
            if row is None:
                return None
            members = self._connection.execute(
                """
                SELECT instrument.symbol, instrument.name, member.position,
                       member.raw_weight, member.normalized_weight
                FROM custom_index_revision_members AS member
                JOIN instruments AS instrument USING (instrument_id)
                WHERE member.revision_id = ? ORDER BY member.position
                """,
                (int(row[9]),),
            ).fetchall()
            coverage = self._connection.execute(
                """
                SELECT min(trade_date), max(trade_date), count(*),
                       count(*) FILTER (WHERE quality_status <> 'complete')
                FROM custom_index_daily_bars WHERE index_id = ?
                """,
                (normalized,),
            ).fetchone()
        return {
            "id": str(row[0]), "symbol": str(row[1]), "name": str(row[2]),
            "description": str(row[3]), "base_date": _date_from_key(int(row[4])),
            "base_value": float(row[5]), "status": str(row[6]), "last_error": row[7],
            "updated_at_ms": int(row[8]), "revision_number": int(row[10]),
            "effective_from": _date_from_key(int(row[11])), "weighting_method": str(row[12]),
            "members": [
                {
                    "symbol": str(item[0]), "name": str(item[1]), "position": int(item[2]),
                    "raw_weight": float(item[3]), "normalized_weight": float(item[4]),
                }
                for item in members
            ],
            "first_trade_date": _date_from_key(int(coverage[0])) if coverage[0] is not None else None,
            "last_trade_date": _date_from_key(int(coverage[1])) if coverage[1] is not None else None,
            "rows": int(coverage[2]),
            "quality_warning_days": int(coverage[3]),
        }

    def rebuild_custom_index(self, index_id: str, mode: str = "backfill") -> dict[str, object]:
        if mode not in {"backfill", "incremental", "correction"}:
            raise ValueError("invalid custom index calculation mode")
        normalized = index_id.lower().removeprefix("cindex:")
        detail = self.get_custom_index(normalized)
        if detail is None:
            raise ValueError("custom index not found")
        started_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock:
            dirty = self._connection.execute(
                "SELECT dirty_from FROM custom_index_dirty_dates WHERE index_id = ?",
                (normalized,),
            ).fetchone()
        calculation_mode = "correction" if dirty is not None else mode
        with self._lock, self._transaction():
            run = self._connection.execute(
                """
                INSERT INTO custom_index_calculation_runs(
                    index_id, mode, started_at_ms, row_count, status, message
                ) VALUES (?, ?, ?, 0, 'running', '') RETURNING run_id
                """,
                (normalized, calculation_mode, started_ms),
            ).fetchone()
            run_id = int(run[0])
            self._connection.execute(
                "UPDATE custom_indices SET status = 'building', last_error = NULL, updated_at_ms = ? WHERE index_id = ?",
                (started_ms, normalized),
            )
        try:
            incremental = calculation_mode == "incremental" and detail["rows"] > 0
            history = (
                self._calculate_custom_index_incremental(normalized)
                if incremental
                else self._calculate_custom_index_history(normalized)
            )
            bars = (
                [item for item in history if _date_key(item.trade_date) >= int(dirty[0])]
                if calculation_mode == "correction" and dirty is not None
                else history
            )
            completed_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            with self._lock, self._transaction():
                if calculation_mode == "correction" and dirty is not None:
                    self._connection.execute(
                        "DELETE FROM custom_index_daily_bars WHERE index_id = ? AND trade_date >= ?",
                        (normalized, int(dirty[0])),
                    )
                elif not incremental:
                    self._connection.execute("DELETE FROM custom_index_daily_bars WHERE index_id = ?", (normalized,))
                self._connection.executemany(
                    """
                    INSERT INTO custom_index_daily_bars(
                        index_id, trade_date, open, high, low, close, volume, daily_return,
                        eligible_count, total_count, quality_status, input_hash,
                        calculation_version, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(index_id, trade_date) DO UPDATE SET
                        open = excluded.open, high = excluded.high, low = excluded.low,
                        close = excluded.close, volume = excluded.volume,
                        daily_return = excluded.daily_return,
                        eligible_count = excluded.eligible_count,
                        total_count = excluded.total_count,
                        quality_status = excluded.quality_status,
                        input_hash = excluded.input_hash,
                        calculation_version = excluded.calculation_version,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        (
                            normalized, _date_key(item.trade_date), item.open, item.high,
                            item.low, item.close, item.volume, item.daily_return, item.eligible_count,
                            item.total_count, item.quality_status, item.input_hash,
                            CALCULATION_VERSION, completed_ms,
                        )
                        for item in bars
                    ),
                )
                self._connection.execute(
                    "UPDATE custom_indices SET status = 'ready', last_error = NULL, updated_at_ms = ? WHERE index_id = ?",
                    (completed_ms, normalized),
                )
                self._connection.execute(
                    """
                    UPDATE custom_index_calculation_runs SET completed_at_ms = ?,
                        from_date = ?, through_date = ?, row_count = ?, status = 'completed', message = 'materialized'
                    WHERE run_id = ?
                    """,
                    (
                        completed_ms,
                        _date_key(bars[0].trade_date) if bars else None,
                        _date_key(bars[-1].trade_date) if bars else None,
                        len(bars), run_id,
                    ),
                )
                self._refresh_custom_index_snapshot_locked(normalized, completed_ms)
                self._connection.execute(
                    "DELETE FROM custom_index_dirty_dates WHERE index_id = ?",
                    (normalized,),
                )
            result = self.get_custom_index(normalized)
            assert result is not None
            return result
        except Exception as exc:
            completed_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            message = str(exc)[:1000]
            with self._lock, self._transaction():
                self._connection.execute(
                    "UPDATE custom_indices SET status = 'error', last_error = ?, updated_at_ms = ? WHERE index_id = ?",
                    (message, completed_ms, normalized),
                )
                self._connection.execute(
                    "UPDATE custom_index_calculation_runs SET completed_at_ms = ?, status = 'failed', message = ? WHERE run_id = ?",
                    (completed_ms, message, run_id),
                )
            raise

    def mark_custom_index_error(self, index_id: str, message: str) -> None:
        normalized = index_id.lower().removeprefix("cindex:")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE custom_indices SET status = 'error', last_error = ?, updated_at_ms = ?
                WHERE index_id = ?
                """,
                (message[:1000], now_ms, normalized),
            )

    def has_dirty_custom_indices(self) -> bool:
        with self._lock:
            return self._connection.execute(
                "SELECT 1 FROM custom_index_dirty_dates LIMIT 1"
            ).fetchone() is not None

    def delete_custom_index(self, index_id: str) -> bool:
        normalized = index_id.lower().removeprefix("cindex:")
        with self._lock, self._transaction():
            row = self._connection.execute(
                "SELECT instrument_id FROM custom_indices WHERE index_id = ?", (normalized,)
            ).fetchone()
            if row is None:
                return False
            instrument_id = int(row[0])
            self._connection.execute("DELETE FROM custom_index_calculation_runs WHERE index_id = ?", (normalized,))
            self._connection.execute("DELETE FROM custom_index_dirty_dates WHERE index_id = ?", (normalized,))
            self._connection.execute("DELETE FROM custom_index_daily_bars WHERE index_id = ?", (normalized,))
            revision_ids = [int(item[0]) for item in self._connection.execute(
                "SELECT revision_id FROM custom_index_revisions WHERE index_id = ?", (normalized,)
            )]
            if revision_ids:
                self._connection.execute(
                    "DELETE FROM custom_index_revision_members WHERE revision_id IN ("
                    + ",".join("?" for _ in revision_ids) + ")",
                    revision_ids,
                )
            self._connection.execute("DELETE FROM custom_index_revisions WHERE index_id = ?", (normalized,))
            self._connection.execute("DELETE FROM custom_indices WHERE index_id = ?", (normalized,))
            self._connection.execute("DELETE FROM market_snapshots WHERE instrument_id = ?", (instrument_id,))
            self._connection.execute("DELETE FROM instruments WHERE instrument_id = ?", (instrument_id,))
        return True

    def _calculate_custom_index_history(self, index_id: str):
        with self._lock:
            custom = self._connection.execute(
                "SELECT base_date, base_value FROM custom_indices WHERE index_id = ?", (index_id,)
            ).fetchone()
            revisions = self._connection.execute(
                """
                SELECT revision_id, effective_from FROM custom_index_revisions
                WHERE index_id = ? ORDER BY effective_from, revision_number
                """,
                (index_id,),
            ).fetchall()
            revision_members: dict[int, list[sqlite3.Row]] = {}
            all_ids: set[int] = set()
            for revision in revisions:
                rows = self._connection.execute(
                    """
                    SELECT member.instrument_id, instrument.symbol, member.normalized_weight,
                           instrument.active,
                           (SELECT min(listed_on) FROM instrument_catalog_entries WHERE instrument_id = member.instrument_id),
                           (SELECT max(delisted_on) FROM instrument_catalog_entries WHERE instrument_id = member.instrument_id)
                    FROM custom_index_revision_members AS member
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE member.revision_id = ? ORDER BY member.position
                    """,
                    (int(revision[0]),),
                ).fetchall()
                revision_members[int(revision[0])] = rows
                all_ids.update(int(item[0]) for item in rows)
            if not all_ids:
                raise ValueError("custom index has no members")
            placeholders = ",".join("?" for _ in all_ids)
            completed_through = self._connection.execute(
                f"SELECT max(trade_date) FROM daily_bars WHERE instrument_id IN ({placeholders})",
                tuple(all_ids),
            ).fetchone()[0]
            if completed_through is None:
                raise ValueError("custom index members have no stored history")
            calendar_rows = self._connection.execute(
                f"""
                SELECT trade_date FROM (
                    SELECT trade_date FROM trading_calendar
                    WHERE trade_date >= ? AND trade_date <= ?
                    UNION
                    SELECT trade_date FROM daily_bars
                    WHERE instrument_id IN ({placeholders})
                      AND trade_date >= ? AND trade_date <= ?
                )
                ORDER BY trade_date
                """,
                (
                    int(custom[0]), int(completed_through), *all_ids,
                    int(custom[0]), int(completed_through),
                ),
            ).fetchall()
        calendar = [int(row[0]) for row in calendar_rows]
        if not calendar:
            with self._lock:
                fallback_rows = self._connection.execute(
                    f"""
                    SELECT DISTINCT trade_date FROM daily_bars
                    WHERE instrument_id IN ({placeholders}) AND trade_date >= ?
                    ORDER BY trade_date
                    """,
                    (*all_ids, int(custom[0])),
                ).fetchall()
            calendar = [int(row[0]) for row in fallback_rows]
        if not calendar:
            raise ValueError("custom index members have no history on or after base date")
        revision_index = 0
        previous: dict[int, tuple[float, float]] = {}
        output = []
        previous_index_close = float(custom[1])
        base_written = False
        for trade_key in calendar:
            while revision_index + 1 < len(revisions) and int(revisions[revision_index + 1][1]) <= trade_key:
                revision_index += 1
            current_revision_id = int(revisions[revision_index][0])
            current_members = revision_members[current_revision_id]
            with self._lock:
                day_rows = self._connection.execute(
                    f"""
                    SELECT bar.instrument_id, instrument.symbol, bar.trade_date,
                           bar.open, bar.high, bar.low, bar.close, bar.volume,
                           factor.factor
                    FROM daily_bars AS bar
                    JOIN instruments AS instrument USING (instrument_id)
                    LEFT JOIN stock_adjustment_factors AS factor
                      ON factor.instrument_id = bar.instrument_id
                     AND factor.trade_date = bar.trade_date
                    WHERE bar.instrument_id IN ({placeholders})
                      AND bar.trade_date = ?
                    ORDER BY bar.instrument_id
                    """,
                    (*all_ids, trade_key),
                ).fetchall()
                status_rows = self._connection.execute(
                    f"""
                    SELECT instrument_id, status FROM stock_trade_status
                    WHERE instrument_id IN ({placeholders}) AND trade_date = ?
                    """,
                    (*all_ids, trade_key),
                ).fetchall()
            today = {int(row[0]): row for row in day_rows}
            day_status = {int(row[0]): str(row[1]) for row in status_rows}
            for instrument_id, row in today.items():
                if row[8] is not None:
                    previous.setdefault(instrument_id, (float(row[6]), float(row[8])))
            if not base_written:
                current_ids = {int(member[0]) for member in current_members}
                day_volume = sum(
                    int(row[7]) for instrument_id, row in today.items()
                    if instrument_id in current_ids
                )
                item = base_bar(
                    _date_from_key(trade_key), previous_index_close,
                    len(current_members), day_volume,
                )
                output.append(item)
                base_written = True
                for instrument_id, row in today.items():
                    if row[8] is not None:
                        previous[instrument_id] = (float(row[6]), float(row[8]))
                continue
            constituents = []
            for member in current_members:
                instrument_id = int(member[0])
                row = today.get(instrument_id)
                prior = previous.get(instrument_id)
                listed_on = int(member[4]) if member[4] is not None else None
                delisted_on = int(member[5]) if member[5] is not None else None
                if (listed_on is not None and trade_key < listed_on) or (delisted_on is not None and trade_key > delisted_on):
                    status = "not_eligible"
                elif row is not None and row[8] is None:
                    status = "missing"
                elif row is not None and prior is not None:
                    status = "trading"
                elif row is not None:
                    status = "missing"
                elif day_status.get(instrument_id) == "suspended" and prior is not None:
                    status = "suspended"
                elif prior is None:
                    status = "not_eligible"
                else:
                    status = "missing"
                constituents.append(ConstituentInput(
                    symbol=str(member[1]), weight=float(member[2]),
                    current=(
                        DailyBar(
                            str(row[1]), _date_from_key(int(row[2])), float(row[3]),
                            float(row[4]), float(row[5]), float(row[6]), int(row[7]),
                        ) if row is not None else None
                    ),
                    previous_close=prior[0] if prior else None,
                    current_factor=float(row[8]) if row is not None else (prior[1] if prior else None),
                    previous_factor=prior[1] if prior else None,
                    status=status,
                ))
            item = calculate_bar(_date_from_key(trade_key), previous_index_close, constituents)
            for instrument_id, row in today.items():
                if row[8] is not None:
                    previous[instrument_id] = (float(row[6]), float(row[8]))
            if item is not None:
                output.append(item)
                previous_index_close = item.close
        return output

    def _calculate_custom_index_incremental(self, index_id: str):
        with self._lock:
            latest = self._connection.execute(
                """
                SELECT trade_date, close FROM custom_index_daily_bars
                WHERE index_id = ? ORDER BY trade_date DESC LIMIT 1
                """,
                (index_id,),
            ).fetchone()
            if latest is None:
                return self._calculate_custom_index_history(index_id)
            completed_through = self._connection.execute(
                """
                SELECT max(bar.trade_date)
                FROM daily_bars AS bar
                WHERE bar.instrument_id IN (
                    SELECT DISTINCT member.instrument_id
                    FROM custom_index_revision_members AS member
                    JOIN custom_index_revisions AS revision USING (revision_id)
                    WHERE revision.index_id = ?
                )
                """,
                (index_id,),
            ).fetchone()[0]
            if completed_through is None:
                return []
            dates = self._connection.execute(
                """
                SELECT trade_date FROM (
                    SELECT trade_date FROM trading_calendar
                    WHERE trade_date > ? AND trade_date <= ?
                    UNION
                    SELECT DISTINCT bar.trade_date
                    FROM daily_bars AS bar
                    WHERE bar.instrument_id IN (
                        SELECT DISTINCT member.instrument_id
                        FROM custom_index_revision_members AS member
                        JOIN custom_index_revisions AS revision USING (revision_id)
                        WHERE revision.index_id = ?
                    )
                      AND bar.trade_date > ? AND bar.trade_date <= ?
                )
                ORDER BY trade_date
                """,
                (
                    int(latest[0]), int(completed_through), index_id,
                    int(latest[0]), int(completed_through),
                ),
            ).fetchall()
        previous_index_close = float(latest[1])
        output = []
        for date_row in dates:
            trade_key = int(date_row[0])
            with self._lock:
                revision = self._connection.execute(
                    """
                    SELECT revision_id FROM custom_index_revisions
                    WHERE index_id = ? AND effective_from <= ?
                    ORDER BY effective_from DESC, revision_number DESC LIMIT 1
                    """,
                    (index_id, trade_key),
                ).fetchone()
                if revision is None:
                    continue
                members = self._connection.execute(
                    """
                    SELECT member.instrument_id, instrument.symbol, member.normalized_weight,
                           (SELECT min(listed_on) FROM instrument_catalog_entries
                            WHERE instrument_id = member.instrument_id),
                           (SELECT max(delisted_on) FROM instrument_catalog_entries
                            WHERE instrument_id = member.instrument_id)
                    FROM custom_index_revision_members AS member
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE member.revision_id = ? ORDER BY member.position
                    """,
                    (int(revision[0]),),
                ).fetchall()
                constituents = []
                for member in members:
                    current = self._connection.execute(
                        """
                        SELECT bar.open, bar.high, bar.low, bar.close, bar.volume, factor.factor
                        FROM daily_bars AS bar
                        LEFT JOIN stock_adjustment_factors AS factor
                          ON factor.instrument_id = bar.instrument_id
                         AND factor.trade_date = bar.trade_date
                        WHERE bar.instrument_id = ? AND bar.trade_date = ?
                        """,
                        (int(member[0]), trade_key),
                    ).fetchone()
                    prior = self._connection.execute(
                        """
                        SELECT bar.close, factor.factor
                        FROM daily_bars AS bar
                        LEFT JOIN stock_adjustment_factors AS factor
                          ON factor.instrument_id = bar.instrument_id
                         AND factor.trade_date = bar.trade_date
                        WHERE bar.instrument_id = ? AND bar.trade_date < ?
                        ORDER BY bar.trade_date DESC LIMIT 1
                        """,
                        (int(member[0]), trade_key),
                    ).fetchone()
                    explicit_status = self._connection.execute(
                        """
                        SELECT status FROM stock_trade_status
                        WHERE instrument_id = ? AND trade_date = ?
                        """,
                        (int(member[0]), trade_key),
                    ).fetchone()
                    listed_on = int(member[3]) if member[3] is not None else None
                    delisted_on = int(member[4]) if member[4] is not None else None
                    if (
                        (listed_on is not None and trade_key < listed_on)
                        or (delisted_on is not None and trade_key > delisted_on)
                    ):
                        status = "not_eligible"
                    elif current is not None and current[5] is None:
                        status = "missing"
                    elif current is not None and prior is not None and prior[1] is not None:
                        status = "trading"
                    elif current is not None:
                        status = "missing"
                    elif (
                        explicit_status is not None
                        and str(explicit_status[0]) == "suspended"
                        and prior is not None
                    ):
                        status = "suspended"
                    elif prior is not None:
                        status = "missing"
                    else:
                        status = "not_eligible"
                    constituents.append(ConstituentInput(
                        symbol=str(member[1]),
                        weight=float(member[2]),
                        current=(
                            DailyBar(
                                str(member[1]), _date_from_key(trade_key),
                                float(current[0]), float(current[1]), float(current[2]),
                                float(current[3]), int(current[4]),
                            ) if current is not None else None
                        ),
                        previous_close=float(prior[0]) if prior is not None else None,
                        current_factor=(
                            float(current[5]) if current is not None and current[5] is not None
                            else float(prior[1]) if prior is not None and prior[1] is not None
                            else None
                        ),
                        previous_factor=(
                            float(prior[1]) if prior is not None and prior[1] is not None else None
                        ),
                        status=status,
                    ))
            item = calculate_bar(
                _date_from_key(trade_key), previous_index_close, constituents
            )
            if item is not None:
                output.append(item)
                previous_index_close = item.close
        return output

    def _refresh_custom_index_snapshot_locked(self, index_id: str, now_ms: int) -> None:
        rows = self._connection.execute(
            """
            SELECT bar.trade_date, bar.close, custom.instrument_id
            FROM custom_index_daily_bars AS bar
            JOIN custom_indices AS custom USING (index_id)
            WHERE bar.index_id = ? ORDER BY bar.trade_date DESC LIMIT 2
            """,
            (index_id,),
        ).fetchall()
        if len(rows) < 2 or float(rows[1][1]) == 0:
            return
        source_id = self._source_id("local_custom_index")
        self._connection.execute(
            """
            INSERT INTO market_snapshots(
                instrument_id, trade_date, change_percent, total_market_cap,
                close, volume, amount, source_id, updated_at_ms
            ) VALUES (?, ?, ?, NULL, ?, NULL, NULL, ?, ?)
            ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                change_percent = excluded.change_percent, close = excluded.close,
                source_id = excluded.source_id, updated_at_ms = excluded.updated_at_ms
            """,
            (
                int(rows[0][2]), int(rows[0][0]),
                (float(rows[0][1]) / float(rows[1][1]) - 1.0) * 100.0,
                float(rows[0][1]), source_id, now_ms,
            ),
        )

    @staticmethod
    def _custom_index_summary(row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": str(row[0]), "symbol": str(row[1]), "name": str(row[2]),
            "description": str(row[3]), "base_date": _date_from_key(int(row[4])),
            "base_value": float(row[5]), "status": str(row[6]), "last_error": row[7],
            "updated_at_ms": int(row[8]), "member_count": int(row[9]),
            "first_trade_date": _date_from_key(int(row[10])) if row[10] is not None else None,
            "last_trade_date": _date_from_key(int(row[11])) if row[11] is not None else None,
            "rows": int(row[12]),
            "quality_warning_days": int(row[13]),
        }

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
