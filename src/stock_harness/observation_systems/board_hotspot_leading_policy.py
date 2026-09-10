"""Pure causal feature policy for the board-hotspot leading radar."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


LEADING_WINDOW_SESSIONS = 5
LEADING_MIN_TRAJECTORY_SESSIONS = 3


def build_leading_sample(feature: Mapping[str, object]) -> dict[str, object]:
    metrics = _mapping(feature.get("metrics"))
    returns = _mapping(metrics.get("returns"))
    relative = _mapping(metrics.get("relative_strength"))
    shape = _mapping(metrics.get("hotspot_shape"))
    members = _mapping(feature.get("member_snapshot"))
    breadth = _mapping(metrics.get("board_breadth"))
    return5 = _number(returns.get("5"))
    return20 = _number(returns.get("20"))
    rs20 = _number(relative.get("20"))
    breadth5 = _number(members.get("positive_return_5_ratio"))
    activity = _number(metrics.get("recent_volume_ratio_5_5"))
    limits = int(_number(members.get("limit_up_count")) or 0)
    broken = int(_number(members.get("broken_up_count")) or 0)
    streak = int(_number(members.get("max_limit_up_streak")) or 0)
    consecutive = int(
        _number(members.get("consecutive_limit_up_count")) or 0
    )
    active_leaders = int(
        _number(members.get("active_limit_up_members_5")) or 0
    )
    failed_limit_ratio = broken / (limits + broken) if limits + broken else 0.0
    extension = _number(shape.get("extension_from_ma20_atr"))
    objective_confirmed = (
        return5 is not None and return5 >= .04
        and return20 is not None and return20 >= .08
        and rs20 is not None and rs20 >= .025
        and breadth5 is not None and breadth5 >= .56
        and ((activity is not None and activity >= 1.03) or limits >= 1 or streak >= 2)
    )
    return {
        "return_1": _number(returns.get("1")),
        "return_3": _number(returns.get("3")),
        "return_5": return5,
        "return_20": return20,
        "relative_strength_3": _number(relative.get("3")),
        "relative_strength_5": _number(relative.get("5")),
        "relative_strength_20": rs20,
        "volume_ratio_20": _number(metrics.get("volume_ratio20")),
        "volume_persistence_5": activity,
        "breadth": _number(breadth.get("breadth")),
        "positive_return_5_ratio": breadth5,
        "limit_up_count": limits,
        "broken_up_count": broken,
        "max_limit_up_streak": streak,
        "consecutive_limit_up_count": consecutive,
        "active_limit_up_members_5": active_leaders,
        "failed_limit_ratio": failed_limit_ratio,
        "max_member_return_5": _number(members.get("max_member_return_5")),
        "average_member_return_5": _number(members.get("average_member_return_5")),
        "shape_path": str(shape.get("path") or "none"),
        "breakout_20_atr": _number(shape.get("breakout20_atr")),
        "range_compression_5_20": _number(shape.get("range_compression_5_20")),
        "extension_from_ma20_atr": extension,
        "objective_confirmed": objective_confirmed,
        "overextended": (
            (extension is not None and extension >= 3.0)
            or (return20 is not None and return20 >= .24)
            or (return5 is not None and return5 >= .14)
        ),
    }


def leading_dimensions(
    current: Mapping[str, object], deltas: Mapping[str, float | None],
) -> dict[str, bool]:
    boundary = _number(current.get("breakout_20_atr"))
    compression = _number(current.get("range_compression_5_20"))
    shape_path = str(current.get("shape_path") or "none")
    extension = _number(current.get("extension_from_ma20_atr"))
    structure = (
        boundary is not None and -.9 <= boundary <= .8
        and (extension is None or extension <= 2.5)
    ) or shape_path in {"platform-breakout", "downtrend-reversal"}
    if shape_path == "trend-continuation":
        structure = structure and (_number(current.get("return_20")) or 0.0) <= .16
    return {
        "structure_proximity": structure or (
            compression is not None and compression <= .82
            and boundary is not None and boundary >= -1.2
        ),
        "relative_strength_acceleration": (
            (_number(current.get("relative_strength_5")) or -1.0) >= .005
            and (deltas.get("relative_strength") or 0.0) >= .003
        ),
        "activity_persistence": (
            (_number(current.get("volume_persistence_5")) or 0.0) >= .95
            and .85 <= (_number(current.get("volume_ratio_20")) or 0.0) <= 2.1
        ),
        "member_diffusion": (
            (_number(current.get("positive_return_5_ratio")) or 0.0) >= .50
            and (
                (deltas.get("breadth") or 0.0) >= .025
                or (_number(current.get("breadth")) or -1.0) >= .15
            )
        ),
        "leader_formation": _leader_supported(current),
    }


def leading_trajectory(
    samples: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    window = list(samples)[-LEADING_WINDOW_SESSIONS:]
    transitions = []
    for previous, current in zip(window, window[1:]):
        changes = {
            "price": _delta(current, previous, "return_5"),
            "relative_strength": _delta(current, previous, "relative_strength_5"),
            "activity": _delta(current, previous, "volume_persistence_5"),
            "breadth": _delta(current, previous, "positive_return_5_ratio"),
        }
        improving = sum((
            (changes["price"] or 0.0) >= .002,
            (changes["relative_strength"] or 0.0) >= .002,
            (changes["activity"] or 0.0) >= .02,
            (changes["breadth"] or 0.0) >= .01,
        ))
        transitions.append({
            "improving_dimensions": improving,
            "coherent": improving >= 2,
        })
    recent_transitions = transitions[-2:]
    coherent_transitions = sum(bool(item["coherent"]) for item in recent_transitions)
    improving_transitions = sum(
        int(item["improving_dimensions"]) >= 1 for item in recent_transitions
    )
    candidate_sessions = sum(bool(item.get("candidate")) for item in window)
    support_sessions = sum(
        (_number(item.get("volume_persistence_5")) or 0.0) >= .95
        and (
            (_number(item.get("positive_return_5_ratio")) or 0.0) >= .50
            or _leader_supported(item)
        )
        for item in window[-3:]
    )
    current = window[-1] if window else {}
    return3 = _number(current.get("return_3"))
    return5 = _number(current.get("return_5"))
    return20 = _number(current.get("return_20"))
    extension = _number(current.get("extension_from_ma20_atr"))
    late_pulse = (
        return3 is not None and return3 >= .05
        and return5 is not None and return5 >= .07
        and (return20 is None or return20 < .08)
        and extension is not None and extension >= 1.8
    )
    observed = len(window)
    return {
        "observed_sessions": observed,
        "candidate_sessions": candidate_sessions,
        "improving_transitions": improving_transitions,
        "coherent_transitions": coherent_transitions,
        "support_sessions": support_sessions,
        "late_pulse": late_pulse,
        "eligible": (
            observed >= LEADING_MIN_TRAJECTORY_SESSIONS
            and candidate_sessions >= 2
            and improving_transitions >= 2
            and coherent_transitions >= 1
            and support_sessions >= 2
            and not late_pulse
        ),
    }


def structure_is_actionable(current: Mapping[str, object]) -> bool:
    path = str(current.get("shape_path") or "none")
    boundary = _number(current.get("breakout_20_atr"))
    compression = _number(current.get("range_compression_5_20"))
    extension = _number(current.get("extension_from_ma20_atr"))
    if path == "platform-breakout":
        return (
            boundary is not None and -.4 <= boundary <= .5
            and compression is not None and compression <= 1.1
            and (extension is None or extension <= 1.8)
        )
    if path == "downtrend-reversal":
        return (
            boundary is not None and -.6 <= boundary <= .6
            and compression is not None and compression <= 1.15
            and (extension is None or extension <= 2.0)
        )
    if path == "trend-continuation":
        return (
            compression is not None and compression <= 1.1
            and (extension is None or extension <= 1.8)
            and (_number(current.get("return_20")) or 0.0) <= .16
        )
    return (
        boundary is not None and -.8 <= boundary <= .25
        and compression is not None and compression <= .78
        and (extension is None or extension <= 1.5)
    )


def leading_components(
    current: Mapping[str, object], deltas: Mapping[str, float | None],
    dimensions: Mapping[str, bool],
) -> dict[str, float]:
    compression = _number(current.get("range_compression_5_20"))
    return {
        "structure_proximity": 22.0 if dimensions["structure_proximity"] else 0.0,
        "relative_strength_acceleration": (
            10.0 if (_number(current.get("relative_strength_5")) or 0.0) >= .01 else 5.0
        ) + min(10.0, max(0.0, (deltas.get("relative_strength") or 0.0) / .02 * 10)),
        "activity_persistence": (
            8.0 if dimensions["activity_persistence"] else 0.0
        ) + min(8.0, max(0.0, (deltas.get("activity") or 0.0) / .25 * 8)),
        "member_diffusion": (
            8.0 if dimensions["member_diffusion"] else 0.0
        ) + min(10.0, max(0.0, (deltas.get("breadth") or 0.0) / .20 * 10)),
        "leader_formation": (8.0 if dimensions["leader_formation"] else 0.0) + (
            8.0 if int(_number(current.get("max_limit_up_streak")) or 0) >= 2
            or int(_number(current.get("consecutive_limit_up_count")) or 0) >= 1
            else 0.0
        ),
        "volatility_compression": (
            8.0 if compression is not None and compression <= .82 else 0.0
        ),
    }


def leading_penalties(current: Mapping[str, object]) -> list[dict[str, object]]:
    penalties: list[dict[str, object]] = []
    volume = _number(current.get("volume_ratio_20"))
    persistence = _number(current.get("volume_persistence_5"))
    if volume is not None and volume >= 2.2 and (persistence is None or persistence < 1.1):
        penalties.append({"code": "single-session-volume-pulse", "points": 18.0})
    if bool(current.get("overextended")):
        penalties.append({"code": "price-overextended", "points": 24.0})
    if (_number(current.get("return_5")) or 0.0) < -.02:
        penalties.append({"code": "short-price-momentum-negative", "points": 10.0})
    if (_number(current.get("failed_limit_ratio")) or 0.0) >= .5:
        penalties.append({"code": "leader-failed-to-seal", "points": 12.0})
    return penalties


def _leader_supported(current: Mapping[str, object]) -> bool:
    streak = int(_number(current.get("max_limit_up_streak")) or 0)
    consecutive = int(_number(current.get("consecutive_limit_up_count")) or 0)
    limits = int(_number(current.get("limit_up_count")) or 0)
    failed_ratio = _number(current.get("failed_limit_ratio")) or 0.0
    return streak >= 2 or consecutive >= 1 or (limits >= 2 and failed_ratio < .5)


def _delta(
    current: Mapping[str, object], previous: Mapping[str, object], key: str,
) -> float | None:
    current_value = _number(current.get(key))
    previous_value = _number(previous.get(key))
    if current_value is None or previous_value is None:
        return None
    return current_value - previous_value


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None
