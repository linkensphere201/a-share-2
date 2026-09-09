"""Deterministic first-level daily observations for market and board indices."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
import hashlib
import json
import math
from statistics import fmean, median

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.models import StoredDailyBar
from stock_harness.pattern_analysis import PatternAnalysisService
from stock_harness.structural_scenario_engine import (
    build_coarse_structural_scenario_items,
    project_scenario_summary,
)


ALGORITHM_VERSION = "daily-market-board-observation-v3"
CONFIG_VERSION = "daily-market-board-defaults-v3"
MINIMUM_BARS = 120
LOOKBACK_BARS = 260


def analyze_daily_series(
    symbol: str,
    bars: Sequence[StoredDailyBar],
    effective_date: date,
    *,
    benchmark_bars: Sequence[StoredDailyBar] = (),
    volume_semantics: str = "traded",
) -> dict[str, object]:
    if volume_semantics not in {"traded", "synthetic-not-traded"}:
        raise ValueError("unsupported daily volume semantics")
    visible = [bar for bar in bars if bar.trade_date <= effective_date]
    if len(visible) < MINIMUM_BARS or visible[-1].trade_date != effective_date:
        reasons = []
        if len(visible) < MINIMUM_BARS:
            reasons.append("insufficient-history")
        if not visible or visible[-1].trade_date != effective_date:
            reasons.append("stale-through-effective-date")
        return _observation(
            symbol, effective_date, visible, "insufficient", {}, [], reasons, [],
        )

    latest = visible[-1]
    closes = [bar.close for bar in visible]
    volumes = [bar.volume for bar in visible]
    structure = PatternAnalysisService.scan_daily(visible)
    atr14 = structure.atr14
    atr5 = structure.atr5
    atr20 = structure.atr20
    returns = {str(period): _return(closes, period) for period in (1, 5, 10, 20)}
    benchmark_returns = {
        str(period): _return([bar.close for bar in benchmark_bars], period)
        for period in (5, 20)
    }
    relative_strength = {
        period: (
            returns[period] - benchmark_returns[period]
            if returns.get(period) is not None and benchmark_returns.get(period) is not None
            else None
        ) for period in ("5", "20")
    }
    volume_enabled = volume_semantics == "traded"
    median_volume20 = median(volumes[-20:]) if volume_enabled else None
    volume_ratio = (
        latest.volume / median_volume20
        if median_volume20 is not None and median_volume20 > 0 else None
    )
    recent_volume_ratio = (
        _safe_ratio(fmean(volumes[-5:]), fmean(volumes[-10:-5]))
        if volume_enabled else None
    )
    atr_compression = _safe_ratio(atr5, atr20)
    short_shape = dict(structure.short_shape)
    medium_shape = dict(structure.medium_shape)
    envelopes = {
        label: dict(value) if value is not None else None
        for label, value in structure.descending_envelopes.items()
    }
    downside = dict(structure.downside_deceleration)
    states: list[str] = []
    attention: list[str] = []
    disqualifiers: list[str] = []

    for label, envelope in envelopes.items():
        if envelope is None:
            continue
        state = str(envelope["state"])
        if state == "approaching":
            states.append(f"descending-envelope-{label}-approaching")
        elif state == "broken":
            states.append(f"descending-envelope-{label}-broken")
            if label in {"6m", "1y"} or (
                volume_ratio is not None and volume_ratio >= 1.2
            ):
                attention.append(f"{label}-descending-envelope-broken")

    valid_distances = [
        float(item["distance_atr"]) for item in envelopes.values()
        if item is not None and item.get("distance_atr") is not None
    ]
    nearest_distance = min(valid_distances, default=None)
    structure_ready = (
        latest.close > min(closes[-5:])
        and short_shape.get("state") != "falling"
    )
    contraction_ready = (
        atr_compression is not None and atr_compression <= .85
    ) or (volume_ratio is not None and volume_ratio <= .90)
    major_approaching = any(
        label in {"6m", "1y"} and item is not None
        and item.get("state") == "approaching"
        for label, item in envelopes.items()
    )
    if (
        nearest_distance is not None and 0 <= nearest_distance <= 1
        and major_approaching and structure_ready and contraction_ready
    ):
        states.append("bullish-transition-candidate")
        attention.append("bullish-boundary-proximity")
    short_approaching = (
        envelopes.get("3m") is not None
        and envelopes["3m"].get("state") == "approaching"
    )
    if (
        short_approaching
        and short_shape.get("state") != "falling"
        and medium_shape.get("state") != "falling"
        and atr_compression is not None and atr_compression <= .75
        and volume_ratio is not None and volume_ratio <= .8
    ):
        states.append("bullish-transition-candidate")
        attention.append("3m-descending-envelope-approaching")
    if any(item and item["state"] == "broken" for item in envelopes.values()):
        states.append("bullish-boundary-triggered")

    exhaustion_ready = (
        downside["extended"] and downside["deceleration_count"] >= 4
    )
    if exhaustion_ready and volume_ratio is not None and volume_ratio <= .9:
        states.append("oversold-exhaustion-candidate")
        attention.append("downside-exhaustion")
    if (
        exhaustion_ready
        and latest.close > max(bar.high for bar in visible[-6:-1])
        and volume_ratio is not None and volume_ratio >= 1.2
    ):
        states.append("oversold-rebound-triggered")
        attention.append("oversold-rebound-triggered")
    if volume_ratio is not None and (
        volume_ratio >= 2.5
        or (
            volume_ratio >= 1.8 and returns["1"] is not None
            and abs(float(returns["1"])) >= .015
        )
    ):
        states.append("sudden-volume-expansion")
        attention.append("sudden-volume-expansion")
    if (
        volume_ratio is not None and volume_ratio <= .65
        and nearest_distance is not None and 0 <= nearest_distance <= 1
    ):
        states.append("boundary-volume-contraction")
        attention.append("boundary-volume-contraction")
    if (
        relative_strength["20"] is not None
        and abs(float(relative_strength["20"])) >= .1
    ):
        states.append("relative-strength-regime")
        if (
            relative_strength["5"] is not None
            and abs(float(relative_strength["5"])) >= .04
            and float(relative_strength["5"]) * float(relative_strength["20"]) > 0
            and volume_ratio is not None and volume_ratio >= 1.5
        ):
            attention.append("relative-strength-regime")

    if not attention:
        states.append("neutral")
    if atr14 <= 0:
        disqualifiers.append("invalid-atr")

    metrics = {
        "trade_date": latest.trade_date.isoformat(),
        "close": _round(latest.close),
        "returns": {key: _round(value) for key, value in returns.items()},
        "relative_strength": {
            key: _round(value) for key, value in relative_strength.items()
        },
        "atr14": _round(atr14),
        "atr_percent": _round(_safe_ratio(atr14, latest.close)),
        "atr_compression_5_20": _round(atr_compression),
        "volume": latest.volume if volume_enabled else None,
        "volume_semantics": volume_semantics,
        "median_volume20": _round(median_volume20),
        "volume_ratio20": _round(volume_ratio),
        "recent_volume_ratio_5_5": _round(recent_volume_ratio),
        "short_shape": short_shape,
        "medium_shape": medium_shape,
        "downside": downside,
        "descending_envelopes": envelopes,
        "price_space": _price_space(visible, states, envelopes, atr14),
    }
    return _observation(
        symbol, effective_date, visible, "complete", metrics,
        sorted(set(states)), disqualifiers, sorted(set(attention)),
    )


def render_board_summary(
    observation: dict[str, object], prior: dict[str, object] | None,
    recent: Sequence[dict[str, object]] = (),
) -> tuple[str, str]:
    metrics = observation.get("metrics", {})
    if not isinstance(metrics, dict) or observation["coverage_state"] != "complete":
        return "data-unavailable", "- 结论：板块日线覆盖不足，当前无法形成固定算法结论。"
    states = [str(item) for item in observation.get("state_codes", [])]
    primary = _primary_state(states)
    transition = _detailed_transition(primary, prior)
    returns = metrics.get("returns", {})
    volume_ratio = metrics.get("volume_ratio20")
    envelopes = metrics.get("descending_envelopes", {})
    nearest = _nearest_envelope(envelopes if isinstance(envelopes, dict) else {})
    boundary_text = (
        f"最近{nearest[0]}下降边界距离{nearest[1]:.2f} ATR"
        if nearest is not None else "暂无合格下降边界"
    )
    return primary, "\n".join((
        f"【临界状态】{_state_label(primary)}（{_transition_label(transition)}）",
        f"- 结论：{_state_conclusion(primary)}",
        f"- 形态：短周期{_shape_label(metrics.get('short_shape'))}；中周期{_shape_label(metrics.get('medium_shape'))}；{boundary_text}。",
        f"- 价：近5日{_percent(_mapping_value(returns, '5'))}，近20日{_percent(_mapping_value(returns, '20'))}。",
        f"- 量：{_volume_sentence(metrics, volume_ratio)}",
        *([f"- 广度：{_breadth_sentence(metrics)}"] if "board_breadth" in metrics else []),
        f"- 目标/空间：{_price_space_sentence(metrics)}",
        f"- 近期对比：{_transition_sentence(transition, prior, recent, primary)}",
        f"- 确认/失效：{_conditions(primary, nearest)}",
    ))


def build_board_analysis_record(
    observation: dict[str, object], prior: dict[str, object] | None,
    recent: Sequence[dict[str, object]] = (),
    correction_prior: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the persisted level-one conclusion and its causal comparison."""
    conclusion_code, rendered_summary = render_board_summary(observation, prior, recent)
    transition = _detailed_transition(conclusion_code, prior)
    prior_effective_date = prior.get("effective_date") if prior else None
    metrics = observation.get("metrics") if isinstance(observation.get("metrics"), dict) else {}
    prior_metrics = _prior_metrics(prior)
    return {
        "conclusion_code": conclusion_code,
        "rendered_summary": rendered_summary,
        "comparison": {
            "transition": transition,
            "prior_run_id": prior.get("run_id") if prior else None,
            "prior_effective_date": (
                prior_effective_date.isoformat()
                if isinstance(prior_effective_date, date)
                else prior_effective_date
            ),
            "correction_baseline": _baseline_identity(correction_prior),
            "shape": {
                horizon: _metric_state_transition(
                    _shape_state(metrics, horizon), _shape_state(prior_metrics, horizon),
                )
                for horizon in ("short_shape", "medium_shape")
            },
            "price": _numeric_transition(
                _nested_number(metrics, "returns", "20"),
                _nested_number(prior_metrics, "returns", "20"), .01,
            ),
            "volume": _deviation_transition(
                _number(metrics.get("volume_ratio20")),
                _number(prior_metrics.get("volume_ratio20")), .15,
            ),
            "recent_sessions": [
                _history_identity(item) for item in recent[:5]
            ],
        },
    }


