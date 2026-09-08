"""Suite runner (runner.py) unit tests."""

from __future__ import annotations

import json

import pytest

from performance.records import RequestRecord
from performance.targets.echomem.orchestrator.runner import (
    run_case,
    run_suite,
    summarize_case_records,
)
from performance.targets.echomem.orchestrator.suites import (
    QuickSpec,
    build_case_profile,
    complete_cases,
)


def _record(**overrides):
    fields = {
        "scene": "scene_capacity", "worker_id": 0, "tenant_idx": 0, "op": "read",
        "stage_ms": 0.0, "status": "ok", "error_type": "", "ts_ms": 0.0,
    }
    fields.update(overrides)
    return RequestRecord(**fields)


def _baseline_case():
    return next(case for case in complete_cases() if case["label"] == "baseline")


# -- summarize_case_records ----------------------------------------------


def test_summarize_case_records():
    records = [
        _record(tenant_idx=0, op="read", stage_ms=100.0, query="PERFANCHOR-0-0-0"),
        _record(tenant_idx=0, op="read", stage_ms=200.0, query="普通查询", quality_ok=False),
        _record(
            tenant_idx=1, op="read", stage_ms=300.0, status="error",
            error_type="http_4xx", retry_after_s=1.0, reason_code="rate_limit",
        ),
        _record(
            tenant_idx=0, op="commit_submit", stage_ms=50.0,
            session_id="s1", archive_id="a1",
        ),
        _record(
            tenant_idx=0, op="commit_done", stage_ms=1000.0,
            session_id="s1", archive_id="a1",
        ),
        _record(
            tenant_idx=1, op="commit_submit", stage_ms=60.0,
            session_id="s2", archive_id="a2",
        ),
        _record(
            tenant_idx=1, op="commit_done", stage_ms=2000.0, status="error",
            error_type="http_5xx", session_id="s2",
        ),
        _record(
            tenant_idx=1, op="commit_submit", stage_ms=70.0, status="error",
            error_type="http_4xx", session_id="s3",
        ),
    ]
    summary = summarize_case_records(records)
    search = summary["metrics"]["search"]
    assert search["submitted"] == 3
    assert search["succeeded"] == 2
    assert search["errors"] == 1
    assert search["success_rate"] == pytest.approx(2 / 3)
    assert search["rate_limited_count"] == 1
    assert search["quality_asserted"] == 1
    assert search["quality_failures"] == 1
    assert search["latency"]["mean_s"] == pytest.approx(0.15)
    assert search["latency"]["p50_s"] == pytest.approx(0.1)
    assert search["latency"]["p95_s"] == pytest.approx(0.2)
    assert search["latency"]["p99_s"] == pytest.approx(0.2)

    commit = summary["metrics"]["commit"]
    assert commit["submitted"] == 3
    assert commit["completed"] == 1
    assert commit["failed"] == 2
    assert commit["success_rate"] == pytest.approx(1 / 3)
    assert commit["rate_limited_count"] == 0

    assert summary["metrics"]["fairness"]["commit_completed_per_tenant"] == {"0": 1}

    per_tenant = summary["metrics"]["per_tenant"]
    assert per_tenant["0"]["commit"] == {
        "submitted": 1, "completed": 1, "completion": {"p50_s": 1.0},
    }
    assert per_tenant["0"]["search"]["submitted"] == 2
    assert per_tenant["0"]["search"]["succeeded"] == 2
    assert per_tenant["0"]["search"]["latency"]["p50_s"] == pytest.approx(0.1)
    assert per_tenant["0"]["search"]["latency"]["p95_s"] == pytest.approx(0.2)
    assert per_tenant["1"]["commit"] == {"submitted": 1, "completed": 0}
    assert "search" not in per_tenant["1"]

    assert summary["details"]["identity_mode"] == "independent_auth_keys"
    assert summary["details"]["quality_seed"] == []
    assert summary["parameters"]["commit_delay_threshold_s"] == 10.0
    assert summary["parameters"]["search_delay_threshold_s"] == 2.5


