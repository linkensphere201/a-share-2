from datetime import date

from stock_harness.models import DailyBar, Instrument, InstrumentKind, RepairBatch
from stock_harness.repair import repair_tushare_stock_date
from stock_harness.sqlite_store import SQLiteMarketDataStore


TARGET = date(2008, 3, 28)


def _instrument(symbol):
    return Instrument(symbol, symbol, InstrumentKind.STOCK, symbol.rsplit(".", 1)[-1], False)


def _bar(symbol, close=10.5):
    return DailyBar(symbol, TARGET, 10, 11, 9, close, 12_300)


class _UniverseProvider:
    code = "baostock"

    def fetch_stock_trade_date(self, trade_date):
        assert trade_date == TARGET
        return RepairBatch(
            tuple(_instrument(symbol) for symbol in ("600000.SH", "000001.SZ", "000002.SZ")),
            (_bar("600000.SH"),),
            ("000001.SZ", "000002.SZ"),
        )


class _FallbackProvider:
    code = "akshare_eastmoney"

    def fetch_symbol_daily_bars(self, symbol, start_date, end_date):
        return [_bar(symbol)] if symbol == "000001.SZ" else []


class _TargetedProvider:
    code = "akshare_eastmoney"

    def fetch_symbol_daily_bars(self, symbol, start_date, end_date):
        return [_bar(symbol)]


def test_repair_uses_baostock_then_akshare_and_keeps_provenance():
    with SQLiteMarketDataStore(":memory:") as store:
        store.record_coverage_gap("tushare", "stock", TARGET, "empty response")
        result = repair_tushare_stock_date(
            store, TARGET, _UniverseProvider(), [_FallbackProvider()]
        )

        assert (result.status, result.expected_rows, result.repaired_rows) == ("partial", 3, 2)
        assert result.unresolved_rows == 1
        assert store.get_daily_bars("600000.SH")[0].source == "baostock"
        assert store.get_daily_bars("000001.SZ")[0].source == "akshare_eastmoney"
        job = store.list_repair_jobs()[0]
        assert (job.status, job.attempt_count, job.unresolved_rows) == ("partial", 1, 1)


def test_later_tushare_snapshot_overwrites_fallback_and_completes_job():
    with SQLiteMarketDataStore(":memory:") as store:
        store.record_coverage_gap("tushare", "stock", TARGET, "empty response")
        repair_tushare_stock_date(store, TARGET, _UniverseProvider(), [_FallbackProvider()])
        store.upsert_daily_snapshot(
            "tushare",
            "stock",
            TARGET,
            [_bar("600000.SH"), _bar("000001.SZ"), _bar("000002.SZ")],
        )

        assert store.get_daily_bars("600000.SH")[0].source == "tushare"
        assert store.get_daily_bars("000001.SZ")[0].source == "tushare"
        assert store.list_repair_jobs()[0].status == "completed"


def test_completed_job_cannot_be_downgraded_by_late_repair_failure():
    with SQLiteMarketDataStore(":memory:") as store:
        store.record_coverage_gap("tushare", "stock", TARGET, "empty response")
        job_id = store.begin_repair_job("tushare", "stock", TARGET)
        store.ensure_instruments([_instrument("600000.SH")])
        store.upsert_daily_snapshot(
            "tushare", "stock", TARGET, [_bar("600000.SH")]
        )

        store.finish_repair_job(job_id, "failed", 0, 0, 0, "late fallback failure")

        job = store.list_repair_jobs()[0]
        assert (job.status, job.last_error) == ("completed", None)


def test_targeted_invalid_bar_repair_runs_even_with_tushare_snapshot():
    with SQLiteMarketDataStore(":memory:") as store:
        store.ensure_instruments([_instrument("920489.BJ"), _instrument("600000.SH")])
        store.upsert_daily_snapshot("tushare", "stock", TARGET, [_bar("600000.SH")])
        store.record_provider_incident(
            "tushare", "daily_ohlcv", "stock", TARGET,
            "invalid_daily_bar", "920489.BJ invalid OHLC envelope"
        )
        store.queue_symbol_repairs(
            "tushare", "stock", TARGET, {"920489.BJ": "invalid OHLC envelope"}
        )

        result = repair_tushare_stock_date(
            store, TARGET, _UniverseProvider(), [], [_TargetedProvider()]
        )

        assert (result.status, result.repaired_rows, result.unresolved_rows) == (
            "completed", 1, 0
        )
        assert store.get_daily_bars("920489.BJ")[0].source == "akshare_eastmoney"
        incident = [
            item for item in store.list_provider_incidents()
            if item.incident_type == "invalid_daily_bar"
        ][0]
        assert incident.status == "resolved"
