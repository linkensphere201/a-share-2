from dataclasses import replace
from datetime import date
import time

import pytest

from stock_harness.analysis_results import (
    AnalysisNamespace,
    AnalysisRunSpec,
    AnalysisRunStatus,
    GeneratedAnalysisItem,
    GeneratedAnalysisTarget,
    GeneratedItemType,
)
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _spec(
    as_of_date: date = date(2026, 8, 18),
    *,
    digest: bytes = b"input-v1",
    namespace: AnalysisNamespace = AnalysisNamespace.OFFICIAL,
) -> AnalysisRunSpec:
    return AnalysisRunSpec(
        system_id="trend",
        symbol="000001.SZ",
        timeframe="daily",
        namespace=namespace,
        as_of_date=as_of_date,
        input_start_date=date(2025, 8, 18),
        input_end_date=as_of_date,
        input_digest=digest,
        algorithm_version="trend-1",
        config_version="settings-3",
        completion_state="complete",
        source_observed_at_ms=123 if namespace is AnalysisNamespace.PREVIEW else None,
        expires_at_ms=456 if namespace is AnalysisNamespace.PREVIEW else None,
    )


@pytest.fixture
def store():
    with SQLiteMarketDataStore(":memory:") as value:
        value.upsert_instruments([
            Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ")
        ])
        yield value


def test_completed_run_persists_every_generated_item_type(store):
    started = store.begin_generated_analysis_run(_spec())
    items = [
        GeneratedAnalysisItem("a1", GeneratedItemType.ANCHOR, {"date": "2026-07-01"}),
        GeneratedAnalysisItem(
            "l1", GeneratedItemType.LINE, {"slope": 0.2}, parent_item_id="a1"
        ),
        GeneratedAnalysisItem("z1", GeneratedItemType.ZONE, {"low": 10, "high": 12}),
        GeneratedAnalysisItem("p1", GeneratedItemType.PATTERN, {"kind": "double-bottom"}),
        GeneratedAnalysisItem("t1", GeneratedItemType.TRANSITION, {"state": "breakout"}),
        GeneratedAnalysisItem("e1", GeneratedItemType.EVIDENCE, {"volume_ratio": 1.8}),
    ]

    completed = store.complete_generated_analysis_run(
        started.run_id, items, duration_ms=12.5, warnings=[{"code": "partial-period"}]
    )
    latest = store.get_latest_generated_analysis_run(
        "000001.SZ", "trend", "daily"
    )

    assert completed.status is AnalysisRunStatus.SUCCEEDED
    assert latest is not None
    assert latest["run_id"] == started.run_id
    assert latest["warnings"] == [{"code": "partial-period"}]
    assert [item["item_type"] for item in latest["items"]] == [
        "anchor", "line", "zone", "pattern", "transition", "evidence",
    ]


def test_successful_identical_run_is_reused_idempotently(store):
    first = store.begin_generated_analysis_run(_spec())
    store.complete_generated_analysis_run(first.run_id, [], duration_ms=1)

    repeated = store.begin_generated_analysis_run(_spec())

    assert repeated.run_id == first.run_id
    assert repeated.reused is True
    assert repeated.attempt == 1


def test_new_as_of_revision_supersedes_without_rewriting_prior_run(store):
    first = store.begin_generated_analysis_run(_spec(date(2026, 8, 18)))
    store.complete_generated_analysis_run(
        first.run_id,
        [GeneratedAnalysisItem("t1", GeneratedItemType.TRANSITION, {"state": "candidate"})],
        duration_ms=1,
    )
    second = store.begin_generated_analysis_run(_spec(date(2026, 8, 19), digest=b"input-v2"))
    store.complete_generated_analysis_run(
        second.run_id,
        [GeneratedAnalysisItem("t2", GeneratedItemType.TRANSITION, {"state": "failed"})],
        duration_ms=1,
    )

    latest = store.get_latest_generated_analysis_run(
        "000001.SZ", "trend", "daily"
    )

    assert second.supersedes_run_id == first.run_id
    assert latest is not None and latest["run_id"] == second.run_id
    assert latest["items"][0]["payload"]["state"] == "failed"


