"""Unit tests for EchoAgentPlugin and EchoAgentClient.

Covers every functional point in plugins/echo_agent/docs/design.md.
All network calls are mocked -- no real HTTP, no real services.

Run: python -m unittest tests.test_plugins_echo_agent -v
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
from plugins.base import AgentResponse, TypingResult
from plugins.echo_agent.client import EchoAgentClient
from plugins.echo_agent.plugin import EchoAgentPlugin


# ------------------------------------------------------------------ #
#  Test helpers                                                       #
# ------------------------------------------------------------------ #

# Env vars read by add_llm_args, add_memory_backend_args, and
# EchoAgentPlugin.add_arguments -- cleared for deterministic defaults.
_ENV_KEYS = (
    "ECHOAGENT_URL", "ECHOAGENT_TEST_USERNAME",
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
    """Context-manager mock for the object returned by urlopen.

    read(n) returns up to *n* bytes from body (supports streaming).
    """

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


class _FakeStreamResponse:
    """Returns predetermined chunks from read() for streaming tests."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.headers = _FakeHeaders()

    def read(self, n=-1):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

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
    """Create an EchoAgentPlugin with mock deps, bypassing setup()."""
    p = EchoAgentPlugin.__new__(EchoAgentPlugin)
    p.client = MagicMock()
    p.memory_client = MagicMock()
    p._memory_engine_endpoint = "http://127.0.0.1:31030"
    p._commit_timeout_s = 0.0
    p._commit_poll_interval_s = 2.0
    p._agent_id = "echoagent"
    p._auth_key = "test-key"
    p._memory_backend = "echomem"
    p._pending_turn_id = ""
    p._typing_committed = False
    p._typing_memory_items = []
    return p


# ------------------------------------------------------------------ #
#  EchoAgentClient tests                                              #
# ------------------------------------------------------------------ #

