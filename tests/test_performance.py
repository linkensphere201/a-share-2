import pytest
from stock_harness.performance import MeasuredRLock, measured, observe, snapshot


def test_bounded_metrics_copy_isolation_exceptions_and_reentrant_lock():
    before = snapshot().get("detector", {}).get("count", 0)
    @measured("detector")
    def fail():
        raise RuntimeError("expected")
    with pytest.raises(RuntimeError):
        fail()
    assert snapshot()["detector"]["count"] == before + 1
    copied = snapshot()
    copied["detector"]["count"] = -1
    assert snapshot()["detector"]["count"] == before + 1
    with pytest.raises(ValueError):
        observe("unbounded-symbol", 1)
    lock = MeasuredRLock()
    with lock, lock:
        assert snapshot()["db-lock"]["count"] >= 2
