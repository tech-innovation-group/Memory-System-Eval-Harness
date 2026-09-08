from performance.targets.echomem.acceptance.main_metric_samples import comparison, summarize_flood
from performance.targets.echomem.acceptance.main_metric_report import derive_conclusions, recovery_counts, redacted_report, render
import json


def sample():
    return {"started_at_monotonic_s": 100, "duration_s": 60,
            "rows": [{"op": "read", "sent": True, "success": True, "http_status": 200,
                      "identity_index": i, "start_s": 5, "end_s": 6, "elapsed_s": 1}
                     for i in range(4)]}


def test_jain_retains_zero_tenant_and_excludes_post_window_commit():
    baseline = sample()
    commits = [{"identity_index": i, "accepted_202": True, "accepted_at": 102,
                "completed": True, "terminal_at": 110 if i < 3 else 170} for i in range(4)]
    result = summarize_flood(baseline, sample(), commits, 4)
    assert result["completed_including_drain"] == 4
    assert result["tenants"][3]["commit_completed_in_search_window"] == 0
    assert abs(result["commit_jain"] - .75) < 1e-12
    assert result["overlap_search"]["sent"] == 4
    assert not result["strict_server_scheduling_proven"]
    assert result["unresolved_after_observation"] == 0


def test_no_commit_completion_is_not_perfect_fairness():
    result = summarize_flood(sample(), sample(), [], 4)
    assert result["commit_jain"] is None
    assert result["overlap_search"]["sent"] == 0


def test_missing_tenant_samples_are_not_zero_latency():
    during = sample()
    during["rows"] = during["rows"][:3]
    pairs = comparison(sample(), during, 4)
    assert pairs[3]["during"]["sent"] == 0
    assert pairs[3]["p95_degradation_percent"] is None


def test_public_report_excludes_credentials_and_private_recovery_identifiers():
    raw = {"metrics": {"M5": {"checks": [{"name": "commit-recovery", "status": "PASS",
        "detail": json.dumps({"idempotency_key": "PRIVATE-IDEMPOTENCY", "session_id": "PRIVATE-SESSION",
                              "accepted_202": True, "autonomous_recovery_observed": True})}]},
        "M6": {"expected_tenants": ["PRIVATE-TENANT"], "expected_lanes": ["commit"],
               "rows": [{"tenant_id": "PRIVATE-TENANT", "lane": "commit", "queued": 0,
                         "auth_key": "PRIVATE-KEY"}]}}}
    public = redacted_report(raw, {"levels": []})
    serialized = json.dumps(public) + render(public)
    assert "PRIVATE-" not in serialized
    assert public["M5"]["autonomous_completed"] is True
    assert public["M5"]["status"] == "INCONCLUSIVE"
    assert public["M6"]["rows"][0]["tenant"] == "T1"


def test_empty_initial_report_keeps_missing_metrics_unknown():
    public = redacted_report({}, {"levels": []})
    html = render(public)
    assert "未采集" in html
    assert public["M1"]["max_dau"] is None
    assert public["M5"]["autonomous_completed"] is None


def test_comprehensive_report_preserves_route_paths_in_fault_and_flood_windows():
    baseline, flood = sample(), sample()
    for row in baseline["rows"]:
        row["executed_layers"] = ["rule", "semantic"]
    for row in flood["rows"]:
        row["executed_layers"] = ["rule", "semantic", "llm"]
    commits = [{"identity_index": i, "accepted_202": True, "accepted_at": 102,
                "completed": True, "terminal_at": 110} for i in range(4)]
    joint = summarize_flood(baseline, flood, commits, 4)
    pairs = comparison(baseline, flood, 4)
    public = redacted_report({"metrics": {"M3_M4": joint,
                            "M2": {"target_index": 0, "pairs": pairs}}}, {"levels": []})
    assert public["M2"]["pairs"][0]["before"]["route_path_timings"]["fast_path"]["observations"] == 1
    assert public["M3_M4"]["overlap_search"]["route_path_timings"]["intent_llm"]["observations"] == 4
    html = render(public)
    assert html.count("<summary>Search 路由路径延迟拆解</summary>") == 4
    assert "T1 故障中" in html and "T1 洪泛中" in html and "T1 公平性窗口" in html
    assert "nearest-rank" in html and "缺失/无效计时" in html


