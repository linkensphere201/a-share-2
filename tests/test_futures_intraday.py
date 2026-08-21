from datetime import date, datetime, time, timedelta, timezone

from stock_harness.config import (
    FuturesProductSessionRule,
    FuturesProvisionalProviderSettings,
    FuturesSessionWindow,
)
from stock_harness.futures_intraday import (
    FuturesProvisionalService,
    futures_contract_session_trading_day,
    futures_session_trading_day,
)
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
    Instrument,
    InstrumentKind,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


CHINA_TIME = timezone(timedelta(hours=8))


def _catalog():
    product = FuturesProduct(
        "FUTPROD:SHFE:CU", "CU", "Copper", FuturesExchange.SHFE,
        None, 5, "contract", "CNY/tonne",
    )
    contract = FuturesContract(
        "FUT:SHFE:CU:202609", "CU2609.SHF", product.symbol, "Copper 2609",
        FuturesExchange.SHFE, "202609", date(2025, 9, 16), date(2026, 9, 15),
        date(2026, 9, 18), None, 5, "contract", "CNY/tonne",
        FuturesLifecycleStatus.TRADING,
    )
    series = FuturesContinuousSeries(
        "FUTCONT:SHFE:CU:MAIN:raw", "CU.SHF", product.symbol, "Copper main",
        FuturesExchange.SHFE, FuturesSeriesKind.MAIN, "MAIN",
        FuturesPriceBasis.RAW, "tushare-fut-mapping-v1",
    )
    return product, contract, series


def _settings() -> FuturesProvisionalProviderSettings:
    return FuturesProvisionalProviderSettings(
        True, "akshare", 8, 1, 1, 30, 90, 10, None,
        (
            FuturesProductSessionRule(
                FuturesExchange.SHFE,
                "*",
                (
                    FuturesSessionWindow(time(9), time(10, 15)),
                    FuturesSessionWindow(time(10, 30), time(11, 30)),
                    FuturesSessionWindow(time(13, 30), time(15)),
                ),
                (),
            ),
            FuturesProductSessionRule(
                FuturesExchange.SHFE,
                "CU",
                (),
                (FuturesSessionWindow(time(21), time(1)),),
            ),
        ),
    )


class _Provider:
    code = "akshare-futures-spot"


class _Monitor:
    def __init__(self) -> None:
        self.provider = _Provider()
        self.requests = []
        self.fail = False
        self.stale_by = timedelta(0)

    def fetch(self, contracts, expected_trading_day, observed_at=None):
        self.requests.append((tuple(item.symbol for item in contracts), expected_trading_day))
        if self.fail:
            raise RuntimeError("spot unavailable")
        contract = contracts[0]
        return (FuturesDailyBar(
            contract.symbol, expected_trading_day, expected_trading_day,
            100, 103, 99, 102, 98, None, 99, 1234, None, 4321, None,
            None, self.provider.code, FuturesBarState.PROVISIONAL,
            observed_at - self.stale_by,
        ),)


class _MainReporter:
    def __init__(self, provider_code: str) -> None:
        self.provider_code = provider_code
        self.requests = []
        self.fail = False

    def report(self, contracts):
        self.requests.append(tuple(item.symbol for item in contracts))
        if self.fail:
            raise RuntimeError("main-contract endpoint unavailable")
        return {item.product_symbol: self.provider_code for item in contracts}