def test_summarize_empty_records():
    summary = summarize_case_records([])
    search = summary["metrics"]["search"]
    assert search["submitted"] == 0
    assert search["success_rate"] is None
    assert search["latency"]["mean_s"] is None
    assert search["latency"]["p50_s"] is None
    assert summary["metrics"]["commit"]["success_rate"] is None
    assert summary["metrics"]["per_tenant"] == {}


# -- run_case ------------------------------------------------------------


def test_run_case(server, tmp_path):
    _, _, base_url = server
    case = _baseline_case()
    profile = build_case_profile(
        case,
        base_url=base_url,
        tenant_count=1,
        auth_headers={},
        quick=QuickSpec(duration_cap_s=1.5),
    )
    case_dir = tmp_path / "case"
    run = run_case(case, profile, case_dir=case_dir, timeout_s=30.0)
    assert run["status"] == "completed"
    assert run["scenario"] == "baseline"
    assert run["scene"] == "scene_capacity"
    assert run["repetition"] == 1
    assert run["policy"] == "server-observe"
    assert run["runner_timeout"] is False
    contract = run["summary"]["measurement_contract"]
    assert contract == {"version": "echomem-case-v1", "tenant_count": 1, "query_mode": "recall"}
    assert json.loads((case_dir / "summary.json").read_text())["measurement_contract"] == contract
    for name in ("summary.json", "records.csv", "commit_results.csv", "search_results.csv"):
        assert (case_dir / name).is_file(), name
    summary = json.loads((case_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["metrics"]["search"]["submitted"] > 0
    assert summary["metrics"]["commit"]["submitted"] >= 0


def test_run_case_attaches_metric_coverage(server, tmp_path):
    _, state, base_url = server
    state.metrics_text = (
        'echomem_lane_queued{lane="recall_engine"} 1\n'
        'echomem_lane_wait_seconds_bucket{lane="recall_engine",le="0.5"} 2\n'
        'echomem_lane_exec_seconds_bucket{lane="recall_engine",le="0.5"} 2\n'
        'echomem_lane_rejected_total{lane="recall_engine"} 0\n'
        'echomem_engine_fanout_exec_seconds{engine="recall"} 0.2\n'
        'echomem_engine_fanout_skipped_total{engine="recall"} 0\n'
    )
    case = _baseline_case()
    profile = build_case_profile(
        case,
        base_url=base_url,
        tenant_count=1,
        auth_headers={},
        quick=QuickSpec(duration_cap_s=1.5),
    )
    case_dir = tmp_path / "case"
    run = run_case(case, profile, case_dir=case_dir, timeout_s=30.0)
    assert run["status"] == "completed"
    assert (case_dir / "metrics_samples.csv").is_file()
    coverage = run["summary"]["details"]["pr421_metric_coverage"]
    assert coverage["present"]["echomem_lane_queued"] is True
    assert coverage["missing"] == []
    assert coverage["lane_quartets"]["recall_engine"] == {
        "queued": True, "wait": True, "exec": True, "rejected": True
    }
    assert coverage["fanout_engines"]["recall"] == {"exec": True, "skipped": True}


def test_run_case_metrics_disabled(server, tmp_path):
    _, state, base_url = server
    state.metrics_text = 'echomem_lane_queued{lane="recall_engine"} 1\n'
    case = _baseline_case()
    profile = build_case_profile(
        case,
        base_url=base_url,
        tenant_count=1,
        auth_headers={},
        quick=QuickSpec(duration_cap_s=1.5),
    )
    case_dir = tmp_path / "case"
    run = run_case(case, profile, case_dir=case_dir, timeout_s=30.0, collect_metrics=False)
    assert run["status"] == "completed"
    assert not (case_dir / "metrics_samples.csv").exists()
    assert "pr421_metric_coverage" not in run["summary"].get("details", {})


# -- run_suite -----------------------------------------------------------


def test_run_suite(server, tmp_path):
    _, _, base_url = server
    tenants_path = tmp_path / "tenants.json"
    tenants_path.write_text(
        json.dumps({"tenants": [{"tenant_id": "t1", "auth_key": "k1"}]}),
        encoding="utf-8",
    )
    profile = {
        "name": "test-instance",
        "base_url": base_url,
        "tenant_config": str(tenants_path),
    }
    suite_dir = tmp_path / "suite"
    manifest = run_suite(
        profile,
        suite_dir=suite_dir,
        quick=QuickSpec(duration_cap_s=1.5),
        profile_name="4u8g",
        base_url=base_url,
        timeout_s=60.0,
        scenarios=["baseline"],
    )
    assert (suite_dir / "suite.json").is_file()
    assert (suite_dir / "acceptance.json").is_file()
    assert "acceptance" in manifest
    assert manifest["scenarios"] == ["baseline"]
    assert manifest["runs"]
    assert manifest["runs"][0]["status"] == "completed"
    assert manifest["seed"]["status"] == "completed"
    assert manifest["seed"]["tenant_count"] == 1
    assert manifest["probe_artifacts"] == {}
    assert manifest["preflight"]["status"] == "NOT_RUN"


def test_run_suite_no_tenant_config(server, tmp_path):
    _, _, base_url = server
    profile = {"name": "test-instance", "base_url": base_url}
    suite_dir = tmp_path / "suite"
    manifest = run_suite(
        profile,
        suite_dir=suite_dir,
        quick=QuickSpec(duration_cap_s=1.5),
        profile_name="4u8g",
        base_url=base_url,
        timeout_s=60.0,
        scenarios=["baseline"],
    )
    assert manifest["seed"]["status"] == "skipped"
    assert manifest["runs"][0]["status"] == "completed"
    assert manifest["runs"][0]["summary"]["metrics"]["search"]["submitted"] > 0


def test_run_suite_resume_merges_completed_runs(server, tmp_path):
    """resume=True：已完成的 case 不再执行，历史 run 合并进 manifest。"""
    _, _, base_url = server
    tenants_path = tmp_path / "tenants.json"
    tenants_path.write_text(
        json.dumps({"tenants": [{"tenant_id": f"t{i}", "auth_key": f"k{i}"} for i in range(4)]}),
        encoding="utf-8",
    )
    profile = {
        "name": "test-instance",
        "base_url": base_url,
        "tenant_config": str(tenants_path),
    }
    suite_dir = tmp_path / "suite"
    # 第一轮只跑完 baseline（模拟中断：mixed 及之后未执行）。
    run_suite(
        profile,
        suite_dir=suite_dir,
        quick=QuickSpec(duration_cap_s=1.5),
        profile_name="4u8g",
        base_url=base_url,
        timeout_s=60.0,
        scenarios=["baseline"],
    )
    baseline_dir = suite_dir / "baseline"
    baseline_before = (
        baseline_dir / "summary.json"
    ).read_text(encoding="utf-8")

    # 第二轮 resume 跑完整场景列表：baseline 跳过，mixed 执行。
    manifest = run_suite(
        profile,
        suite_dir=suite_dir,
        quick=QuickSpec(duration_cap_s=1.5),
        profile_name="4u8g",
        base_url=base_url,
        timeout_s=60.0,
        scenarios=["baseline", "mixed"],
        resume=True,
    )
    assert [run["scenario"] for run in manifest["runs"]] == ["baseline", "mixed"]
    assert all(run["status"] == "completed" for run in manifest["runs"])
    # baseline 未重跑：summary.json 内容原样保留，mixed 有本次结果。
    assert (
        baseline_dir / "summary.json"
    ).read_text(encoding="utf-8") == baseline_before
    assert (suite_dir / "mixed" / "summary.json").is_file()
    # suite.json 写盘包含合并后的两条。
    written = json.loads((suite_dir / "suite.json").read_text(encoding="utf-8"))
    assert [run["scenario"] for run in written["runs"]] == ["baseline", "mixed"]
