import json
import http.client
import urllib.error
from types import SimpleNamespace

from performance.targets.echomem.acceptance.capacity_load import arrival_plan, query_for
from performance.targets.echomem.acceptance.capacity_statistics import evaluate_level, search_summary, wilson
from performance.targets.echomem.acceptance.semantic_corpus import build_corpus
from performance.targets.echomem.acceptance.capacity_report import render
from performance.targets.echomem.acceptance.capacity_publish import redacted_resources, seed_summary
from performance.targets.echomem.acceptance.capacity_recovery import crash_reason, observe_recovery
from performance.targets.echomem.acceptance.capacity_seed import CapacityActor, seed_actor
from performance.targets.echomem.acceptance.capacity_experiment import run_exploration
from performance.targets.echomem.acceptance.capacity_confirmation import (
    _aggregate,
    estimate_dau,
    recompute_confirmation,
)
from performance.targets.echomem.acceptance.capacity_evidence_merge import merge
from performance.targets.echomem.probes.docker_inspect import resource_values
from performance.targets.echomem.acceptance.preflight import config_digest, valid_model_response
from performance.targets.echomem.probes._client import _transport_error_type


def test_arrival_plan_separates_read_message_and_commit_schedules():
    plan = arrival_plan(4, 360, 1, True)
    assert 1200 < sum(e[1] == "read" for e in plan) < 1700
    assert 30 < sum(e[1] == "add" for e in plan) < 70
    assert plan == arrival_plan(4, 360, 1, True)
    assert plan != arrival_plan(4, 360, 1, True, seed=43)
    for identity in range(4):
        commits = [e for e in plan if e[1] == "commit_submit" and e[2] == identity]
        assert commits and commits[0][0] >= 30
        adds = [e for e in plan if e[1] == "add" and e[2] == identity]
        assert adds and adds[0][0] < 1
    assert not any(e[1] != "read" for e in arrival_plan(4, 60, 1, False))


def test_mixed_queries_use_all_paraphrases_with_70_30_split():
    actor = SimpleNamespace(corpus=build_corpus("unit"))
    queries = [query_for(actor, sequence, True) for sequence in range(1000)]
    assert sum(q["query_type"] == "recall" for q in queries) == 700
    assert len({q["id"] for q in queries if q["query_type"] == "recall"}) == 40
    assert len({q["id"] for q in queries if q["query_type"] == "no_recall"}) == 20


def test_zero_errors_in_100_does_not_prove_99_percent_population_success():
    assert wilson(100, 100)[0] < .99
    assert wilson(500, 500)[0] > .99


def test_failed_and_unissued_requests_are_not_hidden():
    rows = [{"sent": True, "success": True, "elapsed_s": .1},
            {"sent": True, "success": False, "elapsed_s": 10, "timeout_censored": True},
            {"sent": False, "success": False}]
    summary = search_summary(rows)
    assert summary["planned"] == 3 and summary["sent"] == 2
    assert summary["quality_rate"] == .5 and summary["timeout_censored"] == 1
    assert summary["p95_s"] == 10 and summary["success_p95_s"] == .1


