"""Unit tests for shared.cli_agent_runner.

Covers CLIAgentRunner (subprocess execution, log collection, thread safety)
and the create_memory_client / inject_memories_to_client helpers.

All subprocess calls are mocked -- no real processes are spawned.

Run: python -m unittest tests.test_cli_agent_runner -v
"""

from __future__ import annotations

import json
import subprocess
import threading
import unittest
from unittest.mock import MagicMock, patch

from backends.memory_types import NullMemoryClient
from shared.cli_agent_runner import (
    CLIAgentRunner,
    CLIRunResult,
    create_memory_client,
    inject_memories_to_client,
)


def _fake_completed_process(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess:
    """Create a CompletedProcess-like object for mocking subprocess.run."""
    return subprocess.CompletedProcess(
        args=["fake"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ------------------------------------------------------------------ #
#  CLIAgentRunner                                                     #
# ------------------------------------------------------------------ #

class CLIAgentRunnerRunTests(unittest.TestCase):
    """run() executes subprocess, captures output, returns CLIRunResult."""

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_run_returns_stdout_stderr_returncode(self, mock_run):
        mock_run.return_value = _fake_completed_process(
            stdout='{"result": "hello"}',
            stderr="some debug",
            returncode=0,
        )
        runner = CLIAgentRunner("claude", 300)
        result = runner.run(["-p", "hi", "--output-format", "json"])

        self.assertIsInstance(result, CLIRunResult)
        self.assertEqual('{"result": "hello"}', result.stdout)
        self.assertEqual("some debug", result.stderr)
        self.assertEqual(0, result.returncode)
        self.assertFalse(result.timed_out)
        self.assertGreater(result.elapsed_s, 0.0)

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_run_passes_correct_command(self, mock_run):
        mock_run.return_value = _fake_completed_process()
        runner = CLIAgentRunner("claude")
        runner.run(["-p", "hello"])

        args, kwargs = mock_run.call_args
        self.assertEqual(["claude", "-p", "hello"], args[0])
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_run_uses_default_timeout_when_none(self, mock_run):
        mock_run.return_value = _fake_completed_process()
        runner = CLIAgentRunner("claude", default_timeout_s=250)
        runner.run(["-p", "hi"])

        _, kwargs = mock_run.call_args
        self.assertEqual(250, kwargs["timeout"])

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_run_uses_explicit_timeout_over_default(self, mock_run):
        mock_run.return_value = _fake_completed_process()
        runner = CLIAgentRunner("claude", default_timeout_s=300)
        runner.run(["-p", "hi"], timeout_s=60)

        _, kwargs = mock_run.call_args
        self.assertEqual(60, kwargs["timeout"])

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_run_zero_timeout_falls_back_to_default(self, mock_run):
        mock_run.return_value = _fake_completed_process()
        runner = CLIAgentRunner("claude", default_timeout_s=300)
        runner.run(["-p", "hi"], timeout_s=0)

        _, kwargs = mock_run.call_args
        self.assertEqual(300, kwargs["timeout"])

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_run_handles_none_stdout_stderr(self, mock_run):
        """subprocess may return None for stdout/stderr when process is killed."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["fake"], returncode=-1, stdout=None, stderr=None,
        )
        runner = CLIAgentRunner("claude", default_timeout_s=300)
        result = runner.run(["-p", "hi"])

        self.assertEqual("", result.stdout)
        self.assertEqual("", result.stderr)
        self.assertEqual(-1, result.returncode)
        # _record must not crash on None
        logs = json.loads(runner.get_logs_json())
        self.assertEqual(0, logs[0]["stdout_len"])

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_run_passes_cwd(self, mock_run):
        mock_run.return_value = _fake_completed_process()
        runner = CLIAgentRunner("claude")
        runner.run(["-p", "hi"], cwd="/tmp/work")

        _, kwargs = mock_run.call_args
        self.assertEqual("/tmp/work", kwargs["cwd"])

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_run_passes_env_merged(self, mock_run):
        mock_run.return_value = _fake_completed_process()
        runner = CLIAgentRunner("claude")
        runner.run(["-p", "hi"], env={"CUSTOM_VAR": "1"})

        _, kwargs = mock_run.call_args
        self.assertIn("CUSTOM_VAR", kwargs["env"])
        self.assertEqual("1", kwargs["env"]["CUSTOM_VAR"])

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_run_passes_none_env_when_not_set(self, mock_run):
        mock_run.return_value = _fake_completed_process()
        runner = CLIAgentRunner("claude")
        runner.run(["-p", "hi"])

        _, kwargs = mock_run.call_args
        self.assertIsNone(kwargs["env"])

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_run_nonzero_exit_does_not_raise(self, mock_run):
        mock_run.return_value = _fake_completed_process(
            stdout="", stderr="error: bad input", returncode=1,
        )
        runner = CLIAgentRunner("claude")
        result = runner.run(["-p", "hi"])

        self.assertEqual(1, result.returncode)
        self.assertEqual("error: bad input", result.stderr)
        self.assertFalse(result.timed_out)


class CLIAgentRunnerTimeoutTests(unittest.TestCase):
    """run() handles TimeoutExpired gracefully."""

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_timeout_returns_timed_out_result(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=30,
        )
        runner = CLIAgentRunner("claude", default_timeout_s=30)
        result = runner.run(["-p", "hi"])

        self.assertTrue(result.timed_out)
        self.assertEqual(-1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("30", result.stderr)

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_timeout_uses_explicit_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=60,
        )
        runner = CLIAgentRunner("claude", default_timeout_s=300)
        runner.run(["-p", "hi"], timeout_s=60)

        _, kwargs = mock_run.call_args
        self.assertEqual(60, kwargs["timeout"])


class CLIAgentRunnerLogTests(unittest.TestCase):
    """get_logs_json() returns accumulated log entries."""

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_get_logs_empty_before_any_run(self, mock_run):
        runner = CLIAgentRunner("claude")
        logs = json.loads(runner.get_logs_json())
        self.assertEqual([], logs)

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_get_logs_has_entry_after_run(self, mock_run):
        mock_run.return_value = _fake_completed_process(
            stdout="ok", stderr="dbg", returncode=0,
        )
        runner = CLIAgentRunner("claude")
        runner.run(["-p", "hi"])

        logs = json.loads(runner.get_logs_json())
        self.assertEqual(1, len(logs))
        entry = logs[0]
        self.assertIn("timestamp", entry)
        self.assertEqual(["claude", "-p", "hi"], entry["command"])
        self.assertEqual(0, entry["returncode"])
        self.assertEqual(2, entry["stdout_len"])
        self.assertEqual("dbg", entry["stderr"])
        self.assertFalse(entry["timed_out"])

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_get_logs_accumulates_multiple_runs(self, mock_run):
        mock_run.return_value = _fake_completed_process()
        runner = CLIAgentRunner("claude")
        runner.run(["-p", "a"])
        runner.run(["-p", "b"])

        logs = json.loads(runner.get_logs_json())
        self.assertEqual(2, len(logs))

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_get_logs_truncates_long_stderr(self, mock_run):
        mock_run.return_value = _fake_completed_process(
            stderr="x" * 5000,
        )
        runner = CLIAgentRunner("claude")
        runner.run(["-p", "hi"])

        logs = json.loads(runner.get_logs_json())
        self.assertEqual(2000, len(logs[0]["stderr"]))

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_get_logs_timeout_entry_has_timed_out_flag(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["claude"], timeout=10)
        runner = CLIAgentRunner("claude")
        runner.run(["-p", "hi"])

        logs = json.loads(runner.get_logs_json())
        self.assertTrue(logs[0]["timed_out"])
        self.assertNotIn("returncode", logs[0])

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_get_logs_returns_valid_json(self, mock_run):
        mock_run.return_value = _fake_completed_process()
        runner = CLIAgentRunner("claude")
        runner.run(["-p", "hi"])

        raw = runner.get_logs_json()
        parsed = json.loads(raw)
        self.assertIsInstance(parsed, list)


class CLIAgentRunnerThreadSafetyTests(unittest.TestCase):
    """Concurrent run() calls do not lose log entries."""

    @patch("shared.cli_agent_runner.subprocess.run")
    def test_concurrent_runs_all_logged(self, mock_run):
        mock_run.return_value = _fake_completed_process()
        runner = CLIAgentRunner("claude")

        threads = []
        for i in range(20):
            t = threading.Thread(target=runner.run, args=(["-p", f"msg{i}"],))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        logs = json.loads(runner.get_logs_json())
        self.assertEqual(20, len(logs))


# ------------------------------------------------------------------ #
#  create_memory_client                                               #
# ------------------------------------------------------------------ #

class CreateMemoryClientTests(unittest.TestCase):
    """create_memory_client returns correct client type per config."""

    def test_no_backend_returns_null(self):
        client = create_memory_client({})
        self.assertIsInstance(client, NullMemoryClient)

    def test_empty_backend_returns_null(self):
        client = create_memory_client({"memory_backend": ""})
        self.assertIsInstance(client, NullMemoryClient)

    def test_unknown_backend_returns_null(self):
        client = create_memory_client({"memory_backend": "unknown"})
        self.assertIsInstance(client, NullMemoryClient)

    @patch("backends.echomem.client.EchoMemClient")
    def test_echomem_backend_creates_echomem_client(self, mock_cls):
        create_memory_client({
            "memory_backend": "echomem",
            "echomem_url": "http://localhost:8010",
            "echomem_auth_key": "key123",
            "account": "acc",
            "user_id": "u1",
            "agent_id": "a1",
            "workspace": "/ws",
            "timeout_s": 30.0,
            "max_retries": 2,
        })
        mock_cls.assert_called_once_with(
            base_url="http://localhost:8010",
            auth_key="key123",
            account="acc",
            user_id="u1",
            agent_id="a1",
            workspace="/ws",
            timeout_s=30.0,
            max_retries=2,
            log_access_key="",
        )

    @patch("backends.openviking.client.OpenVikingClient")
    def test_openviking_backend_creates_openviking_client(self, mock_cls):
        create_memory_client({
            "memory_backend": "openviking",
            "echomem_url": "http://localhost:19080",
            "echomem_auth_key": "key456",
            "account": "acc",
            "user_id": "u1",
            "agent_id": "a1",
            "workspace": "/ws",
            "timeout_s": 45.0,
            "max_retries": 5,
        })
        mock_cls.assert_called_once_with(
            base_url="http://localhost:19080",
            api_key="key456",
            account="acc",
            user_id="u1",
            agent_id="a1",
            workspace="/ws",
            timeout_s=45.0,
            max_retries=5,
        )

    @patch("backends.echomem.client.EchoMemClient")
    def test_benchmark_run_provisions_isolated_identity(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        create_memory_client({
            "memory_backend": "echomem",
            "benchmark_name": "locomo",
            "run_id": "run_001",
            "resume_qa": "",
        })
        mock_client.provision_isolated_identity.assert_called_once()
        label = mock_client.provision_isolated_identity.call_args[0][0]
        self.assertIn("locomo", label)
        self.assertIn("run_001", label)

    @patch("backends.echomem.client.EchoMemClient")
    def test_resume_qa_skips_identity_provisioning(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        create_memory_client({
            "memory_backend": "echomem",
            "benchmark_name": "locomo",
            "run_id": "run_001",
            "resume_qa": "yes",
        })
        mock_client.provision_isolated_identity.assert_not_called()

    @patch("backends.echomem.client.EchoMemClient")
    def test_no_benchmark_name_skips_identity_provisioning(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        create_memory_client({
            "memory_backend": "echomem",
        })
        mock_client.provision_isolated_identity.assert_not_called()


# ------------------------------------------------------------------ #
#  inject_memories_to_client                                          #
# ------------------------------------------------------------------ #

class InjectMemoriesToClientTests(unittest.TestCase):
    """inject_memories_to_client: no-op for Null, real injection for clients."""

    def test_null_client_returns_session_id_unchanged(self):
        result = inject_memories_to_client(
            NullMemoryClient(),
            [{"text": "memory"}],
            session_id="my_session",
        )
        self.assertEqual("my_session", result)

    def test_null_client_with_empty_session_id(self):
        result = inject_memories_to_client(
            NullMemoryClient(),
            [{"text": "memory"}],
        )
        self.assertEqual("", result)

    def test_real_client_opens_session_when_none(self):
        mock_client = MagicMock()
        mock_client.open_session.return_value = "new_session"
        mock_client.commit_session.return_value = "archive_1"
        mock_client.poll_commit.return_value = MagicMock(status="completed")

        result = inject_memories_to_client(
            mock_client,
            [{"text": "mem1"}, {"text": "mem2"}],
        )

        self.assertEqual("new_session", result)
        mock_client.open_session.assert_called_once_with(title="inject")
        self.assertEqual(2, mock_client.add_message.call_count)
        mock_client.commit_session.assert_called_once_with("new_session")
        mock_client.poll_commit.assert_called_once()

    def test_real_client_uses_provided_session_id(self):
        mock_client = MagicMock()
        mock_client.commit_session.return_value = "archive_1"
        mock_client.poll_commit.return_value = MagicMock(status="completed")

        result = inject_memories_to_client(
            mock_client,
            [{"text": "mem1"}],
            session_id="existing_session",
        )

        self.assertEqual("existing_session", result)
        mock_client.open_session.assert_not_called()

    def test_real_client_raises_on_commit_failure(self):
        mock_client = MagicMock()
        mock_client.commit_session.return_value = "archive_1"
        mock_client.poll_commit.return_value = MagicMock(
            status="failed", error="extraction error",
        )

        with self.assertRaises(RuntimeError) as ctx:
            inject_memories_to_client(
                mock_client,
                [{"text": "mem1"}],
                session_id="s1",
            )
        self.assertIn("failed", str(ctx.exception))
        self.assertIn("extraction error", str(ctx.exception))

    def test_real_client_passes_commit_timeout_and_interval(self):
        mock_client = MagicMock()
        mock_client.commit_session.return_value = "archive_1"
        mock_client.poll_commit.return_value = MagicMock(status="completed")

        inject_memories_to_client(
            mock_client,
            [{"text": "mem1"}],
            session_id="s1",
            commit_timeout_s=120.0,
            commit_poll_interval_s=3.0,
        )

        _, kwargs = mock_client.poll_commit.call_args
        self.assertEqual(120.0, kwargs["timeout_s"])
        self.assertEqual(3.0, kwargs["poll_interval_s"])

    def test_real_client_skips_empty_text_memories(self):
        mock_client = MagicMock()
        mock_client.commit_session.return_value = "archive_1"
        mock_client.poll_commit.return_value = MagicMock(status="completed")

        inject_memories_to_client(
            mock_client,
            [{"text": "mem1"}, {"text": ""}, {"text": None}, {}],
            session_id="s1",
        )

        self.assertEqual(1, mock_client.add_message.call_count)

    def test_real_client_passes_created_at(self):
        mock_client = MagicMock()
        mock_client.commit_session.return_value = "archive_1"
        mock_client.poll_commit.return_value = MagicMock(status="completed")

        inject_memories_to_client(
            mock_client,
            [{"text": "mem1", "time": "2024-01-15T10:00:00"}],
            session_id="s1",
        )

        _, args, kwargs = mock_client.add_message.mock_calls[0]
        self.assertEqual("s1", args[0])
        self.assertEqual("user", args[1])
        self.assertEqual("mem1", args[2])
        self.assertEqual("2024-01-15T10:00:00", kwargs["created_at"])


if __name__ == "__main__":
    unittest.main()
