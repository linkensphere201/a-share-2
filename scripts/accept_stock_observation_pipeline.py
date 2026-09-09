"""Run and audit the production-scale stock observation pipeline."""

from __future__ import annotations

import argparse
import ctypes
from datetime import date
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any

from stock_harness.config import load_runtime_settings
from stock_harness.signal_review import DAILY_MARKET_BOARD_SIGNAL, SignalReviewService
from stock_harness.sqlite_store import SQLiteMarketDataStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument("--effective-date", type=date.fromisoformat)
    parser.add_argument("--run-id")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.run and args.run_id:
        parser.error("--run and --run-id cannot be combined")

    settings = load_runtime_settings(args.provider_config, args.storage_config)
    sampler = PeakMemorySampler()
    sampler.start()
    started = time.perf_counter()
    store = _open_store(settings)
    try:
        if args.run:
            effective = args.effective_date or store.get_latest_stock_daily_bar_date()
            if effective is None:
                raise SystemExit("no completed stock daily bars are available")
            run = SignalReviewService(store).run_sync(DAILY_MARKET_BOARD_SIGNAL, effective)
        elif args.run_id:
            run = store.get_signal_review_run(args.run_id)
        else:
            latest = args.effective_date or store.get_latest_stock_daily_bar_date()
            run = (
                store.get_latest_succeeded_signal_review_run(
                    DAILY_MARKET_BOARD_SIGNAL, latest,
                )
                if latest is not None else None
            )
        if run is None:
            raise SystemExit("no signal review run is available")
        report, snapshot_digest = audit(store, run)
    finally:
        store.close()

    reopened = _open_store(settings)
    try:
        restored_run = reopened.get_signal_review_run(str(run["run_id"]))
        restored_board = reopened.get_observation_pool_snapshot(str(run["run_id"]), "board")
        restored_stock = reopened.get_observation_pool_snapshot(str(run["run_id"]), "stock")
        restored_digest = _digest({"board": restored_board, "stock": restored_stock})
    finally:
        reopened.close()
        sampler.stop()
    report["restart_restoration"] = {
        "run_restored": restored_run is not None,
        "snapshot_digest_match": restored_digest == snapshot_digest,
    }
    report["performance"] = {
        "wall_seconds": round(time.perf_counter() - started, 3),
        "pipeline_seconds": (run.get("summary") or {}).get("elapsed_seconds"),
        "peak_working_set_mib": sampler.peak_mib,
    }
    errors = list(report["errors"])
    if not report["restart_restoration"]["run_restored"]:
        errors.append("signal run did not restore after reopening the database")
    if not report["restart_restoration"]["snapshot_digest_match"]:
        errors.append("observation pool snapshots changed after database reopen")
    report["errors"] = errors
    report["ok"] = not errors
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if errors:
        raise SystemExit(1)


