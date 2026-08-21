from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import load_agent_plugin
from plugins.vikingbot.answers import sanitize_final_answer_text
from plugins.vikingbot.prompting import build_system_prompt
from plugins.vikingbot.prompting import build_messages
from plugins.vikingbot.runtime import answer_one_vikingbot_question
from plugins.vikingbot.tools import _engine_uri, execute_tool, tool_definitions
from plugins.vikingbot.vikingboat0411_prompting import format_vikingbot_memory
from plugins.vikingbot.vikingboat0411_prompting import (
    format_natural_no_tools_memory,
)
from backends.memory_types import SearchResult
from benchmarks.locomo.profiles import (
    AGENT_PLUGIN,
    VIKINGBOAT_0411_PROFILE,
    VIKINGBOAT_0411_SETTINGS,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
    PROFILE_SPECS,
    ProfileSettings,
    default_vikingbot_workspace,
)


class _FakeEchoMem:
    def __init__(self):
        self.queries: list[str] = []

    def search(self, query, **_kwargs):
        self.queries.append(query)
        return [
            SearchResult(
                uri="echo://account/sessions/session-1",
                score=1.0,
                content="Jon lost his job on 19 January 2023.",
                memory_type="atomic",
            )
        ]

    def fs_read(self, _uri, **_kwargs):
        return "Jon lost his job on 19 January 2023."


class _FakeLLM:
    base_url = "https://example.test/v1"
    api_key = "test"
    model = "test-model"
    temperature = 0.7
    max_tokens = 1024
    timeout_s = 120.0
    max_retries = 5


