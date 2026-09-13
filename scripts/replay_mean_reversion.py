from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import time

from stock_harness.config import load_runtime_settings
from stock_harness.mean_reversion_replay import (
    MeanReversionReplayAdapter,
    SQLiteReplayFutureDataSource,
    select_replay_dates,
)
from stock_harness.replay import AnalysisReplayEngine
from stock_harness.sqlite_store import SQLiteMarketDataStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay only the mean-reversion analysis system")
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument("--sessions", type=int, default=40)
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument("--evaluation-through", type=date.fromisoformat)
    parser.add_argument("--output", type=Path, default=Path(".tmp/reports/mean-reversion-40d.json"))
    args = parser.parse_args()
    if not 1 <= args.sessions <= 250:
        parser.error("--sessions must be between 1 and 250")

    settings = load_runtime_settings(args.provider_config, args.storage_config)
    started = time.perf_counter()
    with SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    ) as store:
        latest = store.get_latest_stock_daily_bar_date()
        if latest is None:
            raise SystemExit("no completed stock daily bars are available")
        through = min(args.through or latest, latest)
        evaluation_through = min(args.evaluation_through or latest, latest)
        if evaluation_through < through:
            parser.error("--evaluation-through cannot precede --through")
        dates = select_replay_dates(store, through, args.sessions)
        if len(dates) != args.sessions:
            raise SystemExit(f"requested {args.sessions} sessions but found {len(dates)}")
        adapter = MeanReversionReplayAdapter(
            store,
            progress=lambda index, total, cutoff, count: print(
                f"mean_reversion_replay_progress {index}/{total} "
                f"date={cutoff} signals={count}", flush=True,
            ),
        )
        report = AnalysisReplayEngine(
            SQLiteReplayFutureDataSource(store, evaluation_through),
        ).run(adapter, dates)
        report.update({
            "adapter_diagnostics": adapter.diagnostics,
            "requested_sessions": args.sessions,
            "through": through.isoformat(),
            "evaluation_through": evaluation_through.isoformat(),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps({
        "sessions": len(dates), "signals": len(report["signals"]),
        "elapsed_seconds": report["elapsed_seconds"], "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
