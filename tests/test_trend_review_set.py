import json
from pathlib import Path

import pytest

from stock_harness.trend_review_set import load_trend_review_set


DATASET = Path(__file__).parents[1] / "validation" / "trend-review-set-v1.json"


def test_loads_versioned_a_share_review_candidates_without_scoring_proposals():
    review_set = load_trend_review_set(DATASET)

    assert review_set.schema_version == "1.0"
    assert review_set.dataset_version == "2026-08-18.1"
    assert len(review_set.cases) == 3
    assert review_set.scoreable_cases == ()
    assert {case.symbol for case in review_set.cases} == {
        "300308.SZ", "000792.SZ", "600519.SH",
    }
    assert all(case.expected and case.sources for case in review_set.cases)


def test_rejects_duplicate_ids_and_invalid_date_ranges(tmp_path: Path):
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    raw["cases"].append(dict(raw["cases"][0]))
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="IDs must be unique"):
        load_trend_review_set(duplicate)

    raw["cases"].pop()
    raw["cases"][0]["interval_end"] = "2025-01-01"
    invalid_range = tmp_path / "invalid-range.json"
    invalid_range.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid review interval"):
        load_trend_review_set(invalid_range)
