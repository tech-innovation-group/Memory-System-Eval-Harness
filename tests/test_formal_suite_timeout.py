from __future__ import annotations

import os
import subprocess
import unittest
from unittest.mock import patch

from performance.formal_suite import run_case_process


class FormalSuiteTimeoutTests(unittest.TestCase):
    @patch("performance.formal_suite.subprocess.Popen")
    def test_case_process_returns_completed_process(self, popen):
        process = popen.return_value
        process.communicate.return_value = ("out", "err")
        process.returncode = 0

        completed, timed_out = run_case_process(["runner"], timeout_s=5)

        self.assertFalse(timed_out)
        self.assertEqual(0, completed.returncode)
        self.assertEqual("out", completed.stdout)
        process.communicate.assert_called_once_with(timeout=5)

    @patch("performance.formal_suite.subprocess.Popen")
    def test_case_process_kills_process_group_on_timeout(self, popen):
        process = popen.return_value
        process.pid = 123
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["runner"], 1),
            ("partial", "stderr"),
        ]
        process.returncode = -15

        if os.name == "posix":
            with patch("performance.formal_suite.os.killpg") as killpg:
                completed, timed_out = run_case_process(["runner"], timeout_s=1)
            killpg.assert_called_once_with(123, 15)
        else:
            completed, timed_out = run_case_process(["runner"], timeout_s=1)
            process.terminate.assert_called_once()

        self.assertTrue(timed_out)
        self.assertEqual(124, completed.returncode)
        self.assertIn("case wall-clock timeout", completed.stderr)


if __name__ == "__main__":
    unittest.main()
