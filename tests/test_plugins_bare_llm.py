"""Unit tests for plugins.bare_llm.plugin.

Covers every functional point in plugins/bare_llm/docs/design.md and
plugins/bare_llm/plugin.py. All LLM calls are mocked -- no real API calls.

Run: python -m unittest tests.test_plugins_bare_llm -v
"""

from __future__ import annotations

import argparse
import contextlib
import os
import unittest
from unittest.mock import MagicMock, patch

from backends.memory_types import NullMemoryClient
from plugins.base import AgentPlugin, AgentResponse
from plugins.bare_llm.plugin import BareLLMPlugin, _SYSTEM_PROMPT
from shared.llm_client import LLMClient, LLMResponse


# ------------------------------------------------------------------ #
#  Test helpers                                                       #
# ------------------------------------------------------------------ #

# Env vars read by add_llm_args for defaults -- cleared for determinism.
_ENV_KEYS = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")


@contextlib.contextmanager
def _clean_env(**overrides):
    """Clear LLM env vars for deterministic argparse defaults."""
    with patch.dict(os.environ, {}, clear=False):
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(overrides)
        yield


class _FakeLLM:
    """Fake LLMClient: records chat() calls, returns a configurable response."""

    def __init__(self, response=None, raise_exc=None):
        self._response = response or LLMResponse(
            content="Hello from LLM",
            prompt_tokens=10,
            completion_tokens=5,
            elapsed_s=0.05,
        )
        self._raise_exc = raise_exc
        self.chat_calls: list[tuple[list, object]] = []

    def chat(self, messages, *, timeout_s=None):
        if self._raise_exc is not None:
            raise self._raise_exc
        self.chat_calls.append((messages, timeout_s))
        return self._response


def _make_config(**overrides):
    """Minimal config dict for setup()."""
    cfg = {
        "llm_base_url": "http://llm:8080",
        "llm_api_key": "test-key",
        "llm_model": "test-model",
        "llm_temperature": 0.5,
        "llm_max_tokens": 1024,
        "llm_timeout_s": 60.0,
        "llm_retries": 2,
    }
    cfg.update(overrides)
    return cfg


def _make_plugin(config=None, llm=None):
    """Create a BareLLMPlugin after setup() with a fake LLM injected."""
    plugin = BareLLMPlugin()
    plugin.setup(config or _make_config())
    plugin._llm = llm or _FakeLLM()
    return plugin


# ------------------------------------------------------------------ #
#  Tests                                                              #
# ------------------------------------------------------------------ #

class BareLLMPluginDescriptorTests(unittest.TestCase):
    """AgentDescriptor metadata declared as a class attribute."""

    def test_descriptor_fields(self):
        d = BareLLMPlugin.descriptor
        with self.subTest(field="id"):
            self.assertEqual("bare_llm", d.id)
        with self.subTest(field="name"):
            self.assertEqual("Bare LLM", d.name)
        with self.subTest(field="description"):
            self.assertEqual(
                "Stateless LLM; no agent framework, no memory retrieval.",
                d.description,
            )
        with self.subTest(field="capabilities"):
            self.assertEqual((), d.capabilities)

    def test_inherits_agent_plugin(self):
        self.assertTrue(issubclass(BareLLMPlugin, AgentPlugin))