def test_failed_retry_is_new_attempt_and_last_success_remains_readable(store):
    successful = store.begin_generated_analysis_run(_spec(date(2026, 8, 17)))
    store.complete_generated_analysis_run(successful.run_id, [], duration_ms=1)
    failed = store.begin_generated_analysis_run(_spec())
    store.fail_generated_analysis_run(failed.run_id, "detector crashed", duration_ms=2)
    retry = store.begin_generated_analysis_run(_spec())

    latest = store.get_latest_generated_analysis_run(
        "000001.SZ", "trend", "daily"
    )

    assert retry.run_id != failed.run_id
    assert retry.attempt == 2
    assert latest is not None and latest["run_id"] == successful.run_id


def test_preview_namespace_is_isolated_and_requires_observation_time(store):
    preview = store.begin_generated_analysis_run(
        _spec(namespace=AnalysisNamespace.PREVIEW)
    )
    store.complete_generated_analysis_run(preview.run_id, [], duration_ms=1)

    assert store.get_latest_generated_analysis_run(
        "000001.SZ", "trend", "daily"
    ) is None
    assert store.get_latest_generated_analysis_run(
        "000001.SZ", "trend", "daily", AnalysisNamespace.PREVIEW
    )["run_id"] == preview.run_id

    invalid = replace(
        _spec(namespace=AnalysisNamespace.PREVIEW), source_observed_at_ms=None
    )
    with pytest.raises(ValueError, match="observation time"):
        store.begin_generated_analysis_run(invalid)


def test_official_completion_expires_same_date_preview(store):
    preview = store.begin_generated_analysis_run(
        _spec(namespace=AnalysisNamespace.PREVIEW)
    )
    store.complete_generated_analysis_run(preview.run_id, [], duration_ms=1)
    official = store.begin_generated_analysis_run(_spec())
    store.complete_generated_analysis_run(official.run_id, [], duration_ms=1)

    retained_preview = store.get_latest_generated_analysis_run(
        "000001.SZ", "trend", "daily", AnalysisNamespace.PREVIEW
    )

    assert retained_preview is not None
    assert retained_preview["expires_at_ms"] is not None
    assert retained_preview["expires_at_ms"] <= int(time.time() * 1000)


def test_retention_prunes_old_preview_and_failure_but_keeps_official_success(store):
    preview = store.begin_generated_analysis_run(
        _spec(namespace=AnalysisNamespace.PREVIEW)
    )
    store.complete_generated_analysis_run(preview.run_id, [], duration_ms=1)
    failed = store.begin_generated_analysis_run(_spec(digest=b"failed"))
    store.fail_generated_analysis_run(failed.run_id, "failed", duration_ms=1)
    official = store.begin_generated_analysis_run(_spec(digest=b"official"))
    store.complete_generated_analysis_run(official.run_id, [], duration_ms=1)

    removed = store.prune_generated_analysis_runs(
        int(time.time() * 1000) + 31 * 24 * 60 * 60 * 1000
    )

    assert removed == 2
    assert store.get_latest_generated_analysis_run(
        "000001.SZ", "trend", "daily"
    )["run_id"] == official.run_id


def test_invalid_child_order_rolls_back_completion(store):
    started = store.begin_generated_analysis_run(_spec())
    items = [
        GeneratedAnalysisItem("child", GeneratedItemType.LINE, {}, "parent"),
        GeneratedAnalysisItem("parent", GeneratedItemType.ANCHOR, {}),
    ]

    with pytest.raises(ValueError, match="parent must precede"):
        store.complete_generated_analysis_run(started.run_id, items, duration_ms=1)

    assert store.get_latest_generated_analysis_run(
        "000001.SZ", "trend", "daily"
    ) is None


