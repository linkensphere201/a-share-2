"""Cross-session lifecycle projection for board hotspot waves."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
import hashlib


HOTSPOT_WAVE_VERSION = "hotspot-wave-v1"


def project_hotspot_waves(
    scores: Sequence[dict[str, object]],
    prior_snapshots: Sequence[Mapping[str, object]],
    effective_date: date,
    prior_sequences: Mapping[str, int] | None = None,
) -> list[dict[str, object]]:
    """Collapse aliases to trading leaves and advance each leaf once per session."""
    prior_sequences = prior_sequences or {}
    representatives = _theme_representatives(scores)
    prior_by_theme = {
        str(item["theme_id"]): item for item in prior_snapshots
        if str(item.get("status") or "") == "active"
    }
    snapshots: list[dict[str, object]] = []
    for theme_id in sorted(set(representatives) | set(prior_by_theme)):
        score = representatives.get(theme_id)
        prior = prior_by_theme.get(theme_id)
        snapshot = _advance_wave(
            theme_id, score, prior, effective_date,
            int(prior_sequences.get(theme_id, 0)),
        )
        if snapshot is None:
            continue
        snapshots.append(snapshot)
        if score is not None:
            for candidate in scores:
                if _theme_id(candidate) == theme_id:
                    candidate.update(_score_wave_projection(
                        snapshot, candidate is score,
                    ))
    return snapshots


def _advance_wave(
    theme_id: str,
    score: Mapping[str, object] | None,
    prior: Mapping[str, object] | None,
    effective_date: date,
    historical_sequence: int,
) -> dict[str, object] | None:
    daily_stage = str((score or {}).get("hotspot_stage") or "failed")
    signal_stage = _signal_wave_stage(daily_stage)
    if prior is None and (
        signal_stage in {None, "diverging", "exhausted"}
        or not bool((score or {}).get("radar_visible"))
    ):
        return None

    if prior is None:
        sequence = historical_sequence + 1
        started_on = effective_date.isoformat()
        wave_id = _wave_id(theme_id, sequence, started_on)
        stage = signal_stage or "ignition"
        transition = "started"
        weak_sessions = 0
        invisible_sessions = 0
        confirmed_on = effective_date.isoformat() if stage in {
            "confirmed", "advancing", "reaccelerating",
        } else None
        peak_score = _number((score or {}).get("total_score")) or 0.0
        peak_on = effective_date.isoformat()
        session_count = 1
    else:
        sequence = int(prior["wave_sequence"])
        started_on = str(prior["started_on"])
        wave_id = str(prior["wave_id"])
        previous_stage = str(prior["stage"])
        weak_sessions = int(prior.get("weak_session_count") or 0)
        invisible_sessions = int(prior.get("invisible_session_count") or 0)
        session_count = int(prior.get("session_count") or 0) + 1
        confirmed_on = prior.get("confirmed_on")
        peak_score = float(prior.get("peak_score") or 0.0)
        peak_on = str(prior.get("peak_on") or started_on)
        currently_visible = bool((score or {}).get("radar_visible"))
        if not currently_visible and signal_stage not in {None, "exhausted"}:
            invisible_sessions += 1
            if invisible_sessions >= 3:
                stage, transition = "ended", "visibility-ended"
            else:
                stage, transition = "diverging", "left-visible-seat"
        else:
            stage, weak_sessions, transition = _transition(
                previous_stage, signal_stage, weak_sessions,
            )
            invisible_sessions = 0 if currently_visible else invisible_sessions + 1
        if confirmed_on is None and stage in {
            "confirmed", "advancing", "reaccelerating",
        }:
            confirmed_on = effective_date.isoformat()

    latest_score = _number((score or {}).get("total_score")) or 0.0
    if latest_score >= peak_score:
        peak_score = latest_score
        peak_on = effective_date.isoformat()
    status = "ended" if stage == "ended" else "active"
    return {
        "wave_id": wave_id,
        "wave_version": HOTSPOT_WAVE_VERSION,
        "theme_id": theme_id,
        "theme_name": str((score or prior or {}).get("theme_name") or theme_id),
        "theme_parent_id": (score or prior or {}).get("theme_parent_id"),
        "theme_parent_name": (score or prior or {}).get("theme_parent_name"),
        "theme_registry_version": (score or prior or {}).get("theme_registry_version"),
        "wave_sequence": sequence,
        "effective_date": effective_date.isoformat(),
        "status": status,
        "stage": stage,
        "transition": transition,
        "daily_stage": daily_stage,
        "started_on": started_on,
        "confirmed_on": confirmed_on,
        "peak_on": peak_on,
        "ended_on": effective_date.isoformat() if status == "ended" else None,
        "session_count": session_count,
        "weak_session_count": weak_sessions,
        "invisible_session_count": invisible_sessions,
        "peak_score": round(peak_score, 2),
        "latest_score": round(latest_score, 2),
        "representative_symbol": (score or prior or {}).get("symbol"),
        "radar_visible": bool((score or {}).get("radar_visible")),
    }


def _transition(
    previous: str, signal: str | None, weak_sessions: int,
) -> tuple[str, int, str]:
    if signal is None or signal == "exhausted":
        next_weak = weak_sessions + 1
        if next_weak >= 2:
            return "ended", next_weak, "ended"
        return "exhausted", next_weak, "weakened"
    if signal == "diverging":
        next_weak = weak_sessions + 1
        if next_weak >= 3:
            return "ended", next_weak, "ended"
        return "diverging", next_weak, "diverged"
    if previous in {"diverging", "exhausted"}:
        return "reaccelerating", 0, "reaccelerated"
    if signal == previous:
        return signal, 0, "continued"
    return signal, 0, "advanced"


def _signal_wave_stage(stage: str) -> str | None:
    return {
        "leader-ignited": "ignition",
        "trend-emerging": "emerging",
        "breadth-expanding": "confirmed",
        "hotspot-confirmed": "confirmed",
        "accelerating": "advancing",
        "diverging": "diverging",
        "exhausted": "exhausted",
    }.get(stage)


def _theme_representatives(
    scores: Sequence[dict[str, object]],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for score in scores:
        if not bool(score.get("theme_signal_eligible", True)):
            continue
        theme_id = _theme_id(score)
        current = result.get(theme_id)
        if current is None or _representative_key(score) > _representative_key(current):
            result[theme_id] = score
    return result


def _theme_id(score: Mapping[str, object]) -> str:
    return str(score.get("canonical_theme") or score.get("symbol") or "")


def _representative_key(score: Mapping[str, object]) -> tuple[int, float, float, str]:
    return (
        int(bool(score.get("radar_visible"))),
        _number(score.get("visibility_score")) or 0.0,
        _number(score.get("total_score")) or 0.0,
        str(score.get("symbol") or ""),
    )


def _wave_id(theme_id: str, sequence: int, started_on: str) -> str:
    digest = hashlib.sha1(
        f"{HOTSPOT_WAVE_VERSION}:{theme_id}:{sequence}:{started_on}".encode("utf-8")
    ).hexdigest()[:16]
    return f"wave-{digest}"


def _score_wave_projection(
    snapshot: Mapping[str, object], representative: bool,
) -> dict[str, object]:
    return {
        "hotspot_wave_id": snapshot["wave_id"],
        "hotspot_wave_sequence": snapshot["wave_sequence"],
        "hotspot_wave_status": snapshot["status"],
        "hotspot_wave_stage": snapshot["stage"],
        "hotspot_wave_transition": snapshot["transition"],
        "hotspot_wave_started_on": snapshot["started_on"],
        "hotspot_wave_confirmed_on": snapshot["confirmed_on"],
        "hotspot_wave_peak_on": snapshot["peak_on"],
        "hotspot_wave_session_count": snapshot["session_count"],
        "hotspot_wave_weak_session_count": snapshot["weak_session_count"],
        "hotspot_wave_invisible_session_count": snapshot["invisible_session_count"],
        "hotspot_wave_representative": representative,
    }


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None
