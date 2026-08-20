from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from stock_harness.config import FuturesProvisionalProviderSettings
from stock_harness.futures_provisional_provider import AkShareFuturesSpotProvider
from stock_harness.models import (
    FuturesBarState,
    FuturesContract,
    FuturesExchange,
    FuturesLifecycleStatus,
)


CHINA_TIME = timezone(timedelta(hours=8))


class _Client:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, ...]] = []

    def fetch(self, provider_codes):
        self.calls.append(tuple(provider_codes))
        return self.response


def _settings(**changes) -> FuturesProvisionalProviderSettings:
    values = {
        "enabled": True,
        "provider": "akshare",
        "request_timeout_seconds": 5,
        "retries": 0,
        "retry_wait_seconds": 0,
        "refresh_interval_seconds": 30,
        "stale_after_seconds": 90,
        "max_contracts": 50,
        "fallback_provider": None,
    }
    values.update(changes)
    return FuturesProvisionalProviderSettings(**values)


def _contract(
    exchange: FuturesExchange = FuturesExchange.SHFE,
    provider_symbol: str = "CU2609.SHF",
) -> FuturesContract:
    product = "IF" if exchange is FuturesExchange.CFFEX else "CU"
    return FuturesContract(
        symbol=f"FUT:{exchange.value}:{product}:202609",
        provider_symbol=provider_symbol,
        product_symbol=f"FUTPROD:{exchange.value}:{product}",
        display_name=f"{product}2609",
        exchange=exchange,
        contract_month="202609",
        listed_on=date(2025, 9, 16),
        last_trading_date=date(2026, 9, 15),
        delivery_date=date(2026, 9, 18),
        multiplier=300 if exchange is FuturesExchange.CFFEX else None,
        per_unit=None if exchange is FuturesExchange.CFFEX else 5,
        trading_unit="contract",
        quote_unit="CNY/point",
        lifecycle_status=FuturesLifecycleStatus.TRADING,
    )


def _commodity_row(
    code: str = "CU2609",
    trading_day: str = "2026-08-20",
    provider_time: str = "150000",
) -> str:
    fields = [
        "copper 2609", provider_time, "107020.000", "107390.000", "106980.000",
        "107200.000", "107190.000", "107200.000", "107200.000", "107180.000",
        "106980.000", "1", "2", "162899.000", "67532", "SHFE", "copper",
        trading_day,
    ]
    return f'var hq_str_nf_{code}="{",".join(fields)}";'


def _financial_row(code: str = "IF2609", trading_day: str = "2026-08-20") -> str:
    fields = ["" for _ in range(39)]
    fields[0:7] = [
        "4580.000", "4585.000", "4532.400", "4554.000", "62426",
        "284571707.800", "151386.000",
    ]
    fields[36] = trading_day
    fields[37] = "15:00:00"
    fields[38] = "CSI 300 futures 2609"
    return f'var hq_str_nf_{code}="{",".join(fields)}";'


def test_parses_selected_commodity_forming_daily_bar_without_fabricated_fields() -> None:
    contract = _contract()
    client = _Client(_commodity_row())
    provider = AkShareFuturesSpotProvider(_settings(), client)
    bars = provider.fetch(
        [contract],
        expected_trading_day=date(2026, 8, 20),
        observed_at=datetime(2026, 8, 20, 15, 1, tzinfo=CHINA_TIME),
    )
    assert client.calls == [("CU2609",)]
    assert len(bars) == 1
    bar = bars[0]
    assert bar.state is FuturesBarState.PROVISIONAL
    assert (bar.open, bar.high, bar.low, bar.close) == (107020, 107390, 106980, 107200)
    assert bar.previous_close == 107200
    assert bar.previous_settlement == 106980
    assert bar.volume_contracts == 67532
    assert bar.open_interest_contracts == 162899
    assert bar.settlement is None
    assert bar.amount is None
    assert bar.provider_time == datetime(2026, 8, 20, 15, 0, tzinfo=CHINA_TIME)
    assert provider.missing_contracts == ()


def test_parses_financial_row_and_keeps_unavailable_comparison_fields_missing() -> None:
    contract = _contract(FuturesExchange.CFFEX, "IF2609.CFX")
    bar = AkShareFuturesSpotProvider(
        _settings(), _Client(_financial_row())
    ).fetch([contract], observed_at=datetime(2026, 8, 20, 15, 1))[0]
    assert (bar.open, bar.high, bar.low, bar.close) == (4580, 4585, 4532.4, 4554)
    assert bar.volume_contracts == 62426
    assert bar.open_interest_contracts == 151386
    assert bar.previous_close is None
    assert bar.previous_settlement is None
    assert bar.settlement is None
    assert bar.amount is None


def test_night_session_keeps_calendar_time_separate_from_next_trading_day() -> None:
    bar = AkShareFuturesSpotProvider(
        _settings(),
        _Client(_commodity_row(trading_day="2026-08-20", provider_time="210000")),
    ).fetch(
        [_contract()],
        observed_at=datetime(2026, 8, 19, 21, 5, tzinfo=CHINA_TIME),
    )[0]
    assert bar.trading_day == date(2026, 8, 20)
    assert bar.provider_time == datetime(2026, 8, 19, 21, 0, tzinfo=CHINA_TIME)


def test_reports_missing_contract_without_discarding_received_contract() -> None:
    copper = _contract()
    index = _contract(FuturesExchange.CFFEX, "IF2609.CFX")
    provider = AkShareFuturesSpotProvider(_settings(), _Client(_commodity_row()))
    bars = provider.fetch([copper, index])
    assert [item.symbol for item in bars] == [copper.symbol]
    assert provider.missing_contracts == (index.symbol,)


def test_rejects_provider_trading_day_mismatch() -> None:
    provider = AkShareFuturesSpotProvider(_settings(), _Client(_commodity_row()))
    with pytest.raises(ValueError, match="trading-day mismatch"):
        provider.fetch([_contract()], expected_trading_day=date(2026, 8, 19))


def test_bounds_selected_contract_request() -> None:
    contracts = [
        _contract(provider_symbol="CU2609.SHF"),
        _contract(FuturesExchange.CFFEX, "IF2609.CFX"),
    ]
    provider = AkShareFuturesSpotProvider(_settings(max_contracts=1), _Client(""))
    with pytest.raises(ValueError, match="at most 1"):
        provider.fetch(contracts)


def test_rejects_ambiguous_provider_code() -> None:
    contracts = [
        _contract(provider_symbol="CU2609.SHF"),
        replace(
            _contract(provider_symbol="CU2609.DCE"),
            symbol="FUT:DCE:CU:202609",
            exchange=FuturesExchange.DCE,
            product_symbol="FUTPROD:DCE:CU",
        ),
    ]
    provider = AkShareFuturesSpotProvider(_settings(), _Client(""))
    with pytest.raises(ValueError, match="ambiguous Sina"):
        provider.fetch(contracts)
