from __future__ import annotations

from datetime import date
import gzip
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


_SPEC = importlib.util.spec_from_file_location(
    "backtest_stock_opportunities",
    Path(__file__).parents[1] / "scripts" / "backtest_stock_opportunities.py",
)
assert _SPEC and _SPEC.loader
module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(module)


def test_replay_defaults_to_four_bounded_feature_workers() -> None:
    assert module.DEFAULT_SCAN_WORKERS == 4


def test_select_replay_dates_is_deterministic_and_includes_boundaries() -> None:
    dates = [date(2026, 1, day) for day in range(1, 11)]

    selected = module.select_replay_dates(dates, 4)

    assert selected == [dates[0], dates[3], dates[6], dates[9]]


def test_select_replay_dates_uses_all_dates_when_window_is_short() -> None:
    dates = [date(2026, 1, 2), date(2026, 1, 5)]

    assert module.select_replay_dates(dates, 4) == dates


def test_summarize_run_reports_each_strict_gate() -> None:
    class Store:
        def list_signal_review_scores(self, run_id, system_id):
            assert run_id == "run-1"
            return [
                _score("ELIGIBLE"),
                _score("NO_M4", "m4-analysis-unavailable", "scenario-unavailable",
                       "no-credible-target-at-3r", "invalid-long-price-ordering"),
                _score("NO_STATE", "scenario-extended"),
                _score("NO_TARGET", "no-credible-target-at-3r",
                       "invalid-long-price-ordering"),
                _score("BAD_ORDER", "invalid-long-price-ordering"),
            ]

    result = module.summarize_run(Store(), {
        "run_id": "run-1", "effective_date": date(2026, 9, 8),
        "revision": 1, "status": "succeeded", "summary": {"stock_pool_count": 5},
    })

    assert result["funnel"] == {
        "scored": 5, "m4_succeeded": 4, "actionable_state": 3,
        "stressed_target_3r": 2, "valid_long_price_order": 1,
        "strict_opportunity": 1,
    }
    assert result["strict_opportunities"][0]["symbol"] == "ELIGIBLE"
    assert result["disqualifiers"]["invalid-long-price-ordering"] == 3


def test_focus_snapshot_summary_and_future_window_are_bounded() -> None:
    snapshot = {
        "effective_date": date(2026, 9, 8), "source_run_id": "focus-core",
        "items": [{
            "symbol": "A", "payload": {
                "presentation_bucket": "focus", "presentation_rank": 1,
                "presentation_lane": "critical", "presentation_reasons": ["critical"],
                "independent_score": 80,
                "independent_scan": {"price_basis": "raw-continuity-checked"},
            },
        }, {
            "symbol": "R", "payload": {"presentation_bucket": "risk"},
        }],
    }

    result = module.summarize_focus_snapshot(snapshot)

    assert result["focus_count"] == 1
    assert result["risk_count"] == 1
    assert result["focus_lane_counts"] == {"critical": 1}
    assert module._future_window_max([1, 5, 3, 8, 2], 2) == [5, 8, 8, None, None]


def test_replay_state_must_match_latest_result_and_algorithm_versions() -> None:
    state = {
        "effective_date": "2026-09-08",
        "algorithm_version": module.UNIFIED_STOCK_POOL_VERSION,
        "summary": {"presentation": {
            "algorithm_version": module.PRESENTATION_VERSION,
        }},
    }
    report = {"results": [{"effective_date": "2026-09-08"}]}

    assert module._state_matches_report(state, report)
    assert not module._state_matches_report(
        {**state, "effective_date": "2026-09-07"}, report,
    )


def test_selection_quality_reports_precision_baseline_and_capacity_lift() -> None:
    first, second = date(2026, 1, 2), date(2026, 1, 5)
    result = module._selection_quality_metrics(
        {first: {"A", "B"}, second: {"C"}},
        {first: {"A", "B", "X", "Y"}, second: {"A", "C", "X", "Y"}},
        {first: {"A", "X"}, second: {"A", "C"}},
    )

    assert result == {
        "eligible_observation_count": 8,
        "event_observation_count": 3,
        "selected_observation_count": 4,
        "recalled_observation_count": 2,
        "observation_recall": 0.6667,
        "selection_precision": 0.5,
        "universe_event_rate": 0.375,
        "precision_lift": 1.3333,
        "selection_rate": 0.5,
        "recall_lift_vs_capacity": 1.3333,
    }


def test_focus_transitions_report_turnover_and_residence_distribution() -> None:
    result = module._focus_transitions([
        {"effective_date": "2026-01-02", "focus_range": [
            {"symbol": "A"}, {"symbol": "B"},
        ]},
        {"effective_date": "2026-01-05", "focus_range": [
            {"symbol": "B"}, {"symbol": "C"},
        ]},
        {"effective_date": "2026-01-06", "focus_range": [
            {"symbol": "B"}, {"symbol": "C"},
        ]},
    ])

    assert result == {
        "entered_total": 1, "exited_total": 1,
        "average_daily_entered": 0.5,
        "median_daily_entered": 0, "p90_daily_entered": 1,
        "median_consecutive_stay_sessions": 2,
        "p90_consecutive_stay_sessions": 3,
        "maximum_consecutive_stay_sessions": 3,
        "average_daily_jaccard": 0.6667,
    }


def test_feature_cache_requires_completion_marker_and_matching_versions(tmp_path) -> None:
    args = SimpleNamespace(feature_cache_dir=tmp_path)
    effective = date(2026, 9, 8)
    path = module._feature_cache_path(args, effective)
    payload = {
        "feature_cache_version": module.FEATURE_CACHE_VERSION,
        "relative_strength_version": module.RELATIVE_STRENGTH_VERSION,
        "independent_scan_version": module.INDEPENDENT_SCAN_VERSION,
        "effective_date": effective.isoformat(),
        "records": [{"symbol": "A"}],
    }
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)

    assert not module._feature_cache_ready(args, effective)
    module._feature_cache_marker(path).write_text(
        module.FEATURE_CACHE_VERSION + "\n", encoding="ascii",
    )
    assert module._feature_cache_ready(args, effective)
    assert module._read_feature_cache(path) == [{"symbol": "A"}]


def _score(symbol: str, *disqualifiers: str) -> dict[str, object]:
    return {
        "symbol": symbol, "name": symbol, "eligible": not disqualifiers,
        "disqualifiers": list(disqualifiers), "components": {"setup_state": 22},
        "setup_state": "triggered",
        "total_score": 80, "stressed_risk_reward": 3.5,
        "selected_target_label": "T1", "selected_target_price": 12,
    }
