"""Unit tests for dynamic-eval metric collection.

Covers collect_round_metrics / compute_summary in dynamic/metrics.py and
_ask_agent / _failed_round in dynamic/workflows.py.  These functions read
metrics directly from AgentResponse standard fields + extra (no done_event
dict relay), matching the unified metric-collection design.

Run: python -m pytest tests/test_dynamic_metrics.py -v
"""

from __future__ import annotations

import json
import types
import unittest
from unittest.mock import patch

from dynamic.metrics import (
    _parse_evaluation_json,
    build_dynamic_repair_prompt,
    collect_round_metrics,
    compute_summary,
    evaluate_quality,
)
from dynamic.workflows import _ask_agent, _failed_round
from plugins.base import AgentResponse
from shared.llm_client import LLMResponse


class _QualityLLM:
    """Fake evaluator LLM returning predefined completions in order."""

    def __init__(self, responses: list[str]):
        self.responses = iter(responses)
        self.last_chat_kwargs = None

    def chat(
        self,
        messages,
        *,
        temperature=None,
        response_format=False,
        thinking_disabled=False,
        omit_max_tokens=False,
    ):
        self.last_chat_kwargs = {
            "response_format": response_format,
            "thinking_disabled": thinking_disabled,
            "omit_max_tokens": omit_max_tokens,
        }
        return LLMResponse(
            content=next(self.responses),
            prompt_tokens=0,
            completion_tokens=0,
            elapsed_s=0.0,
        )


class _FakePlugin:
    """Minimal AgentPlugin stand-in without typing simulation."""

    supports_typing_simulation = False

    def __init__(self, response: AgentResponse):
        self._response = response

    def send_message(self, session_id, query, context_path):
        return self._response


def _args():
    return types.SimpleNamespace(typing_speed_ms=200, typing_jitter_ms=20)


class CollectRoundMetricsTests(unittest.TestCase):
    def test_reads_all_fields_from_agent_response(self):
        response = AgentResponse(
            text="answer text",
            ttft_ms=100.26,
            prompt_tokens=10,
            completion_tokens=7,
            cached_tokens=3,
            prefetch_committed=True,
            memory_items=[{"text": "mem"}],
            error="",
            extra={
                "elapsed_s": 1.234,
                "retrieval_latency_s": 0.456,
                "llm_latency_s": 0.789,
                "tool_call_count": 2,
                "iterations": 3,
            },
        )
        round_data = {
            "id": "r1",
            "query": "question?",
            "new_session": True,
            "is_injection": False,
            "complexity": "medium",
            "ground_facts": ["f1", "f2"],
        }
        result = collect_round_metrics(round_data, response)
        self.assertEqual("r1", result["round_id"])
        self.assertEqual("question?", result["query"])
        self.assertEqual("answer text", result["reply"])
        self.assertEqual(11, result["reply_length"])
        self.assertEqual(9, result["query_length"])
        self.assertEqual(100.3, result["ttft_ms"])
        self.assertEqual(10, result["prompt_tokens"])
        self.assertEqual(7, result["completion_tokens"])
        self.assertEqual(3, result["cached_tokens"])
        self.assertTrue(result["prefetch_committed"])
        self.assertEqual(1.234, result["elapsed_s"])
        self.assertEqual(0.456, result["retrieval_latency_s"])
        self.assertEqual(0.789, result["llm_latency_s"])
        self.assertEqual(2, result["tool_call_count"])
        self.assertEqual(3, result["iterations"])
        self.assertTrue(result["is_new_session"])
        self.assertFalse(result["is_injection"])
        self.assertEqual("medium", result["complexity"])
        self.assertEqual(["f1", "f2"], result["ground_facts"])
        self.assertEqual("", result["error"])
        self.assertEqual(
            json.dumps([{"text": "mem"}], ensure_ascii=False),
            result["relevant_memory"],
        )

    def test_memory_items_param_wins_over_response(self):
        response = AgentResponse(text="x", memory_items=[{"text": "resp"}])
        result = collect_round_metrics(
            {"query": "q"}, response, [{"text": "param"}],
        )
        self.assertEqual(
            json.dumps([{"text": "param"}], ensure_ascii=False),
            result["relevant_memory"],
        )

    def test_memory_items_falls_back_to_response(self):
        response = AgentResponse(text="x", memory_items=[{"text": "resp"}])
        result = collect_round_metrics({"query": "q"}, response)
        self.assertEqual(
            json.dumps([{"text": "resp"}], ensure_ascii=False),
            result["relevant_memory"],
        )

    def test_empty_extra_and_text_defaults(self):
        response = AgentResponse()
        result = collect_round_metrics({"query": "q"}, response)
        self.assertEqual("", result["reply"])
        self.assertEqual(0, result["reply_length"])
        self.assertIsNone(result["ttft_ms"])
        self.assertEqual(0, result["prompt_tokens"])
        self.assertEqual(0, result["completion_tokens"])
        self.assertEqual(0, result["cached_tokens"])
        self.assertFalse(result["prefetch_committed"])
        self.assertIsNone(result["elapsed_s"])
        self.assertIsNone(result["retrieval_latency_s"])
        self.assertIsNone(result["llm_latency_s"])
        self.assertEqual(0, result["tool_call_count"])
        self.assertEqual(1, result["iterations"])
        self.assertEqual("[]", result["relevant_memory"])


