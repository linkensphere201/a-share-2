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


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda case: case["tags"].append({"invalid": True}), "invalid review tags"),
    (
        lambda case: case["sources"][0].update({"checked_on": "not-a-date"}),
        "invalid trend review date checked_on",
    ),
])
def test_rejects_malformed_tags_and_provenance_dates(
    tmp_path: Path,
    mutation,
    message: str,
):
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    mutation(raw["cases"][0])
    path = tmp_path / "malformed.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_trend_review_set(path)


def test_confirmed_case_requires_machine_scoreable_expected_labels(tmp_path: Path):
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    raw["cases"][0]["review_status"] = "confirmed"
    path = tmp_path / "unscoreable-confirmed.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="requires scoreable expected labels"):
        load_trend_review_set(path)
