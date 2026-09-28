from dataclasses import replace
from datetime import date, timedelta

import pytest

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.low_accumulation_pattern import detect_low_accumulation, STRATEGY_ID, KIND
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.screener import ScreenerService
from stock_harness.sqlite_store import SQLiteMarketDataStore


def fixture():
    closes = [14 - i * 4 / 59 for i in range(60)]
    closes += [10.20 + i * .015 + (0, .09, .04)[i % 3] for i in range(20)]
    rows = []
    for i, close in enumerate(closes):
        day = date(2026, 1, 1) + timedelta(days=i)
        volume = 100 if i < 60 else (210 if close > closes[i-1] else 140)
        rows.append(AnalysisBar(period_start=day, period_end=day, open=close-.02,
            high=close+.10, low=close-.15, close=close, volume=volume,
            sources=("synthetic",), contains_provisional=False, period_complete=True, observed_at_ms=0))
    return rows


def test_five_dimensions_identify_a_low_absorption_platform():
    signal = detect_low_accumulation(fixture())
    assert signal and signal["kind"] == KIND
    assert signal["platform_sessions"] == 20
    assert signal["platform_volume_ratio"] > 1.15
    assert signal["volume_persistence"] >= .6
    assert signal["center_drift_percent"] >= .3
    assert signal["retained_advance_fraction"] >= .6
    assert signal["first_target_price"] is None


@pytest.mark.parametrize("failure", ["no-decline", "no-expansion", "single-spike", "breakdown",
    "falling-center", "sell-volume", "preview", "nan", "unsorted", "short"])
def test_rejects_counterevidence(failure):
    bars = fixture()
    if failure == "no-decline":
        bars[:60] = [replace(b, open=10.1, close=10.1, high=10.2, low=10) for b in bars[:60]]
    if failure in {"no-expansion", "single-spike"}:
        bars[60:] = [replace(b, volume=100) for b in bars[60:]]
        if failure == "single-spike": bars[65] = replace(bars[65], volume=3000)
    if failure == "breakdown": bars[-1] = replace(bars[-1], open=10, high=10, low=9, close=9.1, volume=1000)
    if failure == "falling-center":
        bars[60:] = [replace(b, open=10.7-i*.02, close=10.7-i*.02, high=10.8-i*.02, low=10.5-i*.02)
                     for i,b in enumerate(bars[60:])]
    if failure == "sell-volume":
        bars[60:] = [replace(b, volume=400 if b.close < bars[i+59].close else 140) for i,b in enumerate(bars[60:])]
    if failure == "preview": bars[-1] = replace(bars[-1], contains_provisional=True)
    if failure == "nan": bars[-1] = replace(bars[-1], volume=float("nan"))
    if failure == "unsorted": bars[-1], bars[-2] = bars[-2], bars[-1]
    if failure == "short": bars = bars[-30:]
    assert detect_low_accumulation(bars) is None


def test_screener_and_saved_chart_share_exact_evidence_without_future_data():
    prefix = fixture()
    expected = detect_low_accumulation(prefix)
    assert expected
    for future in (False, True):
        rows = list(prefix)
        if future:
            next_day = rows[-1].period_end + timedelta(days=1)
            rows.append(replace(rows[-1], period_start=next_day, period_end=next_day,
                                open=100, close=100, high=200, low=1, volume=10**9))
        with SQLiteMarketDataStore(":memory:") as store:
            store.upsert_instruments([Instrument("600001.SH", "Fixture", InstrumentKind.STOCK, "SH")])
            store.upsert_daily_bars("tushare", [DailyBar("600001.SH", b.period_end, b.open, b.high,
                                                        b.low, b.close, b.volume) for b in rows])
            store.upsert_trading_dates("tushare", [b.period_end for b in rows])
            run = ScreenerService(store).run_sync([], [], 10, prefix[-1].period_end, STRATEGY_ID)
            candidate, = store.list_screener_candidates(run["run_id"])
            saved = store.get_generated_analysis_run(candidate["analysis_run_id"])
            item = next(item for item in saved["items"] if item["item_id"] == candidate["line_item_id"])
            assert item["payload"] == candidate["evidence"] == expected
