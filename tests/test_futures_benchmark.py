from stock_harness.benchmarks.futures_daily import run


def test_futures_benchmark_covers_increment_build_and_read_paths() -> None:
    result = run(years=1, trading_days_per_contract=3, samples=2)

    assert result["contracts"] == 12
    assert result["canonical_rows"] == 37
    assert result["continuous_rows"] == 37
    assert result["continuous_full_build_rows"] == 37
    assert result["continuous_suffix_build_rows"] == 1
    assert result["no_change_increment_ms"]["p95"] >= 0
    assert result["provisional_refresh_and_fused_read_ms"]["p95"] >= 0
    assert result["warm_history_read_ms"]["p95"] >= 0
