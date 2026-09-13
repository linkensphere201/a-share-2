"""Validated in-process registry for complete review-system objects."""

from __future__ import annotations

import time

from stock_harness.review_systems.contracts import (
    AnalysisSystemContext,
    AnalysisSystemExecution,
    ReviewAnalysisSystem,
    validate_analysis_result,
    validate_system_definition,
)


class ReviewAnalysisSystemRegistry:
    def __init__(self) -> None:
        self._systems: dict[str, ReviewAnalysisSystem] = {}

    def register(self, system: ReviewAnalysisSystem) -> None:
        validate_system_definition(system.definition)
        if system.definition.system_id in self._systems:
            raise ValueError(f"duplicate analysis system: {system.definition.system_id}")
        self._systems[system.definition.system_id] = system

    def get(self, system_id: str) -> ReviewAnalysisSystem:
        try:
            return self._systems[system_id]
        except KeyError as error:
            raise ValueError(f"unknown analysis system: {system_id}") from error

    def definitions(self) -> list[dict[str, object]]:
        return [system.definition.to_dict() for system in self._systems.values()]

    def execute_all(self, context: AnalysisSystemContext) -> list[AnalysisSystemExecution]:
        return [self.execute(system_id, context) for system_id in self._systems]

    def execute(
        self, system_id: str, context: AnalysisSystemContext,
    ) -> AnalysisSystemExecution:
        system = self.get(system_id)
        definition = system.definition
        missing = [name for name in definition.dependencies if name not in context.dependencies]
        if missing:
            return AnalysisSystemExecution(
                definition.system_id, definition.version, (), 0,
                "missing dependencies: " + ", ".join(missing),
            )
        started = time.perf_counter()
        try:
            results = tuple(system.analyze(context))
            for result in results:
                validate_analysis_result(result, definition)
            return AnalysisSystemExecution(
                definition.system_id, definition.version, results,
                round((time.perf_counter() - started) * 1000, 3),
            )
        except Exception as error:
            return AnalysisSystemExecution(
                definition.system_id, definition.version, (),
                round((time.perf_counter() - started) * 1000, 3),
                f"{type(error).__name__}: {error}",
            )
