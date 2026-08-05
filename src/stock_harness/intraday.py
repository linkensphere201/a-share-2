"""Failure-isolated provisional daily bars for selected active-workspace symbols."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stock_harness.config import IntradaySettings
from stock_harness.models import ProvisionalDailyBar


LOGGER = logging.getLogger(__name__)
CHINA_TIME = timezone(timedelta(hours=8))


class IntradayQuoteProvider(Protocol):
    code: str

    def fetch(self, symbols: Sequence[str]) -> Sequence[ProvisionalDailyBar]: ...


class EastmoneySelectedQuoteProvider:
    """Lightweight selected-symbol adapter for the endpoint used by AKShare quotes."""

    code = "eastmoney_selected"
    _url = "https://push2.eastmoney.com/api/qt/ulist.np/get"

    def __init__(self, timeout_seconds: float = 8.0, chunk_size: int = 100) -> None:
        self.timeout_seconds = timeout_seconds
        self.chunk_size = max(1, min(200, chunk_size))

    def fetch(self, symbols: Sequence[str]) -> Sequence[ProvisionalDailyBar]:
        secids = [(symbol, _eastmoney_secid(symbol)) for symbol in symbols]
        supported = [(symbol, secid) for symbol, secid in secids if secid is not None]
        rows: list[ProvisionalDailyBar] = []
        for start in range(0, len(supported), self.chunk_size):
            rows.extend(self._fetch_chunk(supported[start : start + self.chunk_size]))
        return rows

    @staticmethod
    def supports(symbol: str) -> bool:
        return _eastmoney_secid(symbol) is not None

    def _fetch_chunk(self, symbols: Sequence[tuple[str, str]]) -> list[ProvisionalDailyBar]:
        params = urlencode({
            "secids": ",".join(secid for _, secid in symbols),
            "fields": "f2,f3,f5,f6,f12,f13,f14,f15,f16,f17,f18,f124",
            "fltt": "2",
            "invt": "2",
        })
        request = Request(
            f"{self._url}?{params}",
            headers={"Accept": "application/json"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data") or {}
        quote_rows = data.get("diff") or []
        requested = {secid: symbol for symbol, secid in symbols}
        received_at = datetime.now(CHINA_TIME)
        bars: list[ProvisionalDailyBar] = []
        for row in quote_rows:
            secid = f"{int(_number(row.get('f13')))}.{row.get('f12')}"
            symbol = requested.get(secid)
            if symbol is None:
                continue
            bar = _parse_quote(symbol, row, received_at, self.code)
            if bar is not None:
                bars.append(bar)
        return bars


class SinaSelectedQuoteProvider:
    code = "sina_selected"
    _url = "https://hq.sinajs.cn/list="

    def __init__(self, timeout_seconds: float = 8.0, chunk_size: int = 100) -> None:
        self.timeout_seconds = timeout_seconds
        self.chunk_size = max(1, min(200, chunk_size))

    @staticmethod
    def supports(symbol: str) -> bool:
        return _sina_symbol(symbol) is not None

    def fetch(self, symbols: Sequence[str]) -> Sequence[ProvisionalDailyBar]:
        mapped = [(symbol, _sina_symbol(symbol)) for symbol in symbols]
        supported = [(symbol, code) for symbol, code in mapped if code is not None]
        rows: list[ProvisionalDailyBar] = []
        for start in range(0, len(supported), self.chunk_size):
            rows.extend(self._fetch_chunk(supported[start : start + self.chunk_size]))
        return rows

    def _fetch_chunk(self, symbols: Sequence[tuple[str, str]]) -> list[ProvisionalDailyBar]:
        request = Request(
            f"{self._url}{','.join(code for _, code in symbols)}",
            headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            content = response.read().decode("gbk", errors="replace")
        requested = {code: symbol for symbol, code in symbols}
        received_at = datetime.now(CHINA_TIME)
        bars: list[ProvisionalDailyBar] = []
        for provider_symbol, values in re.findall(r'var hq_str_([^=]+)="([^"]*)";', content):
            symbol = requested.get(provider_symbol)
            fields = values.split(",")
            if symbol is None or len(fields) < 32:
                continue
            bar = _parse_sina_quote(symbol, fields, received_at, self.code)
            if bar is not None:
                bars.append(bar)
        return bars


class FallbackSelectedQuoteProvider:
    code = "selected_quote_fallback"

    def __init__(self, timeout_seconds: float = 8.0) -> None:
        self.primary = EastmoneySelectedQuoteProvider(timeout_seconds)
        self.fallback = SinaSelectedQuoteProvider(timeout_seconds)
        self._primary_failed = False
        self._fallback_failed = False

    def supports(self, symbol: str) -> bool:
        return self.primary.supports(symbol) or self.fallback.supports(symbol)

    def fetch(self, symbols: Sequence[str]) -> Sequence[ProvisionalDailyBar]:
        primary_rows: Sequence[ProvisionalDailyBar] = ()
        try:
            primary_rows = self.primary.fetch(symbols)
            if self._primary_failed:
                LOGGER.info("intraday_primary_provider_recovered provider=%s", self.primary.code)
            self._primary_failed = False
        except Exception as exc:
            if not self._primary_failed:
                LOGGER.warning("intraday_primary_provider_failed provider=%s error=%s", self.primary.code, exc)
            self._primary_failed = True
        received = {bar.symbol: bar for bar in primary_rows}
        missing = [symbol for symbol in symbols if symbol not in received and self.fallback.supports(symbol)]
        if missing:
            try:
                received.update({bar.symbol: bar for bar in self.fallback.fetch(missing)})
                if self._fallback_failed:
                    LOGGER.info("intraday_fallback_provider_recovered provider=%s", self.fallback.code)
                self._fallback_failed = False
            except Exception as exc:
                first_failure = not self._fallback_failed
                self._fallback_failed = True
                if not received:
                    raise RuntimeError(f"both selected quote providers failed: {exc}") from exc
                if first_failure:
                    LOGGER.warning("intraday_fallback_provider_partial_failure provider=%s error=%s", self.fallback.code, exc)
        return list(received.values())


class IntradayQuoteService:
    def __init__(
        self,
        settings: IntradaySettings,
        trading_day: Callable[[date], bool],
        provider: IntradayQuoteProvider | None = None,
    ) -> None:
        self.settings = settings
        self._trading_day = trading_day
        self._provider = provider or FallbackSelectedQuoteProvider(
            settings.request_timeout_seconds
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name="stock-harness-intraday", daemon=True
        )
        self._symbols: tuple[str, ...] = ()
        self._group_id: str | None = None
        self._cache: dict[str, ProvisionalDailyBar] = {}
        self._state = "idle"
        self._last_error: str | None = None
        self._last_attempt_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._consecutive_failures = 0
        self._circuit_open_until: datetime | None = None
        self._unsupported_symbols: tuple[str, ...] = ()
        self._missing_symbols: tuple[str, ...] = ()

    def start(self) -> None:
        if self.settings.enabled and not self._thread.is_alive():
            LOGGER.info("intraday_service_start interval_seconds=%s", self.settings.poll_interval_seconds)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(10.0, self.settings.request_timeout_seconds + 2.0))
        LOGGER.info("intraday_service_stop")

    def subscribe(self, group_id: str, symbols: Iterable[str]) -> dict[str, object]:
        normalized = tuple(sorted({item.strip().upper() for item in symbols if item.strip()}))
        if len(normalized) > self.settings.max_symbols:
            raise ValueError(f"at most {self.settings.max_symbols} intraday symbols are allowed")
        with self._lock:
            previous = set(self._symbols)
            current = set(normalized)
            previous_unsupported = self._unsupported_symbols
            self._symbols = normalized
            self._group_id = group_id
            self._unsupported_symbols = tuple(
                symbol for symbol in normalized
                if hasattr(self._provider, "supports") and not self._provider.supports(symbol)  # type: ignore[attr-defined]
            )
            for removed in previous - current:
                self._cache.pop(removed, None)
            added_count = len(current - previous)
            removed_count = len(previous - current)
        LOGGER.info(
            "intraday_subscription_changed group_id=%s symbols=%s added=%s removed=%s",
            group_id, len(normalized), added_count, removed_count,
        )
        if self._unsupported_symbols and self._unsupported_symbols != previous_unsupported:
            LOGGER.warning(
                "intraday_symbols_unsupported count=%s symbols=%s",
                len(self._unsupported_symbols), ",".join(self._unsupported_symbols[:10]),
            )
        self._wake.set()
        return self.status()

    def get(self, symbol: str, now: datetime | None = None) -> dict[str, object] | None:
        now = now or datetime.now(CHINA_TIME)
        with self._lock:
            bar = self._cache.get(symbol.upper())
        if bar is None:
            return None
        age_seconds = max(0.0, (now - bar.received_at).total_seconds())
        result = asdict(bar)
        result.update({
            "bar_state": "intraday",
            "stale": age_seconds > self.settings.stale_after_seconds,
            "age_seconds": round(age_seconds, 1),
        })
        return result

    def list(self, symbols: Iterable[str]) -> list[dict[str, object]]:
        return [item for symbol in symbols if (item := self.get(symbol)) is not None]

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._state,
                "enabled": self.settings.enabled,
                "group_id": self._group_id,
                "symbol_count": len(self._symbols),
                "cached_count": len(self._cache),
                "last_attempt_at": _iso(self._last_attempt_at),
                "last_success_at": _iso(self._last_success_at),
                "last_error": self._last_error,
                "consecutive_failures": self._consecutive_failures,
                "circuit_open_until": _iso(self._circuit_open_until),
                "unsupported_count": len(self._unsupported_symbols),
                "missing_count": len(self._missing_symbols),
            }

    def refresh_once(self, now: datetime | None = None) -> None:
        now = now or datetime.now(CHINA_TIME)
        with self._lock:
            symbols = self._symbols
        with self._refresh_lock:
            self._refresh_symbols(symbols, now, track_missing=True)

    def refresh_symbols(
        self, symbols: Iterable[str], now: datetime | None = None
    ) -> list[dict[str, object]]:
        normalized = tuple(sorted({item.strip().upper() for item in symbols if item.strip()}))
        if len(normalized) > self.settings.max_symbols:
            raise ValueError(f"at most {self.settings.max_symbols} intraday symbols are allowed")
        now = now or datetime.now(CHINA_TIME)
        with self._refresh_lock:
            self._refresh_symbols(normalized, now, track_missing=False)
        return self.list(normalized)

    def _refresh_symbols(
        self,
        symbols: tuple[str, ...],
        now: datetime,
        *,
        track_missing: bool,
    ) -> None:
        with self._lock:
            circuit_open_until = self._circuit_open_until
        if not self.settings.enabled:
            self._set_state("disabled")
            return
        if not symbols:
            self._set_state("idle")
            return
        if not is_market_polling_time(now) or not self._trading_day(now.date()):
            self._set_state("market_closed")
            return
        if circuit_open_until is not None and now < circuit_open_until:
            self._set_state("circuit_open")
            return
        with self._lock:
            self._state = "refreshing"
            self._last_attempt_at = now
        try:
            requested = tuple(
                symbol for symbol in symbols
                if not hasattr(self._provider, "supports") or self._provider.supports(symbol)  # type: ignore[attr-defined]
            )
            if not requested:
                self._set_state("unsupported")
                return
            bars = self._provider.fetch(requested)
            received = {bar.symbol: bar for bar in bars}
            with self._lock:
                previous_missing = self._missing_symbols
                if track_missing:
                    self._missing_symbols = tuple(sorted(set(requested) - set(received)))
                self._cache.update(received)
                self._last_success_at = now
                self._last_error = None
                self._consecutive_failures = 0
                self._circuit_open_until = None
                self._state = "ready" if len(received) == len(requested) else "partial"
            missing_symbols = tuple(sorted(set(requested) - set(received)))
            missing = len(missing_symbols)
            if missing:
                if not track_missing or self._missing_symbols != previous_missing:
                    LOGGER.warning(
                        "intraday_partial provider=%s requested=%s received=%s missing=%s manual=%s",
                        self._provider.code, len(requested), len(received), missing, not track_missing,
                    )
            else:
                if track_missing and previous_missing:
                    LOGGER.info("intraday_missing_symbols_recovered count=%s", len(previous_missing))
                LOGGER.info(
                    "intraday_refresh_ok provider=%s symbols=%s manual=%s",
                    self._provider.code, len(received), not track_missing,
                )
        except Exception as exc:
            with self._lock:
                self._consecutive_failures += 1
                self._last_error = str(exc)
                self._state = "error"
                if self._consecutive_failures >= self.settings.circuit_breaker_failures:
                    self._circuit_open_until = now + timedelta(
                        seconds=self.settings.circuit_breaker_seconds
                    )
                    self._state = "circuit_open"
            LOGGER.warning(
                "intraday_refresh_failed provider=%s symbols=%s failures=%s error=%s",
                self._provider.code, len(symbols), self._consecutive_failures, exc,
                exc_info=True,
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.settings.poll_interval_seconds)
            self._wake.clear()
            if self._stop.is_set():
                break
            self.refresh_once()

    def _set_state(self, value: str) -> None:
        with self._lock:
            self._state = value


def is_market_polling_time(now: datetime) -> bool:
    current = now.timetz().replace(tzinfo=None)
    return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)


def _eastmoney_secid(symbol: str) -> str | None:
    code, separator, suffix = symbol.upper().partition(".")
    if not separator:
        return None
    market = {"SH": "1", "SZ": "0", "BJ": "0", "DC": "90", "CSI": "2"}.get(suffix)
    return f"{market}.{code}" if market is not None else None


def _sina_symbol(symbol: str) -> str | None:
    code, separator, suffix = symbol.upper().partition(".")
    if not separator:
        return None
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suffix)
    return f"{prefix}{code}" if prefix is not None else None


def _parse_quote(
    symbol: str,
    row: dict[str, object],
    received_at: datetime,
    source: str,
) -> ProvisionalDailyBar | None:
    close = _number(row.get("f2"))
    high = _number(row.get("f15"))
    low = _number(row.get("f16"))
    open_price = _number(row.get("f17"))
    previous_close = _number(row.get("f18"))
    if min(close, high, low, open_price, previous_close) <= 0:
        return None
    if low > min(open_price, close) or high < max(open_price, close):
        return None
    provider_seconds = int(_number(row.get("f124")))
    provider_time = datetime.fromtimestamp(provider_seconds, CHINA_TIME)
    return ProvisionalDailyBar(
        symbol=symbol,
        trade_date=provider_time.date(),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=max(0, int(round(_number(row.get("f5")) * 100))),
        amount=max(0.0, _number(row.get("f6"))),
        previous_close=previous_close,
        change_percent=_number(row.get("f3")),
        source=source,
        provider_time=provider_time,
        received_at=received_at,
    )


def _parse_sina_quote(
    symbol: str,
    fields: list[str],
    received_at: datetime,
    source: str,
) -> ProvisionalDailyBar | None:
    open_price = _number(fields[1])
    previous_close = _number(fields[2])
    close = _number(fields[3])
    high = _number(fields[4])
    low = _number(fields[5])
    if min(close, high, low, open_price, previous_close) <= 0:
        return None
    if low > min(open_price, close) or high < max(open_price, close):
        return None
    provider_time = datetime.strptime(
        f"{fields[30]} {fields[31]}", "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=CHINA_TIME)
    return ProvisionalDailyBar(
        symbol=symbol,
        trade_date=provider_time.date(),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=max(0, int(round(_number(fields[8])))),
        amount=max(0.0, _number(fields[9])),
        previous_close=previous_close,
        change_percent=(close - previous_close) / previous_close * 100,
        source=source,
        provider_time=provider_time,
        received_at=received_at,
    )


def _number(value: object) -> float:
    if value in (None, "", "-"):
        return 0.0
    return float(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