class BareLLMPluginAddArgumentsTests(unittest.TestCase):
    """add_arguments() declares LLM + QA args, no memory backend args."""

    def _make_parser(self):
        parser = argparse.ArgumentParser(prog="test")
        BareLLMPlugin.add_arguments(parser)
        return parser

    def test_add_arguments_llm_args_defaults(self):
        with _clean_env():
            parser = self._make_parser()
            args = parser.parse_args([])
        expected = {
            "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "llm_model": "deepseek-v4-flash-0731",
            "llm_api_key": "",
            "llm_temperature": 0.7,
            "llm_max_tokens": 2048,
            "llm_timeout_s": 120.0,
            "llm_retries": 3,
        }
        for key, val in expected.items():
            with self.subTest(arg=key):
                self.assertEqual(val, getattr(args, key))

    def test_add_arguments_qa_args_defaults(self):
        with _clean_env():
            parser = self._make_parser()
            args = parser.parse_args([])
        expected = {
            "top_k": 10,
            "memory_budget_chars": 8000,
            "question_timeout_s": 120.0,
        }
        for key, val in expected.items():
            with self.subTest(arg=key):
                self.assertEqual(val, getattr(args, key))

    def test_add_arguments_no_memory_backend_args(self):
        """Memory-backend CLI flags must be absent (bare_llm has no memory)."""
        with _clean_env():
            parser = self._make_parser()
        absent_dests = [
            "memory_backend",
            "echomem_url",
            "echomem_auth_key",
            "account",
            "user_id",
            "agent_id",
            "workspace",
            "commit_timeout_s",
            "commit_poll_interval_s",
        ]
        for dest in absent_dests:
            with self.subTest(flag=dest):
                with self.assertRaises(SystemExit):
                    flag = "--" + dest.replace("_", "-")
                    parser.parse_args([flag, "x"])

    def test_add_arguments_only_expected_args(self):
        """Parser has exactly LLM + QA dests, nothing extra."""
        with _clean_env():
            parser = self._make_parser()
            args = parser.parse_args([])
        expected_dests = {
            "llm_base_url", "llm_model", "llm_api_key", "llm_temperature",
            "llm_max_tokens", "llm_timeout_s", "llm_retries",
            "top_k", "memory_budget_chars", "question_timeout_s",
        }
        actual_dests = {
            a.dest for a in parser._actions if a.dest != "help"
        }
        self.assertEqual(expected_dests, actual_dests)


class BareLLMPluginSetupTests(unittest.TestCase):
    """setup() builds LLMClient, NullMemoryClient, resets session counter."""

    @patch("plugins.bare_llm.plugin.LLMClient")
    def test_setup_creates_llm_client_with_config(self, mock_cls):
        cfg = _make_config()
        plugin = BareLLMPlugin()
        plugin.setup(cfg)
        mock_cls.assert_called_once_with(
            base_url="http://llm:8080",
            api_key="test-key",
            model="test-model",
            temperature=0.5,
            max_tokens=1024,
            timeout_s=60.0,
            max_retries=2,
        )
        self.assertIs(mock_cls.return_value, plugin._llm)

    @patch("plugins.bare_llm.plugin.LLMClient")
    def test_setup_uses_defaults_for_missing_keys(self, mock_cls):
        plugin = BareLLMPlugin()
        plugin.setup({})
        mock_cls.assert_called_once_with(
            base_url="",
            api_key="",
            model="doubao-seed-2.0-pro",
            temperature=0.7,
            max_tokens=2048,
            timeout_s=120.0,
            max_retries=3,
        )

    def test_setup_creates_null_memory_client(self):
        plugin = BareLLMPlugin()
        plugin.setup(_make_config())
        self.assertIsInstance(plugin.memory_client, NullMemoryClient)

    def test_setup_session_count_zero(self):
        plugin = BareLLMPlugin()
        plugin.setup(_make_config())
        self.assertEqual(0, plugin._session_count)


class BareLLMPluginCreateSessionTests(unittest.TestCase):
    """create_session() returns incrementing 'bare_llm_session_{N}' IDs."""

    def test_create_session_first_id(self):
        plugin = _make_plugin()
        self.assertEqual("bare_llm_session_1", plugin.create_session())

    def test_create_session_unique_incrementing(self):
        plugin = _make_plugin()
        ids = [plugin.create_session() for _ in range(5)]
        self.assertEqual(
            ["bare_llm_session_1", "bare_llm_session_2",
             "bare_llm_session_3", "bare_llm_session_4",
             "bare_llm_session_5"],
            ids,
        )
        self.assertEqual(5, plugin._session_count)

    def test_create_session_ignores_title(self):
        plugin = _make_plugin()
        id_no_title = plugin.create_session()
        id_with_title = plugin.create_session(title="my session")
        # Title is accepted but does not appear in the session ID.
        self.assertNotIn("my session", id_with_title)
        self.assertTrue(id_with_title.startswith("bare_llm_session_"))


class BareLLMPluginTypingTests(unittest.TestCase):
    """bare_llm does not support typing simulation / prefill."""

    def test_supports_typing_simulation_false(self):
        plugin = _make_plugin()
        self.assertFalse(plugin.supports_typing_simulation)

    def test_simulate_typing_returns_none(self):
        plugin = _make_plugin()
        result = plugin.simulate_typing("s1", "/", "hello", 200, 20)
        self.assertIsNone(result)


