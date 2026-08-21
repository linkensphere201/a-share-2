from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from stock_harness.api import create_app
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
            "ready": True, "schema_version": 2, "error": None,
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
        "INSERT INTO futures_schema_metadata VALUES (3, 'ready', 1)"
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
        assert [int(item[0]) for item in versions] == [2, 3]


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


def test_canonical_daily_takes_over_without_deleting_provisional_audit(caplog) -> None:
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
        with caplog.at_level("INFO"):
            store.upsert_futures_daily_bars("tushare-futures", [final])
        fused = store.list_fused_futures_daily_bars(
            contract.symbol, prior_day, current_day
        )
        audit = store.list_futures_provisional_audit(contract.symbol)
        assert fused == [_bar(prior_day), final]
        assert audit[0]["takeover_state"] == "canonical-taken-over"
        assert audit[0]["close"] == 104
        assert "futures_canonical_takeover_completed rows=1" in caplog.text


def test_futures_integrity_audit_separates_backlog_from_structural_errors() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_calendar("tushare-futures", [
            FuturesCalendarDay(FuturesExchange.SHFE, day, True, date(2026, 8, 19))
        ])
        empty = store.audit_futures_integrity()
        assert empty["summary"]["contracts_with_rows"] == 0
        assert empty["products"][0]["contracts_without_rows"] == 1
        assert empty["summary"]["structural_errors"] == 0

        store.upsert_futures_daily_bars("tushare-futures", [_bar(day)])
        store.checkpoint_futures_sync(
            "tushare-futures", "daily", FuturesExchange.SHFE.value,
            contract.symbol, day, day, 1,
        )
        store.upsert_futures_roll_mappings("tushare-futures", [
            FuturesRollMapping(
                series.symbol, series.provider_symbol, day,
                contract.symbol, contract.provider_symbol,
            )
        ])
        report = store.audit_futures_integrity()
    assert report["summary"]["contracts_with_rows"] == 1
    assert report["summary"]["daily_rows"] == 1
    assert report["products"][0]["missing_covered_open_days"] == 0
    assert report["summary"]["missing_covered_open_days"] == 0
    assert report["products"][0]["invalid_bar_rows"] == 0
    assert report["products"][0]["unit_mismatch_contracts"] == 0
    assert report["continuous"]["series_with_mappings"] == 1
    assert report["continuous"]["series_without_mappings"] == 0
    assert report["summary"]["structural_errors"] == 0


def test_continuous_fused_read_projects_the_mapped_contract_provisional_bar() -> None:
    product, contract, series = _catalog()
    prior_day = date(2026, 8, 19)
    current_day = date(2026, 8, 20)
    observed = datetime(2026, 8, 20, 14, 30, tzinfo=CHINA_TIME)
    mapping = FuturesRollMapping(
        series.symbol, series.provider_symbol, current_day,
        contract.symbol, contract.provider_symbol,
    )
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_roll_mappings("tushare-futures", [mapping])
        store.upsert_futures_daily_bars("tushare-futures", [
            replace(
                _bar(prior_day), symbol=series.symbol,
                mapped_contract_symbol=contract.symbol,
            )
        ])
        store.upsert_futures_provisional_daily_bars(
            "akshare-futures-zh-spot",
            [_bar(
                current_day, FuturesBarState.PROVISIONAL,
                "akshare-futures-zh-spot", 104, observed,
            )],
            observed,
        )

        fused = store.list_fused_futures_daily_bars(
            series.symbol, prior_day, current_day
        )

        assert [item.state for item in fused] == [
            FuturesBarState.FINAL, FuturesBarState.PROVISIONAL,
        ]
        assert fused[-1].symbol == series.symbol
        assert fused[-1].mapped_contract_symbol == contract.symbol
        assert fused[-1].close == 104

        store.upsert_futures_daily_bars("tushare-futures", [
            replace(
                _bar(current_day, close=103), symbol=series.symbol,
                mapped_contract_symbol=contract.symbol,
            )
        ])
        assert [item.state for item in store.list_fused_futures_daily_bars(
            series.symbol, prior_day, current_day
        )] == [FuturesBarState.FINAL, FuturesBarState.FINAL]


