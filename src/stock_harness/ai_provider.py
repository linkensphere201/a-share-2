"""Provider-neutral contract for application-owned analysis conversations."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol


AiEventHandler = Callable[[str, dict[str, object]], None]


class AiConversationProvider(Protocol):
    """Minimal local contract a future direct model API adapter must implement."""

    def status(self) -> dict[str, object]: ...
    def thread_policy_version(self) -> str: ...
    def start_thread(self, workdir: Path) -> str: ...
    def ensure_thread(self, thread_id: str, workdir: Path) -> None: ...
    def run_turn(
        self, thread_id: str, prompt: str, on_event: AiEventHandler
    ) -> tuple[str, str, str]: ...
    def interrupt(self, thread_id: str, turn_id: str) -> None: ...
    def close(self) -> None: ...


PROVIDER_EVENT_TYPES = {
    "queued", "started", "status", "delta", "reasoning", "tool-started",
    "tool-completed", "citation", "usage", "warning", "completed", "failed",
    "cancelled",
}
