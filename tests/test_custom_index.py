from datetime import date

import pytest

from stock_harness.custom_index import (
    ConstituentInput, base_bar, calculate_bar, normalize_members,
)
from stock_harness.models import DailyBar


def _bar(symbol: str, open_: float, high: float, low: float, close: float) -> DailyBar:
    return DailyBar(symbol, date(2026, 8, 4), open_, high, low, close, 100)


def test_equal_and_manual_weights_are_normalized_deterministically():
    equal = normalize_members([("000001.SZ", None), ("600000.SH", None)], "equal")
    manual = normalize_members([("000001.SZ", 3), ("600000.SH", 1)], "manual")

    assert [item.normalized_weight for item in equal] == [0.5, 0.5]
    assert [item.normalized_weight for item in manual] == [0.75, 0.25]
    with pytest.raises(ValueError, match="unique"):
        normalize_members([("000001.SZ", 1), ("000001.SZ", 1)], "manual")
    with pytest.raises(ValueError, match="positive"):
        normalize_members([("000001.SZ", 0)], "manual")


def test_single_member_matches_its_adjusted_return_path():
    result = calculate_bar(date(2026, 8, 4), 1000, [
        ConstituentInput(
            "000001.SZ", 1, _bar("000001.SZ", 10, 12, 9, 11),
            previous_close=10, current_factor=2, previous_factor=2,
            status="trading",
        ),
    ])

    assert result is not None
    assert (result.open, result.high, result.low, result.close) == (1000, 1200, 900, 1100)
    assert result.daily_return == pytest.approx(0.1)
    assert result.quality_status == "complete"


def test_weighted_envelope_is_order_invariant_and_handles_adjustment_changes():
    first = ConstituentInput(
        "000001.SZ", 3, _bar("000001.SZ", 5, 6, 4.5, 5.5),
        previous_close=10, current_factor=2, previous_factor=1,
        status="trading",
    )
    second = ConstituentInput(
        "600000.SH", 1, _bar("600000.SH", 20, 22, 18, 21),
        previous_close=20, current_factor=1, previous_factor=1,
        status="trading",
    )

    left = calculate_bar(date(2026, 8, 4), 1000, [first, second])
    right = calculate_bar(date(2026, 8, 4), 1000, [second, first])

    assert left == right
    assert left is not None
    assert left.close == pytest.approx(1087.5)
    assert left.low <= min(left.open, left.close) <= max(left.open, left.close) <= left.high


def test_suspension_is_zero_return_but_unexplained_missing_data_fails():
    suspended = calculate_bar(date(2026, 8, 4), 1000, [
        ConstituentInput(
            "000001.SZ", 1, None, 10, 1, 1, "inferred_suspension",
        ),
    ])

    assert suspended is not None
    assert suspended.close == 1000
    assert suspended.quality_status == "inferred_suspension"
    with pytest.raises(ValueError, match="missing constituent evidence"):
        calculate_bar(date(2026, 8, 4), 1000, [
            ConstituentInput("000001.SZ", 1, None, 10, 1, 1, "missing"),
        ])


def test_base_bar_is_a_persistable_flat_reference_point():
    result = base_bar(date(2026, 1, 1), 1000, 2)

    assert (result.open, result.high, result.low, result.close) == (1000, 1000, 1000, 1000)
    assert result.eligible_count == result.total_count == 2
