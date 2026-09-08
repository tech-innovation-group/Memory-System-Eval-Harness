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
    assert public["M5"]["status"] == "PASS"
    assert public["M6"]["rows"][0]["tenant"] == "T1"


def test_empty_initial_report_keeps_missing_metrics_unknown():
    public = redacted_report({}, {"levels": []})
    html = render(public)
    assert "未采集" in html
    assert public["M1"]["max_dau"] is None
    assert public["M5"]["autonomous_completed"] is None


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
    assert conclusions["M3"]["level"] == "观察到租户完成分布不均"
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
    assert html.count("<b>测试方式：</b>") == 6
    section = html.index("<h2>M1 · 热用户与 DAU</h2>")
    method = html.index("<b>测试方式：</b>", section)
    conclusion = html.index("<div class=\"conclusion\">", section)
    assert section < method < conclusion


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
    ]
    public = recovery_counts({"status": "PASS", "samples": [
        {"status": "PASS", "elapsed_s": 2, "checks": detail(8)},
        {"status": "PASS", "elapsed_s": 3, "checks": detail(12)},
    ]})
    assert public["passed_samples"] == public["sample_count"] == 2
    assert public["expected_messages"] == 20
    assert public["missing_messages"] == 0
    assert "PRIVATE-ID" not in json.dumps(public)
