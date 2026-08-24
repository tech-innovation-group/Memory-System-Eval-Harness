"""Comprehensive unit tests for the VikingBot plugin.

Covers every functional point in plugin.py, runtime.py, tools.py,
prompting.py, and answers.py.  These tests complement
tests/test_vikingbot_qa.py by focusing on plugin lifecycle, argument
parsing, _AuditedMemoryClient, chat_with_tools HTTP transport, and
additional edge cases for every module.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backends.memory_types import CommitResult, SearchResult
from benchmarks.locomo.profiles import (
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
    VIKINGBOAT_0411_PROFILE,
    VIKINGBOAT_0411_SETTINGS,
    profile_settings,
)
from plugins.base import AgentDescriptor, AgentResponse
from plugins.vikingbot.answers import sanitize_final_answer_text
from plugins.vikingbot.plugin import VikingBotPlugin
from plugins.vikingbot.prompting import (
    DEFAULT_BOOTSTRAP_FILES,
    build_messages,
    build_question_prompt,
    build_system_prompt,
    format_memory,
    load_bootstrap,
)
from plugins.vikingbot.runtime import (
    _append_tool_audit,
    _AuditedMemoryClient,
    _retrieval_rows,
    _unique_retrieval_items,
    answer_one_vikingbot_question,
    chat_with_tools,
)
from plugins.vikingbot.tools import (
    MEMORY_GLOB_TOOL,
    MEMORY_GREP_TOOL,
    MEMORY_LIST_TOOL,
    MEMORY_READ_TOOL,
    MEMORY_SEARCH_TOOL,
    SESSIONS_ROOT,
    SESSION_FILE,
    _engine_uri,
    _grep_memory,
    _matching_session_files,
    _search_payload_items,
    _session_candidates_message,
    _session_uri_tail,
    execute_tool,
    search_payload,
    tool_definitions,
)
from shared.qa import QAResult


# ------------------------------------------------------------------ #
#  Test helpers                                                       #
# ------------------------------------------------------------------ #

class _FakeLLM:
    base_url = "https://example.test/v1"
    api_key = "test-key"
    model = "test-model"
    temperature = 0.7
    max_tokens = 1024
    timeout_s = 120.0
    max_retries = 5


class _FakeEchoMem:
    def __init__(self):
        self.queries: list[str] = []
        self.read_uris: list[str] = []

    def search(self, query, **kwargs):
        self.queries.append(query)
        return [
            SearchResult(
                uri="echo://account/sessions/session-1",
                score=1.0,
                content="Jon lost his job on 19 January 2023.",
                memory_type="atomic",
            )
        ]

    def fs_read(self, uri, **kwargs):
        self.read_uris.append(uri)
        return "Jon lost his job on 19 January 2023."

    def fs_list(self, uri, **kwargs):
        return []

    def fs_glob(self, pattern, **kwargs):
        return []


class _FakeHTTPResponse:
    """Context manager mimicking urllib.request.urlopen return value."""

    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


def _llm_response_json(
    content: str = "answer",
    tool_calls: list | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> str:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return json.dumps({
        "id": "resp-1",
        "model": "served-model",
        "system_fingerprint": "fp-abc",
        "created": 1700000000,
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    })


def _make_qa_result(**overrides) -> QAResult:
    defaults = dict(
        question_id="q1",
        question="What?",
        answer="42",
        response="The answer is 42",
        retrieval_items=[{"uri": "echo://mem/1", "score": 0.9, "content": "fact"}],
        prompt_tokens=100,
        completion_tokens=20,
        elapsed_s=0.5,
        tool_call_count=1,
        iterations=2,
        qa_profile="vikingboat0411",
        retrieval_latency_s=0.1,
        llm_latency_s=0.3,
        trace={"key": "value"},
    )
    defaults.update(overrides)
    return QAResult(**defaults)


def _make_plugin(**overrides) -> VikingBotPlugin:
    """Create a VikingBotPlugin with pre-populated instance attributes."""
    plugin = VikingBotPlugin()
    plugin._memory_backend = "echomem"
    plugin._commit_timeout_s = 0.0
    plugin._commit_poll_interval_s = 2.0
    plugin.memory_client = MagicMock()
    plugin._llm = MagicMock()
    plugin._qa_profile = VIKINGBOAT_0411_PROFILE
    plugin._tools_enabled = True
    plugin._top_k = 25
    plugin._tool_search_limit = None
    plugin._user_memory_budget_chars = None
    plugin._agent_memory_budget_chars = None
    plugin._max_iterations = None
    plugin._initial_min_score = None
    plugin._tool_min_score = None
    plugin._tool_search_pool_multiplier = None
    plugin._tool_set = None
    plugin._vikingbot_workspace = ""
    plugin._question_timeout_s = 600.0
    plugin._answer_temperature = None
    plugin._omit_answer_temperature = None
    plugin._initial_retrieval_query_mode = None
    plugin._tool_query_dedup_scope = None
    plugin._retrieval_uri_dedup = None
    plugin._search_tool_target_uri_schema = None
    plugin._documents_mode = False
    plugin.path_title_map = {}
    plugin._docs_memory_budget_chars = 8000
    for key, val in overrides.items():
        setattr(plugin, key, val)
    return plugin


# ------------------------------------------------------------------ #
#  Plugin descriptor                                                  #
# ------------------------------------------------------------------ #

class VikingBotDescriptorTests(unittest.TestCase):
    def test_descriptor_id(self):
        self.assertEqual("vikingbot", VikingBotPlugin.descriptor.id)

    def test_descriptor_name(self):
        self.assertEqual("VikingBot", VikingBotPlugin.descriptor.name)

    def test_descriptor_description(self):
        self.assertIn("VikingBot", VikingBotPlugin.descriptor.description)

    def test_descriptor_capabilities(self):
        expected = (
            "memory_search",
            "memory_read_many",
            "memory_list",
            "memory_grep",
            "memory_glob",
            "openai_tool_loop",
            "workspace_bootstrap",
        )
        self.assertEqual(expected, VikingBotPlugin.descriptor.capabilities)

    def test_descriptor_is_agent_descriptor(self):
        self.assertIsInstance(VikingBotPlugin.descriptor, AgentDescriptor)


# ------------------------------------------------------------------ #
#  Plugin add_arguments                                               #
# ------------------------------------------------------------------ #

class VikingBotAddArgumentsTests(unittest.TestCase):
    def _parse(self, *argv):
        parser = argparse.ArgumentParser()
        VikingBotPlugin.add_arguments(parser)
        return parser.parse_args(argv)

    def test_adds_llm_args(self):
        ns = self._parse()
        self.assertEqual("doubao-seed-2.0-pro", ns.llm_model)
        self.assertEqual(2048, ns.llm_max_tokens)

    def test_adds_qa_args(self):
        ns = self._parse()
        self.assertEqual(10, ns.top_k)
        self.assertEqual(8000, ns.memory_budget_chars)

    def test_adds_memory_backend_choice(self):
        ns = self._parse()
        self.assertEqual("echomem", ns.memory_backend)

    def test_memory_backend_choices(self):
        ns = self._parse("--memory-backend", "openviking")
        self.assertEqual("openviking", ns.memory_backend)

    def test_vikingbot_specific_defaults_none(self):
        ns = self._parse()
        for attr in (
            "tool_search_limit",
            "user_memory_budget_chars",
            "agent_memory_budget_chars",
            "max_iterations",
            "initial_min_score",
            "tool_min_score",
            "tool_search_pool_multiplier",
            "tool_set",
        ):
            with self.subTest(attr=attr):
                self.assertIsNone(getattr(ns, attr))

    def test_tools_defaults_true(self):
        ns = self._parse()
        self.assertTrue(ns.tools)

    def test_no_tools_flag(self):
        ns = self._parse("--no-tools")
        self.assertFalse(ns.tools)

    def test_tool_set_choices(self):
        for choice in ("search_read", "vikingbot_native_safe", "vikingbot_echo_native"):
            with self.subTest(choice=choice):
                ns = self._parse("--tool-set", choice)
                self.assertEqual(choice, ns.tool_set)

    def test_vikingbot_workspace_default(self):
        ns = self._parse()
        self.assertTrue(ns.vikingbot_workspace)

    def test_vikingbot_workspace_override(self):
        ns = self._parse("--vikingbot-workspace", "/tmp/ws")
        self.assertEqual("/tmp/ws", ns.vikingbot_workspace)


# ------------------------------------------------------------------ #
#  Plugin setup                                                       #
# ------------------------------------------------------------------ #

class VikingBotSetupTests(unittest.TestCase):
    def test_creates_echomem_client(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient") as mock_ec,
            patch("plugins.vikingbot.plugin.OpenVikingClient") as mock_ov,
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({"memory_backend": "echomem", "llm_base_url": "u", "llm_api_key": "k"})
            mock_ec.assert_called_once()
            mock_ov.assert_not_called()

    def test_creates_openviking_client(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient") as mock_ec,
            patch("plugins.vikingbot.plugin.OpenVikingClient") as mock_ov,
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({"memory_backend": "openviking", "llm_base_url": "u", "llm_api_key": "k"})
            mock_ov.assert_called_once()
            mock_ec.assert_not_called()

    def test_openviking_client_default_url(self):
        with (
            patch("plugins.vikingbot.plugin.OpenVikingClient") as mock_ov,
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({"memory_backend": "openviking", "llm_base_url": "u", "llm_api_key": "k"})
            kwargs = mock_ov.call_args.kwargs
            self.assertEqual("http://127.0.0.1:19080", kwargs["base_url"])

    def test_echomem_client_default_url(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient") as mock_ec,
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({"memory_backend": "echomem", "llm_base_url": "u", "llm_api_key": "k"})
            kwargs = mock_ec.call_args.kwargs
            self.assertEqual("http://127.0.0.1:8010", kwargs["base_url"])

    def test_identity_isolation_called(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient") as mock_ec,
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({
                "memory_backend": "echomem",
                "benchmark_name": "locomo",
                "run_id": "run1",
                "llm_base_url": "u",
                "llm_api_key": "k",
            })
            mock_ec.return_value.provision_isolated_identity.assert_called_once_with(
                "eval-locomo-run1",
            )

    def test_identity_isolation_not_called_with_resume_qa(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient") as mock_ec,
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({
                "memory_backend": "echomem",
                "benchmark_name": "locomo",
                "run_id": "run1",
                "resume_qa": "run1",
                "llm_base_url": "u",
                "llm_api_key": "k",
            })
            mock_ec.return_value.provision_isolated_identity.assert_not_called()

    def test_identity_isolation_not_called_without_benchmark(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient") as mock_ec,
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({"memory_backend": "echomem", "llm_base_url": "u", "llm_api_key": "k"})
            mock_ec.return_value.provision_isolated_identity.assert_not_called()

    def test_identity_label_truncated_to_120_chars(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient") as mock_ec,
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            long_run_id = "x" * 200
            plugin.setup({
                "memory_backend": "echomem",
                "benchmark_name": "locomo",
                "run_id": long_run_id,
                "llm_base_url": "u",
                "llm_api_key": "k",
            })
            label = mock_ec.return_value.provision_isolated_identity.call_args.args[0]
            self.assertLessEqual(len(label), 120)

    def test_creates_llm_client(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient") as mock_llm,
        ):
            plugin = VikingBotPlugin()
            plugin.setup({
                "llm_base_url": "http://llm",
                "llm_api_key": "secret",
                "llm_model": "my-model",
                "llm_temperature": 0.3,
                "llm_max_tokens": 512,
                "llm_timeout_s": 30.0,
                "llm_retries": 7,
            })
            mock_llm.assert_called_once()
            kwargs = mock_llm.call_args.kwargs
            self.assertEqual("http://llm", kwargs["base_url"])
            self.assertEqual("secret", kwargs["api_key"])
            self.assertEqual("my-model", kwargs["model"])
            self.assertEqual(0.3, kwargs["temperature"])
            self.assertEqual(512, kwargs["max_tokens"])
            self.assertEqual(30.0, kwargs["timeout_s"])
            self.assertEqual(7, kwargs["max_retries"])

    def test_profile_auto_select_tools_true(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({"llm_base_url": "u", "llm_api_key": "k", "tools": True})
            self.assertEqual(VIKINGBOAT_0411_PROFILE, plugin._qa_profile)

    def test_profile_auto_select_tools_false(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({"llm_base_url": "u", "llm_api_key": "k", "tools": False})
            self.assertEqual(VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE, plugin._qa_profile)

    def test_resolve_cli_overrides_profile_default(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({
                "llm_base_url": "u",
                "llm_api_key": "k",
                "tool_search_limit": 10,
            })
            self.assertEqual(10, plugin._tool_search_limit)

    def test_resolve_uses_profile_default_when_cli_none(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({"llm_base_url": "u", "llm_api_key": "k"})
            defaults = profile_settings(VIKINGBOAT_0411_PROFILE)
            self.assertEqual(defaults["tool_search_limit"], plugin._tool_search_limit)
            self.assertEqual(defaults["tool_set"], plugin._tool_set)
            self.assertEqual(defaults["max_iterations"], plugin._max_iterations)

    def test_resolve_returns_none_for_unknown_profile(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({
                "llm_base_url": "u",
                "llm_api_key": "k",
                "qa_profile": "custom-unknown",
            })
            self.assertIsNone(plugin._tool_search_limit)
            self.assertIsNone(plugin._tool_set)
            self.assertIsNone(plugin._max_iterations)

    def test_top_k_defaults_to_10_when_unresolved(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({
                "llm_base_url": "u",
                "llm_api_key": "k",
                "qa_profile": "custom-unknown",
            })
            self.assertEqual(10, plugin._top_k)

    def test_question_timeout_s_resolution(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({"llm_base_url": "u", "llm_api_key": "k"})
            defaults = profile_settings(VIKINGBOAT_0411_PROFILE)
            self.assertEqual(defaults["question_timeout_s"], plugin._question_timeout_s)

    def test_question_timeout_s_cli_override(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({
                "llm_base_url": "u",
                "llm_api_key": "k",
                "question_timeout_s": 42.0,
            })
            self.assertEqual(42.0, plugin._question_timeout_s)

    def test_question_timeout_s_default_120_for_unknown_profile(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({
                "llm_base_url": "u",
                "llm_api_key": "k",
                "qa_profile": "custom-unknown",
            })
            self.assertEqual(120.0, plugin._question_timeout_s)

    def test_commit_timeout_config(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({
                "llm_base_url": "u",
                "llm_api_key": "k",
                "commit_timeout_s": 30.0,
                "commit_poll_interval_s": 1.0,
            })
            self.assertEqual(30.0, plugin._commit_timeout_s)
            self.assertEqual(1.0, plugin._commit_poll_interval_s)

    def test_vikingbot_workspace_from_config(self):
        with (
            patch("plugins.vikingbot.plugin.EchoMemClient"),
            patch("plugins.vikingbot.plugin.OpenVikingClient"),
            patch("plugins.vikingbot.plugin.LLMClient"),
        ):
            plugin = VikingBotPlugin()
            plugin.setup({
                "llm_base_url": "u",
                "llm_api_key": "k",
                "vikingbot_workspace": "/custom/ws",
            })
            self.assertEqual("/custom/ws", plugin._vikingbot_workspace)


# ------------------------------------------------------------------ #
#  Plugin send_message                                                #
# ------------------------------------------------------------------ #

class VikingBotSendMessageTests(unittest.TestCase):
    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_returns_agent_response_with_correct_fields(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin()
        resp = plugin.send_message("s1", "What?", extra={"question_id": "q1", "answer": "42"})
        self.assertIsInstance(resp, AgentResponse)
        self.assertEqual("The answer is 42", resp.text)
        self.assertEqual(100, resp.prompt_tokens)
        self.assertEqual(20, resp.completion_tokens)
        self.assertEqual(0.9, resp.memory_items[0]["score"])

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_extra_fields_passed_to_runtime(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin()
        plugin.send_message("s1", "msg", extra={
            "question_id": "q42",
            "question": "What is 2+2?",
            "answer": "4",
            "question_time": "2024-01-01",
            "sample_id": "s-1",
            "category": "math",
        })
        kwargs = mock_answer.call_args.kwargs
        self.assertEqual("q42", kwargs["question_id"])
        self.assertEqual("What is 2+2?", kwargs["question"])
        self.assertEqual("4", kwargs["answer"])
        self.assertEqual("2024-01-01", kwargs["question_time"])
        self.assertEqual("s-1", kwargs["sample_id"])
        self.assertEqual("math", kwargs["category"])

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_none_extra_uses_defaults(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin()
        plugin.send_message("s1", "hello", extra=None)
        kwargs = mock_answer.call_args.kwargs
        self.assertEqual("", kwargs["question_id"])
        self.assertEqual("hello", kwargs["question"])
        self.assertEqual("", kwargs["answer"])

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_empty_extra_uses_message_as_question(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin()
        plugin.send_message("s1", "my question", extra={})
        kwargs = mock_answer.call_args.kwargs
        self.assertEqual("my question", kwargs["question"])

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_none_config_fields_filtered_out(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin()
        plugin.send_message("s1", "msg")
        kwargs = mock_answer.call_args.kwargs
        for key in (
            "tool_search_limit",
            "user_memory_budget_chars",
            "agent_memory_budget_chars",
            "max_iterations",
            "initial_min_score",
            "tool_min_score",
            "tool_set",
            "answer_temperature",
            "omit_answer_temperature",
        ):
            with self.subTest(key=key):
                self.assertNotIn(key, kwargs)

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_non_none_config_fields_passed(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin(
            _tool_search_limit=15,
            _max_iterations=10,
            _tool_set="search_read",
        )
        plugin.send_message("s1", "msg")
        kwargs = mock_answer.call_args.kwargs
        self.assertEqual(15, kwargs["tool_search_limit"])
        self.assertEqual(10, kwargs["max_iterations"])
        self.assertEqual("search_read", kwargs["tool_set"])

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_system_prompt_append_passed_from_extra(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin()
        plugin.send_message("s1", "msg", extra={
            "system_prompt_append": "extra instructions",
            "system_prompt_append_sha256": "abc123",
            "system_prompt_append_source": "test",
        })
        kwargs = mock_answer.call_args.kwargs
        self.assertIn("extra instructions", kwargs["system_prompt_append"])
        self.assertIn(
            "answer with only the exact answer",
            kwargs["system_prompt_append"],
        )
        self.assertEqual("abc123", kwargs["system_prompt_append_sha256"])
        self.assertEqual("test", kwargs["system_prompt_append_source"])

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_llm_error_propagates_to_response_error(self, mock_answer):
        mock_answer.return_value = _make_qa_result(llm_error="model failed")
        plugin = _make_plugin()
        resp = plugin.send_message("s1", "msg")
        self.assertEqual("model failed", resp.error)

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_empty_llm_error_becomes_none(self, mock_answer):
        mock_answer.return_value = _make_qa_result(llm_error="")
        plugin = _make_plugin()
        resp = plugin.send_message("s1", "msg")
        self.assertIsNone(resp.error)

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_extra_dict_contains_runtime_metrics(self, mock_answer):
        mock_answer.return_value = _make_qa_result(
            retrieval_latency_s=0.15,
            llm_latency_s=0.35,
            retrieval_error="timeout",
        )
        plugin = _make_plugin()
        resp = plugin.send_message("s1", "msg")
        self.assertEqual(0.5, resp.extra["elapsed_s"])
        self.assertEqual(1, resp.extra["tool_call_count"])
        self.assertEqual(2, resp.extra["iterations"])
        self.assertEqual("vikingboat0411", resp.extra["qa_profile"])
        self.assertEqual(0.15, resp.extra["retrieval_latency_s"])
        self.assertEqual(0.35, resp.extra["llm_latency_s"])
        self.assertEqual("timeout", resp.extra["retrieval_error"])
        self.assertEqual({"key": "value"}, resp.extra["trace"])

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_empty_message(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin()
        plugin.send_message("s1", "")
        kwargs = mock_answer.call_args.kwargs
        self.assertEqual("", kwargs["question"])

    def test_documents_mode_builds_full_trace(self):
        class _FakeLLMWithChat:
            base_url = "https://example.test/v1"
            model = "test-model"
            max_tokens = 1024
            timeout_s = 120.0

            def chat(self, messages, timeout_s=None):
                return MagicMock(
                    content="Paris",
                    prompt_tokens=50,
                    completion_tokens=5,
                    error=None,
                )

        plugin = _make_plugin(
            _documents_mode=True,
            _tools_enabled=False,
            path_title_map={"hotpotqa/D1-abc12345": "D1"},
            _docs_memory_budget_chars=8000,
            _top_k=10,
            _question_timeout_s=600.0,
        )
        plugin.memory_client.search_resources = MagicMock(return_value=[{
            "path": "user/hotpotqa/D1-abc12345",
            "source_uri": "viking://user/resources/hotpotqa/D1-abc12345",
            "score": 0.9,
            "text": "The Eiffel Tower is in Paris.",
        }])
        plugin._llm = _FakeLLMWithChat()
        resp = plugin.send_message("s1", "Where is the tower?", extra={
            "question_id": "q1",
            "question": "Where is the tower?",
            "answer": "Paris",
            "question_time": "2024-01-01",
            "sample_id": "s1",
            "category": "comparison",
        })
        trace = resp.extra["trace"]
        self.assertEqual("vikingbot_docs", trace["qa_profile"])
        self.assertEqual("q1", trace["question_id"])
        self.assertEqual("Paris", trace["gold_answer"])
        self.assertEqual("Where is the tower?", trace["question"])
        # 检索链完整记录：检索到的证据带 title/uri/score/content
        items = trace["initial_retrieval"]["items"]
        self.assertEqual(1, len(items))
        self.assertEqual("D1", items[0]["hotpotqa_title"])
        self.assertEqual(0.9, items[0]["score"])
        # 发给 LLM 的完整 prompt（system + user）与模型身份
        self.assertEqual(2, len(trace["initial_messages"]))
        self.assertEqual("test-model", trace["model_request"]["model"])
        self.assertEqual("Paris", trace["final_response"])
        # documents 模式无工具循环：iterations 与 tool_audit 为空
        self.assertEqual([], trace["iterations"])
        self.assertEqual([], trace["tool_audit"]["tools_used"])
        self.assertEqual([], trace["tool_audit"]["tool_calls"])

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_documents_mode_with_tools_routes_to_runtime(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin(
            _documents_mode=True,
            _tools_enabled=True,
            path_title_map={"hotpotqa/D1": "D1"},
            _docs_memory_budget_chars=8000,
            _top_k=10,
        )
        resp = plugin.send_message("s1", "Q", extra={
            "question_id": "q1",
            "question": "Q",
            "answer": "A",
            "question_time": "2024-01-01",
        })
        kwargs = mock_answer.call_args.kwargs
        self.assertTrue(kwargs["search_resources_mode"])
        self.assertEqual({"hotpotqa/D1": "D1"}, kwargs["path_title_map"])
        self.assertTrue(kwargs["tools_enabled"])
        self.assertEqual(8000, kwargs["user_memory_budget_chars"])
        self.assertEqual(8000, kwargs["agent_memory_budget_chars"])
        self.assertEqual(10, kwargs["top_k"])
        # 工具循环固定注入 concise 指令（F1/EM 评测要求简短精确回答）
        self.assertIn(
            "answer with only the exact answer",
            kwargs["system_prompt_append"],
        )
        # 响应带 runtime 指标与 trace
        self.assertEqual(1, resp.extra["tool_call_count"])
        self.assertEqual(2, resp.extra["iterations"])
        self.assertEqual({"key": "value"}, resp.extra["trace"])

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_documents_with_tools_merges_incoming_system_prompt_append(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin(
            _documents_mode=True,
            _tools_enabled=True,
            path_title_map={"hotpotqa/D1": "D1"},
            _docs_memory_budget_chars=8000,
            _top_k=10,
        )
        plugin.send_message("s1", "Q", extra={
            "question_id": "q1",
            "question": "Q",
            "answer": "A",
            "system_prompt_append": "keep this",
        })
        kwargs = mock_answer.call_args.kwargs
        self.assertIn("keep this", kwargs["system_prompt_append"])
        self.assertIn(
            "answer with only the exact answer",
            kwargs["system_prompt_append"],
        )

    @patch("plugins.vikingbot.plugin.answer_one_vikingbot_question")
    def test_benchmark_answer_append_injected_in_non_documents_mode(self, mock_answer):
        mock_answer.return_value = _make_qa_result()
        plugin = _make_plugin()
        plugin.send_message("s1", "Q", extra={
            "question_id": "q1",
            "question": "Q",
            "answer": "A",
            "system_prompt_append": "keep this",
        })
        kwargs = mock_answer.call_args.kwargs
        # 非 documents 模式同样注入 concise 指令，且保留外部 append
        self.assertIn("keep this", kwargs["system_prompt_append"])
        self.assertIn(
            "answer with only the exact answer",
            kwargs["system_prompt_append"],
        )


# ------------------------------------------------------------------ #
#  Plugin inject_memories                                             #
# ------------------------------------------------------------------ #

class VikingBotInjectMemoriesTests(unittest.TestCase):
    def test_opens_session_when_no_session_id(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session.return_value = "sess-1"
        plugin.memory_client.commit_session.return_value = "arch-1"
        plugin.memory_client.poll_commit.return_value = CommitResult(
            "sess-1", "arch-1", "completed", 1.0, 1,
        )
        result = plugin.inject_memories([], session_id="")
        plugin.memory_client.open_session.assert_called_once_with(title="inject")
        self.assertEqual("sess-1", result)

    def test_uses_existing_session_id(self):
        plugin = _make_plugin()
        plugin.memory_client.commit_session.return_value = "arch-1"
        plugin.memory_client.poll_commit.return_value = CommitResult(
            "existing", "arch-1", "completed", 1.0, 1,
        )
        result = plugin.inject_memories([], session_id="existing")
        plugin.memory_client.open_session.assert_not_called()
        self.assertEqual("existing", result)

    def test_adds_messages_with_text(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session.return_value = "s1"
        plugin.memory_client.commit_session.return_value = "a1"
        plugin.memory_client.poll_commit.return_value = CommitResult(
            "s1", "a1", "completed", 1.0, 1,
        )
        memories = [
            {"text": "memory one", "time": "2024-01-01"},
            {"text": "memory two", "time": "2024-01-02"},
            {"text": "", "time": ""},
        ]
        plugin.inject_memories(memories)
        self.assertEqual(2, plugin.memory_client.add_message.call_count)
        plugin.memory_client.add_message.assert_any_call(
            "s1", "user", "memory one", created_at="2024-01-01",
        )
        plugin.memory_client.add_message.assert_any_call(
            "s1", "user", "memory two", created_at="2024-01-02",
        )

    def test_commits_and_polls(self):
        plugin = _make_plugin(_commit_timeout_s=30.0, _commit_poll_interval_s=1.5)
        plugin.memory_client.open_session.return_value = "s1"
        plugin.memory_client.commit_session.return_value = "a1"
        plugin.memory_client.poll_commit.return_value = CommitResult(
            "s1", "a1", "completed", 2.0, 3,
        )
        plugin.inject_memories([{"text": "mem"}], session_id="")
        plugin.memory_client.commit_session.assert_called_once_with("s1")
        plugin.memory_client.poll_commit.assert_called_once_with(
            "s1", "a1", timeout_s=30.0, poll_interval_s=1.5,
        )

    def test_raises_on_non_completed_status(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session.return_value = "s1"
        plugin.memory_client.commit_session.return_value = "a1"
        plugin.memory_client.poll_commit.return_value = CommitResult(
            "s1", "a1", "failed", 1.0, 1, error="extraction error",
        )
        with self.assertRaisesRegex(RuntimeError, "memory injection failed"):
            plugin.inject_memories([{"text": "mem"}])

    def test_empty_memories_still_commits(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session.return_value = "s1"
        plugin.memory_client.commit_session.return_value = "a1"
        plugin.memory_client.poll_commit.return_value = CommitResult(
            "s1", "a1", "completed", 0.0, 0,
        )
        plugin.inject_memories([])
        plugin.memory_client.add_message.assert_not_called()
        plugin.memory_client.commit_session.assert_called_once()

    def test_memory_without_time_uses_empty_string(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session.return_value = "s1"
        plugin.memory_client.commit_session.return_value = "a1"
        plugin.memory_client.poll_commit.return_value = CommitResult(
            "s1", "a1", "completed", 0.0, 0,
        )
        plugin.inject_memories([{"text": "no time"}])
        plugin.memory_client.add_message.assert_called_once_with(
            "s1", "user", "no time", created_at="",
        )


# ------------------------------------------------------------------ #
#  Plugin create_session / qa_profile                                 #
# ------------------------------------------------------------------ #

class VikingBotCreateSessionTests(unittest.TestCase):
    def test_create_session_default_title(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session.return_value = "sess-x"
        result = plugin.create_session()
        plugin.memory_client.open_session.assert_called_once_with(title="qa")
        self.assertEqual("sess-x", result)

    def test_create_session_custom_title(self):
        plugin = _make_plugin()
        plugin.memory_client.open_session.return_value = "sess-y"
        result = plugin.create_session(title="custom")
        plugin.memory_client.open_session.assert_called_once_with(title="custom")
        self.assertEqual("sess-y", result)


class VikingBotQAProfileTests(unittest.TestCase):
    def test_qa_profile_returns_resolved_profile(self):
        plugin = _make_plugin(_qa_profile=VIKINGBOAT_0411_PROFILE)
        self.assertEqual(VIKINGBOAT_0411_PROFILE, plugin.qa_profile)

    def test_qa_profile_returns_natural_no_tools(self):
        plugin = _make_plugin(_qa_profile=VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE)
        self.assertEqual(VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE, plugin.qa_profile)


# ------------------------------------------------------------------ #
#  _AuditedMemoryClient                                               #
# ------------------------------------------------------------------ #

class AuditedMemoryClientTests(unittest.TestCase):
    def test_search_records_successful_operation(self):
        inner = MagicMock()
        inner.search.return_value = [
            SearchResult(uri="echo://m/1", score=0.9, content="c1"),
            SearchResult(uri="echo://m/2", score=0.8, content="c2"),
        ]
        audited = _AuditedMemoryClient(inner)
        results = audited.search("query", top_k=5)
        self.assertEqual(2, len(results))
        self.assertEqual(1, len(audited.operations))
        op = audited.operations[0]
        self.assertEqual("search", op["operation"])
        self.assertEqual("query", op["query"])
        self.assertEqual("ok", op["status"])
        self.assertEqual(2, op["result_count"])
        self.assertEqual(["echo://m/1", "echo://m/2"], op["result_uris"])

    def test_search_records_error_operation(self):
        inner = MagicMock()
        inner.search.side_effect = RuntimeError("backend down")
        audited = _AuditedMemoryClient(inner)
        with self.assertRaises(RuntimeError):
            audited.search("q")
        op = audited.operations[0]
        self.assertEqual("error", op["status"])
        self.assertIn("backend down", op["error"])

    def test_fs_read_records_successful_operation(self):
        inner = MagicMock()
        inner.fs_read.return_value = "content here"
        audited = _AuditedMemoryClient(inner)
        result = audited.fs_read("echo://m/1")
        self.assertEqual("content here", result)
        op = audited.operations[0]
        self.assertEqual("fs_read", op["operation"])
        self.assertEqual("echo://m/1", op["uri"])
        self.assertEqual("ok", op["status"])
        self.assertEqual(12, op["content_chars"])
        self.assertFalse(op["empty"])

    def test_fs_read_records_empty_content(self):
        inner = MagicMock()
        inner.fs_read.return_value = ""
        audited = _AuditedMemoryClient(inner)
        audited.fs_read("echo://m/1")
        op = audited.operations[0]
        self.assertTrue(op["empty"])
        self.assertEqual(0, op["content_chars"])

    def test_fs_read_records_error(self):
        inner = MagicMock()
        inner.fs_read.side_effect = OSError("not found")
        audited = _AuditedMemoryClient(inner)
        with self.assertRaises(OSError):
            audited.fs_read("echo://m/1")
        op = audited.operations[0]
        self.assertEqual("error", op["status"])

    def test_fs_list_records_operation_with_entries(self):
        inner = MagicMock()
        inner.fs_list.return_value = [
            {"uri": "echo://m/1", "name": "f1", "isDir": False, "size": 10},
            {"uri": "echo://m/2", "name": "d1", "kind": "directory", "size": 0},
        ]
        audited = _AuditedMemoryClient(inner)
        audited.fs_list("echo://m/", recursive=True)
        op = audited.operations[0]
        self.assertEqual("fs_list", op["operation"])
        self.assertEqual("ok", op["status"])
        self.assertEqual(2, op["entry_count"])
        rows = op["entries"]
        self.assertFalse(rows[0]["is_dir"])
        self.assertTrue(rows[1]["is_dir"])

    def test_fs_glob_records_operation(self):
        inner = MagicMock()
        inner.fs_glob.return_value = [
            {"uri": "echo://m/1.md", "name": "1.md", "size": 5},
        ]
        audited = _AuditedMemoryClient(inner)
        audited.fs_glob("*.md")
        op = audited.operations[0]
        self.assertEqual("fs_glob", op["operation"])
        self.assertEqual("*.md", op["pattern"])
        self.assertEqual(1, op["entry_count"])

    def test_getattr_delegates_to_inner(self):
        inner = MagicMock()
        inner.open_session.return_value = "s1"
        audited = _AuditedMemoryClient(inner)
        self.assertEqual("s1", audited.open_session(title="test"))
        inner.open_session.assert_called_once_with(title="test")

    def test_entry_rows_filters_non_dict(self):
        entries = [
            {"uri": "echo://m/1", "name": "f1", "isDir": True, "size": 0},
            "not-a-dict",
            {"uri": "echo://m/2", "name": "f2", "kind": "directory", "size": 100},
            {"uri": "", "name": "", "size": 0},
        ]
        rows = _AuditedMemoryClient._entry_rows(entries)
        self.assertEqual(3, len(rows))
        self.assertTrue(rows[0]["is_dir"])
        self.assertTrue(rows[1]["is_dir"])
        self.assertFalse(rows[2]["is_dir"])

    def test_entry_rows_handles_missing_fields(self):
        rows = _AuditedMemoryClient._entry_rows([{}])
        self.assertEqual(1, len(rows))
        self.assertEqual("", rows[0]["uri"])
        self.assertEqual("", rows[0]["name"])
        self.assertFalse(rows[0]["is_dir"])
        self.assertEqual(0, rows[0]["size"])


# ------------------------------------------------------------------ #
#  _append_tool_audit                                                 #
# ------------------------------------------------------------------ #

class AppendToolAuditTests(unittest.TestCase):
    def _make_audit(self):
        return {
            "tool_calls": [],
            "tools_used": [],
            "discovered_files": [],
            "read_files": [],
        }

    def test_appends_tool_call_record(self):
        audit = self._make_audit()
        _append_tool_audit(
            audit, iteration=1, call_id="c1", name="memory_search",
            arguments={"query": "q"}, duplicate_skipped=False, operations=[],
        )
        self.assertEqual(1, len(audit["tool_calls"]))
        record = audit["tool_calls"][0]
        self.assertEqual(1, record["iteration"])
        self.assertEqual("c1", record["call_id"])
        self.assertEqual("memory_search", record["name"])
        self.assertFalse(record["duplicate_skipped"])

    def test_adds_tool_to_tools_used(self):
        audit = self._make_audit()
        _append_tool_audit(audit, iteration=1, call_id="c1", name="memory_search",
                           arguments={}, duplicate_skipped=False, operations=[])
        self.assertEqual(["memory_search"], audit["tools_used"])

    def test_does_not_duplicate_tool_in_tools_used(self):
        audit = self._make_audit()
        for call_id in ("c1", "c2"):
            _append_tool_audit(audit, iteration=1, call_id=call_id, name="memory_search",
                               arguments={}, duplicate_skipped=False, operations=[])
        self.assertEqual(["memory_search"], audit["tools_used"])

    def test_tracks_read_files_from_fs_read(self):
        audit = self._make_audit()
        operations = [
            {"operation": "fs_read", "uri": "echo://m/1", "status": "ok"},
        ]
        _append_tool_audit(audit, iteration=1, call_id="c1", name="memory_read_many",
                           arguments={}, duplicate_skipped=False, operations=operations)
        self.assertEqual(1, len(audit["read_files"]))
        self.assertEqual("echo://m/1", audit["read_files"][0]["uri"])
        self.assertIn("memory_read_many", audit["read_files"][0]["tools"])

    def test_does_not_track_read_files_on_error(self):
        audit = self._make_audit()
        operations = [
            {"operation": "fs_read", "uri": "echo://m/1", "status": "error"},
        ]
        _append_tool_audit(audit, iteration=1, call_id="c1", name="memory_read_many",
                           arguments={}, duplicate_skipped=False, operations=operations)
        self.assertEqual(0, len(audit["read_files"]))

    def test_tracks_discovered_files_from_fs_list(self):
        audit = self._make_audit()
        operations = [
            {"operation": "fs_list", "entries": [
                {"uri": "echo://m/1", "is_dir": False},
                {"uri": "echo://m/d", "is_dir": True},
            ]},
        ]
        _append_tool_audit(audit, iteration=1, call_id="c1", name="memory_list",
                           arguments={}, duplicate_skipped=False, operations=operations)
        self.assertEqual(1, len(audit["discovered_files"]))
        self.assertEqual("echo://m/1", audit["discovered_files"][0]["uri"])

    def test_tracks_discovered_files_from_fs_glob(self):
        audit = self._make_audit()
        operations = [
            {"operation": "fs_glob", "entries": [
                {"uri": "echo://m/f.md", "is_dir": False},
            ]},
        ]
        _append_tool_audit(audit, iteration=1, call_id="c1", name="memory_glob",
                           arguments={}, duplicate_skipped=False, operations=operations)
        self.assertEqual(1, len(audit["discovered_files"]))
        self.assertIn("memory_glob", audit["discovered_files"][0]["tools"])

    def test_merges_same_file_from_multiple_tools(self):
        audit = self._make_audit()
        ops1 = [{"operation": "fs_list", "entries": [{"uri": "echo://m/1", "is_dir": False}]}]
        ops2 = [{"operation": "fs_read", "uri": "echo://m/1", "status": "ok"}]
        _append_tool_audit(audit, iteration=1, call_id="c1", name="memory_list",
                           arguments={}, duplicate_skipped=False, operations=ops1)
        _append_tool_audit(audit, iteration=1, call_id="c2", name="memory_read_many",
                           arguments={}, duplicate_skipped=False, operations=ops2)
        self.assertEqual(1, len(audit["discovered_files"]))
        self.assertEqual(1, len(audit["read_files"]))
        self.assertIn("memory_list", audit["discovered_files"][0]["tools"])
        self.assertIn("memory_read_many", audit["read_files"][0]["tools"])


# ------------------------------------------------------------------ #
#  chat_with_tools                                                    #
# ------------------------------------------------------------------ #

class ChatWithToolsTests(unittest.TestCase):
    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_successful_call_returns_7_tuple(self, mock_urlopen, _mock_sleep):
        mock_urlopen.return_value = _FakeHTTPResponse(_llm_response_json("hello"))
        result = chat_with_tools(_FakeLLM(), [], [], 30.0)
        self.assertEqual(7, len(result))
        message, prompt, completion, attempt, latency, usage, metadata = result
        self.assertEqual("hello", message["content"])
        self.assertEqual(10, prompt)
        self.assertEqual(5, completion)
        self.assertEqual(0, attempt)
        self.assertIsInstance(latency, float)
        self.assertTrue(usage)
        self.assertEqual("resp-1", metadata["response_id"])
        self.assertEqual("served-model", metadata["response_model"])

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_includes_tools_in_payload(self, mock_urlopen, _mock_sleep):
        mock_urlopen.return_value = _FakeHTTPResponse(_llm_response_json())
        tools = [{"type": "function", "function": {"name": "test_tool"}}]
        chat_with_tools(_FakeLLM(), [], tools, 30.0)
        sent_data = json.loads(mock_urlopen.call_args.args[0].data)
        self.assertEqual(tools, sent_data["tools"])
        self.assertEqual("auto", sent_data["tool_choice"])

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_omits_temperature_by_default(self, mock_urlopen, _mock_sleep):
        mock_urlopen.return_value = _FakeHTTPResponse(_llm_response_json())
        chat_with_tools(_FakeLLM(), [], [], 30.0)
        sent_data = json.loads(mock_urlopen.call_args.args[0].data)
        self.assertNotIn("temperature", sent_data)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_includes_temperature_when_not_omitted(self, mock_urlopen, _mock_sleep):
        mock_urlopen.return_value = _FakeHTTPResponse(_llm_response_json())
        chat_with_tools(_FakeLLM(), [], [], 30.0, omit_temperature=False, answer_temperature=0.3)
        sent_data = json.loads(mock_urlopen.call_args.args[0].data)
        self.assertEqual(0.3, sent_data["temperature"])

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_retries_on_error_then_succeeds(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = [
            RuntimeError("transient"),
            _FakeHTTPResponse(_llm_response_json("recovered")),
        ]
        result = chat_with_tools(_FakeLLM(), [], [], 30.0)
        self.assertEqual("recovered", result[0]["content"])
        self.assertEqual(1, result[3])
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_raises_after_all_retries(self, mock_urlopen, mock_sleep):
        llm = _FakeLLM()
        llm.max_retries = 2
        mock_urlopen.side_effect = RuntimeError("persistent")
        with self.assertRaisesRegex(RuntimeError, "persistent"):
            chat_with_tools(llm, [], [], 30.0)
        self.assertEqual(3, mock_urlopen.call_count)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_empty_choices_raises_and_retries(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = [
            _FakeHTTPResponse(json.dumps({"choices": [], "usage": {}})),
            _FakeHTTPResponse(_llm_response_json("ok")),
        ]
        result = chat_with_tools(_FakeLLM(), [], [], 30.0)
        self.assertEqual("ok", result[0]["content"])

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_empty_content_without_tool_calls_raises(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = [
            _FakeHTTPResponse(json.dumps({
                "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                "usage": {},
            })),
            _FakeHTTPResponse(_llm_response_json("ok")),
        ]
        result = chat_with_tools(_FakeLLM(), [], [], 30.0)
        self.assertEqual("ok", result[0]["content"])

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_content_with_tool_calls_does_not_raise(self, mock_urlopen, _mock_sleep):
        tool_calls = [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}]
        mock_urlopen.return_value = _FakeHTTPResponse(
            _llm_response_json("", tool_calls=tool_calls),
        )
        result = chat_with_tools(_FakeLLM(), [], [], 30.0)
        self.assertEqual(tool_calls, result[0]["tool_calls"])

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_request_sha256_in_metadata(self, mock_urlopen, _mock_sleep):
        mock_urlopen.return_value = _FakeHTTPResponse(_llm_response_json())
        result = chat_with_tools(_FakeLLM(), [{"role": "user", "content": "hi"}], [], 30.0)
        metadata = result[6]
        self.assertEqual(64, len(metadata["request_sha256"]))
        self.assertEqual("test-model", metadata["request_model"])

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_usage_observed_false_when_empty(self, mock_urlopen, _mock_sleep):
        mock_urlopen.return_value = _FakeHTTPResponse(json.dumps({
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {},
        }))
        result = chat_with_tools(_FakeLLM(), [], [], 30.0)
        self.assertFalse(result[5])


# ------------------------------------------------------------------ #
#  _unique_retrieval_items / _retrieval_rows                          #
# ------------------------------------------------------------------ #

class UniqueRetrievalItemsTests(unittest.TestCase):
    def test_dedup_by_uri_and_content(self):
        items = [
            SearchResult(uri="echo://m/1", score=0.9, content="same"),
            SearchResult(uri="echo://m/1", score=0.8, content="same"),
            SearchResult(uri="echo://m/1", score=0.7, content="different"),
        ]
        result = _unique_retrieval_items(items)
        self.assertEqual(2, len(result))

    def test_dedup_uses_metadata_source_uri(self):
        items = [
            SearchResult(uri="echo://m/1", score=0.9, content="c",
                         metadata={"source_uri": "echo://native/1"}),
            SearchResult(uri="echo://m/2", score=0.8, content="c",
                         metadata={"source_uri": "echo://native/1"}),
        ]
        result = _unique_retrieval_items(items)
        self.assertEqual(1, len(result))

    def test_no_dedup_for_unique_items(self):
        items = [
            SearchResult(uri="echo://m/1", score=0.9, content="a"),
            SearchResult(uri="echo://m/2", score=0.8, content="b"),
        ]
        self.assertEqual(2, len(_unique_retrieval_items(items)))


class RetrievalRowsTests(unittest.TestCase):
    def test_converts_to_dicts(self):
        items = [
            SearchResult(uri="echo://m/1", score=0.9, content="c1", memory_type="atomic"),
        ]
        rows = _retrieval_rows(items)
        self.assertEqual(1, len(rows))
        self.assertEqual("echo://m/1", rows[0]["uri"])
        self.assertEqual(0.9, rows[0]["score"])
        self.assertEqual("c1", rows[0]["content"])

    def test_empty_list(self):
        self.assertEqual([], _retrieval_rows([]))


# ------------------------------------------------------------------ #
#  answer_one_vikingbot_question (additional cases)                   #
# ------------------------------------------------------------------ #

class AnswerOneVikingbotQuestionTests(unittest.TestCase):
    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_basic_answer_without_tools(self, mock_chat):
        mock_chat.return_value = ({"role": "assistant", "content": "42"}, 10, 5)
        result = answer_one_vikingbot_question(
            _FakeEchoMem(), _FakeLLM(),
            question_id="q1", question="What?", answer="42",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            tools_enabled=False,
        )
        self.assertEqual("42", result.response)
        self.assertEqual(0, result.tool_call_count)
        self.assertEqual(1, result.iterations)
        self.assertEqual("", result.llm_error)

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_retrieval_error_does_not_abort(self, mock_chat):
        class ErrorEchoMem(_FakeEchoMem):
            def search(self, *_args, **_kwargs):
                raise RuntimeError("backend down")

        mock_chat.return_value = ({"role": "assistant", "content": "guess"}, 10, 5)
        result = answer_one_vikingbot_question(
            ErrorEchoMem(), _FakeLLM(),
            question_id="q1", question="Q", answer="a",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            tools_enabled=False,
        )
        self.assertEqual("backend down", result.retrieval_error)
        self.assertEqual("guess", result.response)

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_vikingbot_prompt_query_mode(self, mock_chat):
        echomem = _FakeEchoMem()
        mock_chat.return_value = ({"role": "assistant", "content": "ans"}, 10, 5)
        answer_one_vikingbot_question(
            echomem, _FakeLLM(),
            question_id="q1", question="What?", answer="a",
            question_time="2024-01-01",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            initial_retrieval_query_mode="vikingbot_prompt",
            tools_enabled=False,
        )
        self.assertEqual(
            "Current date: 2024-01-01. Answer the question directly: What?",
            echomem.queries[0],
        )

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_question_only_query_mode(self, mock_chat):
        echomem = _FakeEchoMem()
        mock_chat.return_value = ({"role": "assistant", "content": "ans"}, 10, 5)
        answer_one_vikingbot_question(
            echomem, _FakeLLM(),
            question_id="q1", question="What?", answer="a",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            initial_retrieval_query_mode="question_only",
            tools_enabled=False,
        )
        self.assertEqual("What?", echomem.queries[0])

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_retrieval_uri_dedup(self, mock_chat):
        class DupEchoMem(_FakeEchoMem):
            def search(self, *_args, **_kwargs):
                return [
                    SearchResult(uri="echo://m/1", score=1.0, content="a"),
                    SearchResult(uri="echo://m/1", score=0.9, content="b"),
                    SearchResult(uri="echo://m/2", score=0.8, content="c"),
                ]

        mock_chat.return_value = ({"role": "assistant", "content": "ans"}, 10, 5)
        result = answer_one_vikingbot_question(
            DupEchoMem(), _FakeLLM(),
            question_id="q1", question="Q", answer="a",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            retrieval_uri_dedup=True,
            tools_enabled=False,
        )
        self.assertEqual(2, len(result.retrieval_items))

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_initial_min_score_filter(self, mock_chat):
        class ScoreEchoMem(_FakeEchoMem):
            def search(self, *_args, **_kwargs):
                return [
                    SearchResult(uri="echo://m/1", score=0.9, content="high"),
                    SearchResult(uri="echo://m/2", score=0.3, content="low"),
                ]

        mock_chat.return_value = ({"role": "assistant", "content": "ans"}, 10, 5)
        result = answer_one_vikingbot_question(
            ScoreEchoMem(), _FakeLLM(),
            question_id="q1", question="Q", answer="a",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            initial_min_score=0.5,
            tools_enabled=False,
        )
        self.assertEqual(1, len(result.retrieval_items))
        self.assertEqual("echo://m/1", result.retrieval_items[0]["uri"])

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_llm_error_on_exception(self, mock_chat):
        mock_chat.side_effect = RuntimeError("LLM unavailable")
        result = answer_one_vikingbot_question(
            _FakeEchoMem(), _FakeLLM(),
            question_id="q1", question="Q", answer="a",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            tools_enabled=False,
        )
        self.assertEqual("", result.response)
        self.assertIn("LLM unavailable", result.llm_error)
        self.assertIn("error", result.trace)

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_duplicate_search_skipped_with_question_scope(self, mock_chat):
        echomem = _FakeEchoMem()
        calls = iter([
            (
                {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "memory_search", "arguments": json.dumps({"query": "dup"})},
                }]},
                1, 1,
            ),
            (
                {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "c2", "type": "function",
                    "function": {"name": "memory_search", "arguments": json.dumps({"query": "dup"})},
                }]},
                1, 1,
            ),
            ({"role": "assistant", "content": "done"}, 1, 1),
        ])
        mock_chat.side_effect = lambda *_a, **_kw: next(calls)
        result = answer_one_vikingbot_question(
            echomem, _FakeLLM(),
            question_id="q1", question="Q", answer="done",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            tool_query_dedup_scope="question",
        )
        self.assertEqual(["Q", "dup"], echomem.queries)
        self.assertEqual(2, result.tool_call_count)
        self.assertTrue(
            result.trace["iterations"][1]["tool_calls"][0]["duplicate_skipped"]
        )

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_max_iterations_zero_forces_immediate_answer(self, mock_chat):
        mock_chat.return_value = ({"role": "assistant", "content": "forced"}, 10, 5)
        result = answer_one_vikingbot_question(
            _FakeEchoMem(), _FakeLLM(),
            question_id="q1", question="Q", answer="a",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            max_iterations=0,
        )
        self.assertEqual("forced", result.response)
        self.assertEqual(0, result.iterations)
        self.assertEqual(0, result.tool_call_count)

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_trace_schema_and_agent(self, mock_chat):
        mock_chat.return_value = ({"role": "assistant", "content": "ans"}, 10, 5)
        result = answer_one_vikingbot_question(
            _FakeEchoMem(), _FakeLLM(),
            question_id="q1", question="Q", answer="a",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            tools_enabled=False,
            sample_id="s1", category="temporal",
        )
        trace = result.trace
        self.assertEqual(1, trace["schema_version"])
        self.assertEqual("vikingbot", trace["agent"])
        self.assertEqual("q1", trace["question_id"])
        self.assertEqual("s1", trace["sample_id"])
        self.assertEqual("temporal", trace["category"])
        self.assertIn("settings", trace)
        self.assertIn("initial_retrieval", trace)
        self.assertIn("tool_audit", trace)
        self.assertIn("tool_protocol", trace)

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_forced_final_answer_after_iteration_limit(self, mock_chat):
        calls = iter([
            (
                {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "memory_search", "arguments": json.dumps({"query": "q"})},
                }]},
                10, 2,
            ),
            ({"role": "assistant", "content": "forced answer"}, 12, 3),
        ])

        def fake_chat(_llm, messages, tools, _timeout, **_kw):
            if not tools:
                self.assertIn("Tool-use iteration limit reached", messages[-1]["content"])
            return next(calls)

        mock_chat.side_effect = fake_chat
        result = answer_one_vikingbot_question(
            _FakeEchoMem(), _FakeLLM(),
            question_id="q1", question="Q", answer="a",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            max_iterations=1,
        )
        self.assertEqual("forced answer", result.response)
        self.assertEqual(1, result.iterations)
        self.assertEqual(1, result.tool_call_count)
        self.assertIn("forced_final_answer", result.trace)

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_vikingboat0411_does_not_sanitize(self, mock_chat):
        mock_chat.return_value = (
            {"role": "assistant", "content": "**Bold answer**"}, 10, 5,
        )
        result = answer_one_vikingbot_question(
            _FakeEchoMem(), _FakeLLM(),
            question_id="q1", question="Q", answer="a",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            tools_enabled=False,
        )
        self.assertEqual("**Bold answer**", result.response)
        self.assertFalse(result.trace["answer_sanitized"])

    @patch("plugins.vikingbot.runtime.chat_with_tools")
    def test_model_usage_observed_tracking(self, mock_chat):
        mock_chat.return_value = (
            {"role": "assistant", "content": "ans"}, 10, 5, 0, 0.1, True, {},
        )
        result = answer_one_vikingbot_question(
            _FakeEchoMem(), _FakeLLM(),
            question_id="q1", question="Q", answer="a",
            qa_profile=VIKINGBOAT_0411_PROFILE,
            tools_enabled=False,
        )
        self.assertTrue(result.model_usage_observed)
        self.assertEqual(0, result.model_retry_count)


# ------------------------------------------------------------------ #
#  tool_definitions                                                   #
# ------------------------------------------------------------------ #

class ToolDefinitionsTests(unittest.TestCase):
    def test_search_read_has_two_tools(self):
        tools = tool_definitions("search_read")
        names = [t["function"]["name"] for t in tools]
        self.assertEqual([MEMORY_SEARCH_TOOL, MEMORY_READ_TOOL], names)

    def test_native_safe_has_five_tools(self):
        tools = tool_definitions("vikingbot_native_safe")
        names = {t["function"]["name"] for t in tools}
        self.assertEqual({
            MEMORY_SEARCH_TOOL, MEMORY_READ_TOOL,
            MEMORY_LIST_TOOL, MEMORY_GREP_TOOL, MEMORY_GLOB_TOOL,
        }, names)

    def test_echo_native_has_five_tools(self):
        tools = tool_definitions("vikingbot_echo_native")
        self.assertEqual(5, len(tools))

    def test_search_target_uri_adds_property(self):
        tools = tool_definitions("search_read", search_target_uri=True)
        props = tools[0]["function"]["parameters"]["properties"]
        self.assertIn("target_uri", props)

    def test_no_search_target_uri_omits_property(self):
        tools = tool_definitions("search_read", search_target_uri=False)
        props = tools[0]["function"]["parameters"]["properties"]
        self.assertNotIn("target_uri", props)

    def test_echo_native_has_min_score_default(self):
        tools = tool_definitions("vikingbot_echo_native")
        props = tools[0]["function"]["parameters"]["properties"]
        self.assertIn("min_score", props)
        self.assertEqual(0.35, props["min_score"]["default"])

    def test_non_echo_native_no_min_score(self):
        tools = tool_definitions("search_read")
        props = tools[0]["function"]["parameters"]["properties"]
        self.assertNotIn("min_score", props)

    def test_echo_native_list_has_no_required_uri(self):
        tools = tool_definitions("vikingbot_echo_native")
        list_tool = next(t for t in tools if t["function"]["name"] == MEMORY_LIST_TOOL)
        self.assertEqual([], list_tool["function"]["parameters"]["required"])

    def test_non_echo_native_list_requires_uri(self):
        tools = tool_definitions("vikingbot_native_safe")
        list_tool = next(t for t in tools if t["function"]["name"] == MEMORY_LIST_TOOL)
        self.assertEqual(["uri"], list_tool["function"]["parameters"]["required"])

    def test_echo_native_grep_pattern_is_string(self):
        tools = tool_definitions("vikingbot_echo_native")
        grep_tool = next(t for t in tools if t["function"]["name"] == MEMORY_GREP_TOOL)
        self.assertEqual("string", grep_tool["function"]["parameters"]["properties"]["pattern"]["type"])

    def test_non_echo_native_grep_pattern_is_array(self):
        tools = tool_definitions("vikingbot_native_safe")
        grep_tool = next(t for t in tools if t["function"]["name"] == MEMORY_GREP_TOOL)
        self.assertEqual("array", grep_tool["function"]["parameters"]["properties"]["pattern"]["type"])


# ------------------------------------------------------------------ #
#  _search_payload_items / search_payload                             #
# ------------------------------------------------------------------ #

class SearchPayloadItemsTests(unittest.TestCase):
    def test_dedup_by_uri(self):
        items = [
            SearchResult(uri="echo://m/1", score=0.9, content="a"),
            SearchResult(uri="echo://m/1", score=0.8, content="b"),
            SearchResult(uri="echo://m/2", score=0.7, content="c"),
        ]
        result = _search_payload_items(items, 10)
        self.assertEqual(2, len(result))

    def test_limit_enforced(self):
        items = [SearchResult(uri=f"echo://m/{i}", score=0.9, content="c") for i in range(5)]
        result = _search_payload_items(items, 3)
        self.assertEqual(3, len(result))

    def test_skip_empty_uri(self):
        items = [
            SearchResult(uri="", score=0.9, content="a"),
            SearchResult(uri="echo://m/1", score=0.8, content="b"),
        ]
        result = _search_payload_items(items, 10)
        self.assertEqual(1, len(result))


class SearchPayloadTests(unittest.TestCase):
    def test_payload_structure(self):
        items = [SearchResult(uri="echo://m/1", score=0.123456789, content="content")]
        payload = json.loads(search_payload(items, 10))
        self.assertEqual(1, payload["count"])
        self.assertEqual([], payload["resources"])
        self.assertEqual([], payload["skills"])
        mem = payload["memories"][0]
        self.assertEqual(1, mem["index"])
        self.assertEqual("echo://m/1", mem["uri"])
        self.assertEqual("content", mem["abstract"])
        self.assertTrue(mem["is_leaf"])
        self.assertEqual(0.123457, mem["score"])

    def test_empty_payload(self):
        payload = json.loads(search_payload([], 10))
        self.assertEqual(0, payload["count"])
        self.assertEqual([], payload["memories"])

    def test_abstract_truncated_to_700(self):
        long_content = "x" * 800
        items = [SearchResult(uri="echo://m/1", score=1.0, content=long_content)]
        payload = json.loads(search_payload(items, 10))
        self.assertEqual(700, len(payload["memories"][0]["abstract"]))


# ------------------------------------------------------------------ #
#  execute_tool                                                       #
# ------------------------------------------------------------------ #

class ExecuteToolTests(unittest.TestCase):
    def test_memory_search_empty_query(self):
        text, items = execute_tool(
            _FakeEchoMem(), MEMORY_SEARCH_TOOL, {"query": ""}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertEqual("No results found for empty query", text)
        self.assertEqual([], items)

    def test_memory_search_with_min_score_from_arguments(self):
        class ScoreMem(_FakeEchoMem):
            def search(self, *_a, **_kw):
                return [
                    SearchResult(uri="echo://m/1", score=0.9, content="high"),
                    SearchResult(uri="echo://m/2", score=0.2, content="low"),
                ]

        text, items = execute_tool(
            ScoreMem(), MEMORY_SEARCH_TOOL, {"query": "q", "min_score": 0.5}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertEqual(1, len(items))
        self.assertEqual("echo://m/1", items[0].uri)

    def test_memory_search_falls_back_to_tool_min_score(self):
        class ScoreMem(_FakeEchoMem):
            def search(self, *_a, **_kw):
                return [
                    SearchResult(uri="echo://m/1", score=0.9, content="high"),
                    SearchResult(uri="echo://m/2", score=0.2, content="low"),
                ]

        _, items = execute_tool(
            ScoreMem(), MEMORY_SEARCH_TOOL, {"query": "q"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.5, timeout_s=30,
        )
        self.assertEqual(1, len(items))

    def test_memory_search_invalid_min_score_uses_tool_default(self):
        class ScoreMem(_FakeEchoMem):
            def search(self, *_a, **_kw):
                return [SearchResult(uri="echo://m/1", score=0.9, content="high")]

        _, items = execute_tool(
            ScoreMem(), MEMORY_SEARCH_TOOL, {"query": "q", "min_score": "invalid"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertEqual(1, len(items))

    def test_memory_search_target_uri_filter(self):
        class TargetMem(_FakeEchoMem):
            def search(self, *_a, **_kw):
                return [
                    SearchResult(uri="echo://sessions/s1/msg", score=0.9, content="a"),
                    SearchResult(uri="echo://engine/foo.md", score=0.8, content="b"),
                ]

        _, items = execute_tool(
            TargetMem(), MEMORY_SEARCH_TOOL,
            {"query": "q", "target_uri": "echo://sessions/"},
            {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertEqual(1, len(items))
        self.assertEqual("echo://sessions/s1/msg", items[0].uri)

    def test_memory_search_caches_items(self):
        cache: dict = {}
        execute_tool(
            _FakeEchoMem(), MEMORY_SEARCH_TOOL, {"query": "q"}, cache,
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("echo://account/sessions/session-1", cache)

    def test_memory_read_many_empty_uris(self):
        text, items = execute_tool(
            _FakeEchoMem(), MEMORY_READ_TOOL, {"uris": []}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertEqual("Error: No URIs provided.", text)
        self.assertEqual([], items)

    def test_memory_read_many_no_uris_key(self):
        text, _ = execute_tool(
            _FakeEchoMem(), MEMORY_READ_TOOL, {}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertEqual("Error: No URIs provided.", text)

    def test_memory_read_many_engine_uri(self):
        class ReadMem(_FakeEchoMem):
            def __init__(self):
                super().__init__()
                self.read_uris = []

            def fs_read(self, uri, **_kw):
                self.read_uris.append(uri)
                return "engine content"

        echomem = ReadMem()
        text, _ = execute_tool(
            echomem, MEMORY_READ_TOOL, {"uris": ["echo://engine/foo.md"]}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("engine content", text)
        self.assertEqual(["echo://engine/foo.md"], echomem.read_uris)

    def test_memory_read_many_single_string_uri(self):
        text, _ = execute_tool(
            _FakeEchoMem(), MEMORY_READ_TOOL, {"uris": "echo://engine/foo.md"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("Jon lost his job", text)

    def test_unsupported_tool(self):
        text, items = execute_tool(
            _FakeEchoMem(), "unknown_tool", {}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("unsupported tool", text)
        self.assertEqual([], items)

    def test_memory_list_error(self):
        class ListErrorMem(_FakeEchoMem):
            def fs_list(self, *_a, **_kw):
                raise RuntimeError("list failed")

        text, _ = execute_tool(
            ListErrorMem(), MEMORY_LIST_TOOL, {"uri": "echo://engine/"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("Error listing EchoMemory resources", text)

    def test_memory_list_returns_rows(self):
        class ListMem(_FakeEchoMem):
            def fs_list(self, *_a, **_kw):
                return [
                    {"uri": "echo://m/1", "name": "f1", "size": 10, "kind": "file"},
                    {"uri": "echo://d/1", "name": "d1", "size": 0, "kind": "directory"},
                ]

        text, _ = execute_tool(
            ListMem(), MEMORY_LIST_TOOL, {"uri": "echo://engine/"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        lines = text.strip().split("\n") if text.strip() else []
        self.assertTrue(lines)
        first = json.loads(lines[0])
        self.assertEqual("f1", first["name"])

    def test_memory_list_empty(self):
        text, _ = execute_tool(
            _FakeEchoMem(), MEMORY_LIST_TOOL, {"uri": "echo://engine/"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("No resources found", text)

    def test_memory_glob_returns_uris(self):
        class GlobMem(_FakeEchoMem):
            def fs_glob(self, *_a, **_kw):
                return [
                    {"uri": "echo://m/1.md"},
                    {"uri": "echo://m/2.md"},
                ]

        text, _ = execute_tool(
            GlobMem(), MEMORY_GLOB_TOOL, {"pattern": "*.md"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("Found 2 files", text)
        self.assertIn("echo://m/1.md", text)

    def test_memory_glob_empty(self):
        text, _ = execute_tool(
            _FakeEchoMem(), MEMORY_GLOB_TOOL, {"pattern": "*.md"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("No files found", text)

    def test_memory_glob_error(self):
        class GlobErrorMem(_FakeEchoMem):
            def fs_glob(self, *_a, **_kw):
                raise RuntimeError("glob failed")

        text, _ = execute_tool(
            GlobErrorMem(), MEMORY_GLOB_TOOL, {"pattern": "*.md"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("Error globbing", text)

    def test_memory_glob_with_uri_prefix(self):
        class GlobMem(_FakeEchoMem):
            def __init__(self):
                super().__init__()
                self.patterns = []

            def fs_glob(self, pattern, **_kw):
                self.patterns.append(pattern)
                return []

        echomem = GlobMem()
        execute_tool(
            echomem, MEMORY_GLOB_TOOL, {"pattern": "*.md", "uri": "echo://engine/sub"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertTrue(echomem.patterns[0].endswith("*.md"))

    def test_memory_grep_no_patterns(self):
        text, _ = execute_tool(
            _FakeEchoMem(), MEMORY_GREP_TOOL, {"pattern": [], "uri": ""}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("No matches found", text)

    def test_memory_grep_finds_match(self):
        class GrepMem(_FakeEchoMem):
            def fs_glob(self, *_a, **_kw):
                return [{"uri": "echo://sessions/s1/current/messages.jsonl"}]

            def fs_read(self, uri, **_kw):
                return "Jon bought a Marley floor."

        text, _ = execute_tool(
            GrepMem(), MEMORY_GREP_TOOL, {"pattern": ["Marley"], "uri": ""}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("Marley", text)
        self.assertIn("Found 1 matches", text)

    def test_memory_grep_no_match(self):
        class GrepMem(_FakeEchoMem):
            def fs_glob(self, *_a, **_kw):
                return [{"uri": "echo://sessions/s1/current/messages.jsonl"}]

            def fs_read(self, uri, **_kw):
                return "nothing relevant"

        text, _ = execute_tool(
            GrepMem(), MEMORY_GREP_TOOL, {"pattern": ["nonexistent"], "uri": ""}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("No matches found", text)

    def test_memory_grep_invalid_regex_falls_back(self):
        class GrepMem(_FakeEchoMem):
            def fs_glob(self, *_a, **_kw):
                return [{"uri": "echo://sessions/s1/current/messages.jsonl"}]

            def fs_read(self, uri, **_kw):
                return "line with [invalid brackets]"

        text, _ = execute_tool(
            GrepMem(), MEMORY_GREP_TOOL, {"pattern": ["[invalid"], "uri": ""}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("[invalid brackets]", text)

    def test_memory_grep_case_insensitive(self):
        class GrepMem(_FakeEchoMem):
            def fs_glob(self, *_a, **_kw):
                return [{"uri": "echo://sessions/s1/current/messages.jsonl"}]

            def fs_read(self, uri, **_kw):
                return "Jon BOUGHT something."

        text, _ = execute_tool(
            GrepMem(), MEMORY_GREP_TOOL,
            {"pattern": ["bought"], "uri": "", "case_insensitive": True}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.0, timeout_s=30,
        )
        self.assertIn("BOUGHT", text)

    def test_memory_search_resource_mode_uses_search_resources(self):
        class ResourceMem:
            def __init__(self):
                self.last_tags = None

            def search_resources(self, query, limit=None, tags=None, timeout_s=None):
                self.last_tags = tags
                return [
                    {
                        "uri": "viking://user/resources/hotpotqa/D1-abc",
                        "path": "hotpotqa/D1-abc",
                        "score": 0.9,
                        "text": "body one",
                    },
                    {
                        "uri": "viking://user/resources/hotpotqa/D2-def",
                        "path": "hotpotqa/D2-def",
                        "score": 0.3,
                        "text": "body two",
                    },
                ]

        mem = ResourceMem()
        _, items = execute_tool(
            mem, MEMORY_SEARCH_TOOL, {"query": "q"}, {},
            top_k=25, tool_search_limit=25, tool_search_pool_multiplier=1,
            tool_min_score=0.5, timeout_s=30,
            search_resources=True,
            path_title_map={"hotpotqa/D1-abc": "D1"},
        )
        self.assertEqual(["hotpotqa"], mem.last_tags)
        # min_score 0.5 过滤掉低分项
        self.assertEqual(1, len(items))
        self.assertEqual("viking://user/resources/hotpotqa/D1-abc", items[0].uri)
        # title 从 path_title_map 解析进 metadata，供 supporting-fact 评测使用
        self.assertEqual("D1", items[0].metadata["hotpotqa_title"])


# ------------------------------------------------------------------ #
#  _session_uri_tail                                                  #
# ------------------------------------------------------------------ #

class SessionUriTailTests(unittest.TestCase):
    def test_valid_session_uri(self):
        self.assertEqual("session-1", _session_uri_tail("echo://tenant/sessions/session-1"))

    def test_session_uri_with_file_path(self):
        self.assertEqual(
            "session-1/current/messages.jsonl",
            _session_uri_tail("echo://tenant/sessions/session-1/current/messages.jsonl"),
        )

    def test_sessions_root_empty_tail(self):
        for uri in ("echo://tenant/sessions", "echo://tenant/sessions/"):
            with self.subTest(uri=uri):
                self.assertEqual("", _session_uri_tail(uri))

    def test_non_echo_uri_returns_none(self):
        self.assertIsNone(_session_uri_tail("http://example.com/sessions/s1"))

    def test_no_sessions_marker_returns_none(self):
        self.assertIsNone(_session_uri_tail("echo://tenant/memory/foo"))

    def test_sessionssession_not_matched(self):
        self.assertIsNone(_session_uri_tail("echo://tenant/sessionssession-1"))


# ------------------------------------------------------------------ #
#  _engine_uri                                                        #
# ------------------------------------------------------------------ #

class EngineUriTests(unittest.TestCase):
    def test_engine_uri_passthrough(self):
        self.assertEqual(
            "echo://engine/foo.md",
            _engine_uri("echo://engine/foo.md"),
        )

    def test_engine_uri_sessions_with_leaf(self):
        self.assertEqual(
            "echo://engine/sessions/*/current/messages.jsonl",
            _engine_uri("echo://engine/sessions", leaf_pattern="*/current/messages.jsonl"),
        )

    def test_tenant_sessions_mapped(self):
        self.assertEqual(
            "echo://sessions/session-1",
            _engine_uri("echo://tenant-a/sessions/session-1"),
        )

    def test_empty_string_no_leaf(self):
        self.assertEqual("echo://sessions", _engine_uri(""))

    def test_empty_string_with_leaf(self):
        self.assertEqual(
            "echo://sessions/*/current/messages.jsonl",
            _engine_uri("", leaf_pattern="*/current/messages.jsonl"),
        )

    def test_non_echo_uri_falls_through(self):
        self.assertEqual("echo://sessions", _engine_uri("non-echo-uri"))

    def test_tenant_sessions_with_leaf_pattern(self):
        self.assertEqual(
            "echo://sessions/*/current/messages.jsonl",
            _engine_uri("echo://tenant-a/sessions", leaf_pattern="*/current/messages.jsonl"),
        )

    def test_engine_uri_without_leaf_passthrough_md(self):
        self.assertEqual(
            "echo://engine/memory/Jon.md",
            _engine_uri("echo://engine/memory/Jon.md"),
        )


# ------------------------------------------------------------------ #
#  _session_candidates_message / _matching_session_files              #
# ------------------------------------------------------------------ #

class SessionCandidatesMessageTests(unittest.TestCase):
    def test_cached_content_returned_directly(self):
        echomem = MagicMock()
        result = _session_candidates_message(echomem, "*", timeout_s=30, cached_content="cached")
        self.assertEqual(["cached"], result)
        echomem.fs_glob.assert_not_called()

    def test_returns_matches(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = [
            {"uri": "echo://sessions/s1/current/messages.jsonl"},
        ]
        result = _session_candidates_message(echomem, "s1*", timeout_s=30)
        self.assertTrue(any("s1" in line for line in result))

    def test_no_matches_returns_error(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = []
        result = _session_candidates_message(echomem, "s1*", timeout_s=30)
        self.assertTrue(any("No session file matched" in line for line in result))

    def test_exception_returns_error(self):
        echomem = MagicMock()
        echomem.fs_glob.side_effect = RuntimeError("boom")
        result = _session_candidates_message(echomem, "s1*", timeout_s=30)
        self.assertTrue(any("Unable to list matching sessions" in line for line in result))


class MatchingSessionFilesTests(unittest.TestCase):
    def test_returns_uris(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = [
            {"uri": "echo://sessions/s1/current/messages.jsonl"},
            {"uri": "echo://sessions/s2/current/messages.jsonl"},
        ]
        result = _matching_session_files(echomem, "s*", timeout_s=30)
        self.assertEqual(2, len(result))

    def test_dedup_uris(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = [
            {"uri": "echo://sessions/s1/current/messages.jsonl"},
            {"uri": "echo://sessions/s1/current/messages.jsonl"},
        ]
        result = _matching_session_files(echomem, "s1*", timeout_s=30)
        self.assertEqual(1, len(result))

    def test_limits_to_20(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = [
            {"uri": f"echo://sessions/s{i}/current/messages.jsonl"}
            for i in range(25)
        ]
        result = _matching_session_files(echomem, "s*", timeout_s=30)
        self.assertEqual(20, len(result))

    def test_skips_empty_uris(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = [
            {"uri": ""},
            {"uri": "echo://sessions/s1/current/messages.jsonl"},
        ]
        result = _matching_session_files(echomem, "s*", timeout_s=30)
        self.assertEqual(1, len(result))


# ------------------------------------------------------------------ #
#  _grep_memory                                                       #
# ------------------------------------------------------------------ #

class GrepMemoryTests(unittest.TestCase):
    def test_no_patterns_returns_message(self):
        text = _grep_memory(_FakeEchoMem(), {"pattern": [], "uri": ""}, timeout_s=30)
        self.assertIn("No matches found", text)

    def test_empty_pattern_string_returns_message(self):
        text = _grep_memory(_FakeEchoMem(), {"pattern": "", "uri": ""}, timeout_s=30)
        self.assertIn("No matches found", text)

    def test_finds_match(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = [{"uri": "echo://sessions/s1/current/messages.jsonl"}]
        echomem.fs_read.return_value = "Jon bought Marley flooring."
        text = _grep_memory(echomem, {"pattern": "Marley", "uri": ""}, timeout_s=30)
        self.assertIn("Marley", text)
        self.assertIn("Found 1 matches", text)

    def test_no_match(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = [{"uri": "echo://sessions/s1/current/messages.jsonl"}]
        echomem.fs_read.return_value = "nothing here"
        text = _grep_memory(echomem, {"pattern": "nonexistent", "uri": ""}, timeout_s=30)
        self.assertIn("No matches found", text)

    def test_case_insensitive(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = [{"uri": "echo://sessions/s1/current/messages.jsonl"}]
        echomem.fs_read.return_value = "JON BOUGHT FLOOR"
        text = _grep_memory(
            echomem, {"pattern": "jon", "uri": "", "case_insensitive": True}, timeout_s=30,
        )
        self.assertIn("JON", text)

    def test_multiple_patterns(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = [{"uri": "echo://sessions/s1/current/messages.jsonl"}]
        echomem.fs_read.return_value = "Jon bought Marley. Jon sold carpet."
        text = _grep_memory(
            echomem, {"pattern": ["Marley", "carpet"], "uri": ""}, timeout_s=30,
        )
        self.assertIn("Marley", text)
        self.assertIn("carpet", text)
        self.assertIn("Found 2 matches", text)

    def test_fs_read_error_skipped(self):
        echomem = MagicMock()
        echomem.fs_glob.return_value = [
            {"uri": "echo://sessions/s1/current/messages.jsonl"},
            {"uri": "echo://sessions/s2/current/messages.jsonl"},
        ]
        echomem.fs_read.side_effect = [RuntimeError("read fail"), "Jon bought Marley."]
        text = _grep_memory(echomem, {"pattern": "Marley", "uri": ""}, timeout_s=30)
        self.assertIn("Marley", text)


# ------------------------------------------------------------------ #
#  Prompting: build_question_prompt                                   #
# ------------------------------------------------------------------ #

class BuildQuestionPromptTests(unittest.TestCase):
    def test_with_question_time(self):
        result = build_question_prompt("What happened?", "2024-01-01")
        self.assertIn("Current date: 2024-01-01", result)
        self.assertIn("What happened?", result)

    def test_without_question_time(self):
        result = build_question_prompt("What happened?", "")
        self.assertNotIn("Current date", result)
        self.assertIn("What happened?", result)

    def test_whitespace_question_time_treated_as_empty(self):
        result = build_question_prompt("Q", "   ")
        self.assertNotIn("Current date", result)


# ------------------------------------------------------------------ #
#  Prompting: load_bootstrap                                          #
# ------------------------------------------------------------------ #

class LoadBootstrapTests(unittest.TestCase):
    def test_loads_soul_and_tools(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "SOUL.md").write_text("# Soul", encoding="utf-8")
            (ws / "TOOLS.md").write_text("# Tools", encoding="utf-8")
            result = load_bootstrap(ws)
        self.assertIn("## SOUL.md", result)
        self.assertIn("# Soul", result)
        self.assertIn("## TOOLS.md", result)
        self.assertIn("# Tools", result)

    def test_missing_files_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            result = load_bootstrap(Path(d))
        self.assertEqual("", result)

    def test_empty_file_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "SOUL.md").write_text("", encoding="utf-8")
            (ws / "TOOLS.md").write_text("# Tools", encoding="utf-8")
            result = load_bootstrap(ws)
        self.assertNotIn("SOUL.md", result)
        self.assertIn("TOOLS.md", result)

    def test_custom_bootstrap_files(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "CUSTOM.md").write_text("# Custom", encoding="utf-8")
            result = load_bootstrap(ws, ("CUSTOM.md",))
        self.assertIn("## CUSTOM.md", result)

    def test_default_bootstrap_files_constant(self):
        self.assertEqual(("SOUL.md", "TOOLS.md"), DEFAULT_BOOTSTRAP_FILES)


# ------------------------------------------------------------------ #
#  Prompting: build_system_prompt                                     #
# ------------------------------------------------------------------ #

class BuildSystemPromptTests(unittest.TestCase):
    def test_contains_vikingbot_header(self):
        prompt = build_system_prompt("")
        self.assertIn("# vikingbot", prompt)

    def test_contains_runtime_info(self):
        prompt = build_system_prompt("")
        self.assertIn("Python", prompt)

    def test_contains_capabilities(self):
        prompt = build_system_prompt("")
        self.assertIn("Search the web", prompt)

    def test_contains_qa_evidence_section(self):
        prompt = build_system_prompt("")
        self.assertIn("Direct QA Evidence Use", prompt)

    def test_includes_bootstrap_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "SOUL.md").write_text("Custom soul text", encoding="utf-8")
            prompt = build_system_prompt(str(ws))
        self.assertIn("Custom soul text", prompt)

    def test_resolves_workspace_path(self):
        with tempfile.TemporaryDirectory() as d:
            prompt = build_system_prompt(d)
            self.assertIn(d, prompt)


# ------------------------------------------------------------------ #
#  Prompting: format_memory                                           #
# ------------------------------------------------------------------ #

class FormatMemoryTests(unittest.TestCase):
    def test_full_memory_within_budget(self):
        items = [SearchResult(uri="echo://m/1", score=0.8, content="a fact")]
        result = format_memory(items, 500)
        self.assertIn('type="full"', result)
        self.assertIn("a fact", result)
        self.assertIn("echo://m/1", result)

    def test_link_memory_when_over_budget(self):
        items = [SearchResult(uri="echo://m/1", score=0.8, content="x" * 200)]
        result = format_memory(items, 100)
        self.assertIn('type="link"', result)
        self.assertNotIn("x" * 200, result)

    def test_link_memory_for_empty_content(self):
        items = [SearchResult(uri="echo://m/1", score=0.8, content="")]
        result = format_memory(items, 500)
        self.assertIn('type="link"', result)

    def test_dedup_identical_content(self):
        items = [
            SearchResult(uri="echo://m/1", score=0.9, content="same"),
            SearchResult(uri="echo://m/2", score=0.8, content="same"),
        ]
        result = format_memory(items, 500)
        self.assertEqual(1, result.count('type="full"'))

    def test_empty_items(self):
        self.assertEqual("", format_memory([], 500))

    def test_score_formatted_to_three_decimals(self):
        items = [SearchResult(uri="echo://m/1", score=0.123456, content="c")]
        result = format_memory(items, 500)
        self.assertIn("0.123", result)


# ------------------------------------------------------------------ #
#  Prompting: build_messages (default profile)                        #
# ------------------------------------------------------------------ #

class BuildMessagesDefaultTests(unittest.TestCase):
    def test_returns_three_messages(self):
        messages = build_messages("Q?", "2024-01-01", [], 4000, 2000, "")
        self.assertEqual(3, len(messages))
        self.assertEqual("system", messages[0]["role"])
        self.assertEqual("user", messages[1]["role"])
        self.assertEqual("user", messages[2]["role"])

    def test_last_message_is_question_prompt(self):
        messages = build_messages("What?", "2024-01-01", [], 4000, 2000, "")
        self.assertIn("What?", messages[-1]["content"])

    def test_memory_message_contains_evidence(self):
        items = [SearchResult(uri="echo://m/1", score=0.9, content="a fact")]
        messages = build_messages("Q?", "2024-01-01", items, 4000, 2000, "")
        self.assertIn("a fact", messages[1]["content"])

    def test_user_agent_split_by_uri(self):
        items = [
            SearchResult(uri="echo://user/m/1", score=0.9, content="user fact"),
            SearchResult(uri="echo://agent/m/1", score=0.8, content="agent fact"),
        ]
        messages = build_messages("Q?", "", items, 4000, 2000, "")
        memory_text = messages[1]["content"]
        self.assertIn("user fact", memory_text)
        self.assertIn("agent fact", memory_text)

    def test_agent_split_by_memory_type(self):
        items = [
            SearchResult(uri="echo://m/1", score=0.9, content="user fact", memory_type="atomic"),
            SearchResult(uri="echo://m/2", score=0.8, content="agent fact", memory_type="agent_note"),
        ]
        messages = build_messages("Q?", "", items, 4000, 2000, "")
        memory_text = messages[1]["content"]
        self.assertIn("user fact", memory_text)
        self.assertIn("agent fact", memory_text)

    def test_system_prompt_append_appended_to_system(self):
        baseline = build_messages("Q?", "", [], 4000, 2000, "")
        appended = build_messages("Q?", "", [], 4000, 2000, "", system_prompt_append="EXTRA")
        self.assertTrue(appended[0]["content"].endswith("EXTRA"))
        self.assertTrue(appended[0]["content"].startswith(baseline[0]["content"]))
        self.assertEqual(baseline[1:], appended[1:])

    def test_empty_system_prompt_append_no_change(self):
        baseline = build_messages("Q?", "", [], 4000, 2000, "")
        same = build_messages("Q?", "", [], 4000, 2000, "", system_prompt_append="")
        self.assertEqual(baseline, same)

    def test_no_items_shows_none(self):
        messages = build_messages("Q?", "", [], 4000, 2000, "")
        self.assertIn("(none)", messages[1]["content"])


# ------------------------------------------------------------------ #
#  Answers: sanitize_final_answer_text                                #
# ------------------------------------------------------------------ #

class SanitizeFinalAnswerTextTests(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual("", sanitize_final_answer_text(""))

    def test_none_input(self):
        self.assertEqual("", sanitize_final_answer_text(None))

    def test_strips_mem_thinking(self):
        text = "<mem_thinking>internal</mem_thinking>The answer is 42."
        self.assertEqual("The answer is 42.", sanitize_final_answer_text(text))

    def test_strips_judge_thinking(self):
        text = "<judge_thinking>reasoning</judge_thinking>42"
        self.assertEqual("42", sanitize_final_answer_text(text))

    def test_strips_dsml_trailing(self):
        text = "42<|DSML|more stuff"
        self.assertEqual("42", sanitize_final_answer_text(text))

    def test_strips_memory_search_trailing(self):
        text = "42<memory_search stuff"
        self.assertEqual("42", sanitize_final_answer_text(text))

    def test_strips_code_blocks(self):
        text = "```code\nblock```\nThe answer."
        result = sanitize_final_answer_text(text)
        self.assertIn("The answer.", result)
        self.assertNotIn("code", result)

    def test_strips_inline_code(self):
        text = "Answer `code` here."
        result = sanitize_final_answer_text(text)
        self.assertNotIn("`", result)

    def test_removes_bold_markers(self):
        self.assertEqual("Bold text", sanitize_final_answer_text("**Bold text**"))

    def test_removes_answer_prefix(self):
        text = "answer_01 The answer is 42."
        result = sanitize_final_answer_text(text)
        self.assertTrue(result.startswith("The answer"))

    def test_removes_turn_prefix(self):
        text = "turn_1 The answer."
        result = sanitize_final_answer_text(text)
        self.assertTrue(result.startswith("The answer"))

    def test_removes_bracket_prefix(self):
        text = "[context] The answer."
        result = sanitize_final_answer_text(text)
        self.assertTrue(result.startswith("The answer"))

    def test_removes_memory_prefix(self):
        text = "memory_1: The answer."
        result = sanitize_final_answer_text(text)
        self.assertTrue(result.startswith("The answer"))

    def test_removes_filler_phrases(self):
        for phrase in ("by the way", "speaking of", "actually", "well", "anyway"):
            with self.subTest(phrase=phrase):
                text = f"{phrase}, the answer is 42."
                result = sanitize_final_answer_text(text)
                self.assertTrue(result.startswith("the answer"))

    def test_removes_based_on_memory_lead(self):
        text = "Based on the retrieved memories, 42."
        self.assertEqual("42.", sanitize_final_answer_text(text))

    def test_removes_let_me_check_lead(self):
        text = "Let me check memory. The answer is 42."
        self.assertEqual("The answer is 42.", sanitize_final_answer_text(text))

    def test_removes_chinese_filler(self):
        text = "让我搜索一下。答案是42。"
        result = sanitize_final_answer_text(text)
        self.assertNotIn("让我搜索", result)

    def test_filters_sentences_with_let_me_search(self):
        text = "Let me search for that. The answer is 42."
        result = sanitize_final_answer_text(text)
        self.assertNotIn("Let me search", result)
        self.assertIn("42", result)

    def test_preserves_clean_answer(self):
        self.assertEqual("42", sanitize_final_answer_text("42"))

    def test_collapses_whitespace(self):
        text = "42    lots    of    spaces"
        result = sanitize_final_answer_text(text)
        self.assertEqual("42 lots of spaces", result)

    def test_strips_trailing_punctuation_from_cleanup(self):
        text = "42 -:"
        result = sanitize_final_answer_text(text)
        self.assertEqual("42", result)

    def test_carriage_replaced_with_newline(self):
        text = "line1\r\nline2"
        result = sanitize_final_answer_text(text)
        self.assertNotIn("\r", result)


# ------------------------------------------------------------------ #
#  Constants                                                          #
# ------------------------------------------------------------------ #

class ToolsConstantsTests(unittest.TestCase):
    def test_tool_name_constants(self):
        self.assertEqual("memory_search", MEMORY_SEARCH_TOOL)
        self.assertEqual("memory_read_many", MEMORY_READ_TOOL)
        self.assertEqual("memory_list", MEMORY_LIST_TOOL)
        self.assertEqual("memory_grep", MEMORY_GREP_TOOL)
        self.assertEqual("memory_glob", MEMORY_GLOB_TOOL)

    def test_sessions_root_constant(self):
        self.assertEqual("echo://sessions", SESSIONS_ROOT)

    def test_session_file_constant(self):
        self.assertEqual("current/messages.jsonl", SESSION_FILE)

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
