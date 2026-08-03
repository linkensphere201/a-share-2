"""Configurable Tushare implementation of the minimal daily-data contract."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, timedelta
from typing import Any
from urllib.request import Request, urlopen

from stock_harness.config import TushareSettings, UniverseSymbol, load_provider_token
from stock_harness.models import (
    BoardMembership,
    CatalogEntry,
    DailyBar,
    EtfHolding,
    Instrument,
    InstrumentKind,
    MarketSnapshot,
    ProviderBarRejection,
)


LOGGER = logging.getLogger(__name__)


class TushareDailyProvider:
    code = "tushare"

    def __init__(self, settings: TushareSettings, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client or _TushareHttpClient(
            load_provider_token(settings), settings.api_url, settings.timeout_seconds
        )
        self._minimum_interval = 60.0 / settings.requests_per_minute if settings.requests_per_minute > 0 else 0.0
        self._last_request_at: float | None = None
        self.rejected_bars: tuple[ProviderBarRejection, ...] = ()

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
        self.rejected_bars = ()
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

    def list_etfs(self, selections: Sequence[UniverseSymbol]) -> Sequence[Instrument]:
        configured = {item.symbol for item in selections}
        rows: dict[str, Instrument] = {}
        fields = "ts_code,name,status,list_date,delist_date"
        for status in ("L", "D", "P"):
            payload = self._call("fund_basic", market="E", status=status, fields=fields)
            for row in _iter_rows(payload):
                symbol = str(_field(row, "ts_code"))
                if symbol not in configured:
                    continue
                rows[symbol] = Instrument(
                    symbol=symbol,
                    name=str(_field(row, "name") or symbol),
                    kind=InstrumentKind.ETF,
                    exchange=_exchange(symbol),
                    active=status == "L",
                )
        return _require_configured_instruments(selections, rows, "ETF")

    def list_all_equity_etfs(self, observed_on: date) -> Sequence[CatalogEntry]:
        entries: dict[str, CatalogEntry] = {}
        fields = "ts_code,name,fund_type,list_date,delist_date,status,market"
        for status in ("L", "D"):
            payload = self._call("fund_basic", market="E", status=status, fields=fields)
            for row in _iter_rows(payload):
                name = str(_field(row, "name") or "").strip()
                fund_type = str(_field(row, "fund_type") or "unknown").strip()
                if (
                    "ETF" not in name.upper()
                    or "\u8054\u63a5" in name
                    or fund_type not in {"股票型", "混合型", "其他"}
                ):
                    continue
                symbol = str(_field(row, "ts_code"))
                instrument = Instrument(
                    symbol=symbol,
                    name=name or symbol,
                    kind=InstrumentKind.ETF,
                    exchange=_exchange(symbol),
                    active=status == "L",
                )
                entries[symbol] = CatalogEntry(
                    instrument=instrument,
                    catalog_source="tushare",
                    source_system="tushare",
                    family="exchange_traded_equity_fund",
                    category=fund_type,
                    provider_symbol=symbol,
                    observed_on=observed_on,
                    listed_on=_optional_compact_date(_field(row, "list_date")),
                    delisted_on=_optional_compact_date(_field(row, "delist_date")),
                )
        if not entries:
            raise RuntimeError("Tushare returned no exchange-listed equity ETF catalog")
        return [entries[key] for key in sorted(entries)]

    def list_dc_boards(self, observed_on: date) -> Sequence[CatalogEntry]:
        catalog_date, index_rows = self._latest_dated_rows(
            "dc_index", observed_on, "ts_code,trade_date,name"
        )
        _category_date, daily_rows = self._latest_dated_rows(
            "dc_daily",
            observed_on,
            (
                "ts_code,trade_date,open,high,low,close,change,pct_change,vol,"
                "amount,swing,turnover_rate,category"
            ),
        )
        categories = {
            str(_field(row, "ts_code")): str(_field(row, "category") or "unknown")
            for row in daily_rows
        }
        entries = []
        for row in index_rows:
            symbol = str(_field(row, "ts_code"))
            name = str(_field(row, "name") or symbol)
            category = categories.get(symbol, "unknown")
            entries.append(
                CatalogEntry(
                    instrument=Instrument(
                        symbol, name, InstrumentKind.SECTOR, _exchange(symbol)
                    ),
                    catalog_source="tushare_dc",
                    source_system="eastmoney",
                    family="eastmoney_board",
                    category=category,
                    provider_symbol=symbol,
                    observed_on=catalog_date,
                )
            )
        if not entries:
            raise RuntimeError("Tushare returned no Eastmoney board catalog")
        return sorted(entries, key=lambda item: item.provider_symbol)

    def list_ths_boards(self, observed_on: date) -> Sequence[CatalogEntry]:
        payload = self._call(
            "ths_index",
            exchange="A",
            fields="ts_code,name,count,exchange,list_date,type",
        )
        entries = []
        for row in _iter_rows(payload):
            symbol = str(_field(row, "ts_code"))
            name = str(_field(row, "name") or symbol)
            category = str(_field(row, "type") or "unknown")
            count = _field(row, "count")
            entries.append(
                CatalogEntry(
                    instrument=Instrument(
                        symbol, name, InstrumentKind.SECTOR, _exchange(symbol)
                    ),
                    catalog_source="tushare_ths",
                    source_system="ths",
                    family="ths_board",
                    category=category,
                    provider_symbol=symbol,
                    observed_on=observed_on,
                    listed_on=_optional_compact_date(_field(row, "list_date")),
                    constituent_count=int(count) if count not in (None, "") else None,
                )
            )
        if not entries:
            raise RuntimeError("Tushare returned no THS board catalog")
        return sorted(entries, key=lambda item: item.provider_symbol)

    def fetch_board_daily_bars(
        self,
        source_system: str,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> Sequence[DailyBar]:
        api_name = {"eastmoney": "dc_daily", "ths": "ths_daily"}.get(source_system)
        if api_name is None:
            raise ValueError(f"unsupported board source system: {source_system}")
        bars: dict[date, DailyBar] = {}
        rejected: list[ProviderBarRejection] = []
        # Twenty calendar years stays below 5,000 A-share trading days and
        # therefore below the board endpoints' row limit while minimizing calls.
        for window_start, window_end in _date_windows(start_date, end_date, 7_300):
            payload = self._call(
                api_name,
                ts_code=symbol,
                start_date=_compact_date(window_start),
                end_date=_compact_date(window_end),
                fields="ts_code,trade_date,open,high,low,close,vol",
            )
            for row in _iter_rows(payload):
                trade_date = _parse_compact_date(str(_field(row, "trade_date")))
                if any(_field(row, field) in (None, "") for field in ("open", "high", "low", "close")):
                    continue
                bar = DailyBar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=float(_field(row, "open")),
                    high=float(_field(row, "high")),
                    low=float(_field(row, "low")),
                    close=float(_field(row, "close")),
                    volume=_scaled_volume(
                        _field(row, "vol"), 100 if source_system == "ths" else 1
                    ),
                )
                try:
                    bar.validate()
                except ValueError as exc:
                    rejected.append(ProviderBarRejection(symbol, trade_date, str(exc)))
                    continue
                bars[trade_date] = bar
        self.rejected_bars = tuple(rejected)
        return [bars[key] for key in sorted(bars)]

    def list_board_members(
        self, source_system: str, symbol: str, observed_on: date
    ) -> Sequence[BoardMembership]:
        if source_system == "eastmoney":
            payload = self._call(
                "dc_member",
                ts_code=symbol,
                trade_date=_compact_date(observed_on),
                fields="trade_date,ts_code,con_code,name",
            )
            source = "tushare_dc"
        elif source_system == "ths":
            payload = self._call(
                "ths_member", ts_code=symbol, fields="ts_code,con_code,con_name"
            )
            source = "tushare_ths"
        else:
            raise ValueError(f"unsupported board source system: {source_system}")
        return [
            BoardMembership(
                board_symbol=symbol,
                member_symbol=str(_field(row, "con_code")),
                member_name=str(
                    _field(row, "name") if source_system == "eastmoney" else _field(row, "con_name")
                ),
                source=source,
                observed_on=observed_on,
            )
            for row in _iter_rows(payload)
            if _field(row, "con_code") not in (None, "")
        ]

    def fetch_stock_market_snapshots(self, trade_date: date) -> Sequence[MarketSnapshot]:
        daily_rows = {
            str(_field(row, "ts_code")): row
            for row in _iter_rows(self._call(
                "daily",
                trade_date=_compact_date(trade_date),
                fields="ts_code,trade_date,pct_chg",
            ))
        }
        basic_rows = {
            str(_field(row, "ts_code")): row
            for row in _iter_rows(self._call(
                "daily_basic",
                trade_date=_compact_date(trade_date),
                fields="ts_code,trade_date,total_mv",
            ))
        }
        snapshots: list[MarketSnapshot] = []
        for symbol, row in daily_rows.items():
            row_date = _parse_compact_date(str(_field(row, "trade_date")))
            if row_date != trade_date:
                raise ValueError("stock market snapshot returned an unexpected trade date")
            total_mv = _field(basic_rows.get(symbol, {}), "total_mv")
            snapshots.append(MarketSnapshot(
                symbol=symbol,
                trade_date=trade_date,
                change_percent=float(_field(row, "pct_chg")),
                # Tushare daily_basic reports total_mv in ten-thousand CNY.
                total_market_cap=float(total_mv) * 10_000 if total_mv not in (None, "") else None,
            ))
        return snapshots

    def fetch_etf_holdings(
        self, etf_symbol: str, candidate_dates: Sequence[date]
    ) -> tuple[date | None, Sequence[EtfHolding]]:
        api_name = "etf_sh_cons" if etf_symbol.endswith(".SH") else "etf_sz_cons"
        if not etf_symbol.endswith((".SH", ".SZ")):
            raise ValueError(f"unsupported ETF exchange: {etf_symbol}")
        for candidate in candidate_dates:
            rows = list(_iter_rows(self._call(
                api_name,
                ts_code=etf_symbol,
                trade_date=_compact_date(candidate),
                fields="trade_date,ts_code,con_code,con_name,qty,exchange",
            )))
            if not rows:
                continue
            holdings: dict[str, EtfHolding] = {}
            for rank, row in enumerate(rows, start=1):
                row_etf = str(_field(row, "ts_code"))
                row_date = _parse_compact_date(str(_field(row, "trade_date")))
                if row_etf != etf_symbol or row_date != candidate:
                    raise ValueError("ETF holding response identity mismatch")
                symbol = str(_field(row, "con_code") or "").strip().upper()
                name = str(_field(row, "con_name") or "").strip()
                if not symbol or "申赎现金" in name or symbol == etf_symbol:
                    continue
                quantity_value = _field(row, "qty")
                holdings[symbol] = EtfHolding(
                    etf_symbol=etf_symbol,
                    holding_symbol=symbol,
                    holding_name=name or symbol,
                    as_of_date=candidate,
                    quantity=(
                        float(quantity_value)
                        if quantity_value not in (None, "", "-") else None
                    ),
                    rank=rank,
                )
            return candidate, list(holdings.values())
        return None, []

    def _latest_dated_rows(
        self, api_name: str, observed_on: date, fields: str
    ) -> tuple[date, list[Mapping[str, Any]]]:
        for offset in range(10):
            candidate = observed_on - timedelta(days=offset)
            rows = list(
                _iter_rows(
                    self._call(
                        api_name, trade_date=_compact_date(candidate), fields=fields
                    )
                )
            )
            if rows:
                return candidate, rows
        raise RuntimeError(f"{api_name} returned no dated catalog through {observed_on}")

    def list_broad_indices(
        self, selections: Sequence[UniverseSymbol]
    ) -> Sequence[Instrument]:
        configured = {item.symbol for item in selections}
        rows: dict[str, Instrument] = {}
        fields = "ts_code,name,market,list_date"
        for market in ("SSE", "SZSE", "CSI"):
            payload = self._call("index_basic", market=market, fields=fields)
            for row in _iter_rows(payload):
                symbol = str(_field(row, "ts_code"))
                if symbol not in configured:
                    continue
                rows[symbol] = Instrument(
                    symbol=symbol,
                    name=str(_field(row, "name") or symbol),
                    kind=InstrumentKind.INDEX,
                    exchange=_exchange(symbol),
                )
        return _require_configured_instruments(selections, rows, "index")

    def list_sectors(self, source: str, level: str) -> Sequence[Instrument]:
        payload = self._call(
            "index_classify",
            level=level,
            src=source,
            fields="index_code,industry_name,level,src",
        )
        instruments = [
            Instrument(
                symbol=str(_field(row, "index_code")),
                name=str(_field(row, "industry_name")),
                kind=InstrumentKind.SECTOR,
                exchange=_exchange(str(_field(row, "index_code"))),
            )
            for row in _iter_rows(payload)
            if str(_field(row, "level")) == level
        ]
        if not instruments:
            raise RuntimeError(f"Tushare returned no {source} {level} sector instruments")
        return sorted(instruments, key=lambda item: item.symbol)

    def fetch_symbol_daily_bars(
        self,
        kind: InstrumentKind,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> Sequence[DailyBar]:
        if start_date > end_date:
            return []
        api_name, volume_multiplier, window_days = {
            # fund_daily is capped at 5,000 rows per call.
            InstrumentKind.ETF: ("fund_daily", 100, 3_650),
            # A 30-year single-index series remains below index_daily's 8,000-row cap.
            InstrumentKind.INDEX: ("index_daily", 100, 11_000),
            # sw_daily is capped at 4,000 rows per call.
            InstrumentKind.SECTOR: ("sw_daily", 10_000, 3_650),
        }.get(kind, (None, None, None))
        if api_name is None or volume_multiplier is None or window_days is None:
            raise ValueError(f"unsupported configured-symbol kind: {kind}")

        bars: dict[date, DailyBar] = {}
        incomplete_dates: list[date] = []
        rejected_bars: list[ProviderBarRejection] = []
        for window_start, window_end in _date_windows(start_date, end_date, window_days):
            payload = self._call(
                api_name,
                ts_code=symbol,
                start_date=_compact_date(window_start),
                end_date=_compact_date(window_end),
                fields="ts_code,trade_date,open,high,low,close,vol",
            )
            for row in _iter_rows(payload):
                row_symbol = str(_field(row, "ts_code"))
                if row_symbol != symbol:
                    raise ValueError(
                        f"Tushare {api_name} returned unexpected symbol {row_symbol} for {symbol}"
                    )
                trade_date = _parse_compact_date(str(_field(row, "trade_date")))
                if any(_field(row, field) in (None, "") for field in ("open", "high", "low", "close")):
                    incomplete_dates.append(trade_date)
                    continue
                bar = DailyBar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=float(_field(row, "open")),
                    high=float(_field(row, "high")),
                    low=float(_field(row, "low")),
                    close=float(_field(row, "close")),
                    volume=_scaled_volume(_field(row, "vol"), volume_multiplier),
                )
                try:
                    bar.validate()
                except ValueError as exc:
                    rejected_bars.append(ProviderBarRejection(symbol, trade_date, str(exc)))
                    continue
                bars[trade_date] = bar
        if incomplete_dates:
            LOGGER.warning(
                "incomplete_provider_bars_skipped api=%s symbol=%s count=%d first=%s last=%s",
                api_name,
                symbol,
                len(incomplete_dates),
                min(incomplete_dates),
                max(incomplete_dates),
            )
        self.rejected_bars = tuple(rejected_bars)
        return [bars[key] for key in sorted(bars)]

    def fetch_daily_snapshot(
        self, kind: InstrumentKind, trade_date: date
    ) -> Sequence[DailyBar]:
        api_name, volume_multiplier = {
            InstrumentKind.ETF: ("fund_daily", 100),
            InstrumentKind.INDEX: ("index_daily", 100),
            InstrumentKind.SECTOR: ("sw_daily", 10_000),
        }.get(kind, (None, None))
        if api_name is None or volume_multiplier is None:
            raise ValueError(f"unsupported daily snapshot kind: {kind}")
        return self._fetch_snapshot(api_name, trade_date, volume_multiplier)

    def fetch_board_daily_snapshot(
        self, source_system: str, trade_date: date
    ) -> Sequence[DailyBar]:
        api_name, volume_multiplier = {
            "eastmoney": ("dc_daily", 1),
            "ths": ("ths_daily", 100),
        }.get(source_system, (None, None))
        if api_name is None or volume_multiplier is None:
            raise ValueError(f"unsupported board source system: {source_system}")
        return self._fetch_snapshot(api_name, trade_date, volume_multiplier)

    def _fetch_snapshot(
        self, api_name: str, trade_date: date, volume_multiplier: int
    ) -> Sequence[DailyBar]:
        payload = self._call(
            api_name,
            trade_date=_compact_date(trade_date),
            fields="ts_code,trade_date,open,high,low,close,vol",
        )
        bars: list[DailyBar] = []
        rejected: list[ProviderBarRejection] = []
        for row in _iter_rows(payload):
            if any(
                _field(row, field) in (None, "")
                for field in ("ts_code", "open", "high", "low", "close", "vol")
            ):
                continue
            symbol = str(_field(row, "ts_code"))
            row_date = _parse_compact_date(str(_field(row, "trade_date")))
            bar = DailyBar(
                symbol=symbol,
                trade_date=row_date,
                open=float(_field(row, "open")),
                high=float(_field(row, "high")),
                low=float(_field(row, "low")),
                close=float(_field(row, "close")),
                volume=_scaled_volume(_field(row, "vol"), volume_multiplier),
            )
            try:
                bar.validate()
                if row_date != trade_date:
                    raise ValueError("snapshot returned an unexpected trade date")
            except ValueError as exc:
                rejected.append(ProviderBarRejection(symbol, row_date, str(exc)))
            else:
                bars.append(bar)
        self.rejected_bars = tuple(rejected)
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


class TushareBoardDailyProvider:
    def __init__(self, provider: TushareDailyProvider, source_system: str) -> None:
        if source_system not in {"eastmoney", "ths"}:
            raise ValueError(f"unsupported board source system: {source_system}")
        self.provider = provider
        self.source_system = source_system
        self.code = "tushare_dc" if source_system == "eastmoney" else "tushare_ths"

    @property
    def rejected_bars(self) -> tuple[ProviderBarRejection, ...]:
        return self.provider.rejected_bars

    def trading_dates(self, start_date: date, end_date: date) -> list[date]:
        return self.provider.trading_dates(start_date, end_date)

    def fetch_symbol_daily_bars(
        self,
        kind: InstrumentKind,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> Sequence[DailyBar]:
        if kind is not InstrumentKind.SECTOR:
            raise ValueError("board Provider only accepts sector instruments")
        return self.provider.fetch_board_daily_bars(
            self.source_system, symbol, start_date, end_date
        )


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


def _optional_compact_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    return _parse_compact_date(str(value))


def _parse_compact_date(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _exchange(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1] if "." in symbol else "UNKNOWN"


def _require_configured_instruments(
    selections: Sequence[UniverseSymbol],
    instruments: Mapping[str, Instrument],
    label: str,
) -> list[Instrument]:
    missing = [item.symbol for item in selections if item.symbol not in instruments]
    if missing:
        raise RuntimeError(f"configured {label} symbols missing from Tushare: {', '.join(missing)}")
    return [instruments[item.symbol] for item in selections]


def _date_windows(
    start_date: date, end_date: date, window_days: int
) -> Iterator[tuple[date, date]]:
    current = start_date
    while current <= end_date:
        window_end = min(current + timedelta(days=window_days - 1), end_date)
        yield current, window_end
        current = window_end + timedelta(days=1)


def _scaled_volume(value: object, multiplier: int) -> int:
    if value in (None, ""):
        return 0
    return int(round(float(value) * multiplier))
