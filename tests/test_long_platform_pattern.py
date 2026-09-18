from dataclasses import replace
from datetime import date, timedelta
from math import sin, pi

import pytest

from stock_harness.analysis_inputs import AnalysisBar, AnalysisHorizons, AnalysisInputService, AnalysisTimeframe
from stock_harness.long_platform_pattern import detect_long_platform, STRATEGY_ID, KIND
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.pattern_analysis import PatternAnalysisService, PatternAnalysisRequest
from stock_harness.screener import ScreenerService
from stock_harness.sqlite_store import SQLiteMarketDataStore


def sample(length=120, context="decline"):
    closes = ([16 - i * .1 for i in range(60)] if context == "decline" else
              [6 + i * .065 for i in range(60)])
    closes += [10 + .5 * sin(i * pi / 10) for i in range(length - 20)]
    closes += [10 + .12 * sin(i * pi / 3) for i in range(20)]
    return tuple(AnalysisBar(
        period_start=date(2025, 1, 1) + timedelta(days=i),
        period_end=date(2025, 1, 1) + timedelta(days=i),
        open=c + (.025 if i % 2 else -.025), high=c + .1, low=c - .1,
        close=c, volume=(65 if i % 2 else 90) if i >= len(closes) - 20 else 120,
        sources=("fixture",), contains_provisional=False, period_complete=True, observed_at_ms=0,
    ) for i, c in enumerate(closes))


@pytest.mark.parametrize("length", [60, 120, 250])
@pytest.mark.parametrize("context", ["decline", "rise"])
def test_multiscale_platform_has_dated_geometry_and_soft_evidence(length, context):
    result = detect_long_platform(sample(length, context))
    assert result and result["screen_eligible"]
    assert length <= result["platform_sessions"] <= 250
    assert result["platform_type"] == ("low-base" if context == "decline" else "continuation")
    assert result["lower"] < result["latest_close"] < result["upper"]
    assert result["up_down_volume_ratio"] > 1
    assert result["recent_history_volume_ratio"] < .95
    assert result["recent_range_percent"] < result["platform_range_percent"]
    assert "first_target_price" not in result


@pytest.mark.parametrize("failure", ["breakout", "breakdown", "expanding-volume", "wide", "short", "nan", "preview", "roll", "unsorted", "zero"])
def test_rejects_ineligible_platforms(failure):
    bars = list(sample())
    if failure == "breakout": bars[-1] = replace(bars[-1], open=12, close=12, high=12.1, low=11.9)
    if failure == "breakdown": bars[-1] = replace(bars[-1], open=8, close=8, high=8.1, low=7.9)
    if failure == "expanding-volume": bars = [replace(b, volume=1000) if i >= len(bars)-20 else b for i, b in enumerate(bars)]
    if failure == "wide": bars[-1] = replace(bars[-1], high=11, low=9)
    if failure == "short": bars = bars[-59:]
    if failure == "nan": bars[-1] = replace(bars[-1], close=float("nan"))
    if failure == "preview": bars[-1] = replace(bars[-1], contains_provisional=True)
    if failure == "roll": bars[-1] = replace(bars[-1], contains_roll_event=True)
    if failure == "unsorted": bars[-1], bars[-2] = bars[-2], bars[-1]
    if failure == "zero": bars[-1] = replace(bars[-1], volume=0)
    assert detect_long_platform(bars) is None


def test_slow_directional_decline_is_not_sideways():
    bars = [replace(b, open=12-i*.012, close=12-i*.012, high=12.1-i*.012, low=11.9-i*.012)
            for i, b in enumerate(sample())]
    assert detect_long_platform(bars) is None


def test_historical_volume_spike_cannot_fake_recent_contraction():
    bars = [replace(b, volume=110 if i >= 100 else 100)
            for i, b in enumerate(sample()[-120:])]
    bars[90] = replace(bars[90], volume=10000)
    assert detect_long_platform(bars) is None


@pytest.mark.parametrize("price_scale,volume_scale", [(.1, 1000), (10, .001)])
def test_platform_classification_is_invariant_to_price_and_volume_units(price_scale, volume_scale):
    bars = sample()
    original = detect_long_platform(bars)
    scaled = detect_long_platform([replace(
        b, open=b.open * price_scale, high=b.high * price_scale,
        low=b.low * price_scale, close=b.close * price_scale,
        volume=b.volume * volume_scale,
    ) for b in bars])
    assert original is not None and scaled is not None
    for key in ("start_date", "end_date", "platform_sessions", "platform_type", "platform_stage"):
        assert scaled[key] == original[key]
    for key in ("score", "platform_range_percent", "recent_range_percent",
                "recent_history_volume_ratio", "recent_history_median_volume_ratio"):
        assert scaled[key] == pytest.approx(original[key])
    for key in ("lower", "upper", "center", "latest_close"):
        assert scaled[key] == pytest.approx(original[key] * price_scale)


