from datetime import date, datetime, timezone

from stock_harness.analysis_inputs import (
    AnalysisHorizons,
    AnalysisInputMode,
    AnalysisInputService,
    AnalysisTimeframe,
)
from stock_harness.models import (
    AdjustmentFactor,
    DailyBar,
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
    ProvisionalDailyBar,
    StockTradeStatus,
    StoredDailyBar,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _stored(day: date, close: float, *, source: str = "tushare") -> StoredDailyBar:
    return StoredDailyBar(
        symbol="000001.SZ",
        trade_date=day,
        open=close - 0.2,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=int(close * 100),
        source=source,
        updated_at_ms=int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp() * 1000),
    )


def _provisional(day: date, close: float) -> ProvisionalDailyBar:
    observed = datetime(day.year, day.month, day.day, 6, 30, tzinfo=timezone.utc)
    return ProvisionalDailyBar(
        symbol="000001.SZ",
        trade_date=day,
        open=close - 0.2,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=int(close * 100),
        amount=1000,
        previous_close=close - 0.1,
        change_percent=1,
        source="eastmoney_intraday",
        provider_time=observed,
        received_at=observed,
    )


class FakeStore:
    def __init__(
        self,
        rows: list[StoredDailyBar],
        provisional: ProvisionalDailyBar | None = None,
        *,
        kind: InstrumentKind = InstrumentKind.STOCK,
        factors: list[AdjustmentFactor] | None = None,
        trading_dates: list[date] | None = None,
        statuses: list[StockTradeStatus] | None = None,
        lifecycle: tuple[date | None, date | None] = (None, None),
    ) -> None:
        self.rows = rows
        self.provisional = provisional
        self.kind = kind
        self.factors = factors or []
        self.trading_dates = trading_dates or []
        self.statuses = statuses or []
        self.lifecycle = lifecycle
        self.requests: list[tuple[str, date, int]] = []

    def get_recent_daily_bars(self, symbol, end_date, limit):
        self.requests.append((symbol, end_date, limit))
        return [row for row in self.rows if row.trade_date <= end_date][-limit:]

    def get_instrument_kind(self, symbol):
        return self.kind

    def get_instrument_lifecycle(self, symbol):
        return self.lifecycle

    def get_adjustment_factors(self, symbol, start_date, end_date):
        return [item for item in self.factors if start_date <= item.trade_date <= end_date]

    def get_stock_trade_statuses(self, symbol, start_date, end_date):
        return [item for item in self.statuses if start_date <= item.trade_date <= end_date]

    def list_trading_dates(self, source, start_date, end_date):
        return [item for item in self.trading_dates if start_date <= item <= end_date]

    def get_latest_provisional_daily_bar(self, symbol):
        return self.provisional


def test_historical_as_of_input_never_reads_future_rows():
    store = FakeStore([
        _stored(date(2026, 8, 3), 10),
        _stored(date(2026, 8, 4), 11),
        _stored(date(2026, 8, 5), 99),
    ])

    result = AnalysisInputService(store).build("000001.sz", date(2026, 8, 4))

    assert store.requests == [("000001.SZ", date(2026, 8, 4), 270)]
    assert [bar.period_end for bar in result.bars] == [date(2026, 8, 3), date(2026, 8, 4)]
    assert result.bars[-1].close == 11
    assert result.latest_final_date == date(2026, 8, 4)
    assert result.price_basis == "raw"
    assert result.missing_bar_policy == "preserve-gaps"


def test_preview_appends_only_a_newer_isolated_bar():
    provisional = _provisional(date(2026, 8, 5), 12)
    store = FakeStore([
        _stored(date(2026, 8, 3), 10),
        _stored(date(2026, 8, 4), 11),
    ], provisional)

    final = AnalysisInputService(store).build(
        "000001.SZ", date(2026, 8, 5), mode=AnalysisInputMode.FINAL
    )
    preview = AnalysisInputService(store).build(
        "000001.SZ", date(2026, 8, 5), mode=AnalysisInputMode.PREVIEW
    )

    assert len(final.bars) == 2
    assert len(preview.bars) == 3
    assert preview.bars[-1].contains_provisional is True
    assert preview.bars[-1].sources == ("eastmoney_intraday",)
    assert preview.provisional_date == date(2026, 8, 5)
    assert preview.provisional_provider_time == provisional.provider_time


def test_canonical_same_date_takes_precedence_over_provisional():
    store = FakeStore([
        _stored(date(2026, 8, 4), 11),
        _stored(date(2026, 8, 5), 12.5),
    ], _provisional(date(2026, 8, 5), 99))

    result = AnalysisInputService(store).build(
        "000001.SZ", date(2026, 8, 5), mode=AnalysisInputMode.PREVIEW
    )

    assert result.bars[-1].close == 12.5
    assert result.bars[-1].contains_provisional is False
    assert result.provisional_date is None


