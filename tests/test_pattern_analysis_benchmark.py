from stock_harness.benchmarks.pattern_analysis import run


def test_pattern_analysis_benchmark_reports_bounded_profiles() -> None:
    result = run(scan_symbols=5, scan_days=120, full_days=120, full_runs=1)

    assert result["scan"]["symbols"] == 5
    assert result["scan"]["per_symbol_ms"] >= 0
    assert result["full"]["runs"] == 1
    assert result["full"]["peak_mib"] >= 0
