from datetime import date

from stock_harness.models import Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def test_score_history_survives_run_algorithm_upgrade_without_same_day_duplication() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("BK001.DC", "Board", InstrumentKind.SECTOR, "DC"),
    ])
    first = _complete_scored_run(store, date(2026, 9, 10), 1, "run-v1")
    same_day_revision = _complete_scored_run(
        store, date(2026, 9, 10), 2, "run-v2",
    )
    older_algorithm = _complete_scored_run(
        store, date(2026, 9, 11), 1, "run-v1", parameters={"new-option": True},
    )
    current = store.create_signal_review_run(
        signal_id="daily-market-board-review",
        definition_version="definition-v1",
        algorithm_version="run-v3",
        cadence="daily", effective_date=date(2026, 9, 14), parameters={},
    )

    selected = store.list_compatible_prior_score_runs(
        str(current["run_id"]), "board-hotspot-emergence", "hotspot-v1",
    )

    assert [item["run_id"] for item in selected] == [
        older_algorithm["run_id"], same_day_revision["run_id"],
    ]
    assert first["run_id"] not in {item["run_id"] for item in selected}

    same_day_current = _complete_scored_run(
        store, date(2026, 9, 14), 2, "run-v2",
    )
    rerun = store.create_signal_review_run(
        signal_id="daily-market-board-review",
        definition_version="definition-v1",
        algorithm_version="run-v3",
        cadence="daily", effective_date=date(2026, 9, 14), parameters={},
    )
    selected = store.list_compatible_prior_score_runs(
        str(rerun["run_id"]), "board-hotspot-emergence", "hotspot-v1",
    )
    assert same_day_current["run_id"] not in {item["run_id"] for item in selected}
    store.close()


def _complete_scored_run(
    store: SQLiteMarketDataStore, effective_date: date, expected_revision: int,
    algorithm_version: str, *, parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    run = store.create_signal_review_run(
        signal_id="daily-market-board-review",
        definition_version="definition-v1",
        algorithm_version=algorithm_version,
        cadence="daily", effective_date=effective_date,
        parameters=parameters or {},
    )
    assert run["revision"] == expected_revision
    store.complete_signal_review_run(
        str(run["run_id"]), items=[], summary={}, input_digest="digest",
        scores=[{
            "symbol": "BK001.DC", "entity_key": "BK001.DC",
            "system_id": "board-hotspot-emergence",
            "scorer_version": "hotspot-v1", "entity_scope": "board",
            "eligible": False, "total_score": 50.0, "grade": "C",
            "rank": 1, "participant_count": 1,
            "ranking_universe_digest": "universe", "verdict": "watch",
            "summary": "summary", "risk_summary": "risk",
            "change_summary": "change",
        }],
    )
    return run