def audit(
    store: SQLiteMarketDataStore, run: dict[str, object],
) -> tuple[dict[str, object], str]:
    run_id = str(run["run_id"])
    effective = run["effective_date"]
    if not isinstance(effective, date):
        raise ValueError("signal run effective date is invalid")
    errors: list[str] = []
    if run.get("status") != "succeeded":
        errors.append(f"signal run status is {run.get('status')}")
    board = store.get_observation_pool_snapshot(run_id, "board")
    stock = store.get_observation_pool_snapshot(run_id, "stock")
    snapshot_digest = _digest({"board": board, "stock": stock})
    if board is None:
        errors.append("board observation pool is missing")
    if stock is None:
        errors.append("stock observation pool is missing")
    board = board or {"items": [], "summary": {}}
    stock = stock or {"items": [], "summary": {}}
    board_symbols = {str(item["symbol"]) for item in board["items"]}
    future_sources: list[str] = []
    missing_sources: list[str] = []
    invalid_memberships: list[str] = []
    for pool in (board, stock):
        for item in pool["items"]:
            for source in item.get("sources", []):
                source_type = str(source.get("source_type") or "")
                reference = str(source.get("source_reference") or "")
                entity = str(source.get("source_entity_key") or "")
                source_date = _source_date(store, source_type, reference)
                if source_date is not None and source_date > effective:
                    future_sources.append(f"{source_type}:{reference}:{source_date}")
                if _requires_persisted_reference(source_type) and source_date is None:
                    missing_sources.append(f"{source_type}:{reference}")
                if source_type == "board-membership" and entity not in board_symbols:
                    invalid_memberships.append(f"{item['symbol']}:{entity}")
    errors.extend(f"future source: {value}" for value in future_sources[:10])
    errors.extend(f"missing source: {value}" for value in missing_sources[:10])
    errors.extend(f"invalid board membership: {value}" for value in invalid_memberships[:10])

    stock_items = list(stock["items"])
    m4_items = [
        item for item in stock_items
        if _mapping(_mapping(item.get("payload")).get("m4_analysis"))
    ]
    m4_attempted = [
        item for item in m4_items
        if _mapping(_mapping(item.get("payload")).get("m4_analysis")).get("status")
    ]
    if len(m4_attempted) > 30:
        errors.append(f"M4 analysis budget exceeded: {len(m4_attempted)}")
    invalid_independent = []
    for item in stock_items:
        independent = _mapping(_mapping(item.get("payload")).get("independent_scan"))
        if independent.get("eligible") and independent.get("classification") not in {
            "independent-advance", "counter-trend-resilience", "emerging-independent-move",
        }:
            invalid_independent.append(str(item["symbol"]))
    errors.extend(f"invalid independent eligibility: {value}" for value in invalid_independent[:10])
    lifecycle_counts = _counts(item.get("lifecycle_state") for item in stock_items)
    classification_counts = _counts(
        _mapping(_mapping(item.get("payload")).get("opportunity_classification")).get("classification")
        or _mapping(_mapping(item.get("payload")).get("independent_scan")).get("classification")
        for item in stock_items
    )
    report: dict[str, object] = {
        "ok": False,
        "run_id": run_id,
        "effective_date": effective.isoformat(),
        "revision": run.get("revision"),
        "run_status": run.get("status"),
        "board_pool": {
            "item_count": len(board["items"]),
            "summary": board.get("summary", {}),
        },
        "stock_pool": {
            "item_count": len(stock_items),
            "summary": stock.get("summary", {}),
            "lifecycle_counts": lifecycle_counts,
            "classification_counts": classification_counts,
            "recognized_count": sum(bool(_mapping(item.get("payload")).get("recognized")) for item in stock_items),
            "unrecognized_independent_eligible_count": sum(
                not bool(_mapping(item.get("payload")).get("recognized"))
                and bool(_mapping(_mapping(item.get("payload")).get("independent_scan")).get("eligible"))
                for item in stock_items
            ),
            "emerging_unrecognized_count": sum(
                not bool(_mapping(item.get("payload")).get("recognized"))
                and _mapping(_mapping(item.get("payload")).get("independent_scan")).get("classification")
                == "emerging-independent-move"
                for item in stock_items
            ),
            "m4_attempted_count": len(m4_attempted),
            "m4_succeeded_count": sum(
                _mapping(_mapping(item.get("payload")).get("m4_analysis")).get("status") == "succeeded"
                for item in m4_attempted
            ),
            "m4_deferred_count": sum(
                _mapping(_mapping(item.get("payload")).get("m4_analysis")).get("state")
                == "deferred-resource-limit"
                for item in m4_items
            ),
            "opportunity_eligible_count": sum(
                bool(_mapping(_mapping(item.get("payload")).get("opportunity_classification")).get("opportunity_eligible"))
                for item in stock_items
            ),
        },
        "provenance": {
            "future_source_count": len(future_sources),
            "missing_reference_count": len(missing_sources),
            "invalid_membership_count": len(invalid_memberships),
        },
        "errors": errors,
    }
    return report, snapshot_digest


def _source_date(
    store: SQLiteMarketDataStore, source_type: str, reference: str,
) -> date | None:
    if source_type in {
        "trend-score", "hard-anomaly", "recognition-assignment",
        "independent-strength", "board-membership", "pool-lifecycle",
    }:
        value = store.get_signal_review_run(reference)
        return value.get("effective_date") if value else None  # type: ignore[return-value]
    if source_type == "screener-result":
        value = store.get_screener_run(reference)
        return value.get("as_of_date") if value else None  # type: ignore[return-value]
    if source_type == "m4-analysis":
        value = store.get_generated_analysis_run(reference)
        return value.get("as_of_date") if value else None  # type: ignore[return-value]
    return None


def _requires_persisted_reference(source_type: str) -> bool:
    return source_type in {
        "trend-score", "hard-anomaly", "recognition-assignment",
        "independent-strength", "board-membership", "pool-lifecycle",
        "screener-result", "m4-analysis",
    }


def _counts(values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(value or "unclassified")
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items(), key=lambda item: (-item[1], item[0])))


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=_json_default,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _open_store(settings: Any) -> SQLiteMarketDataStore:
    return SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )


class PeakMemorySampler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak_bytes = _working_set_bytes() or 0

    @property
    def peak_mib(self) -> float | None:
        return round(self._peak_bytes / 1024 / 1024, 3) if self._peak_bytes else None

    def start(self) -> None:
        if _working_set_bytes() is None:
            return
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        value = _working_set_bytes()
        if value is not None:
            self._peak_bytes = max(self._peak_bytes, value)

    def _sample(self) -> None:
        while not self._stop.wait(.1):
            value = _working_set_bytes()
            if value is not None:
                self._peak_bytes = max(self._peak_bytes, value)


def _working_set_bytes() -> int | None:
    if not hasattr(ctypes, "windll"):
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get_current_process = ctypes.windll.kernel32.GetCurrentProcess
    get_current_process.restype = ctypes.c_void_p
    process = get_current_process()
    get_memory_info = getattr(
        ctypes.windll.kernel32, "K32GetProcessMemoryInfo",
        ctypes.windll.psapi.GetProcessMemoryInfo,
    )
    get_memory_info.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters), ctypes.c_ulong,
    ]
    get_memory_info.restype = ctypes.c_int
    if not get_memory_info(
        process, ctypes.byref(counters), counters.cb,
    ):
        return None
    return int(counters.WorkingSetSize)


if __name__ == "__main__":
    main()
