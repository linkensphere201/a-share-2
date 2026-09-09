from datetime import date, timedelta

from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.models import BoardMembership, Instrument, InstrumentKind
from stock_harness.signal_review import (
    DAILY_MARKET_BOARD_SIGNAL, WEEKLY_RECOGNITION_SIGNAL, SignalReviewService,
    _apply_transition_attention,
    _aggregate_assignments, _assign_evidence_aliases,
    _compare_items, _order_daily_deep_candidates, _result_digest,
    _market_style_divergence, _score_history,
    _build_board_pool_snapshot, _stock_opportunity_classification,
)
from stock_harness.daily_signal_analysis import (
    _price_space, analyze_daily_series, build_board_analysis_record,
    render_board_summary,
)
from stock_harness.models import DailyBar, StockDailyLimit
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.chat_context import build_signal_chat_context
from stock_harness.review_scoring import (
    RECOGNITION_SCORER, default_scorer_registry, score_entities,
)


def test_signal_review_snapshots_preserve_revisions_and_diffs() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Alpha", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "Beta", InstrumentKind.STOCK, "SZ"),
    ])
    first = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={},
    )
    store.complete_signal_review_run(
        str(first["run_id"]), items=[_item("000001.SZ", "added")],
        summary={"note": "first"}, input_digest="digest-1",
    )
    second = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={},
    )
    assert second["revision"] == 2
    assert second["prior_run_id"] == first["run_id"]
    store.complete_signal_review_run(
        str(second["run_id"]),
        items=[_item("000001.SZ", "retained"), _item("000002.SZ", "added", rank=2)],
        summary={"note": "second"}, input_digest="digest-2",
    )

    stored = store.get_signal_review_run(str(second["run_id"]))
    assert stored is not None
    assert stored["item_count"] == 2
    assert stored["added_count"] == 1
    assert stored["retained_count"] == 1
    items = store.list_signal_review_items(str(second["run_id"]))
    assert [item["symbol"] for item in items] == ["000001.SZ", "000002.SZ"]
    assert items[0]["evidence"][0]["alias"] == "S1"
    store.close()


def test_signal_review_only_compares_compatible_runs() -> None:
    store = SQLiteMarketDataStore(":memory:")
    compatible = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 1), parameters={"window": 10},
    )
    store.complete_signal_review_run(
        str(compatible["run_id"]), items=[], summary={}, input_digest="first",
    )
    incompatible = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v2",
        cadence="weekly", effective_date=date(2026, 9, 2), parameters={"window": 10},
    )
    assert incompatible["prior_run_id"] is None
    store.fail_signal_review_run(str(incompatible["run_id"]), "test")
    changed_parameters = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 3), parameters={"window": 20},
    )
    assert changed_parameters["prior_run_id"] is None
    store.fail_signal_review_run(str(changed_parameters["run_id"]), "test")
    next_compatible = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={"window": 10},
    )
    assert next_compatible["prior_run_id"] == compatible["run_id"]
    store.complete_signal_review_run(
        str(next_compatible["run_id"]), items=[], summary={}, input_digest="future",
    )
    historical_replay = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 2),
        parameters={"window": 10},
    )
    assert historical_replay["prior_run_id"] == compatible["run_id"]
    store.fail_signal_review_run(str(historical_replay["run_id"]), "test")
    store.close()


def test_score_history_rejects_an_incompatible_scorer_version() -> None:
    class StoreFixture:
        def list_signal_review_scores(self, _run_id, system_id=None):
            assert system_id == "trend-breakout"
            return [
                {"entity_key": "BK001.DC", "symbol": "BK001.DC",
                 "scorer_version": "trend-breakout-score-v0"},
                {"entity_key": "BK002.DC", "symbol": "BK002.DC",
                 "scorer_version": "trend-breakout-score-v1"},
            ]

    prior, recent = _score_history(
        StoreFixture(), [{"run_id": "prior"}],
        "trend-breakout", "trend-breakout-score-v1",
    )

    assert set(prior) == {"BK002.DC"}
    assert len(recent["BK002.DC"]) == 1


def test_latest_succeeded_signal_run_uses_effective_date_not_creation_order() -> None:
    store = SQLiteMarketDataStore(":memory:")
    newer = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL, definition_version="v1",
        algorithm_version="v1", cadence="weekly",
        effective_date=date(2026, 9, 8), parameters={},
    )
    store.complete_signal_review_run(
        str(newer["run_id"]), items=[], summary={}, input_digest="newer",
    )
    replay = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL, definition_version="v1",
        algorithm_version="v1", cadence="weekly",
        effective_date=date(2026, 9, 1), parameters={},
    )
    store.complete_signal_review_run(
        str(replay["run_id"]), items=[], summary={}, input_digest="replay",
    )

    selected = store.get_latest_succeeded_signal_review_run(
        WEEKLY_RECOGNITION_SIGNAL, date(2026, 9, 9),
    )
    assert selected is not None
    assert selected["run_id"] == newer["run_id"]
    store.close()


