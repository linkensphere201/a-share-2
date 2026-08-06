"""Bounded read-only services exposed by the StockHarness MCP server."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
import logging
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
LOGGER = logging.getLogger(__name__)
_WARNING_LOCK = threading.Lock()
_WARNING_TIMES: dict[str, float] = {}


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


class StockHarnessMcpTools:
    """Versioned read-only MCP tool implementation over the local application API."""

    def __init__(self, api: ApiReader) -> None:
        self.api = api

    def health(self) -> dict[str, object]:
        return self._execute("health", lambda: self.api.get("/api/health"))

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
    normalized = value.strip().upper()
    if not normalized or len(normalized) > 200 or "/" in normalized or "\\" in normalized:
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
