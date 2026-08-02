from datetime import date

from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.validation import validate_symbols


class _Validator:
    code = "validator"

    def __init__(self, close=10.5):
        self.close = close

    def fetch_symbol_daily_bars(self, symbol, start_date, end_date):
        return [DailyBar(symbol, start_date, 10, 11, 9, self.close, 12_300)]


def _store():
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([Instrument("600000.SH", "PF Bank", InstrumentKind.STOCK, "SH")])
    store.upsert_daily_bars(
        "tushare", [DailyBar("600000.SH", date(1998, 7, 27), 10, 11, 9, 10.5, 12_300)]
    )
    return store


def test_validation_persists_match():
    with _store() as store:
        summary = validate_symbols(
            store, "tushare", [_Validator()], ["600000.SH"], date(1998, 7, 27)
        )
        assert summary.matched == 1
        assert store.list_validation_results(date(1998, 7, 27))[0].status == "match"


def test_incident_can_be_resolved_after_retry():
    with _store() as store:
        incident_id = store.record_provider_incident(
            "tushare", "daily_ohlcv", "stock", date(1998, 7, 27),
            "empty_open_trade_date", "empty response"
        )
        assert incident_id > 0
        assert store.resolve_provider_incident(
            "tushare", "daily_ohlcv", "stock", date(1998, 7, 27),
            "empty_open_trade_date", "retry returned bars"
        )
        incident = store.list_provider_incidents()[0]
        assert incident.status == "resolved"
        assert incident.occurrence_count == 1


def test_coverage_gap_trigger_opens_incident_and_snapshot_resolves_it():
    with _store() as store:
        target = date(1998, 7, 28)
        store.record_coverage_gap("tushare", "stock", target, "empty response")
        assert store.list_provider_incidents()[0].status == "open"
        assert store.list_repair_jobs()[0].status == "queued"
        store.upsert_daily_snapshot(
            "tushare",
            "stock",
            target,
            [DailyBar("600000.SH", target, 10, 11, 9, 10.5, 12_300)],
        )
        incident = store.list_provider_incidents()[0]
        assert incident.status == "resolved"
        assert incident.resolution == "daily snapshot stored after provider retry"
