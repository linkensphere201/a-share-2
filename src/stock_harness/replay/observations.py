"""Validate frozen analytical claims without assuming orders or positions."""

from collections import Counter
from collections.abc import Mapping, Sequence
from statistics import median
from dataclasses import replace

from stock_harness.models import StoredDailyBar
from stock_harness.replay.contracts import FrozenSignal


def evaluate_observation(
    signal: FrozenSignal, bars: Sequence[StoredDailyBar], *, include_layers: bool = True,
) -> dict:
    target, invalidation = signal.selected_target_price, signal.invalidation_price
    limit = signal.observation_sessions
    source = "explicit" if limit else "unbounded"
    if limit is None:
        legacy = signal.metadata.get("maximum_holding_sessions")
        if type(legacy) is int and legacy > 0:
            limit, source = legacy, "legacy-window"
    base = {
        "window_sessions": limit, "window_source": source,
        "reference_price": signal.reference_close,
        "target_price": target, "invalidation_price": invalidation,
        "boundary_rule": "frozen-price-touch-v1",
        "claim_stage": signal.metadata.get("state", "unspecified"),
    }
    if target is None or invalidation is None:
        return {**base, "status": "not-evaluable", "reason": "missing-boundaries"}
    sign = 1 if signal.direction == "long" else -1
    if not (sign * (target - signal.reference_close) > 0
            and sign * (signal.reference_close - invalidation) > 0):
        return {**base, "status": "not-evaluable", "reason": "invalid-boundary-order"}
    window = bars[:limit] if limit else bars
    status, event_session, event_date = "pending", None, None
    observed = []
    for session, bar in enumerate(window, 1):
        observed.append(bar)
        target_hit = bar.high >= target if sign == 1 else bar.low <= target
        invalid_hit = bar.low <= invalidation if sign == 1 else bar.high >= invalidation
        if target_hit or invalid_hit:
            status = ("ambiguous" if target_hit and invalid_hit else
                      "target-reached" if target_hit else "invalidated")
            event_session, event_date = session, bar.trade_date.isoformat()
            break
    if status == "pending" and limit is not None and len(window) >= limit:
        status = "expired-unfulfilled"
    favorable = adverse = None
    if observed:
        moves = [sign * (price / signal.reference_close - 1)
                 for bar in observed for price in (bar.high, bar.low)]
        favorable, adverse = max(0, max(moves)), min(0, min(moves))
    result = {
        **base, "status": status, "event_session": event_session,
        "event_date": event_date, "observed_sessions": len(observed),
        "observed_through": observed[-1].trade_date.isoformat() if observed else None,
        "favorable_excursion": favorable, "adverse_excursion": adverse,
        "excursion_basis": "whole-daily-bars-through-event-order-unknown",
        "within_7_sessions": event_session <= 7 if event_session else None,
    }
    if include_layers:
        result["target_layers"] = []
        for item in signal.metadata.get("observation_targets", ()):
            if isinstance(item, Mapping) and isinstance(item.get("price"), (int, float)):
                layer = evaluate_observation(
                    replace(signal, selected_target_price=float(item["price"])),
                    bars, include_layers=False,
                )
                result["target_layers"].append({"label": item.get("label"), **layer})
    return result


def observation_events(evaluations: Sequence[Mapping]) -> list[dict]:
    """Only explicit causal identities merge; never guess episodes from future outcomes."""
    groups = {}
    for index, value in enumerate(evaluations):
        signal = value.get("signal", {})
        identity = signal.get("observation_id")
        key = (signal.get("system_id"), signal.get("system_version"),
               signal.get("symbol"), signal.get("scope"), signal.get("setup_family"),
               signal.get("direction"), identity or ("unidentified", index))
        groups.setdefault(key, []).append(value)
    events = []
    for group in groups.values():
        group.sort(key=lambda v: v["signal"]["signal_date"])
        first = group[0]
        events.append({
            "signal": first["signal"], "observation": first.get("observation", {}),
            "daily_updates": [{"date": v["signal"]["signal_date"],
                               "score": v["signal"].get("score"),
                               "stage": v["signal"].get("metadata", {}).get("state")}
                              for v in group],
            "identity_available": bool(first["signal"].get("observation_id")),
        })
    return events