class EchoAgentClientTests(unittest.TestCase):
    """Tests for plugins.echo_agent.client.EchoAgentClient."""

    # -- __init__ -------------------------------------------------------

    def test_init_strips_trailing_slash(self):
        for url in ("http://srv/", "http://srv///"):
            with self.subTest(url=url):
                c = EchoAgentClient(url, "u", "p")
                self.assertEqual("http://srv", c.base_url)

    def test_init_stores_credentials_and_defaults(self):
        c = EchoAgentClient("http://srv", "user1", "pass1")
        self.assertEqual("http://srv", c.base_url)
        self.assertEqual("/v1", c.api_prefix)
        self.assertEqual("user1", c.username)
        self.assertEqual("pass1", c.password)
        self.assertEqual("", c.token)
        self.assertEqual("", c.user_uuid)
        self.assertEqual({}, c._context_seq)

    # -- _headers -------------------------------------------------------

    def test_headers_no_token_no_json(self):
        c = EchoAgentClient("http://srv", "u", "p")
        self.assertEqual({}, c._headers(json_content=False))

    def test_headers_no_token_with_json(self):
        c = EchoAgentClient("http://srv", "u", "p")
        self.assertEqual(
            {"Content-Type": "application/json"}, c._headers(),
        )

    def test_headers_with_token(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c.token = "tok-123"
        self.assertEqual(
            {"Authorization": "Bearer tok-123",
             "Content-Type": "application/json"},
            c._headers(),
        )

    def test_headers_with_token_no_json(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c.token = "tok-123"
        self.assertEqual(
            {"Authorization": "Bearer tok-123"},
            c._headers(json_content=False),
        )

    # -- _request -------------------------------------------------------

    def test_request_returns_parsed_json(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c.token = "tok"
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(json.dumps({"ok": True}).encode())
            result = c._request("POST", "/v1/test", {"key": "val"})
        self.assertEqual({"ok": True}, result)
        req = m.call_args.args[0]
        self.assertEqual("http://srv/v1/test", req.full_url)
        self.assertEqual("POST", req.method)
        self.assertEqual(b'{"key": "val"}', req.data)

    def test_request_no_body_sends_no_data(self):
        c = EchoAgentClient("http://srv", "u", "p")
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(b"{}")
            c._request("GET", "/v1/test")
        self.assertIsNone(m.call_args.args[0].data)

    def test_request_empty_response_returns_empty_dict(self):
        c = EchoAgentClient("http://srv", "u", "p")
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(b"   ")
            result = c._request("GET", "/v1/test")
        self.assertEqual({}, result)

    # -- login ----------------------------------------------------------

    def test_login_extracts_access_token_from_body(self):
        c = EchoAgentClient("http://srv", "user1", "pass1")
        resp_body = json.dumps({
            "access_token": "jwt-token",
            "user": {"id": "uuid-42"},
        }).encode()
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(resp_body)
            c.login()
        self.assertEqual("jwt-token", c.token)
        self.assertEqual("uuid-42", c.user_uuid)

    def test_login_extracts_token_from_set_cookie(self):
        c = EchoAgentClient("http://srv", "user1", "pass1")
        resp_body = json.dumps({"user": {"id": "uuid-42"}}).encode()
        cookies = ["access_token=cookie-token; Path=/; HttpOnly"]
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(resp_body, cookies=cookies)
            c.login()
        self.assertEqual("cookie-token", c.token)
        self.assertEqual("uuid-42", c.user_uuid)

    def test_login_raises_when_no_token_found(self):
        c = EchoAgentClient("http://srv", "user1", "pass1")
        resp_body = json.dumps({"user": {"id": "uuid"}}).encode()
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(resp_body)
            with self.assertRaises(RuntimeError):
                c.login()

    def test_login_user_uuid_empty_when_missing(self):
        c = EchoAgentClient("http://srv", "u", "p")
        resp_body = json.dumps({"access_token": "tok"}).encode()
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(resp_body)
            c.login()
        self.assertEqual("", c.user_uuid)

    # -- get_memory_auth_key --------------------------------------------

    def test_get_memory_auth_key_success(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c.user_uuid = "uuid-123"
        resp = json.dumps({"result": {"authKey": "ek-789"}}).encode()
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(resp)
            key = c.get_memory_auth_key("http://ep:31030")
        self.assertEqual("ek-789", key)
        sent_body = json.loads(m.call_args[0][0].data)
        self.assertEqual("uuid-123", sent_body["userId"])

    def test_get_memory_auth_key_raises_without_authkey(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c.user_uuid = "uuid-123"
        resp = json.dumps({"result": {}}).encode()
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(resp)
            with self.assertRaises(RuntimeError):
                c.get_memory_auth_key("http://ep:31030")

    # -- create_session -------------------------------------------------

    def test_create_session_extracts_id_from_data(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"data": {"id": "sess-1"}})
        sid = c.create_session("title", "")
        self.assertEqual("sess-1", sid)
        self.assertEqual(1, c._request.call_count)

    def test_create_session_extracts_id_from_top_level(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"id": "sess-2"})
        sid = c.create_session("title", "")
        self.assertEqual("sess-2", sid)

    def test_create_session_enables_memory_engine(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"data": {"id": "sess-3"}})
        c.create_session("title", "http://ep:31030")
        # POST /v1/sessions, POST .../memory-engine/test, PUT .../memory-engine
        self.assertEqual(3, c._request.call_count)
        methods = [call.args[0] for call in c._request.call_args_list]
        self.assertEqual(["POST", "POST", "PUT"], methods)

    def test_create_session_ignores_memory_engine_failure(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(side_effect=[
            {"data": {"id": "sess-4"}},
            RuntimeError("boom"),
        ])
        sid = c.create_session("title", "http://ep:31030")
        self.assertEqual("sess-4", sid)

    def test_create_session_generates_title_when_empty(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"data": {"id": "s"}})
        c.create_session("", "")
        body = c._request.call_args.args[2]
        self.assertIsInstance(body, dict)
        self.assertTrue(body["title"].startswith("test-"))

    def test_create_session_no_endpoint_skips_enable(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"data": {"id": "s"}})
        c.create_session("title", "")
        self.assertEqual(1, c._request.call_count)

    # -- prefetch_tick --------------------------------------------------

    def test_prefetch_tick_success(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"data": {"accepted": True}})
        result = c.prefetch_tick("s1", "/", "turn-1", 1, "hi")
        self.assertEqual({"data": {"accepted": True}}, result)
        body = c._request.call_args.args[2]
        self.assertEqual("turn-1", body["clientTurnId"])
        self.assertEqual(1, body["revision"])
        self.assertEqual("hi", body["draftText"])

    def test_prefetch_tick_uses_correct_path(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"data": {}})
        c.prefetch_tick("s1", "/", "t", 1, "x")
        path = c._request.call_args.args[1]
        self.assertIn("/v1/sessions/s1/context-paths/", path)
        self.assertIn("/prefetch/tick", path)

    def test_prefetch_tick_returns_none_on_404(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(side_effect=_http_error(404))
        self.assertIsNone(c.prefetch_tick("s1", "/", "t", 1, "x"))

    def test_prefetch_tick_returns_none_on_other_exception(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(side_effect=ConnectionError("nope"))
        self.assertIsNone(c.prefetch_tick("s1", "/", "t", 1, "x"))

    def test_prefetch_tick_reraises_non_404_http_error(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(side_effect=_http_error(500))
        with self.assertRaises(HTTPError):
            c.prefetch_tick("s1", "/", "t", 1, "x")

    # -- prefetch_finalize ----------------------------------------------

    def test_prefetch_finalize_success(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"data": {"accepted": True}})
        result = c.prefetch_finalize("s1", "/", "turn-1", "full text")
        self.assertEqual({"data": {"accepted": True}}, result)
        body = c._request.call_args.args[2]
        self.assertEqual("turn-1", body["clientTurnId"])
        self.assertEqual("full text", body["fullContent"])

    def test_prefetch_finalize_returns_none_on_404(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(side_effect=_http_error(404))
        self.assertIsNone(c.prefetch_finalize("s1", "/", "t", "x"))

    def test_prefetch_finalize_returns_none_on_other_exception(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(side_effect=ConnectionError("nope"))
        self.assertIsNone(c.prefetch_finalize("s1", "/", "t", "x"))

    def test_prefetch_finalize_reraises_non_404_http_error(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(side_effect=_http_error(500))
        with self.assertRaises(HTTPError):
            c.prefetch_finalize("s1", "/", "t", "x")

    # -- send_message ---------------------------------------------------

    def test_send_message_success_no_retry(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={
            "data": {"latestContextSeq": 3, "messages": []},
        })
        result = c.send_message("s1", "/", "hello")
        self.assertEqual(1, c._request.call_count)
        self.assertEqual(3, c._context_seq["s1:/"])
        body = c._request.call_args.args[2]
        self.assertEqual("hello", body["content"])
        self.assertEqual(0, body["afterSeq"])

    def test_send_message_includes_prefetch_turn_id(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"data": {"latestContextSeq": 1}})
        c.send_message("s1", "/", "hi", "turn-abc")
        body = c._request.call_args.args[2]
        self.assertEqual("turn-abc", body["prefetchClientTurnId"])

    def test_send_message_no_prefetch_turn_id_omits_field(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"data": {"latestContextSeq": 1}})
        c.send_message("s1", "/", "hi")
        body = c._request.call_args.args[2]
        self.assertNotIn("prefetchClientTurnId", body)

    def test_send_message_retries_on_context_seq_outdated(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(side_effect=[
            {"data": {"error": "CONTEXT_SEQ_OUTDATED", "latestContextSeq": 5}},
            {"data": {"latestContextSeq": 7, "messages": []}},
        ])
        c.send_message("s1", "/", "hello")
        self.assertEqual(2, c._request.call_count)
        retry_body = c._request.call_args_list[1].args[2]
        self.assertEqual(5, retry_body["afterSeq"])
        self.assertEqual(7, c._context_seq["s1:/"])

    def test_send_message_retries_on_seq_outdated(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(side_effect=[
            {"data": {"error": "SEQ_OUTDATED", "latestContextSeq": 2}},
            {"data": {"latestContextSeq": 3}},
        ])
        c.send_message("s1", "/", "hello")
        self.assertEqual(2, c._request.call_count)

    def test_send_message_stops_after_3_attempts(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={
            "data": {"error": "CONTEXT_SEQ_OUTDATED", "latestContextSeq": 9},
        })
        c.send_message("s1", "/", "hello")
        self.assertEqual(3, c._request.call_count)

    def test_send_message_uses_cached_context_seq(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._context_seq["s1:/"] = 4
        c._request = MagicMock(return_value={"data": {"latestContextSeq": 5}})
        c.send_message("s1", "/", "hello")
        body = c._request.call_args.args[2]
        self.assertEqual(4, body["afterSeq"])

    def test_send_message_uses_correct_path(self):
        c = EchoAgentClient("http://srv", "u", "p")
        c._request = MagicMock(return_value={"data": {"latestContextSeq": 1}})
        c.send_message("s1", "/", "hello")
        path = c._request.call_args.args[1]
        self.assertIn("/v1/sessions/s1/context-paths/", path)
        self.assertIn("/messages", path)

    # -- stream_reply ---------------------------------------------------

    def test_stream_reply_parses_create_append_done(self):
        c = EchoAgentClient("http://srv", "u", "p")
        sse = _sse(
            ("create", {"fragment": "Hello"}),
            ("append", {"fragment": " World"}),
            ("done", {"promptTokens": 10, "cachedTokens": 5}),
        )
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(sse)
            result = c.stream_reply("s1", "/", 1)
        self.assertEqual("Hello World", result["reply"])
        self.assertIsNotNone(result["ttft_ms"])
        self.assertEqual(10, result["done_event"]["promptTokens"])
        self.assertEqual(5, result["done_event"]["cachedTokens"])

    def test_stream_reply_accumulates_content_key(self):
        c = EchoAgentClient("http://srv", "u", "p")
        sse = _sse(("create", {"content": "ABC"}), ("done", {}))
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(sse)
            result = c.stream_reply("s1", "/", 1)
        self.assertEqual("ABC", result["reply"])

    def test_stream_reply_fragment_dict_uses_content(self):
        c = EchoAgentClient("http://srv", "u", "p")
        sse = _sse(
            ("append", {"fragment": {"content": "dict-frag"}}),
            ("done", {}),
        )
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(sse)
            result = c.stream_reply("s1", "/", 1)
        self.assertEqual("dict-frag", result["reply"])

    def test_stream_reply_error_event(self):
        c = EchoAgentClient("http://srv", "u", "p")
        sse = _sse(
            ("create", {"fragment": "partial"}),
            ("error", {"message": "boom"}),
        )
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(sse)
            result = c.stream_reply("s1", "/", 1)
        self.assertEqual("partial", result["reply"])
        self.assertIn("boom", result["error"])
        self.assertEqual({}, result["done_event"])

    def test_stream_reply_no_done_returns_accumulated(self):
        c = EchoAgentClient("http://srv", "u", "p")
        sse = _sse(("create", {"fragment": "orphan"}))
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(sse)
            result = c.stream_reply("s1", "/", 1)
        self.assertEqual("orphan", result["reply"])
        self.assertEqual({}, result["done_event"])

    def test_stream_reply_multi_chunk(self):
        c = EchoAgentClient("http://srv", "u", "p")
        sse = _sse(("create", {"fragment": "Hello"}), ("done", {}))
        chunks = [sse[i:i + 7] for i in range(0, len(sse), 7)]
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeStreamResponse(chunks)
            result = c.stream_reply("s1", "/", 1)
        self.assertEqual("Hello", result["reply"])

    def test_stream_reply_ttft_set_on_first_fragment(self):
        c = EchoAgentClient("http://srv", "u", "p")
        sse = _sse(("create", {"fragment": "x"}), ("done", {}))
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(sse)
            result = c.stream_reply("s1", "/", 1)
        self.assertIsNotNone(result["ttft_ms"])
        self.assertGreater(result["ttft_ms"], 0)

    def test_stream_reply_ttft_set_on_done_when_no_prior_fragment(self):
        c = EchoAgentClient("http://srv", "u", "p")
        sse = _sse(("done", {"promptTokens": 1}))
        with patch("plugins.echo_agent.client.urlopen") as m:
            m.return_value = _FakeResponse(sse)
            result = c.stream_reply("s1", "/", 1)
        self.assertIsNotNone(result["ttft_ms"])
        self.assertEqual("", result["reply"])


# ------------------------------------------------------------------ #
#  EchoAgentPlugin tests                                              #
# ------------------------------------------------------------------ #

class EchoAgentPluginTests(unittest.TestCase):
    """Tests for plugins.echo_agent.plugin.EchoAgentPlugin."""

    # -- add_arguments --------------------------------------------------

    def test_add_arguments_adds_echoagent_args(self):
        with _clean_env():
            parser = argparse.ArgumentParser()
            EchoAgentPlugin.add_arguments(parser)
            args = parser.parse_args([])
        self.assertEqual("http://127.0.0.1:31020", args.echoagent_url)
        self.assertEqual("test_user", args.username)
        self.assertEqual("", args.password)
        self.assertEqual("http://127.0.0.1:31030", args.memory_engine_endpoint)

    def test_add_arguments_adds_memory_backend_choice(self):
        with _clean_env():
            parser = argparse.ArgumentParser()
            EchoAgentPlugin.add_arguments(parser)
            args = parser.parse_args([])
        self.assertEqual("echomem", args.memory_backend)

    def test_add_arguments_adds_llm_args(self):
        with _clean_env():
            parser = argparse.ArgumentParser()
            EchoAgentPlugin.add_arguments(parser)
            args = parser.parse_args(["--llm-model", "gpt-4"])
        self.assertEqual("gpt-4", args.llm_model)

    def test_add_arguments_env_var_defaults(self):
        cases = [
            ("ECHOAGENT_URL", "echoagent_url", "http://10.0.0.1:31020"),
            ("ECHOAGENT_TEST_USERNAME", "username", "env-user"),
            ("ECHOAGENT_TEST_PASSWORD", "password", "env-pass"),
            ("GLOBAL_MEMORY_ENGINE_ENDPOINT",
             "memory_engine_endpoint", "http://10.0.0.1:31030"),
        ]
        for env_key, dest, value in cases:
            with self.subTest(env_key=env_key):
                with _clean_env(**{env_key: value}):
                    parser = argparse.ArgumentParser()
                    EchoAgentPlugin.add_arguments(parser)
                    args = parser.parse_args([])
                self.assertEqual(value, getattr(args, dest))

    def test_add_arguments_cli_overrides_env(self):
        with _clean_env(ECHOAGENT_URL="http://env:31020"):
            parser = argparse.ArgumentParser()
            EchoAgentPlugin.add_arguments(parser)
            args = parser.parse_args(["--echoagent-url", "http://cli:31020"])
        self.assertEqual("http://cli:31020", args.echoagent_url)

    # -- setup ----------------------------------------------------------

    @patch("plugins.echo_agent.plugin.OpenVikingClient")
    @patch("plugins.echo_agent.plugin.EchoMemClient")
    @patch("plugins.echo_agent.plugin.EchoAgentClient")
    def test_setup_creates_client_and_logs_in(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentPlugin()
        plugin.setup({
            "echoagent_url": "http://srv:31020",
            "username": "u",
            "password": "p",
        })
        mock_agent_cls.assert_called_once_with("http://srv:31020", "u", "p")
        mock_client.login.assert_called_once()

    @patch("plugins.echo_agent.plugin.OpenVikingClient")
    @patch("plugins.echo_agent.plugin.EchoMemClient")
    @patch("plugins.echo_agent.plugin.EchoAgentClient")
    def test_setup_agent_id_defaults_to_echoagent(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentPlugin()
        for agent_id in ("", "default"):
            with self.subTest(agent_id=agent_id):
                cfg = {"agent_id": agent_id}
                plugin.setup(cfg)
                self.assertEqual("echoagent", plugin._agent_id)
                self.assertEqual("echoagent", cfg["agent_id"])

    @patch("plugins.echo_agent.plugin.OpenVikingClient")
    @patch("plugins.echo_agent.plugin.EchoMemClient")
    @patch("plugins.echo_agent.plugin.EchoAgentClient")
    def test_setup_agent_id_uses_config_value(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentPlugin()
        cfg = {"agent_id": "my-agent"}
        plugin.setup(cfg)
        self.assertEqual("my-agent", plugin._agent_id)

    @patch("plugins.echo_agent.plugin.OpenVikingClient")
    @patch("plugins.echo_agent.plugin.EchoMemClient")
    @patch("plugins.echo_agent.plugin.EchoAgentClient")
    def test_setup_auth_key_from_config(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock()
        plugin = EchoAgentPlugin()
        cfg = {"echomem_auth_key": "config-key"}
        plugin.setup(cfg)
        self.assertEqual("config-key", plugin._auth_key)
        mock_client.get_memory_auth_key.assert_not_called()

    @patch("plugins.echo_agent.plugin.OpenVikingClient")
    @patch("plugins.echo_agent.plugin.EchoMemClient")
    @patch("plugins.echo_agent.plugin.EchoAgentClient")
    def test_setup_auth_key_via_get_memory_auth_key(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="resolved-key")
        plugin = EchoAgentPlugin()
        cfg = {}
        plugin.setup(cfg)
        self.assertEqual("resolved-key", plugin._auth_key)
        self.assertEqual("resolved-key", cfg["echomem_auth_key"])
        mock_client.get_memory_auth_key.assert_called_once()

    @patch("plugins.echo_agent.plugin.OpenVikingClient")
    @patch("plugins.echo_agent.plugin.EchoMemClient")
    @patch("plugins.echo_agent.plugin.EchoAgentClient")
    def test_setup_auth_key_empty_on_failure(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(
            side_effect=RuntimeError("nope"),
        )
        plugin = EchoAgentPlugin()
        plugin.setup({})
        self.assertEqual("", plugin._auth_key)

    @patch("plugins.echo_agent.plugin.OpenVikingClient")
    @patch("plugins.echo_agent.plugin.EchoMemClient")
    @patch("plugins.echo_agent.plugin.EchoAgentClient")
    def test_setup_creates_echomem_client(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentPlugin()
        plugin.setup({
            "memory_backend": "echomem",
            "echomem_url": "http://mem:8010",
        })
        mock_echomem_cls.assert_called_once()
        mock_ov_cls.assert_not_called()
        self.assertIs(mock_echomem_cls.return_value, plugin.memory_client)

    @patch("plugins.echo_agent.plugin.OpenVikingClient")
    @patch("plugins.echo_agent.plugin.EchoMemClient")
    @patch("plugins.echo_agent.plugin.EchoAgentClient")
    def test_setup_creates_openviking_client(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentPlugin()
        plugin.setup({
            "memory_backend": "openviking",
            "echomem_url": "http://ov:19080",
        })
        mock_ov_cls.assert_called_once()
        mock_echomem_cls.assert_not_called()
        self.assertIs(mock_ov_cls.return_value, plugin.memory_client)

    @patch("plugins.echo_agent.plugin.OpenVikingClient")
    @patch("plugins.echo_agent.plugin.EchoMemClient")
    @patch("plugins.echo_agent.plugin.EchoAgentClient")
    def test_setup_never_provisions_isolated_identity(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        """EchoAgent plugin must not create an isolated tenant.

        EchoAgent's backend resolves auth_key via the echoagent plugin
        (31030) using the logged-in user's UUID as userId.  If injection
        used a different tenant, retrieval would find nothing.  Therefore
        the injection identity must always match the retrieval identity.
        """
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        mock_mem = mock_echomem_cls.return_value
        plugin = EchoAgentPlugin()
        plugin.setup({"benchmark_name": "locomo", "run_id": "run-1"})
        mock_mem.provision_isolated_identity.assert_not_called()

    @patch("plugins.echo_agent.plugin.OpenVikingClient")
    @patch("plugins.echo_agent.plugin.EchoMemClient")
    @patch("plugins.echo_agent.plugin.EchoAgentClient")
    def test_setup_initializes_typing_state(
        self, mock_agent_cls, mock_echomem_cls, mock_ov_cls,
    ):
        mock_client = mock_agent_cls.return_value
        mock_client.get_memory_auth_key = MagicMock(return_value="ek")
        plugin = EchoAgentPlugin()
        plugin.setup({})
        self.assertEqual("", plugin._pending_turn_id)
        self.assertFalse(plugin._typing_committed)
        self.assertEqual([], plugin._typing_memory_items)

    # -- supports_typing_simulation -------------------------------------

    def test_supports_typing_simulation_returns_true(self):
        plugin = _make_plugin()
        self.assertTrue(plugin.supports_typing_simulation)

    # -- simulate_typing (fast mode: speed_ms < 50) ---------------------

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_fast_mode_committed(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.client.prefetch_finalize = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        result = plugin.simulate_typing("s1", "/", "hello", speed_ms=10)
        self.assertIsInstance(result, TypingResult)
        self.assertTrue(result.committed)
        self.assertEqual([], result.memory_items)
        plugin.client.prefetch_tick.assert_called_once()
        plugin.client.prefetch_finalize.assert_called_once()
        self.assertNotEqual("", plugin._pending_turn_id)
        self.assertTrue(plugin._typing_committed)

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_fast_mode_finalize_none(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.client.prefetch_finalize = MagicMock(return_value=None)
        result = plugin.simulate_typing("s1", "/", "hello", speed_ms=10)
        self.assertFalse(result.committed)
        self.assertNotEqual("", plugin._pending_turn_id)
        self.assertFalse(plugin._typing_committed)

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_fast_mode_tick_none_returns_none(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(return_value=None)
        plugin.client.prefetch_finalize = MagicMock()
        result = plugin.simulate_typing("s1", "/", "hello", speed_ms=10)
        self.assertIsNone(result)
        plugin.client.prefetch_finalize.assert_not_called()

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_fast_mode_sleep_called(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.client.prefetch_finalize = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.simulate_typing("s1", "/", "hi", speed_ms=10)
        mock_sleep.assert_called_once_with(0.5)

    # -- simulate_typing (normal mode: speed_ms >= 50) ------------------

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_normal_mode_committed_with_memory_items(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        mem_items = [{"text": "mem1"}, {"text": "mem2"}]
        plugin.client.prefetch_finalize = MagicMock(
            return_value={"data": {"accepted": True, "memoryItems": mem_items}},
        )
        result = plugin.simulate_typing("s1", "/", "abc", speed_ms=100)
        self.assertTrue(result.committed)
        self.assertEqual(mem_items, result.memory_items)
        self.assertEqual(3, plugin.client.prefetch_tick.call_count)
        self.assertEqual(1, plugin.client.prefetch_finalize.call_count)
        self.assertNotEqual("", plugin._pending_turn_id)
        self.assertTrue(plugin._typing_committed)
        self.assertEqual(mem_items, plugin._typing_memory_items)

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_normal_mode_first_tick_not_accepted(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(
            return_value={"data": {"accepted": False}},
        )
        plugin.client.prefetch_finalize = MagicMock()
        result = plugin.simulate_typing("s1", "/", "abc", speed_ms=100)
        self.assertFalse(result.committed)
        self.assertEqual([], result.memory_items)
        self.assertEqual(1, plugin.client.prefetch_tick.call_count)
        plugin.client.prefetch_finalize.assert_not_called()
        self.assertNotEqual("", plugin._pending_turn_id)
        self.assertFalse(plugin._typing_committed)
        self.assertEqual([], plugin._typing_memory_items)

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_normal_mode_tick_none_returns_none(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(return_value=None)
        plugin.client.prefetch_finalize = MagicMock()
        result = plugin.simulate_typing("s1", "/", "abc", speed_ms=100)
        self.assertIsNone(result)
        plugin.client.prefetch_finalize.assert_not_called()

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_normal_mode_tick_sends_increasing_prefixes(
        self, mock_sleep,
    ):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.client.prefetch_finalize = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.simulate_typing("s1", "/", "ab", speed_ms=100)
        calls = plugin.client.prefetch_tick.call_args_list
        self.assertEqual(1, calls[0].args[3])   # revision
        self.assertEqual("a", calls[0].args[4])  # draft
        self.assertEqual(2, calls[1].args[3])
        self.assertEqual("ab", calls[1].args[4])

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_normal_mode_same_turn_id_across_ticks(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.client.prefetch_finalize = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.simulate_typing("s1", "/", "ab", speed_ms=100)
        turn_ids = {call.args[2] for call in plugin.client.prefetch_tick.call_args_list}
        self.assertEqual(1, len(turn_ids))
        self.assertEqual(
            turn_ids.pop(),
            plugin.client.prefetch_finalize.call_args.args[2],
        )

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_normal_mode_finalize_none(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.client.prefetch_finalize = MagicMock(return_value=None)
        result = plugin.simulate_typing("s1", "/", "ab", speed_ms=100)
        self.assertFalse(result.committed)
        self.assertEqual([], result.memory_items)
        self.assertEqual([], plugin._typing_memory_items)

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_normal_mode_sleep_called_per_char(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.client.prefetch_finalize = MagicMock(
            return_value={"data": {"accepted": True}},
        )
        plugin.simulate_typing("s1", "/", "ab", speed_ms=100, jitter_ms=20)
        self.assertEqual(2, mock_sleep.call_count)

    @patch("plugins.echo_agent.plugin.time.sleep")
    def test_typing_normal_mode_no_sleep_on_early_return(self, mock_sleep):
        plugin = _make_plugin()
        plugin.client.prefetch_tick = MagicMock(
            return_value={"data": {"accepted": False}},
        )
        plugin.client.prefetch_finalize = MagicMock()
        plugin.simulate_typing("s1", "/", "ab", speed_ms=100)
        mock_sleep.assert_not_called()

    # -- create_session (plugin) ----------------------------------------

    def test_create_session_delegates_to_client(self):
        plugin = _make_plugin()
        plugin.client.create_session = MagicMock(return_value="sess-1")
        sid = plugin.create_session("title")
        plugin.client.create_session.assert_called_once_with(
            "title", "http://127.0.0.1:31030",
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
        self.assertEqual("echo_agent", resp.extra.get("qa_profile"))
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
            "qa-q1", "http://127.0.0.1:31030",
        )
        plugin.client.send_message.assert_called_once_with(
            "ea-sess-1", "/", "hi", "",
        )
        plugin.client.stream_reply.assert_called_once_with("ea-sess-1", "/", 5)
        self.assertEqual("hello", resp.text)

    def test_send_message_passes_pending_turn_id(self):
        plugin = _make_plugin()
        plugin._pending_turn_id = "turn-xyz"
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 1, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "", "ttft_ms": None, "done_event": {},
        })
        plugin.send_message("s1", "hi", "/")
        plugin.client.send_message.assert_called_once_with(
            "s1", "/", "hi", "turn-xyz",
        )

    def test_send_message_clears_typing_state(self):
        plugin = _make_plugin()
        plugin._pending_turn_id = "turn-1"
        plugin._typing_committed = True
        plugin._typing_memory_items = [{"text": "m"}]
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 1, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "", "ttft_ms": None, "done_event": {},
        })
        resp = plugin.send_message("s1", "hi", "/")
        self.assertTrue(resp.prefetch_committed)
        self.assertEqual([{"text": "m"}], resp.memory_items)
        self.assertEqual("", plugin._pending_turn_id)
        self.assertFalse(plugin._typing_committed)
        self.assertEqual([], plugin._typing_memory_items)

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
        self.assertEqual("echo_agent", resp.extra.get("qa_profile"))
        plugin.client.stream_reply.assert_not_called()

    def test_send_message_exception_returns_error(self):
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(
            side_effect=ConnectionError("boom"),
        )
        plugin.client.stream_reply = MagicMock()
        resp = plugin.send_message("s1", "hi", "/")
        self.assertEqual("boom", resp.error)
        self.assertEqual("echo_agent", resp.extra.get("qa_profile"))
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

    def test_send_message_metrics_and_done_memory_items(self):
        """done_event carries metrics + memoryItems and there is no prefill:
        metrics read from the snake_case keys, memory_items from done."""
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 1, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "hello",
            "ttft_ms": 90.0,
            "done_event": {
                "metrics": {
                    "ttft_ms": 70.0,
                    "prompt_tokens": 20,
                    "completion_tokens": 12,
                    "cached_tokens": 5,
                    "elapsed_ms": 1000,
                    "retrieval_latency_ms": 200,
                    "llm_latency_ms": 500,
                    "tool_call_count": 1,
                    "turn_iterations": 2,
                    "model_name": "doubao-seed-2.0-pro",
                    "finish_reason": "stop",
                },
                "memoryItems": [{"text": "m1"}],
                "toolAudit": [{"name": "web_search", "callId": "c2", "arguments": "{}"}],
            },
        })
        resp = plugin.send_message("s1", "hi", "/")
        self.assertEqual("hello", resp.text)
        self.assertEqual(70.0, resp.ttft_ms)
        self.assertEqual(20, resp.prompt_tokens)
        self.assertEqual(12, resp.completion_tokens)
        self.assertEqual(5, resp.cached_tokens)
        self.assertFalse(resp.prefetch_committed)
        self.assertEqual([{"text": "m1"}], resp.memory_items)
        self.assertEqual("echo_agent", resp.extra.get("qa_profile"))
        self.assertEqual(1.0, resp.extra["elapsed_s"])
        self.assertEqual(0.2, resp.extra["retrieval_latency_s"])
        self.assertEqual(0.5, resp.extra["llm_latency_s"])
        self.assertEqual(1, resp.extra["tool_call_count"])
        self.assertEqual(2, resp.extra["iterations"])
        self.assertEqual(
            "doubao-seed-2.0-pro", resp.extra["trace"]["model"],
        )
        self.assertEqual("stop", resp.extra["trace"]["finish_reason"])
        self.assertEqual(
            [{"name": "web_search", "callId": "c2", "arguments": "{}"}],
            resp.extra["trace"]["tool_audit"],
        )

    def test_send_message_prefill_memory_items_take_priority(self):
        """prefill memory_items win over done-event memoryItems."""
        plugin = _make_plugin()
        plugin._typing_committed = True
        plugin._typing_memory_items = [{"text": "prefill"}]
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 1, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "x",
            "ttft_ms": None,
            "done_event": {"memoryItems": [{"text": "done"}]},
        })
        resp = plugin.send_message("s1", "hi", "/")
        self.assertTrue(resp.prefetch_committed)
        self.assertEqual([{"text": "prefill"}], resp.memory_items)
        self.assertEqual("", plugin._pending_turn_id)
        self.assertFalse(plugin._typing_committed)
        self.assertEqual([], plugin._typing_memory_items)

    def test_send_message_memory_items_fallback_to_done(self):
        """empty prefill memory_items fall back to done-event memoryItems."""
        plugin = _make_plugin()
        plugin.client.send_message = MagicMock(return_value={
            "data": {"messages": [{"seq": 1, "status": "completed"}]},
        })
        plugin.client.stream_reply = MagicMock(return_value={
            "reply": "x",
            "ttft_ms": None,
            "done_event": {"memoryItems": [{"text": "done"}]},
        })
        resp = plugin.send_message("s1", "hi", "/")
        self.assertFalse(resp.prefetch_committed)
        self.assertEqual([{"text": "done"}], resp.memory_items)

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

    # -- teardown -------------------------------------------------------

    def test_teardown_is_noop(self):
        plugin = _make_plugin()
        plugin.teardown()

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

    def test_getlog_openviking_fetches_console_logs(self):
        plugin = _make_plugin()
        plugin._memory_backend = "openviking"
        plugin.memory_client.fetch_console_logs = MagicMock(
            return_value={"events": []},
        )
        result = plugin.getlog()
        data = json.loads(result)
        self.assertIn("events", data)


if __name__ == "__main__":
    unittest.main()
