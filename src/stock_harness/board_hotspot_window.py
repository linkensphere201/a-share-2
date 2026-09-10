"""Causal rolling-window evidence for board-hotspot admission."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


BOARD_HOTSPOT_WINDOW_VERSION = "board-hotspot-window-v1-five-session-shape"
WINDOW_SESSIONS = 5


def analyze_hotspot_window(
    current: Mapping[str, object],
    recent_results: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Classify a compact five-session evidence path without future inputs."""
    current_sample = _sample(current)
    prior_samples = list(reversed(_recent_samples(recent_results)))
    samples = [*prior_samples, current_sample][-WINDOW_SESSIONS:]
    evidence_scores = [int(_number(item.get("evidence_score")) or 0) for item in samples]
    qualified = [score >= 5 for score in evidence_scores]
    shape_supported = [str(item.get("shape_path") or "none") != "none" for item in samples]
    latest_score = evidence_scores[-1]
    recent_three = qualified[-3:]
    shape_three = shape_supported[-3:]
    qualified_count = sum(qualified)
    prior_qualified_count = sum(qualified[:-1])
    latest_shape = str(current_sample["shape_path"])
    extension_atr = _number(current_sample.get("extension_from_ma20_atr"))
    position60 = _number(current_sample.get("position_60"))
    return20 = _number(current_sample.get("return_20")) or 0.0
    prior_volumes = sorted(
        value for item in samples[:-1]
        if (value := _number(item.get("volume_ratio_20"))) is not None
    )
    prior_volume_median = (
        prior_volumes[len(prior_volumes) // 2] if prior_volumes else None
    )
    current_volume = _number(current_sample.get("volume_ratio_20"))
    activity_climax = (
        current_volume is not None and current_volume >= 2.2
        and prior_volume_median is not None
        and current_volume >= prior_volume_median * 1.5
    )
    overextended = (
        extension_atr is not None and extension_atr >= 4.0
    ) or (
        position60 is not None and position60 >= .95
        and extension_atr is not None and extension_atr >= 3.0
    ) or return20 >= .28
    prior_state = str(
        recent_results[0].get("hotspot_window_state") or ""
    ) if recent_results else ""

    if len(samples) < 2:
        state = "insufficient"
    elif overextended and latest_score >= 5:
        state = "overextended"
    elif latest_score >= 5 and prior_qualified_count == 0:
        state = "pulse"
    elif latest_score <= 3 and prior_qualified_count:
        state = "fading"
    elif sum(recent_three) >= 2 and sum(shape_three) >= 2 and latest_score >= 4:
        state = "persistent" if qualified_count >= 3 else "building"
    else:
        state = "fragmented"
    if state in {"building", "persistent"} and prior_state == "fading":
        state = "reaccelerating"

    delta = latest_score - evidence_scores[-2] if len(evidence_scores) >= 2 else 0
    fit_score = {
        "persistent": 8.0,
        "reaccelerating": 6.0,
        "building": 12.0,
        "fragmented": -8.0,
        "pulse": -14.0,
        "fading": -16.0,
        "overextended": -18.0,
        "insufficient": -20.0,
    }[state]
    if delta >= 2:
        fit_score += 3.0
    elif delta < 0:
        fit_score -= 3.0
    if latest_shape in {"platform-breakout", "downtrend-reversal"}:
        fit_score += 3.0
    if activity_climax:
        fit_score -= 6.0
        failure_reasons = ["single-session-activity-climax"]
    else:
        failure_reasons = []

    failure_reasons.extend(_failure_reasons(current_sample))
    if state == "pulse":
        failure_reasons.append("single-session-pulse")
    elif state == "overextended":
        failure_reasons.append("shape-overextended")
    elif state == "fading":
        failure_reasons.append("window-evidence-fading")
    elif state == "fragmented":
        failure_reasons.append("window-evidence-fragmented")
    elif state == "insufficient":
        failure_reasons.append("insufficient-window-history")

    return {
        "version": BOARD_HOTSPOT_WINDOW_VERSION,
        "state": state,
        "eligible": state in {"building", "persistent", "reaccelerating"},
        "observed_sessions": len(samples),
        "qualified_sessions": qualified_count,
        "latest_evidence_score": latest_score,
        "evidence_delta": delta,
        "shape_support_sessions": sum(shape_supported),
        "shape_path": latest_shape,
        "overextended": overextended,
        "activity_climax": activity_climax,
        "fit_score": round(fit_score, 2),
        "failure_reasons": failure_reasons,
        "sample": current_sample,
    }


def _sample(current: Mapping[str, object]) -> dict[str, object]:
    return5 = _number(current.get("return_5"))
    return20 = _number(current.get("return_20"))
    rs5 = _number(current.get("relative_strength_5"))
    volume = _number(current.get("volume_ratio_20"))
    volume_persistence = _number(current.get("volume_persistence_5"))
    breadth = _number(current.get("positive_return_5_ratio"))
    limit_up_ratio = _number(current.get("limit_up_ratio")) or 0.0
    max_streak = int(_number(current.get("max_limit_up_streak")) or 0)
    shape_path = str(current.get("shape_path") or "none")
    long_shape_state = str(current.get("long_shape_state") or "unavailable")
    shape_coherent = _shape_context_coherent(shape_path, long_shape_state)
    dimensions = {
        "price": return5 is not None and return5 >= .03,
        "relative_strength": rs5 is not None and rs5 >= .02,
        "activity": (
            volume_persistence is not None and volume_persistence >= 1.03
        ) or (volume is not None and volume >= 1.15),
        "breadth": breadth is not None and breadth >= .56,
        "leader": max_streak >= 2 or limit_up_ratio >= .02,
        "shape": shape_path != "none" and shape_coherent,
    }
    return {
        "evidence_score": sum(dimensions.values()),
        "dimensions": dimensions,
        "return_5": return5,
        "return_20": return20,
        "relative_strength_5": rs5,
        "volume_ratio_20": volume,
        "volume_persistence_5": volume_persistence,
        "positive_return_5_ratio": breadth,
        "limit_up_ratio": limit_up_ratio,
        "max_limit_up_streak": max_streak,
        "shape_path": shape_path,
        "long_shape_state": long_shape_state,
        "position_60": _number(current.get("position_60")),
        "extension_from_ma20_atr": _number(
            current.get("extension_from_ma20_atr")
        ),
    }


def _shape_context_coherent(path: str, long_state: str) -> bool:
    if path == "none":
        return False
    if long_state == "unavailable":
        return True
    if path == "downtrend-reversal":
        return long_state in {"falling", "sideways"}
    return long_state in {"rising", "sideways"}


def _failure_reasons(sample: Mapping[str, object]) -> list[str]:
    dimensions = _mapping(sample.get("dimensions"))
    return [
        f"weak-{name.replace('_', '-')}"
        for name, supported in dimensions.items() if not bool(supported)
    ]


def _recent_samples(
    recent_results: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    samples = []
    seen_dates: set[str] = set()
    for result in recent_results:
        effective_date = str(result.get("effective_date") or "")
        if effective_date and effective_date in seen_dates:
            continue
        sample = _mapping(result.get("hotspot_window_sample"))
        if not sample:
            continue
        samples.append(sample)
        if effective_date:
            seen_dates.add(effective_date)
        if len(samples) >= WINDOW_SESSIONS - 1:
            break
    return samples


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