def test_search_error_breakdown_preserves_complete_denominator():
    rows = [
        {"sent": True, "success": True, "http_status": 200, "elapsed_s": .1},
        {"sent": True, "success": False, "http_status": 200, "elapsed_s": .2,
         "degraded": True, "degraded_reasons": ["engine_not_enabled"]},
        {"sent": True, "success": False, "http_status": 200, "elapsed_s": .2},
        {"sent": True, "success": False, "http_status": 401, "elapsed_s": .1,
         "reason_code": "UNAUTHENTICATED"},
        {"sent": True, "success": False, "http_status": 429, "elapsed_s": .1,
         "reason_code": "TENANT_RATE_LIMITED"},
        {"sent": True, "success": False, "http_status": 503, "elapsed_s": .1,
         "reason_code": "RETRIEVAL_BUSY"},
        {"sent": True, "success": False, "http_status": None, "elapsed_s": 5,
         "transport_error_type": "timeout", "timeout_censored": True},
        {"sent": True, "success": False, "http_status": None, "elapsed_s": .1,
         "transport_error_type": "connection_reset"},
        {"sent": True, "success": False, "http_status": None, "elapsed_s": .1},
        {"sent": False, "success": False, "error": "generator_saturated"},
    ]
    summary = search_summary(rows)
    detail = summary["error_breakdown"]
    assert detail["denominator_sent"] == 9
    assert detail["outcome_partition"] == {
        "strict_success": 1,
        "http_200_quality_failure": 2,
        "http_non_200": 3,
        "transport_error": 2,
        "unclassified": 1,
    }
    assert detail["partition_total"] == 9 and detail["partition_complete"]
    assert detail["http_status_counts"] == {"200": 3, "401": 1, "429": 1, "503": 1}
    assert detail["http_4xx"] == 2 and detail["http_5xx"] == 1
    assert detail["authentication_or_permission_http"] == 1
    assert detail["rate_limited_http_429"] == 1
    assert detail["transport_errors"] == 2
    assert detail["transport_error_types"] == {"timeout": 1, "connection_reset": 1}
    assert detail["timeout_censored"] == 1
    assert detail["http_200_quality_failures"] == 2
    assert detail["http_200_degraded"] == 1
    assert detail["http_200_non_degraded_quality_failures"] == 1
    assert detail["reason_code_counts"] == {
        "UNAUTHENTICATED": 1, "TENANT_RATE_LIMITED": 1, "RETRIEVAL_BUSY": 1}
    assert detail["unclassified_failures"] == 1
    assert summary["transport_or_http_errors"] == 5


def test_search_error_breakdown_does_not_emit_none_as_http_status():
    summary = search_summary([{"sent": True, "success": False, "http_status": None,
                               "error": "URLError", "elapsed_s": .1}])
    detail = summary["error_breakdown"]
    assert detail["http_status_counts"] == {}
    assert detail["transport_error_types"] == {"URLError": 1}
    assert detail["partition_complete"]


def test_transport_exception_categories_are_stable_and_secret_free():
    assert _transport_error_type(TimeoutError("private endpoint")) == "timeout"
    assert _transport_error_type(
        urllib.error.URLError(ConnectionRefusedError("private endpoint"))
    ) == "connection_refused"
    assert _transport_error_type(
        http.client.RemoteDisconnected("private endpoint")
    ) == "remote_disconnected"


def test_observation_mode_records_slow_errors_without_performance_rejection():
    measurement = {"rows": [{"op": "read", "identity_index": 0, "query_type": "recall",
        "sent": True, "success": False, "elapsed_s": 20, "http_status": 503,
        "degraded": True, "degraded_reasons": ["model-unavailable"]}],
        "mixed": False, "identity_count": 1, "tenant_count": 1, "duration_s": 30}
    result = evaluate_level(measurement)
    assert result["status"] == "MEASURED"
    assert result["performance_requirements_applied"] is False
    assert result["search"]["p95_s"] == 20
    assert result["search"]["errors"] == 1
    assert result["search"]["http_status_counts"] == {"503": 1}
    assert "slo_observed" not in result


def test_observation_report_does_not_inherit_zero_capacity_from_old_slo():
    html = render({"assessment_mode": "observe", "max_hot_users": 0,
                   "dau": {"status": "ZERO_UNDER_LOCKED_SLO"}, "levels": []})
    assert "绝对最大容量<strong>尚未确定</strong>" in html
    assert "DAU<strong>0</strong>" not in html
    assert "结论已撤销" in html


def test_actual_oom_or_restart_is_boundary_evidence():
    assert crash_reason({"restart_count": 0}, {"running": True, "restart_count": 1}) == "container-restarted-during-load"
    assert crash_reason({}, {"running": False, "oom_killed": True}) == "container-oom"
    assert crash_reason({}, {"running": True, "oom_killed": False}) is None


def test_slow_but_completed_read_is_not_a_capacity_boundary():
    result = observe_recovery([], {"rows": [{"op": "read", "sent": True,
                              "http_status": 200, "elapsed_s": 25, "success": False}]})
    assert result["status"] == "NO_BOUNDARY_OBSERVED"


def test_bad_credentials_are_not_a_capacity_boundary():
    result = observe_recovery([], {"rows": [{"op": "read", "sent": True, "http_status": 401}]})
    assert result["status"] == "INCONCLUSIVE"
    assert result["capacity_boundary_proven"] is False


