from datetime import date

from stock_harness.hotspot_wave import project_hotspot_waves
from stock_harness.models import Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def test_hotspot_wave_advances_diverges_and_reaccelerates() -> None:
    first_scores = [_score("leader-ignited", 48)]
    first = project_hotspot_waves(first_scores, [], date(2026, 9, 1))
    assert first[0]["stage"] == "ignition"
    assert first[0]["transition"] == "started"

    confirmed_scores = [_score("hotspot-confirmed", 72)]
    confirmed = project_hotspot_waves(
        confirmed_scores, first, date(2026, 9, 2), {"theme.seed": 1},
    )
    assert confirmed[0]["stage"] == "confirmed"
    assert confirmed[0]["confirmed_on"] == "2026-09-02"

    divided = project_hotspot_waves(
        [_score("diverging", 61)], confirmed, date(2026, 9, 3),
    )
    assert divided[0]["stage"] == "diverging"
    recovered_scores = [_score("accelerating", 80)]
    recovered = project_hotspot_waves(
        recovered_scores, divided, date(2026, 9, 4),
    )
    assert recovered[0]["stage"] == "reaccelerating"
    assert recovered[0]["wave_id"] == first[0]["wave_id"]
    assert recovered_scores[0]["hotspot_wave_stage"] == "reaccelerating"


def test_hotspot_wave_ends_after_two_exhausted_sessions_and_restarts() -> None:
    assert project_hotspot_waves(
        [_score("exhausted", 30)], [], date(2026, 8, 29),
    ) == []
    first = project_hotspot_waves(
        [_score("trend-emerging", 60)], [], date(2026, 9, 1),
    )
    weak = project_hotspot_waves(
        [_score("exhausted", 34)], first, date(2026, 9, 2),
    )
    ended = project_hotspot_waves([], weak, date(2026, 9, 3))
    assert ended[0]["status"] == "ended"
    assert ended[0]["stage"] == "ended"

    restarted = project_hotspot_waves(
        [_score("leader-ignited", 46)], [], date(2026, 9, 4),
        {"theme.seed": 1},
    )
    assert restarted[0]["wave_sequence"] == 2
    assert restarted[0]["wave_id"] != first[0]["wave_id"]


def test_hotspot_wave_does_not_start_before_theme_wins_a_visible_seat() -> None:
    hidden = _score("trend-emerging", 64, visible=False)
    assert project_hotspot_waves([hidden], [], date(2026, 9, 1)) == []


def test_hotspot_wave_ends_after_three_sessions_outside_visible_seats() -> None:
    first = project_hotspot_waves(
        [_score("hotspot-confirmed", 72)], [], date(2026, 9, 1),
    )
    hidden_score = _score("hotspot-confirmed", 70, visible=False)
    first_hidden = project_hotspot_waves(
        [hidden_score], first, date(2026, 9, 2),
    )
    second_hidden = project_hotspot_waves(
        [hidden_score], first_hidden, date(2026, 9, 3),
    )
    ended = project_hotspot_waves(
        [hidden_score], second_hidden, date(2026, 9, 4),
    )
    assert first_hidden[0]["stage"] == "diverging"
    assert second_hidden[0]["invisible_session_count"] == 2
    assert ended[0]["status"] == "ended"
    assert ended[0]["transition"] == "visibility-ended"


def test_hotspot_wave_collapses_provider_aliases_to_one_theme() -> None:
    scores = [
        _score("trend-emerging", 58, symbol="BK001.DC", visible=False),
        _score("hotspot-confirmed", 70, symbol="BK002.DC", visible=True),
    ]
    snapshots = project_hotspot_waves(scores, [], date(2026, 9, 1))
    assert len(snapshots) == 1
    assert snapshots[0]["representative_symbol"] == "BK002.DC"
    assert scores[0]["hotspot_wave_id"] == scores[1]["hotspot_wave_id"]
    assert scores[0]["hotspot_wave_representative"] is False
    assert scores[1]["hotspot_wave_representative"] is True


def test_hotspot_wave_snapshots_are_persisted_with_signal_run() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("BK001.DC", "种业", InstrumentKind.SECTOR, "DC"),
    ])
    run = store.create_signal_review_run(
        signal_id="daily-market-board-review", definition_version="v1",
        algorithm_version="v1", cadence="daily",
        effective_date=date(2026, 9, 1), parameters={},
    )
    snapshot = project_hotspot_waves(
        [_score("leader-ignited", 48)], [], date(2026, 9, 1),
    )[0]
    store.complete_signal_review_run(
        str(run["run_id"]), items=[], summary={}, input_digest="digest",
        hotspot_wave_snapshots=[snapshot],
    )
    stored = store.list_hotspot_wave_snapshots(str(run["run_id"]))
    assert stored[0]["wave_id"] == snapshot["wave_id"]
    assert store.list_hotspot_wave_history(str(snapshot["wave_id"])) == stored
    assert stored[0]["run_id"] == run["run_id"]
    store.close()


def _score(
    stage: str, score: float, *, symbol: str = "BK001.DC", visible: bool = True,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "canonical_theme": "theme.seed",
        "theme_name": "种业",
        "theme_parent_id": "theme.agriculture",
        "theme_parent_name": "农业",
        "theme_registry_version": "v2",
        "theme_signal_eligible": True,
        "hotspot_stage": stage,
        "total_score": score,
        "visibility_score": score,
        "radar_visible": visible,
    }
