"""One outcome evaluator for trend, mean reversion, and future systems."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from statistics import fmean, median

from stock_harness.models import StoredDailyBar
from stock_harness.replay.contracts import (
    DEFAULT_EVALUATION_HORIZONS,
    EVALUATION_CONTRACT_VERSION,
    FrozenSignal,
)


def evaluate_frozen_signal(
    signal: FrozenSignal,
    future_bars: Sequence[StoredDailyBar],
    *,
    horizons: Sequence[int] = DEFAULT_EVALUATION_HORIZONS,
) -> dict[str, object]:
    """Evaluate a frozen signal without exposing future bars to selection logic."""
    signal.validate()
    ordered_horizons = _validate_horizons(horizons)
    bars = sorted(
        (bar for bar in future_bars if bar.trade_date > signal.signal_date),
        key=lambda bar: bar.trade_date,
    )
    reference = _basis_outcomes(signal, bars, signal.reference_close, "signal-close", ordered_horizons)
    executable = (
        _basis_outcomes(signal, bars, float(bars[0].open), "next-open", ordered_horizons)
        if bars and bars[0].open > 0 else None
    )
    return {
        "contract_version": EVALUATION_CONTRACT_VERSION,
        "signal": signal.to_dict(),
        "available_future_sessions": len(bars),
        "reference_close": reference,
        "next_open": executable,
        "maximum_holding": _maximum_holding_outcome(signal, bars),
    }


def summarize_evaluations(
    evaluations: Sequence[Mapping[str, object]],
    *,
    horizons: Sequence[int] = DEFAULT_EVALUATION_HORIZONS,
    basis: str = "reference_close",
) -> dict[str, object]:
    """Aggregate every system with identical metrics and right-censoring rules."""
    ordered_horizons = _validate_horizons(horizons)
    if basis not in {"reference_close", "next_open"}:
        raise ValueError("unsupported evaluation basis")
    horizon_metrics: dict[str, object] = {}
    for horizon in ordered_horizons:
        complete = []
        censored = 0
        for evaluation in evaluations:
            basis_value = evaluation.get(basis)
            if not isinstance(basis_value, Mapping):
                censored += 1
                continue
            values = basis_value.get("horizons")
            value = values.get(str(horizon)) if isinstance(values, Mapping) else None
            if not isinstance(value, Mapping) or value.get("status") != "complete":
                censored += 1
                continue
            complete.append(value)
        returns = [_number(item.get("close_return")) for item in complete]
        returns = [value for value in returns if value is not None]
        mfe = [_number(item.get("mfe")) for item in complete]
        mfe = [value for value in mfe if value is not None]
        mae = [_number(item.get("mae")) for item in complete]
        mae = [value for value in mae if value is not None]
        mfe_r = [_number(item.get("mfe_r")) for item in complete]
        mfe_r = [value for value in mfe_r if value is not None]
        horizon_metrics[str(horizon)] = {
            "complete_count": len(complete),
            "right_censored_count": censored,
            "positive_close_rate": _ratio(sum(value > 0 for value in returns), len(returns)),
            "mean_close_return": _mean(returns),
            "median_close_return": _median(returns),
            "mean_mfe": _mean(mfe),
            "median_mfe": _median(mfe),
            "mean_mae": _mean(mae),
            "median_mae": _median(mae),
            "mfe_1r_rate": _ratio(sum(value >= 1 for value in mfe_r), len(mfe_r)),
            "mfe_2r_rate": _ratio(sum(value >= 2 for value in mfe_r), len(mfe_r)),
            "mfe_3r_rate": _ratio(sum(value >= 3 for value in mfe_r), len(mfe_r)),
        }
    event_counts = Counter()
    for evaluation in evaluations:
        basis_value = evaluation.get(basis)
        if isinstance(basis_value, Mapping):
            event_counts[str(basis_value.get("first_boundary_event") or "none")] += 1
    holding_values = [
        value for evaluation in evaluations
        if isinstance((value := evaluation.get("maximum_holding")), Mapping)
        and value.get("status") == "complete"
    ]
    holding_returns = [
        value for item in holding_values
        if (value := _number(item.get("close_return"))) is not None
    ]
    holding_events = Counter(
        str(item.get("first_boundary_event") or "none") for item in holding_values
    )
    boundary_sessions = [
        int(value) for item in holding_values
        if isinstance((value := item.get("first_boundary_event_session")), int)
    ]
    return {
        "contract_version": EVALUATION_CONTRACT_VERSION,
        "basis": basis,
        "signal_count": len(evaluations),
        "horizons": horizon_metrics,
        "first_boundary_events": dict(sorted(event_counts.items())),
        "maximum_holding": {
            "complete_count": len(holding_values),
            "right_censored_count": len(evaluations) - len(holding_values),
            "positive_close_rate": _ratio(
                sum(value > 0 for value in holding_returns), len(holding_returns),
            ),
            "mean_close_return": _mean(holding_returns),
            "median_close_return": _median(holding_returns),
            "first_boundary_events": dict(sorted(holding_events.items())),
            "median_first_boundary_session": _median(boundary_sessions),
        },
    }


def summarize_evaluations_by(
    evaluations: Sequence[Mapping[str, object]],
    field: str,
    *,
    horizons: Sequence[int] = DEFAULT_EVALUATION_HORIZONS,
    basis: str = "reference_close",
) -> dict[str, object]:
    """Apply the same evaluator summary to system-owned descriptive groups."""
    groups: dict[str, list[Mapping[str, object]]] = {}
    for evaluation in evaluations:
        signal = evaluation.get("signal")
        value = signal.get(field) if isinstance(signal, Mapping) else None
        groups.setdefault(str(value or "unspecified"), []).append(evaluation)
    return {
        key: summarize_evaluations(
            values, horizons=horizons, basis=basis,
        )
        for key, values in sorted(groups.items())
    }


def _basis_outcomes(
    signal: FrozenSignal,
    bars: Sequence[StoredDailyBar],
    entry: float,
    basis: str,
    horizons: tuple[int, ...],
) -> dict[str, object]:
    risk = _risk(signal, entry)
    first_event, first_event_session = _first_boundary_event(signal, bars)
    values: dict[str, object] = {}
    for horizon in horizons:
        if len(bars) < horizon:
            values[str(horizon)] = {
                "status": "right-censored",
                "available_sessions": len(bars),
                "required_sessions": horizon,
            }
            continue
        window = bars[:horizon]
        favorable = _favorable_return(signal.direction, entry, window)
        adverse = _adverse_return(signal.direction, entry, window)
        values[str(horizon)] = {
            "status": "complete",
            "end_date": window[-1].trade_date.isoformat(),
            "close_return": _round(_directional_return(
                signal.direction, entry, float(window[-1].close),
            )),
            "mfe": _round(favorable),
            "mae": _round(adverse),
            "mfe_r": _round(favorable * entry / risk) if risk else None,
            "mae_r": _round(adverse * entry / risk) if risk else None,
        }
    return {
        "basis": basis,
        "entry_price": _round(entry),
        "risk_per_unit": _round(risk),
        "first_boundary_event": first_event,
        "first_boundary_event_session": first_event_session,
        "horizons": values,
    }


def _first_boundary_event(
    signal: FrozenSignal, bars: Sequence[StoredDailyBar],
) -> tuple[str, int | None]:
    target = signal.selected_target_price
    invalidation = signal.invalidation_price
    if target is None and invalidation is None:
        return "not-configured", None
    for session, bar in enumerate(bars, 1):
        if signal.direction == "long":
            target_hit = target is not None and bar.high >= target
            invalidation_hit = invalidation is not None and bar.low <= invalidation
        else:
            target_hit = target is not None and bar.low <= target
            invalidation_hit = invalidation is not None and bar.high >= invalidation
        if target_hit and invalidation_hit:
            return "same-session-ambiguous", session
        if target_hit:
            return "target-first", session
        if invalidation_hit:
            return "invalidation-first", session
    return "neither", None


def _risk(signal: FrozenSignal, entry: float) -> float | None:
    if signal.invalidation_price is None:
        return None
    value = (
        entry - signal.invalidation_price
        if signal.direction == "long"
        else signal.invalidation_price - entry
    )
    return value if value > 0 else None


def _maximum_holding_outcome(
    signal: FrozenSignal, bars: Sequence[StoredDailyBar],
) -> dict[str, object] | None:
    value = signal.metadata.get("maximum_holding_sessions")
    if not isinstance(value, int) or value <= 0:
        return None
    if len(bars) < value:
        return {
            "status": "right-censored", "available_sessions": len(bars),
            "required_sessions": value,
        }
    window = bars[:value]
    event, session = _first_boundary_event(signal, window)
    return {
        "status": "complete", "holding_sessions": value,
        "end_date": window[-1].trade_date.isoformat(),
        "close_return": _round(_directional_return(
            signal.direction, signal.reference_close, float(window[-1].close),
        )),
        "first_boundary_event": event,
        "first_boundary_event_session": session,
        "expired_without_boundary": event == "neither",
    }


def _favorable_return(
    direction: str, entry: float, bars: Sequence[StoredDailyBar],
) -> float:
    price = max(bar.high for bar in bars) if direction == "long" else min(bar.low for bar in bars)
    return _directional_return(direction, entry, float(price))


def _adverse_return(
    direction: str, entry: float, bars: Sequence[StoredDailyBar],
) -> float:
    price = min(bar.low for bar in bars) if direction == "long" else max(bar.high for bar in bars)
    return _directional_return(direction, entry, float(price))


def _directional_return(direction: str, entry: float, exit_price: float) -> float:
    raw = exit_price / entry - 1.0
    return raw if direction == "long" else -raw


def _validate_horizons(values: Sequence[int]) -> tuple[int, ...]:
    result = tuple(int(value) for value in values)
    if not result or any(value <= 0 for value in result) or tuple(sorted(set(result))) != result:
        raise ValueError("evaluation horizons must be unique, positive, and increasing")
    return result


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _mean(values: Sequence[float]) -> float | None:
    return _round(fmean(values)) if values else None


def _median(values: Sequence[float]) -> float | None:
    return _round(median(values)) if values else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return _round(numerator / denominator) if denominator else None
