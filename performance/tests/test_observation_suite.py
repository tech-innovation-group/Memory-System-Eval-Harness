from __future__ import annotations

import csv
import json
from pathlib import Path

from performance.targets.echomem.acceptance.capacity_load import arrival_plan
from performance.targets.echomem.acceptance.observation import (
    STATUSES,
    derive_observation_recommendations,
    evaluate_observation,
    jain,
    summarize_api_coverage,
    summarize_concurrency_configuration,
    summarize_m5,
    summarize_m6,
    summarize_timing_evidence,
    write_observation_report,
)
from performance.targets.echomem.observation_run import _m1_levels, _validate_m1_resume
from performance.targets.echomem.probes.tenant_observability import expected_lanes_from_config


def test_empty_report_tables_span_their_actual_columns(tmp_path: Path) -> None:
    path = tmp_path / "report.html"
    write_observation_report({"metrics": {"M2": {"status": "PARTIAL", "cases": []}},
                              "status": "PARTIAL", "sampling_mode": "quick"}, path)
    rendered = path.read_text()
    assert 'colspan="3"' in rendered
    assert 'colspan="7"' in rendered
    assert 'colspan="9"' not in rendered


def _run(tmp_path: Path, name: str, rows: list[dict]) -> dict:
    target = tmp_path / name
    target.mkdir()
    fields = sorted(set().union(*(row.keys() for row in rows)))
    with (target / "records.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return {"scenario": name, "output_dir": str(target), "duration_s": 60}


def test_arrival_plan_has_four_real_load_shapes() -> None:
    operations = {}
    for mode in ("search", "commit", "mixed", "hotspot"):
        operations[mode] = {event[1] for event in arrival_plan(
            4, 60, 1, load_mode=mode, seed=7, commit_interval_s=10
        )}
    assert operations["search"] == {"read"}
    assert operations["commit"] == {"add", "commit_submit"}
    assert operations["mixed"] == {"read", "add", "commit_submit"}
    assert operations["hotspot"] == {"read", "add", "commit_submit"}
    normal_reads = sum(event[1] == "read" for event in arrival_plan(4, 60, 1, load_mode="mixed", seed=7))
    hot_reads = sum(event[1] == "read" for event in arrival_plan(4, 60, 1, load_mode="hotspot", seed=7))
    assert hot_reads > normal_reads


def test_jain_all_zero_is_undefined_and_zero_tenant_is_retained() -> None:
    assert jain([0, 0, 0, 0]) is None
    assert jain([8, 8, 8, 0]) == 0.75
    assert jain([1, -1]) is None
    assert jain([1, float("nan")]) is None


def test_m1_explicit_empty_levels_are_not_replaced_by_defaults() -> None:
    assert _m1_levels({}, "m1_tenant_levels", [1, 2]) == [1, 2]
    assert _m1_levels({"m1_tenant_levels": []}, "m1_tenant_levels", [1, 2]) == []


def test_m1_resume_rejects_changed_configuration() -> None:
    expected = {"topology": "cross-tenant", "levels_requested": [1, 2]}
    _validate_m1_resume(dict(expected), expected)
    try:
        _validate_m1_resume(
            {"topology": "cross-tenant", "levels_requested": [1]}, expected
        )
    except ValueError as exc:
        assert "levels_requested" in str(exc)
    else:
        raise AssertionError("changed M1 resume configuration was accepted")


def test_overlap_uses_search_start_not_interval_intersection(tmp_path: Path) -> None:
    baseline = [{"op": "read", "tenant_idx": 0, "status": "ok", "stage_ms": 10,
                 "ts_ms": 500, "quality_ok": True}]
    flood = [
        {"op": "commit_submit", "tenant_idx": 0, "status": "ok", "http_status": 202,
         "session_id": "s", "archive_id": "a", "accepted_at_ms": 1000, "ts_ms": 1000},
        {"op": "commit_done", "tenant_idx": 0, "status": "ok", "session_id": "s",
         "archive_id": "a", "completed_at_ms": 2000, "ts_ms": 2000},
        # Starts before acceptance and ends during the interval: excluded.
        {"op": "read", "tenant_idx": 0, "status": "ok", "stage_ms": 600,
         "ts_ms": 1200, "quality_ok": True},
        # Starts inside the accepted-to-terminal interval: included.
        {"op": "read", "tenant_idx": 0, "status": "error", "error_type": "timeout",
         "stage_ms": 200, "ts_ms": 1700, "quality_ok": False},
    ]
    suite = {"runs": [_run(tmp_path, "m4-baseline", baseline),
                       _run(tmp_path, "m4-flood-uniform", flood)]}
    result = evaluate_observation(suite, {}, quick=False)["metrics"]["M3"]
    assert result["windows"][0]["overlap"]["planned_or_recorded"] == 1
    assert result["windows"][0]["overlap"]["timeouts"] == 1


def test_m5_preserves_three_sample_denominators() -> None:
    required = ("commit-recovery", "pending-before-kill", "message-reconciliation",
                "cursor-reconciliation", "order-reconciliation", "idempotency-replay")
    samples = []
    for index in range(3):
        checks = [{"name": name, "status": "PASS", "detail": "{}"} for name in required]
        checks[0]["detail"] = json.dumps({"accepted_202": True, "pending_before_kill": True,
                                           "autonomous_recovery_observed": index != 2})
        checks[1]["detail"] = json.dumps({"accepted_202": True, "state": "pending"})
        samples.append({"sample_index": index + 1, "checks": checks})
    result = summarize_m5({"commit_recovery": {"samples": samples}}, quick=False)
    assert result["received_202"] == 3
    assert result["autonomous_completed"] == 2
    assert result["fully_reconciled"] == 3
    assert result["complete_samples"] == 2
    assert result["status"] == "PARTIAL"


def test_lanes_are_derived_from_effective_config(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps({"scheduler": {"lanes": ["alpha", "beta"]}}))
    assert expected_lanes_from_config(explicit) == ["alpha", "beta"]
    inferred = tmp_path / "inferred.json"
    inferred.write_text(json.dumps({"recall": {"model": {
        "query_embedding": {"api_base": "https://e", "model": "embed"},
        "rerank": {"enabled": False, "api_base": "https://r", "model": "rank"},
    }}}))
    assert expected_lanes_from_config(inferred) == ["commit", "recall_query_embedding"]


def test_m6_uses_explicit_before_snapshot_for_deltas() -> None:
    def snapshot(accepted: int, rejected: int = 0) -> dict:
        return {"process_id": "p1", "process_started_at": "start-1", "status": "PASS",
                "lane_count_matches_observed": True, "expected_lanes_match_observed": True,
                "rows": [{
            "tenant_id": "t1", "lane": "commit", "queued": 0,
            "wait_seconds_total": accepted, "exec_seconds_total": accepted,
            "rejected_total": rejected, "accepted_total": accepted,
            "completed_total": accepted, "failed_total": 0,
        }]}

    suite = {
        "tenant_observability_before": snapshot(2),
        "tenant_observability_after_all": snapshot(5, 1),
        "tenant_observability_samples": [snapshot(3), snapshot(4, 1)],
    }
    profile = {"tenant_observability": {
        "expected_tenants": ["t1"], "expected_lanes": ["commit"],
    }}
    result = summarize_m6(suite, profile)
    assert result["matrix"][0]["delta"]["accepted_total"] == 3
    assert result["matrix"][0]["delta"]["rejected_total"] == 1
    assert result["scenarios"]["NORMAL"] is True
    assert result["scenarios"]["REJECT"] is True


def test_final_observation_uses_only_four_statuses() -> None:
    result = evaluate_observation({}, {}, quick=False)
    assert result["status"] in STATUSES
    assert {metric["status"] for metric in result["metrics"].values()} <= set(STATUSES)


def test_single_metric_overall_status_ignores_unselected_metrics() -> None:
    result = evaluate_observation(
        {}, {}, m1_reports=[{
            "topology": "cross-tenant", "levels_requested": [1],
            "load_profile": "search", "levels": [{"status": "MEASURED", "hot_users": 1}],
        }], selected_metrics=["M1"],
    )
    assert result["status"] == "MEASURED"


def test_quick_never_reports_selected_metric_as_measured() -> None:
    result = evaluate_observation(
        {}, {}, m1_reports=[{
            "topology": "cross-tenant", "levels_requested": [1],
            "load_profile": "search", "levels": [{"status": "MEASURED", "hot_users": 1}],
        }], quick=True, selected_metrics=["M1"],
    )
    assert result["status"] == "PARTIAL"
    assert result["metrics"]["M1"]["status"] == "PARTIAL"


def test_observation_report_is_summary_first_with_hidden_details(tmp_path: Path) -> None:
    result = evaluate_observation({}, {}, quick=False)
    output = tmp_path / "report.html"
    write_observation_report(result, output)
    page = output.read_text(encoding="utf-8")

    assert "EchoMem 模块改进建议" in page
    assert "原子引擎 Atomic Engine" in page
    assert "多租户调度" in page
    assert "查看 CPU、内存逐点采样" in page
    assert "<details>" in page
    assert "<details open" not in page
    assert all(name in page for name in ("M1", "M2", "M3", "M4", "M5", "M6"))
    assert page.count("<b>测试方式：</b>") == 6
    for code in ("M1", "M2", "M3", "M4", "M5", "M6"):
        section = page.index(f"<h2>{code} ")
        method = page.index("<b>测试方式：</b>", section)
        details = page.index("<details>", section)
        assert section < method < details


def test_observation_recommendations_cover_required_modules() -> None:
    recommendations = derive_observation_recommendations(
        evaluate_observation({}, {}, quick=False)
    )
    modules = {row["module"] for row in recommendations}
    assert {"原子引擎 Atomic Engine", "路由与 Search 编排", "多租户调度"} <= modules
    assert all(row["evidence"] and row["action"] for row in recommendations)


def test_api_coverage_keeps_uncalled_contracts_visible(tmp_path: Path) -> None:
    run = _run(tmp_path, "calls", [
        {"op": "read", "status": "ok", "stage_ms": 12},
        {"op": "commit_done", "status": "ok", "stage_ms": 40, "poll_count": 3},
    ])
    summary = summarize_api_coverage({"runs": [run]})
    rows = {row["operation"]: row for row in summary["operations"]}
    assert rows["search"]["calls"] == 1
    assert rows["commit_status"]["calls"] == 3
    assert rows["message_add"]["status"] == "NOT_COVERED"
    assert summary["invalid_input"]["status"] == "NOT_COVERED"


def test_api_coverage_accepts_readiness_nested_under_resource_preflight() -> None:
    summary = summarize_api_coverage({
        "runs": [],
        "resource_preflight": {"readiness": {"checks": [
            {"name": "ready", "status": "PASS"},
        ]}},
    })
    ready = next(row for row in summary["operations"]
                 if row["operation"] == "system_ready")
    assert ready["status"] == "COVERED"
    assert ready["calls"] == 1


def test_timing_summary_does_not_invent_internal_stages(tmp_path: Path) -> None:
    run = _run(tmp_path, "timings", [
        {"op": "read", "status": "ok", "stage_ms": 10},
        {"op": "read", "status": "ok", "stage_ms": 30},
    ])
    summary = summarize_timing_evidence({"runs": [run]}, [])
    read = next(row for row in summary["operation_timings"] if row["module"].endswith("/read"))
    assert read["observations"] == 2
    assert read["p95_ms"] == 30
    assert "atomic engine" in summary["unobservable_modules"]


def test_service_concurrency_limits_are_recorded_without_capping_client(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "scheduler": {"concurrency": {
            "search": {"max_concurrency": 16, "queue_capacity": 32},
            "commit": {"max_concurrency": 4, "queue_capacity": 256},
        }}
    }))
    result = summarize_concurrency_configuration({
        "preflight_config": str(config), "required_concurrency": 128,
    })
    assert result["client_load_auto_capped_by_service_config"] is False
    assert result["required_client_concurrency"] == 128
    assert result["limits_below_target"] == 3