def test_weekly_and_monthly_aggregation_are_causal_and_keep_partial_period():
    store = FakeStore([
        _stored(date(2026, 7, 31), 10),
        _stored(date(2026, 8, 3), 11),
        _stored(date(2026, 8, 4), 12, source="baostock_repair"),
    ], _provisional(date(2026, 8, 5), 13))
    service = AnalysisInputService(store)

    weekly = service.build(
        "000001.SZ", date(2026, 8, 5), AnalysisTimeframe.WEEKLY,
        AnalysisInputMode.PREVIEW,
    )
    monthly = service.build(
        "000001.SZ", date(2026, 8, 5), AnalysisTimeframe.MONTHLY,
        AnalysisInputMode.PREVIEW,
    )

    assert [(bar.period_start, bar.period_end) for bar in weekly.bars] == [
        (date(2026, 7, 31), date(2026, 7, 31)),
        (date(2026, 8, 3), date(2026, 8, 5)),
    ]
    assert weekly.bars[-1].close == 13
    assert weekly.bars[-1].volume == 3600
    assert weekly.bars[-1].sources == (
        "tushare", "baostock_repair", "eastmoney_intraday",
    )
    assert weekly.bars[-1].contains_provisional is True
    assert [(bar.period_start, bar.period_end) for bar in monthly.bars] == [
        (date(2026, 7, 31), date(2026, 7, 31)),
        (date(2026, 8, 3), date(2026, 8, 5)),
    ]


def test_future_suffix_mutation_cannot_change_as_of_output():
    prefix = [_stored(date(2026, 8, 3), 10), _stored(date(2026, 8, 4), 11)]
    ordinary = AnalysisInputService(FakeStore([
        *prefix, _stored(date(2026, 8, 5), 12),
    ])).build("000001.SZ", date(2026, 8, 4), AnalysisTimeframe.WEEKLY)
    mutated = AnalysisInputService(FakeStore([
        *prefix, _stored(date(2026, 8, 5), 9999),
    ])).build("000001.SZ", date(2026, 8, 4), AnalysisTimeframe.WEEKLY)

    assert ordinary == mutated


def test_stock_prices_are_causally_forward_adjusted_without_changing_volume():
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
    store = FakeStore(
        [_stored(days[0], 5), _stored(days[1], 10), _stored(days[2], 11)],
        factors=[
            AdjustmentFactor("000001.SZ", days[0], 1),
            AdjustmentFactor("000001.SZ", days[1], 2),
            AdjustmentFactor("000001.SZ", days[2], 2),
        ],
    )

    result = AnalysisInputService(store).build("000001.SZ", days[-1])

    assert result.price_basis == "forward-adjusted-as-of"
    assert [bar.close for bar in result.bars] == [2.5, 10, 11]
    assert [bar.volume for bar in result.bars] == [500, 1000, 1100]
    assert not any(item.code == "adjustment_factors_incomplete" for item in result.warnings)


def test_future_adjustment_factor_cannot_change_historical_as_of_output():
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
    rows = [_stored(days[0], 10), _stored(days[1], 11), _stored(days[2], 12)]
    prefix = [
        AdjustmentFactor("000001.SZ", days[0], 1),
        AdjustmentFactor("000001.SZ", days[1], 1),
    ]
    ordinary = AnalysisInputService(FakeStore(
        rows, factors=[*prefix, AdjustmentFactor("000001.SZ", days[2], 2)]
    )).build("000001.SZ", days[1])
    mutated = AnalysisInputService(FakeStore(
        rows, factors=[*prefix, AdjustmentFactor("000001.SZ", days[2], 999)]
    )).build("000001.SZ", days[1])

    assert ordinary == mutated


def test_suspension_is_preserved_without_false_missing_bar_warning():
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
    factors = [
        AdjustmentFactor("000001.SZ", days[0], 1),
        AdjustmentFactor("000001.SZ", days[2], 1),
    ]
    store = FakeStore(
        [_stored(days[0], 10), _stored(days[2], 11)],
        factors=factors,
        trading_dates=days,
        statuses=[StockTradeStatus("000001.SZ", days[1], "suspended")],
    )

    result = AnalysisInputService(store).build("000001.SZ", days[-1])

    assert [bar.period_end for bar in result.bars] == [days[0], days[2]]
    assert not any(item.code == "unexplained_missing_bars" for item in result.warnings)


def test_partial_period_and_instrument_volume_capabilities_are_explicit():
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 10)]
    result = AnalysisInputService(FakeStore(
        [_stored(days[0], 10), _stored(days[1], 11)],
        kind=InstrumentKind.INDEX,
        trading_dates=days,
    )).build("000001.SZ", days[1], AnalysisTimeframe.WEEKLY)

    assert result.bars[-1].period_complete is True
    assert result.volume_semantics == "provider-defined-index-volume"
    assert any(item.code == "volume_not_cross_symbol_comparable" for item in result.warnings)