def test_removed_signal_evidence_is_realiased_after_current_items() -> None:
    current = [_item("000001.SZ", "added")]
    previous_item = _item("000002.SZ", "added")
    result = _compare_items(current, {str(previous_item["item_key"]): previous_item})
    _assign_evidence_aliases(result)
    assert [evidence["alias"] for item in result for evidence in item["evidence"]] == [
        "S1", "S2",
    ]


def test_signal_result_digest_covers_payload_and_evidence() -> None:
    first = [_item("000001.SZ", "added")]
    second = [_item("000001.SZ", "added")]
    second[0]["evidence"][0]["payload"] = {"board_name": "Changed"}
    assert _result_digest(first) != _result_digest(second)


def test_signal_review_rejects_duplicate_evidence_aliases() -> None:
    import pytest

    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Alpha", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "Beta", InstrumentKind.STOCK, "SZ"),
    ])
    run = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={},
    )
    with pytest.raises(ValueError, match="aliases"):
        duplicate = _item("000002.SZ", "added", 2)
        duplicate["evidence"][0]["alias"] = "S1"
        store.complete_signal_review_run(
            str(run["run_id"]),
            items=[_item("000001.SZ", "added"), duplicate],
            summary={}, input_digest="duplicate",
        )
    store.close()


def test_signal_definition_api_is_manual_and_versioned() -> None:
    store = SQLiteMarketDataStore(":memory:")
    with TestClient(create_app(store=store)) as client:
        response = client.get("/api/signals/definitions")
        assert response.status_code == 200
        definition = response.json()["items"][0]
        assert definition["signal_id"] == WEEKLY_RECOGNITION_SIGNAL
        assert definition["manual_only"] is True
        assert definition["cadence"] == "weekly"
        daily = next(item for item in response.json()["items"]
                     if item["signal_id"] == DAILY_MARKET_BOARD_SIGNAL)
        assert daily["manual_only"] is True
        assert daily["cadence"] == "daily"
    store.close()


def test_signal_chat_is_run_bound_and_snapshots_only_selected_items() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Alpha", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "Beta", InstrumentKind.STOCK, "SZ"),
    ])
    run = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={"top": 2},
    )
    source_items = [_item("000001.SZ", "added"), _item("000002.SZ", "retained", 2)]
    frozen_scores = score_entities(
        default_scorer_registry().get(RECOGNITION_SCORER), source_items,
    )
    store.complete_signal_review_run(
        str(run["run_id"]),
        items=source_items, scores=frozen_scores,
        summary={"note": "frozen"}, input_digest="signal-digest",
    )
    conversation = store.get_or_create_signal_chat_conversation(run_id=str(run["run_id"]))
    context = build_signal_chat_context(
        store, run_id=str(run["run_id"]), selected_item_ids=["item-000002.SZ"],
    )
    turn = store.create_chat_turn(
        conversation_id=str(conversation["conversation_id"]), content="复核本期新增",
        template_id="signal-entry-exit", template_version="v1", context=context,
    )

    assert conversation["context_kind"] == "signal_run"
    assert conversation["context_id"] == run["run_id"]
    assert [item["symbol"] for item in context["selected_items"]] == ["000002.SZ"]
    assert context["signal"]["summary"] == {"note": "frozen"}
    assert context["schema_version"] == "signal-chat-context-v2"
    assert len(context["selected_scores"]) == 1
    assert context["selected_scores"][0]["symbol"] == "000002.SZ"
    assert context["selected_scores"][0]["system_id"] == RECOGNITION_SCORER
    persisted = store.get_chat_turn_context(str(turn["turn_id"]))
    assert persisted is not None
    assert persisted["context_kind"] == "signal_run"
    assert persisted["evidence"][0]["code"] == "S2"
    assert store.get_signal_review_run(str(run["run_id"]))["summary"] == {"note": "frozen"}
    store.close()


def test_signal_chat_rejects_foreign_or_excessive_selected_items() -> None:
    store = SQLiteMarketDataStore(":memory:")
    run = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={},
    )
    store.complete_signal_review_run(
        str(run["run_id"]), items=[], summary={}, input_digest="empty",
    )

    import pytest
    with pytest.raises(ValueError, match="do not belong"):
        build_signal_chat_context(
            store, run_id=str(run["run_id"]), selected_item_ids=["foreign-item"],
        )
    with pytest.raises(ValueError, match="at most 20"):
        build_signal_chat_context(
            store, run_id=str(run["run_id"]),
            selected_item_ids=[f"item-{index}" for index in range(21)],
        )
    store.close()