class BareLLMPluginSendMessageTests(unittest.TestCase):
    """send_message() issues a single LLM call and wraps the result."""

    def test_send_message_calls_chat_with_correct_messages(self):
        fake = _FakeLLM()
        plugin = _make_plugin(llm=fake)
        plugin.send_message("s1", "What is 2+2?")
        self.assertEqual(1, len(fake.chat_calls))
        messages, _timeout = fake.chat_calls[0]
        self.assertEqual(2, len(messages))
        self.assertEqual("system", messages[0]["role"])
        self.assertEqual(_SYSTEM_PROMPT, messages[0]["content"])
        self.assertEqual("user", messages[1]["role"])
        self.assertEqual("What is 2+2?", messages[1]["content"])

    def test_send_message_response_fields(self):
        fake = _FakeLLM(response=LLMResponse(
            content="The answer is 4.",
            prompt_tokens=12,
            completion_tokens=7,
            elapsed_s=0.03,
        ))
        plugin = _make_plugin(llm=fake)
        resp = plugin.send_message("s1", "What is 2+2?")
        self.assertIsInstance(resp, AgentResponse)
        with self.subTest(field="text"):
            self.assertEqual("The answer is 4.", resp.text)
        with self.subTest(field="prompt_tokens"):
            self.assertEqual(12, resp.prompt_tokens)
        with self.subTest(field="completion_tokens"):
            self.assertEqual(7, resp.completion_tokens)
        with self.subTest(field="cached_tokens"):
            self.assertEqual(0, resp.cached_tokens)
        with self.subTest(field="prefetch_committed"):
            self.assertFalse(resp.prefetch_committed)
        with self.subTest(field="memory_items"):
            self.assertEqual([], resp.memory_items)
        with self.subTest(field="error"):
            self.assertIsNone(resp.error)
        with self.subTest(field="extra_has_elapsed_s"):
            self.assertIn("elapsed_s", resp.extra)

    def test_send_message_ttft_ms_is_non_negative_float(self):
        fake = _FakeLLM()
        plugin = _make_plugin(llm=fake)
        resp = plugin.send_message("s1", "hi")
        self.assertIsInstance(resp.ttft_ms, float)
        self.assertGreaterEqual(resp.ttft_ms, 0.0)

    def test_send_message_error_from_llm(self):
        fake = _FakeLLM(response=LLMResponse(
            content="",
            prompt_tokens=0,
            completion_tokens=0,
            elapsed_s=0.01,
            error="HTTP 500: internal error",
        ))
        plugin = _make_plugin(llm=fake)
        resp = plugin.send_message("s1", "hi")
        self.assertEqual("HTTP 500: internal error", resp.error)

    def test_send_message_no_error_when_empty_string(self):
        """resp.error='' is falsy -> AgentResponse.error becomes None."""
        fake = _FakeLLM(response=LLMResponse(
            content="ok",
            prompt_tokens=1,
            completion_tokens=1,
            elapsed_s=0.01,
            error="",
        ))
        plugin = _make_plugin(llm=fake)
        resp = plugin.send_message("s1", "hi")
        self.assertIsNone(resp.error)

    def test_send_message_exception_propagates(self):
        """If chat() raises, send_message does not swallow the exception."""
        fake = _FakeLLM(raise_exc=RuntimeError("connection refused"))
        plugin = _make_plugin(llm=fake)
        with self.assertRaises(RuntimeError):
            plugin.send_message("s1", "hi")

    def test_send_message_accepts_extra_variants(self):
        """extra=None and extra=dict must both be accepted without error."""
        fake = _FakeLLM()
        plugin = _make_plugin(llm=fake)
        for extra in (None, {"question_id": "q1", "category": "math"}):
            with self.subTest(extra=extra):
                resp = plugin.send_message("s1", "hi", extra=extra)
                self.assertIsInstance(resp, AgentResponse)

    def test_send_message_extra_only_contains_elapsed_s(self):
        """Input *extra* is not propagated; response extra only has elapsed_s."""
        fake = _FakeLLM()
        plugin = _make_plugin(llm=fake)
        resp = plugin.send_message("s1", "hi", extra={"secret": "x"})
        self.assertEqual(["elapsed_s"], list(resp.extra.keys()))
        self.assertIsInstance(resp.extra["elapsed_s"], float)

    def test_send_message_accepts_context_path(self):
        """context_path is accepted but does not affect the LLM call."""
        fake = _FakeLLM()
        plugin = _make_plugin(llm=fake)
        plugin.send_message("s1", "hi", context_path="/deep/path")
        messages, _ = fake.chat_calls[0]
        self.assertEqual(2, len(messages))

    def test_send_message_empty_message(self):
        fake = _FakeLLM()
        plugin = _make_plugin(llm=fake)
        resp = plugin.send_message("s1", "")
        self.assertIsInstance(resp, AgentResponse)
        messages, _ = fake.chat_calls[0]
        self.assertEqual("", messages[1]["content"])

    def test_send_message_large_message(self):
        fake = _FakeLLM()
        plugin = _make_plugin(llm=fake)
        big = "x" * 100_000
        resp = plugin.send_message("s1", big)
        self.assertIsInstance(resp, AgentResponse)
        messages, _ = fake.chat_calls[0]
        self.assertEqual(big, messages[1]["content"])

    def test_send_message_returns_independent_objects(self):
        """Each call returns a fresh AgentResponse with its own extra dict."""
        fake = _FakeLLM()
        plugin = _make_plugin(llm=fake)
        r1 = plugin.send_message("s1", "a")
        r2 = plugin.send_message("s1", "b")
        self.assertIsNot(r1, r2)
        self.assertIsNot(r1.extra, r2.extra)
        self.assertEqual(2, len(fake.chat_calls))


