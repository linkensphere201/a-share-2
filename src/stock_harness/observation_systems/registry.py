"""Deterministic in-process registry for observation-system plugins."""

from __future__ import annotations

from stock_harness.observation_systems.contracts import (
    ObservationSystemContext,
    ObservationSystemExecution,
    ObservationSystemPlugin,
)


class ObservationSystemRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, ObservationSystemPlugin] = {}

    def register(self, plugin: ObservationSystemPlugin) -> None:
        if not plugin.system_id or not plugin.version:
            raise ValueError("observation system id and version are required")
        if plugin.system_id in self._plugins:
            raise ValueError(f"duplicate observation system: {plugin.system_id}")
        self._plugins[plugin.system_id] = plugin

    def get(self, system_id: str) -> ObservationSystemPlugin:
        try:
            return self._plugins[system_id]
        except KeyError as error:
            raise ValueError(f"unknown observation system: {system_id}") from error

    def definitions(self) -> list[dict[str, object]]:
        return [plugin.definition() for plugin in self._plugins.values()]

    def execute_all(
        self, context: ObservationSystemContext,
    ) -> list[ObservationSystemExecution]:
        executions = []
        for plugin in self._plugins.values():
            missing = [
                name for name in plugin.dependencies
                if name not in context.dependencies
            ]
            if missing:
                executions.append(ObservationSystemExecution(
                    plugin.system_id, plugin.version, plugin.entity_scope, [],
                    f"missing dependencies: {', '.join(missing)}",
                ))
                continue
            try:
                executions.append(plugin.execute(context))
            except Exception as error:
                executions.append(ObservationSystemExecution(
                    plugin.system_id, plugin.version, plugin.entity_scope, [],
                    f"{type(error).__name__}: {error}",
                ))
        return executions
