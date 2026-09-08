from performance.targets.echomem.probes.fault_isolation import recovery_evidence


def test_quality_miss_does_not_mean_control_was_not_disabled():
    after = {"by_tenant": {"t": {"submitted": 2, "succeeded": 1, "rows": [
        {"status_code": 200, "elapsed_s": 1, "start_offset_s": 0},
        {"status_code": 200, "elapsed_s": 2, "start_offset_s": 1}]}}}
    result = recovery_evidence(after, "t", {"status": "PASS", "elapsed_s": .1})
    assert result["fault_disable_acknowledged"]
    assert result["target_http_responding"]
    assert not result["fault_recovered"]
    assert result["target_after_http_success"] == 2
    assert result["target_after_quality_success"] == 1
    assert result["target_recovery_observed_s"] == 1.1


def test_no_success_does_not_invent_recovery_timestamp():
    after = {"elapsed_s": 60, "by_tenant": {"t": {"submitted": 1, "succeeded": 0,
        "rows": [{"status_code": 503, "elapsed_s": 1}]}}}
    result = recovery_evidence(after, "t", {"status": "PASS"})
    assert result["target_recovery_observed_s"] is None
    assert not result["target_http_responding"]


def test_failed_disable_does_not_claim_recovery():
    result = recovery_evidence({}, "t", {"status": "FAIL"})
    assert not result["fault_disable_acknowledged"]
    assert not result["fault_recovered"]
    assert result["target_recovery_observed_s"] is None
