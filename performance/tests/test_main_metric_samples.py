from performance.targets.echomem.acceptance.main_metric_samples import comparison, summarize_flood
from performance.targets.echomem.acceptance.main_metric_report import (
    derive_conclusions,
    derive_module_recommendations,
    recovery_counts,
    redacted_report,
    render,
)
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


def test_last_poll_gap_is_not_confirmed_backlog_and_errors_stay_in_denominator():
    loaded = sample()
    loaded["rows"] += [dict(loaded["rows"][0], start_s=9, end_s=10)]
    loaded["rows"][0].update(success=False, http_status=503)
    commits = [{"identity_index": 0, "accepted_202": True, "accepted_at": 102,
                "last_nonterminal_at": 106, "terminal_at": 110, "completed": True}]
    result = summarize_flood(sample(), loaded, commits, 4)
    assert result["overlap_search"]["sent"] == 5
    assert result["confirmed_overlap_search"]["sent"] == 4
    assert result["confirmed_overlap_search"]["success"] == 3
    assert result["overlap_evidence"]["uncertain_search"] == 1
    assert result["overlap_evidence"]["max_confirmed_inflight"] == 1


def test_legacy_or_invalid_nonterminal_time_is_not_inferred_from_terminal():
    for pending in (None, 101, 111, True, float("nan"), "106"):
        result = summarize_flood(sample(), sample(), [{
            "identity_index": 0, "accepted_202": True, "accepted_at": 102,
            "last_nonterminal_at": pending, "terminal_at": 110, "completed": True}], 4)
        assert result["overlap_search"]["sent"] == 4
        assert result["confirmed_overlap_search"]["sent"] == 0
        assert result["overlap_evidence"]["uncertain_search"] == 4


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


def test_report_derives_evidence_backed_module_recommendations():
    report = {"M1": {"levels": [{"hot_users": 8, "search": {"transport_or_http_errors": 3}}]},
              "M2": {"cases": [{"worst_bystander_p95_change_percent": 25}],
                     "bystander_http_errors": 0},
              "M3_M4": {"commit_planned": 32, "accepted_202": 16, "commit_jain": .75,
                          "search_inverse_p95_jain": .9,
                          "tenants": [{"commit_completed_in_search_window": 2},
                                      {"commit_completed_in_search_window": 0}],
                          "overlap_search": {}, "paired": []},
              "M5": {"passed_samples": 1, "sample_count": 1,
                     "missing_messages": 0, "same_archive": True},
              "M6": {"rows": [{"tenant": "T1", "lane": "commit"}],
                     "expected_cells": 4, "expected_lanes": ["commit"]}}
    recommendations = derive_module_recommendations(report)
    modules = {item["module"] for item in recommendations}
    assert {"原子引擎 Atomic Engine", "路由与意图模型", "租户公平调度"} <= modules
    assert all(item["evidence"] and item["change"] and item["verify"]
               for item in recommendations)


def test_html_explains_metrics_and_echo_mem_modules():
    public = redacted_report({}, {"levels": []})
    html = render(public)
    assert "六个指标分别反映什么" in html
    assert "EchoMem 模块改进优先级" in html
    assert "原子引擎 Atomic Engine" in html
    assert "路由与意图模型" in html
    assert "租户公平调度" in html
    assert "<th>测试方式</th>" in html
    assert "查看 Search 错误完整拆分" in html
    assert html.count("<b>测试方式：</b>") == 6
    for code in range(1, 7):
        section = html.index(f"<h2>M{code} ·")
        end = html.index("</section>", section)
        method = html.index("<b>测试方式：</b>", section)
        conclusion = html.index('<div class="conclusion"', section)
        assert section < method < conclusion < end


