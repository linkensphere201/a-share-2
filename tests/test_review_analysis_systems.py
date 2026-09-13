from __future__ import annotations

from dataclasses import replace

from stock_harness.review_systems import (
    ANALYSIS_RESULT_CONTRACT_VERSION,
    AnalysisSystemContext,
    AnalysisSystemDefinition,
    MeanReversionReviewSystem,
    ReviewAnalysisSystemRegistry,
    ScorerAnalysisSystemAdapter,
)
from stock_harness.review_scoring import TREND_BREAKOUT_SCORER, default_scorer_registry


def test_registry_validates_definitions_dependencies_and_duplicate_ids() -> None:
    registry = ReviewAnalysisSystemRegistry()
    system = MeanReversionReviewSystem()
    registry.register(system)

    execution = registry.execute_all(AnalysisSystemContext(entities_by_scope={}))[0]

    assert execution.results == ()
    assert execution.error == "missing dependencies: mean_reversion_facts"
    try:
        registry.register(system)
    except ValueError as error:
        assert "duplicate analysis system" in str(error)
    else:
        raise AssertionError("duplicate system id must be rejected")


def test_system_failure_is_isolated_from_other_plugins() -> None:
    registry = ReviewAnalysisSystemRegistry()
    registry.register(_BrokenSystem())
    registry.register(MeanReversionReviewSystem())
    context = AnalysisSystemContext(
        entities_by_scope={"board": [_entity("BOARD", "board", score_target=5)]},
        dependencies={"mean_reversion_facts": True},
    )

    broken, healthy = registry.execute_all(context)

    assert broken.error == "RuntimeError: isolated failure"
    assert healthy.error is None
    assert len(healthy.results) == 1


def test_mean_reversion_result_has_complete_contract_and_independent_scope_rank() -> None:
    system = MeanReversionReviewSystem()
    context = AnalysisSystemContext(
        entities_by_scope={
            "market": [_entity("MARKET", "market", score_target=4)],
            "board": [
                _entity("BOARD-A", "board", score_target=5),
                _entity("BOARD-B", "board", score_target=3.2),
            ],
            "stock": [_entity("STOCK", "stock", score_target=6)],
        },
        dependencies={"mean_reversion_facts": True},
    )

    results = list(system.analyze(context))
    board_a = next(item for item in results if item["symbol"] == "BOARD-A")
    board_b = next(item for item in results if item["symbol"] == "BOARD-B")
    market = next(item for item in results if item["symbol"] == "MARKET")
    stock = next(item for item in results if item["symbol"] == "STOCK")

    assert board_a["contract_version"] == ANALYSIS_RESULT_CONTRACT_VERSION
    assert board_a["eligible"] is True
    assert board_a["rank"] == 1 and board_b["rank"] == 2
    assert market["rank"] == 1 and stock["rank"] == 1
    assert board_a["scorecard"]["ranking_universe"] == "board:directional-pullback:daily"
    assert set(board_a["scorecard"]["dimensions"]) == {
        "center_regime", "normalized_deviation", "price_volume",
        "exhaustion_confirmation", "risk_reward",
    }
    assert sum(board_a["scorecard"]["dimensions"].values()) <= 100
    assert {item["role"] for item in board_a["chart_projection"]} >= {
        "moving-center", "atr-deviation-band", "confirmation", "invalidation", "target",
    }


def test_stock_oversold_falling_knife_is_disabled_in_v1() -> None:
    entity = _entity("STOCK", "stock", score_target=5)
    entity["mean_reversion"]["setup_family"] = "oversold-exhaustion"

    result = MeanReversionReviewSystem().analyze(AnalysisSystemContext(
        entities_by_scope={"stock": [entity]},
        dependencies={"mean_reversion_facts": True},
    ))[0]

    assert result["eligible"] is False
    assert "stock-falling-knife-disabled-v1" in result["disqualifiers"]


def test_mean_reversion_without_setup_has_zero_opportunity_score() -> None:
    entity = _entity("BOARD", "board", score_target=8)
    entity["mean_reversion"]["setup_family"] = "none"

    result = MeanReversionReviewSystem().analyze(AnalysisSystemContext(
        entities_by_scope={"board": [entity]},
        dependencies={"mean_reversion_facts": True},
    ))[0]

    assert result["total_score"] == 0
    assert result["eligible"] is False
    assert "no-mean-reversion-setup" in result["disqualifiers"]