def test_only_registered_enabled_target_is_queued_and_ranges_coalesce(store):
    target_id = store.upsert_generated_analysis_target(GeneratedAnalysisTarget(
        "000001.SZ", "trend", "daily", "trend-1", "settings-3"
    ))
    store.upsert_daily_bars("tushare", [
        DailyBar("000001.SZ", date(2026, 8, 18), 10, 12, 9, 11, 100),
        DailyBar("000001.SZ", date(2026, 8, 19), 11, 13, 10, 12, 110),
    ])

    claimed = store.claim_generated_analysis_targets()

    assert len(claimed) == 1
    assert claimed[0].target_id == target_id
    assert claimed[0].dirty_from == date(2026, 8, 18)
    assert claimed[0].dirty_through == date(2026, 8, 19)
    assert claimed[0].generation == 2
    assert store.complete_generated_analysis_target(target_id, claimed[0].generation)
    assert store.claim_generated_analysis_targets() == []


def test_new_write_during_claim_cannot_be_lost_by_old_completion(store):
    target_id = store.upsert_generated_analysis_target(GeneratedAnalysisTarget(
        "000001.SZ", "trend", "daily", "trend-1", "settings-3"
    ))
    assert store.queue_generated_analysis_target(
        "000001.SZ", "trend", "daily",
        date(2026, 8, 18), date(2026, 8, 18), "explicit-rebuild",
    )
    first = store.claim_generated_analysis_targets()[0]
    assert store.queue_generated_analysis_target(
        "000001.SZ", "trend", "daily",
        date(2026, 8, 19), date(2026, 8, 19), "new-final-bar",
    )

    assert not store.complete_generated_analysis_target(target_id, first.generation)
    second = store.claim_generated_analysis_targets()[0]
    assert second.generation == first.generation + 1
    assert second.dirty_from == date(2026, 8, 18)
    assert second.dirty_through == date(2026, 8, 19)


def test_disabled_target_does_not_queue_and_clears_existing_work(store):
    enabled = GeneratedAnalysisTarget(
        "000001.SZ", "trend", "daily", "trend-1", "settings-3"
    )
    store.upsert_generated_analysis_target(enabled)
    store.queue_generated_analysis_target(
        "000001.SZ", "trend", "daily",
        date(2026, 8, 18), date(2026, 8, 18), "explicit-rebuild",
    )
    store.upsert_generated_analysis_target(GeneratedAnalysisTarget(
        "000001.SZ", "trend", "daily", "trend-1", "settings-3", False
    ))
    store.upsert_daily_bars("tushare", [
        DailyBar("000001.SZ", date(2026, 8, 18), 10, 12, 9, 11, 100)
    ])

    assert store.claim_generated_analysis_targets() == []


def test_latest_success_reports_dirty_upgrade_and_failed_run_as_stale(store):
    store.upsert_generated_analysis_target(GeneratedAnalysisTarget(
        "000001.SZ", "trend", "daily", "trend-1", "settings-3"
    ))
    successful = store.begin_generated_analysis_run(_spec(date(2026, 8, 17)))
    store.complete_generated_analysis_run(successful.run_id, [], duration_ms=1)
    store.upsert_generated_analysis_target(GeneratedAnalysisTarget(
        "000001.SZ", "trend", "daily", "trend-2", "settings-4"
    ))
    store.queue_generated_analysis_target(
        "000001.SZ", "trend", "daily",
        date(2026, 8, 18), date(2026, 8, 18), "algorithm-upgrade",
    )
    failed = store.begin_generated_analysis_run(_spec())
    store.fail_generated_analysis_run(failed.run_id, "detector crashed", duration_ms=1)

    latest = store.get_latest_generated_analysis_run(
        "000001.SZ", "trend", "daily"
    )

    assert latest is not None and latest["stale"] is True
    assert latest["stale_reasons"] == [
        "canonical-input-dirty",
        "algorithm-or-config-upgraded",
        "newer-run-incomplete-or-failed",
    ]
