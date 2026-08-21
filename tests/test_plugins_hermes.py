"""Unit tests for plugins.hermes.plugin.

All subprocess calls are mocked -- no real hermes CLI is invoked.

Run: python -m unittest tests.test_plugins_hermes -v
"""

from __future__ import annotations

import argparse
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from backends.memory_types import NullMemoryClient
from plugins.base import AgentPlugin, AgentResponse
from plugins.hermes.plugin import HermesPlugin, _parse_hermes_output
from shared.cli_agent_runner import CLIRunResult


def _make_config(**overrides):
    cfg = {
        "hermes_binary": "hermes",
        "hermes_timeout_s": 300,
        "hermes_workdir": "",
        "hermes_ov_home": "",
        "hermes_config_home": "",
        "hermes_mcp_tools": False,
        "ov_url": "http://127.0.0.1:19080",
        "ov_api_key": "",
        "ov_account": "",
        "ov_user": "",
    }
    cfg.update(overrides)
    return cfg


def _make_plugin(config=None, runner=None):
    plugin = HermesPlugin()
    plugin.setup(config or _make_config())
    if runner is not None:
        plugin._runner = runner
    return plugin


def _hermes_output(text: str) -> str:
    """Build stdout in the format hermes -Q produces (response text only)."""
    return text


def _hermes_stderr(session_id: str = "20260807_120000_abcdef") -> str:
    """Build stderr in the format hermes -Q produces (session_id line)."""
    return f"\nsession_id: {session_id}"


class _FakeRunner:
    """Fake CLIAgentRunner that returns preset CLIRunResult objects."""

    def __init__(self, results=None):
        self._results = list(results or [])
        self.calls: list[dict] = []
        self.default_timeout_s = 300

    def run(self, args, *, timeout_s=None, cwd=None, env=None):
        self.calls.append({
            "args": args,
            "timeout_s": timeout_s,
            "cwd": cwd,
            "env": env,
        })
        if self._results:
            return self._results.pop(0)
        return CLIRunResult(stdout="", stderr="", returncode=0, elapsed_s=0.01)

    def get_logs_json(self):
        return json.dumps([{"command": c["args"]} for c in self.calls])


# ------------------------------------------------------------------ #
#  Descriptor & inheritance                                           #
# ------------------------------------------------------------------ #

class HermesDescriptorTests(unittest.TestCase):

    def test_descriptor_fields(self):
        d = HermesPlugin.descriptor
        self.assertEqual("hermes", d.id)
        self.assertEqual("Hermes Agent", d.name)

    def test_inherits_agent_plugin(self):
        self.assertTrue(issubclass(HermesPlugin, AgentPlugin))


# ------------------------------------------------------------------ #
#  add_arguments                                                      #
# ------------------------------------------------------------------ #

class HermesAddArgumentsTests(unittest.TestCase):

    def _make_parser(self):
        parser = argparse.ArgumentParser(prog="test")
        HermesPlugin.add_arguments(parser)
        return parser

    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            for k in ("HERMES_BINARY", "HERMES_TIMEOUT_S", "HERMES_WORKDIR"):
                os.environ.pop(k, None)
            parser = self._make_parser()
            args = parser.parse_args([])
        self.assertEqual("hermes", args.hermes_binary)
        self.assertEqual(300, args.hermes_timeout_s)
        self.assertEqual("", args.hermes_workdir)

    def test_memory_backend_args_present(self):
        parser = self._make_parser()
        args = parser.parse_args(["--memory-backend", "echomem"])
        self.assertEqual("echomem", args.memory_backend)

    def test_custom_values(self):
        parser = self._make_parser()
        args = parser.parse_args([
            "--hermes-binary", "/usr/bin/hermes",
            "--hermes-timeout-s", "120",
            "--hermes-workdir", "/tmp/proj",
        ])
        self.assertEqual("/usr/bin/hermes", args.hermes_binary)
        self.assertEqual(120, args.hermes_timeout_s)
        self.assertEqual("/tmp/proj", args.hermes_workdir)


