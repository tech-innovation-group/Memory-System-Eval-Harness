from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from performance.targets.echomem.acceptance import fault_matrix
from performance.targets.echomem.acceptance.fault_evidence import case_counts, control_receipt, matrix_counts
from performance.targets.echomem.probes._client import _server_observability
from performance.targets.echomem.acceptance.main_metric_report import redacted_report, render


def summary():
    return {"sent": 10, "not_sent": 0, "success": 10, "transport_or_http_errors": 0, "p95_s": 1,
            "http_reason_counts": {}}


def case(mode="reject", target=0):
    pairs = [{"identity_index": i, "before": summary(), "during": summary()} for i in range(4)]
    recovered = deepcopy(pairs)
    if mode == "reject":
        pairs[target]["during"].update(success=0, transport_or_http_errors=10,
                                      http_reason_counts={"TEST_FAULT_INJECTED": 10})
    else:
        pairs[target]["during"]["p95_s"] = 2
    return {"repeat": 1, "target_index": target, "fault_type": mode, "status": "MEASURED",
            "pairs": pairs, "recovery_pairs": recovered, "target_effect_observed": True,
            "control_evidence": {"enabled": {"active_match": True, "delay_ms": 1000},
                                 "at_end": {"active_match": True, "delay_ms": 1000},
                                 "disabled": {"cleared": True}, "window_covered": True}}


def matrix():
    return {"repeats": 1, "expected_cases": 8, "measured_cases": 8, "delay_ms": 1000,
            "cases": [case(mode, target) for mode in ("reject", "delay") for target in range(4)]}


def test_public_fault_enum_is_extracted_without_exporting_messages():
    result = _server_observability({"error": "TEST_FAULT_INJECTED", "message": "PRIVATE"}, {})
    assert result["reason_code"] == "TEST_FAULT_INJECTED"
    assert "PRIVATE" not in json.dumps(result)
    assert not _server_observability({"error": "PRIVATE"}, {}).get("reason_code")
    assert not _server_observability({"error": {"code": "PRIVATE"}}, {}).get("reason_code")


def test_receipt_requires_matching_tenant_type_and_active_fault():
    body = {"status": "enabled", "fault": {"tenant_id": "PRIVATE", "fault_type": "reject",
            "active": True, "remaining_s": 45, "delay_ms": 1000}}
    reply = {"status": "PASS", "status_code": 200, "body": json.dumps(body)}
    result = control_receipt(reply, "PRIVATE", "reject")
    assert result["active_match"] and "PRIVATE" not in json.dumps(result)
    assert not control_receipt(reply, "wrong", "reject")["active_match"]
    assert not control_receipt(reply, "PRIVATE", "delay")["active_match"]
    for content in ("{}", "[]", "invalid", '{"enabled":true}', '{"enabled":false,"fault":null}'):
        observed = control_receipt({**reply, "body": content}, "PRIVATE", "reject")
        assert not observed["active_match"] and not observed["cleared"]
    cleared = control_receipt({**reply, "body": '{"enabled":true,"fault":null}'}, "PRIVATE", "reject")
    assert cleared["cleared"]


def test_generic_503_is_not_proof_of_injected_rejection():
    value = case()
    value["pairs"][0]["during"]["http_reason_counts"] = {}
    result = case_counts(value, delay_ms=1000)
    assert result["status"] == "INCONCLUSIVE"
    assert result["target_effect_observed"] is False


@pytest.mark.parametrize("mutate", ["window", "control", "baseline", "missing", "duplicate", "recovery"])
def test_saved_measured_status_cannot_hide_missing_evidence(mutate):
    value = case()
    if mutate == "window":
        value["control_evidence"]["window_covered"] = False
    elif mutate == "control":
        value.pop("control_evidence")
    elif mutate == "baseline":
        value["pairs"][2]["before"]["success"] = 9
    elif mutate == "missing":
        value["pairs"].pop()
    elif mutate == "duplicate":
        value["pairs"][3]["identity_index"] = 2
    else:
        value["recovery_pairs"].pop()
    assert case_counts(value, delay_ms=1000)["status"] == "INCONCLUSIVE"


