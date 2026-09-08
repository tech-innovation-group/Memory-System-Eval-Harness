"""Evidence completeness, not latency thresholds or live performance claims."""

import pytest

from performance.targets.echomem.acceptance.observation import summarize_m4, _commit_window_evidence
from performance.tests.test_observation_suite import _run
from performance.targets.echomem.orchestrator.suites import build_case_profile, six_metric_observation_cases


def read(tenant, *, at=1600, latency=100, **extra):
    return {"op": "read", "tenant_idx": tenant, "status": "ok", "http_status": 200,
            "stage_ms": latency, "ts_ms": at, "quality_ok": True, "query_type": "recall",
            "marker_found": True, **extra}


def task(tenant, aid="a"):
    return [
        {"op": "commit_submit", "tenant_idx": tenant, "session_id": "s", "archive_id": aid,
         "http_status": 202, "status": "ok", "accepted_at_ms": 1000, "ts_ms": 1000},
        {"op": "commit_done", "tenant_idx": tenant, "session_id": "s", "archive_id": aid,
         "http_status": 200, "status": "ok", "ts_ms": 2000, "completed_at_ms": 2000,
         "terminal_at_ms": 2000, "observation_ended_at_ms": 2000,
         "last_nonterminal_at_ms": 1800, "poll_count": 3, "poll_http_errors": 0,
         "poll_evidence_version": "echomem-poll-v1", "commit_terminal_state": "completed",
         "poll_outcome": "completed"},
    ]


def runs(tmp_path, *, mutate=None):
    result = {}
    for name in ("m4-baseline", "m4-flood-uniform", "m4-flood-single-tenant"):
        rows = [read(i, at=500 if name.endswith("baseline") else 1600) for i in range(4)]
        if not name.endswith("baseline"):
            for i in range(4):
                rows.extend(task(i if name.endswith("uniform") else 0, str(i)))
        if mutate:
            mutate(name, rows)
        run = _run(tmp_path, name, rows)
        run.update(status="completed", runner_timeout=False, summary={"measurement_contract": {
            "version": "echomem-case-v1", "tenant_count": 4, "query_mode": "recall",
            "barrier_count": 4, "barrier_waves": 1}})
        result[name] = run
    return result


def test_formal_complete_requires_audited_load_not_fast_search(tmp_path):
    def slow_failed_search(name, rows):
        if name.endswith("uniform"):
            rows[0].update(status="error", quality_ok=False, error_type="timeout", stage_ms=9000, ts_ms=10500)
    result = summarize_m4(runs(tmp_path, mutate=slow_failed_search), quick=False)
    assert result["status"] == "MEASURED"
    window = result["windows"][0]
    assert window["confirmed_overlap"]["errors"] == 1
    assert window["confirmed_overlap"]["quality_rate"] == .75
    assert window["confirmed_overlap"]["p95_ms"] == 9000
    assert window["commit_completed"] == 4
    assert window["commit_failed"] == window["commit_pending"] == 0
    assert window["evidence_issues"] == []


@pytest.mark.parametrize("found", [True, False])
def test_semantic_fact_baseline_does_not_require_marker(tmp_path, found):
    def mutate(name, rows):
        for row in rows:
            if row["op"] == "read":
                row.update(marker_found=False, quality_assertion="fixed-fact-in-items",
                           expected_fact_found=found)
    result = summarize_m4(runs(tmp_path, mutate=mutate), quick=False)
    assert result["status"] == ("MEASURED" if found else "PARTIAL")


def test_baseline_quality_failures_are_data_not_performance_gate(tmp_path):
    def mutate(name, rows):
        if name.endswith("baseline"):
            rows.append(read(0, at=550, marker_found=False, quality_ok=False))
    result = summarize_m4(runs(tmp_path, mutate=mutate), quick=False)
    assert result["status"] == "MEASURED"
    assert result["baseline"]["quality_rate"] == .8
    assert result["baseline_tenants"][0]["actual_recall_hits"] == 1
    assert result["baseline_tenants"][0]["quality_ok"] == 1
    assert result["baseline_tenants"][0]["planned_or_recorded"] == 2


@pytest.mark.parametrize("commit_rpm", [0, 60])
def test_read_only_baseline_cannot_inherit_background_writers(commit_rpm):
    case = next(c for c in six_metric_observation_cases() if c["label"] == "m4-baseline")
    case["commit_rpm"] = commit_rpm
    assert case["commit_workers"] > 0 and case["read_only"]
    profile = build_case_profile(case, base_url="http://unused.invalid", tenant_count=4, auth_headers={})
    assert profile.load.mix["write"] == 0
    assert "write" not in profile.load.arrival
    assert profile.load.workers == case["search_workers"]


def test_barrier_background_workers_are_search_only():
    for case in six_metric_observation_cases():
        if case["scene"] != "scene_barrier":
            continue
        profile = build_case_profile(case, base_url="http://unused.invalid", tenant_count=4, auth_headers={})
        assert set(profile.load.mix) == {"read"}
        assert profile.load.workers == case["search_workers"]
        assert profile.params["barrier_count"] == case["commit_barrier_count"]


def test_real_engine_read_only_baseline_emits_no_write_http(server):
    from performance.engine import Engine, load_scene
    from performance.targets.echomem.orchestrator.suites import SCENES_DIR
    _, _, url = server
    case = next(c for c in six_metric_observation_cases() if c["label"] == "m4-baseline")
    case.update(duration_s=.3, search_workers=4)
    profile = build_case_profile(case, base_url=url, tenant_count=4, auth_headers={})
    result = Engine(profile, load_scene(SCENES_DIR / "scene_capacity.py")).run()
    assert result.records
    assert {row.op for row in result.records} == {"read"}


