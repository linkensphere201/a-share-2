from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.pattern_tolerances import build_pattern_tolerance_profile


def _bars(range_percent: float) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    result = []
    close = 100.0
    for index in range(30):
        close *= 1 + (range_percent / 4 if index % 2 == 0 else -range_percent / 4)
        result.append(AnalysisBar(
            start + timedelta(days=index), start + timedelta(days=index),
            close, close * (1 + range_percent / 2),
            close * (1 - range_percent / 2), close,
            100, ("test",), False, True, index,
        ))
    return result


def test_higher_volatility_expands_shape_tolerances_and_move_requirements():
    quiet = build_pattern_tolerance_profile(_bars(0.015))
    volatile = build_pattern_tolerance_profile(_bars(0.08))

    assert volatile.volatility_ratio > quiet.volatility_ratio
    assert volatile.endpoint_tolerance_percent > quiet.endpoint_tolerance_percent
    assert volatile.prominence_percent > quiet.prominence_percent
    assert volatile.boundary_residual_percent > quiet.boundary_residual_percent
    assert volatile.v_move_percent > quiet.v_move_percent
    assert volatile.breakout_buffer_percent > quiet.breakout_buffer_percent


def test_profile_is_bounded_and_builds_valid_family_configs():
    profile = build_pattern_tolerance_profile(_bars(0.5))

    assert profile.volatility_ratio == 0.12
    assert profile.endpoint_tolerance_percent == 0.10
    assert profile.breakout_buffer_percent == 0.015
    profile.classic_config().validate()
    assert profile.consolidation_config().maximum_boundary_residual_percent == 0.08
    assert profile.diamond_config().minimum_boundary_change_percent == 0.08
    assert profile.reversal_config().minimum_v_move_percent == 0.25


def test_single_bar_uses_auditable_fallback_profile():
    profile = build_pattern_tolerance_profile(_bars(0.02)[:1])

    assert profile.sample_size == 0
    assert profile.volatility_ratio == 0.02
    assert "true-range" in profile.method
