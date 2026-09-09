from __future__ import annotations

from copy import deepcopy

from stock_harness.stock_observation_layers import (
    assign_stock_presentation_layers,
    select_stock_m4_candidates,
)


def test_presentation_layers_bound_focus_and_risk_without_losing_archive() -> None:
    items = [
        _item(f"F{index:03}", score=90 - index / 10, recognized=True)
        for index in range(130)
    ]
    items.extend(
        _item(f"R{index:03}", score=80 - index / 10, classification="independent-decline")
        for index in range(120)
    )
    items.extend([
        _item("M001", score=1, manual=True),
        _item("M002", score=2, manual=True),
        _item("O001", score=95, opportunity=True),
        _item("O002", score=94, opportunity=True),
        _item("A001", score=99),
    ])
    snapshot = {"summary": {}, "items": items}

    result = assign_stock_presentation_layers(snapshot)

    assert result["counts"] == {
        "opportunity": 2, "focus": 102, "risk": 100, "archive": 51,
    }
    assert len(snapshot["items"]) == 255
    assert _payload(items, "M001")["presentation_bucket"] == "focus"
    assert _payload(items, "O001")["presentation_bucket"] == "opportunity"
    assert _payload(items, "R119")["presentation_bucket"] == "archive"
    assert result["manual_focus_count"] == 2


def test_presentation_ranking_is_deterministic_and_strict_opportunity_is_not_inferred() -> None:
    snapshot = {"summary": {}, "items": [
        _item("B", score=70, recognized=True),
        _item("A", score=70, recognized=True),
        _item("WAIT", score=99, recognized=True, classification="independent-advance"),
        _item("RISK", score=100, recognized=True, classification="one-session-event-anomaly"),
    ]}
    replay = deepcopy(snapshot)

    assign_stock_presentation_layers(snapshot, focus_limit=2, risk_limit=1)
    assign_stock_presentation_layers(replay, focus_limit=2, risk_limit=1)

    left = [item["payload"] for item in snapshot["items"]]
    right = [item["payload"] for item in replay["items"]]
    assert left == right
    assert _payload(snapshot["items"], "RISK")["presentation_bucket"] == "risk"
    assert _payload(snapshot["items"], "WAIT")["presentation_bucket"] == "focus"
    assert not any(
        item["payload"]["presentation_bucket"] == "opportunity"
        for item in snapshot["items"]
    )


def test_focus_and_m4_allocation_preserve_discovery_lanes_and_separate_risk_names() -> None:
    items = [
        _item(f"B{index:02}", score=90 - index, phase="breakout")
        for index in range(15)
    ] + [
        _item(f"C{index:02}", score=80 - index, phase="critical")
        for index in range(15)
    ] + [
        _item("ST01", score=99, phase="breakout", risk_name=True)
    ]
    snapshot = {"summary": {}, "items": items}

    assign_stock_presentation_layers(snapshot, focus_limit=20, risk_limit=5)
    selected = select_stock_m4_candidates(items, 12)

    focus = [item for item in items if item["payload"]["presentation_bucket"] == "focus"]
    assert {item["payload"]["presentation_lane"] for item in focus} == {
        "breakout", "critical",
    }
    assert items[-1]["payload"]["presentation_bucket"] == "risk"
    assert len(selected) == 12
    assert all(item["payload"]["presentation_bucket"] == "focus" for item in selected)
    assert sum(item["payload"]["presentation_lane"] == "breakout" for item in selected) >= 6
    assert sum(item["payload"]["presentation_lane"] == "critical" for item in selected) == 5
    assert all(item["symbol"] != "ST01" for item in selected)


def test_risk_overflow_is_archived_instead_of_spilling_into_focus() -> None:
    items = [
        _item(f"ST{index:02}", score=100 - index, phase="breakout", risk_name=True)
        for index in range(3)
    ] + [
        _item("CLEAN", score=50, phase="critical")
    ]
    snapshot = {"summary": {}, "items": items}

    assign_stock_presentation_layers(snapshot, focus_limit=2, risk_limit=1)

    assert _payload(items, "ST00")["presentation_bucket"] == "risk"
    assert _payload(items, "ST01")["presentation_bucket"] == "archive"
    assert _payload(items, "ST02")["presentation_bucket"] == "archive"
    assert _payload(items, "CLEAN")["presentation_bucket"] == "focus"


def test_focus_retains_at_most_seventy_prior_qualified_names() -> None:
    retained = [
        _item(f"OLD{index:02}", score=50 - index / 10, phase="breakout")
        for index in range(80)
    ]
    for item in retained:
        item["payload"]["previous_presentation_bucket"] = "focus"
    entrants = [
        _item(f"NEW{index:02}", score=100 - index / 10, phase="breakout")
        for index in range(80)
    ]
    snapshot = {"summary": {}, "items": [*retained, *entrants]}

    assign_stock_presentation_layers(snapshot, focus_limit=100, risk_limit=1)

    focus_symbols = {
        item["symbol"] for item in snapshot["items"]
        if item["payload"]["presentation_bucket"] == "focus"
    }
    assert len(focus_symbols) == 100
    assert sum(symbol.startswith("OLD") for symbol in focus_symbols) == 70
    assert sum(symbol.startswith("NEW") for symbol in focus_symbols) == 30


def _item(
    symbol: str, *, score: float, recognized: bool = False,
    classification: str = "neutral", manual: bool = False,
    opportunity: bool = False,
    phase: str | None = None, risk_name: bool = False,
) -> dict[str, object]:
    eligible = classification == "independent-advance"
    return {
        "symbol": symbol, "rank": 1, "lifecycle_state": "new",
        "payload": {
            "recognized": recognized, "manual_pinned": manual,
            "risk_name": risk_name,
            "independent_score": score,
            "independent_scan": {
                "classification": classification, "eligible": eligible,
                "metrics": {
                    "opportunity_phase": phase,
                    "opportunity_readiness_score": 80 if phase else 0,
                },
            },
            "member_scan": None, "screener_results": [],
            "m4_analysis": None,
            "opportunity_score": {
                "eligible": opportunity, "total_score": score,
            },
        },
        "sources": [],
    }


def _payload(items: list[dict[str, object]], symbol: str) -> dict[str, object]:
    return next(item["payload"] for item in items if item["symbol"] == symbol)  # type: ignore[return-value]