def observation_digest(observation: dict[str, object]) -> str:
    stable = {
        key: value for key, value in observation.items()
        if key not in {"run_id", "created_at_ms"}
    }
    return hashlib.sha256(json.dumps(
        stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=lambda value: value.isoformat() if isinstance(value, date) else str(value),
    ).encode("utf-8")).hexdigest()


def _observation(
    symbol: str, effective_date: date, bars: Sequence[StoredDailyBar],
    coverage: str, metrics: dict[str, object], states: Sequence[str],
    disqualifiers: Sequence[str], attention: Sequence[str],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": symbol.upper(), "effective_date": effective_date,
        "coverage_state": coverage, "state_codes": list(states),
        "metrics": metrics, "disqualifiers": list(disqualifiers),
        "attention_reasons": list(attention), "attention_eligible": bool(attention),
        "deep_analysis_state": "pending" if attention else "not-requested",
        "algorithm_version": ALGORITHM_VERSION, "config_version": CONFIG_VERSION,
        "bar_count": len(bars),
    }
    payload["input_digest"] = observation_digest({
        "symbol": symbol.upper(), "effective_date": effective_date,
        "bars": [[bar.trade_date.isoformat(), bar.open, bar.high, bar.low,
                  bar.close, bar.volume, getattr(bar, "source", "unspecified")]
                 for bar in bars],
        "algorithm_version": ALGORITHM_VERSION, "config_version": CONFIG_VERSION,
    })
    return payload


