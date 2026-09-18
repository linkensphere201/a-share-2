import sqlite3
import pytest
from stock_harness.models import Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def test_read_view_skips_migrations_and_rejects_writes(tmp_path, monkeypatch):
    path = tmp_path / "market.sqlite"
    with SQLiteMarketDataStore(path) as writer:
        writer.upsert_instruments([Instrument("000001.SZ", "Fixture", InstrumentKind.STOCK, "SZ")])
        monkeypatch.setattr("stock_harness.sqlite_initialization.initialize_database",
                            lambda *_: pytest.fail("read view must not initialize schema"))
        with SQLiteMarketDataStore(path, read_only=True) as reader:
            assert reader.get_instrument_kind("000001.SZ") == InstrumentKind.STOCK
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.upsert_instruments([Instrument("000002.SZ", "Forbidden", InstrumentKind.STOCK, "SZ")])


def test_read_view_requires_existing_database(tmp_path):
    with pytest.raises(ValueError):
        SQLiteMarketDataStore(tmp_path / "missing.sqlite", read_only=True)
    assert not (tmp_path / "missing.sqlite").exists()