def test_commit_drain_is_not_counted_as_in_window_throughput():
    measurement = {"rows": [{"op": "commit_submit", "accepted_202": True},
        {"op": "commit_done", "success": True, "end_s": 90, "elapsed_s": 80, "status": "completed"}],
        "duration_s": 60, "identity_count": 1, "tenant_count": 1, "mixed": True}
    result = evaluate_level(measurement)
    assert result["commit"]["completed"] == 1
    assert result["commit"]["completed_in_window"] == 0
    assert result["commit"]["completed_rps"] == 0


def test_commit_summary_reports_peak_in_flight_and_submission_window():
    measurement = {"rows": [
        {"op": "commit_submit", "accepted_202": True, "start_s": 30, "accepted_at_s": 31},
        {"op": "commit_submit", "accepted_202": True, "start_s": 40, "accepted_at_s": 41},
        {"op": "commit_done", "success": True, "accepted_at_s": 31, "end_s": 50,
         "elapsed_s": 19, "status": "completed"},
        {"op": "commit_done", "success": True, "accepted_at_s": 41, "end_s": 60,
         "elapsed_s": 19, "status": "completed"},
    ], "duration_s": 60, "identity_count": 1, "tenant_count": 1, "mixed": True}
    commit = evaluate_level(measurement)["commit"]
    assert commit["peak_in_flight"] == 2
    assert commit["submission_window_s"] == 10


def test_manual_restart_is_detected_even_without_restart_counter_change():
    assert crash_reason({"started_at": "before"}, {"running": True, "started_at": "after"}) == "container-restarted-during-load"


def test_exploration_observation_keeps_advancing_with_slow_requests(tmp_path, monkeypatch):
    actors = [SimpleNamespace(tenant_index=i, user_index=0, client=SimpleNamespace(
        base_url="unit", auth_key="unit", tenant_id=f"t{i}", user_id=f"u{i}",
        account_id=f"t{i}", agent_id="unit"), corpus={}, write_session="s") for i in range(4)]
    monkeypatch.setattr("performance.targets.echomem.acceptance.capacity_experiment.provision_actors",
                        lambda *args, **kwargs: actors)
    monkeypatch.setattr("performance.targets.echomem.acceptance.capacity_experiment.prepare_actors",
                        lambda *args, **kwargs: {"status": "PASS", "actors": []})

    def measured(selected, **kwargs):
        return {"rows": [{"op": "read", "identity_index": i, "query_type": "recall",
                           "sent": True, "http_status": 200, "success": False,
                           "elapsed_s": 20} for i in range(len(selected))],
                "mixed": False, "duration_s": 30, "identity_count": len(selected),
                "tenant_count": len(selected)}

    monkeypatch.setattr("performance.targets.echomem.acceptance.capacity_experiment.measure", measured)
    result = run_exploration(base_url="unit", output=tmp_path / "observation",
                             topology="cross-tenant", levels=[1, 2, 4], warmup_s=1, duration_s=1)
    assert result["status"] == "MEASURED"
    assert [level["hot_users"] for level in result["levels"]] == [1, 2, 4]
    assert result["highest_measured_hot_users"] == 4
    assert result["max_hot_users"] is None


def test_search_summary_separates_engine_time_from_unattributed_residual():
    rows = [{"sent": True, "success": True, "elapsed_s": 1.0,
             "engine_results": [{"engine_id": "atomic_engine", "duration_seconds": .1},
                                {"engine_id": "base_engine", "duration_seconds": .2}]}]
    summary = search_summary(rows)
    assert summary["engine_timings"]["atomic_engine"]["p95_s"] == .1
    assert abs(summary["unattributed_residual_p95_s"] - .7) < 1e-9


def test_search_summary_splits_route_paths_without_hiding_unobserved_rows():
    rows = [
        {"sent": True, "success": True, "elapsed_s": .2,
         "executed_layers": ["rule", "semantic"]},
        {"sent": True, "success": True, "elapsed_s": 2.1,
         "executed_layers": ["rule", "semantic", "llm"]},
        {"sent": True, "success": True, "elapsed_s": .4},
    ]
    timings = search_summary(rows)["route_path_timings"]
    assert timings["fast_path"]["observations"] == 1
    assert timings["intent_llm"]["observations"] == 1
    assert timings["intent_llm"]["p50_s"] == 2.1
    assert timings["unobserved"]["observations"] == 1
    assert sum(value["fraction_of_sent"] for value in timings.values()) == 1


