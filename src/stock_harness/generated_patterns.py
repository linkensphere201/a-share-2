"""Normalize detected pattern families into generated-analysis items."""

from __future__ import annotations

from dataclasses import asdict
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar, AnalysisTimeframe
from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType
from stock_harness.breakout_state import (
    BreakoutDirection,
    StructuralEvent,
    StructuralEventKind,
    evaluate_breakout,
    evaluate_latest_boundary_event,
)
from stock_harness.classic_patterns import detect_double_patterns
from stock_harness.consolidation_patterns import (
    BoundaryLine,
    detect_consolidation_patterns,
)
from stock_harness.diamond_patterns import detect_diamond_patterns
from stock_harness.reversal_patterns import detect_reversal_patterns
from stock_harness.trend_lines_analysis import TrendHorizon
from stock_harness.trend_pivots import PricePivot


def generate_pattern_items(
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    horizon: TrendHorizon,
    timeframe: AnalysisTimeframe,
    *,
    preview: bool,
) -> list[GeneratedAnalysisItem]:
    if not bars:
        return []
    items: list[GeneratedAnalysisItem] = []
    _append_double_patterns(items, bars, pivots, horizon, timeframe, preview)
    _append_consolidations(items, bars, pivots, horizon, timeframe, preview)
    _append_diamonds(items, bars, pivots, horizon, timeframe, preview)
    _append_reversals(items, bars, pivots, horizon, timeframe, preview)
    return items


def _append_double_patterns(
    items: list[GeneratedAnalysisItem],
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    horizon: TrendHorizon,
    timeframe: AnalysisTimeframe,
    preview: bool,
) -> None:
    for index, pattern in enumerate(detect_double_patterns(bars, pivots)):
        item_id = _item_id(horizon, pattern.pattern_type.value, index)
        items.append(GeneratedAnalysisItem(
            item_id=item_id,
            item_type=GeneratedItemType.PATTERN,
            payload={
                **_base_payload(
                    pattern.pattern_type.value, pattern.display_name,
                    pattern.direction.value, horizon, timeframe,
                    pattern.start_date, pattern.end_date, pattern.available_date,
                    pattern.pivots, pattern.state.value, pattern.breakout_date,
                    pattern.invalidation_price, pattern.invalidation_date,
                    pattern.score, pattern.score_components,
                    pattern.volume_ratio, pattern.primary,
                ),
                "boundary_geometry": {
                    "kind": "horizontal-neckline",
                    "price": pattern.neckline_price,
                    "start_date": pattern.start_date.isoformat(),
                    "end_date": bars[-1].period_end.isoformat(),
                },
                "neckline_price": pattern.neckline_price,
            },
        ))
        evaluation = evaluate_breakout(
            bars,
            direction=(
                BreakoutDirection.UP
                if pattern.direction.value == "bullish"
                else BreakoutDirection.DOWN
            ),
            boundary_price=pattern.neckline_price,
            invalidation_price=pattern.invalidation_price,
            available_date=pattern.available_date,
            preview=preview,
        )
        for transition_index, transition in enumerate(evaluation.transitions):
            evidence = asdict(transition.evidence)
            evidence["trade_date"] = transition.evidence.trade_date.isoformat()
            items.append(GeneratedAnalysisItem(
                item_id=f"{item_id}-transition-{transition_index}",
                item_type=GeneratedItemType.TRANSITION,
                parent_item_id=item_id,
                payload={
                    "state": transition.state.value,
                    "transition_date": transition.transition_date.isoformat(),
                    "reason": transition.reason,
                    "preview": evaluation.preview,
                    "evidence": evidence,
                },
            ))
        items.append(GeneratedAnalysisItem(
            item_id=f"{item_id}-breakout-evidence",
            item_type=GeneratedItemType.EVIDENCE,
            parent_item_id=item_id,
            payload={
                "kind": "breakout-state-summary",
                "current_state": evaluation.current_state.value,
                "direction": evaluation.direction.value,
                "boundary_price": evaluation.boundary_price,
                "trigger_level": evaluation.boundary_price,
                "confirmation_level": evaluation.boundary_price,
                "failure_level": evaluation.boundary_price,
                "invalidation_level": evaluation.invalidation_price,
                "available_date": evaluation.available_date.isoformat(),
                "trigger_date": _iso(evaluation.trigger_date),
                "confirmation_date": _iso(evaluation.confirmation_date),
                "failure_date": _iso(evaluation.failure_date),
                "preview": evaluation.preview,
                "analytical_only": True,
            },
        ))


