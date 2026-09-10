"""Contracts for independently versioned observation systems."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

@dataclass(frozen=True)
class ObservationSystemContext:
    observations: Sequence[Mapping[str, object]]
    prior_scores: Mapping[str, Mapping[str, Mapping[str, object]]] = field(
        default_factory=dict
    )
    recent_scores: Mapping[
        str, Mapping[str, Sequence[Mapping[str, object]]]
    ] = field(default_factory=dict)
    dependencies: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ObservationSystemExecution:
    system_id: str
    version: str
    entity_scope: str
    results: list[dict[str, object]]
    error: str | None = None


class ObservationSystemPlugin(Protocol):
    system_id: str
    version: str
    entity_scope: str
    display_name: str
    dependencies: tuple[str, ...]

    def definition(self) -> dict[str, object]: ...

    def execute(
        self, context: ObservationSystemContext,
    ) -> ObservationSystemExecution: ...