# ------------------------------------------------------------------ #
#  setup                                                              #
# ------------------------------------------------------------------ #

class HermesSetupTests(unittest.TestCase):

    def test_creates_runner(self):
        plugin = _make_plugin(_make_config(hermes_binary="/path/hermes", hermes_timeout_s=120))
        self.assertEqual("/path/hermes", plugin._runner.binary)
        self.assertEqual(120, plugin._runner.default_timeout_s)

    def test_creates_null_memory_client_by_default(self):
        plugin = _make_plugin()
        self.assertIsInstance(plugin.memory_client, NullMemoryClient)

    def test_stores_workdir(self):
        plugin = _make_plugin(_make_config(hermes_workdir="/proj"))
        self.assertEqual("/proj", plugin._workdir)

    def test_creates_session_map(self):
        plugin = _make_plugin()
        self.assertEqual({}, plugin._session_map)


# ------------------------------------------------------------------ #
#  create_session                                                     #
# ------------------------------------------------------------------ #

class HermesCreateSessionTests(unittest.TestCase):

    def test_returns_unique_id(self):
        plugin = _make_plugin()
        s1 = plugin.create_session()
        s2 = plugin.create_session()
        self.assertNotEqual(s1, s2)
        self.assertTrue(s1.startswith("eval_hermes_"))
        self.assertTrue(s2.startswith("eval_hermes_"))


# ------------------------------------------------------------------ #
#  _parse_hermes_output                                               #
# ------------------------------------------------------------------ #

class ParseHermesOutputTests(unittest.TestCase):

    def test_parses_text_and_session_id(self):
        stdout = "The answer is 42."
        stderr = "\nsession_id: 20260807_120000_abcdef"
        text, sid = _parse_hermes_output(stdout, stderr)
        self.assertEqual("The answer is 42.", text)
        self.assertEqual("20260807_120000_abcdef", sid)

    def test_no_session_id_returns_all_as_text(self):
        stdout = "Just a plain response with no session line"
        text, sid = _parse_hermes_output(stdout, "")
        self.assertEqual("Just a plain response with no session line", text)
        self.assertEqual("", sid)

    def test_empty_stdout(self):
        text, sid = _parse_hermes_output("", "")
        self.assertEqual("", text)
        self.assertEqual("", sid)

    def test_multiline_response_with_session_id(self):
        stdout = "Line one.\nLine two.\nLine three."
        stderr = "\nsession_id: sid123"
        text, sid = _parse_hermes_output(stdout, stderr)
        self.assertEqual("Line one.\nLine two.\nLine three.", text)
        self.assertEqual("sid123", sid)

    def test_session_id_on_stderr_not_stdout(self):
        """Hermes -Q prints session_id on stderr, not stdout."""
        stdout = "Response text"
        stderr = "\nsession_id: abc123"
        text, sid = _parse_hermes_output(stdout, stderr)
        self.assertEqual("Response text", text)
        self.assertEqual("abc123", sid)


# ------------------------------------------------------------------ #
#  send_message                                                       #
# ------------------------------------------------------------------ #