def test_weekly_recognition_keeps_recent_rank_one_and_strict_historical_high_weight() -> None:
    assignments = [
        _assignment("recent", "000001.SZ", "CPO", 1, .75, .55),
        _assignment("recent", "000002.SZ", "CPO", 2, .90, .80),
        _assignment("historical", "000001.SZ", "CPO", 1, .84, .66),
        _assignment("historical", "000001.SZ", "算力", 2, .82, .62),
        _assignment("historical", "000002.SZ", "CPO", 1, .95, .80),
    ]

    result = _aggregate_assignments(assignments)

    assert {item["item_key"] for item in result} == {
        "recent:000001.SZ", "historical:000001.SZ",
    }
    historical = next(item for item in result if item["profile"] == "historical")
    assert historical["payload"]["board_count"] == 2
    aliases = [
        evidence["alias"] for item in result for evidence in item["evidence"]
    ]
    assert aliases == [f"S{index}" for index in range(1, len(aliases) + 1)]


def test_daily_observation_is_causal_and_template_rendered_without_ai() -> None:
    bars = _daily_bars("BK001.DC", 140, falling=True)
    cutoff = bars[129].trade_date
    first = analyze_daily_series("BK001.DC", bars[:130], cutoff)
    changed_future = [*bars[:130], DailyBar(
        "BK001.DC", bars[130].trade_date, 100, 110, 90, 105, 99_000_000,
    )]
    replay = analyze_daily_series("BK001.DC", changed_future, cutoff)

    assert first["input_digest"] == replay["input_digest"]
    assert first["metrics"] == replay["metrics"]
    code, rendered = render_board_summary(first, None)
    assert code
    assert all(label in rendered for label in (
        "结论", "形态", "价", "量", "上涨目标位", "下跌目标位",
        "盈亏比", "近期对比", "确认/失效",
    ))


def test_daily_price_space_calculates_only_reproducible_long_risk_reward() -> None:
    bars = _daily_bars("BK001.DC", 140, falling=True)
    observation = analyze_daily_series("BK001.DC", bars, bars[-1].trade_date)
    metrics = observation["metrics"]
    metrics["price_space"] = _price_space(
        bars, ["bullish-boundary-triggered"],
        {"3m": {"state": "broken", "boundary": 82.5}}, 1.0,
    )

    _, rendered = render_board_summary(observation, None)

    price_space = metrics["price_space"]
    assert price_space["method"] == "structural-scenario-engine"
    assert price_space["profile"] == "coarse"
    assert price_space["scenario_item_id"] == "structural-trade-scenario-1"
    assert price_space["entry_price"] > 82.5
    assert price_space["invalidation_price"] < 82.5
    assert price_space["risk_reward_ratio"] > 1.5
    assert price_space["has_trade_space"] is True
    assert "上涨目标位" in rendered
    assert "下跌目标位" in rendered
    assert "存在博弈空间" in rendered


def test_daily_price_space_does_not_claim_trade_space_without_a_setup() -> None:
    bars = _daily_bars("BK001.DC", 140)
    observation = analyze_daily_series("BK001.DC", bars, bars[-1].trade_date)
    price_space = observation["metrics"]["price_space"]

    assert price_space["method"] == "structural-scenario-engine"
    assert price_space["profile"] == "coarse"
    assert price_space["upside_target"] is not None
    assert price_space["downside_target"] is not None
    assert price_space["risk_reward_ratio"] is None
    assert price_space["has_trade_space"] is False
    _, rendered = render_board_summary(observation, None)
    assert "不计算盈亏比" in rendered


def test_daily_comparison_separates_session_history_and_correction_baseline() -> None:
    bars = _daily_bars("BK001.DC", 140)
    current = analyze_daily_series("BK001.DC", bars, bars[-1].trade_date)
    current["state_codes"] = ["bullish-boundary-triggered"]
    prior = {
        "run_id": "prior-session", "effective_date": bars[-2].trade_date,
        "payload": {
            "conclusion_code": "bullish-transition-candidate",
            "metrics": current["metrics"],
        },
    }
    correction = {
        "run_id": "same-date-revision", "effective_date": bars[-1].trade_date,
        "payload": {"conclusion_code": "neutral", "metrics": current["metrics"]},
    }

    result = build_board_analysis_record(current, prior, [prior], correction)
    current.update(result)
    _apply_transition_attention(current)

    comparison = result["comparison"]
    assert comparison["transition"] == "strengthened"
    assert comparison["prior_run_id"] == "prior-session"
    assert comparison["correction_baseline"]["run_id"] == "same-date-revision"
    assert comparison["recent_sessions"][0]["run_id"] == "prior-session"
    assert comparison["shape"] == {
        "short_shape": "unchanged", "medium_shape": "unchanged",
    }
    assert current["attention_eligible"] is True
    assert "prior-state-strengthened" in current["attention_reasons"]


