from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import logging
from pathlib import Path
import subprocess
import sys
import time

from stock_harness.config import load_runtime_settings
from stock_harness.signal_review import DAILY_MARKET_BOARD_SIGNAL, SignalReviewService
from stock_harness.sqlite_store import SQLiteMarketDataStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay recent completed daily signal reviews sequentially",
    )
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument("--worker-date", type=date.fromisoformat, help=argparse.SUPPRESS)
    parser.add_argument(
        "--output", type=Path,
        default=Path(".tmp/reports/recent-daily-signal-replay.json"),
    )
    args = parser.parse_args()
    if args.worker_date is not None:
        _run_worker(args)
        return
    if args.sessions < 1 or args.sessions > 250:
        raise SystemExit("--sessions must be between 1 and 250")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_runtime_settings(args.provider_config, args.storage_config)
    failures: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    started = time.perf_counter()
    with SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    ) as store:
        latest = store.get_latest_stock_daily_bar_date()
        through = min(args.through or latest, latest) if latest else None
        if through is None:
            raise SystemExit("no completed stock daily bars are available")
        dates = store.list_trading_dates(
            "tushare", through - timedelta(days=max(60, args.sessions * 3)), through,
        )[-args.sessions:]
        if len(dates) != args.sessions:
            raise SystemExit(
                f"requested {args.sessions} sessions but only {len(dates)} are available"
            )
    for index, effective_date in enumerate(dates, 1):
        print(
            f"daily_review_replay_started index={index} total={len(dates)} "
            f"date={effective_date}",
            flush=True,
        )
        completed = subprocess.run(
            [
                sys.executable, str(Path(__file__).resolve()),
                "--provider-config", str(args.provider_config),
                "--storage-config", str(args.storage_config),
                "--worker-date", effective_date.isoformat(),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="", flush=True)
        marker = next((
            line.removeprefix("daily_review_replay_worker ")
            for line in reversed(completed.stdout.splitlines())
            if line.startswith("daily_review_replay_worker ")
        ), None)
        if completed.returncode == 0 and marker is not None:
            record = json.loads(marker)
            results.append(record)
            print(
                "daily_review_replay_completed "
                + json.dumps(record, ensure_ascii=False, default=str),
                flush=True,
            )
            _write_report(
                args.output, args.sessions, through, results, failures, started,
            )
            continue
        failure = {
            "effective_date": effective_date.isoformat(),
            "error_type": "WorkerProcessError",
            "error": completed.stdout[-2000:] or f"worker exit code {completed.returncode}",
        }
        failures.append(failure)
        print(
            "daily_review_replay_failed " + json.dumps(failure, ensure_ascii=False),
            flush=True,
        )
        _write_report(args.output, args.sessions, through, results, failures, started)

    report = _write_report(
        args.output, args.sessions, through, results, failures, started,
    )
    print(
        "daily_review_replay_finished "
        + json.dumps({
            "completed": len(results), "failed": len(failures),
            "elapsed_seconds": report["elapsed_seconds"], "output": str(args.output),
        }, ensure_ascii=False),
        flush=True,
    )
    if failures:
        raise SystemExit(2)


def _write_report(
    output: Path, requested_sessions: int, through: date,
    results: list[dict[str, object]], failures: list[dict[str, object]],
    started: float,
) -> dict[str, object]:
    report = {
        "signal_id": DAILY_MARKET_BOARD_SIGNAL,
        "requested_sessions": requested_sessions,
        "through": through.isoformat(),
        "results": results,
        "failures": failures,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def _run_worker(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    settings = load_runtime_settings(args.provider_config, args.storage_config)
    with SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    ) as store:
        run = SignalReviewService(store).run_sync(
            DAILY_MARKET_BOARD_SIGNAL, args.worker_date,
        )
    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    record = {
        "effective_date": args.worker_date.isoformat(),
        "run_id": run.get("run_id"),
        "revision": run.get("revision"),
        "status": run.get("status"),
        "mean_reversion_counts": summary.get("mean_reversion_counts"),
        "scoring_errors": summary.get("scoring_errors", []),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    print(
        "daily_review_replay_worker "
        + json.dumps(record, ensure_ascii=False, default=str),
        flush=True,
    )


if __name__ == "__main__":
    main()
