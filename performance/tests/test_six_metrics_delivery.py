"""Delivery contracts only; local fixtures are not performance evidence."""

import json
import threading
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import pytest

from performance.targets.echomem.acceptance.preflight import config_digest, run_preflight, valid_model_response
from performance.targets.echomem.acceptance.readiness import check_readiness
from performance.targets.echomem.acceptance.six_metrics import configure_profile
from performance.targets.echomem.main import _resolve_profile, main
from performance.targets.echomem.orchestrator.suites import six_metric_cases


@pytest.fixture
def delivery_profile(tmp_path):
    tenants = tmp_path / "tenants.json"
    tenants.write_text(json.dumps({"tenants": [{"tenant_id": f"t{i}", "auth_key": f"unit-only-{i}"}
                                               for i in range(32)]}))
    return {"name": "4U8G", "base_url": "http://localhost:8010",
            "resource_container": "dedicated-unit-test", "tenant_config": str(tenants),
            "preflight_config": str(tmp_path / "config.json"),
            "commit_recovery": {"allow_container_restart": True}}


def test_capacity_is_extensible_and_priority_load_is_paired():
    cases = {c["label"]: c for c in six_metric_cases([2, 4, 8, 16, 32, 64])}
    assert cases["capacity-64"]["tenants"] == 64
    assert cases["capacity-64"]["search_rps"] == 64
    for key in ("search_rps", "search_workers", "tenants"):
        assert cases["recall-baseline"][key] == cases["search-priority-blackbox"][key]
    assert "soak" not in cases


@pytest.mark.parametrize("levels", [[4], [4, 2], [2, 2], [True, 4], [2, 3.5], []])
def test_invalid_capacity_levels_rejected(levels):
    with pytest.raises(ValueError):
        six_metric_cases(levels)


def test_normal_profile_prepares_all_six(delivery_profile):
    profile = configure_profile(delivery_profile)
    assert len(profile["fairness_expectations"]["tenant_ids"]) == 4
    assert profile["capacity_levels"][-1] == 32
    assert profile["commit_recovery"]["require_accepted_202"] is True
    assert "unit-only" not in json.dumps(profile)


def test_capacity_identity_shortage_fails_before_seed(delivery_profile):
    with pytest.raises(ValueError, match="64 independent"):
        configure_profile({**delivery_profile, "capacity_levels": [2, 64]})


def test_duplicate_keys_cannot_claim_independent_tenants(delivery_profile):
    path = Path(delivery_profile["tenant_config"])
    payload = json.loads(path.read_text())
    payload["tenants"][1]["auth_key"] = payload["tenants"][0]["auth_key"]
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="independent"):
        configure_profile(delivery_profile)


def test_recovery_cannot_target_unverified_container(delivery_profile):
    delivery_profile["commit_recovery"]["container"] = "another-container"
    with pytest.raises(ValueError, match="must match"):
        configure_profile(delivery_profile)


def test_recovery_requires_explicit_opt_in(delivery_profile):
    delivery_profile["commit_recovery"].clear()
    with pytest.raises(ValueError, match="allow_container_restart"):
        configure_profile(delivery_profile)


def test_profile_env_paths_expand_before_resolving(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILE_TENANTS", "identities.json")
    result = _resolve_profile({"tenant_config": "${PROFILE_TENANTS}"}, tmp_path / "profile.json")
    assert result["tenant_config"] == str(tmp_path / "identities.json")


@pytest.mark.parametrize("kind,payload", [
    ("llm", {}), ("llm", {"error": "provider down"}),
    ("llm", {"choices": []}), ("embedding", {"data": []}),
    ("embedding", {"data": [{"embedding": [float("nan")]}]}),
    ("embedding", {"data": [{"embedding": [True]}]}),
])
def test_http_success_is_not_model_success(kind, payload):
    assert not valid_model_response(kind, payload)


def test_model_preflight_requires_both_providers(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"model": {"api_base": "http://unit.invalid", "model": "llm"}}))
    with patch("performance.targets.echomem.acceptance.preflight.probe_endpoint") as probe:
        result = run_preflight(path, required_kinds=("llm", "embedding"))
    assert not result["ok"]
    assert "embedding" in result["error"]
    probe.assert_not_called()


def test_inline_keys_excluded_from_config_digest():
    assert config_digest([{"model": "x", "_api_key": "a"}]) == config_digest([{"model": "x", "_api_key": "b"}])


def docker_result():
    return {"HostConfig": {"NanoCpus": 4_000_000_000, "Memory": 8 * 1024**3},
            "State": {"Running": True}, "Config": {"Env": ["PRIVATE=never-export"]}}


def test_readiness_missing_token_never_calls_protected_api(delivery_profile, monkeypatch):
    monkeypatch.delenv("ECHOMEM_TEST_CONTROL_TOKEN", raising=False)
    with patch("performance.targets.echomem.acceptance.readiness.inspect_container", return_value=docker_result()), \
         patch("performance.targets.echomem.acceptance.readiness._get", return_value=(200, {"ready": True})) as get:
        result = check_readiness(delivery_profile)
    assert not result["ok"]
    assert get.call_count == 2
    assert "never-export" not in json.dumps(result)


@pytest.mark.parametrize("fault_status,fault_payload,ok", [(200, {"faults": {}}, True),
                                                          (404, {}, False), (200, {}, False),
                                                          (200, {"faults": {"t0": {}}}, False)])
