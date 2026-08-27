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
        from deploy.web_app_server import _is_formal_progress_line

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


if __name__ == "__main__":
    unittest.main()
