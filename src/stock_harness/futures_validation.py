"""Read-only cross-source validation for canonical futures daily bars."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from math import isclose
from typing import Any, Protocol

from stock_harness.models import FuturesBarState, FuturesContract, FuturesDailyBar
from stock_harness.sqlite_store import SQLiteMarketDataStore


class FuturesDailyValidationProvider(Protocol):
    code: str

    def fetch_contract_daily(
        self, contract: FuturesContract, trade_date: date
    ) -> FuturesDailyBar | None: ...


@dataclass(frozen=True, slots=True)
class FuturesFieldDifference:
    field: str
    unit: str
    primary: float | int | None
    validator: float | int | None
    absolute_difference: float | None
    relative_difference: float | None
    matched: bool | None


@dataclass(frozen=True, slots=True)
class FuturesValidationResult:
    symbol: str
    provider_symbol: str
    exchange: str
    trade_date: date
    primary_source: str
    validator_source: str
    status: str
    fields: tuple[FuturesFieldDifference, ...]
    message: str


@dataclass(frozen=True, slots=True)
class FuturesValidationReport:
    schema_version: str
    primary_source: str
    requested_symbols: tuple[str, ...]
    trade_date: date
    price_abs_tolerance: float
    count_abs_tolerance: float
    results: tuple[FuturesValidationResult, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AkShareExchangeFuturesValidationProvider:
    """Exchange-published daily data exposed by AKShare's unified adapter."""

    code = "exchange-official-via-akshare"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def fetch_contract_daily(
        self, contract: FuturesContract, trade_date: date
    ) -> FuturesDailyBar | None:
        frame = self._get_client().get_futures_daily(
            start_date=trade_date.strftime("%Y%m%d"),
            end_date=trade_date.strftime("%Y%m%d"),
            market=contract.exchange.value,
        )
        rows = _records(frame)
        matching = [
            row for row in rows
            if _row_date(row, "date") == trade_date
            and _matches_contract(row.get("symbol"), contract, trade_date)
        ]
        if not matching:
            return None
        if len(matching) != 1:
            raise ValueError(f"exchange source returned duplicate rows: {contract.provider_symbol}")
        return _validation_bar(
            contract, trade_date, matching[0], self.code,
            open_interest_field="open_interest",
        )

    def _get_client(self) -> Any:
        if self._client is None:
            import akshare

            self._client = akshare
        return self._client


class AkShareSinaFuturesValidationProvider:
    """Public Sina futures chart history exposed through AKShare."""

    code = "sina-public-chart-via-akshare"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def fetch_contract_daily(
        self, contract: FuturesContract, trade_date: date
    ) -> FuturesDailyBar | None:
        provider_symbol = contract.provider_symbol.split(".", 1)[0].upper()
        frame = self._get_client().futures_zh_daily_sina(symbol=provider_symbol)
        matching = [row for row in _records(frame) if _row_date(row, "date") == trade_date]
        if not matching:
            return None
        if len(matching) != 1:
            raise ValueError(f"Sina source returned duplicate rows: {provider_symbol}")
        return _validation_bar(
            contract, trade_date, matching[0], self.code,
            open_interest_field="hold",
            zero_settlement_is_missing=True,
        )

    def _get_client(self) -> Any:
        if self._client is None:
            import akshare

            self._client = akshare
        return self._client


def validate_futures_contracts(
    store: SQLiteMarketDataStore,
    providers: Sequence[FuturesDailyValidationProvider],
    symbols: Sequence[str],
    trade_date: date,
    *,
    price_abs_tolerance: float = 0.001,
    count_abs_tolerance: float = 0.0,
) -> FuturesValidationReport:
    if price_abs_tolerance < 0 or count_abs_tolerance < 0:
        raise ValueError("futures validation tolerances must be non-negative")
    requested = tuple(dict.fromkeys(item.strip().upper() for item in symbols if item.strip()))
    contracts = {item.symbol: item for item in store.get_futures_contracts(requested)}
    results: list[FuturesValidationResult] = []
    for symbol in requested:
        contract = contracts.get(symbol)
        if contract is None:
            results.extend(
                _missing_contract_result(symbol, trade_date, provider.code)
                for provider in providers
            )
            continue
        primary_rows = store.list_futures_daily_bars(symbol, trade_date, trade_date)
        primary = primary_rows[0] if primary_rows else None
        for provider in providers:
            results.append(_validate_one(primary, contract, provider, trade_date,
                                         price_abs_tolerance, count_abs_tolerance))
    return FuturesValidationReport(
        schema_version="futures-cross-source-v1",
        primary_source="tushare-final-store",
        requested_symbols=requested,
        trade_date=trade_date,
        price_abs_tolerance=price_abs_tolerance,
        count_abs_tolerance=count_abs_tolerance,
        results=tuple(results),
    )


