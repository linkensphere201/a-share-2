from datetime import date
import logging

from stock_harness.futures_backfill import run_futures_backfill
from stock_harness.futures_provider import FuturesCatalog
from stock_harness.futures_provider import (
    FuturesDailyFetchResult,
    FuturesDailyRowRejection,
)
from stock_harness.futures_update import run_futures_increment
from stock_harness.models import (
    FuturesBarState,
    FuturesCalendarDay,
    FuturesContinuousSeries,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesPriceBasis,
    FuturesProduct,
    FuturesRollMapping,
    FuturesSeriesKind,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _catalog() -> FuturesCatalog:
    product = FuturesProduct(
        "FUTPROD:SHFE:CU", "CU", "Copper", FuturesExchange.SHFE,
        None, 5, "contract", "CNY/tonne",
    )
    contract = FuturesContract(
        "FUT:SHFE:CU:202609", "CU2609.SHF", product.symbol, "Copper 2609",
        FuturesExchange.SHFE, "202609", date(2026, 8, 18), date(2026, 8, 20),
        date(2026, 9, 18), None, 5, "contract", "CNY/tonne",
        FuturesLifecycleStatus.TRADING,
    )
    series = FuturesContinuousSeries(
        "FUTCONT:SHFE:CU:MAIN:raw", "CU.SHF", product.symbol, "Copper main",
        FuturesExchange.SHFE, FuturesSeriesKind.MAIN, "MAIN",
        FuturesPriceBasis.RAW, "tushare-fut-mapping-v1",
    )
    return FuturesCatalog((product,), (contract,), (series,))


def _bar(contract: FuturesContract, day: date) -> FuturesDailyBar:
    return FuturesDailyBar(
        contract.symbol, day, day, 100, 103, 99, 102, 98, 101, 99,
        1234, 5_000_000, 4321, 12, None, "tushare-futures",
        FuturesBarState.FINAL,
    )


class _Provider:
    code = "tushare-futures"

    def __init__(self, fail_daily: bool = False) -> None:
        self.catalog = _catalog()
        self.fail_daily = fail_daily
        self.daily_calls: list[tuple[date, date]] = []

    def discover_exchange(self, exchange, as_of):
        assert exchange is FuturesExchange.SHFE
        return self.catalog

    def calendar(self, exchange, start_date, end_date):
        return tuple(
            FuturesCalendarDay(exchange, date(2026, 8, day), True, None)
            for day in range(18, 21)
        )

    def fetch_contract_daily(self, contract, start_date, end_date):
        self.daily_calls.append((start_date, end_date))
        if self.fail_daily:
            raise RuntimeError("daily unavailable")
        return tuple(
            _bar(contract, date(2026, 8, day))
            for day in range(18, 21)
            if start_date <= date(2026, 8, day) <= end_date
        )

    def fetch_roll_mappings(self, series, contracts, start_date, end_date):
        contract = contracts[0]
        return (FuturesRollMapping(
            series.symbol, series.provider_symbol, date(2026, 8, 18),
            contract.symbol, contract.provider_symbol,
        ),)


class _RejectingProvider(_Provider):
    def fetch_contract_daily_result(self, contract, start_date, end_date):
        return FuturesDailyFetchResult(
            self.fetch_contract_daily(contract, start_date, end_date),
            (FuturesDailyRowRejection(date(2026, 8, 19), "invalid-daily-bar"),),
        )


def test_futures_backfill_is_lifecycle_bounded_and_resumable() -> None:
    provider = _Provider()
    with SQLiteMarketDataStore(":memory:") as store:
        first = run_futures_backfill(
            provider, store, [FuturesExchange.SHFE],
            date(1990, 1, 1), date(2026, 8, 20),
        )
        assert provider.daily_calls == [(date(2026, 8, 18), date(2026, 8, 20))]
        assert first.daily_rows_changed == 3
        assert first.mappings_written == 1
        assert first.coverage[0]["rows"] == 3
        second = run_futures_backfill(
            provider, store, [FuturesExchange.SHFE],
            date(1990, 1, 1), date(2026, 8, 20),
        )
        assert second.contracts_skipped == 1
        assert provider.daily_calls == [(date(2026, 8, 18), date(2026, 8, 20))]


def test_failed_contract_window_does_not_advance_cursor_or_remove_history() -> None:
    healthy = _Provider()
    failing = _Provider(fail_daily=True)
    contract = healthy.catalog.contracts[0]
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            healthy.code, healthy.catalog.products, healthy.catalog.contracts,
            healthy.catalog.continuous_series,
        )
        store.upsert_futures_daily_bars(
            healthy.code, [_bar(contract, date(2026, 8, 18))]
        )
        result = run_futures_backfill(
            failing, store, [FuturesExchange.SHFE],
            date(2026, 8, 18), date(2026, 8, 20),
        )
        assert len(result.errors) == 1
        assert store.get_futures_sync_state(
            failing.code, "daily", "SHFE", contract.symbol
        ) is None
        assert len(store.list_futures_daily_bars(
            contract.symbol, date(2026, 8, 18), date(2026, 8, 20)
        )) == 1


