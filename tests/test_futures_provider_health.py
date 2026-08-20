from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

import pytest

from stock_harness.futures_provider_health import (
    FuturesProviderIssueKind,
    FuturesProviderMonitor,
    classify_futures_provider_error,
)
from stock_harness.models import (
    FuturesBarState,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
    FuturesLifecycleStatus,
)


CHINA_TIME = timezone(timedelta(hours=8))


class _Provider:
    code = "test-futures"

    def __init__(self) -> None:
        self.bars = ()
        self.missing_contracts = ()
        self.error = None

    def fetch(self, contracts, expected_trading_day=None, observed_at=None):
        if self.error is not None:
            raise self.error
        return self.bars


def _contract() -> FuturesContract:
    return FuturesContract(
        symbol="FUT:SHFE:CU:202609",
        provider_symbol="CU2609.SHF",
        product_symbol="FUTPROD:SHFE:CU",
        display_name="CU2609",
        exchange=FuturesExchange.SHFE,
        contract_month="202609",
        listed_on=date(2025, 9, 1),
        last_trading_date=date(2026, 9, 15),
        delivery_date=date(2026, 9, 18),
        multiplier=None,
        per_unit=5,
        trading_unit="contract",
        quote_unit="CNY/tonne",
        lifecycle_status=FuturesLifecycleStatus.TRADING,
    )


def _bar(provider_time: datetime) -> FuturesDailyBar:
    return FuturesDailyBar(
        symbol=_contract().symbol,
        trading_day=date(2026, 8, 20),
        provider_date=date(2026, 8, 20),
        open=100,
        high=103,
        low=99,
        close=102,
        previous_close=100,
        settlement=None,
        previous_settlement=100,
        volume_contracts=10,
        amount=None,
        open_interest_contracts=20,
        open_interest_change_contracts=None,
        delivery_settlement=None,
        source="test-futures",
        state=FuturesBarState.PROVISIONAL,
        provider_time=provider_time,
    )


def test_monitor_reports_ready_and_recovers_from_previous_problem(caplog) -> None:
    caplog.set_level("INFO")
    provider = _Provider()
    monitor = FuturesProviderMonitor(provider, 90)
    now = datetime(2026, 8, 20, 14, 0, tzinfo=CHINA_TIME)
    monitor.fetch([_contract()], observed_at=now)
    provider.bars = (_bar(now),)
    bars = monitor.fetch([_contract()], observed_at=now + timedelta(seconds=1))
    assert len(bars) == 1
    assert monitor.status()["state"] == "ready"
    assert monitor.status()["consecutive_failures"] == 0
    assert "futures_provider_recovered" in caplog.text


def test_empty_partial_and_stale_results_remain_returnable() -> None:
    provider = _Provider()
    monitor = FuturesProviderMonitor(provider, 90)
    now = datetime(2026, 8, 20, 14, 0, tzinfo=CHINA_TIME)
    assert monitor.fetch([_contract()], observed_at=now) == ()
    assert monitor.status()["last_issue"] is FuturesProviderIssueKind.EMPTY_RESPONSE

    provider.bars = (_bar(now),)
    provider.missing_contracts = ("FUT:SHFE:AL:202609",)
    assert monitor.fetch([_contract()], observed_at=now + timedelta(seconds=1))
    assert monitor.status()["last_issue"] is FuturesProviderIssueKind.PARTIAL_RESULT

    provider.missing_contracts = ()
    monitor.fetch([_contract()], observed_at=now + timedelta(seconds=181))
    assert monitor.status()["last_issue"] is FuturesProviderIssueKind.STALE_QUOTE


def test_repeated_warning_is_rate_limited(caplog) -> None:
    provider = _Provider()
    monitor = FuturesProviderMonitor(provider, 90, warning_interval_seconds=60)
    first = datetime(2026, 8, 20, 14, 0, tzinfo=CHINA_TIME)
    monitor.fetch([_contract()], observed_at=first)
    monitor.fetch([_contract()], observed_at=first + timedelta(seconds=30))
    warnings = [
        item for item in caplog.records
        if "futures_provider_warning" in item.message
    ]
    assert len(warnings) == 1


def test_failure_updates_health_and_preserves_exception() -> None:
    provider = _Provider()
    provider.error = ValueError("commodity futures spot row has 4 fields")
    monitor = FuturesProviderMonitor(provider, 90)
    with pytest.raises(ValueError, match="row has 4"):
        monitor.fetch([_contract()])
    status = monitor.status()
    assert status["state"] == "error"
    assert status["consecutive_failures"] == 1
    assert status["last_issue"] is FuturesProviderIssueKind.MALFORMED_ROW


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (HTTPError("http://test", 403, "forbidden", {}, None), FuturesProviderIssueKind.PERMISSION),
        (HTTPError("http://test", 429, "limited", {}, None), FuturesProviderIssueKind.RATE_LIMIT),
        (URLError("offline"), FuturesProviderIssueKind.NETWORK),
        (ValueError("missing futures realtime field: open"), FuturesProviderIssueKind.FIELD_CONTRACT),
        (ValueError("invalid futures spot trading date"), FuturesProviderIssueKind.MALFORMED_ROW),
        (RuntimeError("unexpected"), FuturesProviderIssueKind.UNKNOWN),
    ],
)
def test_classifies_provider_failures(error, expected) -> None:
    assert classify_futures_provider_error(error) is expected
