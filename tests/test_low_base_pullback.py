"""Calibration examples and adversarial controls, not a profitability backtest."""

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.low_base_pullback import detect_low_base_pullback
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.pattern_analysis import PatternAnalysisService
from stock_harness.screener import LOW_BASE_STRATEGY_ID, PULLBACK_STRATEGY_ID, ScreenerService
from stock_harness.sqlite_store import SQLiteMarketDataStore


FILES = {
    "001258.SZ": "low_base_001258_20260226.json",
    "600127.SH": "first_pullback_600127_20260824.json",
    "600371.SH": "first_pullback_600371_20260824.json",
}


def rows(symbol="001258.SZ"):
    data = json.loads((Path(__file__).parent / "fixtures" / FILES[symbol]).read_text(encoding="utf-8"))
    assert hashlib.sha256(json.dumps(data["bars"], separators=(",", ":")).encode()).hexdigest() == data["bars_sha256"]
    return data["bars"]


def bars(symbol="001258.SZ", cutoff="2026-02-11"):
    return tuple(AnalysisBar(
        period_start=date.fromisoformat(d), period_end=date.fromisoformat(d),
        open=o, high=h, low=l, close=c, volume=v, sources=("tushare",),
        contains_provisional=False, period_complete=True, observed_at_ms=0,
    ) for d, o, h, l, c, v in rows(symbol) if d <= cutoff)


@pytest.mark.parametrize("symbol,day,stage", [
    ("001258.SZ", "2026-02-10", "pullback-observation"),
    ("001258.SZ", "2026-02-11", "pullback-observation"),
    ("001258.SZ", "2026-02-24", "pullback-confirmed"),
    ("001258.SZ", "2026-02-25", "pullback-confirmed"),
    ("001258.SZ", "2026-02-26", "pullback-confirmed"),
    ("600127.SH", "2026-08-14", "pullback-observation"),
    ("600371.SH", "2026-08-14", "pullback-observation"),
    ("600127.SH", "2026-08-17", "pullback-confirmed"),
    ("600371.SH", "2026-08-17", "pullback-confirmed"),
])
def test_prelaunch_calibration_uses_only_available_prefix(symbol, day, stage):
    result = detect_low_base_pullback(bars(symbol, day))
    assert result and result["screen_eligible"]
    assert result["stage"] == stage
    assert result["launch_type"] == "low-base-platform"
    assert result["as_of_date"] == day
    assert result["pullback_volume_ratio"] <= .70
    assert result["pullback_platform_volume_ratio"] <= .90
    assert all(v <= day for k, v in result.items() if k.endswith("_date") and v is not None)
    assert result["first_target_price"] is None
    assert result["first_risk_reward"] is None
    shape = result["platform_shape"]
    assert shape["end_date"] < result["pullback_metric_start_date"]
    assert shape["sessions"] >= 3


def test_confirmation_freezes_retest_evidence_not_future_returns():
    first = detect_low_base_pullback(bars(cutoff="2026-02-24"))
    later = detect_low_base_pullback(bars(cutoff="2026-02-26"))
    for key in ("confirmation_date", "recognition_date", "pullback_volume_ratio",
                "pullback_metric_start_date", "pullback_metric_end_date", "flag_window"):
        assert first[key] == later[key]
    assert first["confirmation_date"] == "2026-02-24"


def test_breakout_does_not_improve_shape_score_or_change_plateau_evidence():
    observation = detect_low_base_pullback(bars(cutoff="2026-02-13"))
    confirmed = detect_low_base_pullback(bars(cutoff="2026-02-24"))
    assert observation["platform_shape"] == confirmed["platform_shape"]
    assert observation["score"] == confirmed["score"]


@pytest.mark.parametrize("volumes,quality", [
    ((200, 200, 200, 100, 100), "persistent-bullish-volume"),
    ((1000, 50, 50, 100, 100), "single-pulse-dominated"),
    ((50, 50, 50, 100, 100), "no-persistent-bullish-advantage"),
])
def test_platform_volume_requires_persistence_not_one_large_candle(volumes, quality):
    from stock_harness.low_base_pullback import _platform_evidence
    original = bars()[-5:]
    platform = [replace(b, open=10., close=10.01 if i < 3 else 9.99,
                        high=10.1, low=9.9, volume=volumes[i])
                for i, b in enumerate(original)]
    facts = _platform_evidence(platform)
    assert facts["volume_quality"] == quality
    assert facts["small_body_fraction"] == 1
    assert facts["bullish_sessions"] == 3
    assert facts["bearish_sessions"] == 2


def test_one_sided_platform_is_unknown_not_infinite_bullish_advantage():
    from stock_harness.low_base_pullback import _platform_evidence
    platform = [replace(b, open=b.close * .999) for b in bars()[-5:]]
    facts = _platform_evidence(platform)
    assert facts["volume_quality"] == "insufficient-directional-evidence"
    assert facts["bullish_bearish_volume_ratio"] is None
    assert facts["trimmed_bullish_bearish_volume_ratio"] is None


def test_plateau_metrics_exclude_later_retest_and_breakout():
    from statistics import fmean
    for day in ("2026-02-11", "2026-02-24"):
        prefix = bars(cutoff=day)
        result = detect_low_base_pullback(prefix)
        plateau = [b for b in prefix if result["start_date"] <= b.period_end.isoformat()
                   < result["pullback_metric_start_date"]]
        shape = result["platform_shape"]
        assert shape["sessions"] == len(plateau)
        assert shape["close_drift_percent"] == round((plateau[-1].close / plateau[0].close - 1) * 100, 4)
        assert shape["mean_body_percent"] == round(fmean(abs(b.close - b.open) / b.open for b in plateau) * 100, 4)


