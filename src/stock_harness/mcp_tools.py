"""Bounded read-only services exposed by the StockHarness MCP server."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
import logging
import re
import socket
import threading
import time
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4


SCHEMA_VERSION = "1.0"
MAX_SEARCH_RESULTS = 100
MAX_GROUPS = 200
MAX_GROUP_MEMBERS = 500
MAX_MEMBERSHIPS = 500
MAX_DAILY_BARS = 8_000
MAX_FUTURES_ROWS = 500
MAX_SIGNAL_ITEMS = 200
MIN_SHORT_HORIZON = 5
MAX_LONG_HORIZON = 1_000
LOGGER = logging.getLogger(__name__)
_WARNING_LOCK = threading.Lock()
_WARNING_TIMES: dict[str, float] = {}
_MARKET_SYMBOL_PATTERN = re.compile(r"[A-Z0-9]+(?:\.[A-Z0-9]+)?\Z")
_CUSTOM_SYMBOL_PATTERN = re.compile(
    r"CUSTOM:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\Z",
    re.IGNORECASE,
)
_FUTURES_PRODUCT_PATTERN = re.compile(
    r"FUTPROD:([A-Z0-9]+):([A-Z0-9]+)\Z", re.IGNORECASE
)
_FUTURES_CONTRACT_PATTERN = re.compile(
    r"FUT:([A-Z0-9]+):([A-Z0-9]+):([0-9]{6})\Z", re.IGNORECASE
)
_FUTURES_CONTINUOUS_PATTERN = re.compile(
    r"FUTCONT:([A-Z0-9]+):([A-Z0-9]+):([A-Z0-9-]+):"
    r"(raw|backward-ratio|backward-additive)\Z",
    re.IGNORECASE,
)


class StockHarnessApiError(RuntimeError):
    """An explainable failure while reading the local StockHarness API."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class ApiReader(Protocol):
    def get(
        self, path: str, params: list[tuple[str, object]] | None = None
    ) -> dict[str, object]: ...

    def post(self, path: str, payload: dict[str, object]) -> dict[str, object]: ...


