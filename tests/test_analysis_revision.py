from datetime import date
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.analysis_revision import DEPENDENCIES


def test_revision_ignores_results_tracks_source_writes_and_rollbacks(tmp_path):
    path = tmp_path / "revision.sqlite"
    with SQLiteMarketDataStore(path) as first:
        first.upsert_instruments([Instrument("600001.SH", "Test", InstrumentKind.STOCK, "SH")])
        with SQLiteMarketDataStore(path) as second:
            before = first.analysis_read_version()
            run = second.create_screener_run("test", "v1", date(2026, 9, 18), {})
            second.update_screener_progress(run["run_id"], universe_count=1, scanned_count=0)
            second.complete_screener_run(run["run_id"], [])
            assert first.analysis_read_version() == before
            second.upsert_daily_bars("test", [DailyBar("600001.SH", date(2026, 9, 18), 10, 11, 9, 10, 100)])
            assert first.analysis_read_version() != before
            before = first.analysis_read_version()
            second._connection.execute("BEGIN IMMEDIATE")
            second._connection.execute("UPDATE instruments SET name='rolled back'")
            second._connection.execute("ROLLBACK")
            assert first.analysis_read_version() == before
            tables = {r[0] for r in first._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            triggers = {r[0] for r in first._connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            for table in set(DEPENDENCIES) & tables:
                assert all(f"analysis_revision_{table}_{op}" in triggers for op in ("INSERT", "UPDATE", "DELETE"))
