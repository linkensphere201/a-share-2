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


def test_provisional_futures_bar_survives_restart_and_later_canonical_takeover(
    tmp_path: Path,
) -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    observed = datetime(2026, 8, 20, 14, 30, tzinfo=CHINA_TIME)
    provisional = _bar(
        day, FuturesBarState.PROVISIONAL,
        "akshare-futures-zh-spot", 104, observed,
    )
    path = tmp_path / "futures-restart.sqlite"

    with SQLiteMarketDataStore(path) as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_provisional_daily_bars(
            provisional.source, [provisional], observed
        )

    with SQLiteMarketDataStore(path) as reopened:
        restored = reopened.list_fused_futures_daily_bars(
            contract.symbol, day, day
        )
        assert restored == [provisional]
        final = _bar(day, close=103)
        reopened.upsert_futures_daily_bars("tushare-futures", [final])
        assert reopened.list_fused_futures_daily_bars(
            contract.symbol, day, day
        ) == [final]
        audit = reopened.list_futures_provisional_audit(contract.symbol)
        assert audit[0]["takeover_state"] == "canonical-taken-over"


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


def test_futures_integrity_cli_fails_only_for_structural_errors() -> None:
    _exit_if_structural_errors(0)
    with pytest.raises(SystemExit) as raised:
        _exit_if_structural_errors(1)
    assert raised.value.code == 2


def test_futures_integrity_accepts_negative_tas_spread_prices() -> None:
    product, contract, _series = _catalog()
    tas_product = replace(
        product,
        symbol="FUTPROD:INE:SCTAS",
        product_code="SCTAS",
        exchange=FuturesExchange.INE,
    )
    tas_contract = replace(
        contract,
        symbol="FUT:INE:SCTAS:202510",
        provider_symbol="SCTAS2510.INE",
        product_symbol=tas_product.symbol,
        exchange=FuturesExchange.INE,
    )
    tas_bar = replace(
        _bar(date(2026, 8, 20)),
        symbol=tas_contract.symbol,
        open=0.1,
        high=0.1,
        low=-0.1,
        close=-0.1,
        previous_close=-0.1,
    )
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [tas_product], [tas_contract], []
        )
        store.upsert_futures_daily_bars("tushare-futures", [tas_bar])
        report = store.audit_futures_integrity()
    assert report["products"][0]["invalid_bar_rows"] == 0
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