def test_active_market_value_does_not_treat_synthetic_volume_as_traded_volume() -> None:
    bars = _daily_bars("SHAMV.A", 140)
    bars[-1] = DailyBar(
        "SHAMV.A", bars[-1].trade_date, bars[-1].open, bars[-1].high,
        bars[-1].low, bars[-1].close, bars[-1].volume * 100,
    )
    observation = analyze_daily_series(
        "SHAMV.A", bars, bars[-1].trade_date,
        volume_semantics="synthetic-not-traded",
    )
    observation["metrics"]["active_market_value_diagnostics"] = {
        "coverage_ratio": .991, "eligible_count": 5500,
        "total_count": 5550, "contribution_total": 123456.78,
    }

    _, rendered = render_board_summary(observation, None)

    assert observation["metrics"]["volume_ratio20"] is None
    assert "sudden-volume-expansion" not in observation["state_codes"]
    assert "合成指数不解释K线成交量" in rendered
    assert "99.10%" in rendered


def test_daily_deep_queue_prioritizes_manual_then_previously_deferred_items() -> None:
    observations = [
        {"symbol": "NEW.DC", "attention_reasons": ["sudden-volume-expansion"]},
        {"symbol": "OLD.DC", "attention_reasons": ["boundary-volume-contraction"]},
        {"symbol": "PIN.DC", "attention_reasons": []},
    ]
    registry = {
        "NEW.DC": {"manual_pinned": False, "first_observed_date": date(2026, 9, 7)},
        "OLD.DC": {"manual_pinned": False, "first_observed_date": date(2026, 9, 1)},
        "PIN.DC": {"manual_pinned": True, "first_observed_date": date(2026, 9, 7)},
    }
    history = {"OLD.DC": {"deep_analysis_state": "deferred-resource-limit"}}

    ordered = _order_daily_deep_candidates(
        observations, registry, history, date(2026, 9, 7),
    )

    assert [item["symbol"] for item in ordered] == ["PIN.DC", "OLD.DC", "NEW.DC"]


def test_market_style_divergence_compares_active_value_with_shanghai() -> None:
    result = _market_style_divergence({
        "000001.SH": {
            "coverage_state": "complete",
            "metrics": {"returns": {"5": .01, "20": .03}},
        },
        "SHAMV.A": {
            "coverage_state": "complete",
            "metrics": {"returns": {"5": .04, "20": .09}},
        },
    })

    assert result == {
        "state": "active-value-led", "difference_5": .03,
        "difference_20": .06,
    }


def test_board_breadth_snapshot_uses_deduplicated_member_moves() -> None:
    store = SQLiteMarketDataStore(":memory:")
    effective = date(2026, 9, 4)
    prior = effective - timedelta(days=1)
    store.upsert_instruments([
        Instrument("BK001.DC", "测试板块", InstrumentKind.SECTOR, "DC"),
        Instrument("000001.SZ", "上涨", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "下跌", InstrumentKind.STOCK, "SZ"),
    ])
    for source in ("source-a", "source-b"):
        store.replace_board_memberships(source, "BK001.DC", effective, [
            BoardMembership("BK001.DC", "000001.SZ", "上涨", source, effective),
            BoardMembership("BK001.DC", "000002.SZ", "下跌", source, effective),
        ])
    store.upsert_daily_bars("test", [
        DailyBar("000001.SZ", prior, 10, 10, 10, 10, 100),
        DailyBar("000001.SZ", effective, 11, 11, 11, 11, 200),
        DailyBar("000002.SZ", prior, 20, 20, 20, 20, 100),
        DailyBar("000002.SZ", effective, 19, 19, 19, 19, 100),
    ])
    store.derive_market_snapshots(effective)

    result = store.calculate_board_breadth_snapshots(effective)["BK001.DC"]

    assert result["member_count"] == 2
    assert result["covered_member_count"] == 2
    assert result["advance_count"] == 1
    assert result["decline_count"] == 1
    assert result["breadth"] == 0
    assert result["average_return_1"] == .025
    store.close()


