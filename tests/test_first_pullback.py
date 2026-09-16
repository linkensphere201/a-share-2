from dataclasses import replace
from datetime import date, timedelta

import pytest

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.first_pullback_pattern import detect_first_pullback
from stock_harness.models import AdjustmentFactor, DailyBar, Instrument, InstrumentKind
from stock_harness.pattern_analysis import PatternAnalysisService
from stock_harness.screener import ScreenerService, PULLBACK_STRATEGY_ID
from stock_harness.sqlite_store import SQLiteMarketDataStore


def sample(confirmed=True):
    closes = [10 + .01 * (i % 3) for i in range(40)]
    closes += [10.6, 11.2, 11.9, 12.4, 12.7, 12.2, 11.8, 11.5, 11.45, 11.47]
    if confirmed:
        closes += [11.85]
    return tuple(AnalysisBar(
        period_start=date(2026, 1, 1) + timedelta(days=i),
        period_end=date(2026, 1, 1) + timedelta(days=i),
        open=c - .06 if i == 40 or i == 50 else c + .02,
        high=c + (.04 if i == 50 else .12), low=c - .12, close=c,
        volume=240 if 40 <= i <= 44 else 130 if i == 50 else 100,
        sources=("test",), contains_provisional=False, period_complete=True, observed_at_ms=1,
    ) for i, c in enumerate(closes))


def append(bars, close, volume=100):
    day = bars[-1].period_end + timedelta(days=1)
    return (*bars, replace(bars[-1], period_start=day, period_end=day,
                          open=close-.03, high=close+.04, low=close-.12, close=close, volume=volume))


def test_observation_and_confirmation_are_distinct_and_price_ordered():
    watch = detect_first_pullback(sample(False))
    result = detect_first_pullback(sample())
    assert watch["stage"] == "pullback-observation"
    assert watch["confirmation_date"] is None
    assert result["stage"] == "pullback-confirmed"
    assert result["screen_eligible"]
    assert result["invalidation_price"] < result["latest_close"] < result["first_target_price"]
    assert result["first_risk_reward"] >= 1.5
    assert watch["launch_date"] == result["launch_date"]


def test_every_prefix_is_causal_and_no_later_bar_can_rewrite_a_frozen_result():
    bars = sample()
    history = [detect_first_pullback(bars[:i]) for i in range(1, len(bars)+1)]
    extended = append(append(bars, 9), 14)
    assert [detect_first_pullback(extended[:i]) for i in range(1, len(bars)+1)] == history
    assert all(p is None or p["confirmation_date"] is None for p in history[:-1])
    assert history[-1]["confirmation_date"] == bars[-1].period_end.isoformat()


@pytest.mark.parametrize("change", ["preview", "incomplete", "zero-volume", "nan", "unsorted"])
def test_invalid_input_is_not_a_candidate(change):
    bars = list(sample())
    if change == "preview": bars[-1] = replace(bars[-1], contains_provisional=True)
    if change == "incomplete": bars[-1] = replace(bars[-1], period_complete=False)
    if change == "zero-volume": bars[-1] = replace(bars[-1], volume=0)
    if change == "nan": bars[-1] = replace(bars[-1], close=float("nan"))
    if change == "unsorted": bars[-1], bars[-2] = bars[-2], bars[-1]
    assert detect_first_pullback(bars) is None


def test_failed_breakout_and_heavy_volume_pullback_do_not_screen():
    failed = append(sample()[:41], 9.8)
    assert not detect_first_pullback(failed)["screen_eligible"]
    heavy = [replace(b, volume=400) if i >= 45 else b for i,b in enumerate(sample())]
    result = detect_first_pullback(heavy)
    assert result["stage"] == "disorderly"
    assert not result["screen_eligible"]


def test_support_failure_and_second_pullback_are_not_relabelled():
    bars = sample()
    broken = detect_first_pullback(append(bars, 10))
    assert broken["stage"] == "invalidated"
    assert not broken["screen_eligible"]
    second = detect_first_pullback(append(append(bars, 13), 11.9))
    assert second["stage"] == "completed"
    assert not second["screen_eligible"]
    assert second["launch_date"] == detect_first_pullback(bars)["launch_date"]


def test_confirmation_expires_and_too_late_entry_is_not_eligible():
    bars = sample()
    assert not detect_first_pullback(append(bars, 12.5))["screen_eligible"]
    for _ in range(3): bars = append(bars, 11.9)
    assert detect_first_pullback(bars)["stage"] == "expired"


def test_second_pullback_ends_first_leg_even_without_volume_confirmation():
    bars = append(sample(False), 11.85, volume=50)
    first = detect_first_pullback(bars)
    assert first["confirmation_date"] is None
    assert first["stage"] == "pullback-observation"
    second = detect_first_pullback(append(bars, 11.5))
    assert not second["screen_eligible"]
    assert second["stage"] == "completed"
    assert "second-pullback-started" in second["reasons"]


def test_volume_window_dates_match_sessions_and_freeze_after_confirmation():
    bars = list(sample(False))
    day = bars[45]
    # A shallow first day is included in volume evidence before the 3% recognition.
    bars[45] = replace(day, open=12.6, high=12.75, low=12.5, close=12.6)
    observed = detect_first_pullback(bars)
    assert observed["start_date"] == day.period_end.isoformat()
    assert observed["recognition_date"] == bars[46].period_end.isoformat()
    confirmed = detect_first_pullback(sample())
    followed = detect_first_pullback(append(sample(), 11.9))
    assert confirmed["pullback_sessions"] == followed["pullback_sessions"]
    assert confirmed["pullback_metric_end_date"] == sample()[-2].period_end.isoformat()


