from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from stock_harness.config import FuturesProvisionalProviderSettings
from stock_harness.futures_provisional_provider import (
    AkShareFuturesRealtimeProvider,
    AkShareFuturesSpotProvider,
)
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


class _RealtimeClient:
    def __init__(self, rows_by_node) -> None:
        self.rows_by_node = rows_by_node
        self.node_calls: list[str] = []

    def node_catalog(self):
        return {"CU": "copper_qh"}

    def fetch_node(self, node: str):
        self.node_calls.append(node)
        return self.rows_by_node.get(node, [])


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


def _realtime_row(
    symbol: str = "CU2609",
    exchange: str = "shfe",
    trading_day: str = "2026-08-20",
):
    return {
        "symbol": symbol,
        "exchange": exchange,
        "name": "copper 2609",
        "trade": "107200.00",
        "settlement": "107180.00",
        "presettlement": "106980.000",
        "open": "107020.00",
        "high": "107390.00",
        "low": "106980.00",
        "close": "107200.00",
        "volume": "67532",
        "position": "162899",
        "ticktime": "15:00:00",
        "tradedate": trading_day,
        "preclose": "106850.000",
        "prevsettlement": "106980.00",
    }


def _fallback(client, product_display_names=None):
    return AkShareFuturesRealtimeProvider(
        _settings(),
        product_display_names or {"FUTPROD:SHFE:CU": "CU"},
        client,
    )


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


def test_realtime_fallback_matches_spot_fields_without_promoting_dynamic_settlement() -> None:
    client = _RealtimeClient({"copper_qh": [_realtime_row()]})
    bar = _fallback(client).fetch(
        [_contract()],
        expected_trading_day=date(2026, 8, 20),
        observed_at=datetime(2026, 8, 20, 15, 1, tzinfo=CHINA_TIME),
    )[0]
    assert (bar.open, bar.high, bar.low, bar.close) == (107020, 107390, 106980, 107200)
    assert bar.volume_contracts == 67532
    assert bar.open_interest_contracts == 162899
    assert bar.previous_close == 106850
    assert bar.previous_settlement == 106980
    assert bar.settlement is None
    assert bar.amount is None
    assert client.node_calls == ["copper_qh"]


def test_realtime_fallback_requests_each_product_node_once_and_filters_rows() -> None:
    second = replace(
        _contract(provider_symbol="CU2610.SHF"),
        symbol="FUT:SHFE:CU:202610",
        contract_month="202610",
        display_name="CU2610",
    )
    extra = _realtime_row(symbol="CU2611")
    row_2610 = _realtime_row(symbol="CU2610")
    client = _RealtimeClient({"copper_qh": [_realtime_row(), row_2610, extra]})
    bars = _fallback(client).fetch(
        [_contract(), second]
    )
    assert [item.symbol for item in bars] == [
        "FUT:SHFE:CU:202609", "FUT:SHFE:CU:202610",
    ]
    assert client.node_calls == ["copper_qh"]


def test_realtime_fallback_uses_cffex_shared_node() -> None:
    contract = _contract(FuturesExchange.CFFEX, "IF2609.CFX")
    row = _realtime_row(symbol="IF2609", exchange="cffex")
    client = _RealtimeClient({"qz_qh": [row]})
    bars = _fallback(client).fetch([contract])
    assert bars[0].symbol == contract.symbol
    assert client.node_calls == ["qz_qh"]


def test_realtime_fallback_routes_cffex_by_product_instead_of_exchange() -> None:
    contract = replace(
        _contract(FuturesExchange.CFFEX, "IC2609.CFX"),
        symbol="FUT:CFFEX:IC:202609",
        product_symbol="FUTPROD:CFFEX:IC",
        display_name="IC2609",
    )
    row = _realtime_row(symbol="IC2609", exchange="cffex")
    client = _RealtimeClient({"zzgz_qh": [row]})
    bars = _fallback(client).fetch([contract])
    assert bars[0].symbol == contract.symbol
    assert client.node_calls == ["zzgz_qh"]


def test_realtime_fallback_rejects_unlisted_cffex_product() -> None:
    contract = replace(
        _contract(FuturesExchange.CFFEX, "TL2609.CFX"),
        symbol="FUT:CFFEX:TL:202609",
        product_symbol="FUTPROD:CFFEX:TL",
        display_name="TL2609",
    )
    provider = _fallback(_RealtimeClient({}))
    with pytest.raises(ValueError, match="unsupported CFFEX.*TL"):
        provider.fetch([contract])


def test_realtime_fallback_rejects_conflicting_previous_settlement() -> None:
    row = _realtime_row()
    row["presettlement"] = "106981"
    provider = _fallback(_RealtimeClient({"copper_qh": [row]}))
    with pytest.raises(ValueError, match="conflicting.*previous settlement"):
        provider.fetch([_contract()])


def test_realtime_fallback_does_not_guess_missing_product_node() -> None:
    contract = _contract()
    provider = _fallback(
        _RealtimeClient({}), {contract.product_symbol: "unknown"}
    )
    with pytest.raises(ValueError, match="missing Sina futures realtime node"):
        provider.fetch([contract])