class HermesSendMessageTests(unittest.TestCase):

    def test_constructs_correct_command(self):
        """First call with a session_id: no --resume (no hermes sid yet)."""
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("hi"), stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("sess1", "hello")

        args = runner.calls[0]["args"]
        self.assertEqual(
            ["chat", "-q", "hello", "-Q", "--yolo", "--source", "tool"],
            args,
        )

    def test_no_resume_when_empty_session_id(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("hi"), stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("", "hi")

        args = runner.calls[0]["args"]
        self.assertNotIn("--resume", args)

    def test_resume_on_second_call_with_same_session(self):
        """Second call with same session_id should include --resume."""
        hermes_sid = "20260807_120000_abcdef"
        export_jsonl = json.dumps({
            "id": hermes_sid, "input_tokens": 10, "output_tokens": 5,
            "api_call_count": 1, "messages": [],
        })
        runner = _FakeRunner([
            # First send_message: chat + export
            CLIRunResult(stdout=_hermes_output("first"),
                         stderr=_hermes_stderr(hermes_sid), returncode=0, elapsed_s=0.1),
            CLIRunResult(stdout=export_jsonl, stderr="", returncode=0, elapsed_s=0.01),
            # Second send_message: chat + export
            CLIRunResult(stdout=_hermes_output("second"),
                         stderr=_hermes_stderr(hermes_sid), returncode=0, elapsed_s=0.1),
            CLIRunResult(stdout=export_jsonl, stderr="", returncode=0, elapsed_s=0.01),
        ])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "first msg")
        plugin.send_message("s1", "second msg")

        # calls[0] = first chat (no --resume)
        self.assertNotIn("--resume", runner.calls[0]["args"])
        # calls[1] = first export
        self.assertEqual("sessions", runner.calls[1]["args"][0])
        # calls[2] = second chat (has --resume)
        args2 = runner.calls[2]["args"]
        self.assertIn("--resume", args2)
        resume_idx = args2.index("--resume")
        self.assertEqual(hermes_sid, args2[resume_idx + 1])

    def test_empty_session_id_no_lock_created(self):
        """Empty session_id: no per-session lock is created."""
        plugin = _make_plugin(runner=_FakeRunner([
            CLIRunResult(stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01),
        ]))
        plugin.send_message("", "hi")
        self.assertNotIn("", plugin._session_locks)

    def test_non_empty_session_id_creates_lock(self):
        """Non-empty session_id: a per-session lock is created."""
        plugin = _make_plugin(runner=_FakeRunner([
            CLIRunResult(stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01),
        ]))
        plugin.send_message("s1", "hi")
        self.assertIn("s1", plugin._session_locks)

    def test_parses_text_response(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("The answer is 42."),
            stderr="", returncode=0, elapsed_s=0.5,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "question")

        self.assertEqual("The answer is 42.", resp.text)
        self.assertIsNone(resp.error)

    def test_nonzero_exit_returns_error(self):
        runner = _FakeRunner([CLIRunResult(
            stdout="", stderr="Error: connection refused", returncode=1, elapsed_s=0.05,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertEqual("Error: connection refused", resp.error)
        self.assertEqual("", resp.text)

    def test_timeout_returns_error(self):
        runner = _FakeRunner([CLIRunResult(
            stdout="", stderr="timed out", returncode=-1, elapsed_s=30, timed_out=True,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertIn("timed out", resp.error)
        self.assertTrue(resp.extra["timed_out"])

    def test_extra_none_does_not_raise(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi", extra=None)
        self.assertIsInstance(resp, AgentResponse)

    def test_system_prompt_append_prepended(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "question", extra={"system_prompt_append": "You are a math expert."})

        args = runner.calls[0]["args"]
        # args = ["chat", "-q", <full_message>, "-Q", "--yolo", "--source", "tool"]
        message = args[2]
        self.assertTrue(message.startswith("You are a math expert."))
        self.assertIn("question", message)

    def test_question_timeout_passed_to_runner(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "hi", extra={"question_timeout_s": 45})

        self.assertEqual(45, runner.calls[0]["timeout_s"])

    def test_workdir_passed_to_runner(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(hermes_workdir="/proj"), runner=runner)
        plugin.send_message("s1", "hi")

        self.assertEqual("/proj", runner.calls[0]["cwd"])


# ------------------------------------------------------------------ #
#  getlog & teardown                                                  #
# ------------------------------------------------------------------ #

class HermesGetlogTests(unittest.TestCase):

    def test_getlog_returns_runner_logs(self):
        export_jsonl = json.dumps({
            "id": "sid", "input_tokens": 10, "output_tokens": 5,
            "api_call_count": 1, "messages": [],
        })
        runner = _FakeRunner([
            CLIRunResult(stdout=_hermes_output("ok"),
                         stderr=_hermes_stderr("sid"), returncode=0, elapsed_s=0.01),
            CLIRunResult(stdout=export_jsonl, stderr="", returncode=0, elapsed_s=0.01),
        ])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "hi")

        logs = json.loads(plugin.getlog())
        self.assertEqual(2, len(logs))

    def test_getlog_empty_before_any_call(self):
        runner = _FakeRunner()
        plugin = _make_plugin(runner=runner)
        logs = json.loads(plugin.getlog())
        self.assertEqual([], logs)

    def test_teardown_is_noop(self):
        plugin = _make_plugin()
        plugin.teardown()


# ------------------------------------------------------------------ #
#  inject_memories (no-op for NullMemoryClient)                       #
# ------------------------------------------------------------------ #

class HermesInjectMemoriesTests(unittest.TestCase):

    def test_inject_returns_session_id_unchanged(self):
        plugin = _make_plugin()
        result = plugin.inject_memories(
            [{"text": "memory"}], backend="echomem", session_id="my_session",
        )
        self.assertEqual("my_session", result)

    def test_inject_with_memory_backend(self):
        mock_client = MagicMock()
        mock_client.commit_session.return_value = "archive_1"
        mock_client.poll_commit.return_value = MagicMock(status="completed")

        plugin = _make_plugin(_make_config(memory_backend="echomem"))
        plugin.memory_client = mock_client

        result = plugin.inject_memories(
            [{"text": "mem1"}], session_id="s1",
        )
        self.assertEqual("s1", result)
        mock_client.add_message.assert_called_once()


# ------------------------------------------------------------------ #
#  typing simulation                                                  #
# ------------------------------------------------------------------ #

class HermesTypingTests(unittest.TestCase):

    def test_does_not_support_typing(self):
        plugin = _make_plugin()
        self.assertFalse(plugin.supports_typing_simulation)

    def test_simulate_typing_returns_none(self):
        plugin = _make_plugin()
        self.assertIsNone(plugin.simulate_typing("s1", "/", "hi", 200, 20))


# ------------------------------------------------------------------ #
#  OpenViking integration                                             #
# ------------------------------------------------------------------ #

class HermesOVArgumentTests(unittest.TestCase):

    def _make_parser(self):
        parser = argparse.ArgumentParser(prog="test")
        HermesPlugin.add_arguments(parser)
        return parser

    def test_ov_args_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            for k in ("HERMES_OV_HOME", "OPENVIKING_URL", "OPENVIKING_API_KEY",
                      "OPENVIKING_ACCOUNT", "OPENVIKING_USER"):
                os.environ.pop(k, None)
            parser = self._make_parser()
            args = parser.parse_args([])
        self.assertEqual("", args.hermes_ov_home)
        self.assertEqual("", args.hermes_config_home)
        self.assertFalse(args.hermes_mcp_tools)
        self.assertEqual("http://127.0.0.1:19080", args.ov_url)

    def test_ov_args_custom(self):
        parser = self._make_parser()
        args = parser.parse_args([
            "--hermes-ov-home", "/tmp/ov_hermes",
            "--hermes-config-home", "/home/.hermes",
            "--hermes-mcp-tools",
            "--ov-url", "http://ov.example.com:1933",
            "--ov-api-key", "sk-test",
            "--ov-account", "acct1",
            "--ov-user", "user1",
        ])
        self.assertEqual("/tmp/ov_hermes", args.hermes_ov_home)
        self.assertEqual("/home/.hermes", args.hermes_config_home)
        self.assertTrue(args.hermes_mcp_tools)
        self.assertEqual("http://ov.example.com:1933", args.ov_url)
        self.assertEqual("sk-test", args.ov_api_key)
        self.assertEqual("acct1", args.ov_account)
        self.assertEqual("user1", args.ov_user)

    def test_ov_args_from_env(self):
        with patch.dict(os.environ, {
            "HERMES_OV_HOME": "/from/env",
            "OPENVIKING_URL": "http://env-ov:1933",
            "OPENVIKING_API_KEY": "env-key",
        }):
            parser = self._make_parser()
            args = parser.parse_args([])
        self.assertEqual("/from/env", args.hermes_ov_home)
        self.assertEqual("http://env-ov:1933", args.ov_url)
        self.assertEqual("env-key", args.ov_api_key)


class HermesOVSetupTests(unittest.TestCase):

    @patch("plugins.hermes.plugin.write_hermes_ov_files")
    def test_stores_ov_config(self, mock_write):
        mock_write.return_value = "/ov/home"
        plugin = _make_plugin(_make_config(
            hermes_ov_home="/ov/home",
            hermes_config_home="/user/config",
            hermes_mcp_tools=True,
            ov_url="http://ov:1933",
            ov_api_key="key1",
            ov_account="acct",
            ov_user="usr",
        ))
        self.assertEqual("/ov/home", plugin._ov_home)
        self.assertEqual("/user/config", plugin._config_home)
        self.assertTrue(plugin._ov_mcp_tools)
        self.assertEqual("http://ov:1933", plugin._ov_url)
        self.assertEqual("key1", plugin._ov_api_key)
        self.assertEqual("acct", plugin._ov_account)
        self.assertEqual("usr", plugin._ov_user)

    def test_ov_home_empty_by_default(self):
        plugin = _make_plugin()
        self.assertEqual("", plugin._ov_home)
        self.assertEqual("", plugin._config_home)
        self.assertFalse(plugin._ov_mcp_tools)


class HermesOVSendMessageTests(unittest.TestCase):

    @patch("plugins.hermes.plugin.write_hermes_ov_files")
    def test_ov_home_sets_env_vars(self, mock_write):
        mock_write.return_value = "/resolved/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            hermes_ov_home="/ov/home",
            hermes_config_home="/user/config",
            ov_api_key="sk-test",
            ov_account="acct",
            ov_user="usr",
        ), runner=runner)
        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertIsNotNone(env)
        self.assertEqual("/resolved/ov/home", env["HERMES_HOME"])
        self.assertEqual("sk-test", env["OPENVIKING_API_KEY"])
        self.assertEqual("acct", env["OPENVIKING_ACCOUNT"])
        self.assertEqual("usr", env["OPENVIKING_USER"])
        self.assertEqual("http://127.0.0.1:19080", env["OPENVIKING_URL"])

    @patch("plugins.hermes.plugin.write_hermes_ov_files")
    def test_ov_home_calls_write_with_mcp_off(self, mock_write):
        mock_write.return_value = "/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            hermes_ov_home="/ov/home",
            hermes_config_home="/user/config",
            hermes_mcp_tools=False,
        ), runner=runner)
        plugin.send_message("s1", "hi")

        mock_write.assert_called_once_with(
            "/ov/home",
            mcp_tools=False,
            ov_url="http://127.0.0.1:19080",
            config_home="/user/config",
        )

    @patch("plugins.hermes.plugin.write_hermes_ov_files")
    def test_ov_home_calls_write_with_mcp_on(self, mock_write):
        mock_write.return_value = "/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            hermes_ov_home="/ov/home",
            hermes_config_home="/user/config",
            hermes_mcp_tools=True,
            ov_url="http://custom-ov:1933",
        ), runner=runner)
        plugin.send_message("s1", "hi")

        mock_write.assert_called_once_with(
            "/ov/home",
            mcp_tools=True,
            ov_url="http://custom-ov:1933",
            config_home="/user/config",
        )

    def test_no_ov_home_means_env_none(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "hi")

        self.assertIsNone(runner.calls[0]["env"])

    def test_ov_home_without_config_home_raises(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        with self.assertRaises(ValueError) as ctx:
            _make_plugin(_make_config(
                hermes_ov_home="/ov/home",
                hermes_config_home="",
            ), runner=runner)
        self.assertIn("--hermes-config-home", str(ctx.exception))

    @patch("plugins.hermes.plugin.write_hermes_ov_files")
    def test_ov_env_passes_custom_url(self, mock_write):
        mock_write.return_value = "/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            hermes_ov_home="/ov/home",
            hermes_config_home="/user/config",
            ov_url="http://custom:9999",
        ), runner=runner)
        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertEqual("http://custom:9999", env["OPENVIKING_URL"])

    @patch("plugins.hermes.plugin.create_memory_client")
    @patch("plugins.hermes.plugin.write_hermes_ov_files")
    def test_ov_account_falls_back_to_memory_client(self, mock_write, mock_create_client):
        """When --ov-account is not set, use memory_client's provisioned account."""
        mock_write.return_value = "/ov/home"
        mock_client = MagicMock()
        mock_client.account = "eval-provisioned-acct"
        mock_client.user_id = "provisioned-user"
        mock_create_client.return_value = mock_client
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            hermes_ov_home="/ov/home",
            hermes_config_home="/user/config",
            ov_account="",
            ov_user="",
        ), runner=runner)

        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertEqual("eval-provisioned-acct", env["OPENVIKING_ACCOUNT"])
        self.assertEqual("provisioned-user", env["OPENVIKING_USER"])

    @patch("plugins.hermes.plugin.create_memory_client")
    @patch("plugins.hermes.plugin.write_hermes_ov_files")
    def test_ov_account_explicit_overrides_memory_client(self, mock_write, mock_create_client):
        """Explicit --ov-account takes precedence over memory_client's account."""
        mock_write.return_value = "/ov/home"
        mock_client = MagicMock()
        mock_client.account = "eval-provisioned-acct"
        mock_client.user_id = "provisioned-user"
        mock_create_client.return_value = mock_client
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            hermes_ov_home="/ov/home",
            hermes_config_home="/user/config",
            ov_account="explicit-acct",
            ov_user="explicit-user",
        ), runner=runner)

        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertEqual("explicit-acct", env["OPENVIKING_ACCOUNT"])
        self.assertEqual("explicit-user", env["OPENVIKING_USER"])


