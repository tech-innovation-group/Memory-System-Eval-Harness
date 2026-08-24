"""Unit tests for plugins.kimi_code.plugin.

All subprocess calls are mocked -- no real kimi CLI is invoked.

Run: python -m unittest tests.test_plugins_kimi_code -v
"""

from __future__ import annotations

import argparse
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from backends.memory_types import NullMemoryClient
from plugins.base import AgentPlugin, AgentResponse
from plugins.kimi_code.plugin import KimiCodePlugin
from shared.cli_agent_runner import CLIRunResult


def _make_config(**overrides):
    cfg = {
        "kimi_binary": "kimi",
        "kimi_model": "",
        "kimi_timeout_s": 300,
        "kimi_workdir": "",
        "kimi_ov_home": "",
        "kimi_config_home": "",
        "kimi_mcp_tools": False,
        "ov_url": "http://127.0.0.1:19080",
        "ov_api_key": "",
        "ov_account": "",
        "ov_user": "",
    }
    cfg.update(overrides)
    return cfg


def _make_plugin(config=None, runner=None):
    plugin = KimiCodePlugin()
    plugin.setup(config or _make_config())
    if runner is not None:
        plugin._runner = runner
    return plugin


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
        return CLIRunResult(stdout="{}", stderr="", returncode=0, elapsed_s=0.01)

    def get_logs_json(self):
        return json.dumps([{"command": c["args"]} for c in self.calls])


def _stream_json(*lines):
    """Build a JSONL string from dict objects."""
    return "\n".join(json.dumps(obj) for obj in lines)


# ------------------------------------------------------------------ #
#  Descriptor & inheritance                                           #
# ------------------------------------------------------------------ #

class KimiCodeDescriptorTests(unittest.TestCase):

    def test_descriptor_fields(self):
        d = KimiCodePlugin.descriptor
        self.assertEqual("kimi_code", d.id)
        self.assertEqual("Kimi Code", d.name)

    def test_inherits_agent_plugin(self):
        self.assertTrue(issubclass(KimiCodePlugin, AgentPlugin))


# ------------------------------------------------------------------ #
#  add_arguments                                                      #
# ------------------------------------------------------------------ #

class KimiCodeAddArgumentsTests(unittest.TestCase):

    def _make_parser(self):
        parser = argparse.ArgumentParser(prog="test")
        KimiCodePlugin.add_arguments(parser)
        return parser

    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            for k in ("KIMI_BINARY", "KIMI_MODEL", "KIMI_TIMEOUT_S", "KIMI_WORKDIR"):
                os.environ.pop(k, None)
            parser = self._make_parser()
            args = parser.parse_args([])
        self.assertEqual("kimi", args.kimi_binary)
        self.assertEqual("", args.kimi_model)
        self.assertEqual(300, args.kimi_timeout_s)
        self.assertEqual("", args.kimi_workdir)

    def test_memory_backend_args_present(self):
        parser = self._make_parser()
        args = parser.parse_args(["--memory-backend", "echomem"])
        self.assertEqual("echomem", args.memory_backend)

    def test_custom_values(self):
        parser = self._make_parser()
        args = parser.parse_args([
            "--kimi-binary", "/d/.kimi-code/bin/kimi",
            "--kimi-model", "moonshot-v1-128k",
            "--kimi-timeout-s", "120",
            "--kimi-workdir", "/tmp/proj",
        ])
        self.assertEqual("/d/.kimi-code/bin/kimi", args.kimi_binary)
        self.assertEqual("moonshot-v1-128k", args.kimi_model)
        self.assertEqual(120, args.kimi_timeout_s)
        self.assertEqual("/tmp/proj", args.kimi_workdir)


# ------------------------------------------------------------------ #
#  setup                                                              #
# ------------------------------------------------------------------ #

class KimiCodeSetupTests(unittest.TestCase):

    def test_creates_runner(self):
        plugin = _make_plugin(_make_config(kimi_binary="/path/kimi", kimi_timeout_s=120))
        self.assertEqual("/path/kimi", plugin._runner.binary)
        self.assertEqual(120, plugin._runner.default_timeout_s)

    def test_creates_null_memory_client_by_default(self):
        plugin = _make_plugin()
        self.assertIsInstance(plugin.memory_client, NullMemoryClient)

    def test_stores_model_and_workdir(self):
        plugin = _make_plugin(_make_config(kimi_model="moonshot-v1-128k", kimi_workdir="/proj"))
        self.assertEqual("moonshot-v1-128k", plugin._model)
        self.assertEqual("/proj", plugin._workdir)

    def test_initializes_empty_session_map(self):
        plugin = _make_plugin()
        self.assertEqual({}, plugin._kimi_sessions)
        self.assertEqual({}, plugin._session_locks)


