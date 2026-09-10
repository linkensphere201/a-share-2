"""Objective, future-isolated evaluation for board-hotspot radar output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
import re

from stock_harness.board_hotspot_features import hotspot_feature_value


BOARD_HOTSPOT_EVALUATOR_VERSION = "board-hotspot-evaluator-v4-timing-outcomes"
_ROMAN_SUFFIX = re.compile(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+(?:\(A股\))?$")
_A_SHARE_SUFFIX = re.compile(r"\(A股\)$", re.IGNORECASE)
_GENERIC_SUFFIX = re.compile(r"(?:指数|板块)$")


def canonical_board_name(name: str) -> str:
    """Cluster obvious cross-provider aliases without merging adjacent themes."""
    normalized = re.sub(r"\s+", "", name).replace("（", "(").replace("）", ")")
    normalized = _ROMAN_SUFFIX.sub("", normalized)
    normalized = _A_SHARE_SUFFIX.sub("", normalized)
    normalized = _GENERIC_SUFFIX.sub("", normalized)
    return normalized.casefold()


def named_theme(name: str) -> str | None:
    """Map acceptance examples to broad themes; this never affects online score."""
    value = canonical_board_name(name)
    rules = (
        ("electricity", ("电力", "绿色电力", "电网")),
        ("medicine", (
            "医药", "创新药", "医疗", "cro", "cdmo", "减肥药", "原料药",
        )),
        ("agriculture-seed", (
            "种业", "种子", "农业种植", "农作物", "转基因", "玉米", "粮食",
        )),
        ("hardware-technology", ("算力", "半导体", "光模块", "cpo", "pcb", "消费电子")),
    )
    for theme, aliases in rules:
        if any(alias in value for alias in aliases):
            return theme
    return None


def is_objective_confirmation(feature: Mapping[str, object]) -> bool:
    """Causal confirmation label based only on facts available that session."""
    r5 = hotspot_feature_value(feature, ("metrics", "returns", "5"))
    r20 = hotspot_feature_value(feature, ("metrics", "returns", "20"))
    rs20 = hotspot_feature_value(feature, ("metrics", "relative_strength", "20"))
    breadth5 = hotspot_feature_value(feature, ("member_snapshot", "positive_return_5_ratio"))
    volume5 = hotspot_feature_value(feature, ("metrics", "recent_volume_ratio_5_5"))
    limits = hotspot_feature_value(feature, ("member_snapshot", "limit_up_count")) or 0
    streak = hotspot_feature_value(feature, ("member_snapshot", "max_limit_up_streak")) or 0
    return (
        r5 is not None and r5 >= .04
        and r20 is not None and r20 >= .08
        and rs20 is not None and rs20 >= .025
        and breadth5 is not None and breadth5 >= .56
        and ((volume5 is not None and volume5 >= 1.03) or limits >= 1 or streak >= 2)
    )


def evaluate_hotspot_timelines(
    timelines: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    names: Mapping[str, str] | None = None,
    lead_window: int = 10,
    cooldown: int = 10,
    visible_only: bool = False,
    theme_profiles: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Compare radar episodes with independent two-session confirmations."""
    names = names or {}
    signals: list[dict[str, object]] = []
    confirmations: list[dict[str, object]] = []
    theme_hits: dict[str, list[str]] = {}
    evaluation_series = (
        _visible_theme_timelines(timelines, names, theme_profiles or {})
        if visible_only else {
            symbol: (names.get(symbol, symbol), list(rows))
            for symbol, rows in timelines.items()
        }
    )
    for cluster, (series_name, source_rows) in evaluation_series.items():
        rows = sorted(source_rows, key=lambda row: str(row["effective_date"]))
        cluster_key = cluster if visible_only else canonical_board_name(series_name)
        signal_indexes = _episode_indexes(
            [_is_radar_signal(row, visible_only=visible_only) for row in rows], cooldown,
        )
        raw_confirm = [bool(row.get("_objective_confirmation"))
                       if "_objective_confirmation" in row
                       else is_objective_confirmation(_mapping(row.get("feature")))
                       for row in rows]
        confirmed = [
            current and index > 0 and raw_confirm[index - 1]
            for index, current in enumerate(raw_confirm)
        ]
        confirmation_indexes = _episode_indexes(confirmed, cooldown)
        for index in signal_indexes:
            future = next((candidate for candidate in confirmation_indexes
                           if index <= candidate <= index + lead_window), None)
            item = {
                "symbol": str(rows[index].get("symbol") or cluster),
                "name": str(rows[index].get("name") or series_name),
                "cluster_key": cluster_key,
                "signal_date": str(rows[index]["effective_date"]),
                "confirmation_date": (
                    str(rows[future]["effective_date"]) if future is not None else None
                ),
                "lead_sessions": future - index if future is not None else None,
                "timing_class": _timing_class(
                    future - index if future is not None else None
                ),
                **_forward_outcomes(rows, index),
            }
            signals.append(item)
            theme = named_theme(str(item["name"]))
            if theme and future is not None:
                theme_hits.setdefault(theme, []).append(str(item["symbol"]))
        for index in confirmation_indexes:
            prior = next((candidate for candidate in reversed(signal_indexes)
                          if index - lead_window <= candidate <= index), None)
            confirmations.append({
                "symbol": str(rows[index].get("symbol") or cluster),
                "confirmation_date": str(rows[index]["effective_date"]),
                "preceded_by_signal": prior is not None,
                "lead_sessions": index - prior if prior is not None else None,
            })
    true_signals = [item for item in signals if item["confirmation_date"]]
    recalled = [item for item in confirmations if item["preceded_by_signal"]]
    leads = [int(item["lead_sessions"]) for item in true_signals]
    timing_counts = {
        timing: sum(item["timing_class"] == timing for item in signals)
        for timing in ("synchronous", "early", "late", "missed")
    }
    return {
        "evaluator_version": BOARD_HOTSPOT_EVALUATOR_VERSION,
        "signal_scope": "visible-theme-seats" if visible_only else "raw-radar",
        "signal_events": len(signals),
        "confirmation_events": len(confirmations),
        "true_signal_events": len(true_signals),
        "precision": round(len(true_signals) / len(signals), 4) if signals else None,
        "recall": round(len(recalled) / len(confirmations), 4) if confirmations else None,
        "median_lead_sessions": _median(leads),
        "timing_counts": timing_counts,
        "early_precision": round(
            timing_counts["early"] / len(signals), 4,
        ) if signals else None,
        "synchronous_confirmation_rate": round(
            timing_counts["synchronous"] / len(signals), 4,
        ) if signals else None,
        "late_confirmation_rate": round(
            timing_counts["late"] / len(signals), 4,
        ) if signals else None,
        "median_max_forward_return_5": _median_float([
            item["max_forward_return_5"] for item in signals
            if item["max_forward_return_5"] is not None
        ]),
        "median_max_adverse_excursion_5": _median_float([
            item["max_adverse_excursion_5"] for item in signals
            if item["max_adverse_excursion_5"] is not None
        ]),
        "named_theme_hits": {key: sorted(set(value)) for key, value in theme_hits.items()},
        "theme_registry_version": next((
            str(profile.get("registry_version"))
            for profile in (theme_profiles or {}).values()
            if profile.get("registry_version")
        ), None),
        "events": signals,
    }


