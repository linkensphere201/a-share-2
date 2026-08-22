"""Futures-owned SQLite storage mixin used by the public market-data facade."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import sqlite3
import time

from stock_harness.futures_continuous import FuturesContinuousBuild
from stock_harness.futures_mapping import (
    FuturesContractMappingIdentity,
    normalize_futures_mappings,
    validate_futures_mapping_identities,
)
from stock_harness.models import (
    FuturesBarState,
    FuturesCalendarDay,
    FuturesContinuousSeries,
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
    Instrument,
    InstrumentKind,
    WriteStats,
)
from stock_harness.sqlite_mapping import _date_from_key, _date_key


LOGGER = logging.getLogger(__name__)

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


class SQLiteFuturesStoreMixin:
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
            before_takeover = self._connection.total_changes
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
            takeover_count = self._connection.total_changes - before_takeover
        elapsed_ms = (time.perf_counter() - started) * 1000
        if takeover_count:
            LOGGER.info(
                "futures_canonical_takeover_completed rows=%d", takeover_count
            )
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

    def get_latest_futures_daily_bar_before(
        self,
        symbol: str,
        before_date: date,
    ) -> FuturesDailyBar | None:
        self._require_futures_storage()
        with self._lock:
            row = self._connection.execute(
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
                WHERE instrument.symbol = ? AND bar.trading_day < ?
                ORDER BY bar.trading_day DESC
                LIMIT 1
                """,
                (symbol, _date_key(before_date)),
            ).fetchone()
        return (
            _futures_daily_row(row, FuturesBarState.FINAL)
            if row is not None else None
        )

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
        mappings = normalize_futures_mappings(mappings)
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
            contract_identities = {
                str(row[0]): FuturesContractMappingIdentity(
                    provider_symbol=str(row[1]),
                    listed_on=_date_from_key(int(row[2])),
                    last_trading_date=_date_from_key(int(row[3])),
                )
                for row in self._connection.execute(
                    """
                    SELECT instrument.symbol, contract.provider_symbol,
                           contract.listed_on,
                           contract.last_trading_date
                    FROM futures_contracts AS contract
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE instrument.symbol IN (
                    """ + ",".join("?" for _ in symbols) + ")",
                    sorted(symbols),
                )
            }
            validate_futures_mapping_identities(
                mappings, series_provider_symbols, contract_identities
            )
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

    def list_futures_roll_mappings_for_build(
        self,
        series_symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[FuturesRollMapping]:
        """Return an interval plus the latest mapping effective before it."""
        self._require_futures_storage()
        if start_date > end_date:
            return []
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
                WHERE series.symbol = ?
                  AND (
                    mapping.effective_from BETWEEN ? AND ?
                    OR mapping.effective_from = (
                      SELECT max(prior.effective_from)
                      FROM futures_roll_mappings AS prior
                      WHERE prior.series_instrument_id = mapping.series_instrument_id
                        AND prior.effective_from < ?
                    )
                  )
                ORDER BY mapping.effective_from
                """,
                (
                    series_symbol, _date_key(start_date), _date_key(end_date),
                    _date_key(start_date),
                ),
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
        mappings = normalize_futures_mappings(mappings)
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
            contract_identities = {
                str(row[0]): FuturesContractMappingIdentity(
                    provider_symbol=str(row[1]),
                    listed_on=_date_from_key(int(row[2])),
                    last_trading_date=_date_from_key(int(row[3])),
                )
                for row in self._connection.execute(
                    """
                    SELECT instrument.symbol, contract.provider_symbol,
                           contract.listed_on,
                           contract.last_trading_date
                    FROM futures_contracts AS contract
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE contract.instrument_id IN (
                    """
                    + ",".join("?" for _ in mapping_contract_symbols)
                    + ")",
                    [ids[item] for item in mapping_contract_symbols],
                )
            } if mappings else {}
            validate_futures_mapping_identities(
                mappings,
                {series_symbol: str(series_row[0])},
                contract_identities,
            )
            current_mappings = self._connection.execute(
                """
                SELECT effective_from, contract_instrument_id
                FROM futures_roll_mappings
                WHERE source_id = ? AND series_instrument_id = ?
                  AND effective_from BETWEEN ? AND ?
                ORDER BY effective_from
                """,
                (
                    source_id, ids[series_symbol],
                    _date_key(start_date), _date_key(end_date),
                ),
            ).fetchall()
            requested_mappings = [
                (_date_key(item.effective_from), ids[item.contract_symbol])
                for item in mappings
            ]
            if [tuple(row) for row in current_mappings] != requested_mappings:
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