def test_horizons_limit_reads_and_report_available_ranges():
    rows = [_stored(date(2026, 1, day), float(day)) for day in range(1, 21)]
    store = FakeStore(rows, kind=InstrumentKind.ETF)
    horizons = AnalysisHorizons(short=3, medium=5, long=10)

    result = AnalysisInputService(store).build(
        "000001.SZ", date(2026, 1, 20), horizons=horizons
    )

    assert store.requests[-1][2] == 30
    assert [(item.name, item.available_bars) for item in result.horizon_ranges] == [
        ("short", 3), ("medium", 5), ("long", 10),
    ]


def test_sqlite_analysis_queries_are_bounded_and_preserve_store_contract():
    with SQLiteMarketDataStore(":memory:") as store:
        days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
        store.upsert_instruments([
            Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ")
        ])
        store.upsert_trading_dates("tushare", days)
        store.upsert_daily_bars("tushare", [
            DailyBar("000001.SZ", day, 10, 12, 9, 11, 100) for day in days
        ])
        store.upsert_adjustment_factors("tushare", [
            AdjustmentFactor("000001.SZ", day, 1) for day in days
        ])
        store.upsert_stock_trade_statuses("tushare", [
            StockTradeStatus("000001.SZ", day, "trading") for day in days
        ])

        result = AnalysisInputService(store).build("000001.SZ", days[-1])

        assert len(store.get_recent_daily_bars("000001.SZ", days[-1], 2)) == 2
        assert store.get_instrument_kind("000001.sz") is InstrumentKind.STOCK
        assert store.get_instrument_lifecycle("000001.sz") == (None, None)
        assert len(store.get_adjustment_factors("000001.SZ", days[0], days[-1])) == 3
        assert len(store.get_stock_trade_statuses("000001.SZ", days[0], days[-1])) == 3
        assert len(result.bars) == 3


def test_futures_input_preserves_units_settlement_oi_mapping_and_preview_isolation():
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
        FuturesPriceBasis.RAW, "mapping-v1",
    )
    prior, current = date(2026, 8, 19), date(2026, 8, 20)
    observed = datetime(2026, 8, 20, 6, 30, tzinfo=timezone.utc)
    final = FuturesDailyBar(
        series.symbol, prior, prior, 100, 103, 99, 102, 98, 101, 99,
        1234, 5_000_000, 4321, 12, None, "tushare-futures",
        FuturesBarState.FINAL, mapped_contract_symbol=contract.symbol, roll_event=True,
    )
    provisional = FuturesDailyBar(
        contract.symbol, current, current, 102, 105, 101, 104, 102, None, 101,
        456, None, 4400, None, None, "akshare-futures-zh-spot",
        FuturesBarState.PROVISIONAL, provider_time=observed,
    )
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [contract], [series]
        )
        store.upsert_futures_roll_mappings("tushare-futures", [FuturesRollMapping(
            series.symbol, series.provider_symbol, current,
            contract.symbol, contract.provider_symbol,
        )])
        store.upsert_futures_calendar("tushare-futures", [
            FuturesCalendarDay(FuturesExchange.SHFE, day, True, None)
            for day in (prior, current)
        ])
        store.upsert_futures_daily_bars("tushare-futures", [final])
        store.upsert_futures_provisional_daily_bars(
            provisional.source, [provisional], observed
        )

        service = AnalysisInputService(store)
        official = service.build(series.symbol, current, mode=AnalysisInputMode.FINAL)
        preview = service.build(series.symbol, current, mode=AnalysisInputMode.PREVIEW)

        assert [bar.period_end for bar in official.bars] == [prior]
        assert [bar.period_end for bar in preview.bars] == [prior, current]
        assert official.instrument.product_code == "CU"
        assert official.instrument.change_basis == "previous-settlement"
        assert official.instrument.active is True
        assert official.instrument.lifecycle_status is None
        assert official.instrument.per_unit == 5
        assert official.instrument.trading_unit == "contract"
        assert official.instrument.quote_unit == "CNY/tonne"
        assert official.instrument.rule_version == "mapping-v1"
        assert official.volume_semantics == "contracts"
        assert official.bars[0].settlement == 101
        assert official.bars[0].open_interest == 4321
        assert official.bars[0].open_interest_change == 12
        assert official.bars[0].mapped_contracts == (contract.symbol,)
        assert official.bars[0].contains_roll_event is True
        assert preview.bars[-1].mapped_contracts == (contract.symbol,)
        assert preview.provisional_date == current
        assert preview.provisional_provider_time == observed
