"""Blinded exports must not leak detector labels or future price data."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("shape_review", Path(__file__).parents[1] / "scripts/build_low_base_shape_review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def snapshot():
    fixture = json.loads((Path(__file__).parent / "fixtures/low_base_001258_20260226.json").read_text(encoding="utf-8"))
    rows = [dict(zip(("trade_date", "open", "high", "low", "close", "volume"), row), bar_state="final")
            for row in fixture["bars"]]
    return {"instruments": [{"symbol": "SECRET", "bars": rows, "bars_sha256": review.digest(rows)}]}


def test_future_changes_cannot_change_blind_packet_selection():
    source = snapshot()
    changed = deepcopy(source)
    for bar in changed["instruments"][0]["bars"]:
        if bar["trade_date"] > "2026-02-26":
            bar.update(open=100, high=1000, low=1, close=2, volume=10**10)
    changed["instruments"][0]["bars_sha256"] = review.digest(changed["instruments"][0]["bars"])
    first = review.sample_cases(source, "2026-02-01", "2026-02-26")
    assert first == review.sample_cases(changed, "2026-02-01", "2026-02-26")
    assert first[0]
    for case in first[0]:
        assert len(case["bars"]) == 90
        assert max(b["trade_date"] for b in case["bars"]) == case["as_of_date"]


def test_blind_html_excludes_identity_dates_and_algorithm(tmp_path):
    output = tmp_path / "packet"
    manifest = review.write_packet(snapshot(), output, "2026-02-01", "2026-02-26")
    html = (output / "review.html").read_text(encoding="utf-8")
    assert manifest["sample_count"] > 0
    assert html.count('<svg ') == manifest["sample_count"]
    for secret in ("SECRET", "2026-02", "screen_eligible", "pullback-confirmed", "platform_shape"):
        assert secret not in html
    labels = (output / "labels.csv").read_text(encoding="utf-8")
    assert "match" not in labels
    with pytest.raises(FileExistsError):
        review.write_packet(snapshot(), output, "2026-02-01", "2026-02-26")


def test_altered_snapshot_rejected():
    source = snapshot()
    source["instruments"][0]["bars"][0]["volume"] += 1
    with pytest.raises(ValueError, match="checksum"):
        review.sample_cases(source, "2026-02-01", "2026-02-26")


def test_sampling_order_deterministic_and_group_caps():
    source = snapshot()
    for index in range(5):
        instrument = deepcopy(source["instruments"][0])
        instrument["symbol"] = f"ANON{index}"
        source["instruments"].append(instrument)
    cases, counts = review.sample_cases(source, "2026-02-01", "2026-02-26", 2)
    source["instruments"].reverse()
    assert (cases, counts) == review.sample_cases(source, "2026-02-01", "2026-02-26", 2)
    for group in counts:
        selected = [c for c in cases if c["group"] == group]
        assert len(selected) <= 2
        assert len({c["symbol"] for c in selected}) == len(selected)