def test_route_path_counts_preserve_failures_without_timing_and_unknown_layers():
    rows = [
        {"sent": True, "success": True, "executed_layers": ["rule"], "elapsed_s": .3},
        {"sent": True, "success": False, "executed_layers": ["llm"], "elapsed_s": None},
        {"sent": True, "success": False, "executed_layers": ["llm"],
         "elapsed_s": 4, "http_status": 503, "degraded": True},
        {"sent": True, "success": False, "executed_layers": ["unknown_llm_layer"], "elapsed_s": 1},
        {"sent": True, "success": True, "executed_layers": []},
        {"sent": False, "elapsed_s": 20, "executed_layers": ["rule"]},
    ]
    summary = search_summary(rows)
    paths = summary["route_path_timings"]
    assert sum(value["observations"] for value in paths.values()) == summary["sent"] == 5
    assert sum(value["fraction_of_sent"] for value in paths.values()) == 1
    assert paths["intent_llm"]["observations"] == 2
    assert paths["intent_llm"]["latency_observations"] == 1
    assert paths["intent_llm"]["latency_missing_or_invalid"] == 1
    assert paths["intent_llm"]["errors"] == 2
    assert paths["intent_llm"]["degraded"] == 1
    assert paths["intent_llm"]["p95_s"] == 4
    assert paths["unobserved"]["observations"] == 2
    assert paths["unobserved"]["latency_missing_or_invalid"] == 1


def test_invalid_search_durations_are_counted_without_polluting_percentiles():
    import json

    rows = [{"sent": True, "success": True, "elapsed_s": duration,
             "executed_layers": ["rule"], "start_s": index * 10}
            for index, duration in enumerate([None, float('nan'), float('inf'), -1, True, "oops", .3])]
    summary = search_summary(rows)
    assert summary["sent"] == summary["success"] == 7
    assert summary["latency_missing_or_invalid"] == 6
    assert summary["p95_s"] == summary["success_p95_s"] == .3
    assert summary["p95_block_bootstrap_95"] is None
    assert summary["route_path_timings"]["fast_path"]["observations"] == 7
    assert summary["route_path_timings"]["fast_path"]["latency_observations"] == 1
    json.dumps(summary, allow_nan=False)


def test_missing_identity_cannot_be_capacity_pass():
    measurement = {"rows": [{"op": "read", "identity_index": 0, "query_type": "recall",
                             "sent": True, "success": True, "elapsed_s": .1, "start_s": n}
                            for n in range(100)],
                   "mixed": False, "identity_count": 2, "tenant_count": 2, "duration_s": 100}
    result = evaluate_level(measurement, assessment_mode="slo")
    assert result["status"] == "INCONCLUSIVE"
    assert len(result["cells"]) == 2


def test_completion_mode_uses_request_completion_without_latency_or_quality_thresholds():
    rows = [{"op": "read", "identity_index": 0, "query_type": "recall",
             "sent": True, "http_status": 200, "success": False,
             "elapsed_s": 12, "start_s": n, "end_s": n + 12}
            for n in range(20)]
    result = evaluate_level({"rows": rows, "mixed": False, "identity_count": 1,
                             "tenant_count": 1, "duration_s": 20},
                            assessment_mode="completion")
    assert result["status"] == "PASS"
    assert result["completion_contract"]["search_all_scheduled_sent"]
    assert not result["latency_threshold_applied"]
    assert not result["quality_threshold_applied"]


def test_completion_mode_fails_on_first_http_or_transport_error():
    rows = [{"op": "read", "identity_index": 0, "query_type": "recall",
             "sent": True, "http_status": 429 if n == 3 else 200,
             "success": n != 3, "elapsed_s": .1, "start_s": n, "end_s": n + .1}
            for n in range(20)]
    result = evaluate_level({"rows": rows, "mixed": False, "identity_count": 1,
                             "tenant_count": 1, "duration_s": 20},
                            assessment_mode="completion")
    assert result["status"] == "FAIL"
    assert result["completion_contract"]["search_http_or_transport_errors"] == 1


