"""Deterministic presentation layers over the immutable stock candidate archive."""

from __future__ import annotations

from collections.abc import Mapping


ALGORITHM_VERSION = "stock-observation-presentation-v5"
M4_ALLOCATOR_VERSION = "stock-m4-multilane-v1"
FOCUS_LIMIT = 100
RISK_LIMIT = 100
FOCUS_NEW_INCUMBENCY_BONUS = 36.0
FOCUS_MATURE_INCUMBENCY_BONUS = 28.0
FOCUS_AGED_INCUMBENCY_BONUS = 18.0
FOCUS_LONG_STAY_BONUS = 8.0
FOCUS_ENTRY_MARGIN = 10.0
ACTIONABLE_STATES = {"waiting-trigger", "triggered", "retest"}
RISK_CLASSIFICATIONS = {
    "independent-decline", "one-session-event-anomaly",
    "decaying-independent-move",
}


def assign_stock_presentation_layers(
    snapshot: dict[str, object], *, focus_limit: int = FOCUS_LIMIT,
    risk_limit: int = RISK_LIMIT,
) -> dict[str, object]:
    """Freeze bounded UI layers while preserving every candidate item."""
    if focus_limit <= 0 or risk_limit <= 0:
        raise ValueError("presentation limits must be positive")
    items = [item for item in snapshot.get("items", []) if isinstance(item, dict)]
    for item in items:
        payload = _payload(item)
        for key in (
            "presentation_bucket", "presentation_rank",
            "presentation_reasons", "presentation_version", "presentation_lane",
        ):
            payload.pop(key, None)

    assigned: set[str] = set()
    opportunity = sorted(
        (item for item in items if _opportunity_eligible(item)),
        key=_focus_key,
    )
    _assign(opportunity, "opportunity", assigned)

    manual = sorted(
        (item for item in items if _manual(item) and _symbol(item) not in assigned),
        key=_focus_key,
    )
    _assign(manual, "focus", assigned)

    risk_candidates = sorted(
        (item for item in items if _symbol(item) not in assigned and _risk(item)),
        key=_risk_key,
    )[:risk_limit]
    _assign(risk_candidates, "risk", assigned)

    focus_candidates = _bounded_focus(items, assigned, focus_limit)
    _assign(focus_candidates, "focus", assigned, start_rank=len(manual) + 1)

    archive = sorted(
        (item for item in items if _symbol(item) not in assigned),
        key=lambda item: (int(item.get("rank") or 0), _symbol(item)),
    )
    _assign(archive, "archive", assigned)

    counts = {
        bucket: sum(
            _payload(item).get("presentation_bucket") == bucket for item in items
        )
        for bucket in ("opportunity", "focus", "risk", "archive")
    }
    summary = snapshot.setdefault("summary", {})
    if not isinstance(summary, dict):
        raise ValueError("stock pool summary must be a mapping")
    summary["presentation"] = {
        "algorithm_version": ALGORITHM_VERSION,
        "focus_automatic_limit": focus_limit,
        "risk_automatic_limit": risk_limit,
        "manual_focus_count": len(manual),
        "counts": counts,
    }
    return summary["presentation"]


def _assign(
    items: list[dict[str, object]], bucket: str, assigned: set[str],
    *, start_rank: int = 1,
) -> None:
    for rank, item in enumerate(items, start_rank):
        payload = _payload(item)
        payload["presentation_bucket"] = bucket
        payload["presentation_rank"] = rank
        payload["presentation_reasons"] = _reasons(item, bucket)
        payload["presentation_version"] = ALGORITHM_VERSION
        payload["presentation_lane"] = _focus_lane(item) if bucket == "focus" else bucket
        assigned.add(_symbol(item))


def _focus_key(item: Mapping[str, object]) -> tuple[object, ...]:
    payload = _payload(item)
    analysis = _mapping(payload.get("m4_analysis"))
    scenario = _mapping(analysis.get("scenario"))
    score = _mapping(payload.get("opportunity_score"))
    return (
        not bool(score.get("eligible")),
        analysis.get("status") != "succeeded",
        str(scenario.get("state")) not in ACTIONABLE_STATES,
        not bool(payload.get("screener_results")),
        -_readiness(payload),
        not bool(payload.get("recognized")),
        not _scan_eligible(payload),
        -_number(score.get("total_score")),
        -_number(payload.get("independent_score")),
        int(item.get("rank") or 0),
        _symbol(item),
    )


def _risk_key(item: Mapping[str, object]) -> tuple[object, ...]:
    payload = _payload(item)
    classifications = _classifications(payload)
    severity = (
        0 if "independent-decline" in classifications
        or item.get("lifecycle_state") == "invalidated"
        else 1 if "one-session-event-anomaly" in classifications
        else 2
    )
    return (
        severity,
        -_number(payload.get("independent_score")),
        int(item.get("rank") or 0),
        _symbol(item),
    )