def test_active_workspace_references_resolve_to_bounded_real_contracts() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    monitor = _Monitor()
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_instruments([
            Instrument("600519.SH", "Stock", InstrumentKind.STOCK, "SH")
        ])
        store.upsert_futures_calendar("tushare-futures", [
            FuturesCalendarDay(FuturesExchange.SHFE, day, True, date(2026, 8, 19))
        ])
        store.upsert_futures_roll_mappings("tushare-futures", [
            FuturesRollMapping(
                series.symbol, series.provider_symbol, day,
                contract.symbol, contract.provider_symbol,
            )
        ])
        service = FuturesProvisionalService(_settings(), store, monitor)
        service.subscribe("group-1", [series.symbol, "600519.SH"])
        result = service.refresh_once(datetime(2026, 8, 20, 10, 0, tzinfo=CHINA_TIME))
        assert result["state"] == "ready"
        assert result["received_count"] == 1
        assert result["unresolved_count"] == 1
        assert monitor.requests == [((contract.symbol,), day)]
        fused = store.list_fused_futures_daily_bars(contract.symbol, day, day)
        assert len(fused) == 1
        assert fused[0].state is FuturesBarState.PROVISIONAL

        monitor.fail = True
        failed = service.refresh_once(
            datetime(2026, 8, 20, 10, 1, tzinfo=CHINA_TIME)
        )
        assert failed["state"] == "error"
        assert failed["last_error"] == "RuntimeError"
        assert "spot unavailable" not in str(failed)
        audit = store.list_futures_provisional_audit(contract.symbol)
        assert audit[-1]["stale"] is True
        assert len(store.list_fused_futures_daily_bars(contract.symbol, day, day)) == 1

        closed = service.refresh_once(datetime(2026, 8, 20, 18, 0, tzinfo=CHINA_TIME))
        assert closed["skip_reason"] == "market-closed"
        assert len(monitor.requests) == 2


def test_stale_futures_quote_is_persisted_and_reported_without_dropping_the_bar() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    monitor = _Monitor()
    monitor.stale_by = timedelta(minutes=5)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_calendar("tushare-futures", [
            FuturesCalendarDay(FuturesExchange.SHFE, day, True, date(2026, 8, 19))
        ])
        service = FuturesProvisionalService(_settings(), store, monitor)

        result = service.refresh_once(
            datetime(2026, 8, 20, 10, 0, tzinfo=CHINA_TIME),
            references=[contract.symbol],
        )

        assert result["state"] == "stale"
        assert result["received_count"] == 1
        assert result["stale_count"] == 1
        assert store.list_futures_provisional_audit(contract.symbol)[0]["stale"] is True
        assert len(store.list_fused_futures_daily_bars(contract.symbol, day, day)) == 1


def test_futures_sessions_assign_night_observations_to_next_open_day() -> None:
    friday = date(2026, 8, 21)
    monday = date(2026, 8, 24)
    next_open = lambda value: monday if value >= friday else friday
    observed = datetime(2026, 8, 21, 21, 30, tzinfo=CHINA_TIME)
    assert futures_session_trading_day(
        FuturesExchange.SHFE, observed, next_open
    ) == monday
    assert futures_session_trading_day(
        FuturesExchange.CFFEX, observed, next_open
    ) is None


def test_product_session_uses_wildcard_day_and_exact_cross_midnight_night() -> None:
    _, contract, _ = _catalog()
    settings = _settings()
    friday = date(2026, 8, 21)
    monday = date(2026, 8, 24)
    next_open = lambda value: monday if value >= friday else friday

    assert futures_contract_session_trading_day(
        contract,
        datetime(2026, 8, 21, 10, 0, tzinfo=CHINA_TIME),
        lambda value: value,
        settings.session_rules,
    ) == friday
    assert futures_contract_session_trading_day(
        contract,
        datetime(2026, 8, 21, 0, 30, tzinfo=CHINA_TIME),
        next_open,
        settings.session_rules,
    ) == monday
    assert futures_contract_session_trading_day(
        contract,
        datetime(2026, 8, 21, 1, 30, tzinfo=CHINA_TIME),
        next_open,
        settings.session_rules,
    ) is None


def test_product_without_configured_night_session_is_closed() -> None:
    _, contract, _ = _catalog()
    rules = (_settings().session_rules[0],)
    assert futures_contract_session_trading_day(
        contract,
        datetime(2026, 8, 21, 21, 30, tzinfo=CHINA_TIME),
        lambda value: value,
        rules,
    ) is None