def test_completion_confirmation_is_zero_error_level_not_capacity_maximum(tmp_path):
    from performance.targets.echomem.acceptance.capacity_confirmation import _finalize
    suite = {"assessment_mode": "completion", "levels": [
        {"hot_users": 2, "status": "PASS", "mixed_aggregate": {}},
        {"hot_users": 4, "status": "FAIL", "mixed_aggregate": {}},
    ]}
    result = _finalize(suite)
    assert result["boundary"]["status"] == "ZERO_ERROR_CONFIRMED"
    assert result["zero_error_max_hot_users"] == 2
    assert result["first_nonzero_error_hot_users"] == 4
    assert result["max_hot_users"] is None
    assert result["dau"] is None


def test_capacity_evidence_merge_keeps_zero_error_separate_from_hard_maximum(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"levels": [{"hot_users": 4,
        "pure_aggregate": {"search": {"sent": 10}},
        "mixed_aggregate": {"search": {"sent": 10}, "commit": {"submitted": 4}}}]}))
    result = merge([source], zero_error_level=4, first_nonzero_error=8)
    assert result["max_hot_users"] is None
    assert result["zero_error_max_hot_users"] == 4
    assert result["boundary"]["not_a_capacity_maximum"] is True
    assert len(result["levels"]) == 2


def test_report_never_labels_highest_attempted_level_as_maximum():
    html = render({"status": "BLOCKED", "max_hot_users": 128, "dau": 12345,
                   "environment": {}, "seed": {"actors": []}, "levels": []})
    assert "尚未测得最大热用户数和 DAU" in html
    assert "<strong>128</strong>" not in html
    assert "<strong>12345</strong>" not in html


def test_report_escapes_untrusted_evidence_and_does_not_dump_config():
    html = render({"environment": {"host": "<script>alert(1)</script>", "api_key": "unit-secret"}})
    assert "<script>" not in html and "unit-secret" not in html


def test_report_renders_confirmed_repeats_and_conditional_dau():
    search = {"planned": 300, "sent": 300, "success": 300, "mean_s": .2,
              "p95_s": .4, "p99_s": .6, "degraded": 0, "timeout_censored": 0,
              "quality_rate": 1.0, "atomic_p95_s": .05,
              "route_path_timings": {"fast_path": {"observations": 200,
                  "fraction_of_sent": 2 / 3, "mean_s": .15, "p50_s": .14,
                  "p95_s": .25, "min_s": .1, "max_s": .3},
                  "intent_llm": {"observations": 100, "fraction_of_sent": 1 / 3,
                  "mean_s": 1.8, "p50_s": 1.7, "p95_s": 2.2,
                  "min_s": 1.2, "max_s": 2.5}}}
    commit = {"accepted_202": 3, "completed": 3, "p95_s": 4.0}
    aggregate = {"status": "PASS", "search": search, "commit": commit,
                 "duration_s": 540, "identity_count": 1, "tenant_count": 1,
                 "mixed": False, "effective_search_rps": .55}
    report = {"status": "PASS", "manifest": {}, "seed": {"actors": []},
              "boundary": {"status": "CONFIRMED", "evidence": "three-repeats",
                           "highest_pass": 1, "first_fail": 2},
              "max_hot_users": 1,
              "dau": {"status": "CONDITIONAL_ESTIMATE", "estimates": [
                  {"conservative_dau": 100.0}, {"conservative_dau": 300.0}]},
              "levels": [{"hot_users": 1, "tenant_count": 1, "users_per_tenant": 1,
                          "status": "PASS", "pure_aggregate": aggregate,
                          "mixed_aggregate": {**aggregate, "mixed": True},
                          "repeats": [{"repeat": 1, "seed_status": "PASS",
                                       "pure": aggregate,
                                       "mixed": {**aggregate, "mixed": True}}]}]}
    html = render(report)
    assert "最大热用户数<strong>1</strong>" in html
    assert "条件估算 100–300" in html
    assert "三轮边界确认" in html
    assert "Commit 完成" in html
    assert "Search 路由路径延迟拆解" in html
    assert "意图 LLM 路径" in html


