"""Contracts for the configurable concurrency and payload probes."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from performance.targets.echomem.acceptance.observation import (
    evaluate_observation,
    write_observation_report,
)
from performance.targets.echomem.orchestrator.probes import run_configured_probes
from performance.targets.echomem.orchestrator.report import render_objective_suite_html
from performance.targets.echomem.probes.concurrency_topology import (
    _capacity_levels,
    _commit_call,
    _generator_workers,
    _jain,
    _percentile,
    _search_call,
    _summary,
)


def test_concurrency_summary_keeps_full_denominator_and_errors() -> None:
    rows = [
        {"tenant_id": "a", "http_status": 200, "elapsed_ms": 10.0},
        {"tenant_id": "a", "http_status": 429, "elapsed_ms": 20.0},
        {"tenant_id": "b", "http_status": None, "elapsed_ms": 30.0,
         "transport_error_type": "transport"},
    ]
    result = _summary(rows, 1.0)
    assert result["offered"] == 3
    assert result["completed_2xx"] == 1
    assert result["http_counts"] == {"200": 1, "429": 1, "transport": 1}
    assert result["p95_ms"] == 30.0
    assert result["operational_failures"] == 2
    assert result["boundary_reasons"] == {"429": 1, "transport": 1}


def test_fairness_and_percentile_are_deterministic() -> None:
    assert _jain([1.0, 1.0, 1.0, 1.0]) == 1.0
    assert _jain([1.0, 0.0]) == 0.5
    assert _percentile([4.0, 1.0, 3.0, 2.0], .95) == 4.0
    assert _capacity_levels([16, 32], 128) == [16, 32, 64, 128]
    assert _generator_workers(32, 16, 1, 2) == 32
    assert _generator_workers(32, 8, 4, 1) == 32


def test_search_2xx_requires_expected_fact_for_quality() -> None:
    sample = {
        "id": "q1", "fact_id": "f1", "query": "Where is the launch?",
        "query_type": "recall", "aliases": ["Hangzhou"], "match_policy": "any",
    }

    class Client:
        def search(self, *_args):
            return SimpleNamespace(
                status_code=200, elapsed_s=0.02,
                payload={"result": {"items": [{"content": "Beijing"}]}},
                reason_code="", transport_error_type="",
            )

    row = _search_call(Client(), "tenant-a", "session-a", sample, 1, "small")()
    assert row["http_status"] == 200
    assert row["quality_observed"] is True
    assert row["quality_ok"] is False
    assert row["recall_hit"] is False


def test_commit_202_is_polled_to_terminal_completion() -> None:
    class Client:
        def add_message(self, *_args):
            return SimpleNamespace(status_code=201, reason_code="", transport_error_type="")

        def commit(self, *_args, **_kwargs):
            return SimpleNamespace(
                status_code=202, payload={"archive_id": "archive-1"},
                reason_code="", transport_error_type="",
            )

        def commit_status(self, *_args):
            return SimpleNamespace(payload={"status": "completed"})

    row = _commit_call(Client(), "tenant-a", "session-a", "content", 1)()
    assert row["accepted"] is True
    assert row["archive_id_present"] is True
    assert row["terminal_state"] == "completed"
    assert row["poll_count"] == 1


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

    assert [name for name, _ in calls] == ["concurrency_topology.py", "payload_boundary.py"]
    assert calls[0][1]["levels"] == [16, 32]
    assert calls[1][1]["sizes_bytes"] == [0, 1048576]
    assert set(artifacts) == {"payload_boundary", "concurrency_topology"}


def test_canonical_report_renders_supplemental_probe_data(tmp_path) -> None:
    topology = {
        "planned_levels": [16], "measured_levels": [16],
        "boundary_status": "observed",
        "first_boundary": {"level": 16, "topology": "heterogeneous-users",
                           "reasons": {"HTTP_LANE_SATURATED": 2}},
        "matrix": [{
            "requested_concurrency": 16, "topology": "heterogeneous-users",
            "actual_users": 4, "requested_users": 4, "offered": 16,
            "completed_2xx": 14, "p95_ms": 321.0, "throughput_rps_2xx": 8.2,
            "search_offered": 8, "search_quality_ok": 7,
            "commit_offered": 8, "commit_accepted": 8, "commit_completed": 6,
            "commit_failed": 0, "commit_timed_out": 2,
            "operational_failures": 2, "tenant_throughput_jain": .98,
            "http_counts": {"200": 14, "503": 2},
            "boundary_reasons": {"HTTP_LANE_SATURATED": 2},
        }],
    }
    boundary = {
        "cases": [{"api": "search", "encoding": "binary", "content_bytes": 1024,
                   "wire_bytes": 1024, "http_status": 415,
                   "reason_code": "UNSUPPORTED_MEDIA_TYPE", "elapsed_ms": 2.0,
                   "accepted": False}],
        "long_commit": {"terminal": {"state": "completed"}},
        "mcp_add_memory": {"status": "PASS"},
    }
    suite = {
        "runs": [], "instance_profile": "local",
        "concurrency_topology": {
            "status": "PASS", "checks": [{"name": "concurrency-topology",
                                             "reason": "measured",
                                             "detail": json.dumps(topology)}]},
        "payload_boundary": {
            "status": "PASS", "checks": [{"name": "payload-boundary",
                                             "reason": "measured",
                                             "detail": json.dumps(boundary)}]},
    }
    profile = {"name": "local", "required_concurrency": 16,
               "resource_evidence": {}, "model_preflight": {}}
    result = evaluate_observation(suite, profile, selected_metrics=["M1"])
    output = tmp_path / "report.html"
    write_observation_report(result, output)
    page = output.read_text(encoding="utf-8")
    assert "并发拓扑与请求边界补充证据" in page
    assert "HTTP_LANE_SATURATED" in page
    assert "UNSUPPORTED_MEDIA_TYPE" in page
    assert "concurrency-topology.json" in page
