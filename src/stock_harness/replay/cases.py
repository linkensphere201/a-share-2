"""Deterministic success and failure cases derived from shared replay outcomes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


CASE_LIBRARY_VERSION = "replay-case-library-v1"


def select_replay_cases(
    evaluations: Sequence[Mapping[str, object]], *, per_outcome: int = 8,
) -> list[dict[str, object]]:
    """Select balanced auditable cases without inspecting names or narratives."""
    if per_outcome <= 0:
        raise ValueError("per_outcome must be positive")
    groups: dict[str, list[dict[str, object]]] = {"success": [], "failure": []}
    for evaluation in evaluations:
        signal = _mapping(evaluation.get("signal"))
        lifecycle = _mapping(evaluation.get("maximum_holding"))
        outcome = (
            lifecycle if lifecycle.get("status") == "complete"
            else _mapping(evaluation.get("reference_close"))
        )
        event = str(outcome.get("first_boundary_event") or "")
        label = "success" if event == "target-first" else "failure" if event == "invalidation-first" else None
        if label is None:
            continue
        horizons = _mapping(outcome.get("horizons"))
        groups[label].append({
            "case_version": CASE_LIBRARY_VERSION,
            "outcome": label,
            "system_id": signal.get("system_id"),
            "system_version": signal.get("system_version"),
            "symbol": signal.get("symbol"),
            "scope": signal.get("scope"),
            "setup_family": signal.get("setup_family"),
            "signal_date": signal.get("signal_date"),
            "entry_price": signal.get("reference_close"),
            "target_price": signal.get("selected_target_price"),
            "invalidation_price": signal.get("invalidation_price"),
            "score": signal.get("score"),
            "first_boundary_event": event,
            "first_boundary_event_session": outcome.get("first_boundary_event_session"),
            "returns": {
                horizon: _mapping(value).get("close_return")
                for horizon, value in horizons.items()
                if isinstance(value, Mapping) and value.get("status") == "complete"
            },
            "metadata": dict(_mapping(signal.get("metadata"))),
        })
    selected = []
    for label in ("success", "failure"):
        groups[label].sort(key=lambda item: (
            int(item.get("first_boundary_event_session") or 10_000),
            -float(item.get("score") or 0),
            str(item.get("signal_date") or ""), str(item.get("symbol") or ""),
        ))
        selected.extend(groups[label][:per_outcome])
    return selected


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}
