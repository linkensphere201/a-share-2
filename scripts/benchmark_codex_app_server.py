"""Small target-machine latency and memory benchmark for local Codex app-server."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time

from stock_harness.codex_app_server import CodexAppServerClient


def run_turn(client: CodexAppServerClient, thread_id: str) -> dict[str, float | str]:
    started = time.perf_counter()
    first_delta: list[float] = []

    def event(kind: str, _data: dict[str, object]) -> None:
        if kind == "delta" and not first_delta:
            first_delta.append(time.perf_counter())

    _turn_id, response, status = client.run_turn(
        thread_id, "只回复两个字符：OK", event
    )
    completed = time.perf_counter()
    return {
        "first_token_ms": round(((first_delta[0] if first_delta else completed) - started) * 1000, 1),
        "total_ms": round((completed - started) * 1000, 1),
        "status": status,
        "response": response.strip()[:20],
    }


def working_set_bytes(pid: int) -> int | None:
    if not hasattr(ctypes, "windll"):
        return None
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
    if not handle:
        return None
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    try:
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), ctypes.sizeof(counters)
        ):
            return None
        return int(counters.working_set_size)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--fixture", action="store_true")
    options = parser.parse_args()
    if options.fixture:
        return fixture_benchmark()
    client = CodexAppServerClient()
    started = time.perf_counter()
    try:
        status = client.status()
        startup_ms = round((time.perf_counter() - started) * 1000, 1)
        if not status.get("available") or not status.get("authenticated"):
            print(json.dumps({"status": status}, ensure_ascii=False))
            return 1
        process = client._process
        if options.status_only:
            print(json.dumps({
                "version": status.get("version"),
                "startup_account_ms": startup_ms,
                "app_server_working_set_bytes": (
                    working_set_bytes(process.pid) if process is not None else None
                ),
            }, ensure_ascii=False))
            return 0
        workdir = Path(".tmp/codex-chat-benchmark")
        first_thread = client.start_thread(workdir)
        single = run_turn(client, first_thread)
        concurrent_threads = [client.start_thread(workdir) for _ in range(2)]
        concurrent_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent = list(pool.map(lambda value: run_turn(client, value), concurrent_threads))
        concurrent_wall_ms = round((time.perf_counter() - concurrent_started) * 1000, 1)
        print(json.dumps({
            "version": status.get("version"),
            "startup_account_ms": startup_ms,
            "single": single,
            "concurrent": concurrent,
            "concurrent_wall_ms": concurrent_wall_ms,
            "app_server_working_set_bytes": (
                working_set_bytes(process.pid) if process is not None else None
            ),
        }, ensure_ascii=False))
        return 0
    finally:
        client.close()


def fixture_benchmark() -> int:
    fixture = str(Path("tests/fake_codex_app_server.py").resolve())
    os.environ["STOCK_HARNESS_FAKE_CODEX_MODE"] = "hold"
    held = CodexAppServerClient(
        executable=sys.executable, executable_args=(fixture,), request_timeout=2,
    )
    result: list[tuple[str, str, str]] = []
    started = threading.Event()
    try:
        thread_id = held.start_thread(Path(".tmp/fake-codex-benchmark"))
        worker = threading.Thread(target=lambda: result.append(held.run_turn(
            thread_id, "test",
            lambda kind, _data: started.set() if kind == "turn" else None,
        )))
        worker.start()
        started.wait(1)
        cancelled_at = time.perf_counter()
        held.interrupt(thread_id, "fixture-turn")
        worker.join(2)
        cancellation_ms = round((time.perf_counter() - cancelled_at) * 1000, 1)
    finally:
        held.close()

    os.environ["STOCK_HARNESS_FAKE_CODEX_MODE"] = "crash"
    crashed = CodexAppServerClient(
        executable=sys.executable, executable_args=(fixture,), request_timeout=2,
    )
    detected_at = time.perf_counter()
    try:
        thread_id = crashed.start_thread(Path(".tmp/fake-codex-benchmark"))
        detected_at = time.perf_counter()
        try:
            crashed.run_turn(thread_id, "test", lambda *_args: None)
        except Exception:
            pass
        crash_detection_ms = round((time.perf_counter() - detected_at) * 1000, 1)
    finally:
        crashed.close()
    print(json.dumps({
        "cancellation_ms": cancellation_ms,
        "cancellation_status": result[0][2] if result else "missing",
        "crash_detection_ms": crash_detection_ms,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
