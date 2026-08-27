from __future__ import annotations

import tempfile
import unittest
import json
import threading
import time
from pathlib import Path

from stress.echomem.runner import (
    AdmissionController,
    CommitRecord,
    HttpResult,
    INCONCLUSIVE,
    PASS,
    ResourceSample,
    SearchRecord,
    _server_observability,
    build_report,
    load_tenant_specs,
    linear_slope_per_minute,
    workload_metrics,
    percentile,
    scenario_status,
    scenario_search,
    isolation_probe_query,
)
from stress.echomem.audit_matrix_report import render as render_audit_matrix_report


class StressRunnerTests(unittest.TestCase):
    def test_server_observability_accepts_payload_and_headers(self) -> None:
        values = _server_observability(
            {
                "telemetry": {
                    "received_at": "2026-08-26T00:00:00Z",
                    "queue_depth": 4,
                }
            },
            {
                "x-server-execution-started-at": "2026-08-26T00:00:01Z",
                "x-active-workers": "2",
            },
        )
        self.assertEqual("2026-08-26T00:00:00Z", values["server_received_at"])
        self.assertEqual(4, values["server_queue_depth"])
        self.assertEqual(2, values["server_active_workers"])

    def test_tenant_config_uses_environment_key_without_exposing_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenants.json"
            path.write_text(json.dumps({
                "tenants": [
                    {"tenant_id": "a", "auth_key_env": "KEY_A"},
                    {"tenant_id": "b", "auth_key_env": "KEY_B"},
                ]
            }), encoding="utf-8")
            specs = load_tenant_specs(path, {"KEY_A": "secret-a", "KEY_B": "secret-b"})
            self.assertEqual(["a", "b"], [spec.tenant_id for spec in specs])
            self.assertEqual("secret-a", specs[0].auth_key)
            self.assertEqual("env:KEY_A", specs[0].auth_key_source)

    def test_fifo_admission_preserves_order(self) -> None:
        gate = AdmissionController("fifo", capacity=1)
        wait, depth, order = gate.acquire("commit", "a")
        self.assertGreaterEqual(wait, 0.0)
        self.assertEqual(0, depth)
        self.assertEqual(1, order)
        gate.release("commit", "a")

    def test_search_priority_waits_for_capacity_but_jumps_queued_commit(self) -> None:
        gate = AdmissionController("search-priority", capacity=1)
        _, _, commit_order = gate.acquire("commit", "a")
        acquired = {}

        def acquire_search() -> None:
            acquired["result"] = gate.acquire("search", "b")

        thread = threading.Thread(target=acquire_search)
        thread.start()
        thread.join(0.05)
        self.assertTrue(thread.is_alive())
        gate.release("commit", "a")
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        _, _, search_order = acquired["result"]
        self.assertLess(commit_order, search_order)
        gate.release("search", "b")
        gate.release("commit", "a")

    def test_fixed_rate_search_keeps_completed_futures_in_results(self) -> None:
        class FakeClient:
            def search(self, session_id, query, timeout_s):
                time.sleep(0.02)
                return HttpResult(
                    "POST",
                    "/api/retrieval/search",
                    200,
                    0.02,
                    {"items": []},
                )

        records = scenario_search(
            FakeClient(),
            [("tenant-a", "session-a")],
            duration_s=0.25,
            rps=12.0,
            timeout_s=1.0,
            workers=2,
        )

        # Three arrivals are scheduled in the interval. Completed futures may
        # leave the in-flight map before the final collection, but must remain
        # part of the returned workload evidence.
        self.assertEqual(3, len(records))
        self.assertTrue(all(record.status_code == 200 for record in records))

    def test_percentile_is_interpolated(self) -> None:
        self.assertEqual(2.5, percentile([1, 2, 3, 4], 50))

    def test_rss_slope_requires_four_samples(self) -> None:
        samples = [ResourceSample(float(i), "", 100 + i, None, None, None, None, None) for i in range(4)]
        self.assertIsNotNone(linear_slope_per_minute(samples))
        self.assertIsNone(linear_slope_per_minute(samples[:3]))

    def test_insufficient_samples_is_inconclusive(self) -> None:
        status, details = scenario_status(
            [CommitRecord("t", "s", "a", "", status="completed")],
            [],
            min_samples=4,
            p95_limit_s=2.5,
        )
        self.assertEqual(INCONCLUSIVE, status)
        self.assertIn("insufficient", details["reason"])

    def test_report_contains_resource_charts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            build_report({
                "status": PASS,
                "base_url": "http://127.0.0.1:8010",
                "finished_at": "",
                "scenario_status": {},
                "parameters": {},
                "details": {},
                "resource_points": [
                    {"elapsed_s": 0, "rss_mb": 10, "cpu_percent": 2},
                    {"elapsed_s": 1, "rss_mb": 11, "cpu_percent": 4},
                ],
            }, path)
            content = path.read_text(encoding="utf-8")
            self.assertIn("rss_mb", content)
            self.assertIn("<polyline", content)

    def test_workload_metrics_include_timing_and_tenants(self) -> None:
        commits = [
            CommitRecord(
                "tenant-a", "s1", "a1", "", completed_at="",
                status="completed", queue_wait_s=1.0, service_s=9.0,
                end_to_end_s=10.0,
            ),
            CommitRecord(
                "tenant-b", "s2", "a2", "", completed_at="",
                status="timeout", queue_wait_s=2.0, service_s=12.0,
                end_to_end_s=14.0,
            ),
        ]
        searches = [
            SearchRecord(
                "tenant-a", "s1", "", 0.5, 200,
                service_s=0.5, end_to_end_s=0.5,
            ),
            SearchRecord(
                "tenant-b", "s2", "", 3.0, 200,
                service_s=3.0, end_to_end_s=3.0,
            ),
        ]
        metrics = workload_metrics(
            commits,
            searches,
            ["tenant-a", "tenant-b"],
            duration_s=10,
            commit_delay_threshold_s=10,
            search_delay_threshold_s=2.5,
        )
        self.assertEqual(2, metrics["commit"]["submitted"])
        self.assertEqual(1, metrics["commit"]["completed"])
        self.assertEqual(2, metrics["commit"]["delayed_count"])
        self.assertEqual(1, metrics["search"]["delayed_count"])
        self.assertEqual(2, len(metrics["per_tenant"]))
        self.assertEqual(10.0, metrics["per_tenant"]["tenant-a"]["commit"]["completion"]["mean_s"])
        self.assertGreater(metrics["fairness"]["search_latency_p95_jain"], 0.0)

    def test_workload_metrics_summarize_rate_limit_responses(self) -> None:
        commits = [
            CommitRecord(
                "tenant-a", "s1", "a1", "", status="commit_rejected",
                status_code=429, retry_after_s=3.0,
            ),
        ]
        searches = [
            SearchRecord(
                "tenant-a", "s1", "", 0.2, 429,
                error="HTTP 429", retry_after_s=1.5,
            ),
            SearchRecord("tenant-a", "s1", "", 0.2, 200, service_s=0.2),
        ]
        metrics = workload_metrics(
            commits,
            searches,
            ["tenant-a"],
            duration_s=10,
            commit_delay_threshold_s=10,
            search_delay_threshold_s=2.5,
        )
        self.assertEqual({"429": 1}, metrics["commit"]["http_status_counts"])
        self.assertEqual(1, metrics["commit"]["rate_limited_count"])
        self.assertEqual(3.0, metrics["commit"]["retry_after"]["mean_s"])
        self.assertEqual({"200": 1, "429": 1}, metrics["search"]["http_status_counts"])
        self.assertEqual(1, metrics["search"]["rate_limited_count"])
        self.assertEqual(1.5, metrics["search"]["retry_after"]["mean_s"])

    def test_search_throughput_uses_configured_arrival_window(self) -> None:
        searches = [
            SearchRecord("tenant-a", "s1", "", 0.2, 200, service_s=0.2),
            SearchRecord("tenant-a", "s1", "", 0.2, 200, service_s=0.2),
            SearchRecord("tenant-a", "s1", "", 0.2, 200, service_s=0.2),
        ]
        metrics = workload_metrics(
            [],
            searches,
            ["tenant-a"],
            duration_s=60,
            commit_delay_threshold_s=10,
            search_delay_threshold_s=2.5,
        )
        self.assertEqual(0.05, metrics["search"]["throughput_rps"])
        self.assertEqual(0.05, metrics["search"]["completed_throughput_rps"])
        self.assertEqual(60, metrics["workload_duration_s"])

    def test_isolation_probe_query_does_not_echo_secret_marker(self) -> None:
        query = isolation_probe_query("tenant-a")
        self.assertIn("tenant-a", query)
        self.assertNotIn("echomem-isolation-", query)

    def test_workload_metrics_include_timeline_and_minute_buckets(self) -> None:
        commits = [
            CommitRecord(
                "tenant-a",
                "s1",
                "a1",
                "",
                queued_at="2026-08-26T00:00:01+00:00",
                started_at="2026-08-26T00:00:02+00:00",
                completed_at="2026-08-26T00:00:13+00:00",
                status="completed",
                end_to_end_s=11.0,
                queue_wait_s=1.0,
                admission_order=1,
            ),
        ]
        searches = [
            SearchRecord(
                "tenant-b",
                "s2",
                "2026-08-26T00:00:03+00:00",
                0.4,
                200,
                queued_at="2026-08-26T00:00:03+00:00",
                finished_at="2026-08-26T00:00:03.4+00:00",
                service_s=0.4,
                end_to_end_s=0.4,
                admission_order=2,
            ),
        ]
        metrics = workload_metrics(
            commits,
            searches,
            ["tenant-a", "tenant-b"],
            duration_s=60,
            commit_delay_threshold_s=10,
            search_delay_threshold_s=2.5,
        )
        self.assertEqual(2, len(metrics["timeline"]))
        self.assertEqual("commit", metrics["timeline"][0]["operation"])
        self.assertEqual(0.0, metrics["timeline"][0]["workload_offset_s"])
        self.assertEqual(1, metrics["time_buckets"][0]["commit"]["delayed"])
        self.assertEqual(1, metrics["time_buckets"][0]["search"]["succeeded"])

    def test_workload_metrics_include_server_side_timing_when_present(self) -> None:
        commits = [
            CommitRecord(
                "tenant-a",
                "s1",
                "a1",
                "",
                status="completed",
                server_received_at="2026-08-26T00:00:00+00:00",
                server_queue_entered_at="2026-08-26T00:00:00.100+00:00",
                server_execution_started_at="2026-08-26T00:00:02+00:00",
                server_finished_at="2026-08-26T00:00:07+00:00",
                server_queue_depth=3,
                server_active_workers=2,
            )
        ]
        metrics = workload_metrics(
            commits,
            [],
            ["tenant-a"],
            duration_s=10,
            commit_delay_threshold_s=10,
            search_delay_threshold_s=2.5,
        )
        server = metrics["commit"]["server"]
        self.assertEqual(1, server["observed_count"])
        self.assertEqual(1.9, server["queue_wait"]["mean_s"])
        self.assertEqual(5.0, server["execution"]["mean_s"])
        self.assertEqual(3.0, server["queue_depth"]["mean_s"])

    def test_audit_matrix_report_contains_full_latency_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_dir = root / "fifo"
            policy_dir.mkdir()
            summary = {
                "status": "INCONCLUSIVE",
                "base_url": "http://127.0.0.1:8010",
                "parameters": {"scheduler_policy": "fifo", "tenants": 1},
                "details": {"identity_mode": "single_auth_key"},
                "metrics": {
                    "commit": {
                        "submitted": 2,
                        "completed": 2,
                        "failed": 0,
                        "success_rate": 1.0,
                        "completion": {
                            "count": 2, "mean_s": 10, "min_s": 9,
                            "p50_s": 10, "p90_s": 11, "p95_s": 11.5,
                            "p99_s": 11.9, "max_s": 12, "total_s": 20,
                        },
                        "queue_wait": {"count": 2, "mean_s": 1, "min_s": 0,
                                       "p50_s": 1, "p90_s": 1.5, "p95_s": 1.8,
                                       "p99_s": 1.9, "max_s": 2, "total_s": 2},
                        "service": {},
                        "delayed_threshold_s": 10,
                        "delayed": [{
                            "tenant": "tenant-a", "session_id": "s1",
                            "started_at": "2026-08-26T00:00:00Z",
                            "completed_at": "2026-08-26T00:00:12Z",
                            "completion_s": 12, "queue_wait_s": 2,
                            "admission_wait_s": 1, "admission_order": 7,
                            "status": "completed",
                        }],
                    },
                    "search": {
                        "submitted": 1, "succeeded": 1, "errors": 0,
                        "success_rate": 1.0, "latency": {
                            "count": 1, "mean_s": 1, "min_s": 1,
                            "p50_s": 1, "p90_s": 1, "p95_s": 1,
                            "p99_s": 1, "max_s": 1, "total_s": 1,
                        },
                        "admission_wait": {}, "delayed_threshold_s": 2.5,
                        "delayed": [],
                    },
                    "admission": {"max_queue_depth": 3, "wait": {}},
                    "per_tenant": {},
                },
            }
            (policy_dir / "summary.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )
            (root / "matrix.json").write_text(
                json.dumps({"summaries": [summary]}), encoding="utf-8"
            )
            output = root / "matrix-audit.html"
            render_audit_matrix_report(root / "matrix.json", output)
            content = output.read_text(encoding="utf-8")
            self.assertIn("Commit 端到端完成时间", content)
            self.assertIn("P99", content)
            self.assertIn("2026-08-26T00:00:12Z", content)

    def test_commit_poll_does_not_use_admission_operation(self) -> None:
        class RecordingAdmission:
            def __init__(self):
                self.operations = []

            def acquire(self, operation, tenant):
                self.operations.append(operation)
                return 0.0, 0, 1

            def release(self, operation, tenant):
                self.operations.append(f"release:{operation}")

        from stress.echomem.runner import EchoMemHTTP

        admission = RecordingAdmission()
        client = EchoMemHTTP("http://127.0.0.1:1", admission=admission)
        client.commit_status("session", "archive")
        self.assertEqual([], admission.operations)

    def test_search_service_time_excludes_worker_admission_interval(self) -> None:
        class SlowAdmissionClient:
            def search(self, session_id, query, timeout_s):
                import time

                time.sleep(0.02)
                return HttpResult(
                    "POST",
                    "/api/retrieval/search",
                    200,
                    0.003,
                    {"items": []},
                )

        records = scenario_search(
            SlowAdmissionClient(),
            [("tenant-a", "session-a")],
            duration_s=0.01,
            rps=1,
            timeout_s=1,
            workers=1,
        )
        self.assertEqual(1, len(records))
        self.assertEqual(0.003, records[0].service_s)
        self.assertGreater(records[0].end_to_end_s, records[0].service_s)


if __name__ == "__main__":
    unittest.main()