def test_outside_bar_at_last_index_does_not_crash_or_claim_intraday_order():
    bars = list(sample()[:45])
    bars = list(append(bars, 12))
    bars[-1] = replace(bars[-1], high=13.5)
    result = detect_first_pullback(bars)
    assert result["stage"] == "invalidated"
    assert "outside-bar-order-ambiguous" in result["reasons"]


def seed(store, bars):
    symbol = "000001.SZ"
    store.upsert_instruments([Instrument(symbol, "First pullback", InstrumentKind.STOCK, "SZ")])
    store.upsert_daily_bars("tushare", [DailyBar(symbol, b.period_end, b.open, b.high, b.low, b.close, b.volume) for b in bars])
    store.upsert_adjustment_factors("tushare", [AdjustmentFactor(symbol, b.period_end, 1) for b in bars])
    store.upsert_trading_dates("tushare", [b.period_end for b in bars])


@pytest.mark.parametrize("confirmed", [True, False])
def test_screener_reads_one_saved_analysis_and_preserves_all_evidence(monkeypatch, confirmed):
    bars = sample(confirmed)
    calls = []
    original = PatternAnalysisService.analyze
    def analyze(service, request):
        calls.append(request)
        return original(service, request)
    monkeypatch.setattr(PatternAnalysisService, "analyze", analyze)
    with SQLiteMarketDataStore(":memory:") as store:
        seed(store, bars)
        run = ScreenerService(store).run_sync([], [], 10, bars[-1].period_end, PULLBACK_STRATEGY_ID)
        assert len(calls) == 1
        assert run["status"] == "succeeded"
        candidate, = store.list_screener_candidates(run["run_id"])
        saved = store.get_generated_analysis_run(candidate["analysis_run_id"])
        item = next(i for i in saved["items"] if i["item_id"] == candidate["line_item_id"])
        assert candidate["evidence"] == item["payload"]
        assert candidate["state"] == item["payload"]["stage"]
        assert candidate["score"] == item["payload"]["score"]
        assert candidate["evidence"] == detect_first_pullback(bars)


def test_stale_stock_is_excluded_without_rewriting_its_analysis():
    bars = sample()
    with SQLiteMarketDataStore(":memory:") as store:
        seed(store, bars)
        run = ScreenerService(store).run_sync([], [], 10, bars[-1].period_end + timedelta(days=1), PULLBACK_STRATEGY_ID)
        assert run["candidate_count"] == 0


def test_api_registers_and_accepts_strategy():
    from fastapi.testclient import TestClient
    from stock_harness.api import create_app
    from stock_harness.api_models import ScreenerRunInput
    assert ScreenerRunInput(strategy_id=PULLBACK_STRATEGY_ID).strategy_id == PULLBACK_STRATEGY_ID
    with SQLiteMarketDataStore(":memory:") as store:
        with TestClient(create_app(store)) as client:
            items = client.get("/api/screener/strategies").json()["items"]
            item = next(i for i in items if i["strategy_id"] == PULLBACK_STRATEGY_ID)
            assert item["states"] == ["pullback-observation", "pullback-confirmed"]


def test_recovery_without_confirmation_ends_first_pullback():
    bars = append(sample(False), 13)
    bars = append(bars, 11.9)
    result = detect_first_pullback(bars)
    assert result["stage"] == "completed"
    assert not result["screen_eligible"]


def test_research_zone_does_not_change_existing_trade_scenarios():
    from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType
    from stock_harness.structural_map import build_structural_map
    for stage in ("pullback-confirmed", "invalidated", "expired"):
        item = GeneratedAnalysisItem(item_id="first", item_type=GeneratedItemType.ZONE,
                                     payload={**detect_first_pullback(sample()), "stage": stage})
        assert build_structural_map([item], 11.85).boundaries == ()


def test_legacy_candidate_rows_survive_state_migration(tmp_path, monkeypatch):
    import stock_harness.sqlite_store as module
    from test_screener import _store_with_major_edge
    source, days = _store_with_major_edge()
    with source:
        run = ScreenerService(source).run_sync([], [], 10, days[-1])
        # Populate the old-state table with a real saved analysis and evidence.
        analysis = source._connection.execute('SELECT run_id FROM generated_analysis_runs LIMIT 1').fetchone()[0]
        run = source.create_screener_run("volume-accumulation-20d", "old", days[-1], {})
        source.complete_screener_run(run["run_id"], [{
            "symbol": "000001.SZ", "state": "accumulating", "score": 75,
            "analysis_run_id": analysis, "line_item_id": "old", "line_code": "OLD",
            "evidence": {"original": True},
        }])
        expected = source.list_screener_candidates(run["run_id"])
        database = tmp_path / "old-states.sqlite"
        old_schema = module._SCHEMA.replace(", 'pullback-observation', 'pullback-confirmed'", "")
        with monkeypatch.context() as patch:
            patch.setattr(module, "_SCHEMA", old_schema)
            patch.setattr(SQLiteMarketDataStore, "_ensure_screener_candidate_states", lambda self: None)
            with SQLiteMarketDataStore(database) as old:
                for table in ("instruments", "generated_analysis_runs", "screener_runs", "screener_candidates"):
                    columns = [row[1] for row in source._connection.execute(f'PRAGMA table_info({table})')]
                    rows = source._connection.execute(f'SELECT * FROM {table}').fetchall()
                    old._connection.executemany(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', rows)
                old._connection.commit()
        with SQLiteMarketDataStore(database) as migrated:
            assert migrated.list_screener_candidates(run["run_id"]) == expected
            assert migrated._connection.execute('PRAGMA foreign_key_check').fetchall() == []
