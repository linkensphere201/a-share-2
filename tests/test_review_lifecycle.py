from datetime import date
from threading import Event, Thread

import pytest

from stock_harness.signal_review import SignalReviewService, SignalReviewBusyError, DAILY_MARKET_BOARD_SIGNAL
from stock_harness.sqlite_store import SQLiteMarketDataStore


def test_review_close_drains_accepted_work_before_storage_shutdown(monkeypatch):
    entered, release, closed = Event(), Event(), Event()
    with SQLiteMarketDataStore(":memory:") as store:
        service = SignalReviewService(store)
        def execute(*args):
            entered.set()
            assert release.wait(5)
            store.list_signal_review_runs()
        monkeypatch.setattr(service, "_execute_daily", execute)
        service.start_run(DAILY_MARKET_BOARD_SIGNAL, date(2026, 9, 17))
        assert entered.wait(2)
        worker = Thread(target=lambda: (service.close(), closed.set()))
        worker.start()
        try:
            assert not closed.wait(.05)
            with pytest.raises(SignalReviewBusyError):
                service.start_run(DAILY_MARKET_BOARD_SIGNAL, date(2026, 9, 17))
        finally:
            release.set()
            worker.join(5)
        assert closed.is_set()
