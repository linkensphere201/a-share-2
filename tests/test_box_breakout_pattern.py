from dataclasses import replace
from datetime import timedelta

import pytest

from stock_harness.analysis_inputs import AnalysisInputService
from stock_harness.box_breakout_pattern import detect_box_breakout, STRATEGY_ID, KIND, PLATFORM_CONFIG
from stock_harness.long_platform_pattern import detect_long_platform
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.screener import ScreenerService
from stock_harness.sqlite_store import SQLiteMarketDataStore
from test_long_platform_pattern import sample


def append(bars, *, open=10.6, high=11.1, low=10.5, close=11., volume=240):
    day = bars[-1].period_end + timedelta(days=1)
    return (*bars, replace(bars[-1], period_start=day, period_end=day,
                          open=open, high=high, low=low, close=close, volume=volume))


def fixture():
    return append(sample())


def test_breakout_reuses_pre_break_platform_boundaries():
    bars = fixture()
    base = detect_long_platform(bars[:-1], config=PLATFORM_CONFIG)
    result = detect_box_breakout(bars)
    assert result and result['stage'] == 'broken-out'
    assert result['upper'] == base['upper']
    assert result['lower'] == base['lower']
    assert result['platform_end_date'] == bars[-2].period_end.isoformat()
    assert result['end_date'] == result['platform_end_date'] < result['as_of_date']
    assert result['launch_date'] == bars[-1].period_end.isoformat()
    assert result['upper_touch_count'] >= 2
    assert detect_box_breakout(bars[:-1]) is None


@pytest.mark.parametrize('length', [20, 40, 60, 120, 250])
def test_boxes_at_multiple_durations(length):
    result = detect_box_breakout(append(sample(length)))
    assert result and 20 <= result['platform_sessions'] <= 250


def test_old_breakout_without_current_retest_is_not_a_fresh_signal():
    bars = fixture()
    for _ in range(10):
        bars = append(bars, open=11., high=11.1, low=10.99, close=11.02, volume=100)
    assert detect_box_breakout(bars) is None


def test_retest_is_separate_and_keeps_original_box():
    bars = append(fixture(), open=10.8, high=10.85, low=10.58, close=10.7, volume=100)
    result = detect_box_breakout(bars)
    assert result and result['stage'] == 'breakout-retest'
    assert result['upper'] == detect_box_breakout(bars[:-1])['upper']


@pytest.mark.parametrize('failure', ['weak-volume', 'wick-only', 'failed-break', 'extended', 'provisional', 'unordered'])
def test_counterevidence_rejected(failure):
    bars = fixture()
    if failure == 'weak-volume':
        bars = (*bars[:-1], replace(bars[-1], volume=50))
    elif failure == 'wick-only':
        bars = (*bars[:-1], replace(bars[-1], open=10.4, close=10.5))
    elif failure == 'failed-break':
        bars = append(bars, open=10.1, high=10.2, low=9.8, close=10., volume=100)
    elif failure == 'extended':
        bars = append(bars, open=12., high=13., low=11.9, close=12.8, volume=100)
    elif failure == 'provisional':
        bars = (*bars[:-1], replace(bars[-1], contains_provisional=True))
    else:
        bars = (*bars, bars[-2])
    assert detect_box_breakout(bars) is None


def test_saved_box_and_screener_are_causal_with_future_poison():
    bars = fixture()
    cutoff = bars[-1].period_end
    with SQLiteMarketDataStore(':memory:') as store:
        store.upsert_instruments([Instrument('600001.SH', 'Test', InstrumentKind.STOCK, 'SH')])
        future = append(bars, open=1, high=1.1, low=.9, close=1, volume=100000)
        store.upsert_daily_bars('tushare', [DailyBar('600001.SH', b.period_end, b.open, b.high, b.low, b.close, int(b.volume)) for b in future])
        store.upsert_trading_dates('tushare', [b.period_end for b in future])
        service = ScreenerService(store)
        try:
            run = service.run_sync([], [], 200, cutoff, STRATEGY_ID)
            candidates = store.list_screener_candidates(run['run_id'])
            assert len(candidates) == 1
            saved = store.get_generated_analysis_run(candidates[0]['analysis_run_id'])
            item = next(i for i in saved['items'] if i['payload'].get('kind') == KIND)
            assert item['payload'] == candidates[0]['evidence']
            clean = detect_box_breakout(AnalysisInputService(store).build('600001.SH', cutoff).bars)
            assert {k: v for k, v in item['payload'].items() if k != 'price_basis'} == clean
            assert all(item['item_id'] not in i['payload'].get('evidence_item_ids', [])
                       for i in saved['items'] if i['payload'].get('kind') == 'structural-trade-scenario')
        finally:
            service.close()
