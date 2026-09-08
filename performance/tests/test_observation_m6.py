import copy
import json
import threading

import pytest

from performance.targets.echomem.acceptance.observation import summarize_m6, write_observation_report
from performance.targets.echomem.acceptance.observability_timeline import timeline_counts
from performance.targets.echomem.observation_run import _collect_observation, _sample_observability


def snapshot(at, count, *, boot="private-a", queued=0):
    return {"status": "PASS", "observed_at_s": at, "boot_id": boot,
            "lane_count_matches_observed": True, "expected_lanes_match_observed": True,
            "rows": [{"tenant_id": "private-tenant", "lane": "commit", "queued": queued,
                      "wait_seconds_total": count, "exec_seconds_total": count,
                      "rejected_total": count, "accepted_total": count,
                      "completed_total": count, "failed_total": 0}]}


def full_evidence():
    suite = {"tenant_observability_before": snapshot(9, 0),
             "tenant_observability_samples": [snapshot(11, 2, queued=1),
                                               snapshot(13, 0, boot="private-b"),
                                               snapshot(15, 2, boot="private-b")],
             "tenant_observability_after_all": snapshot(17, 3, boot="private-b"),
             "tenant_observability_monitor": {"window_start_s": 10, "window_end_s": 16,
                                               "max_sampling_gap_s": 3, "errors": []}}
    profile = {"tenant_observability": {"expected_tenants": ["private-tenant"],
                                         "expected_lanes": ["commit"]}}
    return suite, profile


def test_known_restart_is_segmented_without_cross_process_delta():
    suite, profile = full_evidence()
    result = summarize_m6(suite, profile)
    assert result["status"] == "MEASURED"
    assert all(result["scenarios"].values())
    assert result["timeline"]["passed_snapshots"] == result["sample_count"] == 5
    assert all(value is None for value in result["matrix"][0]["delta"].values())
    assert len(result["process_segments"]["segments"]) == 2
    assert "private-" not in json.dumps(result)


@pytest.mark.parametrize("mutation", ["empty", "missing", "duplicate", "nan", "bool", "negative"])
def test_valid_endpoints_do_not_hide_bad_middle_frame(mutation):
    suite, profile = full_evidence()
    frame = suite["tenant_observability_samples"][1]
    if mutation == "empty":
        frame["rows"] = []
    elif mutation == "missing":
        del frame["rows"][0]["exec_seconds_total"]
    elif mutation == "duplicate":
        frame["rows"].append(copy.deepcopy(frame["rows"][0]))
    else:
        frame["rows"][0]["queued"] = {"nan": float("nan"), "bool": True, "negative": -1}[mutation]
    result = summarize_m6(suite, profile)
    assert result["status"] == "PARTIAL"
    assert result["endpoint_complete_cells"] == 1
    assert result["complete_cells"] == 0
    assert result["timeline"]["passed_snapshots"] == 4
    assert result["sample_count"] == 5
    json.dumps(result, allow_nan=False)


def test_middle_counter_drop_is_failure_not_proof_of_reset():
    suite, profile = full_evidence()
    for frame in [*suite["tenant_observability_samples"], suite["tenant_observability_after_all"]]:
        frame["boot_id"] = "private-a"
    result = summarize_m6(suite, profile)
    assert result["status"] == "EXECUTION_ERROR"
    assert result["scenarios"]["RESET"] is False
    assert result["timeline"]["counter_regressions"][0]["classification"] == "same_process"


def test_missing_process_identity_does_not_manufacture_reset_or_deltas():
    suite, profile = full_evidence()
    for frame in [suite["tenant_observability_before"], *suite["tenant_observability_samples"],
                  suite["tenant_observability_after_all"]]:
        del frame["boot_id"]
    result = summarize_m6(suite, profile)
    assert result["status"] == "PARTIAL"
    assert result["scenarios"]["RESET"] is False
    assert result["matrix"][0]["delta"]["accepted_total"] is None


