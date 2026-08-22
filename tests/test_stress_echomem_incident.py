import unittest
import asyncio
import httpx

from scripts.stress_echomem_incident import (
    build_metrics,
    classify_workflow,
    extract_archive_id,
    extract_commit_state,
    poll_commit,
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