def test_market_snapshots_fuse_futures_provisional_and_canonical_metrics() -> None:
    product, contract, series = _catalog()
    prior_day = date(2026, 8, 19)
    current_day = date(2026, 8, 20)
    observed = datetime(2026, 8, 20, 14, 30, tzinfo=CHINA_TIME)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_daily_bars("tushare-futures", [_bar(prior_day)])
        store.upsert_futures_provisional_daily_bars(
            "akshare-futures-zh-spot",
            [_bar(
                current_day, FuturesBarState.PROVISIONAL,
                "akshare-futures-zh-spot", 104, observed,
            )],
            observed,
        )

        provisional = store.list_market_snapshots([contract.symbol])[0]
        assert provisional["trade_date"] == current_day
        assert provisional["source_state"] == "provisional"
        assert provisional["settlement_change_percent"] == pytest.approx(5.050505)
        assert provisional["open_interest"] == 4321
        assert provisional["open_interest_change"] is None
        assert provisional["contract_month"] == "202609"
        assert provisional["last_trading_date"] == date(2026, 9, 15)

        store.upsert_futures_daily_bars(
            "tushare-futures", [_bar(current_day, close=103)]
        )
        final = store.list_market_snapshots([contract.symbol])[0]
        assert final["source_state"] == "final"
        assert final["close"] == 103
        assert final["amount"] == 5_000_000
        assert final["open_interest_change"] == 12

        store.upsert_futures_daily_bars("tushare-futures", [replace(
            _bar(current_day, close=105),
            symbol=series.symbol,
            mapped_contract_symbol=contract.symbol,
        )])
        continuous = store.list_market_snapshots([series.symbol])[0]
        assert continuous["symbol"] == series.symbol
        assert continuous["contract_month"] == "202609"
        assert continuous["last_trading_date"] == date(2026, 9, 15)


def test_daily_bars_api_serves_exact_futures_ohlcv_and_evidence() -> None:
    product, contract, series = _catalog()
    prior_day = date(2026, 8, 19)
    current_day = date(2026, 8, 20)
    observed = datetime(2026, 8, 20, 14, 30, tzinfo=CHINA_TIME)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_roll_mappings("tushare-futures", [FuturesRollMapping(
            series.symbol, series.provider_symbol, current_day,
            contract.symbol, contract.provider_symbol,
        )])
        store.upsert_futures_daily_bars("tushare-futures", [
            _bar(prior_day),
            replace(
                _bar(prior_day, close=105),
                symbol=series.symbol,
                mapped_contract_symbol=contract.symbol,
            ),
        ])
        store.upsert_futures_provisional_daily_bars(
            "akshare-futures-zh-spot",
            [_bar(
                current_day, FuturesBarState.PROVISIONAL,
                "akshare-futures-zh-spot", 104, observed,
            )],
            observed,
        )
        with TestClient(create_app(store)) as client:
            real = client.get(f"/api/instruments/{contract.symbol}/daily-bars")
            continuous = client.get(f"/api/instruments/{series.symbol}/daily-bars")
            detail = client.get(f"/api/instruments/{series.symbol}")
            published = client.post("/api/workspace-context", json={
                "schema_version": "1.0",
                "published_at": "2026-08-20T14:30:00+08:00",
                "active_group_id": "futures-group",
                "active_group_name": "Futures",
                "focused_window_id": "chart-futures",
                "referenced_symbols": [series.symbol],
                "windows": [{
                    "id": "chart-futures", "type": "chart", "title": "Copper",
                    "mode": "detached", "focused": True, "maximized": False,
                    "instrument": {
                        "symbol": series.symbol, "name": series.display_name,
                        "kind": "futures-continuous", "exchange": "SHFE",
                        "price_basis": "raw", "rule_version": series.rule_version,
                    },
                    "chart": {
                        "range": "3Y", "coordinate_mode": "normal",
                        "visible_start": None, "visible_end": None,
                        "volume_visible": True, "indicator": "macd",
                    },
                }],
                "attachments": [], "drawings_by_symbol": {},
            })
            workspace = client.get("/api/workspace-context")

        assert real.status_code == continuous.status_code == detail.status_code == 200
        real_items = real.json()["items"]
        assert [item["bar_state"] for item in real_items] == ["final", "intraday"]
        assert real_items[0]["settlement"] == 101
        assert real_items[0]["open_interest_change"] == 12
        assert real_items[1]["provider_time"] == observed.isoformat()
        assert real_items[1]["stale"] is False
        continuous_item = continuous.json()["items"][0]
        assert continuous.json()["symbol"] == series.symbol
        assert continuous_item["mapped_contract_symbol"] == contract.symbol
        assert continuous_item["volume"] == 1234
        assert published.status_code == 202
        workspace_chart = workspace.json()["windows"][0]
        assert workspace_chart["instrument"]["symbol"] == series.symbol
        assert workspace_chart["instrument"]["price_basis"] == "raw"
        latest = workspace_chart["latest_data_state"]
        assert latest["effective"]["bar_state"] == "provisional"
        assert latest["effective"]["mapped_contract_symbol"] == contract.symbol
        assert latest["effective"]["provider_time"] == observed.isoformat()
        assert latest["effective"]["stale"] is False
        assert latest["latest_final"]["trade_date"] == prior_day.isoformat()