@pytest.mark.parametrize("change", ["missing_bounds", "long_gap", "reordered", "no_during"])
def test_monitoring_gaps_prevent_complete_m6(change):
    suite, profile = full_evidence()
    if change == "missing_bounds":
        del suite["tenant_observability_monitor"]["window_start_s"]
    elif change == "long_gap":
        suite["tenant_observability_monitor"]["window_end_s"] = 60
    elif change == "reordered":
        suite["tenant_observability_samples"].reverse()
    else:
        suite["tenant_observability_samples"] = []
    assert summarize_m6(suite, profile)["status"] == "PARTIAL"


def test_failed_capture_is_retained_and_not_called_zero_activity():
    suite, profile = full_evidence()
    suite["tenant_observability_samples"][1] = {"status": "FAIL", "observed_at_s": 13}
    result = summarize_m6(suite, profile)
    assert result["status"] == "EXECUTION_ERROR"
    assert result["timeline"]["snapshot_count"] == 5
    assert result["timeline"]["snapshots"][2]["missing_cells"] == 1


@pytest.mark.parametrize("field", ["expected_tenants", "expected_lanes"])
def test_duplicate_denominator_is_not_a_complete_contract(field):
    suite, profile = full_evidence()
    profile["tenant_observability"][field] *= 2
    result = summarize_m6(suite, profile)
    assert result["status"] == "BLOCKED"
    assert result["complete_cells"] == 0
    assert result["timeline"]["contract_valid"] is False


def test_shared_timeline_still_rejects_unplanned_contention_restart():
    suite, profile = full_evidence()
    evidence = {**profile["tenant_observability"],
                "before": suite["tenant_observability_before"],
                "during": suite["tenant_observability_samples"],
                "after": suite["tenant_observability_after_all"],
                **suite["tenant_observability_monitor"]}
    assert timeline_counts(evidence)["status"] == "INCONCLUSIVE"
    assert timeline_counts(evidence, allow_restarts=True)["status"] == "PASS"


def test_sampler_retains_sanitized_failure_then_continues(tmp_path):
    class Stop(threading.Event):
        def wait(self, timeout=None):
            return self.is_set()
    stop, samples, errors = Stop(), [], []
    calls = 0

    def collect():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private-token")
        stop.set()
        return snapshot(10, 1)

    _sample_observability(stop, collect, samples, errors, tmp_path)
    assert calls == len(samples) == 2
    assert samples[0]["status"] == "FAIL"
    assert errors == ["RuntimeError"]
    assert "private-token" not in (tmp_path / "tenant-observability-samples.json").read_text()


def test_collector_records_local_receipt_time(monkeypatch):
    monkeypatch.setattr("performance.targets.echomem.observation_run.collect_tenant_observability",
                        lambda **kwargs: {"status": "PASS"})
    monkeypatch.setattr("performance.targets.echomem.observation_run.time.monotonic", lambda: 123)
    assert _collect_observation({"base_url": "http://test.invalid"}, "private")["observed_at_s"] == 123


def test_html_shows_each_frame_and_restart_limitations(tmp_path):
    suite, profile = full_evidence()
    result = summarize_m6(suite, profile)
    path = tmp_path / "report.html"
    write_observation_report({"metrics": {"M6": result}, "status": result["status"],
                              "sampling_mode": "full"}, path)
    page = path.read_text()
    assert "查看每次快照与完整分母" in page
    assert "跨重启不计算累计差值" in page
    assert "private-" not in page
    assert "Commit 洪泛下 Search P95 最大倍率=None" not in page
    assert "多租户调度</td>" not in page


def test_coverage_and_jain_charts_use_fixed_axes(tmp_path):
    path = tmp_path / "report.html"
    write_observation_report({"status": "PARTIAL", "sampling_mode": "full", "metrics": {
        "M3": {"status": "PARTIAL", "windows": [{"commit_throughput_jain": .5}]},
        "M5": {"status": "PARTIAL", "complete_samples": 1, "expected_samples": 4},
        "M6": {"status": "PARTIAL", "complete_cells": 1, "expected_cells": 16,
               "scenarios": {"QUEUE": True}},
    }}, path)
    page = path.read_text()
    assert "width:50.00%" in page
    assert "width:6.25%" in page
    assert page.count("width:25.00%") == 2
    assert "width:100.00%" not in page
