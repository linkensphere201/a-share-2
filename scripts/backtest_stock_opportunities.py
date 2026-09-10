"""Replay the production stock-opportunity funnel over historical dates."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import gc
import gzip
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Sequence

from stock_harness.config import load_runtime_settings
from stock_harness.review_scoring import STOCK_OPPORTUNITY_SCORER
from stock_harness.signal_review import DAILY_MARKET_BOARD_SIGNAL, SignalReviewService
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.stock_observation_layers import (
    ALGORITHM_VERSION as PRESENTATION_VERSION,
    assign_stock_presentation_layers,
)
from stock_harness.stock_observation_scan import (
    INDEPENDENT_SCAN_VERSION,
    UNIFIED_STOCK_POOL_VERSION,
    build_unified_stock_pool_snapshot,
    scan_full_market_independent_strength,
)
from stock_harness.stock_relative_strength import ALGORITHM_VERSION as RELATIVE_STRENGTH_VERSION


FEATURE_CACHE_VERSION = "stock-focus-replay-features-v1"
DEFAULT_SCAN_WORKERS = 4


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--sample-count", type=int, default=26)
    parser.add_argument("--all-trading-days", action="store_true")
    parser.add_argument("--mode", choices=("focus-core", "production"), default="focus-core")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--require-opportunity", action="store_true")
    parser.add_argument("--single-date", type=date.fromisoformat, help=argparse.SUPPRESS)
    parser.add_argument("--single-result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--prior-snapshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--next-snapshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--scan-date", type=date.fromisoformat, help=argparse.SUPPRESS)
    parser.add_argument("--scan-result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--feature-cache-dir", type=Path,
                        default=Path(".tmp/replay-features/stock-focus-v1"))
    parser.add_argument("--scan-workers", type=int, default=DEFAULT_SCAN_WORKERS)
    args = parser.parse_args()
    if args.scan_date:
        if args.scan_result is None:
            parser.error("--scan-result is required with --scan-date")
        _run_scan_date(args)
        return
    if args.single_date:
        if args.single_result is None:
            parser.error("--single-result is required with --single-date")
        _run_single_date(args, settings=None)
        return
    if args.output is None:
        parser.error("--output is required")
    if args.sample_count < 2:
        parser.error("--sample-count must be at least 2")
    if not 1 <= args.scan_workers <= 4:
        parser.error("--scan-workers must be between 1 and 4")

    settings = load_runtime_settings(args.provider_config, args.storage_config)
    store = _open_store(settings)
    try:
        end_date = args.end_date or store.get_latest_stock_daily_bar_date()
        if end_date is None:
            raise SystemExit("no completed stock daily bars are available")
        start_date = args.start_date or end_date - timedelta(days=365)
        benchmark_bars = store.get_daily_bars("000001.SH", start_date, end_date)
        trading_dates = [bar.trade_date for bar in benchmark_bars]
        date_source = "000001.SH canonical daily bars"
        if not trading_dates:
            trading_dates = store.list_trading_dates("tushare", start_date, end_date)
            date_source = "tushare trading calendar fallback"
    finally:
        store.close()
    dates = trading_dates if args.all_trading_days else select_replay_dates(
        trading_dates, args.sample_count,
    )
    if not dates:
        raise SystemExit("no trading dates are available in the requested interval")

    state_path = args.output.with_name(f"{args.output.stem}-state.json")
    report = _load_report(args.output) if not args.no_resume else None
    if (
        _compatible_report(report, dates, args.mode)
        and args.mode == "focus-core"
        and _sequence(_mapping(report).get("results"))
        and not _state_matches_report(_load_report(state_path), report)
    ):
        report = None
    if not _compatible_report(report, dates, args.mode):
        report = {
            "algorithm_contract": {
                "signal_id": DAILY_MARKET_BOARD_SIGNAL,
                "presentation_version": PRESENTATION_VERSION,
                "relative_strength_version": RELATIVE_STRENGTH_VERSION,
                "independent_scan_version": INDEPENDENT_SCAN_VERSION,
                "stock_pool_version": UNIFIED_STOCK_POOL_VERSION,
                "execution": (
                    "stateful full-market scan and presentation functions"
                    if args.mode == "focus-core" else "SignalReviewService.run_sync"
                ),
                "mode": args.mode,
                "parameter_changes_between_dates": False,
                "feature_cache_version": FEATURE_CACHE_VERSION,
                "historical_membership_limit": (
                    "Price inputs are cutoff-causal; board membership uses the locally "
                    "available membership snapshot and can contain survivorship bias."
                ),
            },
            "requested_dates": [value.isoformat() for value in dates],
            "date_source": date_source,
            "results": [],
        }
    else:
        report["algorithm_contract"]["feature_cache_version"] = FEATURE_CACHE_VERSION
    completed_dates = {str(item["effective_date"]) for item in report["results"]}

    if args.mode == "focus-core":
        pending = [value for value in dates if value.isoformat() not in completed_dates]
        _prepare_feature_cache(args, pending)

    started = time.perf_counter()
    for index, effective_date in enumerate(dates, 1):
        if effective_date.isoformat() in completed_dates:
            print(f"[{index}/{len(dates)}] reuse {effective_date}", flush=True)
            continue
        print(f"[{index}/{len(dates)}] run {effective_date}", flush=True)
        date_started = time.perf_counter()
        if args.mode == "focus-core":
            prior_snapshot = _load_report(state_path) if report["results"] else None
            records = _read_feature_cache(_feature_cache_path(args, effective_date))
            snapshot = build_unified_stock_pool_snapshot(
                f"historical-focus-core:{effective_date}", effective_date,
                {"items": []}, records, prior_snapshot=prior_snapshot,
            )
            assign_stock_presentation_layers(snapshot)
            result = summarize_focus_snapshot(snapshot)
            result["reused"] = False
            result["child_wall_seconds"] = round(
                time.perf_counter() - date_started, 3,
            )
            _write_report(state_path, snapshot)
        else:
            single_result = args.output.with_name(f"{args.output.stem}-single.json")
            command = [
                sys.executable, str(Path(__file__).resolve()),
                "--provider-config", str(args.provider_config),
                "--storage-config", str(args.storage_config),
                "--single-date", effective_date.isoformat(),
                "--single-result", str(single_result),
                "--mode", args.mode,
            ]
            completed = subprocess.run(
                command, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode:
                raise RuntimeError(
                    f"single-date replay failed for {effective_date}: {completed.returncode}"
                )
            result = json.loads(single_result.read_text(encoding="utf-8"))
        result["wall_seconds"] = round(time.perf_counter() - date_started, 3)
        report["results"].append(result)
        report["results"].sort(key=lambda item: str(item["effective_date"]))
        _finalize_report(report, started)
        _write_report(args.output, report)
        if args.mode == "focus-core":
            print(
                f"[{index}/{len(dates)}] focus={result['focus_count']} "
                f"risk={result['risk_count']} lanes={result['focus_lane_counts']}",
                flush=True,
            )
        else:
            print(
                f"[{index}/{len(dates)}] "
                f"strict={result['strict_opportunity_count']} "
                f"funnel={result['funnel']}", flush=True,
            )
        gc.collect()

    _finalize_report(report, started)
    if args.mode == "focus-core" and len(report["results"]) == len(dates):
        print("evaluate focus recall against future strong-move labels", flush=True)
        store = _open_store(settings)
        try:
            report["recall_evaluation"] = evaluate_focus_recall(store, report["results"])
        finally:
            store.close()
    _write_report(args.output, report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if args.require_opportunity and not report["summary"]["strict_opportunity_seen"]:
        raise SystemExit(2)


def _prepare_feature_cache(
    args: argparse.Namespace, dates: Sequence[date],
) -> None:
    missing = [value for value in dates if not _feature_cache_ready(args, value)]
    if not missing:
        print(f"feature cache ready for {len(dates)} dates", flush=True)
        return
    print(
        f"prepare feature cache missing={len(missing)} workers={args.scan_workers}",
        flush=True,
    )

    def run(value: date) -> date:
        target = _feature_cache_path(args, value)
        command = [
            sys.executable, str(Path(__file__).resolve()),
            "--provider-config", str(args.provider_config),
            "--storage-config", str(args.storage_config),
            "--scan-date", value.isoformat(),
            "--scan-result", str(target),
        ]
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(
                f"feature scan failed for {value}: {completed.returncode}: {detail}"
            )
        return value

    with ThreadPoolExecutor(max_workers=args.scan_workers) as executor:
        futures = {executor.submit(run, value): value for value in missing}
        for completed_count, future in enumerate(as_completed(futures), 1):
            value = future.result()
            print(
                f"feature cache {completed_count}/{len(missing)} {value}", flush=True,
            )


def _run_scan_date(args: argparse.Namespace) -> None:
    settings = load_runtime_settings(args.provider_config, args.storage_config)
    store = _open_store(settings)
    try:
        records = scan_full_market_independent_strength(store, args.scan_date)
    finally:
        store.close()
    payload = {
        "feature_cache_version": FEATURE_CACHE_VERSION,
        "relative_strength_version": RELATIVE_STRENGTH_VERSION,
        "independent_scan_version": INDEPENDENT_SCAN_VERSION,
        "effective_date": args.scan_date.isoformat(),
        "records": records,
    }
    args.scan_result.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.scan_result, "wt", encoding="utf-8", compresslevel=1) as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
    _feature_cache_marker(args.scan_result).write_text(
        FEATURE_CACHE_VERSION + "\n", encoding="ascii",
    )


def _feature_cache_path(args: argparse.Namespace, effective_date: date) -> Path:
    version = "-".join((
        FEATURE_CACHE_VERSION, RELATIVE_STRENGTH_VERSION, INDEPENDENT_SCAN_VERSION,
    )).replace("/", "-")
    return args.feature_cache_dir / f"{effective_date.isoformat()}-{version}.json.gz"


def _feature_cache_marker(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".complete")


def _feature_cache_ready(args: argparse.Namespace, effective_date: date) -> bool:
    path = _feature_cache_path(args, effective_date)
    marker = _feature_cache_marker(path)
    return bool(
        path.is_file() and path.stat().st_size > 0 and marker.is_file()
        and marker.read_text(encoding="ascii").strip() == FEATURE_CACHE_VERSION
    )


def _read_feature_cache(path: Path) -> list[dict[str, object]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if (
        payload.get("feature_cache_version") != FEATURE_CACHE_VERSION
        or payload.get("relative_strength_version") != RELATIVE_STRENGTH_VERSION
        or payload.get("independent_scan_version") != INDEPENDENT_SCAN_VERSION
    ):
        raise ValueError(f"incompatible feature cache: {path}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"invalid feature cache records: {path}")
    return records


def _run_single_date(args: argparse.Namespace, settings: Any | None) -> None:
    settings = settings or load_runtime_settings(args.provider_config, args.storage_config)
    started = time.perf_counter()
    store = _open_store(settings)
    try:
        if args.mode == "focus-core":
            records = scan_full_market_independent_strength(store, args.single_date)
            prior_snapshot = (
                _load_report(args.prior_snapshot) if args.prior_snapshot else None
            )
            snapshot = build_unified_stock_pool_snapshot(
                f"historical-focus-core:{args.single_date}", args.single_date,
                {"items": []}, records, prior_snapshot=prior_snapshot,
            )
            assign_stock_presentation_layers(snapshot)
            result = summarize_focus_snapshot(snapshot)
            result["reused"] = False
            result["child_wall_seconds"] = round(time.perf_counter() - started, 3)
            if args.next_snapshot:
                _write_report(args.next_snapshot, snapshot)
            _write_report(args.single_result, result)
            return
        run = _find_reusable_run(store, args.single_date)
        reused = run is not None
        if run is None:
            run = SignalReviewService(store).run_sync(
                DAILY_MARKET_BOARD_SIGNAL, args.single_date,
            )
        result = summarize_run(store, run)
        result["reused"] = reused
        result["child_wall_seconds"] = round(time.perf_counter() - started, 3)
    finally:
        store.close()
    _write_report(args.single_result, result)


def summarize_focus_snapshot(snapshot: Mapping[str, object]) -> dict[str, object]:
    items = [
        item for item in _sequence(snapshot.get("items"))
        if isinstance(item, Mapping)
    ]
    focus = [
        item for item in items
        if _mapping(item.get("payload")).get("presentation_bucket") == "focus"
    ]
    opportunity = [
        item for item in items
        if _mapping(item.get("payload")).get("presentation_bucket") == "opportunity"
    ]
    watch = [*opportunity, *focus]
    lanes = Counter(
        str(_mapping(item.get("payload")).get("presentation_lane") or "unknown")
        for item in focus
    )
    price_basis = Counter(
        _item_price_basis(item) for item in watch
    )
    return {
        "effective_date": _iso(snapshot.get("effective_date")),
        "run_id": snapshot.get("source_run_id"),
        "status": "succeeded",
        "stock_pool_count": len(items),
        "focus_count": len(focus),
        "strict_opportunity_count": len(opportunity),
        "risk_count": sum(
            _mapping(item.get("payload")).get("presentation_bucket") == "risk"
            for item in items
        ),
        "focus_lane_counts": dict(sorted(lanes.items())),
        "focus_price_basis_counts": dict(sorted(price_basis.items())),
        "focus_range": [{
            "symbol": item.get("symbol"),
            "rank": _mapping(item.get("payload")).get("presentation_rank"),
            "lane": _mapping(item.get("payload")).get("presentation_lane"),
            "reasons": _mapping(item.get("payload")).get("presentation_reasons", []),
            "score": _mapping(item.get("payload")).get("independent_score"),
            "risk_name": bool(_mapping(item.get("payload")).get("risk_name")),
        } for item in watch],
    }


def select_replay_dates(values: Sequence[date], sample_count: int) -> list[date]:
    dates = sorted(set(values))
    if len(dates) <= sample_count:
        return dates
    last = len(dates) - 1
    return [dates[round(index * last / (sample_count - 1))] for index in range(sample_count)]


def summarize_run(
    store: SQLiteMarketDataStore, run: Mapping[str, object],
) -> dict[str, object]:
    run_id = str(run["run_id"])
    scores = store.list_signal_review_scores(run_id, STOCK_OPPORTUNITY_SCORER)
    disqualifiers = Counter(
        str(reason)
        for score in scores
        for reason in _sequence(score.get("disqualifiers"))
    )
    eligible = [score for score in scores if bool(score.get("eligible"))]

    def passed(score: Mapping[str, object], blocked: Iterable[str]) -> bool:
        reasons = {str(value) for value in _sequence(score.get("disqualifiers"))}
        return not reasons.intersection(blocked)

    m4 = [score for score in scores if passed(score, {"m4-analysis-unavailable"})]
    actionable = [score for score in m4 if not any(
        str(reason).startswith("scenario-")
        for reason in _sequence(score.get("disqualifiers"))
    )]
    target = [score for score in actionable if passed(
        score, {"no-credible-target-at-3r"},
    )]
    valid_order = [score for score in target if passed(
        score, {"invalid-long-price-ordering"},
    )]
    return {
        "effective_date": _iso(run.get("effective_date")),
        "run_id": run_id,
        "revision": run.get("revision"),
        "status": run.get("status"),
        "stock_pool_count": _mapping(run.get("summary")).get("stock_pool_count"),
        "funnel": {
            "scored": len(scores), "m4_succeeded": len(m4),
            "actionable_state": len(actionable), "stressed_target_3r": len(target),
            "valid_long_price_order": len(valid_order), "strict_opportunity": len(eligible),
        },
        "disqualifiers": dict(sorted(disqualifiers.items())),
        "strict_opportunity_count": len(eligible),
        "strict_opportunities": [{
            "symbol": score.get("symbol"), "name": score.get("name"),
            "setup_state": score.get("setup_state"),
            "total_score": score.get("total_score"),
            "stressed_risk_reward": score.get("stressed_risk_reward"),
            "selected_target_label": score.get("selected_target_label"),
            "selected_target_price": score.get("selected_target_price"),
        } for score in eligible],
    }


def _find_reusable_run(
    store: SQLiteMarketDataStore, effective_date: date,
) -> dict[str, object] | None:
    for run in store.list_signal_review_runs(DAILY_MARKET_BOARD_SIGNAL, 500):
        parameters = _mapping(run.get("parameters"))
        if (
            run.get("status") == "succeeded"
            and run.get("effective_date") == effective_date
            and parameters.get("stock_presentation_version") == PRESENTATION_VERSION
        ):
            return run
    return None


def _finalize_report(report: dict[str, Any], started: float) -> None:
    results = list(report.get("results", []))
    total = sum(int(item.get("strict_opportunity_count", 0)) for item in results)
    dates_with_opportunity = [
        str(item["effective_date"]) for item in results
        if int(item.get("strict_opportunity_count", 0)) > 0
    ]
    aggregate: Counter[str] = Counter()
    for item in results:
        aggregate.update({
            str(key): int(value)
            for key, value in _mapping(item.get("disqualifiers")).items()
        })
    report["summary"] = {
        "completed_dates": len(results),
        "strict_opportunity_total": total,
        "strict_opportunity_seen": bool(dates_with_opportunity),
        "dates_with_opportunity": dates_with_opportunity,
        "aggregate_disqualifiers": dict(sorted(aggregate.items())),
        "session_wall_seconds": round(time.perf_counter() - started, 3),
    }
    focus_counts = [int(item.get("focus_count", 0)) for item in results]
    if focus_counts:
        report["summary"].update({
            "focus_count_min": min(focus_counts),
            "focus_count_max": max(focus_counts),
            "focus_count_average": round(sum(focus_counts) / len(focus_counts), 2),
            "focus_unique_symbols": len({
                str(candidate.get("symbol"))
                for item in results
                for candidate in item.get("focus_range", [])
            }),
            "focus_transitions": _focus_transitions(results),
        })


def _compatible_report(
    report: object, dates: Sequence[date], mode: str,
) -> bool:
    return bool(
        isinstance(report, dict)
        and _mapping(report.get("algorithm_contract")).get("presentation_version")
        == PRESENTATION_VERSION
        and _mapping(report.get("algorithm_contract")).get("relative_strength_version")
        == RELATIVE_STRENGTH_VERSION
        and _mapping(report.get("algorithm_contract")).get("stock_pool_version")
        == UNIFIED_STOCK_POOL_VERSION
        and _mapping(report.get("algorithm_contract")).get("mode") == mode
        and report.get("requested_dates") == [value.isoformat() for value in dates]
    )


def _state_matches_report(
    state: object, report: object,
) -> bool:
    if not isinstance(state, Mapping) or not isinstance(report, Mapping):
        return False
    results = [
        item for item in _sequence(report.get("results"))
        if isinstance(item, Mapping)
    ]
    if not results:
        return True
    latest = max(results, key=lambda item: str(item.get("effective_date")))
    presentation = _mapping(_mapping(state.get("summary")).get("presentation"))
    return bool(
        _iso(state.get("effective_date")) == str(latest.get("effective_date"))
        and state.get("algorithm_version") == UNIFIED_STOCK_POOL_VERSION
        and presentation.get("algorithm_version") == PRESENTATION_VERSION
    )


def _focus_transitions(results: Sequence[Mapping[str, object]]) -> dict[str, object]:
    overlaps: list[float] = []
    daily_entered: list[int] = []
    completed_stays: list[int] = []
    active_stays: dict[str, int] = {}
    entered = exited = 0
    previous: set[str] | None = None
    for result in sorted(results, key=lambda item: str(item.get("effective_date"))):
        current = {
            str(item.get("symbol")) for item in _sequence(result.get("focus_range"))
            if isinstance(item, Mapping)
        }
        if previous is not None:
            entered_count = len(current - previous)
            entered += entered_count
            exited += len(previous - current)
            daily_entered.append(entered_count)
            union = current | previous
            overlaps.append(len(current & previous) / len(union) if union else 1.0)
            for symbol in previous - current:
                completed_stays.append(active_stays.pop(symbol, 1))
        active_stays = {
            symbol: active_stays.get(symbol, 0) + 1 for symbol in current
        }
        previous = current
    completed_stays.extend(active_stays.values())
    daily_entered.sort()
    completed_stays.sort()
    return {
        "entered_total": entered, "exited_total": exited,
        "average_daily_entered": (
            round(sum(daily_entered) / len(daily_entered), 2)
            if daily_entered else None
        ),
        "median_daily_entered": _percentile(daily_entered, 0.5),
        "p90_daily_entered": _percentile(daily_entered, 0.9),
        "median_consecutive_stay_sessions": _percentile(completed_stays, 0.5),
        "p90_consecutive_stay_sessions": _percentile(completed_stays, 0.9),
        "maximum_consecutive_stay_sessions": (
            completed_stays[-1] if completed_stays else None
        ),
        "average_daily_jaccard": (
            round(sum(overlaps) / len(overlaps), 4) if overlaps else None
        ),
    }


def _percentile(values: Sequence[int], percentile: float) -> int | None:
    if not values:
        return None
    index = round((len(values) - 1) * percentile)
    return sorted(values)[index]


def evaluate_focus_recall(
    store: SQLiteMarketDataStore,
    results: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Apply future labels only after all causal selections have been frozen."""
    ordered_results = sorted(results, key=lambda item: str(item.get("effective_date")))
    replay_dates = [date.fromisoformat(str(item["effective_date"])) for item in ordered_results]
    if not replay_dates:
        return {"status": "empty"}
    focus_by_date = {
        replay_date: {
            str(item.get("symbol"))
            for item in _sequence(result.get("focus_range"))
            if isinstance(item, Mapping)
        }
        for replay_date, result in zip(replay_dates, ordered_results)
    }
    horizons = {20: 0.30, 60: 0.50, 120: 1.00}
    event_symbols: dict[tuple[int, date], set[str]] = {}
    eligible_symbols: dict[tuple[int, date], set[str]] = {}
    symbols = store.list_stock_symbols_with_daily_bars(replay_dates[0], replay_dates[-1])
    qualified_symbols = skipped_discontinuous = 0
    for position, symbol in enumerate(symbols, 1):
        bars = store.get_daily_bars(symbol, replay_dates[0], replay_dates[-1])
        if not bars:
            continue
        factors = store.get_adjustment_factors(symbol, replay_dates[0], replay_dates[-1])
        factor_by_date = {item.trade_date: item.factor for item in factors}
        exact_adjusted = all(bar.trade_date in factor_by_date for bar in bars)
        if not exact_adjusted and _raw_discontinuous(bars):
            skipped_discontinuous += 1
            continue
        applied_factors = factor_by_date if exact_adjusted else {}
        qualified_symbols += 1
        index_by_date = {bar.trade_date: index for index, bar in enumerate(bars)}
        highs = [
            bar.high * applied_factors.get(bar.trade_date, 1.0) for bar in bars
        ]
        closes = [
            bar.close * applied_factors.get(bar.trade_date, 1.0) for bar in bars
        ]
        future_max = {
            horizon: _future_window_max(highs, horizon) for horizon in horizons
        }
        for replay_date in replay_dates:
            index = index_by_date.get(replay_date)
            if index is None or closes[index] <= 0:
                continue
            for horizon, threshold in horizons.items():
                high = future_max[horizon][index]
                if high is not None:
                    eligible_symbols.setdefault((horizon, replay_date), set()).add(symbol)
                    if high / closes[index] - 1.0 >= threshold:
                        event_symbols.setdefault((horizon, replay_date), set()).add(symbol)
        if position % 500 == 0:
            print(f"recall labels {position}/{len(symbols)}", flush=True)

    metrics = {}
    for horizon, threshold in horizons.items():
        events = {
            replay_date: event_symbols.get((horizon, replay_date), set())
            for replay_date in replay_dates
        }
        eligible = {
            replay_date: eligible_symbols.get((horizon, replay_date), set())
            for replay_date in replay_dates
        }
        quality = _selection_quality_metrics(events, eligible, focus_by_date)
        episodes = _event_episodes(events, replay_dates, focus_by_date)
        metrics[str(horizon)] = {
            "threshold_return": threshold,
            **quality,
            **episodes,
        }
    return {
        "status": "completed",
        "selection_uses_future_labels": False,
        "label_price": "maximum future session high",
        "qualified_symbol_count": qualified_symbols,
        "skipped_raw_discontinuous_count": skipped_discontinuous,
        "horizons": metrics,
    }