def test_mean_reversion_history_only_compares_compatible_setup_family() -> None:
    entity = _entity("BOARD", "board", score_target=5)
    incompatible = {
        **_entity("BOARD", "board", score_target=4),
        "effective_date": "2026-09-10",
        "total_score": 75,
        "scorecard": {
            "total_score": 75,
            "ranking_universe": "board:oversold-exhaustion:daily",
        },
        "setup_family": "oversold-exhaustion",
        "timeframe": "daily",
    }
    compatible = {
        **_entity("BOARD", "board", score_target=3),
        "effective_date": "2026-09-09",
        "total_score": 60,
        "scorecard": {
            "total_score": 60,
            "ranking_universe": "board:directional-pullback:daily",
        },
        "setup_family": "directional-pullback",
        "timeframe": "daily",
    }

    result = MeanReversionReviewSystem().analyze(AnalysisSystemContext(
        entities_by_scope={"board": [entity]},
        prior_results={"mean-reversion": {"BOARD": incompatible}},
        recent_results={"mean-reversion": {"BOARD": [incompatible, compatible]}},
        dependencies={"mean_reversion_facts": True},
    ))[0]

    assert result["comparison"]["prior_total_score"] == 60
    assert [item["effective_date"] for item in result["comparison"]["recent"]] == [
        "2026-09-09",
    ]


def test_legacy_trend_scorer_runs_through_complete_trait_object_contract() -> None:
    scorer = default_scorer_registry().get(TREND_BREAKOUT_SCORER)
    registry = ReviewAnalysisSystemRegistry()
    registry.register(ScorerAnalysisSystemAdapter(scorer, display_name="趋势突破"))
    observation = {
        "symbol": "BK001.DC", "coverage_state": "complete",
        "state_codes": ["bullish-boundary-triggered"], "disqualifiers": [],
        "metrics": {
            "short_shape": {"state": "rising"},
            "medium_shape": {"state": "rising"},
            "volume_ratio20": 1.3,
            "price_space": {
                "state": "triggered", "entry_price": 10, "invalidation_price": 9,
                "targets": [{
                    "label": "T1", "price": 14, "basis": "key-level",
                    "risk_reward_ratio": 4, "stressed_risk_reward_ratio": 3.2,
                    "evidence_item_ids": ["level-1"],
                }],
            },
        },
    }

    execution = registry.execute(TREND_BREAKOUT_SCORER, AnalysisSystemContext(
        entities_by_scope={"board": [observation]},
    ))

    assert execution.error is None
    result = execution.results[0]
    assert result["contract_version"] == ANALYSIS_RESULT_CONTRACT_VERSION
    assert result["system_id"] == TREND_BREAKOUT_SCORER
    assert result["scorecard"]["total_score"] == result["total_score"]
    assert result["opportunity"]["selected_target_label"] == "T1"


def _entity(
    symbol: str, scope: str, *, score_target: float,
) -> dict[str, object]:
    entry = 10.0
    invalidation = 9.5
    target = entry + score_target * (entry - invalidation) / .9 * 1.1
    return {
        "symbol": symbol,
        "entity_key": symbol,
        "entity_scope": scope,
        "mean_reversion": {
            "version": "mean-reversion-facts-v2",
            "coverage_state": "complete",
            "as_of_date": "2026-09-11",
            "setup_family": "directional-pullback",
            "state": "reversal-confirmed",
            "parent_trend": "up",
            "center": {"price": 11, "stable": True},
            "recent_low_deviation_atr": -1.2,
            "momentum": {"negative_decelerating": True},
            "exhaustion": {"signal_count": 4},
            "price_volume": {"path": "volume-backed-reclaim"},
            "confirmation": {
                "confirmed": True, "boundary_price": 9.9, "entry_price": entry,
            },
            "invalidation_price": invalidation,
            "targets": [{
                "label": "T1", "price": target, "basis": "prior-platform",
                "risk_reward_ratio": score_target * 1.2,
                "stressed_risk_reward_ratio": score_target,
            }],
            "chart": {
                "center_points": [{"date": "2026-09-11", "price": 11}],
                "upper_band_points": [{"date": "2026-09-11", "price": 12}],
                "lower_band_points": [{"date": "2026-09-11", "price": 9}],
            },
            "disqualifiers": [],
        },
    }


class _BrokenSystem:
    definition = replace(
        MeanReversionReviewSystem.definition,
        system_id="broken", version="broken-v1", display_name="故障夹具",
        dependencies=(),
    )

    def analyze(self, context: AnalysisSystemContext):
        raise RuntimeError("isolated failure")

    def replay_definition(self):
        return {}