class ComputeSummaryTests(unittest.TestCase):
    def test_aggregates_new_metric_fields(self):
        rounds = [
            {
                "is_injection": False,
                "ttft_ms": 100.0,
                "cached_tokens": 1,
                "prompt_tokens": 2,
                "reply_length": 10,
                "error": "",
                "prefetch_committed": True,
                "elapsed_s": 1.0,
                "retrieval_latency_s": 0.2,
                "llm_latency_s": 0.6,
                "completion_tokens": 10,
                "tool_call_count": 2,
                "iterations": 2,
            },
            {
                "is_injection": False,
                "ttft_ms": 200.0,
                "cached_tokens": 3,
                "prompt_tokens": 4,
                "reply_length": 20,
                "error": "",
                "prefetch_committed": False,
                "elapsed_s": 3.0,
                "retrieval_latency_s": 0.4,
                "llm_latency_s": 1.2,
                "completion_tokens": 20,
                "tool_call_count": 4,
                "iterations": 4,
            },
        ]
        summary = compute_summary(rounds)
        self.assertEqual(2.0, summary["avg_elapsed_s"])
        self.assertEqual(0.3, summary["avg_retrieval_latency_s"])
        self.assertEqual(0.9, summary["avg_llm_latency_s"])
        self.assertEqual(15.0, summary["avg_completion_tokens"])
        self.assertEqual(3.0, summary["avg_tool_call_count"])
        self.assertEqual(3.0, summary["avg_iterations"])

    def test_injection_rounds_excluded_from_averages(self):
        rounds = [
            {
                "is_injection": True,
                "ttft_ms": 999.0,
                "cached_tokens": 99,
                "prompt_tokens": 99,
                "reply_length": 999,
                "error": "",
                "elapsed_s": 99.0,
                "retrieval_latency_s": 99.0,
                "llm_latency_s": 99.0,
                "completion_tokens": 99,
                "tool_call_count": 99,
                "iterations": 99,
            },
            {
                "is_injection": False,
                "ttft_ms": 100.0,
                "cached_tokens": 1,
                "prompt_tokens": 2,
                "reply_length": 10,
                "error": "",
                "elapsed_s": 1.0,
                "retrieval_latency_s": 0.2,
                "llm_latency_s": 0.6,
                "completion_tokens": 10,
                "tool_call_count": 2,
                "iterations": 2,
            },
        ]
        summary = compute_summary(rounds)
        self.assertEqual(1, summary["total_queries"])
        self.assertEqual(1.0, summary["avg_elapsed_s"])
        self.assertEqual(0.2, summary["avg_retrieval_latency_s"])
        self.assertEqual(0.6, summary["avg_llm_latency_s"])
        self.assertEqual(10.0, summary["avg_completion_tokens"])
        self.assertEqual(2.0, summary["avg_tool_call_count"])
        self.assertEqual(2.0, summary["avg_iterations"])

    def test_empty_rounds_fall_back_defaults(self):
        summary = compute_summary([])
        self.assertIsNone(summary["avg_elapsed_s"])
        self.assertIsNone(summary["avg_retrieval_latency_s"])
        self.assertIsNone(summary["avg_llm_latency_s"])
        self.assertIsNone(summary["avg_completion_tokens"])
        self.assertEqual(0, summary["avg_tool_call_count"])
        self.assertEqual(1, summary["avg_iterations"])