@pytest.mark.parametrize("failure", ["no-volume-pulse", "high-position", "heavy-retest",
                                    "broken-support", "wide-platform", "preview", "roll",
                                    "incomplete", "nan", "zero-volume", "unsorted", "short"])
def test_failure_controls_do_not_screen(failure):
    original = list(bars())
    changed = []
    for i, b in enumerate(original):
        if failure == "no-volume-pulse": b = replace(b, volume=100)
        if failure == "high-position" and i < 35:
            b = replace(b, open=b.open / 2, high=b.high / 2, low=b.low / 2, close=b.close / 2)
        if failure == "heavy-retest" and i >= len(original) - 4: b = replace(b, volume=10**9)
        if failure == "wide-platform" and i >= len(original) - 4: b = replace(b, low=b.low * .8)
        changed.append(b)
    last = changed[-1]
    if failure == "broken-support": changed[-1] = replace(last, open=6., high=6.1, low=5.9, close=6.)
    if failure == "preview": changed[-1] = replace(last, contains_provisional=True)
    if failure == "roll": changed[-1] = replace(last, contains_roll_event=True)
    if failure == "incomplete": changed[-1] = replace(last, period_complete=False)
    if failure == "nan": changed[-1] = replace(last, close=float("nan"))
    if failure == "zero-volume": changed[-1] = replace(last, volume=0)
    if failure == "unsorted": changed[-1], changed[-2] = changed[-2], changed[-1]
    if failure == "short": changed = changed[-30:]
    result = detect_low_base_pullback(changed)
    assert result is None or not result["screen_eligible"]


def test_confirmation_expires_and_second_wave_cannot_rearm():
    original = bars(cutoff="2026-02-26")
    extra = []
    for i, close in enumerate((8.1, 8., 7.7, 7.6), 1):
        d = original[-1].period_end + timedelta(days=i)
        extra.append(replace(original[-1], period_start=d, period_end=d,
                             open=close, high=close + .05, low=close - .05, close=close))
    for i in range(1, len(extra) + 1):
        result = detect_low_base_pullback((*original, *extra[:i]))
        assert result is None or not result["screen_eligible"]


def test_post_breakout_hold_does_not_reuse_old_platform_as_a_profit_target():
    original = bars(cutoff="2026-02-25")
    last = original[-1]
    changed = (*original[:-1], replace(last, open=7.82, high=7.85, low=7.76, close=7.80))
    result = detect_low_base_pullback(changed)
    assert result["screen_eligible"]
    assert result["stage"] == "pullback-confirmed"
    assert result["first_target_price"] is None
    assert result["first_risk_reward"] is None


@pytest.mark.parametrize("symbol,day", [("001258.SZ", "2026-02-11"), ("001258.SZ", "2026-02-25"),
                                      ("600127.SH", "2026-08-14"), ("600371.SH", "2026-08-14")])
def test_saved_screener_item_matches_chart_with_missing_real_and_poisoned_future(symbol, day, monkeypatch):
    snapshots = []
    calls = []
    analyze = PatternAnalysisService.analyze
    def counted(service, request):
        calls.append(request)
        return analyze(service, request)
    monkeypatch.setattr(PatternAnalysisService, "analyze", counted)
    for mode in ("prefix", "full", "poison"):
        with SQLiteMarketDataStore(":memory:") as store:
            store.upsert_instruments([Instrument(symbol, symbol, InstrumentKind.STOCK, symbol[-2:])])
            data = []
            for d, o, h, l, c, v in rows(symbol):
                if mode == "prefix" and d > day: continue
                if mode == "poison" and d > day: o, h, l, c, v = 100., 1000., 1., 2., 10**10
                data.append(DailyBar(symbol, date.fromisoformat(d), o, h, l, c, v))
            store.upsert_daily_bars("tushare", data)
            store.upsert_trading_dates("tushare", [b.trade_date for b in data])
            before = len(calls)
            run = ScreenerService(store).run_sync([], [], 10, date.fromisoformat(day), LOW_BASE_STRATEGY_ID)
            assert len(calls) == before + 1
            assert run["status"] == "succeeded"
            candidate, = store.list_screener_candidates(run["run_id"])
            saved = store.get_generated_analysis_run(candidate["analysis_run_id"])
            item = next(i for i in saved["items"] if i["item_id"] == candidate["line_item_id"])
            assert candidate["evidence"] == item["payload"]
            assert candidate["evidence"] == detect_low_base_pullback(bars(symbol, day))
            snapshots.append(candidate["evidence"])
            legacy = ScreenerService(store).run_sync([], [], 10, date.fromisoformat(day), PULLBACK_STRATEGY_ID)
            assert all(c["evidence"]["launch_type"] != "low-base-platform"
                       for c in store.list_screener_candidates(legacy["run_id"]))
    assert snapshots[0] == snapshots[1] == snapshots[2]


def test_api_registers_separate_low_base_strategy():
    from stock_harness.api_models import ScreenerRunInput
    assert ScreenerRunInput(strategy_id=LOW_BASE_STRATEGY_ID).strategy_id == LOW_BASE_STRATEGY_ID
    registered = next(s for s in ScreenerService.strategies() if s["strategy_id"] == LOW_BASE_STRATEGY_ID)
    assert registered["window"] == 30
    assert registered["states"] == ["pullback-observation", "pullback-confirmed"]