def _selection_quality_metrics(
    events: Mapping[date, set[str]],
    eligible: Mapping[date, set[str]],
    focus_by_date: Mapping[date, set[str]],
) -> dict[str, object]:
    event_count = sum(len(values) for values in events.values())
    eligible_count = sum(len(values) for values in eligible.values())
    selected_count = sum(
        len(focus_by_date.get(value, set()) & symbols)
        for value, symbols in eligible.items()
    )
    hit_count = sum(
        len(symbols & focus_by_date.get(value, set()))
        for value, symbols in events.items()
    )
    precision = hit_count / selected_count if selected_count else None
    baseline = event_count / eligible_count if eligible_count else None
    lift = (
        precision / baseline
        if precision is not None and baseline not in {None, 0.0} else None
    )
    selection_rate = selected_count / eligible_count if eligible_count else None
    recall = hit_count / event_count if event_count else None
    recall_lift = (
        recall / selection_rate
        if recall is not None and selection_rate not in {None, 0.0} else None
    )
    return {
        "eligible_observation_count": eligible_count,
        "event_observation_count": event_count,
        "selected_observation_count": selected_count,
        "recalled_observation_count": hit_count,
        "observation_recall": round(recall, 4) if recall is not None else None,
        "selection_precision": round(precision, 4) if precision is not None else None,
        "universe_event_rate": round(baseline, 4) if baseline is not None else None,
        "precision_lift": round(lift, 4) if lift is not None else None,
        "selection_rate": round(selection_rate, 4) if selection_rate is not None else None,
        "recall_lift_vs_capacity": (
            round(recall_lift, 4) if recall_lift is not None else None
        ),
    }