class AskAgentTests(unittest.TestCase):
    def test_returns_metrics_dict_from_response(self):
        response = AgentResponse(
            text="hello world",
            ttft_ms=50.0,
            prompt_tokens=5,
            completion_tokens=8,
            cached_tokens=2,
            prefetch_committed=True,
            memory_items=[{"text": "mem"}],
            extra={
                "elapsed_s": 0.9,
                "retrieval_latency_s": 0.1,
                "llm_latency_s": 0.5,
                "tool_call_count": 1,
                "iterations": 2,
            },
        )
        round_data = {
            "id": "r1",
            "query": "question",
            "new_session": False,
            "is_injection": False,
            "complexity": "simple",
            "ground_facts": [],
        }
        metrics = _ask_agent(_args(), _FakePlugin(response), "sess-1", round_data)
        self.assertEqual("hello world", metrics["reply"])
        self.assertEqual(50.0, metrics["ttft_ms"])
        self.assertEqual(5, metrics["prompt_tokens"])
        self.assertEqual(8, metrics["completion_tokens"])
        self.assertEqual(2, metrics["cached_tokens"])
        self.assertTrue(metrics["prefetch_committed"])
        self.assertEqual(0.9, metrics["elapsed_s"])
        self.assertEqual(0.1, metrics["retrieval_latency_s"])
        self.assertEqual(0.5, metrics["llm_latency_s"])
        self.assertEqual(1, metrics["tool_call_count"])
        self.assertEqual(2, metrics["iterations"])
        self.assertNotIn("done_event", metrics)
        # no typing simulation -> memory_items falls back to response
        self.assertEqual(
            json.dumps([{"text": "mem"}], ensure_ascii=False),
            metrics["relevant_memory"],
        )

    def test_exception_returns_failed_round(self):
        class _BoomPlugin:
            supports_typing_simulation = False

            def send_message(self, session_id, query, context_path):
                raise RuntimeError("boom")

        round_data = {
            "id": "r2",
            "query": "q",
            "new_session": True,
            "is_injection": False,
            "complexity": "simple",
            "ground_facts": [],
        }
        metrics = _ask_agent(_args(), _BoomPlugin(), "sess", round_data)
        self.assertNotEqual("", metrics["error"])
        self.assertEqual("", metrics["reply"])
        self.assertIsNone(metrics["ttft_ms"])
        self.assertEqual(0, metrics["completion_tokens"])
        self.assertIsNone(metrics["elapsed_s"])
        self.assertIsNone(metrics["retrieval_latency_s"])
        self.assertIsNone(metrics["llm_latency_s"])
        self.assertEqual(0, metrics["tool_call_count"])
        self.assertEqual(1, metrics["iterations"])


class FailedRoundTests(unittest.TestCase):
    def test_returns_aligned_schema(self):
        result = _failed_round(
            {
                "id": "r3",
                "query": "q",
                "new_session": True,
                "complexity": "hard",
                "ground_facts": ["f"],
            },
            prefetch_committed=True,
            error=RuntimeError("fail"),
        )
        self.assertEqual("r3", result["round_id"])
        self.assertEqual("q", result["query"])
        self.assertEqual("", result["reply"])
        self.assertEqual(0, result["reply_length"])
        self.assertEqual(1, result["query_length"])
        self.assertIsNone(result["ttft_ms"])
        self.assertEqual(0, result["cached_tokens"])
        self.assertEqual(0, result["prompt_tokens"])
        self.assertEqual(0, result["completion_tokens"])
        self.assertTrue(result["prefetch_committed"])
        self.assertIsNone(result["elapsed_s"])
        self.assertIsNone(result["retrieval_latency_s"])
        self.assertIsNone(result["llm_latency_s"])
        self.assertEqual(0, result["tool_call_count"])
        self.assertEqual(1, result["iterations"])
        self.assertTrue(result["is_new_session"])
        self.assertFalse(result["is_injection"])
        self.assertEqual("hard", result["complexity"])
        self.assertEqual(["f"], result["ground_facts"])
        self.assertEqual("fail", result["error"])
        self.assertEqual("[]", result["relevant_memory"])


