from datetime import date
from unittest.mock import Mock

from stock_harness.screener_result_tags import attach_recognition_tags


def test_tags_use_active_historical_profile_and_preserve_snapshot():
    store = Mock()
    store.get_latest_succeeded_signal_review_run.return_value = {
        "run_id": "weekly-1", "effective_date": "2026-09-07",
    }
    store.list_signal_review_items.return_value = [
        {"symbol": "A", "profile": "historical", "active": True},
        {"symbol": "B", "profile": "recent", "active": True},
        {"symbol": "C", "profile": "historical", "active": False},
    ]
    original = [{"symbol": symbol, "rank": rank} for rank, symbol in enumerate("ABC", 1)]
    result = attach_recognition_tags(store, original, "2026-09-11")
    store.get_latest_succeeded_signal_review_run.assert_called_once_with(
        "weekly-board-recognition", date(2026, 9, 11),
    )
    store.list_signal_review_items.assert_called_once_with("weekly-1")
    assert [item["recognition"]["tags"] for item in result] == [["historical"], [], []]
    assert all(item["recognition"]["available"] for item in result)
    assert result[0]["recognition"]["source_date"] == "2026-09-07"
    assert all("recognition" not in item for item in original)
    assert [item["rank"] for item in result] == [1, 2, 3]


def test_missing_source_is_unknown_not_negative():
    store = Mock()
    store.get_latest_succeeded_signal_review_run.return_value = None
    result = attach_recognition_tags(store, [{"symbol": "A"}], "2026-09-11")
    assert result[0]["recognition"] == {
        "available": False, "source_run_id": None, "source_date": None, "tags": [],
    }
    store.list_signal_review_items.assert_not_called()


def test_empty_candidates_need_no_recognition_read():
    store = Mock()
    assert attach_recognition_tags(store, [], "2026-09-11") == []
    store.get_latest_succeeded_signal_review_run.assert_not_called()