def test_custom_group_preserves_ordered_mixed_futures_members_and_tags() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    stock = Instrument("300308.SZ", "Innolight", InstrumentKind.STOCK, "SZ")
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([stock])
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_daily_bars("tushare-futures", [replace(
            _bar(day), symbol=series.symbol,
            mapped_contract_symbol=contract.symbol,
        )])
        created = store.create_custom_group("mixed", "Mixed", "", [
            {"symbol": stock.symbol, "tags": ["equity"], "note": "stock"},
            {"symbol": series.symbol, "tags": ["main", "copper"], "note": "future"},
        ])

        assert [item["symbol"] for item in created["members"]] == [
            stock.symbol, series.symbol,
        ]
        assert created["members"][1]["tags"] == ["main", "copper"]
        assert store.list_custom_groups()[0]["average_change_percent"] == pytest.approx(
            3.030303
        )
        with TestClient(create_app(store)) as client:
            members = client.get("/api/instruments/CUSTOM:mixed/members").json()["items"]
        assert members[1]["symbol"] == series.symbol
        assert members[1]["settlement_change_percent"] == pytest.approx(3.030303)
        assert members[1]["source_state"] == "final"
        assert members[1]["contract_month"] == "202609"


def test_custom_group_rejects_futures_product_catalog_nodes() -> None:
    product, contract, series = _catalog()
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        with pytest.raises(ValueError, match="catalog nodes"):
            store.create_custom_group("invalid", "Invalid", "", [
                {"symbol": product.symbol, "tags": [], "note": ""},
            ])


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


def test_large_mapping_window_is_written_in_atomic_bounded_batches() -> None:
    product, contract, series = _catalog()
    first_day = date(2018, 1, 1)
    mappings = [
        FuturesRollMapping(
            series.symbol, series.provider_symbol, first_day + timedelta(days=offset),
            contract.symbol, contract.provider_symbol,
        )
        for offset in range(2_001)
    ]
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        state, receipt = store.replace_futures_roll_mapping_window(
            "tushare-futures", "SHFE", series.symbol,
            first_day, first_day + timedelta(days=2_000), mappings,
        )
        stored = store.list_futures_roll_mappings(
            series.symbol, first_day, first_day + timedelta(days=2_000)
        )
    assert len(stored) == 2_001
    assert state.last_batch_rows == 2_001
    assert receipt.row_count == 2_001


def test_futures_coverage_reports_field_completeness() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_daily_bars(
            "tushare-futures",
            [
                replace(_bar(day), settlement=None, open_interest_contracts=None),
                replace(
                    _bar(day), symbol=series.symbol,
                    mapped_contract_symbol=contract.symbol, roll_event=True,
                ),
            ],
        )
        coverage = store.list_futures_coverage(kind=None)
        contract_coverage = next(
            item for item in coverage if item["symbol"] == contract.symbol
        )
        continuous_coverage = next(
            item for item in coverage if item["symbol"] == series.symbol
        )
        assert contract_coverage["rows"] == 1
        assert contract_coverage["missing_settlement_rows"] == 1
        assert contract_coverage["missing_open_interest_rows"] == 1
        assert continuous_coverage["kind"] == "futures-continuous"
        assert continuous_coverage["price_basis"] == "raw"
        assert continuous_coverage["mapped_rows"] == 1
        assert continuous_coverage["roll_event_rows"] == 1

        with TestClient(create_app(store)) as client:
            response = client.get("/api/futures/coverage", params={"limit": 1})
            filtered = client.get(
                "/api/futures/coverage",
                params={"kind": "futures-continuous", "limit": 10},
            )
        assert response.status_code == 200
        assert response.json()["has_more"] is True
        assert response.json()["next_offset"] == 1
        assert filtered.status_code == 200
        assert [item["symbol"] for item in filtered.json()["items"]] == [series.symbol]