def test_report_exposes_baseline_counts_quality_and_mean(tmp_path):
    from performance.targets.echomem.acceptance.observation import evaluate_observation, write_observation_report
    result = evaluate_observation({"runs":[]}, {}, [], quick=True, selected_metrics=["M4"])
    result["metrics"]["M4"]["baseline"] = {"planned_or_recorded":120, "mean_ms":1170.763,
        "p95_ms":2943.393, "errors":0, "quality_ok":109, "quality_missing":0}
    path = tmp_path / "report.html"
    write_observation_report(result, path)
    content = path.read_text()
    assert "独立基线" in content
    assert "1170.76" in content and ">109<" in content and ">120<" in content


@pytest.mark.parametrize("change", ["legacy", "rejected", "duplicate", "missing_tenant", "invalid_time", "no_pending", "baseline_empty_recall"])
def test_three_files_do_not_prove_formal_m4(tmp_path, change):
    def mutate(name, rows):
        if change == "baseline_empty_recall" and name.endswith("baseline"):
            rows[0]["marker_found"] = False
        if name.endswith("baseline"):
            return
        for row in rows:
            if row["op"] == "commit_done":
                if change == "legacy":
                    row.pop("poll_evidence_version")
                if change == "no_pending":
                    row.pop("last_nonterminal_at_ms")
                if change == "timeout":
                    row.update(status="error", http_status=None, terminal_at_ms=None, completed_at_ms=None,
                               commit_terminal_state="", poll_outcome="timeout")
            if row["op"] == "commit_submit":
                if change == "rejected":
                    row.update(http_status=503, archive_id="", accepted_at_ms=None)
                if change == "invalid_time":
                    row["accepted_at_ms"] = -1
        if change == "duplicate":
            rows.append(dict(rows[-1]))
        if change == "missing_tenant":
            rows[:] = [r for r in rows if not (r["op"] == "read" and r["tenant_idx"] == 3)]
    result = summarize_m4(runs(tmp_path, mutate=mutate), quick=False)
    assert result["status"] == "PARTIAL"
    assert result["evidence_issues"]
    if change == "rejected":
        assert result["windows"][0]["commit_completed"] == 0
        assert result["windows"][0]["commit_rejected"] == 4


def test_audited_nonterminal_commits_are_measured_outcomes(tmp_path):
    def mutate(name, rows):
        if name.endswith("baseline"):
            return
        for row in rows:
            if row["op"] == "commit_done":
                row.update(status="error", http_status=None, terminal_at_ms=None,
                           completed_at_ms=None, commit_terminal_state="", poll_outcome="timeout")
    result = summarize_m4(runs(tmp_path, mutate=mutate), quick=False)
    assert result["status"] == "MEASURED"
    assert result["windows"][0]["commit_pending"] == 4
    assert result["windows"][0]["drain_time_s"] is None
    assert "accepted_commit_not_terminal_by_cutoff" in result["windows"][0]["observed_conditions"]


def test_accounted_service_rejection_is_data_not_missing_load(tmp_path):
    def mutate(name, rows):
        if name != "m4-flood-single-tenant":
            return
        submit = next(row for row in rows if row["op"] == "commit_submit")
        key = submit["tenant_idx"], submit["archive_id"]
        submit.update(status="error", http_status=429, archive_id="", accepted_at_ms=None)
        rows[:] = [row for row in rows if not (
            row["op"] == "commit_done" and (row["tenant_idx"], row["archive_id"]) == key)]
    result = summarize_m4(runs(tmp_path, mutate=mutate), quick=False)
    assert result["status"] == "MEASURED"
    assert result["windows"][1]["commit_rejected"] == 1
    assert "commit_submit_rejected" in result["windows"][1]["observed_conditions"]


def test_quick_and_missing_contract_cannot_be_formal(tmp_path):
    data = runs(tmp_path)
    assert summarize_m4(data, quick=True)["status"] == "PARTIAL"
    data["m4-flood-uniform"]["summary"] = {}
    assert summarize_m4(data, quick=False)["status"] == "PARTIAL"


def test_all_key_components_duplicates_and_orphans_are_reconciled():
    rows = task(0, "a") + task(0, "b") + task(1, "a")
    evidence = _commit_window_evidence(rows)
    assert len(evidence["terminals"]) == 3
    rows.append(dict(rows[0]))
    rows.append({**rows[1], "archive_id": "orphan"})
    evidence = _commit_window_evidence(rows)
    assert len(evidence["terminals"]) == 2
    assert evidence["duplicate_receipts"] == 2
    assert evidence["orphan_observations"] == 1


def test_observed_window_is_not_confirmed_until_terminal_poll(tmp_path):
    def late_search(name, rows):
        if not name.endswith("baseline"):
            rows.append(read(0, at=2050))  # starts 1950, after last pending at 1800
            rows.append(read(0, at=9000))  # outside observation, never infinite
    result = summarize_m4(runs(tmp_path, mutate=late_search), quick=False)
    window = result["windows"][0]
    assert window["overlap"]["planned_or_recorded"] == 5
    assert window["confirmed_overlap"]["planned_or_recorded"] == 4
    assert window["uncertain_overlap_reads"] == 1
