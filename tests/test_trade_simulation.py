from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from stock_harness.api_simulation_routes import create_simulation_router
from stock_harness.trade_simulation import SimulationInput, example_scenarios, simulate


def client():
    app = FastAPI()
    app.include_router(create_simulation_router())
    return TestClient(app)


def run(index=0, **patch):
    data = example_scenarios()[index]
    data.update(patch)
    return simulate(SimulationInput.model_validate(data))


def test_examples_are_explicit_synthetic_inputs_and_do_not_mutate():
    samples = example_scenarios()
    before = deepcopy(samples)
    result = simulate(SimulationInput.model_validate(samples[0]))
    assert samples == before
    assert result['mode'] == 'manual-scenario-not-historical-backtest'
    assert result['input']['label'].startswith('合成情景')
    assert result['status'] == 'closed'
    assert [e['kind'] for e in result['events']] == ['entry', 'exit-signal', 'exit']
    assert result['events'][-1]['session'] == '2000-01-06'


def test_cash_fees_and_open_equity_are_consistent():
    result = run()
    entry, _, exit = result['events']
    quantity = result['planned_quantity']
    profit = (exit['price'] * .999 - entry['price'] * 1.001) * quantity
    assert result['summary']['realized_pnl'] == pytest.approx(profit)
    assert result['summary']['final_equity'] == pytest.approx(100000 + profit)
    assert result['summary']['open_quantity'] == 0


def test_limit_down_is_deferred_and_entry_day_exit_not_filled():
    result = run(1)
    assert [e['kind'] for e in result['events']] == ['entry', 'exit-signal', 'deferred', 'exit']
    assert result['events'][-1]['price'] < 9
    assert result['summary']['realized_pnl'] < 0


def test_gap_above_entry_ceiling_does_not_fill():
    result = run(2)
    assert result['status'] == 'expired'
    assert result['summary']['final_equity'] == 100000
    assert result['summary']['realized_pnl'] is None


@pytest.mark.parametrize('patch,reason', [
    ({'eligibility_assumed': False}, 'eligibility-not-assumed'),
    ({'median_amount20': 1e7}, 'liquidity'),
    ({'capital': 100}, 'insufficient-capacity'),
])
def test_rejected_scenarios_have_no_fills(patch, reason):
    result = run(**patch)
    assert result['status'] == 'not-entered'
    assert result['events'][0]['reason'] == reason
    assert result['summary']['net_return'] == 0


def test_end_of_sample_is_not_forced_exit_and_missing_marks_are_visible():
    data = example_scenarios()[0]
    data['sessions'] = data['sessions'][:2]
    data['sessions'][-1]['close'] = None
    result = simulate(SimulationInput.model_validate(data))
    assert result['status'] == 'held'
    assert result['summary']['open_quantity'] > 0
    assert result['summary']['realized_pnl'] is None
    assert result['summary']['stale_valuation'] is True


def test_prefix_is_not_changed_by_future_prices():
    data = example_scenarios()[0]
    first = simulate(SimulationInput.model_validate(data))
    data['sessions'][-1].update(open=8, close=8)
    poisoned = simulate(SimulationInput.model_validate(data))
    assert first['equity'][:2] == poisoned['equity'][:2]
    assert first['events'][:2] == poisoned['events'][:2]
    assert first['input_digest'] != poisoned['input_digest']


def test_api_validates_sequence_bounds_and_geometry():
    with client() as api:
        examples = api.get('/api/trade-simulation/scenarios').json()['items']
        assert len(examples) == 3
        assert api.post('/api/trade-simulation/run', json=examples[0]).status_code == 200
        for patch in ({'target': 10.5}, {'sessions': []}, {'unknown': 1}, {'capital': -1}):
            assert api.post('/api/trade-simulation/run', json={**examples[0], **patch}).status_code == 422
        examples[0]['sessions'][1]['session'] = examples[0]['sessions'][0]['session']
        assert api.post('/api/trade-simulation/run', json=examples[0]).status_code == 422
