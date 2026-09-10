from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_harness.models import (
    BoardMembership, DailyBar, Instrument, InstrumentKind, StockDailyLimit,
)
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
    result = system.execute(ObservationSystemContext(
        observations=[observation], scorer_registry=default_scorer_registry(),
        prior_scores={BOARD_HOTSPOT_SYSTEM: {"BK001.DC": prior} if prior else {}},
        dependencies={"board_hotspot_snapshots": {"BK001.DC": snapshot or {
            "coverage_ratio": .95, "positive_return_5_ratio": .72,
            "limit_up_count": 2, "broken_up_count": 1,
            "max_limit_up_streak": 2,
        }}},
    ))
    return result.results[0]


def test_registry_rejects_duplicates_and_isolates_missing_dependencies() -> None:
    registry = ObservationSystemRegistry()
    registry.register(BoardHotspotSystem())
    with pytest.raises(ValueError, match="duplicate observation system"):
        registry.register(BoardHotspotSystem())

    execution = registry.execute_all(ObservationSystemContext(
        observations=[], scorer_registry=default_scorer_registry(),
    ))[0]
    assert execution.results == []
    assert execution.error == "missing dependencies: board_hotspot_snapshots"


def test_trend_plugin_is_behavior_equivalent_to_existing_scorer() -> None:
    scorers = default_scorer_registry()
    observation = _observation(price_space={
        "state": "no-entry", "entry_price": None,
        "invalidation_price": None, "targets": [],
    })
    plugin = TrendBreakoutSystem(scorers.get(TREND_BREAKOUT_SCORER).version)
    actual = plugin.execute(ObservationSystemContext(
        observations=[observation], scorer_registry=scorers,
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