def _visible_theme_timelines(
    timelines: Mapping[str, Sequence[Mapping[str, object]]],
    names: Mapping[str, str],
    theme_profiles: Mapping[str, Mapping[str, object]],
) -> dict[str, tuple[str, list[dict[str, object]]]]:
    """Merge provider aliases by theme and date for user-visible evaluation."""
    grouped: dict[str, dict[str, list[dict[str, object]]]] = {}
    theme_names: dict[str, str] = {}
    for symbol, rows in timelines.items():
        name = names.get(symbol, symbol)
        profile = theme_profiles.get(symbol, {})
        cluster = str(profile.get("theme_id") or canonical_board_name(name))
        theme_names.setdefault(cluster, str(profile.get("theme_name") or name))
        for source in rows:
            row = dict(source)
            row.setdefault("symbol", symbol)
            row.setdefault("name", name)
            grouped.setdefault(cluster, {}).setdefault(
                str(row["effective_date"]), []
            ).append(row)
    result: dict[str, tuple[str, list[dict[str, object]]]] = {}
    for cluster, by_date in grouped.items():
        merged = []
        for effective_date, rows in sorted(by_date.items()):
            visible = [row for row in rows if bool(row.get("radar_visible"))]
            representative = max(
                visible or rows,
                key=lambda row: (
                    float(row.get("visibility_score") or 0),
                    float(row.get("total_score") or 0),
                    str(row.get("symbol") or ""),
                ),
            )
            merged.append({
                **representative,
                "effective_date": effective_date,
                "radar_visible": bool(visible),
                "_objective_confirmation": any(
                    bool(row.get("_objective_confirmation"))
                    if "_objective_confirmation" in row
                    else is_objective_confirmation(_mapping(row.get("feature")))
                    for row in rows
                ),
            })
        result[cluster] = (theme_names[cluster], merged)
    return result


