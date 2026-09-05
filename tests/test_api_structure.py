from stock_harness.api import create_app


EXPECTED_API_OPERATIONS = {
    ("GET", "/api/health", "200"),
    ("GET", "/api/screener/strategies", "200"),
    ("POST", "/api/screener/runs", "202"),
    ("GET", "/api/screener/runs", "200"),
    ("GET", "/api/screener/runs/{run_id}", "200"),
    ("DELETE", "/api/screener/runs/{run_id}", "204"),
    ("GET", "/api/screener/runs/{run_id}/candidates", "200"),
    ("GET", "/api/analysis/runs/{run_id}", "200"),
    ("GET", "/api/analysis/trend/{symbol}/runs", "200"),
    ("POST", "/api/analysis/trend/recalculate", "200"),
    ("GET", "/api/analysis/trend/{symbol}", "200"),
    ("GET", "/api/analysis/ai/{symbol}", "200"),
    ("GET", "/api/analysis/ai/{symbol}/reports", "200"),
    ("POST", "/api/analysis/ai", "201"),
    ("GET", "/api/ai/codex/status", "200"),
    ("POST", "/api/ai/conversations", "201"),
    ("GET", "/api/ai/conversations", "200"),
    ("GET", "/api/ai/conversations/{conversation_id}", "200"),
    ("PATCH", "/api/ai/conversations/{conversation_id}", "200"),
    ("DELETE", "/api/ai/conversations/{conversation_id}", "204"),
    ("POST", "/api/ai/conversations/{conversation_id}/turns", "202"),
    ("GET", "/api/ai/turns/{turn_id}/events", "200"),
    ("GET", "/api/ai/turns/{turn_id}/context", "200"),
    ("POST", "/api/ai/turns/{turn_id}/cancel", "202"),
    ("POST", "/api/ai/turns/{turn_id}/retry", "202"),
    ("POST", "/api/trend-reviews", "201"),
    ("GET", "/api/trend-reviews", "200"),
    ("GET", "/api/trend-reviews/{review_id}", "200"),
    ("PUT", "/api/trend-reviews/{review_id}", "200"),
    ("POST", "/api/trend-reviews/{review_id}/snapshot", "200"),
    ("GET", "/api/workspace-context", "200"),
    ("POST", "/api/workspace-context", "202"),
    ("GET", "/api/update-status", "200"),
    ("POST", "/api/update/refresh", "202"),
    ("GET", "/api/runtime-events", "200"),
    ("POST", "/api/runtime-events", "202"),
    ("GET", "/api/intraday/status", "200"),
    ("POST", "/api/intraday/subscription", "200"),
    ("GET", "/api/intraday-bars", "200"),
    ("POST", "/api/intraday/refresh", "200"),
    ("GET", "/api/instruments", "200"),
    ("GET", "/api/futures/search-facets", "200"),
    ("GET", "/api/futures/coverage", "200"),
    ("GET", "/api/futures/continuous/{symbol}", "200"),
    ("GET", "/api/custom-groups", "200"),
    ("POST", "/api/custom-groups", "201"),
    ("GET", "/api/custom-groups/{group_id}", "200"),
    ("PUT", "/api/custom-groups/{group_id}", "200"),
    ("DELETE", "/api/custom-groups/{group_id}", "204"),
    ("GET", "/api/custom-indices", "200"),
    ("POST", "/api/custom-indices", "201"),
    ("GET", "/api/custom-indices/{index_id}", "200"),
    ("PUT", "/api/custom-indices/{index_id}", "200"),
    ("DELETE", "/api/custom-indices/{index_id}", "204"),
    ("POST", "/api/custom-indices/{index_id}/rebuild", "200"),
    ("GET", "/api/instruments/{symbol}", "200"),
    ("GET", "/api/market-snapshots", "200"),
    ("GET", "/api/instrument-tags", "200"),
    ("PUT", "/api/instruments/{symbol}/tags", "200"),
    ("GET", "/api/instruments/{symbol}/daily-bars", "200"),
    ("GET", "/api/boards/{symbol}/members", "200"),
    ("GET", "/api/instruments/{symbol}/members", "200"),
    ("GET", "/api/instruments/{symbol}/boards", "200"),
}


def test_api_method_path_and_success_status_contract() -> None:
    operations = {
        (method.upper(), path, _success_status(operation["responses"]))
        for path, methods in create_app().openapi()["paths"].items()
        for method, operation in methods.items()
    }

    assert operations == EXPECTED_API_OPERATIONS


def _success_status(responses: dict[str, object]) -> str:
    return next(code for code in responses if code.startswith("2"))
