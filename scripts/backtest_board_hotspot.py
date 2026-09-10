"""Replay the board-hotspot plugin on bounded named board cases."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from statistics import median
import time

from stock_harness.config import load_runtime_settings
from stock_harness.board_hotspot_evaluation import (
    evaluate_hotspot_timelines, is_objective_confirmation,
    objective_confirmation_failures,
)
from stock_harness.board_hotspot_features import (
    extract_board_hotspot_features, hotspot_feature_value,
)
from stock_harness.board_capacity import classify_board_capacities
from stock_harness.board_hotspot_replay import BoardAggregatePool
from stock_harness.market_liquidity import (
    analyze_benchmark_volume_fallback, analyze_market_liquidity,
)
from stock_harness.hotspot_wave import project_hotspot_waves
from stock_harness.observation_systems import (
    BOARD_HOTSPOT_LEADING_SYSTEM,
    BOARD_HOTSPOT_SYSTEM,
    BoardHotspotLeadingSystem,
    BoardHotspotSystem,
    ObservationSystemContext,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--queries", default="电力,医药,农业,种业,硬件")
    parser.add_argument("--max-boards-per-query", type=int, default=12)
    parser.add_argument("--all-boards", action="store_true")
    parser.add_argument("--workers", type=int, choices=(1, 4), default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = load_runtime_settings(args.provider_config, args.storage_config)
    store = SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )
    try:
        result = replay(
            store, args.start_date, args.end_date,
            [value.strip() for value in args.queries.split(",") if value.strip()],
            args.max_boards_per_query, all_boards=args.all_boards,
            parallel_workers=args.workers,
        )
    finally:
        store.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))


def replay(
    store: SQLiteMarketDataStore, start: date, end: date,
    queries: list[str], max_boards_per_query: int, *, all_boards: bool = False,
    parallel_workers: int = 1,
) -> dict[str, object]:
    setup_started = time.perf_counter()
    boards: dict[str, dict[str, object]] = {}
    query_symbols: dict[str, list[str]] = {}
    if all_boards:
        for classification in ("concept", "industry"):
            offset = 0
            while True:
                page = store.search_instruments(
                    classification=classification, active=True, limit=500,
                    offset=offset,
                )
                boards.update((str(item["symbol"]), {
                    **item, "signal_classification": classification,
                }) for item in page)
                if len(page) < 500:
                    break
                offset += len(page)
    else:
        for query in queries:
            matches: dict[str, dict[str, object]] = {}
            for classification in ("concept", "industry"):
                for item in store.search_instruments(
                    query=query, classification=classification, active=True,
                    limit=max_boards_per_query, offset=0,
                ):
                    matches[str(item["symbol"])] = item
            selected = sorted(matches)[:max_boards_per_query]
            query_symbols[query] = selected
            boards.update((symbol, matches[symbol]) for symbol in selected)
    print(
        f"hotspot_replay_setup boards={len(boards)} "
        f"elapsed_s={time.perf_counter() - setup_started:.1f}", flush=True,
    )
    benchmark = store.get_daily_bars("000001.SH", start, end)
    dates = [bar.trade_date for bar in benchmark]
    all_benchmark = store.get_daily_bars("000001.SH", None, end)
    first_benchmark_index = next(
        index for index, bar in enumerate(all_benchmark)
        if bar.trade_date >= start
    )
    history_start = all_benchmark[max(0, first_benchmark_index - 65)].trade_date
    board_bars = store.get_daily_bars_range_many(
        list(boards), history_start, end,
    )
    market_turnover_rows = store.get_market_turnover_range(history_start, end)
    print(
        f"hotspot_replay_setup bars={sum(len(value) for value in board_bars.values())} "
        f"elapsed_s={time.perf_counter() - setup_started:.1f}", flush=True,
    )
    by_symbol_date = {
        symbol: {bar.trade_date: index for index, bar in enumerate(bars)}
        for symbol, bars in board_bars.items()
    }
    benchmark_by_date = {
        bar.trade_date: index for index, bar in enumerate(all_benchmark)
    }
    print(
        f"hotspot_replay_setup indexes=ready "
        f"elapsed_s={time.perf_counter() - setup_started:.1f}", flush=True,
    )
    system = BoardHotspotSystem()
    leading_system = BoardHotspotLeadingSystem()
    prior: dict[str, dict[str, object]] = {}
    recent: dict[str, list[dict[str, object]]] = {}
    leading_prior: dict[str, dict[str, object]] = {}
    leading_recent: dict[str, list[dict[str, object]]] = {}
    timelines: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in boards}
    leading_timelines: dict[str, list[dict[str, object]]] = {
        symbol: [] for symbol in boards
    }
    stage_counts: dict[str, int] = {}
    visible_counts: list[int] = []
    leading_visible_counts: list[int] = []
    leading_state_counts: dict[str, int] = {}
    raw_preheat_counts: list[int] = []
    visible_preheat_counts: list[int] = []
    active_wave_counts: list[int] = []
    wave_sequences: dict[str, int] = {}
    prior_waves: list[dict[str, object]] = []
    waves: dict[str, dict[str, object]] = {}
    visible_diagnostics: dict[tuple[str, str], dict[str, object]] = {}
    leading_visible_diagnostics: dict[tuple[str, str], dict[str, object]] = {}
    market_regime_days: dict[str, int] = {}
    board_names = {
        symbol: str(item.get("name") or symbol) for symbol, item in boards.items()
    }
    board_themes = store.resolve_board_theme_profiles(board_names)
    print(
        f"hotspot_replay_setup themes={len(board_themes)} "
        f"elapsed_s={time.perf_counter() - setup_started:.1f}", flush=True,
    )
    aggregate_pool = BoardAggregatePool(store, parallel_workers)
    replay_started = time.perf_counter()
    for date_index, effective in enumerate(dates, 1):
        snapshots, breadth_snapshots, capacity_snapshots = (
            aggregate_pool.calculate(effective)
        )
        turnover = [
            value for trade_date, value in market_turnover_rows
            if trade_date <= effective
        ][-25:]
        board_capacities = classify_board_capacities(
            capacity_snapshots, board_names,
            board_themes,
        )
        market_liquidity = (
            analyze_market_liquidity(turnover)
            if len(turnover) >= 25 else None
        )
        observations = []
        features = {}
        for symbol, bars in board_bars.items():
            index = by_symbol_date[symbol].get(effective)
            benchmark_index = benchmark_by_date.get(effective)
            if index is None or benchmark_index is None:
                continue
            feature = extract_board_hotspot_features(
                bars[max(0, index - 65):index + 1],
                all_benchmark[max(0, benchmark_index - 65):benchmark_index + 1],
                breadth_snapshot=breadth_snapshots.get(symbol),
                member_snapshot=snapshots.get(symbol),
            )
            features[symbol] = feature
            observations.append({"symbol": symbol})
        execution = system.execute(ObservationSystemContext(
            observations=observations,
            prior_scores={BOARD_HOTSPOT_SYSTEM: prior},
            recent_scores={BOARD_HOTSPOT_SYSTEM: recent},
            dependencies={
                "board_hotspot_features": features,
                "board_names": board_names,
                "market_liquidity_context": market_liquidity or (
                    analyze_benchmark_volume_fallback(
                        all_benchmark[:benchmark_index + 1]
                    )
                ),
                "board_capacity_features": board_capacities,
                "board_theme_profiles": board_themes,
            },
        ))
        leading_execution = leading_system.execute(ObservationSystemContext(
            observations=observations,
            prior_scores={BOARD_HOTSPOT_LEADING_SYSTEM: leading_prior},
            recent_scores={BOARD_HOTSPOT_LEADING_SYSTEM: leading_recent},
            dependencies={
                "board_hotspot_features": features,
                "board_names": board_names,
                "market_liquidity_context": market_liquidity or (
                    analyze_benchmark_volume_fallback(
                        all_benchmark[:benchmark_index + 1]
                    )
                ),
                "board_capacity_features": board_capacities,
                "board_theme_profiles": board_themes,
            },
        ))
        prior = {str(item["symbol"]): item for item in execution.results}
        recent = {
            symbol: [value, *recent.get(symbol, [])][:5]
            for symbol, value in prior.items()
        }
        leading_prior = {
            str(item["symbol"]): _compact_leading_history(item, effective)
            for item in leading_execution.results
        }
        leading_recent = {
            symbol: [value, *leading_recent.get(symbol, [])][:5]
            for symbol, value in leading_prior.items()
        }
        wave_snapshots = project_hotspot_waves(
            execution.results, prior_waves, effective, wave_sequences,
        )
        prior_waves = [
            item for item in wave_snapshots if item["status"] == "active"
        ]
        active_wave_counts.append(len(prior_waves))
        for item in wave_snapshots:
            theme_id = str(item["theme_id"])
            wave_sequences[theme_id] = max(
                wave_sequences.get(theme_id, 0), int(item["wave_sequence"]),
            )
            wave = waves.setdefault(str(item["wave_id"]), {
                "wave_id": item["wave_id"], "theme_id": theme_id,
                "theme_name": item["theme_name"],
                "wave_sequence": item["wave_sequence"],
                "started_on": item["started_on"], "ended_on": None,
                "session_count": 0, "peak_score": 0.0, "stages": [],
            })
            wave["ended_on"] = item["ended_on"] or wave["ended_on"]
            wave["session_count"] = item["session_count"]
            wave["peak_score"] = item["peak_score"]
            stages = wave["stages"]
            if isinstance(stages, list) and item["stage"] not in stages:
                stages.append(item["stage"])
        visible_counts.append(sum(bool(item.get("radar_visible"))
                                  for item in execution.results))
        leading_visible_counts.append(sum(
            bool(item.get("leading_visible"))
            for item in leading_execution.results
        ))
        preheat_stages = {"leader-ignited", "trend-emerging", "breadth-expanding"}
        raw_preheat_counts.append(sum(
            str(item.get("hotspot_stage")) in preheat_stages
            for item in execution.results
        ))
        visible_preheat_counts.append(sum(
            bool(item.get("radar_visible"))
            and str(item.get("hotspot_stage")) in preheat_stages
            for item in execution.results
        ))
        regime = str(execution.results[0].get("market_liquidity_regime") or "unknown") \
            if execution.results else "unknown"
        market_regime_days[regime] = market_regime_days.get(regime, 0) + 1
        for item in execution.results:
            result_symbol = str(item["symbol"])
            result_bar_index = by_symbol_date[result_symbol].get(effective)
            stage = str(item["hotspot_stage"])
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            timeline_item = {
                key: item.get(key) for key in (
                    "symbol", "hotspot_stage", "total_score", "raw_score",
                    "score_direction", "candidate_streak", "eligible",
                    "radar_visible", "radar_rank", "market_liquidity_regime",
                    "board_capacity_tier", "board_turnover_intensity",
                    "capacity_compatible", "capacity_market_preferred",
                    "capacity_fit_score", "visibility_score",
                    "setup_path",
                    "hotspot_window_state", "hotspot_window_eligible",
                    "hotspot_window_observed_sessions",
                    "hotspot_window_qualified_sessions",
                    "hotspot_window_evidence_score",
                    "hotspot_window_evidence_delta",
                    "hotspot_window_fit_score",
                    "limit_up_count", "max_limit_up_streak", "summary",
                )
            } | {
                "effective_date": effective.isoformat(),
                "_close": (
                    board_bars[result_symbol][result_bar_index].close
                    if result_bar_index is not None else None
                ),
                "_daily_return": (
                    board_bars[result_symbol][result_bar_index].close
                    / board_bars[result_symbol][result_bar_index - 1].close - 1
                    if result_bar_index is not None and result_bar_index > 0
                    and board_bars[result_symbol][result_bar_index - 1].close > 0
                    else None
                ),
                "_objective_confirmation": is_objective_confirmation(
                    features.get(str(item["symbol"]), {})
                ),
                "_objective_failures": objective_confirmation_failures(
                    features.get(str(item["symbol"]), {})
                ),
            }
            if not all_boards:
                timeline_item["feature"] = features.get(str(item["symbol"]), {})
            timelines[str(item["symbol"])].append(timeline_item)
            if bool(item.get("radar_visible")):
                symbol = str(item["symbol"])
                feature = features.get(symbol, {})
                visible_diagnostics[(symbol, effective.isoformat())] = {
                    "symbol": symbol,
                    "name": board_names.get(symbol, symbol),
                    "effective_date": effective.isoformat(),
                    "theme_id": item.get("canonical_theme"),
                    "theme_name": item.get("theme_name"),
                    "hotspot_stage": item.get("hotspot_stage"),
                    "setup_path": item.get("setup_path"),
                    "hotspot_window_state": item.get("hotspot_window_state"),
                    "hotspot_window_observed_sessions": item.get(
                        "hotspot_window_observed_sessions"
                    ),
                    "hotspot_window_qualified_sessions": item.get(
                        "hotspot_window_qualified_sessions"
                    ),
                    "hotspot_window_evidence_score": item.get(
                        "hotspot_window_evidence_score"
                    ),
                    "hotspot_window_evidence_delta": item.get(
                        "hotspot_window_evidence_delta"
                    ),
                    "hotspot_window_fit_score": item.get(
                        "hotspot_window_fit_score"
                    ),
                    "hotspot_window_failure_reasons": item.get(
                        "hotspot_window_failure_reasons"
                    ),
                    "total_score": item.get("total_score"),
                    "raw_score": item.get("raw_score"),
                    "score_delta": item.get("score_delta"),
                    "candidate_streak": item.get("candidate_streak"),
                    "components": item.get("components"),
                    "return_5": hotspot_feature_value(
                        feature, ("metrics", "returns", "5"),
                    ),
                    "return_20": hotspot_feature_value(
                        feature, ("metrics", "returns", "20"),
                    ),
                    "relative_strength_5": hotspot_feature_value(
                        feature, ("metrics", "relative_strength", "5"),
                    ),
                    "relative_strength_20": hotspot_feature_value(
                        feature, ("metrics", "relative_strength", "20"),
                    ),
                    "volume_ratio_20": hotspot_feature_value(
                        feature, ("metrics", "volume_ratio20"),
                    ),
                    "volume_persistence_5": hotspot_feature_value(
                        feature, ("metrics", "recent_volume_ratio_5_5"),
                    ),
                    "breadth": hotspot_feature_value(
                        feature, ("metrics", "board_breadth", "breadth"),
                    ),
                    "positive_return_5_ratio": hotspot_feature_value(
                        feature, ("member_snapshot", "positive_return_5_ratio"),
                    ),
                    "limit_up_count": item.get("limit_up_count"),
                    "broken_up_count": item.get("broken_up_count"),
                    "max_limit_up_streak": item.get("max_limit_up_streak"),
                    "consecutive_limit_up_count": item.get(
                        "consecutive_limit_up_count"
                    ),
                    "active_limit_up_members_5": item.get(
                        "active_limit_up_members_5"
                    ),
                    "failed_limit_ratio": item.get("failed_limit_ratio"),
                    "board_capacity_tier": item.get("board_capacity_tier"),
                    "board_turnover_intensity": item.get("board_turnover_intensity"),
                    "capacity_fit_score": item.get("capacity_fit_score"),
                    "visibility_score": item.get("visibility_score"),
                    "market_liquidity_regime": item.get("market_liquidity_regime"),
                    "objective_confirmation_on_signal_date": (
                        timeline_item["_objective_confirmation"]
                    ),
                }
        for item in leading_execution.results:
            result_symbol = str(item["symbol"])
            result_bar_index = by_symbol_date[result_symbol].get(effective)
            state = str(item.get("leading_state") or "invalidated")
            leading_state_counts[state] = leading_state_counts.get(state, 0) + 1
            leading_timelines[result_symbol].append({
                "symbol": item.get("symbol"),
                "total_score": item.get("total_score"),
                "leading_state": item.get("leading_state"),
                "leading_visible": item.get("leading_visible"),
                "effective_date": effective.isoformat(),
                "radar_visible": bool(item.get("leading_visible")),
                "visibility_score": item.get("leading_visibility_score"),
                "hotspot_stage": (
                    "trend-emerging"
                    if item.get("leading_state") == "strengthening" else
                    "hotspot-confirmed"
                    if item.get("leading_state") == "launch-confirmed" else
                    "failed"
                ),
                "candidate_streak": item.get("leading_streak"),
                "_close": (
                    board_bars[result_symbol][result_bar_index].close
                    if result_bar_index is not None else None
                ),
                "_daily_return": (
                    board_bars[result_symbol][result_bar_index].close
                    / board_bars[result_symbol][result_bar_index - 1].close - 1
                    if result_bar_index is not None and result_bar_index > 0
                    and board_bars[result_symbol][result_bar_index - 1].close > 0
                    else None
                ),
                "_objective_confirmation": is_objective_confirmation(
                    features.get(result_symbol, {})
                ),
                "_objective_failures": objective_confirmation_failures(
                    features.get(result_symbol, {})
                ),
            })
            if bool(item.get("leading_visible")):
                feature = features.get(result_symbol, {})
                leading_visible_diagnostics[(
                    result_symbol, effective.isoformat(),
                )] = {
                    "symbol": result_symbol,
                    "name": board_names.get(result_symbol, result_symbol),
                    "effective_date": effective.isoformat(),
                    "theme_name": item.get("theme_name"),
                    "leading_state": item.get("leading_state"),
                    "total_score": item.get("total_score"),
                    "leading_acceleration_count": item.get(
                        "leading_acceleration_count"
                    ),
                    "leading_dimensions": item.get("leading_dimensions"),
                    "leading_deltas": item.get("leading_deltas"),
                    "leading_trajectory": item.get("leading_trajectory"),
                    "setup_path": item.get("setup_path"),
                    "theme_match_method": item.get("theme_match_method"),
                    "theme_signal_eligible": item.get("theme_signal_eligible"),
                    "market_liquidity_regime": item.get(
                        "market_liquidity_regime"
                    ),
                    "market_admission_eligible": item.get(
                        "market_admission_eligible"
                    ),
                    "market_admission_reasons": item.get(
                        "market_admission_reasons"
                    ),
                    "board_capacity_tier": item.get("board_capacity_tier"),
                    "leading_visible_streak": item.get(
                        "leading_visible_streak"
                    ),
                    "signal_classification": boards.get(
                        result_symbol, {}
                    ).get("signal_classification"),
                    "return_5": hotspot_feature_value(
                        feature, ("metrics", "returns", "5"),
                    ),
                    "return_20": hotspot_feature_value(
                        feature, ("metrics", "returns", "20"),
                    ),
                    "relative_strength_5": hotspot_feature_value(
                        feature, ("metrics", "relative_strength", "5"),
                    ),
                    "relative_strength_20": hotspot_feature_value(
                        feature, ("metrics", "relative_strength", "20"),
                    ),
                    "volume_persistence_5": hotspot_feature_value(
                        feature, ("metrics", "recent_volume_ratio_5_5"),
                    ),
                    "positive_return_5_ratio": hotspot_feature_value(
                        feature, ("member_snapshot", "positive_return_5_ratio"),
                    ),
                    "limit_up_count": item.get("limit_up_count"),
                    "broken_up_count": item.get("broken_up_count"),
                    "max_limit_up_streak": item.get("max_limit_up_streak"),
                    "consecutive_limit_up_count": item.get(
                        "consecutive_limit_up_count"
                    ),
                    "active_limit_up_members_5": item.get(
                        "active_limit_up_members_5"
                    ),
                    "failed_limit_ratio": item.get("failed_limit_ratio"),
                    "breakout_20_atr": hotspot_feature_value(
                        feature, ("metrics", "hotspot_shape", "breakout20_atr"),
                    ),
                    "range_compression_5_20": hotspot_feature_value(
                        feature,
                        ("metrics", "hotspot_shape", "range_compression_5_20"),
                    ),
                    "extension_from_ma20_atr": hotspot_feature_value(
                        feature,
                        ("metrics", "hotspot_shape", "extension_from_ma20_atr"),
                    ),
                }
        if date_index % 10 == 0 or date_index == len(dates):
            print(
                f"hotspot_replay_progress {date_index}/{len(dates)} "
                f"date={effective.isoformat()} "
                f"elapsed_s={time.perf_counter() - replay_started:.1f}",
                flush=True,
            )
    aggregate_pool.close()
    first_detections = []
    eligible_board_count = 0
    for symbol, events in timelines.items():
        visible_events = [item for item in events if item["hotspot_stage"] != "failed"]
        if not visible_events:
            continue
        first = visible_events[0]
        first_eligible = next((item for item in visible_events if item["eligible"]), None)
        eligible_board_count += int(first_eligible is not None)
        first_detections.append({
            "symbol": symbol, "name": boards[symbol].get("name"),
            "first_date": first["effective_date"],
            "first_stage": first["hotspot_stage"],
            "first_score": first["total_score"],
            "peak_score": max(float(item["total_score"]) for item in visible_events),
            "event_days": len(visible_events),
            "first_eligible_date": (
                first_eligible["effective_date"] if first_eligible else None
            ),
            "first_eligible_stage": (
                first_eligible["hotspot_stage"] if first_eligible else None
            ),
        })
    scores = [float(item["first_score"]) for item in first_detections]
    evaluation = evaluate_hotspot_timelines(
        timelines, names={symbol: str(item.get("name") or symbol)
                          for symbol, item in boards.items()},
    )
    visible_evaluation = evaluate_hotspot_timelines(
        timelines, names={symbol: str(item.get("name") or symbol)
                          for symbol, item in boards.items()}, visible_only=True,
        theme_profiles=board_themes,
    )
    leading_evaluation = evaluate_hotspot_timelines(
        leading_timelines,
        names={symbol: str(item.get("name") or symbol)
               for symbol, item in boards.items()},
        visible_only=True, theme_profiles=board_themes,
    )
    wave_rows = sorted(waves.values(), key=lambda item: (
        str(item["started_on"]), str(item["theme_id"]), int(item["wave_sequence"]),
    ))
    confirmed_waves = [
        item for item in wave_rows
        if any(stage in {"confirmed", "advancing", "reaccelerating"}
               for stage in item["stages"])
    ]
    visible_event_diagnostics = []
    for event in visible_evaluation["events"]:
        diagnostic = visible_diagnostics.get((
            str(event["symbol"]), str(event["signal_date"]),
        ), {})
        visible_event_diagnostics.append({
            **diagnostic,
            "cluster_key": event["cluster_key"],
            "confirmation_date": event["confirmation_date"],
            "lead_sessions": event["lead_sessions"],
            "timing_class": event["timing_class"],
            "signal_confirmation_failures": event[
                "signal_confirmation_failures"
            ],
            "next_three_failure_counts": event["next_three_failure_counts"],
            "forward_return_5": event["forward_return_5"],
            "max_forward_return_5": event["max_forward_return_5"],
            "max_adverse_excursion_5": event["max_adverse_excursion_5"],
            "forward_return_10": event["forward_return_10"],
            "max_forward_return_10": event["max_forward_return_10"],
            "max_adverse_excursion_10": event["max_adverse_excursion_10"],
            "confirmed_within_window": bool(event["confirmation_date"]),
        })
    leading_event_diagnostics = []
    for event in leading_evaluation["events"]:
        diagnostic = leading_visible_diagnostics.get((
            str(event["symbol"]), str(event["signal_date"]),
        ), {})
        leading_event_diagnostics.append({
            **diagnostic,
            "cluster_key": event["cluster_key"],
            "confirmation_date": event["confirmation_date"],
            "lead_sessions": event["lead_sessions"],
            "timing_class": event["timing_class"],
            "signal_confirmation_failures": event[
                "signal_confirmation_failures"
            ],
            "next_three_failure_counts": event["next_three_failure_counts"],
            "max_forward_return_5": event["max_forward_return_5"],
            "max_adverse_excursion_5": event["max_adverse_excursion_5"],
            "max_forward_return_10": event["max_forward_return_10"],
            "max_adverse_excursion_10": event["max_adverse_excursion_10"],
        })
    result = {
        "summary": {
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "trading_dates": len(dates), "board_count": len(boards),
            "detected_board_count": len(first_detections),
            "eligible_board_count": eligible_board_count,
            "median_first_score": round(median(scores), 2) if scores else None,
            "stage_counts": stage_counts,
            "algorithm_version": system.version,
            "leading_algorithm_version": leading_system.version,
            "parallel_workers": parallel_workers,
            "aggregate_workers": aggregate_pool.worker_count,
            "online_features_are_causal": True,
            "evaluation": {key: value for key, value in evaluation.items()
                           if key != "events"},
            "visible_evaluation": {
                key: value for key, value in visible_evaluation.items()
                if key != "events"
            },
            "leading_evaluation": {
                key: value for key, value in leading_evaluation.items()
                if key != "events"
            },
            "leading_state_counts": leading_state_counts,
            "leading_visible_theme_days": sum(leading_visible_counts),
            "median_leading_visible_themes_per_day": (
                median(leading_visible_counts) if leading_visible_counts else None
            ),
            "max_leading_visible_themes_per_day": max(
                leading_visible_counts, default=0,
            ),
            "visible_theme_days": sum(visible_counts),
            "median_visible_themes_per_day": (
                median(visible_counts) if visible_counts else None
            ),
            "max_visible_themes_per_day": max(visible_counts, default=0),
            "median_raw_preheat_themes_per_day": (
                median(raw_preheat_counts) if raw_preheat_counts else None
            ),
            "max_raw_preheat_themes_per_day": max(raw_preheat_counts, default=0),
            "median_visible_preheat_themes_per_day": (
                median(visible_preheat_counts) if visible_preheat_counts else None
            ),
            "max_visible_preheat_themes_per_day": max(
                visible_preheat_counts, default=0,
            ),
            "wave_count": len(wave_rows),
            "internally_confirmed_wave_count": len(confirmed_waves),
            "internal_wave_confirmation_rate": round(
                len(confirmed_waves) / len(wave_rows), 4,
            ) if wave_rows else None,
            "median_active_waves_per_day": (
                median(active_wave_counts) if active_wave_counts else None
            ),
            "max_active_waves_per_day": max(active_wave_counts, default=0),
            "market_regime_days": market_regime_days,
        },
        "queries": query_symbols,
        "first_detections": sorted(first_detections, key=lambda item: (
            str(item["first_date"]), -float(item["peak_score"]), str(item["symbol"]),
        )),
        "evaluation_events": evaluation["events"],
        "visible_evaluation_events": visible_evaluation["events"],
        "leading_evaluation_events": leading_evaluation["events"],
        "leading_event_diagnostics": leading_event_diagnostics,
        "visible_event_diagnostics": visible_event_diagnostics,
        "waves": wave_rows,
    }
    if not all_boards:
        result["timelines"] = timelines
        result["leading_timelines"] = leading_timelines
    return result


def _compact_leading_history(
    item: dict[str, object], effective: date,
) -> dict[str, object]:
    """Retain only fields needed by the next causal leading-radar session."""
    return {key: item.get(key) for key in (
        "symbol", "entity_key", "eligible", "total_score", "grade", "rank",
        "ranking_universe_digest", "leading_state", "leading_streak",
        "leading_visible", "leading_visible_streak", "leading_sample",
        "leading_trajectory", "score_direction",
        "market_liquidity_raw_seats", "market_liquidity_seat_streak",
        "leading_slot_limit", "radar_slot_limit",
    )} | {"effective_date": effective.isoformat()}


if __name__ == "__main__":
    main()
