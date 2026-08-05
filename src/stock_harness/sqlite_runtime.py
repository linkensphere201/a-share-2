"""Transaction and interprocess writer-lock primitives for SQLite."""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from contextlib import AbstractContextManager
from pathlib import Path


class Transaction:
    def __init__(
        self,
        connection: sqlite3.Connection,
        writer_lock: AbstractContextManager[None],
    ) -> None:
        self.connection = connection
        self.writer_lock = writer_lock

    def __enter__(self) -> None:
        self.writer_lock.__enter__()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self.writer_lock.__exit__(*sys.exc_info())
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.connection.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.writer_lock.__exit__(exc_type, exc, traceback)


class ThreadOnlyWriterLock(AbstractContextManager[None]):
    def __init__(self) -> None:
        self._lock = threading.RLock()

    def __enter__(self) -> None:
        self._lock.acquire()

    def __exit__(self, *_args: object) -> None:
        self._lock.release()


class InterprocessWriterLock(AbstractContextManager[None]):
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle = None

    def __enter__(self) -> None:
        import msvcrt

        started = time.monotonic()
        handle = self.path.open("a+b")
        if handle.seek(0, 2) == 0:
            handle.write(b"\0")
            handle.flush()
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                self._handle = handle
                return
            except OSError:
                if time.monotonic() - started >= self.timeout_seconds:
                    handle.close()
                    raise TimeoutError(f"timed out waiting for SQLite writer lock: {self.path}")
                time.sleep(0.05)

    def __exit__(self, *_args: object) -> None:
        import msvcrt

        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._handle.close()
            self._handle = None
