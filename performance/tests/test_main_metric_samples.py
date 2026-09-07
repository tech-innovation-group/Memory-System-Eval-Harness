from performance.targets.echomem.acceptance.main_metric_samples import comparison, summarize_flood
from performance.targets.echomem.acceptance.main_metric_report import redacted_report, render
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
    assert public["M6"]["rows"][0]["tenant"] == "T1"


def test_empty_initial_report_keeps_missing_metrics_unknown():
    public = redacted_report({}, {"levels": []})
    html = render(public)
    assert "未采集" in html
    assert public["M1"]["max_dau"] is None
    assert public["M5"]["autonomous_completed"] is None
