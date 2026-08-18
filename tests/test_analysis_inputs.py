from datetime import date, datetime, timezone

from stock_harness.analysis_inputs import (
    AnalysisInputMode,
    AnalysisInputService,
    AnalysisTimeframe,
)
from stock_harness.models import ProvisionalDailyBar, StoredDailyBar


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
    ) -> None:
        self.rows = rows
        self.provisional = provisional
        self.requests: list[tuple[str, date | None, date | None]] = []

    def get_daily_bars(self, symbol, start_date=None, end_date=None):
        self.requests.append((symbol, start_date, end_date))
        return [
            row for row in self.rows
            if (start_date is None or row.trade_date >= start_date)
            and (end_date is None or row.trade_date <= end_date)
        ]

    def get_latest_provisional_daily_bar(self, symbol):
        return self.provisional


def test_historical_as_of_input_never_reads_future_rows():
    store = FakeStore([
        _stored(date(2026, 8, 3), 10),
        _stored(date(2026, 8, 4), 11),
        _stored(date(2026, 8, 5), 99),
    ])

    result = AnalysisInputService(store).build("000001.sz", date(2026, 8, 4))

    assert store.requests == [("000001.SZ", None, date(2026, 8, 4))]
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
