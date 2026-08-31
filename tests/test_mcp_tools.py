import logging
import socket
from urllib.error import HTTPError

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

    def post(self, path, payload):
        self.calls.append((path, payload))
        if self.error:
            raise self.error
        value = self.responses[path]
        return value(payload) if callable(value) else value


def test_save_ai_analysis_uses_the_only_local_write_endpoint():
    api = FakeApi({"/api/analysis/ai": {"report_id": "report-1", "revision": 1}})
    tools = StockHarnessMcpTools(api)
    payload = {"symbol": "000001.SZ", "framework": {}}

    result = tools.save_ai_analysis(payload)

    assert result["ok"] is True
    assert result["operation"] == "save_ai_analysis"
    assert api.calls == [("/api/analysis/ai", payload)]


def test_get_ai_analysis_reads_the_latest_symbol_report():
    api = FakeApi({
        "/api/analysis/ai/000001.SZ": {"report_id": "report-2", "revision": 2}
    })
    result = StockHarnessMcpTools(api).get_ai_analysis("000001.sz")

    assert result["ok"] is True
    assert result["data"]["revision"] == 2
    assert api.calls == [("/api/analysis/ai/000001.SZ", [("timeframe", "daily")])]


def test_local_api_rejects_non_loopback_and_credentials():
    with pytest.raises(ValueError):
        LocalStockHarnessApi("https://example.com")
    with pytest.raises(ValueError):
        LocalStockHarnessApi("http://user:secret@127.0.0.1:8001")


def test_symbols_reject_url_control_characters_and_preserve_custom_group_ids():
    api = FakeApi({
        "/api/instruments/CUSTOM:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/members": {
            "items": []
        }
    })
    tools = StockHarnessMcpTools(api)

    result = tools.list_instrument_members(
        "custom:AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    )

    assert result["ok"] is True
    assert api.calls[0][0] == (
        "/api/instruments/CUSTOM:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/members"
    )
    for value in ("000001.SZ?token=secret", "000001.SZ#fragment", "000001%2FSZ"):
        with pytest.raises(ValueError, match="invalid symbol"):
            tools.get_instrument(value)
    for value in (
        "FUT:SHFE:CU:202609?token=secret",
        "FUTCONT:SHFE:CU:MAIN:raw/../../sql",
        "FUTCONT:SHFE:CU:MAIN:raw#provider",
    ):
        with pytest.raises(ValueError, match="invalid symbol"):
            tools.get_instrument(value)


def test_futures_symbols_preserve_canonical_identity_and_read_bounded_evidence():
    series = "FUTCONT:SHFE:CU:MAIN:raw"
    api = FakeApi({
        f"/api/instruments/{series}": {
            "symbol": series, "kind": "futures-continuous", "price_basis": "raw"
        },
        f"/api/instruments/{series}/daily-bars": {
            "items": [{
                "trade_date": "2026-08-20", "close": 100,
                "volume": 50, "open_interest": 60,
                "mapped_contract_symbol": "FUT:SHFE:CU:202609",
                "bar_state": "intraday", "source": "akshare-futures-zh-spot",
                "stale": False,
            }]
        },
        "/api/futures/coverage": {"items": [{"symbol": series}]},
        f"/api/futures/continuous/{series}": {
            "instrument": {"symbol": series},
            "mappings": [{"contract_symbol": "FUT:SHFE:CU:202609"}],
            "rolls": [],
        },
        f"/api/analysis/trend/{series}": {
            "symbol": series, "official": None, "preview": {"items": []}
        },
    })
    tools = StockHarnessMcpTools(api)

    instrument = tools.get_instrument("futcont:shfe:cu:main:RAW")
    bars = tools.get_daily_bars(series, max_bars=10)
    coverage = tools.list_futures_coverage("futures-continuous", 20, 0)
    continuous = tools.get_futures_continuous(
        series, "2026-01-01", "2026-08-20", 10, 10
    )
    trend = tools.get_trend_analysis(series, "daily")

    assert instrument["ok"] is True
    assert bars["data"]["items"][0]["mapped_contract_symbol"].startswith("FUT:")
    assert bars["data"]["freshness"]["latest_state"] == "intraday"
    assert coverage["ok"] is continuous["ok"] is trend["ok"] is True
    assert api.calls == [
        (f"/api/instruments/{series}", []),
        (f"/api/instruments/{series}/daily-bars", []),
        ("/api/futures/coverage", [
            ("limit", 20), ("offset", 0), ("kind", "futures-continuous"),
        ]),
        (f"/api/futures/continuous/{series}", [
            ("max_mappings", 10), ("max_rolls", 10),
            ("start_date", "2026-01-01"), ("end_date", "2026-08-20"),
        ]),
        (f"/api/analysis/trend/{series}", [("timeframe", "daily")]),
    ]


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


def test_active_workspace_context_is_returned_without_mutation():
    payload = {
        "schema_version": "1.0",
        "active_group_id": "group-primary",
        "windows": [{"id": "chart-1", "type": "chart"}],
    }
    api = FakeApi({"/api/workspace-context": payload})

    result = StockHarnessMcpTools(api).get_active_workspace()

    assert result["ok"] is True
    assert result["data"] == payload
    assert api.calls == [("/api/workspace-context", [])]


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


@pytest.mark.parametrize(
    ("status", "expected_code"),
    ((404, "not_found"), (500, "api_error")),
)
def test_http_errors_are_bounded_and_do_not_return_response_body(
    monkeypatch, status, expected_code
):
    def failed(request, **_kwargs):
        raise HTTPError(
            request.full_url,
            status,
            "provider token=must-not-leak",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("stock_harness.mcp_tools.urlopen", failed)
    result = StockHarnessMcpTools(
        LocalStockHarnessApi("http://127.0.0.1:8765")
    ).get_instrument("000001.SZ")

    assert result["ok"] is False
    assert result["error"] == {
        "code": expected_code,
        "message": f"StockHarness API returned HTTP {status}",
        "status": status,
    }
    assert "token" not in str(result)


def test_stale_provisional_bar_remains_explicit_and_does_not_replace_final_state():
    api = FakeApi({
        "/api/instruments/000001.SZ/daily-bars": {
            "items": [
                {
                    "trade_date": "2026-08-05",
                    "close": 10,
                    "bar_state": "final",
                    "source": "tushare",
                    "stale": False,
                },
                {
                    "trade_date": "2026-08-06",
                    "close": 11,
                    "bar_state": "intraday",
                    "source": "akshare_intraday",
                    "stale": True,
                },
            ]
        }
    })

    result = StockHarnessMcpTools(api).get_daily_bars("000001.SZ")

    assert result["ok"] is True
    assert result["data"]["freshness"] == {
        "observed_at": result["data"]["freshness"]["observed_at"],
        "latest_trade_date": "2026-08-06",
        "latest_state": "intraday",
        "provisional_stale": True,
    }
    assert result["data"]["items"][-2]["bar_state"] == "final"


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
