"""Unit tests for EchoAgentLivePlugin.

Covers every functional point in plugins/echoagent_live/docs/design.md.
EchoAgentClient is shared with echo_agent and tested separately; here we
mock it. All network calls are mocked -- no real HTTP, no real services.

Run: python -m pytest tests/test_plugins_echoagent_live.py -v
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from backends.memory_types import CommitResult
from plugins.base import AgentResponse
from plugins.echoagent_live.plugin import EchoAgentLivePlugin


# ------------------------------------------------------------------ #
#  Test helpers                                                       #
# ------------------------------------------------------------------ #

# Env vars read by add_llm_args, add_memory_backend_args, and
# EchoAgentLivePlugin.add_arguments -- cleared for deterministic defaults.
_ENV_KEYS = (
    "ECHOAGENT_URL", "ECHOAGENT_API_PREFIX", "ECHOAGENT_TEST_USERNAME",
    "ECHOAGENT_TEST_PASSWORD", "GLOBAL_MEMORY_ENGINE_ENDPOINT",
    "ECHOMEM_BASE_URL", "ECHOMEM_AUTH_KEY", "ECHOMEM_ACCOUNT",
    "ECHOMEM_USER_ID", "ECHOMEM_AGENT_ID", "ECHOMEM_WORKSPACE",
    "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY",
)


@contextlib.contextmanager
def _clean_env(**overrides):
    """Clear plugin env vars for deterministic defaults, restore on exit."""
    with patch.dict(os.environ, {}, clear=False):
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(overrides)
        yield


class _FakeHeaders:
    """Minimal stand-in for http.client.HTTPMessage."""

    def __init__(self, cookies=None):
        self._cookies = list(cookies) if cookies else []

    def get_all(self, name):
        if name == "Set-Cookie" and self._cookies:
            return list(self._cookies)
        return None


class _FakeResponse:
    """Context-manager mock for the object returned by urlopen."""

    def __init__(self, body=b"", cookies=None):
        self._body = body.encode() if isinstance(body, str) else body
        self._pos = 0
        self.headers = _FakeHeaders(cookies)

    def read(self, n=-1):
        if n is None or n < 0:
            data = self._body[self._pos:]
            self._pos = len(self._body)
        else:
            data = self._body[self._pos:self._pos + n]
            self._pos += len(data)
        return data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _sse(*events):
    """Build SSE bytes from (event_type, dict) tuples."""
    parts = [f"event: {t}\ndata: {json.dumps(d)}\n\n" for t, d in events]
    return "".join(parts).encode("utf-8")


def _http_error(code):
    return HTTPError("http://test", code, "err", {}, None)


def _make_plugin():
    """Create an EchoAgentLivePlugin with mock deps, bypassing setup()."""
    p = EchoAgentLivePlugin.__new__(EchoAgentLivePlugin)
    p.client = MagicMock()
    p.memory_client = MagicMock()
    p._memory_engine_endpoint = "http://8.134.127.8:31030"
    p._commit_timeout_s = 0.0
    p._commit_poll_interval_s = 2.0
    p._agent_id = "echoagent"
    p._auth_key = "test-key"
    p._memory_backend = "echomem"
    return p


# ------------------------------------------------------------------ #
#  EchoAgentLivePlugin tests                                          #
# ------------------------------------------------------------------ #

class EchoAgentLivePluginTests(unittest.TestCase):
    """Tests for plugins.echoagent_live.plugin.EchoAgentLivePlugin."""

    # -- add_arguments --------------------------------------------------

    def test_add_arguments_defaults_to_external(self):
        with _clean_env():
            parser = argparse.ArgumentParser()
            EchoAgentLivePlugin.add_arguments(parser)
            args = parser.parse_args([])
        self.assertEqual("https://echo-agent.online", args.echoagent_url)
        self.assertEqual("/api", args.echoagent_api_prefix)
        self.assertEqual("test_user", args.username)
        self.assertEqual("", args.password)
        self.assertEqual("http://8.134.127.8:31030", args.memory_engine_endpoint)

    def test_add_arguments_adds_memory_backend_choice(self):
        with _clean_env():
            parser = argparse.ArgumentParser()
            EchoAgentLivePlugin.add_arguments(parser)
            args = parser.parse_args([])
        self.assertEqual("echomem", args.memory_backend)

    def test_add_arguments_adds_llm_args(self):
        with _clean_env():
            parser = argparse.ArgumentParser()
            EchoAgentLivePlugin.add_arguments(parser)
            args = parser.parse_args(["--llm-model", "gpt-4"])
        self.assertEqual("gpt-4", args.llm_model)

    def test_add_arguments_env_var_defaults(self):
        cases = [
            ("ECHOAGENT_URL", "echoagent_url", "http://10.0.0.1:31020"),
            ("ECHOAGENT_API_PREFIX", "echoagent_api_prefix", "/v1"),
            ("ECHOAGENT_TEST_USERNAME", "username", "env-user"),
            ("ECHOAGENT_TEST_PASSWORD", "password", "env-pass"),
            ("GLOBAL_MEMORY_ENGINE_ENDPOINT",
             "memory_engine_endpoint", "http://10.0.0.1:31030"),
        ]
        for env_key, dest, value in cases:
            with self.subTest(env_key=env_key):
                with _clean_env(**{env_key: value}):
                    parser = argparse.ArgumentParser()
                    EchoAgentLivePlugin.add_arguments(parser)
                    args = parser.parse_args([])
                self.assertEqual(value, getattr(args, dest))

    def test_add_arguments_cli_overrides_env(self):
        with _clean_env(ECHOAGENT_URL="http://env:31020"):
            parser = argparse.ArgumentParser()
            EchoAgentLivePlugin.add_arguments(parser)
            args = parser.parse_args(["--echoagent-url", "http://cli:31020"])
        self.assertEqual("http://cli:31020", args.echoagent_url)

    # -- setup ----------------------------------------------------------

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_creates_client_and_logs_in(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentLivePlugin()
        plugin.setup({
            "echoagent_url": "http://srv:31020",
            "username": "u",
            "password": "p",
        })
        mock_agent_cls.assert_called_once_with(
            "http://srv:31020", "u", "p", api_prefix="/api",
        )
        mock_client.login.assert_called_once()

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_passes_custom_api_prefix(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentLivePlugin()
        plugin.setup({
            "echoagent_url": "http://localhost:31020",
            "echoagent_api_prefix": "/v1",
        })
        mock_agent_cls.assert_called_once_with(
            "http://localhost:31020", "test_user", "", api_prefix="/v1",
        )

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_agent_id_defaults_to_echoagent(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentLivePlugin()
        for agent_id in ("", "default"):
            with self.subTest(agent_id=agent_id):
                cfg = {"agent_id": agent_id}
                plugin.setup(cfg)
                self.assertEqual("echoagent", plugin._agent_id)
                self.assertEqual("echoagent", cfg["agent_id"])

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_agent_id_uses_config_value(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentLivePlugin()
        cfg = {"agent_id": "my-agent"}
        plugin.setup(cfg)
        self.assertEqual("my-agent", plugin._agent_id)

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_auth_key_from_config(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock()
        plugin = EchoAgentLivePlugin()
        cfg = {"echomem_auth_key": "config-key"}
        plugin.setup(cfg)
        self.assertEqual("config-key", plugin._auth_key)
        mock_client.get_memory_auth_key.assert_not_called()

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_auth_key_via_get_memory_auth_key(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="resolved-key")
        plugin = EchoAgentLivePlugin()
        cfg = {}
        plugin.setup(cfg)
        self.assertEqual("resolved-key", plugin._auth_key)
        self.assertEqual("resolved-key", cfg["echomem_auth_key"])
        mock_client.get_memory_auth_key.assert_called_once()

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_auth_key_empty_on_failure(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(
            side_effect=RuntimeError("nope"),
        )
        plugin = EchoAgentLivePlugin()
        plugin.setup({})
        self.assertEqual("", plugin._auth_key)

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_creates_echomem_client(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentLivePlugin()
        plugin.setup({
            "memory_backend": "echomem",
            "echomem_url": "http://mem:8010",
        })
        mock_echomem_cls.assert_called_once()
        mock_ov_cls.assert_not_called()
        self.assertIs(mock_echomem_cls.return_value, plugin.memory_client)

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_creates_openviking_client(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentLivePlugin()
        plugin.setup({
            "memory_backend": "openviking",
            "echomem_url": "http://ov:19080",
        })
        mock_ov_cls.assert_called_once()
        mock_echomem_cls.assert_not_called()
        self.assertIs(mock_ov_cls.return_value, plugin.memory_client)

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_never_provisions_isolated_identity(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        """EchoAgent live plugin must not create an isolated tenant.

        EchoAgent's backend resolves auth_key via the echoagent plugin
        (31030) using the logged-in user's UUID as userId.  If injection
        used a different tenant, retrieval would find nothing.  Therefore
        the injection identity must always match the retrieval identity.
        """
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        mock_mem = mock_echomem_cls.return_value
        plugin = EchoAgentLivePlugin()
        plugin.setup({"benchmark_name": "locomo", "run_id": "run-1"})
        mock_mem.provision_isolated_identity.assert_not_called()

    @patch("plugins.echoagent_live.plugin.OpenVikingClient")
    @patch("plugins.echoagent_live.plugin.EchoMemClient")
    @patch("plugins.echoagent_live.plugin.EchoAgentClient")
    def test_setup_no_typing_state_initialized(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentLivePlugin()
        plugin.setup({})
        self.assertFalse(hasattr(plugin, "_pending_turn_id"))
        self.assertFalse(hasattr(plugin, "_typing_committed"))
        self.assertFalse(hasattr(plugin, "_typing_memory_items"))

    # -- supports_typing_simulation -------------------------------------

    def test_supports_typing_simulation_returns_false(self):
        plugin = _make_plugin()
        self.assertFalse(plugin.supports_typing_simulation)

    # -- create_session (plugin) ----------------------------------------

    def test_create_session_delegates_to_client(self):
        plugin = _make_plugin()
        plugin.client.create_session = MagicMock(return_value="sess-1")
        sid = plugin.create_session("title")
        plugin.client.create_session.assert_called_once_with(
            "title", "http://8.134.127.8:31030",
        )
        self.assertEqual("sess-1", sid)

    # -- send_message ---------------------------------------------------

    def test_send_message_success(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 5, "status": "generating"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "hello",
            "ttft_ms": 100.5,
            "done_event": {"promptTokens": 10, "cachedTokens": 3},
        })
        resp = plugin.send_message("s1", "hi", "/")
        self.assertEqual("hello", resp.text)
        self.assertEqual(100.5, resp.ttft_ms)
        self.assertEqual(10, resp.prompt_tokens)
        self.assertEqual(3, resp.cached_tokens)
        self.assertEqual(0, resp.completion_tokens)
        self.assertFalse(resp.prefetch_committed)
        self.assertEqual([], resp.memory_items)
        self.assertIsNone(resp.error)
        self.assertEqual("echoagent_live", resp.extra.get("qa_profile"))
        plugin.client.stream_reply.assert_called_once_with("s1", "/", 5)

    def test_send_message_empty_session_creates_echoagent_session(self):
        """When session_id is empty (benchmark mode), create a new EA session."""
        plugin = _make_plugin()
        plugin.client.create_session = MagicMock(return_value="ea-sess-1")
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 5, "status": "generating"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "hello", "ttft_ms": 10.0, "done_event": {},
        })
        resp = plugin.send_message("", "hi", "/", extra={"question_id": "q1"})
        plugin.client.create_session.assert_called_once_with(
            "qa-q1", "http://8.134.127.8:31030",
        )
        plugin.client.send_message.assert_called_once_with("ea-sess-1", "/", "hi")
        plugin.client.stream_reply.assert_called_once_with("ea-sess-1", "/", 5)
        self.assertEqual("hello", resp.text)

    def test_send_message_empty_session_no_extra_uses_generic_title(self):
        plugin = _make_plugin()
        plugin.client.create_session = MagicMock(return_value="ea-sess-2")
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 1, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "", "ttft_ms": None, "done_event": {},
        })
        plugin.send_message("", "hi", "/")
        plugin.client.create_session.assert_called_once_with(
            "qa", "http://8.134.127.8:31030",
        )

    def test_send_message_does_not_pass_pending_turn_id(self):
        """echoagent_live has no typing state -- send_message calls
        client.send_message with exactly 3 args (no 4th pending_turn_id)."""
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 1, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "", "ttft_ms": None, "done_event": {},
        })
        plugin.send_message("s1", "hi", "/")
        plugin.client.send_message.assert_called_once_with("s1", "/", "hi")

    def test_send_message_seq_from_generating_message(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [
                {"seq": 1, "status": "completed"},
                {"seq": 7, "status": "generating"},
            ]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "", "ttft_ms": None, "done_event": {},
        })
        plugin.send_message("s1", "hi", "/")
        plugin.client.stream_reply.assert_called_once_with("s1", "/", 7)

    def test_send_message_seq_from_completed_message(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 7, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "", "ttft_ms": None, "done_event": {},
        })
        plugin.send_message("s1", "hi", "/")
        plugin.client.stream_reply.assert_called_once_with("s1", "/", 7)

    def test_send_message_seq_fallback_last_message(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 9, "status": "other"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "", "ttft_ms": None, "done_event": {},
        })
        plugin.send_message("s1", "hi", "/")
        plugin.client.stream_reply.assert_called_once_with("s1", "/", 9)

    def test_send_message_seq_fallback_latest_context_seq(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"latestContextSeq": 12},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "", "ttft_ms": None, "done_event": {},
        })
        plugin.send_message("s1", "hi", "/")
        plugin.client.stream_reply.assert_called_once_with("s1", "/", 12)

    def test_send_message_error_in_msg_data(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"error": "BAD", "message": "details"},
        })
        plugin.client.stream_reply = MagicMock()
        resp = plugin.send_message("s1", "hi", "/")
        self.assertIn("BAD", resp.error)
        self.assertIn("details", resp.error)
        self.assertEqual("", resp.text)
        self.assertEqual("echoagent_live", resp.extra.get("qa_profile"))
        plugin.client.stream_reply.assert_not_called()

    def test_send_message_exception_returns_error(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(
            side_effect=ConnectionError("boom"),
        )
        plugin.client.stream_reply = MagicMock()
        resp = plugin.send_message("s1", "hi", "/")
        self.assertEqual("boom", resp.error)
        self.assertEqual("echoagent_live", resp.extra.get("qa_profile"))
        plugin.client.stream_reply.assert_not_called()

    def test_send_message_snake_case_tokens(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 1, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "x",
            "ttft_ms": 50.0,
            "done_event": {"prompt_tokens": 7, "cached_tokens": 2},
        })
        resp = plugin.send_message("s1", "hi", "/")
        self.assertEqual(7, resp.prompt_tokens)
        self.assertEqual(2, resp.cached_tokens)

    def test_send_message_error_from_stream_reply(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 1, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "partial",
            "ttft_ms": 10.0,
            "error": "stream broke",
            "done_event": {},
        })
        resp = plugin.send_message("s1", "hi", "/")
        self.assertEqual("partial", resp.text)
        self.assertEqual("stream broke", resp.error)

    def test_send_message_metrics_and_memory_items(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 5, "status": "generating"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "hello",
            "ttft_ms": 100.5,
            "done_event": {
                "stopReason": "stop",
                "metrics": {
                    "ttft_ms": 80.0,
                    "prompt_tokens": 20,
                    "completion_tokens": 15,
                    "cached_tokens": 6,
                    "elapsed_ms": 1200,
                    "retrieval_latency_ms": 300,
                    "llm_latency_ms": 700,
                    "tool_call_count": 2,
                    "turn_iterations": 3,
                    "model_name": "doubao-seed-2.0-pro",
                    "finish_reason": "stop",
                },
                "memoryItems": [{"text": "m1"}, {"text": "m2"}],
                "toolAudit": [{"name": "ssh_execute", "callId": "c1", "arguments": "{}"}],
            },
        })
        resp = plugin.send_message("s1", "hi", "/")
        self.assertEqual("hello", resp.text)
        self.assertEqual(80.0, resp.ttft_ms)
        self.assertEqual(20, resp.prompt_tokens)
        self.assertEqual(15, resp.completion_tokens)
        self.assertEqual(6, resp.cached_tokens)
        self.assertFalse(resp.prefetch_committed)
        self.assertEqual([{"text": "m1"}, {"text": "m2"}], resp.memory_items)
        self.assertIsNone(resp.error)
        self.assertEqual("echoagent_live", resp.extra.get("qa_profile"))
        self.assertEqual(1.2, resp.extra["elapsed_s"])
        self.assertEqual(0.3, resp.extra["retrieval_latency_s"])
        self.assertEqual(0.7, resp.extra["llm_latency_s"])
        self.assertEqual(2, resp.extra["tool_call_count"])
        self.assertEqual(3, resp.extra["iterations"])
        self.assertEqual(
            "doubao-seed-2.0-pro", resp.extra["trace"]["model"],
        )
        self.assertEqual("stop", resp.extra["trace"]["finish_reason"])
        self.assertEqual(
            [{"name": "ssh_execute", "callId": "c1", "arguments": "{}"}],
            resp.extra["trace"]["tool_audit"],
        )
        self.assertEqual(
            {
                "ttft_ms": 80.0,
                "prompt_tokens": 20,
                "completion_tokens": 15,
                "cached_tokens": 6,
                "elapsed_ms": 1200,
                "retrieval_latency_ms": 300,
                "llm_latency_ms": 700,
                "tool_call_count": 2,
                "turn_iterations": 3,
                "model_name": "doubao-seed-2.0-pro",
                "finish_reason": "stop",
            },
            resp.extra["trace"]["metrics"],
        )

    def test_send_message_no_metrics_falls_back(self):
        """done_event without metrics: fall back to legacy camelCase/0 values."""
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 1, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "x",
            "ttft_ms": 50.0,
            "done_event": {"promptTokens": 7, "cachedTokens": 2},
        })
        resp = plugin.send_message("s1", "hi", "/")
        self.assertEqual("x", resp.text)
        self.assertEqual(50.0, resp.ttft_ms)
        self.assertEqual(7, resp.prompt_tokens)
        self.assertEqual(2, resp.cached_tokens)
        self.assertEqual(0, resp.completion_tokens)
        self.assertFalse(resp.prefetch_committed)
        self.assertEqual([], resp.memory_items)
        self.assertEqual(0.0, resp.extra["elapsed_s"])
        self.assertEqual(0.0, resp.extra["retrieval_latency_s"])
        self.assertEqual(0.0, resp.extra["llm_latency_s"])
        self.assertEqual(0, resp.extra["tool_call_count"])
        self.assertEqual(1, resp.extra["iterations"])
        self.assertEqual({}, resp.extra["trace"]["metrics"])
        self.assertIsNone(resp.extra["trace"]["tool_audit"])

    # -- inject_memories -----------------------------------------------

    def test_inject_memories_opens_session_when_no_id(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session = MagicMock(return_value="sess-new")
        plugin.memory_client.add_message = MagicMock()
        plugin.memory_client.commit_session = MagicMock(return_value="arch-1")
        plugin.memory_client.poll_commit = MagicMock(return_value=CommitResult(
            "sess-new", "arch-1", "completed", 0.0, 1,
        ))
        sid = plugin.inject_memories([{"text": "m1", "time": "2024-01-01"}])
        self.assertEqual("sess-new", sid)
        plugin.memory_client.open_session.assert_called_once()
        plugin.memory_client.add_message.assert_called_once_with(
            "sess-new", "user", "m1", created_at="2024-01-01",
        )

    def test_inject_memories_uses_provided_session_id(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session = MagicMock()
        plugin.memory_client.add_message = MagicMock()
        plugin.memory_client.commit_session = MagicMock(return_value="arch-1")
        plugin.memory_client.poll_commit = MagicMock(return_value=CommitResult(
            "sess-exist", "arch-1", "completed", 0.0, 1,
        ))
        sid = plugin.inject_memories(
            [{"text": "m1"}], session_id="sess-exist",
        )
        self.assertEqual("sess-exist", sid)
        plugin.memory_client.open_session.assert_not_called()

    def test_inject_memories_adds_each_memory(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session = MagicMock()
        plugin.memory_client.add_message = MagicMock()
        plugin.memory_client.commit_session = MagicMock(return_value="a")
        plugin.memory_client.poll_commit = MagicMock(return_value=CommitResult(
            "s", "a", "completed", 0.0, 1,
        ))
        plugin.inject_memories([
            {"text": "m1", "time": "2024-01-01"},
            {"text": "m2", "time": "2024-01-02"},
            {"text": "m3"},
        ], session_id="s")
        self.assertEqual(3, plugin.memory_client.add_message.call_count)
        plugin.memory_client.add_message.assert_any_call(
            "s", "user", "m1", created_at="2024-01-01",
        )
        plugin.memory_client.add_message.assert_any_call(
            "s", "user", "m2", created_at="2024-01-02",
        )
        plugin.memory_client.add_message.assert_any_call(
            "s", "user", "m3", created_at="",
        )

    def test_inject_memories_skips_empty_text(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session = MagicMock()
        plugin.memory_client.add_message = MagicMock()
        plugin.memory_client.commit_session = MagicMock(return_value="a")
        plugin.memory_client.poll_commit = MagicMock(return_value=CommitResult(
            "s", "a", "completed", 0.0, 1,
        ))
        plugin.inject_memories([
            {"text": "", "time": "2024-01-01"},
            {"text": "ok"},
        ], session_id="s")
        self.assertEqual(1, plugin.memory_client.add_message.call_count)

    def test_inject_memories_commits_and_polls(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session = MagicMock()
        plugin.memory_client.add_message = MagicMock()
        plugin.memory_client.commit_session = MagicMock(return_value="arch-1")
        plugin.memory_client.poll_commit = MagicMock(return_value=CommitResult(
            "s", "arch-1", "completed", 5.0, 3,
        ))
        plugin._commit_timeout_s = 30.0
        plugin._commit_poll_interval_s = 1.0
        plugin.inject_memories([{"text": "m1"}], session_id="s")
        plugin.memory_client.commit_session.assert_called_once_with("s")
        plugin.memory_client.poll_commit.assert_called_once_with(
            "s", "arch-1", timeout_s=30.0, poll_interval_s=1.0,
        )

    def test_inject_memories_raises_on_commit_failure(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session = MagicMock()
        plugin.memory_client.add_message = MagicMock()
        plugin.memory_client.commit_session = MagicMock(return_value="arch-1")
        plugin.memory_client.poll_commit = MagicMock(return_value=CommitResult(
            "s", "arch-1", "failed", 1.0, 1, error="extraction error",
        ))
        with self.assertRaises(RuntimeError) as ctx:
            plugin.inject_memories([{"text": "m1"}], session_id="s")
        self.assertIn("failed", str(ctx.exception))
        self.assertIn("extraction error", str(ctx.exception))

    # -- getlog ---------------------------------------------------------

    def test_getlog_echomem_fetches_tenant_logs(self):
        plugin = _make_plugin()
        plugin._memory_backend = "echomem"
        plugin.memory_client.account = "tenant-x"
        plugin.memory_client.user_id = "user-x"
        plugin.memory_client.fetch_logs = MagicMock(
            return_value={"items": [{"ts": "a"}], "page": {}},
        )
        result = plugin.getlog()
        plugin.memory_client.fetch_logs.assert_called_once_with(
            tenant_id="tenant-x",
            user_id="user-x",
        )
        data = json.loads(result)
        self.assertEqual([{"ts": "a"}], data["items"])

    def test_getlog_echomem_returns_error_on_failure(self):
        plugin = _make_plugin()
        plugin._memory_backend = "echomem"
        plugin.memory_client.account = "tenant-x"
        plugin.memory_client.user_id = "user-x"
        plugin.memory_client.fetch_logs = MagicMock(side_effect=RuntimeError("boom"))
        result = plugin.getlog()
        data = json.loads(result)
        self.assertIn("error", data)

    def test_getlog_openviking_fetches_logs(self):
        plugin = _make_plugin()
        plugin._memory_backend = "openviking"
        plugin.memory_client.fetch_console_logs = MagicMock(
            return_value={"events": []},
        )
        result = plugin.getlog()
        data = json.loads(result)
        self.assertIn("events", data)

    # -- teardown -------------------------------------------------------

    def test_teardown_is_noop(self):
        plugin = _make_plugin()
        plugin.teardown()


if __name__ == "__main__":
    unittest.main()