def test_missing_demand_and_ma_history_are_not_hard_rejections():
    bars = [replace(b, open=b.close) for b in sample()[-60:]]
    result = detect_long_platform(bars)
    assert result
    assert result["up_down_volume_ratio"] is None
    assert result["ma240_slope_percent"] is None
    assert result["platform_type"] == "neutral"


def test_screener_chart_and_prefix_match_without_future_information():
    prefix = sample()
    cutoff = prefix[-1].period_end
    outputs = []
    for poison in (False, True):
        with SQLiteMarketDataStore(":memory:") as store:
            store.upsert_instruments([Instrument("600001.SH", "Fixture", InstrumentKind.STOCK, "SH")])
            bars = list(prefix)
            if poison:
                bars.append(replace(bars[-1], period_start=cutoff+timedelta(days=1),
                                    period_end=cutoff+timedelta(days=1), open=80, high=100, low=1, close=2, volume=10**9))
            daily = [DailyBar("600001.SH", b.period_end, b.open, b.high, b.low, b.close, b.volume) for b in bars]
            store.upsert_daily_bars("tushare", daily)
            store.upsert_trading_dates("tushare", [b.trade_date for b in daily])
            run = ScreenerService(store).run_sync([], [], 10, cutoff, STRATEGY_ID)
            assert run["status"] == "succeeded"
            candidate, = store.list_screener_candidates(run["run_id"])
            saved = store.get_generated_analysis_run(candidate["analysis_run_id"])
            item = next(i for i in saved["items"] if i["item_id"] == candidate["line_item_id"])
            assert candidate["evidence"] == item["payload"]
            chart, = PatternAnalysisService(store).analyze(PatternAnalysisRequest(
                "600001.SH", (AnalysisTimeframe.DAILY,), AnalysisHorizons(), "chart", as_of_date=cutoff,
            ))
            assert next(i for i in chart["items"] if i["payload"].get("kind") == KIND)["payload"] == item["payload"]
            expected = detect_long_platform(AnalysisInputService(store).build("600001.SH", cutoff).bars)
            assert {k:v for k,v in item["payload"].items() if k != "price_basis"} == expected
            outputs.append(candidate["evidence"])
    assert outputs[0] == outputs[1]


def test_negative_gate_skips_full_analysis_and_api_registers(monkeypatch):
    from stock_harness.api_models import ScreenerRunInput
    assert ScreenerRunInput(strategy_id=STRATEGY_ID).strategy_id == STRATEGY_ID
    assert any(s["strategy_id"] == STRATEGY_ID for s in ScreenerService.strategies())
    monkeypatch.setattr(PatternAnalysisService, "analyze", lambda *args: pytest.fail("negative must skip full analysis"))
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument("600001.SH", "Empty", InstrumentKind.STOCK, "SH")])
        run = ScreenerService(store).run_sync([], [], 10, date(2026, 9, 17), STRATEGY_ID)
        assert run["status"] == "succeeded" and run["scanned_count"] == 1 and run["candidate_count"] == 0


def test_positive_gate_builds_subject_input_once(monkeypatch):
    bars = sample()
    calls = []
    build = AnalysisInputService.build
    def counted(service, symbol, *args, **kwargs):
        calls.append(symbol)
        return build(service, symbol, *args, **kwargs)
    monkeypatch.setattr(AnalysisInputService, "build", counted)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument("600001.SH", "Fixture", InstrumentKind.STOCK, "SH")])
        store.upsert_daily_bars("tushare", [DailyBar("600001.SH", b.period_end, b.open, b.high, b.low, b.close, b.volume) for b in bars])
        store.upsert_trading_dates("tushare", [b.period_end for b in bars])
        result = PatternAnalysisService(store).analyze_screening_candidate(PatternAnalysisRequest(
            "600001.SH", (AnalysisTimeframe.DAILY,), AnalysisHorizons(), "count-input",
            as_of_date=bars[-1].period_end), "long-platform")
        assert result is not None and calls.count("600001.SH") == 1


@pytest.mark.parametrize("change", ["stale", "missing-session"])
def test_shared_input_rejects_stale_or_unexplained_gaps(change):
    bars = list(sample())
    cutoff = bars[-1].period_end
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument("600001.SH", "Fixture", InstrumentKind.STOCK, "SH")])
        store.upsert_trading_dates("tushare", [b.period_end for b in bars])
        bars.pop(-1 if change == "stale" else -15)
        store.upsert_daily_bars("tushare", [DailyBar("600001.SH", b.period_end, b.open, b.high, b.low, b.close, b.volume) for b in bars])
        request = PatternAnalysisRequest("600001.SH", (AnalysisTimeframe.DAILY,), AnalysisHorizons(), "gate", as_of_date=cutoff)
        assert PatternAnalysisService(store).analyze_screening_candidate(request, "long-platform") is None


def test_research_platform_does_not_add_trade_scenario_boundaries():
    from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType
    from stock_harness.structural_map import build_structural_map
    evidence = detect_long_platform(sample())
    item = GeneratedAnalysisItem("platform", GeneratedItemType.ZONE, evidence)
    result = build_structural_map([item], evidence["latest_close"])
    assert not result.boundaries and not result.setups