class VikingBotCoreTests(unittest.TestCase):
    def test_sanitizes_tool_loop_residue(self):
        self.assertEqual(
            "Jon offers dance classes and workshops.",
            sanitize_final_answer_text(
                "Let me search memory. "
                "Jon offers dance classes and workshops."
            ),
        )
        self.assertEqual(
            "19 January 2023",
            sanitize_final_answer_text(
                "Based on the retrieved memories, 19 January 2023"
            ),
        )

    def test_loads_bootstrap_files(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "SOUL.md").write_text("# Soul\nHistorical soul", encoding="utf-8")
            (workspace / "TOOLS.md").write_text("# Tools\nHistorical tools", encoding="utf-8")

            prompt = build_system_prompt(str(workspace))

        self.assertIn("# vikingbot", prompt)
        self.assertIn("## SOUL.md", prompt)
        self.assertIn("Historical soul", prompt)
        self.assertIn("## TOOLS.md", prompt)
        self.assertIn("Historical tools", prompt)

    def test_default_bootstrap_is_vendored_in_this_repository(self):
        workspace = Path(default_vikingbot_workspace())

        self.assertEqual("bootstrap", workspace.name)
        self.assertTrue((workspace / "SOUL.md").is_file())
        self.assertTrue((workspace / "TOOLS.md").is_file())
        self.assertNotIn("/Code/openviking/", str(workspace))

    def test_uses_original_question_then_historical_tool_loop(self):
        echomem = _FakeEchoMem()
        calls = iter([
            (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "memory_search",
                            "arguments": json.dumps({"query": "Jon banker job loss"}),
                        },
                    }],
                },
                100,
                20,
            ),
            ({"role": "assistant", "content": "19 January 2023"}, 120, 10),
        ])

        with patch(
            "plugins.vikingbot.runtime.chat_with_tools",
            side_effect=lambda *_args: next(calls),
        ):
            result = answer_one_vikingbot_question(
                echomem,
                _FakeLLM(),
                question_id="q1",
                question="When did Jon lose his banking job?",
                answer="19 January 2023",
                question_time="2023-07-23",
                vikingbot_workspace="",
                qa_profile=VIKINGBOAT_0411_PROFILE,
            )

        self.assertEqual(
            ["When did Jon lose his banking job?", "Jon banker job loss"],
            echomem.queries,
        )
        self.assertEqual("19 January 2023", result.response)
        self.assertEqual(1, result.tool_call_count)
        self.assertEqual(2, result.iterations)
        self.assertEqual(VIKINGBOAT_0411_PROFILE, result.qa_profile)
        self.assertEqual(220, result.prompt_tokens)
        self.assertEqual(30, result.completion_tokens)
        self.assertEqual("vikingbot", result.trace["agent"])
        self.assertEqual(2, len(result.trace["iterations"]))
        self.assertEqual(
            "memory_search",
            result.trace["iterations"][0]["tool_calls"][0]["name"],
        )
        self.assertEqual(
            "19 January 2023",
            result.trace["final_response"],
        )

    def test_tool_audit_distinguishes_discovered_and_read_files(self):
        class AuditedEchoMem(_FakeEchoMem):
            def fs_glob(self, _pattern, **_kwargs):
                return [{
                    "name": "messages.jsonl",
                    "size": 42,
                    "uri": "echo://sessions/s1/current/messages.jsonl",
                    "kind": "file",
                }]

            def fs_list(self, _uri, **_kwargs):
                return [{
                    "name": "messages.jsonl",
                    "size": 42,
                    "uri": "echo://sessions/s1/current/messages.jsonl",
                    "kind": "file",
                }]

        echomem = AuditedEchoMem()
        calls = iter([
            (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "list-1",
                        "type": "function",
                        "function": {
                            "name": "memory_list",
                            "arguments": json.dumps({
                                "uri": "echo://sessions/s1",
                                "recursive": False,
                            }),
                        },
                    }],
                },
                1,
                1,
            ),
            (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "read-1",
                        "type": "function",
                        "function": {
                            "name": "memory_read_many",
                            "arguments": json.dumps({
                                "uris": [
                                    "echo://sessions/s1/current/messages.jsonl"
                                ],
                            }),
                        },
                    }],
                },
                1,
                1,
            ),
            ({"role": "assistant", "content": "done"}, 1, 1),
        ])

        with patch(
            "plugins.vikingbot.runtime.chat_with_tools",
            side_effect=lambda *_args, **_kwargs: next(calls),
        ):
            result = answer_one_vikingbot_question(
                echomem,
                _FakeLLM(),
                question_id="q-audit",
                question="Question",
                answer="done",
                tool_set="vikingbot_native_safe",
            )

        audit = result.trace["tool_audit"]
        self.assertEqual(
            ["memory_list", "memory_read_many"],
            audit["tools_used"],
        )
        self.assertEqual(
            ["echo://sessions/s1/current/messages.jsonl"],
            [
                row["uri"]
                for row in audit["discovered_files"]
                if row["uri"].endswith("messages.jsonl")
            ],
        )
        self.assertEqual(
            ["echo://sessions/s1/current/messages.jsonl"],
            [row["uri"] for row in audit["read_files"]],
        )
        self.assertEqual(
            ["memory_list"],
            next(
                row for row in audit["discovered_files"]
                if row["uri"].endswith("messages.jsonl")
            )["tools"],
        )
        self.assertEqual(
            ["memory_read_many"],
            audit["read_files"][0]["tools"],
        )

    def test_profile_resolves_vikingbot_plugin(self):
        plugin = load_agent_plugin(AGENT_PLUGIN, {})

        self.assertEqual("vikingbot", plugin.descriptor.id)

