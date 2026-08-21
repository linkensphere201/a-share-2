from datetime import date

import pytest

from stock_harness.config import FuturesCanonicalProviderSettings
from stock_harness.futures_provider import TushareFuturesProvider
from stock_harness.models import (
    FuturesBarState,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesSeriesKind,
)


class _Client:
    def fut_basic(self, **kwargs):
        exchange = kwargs["exchange"]
        product = "IF" if exchange == "CFFEX" else "CU"
        suffix = {
            "CFFEX": "CFX", "DCE": "DCE", "CZCE": "ZCE",
            "SHFE": "SHF", "INE": "INE", "GFEX": "GFE",
        }[exchange]
        if kwargs["fut_type"] == "2":
            return [{
                "ts_code": f"{product}.{suffix}", "symbol": product, "exchange": exchange,
                "name": f"{product}主力", "fut_code": product,
            }]
        return [{
            "ts_code": f"{product}2609.{suffix}", "symbol": f"{product}2609",
            "exchange": exchange, "name": f"{product}2609", "fut_code": product,
            "multiplier": 300 if exchange == "CFFEX" else None,
            "trade_unit": "contract", "per_unit": None if exchange == "CFFEX" else 5,
            "quote_unit": "CNY/point" if exchange == "CFFEX" else "CNY/tonne",
            "quote_unit_desc": "1", "d_mode_desc": "cash",
            "list_date": "20250916", "delist_date": "20260915",
            "d_month": "202609", "last_ddate": "20260918",
            "trade_time_desc": "day and night",
        }]

    def fut_trade_cal(self, **kwargs):
        return [
            {
                "exchange": kwargs["exchange"], "cal_date": "20260820",
                "is_open": 1, "pretrade_date": "20260819",
            },
            {
                "exchange": kwargs["exchange"], "cal_date": "20260821",
                "is_open": 0, "pretrade_date": "20260820",
            },
        ]

    def fut_daily(self, **kwargs):
        return [{
            "ts_code": kwargs["ts_code"], "trade_date": "20260820",
            "pre_close": 99, "pre_settle": 100, "open": 101,
            "high": 104, "low": 98, "close": 103, "settle": 102,
            "change1": 3, "change2": 2, "vol": 12345.0,
            "amount": 456.75, "oi": 54321.0, "oi_chg": -123.0,
            "delv_settle": None,
        }]

    def fut_mapping(self, **kwargs):
        return [{
            "ts_code": kwargs["ts_code"], "trade_date": "20260820",
            "mapping_ts_code": "CU2609.SHF",
        }]


def _settings(**changes) -> FuturesCanonicalProviderSettings:
    values = {
        "enabled": True,
        "provider": "tushare",
        "token_env": "TUSHARE_TOKEN",
        "env_file": None,
        "requests_per_minute": 0,
        "timeout_seconds": 5,
        "retries": 0,
        "retry_wait_seconds": 0,
        "backoff_multiplier": 1,
        "api_url": "http://example.invalid/dataapi",
    }
    values.update(changes)
    return FuturesCanonicalProviderSettings(**values)


@pytest.mark.parametrize("exchange", list(FuturesExchange))
def test_discovers_real_and_main_contracts_for_all_supported_exchanges(
    exchange: FuturesExchange,
) -> None:
    catalog = TushareFuturesProvider(_settings(), _Client()).discover_exchange(
        exchange, date(2026, 8, 20),
    )
    product = catalog.products[0]
    contract = catalog.contracts[0]
    series = catalog.continuous_series[0]
    assert product.exchange is exchange
    assert contract.symbol.endswith(":202609")
    assert contract.lifecycle_status is FuturesLifecycleStatus.TRADING
    assert contract.provider_symbol.endswith((".CFX", ".DCE", ".ZCE", ".SHF", ".INE", ".GFE"))
    assert series.series_kind is FuturesSeriesKind.MAIN
    assert series.provider_symbol.split(".")[0] == product.product_code


def test_keeps_multiplier_and_per_unit_as_distinct_provider_fields() -> None:
    provider = TushareFuturesProvider(_settings(), _Client())
    commodity = provider.discover_exchange(FuturesExchange.SHFE, date(2026, 8, 20))
    financial = provider.discover_exchange(FuturesExchange.CFFEX, date(2026, 8, 20))
    assert (commodity.contracts[0].multiplier, commodity.contracts[0].per_unit) == (None, 5)
    assert (financial.contracts[0].multiplier, financial.contracts[0].per_unit) == (300, None)


def test_fetches_exchange_calendar_with_previous_trading_day() -> None:
    days = TushareFuturesProvider(_settings(), _Client()).calendar(
        FuturesExchange.SHFE, date(2026, 8, 20), date(2026, 8, 21),
    )
    assert [(item.calendar_date, item.is_open) for item in days] == [
        (date(2026, 8, 20), True), (date(2026, 8, 21), False),
    ]
    assert days[0].previous_trading_day == date(2026, 8, 19)


