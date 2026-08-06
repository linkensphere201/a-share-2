import logging
import socket

import pytest

from stock_harness.mcp_tools import (
    LocalStockHarnessApi,
    StockHarnessApiError,
    StockHarnessMcpTools,
    _WARNING_TIMES,
)


class FakeApi:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params or []))
        if self.error:
            raise self.error
        value = self.responses[path]
        return value() if callable(value) else value


def test_local_api_rejects_non_loopback_and_credentials():
    with pytest.raises(ValueError):
        LocalStockHarnessApi("https://example.com")
    with pytest.raises(ValueError):
        LocalStockHarnessApi("http://user:secret@127.0.0.1:8001")


def test_custom_group_is_bounded_and_preserves_roles_tags_and_notes():
    api = FakeApi({
        "/api/custom-groups/abc": {
            "id": "abc", "name": "AI applications", "description": "leaders",
            "members": [
                {"symbol": "600588.SH", "role": "bellwether", "tags": ["ERP"], "note": "core"},
                {"symbol": "603039.SH", "role": "sentiment_anchor", "tags": [], "note": ""},
            ],
        }
    })
    result = StockHarnessMcpTools(api).get_custom_group("ABC", max_members=1)

    assert result["ok"] is True
    data = result["data"]
    assert data["member_total"] == 2
    assert data["members_truncated"] is True
    assert data["members"][0]["role"] == "bellwether"


def test_daily_bars_report_requested_effective_ranges_and_recent_truncation():
    api = FakeApi({
        "/api/instruments/000001.SZ/daily-bars": {
            "symbol": "000001.SZ",
            "items": [
                {"trade_date": "2026-08-03", "close": 10, "source": "tushare", "bar_state": "final"},
                {"trade_date": "2026-08-04", "close": 11, "source": "akshare_intraday", "bar_state": "intraday"},
            ],
        }
    })
    result = StockHarnessMcpTools(api).get_daily_bars(
        "000001.sz", "2026-08-01", "2026-08-05", max_bars=1
    )

    data = result["data"]
    assert data["requested_range"]["start_date"] == "2026-08-01"
    assert data["effective_range"]["end_date"] == "2026-08-04"
    assert data["items"][0]["bar_state"] == "intraday"
    assert data["truncated"] is True
    assert data["truncation_policy"] == "most_recent"
    assert data["freshness"]["latest_trade_date"] == "2026-08-04"
    assert data["freshness"]["latest_state"] == "intraday"


def test_latest_quote_distinguishes_final_and_provisional():
    api = FakeApi({
        "/api/instruments/000001.SZ": {
            "symbol": "000001.SZ", "name": "Ping An Bank", "last_trade_date": "2026-08-03"
        },
        "/api/instruments/000001.SZ/daily-bars": {
            "items": [
                {"trade_date": "2026-08-03", "close": 10, "bar_state": "final", "source": "tushare"},
                {"trade_date": "2026-08-04", "close": 11, "bar_state": "intraday", "source": "akshare_intraday"},
            ]
        },
    })
    result = StockHarnessMcpTools(api).get_latest_quote("000001.SZ")

    data = result["data"]
    assert data["latest_final"]["trade_date"] == "2026-08-03"
    assert data["provisional"]["trade_date"] == "2026-08-04"
    assert data["effective"]["bar_state"] == "intraday"
    assert data["precedence"] == "provisional_after_latest_final"


def test_unavailable_api_returns_bounded_structured_error():
    api = FakeApi(error=StockHarnessApiError("app_unavailable", "APP unavailable"))
    result = StockHarnessMcpTools(api).health()

    assert result["ok"] is False
    assert result["error"] == {
        "code": "app_unavailable", "message": "APP unavailable", "status": None
    }
    assert result["request_id"]


def test_timeout_has_distinct_error_code(monkeypatch):
    def timed_out(*_args, **_kwargs):
        raise socket.timeout("slow")

    monkeypatch.setattr("stock_harness.mcp_tools.urlopen", timed_out)
    result = StockHarnessMcpTools(
        LocalStockHarnessApi("http://127.0.0.1:8765", timeout_seconds=0.5)
    ).health()

    assert result["ok"] is False
    assert result["error"]["code"] == "request_timeout"


def test_operational_warnings_are_rate_limited_per_operation(caplog):
    _WARNING_TIMES.clear()
    api = FakeApi(error=StockHarnessApiError("app_unavailable", "APP unavailable"))
    tools = StockHarnessMcpTools(api)

    with caplog.at_level(logging.WARNING, logger="stock_harness.mcp_tools"):
        tools.health()
        tools.health()
        tools.list_custom_groups()

    warnings = [record for record in caplog.records if "mcp_api_read_failed" in record.message]
    assert len(warnings) == 2
    assert any("operation=health" in record.getMessage() for record in warnings)
    assert any("operation=list_custom_groups" in record.getMessage() for record in warnings)


def test_invalid_dates_and_limits_fail_before_api_call():
    api = FakeApi()
    tools = StockHarnessMcpTools(api)

    with pytest.raises(ValueError):
        tools.get_daily_bars("000001.SZ", "2026-08-05", "2026-08-01")
    with pytest.raises(ValueError):
        tools.search_instruments(limit=101)
    assert api.calls == []