class VikingBotRuntimeTests(unittest.TestCase):
    def test_same_turn_tools_execute_concurrently_in_original_order(self):
        barrier = threading.Barrier(2)
        worker_threads: list[int] = []
        calls = iter([
            (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "memory_search",
                                "arguments": json.dumps({"query": "first"}),
                            },
                        },
                        {
                            "id": "call-2",
                            "type": "function",
                            "function": {
                                "name": "memory_search",
                                "arguments": json.dumps({"query": "second"}),
                            },
                        },
                    ],
                },
                1,
                1,
            ),
            ({"role": "assistant", "content": "done"}, 1, 1),
        ])

        def fake_execute(_client, _name, arguments, _cache, **_kwargs):
            worker_threads.append(threading.get_ident())
            barrier.wait(timeout=2)
            return arguments["query"], []

        with (
            patch(
                "plugins.vikingbot.runtime.chat_with_tools",
                side_effect=lambda *_args, **_kwargs: next(calls),
            ),
            patch(
                "plugins.vikingbot.runtime.execute_tool",
                side_effect=fake_execute,
            ),
        ):
            result = answer_one_vikingbot_question(
                _FakeEchoMem(),
                _FakeLLM(),
                question_id="q1",
                question="Question",
                answer="done",
                qa_profile=VIKINGBOAT_0411_PROFILE,
                tool_query_dedup_scope="turn",
            )

        self.assertEqual("", result.llm_error)
        self.assertEqual(2, len(set(worker_threads)))
        tool_rows = result.trace["iterations"][0]["tool_calls"]
        self.assertEqual(["first", "second"], [
            row["result"] for row in tool_rows
        ])
        self.assertEqual(
            max(row["latency_ms"] for row in tool_rows),
            result.trace["iterations"][0]["tool_batch_latency_ms"],
        )

    def test_trace_records_provider_response_identity(self):
        metadata = {
            "request_sha256": "abc",
            "request_model": "requested-model",
            "response_model": "served-model",
            "response_id": "response-1",
        }
        with patch(
            "plugins.vikingbot.runtime.chat_with_tools",
            return_value=(
                {"role": "assistant", "content": "done"},
                1,
                1,
                0,
                0.1,
                True,
                metadata,
            ),
        ):
            result = answer_one_vikingbot_question(
                _FakeEchoMem(),
                _FakeLLM(),
                question_id="q1",
                question="Question",
                answer="done",
                qa_profile=VIKINGBOAT_0411_PROFILE,
            )

        self.assertEqual(
            "served-model",
            result.trace["iterations"][0]["model_response"]["response_model"],
        )
        self.assertEqual(
            "https://example.test/v1",
            result.trace["model_request"]["base_url"],
        )
        self.assertEqual(
            ["memory_search", "memory_read_many"],
            result.trace["tool_protocol"]["names"],
        )
        self.assertEqual(
            64,
            len(result.trace["tool_protocol"]["sha256"]),
        )

    def test_engine_uri_maps_tenant_and_file_paths(self):
        self.assertEqual(
            "echo://sessions/session-1/messages.jsonl",
            _engine_uri(
                "echo://tenant-a/sessions/session-1/messages.jsonl",
                leaf_pattern="*/current/messages.jsonl",
            ),
        )
        self.assertEqual(
            "echo://sessions/*/current/messages.jsonl",
            _engine_uri(
                "echo://tenant-a/memory/entities/Jon.md",
                leaf_pattern="*/current/messages.jsonl",
            ),
        )
        for uri in (
            "echo://tenant-a/sessions",
            "echo://tenant-a/sessions/",
        ):
            with self.subTest(uri=uri):
                self.assertEqual(
                    "echo://sessions/*/current/messages.jsonl",
                    _engine_uri(uri, leaf_pattern="*/current/messages.jsonl"),
                )

    def test_read_many_maps_exact_session_file(self):
        class ReadEchoMem(_FakeEchoMem):
            def __init__(self):
                super().__init__()
                self.read_uris: list[str] = []

            def fs_read(self, uri, **_kwargs):
                self.read_uris.append(uri)
                return "message content"

        echomem = ReadEchoMem()
        text, _items = execute_tool(
            echomem,
            "memory_read_many",
            {
                "uris": [
                    "echo://tenant-a/sessions/session-1/messages.jsonl"
                ]
            },
            {},
            top_k=25,
            tool_search_limit=20,
            tool_search_pool_multiplier=1,
            tool_min_score=0.0,
            timeout_s=30,
        )

        self.assertEqual(
            [
                "echo://sessions/session-1/current/messages.jsonl"
            ],
            echomem.read_uris,
        )
        self.assertIn("message content", text)

    def test_search_persists_only_items_exposed_in_tool_payload(self):
        class SearchEchoMem(_FakeEchoMem):
            def search(self, _query, **_kwargs):
                return [
                    SearchResult(
                        uri="echo://session/1",
                        score=1.0,
                        content="first",
                    ),
                    SearchResult(
                        uri="echo://session/1",
                        score=0.9,
                        content="duplicate session",
                    ),
                    SearchResult(
                        uri="echo://session/2",
                        score=0.8,
                        content="second",
                    ),
                    SearchResult(
                        uri="echo://session/3",
                        score=0.7,
                        content="not exposed",
                    ),
                ]

        text, exposed = execute_tool(
            SearchEchoMem(),
            "memory_search",
            {"query": "query"},
            {},
            top_k=4,
            tool_search_limit=2,
            tool_search_pool_multiplier=1,
            tool_min_score=0.0,
            timeout_s=30,
        )

        payload = json.loads(text)
        self.assertEqual(2, payload["count"])
        self.assertEqual(
            ["echo://session/1", "echo://session/2"],
            [item.uri for item in exposed],
        )

    def test_read_many_rejects_sessions_root_without_http_read(self):
        class ReadEchoMem(_FakeEchoMem):
            def __init__(self):
                super().__init__()
                self.glob_patterns: list[str] = []

            def fs_read(self, _uri, **_kwargs):
                raise AssertionError("sessions root must not be read")

            def fs_glob(self, pattern, **_kwargs):
                self.glob_patterns.append(pattern)
                return [{
                    "uri": (
                        "echo://sessions/session-1/"
                        "current/messages.jsonl"
                    )
                }]

        for uri in (
            "echo://tenant-a/sessions/",
            "echo://sessions",
        ):
            with self.subTest(uri=uri):
                echomem = ReadEchoMem()
                text, _items = execute_tool(
                    echomem,
                    "memory_read_many",
                    {"uris": [uri]},
                    {},
                    top_k=25,
                    tool_search_limit=20,
                    tool_search_pool_multiplier=1,
                    tool_min_score=0.0,
                    timeout_s=30,
                )

                self.assertIn("concrete EchoMemory session or file URI", text)
                self.assertIn("session-1/current/messages.jsonl", text)
                self.assertEqual(
                    [
                        "echo://sessions/*/current/messages.jsonl"
                    ],
                    echomem.glob_patterns,
                )

    def test_read_many_lists_matching_sessions_for_ambiguous_prefix(self):
        class ReadEchoMem(_FakeEchoMem):
            def __init__(self):
                super().__init__()
                self.glob_patterns: list[str] = []

            def fs_read(self, _uri, **_kwargs):
                raise AssertionError("ambiguous prefix must not be read")

            def fs_glob(self, pattern, **_kwargs):
                self.glob_patterns.append(pattern)
                return [{
                    "uri": (
                        "echo://sessions/"
                        "echomem-locomo-conv-30-s1-c96138b0/current/messages.jsonl"
                    )
                }]

        echomem = ReadEchoMem()
        text, _items = execute_tool(
            echomem,
            "memory_read_many",
            {
                "uris": [
                    "echo://sessions/echomem-locomo-conv-30"
                ]
            },
            {},
            top_k=25,
            tool_search_limit=20,
            tool_search_pool_multiplier=1,
            tool_min_score=0.0,
            timeout_s=30,
        )

        self.assertIn(
            "echomem-locomo-conv-30-s1-c96138b0/current/messages.jsonl",
            text,
        )
        self.assertEqual(
            [
                "echo://sessions/echomem-locomo-conv-30*/current/messages.jsonl"
            ],
            echomem.glob_patterns,
        )

    def test_list_resolves_single_session_prefix_before_listing(self):
        class ListEchoMem(_FakeEchoMem):
            def __init__(self):
                super().__init__()
                self.list_uris: list[str] = []

            def fs_glob(self, _pattern, **_kwargs):
                return [{
                    "uri": (
                        "echo://sessions/"
                        "session-1-abc123/current/messages.jsonl"
                    )
                }]

            def fs_list(self, uri, **_kwargs):
                self.list_uris.append(uri)
                return []

        echomem = ListEchoMem()
        execute_tool(
            echomem,
            "memory_list",
            {"uri": "echo://tenant-a/sessions/session-1"},
            {},
            top_k=25,
            tool_search_limit=20,
            tool_search_pool_multiplier=1,
            tool_min_score=0.0,
            timeout_s=30,
        )

        self.assertEqual(
            ["echo://sessions/session-1-abc123"],
            echomem.list_uris,
        )

    def test_grep_expands_ambiguous_session_prefix_before_reading(self):
        class GrepEchoMem(_FakeEchoMem):
            def __init__(self):
                super().__init__()
                self.read_uris: list[str] = []

            def fs_glob(self, _pattern, **_kwargs):
                return [{
                    "uri": (
                        "echo://sessions/"
                        "session-1-abc123/current/messages.jsonl"
                    )
                }]

            def fs_read(self, uri, **_kwargs):
                self.read_uris.append(uri)
                return "Jon mentioned Marley flooring."

        echomem = GrepEchoMem()
        text, _items = execute_tool(
            echomem,
            "memory_grep",
            {
                "uri": "echo://tenant-a/sessions/session-1",
                "pattern": ["Marley"],
            },
            {},
            top_k=25,
            tool_search_limit=20,
            tool_search_pool_multiplier=1,
            tool_min_score=0.0,
            timeout_s=30,
        )

        self.assertIn("Marley flooring", text)
        self.assertEqual(
            [
                "echo://sessions/session-1-abc123/current/messages.jsonl"
            ],
            echomem.read_uris,
        )

    def test_read_many_does_not_guess_when_session_prefix_has_no_match(self):
        class ReadEchoMem(_FakeEchoMem):
            def fs_glob(self, _pattern, **_kwargs):
                return []

            def fs_read(self, _uri, **_kwargs):
                raise AssertionError("unmatched session must not be read")

        text, _items = execute_tool(
            ReadEchoMem(),
            "memory_read_many",
            {
                "uris": [
                    "echo://tenant-a/sessions/session-does-not-exist"
                ]
            },
            {},
            top_k=25,
            tool_search_limit=20,
            tool_search_pool_multiplier=1,
            tool_min_score=0.0,
            timeout_s=30,
        )

        self.assertIn("No concrete EchoMemory session matched", text)