def test_observation_report_renders_route_path_breakdown():
    search = {"planned": 2, "sent": 2, "success": 2, "errors": 0,
              "mean_s": 1.1, "p50_s": 1.1, "p95_s": 2.0, "p99_s": 2.0,
              "timeout_censored": 0, "not_sent": 0, "degraded": 0,
              "transport_or_http_errors": 0, "http_status_counts": {"200": 2},
              "atomic_p95_s": .1, "route_path_timings": {
                  "fast_path": {"observations": 1, "fraction_of_sent": .5,
                                "mean_s": .2, "p50_s": .2, "p95_s": .2,
                                "min_s": .2, "max_s": .2},
                  "intent_llm": {"observations": 1, "fraction_of_sent": .5,
                                 "mean_s": 2.0, "p50_s": 2.0, "p95_s": 2.0,
                                 "min_s": 2.0, "max_s": 2.0}}}
    html = render({"assessment_mode": "observe", "levels": [{
        "search": search, "commit": {}, "cells": [], "identity_count": 1,
        "tenant_count": 1, "duration_s": 1, "mixed": False,
        "load_mode": "search", "sent_search_rps": 2.0,
        "effective_search_rps": 2.0}]})
    assert "Search 路由路径延迟拆解" in html
    assert "快速路径（未调用意图 LLM）" in html
    assert "意图 LLM 路径" in html


def test_observation_report_renders_complete_error_breakdown():
    search = search_summary([
        {"sent": True, "success": True, "http_status": 200, "elapsed_s": .1},
        {"sent": True, "success": False, "http_status": 429, "elapsed_s": .1,
         "reason_code": "TENANT_RATE_LIMITED"},
        {"sent": True, "success": False, "http_status": None, "elapsed_s": 5,
         "transport_error_type": "timeout", "timeout_censored": True},
    ])
    html = render({"assessment_mode": "observe", "levels": [{
        "search": search, "commit": {}, "cells": [], "identity_count": 1,
        "tenant_count": 1, "duration_s": 1, "mixed": False,
        "load_mode": "search", "sent_search_rps": 3.0,
        "effective_search_rps": 1.0}]})
    assert "Search 错误完整拆分" in html
    assert "TENANT_RATE_LIMITED: 1" in html
    assert "timeout: 1" in html
    assert "分母对账" in html
    assert "不能据此断言是模型 API Key" in html


def test_redacted_zero_capacity_report_has_no_broken_seed_link():
    html = render({"status": "FAIL", "publication": {"redacted": True},
                   "manifest": {}, "seed_summary": {"queries": 0, "strict_valid": 0},
                   "boundary": {"status": "CONFIRMED", "evidence": "three-repeats",
                                "highest_pass": 0, "first_fail": 1},
                   "max_hot_users": 0,
                   "dau": {"status": "ZERO_UNDER_LOCKED_SLO", "estimates": []},
                   "levels": []})
    assert "容量边界为 0" in html
    assert "seed-evidence.json" not in html
    assert "最大热用户数<strong>0</strong>" in html


def test_marker_failure_does_not_skip_fixed_semantic_questions():
    corpus = build_corpus("unit")

    class UnitClient:
        tenant_id = "unit"
        agent_id = "unit"

        def open_session(self, *args, **kwargs):
            return "unit-session", None

        def add_message(self, *args):
            return SimpleNamespace(status_code=200)

        def commit(self, *args):
            return SimpleNamespace(status_code=202, payload={"archive_id": "unit-archive"})

        def commit_status(self, *args):
            return SimpleNamespace(status_code=200, payload={"status": "completed"})

        def search(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, payload={"result": {"items": []}})

        def get_commit_memories(self, *args):
            return SimpleNamespace(status_code=200, payload={"memories": []})

        def request(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, payload={"result": {
                "items": [{"text": " ".join(f["value"] for f in corpus["facts"])}]}})

    snapshots = []
    result = seed_actor(CapacityActor(0, 0, UnitClient(), corpus),
                        checkpoint=lambda row: snapshots.append(len(row["queries"])))
    assert result["marker_visible"] is False
    assert result["valid_semantic_queries"] == 40
    assert result["status"] == "PASS"
    assert 40 in snapshots


def test_docker_resources_keep_missing_data_unknown():
    empty = resource_values({})
    assert empty["cpu_percent_one_core_100"] is None
    assert empty["rss_bytes"] is None
    sample = resource_values({"cpu_stats": {"cpu_usage": {"total_usage": 50},
        "system_cpu_usage": 200, "online_cpus": 4},
        "precpu_stats": {"cpu_usage": {"total_usage": 25}, "system_cpu_usage": 100},
        "memory_stats": {"usage": 300, "limit": 800, "stats": {"anon": 200, "inactive_file": 80}}})
    assert sample["cpu_percent_one_core_100"] == 100
    assert sample["working_set_bytes"] == 220
    assert sample["rss_bytes"] == 200