def test_readiness_requires_contract_and_clean_fault_state(delivery_profile, monkeypatch, fault_status, fault_payload, ok):
    monkeypatch.setenv("ECHOMEM_TEST_CONTROL_TOKEN", "unit-test-token")
    with patch("performance.targets.echomem.acceptance.readiness.inspect_container", return_value=docker_result()), \
         patch("performance.targets.echomem.acceptance.readiness._get", side_effect=[
             (200, {"ready": True}), (200, {}), (fault_status, fault_payload), (200, {"rows": []})]):
        result = check_readiness(delivery_profile)
    assert result["ok"] is ok
    assert "unit-test-token" not in json.dumps(result)


def test_readiness_blocks_cross_origin_token(delivery_profile, monkeypatch):
    monkeypatch.setenv("ECHOMEM_TEST_CONTROL_TOKEN", "unit-test-token")
    delivery_profile["fault_isolation"] = {"endpoint": "http://other.invalid/control"}
    with patch("performance.targets.echomem.acceptance.readiness.inspect_container", return_value=docker_result()), \
         patch("performance.targets.echomem.acceptance.readiness._get", return_value=(200, {"rows": []})) as get:
        result = check_readiness(delivery_profile)
    assert not result["ok"]
    assert all("other.invalid" not in c.args[0] for c in get.call_args_list)


def test_check_only_does_not_run_load_or_probes(delivery_profile, tmp_path):
    config = tmp_path / "profiles.json"
    config.write_text(json.dumps({"profiles": [delivery_profile]}))
    with patch("performance.targets.echomem.acceptance.readiness.check_readiness", return_value={"ok": True}), \
         patch("performance.targets.echomem.acceptance.preflight.run_preflight", return_value={"ok": True}), \
         patch("performance.targets.echomem.main.run_suite") as run, \
         patch("performance.targets.echomem.main.run_configured_probes") as probe:
        code = main(["--profiles", str(config), "--out-dir", str(tmp_path / "check"), "--six-metrics", "--check-only"])
    assert code == 0
    run.assert_not_called()
    probe.assert_not_called()
    result = json.loads((tmp_path / "check/4U8G/readiness.json").read_text())
    assert len(result["cases"]) == 9 and result["fault_cases"] == 24


def test_existing_output_cannot_be_overwritten(delivery_profile, tmp_path):
    config = tmp_path / "profiles.json"
    config.write_text(json.dumps({"profiles": [delivery_profile]}))
    output = tmp_path / "existing"
    output.mkdir()
    evidence = output / "previous.json"
    evidence.write_text("keep")
    with pytest.raises(SystemExit) as exc:
        main(["--profiles", str(config), "--out-dir", str(output), "--six-metrics"])
    assert exc.value.code == 2 and evidence.read_text() == "keep"


def test_model_gate_failure_does_not_run_recovery(delivery_profile, tmp_path):
    config = tmp_path / "profiles.json"
    config.write_text(json.dumps({"profiles": [delivery_profile]}))
    suite = {"runs": [], "resource_evidence": {"cpus": 4, "memory_bytes": 8 * 1024**3},
             "preflight": {"ok": False, "error": "provider unavailable"}}
    with patch("performance.targets.echomem.main.run_suite", return_value=suite), \
         patch("performance.targets.echomem.main.run_configured_probes") as probe:
        code = main(["--profiles", str(config), "--out-dir", str(tmp_path / "run"), "--six-metrics"])
    assert code == 2
    probe.assert_not_called()
    result = json.loads((tmp_path / "run/4U8G/six-metrics.json").read_text())
    assert len(result["checks"]) == 6 and result["status"] == "INCONCLUSIVE"


@pytest.mark.parametrize("elapsed,valid", [(299, True), (300, False), (301, False), (float("nan"), False)])
def test_expired_fault_window_is_not_valid_isolation_evidence(elapsed, valid):
    from performance.targets.echomem.probes.fault_isolation import fault_window_covered
    assert fault_window_covered("http://localhost/api/inspect/test-control/fault", 300, elapsed) is valid


def test_fault_generator_uses_separate_tenant_pools_and_fixed_arrivals():
    from performance.targets.echomem.probes.fault_isolation import sample_search
    pool_names = {"a": set(), "b": set()}

    def client(tenant):
        def search(*args, **kwargs):
            pool_names[tenant].add(threading.current_thread().name.split("_")[0])
            return SimpleNamespace(status_code=200, payload={}, error="")
        return SimpleNamespace(search=search)

    with patch("performance.targets.echomem.probes.fault_isolation.recall_quality",
               return_value={"quality_ok": True, "degraded": False, "degraded_reasons": []}):
        result = sample_search({t: client(t) for t in pool_names}, {"a": "s1", "b": "s2"},
                               count=1, workers=4, timeout_s=1, phase="unit-only", queries={"a": "a", "b": "b"},
                               duration_s=.02, rps_per_tenant=100, target_tenant="a", target_rps=100)
    assert pool_names["a"].isdisjoint(pool_names["b"])
    assert result["independent_pools"]
    for row in result["by_tenant"].values():
        assert row["submitted"] == 2
        assert [r["scheduled_offset_s"] for r in row["rows"]] == pytest.approx([0, .01])


def test_fault_phase_must_fit_ttl_and_minimum_samples(delivery_profile):
    delivery_profile["fault_isolation"] = {"phase_duration_s": 300, "duration_s": 300}
    with pytest.raises(ValueError, match="fault phases"):
        configure_profile(delivery_profile)