class VikingBoat0411Tests(unittest.TestCase):
    def test_local_prompt_file_content_only_appends_to_system_message(self):
        baseline = build_messages(
            "What happened?",
            "2023-07-23",
            [],
            4000,
            2000,
            "",
            VIKINGBOAT_0411_PROFILE,
        )
        candidate = build_messages(
            "What happened?",
            "2023-07-23",
            [],
            4000,
            2000,
            "",
            VIKINGBOAT_0411_PROFILE,
            "Use a second focused retrieval pass.",
        )

        self.assertEqual(baseline[1:], candidate[1:])
        self.assertTrue(
            candidate[0]["content"].startswith(baseline[0]["content"])
        )
        self.assertTrue(
            candidate[0]["content"].endswith(
                "Use a second focused retrieval pass."
            )
        )

    def test_profile_uses_echomemory_names_and_vikingbot_runtime_contract(self):
        self.assertEqual("vikingboat0411", VIKINGBOAT_0411_PROFILE)
        self.assertEqual(
            "vikingbot_echo_native",
            VIKINGBOAT_0411_SETTINGS["tool_set"],
        )
        self.assertEqual(50, VIKINGBOAT_0411_SETTINGS["max_iterations"])
        self.assertEqual(4096, VIKINGBOAT_0411_SETTINGS["llm_max_tokens"])
        self.assertEqual(4000, VIKINGBOAT_0411_SETTINGS["user_memory_budget_chars"])
        self.assertEqual(0.7, VIKINGBOAT_0411_SETTINGS["answer_temperature"])
        self.assertFalse(VIKINGBOAT_0411_SETTINGS["omit_answer_temperature"])
        self.assertEqual(
            (
                "memory_search",
                "memory_read_many",
                "memory_list",
                "memory_grep",
                "memory_glob",
            ),
            VIKINGBOAT_0411_SETTINGS["tool_names"],
        )

    def test_prompt_keeps_native_rules_without_locomo_heuristics(self):
        messages = build_messages(
            "When did Jon lose his job?",
            "2023-07-23",
            [SearchResult(
                uri="echo://account/sessions/session-1",
                score=0.8,
                content="Jon lost his job in January.",
            )],
            4500,
            2000,
            "",
            VIKINGBOAT_0411_PROFILE,
        )
        prompt = "\n".join(str(item["content"]) for item in messages)

        self.assertIn("# vikingbot", prompt)
        self.assertIn("## memory_search(query=[user_query])", prompt)
        self.assertIn("Current date: 2023-07-23. Answer the question directly", prompt)
        self.assertIn("use memory_read_many on their URIs", prompt)
        self.assertNotIn("openviking_", prompt.lower())
        self.assertNotIn("without 'when'", prompt)
        self.assertNotIn("search each person/fact separately", prompt)
        self.assertNotIn("up to 20", prompt)
        self.assertNotIn("Before replying 'unknown'", prompt)
        self.assertNotIn("'ideal', 'dream', 'looking for', or 'should'", prompt)
        self.assertNotIn("smallest exact final answer", prompt)

    def test_tool_schemas_keep_vikingbot_shapes_with_echomemory_names(self):
        tools = tool_definitions("vikingbot_echo_native", search_target_uri=True)
        by_name = {
            tool["function"]["name"]: tool["function"]
            for tool in tools
        }

        self.assertEqual(set(VIKINGBOAT_0411_SETTINGS["tool_names"]), set(by_name))
        self.assertEqual(
            ["query"],
            by_name["memory_search"]["parameters"]["required"],
        )
        self.assertEqual(
            0.35,
            by_name["memory_search"]["parameters"]["properties"]["min_score"]["default"],
        )
        self.assertEqual(
            [],
            by_name["memory_list"]["parameters"]["required"],
        )
        self.assertEqual(
            "string",
            by_name["memory_grep"]["parameters"]["properties"]["pattern"]["type"],
        )

    def test_memory_search_executes_against_echomemory(self):
        echomem = _FakeEchoMem()
        text, items = execute_tool(
            echomem,
            "memory_search",
            {"query": "Jon job", "min_score": 0.35},
            {},
            top_k=25,
            tool_search_limit=25,
            tool_search_pool_multiplier=1,
            tool_min_score=0.35,
            timeout_s=30,
        )

        self.assertEqual(["Jon job"], echomem.queries)
        self.assertEqual(1, len(items))
        self.assertIn("session-1", text)

    def test_unified_top25_keeps_all_memory_types_and_uri_degradation(self):
        items = [
            SearchResult(
                uri=f"echo://memory/item-{index}.md",
                score=1.0 - index / 100,
                content=f"memory {index}",
                memory_type=("custom" if index % 2 else "atomic"),
            )
            for index in range(30)
        ]
        memory = format_vikingbot_memory(
            items,
            1,
        )

        self.assertEqual(25, memory.count("<memory index="))
        self.assertIn("echo://memory/item-0.md", memory)
        self.assertIn("echo://memory/item-24.md", memory)
        self.assertNotIn("echo://memory/item-25.md", memory)
        self.assertNotIn("<memory_group", memory)
        self.assertNotIn('type="summary"', memory)
        self.assertEqual(25, memory.count('type="uri"'))

    def test_final_answer_is_not_rewritten_for_vikingboat0411(self):
        with patch(
            "plugins.vikingbot.runtime.chat_with_tools",
            return_value=(
                {"role": "assistant", "content": "**Original answer**"},
                10,
                2,
            ),
        ):
            result = answer_one_vikingbot_question(
                _FakeEchoMem(),
                _FakeLLM(),
                question_id="q1",
                question="What happened?",
                answer="Original answer",
                qa_profile=VIKINGBOAT_0411_PROFILE,
                tool_set="vikingbot_echo_native",
            )

        self.assertEqual("**Original answer**", result.response)
        self.assertFalse(result.trace["answer_sanitized"])

    def test_iteration_limit_forces_final_answer_without_tools(self):
        captured_tools: list[list[dict]] = []
        calls = iter([
            (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "memory_search",
                            "arguments": json.dumps({"query": "Jon job"}),
                        },
                    }],
                },
                10,
                2,
            ),
            (
                {"role": "assistant", "content": "19 January 2023"},
                12,
                3,
            ),
        ])

        def fake_chat(_llm, messages, tools, _timeout, **_kwargs):
            captured_tools.append(tools)
            if not tools:
                self.assertIn(
                    "Tool-use iteration limit reached.",
                    messages[-1]["content"],
                )
            return next(calls)

        with patch(
            "plugins.vikingbot.runtime.chat_with_tools",
            side_effect=fake_chat,
        ):
            result = answer_one_vikingbot_question(
                _FakeEchoMem(),
                _FakeLLM(),
                question_id="q-limit",
                question="When did Jon lose his job?",
                answer="19 January 2023",
                qa_profile=VIKINGBOAT_0411_PROFILE,
                tool_set="vikingbot_echo_native",
                max_iterations=1,
            )

        self.assertTrue(captured_tools[0])
        self.assertEqual([], captured_tools[1])
        self.assertEqual("19 January 2023", result.response)
        self.assertEqual(1, result.iterations)
        self.assertEqual(1, result.tool_call_count)
        self.assertEqual(22, result.prompt_tokens)
        self.assertEqual(5, result.completion_tokens)
        self.assertEqual(
            "19 January 2023",
            result.trace["forced_final_answer"]["model_message"]["content"],
        )

    def test_no_tools_keeps_profile_prompt_and_runs_one_model_turn(self):
        captured_tools: list[list[dict]] = []

        def fake_chat(_llm, messages, tools, _timeout, **_kwargs):
            captured_tools.append(tools)
            self.assertIn("# vikingbot", messages[0]["content"])
            return (
                {"role": "assistant", "content": "19 January 2023"},
                10,
                2,
            )

        with patch(
            "plugins.vikingbot.runtime.chat_with_tools",
            side_effect=fake_chat,
        ):
            result = answer_one_vikingbot_question(
                _FakeEchoMem(),
                _FakeLLM(),
                question_id="q1",
                question="When did Jon lose his job?",
                answer="19 January 2023",
                qa_profile=VIKINGBOAT_0411_PROFILE,
                tool_set="vikingbot_echo_native",
                tools_enabled=False,
            )

        self.assertEqual([[]], captured_tools)
        self.assertEqual("19 January 2023", result.response)
        self.assertEqual(0, result.tool_call_count)
        self.assertEqual(1, result.iterations)
        self.assertFalse(result.trace["settings"]["tools_enabled"])
        self.assertEqual([], result.trace["tool_protocol"]["names"])

    def test_natural_no_tools_prompt_has_only_complete_memory(self):
        items = [
            SearchResult(
                uri="echo://memory/full",
                score=0.9,
                content="Jon lost his job on 19 January 2023.",
            ),
            SearchResult(
                uri="echo://memory/uri-only",
                score=0.8,
                content="",
            ),
        ]
        messages = build_messages(
            "When did Jon lose his job?",
            "2023-07-23",
            items,
            4000,
            2000,
            "",
            VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
        )
        prompt = "\n".join(str(item["content"]) for item in messages)

        self.assertIn("Jon lost his job on 19 January 2023.", prompt)
        self.assertNotIn("echo://memory/full", prompt)
        self.assertNotIn("echo://memory/uri-only", prompt)
        self.assertNotIn("memory_search", prompt)
        self.assertNotIn("memory_read_many", prompt)
        self.assertIn("using only the complete memory excerpts", prompt)

    def test_natural_no_tools_memory_skips_over_budget_entries(self):
        memory = format_natural_no_tools_memory(
            [
                SearchResult(
                    uri="echo://memory/too-large",
                    score=0.9,
                    content="x" * 100,
                ),
                SearchResult(
                    uri="echo://memory/fits",
                    score=0.8,
                    content="short fact",
                ),
            ],
            80,
        )

        self.assertNotIn("x" * 20, memory)
        self.assertIn("short fact", memory)
        self.assertNotIn("echo://", memory)


class ProfileSchemaTests(unittest.TestCase):
    def test_all_registered_profiles_are_validated(self):
        self.assertEqual(
            {
                VIKINGBOAT_0411_PROFILE,
                VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
            },
            set(PROFILE_SPECS),
        )
        for spec in PROFILE_SPECS.values():
            with self.subTest(profile=spec.name):
                spec.settings.validate()

    def test_rejects_unknown_profile_field(self):
        values = dict(VIKINGBOAT_0411_SETTINGS)
        values["tool_query_dedup_scpoe"] = values.pop(
            "tool_query_dedup_scope"
        )

        with self.assertRaisesRegex(ValueError, "unknown fields"):
            ProfileSettings.from_mapping(values)

    def test_rejects_invalid_dedup_scope(self):
        values = dict(VIKINGBOAT_0411_SETTINGS)
        values["tool_query_dedup_scope"] = "global"

        with self.assertRaisesRegex(ValueError, "none, turn, or question"):
            ProfileSettings.from_mapping(values)


if __name__ == "__main__":
    unittest.main()
