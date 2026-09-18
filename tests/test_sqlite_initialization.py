from contextlib import nullcontext
import sqlite3

import pytest

from stock_harness.sqlite_initialization import InitializationStep, initialize_database
from stock_harness.sqlite_store import SQLiteMarketDataStore


def test_initializer_runs_schema_before_named_steps_and_is_repeatable():
    connection = sqlite3.connect(':memory:')
    called = []
    def first():
        assert connection.execute("select name from sqlite_master where name='instruments'").fetchone()
        called.append('first')
    steps = (InitializationStep('first', first), InitializationStep('second', lambda: called.append('second')))
    try:
        initialize_database(connection, nullcontext(), steps)
        initialize_database(connection, nullcontext(), steps)
        assert called == ['first', 'second', 'first', 'second']
    finally:
        connection.close()


def test_initialization_failure_stops_later_steps_and_preserves_exception():
    connection = sqlite3.connect(':memory:')
    failure = sqlite3.OperationalError('migration failed')
    called = []
    def fail():
        raise failure
    try:
        with pytest.raises(sqlite3.OperationalError) as raised:
            initialize_database(connection, nullcontext(), (
                InitializationStep('broken', fail),
                InitializationStep('later', lambda: called.append('unexpected')),
            ))
        assert raised.value is failure
        assert failure.__notes__ == ['database initialization step: broken']
        assert called == []
    finally:
        connection.close()


def test_store_preserves_legacy_initialization_order():
    with SQLiteMarketDataStore(':memory:') as store:
        assert [step.name for step in store._initialization_steps()] == [
            'chat-policy', 'chat-sessions', 'chat-typed-contexts', 'chat-workspace-contexts',
            'chat-context-index', 'chat-template', 'futures', 'custom-index-volume',
            'custom-group-roles', 'market-snapshot', 'analysis-target-settings',
            'analysis-scenario-type', 'screener-states', 'active-market-value',
            'signal-observation', 'board-theme', 'pinyin-backfill',
        ]
        assert store._futures_storage_ready


def test_failed_store_initialization_closes_connection(monkeypatch):
    opened = []
    connect = sqlite3.connect
    def capture(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection
    def fail(*args):
        raise RuntimeError('initialization failed')
    monkeypatch.setattr(sqlite3, 'connect', capture)
    monkeypatch.setattr('stock_harness.sqlite_initialization.initialize_database', fail)
    with pytest.raises(RuntimeError, match='initialization failed'):
        SQLiteMarketDataStore(':memory:')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        opened[0].execute('select 1')