def _is_radar_signal(
    row: Mapping[str, object], *, visible_only: bool = False,
) -> bool:
    if visible_only:
        return bool(row.get("radar_visible"))
    stage = str(row.get("hotspot_stage") or "")
    score = row.get("total_score")
    streak = row.get("candidate_streak")
    return (
        stage in {
            "leader-ignited", "trend-emerging", "breadth-expanding",
            "hotspot-confirmed", "accelerating",
        }
        and isinstance(score, (int, float)) and float(score) >= 40
        and isinstance(streak, (int, float)) and int(streak) >= 1
    )


def _timing_class(lead_sessions: int | None) -> str:
    if lead_sessions is None:
        return "missed"
    if lead_sessions == 0:
        return "synchronous"
    if lead_sessions <= 3:
        return "early"
    return "late"


def _forward_outcomes(
    rows: Sequence[Mapping[str, object]], signal_index: int,
) -> dict[str, float | None]:
    base = _number(rows[signal_index].get("_close"))
    result: dict[str, float | None] = {}
    for horizon in (5, 10):
        future = [
            value for row in rows[signal_index + 1:signal_index + horizon + 1]
            if (value := _number(row.get("_close"))) is not None
        ]
        if base is None or base <= 0 or not future:
            result[f"forward_return_{horizon}"] = None
            result[f"max_forward_return_{horizon}"] = None
            result[f"max_adverse_excursion_{horizon}"] = None
            continue
        result[f"forward_return_{horizon}"] = round(future[-1] / base - 1, 6)
        result[f"max_forward_return_{horizon}"] = round(max(future) / base - 1, 6)
        result[f"max_adverse_excursion_{horizon}"] = round(min(future) / base - 1, 6)
    return result


def _episode_indexes(
    states: Sequence[bool], cooldown: int,
) -> list[int]:
    indexes: list[int] = []
    last = -cooldown - 1
    active = False
    for index, state in enumerate(states):
        if state and not active and index - last > cooldown:
            indexes.append(index)
            last = index
        active = state
    return indexes


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _median(values: Sequence[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _median_float(values: Sequence[object]) -> float | None:
    numbers = sorted(float(value) for value in values if isinstance(value, (int, float)))
    if not numbers:
        return None
    middle = len(numbers) // 2
    value = numbers[middle] if len(numbers) % 2 else (
        numbers[middle - 1] + numbers[middle]
    ) / 2
    return round(value, 6)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None