def test_intent_preflight_does_not_accept_reasoning_without_answer():
    payload = {"choices": [{"message": {"content": "", "reasoning_content": "unit reasoning"}}]}
    assert valid_model_response("llm", payload)
    assert not valid_model_response("llm", payload, require_content=True)
    assert config_digest([{"model": "x", "extra_params": {"enable_thinking": False}}]) != \
        config_digest([{"model": "x", "extra_params": {"enable_thinking": True}}])


def test_capacity_exploration_stops_after_first_failed_level(tmp_path, monkeypatch):
    actors = [SimpleNamespace(tenant_index=i, user_index=0, client=SimpleNamespace(
        base_url="unit", auth_key="unit", tenant_id=f"t{i}", user_id=f"u{i}",
        account_id=f"t{i}", agent_id="unit"), corpus={}, write_session="s") for i in range(4)]
    monkeypatch.setattr("performance.targets.echomem.acceptance.capacity_experiment.provision_actors",
                        lambda *args, **kwargs: actors)
    monkeypatch.setattr("performance.targets.echomem.acceptance.capacity_experiment.prepare_actors",
                        lambda *args, **kwargs: {"status": "PASS", "actors": []})
    measured = []

    def fake_measure(selected, **kwargs):
        measured.append(len(selected))
        return {"identity_count": len(selected)}

    monkeypatch.setattr("performance.targets.echomem.acceptance.capacity_experiment.measure", fake_measure)
    monkeypatch.setattr("performance.targets.echomem.acceptance.capacity_experiment.evaluate_level",
                        lambda value, **kwargs: {"status": "PASS" if value["identity_count"] < 4 else "FAIL"})
    result = run_exploration(base_url="unit", output=tmp_path / "m1", topology="cross-tenant",
                             levels=[1, 2, 4], warmup_s=1, duration_s=1, assessment_mode="slo")
    assert measured == [1, 1, 2, 2, 4, 4]
    assert result["boundary"] == {"status": "CANDIDATE", "highest_pass": 2, "first_fail": 4}
    assert result["max_hot_users"] is None and result["dau"] is None


def test_m1_search_uses_locked_end_to_end_and_atomic_slo():
    def result(elapsed, atomic):
        rows = [{"op": "read", "identity_index": 0, "query_type": "recall", "sent": True,
                 "success": True, "elapsed_s": elapsed, "start_s": i,
                 "engine_results": [{"engine_id": "atomic_engine", "duration_seconds": atomic}]}
                for i in range(100)]
        return evaluate_level({"rows": rows, "mixed": False, "identity_count": 1,
            "tenant_count": 1, "duration_s": 100}, assessment_mode="slo")
    assert result(2.49, 1.99)["status"] == "PASS"
    assert result(2.5, 1.99)["status"] == "FAIL"
    assert result(2.49, 2.0)["status"] == "FAIL"


def test_no_recall_does_not_require_an_atomic_engine_execution():
    rows = []
    for kind in ("recall", "no_recall"):
        for i in range(100):
            rows.append({"op": "read", "identity_index": 0, "query_type": kind,
                         "sent": True, "success": True, "elapsed_s": .2, "start_s": i,
                         "engine_results": ([{"engine_id": "atomic_engine",
                                             "duration_seconds": .05}] if kind == "recall" else [])})
    rows.extend([{"op": "commit_submit", "identity_index": 0, "accepted_202": True,
                  "accepted_at_s": 1},
                 {"op": "commit_done", "identity_index": 0, "success": True,
                  "elapsed_s": 1, "end_s": 2}])
    result = evaluate_level({"rows": rows, "mixed": True, "identity_count": 1,
        "tenant_count": 1, "duration_s": 300}, assessment_mode="slo")
    assert result["cells"][1]["query_type"] == "no_recall"
    assert result["cells"][1]["atomic_p95_s"] is None
    assert result["slo_observed"] is True


