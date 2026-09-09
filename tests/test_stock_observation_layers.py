from __future__ import annotations

from copy import deepcopy

from stock_harness.stock_observation_layers import assign_stock_presentation_layers


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


def _item(
    symbol: str, *, score: float, recognized: bool = False,
    classification: str = "neutral", manual: bool = False,
    opportunity: bool = False,
) -> dict[str, object]:
    eligible = classification == "independent-advance"
    return {
        "symbol": symbol, "rank": 1, "lifecycle_state": "new",
        "payload": {
            "recognized": recognized, "manual_pinned": manual,
            "independent_score": score,
            "independent_scan": {
                "classification": classification, "eligible": eligible,
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
