from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.cli import _exit_if_structural_errors
from stock_harness.futures_continuous import materialize_raw_continuous
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
            "ready": True, "schema_version": 3, "error": None,
        }
        table = store._connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'futures_contracts'"
        ).fetchone()
        assert table is not None
        index = store._connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND name = 'futures_roll_mapping_series_date'"
        ).fetchone()
        assert index is not None


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
        assert store.list_futures_products() == [product]
        assert store.get_instrument_summary(contract.symbol)["classification_label"] == "期货合约"
        continuous_summary = store.get_instrument_summary(series.symbol)
        assert continuous_summary["classification_label"] == "期货连续"
        assert continuous_summary["price_basis"] == "raw"
        assert continuous_summary["rule_version"] == "tushare-fut-mapping-v1"
        with TestClient(create_app(store)) as client:
            payload = client.get(
                f"/api/instruments/{series.symbol}/daily-bars"
            ).json()
        assert payload["instrument_kind"] == "futures-continuous"
        assert payload["price_basis"] == "raw"
        assert payload["rule_version"] == "tushare-fut-mapping-v1"
        counts = store._connection.execute(
            """
            SELECT
              (SELECT count(*) FROM futures_products),
              (SELECT count(*) FROM futures_contracts),
              (SELECT count(*) FROM futures_continuous_series)
            """
        ).fetchone()
        assert tuple(counts) == (1, 1, 1)


def test_futures_search_filters_metadata_coverage_and_pinyin() -> None:
    product, contract, series = _catalog()
    product = replace(product, display_name="沪铜")
    contract = replace(contract, display_name="沪铜2609")
    series = replace(series, display_name="沪铜主力")
    expired = replace(
        contract,
        symbol="FUT:SHFE:CU:202603",
        provider_symbol="CU2603.SHF",
        display_name="沪铜2603",
        contract_month="202603",
        last_trading_date=date(2026, 3, 16),
        delivery_date=date(2026, 3, 19),
        lifecycle_status=FuturesLifecycleStatus.EXPIRED,
    )
    day = date(2026, 8, 20)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract, expired], [series]
        )
        store.upsert_futures_daily_bars("tushare-futures", [_bar(day)])

        futures = store.search_instruments(classification="futures")
        trading = store.search_instruments(
            classification="futures-contract",
            exchange="shfe",
            futures_product="cu",
            futures_lifecycle="trading",
        )
        main = store.search_instruments(
            classification="futures-continuous",
            futures_series_kind="main",
        )
        pinyin = store.search_instruments("htzl", classification="futures")

        assert {item["symbol"] for item in futures} == {
            product.symbol, contract.symbol, expired.symbol, series.symbol,
        }
        assert [item["symbol"] for item in trading] == [contract.symbol]
        assert trading[0]["product_code"] == "CU"
        assert trading[0]["lifecycle_status"] == "trading"
        assert trading[0]["contract_month"] == "202609"
        assert trading[0]["first_trade_date"] == day
        assert trading[0]["last_trade_date"] == day
        assert trading[0]["rows"] == 1
        assert [item["symbol"] for item in main] == [series.symbol]
        assert main[0]["series_kind"] == "main"
        assert main[0]["series_variant"] == "MAIN"
        assert main[0]["price_basis"] == "raw"
        assert main[0]["rule_version"] == "tushare-fut-mapping-v1"
        assert [item["symbol"] for item in pinyin] == [series.symbol]


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
        "INSERT INTO futures_schema_metadata VALUES (4, 'ready', 1)"
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
        assert [int(item[0]) for item in versions] == [3, 4]


