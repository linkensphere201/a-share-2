"""Replay the production stock-opportunity funnel over historical dates."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import gc
import json
from pathlib import Path
import time
from typing import Any, Iterable, Mapping, Sequence

from stock_harness.config import load_runtime_settings
from stock_harness.review_scoring import STOCK_OPPORTUNITY_SCORER
from stock_harness.signal_review import DAILY_MARKET_BOARD_SIGNAL, SignalReviewService
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.stock_observation_layers import ALGORITHM_VERSION as PRESENTATION_VERSION


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--sample-count", type=int, default=26)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--require-opportunity", action="store_true")
    args = parser.parse_args()
    if args.sample_count < 2:
        parser.error("--sample-count must be at least 2")

    settings = load_runtime_settings(args.provider_config, args.storage_config)
    store = _open_store(settings)
    try:
        end_date = args.end_date or store.get_latest_stock_daily_bar_date()
        if end_date is None:
            raise SystemExit("no completed stock daily bars are available")
        start_date = args.start_date or end_date - timedelta(days=365)
        trading_dates = store.list_trading_dates("tushare", start_date, end_date)
    finally:
        store.close()
    dates = select_replay_dates(trading_dates, args.sample_count)
    if not dates:
        raise SystemExit("no trading dates are available in the requested interval")

    report = _load_report(args.output) if not args.no_resume else None
    if not _compatible_report(report, dates):
        report = {
            "algorithm_contract": {
                "signal_id": DAILY_MARKET_BOARD_SIGNAL,
                "presentation_version": PRESENTATION_VERSION,
                "execution": "SignalReviewService.run_sync",
                "parameter_changes_between_dates": False,
                "historical_membership_limit": (
                    "Price inputs are cutoff-causal; board membership uses the locally "
                    "available membership snapshot and can contain survivorship bias."
                ),
            },
            "requested_dates": [value.isoformat() for value in dates],
            "results": [],
        }
    completed_dates = {str(item["effective_date"]) for item in report["results"]}

    started = time.perf_counter()
    for index, effective_date in enumerate(dates, 1):
        if effective_date.isoformat() in completed_dates:
            print(f"[{index}/{len(dates)}] reuse {effective_date}", flush=True)
            continue
        print(f"[{index}/{len(dates)}] run {effective_date}", flush=True)
        date_started = time.perf_counter()
        store = _open_store(settings)
        try:
            run = _find_reusable_run(store, effective_date)
            reused = run is not None
            if run is None:
                run = SignalReviewService(store).run_sync(
                    DAILY_MARKET_BOARD_SIGNAL, effective_date,
                )
            result = summarize_run(store, run)
            result["reused"] = reused
            result["wall_seconds"] = round(time.perf_counter() - date_started, 3)
        finally:
            store.close()
        report["results"].append(result)
        report["results"].sort(key=lambda item: str(item["effective_date"]))
        _finalize_report(report, started)
        _write_report(args.output, report)
        print(
            f"[{index}/{len(dates)}] strict={result['strict_opportunity_count']} "
            f"funnel={result['funnel']}", flush=True,
        )
        gc.collect()

    _finalize_report(report, started)
    _write_report(args.output, report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if args.require_opportunity and not report["summary"]["strict_opportunity_seen"]:
        raise SystemExit(2)


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


def _compatible_report(report: object, dates: Sequence[date]) -> bool:
    return bool(
        isinstance(report, dict)
        and _mapping(report.get("algorithm_contract")).get("presentation_version")
        == PRESENTATION_VERSION
        and report.get("requested_dates") == [value.isoformat() for value in dates]
    )


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
