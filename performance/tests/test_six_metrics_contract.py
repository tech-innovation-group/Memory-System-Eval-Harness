"""Deterministic evidence-contract tests, not model/performance results."""

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from performance.targets.echomem.acceptance.six_metrics import (
    configure_profile, evaluate_six, jain, module_issues, search_stats, write_report,
)
from performance.targets.echomem.orchestrator.suites import six_metric_cases
from performance.targets.echomem.protocol import anchor_marker, recall_quality


def evidence_run(root, name, rows):
    directory = Path(root) / name
    directory.mkdir()
    with (directory / "records.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(set().union(*(row.keys() for row in rows))))
        writer.writeheader()
        writer.writerows(rows)
    return {"scenario": name, "output_dir": str(directory), "duration_s": 120}


def good_reads(count=100, tenants=4, **overrides):
    return [{"op": "read", "status": "ok", "quality_ok": True, "stage_ms": 100,
             "tenant_idx": index % tenants, "ts_ms": 5000 + index, **overrides}
            for index in range(count)]


class EvidenceContractTests(unittest.TestCase):
    def test_recovery_cursor_compares_accepted_key_without_exporting_it(self):
        from performance.targets.echomem.probes.commit_recovery import idempotency_cursor_evidence
        key = 'private-retry-key-for-unit-test'
        receipt = {'archive_id': 'original', 'status': 'completed'}
        cursor = {'last_successful': receipt}
        accepted = {'idempotency_key': key}
        evidence = idempotency_cursor_evidence(cursor, 200, accepted, 'original', key)
        self.assertTrue(evidence['key_persistence_failed'])
        self.assertNotIn(key, json.dumps(evidence))
        for status, echoed, current in (
            (404, accepted, receipt), (200, {}, receipt),
            (200, accepted, {**receipt, 'archive_id': 'another'}),
            (200, accepted, {**receipt, 'status': 'pending'}),
            (200, accepted, {**receipt, 'idempotency_key': key}),
        ):
            with self.subTest(http=status, echo=bool(echoed), receipt=current):
                result = idempotency_cursor_evidence({'last_successful': current}, status, echoed, 'original', key)
                self.assertFalse(result['key_persistence_failed'])

    def test_invalid_fault_duration_stops_before_workload(self):
        from performance.targets.echomem.probes import fault_isolation as probe
        for value in (600, 0, float('nan'), float('inf')):
            with self.subTest(duration=value):
                checks = []
                ctx = SimpleNamespace(params={"endpoint": "http://unused/api/inspect/test-control/fault",
                                              "duration_s": value},
                                      check=lambda name, **kw: checks.append({"name": name, **kw}))
                with patch.object(probe, 'sample_search') as search, patch.object(probe, 'control') as control:
                    probe.run(ctx)
                search.assert_not_called()
                control.assert_not_called()
                self.assertFalse(json.loads(checks[0]['detail'])['workload_started'])
        with tempfile.TemporaryDirectory() as root:
            tenant_file = Path(root) / 'tenants.json'
            tenant_file.write_text(json.dumps({'tenants': [{'tenant_id': str(n)} for n in range(4)]}))
            config = {'name': '4U8G', 'tenant_config': str(tenant_file), 'base_url': 'http://unused',
                      'fault_isolation': {'duration_s': 600}}
            with self.assertRaisesRegex(ValueError, '300'):
                configure_profile(config)
            config['fault_isolation']['duration_s'] = 300
            self.assertEqual(configure_profile(config, live=False)['fault_isolation']['duration_s'], 300)

    def test_fault_control_error_is_retained_separately_from_baseline(self):
        payload = {'target_tenant': 'a', 'fault_type': 'reject', 'repetition': 1, 'checks': [
            {'name': 'fault-control-enable', 'status': 'FAIL', 'detail': json.dumps({'status_code': 400})},
            {'name': 'fault-isolation', 'status': 'INCONCLUSIVE', 'detail': json.dumps({'baseline_healthy': False})}]}
        result = evaluate_six({'fault_isolation': {'cases': [payload]}}, {})
        observed = next(c['observed'] for c in result['checks'] if c['id'] == 'M2')
        self.assertEqual(observed['control_enable_failed_cases'], 1)
        self.assertEqual(observed['cases'][0]['control_checks'][0]['http_status'], 400)

    def test_module_issues_preserve_degradation_and_request_failures(self):
        rows = [{"op": "read", "status": "ok", "quality_ok": False, "degraded": True,
                 "degraded_reasons": '["engine_not_enabled:resource_engine"]'},
                {"op": "read", "status": "error", "http_status": "401"}]
        issues = module_issues({"recall-baseline": rows}, {})
        self.assertEqual(sum(issue["count"] for issue in issues), 2)
        self.assertEqual({issue["module"] for issue in issues}, {"Recall 路由 / 引擎配置", "鉴权 / 测试身份"})

    def test_recovery_observes_original_completion_before_same_key_replay(self):
        from performance.targets.echomem.probes import commit_recovery as probe
        for final in ("completed", "failed"):
            with self.subTest(final=final):
                events, checks = [], []
                client = MagicMock()
                response = lambda payload, code=200: SimpleNamespace(payload=payload, status_code=code, error="")
                client.open_session.return_value = ("session-unit", {})
                client.add_message.return_value = response({"id": "message-unit"})
                states = iter(("pending", final))

                def status(*args):
                    state = next(states)
                    events.append(state)
                    return response({"status": {"status": state}})

                def commit(*args, **kwargs):
                    events.append("commit")
                    return response({"archive_id": "archive-unit", "replayed": len(events) > 1}, 202)

                def kill(*args, **kwargs):
                    events.append("kill")
                    return {"kill_returncode": 0, "start_returncode": 0}

                client.commit.side_effect = commit
                client.commit_status.side_effect = status
                for name in ("get_history", "get_commit_memories", "get_archive", "fs_read"):
                    getattr(client, name).return_value = response({"messages": [{"id": "message-unit"}]})
                ctx = SimpleNamespace(base_url="http://not-contacted.invalid", params={
                    "tenant_config": "unit.json", "container": "unit-only", "messages": 1,
                    "kill_delay_s": 0, "require_accepted_202": True},
                    check=lambda name, **fields: checks.append({"name": name, **fields}))
                with patch.object(probe, "EchoMemHTTP", return_value=client), \
                     patch.object(probe, "health", return_value={"healthy": True, "status_code": 200}), \
                     patch.object(probe, "load_tenant", return_value={"auth_key": "unit-key", "tenant_id": "unit", "user_id": "unit"}), \
                     patch.object(probe, "kill_and_start", side_effect=kill):
                    probe.run(ctx)
                expected = ["commit", "pending", "kill", final]
                if final == "completed":
                    expected.append("commit")
                self.assertEqual(events, expected)
                detail = json.loads(next(c["detail"] for c in checks if c["name"] == "commit-recovery"))
                self.assertEqual(detail["autonomous_recovery_observed"], final == "completed")

    def test_seed_visibility_uses_natural_question_not_random_marker(self):
        from performance.targets.echomem.orchestrator.runner import _prepare_seed
        marker = "PERFANCHOR-0-0-0-abcdef1234"
        query = "你还记得我之前记录的第一批事项吗？请告诉我它的编号。"
        queries = [query]
        query_cases = {query: {"id": "seed-0", "query": query, "query_type": "recall", "aliases": [marker]}}
        calls = []

        def search(session, query, timeout_s):
            calls.append(query)
            return SimpleNamespace(status_code=200, payload={"result": {
                "items": [{"text": f"项目编号 {marker}"}], "status": "degraded",
                "degraded_reasons": ["engine_not_enabled:resource_engine"]}})

        client = SimpleNamespace(search=search, agent_id="agent", user_id="user", account_id="account")
        context = SimpleNamespace(tenant_id="tenant", auth_key="unit-test-credential", queries=queries,
                                  query_cases=query_cases, client=client)
        with patch("performance.targets.echomem.orchestrator.runner.load_tenant_specs", return_value=[]), \
             patch("performance.targets.echomem.orchestrator.runner.TenantPreparer") as preparer:
            preparer.return_value.prepare.return_value = [context]
            preparer.return_value.keys_independent.return_value = True
            preparer.return_value.identity_mode.return_value = "independent"
            _, summary = _prepare_seed("http://not-contacted.invalid", "tenants.json", 1, 1, 1)
        self.assertEqual(calls, [query])
        self.assertNotIn(marker, calls[0])
        self.assertTrue(summary["visibility"][0]["visible"])
        self.assertFalse(summary["visibility"][0]["quality_ok"])

    def test_http_200_wrong_memory_does_not_pass(self):
        self.assertFalse(recall_quality({"result": {"items": [{"text": "other tenant"}]}}, "PERFANCHOR-A")["quality_ok"])

    def test_query_echo_outside_items_does_not_count(self):
        self.assertFalse(recall_quality({"result": {"items": [], "debug": "PERFANCHOR-A"}}, "PERFANCHOR-A")["quality_ok"])

    def test_degraded_hit_does_not_pass(self):
        quality = recall_quality({"result": {"items": ["PERFANCHOR-A"], "status": "degraded",
                                             "degraded_reasons": ["engine_not_enabled:resource_engine"]}}, "PERFANCHOR-A")
        self.assertFalse(quality["quality_ok"])
        self.assertTrue(quality["marker_found"])
        self.assertIn("engine_not_enabled:resource_engine", quality["degraded_reasons"])

    def test_no_recall_distinguishes_empty_and_invalid(self):
        self.assertTrue(recall_quality({"result": {"items": []}}, query_type="no_recall")["quality_ok"])
        self.assertFalse(recall_quality({}, query_type="no_recall")["quality_ok"])

    def test_every_failed_request_stays_in_denominator(self):
        rows = [{"op": "read", "status": "ok", "quality_ok": True, "stage_ms": 100},
                {"op": "read", "status": "error", "quality_ok": False, "stage_ms": 30000}]
        observed = search_stats(rows)
        self.assertEqual(observed["quality_rate"], .5)
        self.assertEqual(observed["p95_s"], 30)

    def test_zero_completion_tenant_lowers_jain(self):
        self.assertEqual(jain([8, 8, 8, 0]), .75)

    def test_matrix_has_no_soak_and_preserves_flood(self):
        cases = {c["label"]: c for c in six_metric_cases()}
        self.assertNotIn("soak", cases)
        self.assertIn("capacity-32", cases)
        self.assertEqual(cases["search-priority-blackbox"]["commit_barrier_count"], 32)

    def test_missing_evidence_never_passes_and_html_escapes(self):
        result = evaluate_six({}, {"name": "4U8G"})
        self.assertEqual(len(result["checks"]), 6)
        self.assertTrue(all(c["status"] == "INCONCLUSIVE" for c in result["checks"]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            result["checks"][0]["reason"] = "<script>alert(1)</script>"
            write_report(result, path)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("<script>", text)
            self.assertIn("&lt;script&gt;", text)

    def test_http_200_commit_cannot_satisfy_accepted_202_flood(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.csv"
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["op", "http_status", "ts_ms"])
                writer.writeheader()
                writer.writerows({"op": "commit_submit", "http_status": 200, "ts_ms": i} for i in range(32))
            suite = {"runs": [{"scenario": "search-priority-blackbox", "output_dir": directory}]}
            result = evaluate_six(suite, {"name": "4U8G"})
            priority = result["checks"][3]
            self.assertEqual(priority["status"], "INCONCLUSIVE")
            self.assertEqual(priority["observed"]["accepted_202"], 0)

    def test_recovery_requires_message_and_cursor_evidence(self):
        names = ["commit-recovery", "pending-before-kill", "order-reconciliation", "idempotency-replay"]
        checks = [{"name": name, "status": "PASS"} for name in names]
        checks[0]["detail"] = json.dumps({"autonomous_recovery_observed": True, "accepted_202": True})
        suite = {"commit_recovery": {"checks": checks}}
        self.assertEqual(evaluate_six(suite, {})["checks"][4]["status"], "INCONCLUSIVE")
        checks.extend({"name": name, "status": "PASS"} for name in
                      ("message-reconciliation", "cursor-reconciliation"))
        self.assertEqual(evaluate_six(suite, {})["checks"][4]["status"], "PASS")
        checks[-1]["status"] = "FAIL"
        self.assertEqual(evaluate_six(suite, {})["checks"][4]["status"], "FAIL")

    def test_fault_matrix_requires_all_unique_tenant_type_round_pairs(self):
        ids = ["a", "b", "c", "d"]
        cases = []
        for tenant in ids:
            for kind in ("reject", "delay"):
                for repeat in (1, 2, 3):
                    cases.append({"target_tenant": tenant, "fault_type": kind, "repetition": repeat,
                                  "checks": [{"name": "fault-isolation", "status": "PASS",
                                              "detail": json.dumps({"fault_observed": True, "samples_per_tenant": 100,
                                                                    "before": {"observed": True}, "during": {"observed": True},
                                                                    "after": {"observed": True}, "baseline_healthy": True,
                                                                    "fault_recovered": True})}]})
        profile = {"fairness_expectations": {"tenant_ids": ids}}
        suite = {"fault_isolation": {"cases": cases}}
        self.assertEqual(evaluate_six(suite, profile)["checks"][1]["status"], "PASS")
        cases[-1] = cases[0]
        result = evaluate_six(suite, profile)["checks"][1]
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(len(result["observed"]["missing_cases"]), 1)

    def test_missing_load_identities_are_not_a_service_capacity_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            suite = {"resource_evidence": {"cpus": 4, "memory_bytes": 8589934592}, "runs": [
                evidence_run(root, "capacity-2", good_reads(100, 2)),
                evidence_run(root, "capacity-4", good_reads(100, 2)),
            ]}
            result = evaluate_six(suite, {"dau_model": {"requests_per_user_per_day": 100,
                                                        "peak_to_average_ratio": 2}})["checks"][0]
            self.assertEqual(result["observed"]["active_lower_bound"], 2)
            self.assertEqual(result["observed"]["failure_boundary"], [])
            self.assertEqual(result["status"], "INCONCLUSIVE")

    def test_capacity_needs_actual_slo_failure_and_verified_resource_limits(self):
        with tempfile.TemporaryDirectory() as root:
            suite = {"resource_evidence": {"cpus": 4, "memory_bytes": 8589934592}, "runs": [
                evidence_run(root, "capacity-2", good_reads(100, 2)),
                evidence_run(root, "capacity-4", good_reads(100, 4, stage_ms=6000)),
            ]}
            profile = {"dau_model": {"requests_per_user_per_day": 100, "peak_to_average_ratio": 2}}
            result = evaluate_six(suite, profile)["checks"][0]
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["observed"]["failure_boundary"], [4])
            self.assertFalse(result["observed"]["capacity_levels"][1]["meets_slo"])
            suite["resource_evidence"]["cpus"] = 2
            self.assertEqual(evaluate_six(suite, profile)["checks"][0]["status"], "INCONCLUSIVE")

    def test_observability_requires_valid_current_run_counter_delta(self):
        row = {"tenant_id": "a", "lane": "commit", "accepted_total": 5,
               "completed_total": 5, "failed_total": 0, "rejected_total": 0,
               "wait_seconds_total": 1, "exec_seconds_total": 2}
        suite = {"tenant_observability": {"checks": [{"name": "tenant-observability", "status": "PASS",
                                                      "detail": json.dumps({"rows": [row]})}]},
                 "tenant_observability_before": {"http_status": 200, "rows": [{**row, "accepted_total": 4}]}}
        self.assertEqual(evaluate_six(suite, {})["checks"][5]["status"], "PASS")
        suite["tenant_observability_before"]["rows"][0]["accepted_total"] = 5
        self.assertEqual(evaluate_six(suite, {})["checks"][5]["status"], "INCONCLUSIVE")
        suite["tenant_observability_before"]["rows"][0]["accepted_total"] = None
        self.assertEqual(evaluate_six(suite, {})["checks"][5]["status"], "INCONCLUSIVE")

    def test_priority_needs_distinct_accepted_commits_and_actual_overlap(self):
        commits = [{"op": "commit_submit", "http_status": 202, "ts_ms": 1000 + n,
                    "session_id": f"s{n}", "archive_id": f"a{n}", "tenant_idx": n % 4}
                   for n in range(32)]
        done = [{**row, "op": "commit_done", "ts_ms": 8000} for row in commits]
        with tempfile.TemporaryDirectory() as root:
            baseline = evidence_run(root, "recall-baseline", good_reads(400))
            flood = evidence_run(root, "search-priority-blackbox", commits + done + good_reads(400))
            suite = {"runs": [baseline, flood]}
            self.assertEqual(evaluate_six(suite, {})["checks"][3]["status"], "PASS")
        with tempfile.TemporaryDirectory() as root:
            slow = good_reads(400)
            for row in slow:
                if row["tenant_idx"] == 0:
                    row["stage_ms"] = 1000
            suite = {"runs": [evidence_run(root, "recall-baseline", good_reads(400)),
                              evidence_run(root, "search-priority-blackbox", commits + done + slow)]}
            result = evaluate_six(suite, {})["checks"][3]
            self.assertEqual(result["observed"]["tenants"][0]["p95_ratio"], 10)
            self.assertEqual(result["status"], "FAIL")
        with tempfile.TemporaryDirectory() as root:
            suite = {"runs": [evidence_run(root, "recall-baseline", good_reads()),
                              evidence_run(root, "search-priority-blackbox", [commits[0]] * 32 + good_reads())]}
            result = evaluate_six(suite, {})["checks"][3]
            self.assertEqual(result["status"], "INCONCLUSIVE")
            self.assertEqual(result["observed"]["accepted_202"], 1)
        with tempfile.TemporaryDirectory() as root:
            suite = {"runs": [evidence_run(root, "recall-baseline", good_reads()),
                              evidence_run(root, "search-priority-blackbox", commits + done + good_reads(ts_ms=10000))]}
            self.assertEqual(evaluate_six(suite, {})["checks"][3]["observed"]["overlap"]["submitted"], 0)

    def test_fairness_includes_zero_completion_tenant_in_fixed_window(self):
        commits = [{"op": "commit_submit", "tenant_idx": n % 4, "ts_ms": 1000,
                    "stage_ms": 10, "status": "ok"} for n in range(32)]
        completed = [{**row, "op": "commit_done", "ts_ms": 6000} for row in commits]
        for missing_tenant in (None, 3):
            with self.subTest(missing_tenant=missing_tenant), tempfile.TemporaryDirectory() as root:
                rows = good_reads(400, 4) + commits + [r for r in completed if r["tenant_idx"] != missing_tenant]
                suite = {"runs": [evidence_run(root, "fairness-bounded", rows)]}
                check = evaluate_six(suite, {})["checks"][2]
                self.assertEqual(check["status"], "PASS" if missing_tenant is None else "FAIL")
                self.assertEqual(len(check["observed"]["tenants"]), 4)
                self.assertEqual(check["observed"]["commit_jain"], 1 if missing_tenant is None else .75)


if __name__ == "__main__":
    unittest.main()
