from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_harness.models import (
    BoardMembership, DailyBar, Instrument, InstrumentKind, StockDailyLimit,
    StoredDailyBar,
)
from stock_harness.board_hotspot_evaluation import (
    canonical_board_name, evaluate_hotspot_timelines, is_objective_confirmation,
)
from stock_harness.board_hotspot_features import extract_board_hotspot_features
from stock_harness.observation_systems import (
    BOARD_HOTSPOT_SYSTEM,
    BoardHotspotSystem,
    ObservationSystemContext,
    ObservationSystemRegistry,
    TrendBreakoutSystem,
)
from stock_harness.review_scoring import (
    TREND_BREAKOUT_SCORER, default_scorer_registry, execute_scorer,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _observation(**overrides: object) -> dict[str, object]:
    metrics = {
        "returns": {"5": .07, "20": .14},
        "relative_strength": {"5": .04, "20": .08},
        "volume_ratio20": 1.4,
        "recent_volume_ratio_5_5": 1.2,
        "short_shape": {"state": "rising"},
        "medium_shape": {"state": "rising"},
        "board_breadth": {
            "breadth": .45, "impact_concentration_hhi": .12,
        },
    }
    metrics.update(overrides)
    return {
        "symbol": "BK001.DC", "coverage_state": "complete",
        "metrics": metrics, "state_codes": [], "attention_reasons": [],
        "disqualifiers": [],
    }


def _execute(
    observation: dict[str, object], prior: dict[str, object] | None = None,
    snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    system = BoardHotspotSystem()
    member_snapshot = snapshot or {
        "coverage_ratio": .95, "positive_return_5_ratio": .72,
        "limit_up_count": 2, "broken_up_count": 1,
        "max_limit_up_streak": 2,
    }
    result = system.execute(ObservationSystemContext(
        observations=[observation],
        prior_scores={BOARD_HOTSPOT_SYSTEM: {"BK001.DC": prior} if prior else {}},
        dependencies={"board_hotspot_features": {"BK001.DC": {
            "coverage_state": observation["coverage_state"],
            "metrics": observation["metrics"],
            "member_snapshot": member_snapshot,
        }}},
    ))
    return result.results[0]


def test_registry_rejects_duplicates_and_isolates_missing_dependencies() -> None:
    registry = ObservationSystemRegistry()
    registry.register(BoardHotspotSystem())
    with pytest.raises(ValueError, match="duplicate observation system"):
        registry.register(BoardHotspotSystem())

    execution = registry.execute_all(ObservationSystemContext(
        observations=[],
    ))[0]
    assert execution.results == []
    assert execution.error == "missing dependencies: board_hotspot_features"


def test_trend_plugin_is_behavior_equivalent_to_existing_scorer() -> None:
    scorers = default_scorer_registry()
    observation = _observation(price_space={
        "state": "no-entry", "entry_price": None,
        "invalidation_price": None, "targets": [],
    })
    plugin = TrendBreakoutSystem(scorers.get(TREND_BREAKOUT_SCORER).version)
    actual = plugin.execute(ObservationSystemContext(
        observations=[observation],
        dependencies={"review_scorer_registry": scorers},
    )).results
    expected = execute_scorer(
        scorers.get(TREND_BREAKOUT_SCORER), [observation],
    ).results
    assert actual == expected


def test_hotspot_requires_persistence_then_tracks_acceleration_and_decay() -> None:
    first = _execute(_observation())
    assert first["candidate_streak"] == 1
    assert first["eligible"] is False

    second = _execute(_observation(), first)
    assert second["candidate_streak"] == 2
    assert second["eligible"] is True
    assert second["hotspot_stage"] in {"breadth-expanding", "hotspot-confirmed", "accelerating"}

    weak = _execute(_observation(
        returns={"5": -.04, "20": .03},
        relative_strength={"5": -.05, "20": -.03},
        volume_ratio20=.7, recent_volume_ratio_5_5=.7,
        short_shape={"state": "falling"}, medium_shape={"state": "falling"},
        board_breadth={"breadth": -.5, "impact_concentration_hhi": .2},
    ), second, {
        "coverage_ratio": .95, "positive_return_5_ratio": .2,
        "limit_up_count": 0, "broken_up_count": 0, "max_limit_up_streak": 0,
    })
    assert weak["hotspot_stage"] == "exhausted"
    assert weak["score_direction"] == "declining"


def test_hotspot_filters_single_member_and_one_session_volume_noise() -> None:
    noisy = _execute(_observation(
        returns={"5": .01, "20": .03}, volume_ratio20=2.4,
        board_breadth={"breadth": .05, "impact_concentration_hhi": .58},
    ), snapshot={
        "coverage_ratio": .9, "positive_return_5_ratio": .51,
        "limit_up_count": 1, "broken_up_count": 0, "max_limit_up_streak": 1,
    })
    codes = {item["code"] for item in noisy["penalties"]}
    assert {"single-member-concentration", "one-session-volume-noise"} <= codes
    assert noisy["eligible"] is False


def test_board_hotspot_snapshot_aggregates_breadth_and_limit_streak() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("BK001.DC", "Board", InstrumentKind.SECTOR, "DC"),
        Instrument("000001.SZ", "Leader", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "Follower", InstrumentKind.STOCK, "SZ"),
    ])
    start = date(2026, 9, 1)
    days = [start + timedelta(days=index) for index in range(6)]
    bars = []
    limits = []
    for index, day in enumerate(days):
        leader = 10 * (1.1 ** index)
        follower = 10 * (1.01 ** index)
        bars.extend([
            DailyBar("000001.SZ", day, leader, leader, leader, leader, 100),
            DailyBar("000002.SZ", day, follower, follower, follower, follower, 100),
        ])
        limits.extend([
            StockDailyLimit("000001.SZ", day, leader, leader * .8),
            StockDailyLimit("000002.SZ", day, follower * 1.1, follower * .9),
        ])
    store.upsert_daily_bars("test", bars)
    store.upsert_stock_daily_limits("test", limits)
    store.replace_board_memberships("test", "BK001.DC", days[-1], [
        BoardMembership("BK001.DC", "000001.SZ", "Leader", "test", days[-1]),
        BoardMembership("BK001.DC", "000002.SZ", "Follower", "test", days[-1]),
    ])

    snapshot = store.calculate_board_hotspot_snapshots(days[-1])["BK001.DC"]
    assert snapshot["coverage_ratio"] == 1
    assert snapshot["limit_up_count"] == 1
    assert snapshot["max_limit_up_streak"] == 5
    assert snapshot["positive_return_5_ratio"] == 1
    store.close()


