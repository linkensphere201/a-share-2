from stock_harness.mean_reversion_context import (
    board_execution_context,
    build_market_permission,
    normalize_relative_strength,
)


def test_market_permission_uses_same_date_candidate_pressure_without_future_label() -> None:
    entities = [{
        "symbol": "000001.SH",
        "mean_reversion": {
            "parent_trend": "up", "structural_break": False,
            "regime": {"return20": .03, "persistent_one_way_decline": False},
        },
    }]

    permission = build_market_permission(
        entities, candidate_count=25, entity_count=5_000,
        liquidity={"capacity_tier": "high", "direction": "neutral"},
    )

    assert permission["status"] == "blocked"
    assert permission["crowded"] is True
    assert permission["causal"] is True
    assert "future" not in permission


def test_market_permission_keeps_absolute_liquidity_context_auditable() -> None:
    permission = build_market_permission(
        [{
            "symbol": "000001.SH",
            "mean_reversion": {
                "parent_trend": "up", "structural_break": False,
                "regime": {"return20": .02, "persistent_one_way_decline": False},
            },
        }],
        candidate_count=2, entity_count=5_000,
        liquidity={
            "capacity_tier": "high", "direction": "expanding",
            "regime": "high-expanding", "source": "all-stock-close-volume",
        },
    )

    assert permission["status"] == "allowed"
    assert permission["liquidity"]["source"] == "all-stock-close-volume"


def test_low_absolute_capacity_and_moderate_pressure_block_new_entries() -> None:
    permission = build_market_permission(
        [{
            "symbol": "000001.SH",
            "mean_reversion": {
                "parent_trend": "up", "structural_break": False,
                "regime": {"return20": .01, "persistent_one_way_decline": False},
            },
        }],
        candidate_count=9, entity_count=5_000,
        liquidity={"capacity_tier": "low", "direction": "neutral"},
    )

    assert permission["status"] == "blocked"
    assert permission["capacity_pressure_risk"] is True


def test_relative_strength_requires_short_window_to_stop_deteriorating() -> None:
    recovering = normalize_relative_strength({
        "mean_reversion": {},
        "relative_strength": {"market_excess": {"5": -.01, "20": -.04}},
    })
    weakening = normalize_relative_strength({
        "mean_reversion": {},
        "relative_strength": {"market_excess": {"5": -.05, "20": -.01}},
    })

    assert recovering["passed"] is True
    assert weakening["passed"] is False


def test_board_execution_requires_breadth_diffusion_and_capacity_coverage() -> None:
    context = board_execution_context(
        {"board_breadth": {
            "member_count": 10, "covered_member_count": 9, "breadth": .2,
        }},
        {"coverage_ratio": .8},
        {"positive_return_5_ratio": .6},
    )

    assert context["available"] is True
    assert context["passed"] is True