# ------------------------------------------------------------------ #
#  create_session                                                     #
# ------------------------------------------------------------------ #

class KimiCodeCreateSessionTests(unittest.TestCase):

    def test_returns_unique_id(self):
        plugin = _make_plugin()
        s1 = plugin.create_session()
        s2 = plugin.create_session()
        self.assertNotEqual(s1, s2)
        self.assertTrue(s1.startswith("eval_kimi_"))
        self.assertTrue(s2.startswith("eval_kimi_"))


# ------------------------------------------------------------------ #
#  send_message                                                       #
# ------------------------------------------------------------------ #

class KimiCodeSendMessageTests(unittest.TestCase):

    def test_constructs_correct_command(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "hi"},
                {"role": "meta", "type": "session.resume_hint",
                 "session_id": "session_abc", "command": "kimi -r session_abc",
                 "content": "To resume this session: kimi -r session_abc"},
            ),
            stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("sess1", "hello")

        args = runner.calls[0]["args"]
        # First call: no -S since we don't have a kimi session yet
        self.assertEqual(["-p", "hello", "--output-format", "stream-json"], args)

    def test_includes_model_when_set(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "hi"},
                {"role": "meta", "type": "session.resume_hint",
                 "session_id": "session_abc", "command": "", "content": ""},
            ),
            stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(_make_config(kimi_model="moonshot-v1-128k"), runner=runner)
        plugin.send_message("s1", "hi")

        args = runner.calls[0]["args"]
        self.assertIn("-m", args)
        self.assertIn("moonshot-v1-128k", args)

    def test_parses_stream_json_response(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "The answer is 42."},
                {"role": "meta", "type": "session.resume_hint",
                 "session_id": "session_abc123",
                 "command": "kimi -r session_abc123",
                 "content": "To resume this session: kimi -r session_abc123"},
            ),
            stderr="", returncode=0, elapsed_s=0.5,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "question")

        self.assertEqual("The answer is 42.", resp.text)
        self.assertIsNone(resp.error)
        self.assertEqual("session_abc123", resp.extra["kimi_session_id"])

    def test_stores_kimi_session_id_for_reuse(self):
        runner = _FakeRunner([
            CLIRunResult(
                stdout=_stream_json(
                    {"role": "assistant", "content": "first"},
                    {"role": "meta", "type": "session.resume_hint",
                     "session_id": "session_xyz",
                     "command": "", "content": ""},
                ),
                stderr="", returncode=0, elapsed_s=0.1,
            ),
            CLIRunResult(
                stdout=_stream_json(
                    {"role": "assistant", "content": "second"},
                ),
                stderr="", returncode=0, elapsed_s=0.1,
            ),
        ])
        plugin = _make_plugin(runner=runner)

        # First call: no -S
        plugin.send_message("s1", "first")
        self.assertNotIn("-S", runner.calls[0]["args"])
        self.assertEqual("session_xyz", plugin._kimi_sessions["s1"])

        # Second call: should include -S session_xyz
        plugin.send_message("s1", "second")
        args = runner.calls[1]["args"]
        self.assertIn("-S", args)
        idx = args.index("-S")
        self.assertEqual("session_xyz", args[idx + 1])

    def test_empty_session_id_no_resume_no_mapping(self):
        """Empty session_id: each call is independent (no -S, no mapping)."""
        runner = _FakeRunner([
            CLIRunResult(
                stdout=_stream_json(
                    {"role": "assistant", "content": "first"},
                    {"role": "meta", "type": "session.resume_hint",
                     "session_id": "session_a", "command": "", "content": ""},
                ),
                stderr="", returncode=0, elapsed_s=0.1,
            ),
            CLIRunResult(
                stdout=_stream_json(
                    {"role": "assistant", "content": "second"},
                    {"role": "meta", "type": "session.resume_hint",
                     "session_id": "session_b", "command": "", "content": ""},
                ),
                stderr="", returncode=0, elapsed_s=0.1,
            ),
        ])
        plugin = _make_plugin(runner=runner)

        # First call: no -S
        plugin.send_message("", "first")
        self.assertNotIn("-S", runner.calls[0]["args"])

        # Second call: still no -S (empty session_id never maps)
        plugin.send_message("", "second")
        self.assertNotIn("-S", runner.calls[1]["args"])

        # No mapping stored for empty session_id
        self.assertNotIn("", plugin._kimi_sessions)

    def test_non_empty_session_id_creates_lock(self):
        """Non-empty session_id: a per-session lock is created."""
        plugin = _make_plugin(runner=_FakeRunner())
        plugin.send_message("s1", "hi")
        self.assertIn("s1", plugin._session_locks)

    def test_empty_session_id_no_lock_created(self):
        """Empty session_id: no per-session lock is created."""
        plugin = _make_plugin(runner=_FakeRunner())
        plugin.send_message("", "hi")
        self.assertNotIn("", plugin._session_locks)

    def test_multi_line_assistant_output(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "line 1"},
                {"role": "assistant", "content": "line 2"},
                {"role": "meta", "type": "session.resume_hint",
                 "session_id": "session_m", "command": "", "content": ""},
            ),
            stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertEqual("line 1\nline 2", resp.text)

    def test_non_json_line_skipped(self):
        """Non-JSON lines (hook output, banners) are skipped in stream-json mode."""
        runner = _FakeRunner([CLIRunResult(
            stdout="UserPromptSubmit hook\n" + json.dumps(
                {"role": "assistant", "content": "json part"}
            ),
            stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertNotIn("UserPromptSubmit hook", resp.text)
        self.assertEqual("json part", resp.text)

    def test_hook_output_in_assistant_json_filtered(self):
        """kimi-code wraps hook stdout in role=assistant JSON; filter by <openviking-context>."""
        runner = _FakeRunner([CLIRunResult(
            stdout="\n".join([
                json.dumps({"role": "assistant", "content": (
                    'UserPromptSubmit hook\n\n<openviking-context>\n'
                    '<memory_section source="global">\n</openviking-context>'
                )}),
                json.dumps({"role": "assistant", "content": "The answer is 42."}),
                json.dumps({"role": "meta", "type": "session.resume_hint",
                            "session_id": "s_x", "command": "", "content": ""}),
            ]),
            stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertNotIn("UserPromptSubmit hook", resp.text)
        self.assertNotIn("<openviking-context>", resp.text)
        self.assertEqual("The answer is 42.", resp.text)

    def test_empty_output(self):
        runner = _FakeRunner([CLIRunResult(
            stdout="", stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertEqual("", resp.text)
        self.assertIsNone(resp.error)

    def test_no_meta_line_means_no_session_stored(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "response without meta"},
            ),
            stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertEqual("response without meta", resp.text)
        self.assertIsNone(resp.extra["kimi_session_id"])
        self.assertNotIn("s1", plugin._kimi_sessions)

    def test_nonzero_exit_returns_error(self):
        runner = _FakeRunner([CLIRunResult(
            stdout="", stderr="Error: API key invalid", returncode=1, elapsed_s=0.05,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertEqual("Error: API key invalid", resp.error)
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
            stdout=_stream_json(
                {"role": "assistant", "content": "ok"},
            ),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi", extra=None)
        self.assertIsInstance(resp, AgentResponse)

    def test_system_prompt_append_prepended(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "ok"},
            ),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "question", extra={"system_prompt_append": "You are a math expert."})

        args = runner.calls[0]["args"]
        # args = ["-p", <full_message>, "--output-format", "stream-json"]
        message = args[1]
        self.assertTrue(message.startswith("You are a math expert."))
        self.assertIn("question", message)

    def test_question_timeout_passed_to_runner(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "ok"},
            ),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "hi", extra={"question_timeout_s": 45})

        self.assertEqual(45, runner.calls[0]["timeout_s"])

    def test_workdir_passed_to_runner(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "ok"},
            ),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(kimi_workdir="/proj"), runner=runner)
        plugin.send_message("s1", "hi")

        self.assertEqual("/proj", runner.calls[0]["cwd"])


# ------------------------------------------------------------------ #
#  getlog & teardown                                                  #
# ------------------------------------------------------------------ #

class KimiCodeGetlogTests(unittest.TestCase):

    def test_getlog_returns_runner_logs(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "ok"},
            ),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "hi")

        logs = json.loads(plugin.getlog())
        self.assertEqual(1, len(logs))

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

class KimiCodeInjectMemoriesTests(unittest.TestCase):

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

class KimiCodeTypingTests(unittest.TestCase):

    def test_does_not_support_typing(self):
        plugin = _make_plugin()
        self.assertFalse(plugin.supports_typing_simulation)

    def test_simulate_typing_returns_none(self):
        plugin = _make_plugin()
        self.assertIsNone(plugin.simulate_typing("s1", "/", "hi", 200, 20))


# ------------------------------------------------------------------ #
#  OpenViking integration                                             #
# ------------------------------------------------------------------ #

class KimiCodeOVArgumentTests(unittest.TestCase):

    def _make_parser(self):
        parser = argparse.ArgumentParser(prog="test")
        KimiCodePlugin.add_arguments(parser)
        return parser

    def test_ov_args_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            for k in ("KIMI_OV_HOME", "OPENVIKING_URL", "OPENVIKING_API_KEY",
                      "OPENVIKING_ACCOUNT", "OPENVIKING_USER"):
                os.environ.pop(k, None)
            parser = self._make_parser()
            args = parser.parse_args([])
        self.assertEqual("", args.kimi_ov_home)
        self.assertEqual("", args.kimi_config_home)
        self.assertFalse(args.kimi_mcp_tools)
        self.assertEqual("http://127.0.0.1:19080", args.ov_url)

    def test_ov_args_custom(self):
        parser = self._make_parser()
        args = parser.parse_args([
            "--kimi-ov-home", "/tmp/ov_kimi",
            "--kimi-config-home", "/home/.kimi-code",
            "--kimi-mcp-tools",
            "--ov-url", "http://ov.example.com:1933",
            "--ov-api-key", "sk-test",
            "--ov-account", "acct1",
            "--ov-user", "user1",
        ])
        self.assertEqual("/tmp/ov_kimi", args.kimi_ov_home)
        self.assertEqual("/home/.kimi-code", args.kimi_config_home)
        self.assertTrue(args.kimi_mcp_tools)
        self.assertEqual("http://ov.example.com:1933", args.ov_url)
        self.assertEqual("sk-test", args.ov_api_key)
        self.assertEqual("acct1", args.ov_account)
        self.assertEqual("user1", args.ov_user)

    def test_ov_args_from_env(self):
        with patch.dict(os.environ, {
            "KIMI_OV_HOME": "/from/env",
            "OPENVIKING_URL": "http://env-ov:1933",
            "OPENVIKING_API_KEY": "env-key",
        }):
            parser = self._make_parser()
            args = parser.parse_args([])
        self.assertEqual("/from/env", args.kimi_ov_home)
        self.assertEqual("http://env-ov:1933", args.ov_url)
        self.assertEqual("env-key", args.ov_api_key)


class KimiCodeOVSetupTests(unittest.TestCase):

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_stores_ov_config(self, mock_write):
        mock_write.return_value = "/ov/home"
        plugin = _make_plugin(_make_config(
            kimi_ov_home="/ov/home",
            kimi_config_home="/user/config",
            kimi_mcp_tools=True,
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


class KimiCodeOVSendMessageTests(unittest.TestCase):

    def _ov_result():
        return CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "ok"},
            ),
            stderr="", returncode=0, elapsed_s=0.01,
        )

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_ov_home_sets_env_vars(self, mock_write):
        mock_write.return_value = "/resolved/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            kimi_ov_home="/ov/home",
            kimi_config_home="/user/config",
            ov_api_key="sk-test",
            ov_account="acct",
            ov_user="usr",
        ), runner=runner)
        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertIsNotNone(env)
        self.assertEqual("/resolved/ov/home", env["KIMI_CODE_HOME"])
        self.assertEqual("sk-test", env["OPENVIKING_API_KEY"])
        self.assertEqual("acct", env["OPENVIKING_ACCOUNT"])
        self.assertEqual("usr", env["OPENVIKING_USER"])
        self.assertEqual("http://127.0.0.1:19080", env["OPENVIKING_URL"])

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_ov_home_calls_write_with_mcp_off(self, mock_write):
        mock_write.return_value = "/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            kimi_ov_home="/ov/home",
            kimi_config_home="/user/config",
            kimi_mcp_tools=False,
        ), runner=runner)
        plugin.send_message("s1", "hi")

        mock_write.assert_called_once_with(
            "/ov/home",
            mcp_tools=False,
            ov_url="http://127.0.0.1:19080",
            config_home="/user/config",
        )

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_ov_home_calls_write_with_mcp_on(self, mock_write):
        mock_write.return_value = "/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            kimi_ov_home="/ov/home",
            kimi_config_home="/user/config",
            kimi_mcp_tools=True,
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
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "hi")

        self.assertIsNone(runner.calls[0]["env"])

    def test_ov_home_without_config_home_raises(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        with self.assertRaises(ValueError) as ctx:
            _make_plugin(_make_config(
                kimi_ov_home="/ov/home",
                kimi_config_home="",
            ), runner=runner)
        self.assertIn("--kimi-config-home", str(ctx.exception))

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_ov_env_passes_custom_url(self, mock_write):
        mock_write.return_value = "/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            kimi_ov_home="/ov/home",
            kimi_config_home="/user/config",
            ov_url="http://custom:9999",
        ), runner=runner)
        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertEqual("http://custom:9999", env["OPENVIKING_URL"])

    @patch("plugins.kimi_code.plugin.create_memory_client")
    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_ov_account_falls_back_to_memory_client(self, mock_write, mock_create_client):
        """When --ov-account is not set, use memory_client's provisioned account."""
        mock_write.return_value = "/ov/home"
        mock_client = MagicMock()
        mock_client.account = "eval-provisioned-acct"
        mock_client.user_id = "provisioned-user"
        mock_create_client.return_value = mock_client
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            kimi_ov_home="/ov/home",
            kimi_config_home="/user/config",
            ov_account="",
            ov_user="",
        ), runner=runner)

        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertEqual("eval-provisioned-acct", env["OPENVIKING_ACCOUNT"])
        self.assertEqual("provisioned-user", env["OPENVIKING_USER"])

    @patch("plugins.kimi_code.plugin.create_memory_client")
    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_ov_account_explicit_overrides_memory_client(self, mock_write, mock_create_client):
        """Explicit --ov-account takes precedence over memory_client's account."""
        mock_write.return_value = "/ov/home"
        mock_client = MagicMock()
        mock_client.account = "eval-provisioned-acct"
        mock_client.user_id = "provisioned-user"
        mock_create_client.return_value = mock_client
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            kimi_ov_home="/ov/home",
            kimi_config_home="/user/config",
            ov_account="explicit-acct",
            ov_user="explicit-user",
        ), runner=runner)

        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertEqual("explicit-acct", env["OPENVIKING_ACCOUNT"])
        self.assertEqual("explicit-user", env["OPENVIKING_USER"])


