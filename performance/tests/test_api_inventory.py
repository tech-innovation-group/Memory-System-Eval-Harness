from performance.targets.echomem.acceptance.api_inventory import discover_routes, compare_observed


def test_static_inventory_does_not_execute_code_or_claim_complete(tmp_path):
    path = tmp_path / 'handlers.py'
    path.write_text('''raise RuntimeError("must not execute")
_UNSAFE_ROUTE_CONTRACTS: dict = {("POST", "/api/sessions/{session}/commit"): "tenant_mutation"}
def _dispatch_get(path):
    if path in {"/health", "/ready"}: pass
    parts = path.split("/")
    if parts[0] == "dynamic": pass
    if path == "/after_dynamic": pass
''')
    inventory = discover_routes(path)
    assert len(inventory['routes']) == 4
    assert inventory['unresolved']
    result = compare_observed(inventory, [{
        'endpoint': 'http/POST /api/sessions/private-id/commit',
        'observed_completions': 2, 'status_counts': {'202': 2}}])
    assert result['observed_candidates'] == 1
    assert result['all_product_apis_covered'] is False
    assert 'private-id' not in str(result)
    assert not any(r['business_contract_verified'] for r in result['routes'])


def test_http_method_and_path_must_both_match(tmp_path):
    path = tmp_path / 'handlers.py'
    path.write_text('def _dispatch_get(path):\n    if path == "/health": pass\n')
    result = compare_observed(discover_routes(path), [
        {'endpoint': 'http/POST /health', 'observed_completions': 3, 'status_counts': {'404': 3}}])
    assert result['observed_candidates'] == 0
