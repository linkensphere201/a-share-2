import pytest
from stock_harness.screener_strategies import STRATEGIES, get_strategy, strategy_definitions


def test_catalog_parameters_are_isolated_and_all_shapes_have_adapters():
    assert len(STRATEGIES) == 7
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
