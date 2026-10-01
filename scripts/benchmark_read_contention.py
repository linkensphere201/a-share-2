"""Isolated file-backed fixture: chart API latency during paged background reads."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from pathlib import Path
from statistics import median
import threading
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.performance import snapshot
from stock_harness.sqlite_store import SQLiteMarketDataStore


def main():
    folder = Path(".tmp") / f"read-benchmark-{uuid4().hex}"
    folder.mkdir(parents=True)
    with SQLiteMarketDataStore(folder / "fixture.sqlite") as store:
        symbols = [f"{600000 + i}.SH" for i in range(100)]
        days = [date(2025, 1, 1) + timedelta(days=i) for i in range(500)]
        store.upsert_instruments([Instrument(s, "Fixture", InstrumentKind.STOCK, "SH") for s in symbols])
        started = perf_counter()
        stats = store.upsert_daily_bars("fixture", [DailyBar(s, d, 10, 11, 9, 10, 1000) for s in symbols for d in days])
        print(json.dumps({"fixture_path": str(folder), "write_rows": stats.changed,
                          "write_ms_with_revision_triggers": (perf_counter() - started) * 1000}))
        with TestClient(create_app(store)) as client, SQLiteMarketDataStore(store.path, read_only=True) as reader:
            for isolated in (False, True):
                source = reader if isolated else store
                ready, stop = threading.Event(), threading.Event()
                before = snapshot()

                def background():
                    count = 0
                    ready.set()
                    while not stop.is_set():
                        source.get_recent_daily_bars_many(symbols, days[-1], 500)
                        count += 1
                    return count

                durations = []
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(background)
                    ready.wait()
                    try:
                        for symbol in symbols[:30]:
                            start = perf_counter()
                            response = client.get(f"/api/instruments/{symbol}/daily-bars")
                            response.raise_for_status()
                            assert len(response.json()["items"]) == 500
                            durations.append((perf_counter() - start) * 1000)
                    finally:
                        stop.set()
                    pages = future.result()
                after = snapshot()
                print(json.dumps({"isolated": isolated, "requests": len(durations), "background_pages": pages,
                    "api_median_ms": median(durations), "api_p95_ms": sorted(durations)[28],
                    "db_lock_wait_ms": after["db-lock"]["total_ms"] - before.get("db-lock", {}).get("total_ms", 0)}))


if __name__ == "__main__":
    main()
