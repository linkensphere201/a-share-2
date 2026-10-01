"""Bounded read-only aggregation used by board-hotspot historical replay."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from datetime import date

from stock_harness.sqlite_store import SQLiteMarketDataStore


class BoardAggregatePool:
    """Calculate independent SQLite aggregates without sharing connections."""

    def __init__(self, source: SQLiteMarketDataStore, workers: int) -> None:
        if workers not in {1, 4}:
            raise ValueError("replay workers must be 1 or 4")
        self._source = source
        self._executor: ThreadPoolExecutor | None = None
        self._stores: list[SQLiteMarketDataStore] = []
        if workers == 4 and str(source.path) != ":memory:":
            self._stores = [
                SQLiteMarketDataStore(
                    source.path,
                    cache_size_kib=8_192,
                    mmap_size_mib=source.mmap_size_mib,
                    temp_store="FILE",
                    busy_timeout_ms=source.busy_timeout_ms,
                    read_only=True,
                )
                for _ in range(3)
            ]
            self._executor = ThreadPoolExecutor(
                max_workers=3, thread_name_prefix="hotspot-replay",
            )

    @property
    def worker_count(self) -> int:
        return 3 if self._executor is not None else 1

    def calculate(self, effective: date, *, check_stopping: Callable[[], None] = lambda: None) -> tuple[
        dict[str, dict[str, object]],
        dict[str, dict[str, object]],
        dict[str, dict[str, object]],
    ]:
        methods = (
            "calculate_board_hotspot_snapshots",
            "calculate_board_breadth_snapshots",
            "calculate_board_capacity_snapshots",
        )
        results = []
        if self._executor is None:
            for method in methods:
                check_stopping()
                results.append(getattr(self._source, method)(effective))
        else:
            futures = []
            for store, method in zip(self._stores, methods, strict=True):
                check_stopping()
                futures.append(self._executor.submit(getattr(store, method), effective))
            for future in futures:
                check_stopping()
                results.append(future.result())
        check_stopping()
        return tuple(results)  # type: ignore[return-value]

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
        for store in self._stores:
            store.close()

    def __enter__(self) -> BoardAggregatePool:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