def _focus(item: Mapping[str, object]) -> bool:
    if item.get("lifecycle_state") in {"cooldown", "invalidated"}:
        return False
    payload = _payload(item)
    if not _selection_qualified(payload):
        return bool(payload.get("manual_pinned"))
    analysis = _mapping(payload.get("m4_analysis"))
    scenario = _mapping(analysis.get("scenario"))
    return bool(
        payload.get("recognized")
        or payload.get("screener_results")
        or _readiness(payload) > 0
        or _scan_eligible(payload)
        or _was_focus(item)
        or str(scenario.get("state")) in ACTIONABLE_STATES
    )


def _risk(item: Mapping[str, object]) -> bool:
    return bool(
        _payload(item).get("risk_name")
        or
        item.get("lifecycle_state") == "invalidated"
        or _classifications(_payload(item)) & RISK_CLASSIFICATIONS
    )


def _opportunity_eligible(item: Mapping[str, object]) -> bool:
    return bool(_mapping(_payload(item).get("opportunity_score")).get("eligible"))


def _scan_eligible(payload: Mapping[str, object]) -> bool:
    return any(
        bool(_mapping(payload.get(key)).get("eligible"))
        for key in ("independent_scan", "member_scan")
    )


def _selection_qualified(payload: Mapping[str, object]) -> bool:
    scans = [
        _mapping(payload.get(key)) for key in ("independent_scan", "member_scan")
        if _mapping(payload.get(key))
    ]
    return any(scan.get("selection_qualified", True) for scan in scans) if scans else True


def _classifications(payload: Mapping[str, object]) -> set[str]:
    return {
        str(value)
        for key in ("independent_scan", "member_scan")
        if (value := _mapping(payload.get(key)).get("classification"))
    }


def _reasons(item: Mapping[str, object], bucket: str) -> list[str]:
    payload = _payload(item)
    if bucket == "opportunity":
        return ["strict-m4-opportunity"]
    if bucket == "archive":
        return ["candidate-evidence-retained"]
    result = []
    if _manual(item):
        result.append("manual-pinned")
    if payload.get("recognized"):
        result.append("board-recognition")
    if _scan_eligible(payload):
        result.append("independent-strength")
    if payload.get("screener_results"):
        result.append("screener-result")
    if _readiness(payload) > 0:
        result.append(f"readiness-{_focus_lane(item)}")
    classifications = _classifications(payload)
    result.extend(sorted(classifications & RISK_CLASSIFICATIONS))
    if item.get("lifecycle_state") in {"invalidated", "weakened"}:
        result.append(f"lifecycle-{item['lifecycle_state']}")
    analysis = _mapping(payload.get("m4_analysis"))
    scenario = _mapping(analysis.get("scenario"))
    if str(scenario.get("state")) in ACTIONABLE_STATES:
        result.append(f"m4-{scenario['state']}")
    return list(dict.fromkeys(result)) or [f"{bucket}-ranking"]


def _manual(item: Mapping[str, object]) -> bool:
    return bool(_payload(item).get("manual_pinned"))


def _payload(item: Mapping[str, object]) -> dict[str, object]:
    value = item.get("payload")
    return value if isinstance(value, dict) else {}


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _symbol(item: Mapping[str, object]) -> str:
    return str(item.get("symbol") or "").upper()


def _bounded_focus(
    items: list[dict[str, object]], assigned: set[str], limit: int,
) -> list[dict[str, object]]:
    lane_floors = {
        "screener": 5, "retest": 10, "breakout": 10,
        "critical": 10, "recognized": 5, "independent": 5,
    }
    used = set(assigned)
    candidates = [
        item for item in items
        if _symbol(item) not in used and not _risk(item) and _focus(item)
    ]
    ordered = sorted(candidates, key=_dynamic_focus_key)
    incumbents = [
        item for item in ordered if _was_focus(item) and _retention_qualified(item)
    ]
    entrants = [item for item in ordered if not _was_focus(item)]
    selected = incumbents[:limit]
    for entrant in entrants:
        if len(selected) < limit:
            selected.append(entrant)
            continue
        weakest = max(selected, key=_dynamic_focus_key)
        if (
            (_hard_focus_signal(entrant) and not _hard_focus_signal(weakest))
            or _focus_quality(entrant) >= (
                _focus_quality(weakest) + FOCUS_ENTRY_MARGIN
            )
        ):
            selected.remove(weakest)
            selected.append(entrant)
    selected_symbols = {_symbol(item) for item in selected}

    # Floors preserve discovery diversity without forcing every lane to refill a
    # large quota each day. The weakest non-protected seat is replaced only when
    # a lane would otherwise disappear from the bounded range.
    for lane, floor in lane_floors.items():
        available = [item for item in ordered if _focus_lane(item) == lane]
        target = min(floor, len(available), limit)
        while sum(_focus_lane(item) == lane for item in selected) < target:
            entrant = next(
                (item for item in available if _symbol(item) not in selected_symbols),
                None,
            )
            if entrant is None:
                break
            replaceable = [
                item for item in selected
                if not _hard_focus_signal(item)
                and sum(
                    _focus_lane(value) == _focus_lane(item) for value in selected
                ) > min(lane_floors.get(_focus_lane(item), 0), limit)
            ]
            if not replaceable:
                break
            incumbent = max(replaceable, key=_dynamic_focus_key)
            selected.remove(incumbent)
            selected_symbols.remove(_symbol(incumbent))
            selected.append(entrant)
            selected_symbols.add(_symbol(entrant))
    return sorted(selected, key=_dynamic_focus_key)[:limit]


