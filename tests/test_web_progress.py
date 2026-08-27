from __future__ import annotations

import unittest
import importlib.util
from unittest.mock import patch


HAS_WEB_RUNTIME = importlib.util.find_spec("docker") is not None


@unittest.skipUnless(
    HAS_WEB_RUNTIME,
    "Web service tests require the docker Python package",
)
class FormalProgressTests(unittest.TestCase):
    def test_formal_progress_protocol_is_distinguished_from_child_output(self) -> None:
        from deploy.web_app_server import _is_formal_progress_line, _split_log_chunk

        self.assertTrue(
            _is_formal_progress_line(
                "FORMAL_PROGRESS 4/15 scenario=mixed repeat=1 policy=server-observe"
            )
        )
        self.assertTrue(
            _is_formal_progress_line(
                "FORMAL_HEARTBEAT scenario=mixed repeat=1 "
                "policy=server-observe elapsed_s=600"
            )
        )
        self.assertFalse(_is_formal_progress_line('"server_queue_depth": ""'))
        self.assertFalse(
            _is_formal_progress_line(
                "HTTP request completed method=POST status_code=200"
            )
        )
        chunks = [
            b"FORMAL_HEART",
            b"BEAT scenario=mixed repeat=1 policy=server-observe ",
            b"elapsed_s=600\nFORMAL_PROGRESS 4/",
            b"15 scenario=mixed repeat=1 policy=server-observe\n",
        ]
        pending = ""
        decoded = []
        for chunk in chunks:
            lines, pending = _split_log_chunk(chunk, pending)
            decoded.extend(lines)
        if pending:
            decoded.append(pending)
        self.assertEqual(
            [
                "FORMAL_HEARTBEAT scenario=mixed repeat=1 "
                "policy=server-observe elapsed_s=600",
                "FORMAL_PROGRESS 4/15 scenario=mixed repeat=1 "
                "policy=server-observe",
            ],
            decoded,
        )

    def test_formal_suite_does_not_overwrite_case_progress_with_json(self) -> None:
        from deploy import web_app_server as server

        job = {
            "id": "formal-test",
            "test_type": "stress",
            "stress_config": {"formal_suite": True},
            "progress": server.default_progress("qa"),
        }
        job["progress"].update({"current": 4, "total": 15, "last_log": "FORMAL_PROGRESS 4/15"})

        with patch.object(server, "get_job", return_value=job), patch.object(
            server, "update_job"
        ) as update:
            server.update_progress_from_line(
                "formal-test", '"server_queue_depth": ""'
            )
            update.assert_not_called()

            server.update_progress_from_line(
                "formal-test",
                "FORMAL_HEARTBEAT scenario=mixed repeat=1 "
                "policy=server-observe elapsed_s=600",
            )
            update.assert_called_once()
            progress = update.call_args.kwargs["progress"]
            self.assertEqual(4, progress["current"])
            self.assertEqual(15, progress["total"])
            self.assertEqual(
                "FORMAL_HEARTBEAT scenario=mixed repeat=1 "
                "policy=server-observe elapsed_s=600",
                progress["last_log"],
            )

    def test_final_stress_progress_preserves_partial_formal_run(self) -> None:
        from deploy import web_app_server as server

        job = {
            "id": "formal-test",
            "test_type": "stress",
            "stress_config": {"formal_suite": True},
            "progress": {
                **server.default_progress("qa"),
                "current": 5,
                "total": 15,
                "percent": 33,
                "last_log": "FORMAL_PROGRESS 5/15 scenario=mixed repeat=2",
            },
        }
        progress = server.final_stress_progress(job, "failed")
        self.assertEqual(5, progress["current"])
        self.assertEqual(15, progress["total"])
        self.assertEqual(33, progress["percent"])

    def test_failed_non_formal_run_also_keeps_diagnostic_progress(self) -> None:
        from deploy import web_app_server as server

        job = {
            "id": "stress-test",
            "test_type": "stress",
            "stress_config": {"formal_suite": False},
            "progress": {
                **server.default_progress("qa"),
                "current": 7,
                "total": 81,
                "percent": 9,
                "last_log": "QA checkpoint: 7/81",
            },
        }
        progress = server.final_stress_progress(job, "failed")
        self.assertEqual(7, progress["current"])
        self.assertEqual(81, progress["total"])
        self.assertEqual(9, progress["percent"])


if __name__ == "__main__":
    unittest.main()
