"""Lightweight adapter for the endpoint used by AKShare futures_zh_spot."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time as clock_time, timedelta, timezone
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stock_harness.config import FuturesProvisionalProviderSettings
from stock_harness.models import (
    FuturesBarState,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
)


CHINA_TIME = timezone(timedelta(hours=8))
CFFEX_REALTIME_NODES = {
    "IF": "qz_qh",
    "TF": "gz_qh",
    "T": "sngz_qh",
    "IH": "szgz_qh",
    "IC": "zzgz_qh",
    "TS": "engz_qh",
    "IM": "im_qh",
}
REALTIME_PRODUCT_NAME_ALIASES = {
    (FuturesExchange.DCE, "P"): "棕榈",
    (FuturesExchange.DCE, "PG"): "液化石油气",
    (FuturesExchange.DCE, "PP"): "PP",
    (FuturesExchange.CZCE, "AP"): "鲜苹果",
    (FuturesExchange.CZCE, "CF"): "棉花",
    (FuturesExchange.CZCE, "MA"): "郑醇",
    (FuturesExchange.CZCE, "PR"): "瓶级聚酯切片",
    (FuturesExchange.CZCE, "PX"): "二甲苯",
    (FuturesExchange.SHFE, "AD"): "铸造铝合金期货",
    (FuturesExchange.SHFE, "AG"): "白银",
    (FuturesExchange.SHFE, "AU"): "黄金",
    (FuturesExchange.SHFE, "OP"): "胶版印刷纸期货",
    (FuturesExchange.INE, "EC"): "集运指数(欧线)期货",
}


class FuturesSpotClient(Protocol):
    def fetch(self, provider_codes: Sequence[str]) -> str: ...


class FuturesRealtimeClient(Protocol):
    def node_catalog(self) -> Mapping[str, str]: ...

    def fetch_node(self, node: str) -> Sequence[Mapping[str, object]]: ...


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


class AkShareFuturesRealtimeProvider:
    """Explicit fallback matching AKShare futures_zh_realtime semantics."""

    code = "akshare-futures-zh-realtime"

    def __init__(
        self,
        settings: FuturesProvisionalProviderSettings,
        product_display_names: Mapping[str, str],
        client: FuturesRealtimeClient | None = None,
    ) -> None:
        self.settings = settings
        self._product_display_names = product_display_names
        self._client = client or _SinaFuturesRealtimeClient(
            settings.request_timeout_seconds
        )
        self.missing_contracts: tuple[str, ...] = ()

    def fetch(
        self,
        contracts: Sequence[FuturesContract],
        expected_trading_day: date | None = None,
        observed_at: datetime | None = None,
    ) -> tuple[FuturesDailyBar, ...]:
        unique = {item.symbol: item for item in contracts}
        if len(unique) != len(contracts):
            raise ValueError("duplicate futures contracts in fallback request")
        if len(unique) > self.settings.max_contracts:
            raise ValueError(
                f"at most {self.settings.max_contracts} fallback futures contracts are allowed"
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

        catalog = self._fetch_with_retry(self._client.node_catalog)
        by_node: dict[str, set[str]] = {}
        for code, contract in by_code.items():
            node = _realtime_node(contract, self._product_display_names, catalog)
            by_node.setdefault(node, set()).add(code)

        received_at = observed_at or datetime.now(CHINA_TIME)
        bars: dict[str, FuturesDailyBar] = {}
        for node in sorted(by_node):
            rows = self._fetch_with_retry(lambda node=node: self._client.fetch_node(node))
            for row in rows:
                code = str(row.get("symbol", "")).strip().upper()
                if code not in by_node[node]:
                    continue
                contract = by_code[code]
                bar = _parse_realtime(contract, row, received_at, self.code)
                if expected_trading_day is not None and bar.trading_day != expected_trading_day:
                    raise ValueError(
                        "futures fallback trading-day mismatch: "
                        f"expected {expected_trading_day}, got {bar.trading_day} "
                        f"for {contract.symbol}"
                    )
                bar.validate()
                bars[contract.symbol] = bar
        self.missing_contracts = tuple(
            item.symbol for item in by_code.values() if item.symbol not in bars
        )
        return tuple(bars[key] for key in sorted(bars))

    def _fetch_with_retry(self, operation):
        attempts = self.settings.retries + 1
        for attempt in range(attempts):
            try:
                return operation()
            except Exception:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(self.settings.retry_wait_seconds)
        raise RuntimeError("futures realtime fallback failed unexpectedly")


class AkShareFuturesMainContractReporter:
    """Bounded AKShare-compatible main-contract evidence for selected products."""

    code = "akshare-match-main-contract"

    def __init__(
        self,
        settings: FuturesProvisionalProviderSettings,
        product_display_names: Mapping[str, str],
        client: FuturesRealtimeClient | None = None,
    ) -> None:
        self.settings = settings
        self._product_display_names = product_display_names
        self._client = client or _SinaFuturesRealtimeClient(
            settings.request_timeout_seconds
        )
        self._catalog: Mapping[str, str] | None = None

    def update_product_display_names(self, values: Mapping[str, str]) -> None:
        self._product_display_names = dict(values)

    def report(self, contracts: Sequence[FuturesContract]) -> dict[str, str]:
        selected = {item.product_symbol: item for item in contracts}
        if len(selected) > self.settings.max_contracts:
            raise ValueError("main-contract evidence exceeds configured maximum")
        if not selected:
            return {}
        catalog = self._catalog or self._fetch_with_retry(self._client.node_catalog)
        self._catalog = catalog
        result: dict[str, str] = {}
        for product_symbol, contract in selected.items():
            node = _realtime_node(contract, self._product_display_names, catalog)
            rows = self._fetch_with_retry(lambda node=node: self._client.fetch_node(node))
            reported = _reported_main_contract(rows)
            if reported is not None:
                result[product_symbol] = reported
        return result

    def _fetch_with_retry(self, operation):
        attempts = self.settings.retries + 1
        for attempt in range(attempts):
            try:
                return operation()
            except Exception:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(self.settings.retry_wait_seconds)
        raise RuntimeError("futures main-contract evidence failed unexpectedly")


class _SinaFuturesRealtimeClient:
    _catalog_url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/view/js/"
        "qihuohangqing.js"
    )
    _realtime_url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQFuturesData"
    )

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def node_catalog(self) -> Mapping[str, str]:
        text = self._request(self._catalog_url).decode("gbk", errors="replace")
        pairs = re.findall(
            r"\[\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+_qh)['\"]",
            text,
        )
        catalog = {name.strip(): node.strip() for name, node in pairs}
        if not catalog:
            raise ValueError("empty Sina futures realtime node catalog")
        return catalog

    def fetch_node(self, node: str) -> Sequence[Mapping[str, object]]:
        query = urlencode({
            "page": "1", "num": "5", "sort": "position", "asc": "0",
            "node": node, "base": "futures",
        })
        payload = json.loads(
            self._request(f"{self._realtime_url}?{query}").decode("utf-8")
        )
        if not isinstance(payload, list):
            raise ValueError("invalid Sina futures realtime response")
        if not all(isinstance(item, dict) for item in payload):
            raise ValueError("invalid Sina futures realtime row")
        return payload

    def _request(self, url: str) -> bytes:
        request = Request(
            url,
            headers={
                "Accept": "*/*",
                "Referer": "https://vip.stock.finance.sina.com.cn/",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read()


def _reported_main_contract(
    rows: Sequence[Mapping[str, object]],
) -> str | None:
    if len(rows) == 1:
        value = str(rows[0].get("symbol", "")).strip().upper()
        return value or None
    seen: set[tuple[str, ...]] = set()
    evidence_fields = (
        "trade", "settlement", "presettlement", "open", "high", "low",
        "close", "volume", "position", "ticktime", "tradedate",
    )
    for row in rows:
        fingerprint = tuple(str(row.get(field, "")).strip() for field in evidence_fields)
        symbol = str(row.get("symbol", "")).strip().upper()
        if symbol and sum(bool(item) for item in fingerprint) >= 6 and fingerprint in seen:
            return symbol
        if sum(bool(item) for item in fingerprint) >= 6:
            seen.add(fingerprint)
    return None


def futures_provider_contract_code(contract: FuturesContract) -> str:
    return _sina_contract_code(contract)


def _realtime_node(
    contract: FuturesContract,
    product_display_names: Mapping[str, str],
    catalog: Mapping[str, str],
) -> str:
    product_code = contract.product_symbol.rsplit(":", 1)[-1].upper()
    if contract.exchange is FuturesExchange.CFFEX:
        node = CFFEX_REALTIME_NODES.get(product_code)
        if node is None:
            raise ValueError(
                f"unsupported CFFEX futures realtime product: {product_code}"
            )
        return node
    product_name = product_display_names.get(contract.product_symbol)
    if product_name is None:
        raise ValueError(
            f"missing futures product metadata: {contract.product_symbol}"
        )
    product_name = REALTIME_PRODUCT_NAME_ALIASES.get(
        (contract.exchange, product_code), product_name
    )
    node = catalog.get(product_name)
    if node is None:
        raise ValueError(
            "missing Sina futures realtime node for "
            f"{contract.product_symbol} ({product_name})"
        )
    return node


def _parse_realtime(
    contract: FuturesContract,
    row: Mapping[str, object],
    received_at: datetime,
    source: str,
) -> FuturesDailyBar:
    provider_exchange = str(row.get("exchange", "")).strip().upper()
    if provider_exchange != contract.exchange.value:
        raise ValueError(
            "futures realtime exchange mismatch: "
            f"expected {contract.exchange.value}, got {provider_exchange}"
        )
    trading_day = _provider_date(str(_required_mapping(row, "tradedate")))
    previous_settlement = _optional_mapping_number(row, "prevsettlement")
    duplicate_previous_settlement = _optional_mapping_number(row, "presettlement")
    if (
        previous_settlement is not None
        and duplicate_previous_settlement is not None
        and abs(previous_settlement - duplicate_previous_settlement) > 1e-6
    ):
        raise ValueError("conflicting futures realtime previous settlement fields")
    return FuturesDailyBar(
        symbol=contract.symbol,
        trading_day=trading_day,
        provider_date=trading_day,
        open=_mapping_number(row, "open"),
        high=_mapping_number(row, "high"),
        low=_mapping_number(row, "low"),
        close=_mapping_number(row, "trade"),
        previous_close=_optional_mapping_number(row, "preclose"),
        settlement=None,
        previous_settlement=(
            previous_settlement
            if previous_settlement is not None
            else duplicate_previous_settlement
        ),
        volume_contracts=_mapping_integer(row, "volume"),
        amount=None,
        open_interest_contracts=_optional_mapping_number(row, "position"),
        open_interest_change_contracts=None,
        delivery_settlement=None,
        source=source,
        state=FuturesBarState.PROVISIONAL,
        provider_time=_provider_time(
            str(_required_mapping(row, "ticktime")), received_at
        ),
    )


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


def _required_mapping(row: Mapping[str, object], field: str) -> object:
    value = row.get(field)
    if value is None or str(value).strip() == "":
        raise ValueError(f"missing futures realtime field: {field}")
    return value


def _mapping_number(row: Mapping[str, object], field: str) -> float:
    return _number(str(_required_mapping(row, field)), field)


def _optional_mapping_number(
    row: Mapping[str, object],
    field: str,
) -> float | None:
    value = row.get(field)
    if value is None or str(value).strip() == "":
        return None
    return _optional_number(str(value))


def _mapping_integer(row: Mapping[str, object], field: str) -> int:
    return _integer(str(_required_mapping(row, field)), field)