class BareLLMPluginInheritedMethodsTests(unittest.TestCase):
    """inject_memories, qa_profile, teardown -- inherited from AgentPlugin."""

    def test_inject_memories_returns_session_id_unchanged(self):
        plugin = _make_plugin()
        result = plugin.inject_memories(
            [{"text": "memory"}], backend="echomem", session_id="my_session",
        )
        self.assertEqual("my_session", result)

    def test_inject_memories_default_session_id(self):
        plugin = _make_plugin()
        result = plugin.inject_memories([{"text": "m"}])
        self.assertEqual("", result)

    def test_qa_profile_returns_descriptor_id(self):
        plugin = _make_plugin()
        self.assertEqual("bare_llm", plugin.qa_profile)

    def test_teardown_is_noop(self):
        plugin = _make_plugin()
        # Must not raise.
        plugin.teardown()


class BareLLMPluginIntegrationTests(unittest.TestCase):
    """Multi-step lifecycle: setup -> create_session -> send_message."""

    def test_multiple_sessions_then_send(self):
        fake = _FakeLLM()
        plugin = _make_plugin(llm=fake)
        s1 = plugin.create_session()
        s2 = plugin.create_session()
        s3 = plugin.create_session()
        self.assertEqual("bare_llm_session_1", s1)
        self.assertEqual("bare_llm_session_2", s2)
        self.assertEqual("bare_llm_session_3", s3)

        resp1 = plugin.send_message(s1, "first question")
        resp2 = plugin.send_message(s2, "second question")
        self.assertIsInstance(resp1, AgentResponse)
        self.assertIsInstance(resp2, AgentResponse)
        self.assertEqual(2, len(fake.chat_calls))

    def test_send_message_uses_same_system_prompt_every_call(self):
        fake = _FakeLLM()
        plugin = _make_plugin(llm=fake)
        plugin.send_message("s1", "q1")
        plugin.send_message("s1", "q2")
        for idx, (messages, _) in enumerate(fake.chat_calls):
            with self.subTest(call=idx):
                self.assertEqual(_SYSTEM_PROMPT, messages[0]["content"])

    def test_null_memory_client_returns_empty_results(self):
        """memory_client is a real NullMemoryClient that returns empties."""
        plugin = _make_plugin()
        mc = plugin.memory_client
        self.assertEqual([], mc.search("query"))
        self.assertEqual("", mc.fs_read("uri"))
        self.assertEqual([], mc.fs_list("uri"))
        self.assertEqual([], mc.fs_glob("pattern"))
        self.assertEqual({"status": "ok"}, mc.health())


if __name__ == "__main__":
    unittest.main()
