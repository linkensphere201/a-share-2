"""Batch admission, shared-input bounds and independent result parity."""
from datetime import date
from dataclasses import replace
from threading import Event
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.analysis_inputs import AnalysisInputService
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.pattern_analysis import PatternAnalysisRequest, PatternAnalysisService
from stock_harness.analysis_inputs import AnalysisTimeframe
from stock_harness.screener import ScreenerService, DEFAULT_HORIZONS
from stock_harness.screener_strategies import STRATEGIES, ScreenerStrategy
from stock_harness.sqlite_store import SQLiteMarketDataStore
from test_screener_concurrency import wait_until
from test_screener_performance import source_bars
from test_long_platform_pattern import sample as long_platform_bars
from test_low_accumulation_pattern import fixture as low_accumulation_bars


SHAPES = [s for s in STRATEGIES.values() if s.execution == 'shape']
CUTOFF = date(2026, 9, 28)


def test_batch_admission_skips_busy_and_isolates_failed_strategy(monkeypatch):
    with SQLiteMarketDataStore(':memory:') as store:
        store.upsert_instruments([Instrument('600001.SH', 'Test', InstrumentKind.STOCK, 'SH')])
        service = ScreenerService(store)
        release = Event()
        monkeypatch.setattr(store, 'get_latest_stock_daily_bar_date', lambda: CUTOFF)
        def prepare(request):
            assert release.wait(5)
            return object()
        monkeypatch.setattr(service._analysis, 'prepare_screening_subject', prepare)
        def analyze(strategy, facade, request):
            if strategy.strategy_id == SHAPES[0].strategy_id:
                raise RuntimeError('isolated shape failure')
            return None
        monkeypatch.setattr(ScreenerStrategy, 'analyze', analyze)
        def execute(run_id, *args):
            assert release.wait(5)
            store.complete_screener_run(run_id, [])
        monkeypatch.setattr(service, '_execute', execute)
        try:
            batch = service.start_all(50)
            assert len(batch['items']) == len(STRATEGIES)
            assert not batch['skipped']
            assert {r['as_of_date'] for r in batch['items']} == {CUTOFF}
            assert all(r['parameters']['batch_id'] == batch['batch_id'] for r in batch['items'])
            assert all(r['parameters']['max_results'] == 50 for r in batch['items'])
            again = service.start_all(50)
            assert again['items'] == []
            assert len(again['skipped']) == len(STRATEGIES)
            assert {s['reason'] for s in again['skipped']} == {'already-running'}
            release.set()
            wait_until(lambda: not service._jobs)
            statuses = {r['strategy_id']: store.get_screener_run(r['run_id'])['status'] for r in batch['items']}
            assert statuses.pop(SHAPES[0].strategy_id) == 'failed'
            assert set(statuses.values()) == {'succeeded'}
        finally:
            release.set()
            service.close()


def test_batch_api_and_cutoff_validation(monkeypatch):
    with SQLiteMarketDataStore(':memory:') as store, TestClient(create_app(store=store)) as client:
        assert client.post('/api/screener/batches', json={}).status_code == 422
        for limit in (0, 501):
            assert client.post('/api/screener/batches', json={'max_results': limit}).status_code == 422
        monkeypatch.setattr(ScreenerService, '_execute_shape_batch',
                            lambda self, runs, *args: [store.complete_screener_run(r['run_id'], []) for r in runs])
        monkeypatch.setattr(ScreenerService, '_execute',
                            lambda self, run_id, *args: store.complete_screener_run(run_id, []))
        response = client.post('/api/screener/batches', json={'as_of_date': '2026-01-01'})
        assert response.status_code == 202
        payload = response.json()
        assert payload['as_of_date'] == '2026-01-01'
        assert payload['skipped'] == [{'strategy_id': 'deep-drawdown-consolidation', 'reason': 'cutoff-unavailable'}]
        assert len(payload['items']) == len(STRATEGIES) - 1


def test_batch_submission_failure_releases_all_reservations(monkeypatch):
    with SQLiteMarketDataStore(':memory:') as store:
        service = ScreenerService(store)
        def fail(*args):
            raise RuntimeError('executor unavailable')
        monkeypatch.setattr(service._executor, 'submit', fail)
        try:
            batch = service.start_all(as_of_date=CUTOFF)
            assert not service._jobs
            assert {r['status'] for r in batch['items']} == {'failed'}
        finally:
            service.close()


def setup_bars(store, bars, *, flat=False):
    store.upsert_instruments([Instrument('600001.SH', 'Test', InstrumentKind.STOCK, 'SH')])
    store.upsert_daily_bars('tushare', [DailyBar('600001.SH', b.period_end,
        10 if flat else b.open, 10.1 if flat else b.high, 9.9 if flat else b.low,
        10 if flat else b.close, 100 if flat else int(b.volume)) for b in bars])
    store.upsert_trading_dates('tushare', [b.period_end for b in bars])


def create_shape_runs(store, cutoff):
    return [store.create_screener_run(s.strategy_id, s.version, cutoff, s.parameters([], [], 200))
            for s in SHAPES if s.valid_from is None or cutoff >= s.valid_from]


