from dataclasses import replace
from datetime import date, timedelta

import pytest

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.bull_flag_pattern import detect_bull_flag, STRATEGY_ID
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.pattern_analysis import PatternAnalysisService
from stock_harness.screener import ScreenerService
from stock_harness.sqlite_store import SQLiteMarketDataStore


def fixture(age=8):
    rows = [(10., 10., 100.)] * 60 + [(10., 10.4, 300.), (10.4, 10.8, 350.), (10.8, 11.1, 320.)]
    rows += [(11.02 - i * .01, 11.04 - i * .015 + (.015 if i % 2 else 0), 180. - i * 12)
             for i in range(age - 2)]
    return tuple(AnalysisBar(period_start=date(2026, 1, 1) + timedelta(days=i),
                            period_end=date(2026, 1, 1) + timedelta(days=i),
                            open=o, high=max(o, c) + .1, low=min(o, c) - .05, close=c, volume=v,
                            sources=("synthetic",), contains_provisional=False,
                            period_complete=True, observed_at_ms=0)
                 for i, (o, c, v) in enumerate(rows))


@pytest.mark.parametrize("age", [7, 8, 9, 10, 11, 12])
def test_ongoing_flag_with_declining_volume(age):
    result = detect_bull_flag(fixture(age))
    assert result and result["screen_eligible"]
    assert result["launch_age_sessions"] == age
    assert 8 <= result["impulse_gain_percent"] <= 15
    assert result["lower"] >= result["pole_low"]
    assert result["center_drift_percent"] <= .5
    assert result["first_target_price"] is None
    assert result["stage"] == "pullback-observation"


@pytest.mark.parametrize("failure", ["too-young", "too-old", "volume-flat", "volume-growing", "floor-broken",
                                    "breakout", "preview", "roll", "nan", "zero-volume", "unsorted", "short"])
def test_rejects_non_flags(failure):
    bars = list(fixture(6 if failure == "too-young" else 13 if failure == "too-old" else 8))
    if failure == "short": bars = bars[-7:]
    if failure == "unsorted": bars[-1], bars[-2] = bars[-2], bars[-1]
    if failure in ("volume-flat", "volume-growing"):
        for i in range(63, len(bars)):
            bars[i] = replace(bars[i], volume=180 if failure == "volume-flat" else 180 + i)
    if failure == "floor-broken": bars[-1] = replace(bars[-1], low=9.8, close=9.9)
    if failure == "breakout": bars[-1] = replace(bars[-1], high=11.5, close=11.4)
    if failure == "preview": bars[-1] = replace(bars[-1], contains_provisional=True)
    if failure == "roll": bars[-1] = replace(bars[-1], contains_roll_event=True)
    if failure == "nan": bars[-1] = replace(bars[-1], volume=float("nan"))
    if failure == "zero-volume": bars[-1] = replace(bars[-1], volume=0)
    assert detect_bull_flag(bars) is None


def test_breakout_then_return_is_not_an_ongoing_flag():
    bars = list(fixture(10))
    bars[-3] = replace(bars[-3], open=11.10, close=11.17, high=11.19)
    assert detect_bull_flag(bars) is None


@pytest.mark.parametrize("gain,eligible", [(.079, False), (.08, True), (.15, True), (.151, False), (.22, False)])
def test_pole_gain_boundaries_do_not_relabel_an_inner_rally(gain, eligible):
    factor = gain / .12
    bars = tuple(replace(b, **{key: 10 + (getattr(b, key) - 10) * factor
                              for key in ("open", "high", "low", "close")}) for b in fixture())
    assert (detect_bull_flag(bars) is not None) == eligible


def test_strongly_rising_flag_center_is_not_sideways_consolidation():
    bars = list(fixture())
    for i in range(63, len(bars)):
        close = 10.8 + (i - 63) * .06
        bars[i] = replace(bars[i], open=close - .01, close=close, high=close + .04, low=close - .05)
    assert detect_bull_flag(bars) is None


def test_saved_screener_chart_and_detector_have_identical_causal_evidence(monkeypatch):
    prefix = fixture()
    cutoff = prefix[-1].period_end
    evidence = detect_bull_flag(prefix)
    original = PatternAnalysisService.analyze
    calls = []
    def counted(service, request):
        calls.append(request)
        return original(service, request)
    monkeypatch.setattr(PatternAnalysisService, "analyze", counted)
    for mode in ("prefix", "future", "poison"):
        bars = list(prefix)
        if mode != "prefix":
            bars.append(replace(prefix[-1], period_start=cutoff + timedelta(days=1),
                                period_end=cutoff + timedelta(days=1), volume=10**9 if mode == "poison" else 100,
                                open=100 if mode == "poison" else 11, high=1000 if mode == "poison" else 11.5,
                                low=1 if mode == "poison" else 10.5, close=2 if mode == "poison" else 11.2))
        with SQLiteMarketDataStore(":memory:") as store:
            store.upsert_instruments([Instrument("600001.SH", "Synthetic", InstrumentKind.STOCK, "SH")])
            daily = [DailyBar("600001.SH", b.period_end, b.open, b.high, b.low, b.close, b.volume) for b in bars]
            store.upsert_daily_bars("tushare", daily)
            store.upsert_trading_dates("tushare", [b.trade_date for b in daily])
            before = len(calls)
            run = ScreenerService(store).run_sync([], [], 10, cutoff, STRATEGY_ID)
            assert len(calls) == before + 1
            candidate, = store.list_screener_candidates(run["run_id"])
            saved = store.get_generated_analysis_run(candidate["analysis_run_id"])
            item = next(i for i in saved["items"] if i["item_id"] == candidate["line_item_id"])
            assert item["payload"] == candidate["evidence"] == evidence


def test_api_registers_the_independent_strategy():
    from stock_harness.api_models import ScreenerRunInput
    assert ScreenerRunInput(strategy_id=STRATEGY_ID).strategy_id == STRATEGY_ID
    assert next(s for s in ScreenerService.strategies() if s["strategy_id"] == STRATEGY_ID)["states"] == ["pullback-observation"]