def _dynamic_focus_key(item: Mapping[str, object]) -> tuple[object, ...]:
    """Rank current quality with a small, bounded incumbent advantage."""
    return (not _hard_focus_signal(item), -_focus_quality(item), *_focus_key(item))


def _hard_focus_signal(item: Mapping[str, object]) -> bool:
    payload = _payload(item)
    scenario = _mapping(_mapping(payload.get("m4_analysis")).get("scenario"))
    return bool(
        payload.get("screener_results")
        or _opportunity_eligible(item)
        or str(scenario.get("state")) in {"triggered", "retest"}
    )


def _focus_quality(item: Mapping[str, object]) -> float:
    payload = _payload(item)
    analysis = _mapping(payload.get("m4_analysis"))
    scenario = _mapping(analysis.get("scenario"))
    phase = _focus_lane(item)
    phase_bonus = {
        "screener": 12.0, "breakout": 9.0, "critical": 7.0,
        "retest": 6.0, "recognized": 4.0,
    }.get(phase, 0.0)
    score = (
        _number(payload.get("independent_score")) * 0.45
        + _readiness(payload) * 0.35
        + _number(_mapping(payload.get("opportunity_score")).get("total_score")) * 0.10
        + phase_bonus
        + (8.0 if payload.get("recognized") else 0.0)
        + (6.0 if _scan_eligible(payload) else 0.0)
        + (8.0 if str(scenario.get("state")) in ACTIONABLE_STATES else 0.0)
    )
    if _was_focus(item) and _retention_qualified(item):
        streak = int(payload.get("focus_streak_sessions") or 0)
        score += _incumbency_bonus(streak)
    return score


def _incumbency_bonus(streak: int) -> float:
    if streak <= 3:
        return FOCUS_NEW_INCUMBENCY_BONUS
    if streak <= 10:
        return FOCUS_MATURE_INCUMBENCY_BONUS
    if streak <= 20:
        return FOCUS_AGED_INCUMBENCY_BONUS
    return FOCUS_LONG_STAY_BONUS


def _retention_qualified(item: Mapping[str, object]) -> bool:
    """Require current evidence; prior membership alone cannot hold a seat."""
    payload = _payload(item)
    analysis = _mapping(payload.get("m4_analysis"))
    scenario = _mapping(analysis.get("scenario"))
    return bool(
        _selection_qualified(payload)
        and not _risk(item)
        and (
            payload.get("recognized")
            or payload.get("screener_results")
            or _scan_eligible(payload)
            or _readiness(payload) > 0
            or _number(payload.get("independent_score")) >= 50.0
            or str(scenario.get("state")) in ACTIONABLE_STATES
        )
    )


def _was_focus(item: Mapping[str, object]) -> bool:
    return _payload(item).get("previous_presentation_bucket") in {
        "focus", "opportunity",
    }


def select_stock_m4_candidates(
    items: list[dict[str, object]], limit: int,
) -> list[dict[str, object]]:
    """Allocate expensive analysis across discovery lanes without gating focus."""
    quotas = (("screener", 6), ("retest", 6), ("breakout", 6),
              ("critical", 6), ("recognized", 3), ("independent", 3))
    selected: list[dict[str, object]] = []
    used: set[str] = set()
    has_presentation = any(
        _payload(item).get("presentation_bucket") for item in items
    )
    eligible = [
        item for item in items
        if not bool(_payload(item).get("risk_name"))
        and item.get("lifecycle_state") not in {"cooldown", "invalidated"}
        and (
            not has_presentation
            or _payload(item).get("presentation_bucket") == "focus"
        )
    ]
    for lane, quota in quotas:
        matching = sorted(
            (item for item in eligible if _focus_lane(item) == lane),
            key=_focus_key,
        )[:quota]
        for item in matching:
            symbol = _symbol(item)
            if symbol not in used:
                selected.append(item)
                used.add(symbol)
    for item in sorted(eligible, key=_focus_key):
        if len(selected) >= limit:
            break
        symbol = _symbol(item)
        if symbol not in used:
            selected.append(item)
            used.add(symbol)
    return selected[:limit]


def _focus_lane(item: Mapping[str, object]) -> str:
    payload = _payload(item)
    if payload.get("manual_pinned"):
        return "manual"
    if payload.get("screener_results"):
        return "screener"
    phases = {
        str(_mapping(_mapping(payload.get(key)).get("metrics")).get("opportunity_phase") or "")
        for key in ("independent_scan", "member_scan")
    }
    for phase in ("retest", "breakout", "critical"):
        if phase in phases:
            return phase
    if payload.get("recognized"):
        return "recognized"
    return "independent"


def _readiness(payload: Mapping[str, object]) -> float:
    return max((
        _number(_mapping(_mapping(payload.get(key)).get("metrics")).get(
            "opportunity_readiness_score"
        ))
        for key in ("independent_scan", "member_scan")
    ), default=0.0)
