import unittest
import asyncio
import httpx

from scripts.stress_echomem_incident import (
    build_metrics,
    build_tenant_metrics,
    classify_workflow,
    extract_archive_id,
    extract_commit_state,
    poll_commit,
    post_commit_with_retry,
    percentile,
    retry_after_seconds,
)


class StressIncidentHelpersTest(unittest.TestCase):
    def test_percentile_interpolates_and_handles_empty(self):
        self.assertIsNone(percentile([], 95))
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(percentile([4, 1, 3, 2], 95), 3.85)

    def test_extract_archive_id_supports_nested_envelopes(self):
        payload = {"data": {"result": {"archive_id": 123}}}
        self.assertEqual(extract_archive_id(payload), "123")

    def test_extract_commit_state_supports_nested_envelopes(self):
        payload = {"result": {"data": {"state": "COMPLETED"}}}
        self.assertEqual(extract_commit_state(payload), "completed")

    def test_classifies_async_commit_outcomes(self):
        base = {
            "open_status": 200,
            "message_status": 200,
            "commit_status": 202,
        }
        self.assertEqual(
            classify_workflow({**base, "commit_poll_state": "completed"}),
            "ok",
        )
        self.assertEqual(
            classify_workflow({**base, "commit_poll_state": "failed"}),
            "commit:failed",
        )
        self.assertEqual(
            classify_workflow({**base, "commit_poll_state": "timeout"}),
            "commit:timeout",
        )
        self.assertEqual(
            classify_workflow(
                {**base, "commit_poll_requested": True},
            ),
            "commit:missing_archive_id",
        )

    def test_exception_has_priority_over_http_classification(self):
        self.assertEqual(
            classify_workflow(
                {
                    "exception": "ReadTimeout",
                    "open_status": 200,
                }
            ),
            "exception:ReadTimeout",
        )

    def test_retry_after_seconds_reads_numeric_header(self):
        import httpx

        response = httpx.Response(
            429,
            headers={"Retry-After": "5"},
        )
        self.assertEqual(retry_after_seconds(response), 5.0)

    def test_retry_after_seconds_ignores_invalid_header(self):
        import httpx

        response = httpx.Response(
            429,
            headers={"Retry-After": "later"},
        )
        self.assertIsNone(retry_after_seconds(response))

    def test_retry_after_seconds_reads_nested_json_body(self):
        response = httpx.Response(
            429,
            json={"error": {"retry_after_s": 5}},
        )
        self.assertEqual(retry_after_seconds(response), 5.0)

    def test_retry_after_seconds_reads_http_date_header(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime

        import httpx

        retry_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        response = httpx.Response(
            429,
            headers={"Retry-After": format_datetime(retry_at, usegmt=True)},
        )
        delay = retry_after_seconds(response)
        self.assertIsNotNone(delay)
        self.assertGreaterEqual(delay, 0.0)
        self.assertLessEqual(delay, 5.0)

    def test_metrics_keep_request_and_completion_latency_separate(self):
        metrics = build_metrics(
            [
                {
                    "result": "ok",
                    "duration_ms": 150,
                    "request_duration_ms": 20,
                    "commit_completion_ms": 120,
                    "commit_ms": 5,
                    "commit_status": 202,
                }
            ],
            elapsed_ms=150,
            requested_workflows=1,
            concurrency=1,
            stage=1,
        )
        self.assertEqual(metrics["request_latency_ms"]["p50"], 20)
        self.assertEqual(
            metrics["commit_completion_latency_ms"]["p50"],
            120,
        )

    def test_metrics_keep_window_and_final_completion_separate(self):
        metrics = build_metrics(
            [
                {
                    "result": "ok",
                    "duration_ms": 120,
                    "request_duration_ms": 20,
                    "request_completed_at": 2,
                    "started_at": 1,
                    "commit_completion_ms": 120,
                    "commit_ms": 5,
                    "commit_status": 202,
                    "window_commit_poll_state": "timeout",
                    "commit_poll_state": "completed",
                }
            ],
            elapsed_ms=120,
            requested_workflows=1,
            concurrency=1,
            stage=1,
        )
        self.assertEqual(metrics["window_completed_commits"], 0)
        self.assertEqual(metrics["window_commit_timeouts"], 1)
        self.assertEqual(metrics["final_completed_commits"], 1)

    def test_metrics_expose_structured_failure_details(self):
        metrics = build_metrics(
            [
                {
                    "result": "commit:failed",
                    "duration_ms": 10,
                    "commit_status": 202,
                    "commit_poll_state": "failed",
                    "commit_poll_body": {
                        "status": {
                            "error": "Atomic extraction failed (window)",
                        }
                    },
                }
            ],
            elapsed_ms=10,
            requested_workflows=1,
            concurrency=1,
            stage=1,
        )
        self.assertEqual(
            metrics["failure_details"],
            {"Atomic extraction failed (window)": 1},
        )

    def test_tenant_metrics_are_separated(self):
        metrics = build_tenant_metrics(
            [
                {
                    "tenant": "a",
                    "result": "ok",
                    "commit_status": 202,
                    "commit_poll_state": "completed",
                    "window_commit_poll_state": "completed",
                    "commit_completion_ms": 10,
                },
                {
                    "tenant": "b",
                    "result": "http:commit:429",
                    "commit_status": 429,
                    "commit_initial_status": 429,
                },
            ]
        )
        self.assertEqual(metrics["a"]["final_completed_commits"], 1)
        self.assertEqual(metrics["b"]["initial_429"], 1)
        self.assertEqual(metrics["b"]["failed_workflows"], 1)

    def test_auto_commit_requires_archive_when_polling(self):
        self.assertEqual(
            classify_workflow(
                {
                    "commit_mode": "auto",
                    "open_status": 200,
                    "message_status": 200,
                    "commit_poll_requested": True,
                }
            ),
            "auto_commit:missing_archive_id",
        )

    def test_commit_retry_reuses_idempotency_key_and_json_delay(self):
        async def run():
            requests = []

            def handler(request):
                requests.append(request)
                if len(requests) == 1:
                    return httpx.Response(429, json={"retry_after_s": 0})
                return httpx.Response(202, json={"archive_id": "a1"})

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                result = await post_commit_with_retry(
                    client,
                    "/commit",
                    payload={},
                    headers={},
                    max_retries=1,
                    retry_backoff_s=0,
                    idempotency_key="run:session:commit",
                )
            return requests, result

        requests, (_, _, info) = asyncio.run(run())
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            [request.headers["Idempotency-Key"] for request in requests],
            ["run:session:commit", "run:session:commit"],
        )
        self.assertEqual(info["commit_initial_status"], 429)
        self.assertEqual(info["commit_attempts"], 2)

    def test_poll_404_is_immediate_failure(self):
        async def run():
            transport = httpx.MockTransport(
                lambda request: httpx.Response(404, json={"detail": "missing"})
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                return await poll_commit(
                    client,
                    session_id="s1",
                    archive_id="a1",
                    headers={},
                    timeout_s=10,
                    interval_s=0,
                    max_retries=3,
                    retry_backoff_s=0,
                )

        result = asyncio.run(run())
        self.assertEqual(result["commit_poll_state"], "failed")
        self.assertEqual(result["commit_poll_raw_state"], "http_404")
        self.assertEqual(result["commit_poll_count"], 1)


if __name__ == "__main__":
    unittest.main()