def test_route_report_export_omits_private_evidence_and_retains_missing_timing(tmp_path):
    from performance.targets.echomem.acceptance.route_path_report import publish_route_paths

    measurement = sample()
    measurement["api_key"] = "PRIVATE-KEY"
    measurement["rows"][0].update(executed_layers=["llm"], response="PRIVATE-RESPONSE")
    measurement["rows"][1].update(elapsed_s=None, executed_layers=["llm"])
    measurement["rows"].append({"op": "commit_submit", "sent": True,
                                 "api_key": "PRIVATE-COMMIT", "elapsed_s": 90})
    path = tmp_path / "routes.html"
    public = publish_route_paths(measurement, path, '<script>unsafe</script>')
    html = path.read_text()
    serialized = path.with_suffix('.json').read_text()
    assert public["sent"] == 4
    assert public["route_path_timings"]["intent_llm"]["observations"] == 2
    assert public["route_path_timings"]["intent_llm"]["latency_missing_or_invalid"] == 1
    assert "PRIVATE-" not in html + serialized
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<details open>' in html


def test_each_metric_has_an_explicit_bounded_conclusion():
    level = {"hot_users": 32, "mixed": False,
             "search": {"sent": 100, "success": 30, "p95_s": 9.5,
                        "transport_or_http_errors": 65},
             "recovery": {"status": "NO_BOUNDARY_OBSERVED"}}
    report = {"M1": {"levels": [level]},
              "M2": {"target_index": 0, "target_http_errors": 10,
                     "baseline_strict_valid": False, "pairs": []},
              "M3_M4": {"commit_jain": .75, "search_inverse_p95_jain": .99,
                        "tenants": [], "paired": [], "overlap_search": {},
                        "accepted_202": 0, "commit_planned": 0,
                        "completed_including_drain": 0, "unresolved_after_observation": 0},
              "M5": {"checks": [], "accepted_202": None},
              "M6": {"rows": [], "expected_cells": 16, "missing_cells": 16,
                     "invalid_cells": 0, "expected_lanes": []}}
    conclusions = derive_conclusions(report)
    assert set(conclusions) == {"M1", "M2", "M3", "M4", "M5", "M6"}
    assert all(item["conclusion"] and item["evidence"] and item["next"] for item in conclusions.values())
    assert "不能把最高已测档写成绝对容量上限" in conclusions["M1"]["conclusion"]
    assert conclusions["M3"]["level"] == "公平性证据不完整"
    assert "严格优先仍未证明" in conclusions["M4"]["level"]


def test_recovery_matrix_is_reduced_to_public_counts():
    detail = lambda messages: [
        {"name": "commit-recovery", "status": "PASS", "detail": json.dumps({
            "accepted_202": True, "autonomous_recovery_observed": True,
            "idempotency_key": "PRIVATE-ID"})},
        {"name": "message-reconciliation", "status": "PASS", "detail": json.dumps({
            "expected_server_message_ids": list(range(messages)),
            "missing_server_message_ids": []})},
        {"name": "idempotency-replay", "status": "PASS", "detail": json.dumps({
            "same_archive": True})},
        {"name": "pending-before-kill", "status": "PASS"},
        {"name": "cursor-reconciliation", "status": "PASS"},
        {"name": "order-reconciliation", "status": "PASS"},
    ]
    public = recovery_counts({"status": "PASS", "samples": [
        {"status": "PASS", "elapsed_s": 2, "checks": detail(8)},
        {"status": "PASS", "elapsed_s": 3, "checks": detail(12)},
    ]})
    assert public["passed_samples"] == public["sample_count"] == 2
    assert public["expected_messages"] == 20
    assert public["missing_messages"] == 0
    assert "PRIVATE-ID" not in json.dumps(public)
