"""Configured Tushare adapters for canonical domestic-futures evidence."""

from __future__ import annotations

import re
import time
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from stock_harness.config import FuturesCanonicalProviderSettings, load_provider_token
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
    canonical_futures_continuous_symbol,
    canonical_futures_contract_symbol,
    canonical_futures_product_symbol,
)
from stock_harness.tushare_provider import TushareHttpClient


LOGGER = logging.getLogger(__name__)


FUTURES_BASIC_FIELDS = (
    "ts_code,symbol,exchange,name,fut_code,multiplier,trade_unit,per_unit,"
    "quote_unit,quote_unit_desc,d_mode_desc,list_date,delist_date,d_month,"
    "last_ddate,trade_time_desc"
)
FUTURES_DAILY_FIELDS = (
    "ts_code,trade_date,pre_close,pre_settle,open,high,low,close,settle,"
    "change1,change2,vol,amount,oi,oi_chg,delv_settle"
)


@dataclass(frozen=True, slots=True)
class FuturesCatalog:
    products: tuple[FuturesProduct, ...]
    contracts: tuple[FuturesContract, ...]
    continuous_series: tuple[FuturesContinuousSeries, ...]


@dataclass(frozen=True, slots=True)
class FuturesDailyRowRejection:
    trading_day: date
    reason_code: str


@dataclass(frozen=True, slots=True)
class FuturesDailyFetchResult:
    bars: tuple[FuturesDailyBar, ...]
    rejections: tuple[FuturesDailyRowRejection, ...]


