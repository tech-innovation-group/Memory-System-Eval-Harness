import pytest

from performance.targets.echomem.extended_profile import expand_extended_profile


def test_explicit_extension_includes_full_contract_without_mutating_input():
    source = {"extended_load_tests": True, "payload_boundary": {"mcp_base_url": "http://localhost:8001"}}
    result = expand_extended_profile(source, ["M1", "M2", "M3"])
    assert "concurrency_topology" not in source
    assert result["concurrency_topology"]["levels"] == [16, 64]
    assert result["concurrency_topology"]["stop_after_boundary"] is False
    assert len(result["payload_boundary"]["sizes_bytes"]) == 7
    assert result["payload_boundary"]["commit_content_chars"] == 1048576


@pytest.mark.parametrize("override", [
    {"payload_boundary": {}},
    {"concurrency_topology": {"levels": [16]}},
    {"concurrency_topology": {"stop_after_boundary": True}},
    {"payload_boundary": {"skip_mcp": True}},
    {"payload_boundary": {"skip_long_commit": True}},
    {"payload_boundary": {"sizes_bytes": [1024]}},
    {"payload_boundary": {"commit_content_chars": 100}},
])
def test_extension_refuses_partial_contract(override):
    source = {"extended_load_tests": True, "payload_boundary": {"mcp_base_url": "http://localhost:8001"}}
    for key, value in override.items():
        source[key] = {**source.get(key, {}), **value} if value else value
    with pytest.raises(ValueError):
        expand_extended_profile(source, ["M1", "M2", "M3"])


def test_normal_profiles_unchanged_and_single_metric_not_mislabeled():
    assert expand_extended_profile({"name": "local"}, ["M1"]) == {"name": "local"}
    with pytest.raises(ValueError):
        expand_extended_profile({"extended_load_tests": True}, ["M1"])
