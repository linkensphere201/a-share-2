from dataclasses import replace
from datetime import date, timedelta

import pytest

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.low_base_validation import POLICY, analysis_bars, evaluate_episode, replay_snapshot, summarize


def make_bars(values):
    result = []
    for i, (opening, closing) in enumerate(values):
        day = date(2026, 1, 5) + timedelta(days=i)
        result.append(AnalysisBar(period_start=day, period_end=day, open=opening, close=closing,
                                  high=max(opening, closing) + .1, low=min(opening, closing) - .1,
                                  volume=100, sources=("test",), contains_provisional=False,
                                  period_complete=True, observed_at_ms=0))
    return tuple(result)


def evidence(bars, index=0, stage="pullback-observation"):
    return {"as_of_date": bars[index].period_end.isoformat(), "launch_date": "2026-01-01",
            "start_date": bars[0].period_end.isoformat(),
            "invalidation_price": 9., "breakout_price": 10., "score": 70., "stage": stage,
            "screen_eligible": True}


def test_stop_is_next_open_not_signal_close_or_ideal_stop_fill():
    bars = make_bars([(10, 10), (10.5, 8.5), (7, 100)])
    r = evaluate_episode(bars, 0, evidence(bars))
    assert r["entry_date"] == "2026-01-06"
    assert r["entry_price"] == pytest.approx(10.5 * 1.0005)
    assert r["exit_trigger_date"] == "2026-01-06"
    assert r["exit_date"] == "2026-01-07"
    assert r["exit_price"] == pytest.approx(7 * .9995)
    assert r["exit_reason"] == "structural-stop"
    assert r["net_return_percent"] < -33
    assert not r["reached_100_percent"]  # Exit-day close is future at the opening fill.


def test_one_price_entry_is_skipped_without_retry():
    bars = list(make_bars([(10, 10), (11, 11), (11, 12)]))
    bars[1] = replace(bars[1], high=11, low=11)
    r = evaluate_episode(bars, 0, evidence(bars))
    assert r["status"] == "entry-unavailable"
    assert r["entry_date"] is None


def test_one_price_exit_is_deferred_and_gap_loss_not_hidden():
    bars = list(make_bars([(10, 10), (10, 8.5), (8, 8), (7, 8)]))
    bars[2] = replace(bars[2], high=8, low=8)
    r = evaluate_episode(bars, 0, evidence(bars))
    assert r["exit_date"] == "2026-01-08"
    assert r["exit_delayed_sessions"] == 1
    assert r["exit_price"] < 7


def test_opening_below_frozen_stop_is_not_a_fill():
    bars = make_bars([(10, 10), (8, 15)])
    r = evaluate_episode(bars, 0, evidence(bars))
    assert r["status"] == "entry-below-stop"


def test_confirmation_uses_stricter_frozen_breakout_hold_boundary():
    bars = make_bars([(10, 10), (9.5, 12)])
    assert evaluate_episode(bars, 0, evidence(bars))["entry_date"]
    r = evaluate_episode(bars, 0, evidence(bars, stage="pullback-confirmed"))
    assert r["status"] == "entry-below-stop"
    assert r["initial_stop"] == pytest.approx(9.8)


def test_trailing_profit_requires_prior_gain_and_executes_next_open():
    bars = make_bars([(10, 10), (10, 10), (10, 12), (12, 10.7), (10.6, 15)])
    r = evaluate_episode(bars, 0, evidence(bars))
    assert r["exit_trigger_date"] == "2026-01-08"
    assert r["exit_date"] == "2026-01-09"
    assert r["exit_reason"] == "trailing-profit-stop"
    assert r["max_close_gain_percent"] < 20  # Entry slippage matters.
    assert 5 < r["net_return_percent"] < 6


def test_sub_activation_pullback_does_not_arm_trailing_exit():
    bars = make_bars([(10, 10), (10, 11.4), (11, 10)])
    assert evaluate_episode(bars, 0, evidence(bars))["status"] == "open-censored"


@pytest.mark.parametrize("close,status", [(10.5, "open-censored"), (8., "exit-pending-censored")])
def test_sample_end_does_not_force_liquidation(close, status):
    bars = make_bars([(10, 10), (10, close)])
    r = evaluate_episode(bars, 0, evidence(bars))
    assert r["status"] == status
    assert r["net_return_percent"] is None
    assert r["mark_return_percent"] is not None
    assert summarize([r])["closed"] == 0
    assert summarize([r])["closed_positive_fraction"] is None


