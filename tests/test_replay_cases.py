from stock_harness.replay import select_replay_cases


def test_case_selection_is_balanced_and_uses_only_boundary_outcomes() -> None:
    evaluations = [
        _evaluation("A", "target-first", 4, 70),
        _evaluation("B", "target-first", 2, 60),
        _evaluation("C", "invalidation-first", 1, 90),
        _evaluation("D", "neither", None, 100),
    ]

    cases = select_replay_cases(evaluations, per_outcome=1)

    assert [(item["symbol"], item["outcome"]) for item in cases] == [
        ("B", "success"), ("C", "failure"),
    ]
    assert cases[0]["returns"] == {"10": .05}


def _evaluation(symbol, event, session, score):
    return {
        "signal": {
            "system_id": "mean-reversion", "system_version": "v1",
            "symbol": symbol, "scope": "stock", "signal_date": "2026-01-01",
            "reference_close": 10, "score": score,
            "setup_family": "directional-pullback", "invalidation_price": 9,
            "selected_target_price": 12, "metadata": {},
        },
        "reference_close": {
            "first_boundary_event": event,
            "first_boundary_event_session": session,
            "horizons": {"10": {"status": "complete", "close_return": .05}},
        },
    }