def _future_window_max(values: Sequence[float], horizon: int) -> list[float | None]:
    import heapq

    result: list[float | None] = [None] * len(values)
    heap: list[tuple[float, int]] = []
    for index in range(len(values) - 1, -1, -1):
        future_index = index + 1
        if future_index < len(values):
            heapq.heappush(heap, (-values[future_index], future_index))
        while heap and heap[0][1] > index + horizon:
            heapq.heappop(heap)
        if index + horizon < len(values) and heap:
            result[index] = -heap[0][0]
    return result


def _event_episodes(
    events: Mapping[date, set[str]], replay_dates: Sequence[date],
    focus_by_date: Mapping[date, set[str]],
) -> dict[str, object]:
    date_index = {value: index for index, value in enumerate(replay_dates)}
    by_symbol: dict[str, list[int]] = {}
    for event_date, symbols in events.items():
        for symbol in symbols:
            by_symbol.setdefault(symbol, []).append(date_index[event_date])
    episode_count = recalled = 0
    lead_sessions: list[int] = []
    for symbol, indices in by_symbol.items():
        ordered_indices = sorted(indices)
        starts = [
            index for offset, index in enumerate(ordered_indices)
            if offset == 0 or index > ordered_indices[offset - 1] + 1
        ]
        for start in starts:
            episode_count += 1
            candidates = [
                index for index in range(max(0, start - 20), start + 1)
                if symbol in focus_by_date[replay_dates[index]]
            ]
            if candidates:
                recalled += 1
                lead_sessions.append(start - min(candidates))
    lead_sessions.sort()
    return {
        "episode_count": episode_count,
        "recalled_episode_count": recalled,
        "episode_recall": round(recalled / episode_count, 4) if episode_count else None,
        "median_first_discovery_lead_sessions": (
            lead_sessions[len(lead_sessions) // 2] if lead_sessions else None
        ),
    }


def _raw_discontinuous(bars: Sequence[object]) -> bool:
    return any(
        previous.close <= 0
        or abs(current.open / previous.close - 1.0) > 0.25
        or abs(current.close / previous.close - 1.0) > 0.30
        for previous, current in zip(bars, bars[1:])
    )


def _item_price_basis(item: Mapping[str, object]) -> str:
    payload = _mapping(item.get("payload"))
    for key in ("independent_scan", "member_scan"):
        basis = _mapping(payload.get(key)).get("price_basis")
        if basis:
            return str(basis)
    return "unknown"


def _load_report(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        report, ensure_ascii=False, indent=2, default=_json_default,
    ) + "\n", encoding="utf-8")


def _open_store(settings: Any) -> SQLiteMarketDataStore:
    return SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _iso(value: object) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


if __name__ == "__main__":
    main()