def test_continuous_build_persists_roll_evidence_and_rebuilds_dirty_suffix() -> None:
    product, first_contract, series = _catalog()
    second_contract = replace(
        first_contract,
        symbol="FUT:SHFE:CU:202610", provider_symbol="CU2610.SHF",
        contract_month="202610", display_name="Copper 2610",
        last_trading_date=date(2026, 10, 15), delivery_date=date(2026, 10, 20),
    )
    first_day, roll_day = date(2026, 8, 19), date(2026, 8, 20)
    mappings = [
        FuturesRollMapping(
            series.symbol, series.provider_symbol, first_day,
            first_contract.symbol, first_contract.provider_symbol,
        ),
        FuturesRollMapping(
            series.symbol, series.provider_symbol, roll_day,
            second_contract.symbol, second_contract.provider_symbol,
        ),
    ]
    first_bar = _bar(first_day, close=101)
    outgoing_overlap = _bar(roll_day, close=103)
    incoming_bar = replace(_bar(roll_day, close=108), symbol=second_contract.symbol)

    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [first_contract, second_contract], [series]
        )
        store.upsert_futures_roll_mappings("tushare-futures", mappings)
        store.upsert_futures_daily_bars(
            "tushare-futures", [first_bar, outgoing_overlap, incoming_bar]
        )
        assert store.get_futures_continuous_dirty_state(series.symbol)["dirty_from"] == first_day

        full = materialize_raw_continuous(
            series, mappings, [first_bar, outgoing_overlap, incoming_bar]
        )
        result = store.persist_futures_continuous_build("tushare-futures", full)
        assert result["rows"] == 2
        assert store.get_futures_continuous_dirty_state(series.symbol) is None
        stored = store.list_futures_daily_bars(series.symbol, first_day, roll_day)
        assert [item.mapped_contract_symbol for item in stored] == [
            first_contract.symbol, second_contract.symbol,
        ]
        assert stored[-1].roll_event is True
        rolls = store.list_futures_continuous_roll_events(series.symbol)
        assert rolls[0]["effective_from"] == roll_day
        assert rolls[0]["incoming_contract_symbol"] == second_contract.symbol
        assert store.get_futures_continuous_build_status(series.symbol)["status"] == "complete"

        corrected = replace(_bar(roll_day, close=110), symbol=second_contract.symbol)
        store.upsert_futures_daily_bars("tushare-futures", [corrected])
        assert store.get_futures_continuous_dirty_state(series.symbol)["dirty_from"] == roll_day
        suffix = materialize_raw_continuous(
            series, mappings, [first_bar, outgoing_overlap, corrected],
            start_date=roll_day,
        )
        assert suffix.bars[0].roll_event is True
        store.persist_futures_continuous_build(
            "tushare-futures", suffix, rebuilt_from=roll_day
        )
        assert store.list_futures_daily_bars(
            series.symbol, first_day, roll_day
        )[-1].close == 110
        assert store.get_futures_continuous_dirty_state(series.symbol) is None
        with TestClient(create_app(store)) as client:
            detail = client.get(
                f"/api/futures/continuous/{series.symbol}",
                params={
                    "start_date": first_day.isoformat(),
                    "end_date": roll_day.isoformat(),
                },
            )
        payload = detail.json()
        assert detail.status_code == 200
        assert payload["instrument"]["symbol"] == series.symbol
        assert payload["build"]["input_digest"] == suffix.input_digest.hex()
        assert [item["contract_symbol"] for item in payload["mappings"]] == [
            first_contract.symbol, second_contract.symbol,
        ]
        assert payload["rolls"][0]["input_digest"] == suffix.input_digest.hex()
