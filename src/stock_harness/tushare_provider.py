"""Configurable Tushare implementation of the minimal daily-data contract."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from typing import Any
from urllib.request import Request, urlopen

from stock_harness.config import TushareSettings, load_provider_token
from stock_harness.models import DailyBar, Instrument, InstrumentKind


class TushareDailyProvider:
    code = "tushare"

    def __init__(self, settings: TushareSettings, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client or _TushareHttpClient(
            load_provider_token(settings), settings.api_url, settings.timeout_seconds
        )
        self._minimum_interval = 60.0 / settings.requests_per_minute if settings.requests_per_minute > 0 else 0.0
        self._last_request_at: float | None = None

    def list_instruments(self) -> Sequence[Instrument]:
        rows: dict[str, Instrument] = {}
        fields = "ts_code,name,market,list_date,delist_date"
        for status in ("L", "D", "P"):
            payload = self._call("stock_basic", exchange="", list_status=status, fields=fields)
            for row in _iter_rows(payload):
                symbol = str(_field(row, "ts_code"))
                rows[symbol] = Instrument(
                    symbol=symbol,
                    name=str(_field(row, "name") or symbol),
                    kind=InstrumentKind.STOCK,
                    exchange=_exchange(symbol),
                    active=status == "L",
                )
        return sorted(rows.values(), key=lambda item: item.symbol)

    def trading_dates(self, start_date: date, end_date: date) -> list[date]:
        payload = self._call(
            "trade_cal",
            exchange="SSE",
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
            is_open="1",
            fields="cal_date,is_open",
        )
        dates = {
            _parse_compact_date(str(_field(row, "cal_date")))
            for row in _iter_rows(payload)
            if int(_field(row, "is_open")) == 1
        }
        return sorted(dates)

    def fetch_daily_bars(self, trade_date: date) -> Sequence[DailyBar]:
        payload = self._call(
            "daily",
            trade_date=_compact_date(trade_date),
            fields="ts_code,trade_date,open,high,low,close,vol",
        )
        bars: list[DailyBar] = []
        for row in _iter_rows(payload):
            bars.append(
                DailyBar(
                    symbol=str(_field(row, "ts_code")),
                    trade_date=_parse_compact_date(str(_field(row, "trade_date"))),
                    open=float(_field(row, "open")),
                    high=float(_field(row, "high")),
                    low=float(_field(row, "low")),
                    close=float(_field(row, "close")),
                    volume=int(round(float(_field(row, "vol")) * 100)),
                )
            )
        return bars

    def _call(self, method_name: str, **kwargs: object):
        attempts = self.settings.retries + 1
        for attempt in range(attempts):
            self._wait_for_rate_limit()
            try:
                return getattr(self._client, method_name)(**kwargs)
            except Exception:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(self.settings.retry_wait_seconds * (self.settings.backoff_multiplier**attempt))
        raise RuntimeError("provider request failed unexpectedly")

    def _wait_for_rate_limit(self) -> None:
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = self._minimum_interval - (now - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()


class _TushareHttpClient:
    """Minimal JSON client that avoids importing the pandas-based Tushare SDK."""

    def __init__(self, token: str, api_url: str, timeout_seconds: float) -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def __getattr__(self, api_name: str):
        def call(**kwargs: object) -> list[dict[str, object]]:
            fields = str(kwargs.pop("fields", ""))
            params = dict(kwargs)
            params.setdefault("ts_type_name", self._api_url)
            body = json.dumps(
                {
                    "api_name": api_name,
                    "token": self._token,
                    "params": params,
                    "fields": fields,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            request = Request(
                f"{self._api_url}/{api_name}",
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=self._timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
            if int(result.get("code", -1)) != 0:
                raise RuntimeError(f"Tushare API {api_name} failed: {result.get('msg', '')}")
            data = result.get("data") or {}
            columns = data.get("fields") or []
            items = data.get("items") or []
            return [dict(zip(columns, item, strict=True)) for item in items]

        return call


def _iter_rows(payload: Any) -> Iterator[Any]:
    if hasattr(payload, "itertuples"):
        yield from payload.itertuples(index=False)
        return
    yield from payload


def _field(row: Any, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return getattr(row, name)


def _compact_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _parse_compact_date(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _exchange(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1] if "." in symbol else "UNKNOWN"