class LocalStockHarnessApi:
    """Small JSON client restricted to a loopback StockHarness HTTP endpoint."""

    def __init__(self, base_url: str, timeout_seconds: float = 5.0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("StockHarness MCP API URL must be a loopback HTTP address")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("StockHarness MCP API URL cannot contain credentials or query data")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(0.5, min(float(timeout_seconds), 30.0))

    def get(
        self, path: str, params: list[tuple[str, object]] | None = None
    ) -> dict[str, object]:
        query = urlencode(params or [], doseq=True)
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            code = "not_found" if exc.code == 404 else "api_error"
            raise StockHarnessApiError(
                code, f"StockHarness API returned HTTP {exc.code}", status=exc.code
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise StockHarnessApiError(
                "request_timeout",
                "StockHarness API request exceeded the configured timeout",
            ) from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise StockHarnessApiError(
                    "request_timeout",
                    "StockHarness API request exceeded the configured timeout",
                ) from exc
            raise StockHarnessApiError(
                "app_unavailable",
                "StockHarness APP is not available at the configured local API address",
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StockHarnessApiError(
                "invalid_response", "StockHarness API returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise StockHarnessApiError(
                "invalid_response", "StockHarness API returned a non-object response"
            )
        return payload

    def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        url = f"{self.base_url}{path}"
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = f"StockHarness API returned HTTP {exc.code}"
            try:
                body = json.loads(exc.read().decode("utf-8"))
                if isinstance(body, dict) and isinstance(body.get("detail"), str):
                    detail = body["detail"]
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise StockHarnessApiError("api_error", detail, status=exc.code) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise StockHarnessApiError("request_timeout", "StockHarness API request timed out") from exc
        except URLError as exc:
            raise StockHarnessApiError("app_unavailable", "StockHarness APP is unavailable") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StockHarnessApiError("invalid_response", "StockHarness API returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise StockHarnessApiError("invalid_response", "StockHarness API returned a non-object response")
        return value


class StockHarnessMcpTools:
    """Versioned read-only MCP tool implementation over the local application API."""

    def __init__(self, api: ApiReader) -> None:
        self.api = api

    def health(self) -> dict[str, object]:
        return self._execute("health", lambda: self.api.get("/api/health"))

    def get_active_workspace(self) -> dict[str, object]:
        return self._execute(
            "get_active_workspace", lambda: self.api.get("/api/workspace-context")
        )

    def list_signal_definitions(self) -> dict[str, object]:
        return self._execute(
            "list_signal_definitions", lambda: self.api.get("/api/signals/definitions")
        )

    def list_signal_runs(
        self, signal_id: str | None = None, limit: int = 20,
    ) -> dict[str, object]:
        limit = _bounded(limit, 1, 100, "limit")
        params: list[tuple[str, object]] = [("limit", limit)]
        if signal_id:
            params.append(("signal_id", signal_id.strip()))
        return self._execute(
            "list_signal_runs", lambda: self.api.get("/api/signals/runs", params)
        )

    def get_signal_run(
        self, run_id: str, max_items: int = MAX_SIGNAL_ITEMS,
    ) -> dict[str, object]:
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id is required")
        max_items = _bounded(max_items, 1, MAX_SIGNAL_ITEMS, "max_items")

        def load() -> dict[str, object]:
            run = self.api.get(f"/api/signals/runs/{normalized}")
            payload = self.api.get(
                f"/api/signals/runs/{normalized}/items", [("limit", max_items)]
            )
            items = _items(payload)
            total = int(payload.get("total", len(items)))
            return {**run, "items": items, "items_total": total,
                    "items_truncated": total > len(items)}

        return self._execute("get_signal_run", load)

    def get_signal_item(self, run_id: str, item_id: str) -> dict[str, object]:
        normalized_run = run_id.strip()
        normalized_item = item_id.strip()
        if not normalized_run or not normalized_item:
            raise ValueError("run_id and item_id are required")

        def load() -> dict[str, object]:
            run = self.api.get(f"/api/signals/runs/{normalized_run}")
            selected = self.api.get(
                f"/api/signals/runs/{normalized_run}/items/{normalized_item}"
            )
            return {
                "run_id": normalized_run, "signal_id": run.get("signal_id"),
                "effective_date": run.get("effective_date"), "revision": run.get("revision"),
                "item": selected,
            }

        return self._execute("get_signal_item", load)

    def search_instruments(
        self,
        query: str = "",
        classification: str | None = None,
        source_system: str | None = None,
        family: str | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, object]:
        limit = _bounded(limit, 1, MAX_SEARCH_RESULTS, "limit")
        offset = _bounded(offset, 0, 100_000, "offset")
        params: list[tuple[str, object]] = [
            ("query", query.strip()), ("limit", limit), ("offset", offset)
        ]
        for key, value in (
            ("classification", classification), ("source_system", source_system),
            ("family", family), ("category", category),
        ):
            if value:
                params.append((key, value))
        return self._execute(
            "search_instruments", lambda: self.api.get("/api/instruments", params)
        )

    def get_instrument(self, symbol: str) -> dict[str, object]:
        normalized = _symbol(symbol)
        return self._execute(
            "get_instrument",
            lambda: self.api.get(f"/api/instruments/{normalized}"),
        )

    def list_custom_groups(self, query: str = "", limit: int = 100) -> dict[str, object]:
        limit = _bounded(limit, 1, MAX_GROUPS, "limit")

        def load() -> dict[str, object]:
            payload = self.api.get("/api/custom-groups", [("query", query.strip())])
            items = _items(payload)
            return {
                "items": items[:limit],
                "total": len(items),
                "truncated": len(items) > limit,
            }

        return self._execute("list_custom_groups", load)

    def get_custom_group(
        self, group_id: str, max_members: int = MAX_GROUP_MEMBERS
    ) -> dict[str, object]:
        max_members = _bounded(max_members, 1, MAX_GROUP_MEMBERS, "max_members")
        normalized = group_id.strip().lower()
        if not normalized:
            raise ValueError("group_id is required")

        def load() -> dict[str, object]:
            payload = self.api.get(f"/api/custom-groups/{normalized}")
            members = payload.get("members", [])
            if not isinstance(members, list):
                raise StockHarnessApiError(
                    "invalid_response", "custom group members are not a list"
                )
            return {
                **payload,
                "members": members[:max_members],
                "member_total": len(members),
                "members_truncated": len(members) > max_members,
            }

        return self._execute("get_custom_group", load)

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        max_bars: int = MAX_DAILY_BARS,
    ) -> dict[str, object]:
        normalized = _symbol(symbol)
        start = _iso_date(start_date, "start_date")
        end = _iso_date(end_date, "end_date")
        if start and end and start > end:
            raise ValueError("start_date must not be after end_date")
        max_bars = _bounded(max_bars, 1, MAX_DAILY_BARS, "max_bars")

        def load() -> dict[str, object]:
            params: list[tuple[str, object]] = []
            if start:
                params.append(("start_date", start.isoformat()))
            if end:
                params.append(("end_date", end.isoformat()))
            payload = self.api.get(
                f"/api/instruments/{normalized}/daily-bars", params
            )
            items = _items(payload)
            returned = items[-max_bars:]
            latest = returned[-1] if returned else None
            return {
                "symbol": normalized,
                "requested_range": {
                    "start_date": start.isoformat() if start else None,
                    "end_date": end.isoformat() if end else None,
                },
                "effective_range": {
                    "start_date": returned[0].get("trade_date") if returned else None,
                    "end_date": returned[-1].get("trade_date") if returned else None,
                },
                "items": returned,
                "total": len(items),
                "truncated": len(items) > max_bars,
                "truncation_policy": "most_recent" if len(items) > max_bars else None,
                "freshness": {
                    "observed_at": datetime.now(UTC).isoformat(),
                    "latest_trade_date": latest.get("trade_date") if latest else None,
                    "latest_state": latest.get("bar_state") if latest else None,
                    "provisional_stale": latest.get("stale") if latest else None,
                },
            }

        return self._execute("get_daily_bars", load)

    def get_latest_quote(self, symbol: str) -> dict[str, object]:
        normalized = _symbol(symbol)

        def load() -> dict[str, object]:
            instrument = self.api.get(f"/api/instruments/{normalized}")
            final_date = _optional_date(instrument.get("last_trade_date"))
            start = final_date or (date.today() - timedelta(days=45))
            payload = self.api.get(
                f"/api/instruments/{normalized}/daily-bars",
                [("start_date", start.isoformat()), ("end_date", date.today().isoformat())],
            )
            items = _items(payload)
            final = next(
                (item for item in reversed(items) if item.get("bar_state") == "final"), None
            )
            provisional = next(
                (item for item in reversed(items) if item.get("bar_state") == "intraday"), None
            )
            effective = provisional or final
            return {
                "symbol": normalized,
                "name": instrument.get("name"),
                "effective": effective,
                "latest_final": final,
                "provisional": provisional,
                "precedence": (
                    "provisional_after_latest_final" if provisional else "latest_final"
                ),
            }

        return self._execute("get_latest_quote", load)

    def list_futures_coverage(
        self,
        kind: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, object]:
        if kind not in {None, "futures-contract", "futures-continuous"}:
            raise ValueError("kind must be futures-contract or futures-continuous")
        limit = _bounded(limit, 1, MAX_FUTURES_ROWS, "limit")
        offset = _bounded(offset, 0, 100_000, "offset")
        params: list[tuple[str, object]] = [("limit", limit), ("offset", offset)]
        if kind is not None:
            params.append(("kind", kind))
        return self._execute(
            "list_futures_coverage",
            lambda: self.api.get("/api/futures/coverage", params),
        )

    def get_futures_continuous(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        max_mappings: int = 200,
        max_rolls: int = 200,
    ) -> dict[str, object]:
        normalized = _symbol(symbol)
        if not normalized.startswith("FUTCONT:"):
            raise ValueError("symbol must identify a futures continuous series")
        start = _iso_date(start_date, "start_date")
        end = _iso_date(end_date, "end_date")
        if start and end and start > end:
            raise ValueError("start_date must not be after end_date")
        max_mappings = _bounded(max_mappings, 1, MAX_FUTURES_ROWS, "max_mappings")
        max_rolls = _bounded(max_rolls, 1, MAX_FUTURES_ROWS, "max_rolls")
        params: list[tuple[str, object]] = [
            ("max_mappings", max_mappings), ("max_rolls", max_rolls)
        ]
        if start:
            params.append(("start_date", start.isoformat()))
        if end:
            params.append(("end_date", end.isoformat()))
        return self._execute(
            "get_futures_continuous",
            lambda: self.api.get(f"/api/futures/continuous/{normalized}", params),
        )

    def get_trend_analysis(
        self, symbol: str, timeframe: str = "daily"
    ) -> dict[str, object]:
        normalized = _symbol(symbol)
        if timeframe not in {"daily", "weekly", "monthly"}:
            raise ValueError("timeframe must be daily, weekly, or monthly")
        return self._execute(
            "get_trend_analysis",
            lambda: self.api.get(
                f"/api/analysis/trend/{normalized}", [("timeframe", timeframe)]
            ),
        )

    def recalculate_trend_analysis(
        self,
        symbol: str,
        short_horizon_bars: int = 60,
        medium_horizon_bars: int = 120,
        long_horizon_bars: int = 250,
        include_preview: bool = True,
    ) -> dict[str, object]:
        normalized = _symbol(symbol)
        short = _bounded(
            short_horizon_bars, MIN_SHORT_HORIZON, 250, "short_horizon_bars"
        )
        medium = _bounded(medium_horizon_bars, 10, 500, "medium_horizon_bars")
        long = _bounded(long_horizon_bars, 60, MAX_LONG_HORIZON, "long_horizon_bars")
        if not short < medium < long:
            raise ValueError("trend horizons must satisfy short < medium < long")

        def calculate() -> dict[str, object]:
            payload = self.api.post("/api/analysis/trend/recalculate", {
                "symbol": normalized,
                "timeframes": ["daily"],
                "short_horizon_bars": short,
                "medium_horizon_bars": medium,
                "long_horizon_bars": long,
                "config_version": f"embedded-mcp-r1-{short}-{medium}-{long}",
                "include_preview": bool(include_preview),
            })
            results = payload.get("results", [])
            if not isinstance(results, list):
                raise StockHarnessApiError(
                    "invalid_response", "Trend recalculation results are not a list"
                )
            summaries = []
            for result in results:
                if not isinstance(result, dict):
                    continue
                items = result.get("items", [])
                summaries.append({
                    "run_id": result.get("run_id"),
                    "symbol": result.get("symbol", normalized),
                    "timeframe": result.get("timeframe", "daily"),
                    "as_of_date": result.get("as_of_date"),
                    "completion_state": result.get("completion_state"),
                    "algorithm_version": result.get("algorithm_version"),
                    "config_version": result.get("config_version"),
                    "preview": result.get("expires_at_ms") is not None,
                    "item_count": len(items) if isinstance(items, list) else 0,
                })
            return {
                "status": payload.get("status", "completed"),
                "symbol": normalized,
                "timeframe": "daily",
                "horizons": {"short": short, "medium": medium, "long": long},
                "results": summaries,
                "next_operation": "get_trend_analysis",
            }

        return self._execute("recalculate_trend_analysis", calculate)

    def save_ai_analysis(self, payload: dict[str, object]) -> dict[str, object]:
        return self._execute(
            "save_ai_analysis",
            lambda: self.api.post("/api/analysis/ai", payload),
        )

    def get_ai_analysis(
        self, symbol: str, timeframe: str = "daily"
    ) -> dict[str, object]:
        normalized = _symbol(symbol)
        if timeframe not in {"daily", "weekly", "monthly"}:
            raise ValueError("timeframe must be daily, weekly, or monthly")
        return self._execute(
            "get_ai_analysis",
            lambda: self.api.get(
                f"/api/analysis/ai/{normalized}", [("timeframe", timeframe)]
            ),
        )

    def list_instrument_members(
        self, symbol: str, limit: int = 100, offset: int = 0
    ) -> dict[str, object]:
        normalized = _symbol(symbol)
        limit = _bounded(limit, 1, MAX_MEMBERSHIPS, "limit")
        offset = _bounded(offset, 0, 100_000, "offset")
        return self._execute(
            "list_instrument_members",
            lambda: self.api.get(
                f"/api/instruments/{normalized}/members",
                [("limit", limit), ("offset", offset)],
            ),
        )

    def list_symbol_boards(
        self, symbol: str, limit: int = 100, offset: int = 0
    ) -> dict[str, object]:
        normalized = _symbol(symbol)
        limit = _bounded(limit, 1, MAX_MEMBERSHIPS, "limit")
        offset = _bounded(offset, 0, 100_000, "offset")
        return self._execute(
            "list_symbol_boards",
            lambda: self.api.get(
                f"/api/instruments/{normalized}/boards",
                [("limit", limit), ("offset", offset)],
            ),
        )

    def _execute(
        self, operation: str, callback: Callable[[], dict[str, object]]
    ) -> dict[str, object]:
        request_id = str(uuid4())
        try:
            result = callback()
            return {
                "schema_version": SCHEMA_VERSION,
                "request_id": request_id,
                "ok": True,
                "operation": operation,
                "data": result,
            }
        except StockHarnessApiError as exc:
            _warn_limited(
                f"{operation}:{exc.code}",
                "mcp_api_read_failed request_id=%s operation=%s code=%s status=%s",
                request_id,
                operation,
                exc.code,
                exc.status,
            )
            return {
                "schema_version": SCHEMA_VERSION,
                "request_id": request_id,
                "ok": False,
                "operation": operation,
                "error": {"code": exc.code, "message": str(exc), "status": exc.status},
            }


def _bounded(value: int, minimum: int, maximum: int, name: str) -> int:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _symbol(value: str) -> str:
    stripped = value.strip()
    custom_match = _CUSTOM_SYMBOL_PATTERN.fullmatch(stripped)
    if custom_match:
        return f"CUSTOM:{custom_match.group(1).lower()}"
    product_match = _FUTURES_PRODUCT_PATTERN.fullmatch(stripped)
    if product_match:
        return f"FUTPROD:{product_match.group(1).upper()}:{product_match.group(2).upper()}"
    contract_match = _FUTURES_CONTRACT_PATTERN.fullmatch(stripped)
    if contract_match:
        return (
            f"FUT:{contract_match.group(1).upper()}:"
            f"{contract_match.group(2).upper()}:{contract_match.group(3)}"
        )
    continuous_match = _FUTURES_CONTINUOUS_PATTERN.fullmatch(stripped)
    if continuous_match:
        return (
            f"FUTCONT:{continuous_match.group(1).upper()}:"
            f"{continuous_match.group(2).upper()}:"
            f"{continuous_match.group(3).upper()}:"
            f"{continuous_match.group(4).lower()}"
        )
    normalized = stripped.upper()
    if not normalized or len(normalized) > 200 or not _MARKET_SYMBOL_PATTERN.fullmatch(normalized):
        raise ValueError("invalid symbol")
    return normalized


def _iso_date(value: str | None, name: str) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _items(payload: dict[str, object]) -> list[dict[str, object]]:
    items = payload.get("items", [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise StockHarnessApiError("invalid_response", "API items are not an object list")
    return items


def _warn_limited(key: str, message: str, *args: object) -> None:
    now = time.monotonic()
    with _WARNING_LOCK:
        previous = _WARNING_TIMES.get(key, 0.0)
        if now - previous < 60.0:
            return
        _WARNING_TIMES[key] = now
    LOGGER.warning(message, *args)
