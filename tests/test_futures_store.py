from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from stock_harness.models import (
    FuturesContinuousSeries,
    FuturesBarState,
    FuturesCalendarDay,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesPriceBasis,
    FuturesProduct,
    FuturesRollMapping,
    FuturesSeriesKind,
    Instrument,
    InstrumentKind,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


CHINA_TIME = timezone(timedelta(hours=8))


def _catalog():
    product = FuturesProduct(
        symbol="FUTPROD:SHFE:CU",
        product_code="CU",
        display_name="Copper",
        exchange=FuturesExchange.SHFE,
        multiplier=None,
        per_unit=5,
        trading_unit="contract",
        quote_unit="CNY/tonne",
    )
    contract = FuturesContract(
        symbol="FUT:SHFE:CU:202609",
        provider_symbol="CU2609.SHF",
        product_symbol=product.symbol,
        display_name="Copper 2609",
        exchange=FuturesExchange.SHFE,
        contract_month="202609",
        listed_on=date(2025, 9, 16),
        last_trading_date=date(2026, 9, 15),
        delivery_date=date(2026, 9, 18),
        multiplier=None,
        per_unit=5,
        trading_unit="contract",
        quote_unit="CNY/tonne",
        lifecycle_status=FuturesLifecycleStatus.TRADING,
    )
    series = FuturesContinuousSeries(
        symbol="FUTCONT:SHFE:CU:MAIN:raw",
        provider_symbol="CU.SHF",
        product_symbol=product.symbol,
        display_name="Copper main",
        exchange=FuturesExchange.SHFE,
        series_kind=FuturesSeriesKind.MAIN,
        series_variant="MAIN",
        price_basis=FuturesPriceBasis.RAW,
        rule_version="tushare-fut-mapping-v1",
    )
    return product, contract, series


def _bar(
    trading_day: date,
    state: FuturesBarState = FuturesBarState.FINAL,
    source: str = "tushare-futures",
    close: float = 102,
    provider_time: datetime | None = None,
) -> FuturesDailyBar:
    return FuturesDailyBar(
        symbol="FUT:SHFE:CU:202609",
        trading_day=trading_day,
        provider_date=trading_day,
        open=100,
        high=max(103, close),
        low=99,
        close=close,
        previous_close=98,
        settlement=101 if state is FuturesBarState.FINAL else None,
        previous_settlement=99,
        volume_contracts=1234,
        amount=5_000_000 if state is FuturesBarState.FINAL else None,
        open_interest_contracts=4321,
        open_interest_change_contracts=(12 if state is FuturesBarState.FINAL else None),
        delivery_settlement=None,
        source=source,
        state=state,
        provider_time=provider_time,
    )


def test_new_store_applies_ready_futures_schema() -> None:
    with SQLiteMarketDataStore(":memory:") as store:
        assert store.futures_storage_status() == {
            "ready": True, "schema_version": 1, "error": None,
        }
        table = store._connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'futures_contracts'"
        ).fetchone()
        assert table is not None


def test_futures_catalog_upsert_is_idempotent_and_preserves_relations() -> None:
    product, contract, series = _catalog()
    with SQLiteMarketDataStore(":memory:") as store:
        first = store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        second = store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        assert first == second == {
            "products": 1, "contracts": 1, "continuous_series": 1,
        }
        assert store.list_futures_contracts() == [contract]
        counts = store._connection.execute(
            """
            SELECT
              (SELECT count(*) FROM futures_products),
              (SELECT count(*) FROM futures_contracts),
              (SELECT count(*) FROM futures_continuous_series)
            """
        ).fetchone()
        assert tuple(counts) == (1, 1, 1)


def test_catalog_with_missing_product_rolls_back_before_exposing_instrument() -> None:
    product, contract, _series = _catalog()
    invalid = replace(contract, product_symbol="FUTPROD:SHFE:AL")
    with SQLiteMarketDataStore(":memory:") as store:
        with pytest.raises(ValueError, match="missing products"):
            store.upsert_futures_catalog("tushare-futures", [product], [invalid], [])
        assert store.search_instruments("Copper 2609") == []


def test_existing_stock_database_reopens_with_futures_schema(tmp_path: Path) -> None:
    path = tmp_path / "market.sqlite"
    with SQLiteMarketDataStore(path) as store:
        store.upsert_instruments([
            Instrument("600519.SH", "Kweichow Moutai", InstrumentKind.STOCK, "SH")
        ])
    with SQLiteMarketDataStore(path) as reopened:
        assert reopened.futures_storage_status()["ready"] is True
        assert reopened.get_instrument_summary("600519.SH")["symbol"] == "600519.SH"


def test_failed_futures_migration_leaves_stock_store_usable(caplog) -> None:
    with patch(
        "stock_harness.sqlite_store._FUTURES_SCHEMA",
        "CREATE TABLE futures_broken(;",
    ):
        with SQLiteMarketDataStore(":memory:") as store:
            status = store.futures_storage_status()
            assert status["ready"] is False
            assert status["error"]
            stock = Instrument(
                "600519.SH", "Kweichow Moutai", InstrumentKind.STOCK, "SH"
            )
            store.upsert_instruments([stock])
            assert store.get_instrument_summary(stock.symbol)["kind"] == "stock"
            with pytest.raises(RuntimeError, match="futures storage is unavailable"):
                store.list_futures_contracts()
    assert "futures_schema_migration_failed" in caplog.text