def test_confirmation_aggregate_shifts_repeats_and_dau_is_conditional():
    source = {"mixed": True, "identity_count": 1, "tenant_count": 1,
              "per_user_search_rps": 1, "request_timeout_s": 10, "commit_deadline_s": 180,
              "duration_s": 10, "elapsed_with_drain_s": 11, "planned_search": 1,
              "rows": [{"op": "read", "start_s": 2, "end_s": 3}]}
    aggregate = _aggregate([source, source])
    assert aggregate["duration_s"] == 20 and aggregate["repeat_count"] == 2
    assert aggregate["rows"][1]["start_s"] == 22
    estimates = estimate_dau({"effective_search_rps": 8, "duration_s": 100,
                              "identity_count": 2, "per_user_commit_interval_s": 300,
                              "commit": {"completed": 20}})
    assert len(estimates) == 6
    assert all(row["conservative_dau"] <= row["search_limited_dau"] for row in estimates)
    assert all(row["steady_commit_capacity_rps"] == 2 / 300 for row in estimates)


def test_confirmation_can_be_recomputed_from_raw_files(tmp_path):
    root = tmp_path / "confirmation"
    root.mkdir()
    (root / "report.json").write_text(json.dumps({
        "topology": "cross-tenant", "levels_requested": [1], "fixed_tenants": 4,
        "repeats": 3, "pure_duration_s": 180, "mixed_duration_s": 300,
        "manifest": {"cpus": 4, "memory_bytes": 8 * 1024**3},
    }))

    def measurement(mixed):
        rows = []
        for kind in (("recall", "no_recall") if mixed else ("recall",)):
            for index in range(150):
                rows.append({"op": "read", "identity_index": 0, "query_type": kind,
                             "sent": True, "success": True, "elapsed_s": .2,
                             "start_s": index,
                             "engine_results": ([{"engine_id": "atomic_engine",
                                                  "duration_seconds": .05}]
                                                if kind == "recall" else [])})
        if mixed:
            rows.extend([{"op": "commit_submit", "identity_index": 0,
                          "accepted_202": True, "accepted_at_s": 1},
                         {"op": "commit_done", "identity_index": 0, "success": True,
                          "elapsed_s": 1, "end_s": 2}])
        return {"rows": rows, "mixed": mixed, "identity_count": 1,
                "tenant_count": 1, "duration_s": 300 if mixed else 180,
                "elapsed_with_drain_s": 300 if mixed else 180,
                "planned_search": len([r for r in rows if r["op"] == "read"]),
                "per_user_search_rps": 1, "request_timeout_s": 10,
                "commit_deadline_s": 180, "per_user_commit_interval_s": 300}

    for repeat in range(1, 4):
        directory = root / f"level-1-repeat-{repeat:02d}"
        directory.mkdir()
        (directory / "seed-evidence.json").write_text(json.dumps({"status": "PASS"}))
        (directory / "pure-measurement.json").write_text(json.dumps(measurement(False)))
        (directory / "mixed-measurement.json").write_text(json.dumps(measurement(True)))
    result = recompute_confirmation(root, assessment_mode="slo")
    assert result["derived_from_raw"] is True
    assert result["levels"][0]["status"] == "PASS"
    assert result["levels"][0]["pure_aggregate"]["search"]["engine_timings"]["atomic_engine"]["p95_s"] == .05
    assert result["status"] == "INCONCLUSIVE"  # no adjacent failed level yet


def test_published_seed_and_resources_are_aggregate_only(tmp_path):
    directory = tmp_path / "level-1-repeat-01"
    directory.mkdir()
    (directory / "seed-evidence.json").write_text(json.dumps({"actors": [{
        "status": "PASS", "auth_key": "must-not-leak", "input_documents": 5,
        "input_characters": 400, "queries": [{"success": True,
        "matched_expected_fact": True, "degraded": False, "hit_count": 1}],
    }]}))
    (directory / "pure-measurement.json").write_text("{}")
    (directory / "mixed-measurement.json").write_text("{}")
    (directory / "pure-measurement-resources.json").write_text(json.dumps([{
        "cpu_percent_one_core_100": 50, "rss_bytes": 100,
        "working_set_bytes": 90, "pids": 4, "container": "private-name",
    }]))
    seed = seed_summary(tmp_path)
    resources, summary = redacted_resources(tmp_path)
    assert seed["strict_valid"] == 1 and "auth_key" not in json.dumps(seed)
    assert resources == [{"cpu_percent_one_core_100": 50, "rss_bytes": 100,
                          "working_set_bytes": 90, "pids": 4}]
    assert summary["rss_peak_bytes"] == 100