def test_end_date_is_respected_and_out_of_range_signal_rejected():
    bars = make_bars([(10, 10), (10, 11), (11, 100)])
    policy = replace(POLICY, outcome_end="2026-01-06")
    assert not evaluate_episode(bars, 0, evidence(bars), policy)["reached_100_percent"]
    assert evaluate_episode(bars[:1], 0, evidence(bars))["status"] == "no-next-bar"
    with pytest.raises(ValueError, match="evidence date"):
        evaluate_episode(bars, 1, evidence(bars))


def snapshot(bars):
    return {"instruments": [{"symbol": "000001.SZ", "name": "Synthetic", "bars": [
        {"trade_date": b.period_end.isoformat(), "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume, "source": "test", "bar_state": "final"} for b in bars]}]}


def test_replay_deduplicates_stages_and_supplies_only_daily_prefixes(monkeypatch):
    import stock_harness.low_base_validation as module
    bars = make_bars([(10, 10)] * 7)
    seen = []
    def detector(prefix):
        seen.append(len(prefix))
        return evidence(prefix, len(prefix) - 1, "pullback-observation" if len(prefix) < 4 else "pullback-confirmed")
    monkeypatch.setattr(module, "detect_low_base_pullback", detector)
    report = replay_snapshot(snapshot(bars))
    assert seen == list(range(1, 8))
    assert len(report["episodes"]) == 2
    assert report["unique_seeds"] == 1
    assert report["observed_seeds_confirmed_in_signal_window"] == 1
    assert report["screened_symbol_days"] == {"pullback-observation": 3, "pullback-confirmed": 4}


def test_duplicate_dates_are_not_silently_repaired():
    rows = snapshot(make_bars([(10, 10)] * 2))["instruments"][0]["bars"]
    with pytest.raises(ValueError, match="strictly increasing"):
        analysis_bars([rows[0], rows[0]])


def test_discontinuities_flag_but_do_not_remove_losses():
    bars = make_bars([(10, 10), (10, 6), (5, 5)])
    result = evaluate_episode(bars, 0, evidence(bars))
    assert result["raw_discontinuity_dates"]
    assert result["status"] == "closed"
    assert summarize([result])["closed_nonpositive"] == 1


def test_real_snapshot_prefix_signals_do_not_depend_on_future_rows():
    import json
    from pathlib import Path
    from stock_harness.low_base_validation import replay_snapshot
    data = json.loads((Path(__file__).parent / "fixtures" / "low_base_001258_20260226.json").read_text())
    rows = [{"trade_date": d, "open": o, "high": h, "low": l, "close": c, "volume": v,
             "bar_state": "final", "source": "tushare"} for d, o, h, l, c, v in data["bars"]]
    cutoff = "2026-02-11"
    policy = replace(POLICY, signal_end=cutoff)
    snapshots = [rows, [b for b in rows if b["trade_date"] <= cutoff],
                 [b if b["trade_date"] <= cutoff else {**b, "open": 100., "high": 1000., "low": 1., "close": 2., "volume": 10**10} for b in rows]]
    results = [replay_snapshot({"instruments": [{"symbol": "001258.SZ", "bars": b}]}, policy) for b in snapshots]
    assert results[0]["signals"]
    assert results[0]["signals"] == results[1]["signals"] == results[2]["signals"]
    # Outcome histories are intentionally different; only signal evidence must be invariant.
    assert results[0]["episodes"] != results[2]["episodes"]


def test_frozen_replay_refuses_policy_or_manifest_changes():
    from dataclasses import asdict
    import hashlib
    from pathlib import Path
    from scripts.validate_low_base_pullback import digest, validate_manifest
    from stock_harness.low_base_pullback import ALGORITHM_VERSION, CONFIG
    manifest = {"detector_source_sha256": hashlib.sha256(Path("src/stock_harness/low_base_pullback.py").read_bytes()).hexdigest(),
                "parameters": asdict(CONFIG), "policy": asdict(POLICY), "algorithm_version": ALGORITHM_VERSION}
    snapshot = {"manifest_sha256": digest(manifest)}
    validate_manifest(snapshot, manifest)
    manifest["policy"] = {**manifest["policy"], "trailing_drawdown": .50}
    with pytest.raises(ValueError, match="checksum"):
        validate_manifest(snapshot, manifest)
    snapshot["manifest_sha256"] = digest(manifest)
    with pytest.raises(ValueError, match="frozen detector/policy changed"):
        validate_manifest(snapshot, manifest)