# ------------------------------------------------------------------ #
#  Telemetry                                                          #
# ------------------------------------------------------------------ #

class HermesReadSidecarTests(unittest.TestCase):

    def test_reads_sidecar_file(self):
        import tempfile
        sidecar = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8",
        )
        json.dump({
            "query": "test query",
            "items": [{"uri": "mem://1", "score": 0.8, "content": "hello"}],
            "rendered": "<memory>hello</memory>",
            "latency_ms": 42,
            "error": "",
        }, sidecar)
        sidecar.close()

        items, latency, error = HermesPlugin._read_hook_sidecar(sidecar.name)
        self.assertEqual(1, len(items))
        self.assertEqual("mem://1", items[0]["uri"])
        self.assertAlmostEqual(0.042, latency, places=3)
        self.assertEqual("", error)

        os.unlink(sidecar.name)

    def test_missing_file_returns_empty(self):
        items, latency, error = HermesPlugin._read_hook_sidecar(
            "/nonexistent/path/file.json",
        )
        self.assertEqual([], items)
        self.assertEqual(0.0, latency)
        self.assertEqual("", error)

    def test_empty_path_returns_empty(self):
        items, latency, error = HermesPlugin._read_hook_sidecar("")
        self.assertEqual([], items)
        self.assertEqual(0.0, latency)
        self.assertEqual("", error)


