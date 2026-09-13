from __future__ import annotations

from datetime import date, timedelta

from stock_harness.mean_reversion_facts import build_mean_reversion_facts
from stock_harness.models import StoredDailyBar


def test_directional_pullback_requires_price_confirmation() -> None:
    closes = [100 + index * .35 for index in range(125)]
    closes.extend([144 - index * .55 for index in range(14)])
    closes.extend([136.6, 136.8, 137.0, 138.2])
    bars = _bars(closes, final_high_offset=.3, final_volume=1_400)

    facts = build_mean_reversion_facts(bars)

    assert facts["setup_family"] == "directional-pullback"
    assert facts["state"] == "reversal-confirmed"
    assert facts["parent_trend"] == "up"
    assert facts["confirmation"]["confirmed"] is True
    assert facts["price_volume"]["path"] == "volume-backed-reclaim"
    assert len(facts["chart"]["center_points"]) == 120


def test_expanding_structural_decline_is_never_treated_as_reversion() -> None:
    closes = [100 + index * .1 for index in range(120)]
    closes.extend([112 - index * 1.5 for index in range(20)])

    facts = build_mean_reversion_facts(_bars(closes, final_volume=2_000))

    assert facts["structural_break"] is True
    assert facts["state"] == "structural-break"
    assert facts["setup_family"] == "none"
    assert "structural-break" in facts["disqualifiers"]


def test_fact_generation_is_point_in_time_and_ignores_future_suffix() -> None:
    prefix = [100 + index * .2 for index in range(130)]
    future = [500, 5, 800, 3]
    all_bars = _bars([*prefix, *future])

    baseline = build_mean_reversion_facts(_bars(prefix, final_high_offset=.6))
    replay = build_mean_reversion_facts(all_bars[: len(prefix)])

    assert baseline == replay
    assert baseline["as_of_date"] == all_bars[len(prefix) - 1].trade_date.isoformat()


def test_synthetic_volume_disables_volume_confirmation() -> None:
    closes = [100 + index * .1 for index in range(130)]

    facts = build_mean_reversion_facts(
        _bars(closes, final_volume=10_000), volume_semantics="synthetic",
    )

    assert facts["price_volume"]["volume_semantics"] == "synthetic"
    assert facts["price_volume"]["volume_ratio20"] is None
    assert facts["price_volume"]["selling_volume_ratio"] is None


def _bars(
    closes: list[float], *, final_high_offset: float = 1,
    final_volume: int = 1_000,
) -> list[StoredDailyBar]:
    start = date(2025, 1, 1)
    return [StoredDailyBar(
        symbol="TEST",
        trade_date=start + timedelta(days=index),
        open=close - .2,
        high=close + (final_high_offset if index == len(closes) - 1 else .6),
        low=close - .6,
        close=close,
        volume=final_volume if index == len(closes) - 1 else 1_000,
        source="test",
        updated_at_ms=0,
    ) for index, close in enumerate(closes)]
