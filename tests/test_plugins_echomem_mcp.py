"""Exhaustive unit tests for the echomem_mcp agent plugin.

Covers every functional point in:
- plugins/echomem_mcp/mcp_client.py  (McpClient: handshake, tool calls, SSE, errors)
- plugins/echomem_mcp/runtime.py      (MCP_TOOLS, _SYSTEM_PROMPT)
- plugins/echomem_mcp/plugin.py       (EchoMemMCPPlugin: args, setup, inject, send_message)

All HTTP is mocked via urllib.request.urlopen; LLMClient and EchoMemClient are
mocked for plugin-level tests.  No real network is used.
"""

from __future__ import annotations

import argparse
import email.message
import io
import json
import unittest
import urllib.error
from typing import Any
from unittest.mock import ANY, MagicMock, patch

from backends.memory_types import CommitResult, SearchResult
from plugins.echomem_mcp.mcp_client import McpClient
from plugins.echomem_mcp.plugin import EchoMemMCPPlugin, format_split_memory_section
from plugins.echomem_mcp.runtime import (
    MCP_TOOLS,
    _NO_TOOLS_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
    configured_tools,
)
from shared.llm_client import LLMResponse, LLMToolResponse


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _sse(payload: dict[str, Any]) -> bytes:
    """Build a minimal SSE stream carrying one JSON ``data:`` payload."""
    return ("event: message\ndata: " + json.dumps(payload) + "\n\n").encode("utf-8")


def _urlopen_cm(raw: bytes, headers: dict[str, str]):
    """Build a context-manager mock for ``urllib.request.urlopen``.

    The returned object is usable as ``with urlopen(...) as resp:`` and yields a
    response whose ``.headers`` (a plain dict) and ``.read()`` are controlled.
    """
    resp = MagicMock()
    resp.headers = headers
    resp.read.return_value = raw
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    """Build a real HTTPError that ``.read()`` returns *body*."""
    return urllib.error.HTTPError(
        "http://localhost/mcp", code, "Error", email.message.Message(), io.BytesIO(body)
    )


def _make_plugin(**overrides: Any) -> EchoMemMCPPlugin:
    """Construct an EchoMemMCPPlugin with mocked LLM/memory clients.

    Internal config fields are set directly so send_message tests have full
    control without going through setup().  ``manual_search`` is an internal
    test/config switch only; it is not exposed as an end-user CLI flag.
    """
    p = EchoMemMCPPlugin()
    p._mcp_url = overrides.get("mcp_url", "http://127.0.0.1:8001")
    p._auth_key = overrides.get("auth_key", "mcp-key")
    p._max_iterations = overrides.get("max_iterations", 10)
    p._tool_calling = overrides.get("tool_calling", True)
    p._search_in_tools = overrides.get("search_in_tools", True)
    p._manual_search = overrides.get("manual_search", False)
    p._mcp_read_mode = overrides.get("mcp_read_mode", "allow")
    p._top_k = overrides.get("top_k", 25)
    p._memory_budget_chars = overrides.get("memory_budget_chars", 0)
    p._user_memory_budget_chars = overrides.get("user_memory_budget_chars", 4000)
    p._agent_memory_budget_chars = overrides.get("agent_memory_budget_chars", 2000)
    p._question_timeout_s = overrides.get("question_timeout_s", 120.0)
    p._commit_timeout_s = overrides.get("commit_timeout_s", 0.0)
    p._commit_poll_interval_s = overrides.get("commit_poll_interval_s", 2.0)
    p._llm = MagicMock()
    p.memory_client = MagicMock()
    p.memory_client.search.return_value = []
    return p


def _tool_call(name: str, arguments: dict[str, Any], cid: str = "c1") -> dict[str, Any]:
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


_URLOPEN = "plugins.echomem_mcp.mcp_client.urllib.request.urlopen"
_MCP_CLIENT = "plugins.echomem_mcp.plugin.McpClient"


# --------------------------------------------------------------------------- #
#  McpClient -- constructor                                                   #
# --------------------------------------------------------------------------- #

class McpClientConstructorTests(unittest.TestCase):
    def test_stores_fields_and_strips_trailing_slash(self) -> None:
        c = McpClient("http://localhost:8001/", auth_key="k", timeout_s=30.0)
        self.assertEqual("http://localhost:8001", c.base_url)
        self.assertEqual("k", c.auth_key)
        self.assertEqual(30.0, c.timeout_s)
        self.assertIsNone(c._session_id)
        self.assertEqual(0, c._req_id)

    def test_defaults(self) -> None:
        c = McpClient("http://localhost:8001")
        self.assertEqual("", c.auth_key)
        self.assertEqual(60.0, c.timeout_s)
        self.assertIsNone(c._session_id)
        self.assertEqual(0, c._req_id)


class McpClientMiscTests(unittest.TestCase):
    def test_next_id_increments_from_one(self) -> None:
        c = McpClient("http://x")
        self.assertEqual(1, c._next_id())
        self.assertEqual(2, c._next_id())
        self.assertEqual(3, c._next_id())

    def test_close_is_noop(self) -> None:
        c = McpClient("http://x")
        c.close()  # must not raise


# --------------------------------------------------------------------------- #
#  McpClient -- _build_headers                                                #
# --------------------------------------------------------------------------- #

class McpClientHeadersTests(unittest.TestCase):
    def test_content_type_and_accept_always_present(self) -> None:
        h = McpClient("http://x")._build_headers(include_session=False)
        self.assertEqual("application/json", h["Content-Type"])
        self.assertEqual("application/json, text/event-stream", h["Accept"])

    def test_auth_key_included_when_set(self) -> None:
        h = McpClient("http://x", auth_key="secret")._build_headers(include_session=False)
        self.assertEqual("secret", h["X-Auth-Key"])

    def test_auth_key_omitted_when_empty(self) -> None:
        h = McpClient("http://x")._build_headers(include_session=False)
        self.assertNotIn("X-Auth-Key", h)

    def test_session_headers_included_with_session_id(self) -> None:
        c = McpClient("http://x", auth_key="k")
        c._session_id = "sess-1"
        h = c._build_headers(include_session=True)
        self.assertEqual("sess-1", h["Mcp-Session-Id"])
        self.assertEqual("2025-06-18", h["mcp-protocol-version"])

    def test_session_headers_omitted_without_session_id(self) -> None:
        c = McpClient("http://x", auth_key="k")
        h = c._build_headers(include_session=True)
        self.assertNotIn("Mcp-Session-Id", h)
        self.assertNotIn("mcp-protocol-version", h)


# --------------------------------------------------------------------------- #
#  McpClient -- _parse_sse (static)                                           #
# --------------------------------------------------------------------------- #