def test_fetches_final_daily_fields_without_stock_volume_semantics() -> None:
    provider = TushareFuturesProvider(_settings(), _Client())
    contract = provider.discover_exchange(
        FuturesExchange.SHFE, date(2026, 8, 20),
    ).contracts[0]
    bar = provider.fetch_contract_daily(
        contract, date(2026, 8, 1), date(2026, 8, 20),
    )[0]
    assert bar.state is FuturesBarState.FINAL
    assert bar.volume_contracts == 12345
    assert bar.amount == 4_567_500
    assert bar.open_interest_contracts == 54321
    assert bar.open_interest_change_contracts == -123
    assert bar.previous_settlement == 100
    assert bar.change_percent() == pytest.approx(3)


def test_rejects_only_invalid_daily_rows_without_losing_valid_history(caplog) -> None:
    class MixedDailyClient(_Client):
        def fut_daily(self, **kwargs):
            valid = super().fut_daily(**kwargs)[0]
            missing_open = {**valid, "trade_date": "20260819", "open": None}
            invalid_envelope = {
                **valid, "trade_date": "20260818", "high": 102, "close": 103,
            }
            return [missing_open, valid, invalid_envelope]

    provider = TushareFuturesProvider(_settings(), MixedDailyClient())
    contract = provider.discover_exchange(
        FuturesExchange.SHFE, date(2026, 8, 20),
    ).contracts[0]
    with caplog.at_level("WARNING"):
        bars = provider.fetch_contract_daily(
            contract, date(2026, 8, 18), date(2026, 8, 20),
        )

    assert [bar.trading_day for bar in bars] == [date(2026, 8, 20)]
    assert "futures_provider_daily_rows_rejected" in caplog.text
    assert "count=2" in caplog.text
    assert "empty field" not in caplog.text


def test_resolves_roll_mapping_to_known_canonical_real_contract() -> None:
    provider = TushareFuturesProvider(_settings(), _Client())
    catalog = provider.discover_exchange(FuturesExchange.SHFE, date(2026, 8, 20))
    mapping = provider.fetch_roll_mappings(
        catalog.continuous_series[0], catalog.contracts,
        date(2026, 8, 1), date(2026, 8, 20),
    )[0]
    assert mapping.series_symbol == catalog.continuous_series[0].symbol
    assert mapping.contract_symbol == catalog.contracts[0].symbol
    assert mapping.effective_from == date(2026, 8, 20)


def test_rejects_mapping_to_an_undiscovered_contract() -> None:
    class UnknownMappingClient(_Client):
        def fut_mapping(self, **kwargs):
            rows = super().fut_mapping(**kwargs)
            rows[0]["mapping_ts_code"] = "CU2610.SHF"
            return rows

    provider = TushareFuturesProvider(_settings(), UnknownMappingClient())
    catalog = provider.discover_exchange(FuturesExchange.SHFE, date(2026, 8, 20))
    with pytest.raises(ValueError, match="unknown real contract"):
        provider.fetch_roll_mappings(
            catalog.continuous_series[0], catalog.contracts,
            date(2026, 8, 1), date(2026, 8, 20),
        )


def test_missing_required_provider_field_is_not_silently_defaulted() -> None:
    class MalformedClient(_Client):
        def fut_basic(self, **kwargs):
            rows = super().fut_basic(**kwargs)
            if kwargs["fut_type"] == "1":
                rows[0].pop("d_month")
            return rows

    with pytest.raises(ValueError, match="missing field: d_month"):
        TushareFuturesProvider(_settings(), MalformedClient()).discover_exchange(
            FuturesExchange.SHFE, date(2026, 8, 20),
        )


def test_long_daily_and_mapping_ranges_are_split_below_provider_row_limits() -> None:
    class WindowClient(_Client):
        def __init__(self) -> None:
            self.daily_calls = []
            self.mapping_calls = []

        def fut_daily(self, **kwargs):
            self.daily_calls.append((kwargs["start_date"], kwargs["end_date"]))
            row = super().fut_daily(**kwargs)[0]
            row["trade_date"] = kwargs["end_date"]
            return [row]

        def fut_mapping(self, **kwargs):
            self.mapping_calls.append((kwargs["start_date"], kwargs["end_date"]))
            row = super().fut_mapping(**kwargs)[0]
            row["trade_date"] = kwargs["end_date"]
            return [row]

    client = WindowClient()
    provider = TushareFuturesProvider(_settings(), client)
    catalog = provider.discover_exchange(FuturesExchange.SHFE, date(2026, 8, 20))
    start, end = date(2010, 1, 1), date(2020, 1, 1)
    bars = provider.fetch_contract_daily(catalog.contracts[0], start, end)
    mappings = provider.fetch_roll_mappings(
        catalog.continuous_series[0], catalog.contracts, start, end,
    )
    assert len(client.daily_calls) == 3
    assert len(client.mapping_calls) == 3
    assert len(bars) == 3
    assert len(mappings) == 3
    assert all(
        (date.fromisoformat(end_date[:4] + "-" + end_date[4:6] + "-" + end_date[6:])
         - date.fromisoformat(start_date[:4] + "-" + start_date[4:6] + "-" + start_date[6:])).days < 1800
        for start_date, end_date in client.daily_calls + client.mapping_calls
    )