def test_daily_signal_persists_every_board_but_displays_attention_only() -> None:
    store = SQLiteMarketDataStore(":memory:")
    instruments = [
        Instrument("000001.SH", "上证指数", InstrumentKind.INDEX, "SH"),
        Instrument("SHAMV.A", "活跃市值", InstrumentKind.INDEX, "LOCAL"),
        Instrument("BK001.DC", "平稳板块", InstrumentKind.SECTOR, "DC"),
        Instrument("BK002.DC", "放量板块", InstrumentKind.SECTOR, "DC"),
    ]
    store.upsert_instruments(instruments)
    baseline = _daily_bars("000001.SH", 140)
    active = _daily_bars("SHAMV.A", 140)
    quiet = _daily_bars("BK001.DC", 140)
    loud = _daily_bars("BK002.DC", 140)
    loud[-1] = DailyBar(
        loud[-1].symbol, loud[-1].trade_date, loud[-1].open,
        loud[-1].high, loud[-1].low, loud[-1].close, loud[-1].volume * 3,
    )
    store.upsert_daily_bars("test", [*baseline, *active, *quiet, *loud])

    result = __import__("stock_harness.signal_review", fromlist=["SignalReviewService"])
    service = result.SignalReviewService(store)
    service._boards = lambda: [
        {"symbol": "BK001.DC", "name": "平稳板块"},
        {"symbol": "BK002.DC", "name": "放量板块"},
    ]
    run = service.run_sync(DAILY_MARKET_BOARD_SIGNAL, baseline[-1].trade_date)

    assert run["summary"]["expected_board_count"] == 2
    assert run["summary"]["saved_observation_count"] == 2
    assert store.count_board_daily_observations(str(run["run_id"])) == 2
    observations = store.list_board_daily_observations(run_id=str(run["run_id"]), limit=10)
    assert {item["symbol"] for item in observations} == {"BK001.DC", "BK002.DC"}
    assert all(item["conclusion_code"] for item in observations)
    assert all("近期对比" in item["rendered_summary"] for item in observations)
    assert all(item["comparison"]["transition"] == "new" for item in observations)
    loud_observation = next(item for item in observations if item["symbol"] == "BK002.DC")
    assert loud_observation["deep_analysis_state"] in {
        "confirmed", "completed-no-structural-evidence",
    }
    assert loud_observation["deep_analysis_run_id"]
    items = store.list_signal_review_items(str(run["run_id"]))
    scores = store.list_signal_review_scores(str(run["run_id"]))
    assert len(scores) == 4
    assert {score["system_id"] for score in scores} == {
        "market-regime", "trend-breakout",
    }
    assert all(score["participant_count"] == 2 for score in scores)
    assert {item["symbol"] for item in items if item["profile"] == "attention"} == {"BK002.DC"}
    focused_board = next(item for item in items if item["profile"] == "attention")
    assert focused_board["payload"]["score_result"]["system_id"] == "trend-breakout"
    assert "- 证据：[S" in focused_board["payload"]["rendered_summary"]
    market_item = next(item for item in items if item["symbol"] == "000001.SH")
    assert "- 风格：" in market_item["payload"]["rendered_summary"]
    assert any(
        evidence["evidence_type"] == "market-style-divergence"
        for evidence in market_item["evidence"]
    )
    assert run["summary"]["ai_used"] is False
    with TestClient(create_app(store=store)) as client:
        score_response = client.get(
            f"/api/signals/runs/{run['run_id']}/scores",
            params={"system_id": "trend-breakout"},
        )
    assert score_response.status_code == 200
    assert score_response.json()["total"] == 2

    replay = service.run_sync(DAILY_MARKET_BOARD_SIGNAL, baseline[-1].trade_date)
    replay_observations = store.list_board_daily_observations(
        run_id=str(replay["run_id"]), limit=10,
    )
    assert all(
        item["comparison"]["prior_run_id"] is None
        for item in replay_observations
    )
    assert all(
        item["comparison"]["correction_baseline"]["run_id"] == run["run_id"]
        for item in replay_observations
    )
    next_date = baseline[-1].trade_date + timedelta(days=1)
    store.upsert_daily_bars("test", [
        DailyBar(series[-1].symbol, next_date, series[-1].close,
                 series[-1].close + .5, series[-1].close - .5,
                 series[-1].close + .1, series[-1].volume)
        for series in (baseline, active, quiet, loud)
    ])
    next_run = service.run_sync(DAILY_MARKET_BOARD_SIGNAL, next_date)
    next_observations = store.list_board_daily_observations(
        run_id=str(next_run["run_id"]), limit=10,
    )
    assert all(
        item["comparison"]["prior_run_id"] == replay["run_id"]
        and len(item["comparison"]["recent_sessions"]) == 1
        and item["comparison"]["correction_baseline"] is None
        for item in next_observations
    )
    store.close()