def test_missing_evidence_cannot_generate_confident_module_diagnoses():
    report = redacted_report({}, {"levels": []})
    modules = {r["module"]: r for r in derive_module_recommendations(report)}
    assert "公平性" in modules["租户公平调度"]["judgment"]
    assert "缺少租户计数" in modules["租户公平调度"]["judgment"]
    assert "尚不能确认" in modules["租户故障隔离"]["judgment"]
    assert "未采集" in modules["原子引擎 Atomic Engine"]["judgment"]
    assert "未采集" in modules["Admission 与容量保护"]["evidence"]
    assert "尚不能确认" in modules["Commit 持久化与恢复"]["judgment"]
    assert "尚未全部通过" in modules["可观测性"]["judgment"]
    conclusion = derive_conclusions(report)["M1"]
    assert "0.00s" not in conclusion["evidence"]
    assert "尚未采集" in conclusion["conclusion"]
    assert "无崩溃/OOM=True" not in conclusion["evidence"]


def test_module_advice_uses_actual_equal_tenants_not_cached_jain():
    joint = summarize_flood(sample(), sample(), [
        {"identity_index": i, "accepted_202": True, "accepted_at": 102,
         "completed": True, "terminal_at": 110} for i in range(4)], 4)
    joint["commit_jain"] = .25
    report = redacted_report({"metrics": {"M3_M4": joint}}, {"levels": []})
    module = next(r for r in derive_module_recommendations(report) if r["module"] == "租户公平调度")
    assert "完成数相等" in module["judgment"]
    assert "Commit Jain=1.0000" in module["evidence"]
    assert "没有获得近似等权" not in module["judgment"]


def test_lane_rejection_advice_requires_explicit_public_reason_evidence():
    report = redacted_report({"metrics": {"M3_M4": {
        "commit_outcomes": {"rejection_reason_counts": {"HTTP_LANE_SATURATED": 15}}}}}, {"levels": []})
    module = next(r for r in derive_module_recommendations(report) if r["module"] == "Admission 与容量保护")
    assert "HTTP_LANE_SATURATED拒绝 15 次" in module["evidence"]
    assert "不能算成已受理任务执行失败" in module["judgment"]
    assert "不能排除外部依赖间接" in module["judgment"]


def test_failed_recovery_and_incomplete_timeline_are_not_module_passes():
    report = redacted_report({"metrics": {"M5": {"status": "FAIL"}}}, {"levels": []})
    report["M6"].update(status="INCONCLUSIVE", snapshot_status="PASS",
                         expected_cells=16, valid_cells=16, tenant_count=4,
                         timeline={"status": "INCONCLUSIVE", "snapshot_count": 8})
    modules = {r["module"]: r for r in derive_module_recommendations(report)}
    assert "至少一项恢复检查失败" in modules["Commit 持久化与恢复"]["judgment"]
    assert "末次快照" in modules["可观测性"]["judgment"]
    assert "不等于每次都覆盖完整" in modules["可观测性"]["judgment"]


def test_render_recomputes_cached_conclusions_and_recommendations():
    report = redacted_report({}, {"levels": []})
    report["conclusions"]["M1"]["conclusion"] = "STALE-CAPACITY-PASS"
    report["module_recommendations"][0]["judgment"] = "STALE-MODULE-PASS"
    html = render(report)
    assert "STALE-" not in html


def test_m1_report_distinguishes_request_errors_from_operational_boundary():
    report = redacted_report({}, {"levels": [], "max_hot_users": 2,
                                  "boundary": {"status": "CONFIRMED", "first_fail": 4}})
    conclusion = derive_conclusions(report)["M1"]
    assert "硬容量未确定" in conclusion["level"]
    assert "锁定SLO确认" not in conclusion["next"]
    report["M1"]["operational_boundary"] = {
        "hot_users": 32, "evidence": {"status": "BOUNDARY_OBSERVED",
                                      "reason": "container-oom", "recovery_window_s": 300}}
    conclusion = derive_conclusions(report)["M1"]
    assert conclusion["level"] == "已观察到运行边界"
    assert "container-oom" in conclusion["conclusion"]


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
