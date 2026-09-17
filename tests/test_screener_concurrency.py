from datetime import date
import threading
from time import monotonic, sleep

import pytest

from stock_harness.screener import (
    ScreenerService, ScreenerBusyError, STRATEGY_ID, BULL_FLAG_STRATEGY_ID,
    PULLBACK_STRATEGY_ID,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


def wait_until(predicate):
    deadline = monotonic() + 5
    while not predicate():
        assert monotonic() < deadline
        sleep(.01)


def test_two_strategies_overlap_third_queues_and_failure_releases_slot(monkeypatch):
    with SQLiteMarketDataStore(":memory:") as store:
        service = ScreenerService(store)
        release = threading.Event()
        entered = []
        lock = threading.Lock()

        def execute(run_id, cutoff, periods, states, limit, strategy):
            with lock:
                entered.append(strategy)
            store.update_screener_progress(run_id, universe_count=100, scanned_count=7)
            assert release.wait(5)
            if strategy == STRATEGY_ID:
                raise RuntimeError("isolated test failure")
            store.complete_screener_run(run_id, [])

        monkeypatch.setattr(service, "_execute", execute)
        try:
            first = service.start_run([], [], 10, date(2026, 9, 16), STRATEGY_ID)
            second = service.start_run([], [], 10, date(2026, 9, 16), BULL_FLAG_STRATEGY_ID)
            wait_until(lambda: len(entered) == 2)
            third = service.start_run([], [], 10, date(2026, 9, 16), PULLBACK_STRATEGY_ID)
            assert third["execution_state"] == "queued"
            assert third["queue_position"] == 1
            for strategy in (STRATEGY_ID, BULL_FLAG_STRATEGY_ID, PULLBACK_STRATEGY_ID):
                with pytest.raises(ScreenerBusyError):
                    service.start_run([], [], 10, date(2026, 9, 17), strategy)
            assert len(store.list_screener_runs()) == 3
            assert store.get_screener_run(first["run_id"])["scanned_count"] == 7
            assert store.get_screener_run(second["run_id"])["scanned_count"] == 7
            assert store.get_screener_run(third["run_id"])["scanned_count"] == 0
            release.set()
            wait_until(lambda: not service._jobs)
            assert store.get_screener_run(first["run_id"])["status"] == "failed"
            assert store.get_screener_run(second["run_id"])["status"] == "succeeded"
            assert store.get_screener_run(third["run_id"])["status"] == "succeeded"
            assert entered[-1] == PULLBACK_STRATEGY_ID
        finally:
            release.set()
            service.close()


def test_shutdown_drains_queue_before_store_close(monkeypatch):
    with SQLiteMarketDataStore(":memory:") as store:
        service = ScreenerService(store)
        entered = []

        def execute(*args):
            entered.append(args[0])
            assert service._stopping.wait(5)
            service._check_stopping()

        monkeypatch.setattr(service, "_execute", execute)
        runs = [service.start_run([], [], 10, date(2026, 9, 16), strategy)
                for strategy in (STRATEGY_ID, BULL_FLAG_STRATEGY_ID, PULLBACK_STRATEGY_ID)]
        wait_until(lambda: len(entered) == 2)
        service.close()
        assert len(entered) == 2
        assert not service._jobs
        assert all(store.get_screener_run(run["run_id"])["status"] == "failed" for run in runs)
        with pytest.raises(ScreenerBusyError):
            service.start_run([], [], 10, date(2026, 9, 16))
