from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.volume_accumulation import detect_volume_accumulation


def _bars(*, recent_volumes: list[int], recent_closes: list[float]) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    volumes = [100] * 20 + recent_volumes
    closes = [10.0] * 20 + recent_closes
    return [
        AnalysisBar(
            period_start=start + timedelta(days=index),
            period_end=start + timedelta(days=index),
            open=close,
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            volume=volume,
            sources=("test",),
            contains_provisional=False,
            period_complete=True,
            observed_at_ms=0,
        )
        for index, (volume, close) in enumerate(zip(volumes, closes))
    ]


def test_detects_distributed_volume_pile_with_compressed_price() -> None:
    bars = _bars(
        recent_volumes=[145, 160, 140, 155] * 5,
        recent_closes=[10.0, 10.1, 10.0, 10.08] * 5,
    )

    signal = detect_volume_accumulation(bars)

    assert signal is not None
    assert signal.evidence["elevated_sessions"] == 20
    assert signal.evidence["supported_blocks"] == 4
    assert signal.evidence["limit_up_count"] == 0


def test_rejects_single_day_volume_spike() -> None:
    bars = _bars(
        recent_volumes=[100] * 19 + [1200],
        recent_closes=[10.0] * 20,
    )

    assert detect_volume_accumulation(bars) is None


def test_detects_recent_local_volume_cluster_without_full_window_expansion() -> None:
    bars = _bars(
        recent_volumes=[80] * 10 + [80, 80, 240, 190, 150, 85, 80, 75, 80, 75],
        recent_closes=[10.0, 10.05, 9.95, 10.0, 10.05] * 4,
    )

    signal = detect_volume_accumulation(bars)

    assert signal is not None
    assert signal.evidence["pile_mode"] == "clustered"
    assert signal.evidence["cluster_sessions"] >= 2


def test_rejects_recent_limit_up_even_when_other_features_pass() -> None:
    bars = _bars(
        recent_volumes=[150] * 20,
        recent_closes=[10.0] * 20,
    )

    assert detect_volume_accumulation(
        bars, limit_up_dates=frozenset({bars[-3].period_end.isoformat()}),
    ) is None


def test_rejects_large_directional_move_instead_of_small_rises_and_falls() -> None:
    bars = _bars(
        recent_volumes=[150] * 20,
        recent_closes=[10 + index * 0.08 for index in range(20)],
    )

    assert detect_volume_accumulation(bars) is None
