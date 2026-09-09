from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "backtest_stock_opportunities",
    Path(__file__).parents[1] / "scripts" / "backtest_stock_opportunities.py",
)
assert _SPEC and _SPEC.loader
module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(module)


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


def _score(symbol: str, *disqualifiers: str) -> dict[str, object]:
    return {
        "symbol": symbol, "name": symbol, "eligible": not disqualifiers,
        "disqualifiers": list(disqualifiers), "components": {"setup_state": 22},
        "setup_state": "triggered",
        "total_score": 80, "stressed_risk_reward": 3.5,
        "selected_target_label": "T1", "selected_target_price": 12,
    }
