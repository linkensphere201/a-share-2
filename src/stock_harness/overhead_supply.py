"""Explicitly uncertain overhead-supply proxy derived from daily evidence."""

from __future__ import annotations

from datetime import date
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType


SUPPLY_ITEM_ID = "overhead-supply-proxy"
SUPPLY_METHOD = "daily-volume-zone-failed-breakout-proxy-v1"


def build_overhead_supply_item(
    bars: Sequence[AnalysisBar],
    items: Sequence[GeneratedAnalysisItem],
) -> GeneratedAnalysisItem:
    latest = bars[-1]
    volume_zones = []
    estimated_share = 0.0
    recency_score = 0.0
    failed_attempts = 0
    for item in items:
        payload = item.payload
        if (
            item.item_type is GeneratedItemType.ZONE
            and payload.get("kind") == "estimated-volume-at-price"
            and _number(payload.get("lower")) is not None
            and float(payload["lower"]) > latest.close
        ):
            share = max(0.0, _number(payload.get("estimated_share")) or 0.0)
            evidence_dates = payload.get("evidence_dates")
            parsed_dates = [
                parsed for value in evidence_dates
                if (parsed := _parse_date(value)) is not None
            ] if isinstance(evidence_dates, list) else []
            latest_evidence = max(parsed_dates, default=None)
            age = (latest.period_end - latest_evidence).days if latest_evidence else 365
            recency = max(0.0, 1 - age / 365)
            estimated_share += share
            recency_score = max(recency_score, recency)
            volume_zones.append({
                "source_item_id": item.item_id,
                "lower": payload.get("lower"),
                "upper": payload.get("upper"),
                "estimated_share": round(share, 6),
                "recency": round(recency, 6),
            })
        if (
            item.item_type is GeneratedItemType.EVIDENCE
            and payload.get("kind") in {
                "breakout-state-summary", "latest-structural-event-summary",
            }
            and payload.get("current_state") in {"failed", "invalidated"}
            and payload.get("direction") in {"up", "long"}
        ):
            failed_attempts += 1
    turnover_values = [
        bar.volume for bar in bars[-20:] if bar.volume > 0
    ]
    recent_volume_ratio = (
        sum(turnover_values[-5:]) / 5 / (sum(turnover_values[:-5]) / len(turnover_values[:-5]))
        if len(turnover_values) >= 10 and sum(turnover_values[:-5]) > 0 else None
    )
    score = min(1.0,
        min(0.55, estimated_share * 2.2)
        + min(0.25, failed_attempts * 0.1)
        + min(0.1, recency_score * 0.1)
        + (0.1 if recent_volume_ratio is not None and recent_volume_ratio > 1.5 else 0)
    )
    return GeneratedAnalysisItem(
        item_id=SUPPLY_ITEM_ID,
        item_type=GeneratedItemType.EVIDENCE,
        payload={
            "kind": "overhead-supply-proxy",
            "method": SUPPLY_METHOD,
            "score": round(score, 6),
            "state": "high" if score >= 0.65 else "medium" if score >= 0.35 else "low",
            "estimated_share_above_price": round(estimated_share, 6),
            "failed_upward_attempts": failed_attempts,
            "recent_volume_ratio_5_15": (
                round(recent_volume_ratio, 6) if recent_volume_ratio is not None else None
            ),
            "zones": volume_zones,
            "turnover_available": False,
            "assumptions": [
                "daily volume is distributed approximately inside each bar range",
                "recent failed upward breaks increase estimated selling pressure",
                "volume acceleration is supporting evidence, not holder identity",
            ],
            "uncertainty": (
                "This is an overhead-supply proxy from daily OHLCV, not actual holder cost, "
                "trapped-position, or main-force position data."
            ),
        },
    )


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