def test_manual_attention_keeps_neutral_board_in_compact_result() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SH", "上证指数", InstrumentKind.INDEX, "SH"),
        Instrument("SHAMV.A", "活跃市值", InstrumentKind.INDEX, "LOCAL"),
        Instrument("BK001.DC", "平稳板块", InstrumentKind.SECTOR, "DC"),
    ])
    bars = _daily_bars("000001.SH", 140)
    board = _daily_bars("BK001.DC", 140)
    store.upsert_daily_bars("test", [*bars, *board, *_daily_bars("SHAMV.A", 140)])
    store.set_signal_attention(
        DAILY_MARKET_BOARD_SIGNAL, "BK001.DC", manual_pinned=True,
        effective_date=bars[-1].trade_date,
    )
    result = __import__("stock_harness.signal_review", fromlist=["SignalReviewService"])
    service = result.SignalReviewService(store)
    service._boards = lambda: [{"symbol": "BK001.DC", "name": "平稳板块"}]
    run = service.run_sync(
        DAILY_MARKET_BOARD_SIGNAL, bars[-1].trade_date,
    )
    items = store.list_signal_review_items(str(run["run_id"]))
    assert any(item["symbol"] == "BK001.DC" and item["active"] for item in items)
    assert store.get_signal_attention(
        DAILY_MARKET_BOARD_SIGNAL, "BK001.DC",
    )["status"] == "manual-pinned"
    store.close()


def test_market_emotion_uses_official_limits_and_persists_with_run() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "涨停股", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "炸板股", InstrumentKind.STOCK, "SZ"),
        Instrument("000003.SZ", "跌停股", InstrumentKind.STOCK, "SZ"),
    ])
    first = date(2026, 9, 3)
    current = date(2026, 9, 4)
    store.upsert_daily_bars("test", [
        DailyBar("000001.SZ", first, 10, 10, 10, 10, 100),
        DailyBar("000002.SZ", first, 10, 10, 10, 10, 100),
        DailyBar("000003.SZ", first, 10, 10, 10, 10, 100),
        DailyBar("000001.SZ", current, 10.5, 11, 10.5, 11, 300),
        DailyBar("000002.SZ", current, 10.5, 11, 10.2, 10.6, 400),
        DailyBar("000003.SZ", current, 9.5, 9.5, 9, 9, 500),
    ])
    store.upsert_stock_daily_limits("test", [
        StockDailyLimit("000001.SZ", current, 11, 9),
        StockDailyLimit("000002.SZ", current, 11, 9),
        StockDailyLimit("000003.SZ", current, 11, 9),
    ])
    run = store.create_signal_review_run(
        signal_id=DAILY_MARKET_BOARD_SIGNAL,
        definition_version="daily-v1", algorithm_version="algorithm-v1",
        cadence="daily", effective_date=current, parameters={},
    )
    snapshot = store.calculate_market_emotion_snapshot(str(run["run_id"]), current)

    assert snapshot["status"] == "complete"
    assert snapshot["metrics"] == {
        "advance_count": 2, "decline_count": 1, "unchanged_count": 0,
        "breadth": 0.333333, "limit_up_count": 1, "limit_down_count": 1,
        "broken_up_count": 1, "sealing_rate": 0.5, "limit_balance": 0.0,
    }
    assert store.get_market_emotion_snapshot(str(run["run_id"]))["input_digest"] == snapshot["input_digest"]
    store.fail_signal_review_run(str(run["run_id"]), "test cleanup")
    store.close()


def test_signal_observation_and_attention_api() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("BK001.DC", "测试板块", InstrumentKind.SECTOR, "DC"),
        Instrument("000001.SZ", "日期基准", InstrumentKind.STOCK, "SZ"),
    ])
    effective = date(2026, 9, 4)
    store.upsert_daily_bars("test", [DailyBar(
        "000001.SZ", effective, 10, 10, 10, 10, 100,
    )])
    run = store.create_signal_review_run(
        signal_id=DAILY_MARKET_BOARD_SIGNAL,
        definition_version="daily-v1", algorithm_version="algorithm-v1",
        cadence="daily", effective_date=effective, parameters={},
    )
    observation = analyze_daily_series("BK001.DC", [], effective)
    store.save_board_daily_observations(str(run["run_id"]), [observation])
    store.fail_signal_review_run(str(run["run_id"]), "fixture complete")

    with TestClient(create_app(store=store)) as client:
        history = client.get(
            f"/api/signals/runs/{run['run_id']}/board-observations",
            params={"symbol": "BK001.DC"},
        )
        assert history.status_code == 200
        assert history.json()["items"][0]["coverage_state"] == "insufficient"
        pinned = client.put(
            f"/api/signals/{DAILY_MARKET_BOARD_SIGNAL}/attention/BK001.DC",
            json={"manual_pinned": True, "effective_date": effective.isoformat()},
        )
        assert pinned.status_code == 200
        attention = client.get(
            f"/api/signals/{DAILY_MARKET_BOARD_SIGNAL}/attention"
        ).json()["items"]
        assert attention[0]["status"] == "manual-pinned"
    store.close()