@pytest.mark.parametrize('fixture_strategy', [
    'bull-flag-consolidation', 'strong-first-pullback', 'low-base-platform-pullback',
    'deep-drawdown-consolidation',
    'long-consolidation-platform', 'low-accumulation-platform',
])
def test_batch_matches_individual_scores_order_and_saved_evidence(fixture_strategy):
    bars = (long_platform_bars() if fixture_strategy == 'long-consolidation-platform' else
            low_accumulation_bars() if fixture_strategy == 'low-accumulation-platform' else
            source_bars(fixture_strategy))
    results = []
    for batch in (False, True):
        with SQLiteMarketDataStore(':memory:') as store:
            setup_bars(store, bars)
            service = ScreenerService(store)
            try:
                cutoff = bars[-1].period_end
                runs = create_shape_runs(store, cutoff)
                started = perf_counter()
                if batch:
                    service._execute_shape_batch(runs, cutoff, 200)
                else:
                    for run in runs:
                        service._execute_shape_strategy(run['run_id'], cutoff, 200, run['strategy_id'])
                values = {}
                for run in runs:
                    assert store.get_screener_run(run['run_id'])['status'] == 'succeeded'
                    candidates = store.list_screener_candidates(run['run_id'])
                    for candidate in candidates:
                        saved = store.get_generated_analysis_run(candidate['analysis_run_id'])
                        assert any(i['item_id'] == candidate['line_item_id'] for i in saved['items'])
                    values[run['strategy_id']] = [{k: c[k] for k in ('symbol', 'state', 'score', 'evidence')} for c in candidates]
                results.append(values)
                print(f'{fixture_strategy} batch={batch} elapsed={perf_counter()-started:.3f}s')
            finally:
                service.close()
    assert any(results[0].values())
    assert results[0] == results[1]


def test_negative_batch_builds_subject_once_per_symbol(monkeypatch):
    with SQLiteMarketDataStore(':memory:') as store:
        setup_bars(store, source_bars('bull-flag-consolidation'), flat=True)
        service = ScreenerService(store)
        builds = []
        original = AnalysisInputService.build
        def counted(self, *args, **kwargs):
            builds.append(args[0])
            return original(self, *args, **kwargs)
        monkeypatch.setattr(AnalysisInputService, 'build', counted)
        try:
            runs = create_shape_runs(store, CUTOFF)
            service._execute_shape_batch(runs, CUTOFF, 200)
            assert builds == ['600001.SH']
            assert all(store.get_screener_run(r['run_id'])['candidate_count'] == 0 for r in runs)
        finally:
            service.close()


def test_shared_subject_is_invalidated_by_database_change(monkeypatch):
    with SQLiteMarketDataStore(':memory:') as store:
        bars = source_bars('bull-flag-consolidation')
        setup_bars(store, bars, flat=True)
        service = PatternAnalysisService(store)
        request = PatternAnalysisRequest('600001.SH', (AnalysisTimeframe.DAILY,), DEFAULT_HORIZONS,
                                         'test-batch', as_of_date=CUTOFF)
        prepared = service.prepare_screening_subject(request)
        store.upsert_daily_bars('tushare', [DailyBar('600001.SH', bars[-1].period_end, 9, 9.1, 8.9, 9, 100)])
        builds = []
        original = AnalysisInputService.build
        def counted(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            builds.append(result.bars[-1].close)
            return result
        monkeypatch.setattr(AnalysisInputService, 'build', counted)
        service.analyze_screening_candidate(replace(request, prepared_input=prepared), 'bull-flag')
        assert builds == [9]


def test_batch_reduces_preparation_work_for_negative_universe(monkeypatch):
    bars = source_bars('bull-flag-consolidation')
    counts = []
    for batch in (False, True):
        with SQLiteMarketDataStore(':memory:') as store:
            instruments = [Instrument(f'{600001+i}.SH', str(i), InstrumentKind.STOCK, 'SH') for i in range(40)]
            store.upsert_instruments(instruments)
            store.upsert_daily_bars('tushare', [DailyBar(i.symbol, b.period_end, 10, 10.1, 9.9, 10, 100)
                                                for i in instruments for b in bars])
            service = ScreenerService(store)
            builds = []
            original = AnalysisInputService.build
            def counted(self, *args, **kwargs):
                builds.append(args[0])
                return original(self, *args, **kwargs)
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(AnalysisInputService, 'build', counted)
                    runs = create_shape_runs(store, CUTOFF)
                    started = perf_counter()
                    if batch:
                        service._execute_shape_batch(runs, CUTOFF, 200)
                    else:
                        for run in runs:
                            service._execute_shape_strategy(run['run_id'], CUTOFF, 200, run['strategy_id'])
                    print(f'40 negative stocks, {len(runs)} shapes, batch={batch}: '
                          f'{perf_counter()-started:.3f}s, preparations={len(builds)}')
                    counts.append(len(builds))
            finally:
                service.close()
    assert counts == [40 * len(SHAPES), 40]