class McpClientParseSseTests(unittest.TestCase):
    def test_parses_first_data_line(self) -> None:
        text = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"x":1}}\n\n'
        self.assertEqual({"jsonrpc": "2.0", "id": 1, "result": {"x": 1}},
                         McpClient._parse_sse(text))

    def test_data_without_space(self) -> None:
        self.assertEqual({"a": 1}, McpClient._parse_sse('data:{"a":1}'))

    def test_strips_whitespace_around_payload(self) -> None:
        self.assertEqual({"a": 1}, McpClient._parse_sse('data:   {"a": 1}  \n'))

    def test_returns_first_valid_when_multiple_data_lines(self) -> None:
        text = 'data: {"a":1}\ndata: {"b":2}\n'
        self.assertEqual({"a": 1}, McpClient._parse_sse(text))

    def test_skips_invalid_json_and_continues(self) -> None:
        text = 'data: not json\ndata: {"ok":true}\n'
        self.assertEqual({"ok": True}, McpClient._parse_sse(text))

    def test_returns_none_when_all_data_invalid(self) -> None:
        self.assertIsNone(McpClient._parse_sse("data: {bad\n"))

    def test_skips_empty_payload(self) -> None:
        # "data:" with empty payload is skipped; next valid line returned.
        text = "data: \ndata: {\"ok\":1}\n"
        self.assertEqual({"ok": 1}, McpClient._parse_sse(text))

    def test_returns_none_for_no_data_line(self) -> None:
        self.assertIsNone(McpClient._parse_sse("event: message\n\n"))

    def test_returns_none_for_empty_input(self) -> None:
        self.assertIsNone(McpClient._parse_sse(""))


# --------------------------------------------------------------------------- #
#  McpClient -- _post                                                         #
# --------------------------------------------------------------------------- #

