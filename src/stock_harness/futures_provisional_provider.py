"""Lightweight adapter for the endpoint used by AKShare futures_zh_spot."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from datetime import date, datetime, time as clock_time, timedelta, timezone
from typing import Protocol
from urllib.request import Request, urlopen

from stock_harness.config import FuturesProvisionalProviderSettings
from stock_harness.models import (
    FuturesBarState,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
)


CHINA_TIME = timezone(timedelta(hours=8))


class FuturesSpotClient(Protocol):
    def fetch(self, provider_codes: Sequence[str]) -> str: ...


class AkShareFuturesSpotProvider:
    """Selected-contract Sina adapter matching AKShare futures_zh_spot semantics."""

    code = "akshare-futures-zh-spot"

    def __init__(
        self,
        settings: FuturesProvisionalProviderSettings,
        client: FuturesSpotClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or _SinaFuturesSpotClient(settings.request_timeout_seconds)
        self.missing_contracts: tuple[str, ...] = ()

    def fetch(
        self,
        contracts: Sequence[FuturesContract],
        expected_trading_day: date | None = None,
        observed_at: datetime | None = None,
    ) -> tuple[FuturesDailyBar, ...]:
        unique = {item.symbol: item for item in contracts}
        if len(unique) != len(contracts):
            raise ValueError("duplicate futures contracts in provisional request")
        if len(unique) > self.settings.max_contracts:
            raise ValueError(
                f"at most {self.settings.max_contracts} provisional futures contracts are allowed"
            )
        by_code: dict[str, FuturesContract] = {}
        for contract in unique.values():
            code = _sina_contract_code(contract)
            if code in by_code:
                raise ValueError(f"ambiguous Sina futures contract code: {code}")
            by_code[code] = contract
        if not by_code:
            self.missing_contracts = ()
            return ()
        content = self._fetch_with_retry(tuple(by_code))
        received_at = observed_at or datetime.now(CHINA_TIME)
        bars: dict[str, FuturesDailyBar] = {}
        for provider_code, payload in re.findall(
            r'var hq_str_nf_([^=]+)="([^"]*)"', content
        ):
            code = provider_code.strip().upper()
            contract = by_code.get(code)
            if contract is None or not payload:
                continue
            fields = payload.split(",")
            bar = (
                _parse_financial(contract, fields, received_at, self.code)
                if contract.exchange is FuturesExchange.CFFEX
                else _parse_commodity(contract, fields, received_at, self.code)
            )
            if expected_trading_day is not None and bar.trading_day != expected_trading_day:
                raise ValueError(
                    "futures provisional trading-day mismatch: "
                    f"expected {expected_trading_day}, got {bar.trading_day} for {contract.symbol}"
                )
            bar.validate()
            bars[contract.symbol] = bar
        self.missing_contracts = tuple(
            item.symbol for code, item in by_code.items() if item.symbol not in bars
        )
        return tuple(bars[key] for key in sorted(bars))

    def _fetch_with_retry(self, codes: Sequence[str]) -> str:
        attempts = self.settings.retries + 1
        for attempt in range(attempts):
            try:
                return self._client.fetch(codes)
            except Exception:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(self.settings.retry_wait_seconds)
        raise RuntimeError("futures provisional Provider failed unexpectedly")


class _SinaFuturesSpotClient:
    _url = "https://hq.sinajs.cn/list="

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self, provider_codes: Sequence[str]) -> str:
        subscriptions = ",".join(f"nf_{item}" for item in provider_codes)
        request = Request(
            f"{self._url}{subscriptions}",
            headers={
                "Accept": "*/*",
                "Referer": "https://vip.stock.finance.sina.com.cn/",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read().decode("gbk", errors="replace")


def _parse_commodity(
    contract: FuturesContract,
    fields: Sequence[str],
    received_at: datetime,
    source: str,
) -> FuturesDailyBar:
    if len(fields) < 18:
        raise ValueError(f"commodity futures spot row has {len(fields)} fields")
    trading_day = _provider_date(fields[17])
    return FuturesDailyBar(
        symbol=contract.symbol,
        trading_day=trading_day,
        provider_date=trading_day,
        open=_number(fields[2], "open"),
        high=_number(fields[3], "high"),
        low=_number(fields[4], "low"),
        close=_number(fields[8], "current price"),
        previous_close=_optional_number(fields[5]),
        settlement=None,
        previous_settlement=_optional_number(fields[10]),
        volume_contracts=_integer(fields[14], "volume"),
        amount=None,
        open_interest_contracts=_optional_number(fields[13]),
        open_interest_change_contracts=None,
        delivery_settlement=None,
        source=source,
        state=FuturesBarState.PROVISIONAL,
        provider_time=_provider_time(fields[1], received_at),
    )


def _parse_financial(
    contract: FuturesContract,
    fields: Sequence[str],
    received_at: datetime,
    source: str,
) -> FuturesDailyBar:
    if len(fields) < 38:
        raise ValueError(f"financial futures spot row has {len(fields)} fields")
    trading_day = _provider_date(fields[36])
    return FuturesDailyBar(
        symbol=contract.symbol,
        trading_day=trading_day,
        provider_date=trading_day,
        open=_number(fields[0], "open"),
        high=_number(fields[1], "high"),
        low=_number(fields[2], "low"),
        close=_number(fields[3], "current price"),
        previous_close=None,
        settlement=None,
        previous_settlement=None,
        volume_contracts=_integer(fields[4], "volume"),
        amount=None,
        open_interest_contracts=_optional_number(fields[6]),
        open_interest_change_contracts=None,
        delivery_settlement=None,
        source=source,
        state=FuturesBarState.PROVISIONAL,
        provider_time=_provider_time(fields[37], received_at),
    )


def _sina_contract_code(contract: FuturesContract) -> str:
    code = contract.provider_symbol.split(".", 1)[0].strip().upper()
    if not code or not code.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"unsupported Sina futures contract code: {contract.provider_symbol}")
    return code


def _provider_date(value: str) -> date:
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"invalid futures spot trading date: {value}") from error


def _provider_time(value: str, received_at: datetime) -> datetime:
    text = value.strip()
    formats = ("%H%M%S", "%H:%M:%S")
    parsed: clock_time | None = None
    for format_string in formats:
        try:
            parsed = datetime.strptime(text, format_string).time()
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(f"invalid futures spot Provider time: {value}")
    localized = (
        received_at
        if received_at.tzinfo is not None
        else received_at.replace(tzinfo=CHINA_TIME)
    )
    # Provider trading day may be later than the night-session calendar date.
    calendar_day = localized.astimezone(CHINA_TIME).date()
    return datetime.combine(calendar_day, parsed, tzinfo=CHINA_TIME)


def _number(value: str, label: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"futures spot {label} must be positive")
    return parsed


def _optional_number(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    parsed = float(text)
    return parsed if parsed >= 0 else None


def _integer(value: str, label: str) -> int:
    parsed = int(round(float(value)))
    if parsed < 0:
        raise ValueError(f"futures spot {label} must be non-negative")
    return parsed
