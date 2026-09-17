"""Fixed, causal price/volume similarity for the user-selected deep-drawdown base."""

from datetime import date
from math import isfinite, log, sqrt
from statistics import correlation, fmean, pstdev
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar

STRATEGY_ID = "deep-drawdown-consolidation"
ALGORITHM_VERSION = "deep-drawdown-consolidation-v2"
KIND = "deep-drawdown-range"
STATE = "shape-match"
WINDOW = 60
MIN_SCORE = 85.
REFERENCE_SYMBOL = "002137.SZ"
REFERENCE_START = "2026-06-24"
REFERENCE_END = "2026-09-15"
# Frozen Tushare final closes and share volumes; the September 16 limit-up is absent.
REFERENCE_CLOSES = (
    10.21, 10.71, 11.02, 11.30, 11.50, 10.77, 9.89, 10.42, 10.39, 11.43,
    11.72, 12.25, 12.02, 10.82, 9.74, 8.77, 8.15, 7.34, 6.90, 7.24,
    6.87, 6.94, 6.70, 6.95, 7.10, 7.14, 7.09, 7.31, 7.47, 7.66,
    7.88, 7.88, 8.02, 8.03, 7.93, 8.05, 7.92, 7.96, 8.08, 8.16,
    7.68, 7.60, 7.60, 7.45, 7.60, 7.57, 7.66, 7.70, 7.85, 7.92,
    7.81, 7.80, 7.69, 7.80, 7.78, 7.66, 7.56, 7.46, 7.54, 7.45,
)
REFERENCE_VOLUMES = (
    57728149, 74113052, 94215071, 88887281, 80429411, 75306357, 55943122, 76592111, 63015431, 100131150,
    171751405, 140025591, 115499694, 59808702, 14440100, 81903601, 67824338, 56261070, 52816907, 52752967,
    40731902, 26725390, 23747702, 28245599, 41986800, 27432000, 25213800, 47737660, 36085064, 32373600,
    40836769, 29852787, 31710602, 24316110, 21617400, 19116101, 22406567, 18739600, 18162079, 22715699,
    27370299, 18509011, 14161812, 16730700, 15426700, 11774800, 11029100, 18186700, 14931428, 16482424,
    10037700, 11416800, 14925700, 13477100, 13174100, 11377900, 9279500, 10206200, 8310100, 7523800,
)


def _volume_path(volumes: Sequence[float]) -> tuple[float, ...]:
    values = tuple(log(v) for v in volumes)
    center = fmean(values)
    return tuple(fmean(values[i:i + 5]) - center for i in range(len(values) - 4))


_REFERENCE_PRICES = tuple(log(p) for p in REFERENCE_CLOSES)
_REFERENCE_VOLUME_PATH = _volume_path(REFERENCE_VOLUMES)
_REFERENCE_AMPLITUDE = pstdev(_REFERENCE_PRICES)


def detect_deep_drawdown(bars: Sequence[AnalysisBar]) -> dict[str, object] | None:
    bars = tuple(bars[-WINDOW:])
    if len(bars) != WINDOW or bars[-1].period_end < date.fromisoformat(REFERENCE_END):
        return None
    if any(
        not b.period_complete or b.contains_provisional or b.contains_roll_event
        or not all(isfinite(v) and v > 0 for v in (b.open, b.high, b.low, b.close, b.volume))
        or not b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high
        for b in bars
    ) or any(a.period_end >= b.period_start for a, b in zip(bars, bars[1:])):
        return None
    volume_ratio = fmean(b.volume for b in bars[-10:]) / fmean(b.volume for b in bars[:20])
    # Price similarity must not compensate for the absence of volume contraction.
    if volume_ratio >= 1:
        return None
    prices = tuple(log(b.close) for b in bars)
    amplitude = pstdev(prices)
    if amplitude < 1e-8:
        return None
    price_corr = max(-1., min(1., correlation(prices, _REFERENCE_PRICES)))
    tail_corr = (max(-1., min(1., correlation(prices[-20:], _REFERENCE_PRICES[-20:])))
                 if pstdev(prices[-20:]) >= 1e-8 else 0.)
    amplitude_error = min(1., abs(log(amplitude / _REFERENCE_AMPLITUDE)) / log(4))
    volume_path = _volume_path(tuple(b.volume for b in bars))
    volume_error = min(1., sqrt(fmean((a - b) ** 2 for a, b in
                                    zip(volume_path, _REFERENCE_VOLUME_PATH))) / log(3))
    components = {
        "price_path": 50 * (price_corr + 1) / 2,
        "recent_path": 20 * (tail_corr + 1) / 2,
        "amplitude": 15 * (1 - amplitude_error),
        "volume_path": 15 * (1 - volume_error),
    }
    score = sum(components.values())
    if score < MIN_SCORE:
        return None
    peak, drawdown = bars[0].close, 0.
    for b in bars:
        peak = max(peak, b.close)
        drawdown = min(drawdown, b.close / peak - 1)
    tail = bars[-20:]
    return {
        "kind": KIND, "algorithm_version": ALGORITHM_VERSION, "stage": STATE,
        "screen_eligible": True, "window": WINDOW, "score": round(score, 6),
        "minimum_score": MIN_SCORE, "as_of_date": bars[-1].period_end.isoformat(),
        "window_start_date": bars[0].period_start.isoformat(),
        "start_date": tail[0].period_start.isoformat(), "end_date": tail[-1].period_end.isoformat(),
        "lower": min(b.low for b in tail), "upper": max(b.high for b in tail),
        "latest_close": bars[-1].close,
        "price_correlation": round(price_corr, 6), "recent_correlation": round(tail_corr, 6),
        "similarity_components": {k: round(v, 6) for k, v in components.items()},
        "return_60d_percent": round((bars[-1].close / bars[0].close - 1) * 100, 4),
        "max_drawdown_percent": round(drawdown * 100, 4),
        "close_range_20d_percent": round((max(b.close for b in tail) / min(b.close for b in tail) - 1) * 100, 4),
        "recent_early_volume_ratio": round(volume_ratio, 6),
        "reference": {"symbol": REFERENCE_SYMBOL, "start_date": REFERENCE_START,
                      "end_date": REFERENCE_END, "source": "tushare", "price_basis": "raw"},
        "first_target_price": None, "first_risk_reward": None,
    }