def _append_consolidations(
    items: list[GeneratedAnalysisItem],
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    horizon: TrendHorizon,
    timeframe: AnalysisTimeframe,
    preview: bool,
) -> None:
    index_by_date = {bar.period_end: index for index, bar in enumerate(bars)}
    for index, pattern in enumerate(detect_consolidation_patterns(bars, pivots)):
        latest, previous = len(bars) - 1, len(bars) - 2
        start = index_by_date[pattern.start_date]
        upper_latest = _project(pattern.upper_boundary, latest - start)
        upper_previous = _project(pattern.upper_boundary, previous - start)
        lower_latest = _project(pattern.lower_boundary, latest - start)
        lower_previous = _project(pattern.lower_boundary, previous - start)
        monitor_up = bars[-2].close <= (upper_previous + lower_previous) / 2
        boundary = upper_latest if monitor_up else lower_latest
        prior_boundary = upper_previous if monitor_up else lower_previous
        item_id = _item_id(horizon, pattern.pattern_type.value, index)
        items.append(GeneratedAnalysisItem(
            item_id=item_id,
            item_type=GeneratedItemType.PATTERN,
            payload={
                **_base_payload(
                    pattern.pattern_type.value, pattern.display_name,
                    pattern.breakout_direction or "neutral", horizon, timeframe,
                    pattern.start_date, pattern.end_date, pattern.available_date,
                    pattern.pivots, pattern.completion_state,
                    pattern.breakout_date,
                    lower_latest if monitor_up else upper_latest,
                    pattern.invalidation_date, pattern.score,
                    pattern.score_components, pattern.volume_ratio, pattern.primary,
                ),
                "boundary_geometry": {
                    "kind": "two-lines",
                    "upper": _boundary_payload(pattern.upper_boundary),
                    "lower": _boundary_payload(pattern.lower_boundary),
                },
                "neckline_price": boundary,
            },
        ))
        _append_latest_event(
            items, item_id, bars, monitor_up, boundary, prior_boundary, preview
        )


def _append_diamonds(
    items: list[GeneratedAnalysisItem],
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    horizon: TrendHorizon,
    timeframe: AnalysisTimeframe,
    preview: bool,
) -> None:
    index_by_date = {bar.period_end: index for index, bar in enumerate(bars)}
    for index, pattern in enumerate(detect_diamond_patterns(bars, pivots)):
        latest, previous = len(bars) - 1, len(bars) - 2
        upper_start = index_by_date[pattern.upper_active.start_date]
        lower_start = index_by_date[pattern.lower_active.start_date]
        upper_latest = _project(pattern.upper_active, latest - upper_start)
        upper_previous = _project(pattern.upper_active, previous - upper_start)
        lower_latest = _project(pattern.lower_active, latest - lower_start)
        lower_previous = _project(pattern.lower_active, previous - lower_start)
        monitor_up = bars[-2].close <= (upper_previous + lower_previous) / 2
        boundary = upper_latest if monitor_up else lower_latest
        prior_boundary = upper_previous if monitor_up else lower_previous
        item_id = _item_id(horizon, pattern.pattern_type.value, index)
        items.append(GeneratedAnalysisItem(
            item_id=item_id,
            item_type=GeneratedItemType.PATTERN,
            payload={
                **_base_payload(
                    pattern.pattern_type.value, pattern.display_name,
                    pattern.breakout_direction or "neutral", horizon, timeframe,
                    pattern.start_date, pattern.end_date, pattern.available_date,
                    pattern.pivots, pattern.completion_state,
                    pattern.breakout_date,
                    lower_latest if monitor_up else upper_latest,
                    pattern.invalidation_date, pattern.score,
                    pattern.score_components, pattern.volume_ratio, pattern.primary,
                ),
                "boundary_geometry": {
                    "kind": "broadening-then-contracting",
                    "segments": [
                        _boundary_payload(value)
                        for value in pattern.boundary_segments
                    ],
                },
                "neckline_price": boundary,
                "context_change_percent": pattern.context_change_percent,
            },
        ))
        _append_latest_event(
            items, item_id, bars, monitor_up, boundary, prior_boundary, preview
        )


def _append_reversals(
    items: list[GeneratedAnalysisItem],
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    horizon: TrendHorizon,
    timeframe: AnalysisTimeframe,
    preview: bool,
) -> None:
    for index, pattern in enumerate(detect_reversal_patterns(bars, pivots)):
        item_id = _item_id(horizon, pattern.pattern_type.value, index)
        items.append(GeneratedAnalysisItem(
            item_id=item_id,
            item_type=GeneratedItemType.PATTERN,
            payload={
                **_base_payload(
                    pattern.pattern_type.value, pattern.display_name,
                    pattern.direction, horizon, timeframe,
                    pattern.start_date, pattern.end_date, pattern.available_date,
                    pattern.pivots, pattern.state, pattern.breakout_date,
                    pattern.invalidation_price, pattern.invalidation_date,
                    pattern.score, pattern.score_components,
                    pattern.volume_ratio, pattern.primary,
                ),
                "boundary_geometry": {
                    "kind": "segments",
                    "segments": [
                        _boundary_payload(value)
                        for value in pattern.boundary_segments
                    ],
                },
                "neckline_price": pattern.neckline_price,
                "neckline_slope_per_bar": pattern.neckline_slope_per_bar,
            },
        ))
        _append_latest_event(
            items, item_id, bars, pattern.direction == "bullish",
            pattern.neckline_price,
            pattern.neckline_price - pattern.neckline_slope_per_bar,
            preview,
        )