def test_rejected_daily_rows_are_persisted_as_partial_quality_evidence() -> None:
    provider = _RejectingProvider()
    contract = provider.catalog.contracts[0]
    with SQLiteMarketDataStore(":memory:") as store:
        result = run_futures_backfill(
            provider, store, [FuturesExchange.SHFE],
            date(2026, 8, 18), date(2026, 8, 20),
        )
        receipt = store.get_futures_update_receipt(
            provider.code, "daily", contract.symbol, date(2026, 8, 20)
        )
        audit = store.audit_futures_integrity()

    assert result.rejected_daily_rows == 1
    assert receipt is not None
    assert (receipt.status, receipt.message) == ("partial", "rejected_rows=1")
    assert audit["receipt_status_counts"]["partial"] == 1


def test_backfill_logs_bounded_structure_without_provider_error_text(caplog) -> None:
    provider = _Provider(fail_daily=True)
    with SQLiteMarketDataStore(":memory:") as store, caplog.at_level(logging.INFO):
        run_futures_backfill(
            provider, store, [FuturesExchange.SHFE],
            date(2026, 8, 18), date(2026, 8, 20),
        )
    assert "futures_backfill_started" in caplog.text
    assert "futures_backfill_item_failed" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "daily unavailable" not in caplog.text
    assert "futures_backfill_completed" in caplog.text


def test_increment_logs_bounded_structure_without_provider_error_text(caplog) -> None:
    provider = _Provider(fail_daily=True)
    with SQLiteMarketDataStore(":memory:") as store, caplog.at_level(logging.INFO):
        result = run_futures_increment(
            provider, store, [FuturesExchange.SHFE], date(2026, 8, 20), 2
        )
    assert result.errors[0] == "FUT:SHFE:CU:202609 final increment: RuntimeError"
    assert all("daily unavailable" not in item for item in result.errors)
    assert "futures_increment_started" in caplog.text
    assert "futures_increment_item_failed" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "daily unavailable" not in caplog.text
    assert "futures_increment_completed" in caplog.text


def test_futures_increment_refreshes_correction_window_and_missing_tail() -> None:
    provider = _Provider()
    contract = provider.catalog.contracts[0]
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            provider.code, provider.catalog.products, provider.catalog.contracts,
            provider.catalog.continuous_series,
        )
        store.upsert_futures_daily_bars(
            provider.code, [_bar(contract, date(2026, 8, 18))]
        )
        store.checkpoint_futures_sync(
            provider.code, "daily", "SHFE", contract.symbol,
            date(2026, 8, 18), date(2026, 8, 18), 1,
        )
        result = run_futures_increment(
            provider, store, [FuturesExchange.SHFE], date(2026, 8, 20), 2
        )
        assert provider.daily_calls == [(date(2026, 8, 19), date(2026, 8, 20))]
        assert result.contracts_updated == 1
        assert result.rows_changed == 2
        state = store.get_futures_sync_state(
            provider.code, "daily", "SHFE", contract.symbol
        )
        assert state.covered_through == date(2026, 8, 20)


def test_zero_correction_window_skips_already_completed_contract() -> None:
    provider = _Provider()
    contract = provider.catalog.contracts[0]
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            provider.code, provider.catalog.products, provider.catalog.contracts,
            provider.catalog.continuous_series,
        )
        store.checkpoint_futures_sync(
            provider.code, "daily", "SHFE", contract.symbol,
            date(2026, 8, 18), date(2026, 8, 20), 3,
        )
        result = run_futures_increment(
            provider, store, [FuturesExchange.SHFE], date(2026, 8, 20), 0
        )
        assert provider.daily_calls == []
        assert result.contracts_updated == 0