class McpClientPostTests(unittest.TestCase):
    @patch(_URLOPEN)
    def test_returns_result_and_headers(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(
            _sse({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}),
            {"Mcp-Session-Id": "s"},
        )
        c = McpClient("http://localhost:8001")
        result, headers = c._post({"jsonrpc": "2.0", "id": 1, "method": "x"})
        self.assertEqual({"ok": True}, result)
        self.assertEqual("s", headers["Mcp-Session-Id"])

    @patch(_URLOPEN)
    def test_post_url_is_base_plus_mcp(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(
            _sse({"jsonrpc": "2.0", "id": 1, "result": {}}), {},
        )
        c = McpClient("http://localhost:8001")
        c._post({"jsonrpc": "2.0", "id": 1, "method": "x"})
        req = mock_urlopen.call_args.args[0]
        self.assertEqual("http://localhost:8001/mcp", req.full_url)
        self.assertEqual("POST", req.method)

    @patch(_URLOPEN)
    def test_notification_returns_none_result(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(b"", {"X": "y"})
        c = McpClient("http://localhost:8001")
        result, headers = c._post(
            {"jsonrpc": "2.0", "method": "n"}, is_notification=True,
        )
        self.assertIsNone(result)
        self.assertEqual("y", headers["X"])

    @patch(_URLOPEN)
    def test_http_error_raises_runtime(self, mock_urlopen: MagicMock) -> None:
        for code, body in [(500, b"server down"), (404, b"not found"), (401, b"unauth")]:
            with self.subTest(code=code):
                mock_urlopen.side_effect = _http_error(code, body)
                c = McpClient("http://localhost:8001")
                with self.assertRaises(RuntimeError) as ctx:
                    c._post({"jsonrpc": "2.0", "id": 1, "method": "x"})
                self.assertIn(f"HTTP {code}", str(ctx.exception))
                self.assertIn(body.decode(), str(ctx.exception))

    @patch(_URLOPEN)
    def test_jsonrpc_error_raises_runtime(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(
            _sse({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "bad"}}),
            {},
        )
        c = McpClient("http://localhost:8001")
        with self.assertRaises(RuntimeError) as ctx:
            c._post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertIn("JSON-RPC error", str(ctx.exception))
        self.assertIn("bad", str(ctx.exception))

    @patch(_URLOPEN)
    def test_no_sse_data_raises_runtime(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(b"plain text no data", {})
        c = McpClient("http://localhost:8001")
        with self.assertRaises(RuntimeError) as ctx:
            c._post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertIn("no SSE data", str(ctx.exception))


# --------------------------------------------------------------------------- #
#  McpClient -- initialize                                                    #
# --------------------------------------------------------------------------- #

class McpClientInitializeTests(unittest.TestCase):
    @patch(_URLOPEN)
    def test_captures_session_id_from_header(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = [
            _urlopen_cm(_sse({"jsonrpc": "2.0", "id": 1, "result": {}}),
                        {"Mcp-Session-Id": "sess-123"}),
            _urlopen_cm(b"", {}),  # notification response (HTTP 202)
        ]
        c = McpClient("http://localhost:8001", auth_key="k")
        c.initialize()
        self.assertEqual("sess-123", c._session_id)
        self.assertEqual(2, mock_urlopen.call_count)

    @patch(_URLOPEN)
    def test_captures_session_id_from_lowercase_header(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = [
            _urlopen_cm(_sse({"jsonrpc": "2.0", "id": 1, "result": {}}),
                        {"mcp-session-id": "sess-lower"}),
            _urlopen_cm(b"", {}),
        ]
        c = McpClient("http://localhost:8001")
        c.initialize()
        self.assertEqual("sess-lower", c._session_id)

    @patch(_URLOPEN)
    def test_missing_session_id_raises(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(
            _sse({"jsonrpc": "2.0", "id": 1, "result": {}}), {},
        )
        c = McpClient("http://localhost:8001")
        with self.assertRaises(RuntimeError) as ctx:
            c.initialize()
        self.assertIn("session ID", str(ctx.exception))

    @patch(_URLOPEN)
    def test_initialize_request_body(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = [
            _urlopen_cm(_sse({"jsonrpc": "2.0", "id": 1, "result": {}}),
                        {"Mcp-Session-Id": "s1"}),
            _urlopen_cm(b"", {}),
        ]
        c = McpClient("http://localhost:8001")
        c.initialize()
        first_req = mock_urlopen.call_args_list[0].args[0]
        body = json.loads(first_req.data)
        self.assertEqual("2.0", body["jsonrpc"])
        self.assertEqual("initialize", body["method"])
        self.assertEqual(1, body["id"])
        self.assertEqual("2025-06-18", body["params"]["protocolVersion"])
        self.assertEqual("eval-harness", body["params"]["clientInfo"]["name"])

    @patch(_URLOPEN)
    def test_sends_initialized_notification_without_id(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = [
            _urlopen_cm(_sse({"jsonrpc": "2.0", "id": 1, "result": {}}),
                        {"Mcp-Session-Id": "s1"}),
            _urlopen_cm(b"", {}),
        ]
        c = McpClient("http://localhost:8001")
        c.initialize()
        notif_req = mock_urlopen.call_args_list[1].args[0]
        body = json.loads(notif_req.data)
        self.assertEqual("notifications/initialized", body["method"])
        self.assertNotIn("id", body)


# --------------------------------------------------------------------------- #
#  McpClient -- call_tool                                                     #
# --------------------------------------------------------------------------- #

class McpClientCallToolTests(unittest.TestCase):
    @patch(_URLOPEN)
    def test_returns_content_text(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(
            _sse({"jsonrpc": "2.0", "id": 2, "result": {
                "content": [{"type": "text", "text": "hello"}]}}), {},
        )
        c = McpClient("http://localhost:8001", auth_key="k")
        c._session_id = "sess-1"
        self.assertEqual("hello", c.call_tool("memory_query", {"query": "hi"}))

    @patch(_URLOPEN)
    def test_request_body_is_tools_call(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(
            _sse({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"text": "x"}]}}), {},
        )
        c = McpClient("http://localhost:8001")
        c._session_id = "sess-1"
        c.call_tool("memory_query", {"query": "hi", "limit": 5})
        req = mock_urlopen.call_args.args[0]
        body = json.loads(req.data)
        self.assertEqual("tools/call", body["method"])
        self.assertEqual("memory_query", body["params"]["name"])
        self.assertEqual({"query": "hi", "limit": 5}, body["params"]["arguments"])

    @patch(_URLOPEN)
    def test_requires_session_before_call(self, mock_urlopen: MagicMock) -> None:
        c = McpClient("http://localhost:8001")
        with self.assertRaises(RuntimeError) as ctx:
            c.call_tool("memory_query", {})
        self.assertIn("not initialized", str(ctx.exception))
        mock_urlopen.assert_not_called()

    @patch(_URLOPEN)
    def test_iserror_raises_runtime(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(
            _sse({"jsonrpc": "2.0", "id": 2, "result": {
                "isError": True, "content": [{"text": "boom"}]}}), {},
        )
        c = McpClient("http://localhost:8001")
        c._session_id = "s1"
        with self.assertRaises(RuntimeError) as ctx:
            c.call_tool("read", {"uris": "echo://x"})
        self.assertIn("boom", str(ctx.exception))
        self.assertIn("read", str(ctx.exception))

    @patch(_URLOPEN)
    def test_empty_content_returns_empty_string(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(
            _sse({"jsonrpc": "2.0", "id": 2, "result": {"content": []}}), {},
        )
        c = McpClient("http://localhost:8001")
        c._session_id = "s1"
        self.assertEqual("", c.call_tool("read", {}))

    @patch(_URLOPEN)
    def test_missing_content_key_returns_empty_string(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _urlopen_cm(
            _sse({"jsonrpc": "2.0", "id": 2, "result": {}}), {},
        )
        c = McpClient("http://localhost:8001")
        c._session_id = "s1"
        self.assertEqual("", c.call_tool("read", {}))


# --------------------------------------------------------------------------- #
#  Runtime -- MCP_TOOLS and _SYSTEM_PROMPT                                    #
# --------------------------------------------------------------------------- #

class RuntimeToolsTests(unittest.TestCase):
    def test_four_tools_defined(self) -> None:
        names = [t["function"]["name"] for t in MCP_TOOLS]
        self.assertEqual(4, len(MCP_TOOLS))
        self.assertEqual({"memory_query", "read", "list", "glob"}, set(names))

    def test_each_tool_has_valid_schema(self) -> None:
        for tool in MCP_TOOLS:
            name = tool["function"]["name"]
            with self.subTest(tool=name):
                self.assertEqual("function", tool["type"])
                func = tool["function"]
                self.assertIn("name", func)
                self.assertIsInstance(func["description"], str)
                self.assertTrue(func["description"])
                params = func["parameters"]
                self.assertEqual("object", params["type"])
                self.assertIn("required", params)
                self.assertIn("properties", params)

    def test_memory_query_schema(self) -> None:
        tool = next(t for t in MCP_TOOLS if t["function"]["name"] == "memory_query")
        params = tool["function"]["parameters"]
        self.assertIn("query", params["required"])
        self.assertIn("limit", params["properties"])
        self.assertEqual(8, params["properties"]["limit"]["default"])

    def test_read_requires_uris(self) -> None:
        tool = next(t for t in MCP_TOOLS if t["function"]["name"] == "read")
        self.assertIn("uris", tool["function"]["parameters"]["required"])
        props = tool["function"]["parameters"]["properties"]
        self.assertEqual("array", props["uris"]["type"])
        self.assertIn("never a singular `uri`", props["uris"]["description"])

    def test_disabled_read_mode_removes_read_tool(self) -> None:
        names = {
            tool["function"]["name"]
            for tool in configured_tools("disabled")
        }
        self.assertEqual({"memory_query", "list", "glob"}, names)

    def test_allow_and_require_modes_include_read_tool(self) -> None:
        for mode in ("allow", "require"):
            with self.subTest(mode=mode):
                names = {
                    tool["function"]["name"]
                    for tool in configured_tools(mode)
                }
                self.assertIn("read", names)

    def test_list_requires_uri(self) -> None:
        tool = next(t for t in MCP_TOOLS if t["function"]["name"] == "list")
        params = tool["function"]["parameters"]
        self.assertIn("uri", params["required"])
        self.assertIn("recursive", params["properties"])
        self.assertIn("max_depth", params["properties"])

    def test_glob_requires_pattern(self) -> None:
        tool = next(t for t in MCP_TOOLS if t["function"]["name"] == "glob")
        self.assertIn("pattern", tool["function"]["parameters"]["required"])


class RuntimePromptTests(unittest.TestCase):
    def test_system_prompt_mentions_memory_tools(self) -> None:
        self.assertIn("EchoMem", _SYSTEM_PROMPT)
        self.assertIn("MCP tools", _SYSTEM_PROMPT)

    def test_system_prompt_is_nonempty_string(self) -> None:
        self.assertIsInstance(_SYSTEM_PROMPT, str)
        self.assertTrue(_SYSTEM_PROMPT.strip())

    def test_no_tools_prompt_forbids_function_markup(self) -> None:
        self.assertIn("Do not emit tool calls", _NO_TOOLS_SYSTEM_PROMPT)


# --------------------------------------------------------------------------- #
#  Plugin -- descriptor and qa_profile                                        #
# --------------------------------------------------------------------------- #

class PluginDescriptorTests(unittest.TestCase):
    def test_descriptor_fields(self) -> None:
        d = EchoMemMCPPlugin.descriptor
        self.assertEqual("echomem_mcp", d.id)
        self.assertEqual("EchoMem MCP Agent", d.name)
        self.assertIn("MCP", d.description)

    def test_qa_profile_returns_descriptor_id(self) -> None:
        self.assertEqual("echomem_mcp", EchoMemMCPPlugin().qa_profile)


# --------------------------------------------------------------------------- #
#  Plugin -- add_arguments                                                    #
# --------------------------------------------------------------------------- #

class PluginAddArgumentsTests(unittest.TestCase):
    def _parse(self, *argv: str) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        EchoMemMCPPlugin.add_arguments(parser)
        return parser.parse_args(argv)

    def test_mcp_specific_defaults(self) -> None:
        args = self._parse()
        self.assertEqual("http://127.0.0.1:8001", args.mcp_url)
        self.assertEqual("", args.mcp_auth_key)
        self.assertEqual(50, args.mcp_max_iterations)
        self.assertTrue(args.tool_calling)
        self.assertTrue(args.search_in_tools)
        self.assertFalse(hasattr(args, "manual_search"))
        self.assertFalse(hasattr(args, "mcp_initial_search"))
        self.assertEqual(4000, args.user_memory_budget_chars)
        self.assertEqual(2000, args.agent_memory_budget_chars)

    def test_memory_budget_overrides_are_ints(self) -> None:
        args = self._parse(
            "--user-memory-budget-chars", "1234",
            "--agent-memory-budget-chars", "567",
        )
        self.assertEqual(1234, args.user_memory_budget_chars)
        self.assertEqual(567, args.agent_memory_budget_chars)

    def test_no_tool_calling_flag(self) -> None:
        self.assertFalse(self._parse("--no-tool-calling").tool_calling)

    def test_tool_calling_flag_explicit(self) -> None:
        self.assertTrue(self._parse("--tool-calling").tool_calling)

    def test_no_search_in_tools_flag(self) -> None:
        self.assertFalse(self._parse("--no-search-in-tools").search_in_tools)

    def test_manual_search_flag_removed(self) -> None:
        with self.assertRaises(SystemExit):
            self._parse("--manual-search")
        with self.assertRaises(SystemExit):
            self._parse("--no-manual-search")

    def test_mcp_max_iterations_is_int(self) -> None:
        self.assertEqual(5, self._parse("--mcp-max-iterations", "5").mcp_max_iterations)

    def test_no_memory_backend_choice_added(self) -> None:
        # add_memory_backend_args is called without with_backend_choice.
        args = self._parse()
        self.assertFalse(hasattr(args, "memory_backend"))

    def test_mcp_url_and_auth_key_overridable(self) -> None:
        args = self._parse("--mcp-url", "http://host:9", "--mcp-auth-key", "k")
        self.assertEqual("http://host:9", args.mcp_url)
        self.assertEqual("k", args.mcp_auth_key)


# --------------------------------------------------------------------------- #
#  Plugin -- setup                                                            #
# --------------------------------------------------------------------------- #

class PluginSetupTests(unittest.TestCase):
    @patch("plugins.echomem_mcp.plugin.EchoMemClient")
    @patch("plugins.echomem_mcp.plugin.LLMClient")
    def test_setup_creates_clients_and_stores_config(
        self, mock_llm_cls: MagicMock, mock_echomem_cls: MagicMock,
    ) -> None:
        mock_llm, mock_mem = MagicMock(), MagicMock()
        mock_llm_cls.return_value = mock_llm
        mock_echomem_cls.return_value = mock_mem

        config = {
            "llm_base_url": "http://llm", "llm_api_key": "k", "llm_model": "m",
            "llm_temperature": 0.5, "llm_max_tokens": 100, "llm_timeout_s": 30,
            "llm_retries": 2,
            "echomem_url": "http://mem", "echomem_auth_key": "ek", "account": "a",
            "user_id": "u", "agent_id": "ag", "workspace": "w", "timeout_s": 50,
            "max_retries": 4,
            "mcp_url": "http://mcp", "mcp_auth_key": "mk", "mcp_max_iterations": 5,
            "tool_calling": False, "search_in_tools": False, "manual_search": False,
            "top_k": 15, "memory_budget_chars": 1000, "question_timeout_s": 60,
            "user_memory_budget_chars": 4000, "agent_memory_budget_chars": 2000,
            "commit_timeout_s": 10, "commit_poll_interval_s": 1,
        }
        p = EchoMemMCPPlugin()
        p.setup(config)

        self.assertEqual("http://mcp", p._mcp_url)
        self.assertEqual("mk", p._auth_key)
        self.assertEqual(5, p._max_iterations)
        self.assertFalse(p._tool_calling)
        self.assertFalse(p._search_in_tools)
        self.assertFalse(p._manual_search)
        self.assertEqual(15, p._top_k)
        self.assertEqual(1000, p._memory_budget_chars)
        self.assertEqual(4000, p._user_memory_budget_chars)
        self.assertEqual(2000, p._agent_memory_budget_chars)
        self.assertEqual(60.0, p._question_timeout_s)
        self.assertEqual(10.0, p._commit_timeout_s)
        self.assertEqual(1.0, p._commit_poll_interval_s)
        self.assertIs(mock_llm, p._llm)
        self.assertIs(mock_mem, p.memory_client)

        llm_call = mock_llm_cls.call_args
        self.assertEqual("http://llm", llm_call.kwargs["base_url"])
        self.assertEqual("k", llm_call.kwargs["api_key"])
        self.assertEqual("m", llm_call.kwargs["model"])
        self.assertEqual(0.5, llm_call.kwargs["temperature"])
        self.assertEqual(100, llm_call.kwargs["max_tokens"])
        self.assertEqual(30, llm_call.kwargs["timeout_s"])
        self.assertEqual(2, llm_call.kwargs["max_retries"])

        mem_call = mock_echomem_cls.call_args
        self.assertEqual("http://mem", mem_call.kwargs["base_url"])
        self.assertEqual("ek", mem_call.kwargs["auth_key"])
        self.assertEqual("a", mem_call.kwargs["account"])
        self.assertEqual("u", mem_call.kwargs["user_id"])
        self.assertEqual("ag", mem_call.kwargs["agent_id"])
        self.assertEqual("w", mem_call.kwargs["workspace"])
        self.assertEqual(50.0, mem_call.kwargs["timeout_s"])
        self.assertEqual(4, mem_call.kwargs["max_retries"])

    def test_setup_defaults_with_empty_config(self) -> None:
        # No network in constructors; safe to call without mocks.
        p = EchoMemMCPPlugin()
        p.setup({})
        self.assertEqual("http://127.0.0.1:8001", p._mcp_url)
        self.assertEqual("", p._auth_key)
        self.assertEqual(50, p._max_iterations)
        self.assertTrue(p._tool_calling)
        self.assertTrue(p._search_in_tools)
        self.assertTrue(p._manual_search)
        self.assertEqual(25, p._top_k)
        self.assertEqual(8000, p._memory_budget_chars)
        self.assertEqual(4000, p._user_memory_budget_chars)
        self.assertEqual(2000, p._agent_memory_budget_chars)
        self.assertEqual(120.0, p._question_timeout_s)
        self.assertEqual(0.0, p._commit_timeout_s)
        self.assertEqual(2.0, p._commit_poll_interval_s)

    @patch("plugins.echomem_mcp.plugin.EchoMemClient")
    @patch("plugins.echomem_mcp.plugin.LLMClient")
    def test_auth_key_falls_back_to_echomem_auth_key(
        self, mock_llm_cls: MagicMock, mock_echomem_cls: MagicMock,
    ) -> None:
        p = EchoMemMCPPlugin()
        p.setup({"mcp_auth_key": "", "echomem_auth_key": "ek-fallback"})
        self.assertEqual("ek-fallback", p._auth_key)

    @patch("plugins.echomem_mcp.plugin.EchoMemClient")
    @patch("plugins.echomem_mcp.plugin.LLMClient")
    def test_reuses_explicit_auth_key_without_reprovisioning(
        self, mock_llm_cls: MagicMock, mock_echomem_cls: MagicMock,
    ) -> None:
        mock_mem = MagicMock()
        mock_echomem_cls.return_value = mock_mem
        p = EchoMemMCPPlugin()
        p.setup({
            "benchmark_name": "locomo",
            "run_id": "r1",
            "resume_qa": "",
            "echomem_auth_key": "ek-existing",
        })
        mock_mem.provision_isolated_identity.assert_not_called()

    @patch("plugins.echomem_mcp.plugin.EchoMemClient")
    @patch("plugins.echomem_mcp.plugin.LLMClient")
    def test_provisions_isolated_identity_when_benchmark_and_run(
        self, mock_llm_cls: MagicMock, mock_echomem_cls: MagicMock,
    ) -> None:
        mock_mem = MagicMock()
        mock_echomem_cls.return_value = mock_mem
        p = EchoMemMCPPlugin()
        p.setup({"benchmark_name": "locomo", "run_id": "r1", "resume_qa": ""})
        mock_mem.provision_isolated_identity.assert_called_once_with("eval-locomo-r1")

    @patch("plugins.echomem_mcp.plugin.EchoMemClient")
    @patch("plugins.echomem_mcp.plugin.LLMClient")
    def test_no_identity_isolation_when_resume_qa(
        self, mock_llm_cls: MagicMock, mock_echomem_cls: MagicMock,
    ) -> None:
        mock_mem = MagicMock()
        mock_echomem_cls.return_value = mock_mem
        p = EchoMemMCPPlugin()
        p.setup({"benchmark_name": "locomo", "run_id": "r1", "resume_qa": "yes"})
        mock_mem.provision_isolated_identity.assert_not_called()

    @patch("plugins.echomem_mcp.plugin.EchoMemClient")
    @patch("plugins.echomem_mcp.plugin.LLMClient")
    def test_no_identity_isolation_without_benchmark_name(
        self, mock_llm_cls: MagicMock, mock_echomem_cls: MagicMock,
    ) -> None:
        mock_mem = MagicMock()
        mock_echomem_cls.return_value = mock_mem
        p = EchoMemMCPPlugin()
        p.setup({"benchmark_name": "", "run_id": "r1"})
        mock_mem.provision_isolated_identity.assert_not_called()

    @patch("plugins.echomem_mcp.plugin.EchoMemClient")
    @patch("plugins.echomem_mcp.plugin.LLMClient")
    def test_identity_label_truncated_to_120_chars(
        self, mock_llm_cls: MagicMock, mock_echomem_cls: MagicMock,
    ) -> None:
        mock_mem = MagicMock()
        mock_echomem_cls.return_value = mock_mem
        long_name = "x" * 200
        p = EchoMemMCPPlugin()
        p.setup({"benchmark_name": long_name, "run_id": "r1"})
        label = mock_mem.provision_isolated_identity.call_args.args[0]
        self.assertLessEqual(len(label), 120)


# --------------------------------------------------------------------------- #
#  Plugin -- inject_memories                                                  #
# --------------------------------------------------------------------------- #

class PluginInjectMemoriesTests(unittest.TestCase):
    def test_creates_session_commits_and_returns_id(self) -> None:
        p = _make_plugin()
        p.memory_client.open_session.return_value = "sess-1"
        p.memory_client.commit_session.return_value = "arch-1"
        p.memory_client.poll_commit.return_value = CommitResult(
            "sess-1", "arch-1", "completed", 0.5, 2,
        )
        sid = p.inject_memories([
            {"text": "mem1", "time": "2026-01-01"},
            {"text": "mem2"},
        ])
        self.assertEqual("sess-1", sid)
        p.memory_client.open_session.assert_called_once_with(title="inject")
        self.assertEqual(2, p.memory_client.add_message.call_count)
        p.memory_client.commit_session.assert_called_once_with("sess-1")
        p.memory_client.poll_commit.assert_called_once_with(
            "sess-1", "arch-1", timeout_s=p._commit_timeout_s,
            poll_interval_s=p._commit_poll_interval_s,
        )

    def test_add_message_passes_text_and_created_at(self) -> None:
        p = _make_plugin()
        p.memory_client.open_session.return_value = "sess-1"
        p.memory_client.commit_session.return_value = "arch-1"
        p.memory_client.poll_commit.return_value = CommitResult(
            "sess-1", "arch-1", "completed", 0.1, 1,
        )
        p.inject_memories([{"text": "hello", "time": "2026-01-01"}])
        call = p.memory_client.add_message.call_args
        self.assertEqual("sess-1", call.args[0])
        self.assertEqual("user", call.args[1])
        self.assertEqual("hello", call.args[2])
        self.assertEqual("2026-01-01", call.kwargs["created_at"])

    def test_uses_provided_session_id_without_opening(self) -> None:
        p = _make_plugin()
        p.memory_client.commit_session.return_value = "arch-1"
        p.memory_client.poll_commit.return_value = CommitResult(
            "sess-x", "arch-1", "completed", 0.1, 1,
        )
        sid = p.inject_memories([{"text": "m"}], session_id="sess-x")
        self.assertEqual("sess-x", sid)
        p.memory_client.open_session.assert_not_called()

    def test_skips_empty_and_none_text(self) -> None:
        p = _make_plugin()
        p.memory_client.open_session.return_value = "sess-1"
        p.memory_client.commit_session.return_value = "arch-1"
        p.memory_client.poll_commit.return_value = CommitResult(
            "sess-1", "arch-1", "completed", 0.1, 1,
        )
        p.inject_memories([{"text": ""}, {"text": None}, {}, {"text": "real"}])
        self.assertEqual(1, p.memory_client.add_message.call_count)

    def test_raises_on_commit_failure(self) -> None:
        p = _make_plugin()
        p.memory_client.open_session.return_value = "sess-1"
        p.memory_client.commit_session.return_value = "arch-1"
        p.memory_client.poll_commit.return_value = CommitResult(
            "sess-1", "arch-1", "failed", 0.1, 1, error="boom",
        )
        with self.assertRaises(RuntimeError) as ctx:
            p.inject_memories([{"text": "m"}])
        self.assertIn("failed", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))


# --------------------------------------------------------------------------- #
#  Plugin -- create_session                                                   #
# --------------------------------------------------------------------------- #

class PluginCreateSessionTests(unittest.TestCase):
    def test_returns_incrementing_session_ids(self) -> None:
        p = EchoMemMCPPlugin()
        self.assertEqual("echomem_mcp_session_1", p.create_session())
        self.assertEqual("echomem_mcp_session_2", p.create_session())
        self.assertEqual("echomem_mcp_session_3", p.create_session())


# --------------------------------------------------------------------------- #
#  Plugin -- send_message                                                     #
# --------------------------------------------------------------------------- #

class PluginSendMessageTests(unittest.TestCase):
    # -- Phase A: manual pre-fetch -------------------------------------------

    @patch(_MCP_CLIENT)
    def test_manual_search_prefetch_populates_memory_items(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=False, manual_search=True)
        mock_mcp = MagicMock()
        mock_mcp.call_tool.return_value = json.dumps({
            "items": [
                {"uri": "echo://a", "score": 0.9, "content": "memory A"},
                {"uri": "echo://b", "score": 0.5, "content": "memory B"},
            ]
        })
        mock_cls.return_value = mock_mcp
        p._llm.chat.return_value = LLMResponse(
            "answer", 10, 5, 0.1,
        )
        resp = p.send_message("s1", "What do you know?")

        self.assertEqual("answer", resp.text)
        p.memory_client.search.assert_not_called()
        mock_mcp.call_tool.assert_called_once_with(
            "memory_query",
            {"query": "What do you know?", "limit": 25},
            timeout_s=ANY,
        )
        self.assertEqual(2, len(resp.memory_items))
        self.assertEqual("echo://a", resp.memory_items[0]["uri"])
        self.assertGreater(resp.extra["retrieval_latency_s"], 0.0)
        self.assertTrue(resp.extra["initial_search_via_mcp"])

    @patch(_MCP_CLIENT)
    def test_manual_search_injects_memory_into_messages(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=False, manual_search=True)
        mock_mcp = MagicMock()
        mock_mcp.call_tool.return_value = json.dumps({
            "items": [
                {"uri": "echo://a", "score": 0.9, "content": "memory A"},
                {"uri": "echo://agent/a", "score": 0.8, "content": "agent memory"},
            ]
        })
        mock_cls.return_value = mock_mcp
        p._llm.chat.return_value = LLMResponse("answer", 10, 5, 0.1)
        p.send_message("s1", "q")
        messages = p._llm.chat.call_args.args[0]
        # system, memory(user), question(user)
        self.assertEqual(3, len(messages))
        self.assertEqual("system", messages[0]["role"])
        self.assertEqual("user", messages[1]["role"])
        self.assertIn("### user memories", messages[1]["content"])
        self.assertIn("memory A", messages[1]["content"])
        self.assertIn("### agent memories", messages[1]["content"])
        self.assertIn("agent memory", messages[1]["content"])
        self.assertEqual("user", messages[2]["role"])

    def test_split_memory_section_applies_user_and_agent_budgets(self) -> None:
        text = format_split_memory_section(
            [
                SearchResult(uri="echo://user/1", score=0.9, content="user keep"),
                SearchResult(uri="echo://user/2", score=0.8, content="user drop"),
                SearchResult(uri="echo://agent/1", score=0.7, content="agent keep"),
                SearchResult(uri="echo://agent/2", score=0.6, content="agent drop"),
            ],
            user_memory_budget_chars=60,
            agent_memory_budget_chars=62,
        )
        self.assertIn("### user memories", text)
        self.assertIn("user keep", text)
        self.assertNotIn("user drop", text)
        self.assertIn("### agent memories", text)
        self.assertIn("agent keep", text)
        self.assertNotIn("agent drop", text)

    def test_split_memory_section_keeps_uri_and_continues_after_long_item(self) -> None:
        text = format_split_memory_section(
            [
                SearchResult(uri="echo://user/1", score=0.9, content="first"),
                SearchResult(uri="echo://user/2", score=0.8, content="x" * 200),
                SearchResult(uri="echo://user/3", score=0.7, content="later"),
            ],
            user_memory_budget_chars=150,
            agent_memory_budget_chars=0,
        )
        self.assertIn("first", text)
        self.assertIn("echo://user/2", text)
        self.assertNotIn("x" * 200, text)
        self.assertIn("later", text)

    @patch(_MCP_CLIENT)
    def test_manual_search_empty_results_no_injection(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=False, manual_search=True)
        mock_mcp = MagicMock()
        mock_mcp.call_tool.return_value = json.dumps({"items": []})
        mock_cls.return_value = mock_mcp
        p._llm.chat.return_value = LLMResponse("answer", 5, 3, 0.1)
        resp = p.send_message("s1", "q")
        self.assertEqual([], resp.memory_items)
        messages = p._llm.chat.call_args.args[0]
        self.assertEqual(2, len(messages))  # system + user only

    @patch(_MCP_CLIENT)
    def test_manual_search_exception_is_reported(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=False, manual_search=True)
        mock_mcp = MagicMock()
        mock_mcp.call_tool.side_effect = RuntimeError("search down")
        mock_cls.return_value = mock_mcp
        p._llm.chat.return_value = LLMResponse("answer", 5, 3, 0.1)
        resp = p.send_message("s1", "q")
        self.assertEqual("answer", resp.text)
        self.assertEqual([], resp.memory_items)
        self.assertEqual(0.0, resp.extra["retrieval_latency_s"])
        self.assertEqual(
            "RuntimeError: search down",
            resp.extra["retrieval_error"],
        )

    def test_manual_search_disabled_skips_search(self) -> None:
        p = _make_plugin(tool_calling=False, manual_search=False)
        p._llm.chat.return_value = LLMResponse("answer", 5, 3, 0.1)
        p.send_message("s1", "q")
        p.memory_client.search.assert_not_called()

    # -- Phase B: tool list building -----------------------------------------

    @patch(_MCP_CLIENT)
    def test_all_four_tools_passed_when_search_in_tools(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, search_in_tools=True, manual_search=False)
        mock_cls.return_value = MagicMock()
        p._llm.chat_with_tools.return_value = LLMToolResponse(
            "ans", [], 5, 3,
        )
        p.send_message("s1", "q")
        tools = p._llm.chat_with_tools.call_args.args[1]
        self.assertEqual(4, len(tools))

    @patch(_MCP_CLIENT)
    def test_search_in_tools_false_excludes_memory_query(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, search_in_tools=False, manual_search=False)
        mock_cls.return_value = MagicMock()
        p._llm.chat_with_tools.return_value = LLMToolResponse("ans", [], 5, 3)
        p.send_message("s1", "q")
        tools = p._llm.chat_with_tools.call_args.args[1]
        names = [t["function"]["name"] for t in tools]
        self.assertNotIn("memory_query", names)
        self.assertEqual({"read", "list", "glob"}, set(names))

    def test_tool_calling_false_yields_empty_tools_and_single_call(self) -> None:
        p = _make_plugin(tool_calling=False, manual_search=False)
        p._llm.chat.return_value = LLMResponse("answer", 5, 3, 0.1)
        p.send_message("s1", "q")
        p._llm.chat_with_tools.assert_not_called()
        p._llm.chat.assert_called_once()
        system_prompt = p._llm.chat.call_args.args[0][0]["content"]
        self.assertEqual(_NO_TOOLS_SYSTEM_PROMPT, system_prompt)

    @patch(_MCP_CLIENT)
    def test_disabled_read_mode_rejects_hallucinated_read_call(
        self,
        mock_cls: MagicMock,
    ) -> None:
        p = _make_plugin(
            tool_calling=True,
            manual_search=False,
            mcp_read_mode="disabled",
        )
        mock_mcp = MagicMock()
        mock_cls.return_value = mock_mcp
        p._llm.chat_with_tools.side_effect = [
            LLMToolResponse(
                "",
                [_tool_call(
                    "read",
                    {"uris": ["echo://x/current/messages.jsonl"]},
                )],
                5,
                2,
            ),
            LLMToolResponse("answer", [], 5, 2),
        ]

        response = p.send_message("s1", "q")

        first_tools = p._llm.chat_with_tools.call_args_list[0].args[1]
        self.assertNotIn(
            "read",
            {tool["function"]["name"] for tool in first_tools},
        )
        mock_mcp.call_tool.assert_not_called()
        audit = response.extra["trace"]["tool_audit"]
        self.assertEqual("tool_not_exposed", audit["tool_calls"][0]["error"])

    # -- Phase C: tool-call loop ---------------------------------------------

    @patch(_MCP_CLIENT)
    def test_immediate_answer_no_tool_calls(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=False)
        mock_mcp = MagicMock()
        mock_cls.return_value = mock_mcp
        p._llm.chat_with_tools.return_value = LLMToolResponse(
            "direct answer", [], 5, 3,
        )
        resp = p.send_message("s1", "q")
        self.assertEqual("direct answer", resp.text)
        self.assertEqual(1, resp.extra["iterations"])
        self.assertEqual(0, resp.extra["tool_call_count"])
        mock_mcp.call_tool.assert_not_called()
        mock_mcp.close.assert_called_once()

    @patch(_MCP_CLIENT)
    def test_tool_call_loop_then_final_answer(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=False)
        mock_mcp = MagicMock()
        mock_cls.return_value = mock_mcp
        mock_mcp.call_tool.return_value = "search results"
        p._llm.chat_with_tools.side_effect = [
            LLMToolResponse("", [_tool_call("memory_query", {"query": "hi"})], 10, 5),
            LLMToolResponse("The answer is 42.", [], 20, 8),
        ]
        resp = p.send_message("s1", "What is the answer?")

        self.assertEqual("The answer is 42.", resp.text)
        self.assertEqual(2, resp.extra["iterations"])
        self.assertEqual(1, resp.extra["tool_call_count"])
        self.assertEqual(30, resp.prompt_tokens)
        self.assertEqual(13, resp.completion_tokens)
        self.assertIsNone(resp.error)
        # memory_query tool result captured in memory_items
        self.assertEqual(1, len(resp.memory_items))
        self.assertEqual("memory_query", resp.memory_items[0]["tool"])
        self.assertEqual("hi", resp.memory_items[0]["query"])
        self.assertEqual("search results", resp.memory_items[0]["result"])
        mock_mcp.close.assert_called_once()

    @patch(_MCP_CLIENT)
    def test_memory_query_result_truncated_to_2000(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=False)
        mock_mcp = MagicMock()
        mock_cls.return_value = mock_mcp
        mock_mcp.call_tool.return_value = "x" * 3000
        p._llm.chat_with_tools.side_effect = [
            LLMToolResponse("", [_tool_call("memory_query", {"query": "t"})], 5, 2),
            LLMToolResponse("ans", [], 10, 3),
        ]
        resp = p.send_message("s1", "q")
        self.assertEqual(2000, len(resp.memory_items[0]["result"]))

    @patch(_MCP_CLIENT)
    def test_non_memory_query_tool_not_added_to_memory_items(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=False)
        mock_mcp = MagicMock()
        mock_cls.return_value = mock_mcp
        mock_mcp.call_tool.return_value = "content"
        p._llm.chat_with_tools.side_effect = [
            LLMToolResponse("", [_tool_call("read", {"uris": "echo://x"})], 5, 2),
            LLMToolResponse("ans", [], 10, 3),
        ]
        resp = p.send_message("s1", "q")
        self.assertEqual(0, len(resp.memory_items))
        self.assertEqual(1, resp.extra["tool_call_count"])

    @patch(_MCP_CLIENT)
    def test_invalid_tool_arguments_handled_as_empty(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=False)
        mock_mcp = MagicMock()
        mock_cls.return_value = mock_mcp
        mock_mcp.call_tool.return_value = "result"
        bad_call = {
            "id": "c1", "type": "function",
            "function": {"name": "read", "arguments": "not json"},
        }
        p._llm.chat_with_tools.side_effect = [
            LLMToolResponse("", [bad_call], 5, 2),
            LLMToolResponse("ans", [], 10, 3),
        ]
        p.send_message("s1", "q")
        mock_mcp.call_tool.assert_called_once()
        self.assertEqual({}, mock_mcp.call_tool.call_args.args[1])

    @patch(_MCP_CLIENT)
    def test_tool_call_error_does_not_increment_count(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=False)
        mock_mcp = MagicMock()
        mock_cls.return_value = mock_mcp
        mock_mcp.call_tool.side_effect = RuntimeError("tool boom")
        p._llm.chat_with_tools.side_effect = [
            LLMToolResponse("", [_tool_call("read", {"uris": "echo://x"})], 5, 2),
            LLMToolResponse("ans", [], 10, 3),
        ]
        resp = p.send_message("s1", "q")
        self.assertEqual("ans", resp.text)
        self.assertEqual(0, resp.extra["tool_call_count"])

    @patch(_MCP_CLIENT)
    def test_max_iterations_forces_no_tool_answer(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=False, max_iterations=2)
        mock_mcp = MagicMock()
        mock_cls.return_value = mock_mcp
        mock_mcp.call_tool.return_value = "content"
        looping = LLMToolResponse("", [_tool_call("read", {"uris": "echo://x"})], 5, 2)
        final = LLMToolResponse("final answer", [], 30, 10)
        p._llm.chat_with_tools.side_effect = [looping, looping, final]

        resp = p.send_message("s1", "q")
        self.assertEqual("final answer", resp.text)
        self.assertEqual(2, resp.extra["iterations"])
        self.assertEqual(2, resp.extra["tool_call_count"])
        # Third call forced no tools.
        forced_tools = p._llm.chat_with_tools.call_args_list[2].args[1]
        self.assertEqual([], forced_tools)

    @patch(_MCP_CLIENT)
    def test_llm_error_in_tool_loop_breaks_with_error(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=False)
        mock_cls.return_value = MagicMock()
        p._llm.chat_with_tools.return_value = LLMToolResponse(
            error="LLM 500", prompt_tokens=5, completion_tokens=0,
        )
        resp = p.send_message("s1", "q")
        self.assertEqual("", resp.text)
        self.assertEqual("LLM 500", resp.error)
        self.assertEqual(1, resp.extra["iterations"])

    @patch(_MCP_CLIENT)
    def test_mcp_init_failure_falls_back_to_single_llm_call(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=False)
        mock_mcp = MagicMock()
        mock_mcp.initialize.side_effect = RuntimeError("init failed")
        mock_cls.return_value = mock_mcp
        p._llm.chat.return_value = LLMResponse("fallback answer", 10, 5, 0.1)

        resp = p.send_message("s1", "q")
        self.assertEqual("fallback answer", resp.text)
        self.assertEqual(0, resp.extra["iterations"])
        self.assertEqual(0, resp.extra["tool_call_count"])
        p._llm.chat_with_tools.assert_not_called()
        p._llm.chat.assert_called_once()
        mock_mcp.close.assert_not_called()

    @patch(_MCP_CLIENT)
    def test_mcp_init_success_uses_chat_with_tools_not_chat(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=False)
        mock_cls.return_value = MagicMock()
        p._llm.chat_with_tools.return_value = LLMToolResponse("ans", [], 5, 3)
        p.send_message("s1", "q")
        p._llm.chat_with_tools.assert_called_once()
        p._llm.chat.assert_not_called()

    # -- message building ----------------------------------------------------

    def test_no_tool_calling_uses_no_tools_prompt(self) -> None:
        p = _make_plugin(tool_calling=False, manual_search=False)
        p._llm.chat.return_value = LLMResponse("ans", 5, 3, 0.1)
        p.send_message("s1", "q")
        messages = p._llm.chat.call_args.args[0]
        self.assertEqual(_NO_TOOLS_SYSTEM_PROMPT, messages[0]["content"])

    def test_question_time_injected_into_user_message(self) -> None:
        p = _make_plugin(tool_calling=False, manual_search=False)
        p._llm.chat.return_value = LLMResponse("ans", 5, 3, 0.1)
        p.send_message("s1", "q", extra={"question_time": "2026-01-01"})
        user_content = p._llm.chat.call_args.args[0][1]["content"]
        self.assertIn("Current date: 2026-01-01", user_content)

    def test_no_question_time_omits_time_context(self) -> None:
        p = _make_plugin(tool_calling=False, manual_search=False)
        p._llm.chat.return_value = LLMResponse("ans", 5, 3, 0.1)
        p.send_message("s1", "q", extra={"question_time": ""})
        user_content = p._llm.chat.call_args.args[0][1]["content"]
        self.assertNotIn("Current date", user_content)

    def test_question_time_from_whitespace_only_omitted(self) -> None:
        p = _make_plugin(tool_calling=False, manual_search=False)
        p._llm.chat.return_value = LLMResponse("ans", 5, 3, 0.1)
        p.send_message("s1", "q", extra={"question_time": "   "})
        user_content = p._llm.chat.call_args.args[0][1]["content"]
        self.assertNotIn("Current date", user_content)

    # -- edge cases ----------------------------------------------------------

    def test_extra_none_is_tolerated(self) -> None:
        p = _make_plugin(tool_calling=False, manual_search=False)
        p._llm.chat.return_value = LLMResponse("ans", 5, 3, 0.1)
        resp = p.send_message("s1", "q", extra=None)
        self.assertEqual("ans", resp.text)

    def test_empty_message_is_accepted(self) -> None:
        p = _make_plugin(tool_calling=False, manual_search=False)
        p._llm.chat.return_value = LLMResponse("ans", 5, 3, 0.1)
        resp = p.send_message("s1", "")
        self.assertEqual("ans", resp.text)

    def test_zero_timeout_disables_deadline(self) -> None:
        p = _make_plugin(tool_calling=False, manual_search=False, question_timeout_s=0.0)
        p._llm.chat.return_value = LLMResponse("ans", 5, 3, 0.1)
        p.send_message("s1", "q")
        # With no deadline, remaining() returns None -> timeout_s=None.
        self.assertIsNone(p._llm.chat.call_args.kwargs["timeout_s"])

    def test_response_extra_fields_present(self) -> None:
        p = _make_plugin(tool_calling=False, manual_search=False)
        p._llm.chat.return_value = LLMResponse("ans", 5, 3, 0.1)
        resp = p.send_message("s1", "q")
        self.assertIn("tool_call_count", resp.extra)
        self.assertIn("iterations", resp.extra)
        self.assertEqual("echomem_mcp", resp.extra["qa_profile"])
        self.assertIn("elapsed_s", resp.extra)
        self.assertIn("retrieval_latency_s", resp.extra)
        self.assertIn("llm_latency_s", resp.extra)
        self.assertEqual(0, resp.extra["tool_call_count"])
        self.assertEqual(0, resp.extra["iterations"])

    @patch(_MCP_CLIENT)
    def test_combined_manual_search_and_tool_calling(self, mock_cls: MagicMock) -> None:
        p = _make_plugin(tool_calling=True, manual_search=True)
        mock_mcp = MagicMock()
        mock_cls.return_value = mock_mcp
        mock_mcp.call_tool.side_effect = [
            json.dumps({
                "items": [
                    {"uri": "echo://pre", "score": 0.8, "content": "prefetched"},
                ]
            }),
            "tool result",
        ]
        p._llm.chat_with_tools.side_effect = [
            LLMToolResponse("", [_tool_call("memory_query", {"query": "x"})], 5, 2),
            LLMToolResponse("ans", [], 10, 3),
        ]
        resp = p.send_message("s1", "q")
        # Both pre-fetch and tool-call contribute to memory_items.
        self.assertEqual(2, len(resp.memory_items))
        self.assertEqual("echo://pre", resp.memory_items[0]["uri"])
        self.assertEqual("memory_query", resp.memory_items[1]["tool"])

    # -- getlog ---------------------------------------------------------

    def test_getlog_fetches_tenant_logs(self):
        p = _make_plugin()
        p.memory_client.account = "tenant-x"
        p.memory_client.user_id = "user-x"
        p.memory_client.fetch_logs = MagicMock(
            return_value={"items": [{"ts": "a"}], "page": {}},
        )
        result = p.getlog()
        p.memory_client.fetch_logs.assert_called_once_with(
            tenant_id="tenant-x",
            user_id="user-x",
        )
        data = json.loads(result)
        self.assertEqual([{"ts": "a"}], data["items"])

    def test_getlog_returns_error_on_failure(self):
        p = _make_plugin()
        p.memory_client.account = "tenant-x"
        p.memory_client.user_id = "user-x"
        p.memory_client.fetch_logs = MagicMock(side_effect=RuntimeError("boom"))
        result = p.getlog()
        data = json.loads(result)
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
