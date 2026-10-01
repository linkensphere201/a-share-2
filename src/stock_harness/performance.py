"""Bounded process-local counters; no symbols, SQL, credentials or per-call retention."""
from contextlib import contextmanager
from functools import wraps
from threading import Lock, RLock
from time import perf_counter

STAGES = frozenset({"input", "prefilter", "detector", "context", "persistence", "db-lock", "writer-lock", "api"})
_lock = Lock()
_values: dict[str, dict[str, float | int]] = {}


def observe(stage: str, seconds: float, size: int = 0) -> None:
    if stage not in STAGES:
        raise ValueError("unknown performance stage")
    with _lock:
        value = _values.setdefault(stage, {"count": 0, "total_ms": 0., "max_ms": 0., "bytes": 0})
        value["count"] += 1
        value["total_ms"] += seconds * 1000
        value["max_ms"] = max(value["max_ms"], seconds * 1000)
        value["bytes"] += size


def snapshot() -> dict[str, dict[str, float | int]]:
    with _lock:
        return {key: dict(value) for key, value in _values.items()}


@contextmanager
def measure(stage: str):
    started = perf_counter()
    try:
        yield
    finally:
        observe(stage, perf_counter() - started)


def measured(stage: str):
    if stage not in STAGES:
        raise ValueError("unknown performance stage")
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with measure(stage):
                return function(*args, **kwargs)
        return call
    return decorate


class MeasuredRLock:
    """Store context-manager lock, retaining reentrancy and measuring acquisition only."""
    def __init__(self):
        self._lock = RLock()

    def __enter__(self):
        started = perf_counter()
        self._lock.acquire()
        observe("db-lock", perf_counter() - started)
        return self

    def __exit__(self, *_args):
        self._lock.release()