def test_board_observation_pool_unions_scores_anomalies_and_recognition() -> None:
    store = SQLiteMarketDataStore(":memory:")
    effective = date(2026, 9, 4)
    store.upsert_instruments([
        Instrument("BK001.DC", "Score Board", InstrumentKind.SECTOR, "DC"),
        Instrument("BK002.DC", "Anomaly Board", InstrumentKind.SECTOR, "DC"),
        Instrument("000001.SZ", "Leader", InstrumentKind.STOCK, "SZ"),
    ])
    recognition = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="weekly-v1", algorithm_version="recognition-v1",
        cadence="weekly", effective_date=effective - timedelta(days=1), parameters={},
    )
    recognition_item = _item("000001.SZ", "added")
    recognition_item["evidence"][0]["payload"] = {
        "board_symbol": "BK001.DC", "board_name": "Score Board",
        "rank": 1, "score": .9, "confidence": .8,
    }
    store.complete_signal_review_run(
        str(recognition["run_id"]), items=[recognition_item],
        summary={}, input_digest="recognition",
    )
    daily = store.create_signal_review_run(
        signal_id=DAILY_MARKET_BOARD_SIGNAL,
        definition_version="daily-v1", algorithm_version="daily-v1",
        cadence="daily", effective_date=effective, parameters={},
    )
    scores = [{
        "symbol": "BK001.DC", "entity_key": "BK001.DC", "eligible": True,
        "rank": 1, "total_score": 82, "grade": "A", "comparison": {"state": "new"},
        "hard_events": [],
    }, {
        "symbol": "BK002.DC", "entity_key": "BK002.DC", "eligible": False,
        "rank": 2, "total_score": 42, "grade": "D", "comparison": {"state": "new"},
        "hard_events": [{"event_type": "sudden-volume-expansion", "state": "new"}],
    }]
    snapshot = _build_board_pool_snapshot(
        store, str(daily["run_id"]), effective, scores, [], None,
    )
    store.complete_signal_review_run(
        str(daily["run_id"]), items=[], summary={}, input_digest="daily",
        pool_snapshots=[snapshot],
    )

    stored = store.get_observation_pool_snapshot(str(daily["run_id"]), "board")
    assert stored is not None
    assert [item["symbol"] for item in stored["items"]] == ["BK001.DC", "BK002.DC"]
    scored = stored["items"][0]
    assert scored["lifecycle_state"] == "new"
    assert {source["source_type"] for source in scored["sources"]} == {
        "trend-score", "recognition-assignment",
    }
    recognition_source = next(
        source for source in scored["sources"]
        if source["source_type"] == "recognition-assignment"
    )
    assert recognition_source["source_reference"] == recognition["run_id"]
    assert recognition_source["payload"]["member_symbol"] == "000001.SZ"
    assert recognition_source["payload"]["recognition_role"] == "recent-rank-1"
    anomaly = stored["items"][1]
    assert anomaly["payload"]["trend_eligible"] is False
    assert anomaly["sources"][0]["reason"] == "sudden-volume-expansion"

    context = build_signal_chat_context(
        store, run_id=str(daily["run_id"]),
        selected_item_ids=["pool:board:BK001.DC"],
    )
    assert context["selected_items"][0]["symbol"] == "BK001.DC"
    assert context["selected_items"][0]["item_id"] == "pool:board:BK001.DC"
    assert {evidence["kind"] for evidence in context["evidence"]} == {
        "trend-score", "recognition-assignment",
    }
    assert context["evidence"][0]["code"] == "O1"

    with TestClient(create_app(store=store)) as client:
        response = client.get(
            f"/api/observation-pools/runs/{daily['run_id']}/board"
        )
    assert response.status_code == 200
    assert response.json()["summary"]["item_count"] == 2
    store.close()


def test_stock_opportunity_classification_keeps_recognition_and_m4_separate() -> None:
    scenario = {
        "state": "retest",
        "targets": [{"stressed_risk_reward_ratio": 3.4}],
    }
    recognized = _stock_opportunity_classification({
        "recognized": True,
        "independent_scan": {"eligible": False},
    }, scenario)
    emerging = _stock_opportunity_classification({
        "recognized": False,
        "independent_scan": {"eligible": True},
    }, scenario)
    waiting = _stock_opportunity_classification({
        "recognized": True,
        "independent_scan": {"eligible": True},
    }, {"state": "waiting-trigger", "targets": [
        {"stressed_risk_reward_ratio": 2.9},
    ]})

    assert recognized["classification"] == "recognized-and-eligible"
    assert emerging["classification"] == "emerging-core-candidate"
    assert waiting["classification"] == "recognized-but-ineligible"
    assert waiting["opportunity_eligible"] is False