def test_slow_fault_requires_matching_delay_configuration():
    value = case("delay")
    assert case_counts(value, delay_ms=1000)["status"] == "MEASURED"
    assert case_counts(value, delay_ms=0)["status"] == "INCONCLUSIVE"
    assert case_counts(value, delay_ms=500)["status"] == "INCONCLUSIVE"


def test_missing_and_duplicate_cases_do_not_complete_matrix():
    value = matrix()
    assert matrix_counts(value)["status"] == "MEASURED"
    value["cases"][-1] = deepcopy(value["cases"][0])
    result = matrix_counts(value)
    assert result["status"] == "INCONCLUSIVE"
    assert result["unexecuted_cases"] == result["duplicate_cases"] == 1
    assert result["measured_cases"] == 6


def test_failed_control_case_errors_are_not_excluded_from_total():
    value = matrix()
    value["cases"][0].pop("control_evidence")
    value["cases"][0]["pairs"][1]["during"].update(success=7, transport_or_http_errors=3)
    result = matrix_counts(value)
    assert result["status"] == "INCONCLUSIVE"
    assert result["known_bystander_http_errors"] == result["bystander_http_errors"] == 3
    value["cases"].pop()
    result = matrix_counts(value)
    assert result["known_bystander_http_errors"] == 3
    assert result["bystander_http_errors"] is None


def test_large_bystander_delay_is_observed_without_an_slo_gate():
    value = case()
    value["pairs"][1]["during"]["p95_s"] = 100
    result = case_counts(value, delay_ms=1000)
    assert result["status"] == "MEASURED"
    assert result["worst_bystander_p95_change_percent"] == 9900


def test_historical_matrix_without_receipts_renders_and_preserves_numbers():
    value = matrix()
    for item in value["cases"]:
        item.pop("control_evidence")
    public = redacted_report({"metrics": {"M2": value}}, {"levels": []})
    assert public["M2"]["verification"]["recorded_cases"] == 8
    assert public["M2"]["verification"]["measured_cases"] == 0
    assert "逐用例、逐租户" in render(public)


def test_control_receipt_public_projection_never_exports_private_fields():
    value = matrix()
    for item in value["cases"]:
        item["control_evidence"]["body"] = "PRIVATE-CONTROL"
        item["control_evidence"]["enabled"]["tenant"] = "PRIVATE-TENANT"
    public = redacted_report({"metrics": {"M2": value}}, {"levels": []})
    assert "PRIVATE" not in json.dumps(public) + render(public)


def install_runner_stubs(monkeypatch):
    actors = [SimpleNamespace(client=SimpleNamespace(tenant_id=f"T{i}")) for i in range(4)]
    monkeypatch.setattr(fault_matrix, "_load_actors", lambda *args: (actors, {"status": "PASS"}))
    monkeypatch.setattr(fault_matrix, "inspect_container", lambda *args: {
        "State": {"Running": True}, "HostConfig": {"NanoCpus": 4_000_000_000, "Memory": 8_589_934_592}})
    monkeypatch.setenv("ECHOMEM_TEST_CONTROL_TOKEN", "PRIVATE")
    faults, measurements = {}, []

    def control(config, *, action, target_tenant, fault_type="reject", **kwargs):
        if action == "enable":
            faults[target_tenant] = fault_type
        elif action == "disable":
            faults.pop(target_tenant, None)
        active = faults.get(target_tenant)
        return {"status": "PASS", "status_code": 200, "body": json.dumps({"enabled": True, "fault": {
            "tenant_id": target_tenant, "fault_type": active, "active": True, "remaining_s": 45,
            "delay_ms": 1000} if active else None})}

    def measure(actors, **kwargs):
        measurements.append({**kwargs, "active": dict(faults)})
        rows = []
        for i in range(4):
            mode = faults.get(f"T{i}")
            rows.append({"op": "read", "identity_index": i, "sent": True, "success": mode != "reject",
                         "elapsed_s": 2 if mode == "delay" else 1,
                         "http_status": 503 if mode == "reject" else 200,
                         "reason_code": "TEST_FAULT_INJECTED" if mode == "reject" else ""})
        return {"rows": rows}

    monkeypatch.setattr(fault_matrix, "control", control)
    monkeypatch.setattr(fault_matrix, "measure", measure)
    return faults, measurements


