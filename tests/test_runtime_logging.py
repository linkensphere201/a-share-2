import logging
from pathlib import Path

from stock_harness import runtime_logging


class _FakeFileHandler(logging.Handler):
    attempts: list[Path] = []

    def __init__(self, path: Path, **_: object) -> None:
        super().__init__()
        self.attempts.append(Path(path))
        if len(self.attempts) == 1:
            raise PermissionError("primary log is locked")

    def emit(self, record: logging.LogRecord) -> None:
        pass


def test_runtime_logging_falls_back_when_primary_log_is_locked(
    tmp_path: Path, monkeypatch,
) -> None:
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    _FakeFileHandler.attempts = []
    root.handlers = [
        handler for handler in root.handlers
        if not getattr(handler, "_stock_harness_file", False)
        and not isinstance(handler, runtime_logging.RuntimeEventHandler)
    ]
    monkeypatch.setattr(runtime_logging, "RotatingFileHandler", _FakeFileHandler)
    try:
        selected = runtime_logging.configure_runtime_logging(tmp_path)
    finally:
        root.handlers = original_handlers

    assert selected == tmp_path / "stock-harness-fallback.log"
    assert _FakeFileHandler.attempts == [
        tmp_path / "stock-harness.log",
        tmp_path / "stock-harness-fallback.log",
    ]
