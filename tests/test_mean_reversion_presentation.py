from stock_harness.review_systems.mean_reversion import _risk_summary


def test_no_setup_risk_summary_explains_state_without_raw_diagnostic_codes() -> None:
    summary = _risk_summary([
        "board-execution-evidence-unavailable",
        "confirmation-quality-insufficient",
        "invalid-long-price-ordering",
        "market-permission-blocked",
        "no-mean-reversion-setup",
    ], {"setup_family": "none"})

    assert summary == (
        "当前未形成可执行的均值回归偏离；"
        "市场流动性环境暂不允许新机会；板块执行证据覆盖不足。"
    )
    assert "invalid-long-price-ordering" not in summary