def _counts(events: Sequence[Mapping]) -> dict:
    values = [v.get("observation", {}) for v in events]
    counts = Counter(v.get("status", "legacy-not-evaluated") for v in values)
    completed = sum(counts[s] for s in ("target-reached", "invalidated", "expired-unfulfilled"))
    times = [v["event_session"] for v in values if v.get("status") == "target-reached"]
    favorable = [v["favorable_excursion"] for v in values if v.get("favorable_excursion") is not None]
    adverse = [v["adverse_excursion"] for v in values if v.get("adverse_excursion") is not None]
    return {
        "count": len(events), "status_counts": dict(counts),
        "conclusive_count": completed,
        "target_rate": counts["target-reached"] / completed if completed else None,
        "median_target_sessions": median(times) if times else None,
        "targets_within_7_sessions": sum(t <= 7 for t in times),
        "median_favorable_excursion": median(favorable) if favorable else None,
        "median_adverse_excursion": median(adverse) if adverse else None,
    }


def summarize_observations(evaluations: Sequence[Mapping]) -> dict:
    events = observation_events(evaluations)
    buckets = {}
    for event in events:
        s = event["signal"]
        score = s.get("score")
        bucket = "unscored" if score is None else f"{min(9, int(score // 10)) * 10}-{min(9, int(score // 10)) * 10 + 10}"
        # Scores are comparable only within the same system/version/scope/family.
        key = "|".join(str(s.get(k) or "unspecified") for k in
                       ("system_id", "system_version", "scope", "setup_family")) + "|" + bucket
        buckets.setdefault(key, []).append(event)
    result = _counts(events)
    counts = result["status_counts"]
    result.update({
        "dated_claim_count": len(evaluations),
        "merged_updates": len(evaluations) - len(events),
        "claims_without_event_identity": sum(not v["identity_available"] for v in events),
        "score_buckets": {key: _counts(values) for key, values in sorted(buckets.items())},
        "summary": (
            f"观察记录{len(events)}项：目标达成{counts.get('target-reached', 0)}项，"
            f"先失效{counts.get('invalidated', 0)}项，"
            f"到期未兑现{counts.get('expired-unfulfilled', 0)}项，"
            f"待观察{counts.get('pending', 0)}项，"
            f"同日顺序不明{counts.get('ambiguous', 0)}项，"
            f"无法评估{counts.get('not-evaluable', 0) + counts.get('legacy-not-evaluated', 0)}项。"
        ),
        "coverage": {"status": "not-evaluated", "reason": "requires-independent-wave-labels"},
    })
    return result


def evaluate_wave_coverage(events: Sequence[Mapping], labels: Sequence[Mapping]) -> dict:
    """Independent retrospective labels are evaluation-only, never selection inputs.

    Each label supplies symbol, scope, system_id, start_date and a frozen list of
    eligible detection dates (trading sessions). This avoids calendar-day guesses.
    """
    matched, leads = 0, []
    for label in labels:
        dates = list(label["detection_dates"])
        hits = [event["signal"]["signal_date"] for event in events
                if all(event["signal"].get(k) == label[k]
                       for k in ("symbol", "scope", "system_id"))
                and event["signal"]["signal_date"] in dates]
        if hits:
            matched += 1
            if label["start_date"] in dates:
                leads.append(dates.index(label["start_date"]) - dates.index(min(hits)))
    return {
        "label_count": len(labels), "detected_count": matched,
        "coverage_rate": matched / len(labels) if labels else None,
        "median_lead_sessions": median(leads) if leads else None,
        "label_role": "retrospective-evaluation-only",
    }
