"""Comprehensive unit tests for benchmarks/locomo/ modules.

Covers every functional point in benchmarks/locomo/docs/usage.md that is not
already exercised by the existing test files
(test_locomo_workflows, test_locomo_provenance, test_locomo_reporting).

All tests use mocks -- no real services, no subprocesses.
"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from benchmarks.locomo.dataset import (
    _memory_users,
    _parse_datetime,
    _sample_question_time,
    conversation_events,
    load_dataset,
    session_batches,
)
from benchmarks.locomo.diagnosis import classify_failure
from benchmarks.locomo.import_memory import (
    ImportOptions,
    import_locomo_memory,
    resolve_session_mode,
    selected_session_batches,
)
from benchmarks.locomo.judge import (
    LOCOMO_JUDGE_SYSTEM,
    LOCOMO_JUDGE_TEMPLATE,
    JudgeReport,
    judge_locomo_results,
    judge_with_metrics,
    locomo_judge,
    parse_judge_json,
)
from benchmarks.locomo.memory_scope import (
    ExcludingMemoryFilesClient,
    SessionPrefixMemoryClient,
)
from benchmarks.locomo.profiles import (
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
    VIKINGBOAT_0411_PROFILE,
    profile_reference,
    profile_source,
    profile_spec,
    profile_settings,
)
from benchmarks.locomo.profiles.schema import ProfileSettings
from benchmarks.locomo.provenance import (
    expected_session_count,
    inspect_memory_provenance,
)
from benchmarks.locomo.qa import QAOptions, build_qa_tasks
from benchmarks.locomo.reporting import build_summary
from benchmarks.locomo.retry import build_retry_command, latest_qa_csv
from benchmarks.locomo.run_eval import _build_agent_options, build_parser
from benchmarks.locomo.selection import parse_question_ids, select_questions
from benchmarks.locomo.stats import summarize_judge_rows
from benchmarks.locomo.blackbox import metric_stats, percentile
from shared.eval_base import EvalConfig
from shared.qa import QAResult


# ------------------------------------------------------------------ #
#  Helpers                                                            #
# ------------------------------------------------------------------ #

class _Log:
    """Minimal logger stub."""

    def info(self, *_a, **_kw):
        pass

    def error(self, *_a, **_kw):
        pass

    def warning(self, *_a, **_kw):
        pass


def _make_sample(
    *,
    sample_id: str = "sample_0",
    sessions: dict | None = None,
    qa: list | None = None,
    speaker_a: str = "Jon",
    speaker_b: str = "Maya",
) -> dict:
    """Build a minimal LoCoMo sample dict."""
    conversation: dict = {"speaker_a": speaker_a, "speaker_b": speaker_b}
    if sessions:
        conversation.update(sessions)
    return {"sample_id": sample_id, "conversation": conversation, "qa": qa or []}


def _write_jsonl_dataset(path: Path, samples: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(s) + "\n" for s in samples),
        encoding="utf-8",
    )


# ------------------------------------------------------------------ #
#  dataset.py                                                        #
# ------------------------------------------------------------------ #

class ParseDatetimeTests(unittest.TestCase):
    """Tests for _parse_datetime across known LoCoMo date formats."""

    def test_formats(self):
        cases = {
            "11:30 AM on 15 June, 2023": datetime(2023, 6, 15, 11, 30),
            "2023-06-15 11:30:00": datetime(2023, 6, 15, 11, 30),
            "2023-06-15T11:30:00": datetime(2023, 6, 15, 11, 30),
            "2023/06/15 11:30:00": datetime(2023, 6, 15, 11, 30),
            "2023-06-15": datetime(2023, 6, 15),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, _parse_datetime(raw))

    def test_on_suffix_fallback(self):
        result = _parse_datetime("some prefix on 15 June, 2023")
        self.assertEqual(datetime(2023, 6, 15), result)

    def test_empty_or_unparseable_returns_none(self):
        for raw in ("", None, "not a date at all"):
            with self.subTest(raw=raw):
                self.assertIsNone(_parse_datetime(raw))


class ConversationEventsTests(unittest.TestCase):
    """Tests for conversation_events timeline parsing."""

    def test_extracts_speaker_dia_id_and_text(self):
        sample = _make_sample(sessions={
            "session_1": [
                {"speaker": "Jon", "dia_id": "1", "text": "Hello there"},
                {"speaker": "Maya", "dia_id": "2", "blip_caption": "beach.jpg"},
            ],
            "session_1_date_time": "11:30 AM on 15 June, 2023",
        })
        events = conversation_events(sample)
        self.assertEqual(2, len(events))
        self.assertEqual("11:30 AM on 15 June, 2023", events[0]["time"])
        self.assertIn("Jon 1:", events[0]["text"])
        self.assertIn("Hello there", events[0]["text"])
        self.assertIn("image: beach.jpg", events[1]["text"])

    def test_skips_non_dict_messages(self):
        sample = _make_sample(sessions={
            "session_1": ["bad", {"speaker": "Jon", "text": "ok"}],
            "session_1_date_time": "2023-06-15",
        })
        events = conversation_events(sample)
        self.assertEqual(1, len(events))

    def test_query_field_appended(self):
        sample = _make_sample(sessions={
            "session_1": [{"speaker": "Jon", "query": "search term"}],
            "session_1_date_time": "2023-06-15",
        })
        events = conversation_events(sample)
        self.assertIn("query: search term", events[0]["text"])

    def test_no_conversation_returns_empty(self):
        self.assertEqual([], conversation_events({}))


class SessionBatchesTests(unittest.TestCase):
    """Tests for session_batches message normalization."""

    def test_maps_speaker_roles_and_created_at(self):
        sample = _make_sample(sessions={
            "session_1": [
                {"speaker": "Jon", "text": "hi", "dia_id": "1"},
                {"speaker": "agent", "text": "hello", "dia_id": "2"},
            ],
            "session_1_date_time": "11:30 AM on 15 June, 2023",
        })
        batches = session_batches(sample)
        self.assertEqual(1, len(batches))
        self.assertEqual("session_1", batches[0]["session_key"])
        msgs = batches[0]["messages"]
        self.assertEqual("user", msgs[0]["role"])
        self.assertEqual("assistant", msgs[1]["role"])
        self.assertEqual("2023-06-15T11:30:00", msgs[0]["created_at"])
        self.assertEqual("2023-06-15T11:30:01", msgs[1]["created_at"])

    def test_blip_caption_and_query_formatting(self):
        sample = _make_sample(sessions={
            "session_1": [
                {"speaker": "Jon", "blip_caption": "photo"},
                {"speaker": "Maya", "query": "sunset"},
            ],
            "session_1_date_time": "2023-06-15",
        })
        batches = session_batches(sample)
        self.assertIn("attached image; image description: photo", batches[0]["messages"][0]["content"])
        self.assertIn("image search/query text: sunset", batches[0]["messages"][1]["content"])

    def test_skips_empty_parts(self):
        sample = _make_sample(sessions={
            "session_1": [{"speaker": "Jon"}],
            "session_1_date_time": "2023-06-15",
        })
        self.assertEqual([], session_batches(sample))


class LoadDatasetTests(unittest.TestCase):
    """Tests for load_dataset filtering and BenchmarkQuestion fields."""

    def _two_sample_dataset(self) -> list[dict]:
        return [
            _make_sample(
                sample_id="sample_0",
                sessions={
                    "session_1": [{"speaker": "Jon", "text": "hello", "dia_id": "1"}],
                    "session_1_date_time": "2023-06-15",
                },
                qa=[
                    {"question": "Q1?", "answer": "A1", "category": "1"},
                    {"question": "Q2?", "answer": "A2", "category": "5"},
                ],
            ),
            _make_sample(
                sample_id="sample_1",
                sessions={
                    "session_1": [{"speaker": "Maya", "text": "world", "dia_id": "1"}],
                    "session_1_date_time": "2023-07-01",
                },
                qa=[
                    {"question": "Q3?", "answer": "A3", "category": "2"},
                ],
            ),
        ]

    def test_filters_by_sample_id(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "locomo.jsonl"
            _write_jsonl_dataset(path, self._two_sample_dataset())
            jobs, plans = load_dataset(path, sample_filter="sample_0")
        self.assertEqual(1, len(plans))
        self.assertEqual("sample_0", plans[0]["sample_id"])
        # category 5 excluded -> only 1 QA from sample_0
        self.assertEqual(1, len(jobs))
        self.assertEqual("sample_0_qa0", jobs[0].question_id)

    def test_category_five_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "locomo.jsonl"
            _write_jsonl_dataset(path, self._two_sample_dataset())
            jobs, plans = load_dataset(path)
        self.assertEqual(2, len(jobs))
        categories = {job.category for job in jobs}
        self.assertNotIn("5", categories)

    def test_benchmark_question_fields(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "locomo.jsonl"
            _write_jsonl_dataset(path, self._two_sample_dataset())
            jobs, _ = load_dataset(path, sample_filter="sample_0")
        job = jobs[0]
        self.assertEqual("locomo", job.dataset_format)
        self.assertEqual("sample_0", job.sample_id)
        self.assertEqual("Q1?", job.question)
        self.assertEqual("A1", job.answer)
        self.assertEqual("1", job.category)
        self.assertEqual("sample_0", job.original_sample_id)
        self.assertEqual("0", job.question_index)
        self.assertEqual("sample_0_qa0", job.question_id)
        self.assertEqual("sample_0_qa0", job.native_question_id)
        self.assertIn("Jon", job.memory_users)
        self.assertIn("Maya", job.memory_users)
        self.assertTrue(job.injection_events >= 1)

    def test_plans_contain_session_batches_and_events(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "locomo.jsonl"
            _write_jsonl_dataset(path, self._two_sample_dataset())
            _, plans = load_dataset(path)
        plan = plans[0]
        self.assertTrue(len(plan["events"]) >= 1)
        self.assertEqual(plan["events"], plan["preview_events"][:len(plan["events"])])
        self.assertTrue(len(plan["session_batches"]) >= 1)
        self.assertEqual("sample_0", plan["sample_id"])


class MemoryUsersAndQuestionTimeTests(unittest.TestCase):
    """Tests for _memory_users and _sample_question_time helpers."""

    def test_memory_users_dedup_and_order(self):
        sample = _make_sample(speaker_a="Jon", speaker_b="Jon")
        self.assertEqual(["Jon"], _memory_users(sample))

    def test_memory_users_missing_speakers(self):
        sample = {"conversation": {}}
        self.assertEqual([], _memory_users(sample))

    def test_question_time_from_last_session(self):
        sample = _make_sample(sessions={
            "session_1": [{"speaker": "Jon", "text": "first"}],
            "session_1_date_time": "2023-06-15",
            "session_2": [{"speaker": "Maya", "text": "second"}],
            "session_2_date_time": "11:00 AM on 20 July, 2023",
        })
        self.assertEqual("2023-07-20", _sample_question_time(sample))

    def test_question_time_empty_when_no_sessions(self):
        self.assertEqual("", _sample_question_time({}))


# ------------------------------------------------------------------ #
#  import_memory.py                                                  #
# ------------------------------------------------------------------ #

class ResolveSessionModeTests(unittest.TestCase):
    """Tests for resolve_session_mode edge cases."""

    def test_locomo_with_multiple_samples_raises(self):
        with self.assertRaisesRegex(ValueError, "cannot safely isolate multiple samples"):
            resolve_session_mode("locomo", 2)

    def test_explicit_locomo_single_sample_ok(self):
        self.assertEqual("locomo", resolve_session_mode("locomo", 1))

    def test_auto_single_sample_becomes_locomo(self):
        self.assertEqual("locomo", resolve_session_mode("auto", 1))

    def test_auto_multi_sample_becomes_single(self):
        self.assertEqual("single", resolve_session_mode("auto", 2))

    def test_explicit_single_passes_through(self):
        self.assertEqual("single", resolve_session_mode("single", 1))


class SelectedSessionBatchesTests(unittest.TestCase):
    """Tests for selected_session_batches mode and truncation."""

    def setUp(self):
        self.plan = {
            "session_batches": [
                {"session_key": "session_1", "messages": [{"content": "a"}]},
                {"session_key": "session_2", "messages": [{"content": "b"}]},
                {"session_key": "session_3", "messages": [{"content": "c"}]},
            ],
        }

    def test_single_mode_merges_all_batches(self):
        result = selected_session_batches(self.plan, session_mode="single", max_sessions=0)
        self.assertEqual(1, len(result))
        self.assertEqual("all", result[0]["session_key"])
        self.assertEqual(3, len(result[0]["messages"]))

    def test_locomo_mode_preserves_individual_batches(self):
        result = selected_session_batches(self.plan, session_mode="locomo", max_sessions=0)
        self.assertEqual(3, len(result))
        self.assertEqual(["session_1", "session_2", "session_3"],
                         [b["session_key"] for b in result])

    def test_max_sessions_truncates(self):
        result = selected_session_batches(self.plan, session_mode="locomo", max_sessions=2)
        self.assertEqual(2, len(result))

    def test_single_mode_with_max_sessions(self):
        result = selected_session_batches(self.plan, session_mode="single", max_sessions=2)
        self.assertEqual(1, len(result))
        self.assertEqual(2, len(result[0]["messages"]))


class ImportLocomoMemoryTests(unittest.TestCase):
    """Tests for import_locomo_memory injection flow and error handling."""

    def test_normal_injection_calls_open_add_commit_poll(self):
        client = MagicMock()
        client.open_session.return_value = "sess-1"
        client.commit_session.return_value = "archive-1"
        client.poll_commit.return_value = SimpleNamespace(
            status="completed", elapsed_s=1.5, error="", polls=1,
        )
        plans = [{
            "sample_id": "s0",
            "session_batches": [
                {"session_key": "session_1", "messages": [
                    {"role": "user", "content": "hello", "role_id": "Jon"},
                    {"role": "assistant", "content": "world", "role_id": "agent"},
                ]},
            ],
        }]
        with tempfile.TemporaryDirectory() as d:
            report = import_locomo_memory(
                plans, client, EvalConfig(),
                ImportOptions("locomo", 0, False, "all", None),
                Path(d), _Log(),
            )
        client.open_session.assert_called_once()
        self.assertEqual(2, client.add_message.call_count)
        client.commit_session.assert_called_once_with("sess-1")
        client.poll_commit.assert_called_once()
        self.assertEqual(1, report.total)
        self.assertEqual(1, report.completed)
        self.assertEqual(0, report.incomplete)
        self.assertEqual(2, report.expected_messages)
        self.assertEqual(2, report.submitted_messages)
        self.assertEqual("completed", report.rows[0]["status"])
        self.assertEqual("sess-1", report.rows[0]["session_id"])

    def test_import_error_recorded(self):
        client = MagicMock()
        client.open_session.side_effect = RuntimeError("backend down")
        plans = [{
            "sample_id": "s0",
            "session_batches": [
                {"session_key": "session_1", "messages": [
                    {"role": "user", "content": "hello"},
                ]},
            ],
        }]
        with tempfile.TemporaryDirectory() as d:
            report = import_locomo_memory(
                plans, client, EvalConfig(),
                ImportOptions("locomo", 0, False, "all", None),
                Path(d), _Log(),
            )
        self.assertEqual(1, report.total)
        self.assertEqual(0, report.completed)
        self.assertEqual(1, report.incomplete)
        self.assertEqual("error", report.rows[0]["status"])
        self.assertIn("backend down", report.rows[0]["error"])

    def test_empty_messages_skipped_in_submission_count(self):
        client = MagicMock()
        client.open_session.return_value = "sess-1"
        client.commit_session.return_value = "archive-1"
        client.poll_commit.return_value = SimpleNamespace(
            status="completed", elapsed_s=0.1, error="", polls=1,
        )
        plans = [{
            "sample_id": "s0",
            "session_batches": [
                {"session_key": "session_1", "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "user", "content": ""},
                ]},
            ],
        }]
        with tempfile.TemporaryDirectory() as d:
            report = import_locomo_memory(
                plans, client, EvalConfig(),
                ImportOptions("locomo", 0, False, "all", None),
                Path(d), _Log(),
            )
        # message_count counts all messages, submitted_messages only non-empty
        self.assertEqual(2, report.expected_messages)
        self.assertEqual(1, report.submitted_messages)

    def test_no_batches_produces_error_row(self):
        client = MagicMock()
        plans = [{"sample_id": "s0", "session_batches": []}]
        with tempfile.TemporaryDirectory() as d:
            report = import_locomo_memory(
                plans, client, EvalConfig(),
                ImportOptions("locomo", 0, False, "all", None),
                Path(d), _Log(),
            )
        self.assertEqual(1, report.total)
        self.assertEqual(0, report.completed)
        self.assertEqual("error", report.rows[0]["status"])
        self.assertIn("no LoCoMo session batches", report.rows[0]["error"])


# ------------------------------------------------------------------ #
#  judge.py                                                          #
# ------------------------------------------------------------------ #

class ParseJudgeJsonTests(unittest.TestCase):
    """Tests for parse_judge_json verdict extraction."""

    def test_valid_correct(self):
        text = '{"is_correct": "CORRECT", "reasoning": "matches gold"}'
        verdict, reasoning = parse_judge_json(text)
        self.assertEqual("CORRECT", verdict)
        self.assertEqual("matches gold", reasoning)

    def test_valid_wrong(self):
        text = 'prefix {"is_correct": "WRONG", "reasoning": "off topic"} suffix'
        verdict, reasoning = parse_judge_json(text)
        self.assertEqual("WRONG", verdict)

    def test_no_json_raises(self):
        with self.assertRaisesRegex(ValueError, "contains no JSON"):
            parse_judge_json("no json here")

    def test_unknown_verdict_raises(self):
        text = '{"is_correct": "MAYBE"}'
        with self.assertRaisesRegex(ValueError, "unknown verdict"):
            parse_judge_json(text)


class LocomoJudgeTests(unittest.TestCase):
    """Tests for locomo_judge with mocked LLM."""

    def test_calls_judge_with_formatted_prompt(self):
        llm = MagicMock()
        llm.judge.return_value = '{"is_correct": "CORRECT", "reasoning": "ok"}'
        verdict, reasoning = locomo_judge(llm, "What?", "Gold", "Response")
        self.assertEqual("CORRECT", verdict)
        self.assertEqual("ok", reasoning)
        llm.judge.assert_called_once()
        args = llm.judge.call_args
        self.assertEqual(LOCOMO_JUDGE_SYSTEM, args[0][0])
        self.assertIn("What?", args[0][1])
        self.assertIn("Gold", args[0][1])
        self.assertIn("Response", args[0][1])


class JudgeWithMetricsTests(unittest.TestCase):
    """Tests for judge_with_metrics usage tracking and retries."""

    def test_with_chat_method_tracks_usage(self):
        from shared.llm_client import LLMResponse
        llm = MagicMock()
        llm.chat.return_value = LLMResponse(
            content='{"is_correct": "CORRECT", "reasoning": "yes"}',
            prompt_tokens=50,
            completion_tokens=10,
            elapsed_s=0.5,
            usage_observed=True,
        )
        verdict, reasoning, metrics = judge_with_metrics(llm, "Q", "A", "R")
        self.assertEqual("CORRECT", verdict)
        self.assertEqual(50, metrics["prompt_tokens"])
        self.assertEqual(10, metrics["completion_tokens"])
        self.assertTrue(metrics["usage_observed"])

    def test_without_chat_falls_back_to_judge(self):
        llm = MagicMock(spec=["judge"])
        llm.judge.return_value = '{"is_correct": "WRONG", "reasoning": "no"}'
        verdict, reasoning, metrics = judge_with_metrics(llm, "Q", "A", "R")
        self.assertEqual("WRONG", verdict)
        self.assertEqual(0, metrics["prompt_tokens"])
        self.assertFalse(metrics["usage_observed"])

    def test_chat_error_retries_then_raises(self):
        from shared.llm_client import LLMResponse
        llm = MagicMock()
        llm.chat.return_value = LLMResponse(
            content="", prompt_tokens=0, completion_tokens=0,
            elapsed_s=0.1, error="timeout",
        )
        with patch("benchmarks.locomo.judge.time.sleep"):
            with self.assertRaises(RuntimeError):
                judge_with_metrics(llm, "Q", "A", "R", attempts=2)
        self.assertEqual(2, llm.chat.call_count)


class JudgeLocomoResultsTests(unittest.TestCase):
    """Tests for judge_locomo_results resume and error handling."""

    def test_skips_errored_qa_as_error_verdict(self):
        qa_results = [QAResult(
            question_id="q1", question="Q", answer="A",
            response="", llm_error="timeout",
        )]
        with tempfile.TemporaryDirectory() as d:
            report = judge_locomo_results(
                qa_results, MagicMock(), Path(d), _Log(),
                concurrency=1, checkpoint_interval=0,
            )
        self.assertEqual("ERROR", report.rows[0]["verdict"])
        self.assertIn("skipped", report.rows[0]["judge_error"])

    def test_resume_reuses_matching_rows(self):
        qa_results = [
            QAResult(question_id="q1", question="Q1", answer="A1", response="R1"),
            QAResult(question_id="q2", question="Q2", answer="A2", response="R2"),
        ]
        existing = [{
            "question_id": "q1",
            "question": "Q1",
            "answer": "A1",
            "response": "R1",
            "verdict": "CORRECT",
            "reasoning": "reuse",
            "judge_error": "",
            "judge_prompt_tokens": "",
            "judge_completion_tokens": "",
            "judge_total_tokens": "",
            "judge_retry_count": "0",
            "judge_latency_ms": "0.0",
        }]
        llm = MagicMock()
        from shared.llm_client import LLMResponse
        llm.chat.return_value = LLMResponse(
            content='{"is_correct": "WRONG", "reasoning": "new"}',
            prompt_tokens=10, completion_tokens=5, elapsed_s=0.1,
        )
        with tempfile.TemporaryDirectory() as d:
            report = judge_locomo_results(
                qa_results, llm, Path(d), _Log(),
                concurrency=1, checkpoint_interval=0,
                existing_rows=existing,
            )
        # q1 reused, q2 judged
        self.assertEqual(2, len(report.rows))
        self.assertEqual("CORRECT", report.rows[0]["verdict"])
        self.assertEqual("WRONG", report.rows[1]["verdict"])
        llm.chat.assert_called_once()

    def test_resume_rejects_mismatched_response(self):
        qa_results = [QAResult(
            question_id="q1", question="Q", answer="A", response="new-response",
        )]
        existing = [{
            "question_id": "q1", "question": "Q", "answer": "A",
            "response": "old-response", "verdict": "CORRECT",
            "reasoning": "", "judge_error": "",
            "judge_prompt_tokens": "", "judge_completion_tokens": "",
            "judge_total_tokens": "", "judge_retry_count": "0",
            "judge_latency_ms": "0.0",
        }]
        llm = MagicMock()
        from shared.llm_client import LLMResponse
        llm.chat.return_value = LLMResponse(
            content='{"is_correct": "WRONG", "reasoning": "no"}',
            prompt_tokens=0, completion_tokens=0, elapsed_s=0.1,
        )
        with tempfile.TemporaryDirectory() as d:
            report = judge_locomo_results(
                qa_results, llm, Path(d), _Log(),
                concurrency=1, checkpoint_interval=0,
                existing_rows=existing,
            )
        # mismatched -> re-judged
        self.assertEqual("WRONG", report.rows[0]["verdict"])

    def test_resume_rejects_unknown_question_ids(self):
        qa_results = [QAResult(
            question_id="q1", question="Q", answer="A", response="R",
        )]
        existing = [{
            "question_id": "unknown-q", "question": "Q", "answer": "A",
            "response": "R", "verdict": "CORRECT", "reasoning": "",
            "judge_error": "",
            "judge_prompt_tokens": "", "judge_completion_tokens": "",
            "judge_total_tokens": "", "judge_retry_count": "0",
            "judge_latency_ms": "0.0",
        }]
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "outside the current QA"):
                judge_locomo_results(
                    qa_results, MagicMock(), Path(d), _Log(),
                    concurrency=1, checkpoint_interval=0,
                    existing_rows=existing,
                )


# ------------------------------------------------------------------ #
#  diagnosis.py                                                      #
# ------------------------------------------------------------------ #

class ClassifyFailureTests(unittest.TestCase):
    """Tests for classify_failure across all failure categories."""

    def _qa_row(self, **overrides) -> dict:
        base = {
            "question_id": "q1",
            "question": "What drink?",
            "answer": "jasmine tea",
            "response": "coffee",
            "llm_error": "",
            "retrieval_error": "",
            "retrieval_items_json": "[]",
        }
        base.update(overrides)
        return base

    def test_correct_verdict(self):
        result = classify_failure(self._qa_row(), {"verdict": "CORRECT"})
        self.assertEqual("correct", result["mode"])
        self.assertFalse(result["retryable"])

    def test_model_error(self):
        result = classify_failure(
            self._qa_row(llm_error="api timeout"), {"verdict": "WRONG"},
        )
        self.assertEqual("model_error", result["mode"])
        self.assertTrue(result["retryable"])

    def test_retrieval_error(self):
        result = classify_failure(
            self._qa_row(retrieval_error="backend 500"), {"verdict": "WRONG"},
        )
        self.assertEqual("retrieval_error", result["mode"])
        self.assertTrue(result["retryable"])

    def test_empty_answer(self):
        result = classify_failure(
            self._qa_row(response=""), {"verdict": "WRONG"},
        )
        self.assertEqual("empty_answer", result["mode"])
        self.assertTrue(result["retryable"])

    def test_empty_retrieval(self):
        result = classify_failure(
            self._qa_row(), {"verdict": "WRONG"},
        )
        self.assertEqual("empty_retrieval", result["mode"])
        self.assertTrue(result["retryable"])

    def test_judge_error(self):
        row = self._qa_row(retrieval_items_json=json.dumps([{"content": "evidence"}]))
        result = classify_failure(row, {"verdict": "ERROR", "judge_error": "timeout"})
        self.assertEqual("judge_error", result["mode"])
        self.assertTrue(result["retryable"])

    def test_temporal_reasoning_failure(self):
        row = self._qa_row(
            question="When did Maya travel?",
            answer="July 2023",
            retrieval_items_json=json.dumps([{"content": "Maya travelled in July 2023"}]),
        )
        result = classify_failure(row, {"verdict": "WRONG"})
        self.assertEqual("temporal_reasoning", result["mode"])
        self.assertFalse(result["retryable"])

    def test_evidence_unused(self):
        row = self._qa_row(
            retrieval_items_json=json.dumps([{"content": "Maya prefers jasmine tea"}]),
        )
        result = classify_failure(row, {"verdict": "WRONG"})
        self.assertEqual("evidence_unused", result["mode"])
        self.assertFalse(result["retryable"])

    def test_evidence_mismatch(self):
        row = self._qa_row(
            retrieval_items_json=json.dumps([{"content": "unrelated info here"}]),
        )
        result = classify_failure(row, {"verdict": "WRONG"})
        self.assertEqual("evidence_mismatch", result["mode"])
        self.assertFalse(result["retryable"])

    def test_memory_missing_no_evidence(self):
        row = self._qa_row(
            retrieval_items_json=json.dumps([{"score": 0.5}]),
        )
        result = classify_failure(row, {"verdict": "WRONG"})
        self.assertEqual("memory_missing", result["mode"])
        self.assertFalse(result["retryable"])


# ------------------------------------------------------------------ #
#  retry.py                                                          #
# ------------------------------------------------------------------ #

class BuildRetryCommandTests(unittest.TestCase):
    """Tests for build_retry_command structure."""

    def test_generates_command_with_resume_qa(self):
        cmd = build_retry_command(
            project_root=Path("/project"),
            dataset=Path("/data/locomo.json"),
            sample="conv-30",
            question_ids=["q1", "q2"],
            round_dir=Path("/output/retry_001"),
            resume_source=Path("/prior/run"),
            eval_args=["--llm-api-key", "secret"],
        )
        self.assertEqual(cmd[0], cmd[0])  # python executable
        self.assertIn("locomo", cmd)
        self.assertIn("--dataset", cmd)
        self.assertIn(str(Path("/data/locomo.json")), cmd)
        self.assertIn("--sample", cmd)
        self.assertIn("conv-30", cmd)
        self.assertIn("--question-ids", cmd)
        self.assertIn("q1,q2", cmd)
        self.assertIn("--resume-qa", cmd)
        self.assertIn(str(Path("/prior/run")), cmd)
        self.assertIn("--out-dir", cmd)
        self.assertIn(str(Path("/output/retry_001")), cmd)
        self.assertIn("--llm-api-key", cmd)
        self.assertIn("secret", cmd)


class LatestQaCsvTests(unittest.TestCase):
    """Tests for latest_qa_csv finding newest QA CSV."""

    def test_returns_none_when_no_csvs(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(latest_qa_csv(Path(d)))

    def test_finds_latest_by_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run1 = root / "run1"
            run2 = root / "run2"
            run1.mkdir()
            run2.mkdir()
            (run1 / "qa_results.csv").write_text("header\n", encoding="utf-8")
            import time
            time.sleep(0.05)
            (run2 / "qa_results.csv").write_text("header\n", encoding="utf-8")
            result = latest_qa_csv(root)
            self.assertIsNotNone(result)
            self.assertEqual(run2 / "qa_results.csv", result)


# ------------------------------------------------------------------ #
#  reporting.py                                                      #
# ------------------------------------------------------------------ #

class BuildSummaryTests(unittest.TestCase):
    """Tests for build_summary metric aggregation."""

    def _import_report(self, completed=2, total=2, incomplete=0):
        from benchmarks.locomo.import_memory import ImportReport
        return ImportReport(
            rows=[{"status": "completed"}] * total,
            sample_to_session_ids={},
            completed=completed,
            total=total,
            incomplete=incomplete,
            expected_messages=10,
            submitted_messages=10,
        )

    def _judge_report(self, correct=1, wrong=1, errors=0):
        return JudgeReport(
            rows=[], correct=correct, wrong=wrong, errors=errors,
            graded=correct + wrong, accuracy=correct / (correct + wrong) if correct + wrong else 0,
        )

    def test_memory_source_injected_for_fresh_run(self):
        summary = build_summary(
            dataset_path="/data/locomo.json",
            sample_filter="all",
            total_samples=1,
            total_questions=2,
            import_report=self._import_report(),
            resume_qa=False,
            qa_results=[],
            judge_report=self._judge_report(),
            qa_options=QAOptions(profile=VIKINGBOAT_0411_PROFILE, top_k=25),
            session_mode="locomo",
            evaluation_identity={"mode": "fresh"},
        )
        self.assertEqual("injected", summary["memory_source"])
        self.assertEqual("completed", summary["status"])

    def test_memory_source_existing_for_resume(self):
        summary = build_summary(
            dataset_path="/data/locomo.json",
            sample_filter="all",
            total_samples=1,
            total_questions=2,
            import_report=self._import_report(),
            resume_qa=True,
            qa_results=[],
            judge_report=self._judge_report(),
            qa_options=QAOptions(profile=VIKINGBOAT_0411_PROFILE, top_k=25),
            session_mode="locomo",
            evaluation_identity={"mode": "resumed"},
        )
        self.assertEqual("existing", summary["memory_source"])

    def test_status_failed_when_errors_present(self):
        summary = build_summary(
            dataset_path="/data/locomo.json",
            sample_filter="all",
            total_samples=1,
            total_questions=2,
            import_report=self._import_report(incomplete=1),
            resume_qa=False,
            qa_results=[],
            judge_report=self._judge_report(),
            qa_options=QAOptions(profile=VIKINGBOAT_0411_PROFILE),
            session_mode="locomo",
            evaluation_identity={},
        )
        self.assertEqual("failed", summary["status"])

    def test_tool_stats_and_served_models(self):
        qa_results = [
            QAResult(
                question_id="q1", question="Q", answer="A", response="R",
                tool_call_count=3, iterations=2, prompt_tokens=100,
                completion_tokens=50, elapsed_s=1.5,
                trace={
                    "iterations": [
                        {"model_response": {"response_model": "doubao-1"}},
                    ],
                    "tool_protocol": {"sha256": "abc123"},
                },
            ),
        ]
        summary = build_summary(
            dataset_path="/data/locomo.json",
            sample_filter="all",
            total_samples=1,
            total_questions=1,
            import_report=self._import_report(completed=1, total=1),
            resume_qa=False,
            qa_results=qa_results,
            judge_report=self._judge_report(correct=1, wrong=0),
            qa_options=QAOptions(profile=VIKINGBOAT_0411_PROFILE, top_k=25, tools_enabled=True),
            session_mode="single",
            evaluation_identity={},
        )
        self.assertEqual(3, summary["tool_call_total"])
        self.assertEqual(2.0, summary["avg_iterations"])
        self.assertEqual(150, summary["total_prompt_tokens"] + summary["total_completion_tokens"])
        self.assertEqual(["doubao-1"], summary["served_model_ids"])
        self.assertEqual(["abc123"], summary["tool_protocol_sha256"])
        self.assertEqual("session", summary["retrieval_scope"])
        self.assertTrue(summary["tools_enabled"])

    def test_resume_accumulates_tokens_across_segments(self):
        # 用户诉求的回归：resume 后 summary 的 token 是「旧+新」累计，而非只算本轮。
        old = QAResult(
            question_id="q1", question="Q", answer="A", response="R",
            prompt_tokens=500, completion_tokens=200, elapsed_s=1.0,
        )
        new = QAResult(
            question_id="q2", question="Q", answer="A", response="R",
            prompt_tokens=700, completion_tokens=300, elapsed_s=2.0,
        )
        merged = [old, new]
        summary = build_summary(
            dataset_path="/data/locomo.json",
            sample_filter="all",
            total_samples=1,
            total_questions=2,
            import_report=self._import_report(completed=1, total=1),
            resume_qa=True,
            qa_results=merged,
            judge_report=self._judge_report(correct=1, wrong=1),
            qa_options=QAOptions(profile=VIKINGBOAT_0411_PROFILE, top_k=25),
            session_mode="single",
            evaluation_identity={},
        )
        self.assertEqual(1200, summary["total_prompt_tokens"])
        self.assertEqual(500, summary["total_completion_tokens"])
        self.assertEqual(1.5, summary["avg_qa_elapsed_s"])

    def test_qa_prompt_append_fields(self):
        summary = build_summary(
            dataset_path="/data/locomo.json",
            sample_filter="all",
            total_samples=1,
            total_questions=1,
            import_report=self._import_report(completed=1, total=1),
            resume_qa=False,
            qa_results=[],
            judge_report=self._judge_report(),
            qa_options=QAOptions(
                profile=VIKINGBOAT_0411_PROFILE,
                system_prompt_append="extra prompt",
                system_prompt_append_sha256="deadbeef",
                system_prompt_append_source="custom.txt",
            ),
            session_mode="locomo",
            evaluation_identity={},
        )
        self.assertTrue(summary["qa_prompt_append"]["enabled"])
        self.assertEqual("custom.txt", summary["qa_prompt_append"]["source"])
        self.assertEqual("deadbeef", summary["qa_prompt_append"]["sha256"])

    def test_agent_options_included(self):
        options = {
            "agent_plugin": "echomem_mcp",
            "initial_retrieval_protocol": "mcp",
            "mcp_read_mode": "allow",
            "user_memory_budget_chars": 4000,
            "agent_memory_budget_chars": 2000,
        }
        summary = build_summary(
            dataset_path="/data/locomo.json",
            sample_filter="all",
            total_samples=1,
            total_questions=1,
            import_report=self._import_report(completed=1, total=1),
            resume_qa=False,
            qa_results=[],
            judge_report=self._judge_report(),
            qa_options=QAOptions(
                profile=VIKINGBOAT_0411_PROFILE,
                agent_options=options,
            ),
            session_mode="locomo",
            evaluation_identity={},
        )
        self.assertEqual(options, summary["agent_options"])


# ------------------------------------------------------------------ #
#  qa.py                                                             #
# ------------------------------------------------------------------ #

class BuildQaTasksTests(unittest.TestCase):
    """Tests for build_qa_tasks task construction."""

    def test_task_structure_and_session_id(self):
        jobs = [
            SimpleNamespace(
                question_id="q1", sample_id="s0", category="1",
                question="What?", answer="Gold", query_time="2023-06-15",
            ),
        ]
        config = EvalConfig(top_k=25, memory_budget_chars=6000)
        options = QAOptions(profile=VIKINGBOAT_0411_PROFILE, tools_enabled=True)
        tasks = build_qa_tasks(
            jobs, {"s0": ["sess-1"]}, config, options, agent_id="agent-x",
        )
        self.assertEqual(1, len(tasks))
        task = tasks[0]
        self.assertEqual("q1", task["question_id"])
        self.assertEqual("sess-1", task["session_id"])
        self.assertEqual("agent-x", task["agent_id"])
        self.assertEqual(25, task["top_k"])
        self.assertEqual(6000, task["memory_budget_chars"])
        self.assertEqual(VIKINGBOAT_0411_PROFILE, task["qa_profile"])
        self.assertTrue(task["tools_enabled"])
        self.assertEqual("2023-06-15", task["question_time"])

    def test_empty_session_id_when_multiple_sessions(self):
        jobs = [SimpleNamespace(
            question_id="q1", sample_id="s0", category="1",
            question="Q", answer="A", query_time="",
        )]
        tasks = build_qa_tasks(
            jobs, {"s0": ["sess-1", "sess-2"]}, EvalConfig(),
            QAOptions(profile=VIKINGBOAT_0411_PROFILE),
        )
        self.assertEqual("", tasks[0]["session_id"])

    def test_empty_session_id_when_no_sessions(self):
        jobs = [SimpleNamespace(
            question_id="q1", sample_id="s0", category="1",
            question="Q", answer="A", query_time="",
        )]
        tasks = build_qa_tasks(
            jobs, {}, EvalConfig(),
            QAOptions(profile=VIKINGBOAT_0411_PROFILE),
        )
        self.assertEqual("", tasks[0]["session_id"])


class QAOptionsTests(unittest.TestCase):
    """Tests for QAOptions dataclass defaults and fields."""

    def test_defaults(self):
        opts = QAOptions(profile=VIKINGBOAT_0411_PROFILE)
        self.assertEqual(0, opts.checkpoint_interval)
        self.assertEqual(0, opts.top_k)
        self.assertEqual(0, opts.memory_budget_chars)
        self.assertTrue(opts.tools_enabled)
        self.assertEqual("", opts.system_prompt_append)
        self.assertEqual("", opts.system_prompt_append_sha256)
        self.assertEqual("", opts.system_prompt_append_source)

    def test_frozen(self):
        opts = QAOptions(profile=VIKINGBOAT_0411_PROFILE)
        with self.assertRaises(Exception):
            opts.profile = "other"  # type: ignore[misc]


# ------------------------------------------------------------------ #
#  memory_scope.py                                                   #
# ------------------------------------------------------------------ #

class MemoryScopeValidationTests(unittest.TestCase):
    """Tests for memory_scope client validation and delegation."""

    def test_session_prefix_empty_raises(self):
        with self.assertRaisesRegex(ValueError, "session prefix must not be empty"):
            SessionPrefixMemoryClient(MagicMock(), "")

    def test_excluding_files_empty_raises(self):
        with self.assertRaisesRegex(ValueError, "excluded filenames must not be empty"):
            ExcludingMemoryFilesClient(MagicMock(), [])

    def test_session_prefix_delegates_unknown_attrs(self):
        inner = MagicMock()
        inner.custom_method.return_value = "delegated"
        scoped = SessionPrefixMemoryClient(inner, "prefix-")
        self.assertEqual("delegated", scoped.custom_method(42))
        inner.custom_method.assert_called_once_with(42)

    def test_excluding_delegates_unknown_attrs(self):
        inner = MagicMock()
        inner.health.return_value = True
        filtered = ExcludingMemoryFilesClient(inner, ["excluded.txt"])
        self.assertTrue(filtered.health())
        inner.health.assert_called_once()

    def test_session_prefix_filters_search_by_uri(self):
        from backends.memory_types import SearchResult
        inner = MagicMock()
        inner.search.return_value = [
            SearchResult("echo://sessions/keep-1", 1.0, "kept"),
            SearchResult("echo://sessions/drop-1", 0.9, "dropped"),
            SearchResult("graph://entity:foo", 0.8, "graph"),
        ]
        scoped = SessionPrefixMemoryClient(inner, "keep-")
        results = scoped.search("query")
        uris = [r.uri for r in results]
        self.assertIn("echo://sessions/keep-1", uris)
        self.assertIn("graph://entity:foo", uris)
        self.assertNotIn("echo://sessions/drop-1", uris)

    def test_excluding_filters_fs_list_and_glob(self):
        inner = MagicMock()
        inner.fs_list.return_value = [
            {"uri": "echo://sessions/s1/overview.md"},
            {"uri": "echo://sessions/s1/messages.jsonl"},
        ]
        inner.fs_glob.return_value = [
            {"uri": "echo://sessions/s1/overview.md"},
            {"uri": "echo://sessions/s1/messages.jsonl"},
        ]
        filtered = ExcludingMemoryFilesClient(inner, ["messages.jsonl"])
        self.assertEqual(1, len(filtered.fs_list()))
        self.assertEqual(1, len(filtered.fs_glob()))
        self.assertEqual(
            "echo://sessions/s1/overview.md",
            filtered.fs_list()[0]["uri"],
        )


# ------------------------------------------------------------------ #
#  provenance.py                                                     #
# ------------------------------------------------------------------ #

class ProvenanceAdditionalTests(unittest.TestCase):
    """Additional provenance tests beyond existing test_locomo_provenance."""

    def test_expected_count_with_max_sessions(self):
        plans = [{
            "session_batches": [
                {"session_key": f"session_{i}", "messages": [{"content": "x"}]}
                for i in range(1, 5)
            ],
        }]
        self.assertEqual(4, expected_session_count(plans, session_mode="locomo", max_sessions=0))
        self.assertEqual(2, expected_session_count(plans, session_mode="locomo", max_sessions=2))
        self.assertEqual(1, expected_session_count(plans, session_mode="single", max_sessions=0))

    def test_inspect_provenance_matched_status(self):
        class Client:
            def fs_glob(self, pattern, **kw):
                return [
                    {"uri": "echo://sessions/s1/current/messages.jsonl"},
                    {"uri": "echo://sessions/s2/current/messages.jsonl"},
                ]
        plans = [{
            "session_batches": [
                {"session_key": "session_1", "messages": [{"content": "a"}]},
                {"session_key": "session_2", "messages": [{"content": "b"}]},
            ],
        }]
        with tempfile.TemporaryDirectory() as d:
            dataset = Path(d) / "locomo.json"
            dataset.write_text("[]", encoding="utf-8")
            prov = inspect_memory_provenance(
                Client(), dataset_path=dataset, plans=plans,
                session_mode="locomo", max_sessions=0,
            )
        self.assertEqual("matched", prov["status"])
        self.assertEqual(2, prov["expected_session_count"])
        self.assertEqual(2, prov["actual_session_count"])
        self.assertEqual(2, len(prov["session_uris"]))


# ------------------------------------------------------------------ #
#  profiles/                                                         #
# ------------------------------------------------------------------ #

class ProfileSettingsTests(unittest.TestCase):
    """Tests for profile_settings and ProfileSettings validation."""

    def test_returns_dict_for_each_profile(self):
        for name in (
            VIKINGBOAT_0411_PROFILE,
            VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
        ):
            with self.subTest(profile=name):
                settings = profile_settings(name)
                self.assertIsInstance(settings, dict)
                self.assertIn("top_k", settings)
                self.assertIn("tool_set", settings)
                self.assertIn("tool_names", settings)
                self.assertIn("agent_plugin", settings)

    def test_unknown_profile_raises(self):
        with self.assertRaisesRegex(ValueError, "unknown LoCoMo QA profile"):
            profile_settings("nonexistent")

    def test_vikingboat0411_has_positive_score_thresholds(self):
        s = profile_settings(VIKINGBOAT_0411_PROFILE)
        self.assertEqual(0.1, s["initial_min_score"])
        self.assertEqual(0.35, s["tool_min_score"])
        self.assertEqual("vikingbot_echo_native", s["tool_set"])
        self.assertEqual(4096, s["llm_max_tokens"])

    def test_natural_no_tools_inherits_vikingboat0411(self):
        s = profile_settings(VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE)
        base = profile_settings(VIKINGBOAT_0411_PROFILE)
        self.assertEqual(base, s)

    def test_all_profiles_share_same_top_k_and_timeout(self):
        for name in (
            VIKINGBOAT_0411_PROFILE,
            VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
        ):
            with self.subTest(profile=name):
                s = profile_settings(name)
                self.assertEqual(25, s["top_k"])
                self.assertEqual(25, s["tool_search_limit"])
                self.assertEqual(600.0, s["question_timeout_s"])

    def test_from_mapping_rejects_missing_fields(self):
        with self.assertRaisesRegex(ValueError, "missing fields"):
            ProfileSettings.from_mapping({"top_k": 10})

    def test_from_mapping_rejects_unknown_fields(self):
        full = profile_settings(VIKINGBOAT_0411_PROFILE)
        bad = {**full, "nonexistent_field": 42}
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            ProfileSettings.from_mapping(bad)

    def test_from_mapping_rejects_non_positive_values(self):
        full = profile_settings(VIKINGBOAT_0411_PROFILE)
        bad = {**full, "top_k": 0}
        with self.assertRaisesRegex(ValueError, "must be >= 1"):
            ProfileSettings.from_mapping(bad)

    def test_from_mapping_rejects_negative_score(self):
        full = profile_settings(VIKINGBOAT_0411_PROFILE)
        bad = {**full, "initial_min_score": -0.5}
        with self.assertRaisesRegex(ValueError, "score thresholds must be >= 0"):
            ProfileSettings.from_mapping(bad)

    def test_validate_rejects_bad_query_mode(self):
        full = profile_settings(VIKINGBOAT_0411_PROFILE)
        bad = {**full, "initial_retrieval_query_mode": "invalid"}
        with self.assertRaisesRegex(ValueError, "initial_retrieval_query_mode"):
            ProfileSettings.from_mapping(bad)


class ProfileSourceReferenceTests(unittest.TestCase):
    """Tests for profile_source and profile_reference."""

    def test_profile_source_returns_dict(self):
        src = profile_source(VIKINGBOAT_0411_PROFILE)
        self.assertIn("repository", src)
        self.assertEqual("openviking", src["repository"])

    def test_profile_source_unknown_returns_empty(self):
        self.assertEqual({}, profile_source("nonexistent"))

    def test_profile_reference_returns_string(self):
        ref = profile_reference(VIKINGBOAT_0411_PROFILE)
        self.assertIsInstance(ref, str)
        self.assertTrue(ref)

    def test_profile_reference_unknown_returns_empty(self):
        self.assertEqual("", profile_reference("nonexistent"))

    def test_profile_spec_returns_full_spec(self):
        spec = profile_spec(VIKINGBOAT_0411_PROFILE)
        self.assertEqual(VIKINGBOAT_0411_PROFILE, spec.name)
        self.assertIsInstance(spec.settings, ProfileSettings)


# ------------------------------------------------------------------ #
#  selection.py                                                      #
# ------------------------------------------------------------------ #

class ParseQuestionIdsTests(unittest.TestCase):
    """Tests for parse_question_ids parsing and dedup."""

    def test_dedup_preserves_order(self):
        self.assertEqual(["q1", "q2", "q3"],
                         parse_question_ids("q1,q2,q3,q1"))

    def test_strips_whitespace(self):
        self.assertEqual(["q1", "q2"],
                         parse_question_ids("  q1 , q2  "))

    def test_empty_returns_empty(self):
        self.assertEqual([], parse_question_ids(""))
        self.assertEqual([], parse_question_ids(None))
        self.assertEqual([], parse_question_ids(" , , "))


class SelectQuestionsLimitTests(unittest.TestCase):
    """Tests for select_questions limit (complementing existing tests)."""

    def test_limit_truncates(self):
        jobs = [SimpleNamespace(question_id=f"q{i}") for i in range(5)]
        selected = select_questions(jobs, limit=3)
        self.assertEqual(["q0", "q1", "q2"], [j.question_id for j in selected])

    def test_limit_zero_returns_all(self):
        jobs = [SimpleNamespace(question_id="q1"), SimpleNamespace(question_id="q2")]
        selected = select_questions(jobs, limit=0)
        self.assertEqual(2, len(selected))

    def test_question_ids_and_limit_combined(self):
        jobs = [SimpleNamespace(question_id=f"q{i}") for i in range(5)]
        selected = select_questions(jobs, question_ids=["q1", "q3"], limit=1)
        self.assertEqual(["q1"], [j.question_id for j in selected])


# ------------------------------------------------------------------ #
#  stats.py                                                          #
# ------------------------------------------------------------------ #

class SummarizeJudgeRowsTests(unittest.TestCase):
    """Tests for summarize_judge_rows with result field alias."""

    def test_uses_result_field_as_alias(self):
        summary = summarize_judge_rows([
            {"verdict": "", "result": "CORRECT", "judge_error": ""},
            {"verdict": "", "result": "WRONG", "judge_error": ""},
        ])
        self.assertEqual(2, summary["graded"])
        self.assertEqual(1, summary["correct"])
        self.assertEqual(1, summary["wrong"])
        self.assertEqual(0.5, summary["accuracy"])

    def test_empty_rows(self):
        summary = summarize_judge_rows([])
        self.assertEqual(0, summary["total"])
        self.assertEqual(0.0, summary["accuracy"])

    def test_errors_counted(self):
        summary = summarize_judge_rows([
            {"verdict": "CORRECT", "judge_error": ""},
            {"verdict": "ERROR", "judge_error": "timeout"},
        ])
        self.assertEqual(1, summary["graded"])
        self.assertEqual(1, summary["errors"])


# ------------------------------------------------------------------ #
#  blackbox.py (percentile/metric_stats)                             #
# ------------------------------------------------------------------ #

class BlackboxPercentileTests(unittest.TestCase):
    """Tests for percentile and metric_stats helpers."""

    def test_percentile_single_value(self):
        self.assertEqual(5.0, percentile([5.0], 0.5))

    def test_percentile_empty(self):
        self.assertIsNone(percentile([], 0.5))

    def test_percentile_median(self):
        self.assertEqual(3.0, percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5))

    def test_percentile_p95(self):
        vals = [float(i) for i in range(1, 21)]
        result = percentile(vals, 0.95)
        self.assertIsNotNone(result)
        self.assertTrue(18.0 <= result <= 20.0)

    def test_metric_stats_basic(self):
        stats = metric_stats([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(5, stats["count"])
        self.assertEqual(15.0, stats["sum"])
        self.assertEqual(3.0, stats["avg"])
        self.assertEqual(3.0, stats["p50"])
        self.assertEqual(5.0, stats["max"])

    def test_metric_stats_empty(self):
        stats = metric_stats([])
        self.assertEqual(0, stats["count"])
        self.assertIsNone(stats["avg"])
        self.assertIsNone(stats["max"])


# ------------------------------------------------------------------ #
#  run_eval.py build_parser                                          #
# ------------------------------------------------------------------ #

class RunEvalParserTests(unittest.TestCase):
    """Tests for build_parser declared arguments and defaults."""

    def setUp(self):
        self.parser = build_parser()
        # Use a minimal argv that provides all required env-dependent args
        self.args = self.parser.parse_args([
            "--llm-base-url", "https://model.test/v1",
            "--llm-api-key", "key",
        ])

    def test_dataset_params(self):
        self.assertEqual("", self.args.dataset)
        self.assertEqual("all", self.args.sample)
        self.assertEqual(0, self.args.questions)
        self.assertEqual("", self.args.question_ids)
        self.assertEqual("auto", self.args.session_mode)
        self.assertEqual(0, self.args.max_sessions)

    def test_judge_params(self):
        self.assertEqual("", self.args.judge_model)
        self.assertEqual("", self.args.judge_api_key)
        self.assertEqual("", self.args.judge_base_url)
        self.assertEqual(4, self.args.judge_concurrency)
        self.assertEqual(10, self.args.judge_checkpoint_interval)
        self.assertEqual("", self.args.resume_judge)

    def test_eval_infra_params(self):
        self.assertEqual(4, self.args.concurrency)
        self.assertEqual("results", self.args.out_dir)
        self.assertFalse(self.args.allow_diagnostics)

    def test_qa_profile_params(self):
        self.assertIsNone(self.args.qa_profile)
        self.assertEqual("", self.args.qa_prompt_file)
        self.assertEqual(10, self.args.checkpoint_interval)
        self.assertEqual("", self.args.resume_qa)

    def test_session_mode_choices(self):
        for mode in ("auto", "locomo", "single"):
            with self.subTest(mode=mode):
                args = self.parser.parse_args([
                    "--llm-base-url", "x", "--llm-api-key", "k",
                    "--session-mode", mode,
                ])
                self.assertEqual(mode, args.session_mode)

    def test_qa_profile_choices(self):
        for profile in (
            VIKINGBOAT_0411_PROFILE,
            VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
        ):
            with self.subTest(profile=profile):
                args = self.parser.parse_args([
                    "--llm-base-url", "x", "--llm-api-key", "k",
                    "--qa-profile", profile,
                ])
                self.assertEqual(profile, args.qa_profile)


# ------------------------------------------------------------------ #
#  resume.py (additional coverage)                                   #
# ------------------------------------------------------------------ #

class ResumeManifestTests(unittest.TestCase):
    """Tests for resume manifest building (complementing existing tests)."""

    def test_judge_resume_manifest_structure(self):
        from benchmarks.locomo.resume import build_judge_resume_manifest
        manifest = build_judge_resume_manifest(
            base_url="https://judge.test/v1/",
            model="judge-model",
            system_prompt="sys",
            prompt_template="tmpl",
        )
        self.assertEqual(1, manifest["schema_version"])
        self.assertEqual("locomo", manifest["benchmark"])
        self.assertEqual("https://judge.test/v1", manifest["judge"]["base_url"])
        self.assertEqual("judge-model", manifest["judge"]["model"])
        self.assertEqual(64, len(manifest["judge"]["prompt_sha256"]))

    def test_qa_resume_manifest_includes_memory_identity(self):
        from benchmarks.locomo.resume import build_qa_resume_manifest
        config = EvalConfig(
            llm_base_url="https://model.test/v1",
            llm_model="model",
            llm_max_tokens=1024,
        )
        agent_options = {
            "agent_plugin": "echomem_mcp",
            "initial_retrieval_protocol": "mcp",
            "user_memory_budget_chars": 4000,
            "agent_memory_budget_chars": 2000,
        }
        options = QAOptions(
            profile=VIKINGBOAT_0411_PROFILE,
            top_k=25,
            agent_options=agent_options,
        )
        manifest = build_qa_resume_manifest(
            dataset_path="/data/locomo.json",
            sample_filter="conv-30",
            session_mode="locomo",
            config=config,
            options=options,
            memory_identity={"account": "acc", "user_id": "u", "auth_key": "k"},
        )
        self.assertEqual("acc", manifest["memory_identity"]["account"])
        self.assertEqual("u", manifest["memory_identity"]["user_id"])
        self.assertEqual("locomo", manifest["session_mode"])
        self.assertEqual("model", manifest["answer_model"]["model"])
        self.assertEqual(agent_options, manifest["qa"]["agent_options"])

    def test_build_agent_options_records_mcp_switches_and_redacts_keys(self):
        args = SimpleNamespace(
            agent_plugin="echomem_mcp",
            qa_profile=None,
            tool_calling=True,
            search_in_tools=False,
            mcp_url="http://127.0.0.1:8001",
            mcp_auth_key="test-mcp-secret-123456",
            mcp_max_iterations=50,
            mcp_read_mode="disabled",
            echomem_auth_key="test-echomem-secret-abcdef",
            user_memory_budget_chars=4000,
            agent_memory_budget_chars=2000,
            judge_concurrency=10,
        )
        config = EvalConfig(
            top_k=25,
            memory_budget_chars=8000,
            question_timeout_s=0,
            llm_temperature=0.7,
            llm_timeout_s=600,
            llm_retries=3,
            concurrency=10,
        )
        options = _build_agent_options(args, config)
        self.assertEqual("echomem_mcp", options["agent_plugin"])
        self.assertTrue(options["tool_calling"])
        self.assertEqual("mcp", options["initial_retrieval_protocol"])
        self.assertNotIn("manual_search", options)
        self.assertNotIn("mcp_initial_search", options)
        self.assertEqual("disabled", options["mcp_read_mode"])
        self.assertEqual(4000, options["user_memory_budget_chars"])
        self.assertEqual(2000, options["agent_memory_budget_chars"])
        self.assertTrue(options["mcp_auth_key_configured"])
        self.assertEqual("test***3456", options["mcp_auth_key_redacted"])
        self.assertNotIn("test-mcp-secret-123456", json.dumps(options))

    def test_copy_resume_traces_filters_by_reusable_ids(self):
        from benchmarks.locomo.resume import (
            QAResumeState,
            copy_resume_traces,
        )
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            dest = Path(d) / "dest"
            trace_dir = source / "agent_traces"
            trace_dir.mkdir(parents=True)
            # 真实 trace 内容不含 question_id 字段，靠文件名（sanitized id）匹配
            (trace_dir / "q1.json").write_text(
                json.dumps({"tool_audit": {"tools_used": []}}), encoding="utf-8",
            )
            (trace_dir / "q2.json").write_text(
                json.dumps({"tool_audit": {"tools_used": []}}), encoding="utf-8",
            )
            state = QAResumeState(
                source_csv=source / "qa_results.csv",
                source_dir=source,
                results=[
                    QAResult(question_id="q1", question="Q", answer="A", response="R"),
                ],
                discarded_question_ids=[],
                manifest={},
            )
            copied = copy_resume_traces(state, dest)
            self.assertEqual(1, copied)
            self.assertTrue((dest / "agent_traces" / "q1.json").exists())
            self.assertFalse((dest / "agent_traces" / "q2.json").exists())

    def test_copy_resume_traces_no_trace_dir(self):
        from benchmarks.locomo.resume import (
            QAResumeState,
            copy_resume_traces,
        )
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            source.mkdir()
            state = QAResumeState(
                source_csv=source / "qa.csv",
                source_dir=source,
                results=[],
                discarded_question_ids=[],
                manifest={},
            )
            self.assertEqual(0, copy_resume_traces(state, Path(d) / "dest"))

    def test_restore_resume_traces_populates_reused_results(self):
        from benchmarks.locomo.resume import restore_resume_traces
        with tempfile.TemporaryDirectory() as d:
            result_dir = Path(d) / "result"
            trace_dir = result_dir / "agent_traces"
            trace_dir.mkdir(parents=True)
            (trace_dir / "q1.json").write_text(
                json.dumps({
                    "question_id": "q1",
                    "iterations": [{"model_response": {"response_model": "m1"}}],
                    "tool_protocol": {"sha256": "abc"},
                }),
                encoding="utf-8",
            )
            results = [
                QAResult(question_id="q1", question="Q", answer="A", response="R"),
                QAResult(question_id="q2", question="Q", answer="A", response="R"),
            ]
            restored = restore_resume_traces(results, result_dir)
            self.assertEqual(1, restored)
            self.assertEqual("m1", results[0].trace["iterations"][0]["model_response"]["response_model"])
            self.assertEqual("abc", results[0].trace["tool_protocol"]["sha256"])
            self.assertEqual({}, results[1].trace)

    def test_restore_resume_traces_no_trace_dir(self):
        from benchmarks.locomo.resume import restore_resume_traces
        with tempfile.TemporaryDirectory() as d:
            results = [
                QAResult(question_id="q1", question="Q", answer="A", response="R"),
            ]
            self.assertEqual(0, restore_resume_traces(results, Path(d)))

    def test_resume_dir_keeps_traces_and_tool_audits_for_reused(self):
        # resume 目录要与从 0 运行等价：复用题的 agent_traces + tool_audits 都要有
        from benchmarks.locomo.qa import write_tool_audits
        from benchmarks.locomo.resume import (
            QAResumeState,
            copy_resume_traces,
            restore_resume_traces,
        )
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            dest = Path(d) / "dest"
            trace_dir = source / "agent_traces"
            trace_dir.mkdir(parents=True)
            # 真实 trace：内容不含 question_id，靠文件名匹配
            (trace_dir / "q1.json").write_text(
                json.dumps({
                    "tool_audit": {
                        "tools_used": ["memory_list"],
                        "tool_calls": [],
                        "messages_jsonl_reads": [],
                    },
                }),
                encoding="utf-8",
            )
            state = QAResumeState(
                source_csv=source / "qa_results.csv",
                source_dir=source,
                results=[
                    QAResult(question_id="q1", question="Q", answer="A", response="R"),
                ],
                discarded_question_ids=[],
                manifest={},
            )
            self.assertEqual(1, copy_resume_traces(state, dest))
            results = [
                QAResult(question_id="q1", question="Q", answer="A", response="R"),
            ]
            self.assertEqual(1, restore_resume_traces(results, dest))
            write_tool_audits(dest, results)

            self.assertTrue((dest / "agent_traces" / "q1.json").is_file())
            audit_rows = [
                json.loads(line)
                for line in (dest / "tool_audits.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(1, len(audit_rows))
            self.assertEqual("q1", audit_rows[0]["question_id"])
            self.assertEqual(["memory_list"], audit_rows[0]["tools_used"])

    def test_find_judge_resume_csv_none_when_no_judge(self):
        from benchmarks.locomo.resume import find_judge_resume_csv
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "qa_results.csv").write_text("question_id\n", encoding="utf-8")
            # QA done but judge not yet -> no judge resume source
            self.assertIsNone(find_judge_resume_csv(root))

    def test_find_judge_resume_csv_returns_csv_when_present(self):
        from benchmarks.locomo.resume import find_judge_resume_csv
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "judge_results.csv").write_text("question_id\n", encoding="utf-8")
            self.assertEqual(root / "judge_results.csv", find_judge_resume_csv(root))

    def test_find_judge_resume_csv_falls_back_to_checkpoint(self):
        from benchmarks.locomo.resume import find_judge_resume_csv
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "judge_results.checkpoint.csv").write_text("question_id\n", encoding="utf-8")
            self.assertEqual(
                root / "judge_results.checkpoint.csv",
                find_judge_resume_csv(root),
            )


# ------------------------------------------------------------------ #
#  compare.py (additional coverage)                                  #
# ------------------------------------------------------------------ #

class CompareRunsAdditionalTests(unittest.TestCase):
    """Tests for compare_runs transitions and compatibility."""

    def _write_run(self, path: Path, qa_rows: list, judge_rows: list) -> None:
        path.mkdir(parents=True, exist_ok=True)
        for name, rows in (("qa_results.csv", qa_rows), ("judge_results.csv", judge_rows)):
            with (path / name).open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["question_id"])
                writer.writeheader()
                writer.writerows(rows)

    def test_added_and_missing_transitions(self):
        from benchmarks.locomo.compare import compare_runs
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            left_qa = [{"question_id": "q1", "category": "1", "question": "Q", "answer": "A", "response": "R"}]
            right_qa = [{"question_id": "q2", "category": "1", "question": "Q2", "answer": "A2", "response": "R2"}]
            self._write_run(root / "left", left_qa, [{"question_id": "q1", "verdict": "CORRECT"}])
            self._write_run(root / "right", right_qa, [{"question_id": "q2", "verdict": "WRONG"}])
            report = compare_runs(root / "left", root / "right")
            self.assertEqual(1, report["transition_counts"].get("missing", 0))
            self.assertEqual(1, report["transition_counts"].get("added", 0))

    def test_stable_transitions(self):
        from benchmarks.locomo.compare import compare_runs
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            qa = [{"question_id": "q1", "category": "1", "question": "Q", "answer": "A", "response": "R"}]
            self._write_run(root / "left", qa, [{"question_id": "q1", "verdict": "CORRECT"}])
            self._write_run(root / "right", qa, [{"question_id": "q1", "verdict": "CORRECT"}])
            report = compare_runs(root / "left", root / "right")
            self.assertEqual(1, report["transition_counts"].get("stable_correct", 0))


# ------------------------------------------------------------------ #
#  blackbox.py _import_observation / _elapsed_seconds                #
# ------------------------------------------------------------------ #

class BlackboxImportObservationTests(unittest.TestCase):
    """Tests for import observation and elapsed time in blackbox."""

    def test_reused_only_status(self):
        from benchmarks.locomo.blackbox import _import_observation
        result = _import_observation([{"status": "reused", "message_count": "5", "submitted_messages": "5"}])
        self.assertEqual("reused", result["status"])
        self.assertIsNone(result["expected_messages"])

    def test_completed_status(self):
        from benchmarks.locomo.blackbox import _import_observation
        result = _import_observation([
            {"status": "completed", "message_count": "10", "submitted_messages": "10"},
        ])
        self.assertEqual("completed", result["status"])
        self.assertEqual(10, result["expected_messages"])
        self.assertEqual(10, result["submitted_messages"])

    def test_incomplete_status(self):
        from benchmarks.locomo.blackbox import _import_observation
        result = _import_observation([
            {"status": "completed", "message_count": "10", "submitted_messages": "10"},
            {"status": "error", "message_count": "5", "submitted_messages": "3"},
        ])
        self.assertEqual("incomplete", result["status"])
        self.assertEqual(15, result["expected_messages"])
        self.assertEqual(13, result["submitted_messages"])

    def test_elapsed_seconds_valid(self):
        from benchmarks.locomo.blackbox import _elapsed_seconds
        self.assertEqual(
            4.0,
            _elapsed_seconds("2026-07-28T00:00:00+00:00", "2026-07-28T00:00:04+00:00"),
        )

    def test_elapsed_seconds_missing(self):
        from benchmarks.locomo.blackbox import _elapsed_seconds
        self.assertIsNone(_elapsed_seconds("", "2026-07-28T00:00:04+00:00"))
        self.assertIsNone(_elapsed_seconds("2026-07-28T00:00:00+00:00", ""))


if __name__ == "__main__":
    unittest.main()