class HermesSendMessageTelemetryTests(unittest.TestCase):

    @patch("plugins.hermes.plugin.write_hermes_ov_files")
    def test_sidecar_env_set_when_ov_enabled(self, mock_write):
        mock_write.return_value = "/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            hermes_ov_home="/ov/home",
            hermes_config_home="/user/config",
        ), runner=runner)
        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertIn("OV_SIDECAR_PATH", env)
        self.assertIn("recall_", env["OV_SIDECAR_PATH"])

    def test_sidecar_env_not_set_when_ov_disabled(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertIsNone(env)

    def test_send_message_returns_telemetry_fields(self):
        """Hermes -Q output is plain text; session export provides telemetry."""
        session_jsonl = json.dumps({
            "id": "sid1",
            "input_tokens": 100,
            "output_tokens": 20,
            "api_call_count": 2,
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "ok", "tool_calls": []},
            ],
        })
        runner = _FakeRunner([
            CLIRunResult(stdout=_hermes_output("The answer is 42."),
                         stderr=_hermes_stderr("sid1"), returncode=0, elapsed_s=0.5),
            # Second call: sessions export
            CLIRunResult(stdout=session_jsonl, stderr="", returncode=0, elapsed_s=0.01),
        ])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "question")

        self.assertEqual("The answer is 42.", resp.text)
        self.assertEqual(100, resp.prompt_tokens)
        self.assertEqual(20, resp.completion_tokens)
        self.assertIn("tool_call_count", resp.extra)
        self.assertIn("iterations", resp.extra)
        self.assertIn("retrieval_latency_s", resp.extra)
        self.assertIn("retrieval_error", resp.extra)
        self.assertIn("trace", resp.extra)
        self.assertIn("tool_audit", resp.extra["trace"])
        self.assertEqual(0, resp.extra["tool_call_count"])
        self.assertEqual(2, resp.extra["iterations"])

    def test_tool_calls_parsed_from_session_export(self):
        """Tool calls are extracted from the session export JSONL."""
        session_jsonl = json.dumps({
            "id": "sid2",
            "input_tokens": 500,
            "output_tokens": 50,
            "api_call_count": 3,
            "messages": [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "search_files",
                                  "arguments": '{"pattern": "*.py"}'}},
                    {"function": {"name": "terminal",
                                  "arguments": '{"command": "ls"}'}},
                ]},
                {"role": "tool", "content": "result"},
                {"role": "assistant", "content": "done", "tool_calls": []},
            ],
        })
        runner = _FakeRunner([
            CLIRunResult(stdout=_hermes_output("Done."),
                         stderr=_hermes_stderr("sid2"), returncode=0, elapsed_s=0.5),
            CLIRunResult(stdout=session_jsonl, stderr="", returncode=0, elapsed_s=0.01),
        ])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertEqual(2, resp.extra["tool_call_count"])
        self.assertEqual(3, resp.extra["iterations"])
        self.assertEqual(500, resp.prompt_tokens)
        self.assertEqual(50, resp.completion_tokens)
        audit = resp.extra["trace"]["tool_audit"]
        self.assertIn("search_files", audit["tools_used"])
        self.assertIn("terminal", audit["tools_used"])
        self.assertEqual(2, len(audit["tool_calls"]))
        self.assertEqual("search_files", audit["tool_calls"][0]["name"])
        self.assertEqual({"pattern": "*.py"}, audit["tool_calls"][0]["arguments"])
        self.assertEqual("terminal", audit["tool_calls"][1]["name"])
        self.assertEqual({"command": "ls"}, audit["tool_calls"][1]["arguments"])

    def test_session_export_failure_returns_zeros(self):
        """If session export fails, telemetry fields are zero/empty."""
        runner = _FakeRunner([
            CLIRunResult(stdout=_hermes_output("ok"),
                         stderr=_hermes_stderr("sid3"), returncode=0, elapsed_s=0.01),
            # Export fails
            CLIRunResult(stdout="", stderr="error", returncode=1, elapsed_s=0.01),
        ])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertEqual(0, resp.prompt_tokens)
        self.assertEqual(0, resp.completion_tokens)
        self.assertEqual(0, resp.extra["tool_call_count"])
        self.assertEqual(1, resp.extra["iterations"])

    def test_no_session_id_falls_back_to_recent_export(self):
        """When hermes doesn't return a session_id, falls back to
        exporting the most recent session (without --session-id)."""
        runner = _FakeRunner([
            CLIRunResult(
                stdout="Plain text without session_id line",
                stderr="", returncode=0, elapsed_s=0.01,
            ),
            # Fallback export returns empty (no recent session found)
            CLIRunResult(stdout="", stderr="", returncode=0, elapsed_s=0.01),
        ])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        # Two calls: chat + fallback export (without --session-id)
        self.assertEqual(2, len(runner.calls))
        export_args = runner.calls[1]["args"]
        self.assertNotIn("--session-id", export_args)
        self.assertEqual(0, resp.prompt_tokens)
        self.assertEqual(0, resp.completion_tokens)

    @patch("plugins.hermes.plugin.write_hermes_ov_files")
    def test_memory_items_from_sidecar(self, mock_write):
        """When OV hook writes a sidecar, memory_items appear in the response."""
        import tempfile, shutil
        ov_dir = tempfile.mkdtemp()
        try:
            mock_write.return_value = ov_dir
            runner = _FakeRunner([CLIRunResult(
                stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
            )])
            plugin = _make_plugin(_make_config(
                hermes_ov_home=ov_dir,
                hermes_config_home="/user/config",
            ), runner=runner)

            test_hex = "abc123def456"
            sidecar_path = os.path.join(ov_dir, f"recall_{test_hex}.json")
            with open(sidecar_path, "w", encoding="utf-8") as f:
                json.dump({
                    "query": "hi",
                    "items": [{"uri": "mem://1", "score": 0.9, "content": "recalled"}],
                    "rendered": "<memory>recalled</memory>",
                    "latency_ms": 15,
                    "error": "",
                }, f)

            import plugins.hermes.plugin as mod
            with patch.object(mod.uuid, "uuid4") as mock_uuid:
                mock_uuid.return_value.hex = test_hex
                resp = plugin.send_message("s1", "hi")

            self.assertEqual(1, len(resp.memory_items))
            self.assertEqual("mem://1", resp.memory_items[0]["uri"])
            self.assertAlmostEqual(0.015, resp.extra["retrieval_latency_s"], places=3)
            self.assertEqual("", resp.extra["retrieval_error"])
        finally:
            shutil.rmtree(ov_dir, ignore_errors=True)

    @patch("plugins.hermes.plugin.write_hermes_ov_files")
    def test_sidecar_cleaned_up_after_success(self, mock_write):
        import tempfile, shutil
        ov_dir = tempfile.mkdtemp()
        try:
            mock_write.return_value = ov_dir
            runner = _FakeRunner([CLIRunResult(
                stdout=_hermes_output("ok"), stderr="", returncode=0, elapsed_s=0.01,
            )])
            plugin = _make_plugin(_make_config(
                hermes_ov_home=ov_dir,
                hermes_config_home="/user/config",
            ), runner=runner)

            test_hex = "abc123def456"
            sidecar_path = os.path.join(ov_dir, f"recall_{test_hex}.json")
            with open(sidecar_path, "w", encoding="utf-8") as f:
                json.dump({"query": "hi", "items": [], "rendered": "",
                           "latency_ms": 0, "error": ""}, f)

            import plugins.hermes.plugin as mod
            with patch.object(mod.uuid, "uuid4") as mock_uuid:
                mock_uuid.return_value.hex = test_hex
                plugin.send_message("s1", "hi")

            self.assertFalse(os.path.exists(sidecar_path))
        finally:
            shutil.rmtree(ov_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
