"""One bounded read-only connection per background operation; no migrations."""
from contextlib import contextmanager

from stock_harness.sqlite_store import SQLiteMarketDataStore


@contextmanager
def background_reader(source: SQLiteMarketDataStore):
    if str(source.path) == ":memory:":
        yield source
        return
    with SQLiteMarketDataStore(source.path, read_only=True, cache_size_kib=8192,
                              mmap_size_mib=source.mmap_size_mib, temp_store="FILE",
                              busy_timeout_ms=source.busy_timeout_ms) as reader:
        yield reader
