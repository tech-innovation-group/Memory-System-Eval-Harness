"""M3 accounting fixtures are not real-model stress measurements."""

import csv

from performance.targets.echomem.acceptance.observation import _fairness_window, summarize_m3
from performance.targets.echomem.orchestrator.suites import build_case_profile, six_metric_observation_cases


def write_run(tmp_path, tenants=4, *, zero_last=False):
    rows = []
    for tenant in range(tenants):
        for seq, start in enumerate((11000, 12000)):
            for task in ("read", "write"):
                rows.append(dict(op="arrival", tenant_idx=tenant, arrival_task=task,
                                 arrival_sequence=seq, planned_at_ms=start, ts_ms=start,
                                 stage_ms=0, status="ok"))
            rows.append(dict(op="read", tenant_idx=tenant, ts_ms=start + 100,
                             stage_ms=100, status="ok", quality_ok=True, degraded=False))
            key = dict(tenant_idx=tenant, session_id=f"s-{tenant}-{seq}", archive_id=f"a-{tenant}-{seq}")
            rows.append(dict(**key, op="commit_submit", ts_ms=start + 10,
                             stage_ms=10, accepted_at_ms=start + 10, http_status=202))
            complete = 12000 if seq == 0 and not (zero_last and tenant == tenants - 1) else 14000
            rows.append(dict(**key, op="commit_done", ts_ms=complete, completed_at_ms=complete,
                             terminal_at_ms=complete, observation_ended_at_ms=complete,
                             commit_terminal_state="completed", poll_evidence_version="echomem-poll-v1",
                             poll_count=2, poll_http_errors=0, http_status=200, status="ok",
                             poll_outcome="completed"))
    directory = tmp_path / f"run-{tenants}"
    directory.mkdir()
    with (directory / "records.csv").open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(set().union(*(r.keys() for r in rows))))
        writer.writeheader()
        writer.writerows(rows)
    run = dict(scenario=f"m3-fairness-{tenants}t", status="completed", output_dir=str(directory), duration_s=5,
               summary={"run_clock": {"started_wall_ms": 10000, "load_duration_s": 5},
                        "measurement_contract": {"fairness_mode": "independent-periodic-v1",
                            "measurement_start_s": 1, "measurement_end_s": 3,
                            "arrival": {task: {"scope": "per_tenant", "rps": 1, "start_s": 1, "end_s": 3}
                                        for task in ("read", "write")}}})
    return run


def test_drain_completions_do_not_inflate_window_throughput(tmp_path):
    window = _fairness_window(write_run(tmp_path), 4)
    assert window["evidence_complete"]
    assert window["duration_s"] == 2
    for row in window["tenants"]:
        assert row["commit_completed"] == 1
        assert row["commit_completed_after_window"] == 1
        assert row["commit_completed_total"] == 2
        assert row["commit_completed_per_s"] == .5
        assert row["commit_pending"] == 0
        assert row["search"]["completed"] == 2
        assert row["arrivals"]["write"]["planned"] == 2


def test_zero_completion_tenant_stays_in_jain_denominator(tmp_path):
    window = _fairness_window(write_run(tmp_path, zero_last=True), 4)
    assert window["commit_throughput_jain"] == .75
    assert window["tenants"][-1]["commit_completed"] == 0
    assert window["tenants"][-1]["longest_no_completion_s"] == 2


def test_missing_run_clock_never_claims_full_fairness(tmp_path):
    run = write_run(tmp_path)
    run["summary"].pop("run_clock")
    window = _fairness_window(run, 4)
    assert not window["evidence_complete"]
    assert window["window_start_ms"] is None
    assert window["tenants"][0]["commit_completed_after_window"] is None


def test_successful_polls_without_recognized_state_are_protocol_gap(tmp_path, monkeypatch):
    from performance.targets.echomem.acceptance import observation
    run = write_run(tmp_path)
    rows = observation._records(run)
    for row in rows:
        if row["op"] == "commit_done":
            row.update(status="error", poll_outcome="timeout", commit_terminal_state="",
                       completed_at_ms="", terminal_at_ms="", last_nonterminal_at_ms="")
    monkeypatch.setattr(observation, "_records", lambda _: rows)
    result = _fairness_window(run, 4)
    assert not result["evidence_complete"]
    assert any("需核对协议解析" in issue for issue in result["evidence_issues"])


def test_load_generator_missing_demand_is_visible(tmp_path):
    run = write_run(tmp_path)
    run["summary"]["measurement_contract"]["arrival"]["write"]["rps"] = 2
    window = _fairness_window(run, 4)
    assert not window["evidence_complete"]
    assert window["tenants"][0]["arrivals"]["write"]["missing_starts"] == 2


def test_full_and_quick_status_and_historical_contract(tmp_path):
    runs = {run["scenario"]: run for run in (write_run(tmp_path, 4), write_run(tmp_path, 8))}
    assert summarize_m3(runs, quick=False)["status"] == "MEASURED"
    assert summarize_m3(runs, quick=True)["status"] == "PARTIAL"
    runs["m3-fairness-4t"]["summary"]["measurement_contract"].pop("fairness_mode")
    assert summarize_m3(runs, quick=False)["status"] == "PARTIAL"


def test_formal_m3_is_periodic_not_barrier():
    for case in six_metric_observation_cases()[:2]:
        assert case["scene"] == "scene_c_mixed"
        assert not case["commit_barrier"]
        p = build_case_profile(case, base_url="http://unused.invalid", tenant_count=case["tenants"], auth_headers={})
        assert p.load.duration_s == 480
        assert p.load.arrival["write"].scope == "per_tenant"
        assert p.load.arrival["write"].rps == 1 / 30
        assert p.load.arrival["write"].start_s == 30
        assert p.load.arrival["write"].end_s == 300
        assert p.load.arrival["read"].rps == 1
