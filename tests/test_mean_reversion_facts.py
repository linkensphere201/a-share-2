from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from stock_harness.mean_reversion_facts import (
    _atr_series,
    _ema_series,
    build_mean_reversion_facts,
)
from stock_harness.models import StoredDailyBar


def test_directional_pullback_requires_price_confirmation() -> None:
    closes = [100 + index * .35 for index in range(125)]
    closes.extend([144 - index * .55 for index in range(14)])
    closes.extend([136.6, 136.8, 137.0, 138.5, 138.7, 139.0])
    bars = _bars(closes, final_high_offset=.3, final_volume=1_400)
    bars[-3] = replace(bars[-3], high=closes[-3] + .3)
    bars[-2] = replace(bars[-2], volume=1_400)

    facts = build_mean_reversion_facts(bars)

    assert facts["setup_family"] == "directional-pullback"
    assert facts["state"] == "reversal-confirmed"
    assert facts["parent_trend"] == "up"
    assert facts["confirmation"]["confirmed"] is True
    assert facts["price_volume"]["path"] == "volume-backed-reclaim"
    assert len(facts["chart"]["center_points"]) == 120


def test_first_reclaim_is_observation_not_same_day_opportunity() -> None:
    closes = [100 + index * .35 for index in range(125)]
    closes.extend([144 - index * .55 for index in range(14)])
    closes.extend([136.6, 136.8, 137.0, 138.5])

    bars = _bars(closes, final_high_offset=.3, final_volume=1_400)
    facts = build_mean_reversion_facts(bars)

    assert facts["confirmation"]["stage"] == "initial-reclaim"
    assert facts["confirmation"]["confirmed"] is False
    assert facts["state"] == "initial-reclaim-observation"


def test_consecutive_momentum_day_does_not_count_as_one_day_retest() -> None:
    closes = [100 + index * .35 for index in range(125)]
    closes.extend([144 - index * .55 for index in range(14)])
    closes.extend([136.6, 136.8, 137.0, 138.5, 141.0])

    bars = _bars(closes, final_volume=1_800)
    bars[-2] = replace(bars[-2], high=closes[-2] + .3)
    facts = build_mean_reversion_facts(bars)

    assert facts["confirmation"]["hold_sessions"] == 1
    assert facts["confirmation"]["first_hold_retest"] is False
    assert facts["confirmation"]["first_hold_stand"] is False
    assert facts["confirmation"]["confirmed"] is False
    assert facts["state"] == "confirmation-hold"


def test_expanding_structural_decline_is_never_treated_as_reversion() -> None:
    closes = [100 + index * .1 for index in range(120)]
    closes.extend([112 - index * 1.5 for index in range(20)])

    facts = build_mean_reversion_facts(_bars(closes, final_volume=2_000))

    assert facts["structural_break"] is True
    assert facts["state"] == "structural-break"
    assert facts["setup_family"] == "none"
    assert "structural-break" in facts["disqualifiers"]
    assert facts["chart"] == {}


def test_fact_generation_is_point_in_time_and_ignores_future_suffix() -> None:
    prefix = [100 + index * .2 for index in range(130)]
    future = [500, 5, 800, 3]
    all_bars = _bars([*prefix, *future])

    baseline = build_mean_reversion_facts(_bars(prefix, final_high_offset=.6))
    replay = build_mean_reversion_facts(all_bars[: len(prefix)])

    assert baseline == replay
    assert baseline["as_of_date"] == all_bars[len(prefix) - 1].trade_date.isoformat()


def test_decision_inputs_are_causal_even_when_future_path_reverses() -> None:
    prefix = [100 + index * .35 for index in range(125)]
    prefix.extend([144 - index * .55 for index in range(14)])
    prefix.extend([136.6, 136.8, 137.0, 138.2])
    falling_future = [130, 120, 110]
    rallying_future = [145, 155, 165]

    prefix_bars = _bars(prefix)
    cutoff = build_mean_reversion_facts(prefix_bars)
    falling = build_mean_reversion_facts([
        *prefix_bars, *_bars(falling_future, start_offset=len(prefix)),
    ][:len(prefix)])
    rallying = build_mean_reversion_facts([
        *prefix_bars, *_bars(rallying_future, start_offset=len(prefix)),
    ][:len(prefix)])

    assert cutoff == falling == rallying
    assert cutoff["regime"]["causal_through"] == cutoff["as_of_date"]


def test_targets_separate_reversion_centers_from_trend_extension() -> None:
    closes = [100 + index * .35 for index in range(125)]
    closes.extend([144 - index * .55 for index in range(14)])
    closes.extend([136.6, 136.8, 137.0, 138.5, 138.7, 139.0])

    bars = _bars(closes, final_high_offset=.3, final_volume=1_400)
    bars[-3] = replace(bars[-3], high=closes[-3] + .3)
    bars[-2] = replace(bars[-2], volume=1_400)
    facts = build_mean_reversion_facts(bars)

    assert facts["version"] == "mean-reversion-facts-v4"
    assert facts["confirmation"]["quality_score"] >= 55
    assert any(target["target_class"] == "mean-reversion" for target in facts["targets"])
    assert {target["target_class"] for target in facts["targets"]} <= {
        "mean-reversion", "extension",
    }
    assert facts["maximum_holding_sessions"] == 10


def test_synthetic_volume_disables_volume_confirmation() -> None:
    closes = [100 + index * .1 for index in range(130)]

    facts = build_mean_reversion_facts(
        _bars(closes, final_volume=10_000), volume_semantics="synthetic",
    )

    assert facts["price_volume"]["volume_semantics"] == "synthetic"
    assert facts["price_volume"]["volume_ratio20"] is None
    assert facts["price_volume"]["selling_volume_ratio"] is None


def test_recent_low_deviation_uses_each_sessions_center_and_atr() -> None:
    closes = [80 + index * .4 for index in range(130)]
    closes.extend([132, 127, 123, 121, 120, 121, 122, 123, 124, 125])
    bars = _bars(closes)

    facts = build_mean_reversion_facts(bars)
    ema20 = _ema_series([bar.close for bar in bars], 20)
    atr14 = _atr_series(bars, 14)
    expected = min(
        (bar.low - center) / atr
        for bar, center, atr in zip(bars[-10:], ema20[-10:], atr14[-10:])
    )

    assert facts["recent_low_deviation_atr"] == round(expected, 6)


def _bars(
    closes: list[float], *, final_high_offset: float = 1,
    final_volume: int = 1_000, start_offset: int = 0,
) -> list[StoredDailyBar]:
    start = date(2025, 1, 1)
    return [StoredDailyBar(
        symbol="TEST",
        trade_date=start + timedelta(days=start_offset + index),
        open=close - .2,
        high=close + (final_high_offset if index == len(closes) - 1 else .6),
        low=close - .6,
        close=close,
        volume=final_volume if index == len(closes) - 1 else 1_000,
        source="test",
        updated_at_ms=0,
    ) for index, close in enumerate(closes)]
