from dataclasses import replace

import pytest

from stock_harness import trend_analysis
from stock_harness.pattern_analysis import PatternAnalysisService
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_pivots import DirectionalChangeConfig
from test_prepared_analysis_input import prepare, request, seed


def counted(monkeypatch, service):
    calls = {"detector": 0, "context": 0}
    compute = trend_analysis._compute_items
    context = service._delegate._build_context_evidence

    def detect(*args, **kwargs):
        calls["detector"] += 1
        return compute(*args, **kwargs)

    def build_context(*args, **kwargs):
        calls["context"] += 1
        return context(*args, **kwargs)

    monkeypatch.setattr(trend_analysis, "_compute_items", detect)
    monkeypatch.setattr(service._delegate, "_build_context_evidence", build_context)
    return calls


def test_batch_reuses_computation_but_keeps_strategy_provenance(monkeypatch):
    with SQLiteMarketDataStore(":memory:") as store:
        bars = seed(store)
        service = PatternAnalysisService(store)
        base = request(prepare(store, bars[-1].trade_date))
        calls = counted(monkeypatch, service)
        with service.shared_computation():
            first = service.analyze(base)[0]
            second = service.analyze(replace(base, config_version="second"))[0]
        assert calls == {"detector": 1, "context": 1}
        assert first["run_id"] != second["run_id"]
        assert first["config_version"] != second["config_version"]
        assert first["items"] == second["items"]
        # Scope exit discards all prepared computations.
        service.analyze(replace(base, config_version="third"))
        assert calls == {"detector": 2, "context": 2}


def test_source_and_parameter_changes_do_not_reuse_computation(monkeypatch):
    with SQLiteMarketDataStore(":memory:") as store:
        bars = seed(store)
        service = PatternAnalysisService(store)
        base = request(prepare(store, bars[-1].trade_date))
        calls = counted(monkeypatch, service)
        with service.shared_computation():
            service.analyze(base)
            service.analyze(replace(base, config_version="pivot", pivot_config=DirectionalChangeConfig(
                atr_multiplier=3.0,
            )))
            store.upsert_daily_bars("tushare", [replace(bars[-1], volume=5000)])
            service.analyze(replace(base, config_version="updated"))
        assert calls == {"detector": 3, "context": 3}


def test_cached_items_are_isolated_from_persistence_mutation(monkeypatch):
    with SQLiteMarketDataStore(":memory:") as store:
        bars = seed(store)
        service = PatternAnalysisService(store)
        base = request(prepare(store, bars[-1].trade_date))
        complete = store.complete_generated_analysis_run
        seen = []

        def mutate(run_id, items, **kwargs):
            seen.append(items[0].payload.copy())
            complete(run_id, items, **kwargs)
            items[0].payload["mutation"] = True

        monkeypatch.setattr(store, "complete_generated_analysis_run", mutate)
        with service.shared_computation():
            service.analyze(base)
            service.analyze(replace(base, config_version="second"))
        assert len(seen) == 2 and seen[0] == seen[1]
        assert "mutation" not in seen[1]


def test_failed_detector_is_retried_and_nested_scopes_are_isolated(monkeypatch):
    with SQLiteMarketDataStore(":memory:") as store:
        bars = seed(store)
        service = PatternAnalysisService(store)
        base = request(prepare(store, bars[-1].trade_date))
        compute = trend_analysis._compute_items
        calls = []

        def failing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("fixture detector failure")
            return compute(*args, **kwargs)

        monkeypatch.setattr(trend_analysis, "_compute_items", failing)
        with service.shared_computation():
            with pytest.raises(ValueError, match="fixture detector failure"):
                service.analyze(base)
            service.analyze(replace(base, config_version="retry"))
            with service.shared_computation():
                service.analyze(replace(base, config_version="nested"))
            service.analyze(replace(base, config_version="outer"))
        assert len(calls) == 3