def _return(values: Sequence[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] <= 0:
        return None
    return values[-1] / values[-periods - 1] - 1


def _price_space(
    bars: Sequence[StoredDailyBar], states: Sequence[str],
    envelopes: dict[str, dict[str, object] | None], _atr14: float,
) -> dict[str, object]:
    generated = build_coarse_structural_scenario_items(
        tuple(_analysis_bar(bar) for bar in bars), states, envelopes,
    )
    return project_scenario_summary(generated)


def _analysis_bar(bar: StoredDailyBar) -> AnalysisBar:
    return AnalysisBar(
        period_start=bar.trade_date, period_end=bar.trade_date,
        open=bar.open, high=bar.high, low=bar.low, close=bar.close,
        volume=bar.volume, sources=(str(getattr(bar, "source", "daily-scan")),),
        contains_provisional=False, period_complete=True,
        observed_at_ms=int(getattr(bar, "updated_at_ms", 0)),
    )


def _safe_ratio(left: float, right: float) -> float | None:
    return left / right if right else None


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None and math.isfinite(value) else None


def _primary_state(states: Sequence[str]) -> str:
    order = (
        "bullish-boundary-triggered", "oversold-rebound-triggered",
        "bullish-transition-candidate", "oversold-exhaustion-candidate",
        "sudden-volume-expansion", "boundary-volume-contraction",
        "relative-strength-regime", "neutral",
    )
    return next((state for state in order if state in states), states[0] if states else "neutral")


def _detailed_transition(primary: str, prior: dict[str, object] | None) -> str:
    if prior is None:
        return "new"
    prior_payload = prior.get("payload", prior)
    if not isinstance(prior_payload, dict):
        return "new"
    prior_code = str(prior_payload.get("conclusion_code") or "neutral")
    if prior_code == primary:
        return "unchanged"
    if primary == "neutral":
        return "invalidated"
    families = {
        "bullish-transition-candidate": ("bullish", 1),
        "bullish-boundary-triggered": ("bullish", 2),
        "oversold-exhaustion-candidate": ("oversold", 1),
        "oversold-rebound-triggered": ("oversold", 2),
    }
    current_family = families.get(primary)
    prior_family = families.get(prior_code)
    if current_family and prior_family and current_family[0] == prior_family[0]:
        return "strengthened" if current_family[1] > prior_family[1] else "weakened"
    if prior_code == "neutral":
        return "strengthened"
    return "changed"


def _prior_metrics(prior: dict[str, object] | None) -> dict[str, object]:
    if prior is None:
        return {}
    payload = prior.get("payload", prior)
    if not isinstance(payload, dict):
        return {}
    metrics = payload.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _shape_state(metrics: dict[str, object], key: str) -> str | None:
    value = metrics.get(key)
    return str(value.get("state")) if isinstance(value, dict) and value.get("state") else None


def _metric_state_transition(current: str | None, prior: str | None) -> str:
    if prior is None:
        return "new"
    if current is None:
        return "invalidated"
    if current == prior:
        return "unchanged"
    rank = {"falling": -1, "sideways": 0, "rising": 1}
    if current not in rank or prior not in rank:
        return "changed"
    return "strengthened" if rank[current] > rank[prior] else "weakened"


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _nested_number(metrics: dict[str, object], group: str, key: str) -> float | None:
    value = metrics.get(group)
    return _number(value.get(key)) if isinstance(value, dict) else None


def _numeric_transition(
    current: float | None, prior: float | None, tolerance: float,
) -> str:
    if prior is None:
        return "new"
    if current is None:
        return "invalidated"
    if abs(current - prior) <= tolerance:
        return "unchanged"
    return "strengthened" if current > prior else "weakened"


def _deviation_transition(
    current: float | None, prior: float | None, tolerance: float,
) -> str:
    if prior is None:
        return "new"
    if current is None:
        return "invalidated"
    current_deviation = abs(current - 1)
    prior_deviation = abs(prior - 1)
    if abs(current_deviation - prior_deviation) <= tolerance:
        return "unchanged"
    return "strengthened" if current_deviation > prior_deviation else "weakened"


def _baseline_identity(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    payload = value.get("payload", value)
    return {
        "run_id": value.get("run_id"),
        "effective_date": str(value.get("effective_date") or ""),
        "conclusion_code": (
            payload.get("conclusion_code") if isinstance(payload, dict) else None
        ),
    }


def _history_identity(value: dict[str, object]) -> dict[str, object]:
    metrics = _prior_metrics(value)
    payload = value.get("payload", value)
    return {
        "run_id": value.get("run_id"),
        "effective_date": str(value.get("effective_date") or ""),
        "conclusion_code": (
            payload.get("conclusion_code") if isinstance(payload, dict) else None
        ),
        "return20": _nested_number(metrics, "returns", "20"),
        "volume_ratio20": _number(metrics.get("volume_ratio20")),
    }


def _nearest_envelope(envelopes: dict[str, object]) -> tuple[str, float] | None:
    candidates = []
    for key, value in envelopes.items():
        if isinstance(value, dict) and value.get("distance_atr") is not None:
            candidates.append((str(key), float(value["distance_atr"])))
    return min(candidates, key=lambda item: abs(item[1])) if candidates else None


def _mapping_value(value: object, key: str) -> float | None:
    return float(value[key]) if isinstance(value, dict) and value.get(key) is not None else None


def _state_label(state: str) -> str:
    return {
        "bullish-boundary-triggered": "多头边界已触发",
        "oversold-rebound-triggered": "超跌反弹已触发",
        "bullish-transition-candidate": "多头临界",
        "oversold-exhaustion-candidate": "下跌衰竭反转临界",
        "sudden-volume-expansion": "突然放量",
        "boundary-volume-contraction": "边界附近缩量",
        "relative-strength-regime": "相对强弱异动",
        "neutral": "中性",
    }.get(state, state)


def _state_conclusion(state: str) -> str:
    return {
        "bullish-boundary-triggered": "收盘已越过下降边界，仍需后续量价确认。",
        "bullish-transition-candidate": "价格接近下降压力边界，尚未形成有效突破。",
        "oversold-exhaustion-candidate": "下跌已经扩展且多项动能衰减，尚未确认反转。",
        "oversold-rebound-triggered": "下跌动能衰减后收盘突破短期反转边界，仍需后续确认。",
        "sudden-volume-expansion": "成交量显著偏离近期基准，需要结合价格方向继续观察。",
        "boundary-volume-contraction": "价格临近边界且成交收缩，处于等待方向选择阶段。",
        "relative-strength-regime": "相对市场强弱明显偏离近期常态。",
        "neutral": "当前未命中版本化异动门槛。",
    }.get(state, "当前状态由固定算法识别，需结合证据查看。")


def _shape_label(value: object) -> str:
    state = value.get("state") if isinstance(value, dict) else "insufficient"
    return {"rising": "上行", "falling": "下行", "sideways": "震荡"}.get(str(state), "数据不足")


def _percent(value: float | None) -> str:
    return "不可用" if value is None else f"{value * 100:+.2f}%"


def _multiple(value: object) -> str:
    return "不可用" if value is None else f"{float(value):.2f}倍"


def _volume_sentence(metrics: dict[str, object], volume_ratio: object) -> str:
    if metrics.get("volume_semantics") == "synthetic-not-traded":
        diagnostics = metrics.get("active_market_value_diagnostics")
        if not isinstance(diagnostics, dict):
            return "合成指数不解释K线成交量；当日覆盖诊断不可用。"
        coverage = diagnostics.get("coverage_ratio")
        coverage_text = "不可用" if coverage is None else f"{float(coverage) * 100:.2f}%"
        return (
            "合成指数不解释K线成交量；"
            f"覆盖{coverage_text}（{diagnostics.get('eligible_count', 0)}/"
            f"{diagnostics.get('total_count', 0)}），"
            f"活跃市值贡献{float(diagnostics.get('contribution_total') or 0):.2f}。"
        )
    return f"当日量为20日中位量的{_multiple(volume_ratio)}。"


def _breadth_sentence(metrics: dict[str, object]) -> str:
    breadth = metrics.get("board_breadth")
    if not isinstance(breadth, dict) or breadth.get("covered_member_count") is None:
        return "成分覆盖不可用。"
    value = breadth.get("breadth")
    concentration = breadth.get("impact_concentration_hhi")
    value_text = "不可用" if value is None else f"{float(value):+.2f}"
    concentration_text = (
        "不可用" if concentration is None else f"{float(concentration) * 100:.1f}%"
    )
    return (
        f"上涨{breadth.get('advance_count', 0)}、下跌{breadth.get('decline_count', 0)}，"
        f"宽度{value_text}；价格量能影响代理HHI为{concentration_text}。"
    )


def _price_space_sentence(metrics: dict[str, object]) -> str:
    value = metrics.get("price_space")
    if not isinstance(value, dict):
        return "目标位数据不可用，不计算盈亏比。"
    upside = value.get("upside_target")
    downside = value.get("downside_target")
    upside_text = _target_text(upside, "上涨")
    downside_text = _target_text(downside, "下跌")
    ratio = _number(value.get("risk_reward_ratio"))
    entry = _number(value.get("entry_price"))
    invalidation = _number(value.get("invalidation_price"))
    if ratio is None or entry is None or invalidation is None:
        return f"{upside_text}；{downside_text}；当前没有可复现的入场/失效组合，不计算盈亏比。"
    threshold = _number(value.get("minimum_risk_reward")) or 1.5
    verdict = "存在博弈空间" if bool(value.get("has_trade_space")) else f"低于{threshold:.2f}:1，空间不足"
    return (
        f"{upside_text}；{downside_text}；计划入场{entry:.2f}、"
        f"失效位{invalidation:.2f}，盈亏比{ratio:.2f}:1（{verdict}）。"
    )


def _target_text(value: object, direction: str) -> str:
    if not isinstance(value, dict) or value.get("price") is None:
        return f"{direction}目标位暂无合格历史区间位"
    return (
        f"{direction}目标位{float(value['price']):.2f}"
        f"（{int(value.get('lookback_sessions') or 0)}日区间）"
    )


def _transition_label(value: str) -> str:
    return {
        "new": "首次观察", "unchanged": "状态延续",
        "strengthened": "状态增强", "weakened": "状态减弱",
        "changed": "状态切换", "invalidated": "原状态失效",
    }[value]


def _transition_sentence(
    value: str, prior: dict[str, object] | None,
    recent: Sequence[dict[str, object]], primary: str,
) -> str:
    if prior is None:
        return "没有兼容的历史运行，建立首个比较基线。"
    prior_date = prior.get("effective_date")
    if prior_date is None and isinstance(prior.get("payload"), dict):
        prior_date = prior["payload"].get("effective_date")
    label = str(prior_date) if prior_date else "上一兼容运行"
    same_count = sum(
        1 for item in recent[:5]
        if isinstance(item.get("payload", item), dict)
        and item.get("payload", item).get("conclusion_code") == primary
    )
    context = f"近{min(len(recent), 5)}期中当前状态出现{same_count}次" if recent else "近五期上下文不足"
    return f"较{label}{_transition_label(value)}；{context}。"


def _conditions(state: str, nearest: tuple[str, float] | None) -> str:
    if state in {"bullish-transition-candidate", "bullish-boundary-triggered"}:
        period = nearest[0] if nearest else "当前"
        return f"放量收于{period}边界上方确认；重新跌回边界下方0.25 ATR视为失败。"
    if state == "oversold-exhaustion-candidate":
        return "收盘突破短期反转边界才确认；放量创新低则失效。"
    if state == "oversold-rebound-triggered":
        return "已触发短期反转边界；重新放量创新低则失效。"
    return "继续观察下一交易日价格方向、量能及结构状态变化。"
