import pytest
from datetime import date, timedelta
from dataclasses import replace
from stock_harness.screener_strategies import STRATEGIES, get_strategy, strategy_definitions


def test_catalog_parameters_are_isolated_and_all_shapes_have_adapters():
    assert len(STRATEGIES) == 8
    definitions = strategy_definitions()
    definitions[0]["name"] = "mutated"
    assert strategy_definitions()[0]["name"] != "mutated"
    for strategy in STRATEGIES.values():
        first = strategy.parameters((), (), 20)
        first["max_results"] = 999
        assert strategy.parameters((), (), 20)["max_results"] == 20
        if strategy.structure:
            assert strategy.kind and strategy.line_code
    with pytest.raises(ValueError):
        get_strategy("unknown")


def test_descriptor_metadata_cannot_be_mutated_through_public_catalog():
    strategy = next(iter(STRATEGIES.values()))
    strategy.definition['version'] = 'invalid'
    assert strategy.version != 'invalid'
    with pytest.raises(TypeError):
        STRATEGIES['injected'] = strategy


@pytest.mark.parametrize('strategy', [s for s in STRATEGIES.values() if s.selection], ids=lambda s: s.strategy_id)
def test_shape_matching_and_ranking_preserve_existing_rules(strategy):
    cutoff = date(2026, 9, 17)
    selection = strategy.selection
    evidence = {'kind': selection.kind, 'stage': 'pullback-observation',
                'screen_eligible': True, 'as_of_date': cutoff.isoformat()}
    if selection.low_base:
        evidence['launch_type'] = 'low-base-platform'
    item = {'item_type': 'zone', 'payload': evidence}
    assert selection.matches(item, cutoff)
    assert not selection.matches(item, cutoff + timedelta(days=1))
    assert not selection.matches({**item, 'item_type': 'line'}, cutoff)
    assert not selection.matches({**item, 'payload': {**evidence, 'screen_eligible': False}}, cutoff)
    candidates = [
        {'symbol': 'b', 'state': 'pullback-confirmed', 'score': 50, 'evidence': {'maturity_rank': 2}},
        {'symbol': 'a', 'state': 'pullback-observation', 'score': 80, 'evidence': {'maturity_rank': 1}},
        {'symbol': 'c', 'state': 'pullback-confirmed', 'score': 70, 'evidence': {}},
    ]
    expected = ['a', 'b', 'c'] if selection.low_base else ['c', 'b', 'a']
    assert [c['symbol'] for c in sorted(candidates, key=selection.rank_key)] == expected


def test_reference_constraints_are_owned_by_descriptor():
    reference = get_strategy('deep-drawdown-consolidation')
    reference.validate_cutoff(reference.valid_from)
    with pytest.raises(ValueError, match='reference is only available'):
        reference.validate_cutoff(reference.valid_from - timedelta(days=1))
    assert not reference.allows_symbol(next(iter(reference.excluded_symbols)))
    assert reference.allows_symbol('600001.SH')
    assert get_strategy('long-consolidation-platform').allows_symbol('002137.SZ')


def test_new_shape_descriptor_does_not_require_strategy_id_branches():
    original = get_strategy('low-base-platform-pullback')
    renamed = replace(original, _definition={**original.definition, 'strategy_id': 'new-shape'})
    assert renamed.selection is original.selection
    assert renamed.execution == 'shape'
    assert renamed.analysis_config == original.analysis_config