def test_runner_locks_a_separate_before_during_after_for_every_target(monkeypatch, tmp_path):
    faults, measurements = install_runner_stubs(monkeypatch)
    result = fault_matrix.run(base_url="http://test", seed_directory=tmp_path, output=tmp_path / "run",
                              container="dedicated", repeats=1, phase_duration_s=15, recovery_duration_s=15)
    assert result["measured_cases"] == 8 and not faults
    assert len(measurements) == 24
    for offset in range(0, 24, 3):
        before, during, after = measurements[offset:offset + 3]
        assert not before["active"] and len(during["active"]) == 1 and not after["active"]
        assert before["seed"] == during["seed"] == after["seed"]
        assert before["isolate_read_workers"] and during["isolate_read_workers"] and after["isolate_read_workers"]
    assert len(list((tmp_path / "run").glob("*/attempt-*/baseline.json"))) == 8
    assert "PRIVATE" not in json.dumps(result)


def test_unverified_initial_clear_stops_before_workload(monkeypatch, tmp_path):
    _, measurements = install_runner_stubs(monkeypatch)
    monkeypatch.setattr(fault_matrix, "control", lambda *args, **kwargs: {"status": "PASS", "body": "{}"})
    with pytest.raises(RuntimeError, match="fault-free baseline"):
        fault_matrix.run(base_url="http://test", seed_directory=tmp_path, output=tmp_path / "run",
                         container="dedicated", repeats=1)
    assert not measurements
    assert json.loads((tmp_path / "run" / "report.json").read_text())["status"] == "EXECUTION_ERROR"


def test_generator_misses_are_retained_and_prevent_a_complete_case():
    value = case()
    value["pairs"][1]["during"]["not_sent"] = 5
    result = case_counts(value, delay_ms=1000)
    assert result["status"] == "INCONCLUSIVE"
    assert result["rows"][1]["during"]["not_sent"] == 5


def test_slow_tenant_cannot_exhaust_bystander_client_threads(monkeypatch):
    import threading
    from performance.targets.echomem.acceptance import capacity_load

    release = threading.Event()
    plan = [(0, "read", 0, i) for i in range(20)] + [(0, "read", 1, 0)]
    monkeypatch.setattr(capacity_load, "arrival_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(capacity_load, "assess_retrieval", lambda *args: {"quality_ok": True})

    def request(index, *args, **kwargs):
        if index == 0:
            release.wait(timeout=2)
        else:
            release.set()
        return SimpleNamespace(payload={}, status_code=200, reason_code="")

    corpus = {"recall_queries": [{"query": "where", "id": "q", "query_type": "recall"}]}
    actors = [SimpleNamespace(tenant_index=i, user_index=i, corpus=corpus,
                              client=SimpleNamespace(agent_id="unit", request=lambda *a, i=i, **k: request(i, *a, **k)))
              for i in range(2)]
    try:
        result = capacity_load.measure(actors, duration_s=.2, isolate_read_workers=True)
    finally:
        release.set()
    assert result["read_worker_isolation"] == "per_identity"
    bystander = [r for r in result["rows"] if r["identity_index"] == 1]
    assert len(bystander) == 1 and bystander[0]["sent"] and bystander[0]["success"]
    assert any(r.get("error") == "generator_saturated" for r in result["rows"] if r["identity_index"] == 0)