class EvaluateQualityTests(unittest.TestCase):
    """evaluate_quality retries malformed evaluator output instead of failing."""

    @staticmethod
    def _config() -> dict:
        return {
            "evaluate_prompt": "{query}\n{reply}\n{dimension_criteria}",
            "dimensions": [
                {"name": "fact_coverage", "display_name": "事实覆盖", "max_score": 40},
                {"name": "coherence", "display_name": "连贯", "max_score": 60},
            ],
        }

    def test_parses_valid_json(self) -> None:
        llm = _QualityLLM([
            '{"score": 80, "dimension_scores": '
            '{"fact_coverage": 30, "coherence": 50}}',
        ])
        result = evaluate_quality(llm, self._config(), "Q", "reply", ["fact"])
        self.assertEqual(80, result["score"])
        self.assertEqual(30, result["dimension_scores"]["fact_coverage"])
        self.assertEqual("", result.get("error", ""))

    def test_retries_malformed_output_then_parses(self) -> None:
        llm = _QualityLLM([
            "not json",
            '{"score": 90, "dimension_scores": {}}',
        ])
        with patch("shared.llm_client.time.sleep"):
            result = evaluate_quality(llm, self._config(), "Q", "reply", [])
        self.assertEqual(90, result["score"])
        self.assertEqual("", result.get("error", ""))

    def test_returns_error_when_all_attempts_fail(self) -> None:
        llm = _QualityLLM(["not json", "not json", "not json"])
        with patch("shared.llm_client.time.sleep"):
            result = evaluate_quality(llm, self._config(), "Q", "reply", [])
        self.assertIsNone(result["score"])
        self.assertNotEqual("", result["error"])

    def test_parses_fenced_json_from_first_attempt(self) -> None:
        llm = _QualityLLM([
            '```json\n{"score": 95, "dimension_scores": '
            '{"fact_coverage": 40, "coherence": 55}}\n```',
        ])
        result = evaluate_quality(llm, self._config(), "Q", "reply", [])
        self.assertEqual(95, result["score"])
        self.assertEqual("", result.get("error", ""))

    def test_judge_uses_structured_judge_mode(self) -> None:
        llm = _QualityLLM([
            '{"score": 88, "dimension_scores": '
            '{"fact_coverage": 40, "coherence": 48}}',
        ])
        result = evaluate_quality(llm, self._config(), "Q", "reply", [])
        self.assertEqual(88, result["score"])
        self.assertEqual("", result.get("error", ""))
        # 评测走 judge 模式：强制 JSON、关闭思考、不传 max_tokens。
        self.assertTrue(llm.last_chat_kwargs["response_format"])
        self.assertTrue(llm.last_chat_kwargs["thinking_disabled"])
        self.assertTrue(llm.last_chat_kwargs["omit_max_tokens"])


class ParseEvaluationJsonTests(unittest.TestCase):
    """_parse_evaluation_json tolerates common malformed evaluator output."""

    def test_parses_plain_object(self) -> None:
        self.assertEqual({"score": 80}, _parse_evaluation_json('{"score": 80}'))

    def test_parses_code_fenced_json(self) -> None:
        raw = '```json\n{"score": 90, "reason": "ok"}\n```'
        self.assertEqual(90, _parse_evaluation_json(raw)["score"])

    def test_parses_object_with_trailing_braced_prose(self) -> None:
        # 贪婪正则会把尾注里多余的 '}' 一并吞入导致语法错误；
        # 配平解析应只取第一个 JSON 对象。
        raw = '{"score": 85} 备注：范围 0-100 }'
        self.assertEqual(85, _parse_evaluation_json(raw)["score"])

    def test_parses_nested_dimension_scores(self) -> None:
        raw = (
            '{"score": 88, "dimension_scores": {"a": {"x": 1, "y": 2}}, '
            '"tags": ["t1", "t2"]}'
        )
        parsed = _parse_evaluation_json(raw)
        self.assertEqual(88, parsed["score"])
        self.assertEqual({"x": 1, "y": 2}, parsed["dimension_scores"]["a"])

    def test_repairs_trailing_comma(self) -> None:
        raw = '{"score": 70, "dimension_scores": {"a": 10,},}'
        parsed = _parse_evaluation_json(raw)
        self.assertEqual(70, parsed["score"])
        self.assertEqual(10, parsed["dimension_scores"]["a"])

    def test_raises_when_no_json_object(self) -> None:
        with self.assertRaises(ValueError):
            _parse_evaluation_json("该回复质量不错，无需评分 JSON。")


class RepairPromptTests(unittest.TestCase):
    """build_dynamic_repair_prompt spells out the full expected schema."""

    def test_lists_dimension_names_and_max_scores(self) -> None:
        prompt = build_dynamic_repair_prompt([
            {"name": "fact_coverage", "max_score": 40},
            {"name": "coherence", "max_score": 60},
        ])
        self.assertIn('"fact_coverage" (0-40)', prompt)
        self.assertIn('"coherence" (0-60)', prompt)
        self.assertIn('"score" (0-100)', prompt)
        self.assertIn("markdown", prompt)


if __name__ == "__main__":
    unittest.main()