def _validate_one(
    primary: FuturesDailyBar | None,
    contract: FuturesContract,
    provider: FuturesDailyValidationProvider,
    trade_date: date,
    price_tolerance: float,
    count_tolerance: float,
) -> FuturesValidationResult:
    try:
        validator = provider.fetch_contract_daily(contract, trade_date)
    except Exception as exc:
        return FuturesValidationResult(
            contract.symbol, contract.provider_symbol, contract.exchange.value,
            trade_date, "tushare-final-store", provider.code, "error", (),
            f"{type(exc).__name__}: {exc}",
        )
    if primary is None or validator is None:
        return FuturesValidationResult(
            contract.symbol, contract.provider_symbol, contract.exchange.value,
            trade_date, "tushare-final-store", provider.code, "missing", (),
            f"primary={'present' if primary else 'missing'} "
            f"validator={'present' if validator else 'missing'}",
        )
    specs = (
        ("open", contract.quote_unit, price_tolerance),
        ("high", contract.quote_unit, price_tolerance),
        ("low", contract.quote_unit, price_tolerance),
        ("close", contract.quote_unit, price_tolerance),
        ("settlement", contract.quote_unit, price_tolerance),
        ("volume_contracts", "contracts", count_tolerance),
        ("open_interest_contracts", "contracts", count_tolerance),
    )
    fields = tuple(
        _difference(field, unit, getattr(primary, field), getattr(validator, field), tolerance)
        for field, unit, tolerance in specs
    )
    mismatches = [item.field for item in fields if item.matched is False]
    unavailable = [item.field for item in fields if item.matched is None]
    status = "mismatch" if mismatches else "partial" if unavailable else "match"
    message = (
        f"mismatched fields: {', '.join(mismatches)}"
        if mismatches else
        f"unavailable fields: {', '.join(unavailable)}"
        if unavailable else
        "all required fields matched"
    )
    return FuturesValidationResult(
        contract.symbol, contract.provider_symbol, contract.exchange.value,
        trade_date, "tushare-final-store", provider.code,
        status, fields, message,
    )


def _difference(
    field: str,
    unit: str,
    primary: float | int | None,
    validator: float | int | None,
    tolerance: float,
) -> FuturesFieldDifference:
    if primary is None or validator is None:
        return FuturesFieldDifference(field, unit, primary, validator, None, None, None)
    absolute = abs(float(primary) - float(validator))
    denominator = abs(float(primary))
    relative = absolute / denominator if denominator else (0.0 if absolute == 0 else None)
    return FuturesFieldDifference(
        field, unit, primary, validator, absolute, relative,
        isclose(float(primary), float(validator), rel_tol=0.0, abs_tol=tolerance),
    )


def _missing_contract_result(
    symbol: str, trade_date: date, provider_code: str
) -> FuturesValidationResult:
    return FuturesValidationResult(
        symbol, "", "", trade_date, "tushare-final-store", provider_code,
        "missing", (), "contract is not registered in the local futures catalog",
    )


def _validation_bar(
    contract: FuturesContract,
    trade_date: date,
    row: Mapping[str, Any],
    source: str,
    *,
    open_interest_field: str,
    zero_settlement_is_missing: bool = False,
) -> FuturesDailyBar:
    settlement = _optional_number(row, "settle")
    if zero_settlement_is_missing and settlement == 0:
        settlement = None
    bar = FuturesDailyBar(
        symbol=contract.symbol,
        trading_day=trade_date,
        provider_date=trade_date,
        open=_number(row, "open"), high=_number(row, "high"),
        low=_number(row, "low"), close=_number(row, "close"),
        previous_close=None,
        settlement=settlement,
        previous_settlement=_optional_number(row, "pre_settle"),
        volume_contracts=int(round(_number(row, "volume"))),
        amount=None,
        open_interest_contracts=_optional_number(row, open_interest_field),
        open_interest_change_contracts=None,
        delivery_settlement=None,
        source=source,
        state=FuturesBarState.FINAL,
    )
    bar.validate()
    return bar


def _records(frame: Any) -> list[Mapping[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, list):
        return frame
    if getattr(frame, "empty", False):
        return []
    records = frame.to_dict("records")
    return list(records)


def _row_date(row: Mapping[str, Any], field: str) -> date:
    value = row.get(field)
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _matches_contract(value: Any, contract: FuturesContract, trade_date: date) -> bool:
    observed = "".join(character for character in str(value).upper() if character.isalnum())
    expected = contract.provider_symbol.split(".", 1)[0].upper()
    if observed == expected:
        return True
    product = "".join(character for character in expected if character.isalpha())
    four_digit = contract.contract_month[-4:]
    if contract.exchange.value == "CZCE" and observed in {
        product + four_digit,
        product + four_digit[-3:],
    }:
        return str(trade_date.year)[-1] == four_digit[1]
    return False


def _number(row: Mapping[str, Any], field: str) -> float:
    value = _optional_number(row, field)
    if value is None:
        raise ValueError(f"validation source missing field: {field}")
    return value


def _optional_number(row: Mapping[str, Any], field: str) -> float | None:
    value = row.get(field)
    if value is None or str(value).strip() in {"", "-", "--", "nan", "None"}:
        return None
    return float(value)