def test_newer_futures_schema_is_not_downgraded(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite"
    with SQLiteMarketDataStore(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO futures_schema_metadata VALUES (2, 'ready', 1)"
    )
    connection.commit()
    connection.close()

    with SQLiteMarketDataStore(path) as reopened:
        assert reopened.futures_storage_status()["ready"] is False
        assert "newer than this application" in reopened.futures_storage_status()["error"]
        reopened.upsert_instruments([
            Instrument("600519.SH", "Kweichow Moutai", InstrumentKind.STOCK, "SH")
        ])
        versions = reopened._connection.execute(
            "SELECT schema_version FROM futures_schema_metadata ORDER BY schema_version"
        ).fetchall()
        assert [int(item[0]) for item in versions] == [1, 2]


def test_final_daily_storage_is_idempotent_and_preserves_futures_fields() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    bar = _bar(day)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        first = store.upsert_futures_daily_bars("tushare-futures", [bar])
        second = store.upsert_futures_daily_bars("tushare-futures", [bar])
        stored = store.list_futures_daily_bars(contract.symbol, day, day)
        assert (first.changed, second.changed, second.unchanged) == (1, 0, 1)
        assert stored == [bar]
        corrected = replace(bar, close=103)
        assert store.upsert_futures_daily_bars(
            "tushare-futures", [corrected]
        ).changed == 1
        assert store.list_futures_daily_bars(contract.symbol, day, day)[0].close == 103


def test_calendar_and_roll_mapping_round_trip() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    calendar = FuturesCalendarDay(
        FuturesExchange.SHFE, day, True, date(2026, 8, 19)
    )
    mapping = FuturesRollMapping(
        series.symbol, series.provider_symbol, day,
        contract.symbol, contract.provider_symbol,
    )
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        assert store.upsert_futures_calendar("tushare-futures", [calendar]) == 1
        assert store.upsert_futures_roll_mappings("tushare-futures", [mapping]) == 1
        assert store.list_futures_calendar(
            "tushare-futures", FuturesExchange.SHFE, day, day
        ) == [calendar]
        assert store.list_futures_roll_mappings(series.symbol, day, day) == [mapping]


def test_canonical_daily_takes_over_without_deleting_provisional_audit() -> None:
    product, contract, series = _catalog()
    prior_day = date(2026, 8, 19)
    current_day = date(2026, 8, 20)
    observed = datetime(2026, 8, 20, 14, 30, tzinfo=CHINA_TIME)
    provisional = _bar(
        current_day, FuturesBarState.PROVISIONAL,
        "akshare-futures-zh-spot", 104, observed,
    )
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_daily_bars("tushare-futures", [_bar(prior_day)])
        store.upsert_futures_provisional_daily_bars(
            provisional.source, [provisional], observed
        )
        fused = store.list_fused_futures_daily_bars(
            contract.symbol, prior_day, current_day
        )
        assert [item.state for item in fused] == [
            FuturesBarState.FINAL, FuturesBarState.PROVISIONAL,
        ]

        final = _bar(current_day, close=103)
        store.upsert_futures_daily_bars("tushare-futures", [final])
        fused = store.list_fused_futures_daily_bars(
            contract.symbol, prior_day, current_day
        )
        audit = store.list_futures_provisional_audit(contract.symbol)
        assert fused == [_bar(prior_day), final]
        assert audit[0]["takeover_state"] == "canonical-taken-over"
        assert audit[0]["close"] == 104


def test_fused_read_uses_latest_active_provisional_source() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    early = datetime(2026, 8, 20, 14, 0, tzinfo=CHINA_TIME)
    late = early + timedelta(seconds=10)
    spot = _bar(day, FuturesBarState.PROVISIONAL, "spot", 102, early)
    fallback = _bar(day, FuturesBarState.PROVISIONAL, "fallback", 103, late)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_provisional_daily_bars("spot", [spot], early)
        store.upsert_futures_provisional_daily_bars("fallback", [fallback], late)
        fused = store.list_fused_futures_daily_bars(contract.symbol, day, day)
        assert fused == [fallback]
        assert len(store.list_futures_provisional_audit(contract.symbol)) == 2


def test_final_and_provisional_namespaces_reject_wrong_state() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    observed = datetime(2026, 8, 20, 14, 0, tzinfo=CHINA_TIME)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        with pytest.raises(ValueError, match="final bars only"):
            store.upsert_futures_daily_bars(
                "spot", [_bar(day, FuturesBarState.PROVISIONAL, "spot", 102, observed)]
            )
        with pytest.raises(ValueError, match="provisional bars only"):
            store.upsert_futures_provisional_daily_bars(
                "tushare-futures", [_bar(day)], observed
            )


def test_futures_storage_rejects_stock_target_and_mapping_provider_mismatch() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        stock = Instrument("600519.SH", "Kweichow Moutai", InstrumentKind.STOCK, "SH")
        store.upsert_instruments([stock])
        with pytest.raises(ValueError, match="invalid instrument kinds"):
            store.upsert_futures_daily_bars(
                "tushare-futures", [replace(_bar(day), symbol=stock.symbol)]
            )
        invalid_mapping = FuturesRollMapping(
            series.symbol, "WRONG.SHF", day,
            contract.symbol, contract.provider_symbol,
        )
        with pytest.raises(ValueError, match="Provider identity mismatch"):
            store.upsert_futures_roll_mappings(
                "tushare-futures", [invalid_mapping]
            )


def test_futures_sync_checkpoint_and_receipt_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sync.sqlite"
    with SQLiteMarketDataStore(path) as store:
        first = store.checkpoint_futures_sync(
            "tushare-futures", "daily", "SHFE", "CU2609.SHF",
            date(2026, 8, 1), date(2026, 8, 10), 8,
        )
        merged = store.checkpoint_futures_sync(
            "tushare-futures", "daily", "SHFE", "CU2609.SHF",
            date(2026, 8, 5), date(2026, 8, 20), 7,
        )
        receipt = store.record_futures_update_receipt(
            "tushare-futures", "daily", "CU2609.SHF",
            date(2026, 8, 20), 7, b"digest", "complete", "ok",
        )
        assert first.covered_through == date(2026, 8, 10)
        assert (merged.covered_from, merged.covered_through) == (
            date(2026, 8, 1), date(2026, 8, 20)
        )
        assert merged.last_batch_rows == 7
    with SQLiteMarketDataStore(path) as reopened:
        assert reopened.get_futures_sync_state(
            "tushare-futures", "daily", "SHFE", "CU2609.SHF"
        ) == merged
        assert reopened.get_futures_update_receipt(
            "tushare-futures", "daily", "CU2609.SHF", date(2026, 8, 20)
        ) == receipt


def test_mapping_window_replacement_is_atomic_and_correction_aware() -> None:
    product, first_contract, series = _catalog()
    second_contract = replace(
        first_contract,
        symbol="FUT:SHFE:CU:202610",
        provider_symbol="CU2610.SHF",
        contract_month="202610",
        display_name="Copper 2610",
        last_trading_date=date(2026, 10, 15),
        delivery_date=date(2026, 10, 20),
    )
    first_day, second_day = date(2026, 8, 19), date(2026, 8, 20)

    def mapping(day, contract):
        return FuturesRollMapping(
            series.symbol, series.provider_symbol, day,
            contract.symbol, contract.provider_symbol,
        )

    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [first_contract, second_contract], [series]
        )
        state, initial_receipt = store.replace_futures_roll_mapping_window(
            "tushare-futures", "SHFE", series.symbol, first_day, second_day,
            [mapping(first_day, first_contract), mapping(second_day, first_contract)],
        )
        assert (state.covered_from, state.covered_through) == (first_day, second_day)
        corrected_state, corrected_receipt = store.replace_futures_roll_mapping_window(
            "tushare-futures", "SHFE", series.symbol, second_day, second_day,
            [mapping(second_day, second_contract)],
        )
        stored = store.list_futures_roll_mappings(series.symbol, first_day, second_day)
        assert [item.contract_symbol for item in stored] == [
            first_contract.symbol, second_contract.symbol,
        ]
        assert corrected_state.covered_from == first_day
        assert corrected_receipt.payload_hash != initial_receipt.payload_hash

        invalid = replace(
            mapping(second_day, first_contract),
            contract_provider_symbol="WRONG.SHF",
        )
        with pytest.raises(ValueError, match="Provider identity mismatch"):
            store.replace_futures_roll_mapping_window(
                "tushare-futures", "SHFE", series.symbol,
                second_day, second_day, [invalid],
            )
        assert store.list_futures_roll_mappings(
            series.symbol, second_day, second_day
        )[0].contract_symbol == second_contract.symbol


def test_empty_mapping_window_removes_suffix_and_records_empty_receipt() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    mapping = FuturesRollMapping(
        series.symbol, series.provider_symbol, day,
        contract.symbol, contract.provider_symbol,
    )
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.replace_futures_roll_mapping_window(
            "tushare-futures", "SHFE", series.symbol, day, day, [mapping]
        )
        _state, receipt = store.replace_futures_roll_mapping_window(
            "tushare-futures", "SHFE", series.symbol, day, day, []
        )
        assert store.list_futures_roll_mappings(series.symbol, day, day) == []
        assert (receipt.status, receipt.row_count) == ("empty", 0)


def test_futures_coverage_reports_field_completeness() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_daily_bars(
            "tushare-futures",
            [replace(_bar(day), settlement=None, open_interest_contracts=None)],
        )
        coverage = store.list_futures_coverage()[0]
        assert coverage["symbol"] == contract.symbol
        assert coverage["rows"] == 1
        assert coverage["missing_settlement_rows"] == 1
        assert coverage["missing_open_interest_rows"] == 1