# ------------------------------------------------------------------ #
#  Telemetry: _parse_stream_json tool_calls & iterations              #
# ------------------------------------------------------------------ #

class KimiCodeStreamJsonTelemetryTests(unittest.TestCase):

    def test_extracts_tool_calls_and_iterations(self):
        """_parse_stream_json returns tool_audit and iterations alongside text."""
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "Let me search.",
                 "tool_calls": [
                     {"id": "tc1", "function": {"name": "memory_query",
                      "arguments": '{"query": "Gina tattoo"}'}},
                 ]},
                {"role": "tool", "tool_call_id": "tc1",
                 "content": "Found: Gina got a tattoo in March."},
                {"role": "assistant", "content": "Gina got a tattoo in March."},
                {"role": "meta", "type": "session.resume_hint",
                 "session_id": "s_t", "command": "", "content": ""},
            ),
            stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        # Two assistant lines with content -> iterations=2
        self.assertEqual(2, resp.extra["iterations"])
        # One tool call
        self.assertEqual(1, resp.extra["tool_call_count"])
        audit = resp.extra["trace"]["tool_audit"]
        self.assertIn("memory_query", audit["tools_used"])
        self.assertEqual(1, len(audit["tool_calls"]))
        self.assertEqual("memory_query", audit["tool_calls"][0]["name"])
        self.assertEqual({"query": "Gina tattoo"},
                         audit["tool_calls"][0]["arguments"])
        self.assertIn("Found: Gina got a tattoo",
                       audit["tool_calls"][0]["result_preview"])

    def test_multiple_tool_calls(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "tc1", "function": {"name": "memory_query",
                     "arguments": '{"query": "a"}'}},
                    {"id": "tc2", "function": {"name": "read",
                     "arguments": '{"uris": "mem://1"}'}},
                ]},
                {"role": "tool", "tool_call_id": "tc1", "content": "result1"},
                {"role": "tool", "tool_call_id": "tc2", "content": "result2"},
                {"role": "assistant", "content": "Final answer."},
                {"role": "meta", "type": "session.resume_hint",
                 "session_id": "s_x", "command": "", "content": ""},
            ),
            stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertEqual(2, resp.extra["tool_call_count"])
        audit = resp.extra["trace"]["tool_audit"]
        self.assertEqual(2, len(audit["tools_used"]))
        self.assertEqual("Final answer.", resp.text)

    def test_hook_output_not_counted_as_iteration(self):
        """Assistant lines with <openviking-context> don't count as iterations."""
        runner = _FakeRunner([CLIRunResult(
            stdout="\n".join([
                json.dumps({"role": "assistant", "content": (
                    'UserPromptSubmit hook\n\n<openviking-context>\n'
                    '<memory_section source="global">\n</openviking-context>'
                )}),
                json.dumps({"role": "assistant", "content": "Real answer."}),
            ]),
            stderr="", returncode=0, elapsed_s=0.1,
        )])
        plugin = _make_plugin(runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertEqual(1, resp.extra["iterations"])
        self.assertEqual("Real answer.", resp.text)


# ------------------------------------------------------------------ #
#  Telemetry: _read_hook_sidecar                                      #
# ------------------------------------------------------------------ #

class KimiCodeReadSidecarTests(unittest.TestCase):

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

        items, latency, error = KimiCodePlugin._read_hook_sidecar(sidecar.name)
        self.assertEqual(1, len(items))
        self.assertEqual("mem://1", items[0]["uri"])
        self.assertAlmostEqual(0.042, latency, places=3)
        self.assertEqual("", error)

        os.unlink(sidecar.name)

    def test_missing_file_returns_empty(self):
        items, latency, error = KimiCodePlugin._read_hook_sidecar(
            "/nonexistent/path/file.json",
        )
        self.assertEqual([], items)
        self.assertEqual(0.0, latency)
        self.assertEqual("", error)

    def test_empty_path_returns_empty(self):
        items, latency, error = KimiCodePlugin._read_hook_sidecar("")
        self.assertEqual([], items)
        self.assertEqual(0.0, latency)
        self.assertEqual("", error)

    def test_error_in_sidecar(self):
        import tempfile
        sidecar = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8",
        )
        json.dump({
            "query": "test", "items": [], "rendered": "",
            "latency_ms": 5, "error": "http_500",
        }, sidecar)
        sidecar.close()

        items, latency, error = KimiCodePlugin._read_hook_sidecar(sidecar.name)
        self.assertEqual([], items)
        self.assertAlmostEqual(0.005, latency, places=3)
        self.assertEqual("http_500", error)

        os.unlink(sidecar.name)


