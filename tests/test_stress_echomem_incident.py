import unittest

from scripts.stress_echomem_incident import (
    classify_workflow,
    extract_archive_id,
    extract_commit_state,
    percentile,
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


if __name__ == "__main__":
    unittest.main()
