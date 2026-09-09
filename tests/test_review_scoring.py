from __future__ import annotations

from stock_harness.review_scoring import (
    TREND_BREAKOUT_SCORER,
    ReviewScorerRegistry,
    default_scorer_registry,
    execute_scorer,
    hard_signal_events,
    score_entities,
)


def _observation(*, rr: float = 4.0, state: str = "retest") -> dict[str, object]:
    return {
        "symbol": "BK001.DC", "coverage_state": "complete",
        "state_codes": ["bullish-boundary-triggered", "sudden-volume-expansion"],
        "attention_reasons": ["6m-descending-envelope-broken"],
        "disqualifiers": [],
        "metrics": {
            "volume_ratio20": .8,
            "short_shape": {"state": "rising"},
            "medium_shape": {"state": "rising"},
            "relative_strength": {"5": .03, "20": .08},
            "price_space": {
                "state": state, "entry_price": 10, "invalidation_price": 9,
                "scenario_item_id": "scenario-1",
                "targets": [{
                    "label": "T1", "price": 10.5,
                    "basis": "key-level", "risk_reward_ratio": .5,
                    "stressed_risk_reward_ratio": .4,
                    "evidence_item_ids": ["level-1"],
                }, {
                    "label": "T2", "price": 10 + rr,
                    "basis": "estimated-volume-at-price+key-level",
                    "risk_reward_ratio": rr + .2,
                    "stressed_risk_reward_ratio": rr,
                    "evidence_item_ids": ["volume-1", "level-2"],
                }],
            },
        },
    }


def test_trend_breakout_requires_three_stressed_r_and_uses_nearest_target() -> None:
    scorer = default_scorer_registry().get(TREND_BREAKOUT_SCORER)
    result = score_entities(scorer, [_observation()])[0]

    assert result["eligible"] is True
    assert result["selected_target_label"] == "T2"
    assert result["stressed_risk_reward"] == 4.0
    assert result["components"]["risk_reward"] == 30.0
    assert result["total_score"] >= 75
    assert "4.00:1" in result["summary"]

    rejected = score_entities(scorer, [_observation(rr=2.9)])[0]
    assert rejected["eligible"] is False
    assert "no-credible-target-at-3r" in rejected["disqualifiers"]
    assert "不足3:1" in rejected["risk_summary"]


def test_hard_events_remain_visible_without_adding_a_bonus_component() -> None:
    result = score_entities(
        default_scorer_registry().get(TREND_BREAKOUT_SCORER),
        [_observation(rr=2.0)],
    )[0]

    assert {value["event_type"] for value in result["hard_events"]} == {
        "major-trend-breakout", "bullish-boundary-triggered",
        "sudden-volume-expansion",
    }
    assert "hard_event" not in result["components"]
    assert result["eligible"] is False
    assert hard_signal_events({
        "state_codes": ["descending-envelope-1y-broken"],
    })[0]["event_type"] == "major-trend-breakout"


def test_hard_event_lifecycle_tracks_continuing_and_resolved_events() -> None:
    scorer = default_scorer_registry().get(TREND_BREAKOUT_SCORER)
    prior = score_entities(scorer, [_observation()])[0]
    changed = _observation()
    changed["state_codes"] = ["bullish-boundary-triggered"]
    changed["attention_reasons"] = []

    result = score_entities(
        scorer, [changed], prior_by_symbol={"BK001.DC": prior},
    )[0]
    event_states = {
        event["event_type"]: event["state"] for event in result["hard_events"]
    }

    assert event_states["bullish-boundary-triggered"] == "continuing"
    assert event_states["major-trend-breakout"] == "resolved"
    assert event_states["sudden-volume-expansion"] == "resolved"


def test_ranking_is_eligible_first_and_history_marks_universe_changes() -> None:
    first = _observation(rr=3.2)
    second = {**_observation(rr=5.0), "symbol": "BK002.DC"}
    prior = {
        "symbol": "BK002.DC", "entity_key": "BK002.DC", "total_score": 70,
        "rank": 4, "ranking_universe_digest": "old", "effective_date": "2026-09-08",
    }
    results = score_entities(
        default_scorer_registry().get(TREND_BREAKOUT_SCORER),
        [first, second], prior_by_symbol={"BK002.DC": prior},
        recent_by_symbol={"BK002.DC": [prior]},
    )

    assert results[0]["symbol"] == "BK002.DC"
    assert results[0]["comparison"]["universe_changed"] is True
    assert results[0]["history"][0]["total_score"] == 70


def test_registry_accepts_a_third_scorer_without_shared_code_changes() -> None:
    class Dummy:
        system_id = "dummy"
        version = "dummy-v1"
        entity_scope = "board"

        def score(self, entity):
            return {
                "symbol": entity["symbol"], "eligible": True,
                "total_score": 50, "grade": "C", "verdict": "test",
                "summary": "test", "risk_summary": "test", "components": {},
                "penalties": [], "disqualifiers": [], "hard_events": [],
                "evidence_refs": [], "selected_scenario_id": None,
                "selected_target_label": None, "selected_target_price": None,
                "raw_risk_reward": None, "stressed_risk_reward": None,
            }

    registry = ReviewScorerRegistry()
    registry.register(Dummy())
    assert score_entities(registry.get("dummy"), [{"symbol": "BK001.DC"}])[0][
        "system_id"
    ] == "dummy"


def test_scorer_failure_is_isolated_as_an_explicit_execution_error() -> None:
    class Broken:
        system_id = "broken"
        version = "broken-v1"
        entity_scope = "board"
        cadence = "daily"
        ranking_universe = "test"
        input_dependencies = ()
        hard_gates = ()
        dimensions = ()
        penalties = ()
        tie_breakers = ()
        evidence_requirements = ()
        presentation_fields = ()

        def score(self, _entity):
            raise RuntimeError("fixture failure")

    execution = execute_scorer(Broken(), [{"symbol": "BK001.DC"}])

    assert execution.results == []
    assert execution.error == "RuntimeError: fixture failure"