def test_night_session_does_not_bridge_a_long_exchange_holiday() -> None:
    _, contract, _ = _catalog()
    assert futures_contract_session_trading_day(
        contract,
        datetime(2026, 10, 1, 21, 30, tzinfo=CHINA_TIME),
        lambda _value: date(2026, 10, 9),
        _settings().session_rules,
    ) is None


def test_continuous_main_mapping_conflict_is_unavailable_without_silent_switch() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    monitor = _Monitor()
    reporter = _MainReporter("CU2610")
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_calendar("tushare-futures", [
            FuturesCalendarDay(FuturesExchange.SHFE, day, True, date(2026, 8, 19))
        ])
        store.upsert_futures_roll_mappings("tushare-futures", [
            FuturesRollMapping(
                series.symbol, series.provider_symbol, day,
                contract.symbol, contract.provider_symbol,
            )
        ])
        service = FuturesProvisionalService(
            _settings(), store, monitor, main_contract_reporter=reporter
        )
        service.subscribe("group-1", [series.symbol])
        result = service.refresh_once(
            datetime(2026, 8, 20, 10, 0, tzinfo=CHINA_TIME)
        )

        assert result["state"] == "skipped"
        assert result["skip_reason"] == "mapping-ambiguous"
        assert result["ambiguous_count"] == 1
        assert result["ambiguous"][series.symbol] == {
            "mapped_contract": contract.symbol,
            "mapped_provider_contract": "CU2609",
            "reported_provider_contract": "CU2610",
        }
        assert monitor.requests == []
        assert store.list_fused_futures_daily_bars(contract.symbol, day, day) == []


def test_mapping_conflict_does_not_block_explicit_real_contract_reference() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    monitor = _Monitor()
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_calendar("tushare-futures", [
            FuturesCalendarDay(FuturesExchange.SHFE, day, True, date(2026, 8, 19))
        ])
        store.upsert_futures_roll_mappings("tushare-futures", [
            FuturesRollMapping(
                series.symbol, series.provider_symbol, day,
                contract.symbol, contract.provider_symbol,
            )
        ])
        service = FuturesProvisionalService(
            _settings(), store, monitor,
            main_contract_reporter=_MainReporter("CU2610"),
        )
        service.subscribe("group-1", [series.symbol, contract.symbol])
        result = service.refresh_once(
            datetime(2026, 8, 20, 10, 0, tzinfo=CHINA_TIME)
        )

        assert result["state"] == "ready"
        assert result["resolved"][series.symbol] is None
        assert result["resolved"][contract.symbol] == contract.symbol
        assert monitor.requests == [((contract.symbol,), day)]


def test_known_mapping_conflict_remains_blocked_during_evidence_outage() -> None:
    product, contract, series = _catalog()
    day = date(2026, 8, 20)
    reporter = _MainReporter("CU2610")
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_calendar("tushare-futures", [
            FuturesCalendarDay(FuturesExchange.SHFE, day, True, date(2026, 8, 19))
        ])
        store.upsert_futures_roll_mappings("tushare-futures", [
            FuturesRollMapping(
                series.symbol, series.provider_symbol, day,
                contract.symbol, contract.provider_symbol,
            )
        ])
        service = FuturesProvisionalService(
            _settings(), store, _Monitor(), main_contract_reporter=reporter
        )
        service.subscribe("group-1", [series.symbol])
        first = service.refresh_once(
            datetime(2026, 8, 20, 10, 0, tzinfo=CHINA_TIME)
        )
        reporter.fail = True
        second = service.refresh_once(
            datetime(2026, 8, 20, 10, 1, tzinfo=CHINA_TIME)
        )

        assert first["skip_reason"] == "mapping-ambiguous"
        assert second["skip_reason"] == "mapping-ambiguous"
        assert second["ambiguous"] == first["ambiguous"]
