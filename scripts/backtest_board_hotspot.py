"""Replay the board-hotspot plugin on bounded named board cases."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from statistics import median

from stock_harness.config import load_runtime_settings
from stock_harness.daily_signal_analysis import analyze_daily_series
from stock_harness.observation_systems import (
    BOARD_HOTSPOT_SYSTEM,
    BoardHotspotSystem,
    ObservationSystemContext,
)
from stock_harness.review_scoring import default_scorer_registry
from stock_harness.sqlite_store import SQLiteMarketDataStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--queries", default="电力,医药,农业,种业,硬件")
    parser.add_argument("--max-boards-per-query", type=int, default=12)
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
            args.max_boards_per_query,
        )
    finally:
        store.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))


def replay(
    store: SQLiteMarketDataStore, start: date, end: date,
    queries: list[str], max_boards_per_query: int,
) -> dict[str, object]:
    boards: dict[str, dict[str, object]] = {}
    query_symbols: dict[str, list[str]] = {}
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
    board_bars = {
        symbol: store.get_daily_bars(symbol, None, end) for symbol in boards
    }
    by_symbol_date = {
        symbol: {bar.trade_date: index for index, bar in enumerate(bars)}
        for symbol, bars in board_bars.items()
    }
    benchmark_by_date = {
        bar.trade_date: index for index, bar in enumerate(all_benchmark)
    }
    system = BoardHotspotSystem()
    scorer_registry = default_scorer_registry()
    prior: dict[str, dict[str, object]] = {}
    timelines: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in boards}
    stage_counts: dict[str, int] = {}
    for effective in dates:
        snapshots = store.calculate_board_hotspot_snapshots(effective)
        observations = []
        for symbol, bars in board_bars.items():
            index = by_symbol_date[symbol].get(effective)
            benchmark_index = benchmark_by_date.get(effective)
            if index is None or benchmark_index is None:
                continue
            observation = analyze_daily_series(
                symbol, bars[:index + 1], effective,
                benchmark_bars=all_benchmark[:benchmark_index + 1],
            )
            breadth = observation.get("metrics", {}).get("board_breadth")
            if breadth is None and isinstance(observation.get("metrics"), dict):
                member = snapshots.get(symbol, {})
                ratio = member.get("positive_return_5_ratio")
                observation["metrics"]["board_breadth"] = {
                    "breadth": (float(ratio) * 2 - 1) if ratio is not None else None,
                    "impact_concentration_hhi": None,
                }
            observations.append(observation)
        execution = system.execute(ObservationSystemContext(
            observations=observations, scorer_registry=scorer_registry,
            prior_scores={BOARD_HOTSPOT_SYSTEM: prior},
            dependencies={"board_hotspot_snapshots": snapshots},
        ))
        prior = {str(item["symbol"]): item for item in execution.results}
        for item in execution.results:
            stage = str(item["hotspot_stage"])
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            if stage == "failed":
                continue
            timelines[str(item["symbol"])].append({
                key: item.get(key) for key in (
                    "symbol", "hotspot_stage", "total_score", "raw_score",
                    "score_direction", "candidate_streak", "eligible",
                    "limit_up_count", "max_limit_up_streak", "summary",
                )
            } | {"effective_date": effective.isoformat()})
    first_detections = []
    eligible_board_count = 0
    for symbol, events in timelines.items():
        if not events:
            continue
        first = events[0]
        first_eligible = next((item for item in events if item["eligible"]), None)
        eligible_board_count += int(first_eligible is not None)
        first_detections.append({
            "symbol": symbol, "name": boards[symbol].get("name"),
            "first_date": first["effective_date"],
            "first_stage": first["hotspot_stage"],
            "first_score": first["total_score"],
            "peak_score": max(float(item["total_score"]) for item in events),
            "event_days": len(events),
            "first_eligible_date": (
                first_eligible["effective_date"] if first_eligible else None
            ),
            "first_eligible_stage": (
                first_eligible["hotspot_stage"] if first_eligible else None
            ),
        })
    scores = [float(item["first_score"]) for item in first_detections]
    return {
        "summary": {
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "trading_dates": len(dates), "board_count": len(boards),
            "detected_board_count": len(first_detections),
            "eligible_board_count": eligible_board_count,
            "median_first_score": round(median(scores), 2) if scores else None,
            "stage_counts": stage_counts,
            "algorithm_version": system.version,
            "online_features_are_causal": True,
        },
        "queries": query_symbols,
        "first_detections": sorted(first_detections, key=lambda item: (
            str(item["first_date"]), -float(item["peak_score"]), str(item["symbol"]),
        )),
        "timelines": timelines,
    }


if __name__ == "__main__":
    main()