def _stored_bars(symbol: str, closes: list[float], volumes: list[int]) -> list[StoredDailyBar]:
    start = date(2026, 1, 1)
    return [
        StoredDailyBar(
            symbol, start + timedelta(days=index), value, value, value, value,
            volumes[index], "test", 0,
        )
        for index, value in enumerate(closes)
    ]


def test_hotspot_feature_extraction_is_causal_and_reusable() -> None:
    closes = [100 + index * .6 for index in range(31)]
    bars = _stored_bars("BK001.DC", closes, [100] * 26 + [150] * 5)
    benchmark = _stored_bars("000001.SH", [100 + index * .1 for index in range(31)], [100] * 31)
    feature = extract_board_hotspot_features(
        bars, benchmark,
        breadth_snapshot={"breadth": .5, "impact_concentration_hhi": .1},
        member_snapshot={
            "positive_return_5_ratio": .7, "limit_up_count": 1,
            "max_limit_up_streak": 2,
        },
    )
    assert feature["coverage_state"] == "complete"
    assert feature["metrics"]["relative_strength"]["20"] > 0
    assert feature["metrics"]["recent_volume_ratio_5_5"] == 1.5

    earlier = extract_board_hotspot_features(bars[:-1], benchmark[:-1])
    assert earlier["metrics"]["returns"]["5"] != feature["metrics"]["returns"]["5"]


def test_hotspot_evaluator_clusters_aliases_and_measures_lead_without_future_inputs() -> None:
    assert canonical_board_name("农业综合Ⅲ(A股)") == "农业综合"
    feature = {
        "metrics": {
            "returns": {"5": .06, "20": .12},
            "relative_strength": {"20": .05},
            "recent_volume_ratio_5_5": 1.1,
        },
        "member_snapshot": {"positive_return_5_ratio": .7, "limit_up_count": 1},
    }
    assert is_objective_confirmation(feature)
    timeline = [
        {"effective_date": f"2026-01-0{index + 1}", "eligible": index == 0,
         "hotspot_stage": "trend-emerging" if index == 0 else "failed",
         "total_score": 55 if index == 0 else 20, "candidate_streak": 1,
         "feature": feature if index >= 2 else {}}
        for index in range(4)
    ]
    result = evaluate_hotspot_timelines(
        {"BK001.DC": timeline}, names={"BK001.DC": "绿色电力"},
    )
    assert result["precision"] == 1
    assert result["recall"] == 1
    assert result["median_lead_sessions"] == 3
    assert result["named_theme_hits"]["electricity"] == ["BK001.DC"]
