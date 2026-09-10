"""Replay the board-hotspot plugin on bounded named board cases."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
from statistics import median

from stock_harness.config import load_runtime_settings
from stock_harness.board_hotspot_evaluation import evaluate_hotspot_timelines
from stock_harness.board_hotspot_features import extract_board_hotspot_features
from stock_harness.board_capacity import classify_board_capacities
from stock_harness.market_liquidity import (
    analyze_benchmark_volume_fallback, analyze_market_liquidity,
)
from stock_harness.observation_systems import (
    BOARD_HOTSPOT_SYSTEM,
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
        )
    finally:
        store.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))


def replay(
    store: SQLiteMarketDataStore, start: date, end: date,
    queries: list[str], max_boards_per_query: int, *, all_boards: bool = False,
) -> dict[str, object]:
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
    benchmark = store.get_daily_bars("000001.SH", start, end)
    dates = [bar.trade_date for bar in benchmark]
    all_benchmark = store.get_daily_bars("000001.SH", None, end)
    history_start = start - timedelta(days=60)
    board_bars = {
        symbol: store.get_daily_bars(symbol, history_start, end) for symbol in boards
    }
    by_symbol_date = {
        symbol: {bar.trade_date: index for index, bar in enumerate(bars)}
        for symbol, bars in board_bars.items()
    }
    benchmark_by_date = {
        bar.trade_date: index for index, bar in enumerate(all_benchmark)
    }
    system = BoardHotspotSystem()
    prior: dict[str, dict[str, object]] = {}
    timelines: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in boards}
    stage_counts: dict[str, int] = {}
    visible_counts: list[int] = []
    market_regime_days: dict[str, int] = {}
    board_names = {
        symbol: str(item.get("name") or symbol) for symbol, item in boards.items()
    }
    board_themes = store.resolve_board_theme_profiles(board_names)
    for effective in dates:
        snapshots = store.calculate_board_hotspot_snapshots(effective)
        breadth_snapshots = store.calculate_board_breadth_snapshots(effective)
        turnover = store.get_market_turnover_proxy(effective)
        board_capacities = classify_board_capacities(
            store.calculate_board_capacity_snapshots(effective), board_names,
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
                bars[:index + 1], all_benchmark[:benchmark_index + 1],
                breadth_snapshot=breadth_snapshots.get(symbol),
                member_snapshot=snapshots.get(symbol),
            )
            features[symbol] = feature
            observations.append({"symbol": symbol})
        execution = system.execute(ObservationSystemContext(
            observations=observations,
            prior_scores={BOARD_HOTSPOT_SYSTEM: prior},
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
        visible_counts.append(sum(bool(item.get("radar_visible"))
                                  for item in execution.results))
        regime = str(execution.results[0].get("market_liquidity_regime") or "unknown") \
            if execution.results else "unknown"
        market_regime_days[regime] = market_regime_days.get(regime, 0) + 1
        for item in execution.results:
            stage = str(item["hotspot_stage"])
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            timelines[str(item["symbol"])].append({
                key: item.get(key) for key in (
                    "symbol", "hotspot_stage", "total_score", "raw_score",
                    "score_direction", "candidate_streak", "eligible",
                    "radar_visible", "radar_rank", "market_liquidity_regime",
                    "board_capacity_tier", "board_turnover_intensity",
                    "capacity_compatible", "capacity_market_preferred",
                    "capacity_fit_score", "visibility_score",
                    "setup_path",
                    "limit_up_count", "max_limit_up_streak", "summary",
                )
            } | {
                "effective_date": effective.isoformat(),
                "feature": features.get(str(item["symbol"]), {}),
            })
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
    result = {
        "summary": {
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "trading_dates": len(dates), "board_count": len(boards),
            "detected_board_count": len(first_detections),
            "eligible_board_count": eligible_board_count,
            "median_first_score": round(median(scores), 2) if scores else None,
            "stage_counts": stage_counts,
            "algorithm_version": system.version,
            "online_features_are_causal": True,
            "evaluation": {key: value for key, value in evaluation.items()
                           if key != "events"},
            "visible_evaluation": {
                key: value for key, value in visible_evaluation.items()
                if key != "events"
            },
            "visible_theme_days": sum(visible_counts),
            "median_visible_themes_per_day": (
                median(visible_counts) if visible_counts else None
            ),
            "max_visible_themes_per_day": max(visible_counts, default=0),
            "market_regime_days": market_regime_days,
        },
        "queries": query_symbols,
        "first_detections": sorted(first_detections, key=lambda item: (
            str(item["first_date"]), -float(item["peak_score"]), str(item["symbol"]),
        )),
        "evaluation_events": evaluation["events"],
        "visible_evaluation_events": visible_evaluation["events"],
    }
    if not all_boards:
        result["timelines"] = timelines
    return result


if __name__ == "__main__":
    main()