def test_stock_pool_runs_and_links_bounded_authoritative_m4_analysis() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Stock", InstrumentKind.STOCK, "SZ"),
    ])
    bars = _daily_bars("000001.SZ", 320)
    store.upsert_daily_bars("test", bars)
    service = SignalReviewService(store)
    run = store.create_signal_review_run(
        signal_id=DAILY_MARKET_BOARD_SIGNAL,
        definition_version="daily-v1", algorithm_version="daily-v1",
        cadence="daily", effective_date=bars[-1].trade_date, parameters={},
    )
    pool = {"items": [{
        "symbol": "000001.SZ", "lifecycle_state": "new", "rank": 1,
        "payload": {
            "recognized": True, "independent_scan": {"eligible": True},
            "member_scan": None, "screener_results": [],
        },
        "sources": [],
    }]}

    summary = service._run_stock_pool_analysis(
        str(run["run_id"]), bars[-1].trade_date, pool,
    )

    payload = pool["items"][0]["payload"]
    assert summary["confirmed_count"] == 1
    assert payload["m4_analysis"]["run_id"]
    assert payload["m4_analysis"]["state"] == "confirmed"
    assert payload["opportunity_classification"]["recognition_state"] == "recognized"
    assert pool["items"][0]["sources"][0]["source_type"] == "m4-analysis"
    store.fail_signal_review_run(str(run["run_id"]), "fixture complete")
    store.close()


def test_automatic_attention_moves_through_cooldown_without_touching_manual_pins() -> None:
    from datetime import timedelta

    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("BK001.DC", "自动板块", InstrumentKind.SECTOR, "DC"),
        Instrument("BK002.DC", "固定板块", InstrumentKind.SECTOR, "DC"),
    ])
    start = date(2026, 9, 4)
    store.promote_signal_attention(
        DAILY_MARKET_BOARD_SIGNAL, "BK001.DC", start, ["sudden-volume-expansion"],
    )
    store.set_signal_attention(
        DAILY_MARKET_BOARD_SIGNAL, "BK002.DC", manual_pinned=True,
        effective_date=start,
    )

    cooling = store.advance_signal_attention_lifecycle(
        DAILY_MARKET_BOARD_SIGNAL, "BK001.DC", start + timedelta(days=1),
        start + timedelta(days=7),
    )
    inactive = store.advance_signal_attention_lifecycle(
        DAILY_MARKET_BOARD_SIGNAL, "BK001.DC", start + timedelta(days=7),
        start + timedelta(days=14),
    )
    manual = store.advance_signal_attention_lifecycle(
        DAILY_MARKET_BOARD_SIGNAL, "BK002.DC", start + timedelta(days=7),
        start + timedelta(days=14),
    )

    assert cooling["status"] == "cooldown"
    assert inactive["status"] == "inactive"
    assert manual["status"] == "manual-pinned"
    store.close()


def _item(symbol: str, change_type: str, rank: int = 1) -> dict[str, object]:
    return {
        "item_id": f"item-{symbol}", "item_key": f"recent:{symbol}",
        "rank": rank, "symbol": symbol, "profile": "recent",
        "change_type": change_type, "active": change_type != "removed",
        "score": 0.91, "confidence": 0.72,
        "payload": {"board_count": 2},
        "evidence": [{
            "evidence_id": f"evidence-{symbol}", "alias": f"S{rank}",
            "evidence_type": "board-recognition-ranking",
            "payload": {"board_name": "CPO"},
        }],
    }


def _assignment(
    profile: str, symbol: str, board: str, rank: int, score: float, confidence: float,
) -> dict[str, object]:
    return {
        "profile": profile, "board_symbol": f"BOARD-{board}", "board_name": board,
        "board_classification": "concept", "member_symbol": symbol,
        "member_name": symbol, "rank": rank, "score": score,
        "confidence": confidence, "components": {"association": .8},
    }


def _daily_bars(symbol: str, count: int, falling: bool = False) -> list[DailyBar]:
    from datetime import timedelta

    start = date(2026, 1, 1)
    result = []
    for index in range(count):
        close = 100 - index * .12 if falling else 100 + index * .03
        result.append(DailyBar(
            symbol, start + timedelta(days=index), close - .2, close + .5,
            close - .5, close, 1_000_000 + index * 100,
        ))
    return result
