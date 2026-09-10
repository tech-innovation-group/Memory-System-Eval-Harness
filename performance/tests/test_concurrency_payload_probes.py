"""Contracts for the configurable concurrency and payload probes."""

from __future__ import annotations

import json
from unittest.mock import patch

from performance.targets.echomem.orchestrator.probes import run_configured_probes
from performance.targets.echomem.orchestrator.report import render_objective_suite_html
from performance.targets.echomem.probes.concurrency_topology import (
    _jain,
    _percentile,
    _summary,
)


def test_concurrency_summary_keeps_full_denominator_and_errors() -> None:
    rows = [
        {"tenant_id": "a", "http_status": 200, "elapsed_ms": 10.0},
        {"tenant_id": "a", "http_status": 429, "elapsed_ms": 20.0},
        {"tenant_id": "b", "http_status": None, "elapsed_ms": 30.0},
    ]
    result = _summary(rows, 1.0)
    assert result["offered"] == 3
    assert result["completed_2xx"] == 1
    assert result["http_counts"] == {"200": 1, "429": 1, "transport": 1}
    assert result["p95_ms"] == 30.0


def test_generated_config_preserves_engines_and_respects_share_constraints():
    from performance.targets.echomem.prepare_concurrency_configs import configure
    source = {"model": {"embedding": {"model": "qwen3.7-text-embedding-flash"}},
              "engine": {"enabled": ["atomic_engine"]},
              "scheduling": {"commit": {"executor_workers": 5, "gate_workers": 3}}}
    for level in (16, 32, 64, 128):
        config = configure(source, level)
        scheduling = config["scheduling"]
        assert scheduling["http"]["max_workers"] >= 4 * scheduling["retrieval"]["admission_permits"]
        assert scheduling["fanout"]["executor_workers"] > scheduling["fanout"]["engine_max_inflight"]
        assert scheduling["commit"]["executor_workers"] == 5
        assert config["engine"] == source["engine"]
        assert config["recall"]["max_inflight"] == level
    assert "recall" not in source


def test_session_gate_caps_actual_parallel_calls():
    import threading
    import time
    from performance.targets.echomem.probes.concurrency_topology import _bounded_call, _run_calls
    gate = threading.Semaphore(2)
    mutex = threading.Lock()
    state = {"active": 0, "peak": 0}

    def operation():
        time.sleep(.02)
        return {"ok": True}

    rows, _ = _run_calls([lambda: _bounded_call(operation, gate, state, mutex)] * 16, 16)
    assert len(rows) == 16
    assert state["peak"] == 2
    assert state["active"] == 0


def test_drain_does_not_treat_missing_archive_as_completed():
    from performance.targets.echomem.probes.concurrency_topology import _drain
    result = _drain([{"operation": "commit", "http_status": 202}], {}, 0)
    assert result["accepted_202"] == 1
    assert result["pending"] == 1
    assert result["completed"] == 0
    assert not result["drained"]


def test_fairness_and_percentile_are_deterministic() -> None:
    assert _jain([1.0, 1.0, 1.0, 1.0]) == 1.0
    assert _jain([1.0, 0.0]) == 0.5
    assert _percentile([4.0, 1.0, 3.0, 2.0], .95) == 4.0


def test_report_renders_topology_and_payload_evidence() -> None:
    topology = {
        "matrix": [{
            "level": 16, "topology": "many-users-one-session-serial",
            "actual_users": 8, "requested_users": 16, "completed_2xx": 7,
            "offered": 8, "p95_ms": 123.0, "throughput_rps_2xx": 4.2,
            "tenant_throughput_jain": .99, "http_counts": {"200": 7, "429": 1},
        }]
    }
    boundary = {
        "cases": [{"api": "search", "encoding": "binary", "content_bytes": 1024,
                   "wire_bytes": 1024, "http_status": 415,
                   "reason_code": "UNSUPPORTED_MEDIA_TYPE", "elapsed_ms": 2.0}],
        "long_commit": {"requested_chars": 1048576},
        "mcp_add_memory": {"status": "PASS"},
    }
    result = {
        "profiles": [{
            "name": "local",
            "objectives": [],
            "concurrency_topology": {"checks": [{"detail": json.dumps(topology)}]},
            "payload_boundary": {"checks": [{"detail": json.dumps(boundary)}]},
        }]
    }
    page = render_objective_suite_html(result)
    assert "many-users-one-session-serial" in page
    assert "UNSUPPORTED_MEDIA_TYPE" in page
    assert "1048576" in page


def test_orchestrator_runs_both_new_probes(tmp_path) -> None:
    tenants = tmp_path / "tenants.json"
    tenants.write_text(json.dumps({"tenants": [{"tenant_id": "t1", "auth_key": "secret"}]}))
    profile = {
        "tenant_config": str(tenants),
        "concurrency_topology": {"levels": [16, 32]},
        "payload_boundary": {"sizes_bytes": [0, 1048576]},
    }
    calls = []

    def fake_run(params, **kwargs):
        calls.append((kwargs["scene"], params))
        return {"status": "PASS", "checks": []}, {"status": "PASS"}

    with patch("performance.targets.echomem.orchestrator.probes.run_configured_probe",
               side_effect=fake_run):
        artifacts, _ = run_configured_probes(
            profile, base_url="http://127.0.0.1:8010", suite_dir=tmp_path,
            auth_headers={}, tenant_config=json.loads(tenants.read_text()), quick=True,
        )

    assert [name for name, _ in calls] == ["payload_boundary.py", "concurrency_topology.py"]
    assert calls[0][1]["sizes_bytes"] == [0, 1048576]
    assert calls[1][1]["levels"] == [16, 32]
    assert set(artifacts) == {"payload_boundary", "concurrency_topology"}
