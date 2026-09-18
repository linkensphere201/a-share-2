"""Ordered schema initialization; never run by read-only worker connections."""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
import sqlite3

from stock_harness.sqlite_schema import SCHEMA


@dataclass(frozen=True)
class InitializationStep:
    name: str
    run: Callable[[], object]


def initialize_database(connection: sqlite3.Connection,
                        writer_lock: AbstractContextManager[None],
                        steps: Sequence[InitializationStep]) -> None:
    """Run independently idempotent steps; callbacks own their transactions/state."""
    with writer_lock:
        connection.executescript(SCHEMA)
    for step in steps:
        try:
            step.run()
        except Exception as error:
            error.add_note(f"database initialization step: {step.name}")
            raise