class TushareFuturesProvider:
    code = "tushare-futures"

    def __init__(
        self,
        settings: FuturesCanonicalProviderSettings,
        client: Any | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or TushareHttpClient(
            load_provider_token(settings), settings.api_url, settings.timeout_seconds
        )
        self._minimum_interval = (
            60.0 / settings.requests_per_minute
            if settings.requests_per_minute > 0
            else 0.0
        )
        self._last_request_at: float | None = None

    def discover_exchange(
        self,
        exchange: FuturesExchange,
        as_of: date,
    ) -> FuturesCatalog:
        real_rows = self._rows(self._call(
            "fut_basic", exchange=exchange.value, fut_type="1", fields=FUTURES_BASIC_FIELDS,
        ))
        continuous_rows = self._rows(self._call(
            "fut_basic", exchange=exchange.value, fut_type="2", fields=FUTURES_BASIC_FIELDS,
        ))
        products: dict[str, FuturesProduct] = {}
        contracts: dict[str, FuturesContract] = {}
        for row in real_rows:
            row_exchange = _exchange_field(row)
            if row_exchange is not exchange:
                raise ValueError(
                    f"fut_basic exchange mismatch: expected {exchange.value}, got {row_exchange.value}"
                )
            product_code = _text(row, "fut_code").upper()
            product_symbol = canonical_futures_product_symbol(exchange, product_code)
            multiplier = _optional_float(row, "multiplier")
            per_unit = _optional_float(row, "per_unit")
            product = FuturesProduct(
                symbol=product_symbol,
                product_code=product_code,
                display_name=_product_name(_text(row, "name"), product_code),
                exchange=exchange,
                multiplier=multiplier,
                per_unit=per_unit,
                trading_unit=_text(row, "trade_unit"),
                quote_unit=_text(row, "quote_unit"),
            )
            product.validate()
            existing = products.get(product_symbol)
            if existing is not None and _product_contract(existing) != _product_contract(product):
                raise ValueError(f"inconsistent fut_basic product metadata: {product_code}")
            products[product_symbol] = existing or product

            contract_month = _contract_month(row)
            contract = FuturesContract(
                symbol=canonical_futures_contract_symbol(exchange, product_code, contract_month),
                provider_symbol=_text(row, "ts_code").upper(),
                product_symbol=product_symbol,
                display_name=_text(row, "name"),
                exchange=exchange,
                contract_month=contract_month,
                listed_on=_required_date(row, "list_date"),
                last_trading_date=_required_date(row, "delist_date"),
                delivery_date=_optional_date(row, "last_ddate"),
                multiplier=multiplier,
                per_unit=per_unit,
                trading_unit=_text(row, "trade_unit"),
                quote_unit=_text(row, "quote_unit"),
                lifecycle_status=_lifecycle(row, as_of),
            )
            contract.validate()
            if contract.symbol in contracts:
                raise ValueError(f"duplicate canonical futures contract: {contract.symbol}")
            contracts[contract.symbol] = contract

        series: dict[str, FuturesContinuousSeries] = {}
        for row in continuous_rows:
            row_exchange = _exchange_field(row)
            if row_exchange is not exchange:
                raise ValueError(
                    f"fut_basic exchange mismatch: expected {exchange.value}, got {row_exchange.value}"
                )
            product_code = _text(row, "fut_code").upper()
            product_symbol = canonical_futures_product_symbol(exchange, product_code)
            if product_symbol not in products:
                raise ValueError(f"continuous futures product has no real contracts: {product_code}")
            series_kind = (
                FuturesSeriesKind.MAIN
                if "主力" in _text(row, "name")
                else FuturesSeriesKind.CONTINUOUS
            )
            provider_symbol = _text(row, "ts_code").upper()
            series_variant = (
                "MAIN"
                if series_kind is FuturesSeriesKind.MAIN
                else _optional_text(row, "symbol") or provider_symbol.split(".", 1)[0]
            )
            item = FuturesContinuousSeries(
                symbol=canonical_futures_continuous_symbol(
                    exchange, product_code, series_variant, FuturesPriceBasis.RAW,
                ),
                provider_symbol=provider_symbol,
                product_symbol=product_symbol,
                display_name=_text(row, "name"),
                exchange=exchange,
                series_kind=series_kind,
                series_variant=series_variant.upper(),
                price_basis=FuturesPriceBasis.RAW,
                rule_version="tushare-fut-mapping-v1",
            )
            item.validate()
            if item.symbol in series:
                raise ValueError(f"duplicate canonical futures series: {item.symbol}")
            series[item.symbol] = item
        return FuturesCatalog(
            products=tuple(products[key] for key in sorted(products)),
            contracts=tuple(contracts[key] for key in sorted(contracts)),
            continuous_series=tuple(series[key] for key in sorted(series)),
        )

    def calendar(
        self,
        exchange: FuturesExchange,
        start_date: date,
        end_date: date,
    ) -> tuple[FuturesCalendarDay, ...]:
        if start_date > end_date:
            return ()
        rows = self._rows(self._call(
            "fut_trade_cal",
            exchange=exchange.value,
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
            fields="exchange,cal_date,is_open,pretrade_date",
        ))
        days: dict[date, FuturesCalendarDay] = {}
        for row in rows:
            if _exchange_field(row) is not exchange:
                raise ValueError("fut_trade_cal returned an unexpected exchange")
            calendar_date = _required_date(row, "cal_date")
            day = FuturesCalendarDay(
                exchange=exchange,
                calendar_date=calendar_date,
                is_open=int(_required(row, "is_open")) == 1,
                previous_trading_day=_optional_date(row, "pretrade_date"),
            )
            day.validate()
            days[calendar_date] = day
        return tuple(days[key] for key in sorted(days))

    def fetch_contract_daily(
        self,
        contract: FuturesContract,
        start_date: date,
        end_date: date,
    ) -> tuple[FuturesDailyBar, ...]:
        return self.fetch_contract_daily_result(
            contract, start_date, end_date
        ).bars

    def fetch_contract_daily_result(
        self,
        contract: FuturesContract,
        start_date: date,
        end_date: date,
    ) -> FuturesDailyFetchResult:
        if start_date > end_date:
            return FuturesDailyFetchResult((), ())
        bars: dict[date, FuturesDailyBar] = {}
        rejections: list[FuturesDailyRowRejection] = []
        for window_start, window_end in _date_windows(start_date, end_date, 1_800):
            rows = self._rows(self._call(
                "fut_daily",
                ts_code=contract.provider_symbol,
                start_date=_compact_date(window_start),
                end_date=_compact_date(window_end),
                fields=FUTURES_DAILY_FIELDS,
            ))
            for row in rows:
                provider_symbol = _text(row, "ts_code").upper()
                if provider_symbol != contract.provider_symbol.upper():
                    raise ValueError("fut_daily returned an unexpected contract")
                trading_day = _required_date(row, "trade_date")
                if not window_start <= trading_day <= window_end:
                    raise ValueError("fut_daily returned a date outside the requested window")
                try:
                    bar = FuturesDailyBar(
                        symbol=contract.symbol,
                        trading_day=trading_day,
                        provider_date=trading_day,
                        open=_number(row, "open"),
                        high=_number(row, "high"),
                        low=_number(row, "low"),
                        close=_number(row, "close"),
                        previous_close=_optional_float(row, "pre_close"),
                        settlement=_optional_float(row, "settle"),
                        previous_settlement=_optional_float(row, "pre_settle"),
                        volume_contracts=int(round(_number(row, "vol"))),
                        # Tushare documents futures amount in ten-thousand CNY.
                        amount=_scaled_optional(row, "amount", 10_000),
                        open_interest_contracts=_optional_float(row, "oi"),
                        open_interest_change_contracts=_optional_float(row, "oi_chg"),
                        delivery_settlement=_optional_float(row, "delv_settle"),
                        source=self.code,
                        state=FuturesBarState.FINAL,
                    )
                    bar.validate()
                except (TypeError, ValueError):
                    rejections.append(FuturesDailyRowRejection(
                        trading_day, "invalid-daily-bar"
                    ))
                    continue
                _insert_unique(bars, trading_day, bar, "fut_daily")
        if rejections:
            LOGGER.warning(
                "futures_provider_daily_rows_rejected symbol=%s count=%d first_date=%s last_date=%s",
                contract.symbol, len(rejections),
                min(item.trading_day for item in rejections),
                max(item.trading_day for item in rejections),
            )
        return FuturesDailyFetchResult(
            tuple(bars[key] for key in sorted(bars)), tuple(rejections)
        )

    def fetch_roll_mappings(
        self,
        series: FuturesContinuousSeries,
        contracts: Sequence[FuturesContract],
        start_date: date,
        end_date: date,
    ) -> tuple[FuturesRollMapping, ...]:
        if start_date > end_date:
            return ()
        by_provider = {item.provider_symbol.upper(): item for item in contracts}
        mappings: dict[date, FuturesRollMapping] = {}
        for window_start, window_end in _date_windows(start_date, end_date, 1_800):
            rows = self._rows(self._call(
                "fut_mapping",
                ts_code=series.provider_symbol,
                start_date=_compact_date(window_start),
                end_date=_compact_date(window_end),
                fields="ts_code,trade_date,mapping_ts_code",
            ))
            for row in rows:
                provider_series = _text(row, "ts_code").upper()
                if provider_series != series.provider_symbol.upper():
                    raise ValueError("fut_mapping returned an unexpected continuous series")
                mapped_provider = _text(row, "mapping_ts_code").upper()
                contract = by_provider.get(mapped_provider)
                if contract is None:
                    raise ValueError(f"fut_mapping references unknown real contract: {mapped_provider}")
                effective_from = _required_date(row, "trade_date")
                if not window_start <= effective_from <= window_end:
                    raise ValueError("fut_mapping returned a date outside the requested window")
                mapping = FuturesRollMapping(
                    series_symbol=series.symbol,
                    series_provider_symbol=provider_series,
                    effective_from=effective_from,
                    contract_symbol=contract.symbol,
                    contract_provider_symbol=mapped_provider,
                )
                mapping.validate()
                _insert_unique(mappings, effective_from, mapping, "fut_mapping")
        return tuple(mappings[key] for key in sorted(mappings))

    def _call(self, method_name: str, **kwargs: object) -> Any:
        attempts = self.settings.retries + 1
        for attempt in range(attempts):
            self._wait_for_rate_limit()
            started = time.perf_counter()
            try:
                result = getattr(self._client, method_name)(**kwargs)
                LOGGER.debug(
                    "futures_provider_call_completed method=%s attempt=%d duration_ms=%.3f",
                    method_name, attempt + 1, (time.perf_counter() - started) * 1000,
                )
                return result
            except Exception as exc:
                LOGGER.debug(
                    "futures_provider_call_failed method=%s attempt=%d duration_ms=%.3f error_type=%s",
                    method_name, attempt + 1, (time.perf_counter() - started) * 1000,
                    type(exc).__name__,
                )
                if attempt + 1 >= attempts:
                    raise
                time.sleep(
                    self.settings.retry_wait_seconds
                    * (self.settings.backoff_multiplier**attempt)
                )
        raise RuntimeError("futures Provider request failed unexpectedly")

    def _wait_for_rate_limit(self) -> None:
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = self._minimum_interval - (now - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _rows(payload: Any) -> list[Any]:
        if hasattr(payload, "itertuples"):
            return list(payload.itertuples(index=False))
        return list(payload)


def _required(row: Any, name: str) -> object:
    try:
        value = row[name] if isinstance(row, Mapping) else getattr(row, name)
    except (KeyError, AttributeError) as error:
        raise ValueError(f"Tushare futures response missing field: {name}") from error
    if value in (None, ""):
        raise ValueError(f"Tushare futures response has empty field: {name}")
    return value


def _text(row: Any, name: str) -> str:
    return str(_required(row, name)).strip()


def _number(row: Any, name: str) -> float:
    return float(_required(row, name))


def _optional_value(row: Any, name: str) -> object | None:
    try:
        value = row[name] if isinstance(row, Mapping) else getattr(row, name)
    except (KeyError, AttributeError) as error:
        raise ValueError(f"Tushare futures response missing field: {name}") from error
    return None if value in (None, "") else value


def _optional_float(row: Any, name: str) -> float | None:
    value = _optional_value(row, name)
    return None if value is None else float(value)


def _optional_text(row: Any, name: str) -> str | None:
    value = _optional_value(row, name)
    return None if value is None else str(value).strip().upper()


def _scaled_optional(row: Any, name: str, scale: float) -> float | None:
    value = _optional_float(row, name)
    return None if value is None else value * scale


def _required_date(row: Any, name: str) -> date:
    return _parse_date(str(_required(row, name)))


def _optional_date(row: Any, name: str) -> date | None:
    value = _optional_value(row, name)
    return None if value is None else _parse_date(str(value))


def _parse_date(value: str) -> date:
    text = value.strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"Tushare futures date must use YYYYMMDD: {value}")
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def _compact_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _exchange_field(row: Any) -> FuturesExchange:
    try:
        return FuturesExchange(_text(row, "exchange").upper())
    except ValueError as error:
        raise ValueError("Tushare futures response has unsupported exchange") from error


def _contract_month(row: Any) -> str:
    month = _text(row, "d_month")
    if len(month) != 6 or not month.isdigit():
        raise ValueError(f"fut_basic contract month must use YYYYMM: {month}")
    return month


def _lifecycle(row: Any, as_of: date) -> FuturesLifecycleStatus:
    listed_on = _required_date(row, "list_date")
    last_trading = _required_date(row, "delist_date")
    delivery = _optional_date(row, "last_ddate")
    if as_of < listed_on:
        return FuturesLifecycleStatus.PENDING
    if as_of <= last_trading:
        return FuturesLifecycleStatus.TRADING
    if delivery is not None and as_of >= delivery:
        return FuturesLifecycleStatus.DELIVERED
    return FuturesLifecycleStatus.EXPIRED


def _product_name(contract_name: str, fallback: str) -> str:
    return re.sub(r"\d+$", "", contract_name).strip() or fallback


def _product_contract(product: FuturesProduct) -> tuple[object, ...]:
    return (
        product.exchange,
        product.multiplier,
        product.per_unit,
        product.trading_unit,
        product.quote_unit,
    )


def _date_windows(
    start_date: date,
    end_date: date,
    window_days: int,
):
    current = start_date
    while current <= end_date:
        window_end = min(current + timedelta(days=window_days - 1), end_date)
        yield current, window_end
        current = window_end + timedelta(days=1)


def _insert_unique(target: dict[date, Any], key: date, value: Any, api_name: str) -> None:
    existing = target.get(key)
    if existing is not None and existing != value:
        raise ValueError(f"{api_name} returned conflicting duplicate date: {key}")
    target[key] = value
