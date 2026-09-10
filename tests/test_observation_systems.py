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
from stock_harness.board_capacity import (
    classify_board_capacities, market_capacity_fit,
)
from stock_harness.board_theme_registry import (
    BOARD_THEME_REGISTRY_VERSION, THEMES, alias_rows,
    resolve_board_theme_profiles,
)
from stock_harness.market_liquidity import (
    analyze_benchmark_volume_fallback, analyze_market_liquidity,
    stabilize_seat_budget,
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
        "hotspot_shape": {"path": "trend-continuation"},
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
    theme_profile: dict[str, object] | None = None,
) -> dict[str, object]:
    system = BoardHotspotSystem()
    member_snapshot = snapshot or {
        "coverage_ratio": .95, "positive_return_5_ratio": .72,
        "limit_up_count": 2, "broken_up_count": 1,
        "max_limit_up_streak": 2,
        "member_count": 40,
    }
    result = system.execute(ObservationSystemContext(
        observations=[observation],
        prior_scores={BOARD_HOTSPOT_SYSTEM: {"BK001.DC": prior} if prior else {}},
        dependencies={
            "board_hotspot_features": {"BK001.DC": {
                "coverage_state": observation["coverage_state"],
                "metrics": observation["metrics"],
                "member_snapshot": member_snapshot,
            }},
            "board_names": {"BK001.DC": "Board"},
            "market_liquidity_context": {
                "capacity_tier": "low", "direction": "contracting",
                "regime": "low-contracting", "raw_visible_seats": 1,
            },
            "board_capacity_features": {"BK001.DC": {
                "version": "test", "capacity_tier": "small",
                "turnover_capacity_20": 1_000_000,
                "turnover_intensity": 1.2, "member_count": 40,
                "coverage_ratio": .95, "turnover_concentration_hhi": .1,
                "largest_member_share": .2,
            }},
            "board_theme_profiles": {"BK001.DC": theme_profile or {
                "theme_id": "board", "theme_name": "Board",
                "registry_version": "test", "match_method": "test",
            }},
        },
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
    assert execution.error == (
        "missing dependencies: board_hotspot_features, board_names, "
        "market_liquidity_context, board_capacity_features, "
        "board_theme_profiles"
    )


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


def test_taxonomy_only_parent_never_occupies_a_visible_hotspot_seat() -> None:
    first = _execute(_observation())
    parent = _execute(_observation(), first, theme_profile={
        "theme_id": "agriculture", "theme_name": "农业",
        "theme_level": "broad", "match_method": "taxonomy-only",
        "signal_eligible": False,
    })
    assert parent["eligible"] is True
    assert parent["theme_signal_eligible"] is False
    assert parent["radar_visible"] is False


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
    assert len(store.get_market_turnover_proxy(days[-1], sessions=6)) == 6
    capacity = store.calculate_board_capacity_snapshots(days[-1])["BK001.DC"]
    assert capacity["member_count"] == 2
    assert capacity["coverage_ratio"] == 1
    assert capacity["turnover_capacity_20"] > 0
    assert .5 <= capacity["turnover_concentration_hhi"] <= 1
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


def test_hotspot_shape_paths_do_not_require_a_fixed_twenty_day_gain() -> None:
    closes = [100] * 45 + [94, 95, 96, 97, 99, 101]
    bars = _stored_bars("BK001.DC", closes, [100] * 46 + [130] * 5)
    benchmark = _stored_bars("000001.SH", [100] * len(closes), [100] * len(closes))
    feature = extract_board_hotspot_features(bars, benchmark)
    shape = feature["metrics"]["hotspot_shape"]
    assert feature["metrics"]["returns"]["20"] < .03
    assert shape["path"] in {"platform-breakout", "downtrend-reversal"}


def test_market_liquidity_contraction_limits_visible_hotspots_to_one_theme() -> None:
    market = _stored_bars("000001.SH", [100] * 25, [200] * 20 + [140, 130, 120, 110, 100])
    context = analyze_benchmark_volume_fallback(market)
    context.update({
        "capacity_tier": "low", "direction": "contracting",
        "regime": "low-contracting", "raw_visible_seats": 1,
    })

    observations = [_observation(), {**_observation(), "symbol": "BK002.DC"}]
    feature = {
        "coverage_state": "complete", "metrics": observations[0]["metrics"],
        "member_snapshot": {
            "member_count": 40, "coverage_ratio": .95,
            "positive_return_5_ratio": .72, "limit_up_count": 2,
            "broken_up_count": 1, "max_limit_up_streak": 3,
        },
    }
    priors = {
        symbol: {"candidate_streak": 1, "positive_return_5_ratio": .7,
                 "total_score": 55, "hotspot_stage": "trend-emerging",
                 "market_liquidity_regime": "contracting",
                 "market_liquidity_raw_seats": 1,
                 "market_liquidity_seat_streak": 1,
                 "radar_slot_limit": 1}
        for symbol in ("BK001.DC", "BK002.DC")
    }
    results = BoardHotspotSystem().execute(ObservationSystemContext(
        observations=observations,
        prior_scores={BOARD_HOTSPOT_SYSTEM: priors},
        dependencies={
            "board_hotspot_features": {
                "BK001.DC": feature, "BK002.DC": feature,
            },
            "board_names": {"BK001.DC": "Theme A", "BK002.DC": "Theme B"},
            "market_liquidity_context": context,
            "board_capacity_features": {
                symbol: {
                    "capacity_tier": "small" if symbol == "BK001.DC" else "large",
                    "turnover_intensity": 1.1,
                    "member_count": 40, "coverage_ratio": .95,
                    "turnover_concentration_hhi": .1,
                }
                for symbol in ("BK001.DC", "BK002.DC")
            },
            "board_theme_profiles": {
                "BK001.DC": {"theme_id": "theme-a"},
                "BK002.DC": {"theme_id": "theme-b"},
            },
        },
    )).results
    assert sum(bool(item["radar_visible"]) for item in results) == 1
    assert {item["radar_slot_limit"] for item in results} == {1}
    assert next(item for item in results if item["radar_visible"])["symbol"] == "BK001.DC"
    assert next(item for item in results if item["symbol"] == "BK002.DC")[
        "capacity_market_preferred"
    ] is False


def test_board_capacity_classification_and_market_fit_are_separate() -> None:
    snapshots = {
        "B1": {"turnover_capacity_20": 10, "turnover_recent_5": 12,
               "turnover_intensity": 1.2, "member_count": 20,
               "coverage_ratio": .9, "turnover_concentration_hhi": .12},
        "B2": {"turnover_capacity_20": 40, "turnover_recent_5": 40,
               "turnover_intensity": 1.0, "member_count": 30,
               "coverage_ratio": .9, "turnover_concentration_hhi": .12},
        "B3": {"turnover_capacity_20": 100, "turnover_recent_5": 110,
               "turnover_intensity": 1.1, "member_count": 40,
               "coverage_ratio": .9, "turnover_concentration_hhi": .12},
        "B4": {"turnover_capacity_20": 400, "turnover_recent_5": 500,
               "turnover_intensity": 1.25, "member_count": 60,
               "coverage_ratio": .9, "turnover_concentration_hhi": .12},
    }
    profiles = classify_board_capacities(
        snapshots, {symbol: symbol for symbol in snapshots},
    )
    assert profiles["B1"]["capacity_tier"] == "micro"
    assert profiles["B4"]["capacity_tier"] == "mega"
    assert market_capacity_fit(
        profiles["B4"], {"capacity_tier": "low"},
    )["market_compatible"] is False
    assert market_capacity_fit(
        profiles["B4"], {"capacity_tier": "low"},
    )["compatible"] is True
    assert market_capacity_fit(
        profiles["B4"], {"capacity_tier": "high"},
    )["compatible"] is True


def test_versioned_board_theme_registry_keeps_leaf_and_parent_distinct() -> None:
    store = SQLiteMarketDataStore(":memory:")
    profiles = store.resolve_board_theme_profiles({
        "CPO.DC": "CPO概念", "OPTICAL.DC": "光模块",
        "PCB.DC": "PCB", "PACK.DC": "封装测试",
        "ADVANCED.DC": "先进封装", "POWER.DC": "电力", "SEED.DC": "种业",
        "GMO.DC": "转基因", "OTHER.DC": "酒店餐饮",
    })
    assert profiles["CPO.DC"]["registry_version"] == BOARD_THEME_REGISTRY_VERSION
    assert profiles["CPO.DC"]["theme_id"] == "cpo"
    assert profiles["PCB.DC"]["theme_id"] == "pcb"
    assert profiles["CPO.DC"]["parent_theme_id"] == "hardware-technology"
    assert profiles["OPTICAL.DC"]["theme_id"] == "optical-module"
    assert profiles["OPTICAL.DC"]["theme_id"] != profiles["CPO.DC"]["theme_id"]
    assert profiles["PACK.DC"]["theme_id"] == "semiconductor-packaging"
    assert profiles["ADVANCED.DC"]["theme_id"] == "advanced-packaging"
    assert profiles["SEED.DC"]["theme_id"] == "seed-industry"
    assert profiles["GMO.DC"]["theme_id"] == "genetically-modified"
    assert profiles["POWER.DC"]["theme_id"] == "power"
    assert profiles["POWER.DC"]["match_method"] == "taxonomy-only"
    assert profiles["POWER.DC"]["signal_eligible"] is False
    assert profiles["SEED.DC"]["signal_eligible"] is True
    assert profiles["OTHER.DC"]["match_method"] == "canonical-name-fallback"
    assert resolve_board_theme_profiles({"A": "医疗研发外包"})["A"][
        "theme_id"
    ] == "cro"
    versions = store.list_board_theme_registry_versions()
    assert versions == [{
        "registry_version": BOARD_THEME_REGISTRY_VERSION,
        "theme_count": len({theme.theme_id for theme in THEMES}),
        "alias_count": len(alias_rows()), "current": True,
    }]
    store.close()


def test_absolute_liquidity_capacity_caps_relative_expansion() -> None:
    low = analyze_market_liquidity(
        [1.8e12] * 20 + [1.9e12, 1.95e12, 2e12, 2.05e12, 2.1e12]
    )
    assert low["capacity_tier"] == "low"
    assert low["direction"] == "expanding"
    assert low["raw_visible_seats"] == 1

    high = analyze_market_liquidity(
        [3.2e12] * 20 + [3.3e12, 3.4e12, 3.5e12, 3.6e12, 3.7e12]
    )
    assert high["capacity_tier"] == "high"
    assert high["raw_visible_seats"] == 5

    seats, streak = stabilize_seat_budget(high, {
        "market_liquidity_raw_seats": 1, "market_liquidity_seat_streak": 2,
        "radar_slot_limit": 1,
    })
    assert (seats, streak) == (1, 1)
    seats, streak = stabilize_seat_budget(high, {
        "market_liquidity_raw_seats": 5, "market_liquidity_seat_streak": 1,
        "radar_slot_limit": 1,
    })
    assert (seats, streak) == (5, 2)


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


def test_visible_hotspot_evaluation_merges_provider_alias_rotation() -> None:
    confirmation = {
        "metrics": {
            "returns": {"5": .06, "20": .12},
            "relative_strength": {"20": .05},
            "recent_volume_ratio_5_5": 1.1,
        },
        "member_snapshot": {
            "positive_return_5_ratio": .7, "limit_up_count": 1,
        },
    }
    dates = [f"2026-01-0{index + 1}" for index in range(4)]
    timelines = {
        "A": [{"effective_date": day, "symbol": "A",
               "radar_visible": index == 0, "feature": {}}
              for index, day in enumerate(dates)],
        "B": [{"effective_date": day, "symbol": "B",
               "radar_visible": index == 1,
               "feature": confirmation if index >= 2 else {}}
              for index, day in enumerate(dates)],
    }
    result = evaluate_hotspot_timelines(
        timelines, names={"A": "Same Theme", "B": "Same Theme"},
        visible_only=True,
    )
    assert result["signal_events"] == 1
    assert result["true_signal_events"] == 1
    assert result["precision"] == 1
