from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from stock_harness.futures_validation import (
    AkShareExchangeFuturesValidationProvider,
    AkShareSinaFuturesValidationProvider,
    compare_futures_provisional_takeovers,
    validate_futures_contracts,
)
from stock_harness.models import (
    FuturesBarState,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesProduct,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


DAY = date(2026, 8, 20)


class _Frame:
    empty = False

    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


class _AkShare:
    def get_futures_daily(self, **kwargs):
        assert kwargs == {"start_date": "20260820", "end_date": "20260820", "market": "SHFE"}
        return _Frame([{
            "symbol": "CU2609", "date": DAY, "open": 101, "high": 104,
            "low": 98, "close": 103, "settle": 102, "pre_settle": 100,
            "volume": 12345, "open_interest": 54321,
        }])

    def futures_zh_daily_sina(self, **kwargs):
        assert kwargs == {"symbol": "CU2609"}
        return _Frame([{
            "date": DAY, "open": 101, "high": 104, "low": 98, "close": 103,
            "settle": 102, "volume": 12345, "hold": 54321,
        }])


def _contract(exchange=FuturesExchange.SHFE, provider_symbol="CU2609.SHF"):
    return FuturesContract(
        symbol=f"FUT:{exchange.value}:CU:202609", provider_symbol=provider_symbol,
        product_symbol=f"FUTPROD:{exchange.value}:CU", display_name="Copper 2609",
        exchange=exchange, contract_month="202609", listed_on=date(2025, 9, 16),
        last_trading_date=date(2026, 9, 15), delivery_date=date(2026, 9, 18),
        multiplier=None, per_unit=5, trading_unit="contract", quote_unit="CNY/tonne",
        lifecycle_status=FuturesLifecycleStatus.TRADING,
    )


def _store():
    store = SQLiteMarketDataStore(":memory:")
    contract = _contract()
    store.upsert_futures_catalog("tushare", [FuturesProduct(
        symbol=contract.product_symbol, product_code="CU", display_name="Copper",
        exchange=FuturesExchange.SHFE, multiplier=None, per_unit=5,
        trading_unit="contract", quote_unit="CNY/tonne",
    )], [contract], [])
    store.upsert_futures_daily_bars("tushare", [FuturesDailyBar(
        symbol=contract.symbol, trading_day=DAY, provider_date=DAY,
        open=101, high=104, low=98, close=103, previous_close=99,
        settlement=102, previous_settlement=100, volume_contracts=12345,
        amount=4_567_500, open_interest_contracts=54321,
        open_interest_change_contracts=-123, delivery_settlement=None,
        source="tushare", state=FuturesBarState.FINAL,
    )])
    return store, contract


def test_compares_stored_tushare_with_exchange_and_public_chart_sources():
    with _store()[0] as store:
        report = validate_futures_contracts(
            store,
            [AkShareExchangeFuturesValidationProvider(_AkShare()),
             AkShareSinaFuturesValidationProvider(_AkShare())],
            ["FUT:SHFE:CU:202609"], DAY,
        )
    assert report.schema_version == "futures-cross-source-v1"
    assert [item.status for item in report.results] == ["match", "match"]
    assert next(
        item for item in report.results[0].fields if item.field == "volume_contracts"
    ).unit == "contracts"


def test_reports_provider_errors_and_missing_catalog_without_mutating_store():
    class Broken:
        code = "broken"

        def fetch_contract_daily(self, contract, trade_date):
            raise RuntimeError("offline")

    with _store()[0] as store:
        before = store.list_futures_daily_bars("FUT:SHFE:CU:202609", DAY, DAY)
        report = validate_futures_contracts(
            store, [Broken()], ["FUT:SHFE:CU:202609", "FUT:DCE:A:202609"], DAY,
        )
        after = store.list_futures_daily_bars("FUT:SHFE:CU:202609", DAY, DAY)
    assert [item.status for item in report.results] == ["error", "missing"]
    assert before == after


def test_reports_sina_zero_settlement_as_unavailable_instead_of_mismatch():
    class SinaWithoutSettlement(_AkShare):
        def futures_zh_daily_sina(self, **kwargs):
            frame = super().futures_zh_daily_sina(**kwargs)
            frame.rows[0]["settle"] = 0
            return frame

    with _store()[0] as store:
        report = validate_futures_contracts(
            store, [AkShareSinaFuturesValidationProvider(SinaWithoutSettlement())],
            ["FUT:SHFE:CU:202609"], DAY,
        )
    assert report.results[0].status == "partial"
    assert report.results[0].message == "unavailable fields: settlement"
    settlement = next(
        item for item in report.results[0].fields if item.field == "settlement"
    )
    assert settlement.validator is None
    assert settlement.matched is None


def test_accepts_czce_three_digit_exchange_symbol_only_for_matching_decade():
    contract = _contract(FuturesExchange.CZCE, "CU2609.ZCE")

    class Client:
        def get_futures_daily(self, **kwargs):
            return _Frame([{
                "symbol": "CU609", "date": DAY, "open": 1, "high": 1,
                "low": 1, "close": 1, "settle": 1, "pre_settle": 1,
                "volume": 1, "open_interest": 1,
            }])

    bar = AkShareExchangeFuturesValidationProvider(Client()).fetch_contract_daily(contract, DAY)
    assert bar is not None
    assert bar.symbol == contract.symbol


def test_compares_retained_day_and_night_observations_with_later_final_rows():
    china = timezone(timedelta(hours=8))
    with _store()[0] as store:
        contract = store.get_futures_contracts(["FUT:SHFE:CU:202609"])[0]
        day_time = datetime(2026, 8, 20, 14, 30, tzinfo=china)
        store.upsert_futures_provisional_daily_bars(
            "akshare-futures-spot",
            [FuturesDailyBar(
                symbol=contract.symbol, trading_day=DAY, provider_date=DAY,
                open=100, high=105, low=97, close=104, previous_close=99,
                settlement=None, previous_settlement=100, volume_contracts=12000,
                amount=None, open_interest_contracts=54000,
                open_interest_change_contracts=None, delivery_settlement=None,
                source="akshare-futures-spot", state=FuturesBarState.PROVISIONAL,
                provider_time=day_time,
            )],
            day_time,
        )
        next_day = date(2026, 8, 21)
        night_time = datetime(2026, 8, 20, 21, 30, tzinfo=china)
        store.upsert_futures_provisional_daily_bars(
            "akshare-futures-spot",
            [FuturesDailyBar(
                symbol=contract.symbol, trading_day=next_day, provider_date=DAY,
                open=103, high=106, low=102, close=105, previous_close=103,
                settlement=None, previous_settlement=102, volume_contracts=500,
                amount=None, open_interest_contracts=54500,
                open_interest_change_contracts=None, delivery_settlement=None,
                source="akshare-futures-spot", state=FuturesBarState.PROVISIONAL,
                provider_time=night_time,
            )],
            night_time,
        )
        prior_final = store.list_futures_daily_bars(contract.symbol, DAY, DAY)[0]
        store.upsert_futures_daily_bars("tushare", [replace(
            prior_final, trading_day=next_day, provider_date=next_day,
            open=103, high=107, low=101, close=104, settlement=104.5,
            volume_contracts=700, open_interest_contracts=54600,
        )])
        report = compare_futures_provisional_takeovers(
            store, [contract.symbol], DAY, next_day
        )
    assert [item.session_phase for item in report.results] == ["day", "night"]
    assert [item.status for item in report.results] == ["changed", "changed"]
    assert all(item.takeover_state == "canonical-taken-over" for item in report.results)
    assert report.results[1].provider_date == DAY
    assert report.results[1].trading_day == next_day
    assert report.results[1].final_settlement == 104.5
    assert "high" in report.results[1].message


def test_takeover_audit_keeps_pending_final_and_missing_catalog_explicit():
    china = timezone(timedelta(hours=8))
    with _store()[0] as store:
        contract = store.get_futures_contracts(["FUT:SHFE:CU:202609"])[0]
        next_day = date(2026, 8, 21)
        observed = datetime(2026, 8, 20, 21, 0, tzinfo=china)
        store.upsert_futures_provisional_daily_bars(
            "akshare-futures-spot",
            [FuturesDailyBar(
                symbol=contract.symbol, trading_day=next_day, provider_date=DAY,
                open=103, high=104, low=102, close=103, previous_close=103,
                settlement=None, previous_settlement=102, volume_contracts=1,
                amount=None, open_interest_contracts=None,
                open_interest_change_contracts=None, delivery_settlement=None,
                source="akshare-futures-spot", state=FuturesBarState.PROVISIONAL,
                provider_time=observed,
            )], observed,
        )
        report = compare_futures_provisional_takeovers(
            store, [contract.symbol, "FUT:DCE:A:202609"], next_day, next_day
        )
    assert [item.status for item in report.results] == ["pending-final", "missing-catalog"]