def _base_payload(
    pattern_type, display_name, direction, horizon, timeframe,
    start_date, end_date, available_date, pivots, completion_state,
    breakout_date, invalidation_price, invalidation_date, score,
    score_components, volume_ratio, primary,
) -> dict[str, object]:
    return {
        "pattern_type": pattern_type,
        "display_name": display_name,
        "direction": direction,
        "horizon": horizon.value,
        "timeframe": timeframe.value,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "available_date": available_date.isoformat(),
        "pivots": [_pivot_payload(pivot) for pivot in pivots],
        "completion_state": completion_state,
        "breakout_date": _iso(breakout_date),
        "invalidation_price": invalidation_price,
        "invalidation_date": _iso(invalidation_date),
        "score": score,
        "score_components": score_components,
        "volume_ratio": volume_ratio,
        "primary": primary,
    }


def _pivot_payload(pivot) -> dict[str, object]:
    kind = getattr(pivot.kind, "value", pivot.kind)
    return {
        "kind": kind,
        "pivot_date": pivot.pivot_date.isoformat(),
        "price": pivot.price,
        "confirmed_date": pivot.confirmed_date.isoformat(),
    }


def _append_latest_event(
    items: list[GeneratedAnalysisItem],
    parent_item_id: str,
    bars: Sequence[AnalysisBar],
    monitor_up: bool,
    boundary: float,
    previous_boundary: float,
    preview: bool,
) -> None:
    if len(bars) < 2:
        return
    event = evaluate_latest_boundary_event(
        bars,
        direction=BreakoutDirection.UP if monitor_up else BreakoutDirection.DOWN,
        boundary_price=boundary,
        previous_boundary_price=previous_boundary,
        preview=preview,
    )
    if event is not None:
        items.extend(_latest_event_items(parent_item_id, event))


def _latest_event_items(
    parent_item_id: str, event: StructuralEvent
) -> list[GeneratedAnalysisItem]:
    state = {
        StructuralEventKind.UPWARD_BREAKOUT: "triggered",
        StructuralEventKind.DOWNWARD_BREAKDOWN: "triggered",
        StructuralEventKind.RETEST: "retesting",
        StructuralEventKind.FALSE_BREAKOUT_RISK: "failed",
        StructuralEventKind.NO_CHANGE: "ready",
    }[event.kind]
    evidence = asdict(event.evidence)
    evidence["trade_date"] = event.evidence.trade_date.isoformat()
    payload = {
        "event_kind": event.kind.value,
        "current_state": state,
        "direction": event.direction.value,
        "event_date": event.event_date.isoformat(),
        "boundary_price": event.boundary_price,
        "previous_boundary_price": event.previous_boundary_price,
        "preview": event.preview,
        "reason": event.reason,
        "evidence": evidence,
    }
    return [
        GeneratedAnalysisItem(
            f"{parent_item_id}-latest-event-transition",
            GeneratedItemType.TRANSITION,
            payload,
            parent_item_id,
        ),
        GeneratedAnalysisItem(
            f"{parent_item_id}-latest-event-evidence",
            GeneratedItemType.EVIDENCE,
            {
                "kind": "latest-structural-event-summary",
                **payload,
                "invalidation_level": event.boundary_price,
                "trigger_date": (
                    event.event_date.isoformat()
                    if event.kind in {
                        StructuralEventKind.UPWARD_BREAKOUT,
                        StructuralEventKind.DOWNWARD_BREAKDOWN,
                    } else None
                ),
                "failure_date": (
                    event.event_date.isoformat()
                    if event.kind is StructuralEventKind.FALSE_BREAKOUT_RISK
                    else None
                ),
            },
            parent_item_id,
        ),
    ]


def _boundary_payload(value: BoundaryLine) -> dict[str, object]:
    return {
        "start_date": value.start_date.isoformat(),
        "start_price": value.start_price,
        "end_date": value.end_date.isoformat(),
        "end_price": value.end_price,
        "slope_per_bar": value.slope_per_bar,
    }


def _project(value: BoundaryLine, offset: int) -> float:
    return value.start_price + value.slope_per_bar * offset


def _item_id(horizon: TrendHorizon, pattern_type: str, index: int) -> str:
    return f"{horizon.value}-pattern-{pattern_type}-{index}"


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None
