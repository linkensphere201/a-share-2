from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from stock_harness.analysis_inputs import (
    AnalysisHorizons, AnalysisInputMode, AnalysisInputService, AnalysisTimeframe, PreparedAnalysisInput,
)
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.pattern_analysis import PatternAnalysisRequest, PatternAnalysisService
from stock_harness.sqlite_store import SQLiteMarketDataStore


def seed(store):
    store.upsert_instruments([Instrument('600001.SH', 'Fixture', InstrumentKind.STOCK, 'SH')])
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(80)]
    bars = [DailyBar('600001.SH', day, 10, 11, 9, 10, 1000) for day in days]
    store.upsert_daily_bars('tushare', bars)
    store.upsert_trading_dates('tushare', days)
    return bars


def prepare(store, cutoff):
    stamp = store.analysis_read_version()
    value = AnalysisInputService(store).build('600001.SH', cutoff)
    return PreparedAnalysisInput(value, stamp)


def request(prepared):
    return PatternAnalysisRequest('600001.SH', (AnalysisTimeframe.DAILY,), AnalysisHorizons(),
                                  'prepared-test', as_of_date=prepared.value.as_of_date,
                                  prepared_input=prepared)


@pytest.mark.parametrize('external', [False, True])
def test_update_before_reservation_rebuilds_input(tmp_path, monkeypatch, external):
    path = tmp_path / 'market.sqlite'
    with SQLiteMarketDataStore(path) as store:
        bars = seed(store)
        prepared = prepare(store, bars[-1].trade_date)
        updated = replace(bars[-1], open=20, high=22, low=18, close=20)
        if external:
            with SQLiteMarketDataStore(path) as writer:
                writer.upsert_daily_bars('tushare', [updated])
        else:
            store.upsert_daily_bars('tushare', [updated])
        service = PatternAnalysisService(store)
        built = []
        build = service._delegate._inputs.build
        def capture(*args, **kwargs):
            value = build(*args, **kwargs)
            if value.symbol == '600001.SH':
                built.append(value)
            return value
        monkeypatch.setattr(service._delegate._inputs, 'build', capture)
        result = service.analyze(request(prepared))[0]
        assert result['status'] == 'succeeded'
        assert len(built) == 1 and built[0].bars[-1].close == 20
        assert not store.claim_generated_analysis_targets()


def test_update_after_reservation_keeps_new_generation_dirty(monkeypatch):
    with SQLiteMarketDataStore(':memory:') as store:
        bars = seed(store)
        prepared = prepare(store, bars[-1].trade_date)
        reserve = store.reserve_generated_analysis_target
        def interleaved(*args, **kwargs):
            claim = reserve(*args, **kwargs)
            store.upsert_daily_bars('tushare', [replace(bars[-1], volume=5000)])
            return claim
        monkeypatch.setattr(store, 'reserve_generated_analysis_target', interleaved)
        PatternAnalysisService(store).analyze(request(prepared))
        assert len(store.claim_generated_analysis_targets()) == 1


@pytest.mark.parametrize('case', ['mode', 'date', 'source', 'time', 'bar', 'future', 'overlap', 'latest'])
def test_final_input_rejects_preview_and_invalid_dates(case):
    with SQLiteMarketDataStore(':memory:') as store:
        bars = seed(store)
        prepared = prepare(store, bars[-1].trade_date)
        value = prepared.value
        changes = {
            'mode': {'mode': AnalysisInputMode.PREVIEW},
            'date': {'provisional_date': value.as_of_date},
            'source': {'provisional_source': 'preview'},
            'time': {'provisional_provider_time': datetime.now(timezone.utc)},
            'bar': {'bars': (*value.bars[:-1], replace(value.bars[-1], contains_provisional=True))},
            'future': {'bars': (*value.bars[:-1], replace(value.bars[-1], period_end=value.as_of_date + timedelta(days=1)))},
            'overlap': {'bars': (*value.bars, value.bars[-1])},
            'latest': {'latest_final_date': value.as_of_date + timedelta(days=1)},
        }
        invalid = replace(prepared, value=replace(value, **changes[case]))
        with pytest.raises(ValueError, match='prepared input'):
            request(invalid).validate()
        with pytest.raises(ValueError, match='prepared input'):
            PatternAnalysisService(store)._delegate.recalculate(
                value.symbol, (value.timeframe,), value.horizons, config_version='invalid',
                include_preview=False, as_of_date=value.as_of_date, prepared_input=invalid)


def test_read_stamp_is_bound_to_its_connection():
    with SQLiteMarketDataStore(':memory:') as first, SQLiteMarketDataStore(':memory:') as second:
        seed(first)
        seed(second)
        assert first.analysis_read_version() != second.analysis_read_version()
