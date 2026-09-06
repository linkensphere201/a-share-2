"""Deterministic first-level daily observations for market and board indices."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
import hashlib
import json
import math
from statistics import fmean, median

from stock_harness.models import StoredDailyBar


ALGORITHM_VERSION = "daily-market-board-observation-v1"
CONFIG_VERSION = "daily-market-board-defaults-v1"
MINIMUM_BARS = 120
LOOKBACK_BARS = 260


def analyze_daily_series(
    symbol: str,
    bars: Sequence[StoredDailyBar],
    effective_date: date,
    *,
    benchmark_bars: Sequence[StoredDailyBar] = (),
) -> dict[str, object]:
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
    atr14 = _atr(visible, 14)
    atr5 = _atr(visible, 5)
    atr20 = _atr(visible, 20)
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
    median_volume20 = median(volumes[-20:])
    volume_ratio = latest.volume / median_volume20 if median_volume20 > 0 else None
    recent_volume_ratio = _safe_ratio(fmean(volumes[-5:]), fmean(volumes[-10:-5]))
    atr_compression = _safe_ratio(atr5, atr20)
    short_shape = _shape(closes, 14)
    medium_shape = _shape(closes, 28)
    envelopes = {
        label: _descending_upper_envelope(visible, period, atr14)
        for label, period in (("3m", 63), ("6m", 126), ("1y", 250))
    }
    downside = _downside_deceleration(visible, atr14)
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
    if any(item and item["state"] == "broken" for item in envelopes.values()):
        states.append("bullish-boundary-triggered")

    if downside["extended"] and downside["deceleration_count"] >= 3:
        states.append("oversold-exhaustion-candidate")
        attention.append("downside-exhaustion")
    if volume_ratio is not None and volume_ratio >= 1.8:
        states.append("sudden-volume-expansion")
        attention.append("sudden-volume-expansion")
    if (
        volume_ratio is not None and volume_ratio <= .65
        and nearest_distance is not None and 0 <= nearest_distance <= 1
    ):
        states.append("boundary-volume-contraction")
        attention.append("boundary-volume-contraction")
    if relative_strength["20"] is not None and abs(float(relative_strength["20"])) >= .08:
        states.append("relative-strength-regime")
        if volume_ratio is not None and volume_ratio >= 1.2:
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
        "volume": latest.volume,
        "median_volume20": _round(median_volume20),
        "volume_ratio20": _round(volume_ratio),
        "recent_volume_ratio_5_5": _round(recent_volume_ratio),
        "short_shape": short_shape,
        "medium_shape": medium_shape,
        "downside": downside,
        "descending_envelopes": envelopes,
    }
    return _observation(
        symbol, effective_date, visible, "complete", metrics,
        sorted(set(states)), disqualifiers, sorted(set(attention)),
    )


def render_board_summary(
    observation: dict[str, object], prior: dict[str, object] | None,
) -> tuple[str, str]:
    metrics = observation.get("metrics", {})
    if not isinstance(metrics, dict) or observation["coverage_state"] != "complete":
        return "data-unavailable", "- 结论：板块日线覆盖不足，当前无法形成固定算法结论。"
    states = [str(item) for item in observation.get("state_codes", [])]
    primary = _primary_state(states)
    transition = _transition(primary, prior)
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
        f"- 量：当日量为20日中位量的{_multiple(volume_ratio)}。",
        f"- 近期对比：{_transition_sentence(transition, prior)}",
        f"- 确认/失效：{_conditions(primary, nearest)}",
    ))


def build_board_analysis_record(
    observation: dict[str, object], prior: dict[str, object] | None,
) -> dict[str, object]:
    """Build the persisted level-one conclusion and its causal comparison."""
    conclusion_code, rendered_summary = render_board_summary(observation, prior)
    transition = _transition(conclusion_code, prior)
    prior_effective_date = prior.get("effective_date") if prior else None
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


def _atr(bars: Sequence[StoredDailyBar], periods: int) -> float:
    selected = bars[-(periods + 1):]
    ranges = [
        max(current.high - current.low, abs(current.high - previous.close),
            abs(current.low - previous.close))
        for previous, current in zip(selected, selected[1:])
    ]
    return fmean(ranges) if ranges else 0.0


def _shape(closes: Sequence[float], periods: int) -> dict[str, object]:
    values = list(closes[-periods:])
    if len(values) < periods:
        return {"state": "insufficient", "slope_per_10": None}
    logs = [math.log(value) for value in values if value > 0]
    if len(logs) != len(values):
        return {"state": "invalid", "slope_per_10": None}
    slope = _linear_slope(logs)
    change = math.exp(slope * 10) - 1
    state = "rising" if change >= .02 else "falling" if change <= -.02 else "sideways"
    return {"state": state, "slope_per_10": _round(change)}


def _descending_upper_envelope(
    bars: Sequence[StoredDailyBar], periods: int, atr14: float,
) -> dict[str, object] | None:
    if len(bars) < periods + 1 or atr14 <= 0:
        return None
    history = list(bars[-(periods + 1):-1])
    highs = [bar.high for bar in history]
    slope = _linear_slope(highs)
    if slope >= 0:
        return None
    intercept = fmean(highs) - slope * (len(highs) - 1) / 2
    intercept += max(value - (intercept + slope * index) for index, value in enumerate(highs))
    boundary = intercept + slope * len(highs)
    latest = bars[-1]
    distance = (boundary - latest.close) / atr14
    buffer = max(boundary * .005, atr14 * .25)
    state = "broken" if latest.close > boundary + buffer else "approaching" if 0 <= distance <= 1 else "none"
    return {
        "period_bars": periods, "boundary": _round(boundary),
        "slope_per_bar": _round(slope), "distance_atr": _round(distance),
        "state": state,
    }


def _downside_deceleration(
    bars: Sequence[StoredDailyBar], atr14: float,
) -> dict[str, object]:
    closes = [bar.close for bar in bars]
    latest = closes[-1]
    high20 = max(bar.high for bar in bars[-20:])
    ma20 = fmean(closes[-20:])
    extended = atr14 > 0 and ((high20 - latest) / atr14 >= 2.5 or (ma20 - latest) / atr14 >= 1.5)
    recent = closes[-6:]
    prior = closes[-11:-5]
    recent_negative = sum(abs(b / a - 1) for a, b in zip(recent, recent[1:]) if b < a)
    prior_negative = sum(abs(b / a - 1) for a, b in zip(prior, prior[1:]) if b < a)
    count = 0
    facts: list[str] = []
    if prior_negative > 0 and recent_negative <= prior_negative * .65:
        count += 1; facts.append("negative-return-deceleration")
    if latest >= min(closes[-4:]):
        count += 1; facts.append("no-new-low-three-sessions")
    recent_bodies = fmean(abs(bar.close - bar.open) for bar in bars[-3:])
    prior_bodies = fmean(abs(bar.close - bar.open) for bar in bars[-8:-3])
    if prior_bodies > 0 and recent_bodies <= prior_bodies * .8:
        count += 1; facts.append("body-contraction")
    recent_down_volume = [bar.volume for before, bar in zip(bars[-6:-1], bars[-5:]) if bar.close < before.close]
    prior_down_volume = [bar.volume for before, bar in zip(bars[-11:-6], bars[-10:-5]) if bar.close < before.close]
    if recent_down_volume and prior_down_volume and fmean(recent_down_volume) <= fmean(prior_down_volume) * .85:
        count += 1; facts.append("sell-volume-contraction")
    if _linear_slope([math.log(value) for value in closes[-14:]]) > _linear_slope([math.log(value) for value in closes[-28:-14]]):
        count += 1; facts.append("slope-improvement")
    return {"extended": extended, "deceleration_count": count, "facts": facts}


def _linear_slope(values: Sequence[float]) -> float:
    count = len(values)
    center = (count - 1) / 2
    denominator = sum((index - center) ** 2 for index in range(count))
    return sum((index - center) * value for index, value in enumerate(values)) / denominator if denominator else 0.0


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


def _transition(primary: str, prior: dict[str, object] | None) -> str:
    if prior is None:
        return "new"
    prior_payload = prior.get("payload", prior)
    if not isinstance(prior_payload, dict):
        return "new"
    prior_code = str(prior_payload.get("conclusion_code") or "neutral")
    return "unchanged" if prior_code == primary else "invalidated" if primary == "neutral" else "changed"


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


def _transition_label(value: str) -> str:
    return {"new": "首次观察", "unchanged": "状态延续", "changed": "状态变化", "invalidated": "原状态失效"}[value]


def _transition_sentence(value: str, prior: dict[str, object] | None) -> str:
    if prior is None:
        return "没有兼容的历史运行，建立首个比较基线。"
    prior_date = prior.get("effective_date")
    if prior_date is None and isinstance(prior.get("payload"), dict):
        prior_date = prior["payload"].get("effective_date")
    label = str(prior_date) if prior_date else "上一兼容运行"
    return f"较{label}{_transition_label(value)}。"


def _conditions(state: str, nearest: tuple[str, float] | None) -> str:
    if state in {"bullish-transition-candidate", "bullish-boundary-triggered"}:
        period = nearest[0] if nearest else "当前"
        return f"放量收于{period}边界上方确认；重新跌回边界下方0.25 ATR视为失败。"
    if state == "oversold-exhaustion-candidate":
        return "收盘突破短期反转边界才确认；放量创新低则失效。"
    return "继续观察下一交易日价格方向、量能及结构状态变化。"
