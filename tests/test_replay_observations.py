from dataclasses import replace
from datetime import date, timedelta

from stock_harness.models import StoredDailyBar
from stock_harness.replay import FrozenSignal, evaluate_frozen_signal, summarize_evaluations
from stock_harness.replay.observations import evaluate_wave_coverage, observation_events
from stock_harness.replay.cases import select_replay_cases


def signal(**kwargs):
    return replace(FrozenSignal(
        system_id="mean-reversion", system_version="v5", symbol="TEST", scope="stock",
        signal_date=date(2026, 1, 1), direction="long", reference_close=10,
        invalidation_price=9, selected_target_price=12, observation_sessions=3, score=75,
    ), **kwargs)


def bars(highs, lows=None):
    return [StoredDailyBar(
        symbol="TEST", trade_date=date(2026, 1, 2) + timedelta(days=i),
        open=10, high=high, low=lows[i] if lows else 9.5, close=10,
        volume=1000, source="test", updated_at_ms=0,
    ) for i, high in enumerate(highs)]


def test_claim_ends_on_target_and_suffix_cannot_change_it():
    value = signal()
    a = evaluate_frozen_signal(value, bars([12]))
    b = evaluate_frozen_signal(value, bars([12, 15], [9.5, 2]))
    assert a["observation"] == b["observation"]
    assert a["observation"]["status"] == "target-reached"
    assert a["trade"] == b["trade"]
    assert a["primary_metric"] == "simulated-exit-net-return"


def test_ambiguous_not_failure_and_pending_not_unfulfilled():
    ambiguous = evaluate_frozen_signal(signal(), bars([13], [8]))
    pending = evaluate_frozen_signal(signal(), bars([11]))
    expired = evaluate_frozen_signal(signal(), bars([11, 11, 11]))
    assert ambiguous["observation"]["status"] == "ambiguous"
    assert pending["observation"]["status"] == "pending"
    assert expired["observation"]["status"] == "expired-unfulfilled"
    summary = summarize_evaluations([ambiguous, pending, expired])["observation"]
    assert summary["conclusive_count"] == 1
    assert summary["target_rate"] == 0


def test_deadline_prevents_late_success_and_short_direction():
    late = evaluate_frozen_signal(signal(observation_sessions=1), bars([11, 13]))
    assert late["observation"]["status"] == "expired-unfulfilled"
    short = signal(direction="short", selected_target_price=8, invalidation_price=11)
    assert evaluate_frozen_signal(short, bars([10.5], [8]))["observation"]["status"] == "target-reached"


def test_explicit_episode_preserves_first_claim_and_score_history():
    a = evaluate_frozen_signal(signal(observation_id="wave-1"), bars([11, 12]))
    b = evaluate_frozen_signal(signal(
        observation_id="wave-1", signal_date=date(2026, 1, 2), score=95,
    ), bars([11, 12]))
    summary = summarize_evaluations([a, b])["observation"]
    assert summary["count"] == 1
    assert summary["merged_updates"] == 1
    assert list(summary["score_buckets"])[0].endswith("70-80")
    other = evaluate_frozen_signal(signal(system_id="trend", observation_id="wave-1"), bars([12]))
    assert summarize_evaluations([a, other])["observation"]["count"] == 2


def test_missing_identity_and_boundaries_are_explicit():
    a = evaluate_frozen_signal(signal(selected_target_price=None), bars([12]))
    summary = summarize_evaluations([a, a])["observation"]
    assert summary["claims_without_event_identity"] == 2
    assert summary["status_counts"]["not-evaluable"] == 2
    assert summary["target_rate"] is None
    assert summarize_evaluations([a])["trade"]["status_counts"] == {"not-configured": 1}


def test_layered_targets_do_not_rewrite_primary_claim():
    value = signal(metadata={"observation_targets": [
        {"label": "T1", "price": 12}, {"label": "T2", "price": 14},
    ]})
    result = evaluate_frozen_signal(value, bars([12, 13, 13]))["observation"]
    assert result["status"] == "target-reached"
    assert result["target_layers"][1]["status"] == "expired-unfulfilled"


def test_coverage_uses_separate_evaluation_labels_and_cases_ignore_ambiguity():
    value = evaluate_frozen_signal(signal(), bars([13], [8]))
    assert select_replay_cases([value]) == []
    coverage = evaluate_wave_coverage(observation_events([value]), [{
        "symbol": "TEST", "scope": "stock", "system_id": "mean-reversion",
        "start_date": "2026-01-02", "detection_dates": ["2026-01-01", "2026-01-02"],
    }])
    assert coverage["coverage_rate"] == 1
    assert coverage["median_lead_sessions"] == 1
