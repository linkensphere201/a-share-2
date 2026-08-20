from dataclasses import replace
from datetime import date
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from stock_harness.models import (
    FuturesContinuousSeries,
    FuturesContract,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesPriceBasis,
    FuturesProduct,
    FuturesSeriesKind,
    Instrument,
    InstrumentKind,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


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