# ------------------------------------------------------------------ #
#  Telemetry: _read_wire_jsonl_usage                                  #
# ------------------------------------------------------------------ #

class KimiCodeReadWireUsageTests(unittest.TestCase):

    def test_reads_usage_from_wire_jsonl(self):
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="wire_test_")
        kimi_home = os.path.join(tmpdir, "kimi_home")
        session_dir = os.path.join(kimi_home, "sessions", "wd_x", "s_abc")
        wire_dir = os.path.join(session_dir, "agents", "main")
        os.makedirs(wire_dir)

        # Write session_index.jsonl
        index_path = os.path.join(kimi_home, "session_index.jsonl")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "sessionId": "s_abc",
                "sessionDir": session_dir,
                "workDir": "/tmp",
            }) + "\n")

        # Write wire.jsonl with two usage records
        wire_path = os.path.join(wire_dir, "wire.jsonl")
        with open(wire_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "usage.record",
                "usage": {"inputOther": 100, "output": 50,
                          "inputCacheRead": 200, "inputCacheCreation": 10},
            }) + "\n")
            f.write(json.dumps({
                "type": "usage.record",
                "usage": {"inputOther": 80, "output": 30,
                          "inputCacheRead": 0, "inputCacheCreation": 0},
            }) + "\n")
            f.write(json.dumps({"type": "other.event", "data": "skip"}) + "\n")

        prompt, completion = KimiCodePlugin._read_wire_jsonl_usage(
            "s_abc", kimi_home,
        )
        # prompt = (100+200) + (80+0) = 380
        # completion = 50 + 30 = 80
        self.assertEqual(380, prompt)
        self.assertEqual(80, completion)

    def test_missing_session_index(self):
        prompt, completion = KimiCodePlugin._read_wire_jsonl_usage(
            "s_abc", "/nonexistent/home",
        )
        self.assertEqual(0, prompt)
        self.assertEqual(0, completion)

    def test_none_args_return_zero(self):
        prompt, completion = KimiCodePlugin._read_wire_jsonl_usage(None, "/home")
        self.assertEqual(0, prompt)
        prompt, completion = KimiCodePlugin._read_wire_jsonl_usage("s", None)
        self.assertEqual(0, prompt)

    def test_session_id_not_in_index(self):
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="wire_test_")
        kimi_home = os.path.join(tmpdir, "kimi_home")
        os.makedirs(kimi_home)
        with open(os.path.join(kimi_home, "session_index.jsonl"), "w",
                  encoding="utf-8") as f:
            f.write(json.dumps({
                "sessionId": "other_session",
                "sessionDir": "/tmp/other",
            }) + "\n")

        prompt, completion = KimiCodePlugin._read_wire_jsonl_usage(
            "s_abc", kimi_home,
        )
        self.assertEqual(0, prompt)
        self.assertEqual(0, completion)


