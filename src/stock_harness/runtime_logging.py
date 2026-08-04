"""Rotating application logs plus a bounded WARN/ERROR event feed for the UI."""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_id: int
    timestamp: str
    level: str
    source: str
    logger: str
    message: str


class RuntimeEventBuffer:
    def __init__(self, capacity: int = 500) -> None:
        self._events: deque[RuntimeEvent] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._next_id = 1

    def append(self, level: str, source: str, logger: str, message: str) -> RuntimeEvent:
        with self._lock:
            event = RuntimeEvent(
                event_id=self._next_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                level=level.upper(),
                source=source,
                logger=logger,
                message=message[:1000],
            )
            self._next_id += 1
            self._events.append(event)
            return event

    def list(self, after_id: int = 0, min_level: str = "WARNING", limit: int = 100) -> list[dict[str, object]]:
        threshold = _level_number(min_level)
        with self._lock:
            events = [
                event for event in self._events
                if event.event_id > after_id and _level_number(event.level) >= threshold
            ][-limit:]
        return [asdict(event) for event in events]


class RuntimeEventHandler(logging.Handler):
    def __init__(self, buffer: RuntimeEventBuffer) -> None:
        super().__init__(logging.WARNING)
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "skip_runtime_event", False):
            return
        try:
            self.buffer.append(record.levelname, "backend", record.name, record.getMessage())
        except Exception:
            self.handleError(record)


EVENT_BUFFER = RuntimeEventBuffer()


def configure_runtime_logging(log_dir: Path, level: int = logging.INFO) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "stock-harness.log"
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(threadName)s %(name)s %(message)s"
    )
    if not any(getattr(handler, "_stock_harness_file", False) for handler in root.handlers):
        file_handler = RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler._stock_harness_file = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    if not any(isinstance(handler, RuntimeEventHandler) for handler in root.handlers):
        root.addHandler(RuntimeEventHandler(EVENT_BUFFER))
    logging.getLogger(__name__).info("runtime_logging_ready path=%s", log_path)
    return log_path


def record_frontend_event(level: str, message: str, logger: str = "frontend") -> RuntimeEvent:
    normalized = level.upper()
    event = EVENT_BUFFER.append(normalized, "frontend", logger, message)
    logging.getLogger(f"frontend.{logger}").log(
        _level_number(normalized), message, extra={"skip_runtime_event": True}
    )
    return event


def _level_number(level: str) -> int:
    return {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }.get(level.upper(), logging.WARNING)