# ------------------------------------------------------------------ #
#  Telemetry: send_message integration                                #
# ------------------------------------------------------------------ #

class KimiCodeSendMessageTelemetryTests(unittest.TestCase):

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_sidecar_env_set_when_ov_enabled(self, mock_write):
        mock_write.return_value = "/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            kimi_ov_home="/ov/home",
            kimi_config_home="/user/config",
        ), runner=runner)
        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertIn("OV_SIDECAR_PATH", env)
        self.assertIn("recall_", env["OV_SIDECAR_PATH"])
        self.assertTrue(
            env["OV_SIDECAR_PATH"].endswith(".json"),
            f"Expected sidecar path to end with .json, got: {env['OV_SIDECAR_PATH']}",
        )

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_ov_account_refreshed_after_init(self, mock_write):
        """Account set on memory_client after plugin init is reflected in env."""
        mock_write.return_value = "/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(_make_config(
            kimi_ov_home="/ov/home",
            kimi_config_home="/user/config",
        ), runner=runner)

        # Simulate --reuse-memory-from setting the account post-init
        plugin.memory_client.account = "eval-locomo-resumed-tenant"
        plugin.memory_client.user_id = "default"

        plugin.send_message("s1", "hi")
        env = runner.calls[0]["env"]
        self.assertEqual("eval-locomo-resumed-tenant", env.get("OPENVIKING_ACCOUNT"))
        self.assertEqual("default", env.get("OPENVIKING_USER"))

    def test_sidecar_env_not_set_when_ov_disabled(self):
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json({"role": "assistant", "content": "ok"}),
            stderr="", returncode=0, elapsed_s=0.01,
        )])
        plugin = _make_plugin(runner=runner)
        plugin.send_message("s1", "hi")

        env = runner.calls[0]["env"]
        self.assertIsNone(env)

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_send_message_returns_all_telemetry_fields(self, mock_write):
        mock_write.return_value = "/ov/home"
        runner = _FakeRunner([CLIRunResult(
            stdout=_stream_json(
                {"role": "assistant", "content": "answer"},
                {"role": "meta", "type": "session.resume_hint",
                 "session_id": "s_t", "command": "", "content": ""},
            ),
            stderr="", returncode=0, elapsed_s=0.5,
        )])
        plugin = _make_plugin(_make_config(
            kimi_ov_home="/ov/home",
            kimi_config_home="/user/config",
        ), runner=runner)
        resp = plugin.send_message("s1", "hi")

        self.assertEqual("answer", resp.text)
        self.assertEqual("s_t", resp.extra["kimi_session_id"])
        self.assertIn("tool_call_count", resp.extra)
        self.assertIn("iterations", resp.extra)
        self.assertIn("retrieval_latency_s", resp.extra)
        self.assertIn("retrieval_error", resp.extra)
        self.assertIn("trace", resp.extra)
        self.assertIn("tool_audit", resp.extra["trace"])
        self.assertEqual(0, resp.extra["tool_call_count"])
        self.assertEqual(1, resp.extra["iterations"])

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_sidecar_cleaned_up_after_success(self, mock_write):
        import tempfile, shutil
        ov_dir = tempfile.mkdtemp()
        try:
            mock_write.return_value = ov_dir
            runner = _FakeRunner([CLIRunResult(
                stdout=_stream_json({"role": "assistant", "content": "ok"}),
                stderr="", returncode=0, elapsed_s=0.01,
            )])
            plugin = _make_plugin(_make_config(
                kimi_ov_home=ov_dir,
                kimi_config_home="/user/config",
            ), runner=runner)

            test_hex = "abc123def456"
            sidecar_path = os.path.join(ov_dir, f"recall_{test_hex}.json")
            with open(sidecar_path, "w", encoding="utf-8") as f:
                json.dump({"query": "hi", "items": [], "rendered": "",
                           "latency_ms": 0, "error": ""}, f)

            import plugins.kimi_code.plugin as mod
            with patch.object(mod.uuid, "uuid4") as mock_uuid:
                mock_uuid.return_value.hex = test_hex
                plugin.send_message("s1", "hi")

            self.assertFalse(os.path.exists(sidecar_path))
        finally:
            shutil.rmtree(ov_dir, ignore_errors=True)

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_sidecar_cleaned_up_on_timeout(self, mock_write):
        import tempfile, shutil
        ov_dir = tempfile.mkdtemp()
        try:
            mock_write.return_value = ov_dir
            runner = _FakeRunner([CLIRunResult(
                stdout="", stderr="timed out", returncode=-1,
                elapsed_s=30, timed_out=True,
            )])
            plugin = _make_plugin(_make_config(
                kimi_ov_home=ov_dir,
                kimi_config_home="/user/config",
            ), runner=runner)

            test_hex = "abc123def456"
            sidecar_path = os.path.join(ov_dir, f"recall_{test_hex}.json")
            with open(sidecar_path, "w", encoding="utf-8") as f:
                json.dump({"query": "hi", "items": [], "rendered": "",
                           "latency_ms": 0, "error": ""}, f)

            import plugins.kimi_code.plugin as mod
            with patch.object(mod.uuid, "uuid4") as mock_uuid:
                mock_uuid.return_value.hex = test_hex
                plugin.send_message("s1", "hi")

            self.assertFalse(os.path.exists(sidecar_path))
        finally:
            shutil.rmtree(ov_dir, ignore_errors=True)

    @patch("plugins.kimi_code.plugin.write_kimi_ov_files")
    def test_memory_items_from_sidecar(self, mock_write):
        """When OV hook writes a sidecar, memory_items appear in the response."""
        import tempfile, shutil
        ov_dir = tempfile.mkdtemp()
        try:
            mock_write.return_value = ov_dir
            runner = _FakeRunner([CLIRunResult(
                stdout=_stream_json({"role": "assistant", "content": "ok"}),
                stderr="", returncode=0, elapsed_s=0.01,
            )])
            plugin = _make_plugin(_make_config(
                kimi_ov_home=ov_dir,
                kimi_config_home="/user/config",
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

            import plugins.kimi_code.plugin as mod
            with patch.object(mod.uuid, "uuid4") as mock_uuid:
                mock_uuid.return_value.hex = test_hex
                resp = plugin.send_message("s1", "hi")

            self.assertEqual(1, len(resp.memory_items))
            self.assertEqual("mem://1", resp.memory_items[0]["uri"])
            self.assertAlmostEqual(0.015, resp.extra["retrieval_latency_s"], places=3)
            self.assertEqual("", resp.extra["retrieval_error"])
        finally:
            shutil.rmtree(ov_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
