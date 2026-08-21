"""Comprehensive unit tests for benchmarks/longmemeval/ modules.

Covers every functional point in the LongMemEval benchmark source code
that is not already exercised by tests/test_longmemeval_workflows.py.

All tests use mocks -- no real services, no subprocesses, no network.
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from benchmarks.longmemeval.dataset import (
    _compact,
    _documents,
    _events,
    _messages,
    _sessions,
    _token_estimate,
    load_dataset,
    parse_datetime,
    session_batches,
)
from benchmarks.longmemeval.evaluate import (
    EVAL_FIELDS,
    TASK_TYPES,
    EvaluationReport,
    evaluate_longmemeval,
)
from benchmarks.longmemeval.import_memory import (
    IMPORT_FIELDS,
    ImportReport,
    _fallback_batches,
    import_longmemeval_memory,
)
from benchmarks.longmemeval.judge import (
    build_answer_check_prompt,
    judge_answer,
    parse_yes_no,
)
from benchmarks.longmemeval.parallel import (
    _clean_forwarded_args,
    build_shard_commands,
    partition_question_ids,
    run_parallel,
)
from benchmarks.longmemeval.qa import (
    QA_FIELDS,
    build_qa_tasks,
    run_longmemeval_qa,
)
from benchmarks.longmemeval.recovery import (
    build_parser as build_recovery_parser,
    main as recovery_main,
    merge_csv_files,
    merge_shard_artifacts,
)
from benchmarks.longmemeval.reporting import build_summary
from benchmarks.longmemeval.run_eval import build_parser as build_eval_parser
from benchmarks.longmemeval.selection import (
    parse_question_ids,
    select_jobs_and_plans,
)
from shared.eval_base import EvalConfig
from shared.llm_client import LLMResponse
from shared.qa import BASE_QA_FIELDS, QAResult


# ------------------------------------------------------------------ #
#  Helpers                                                            #
# ------------------------------------------------------------------ #

class _Log:
    """Minimal logger stub."""

    def info(self, *_args):
        return None

    def error(self, *_args):
        return None


class _JudgeLLM:
    """Fake judge LLM that returns predefined responses in order.

    Repeats the last response when exhausted so retry loops run their full
    budget instead of raising StopIteration.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self._index = 0
        self.calls = []

    def chat(
        self,
        messages,
        *,
        temperature=None,
        response_format=False,
        thinking_disabled=False,
        omit_max_tokens=False,
    ):
        self.calls.append((
            messages,
            temperature,
            response_format,
            thinking_disabled,
            omit_max_tokens,
        ))
        content = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        return LLMResponse(
            content=content,
            prompt_tokens=0,
            completion_tokens=0,
            elapsed_s=0.0,
        )


class _CommitResult:
    """Fake poll_commit result."""

    def __init__(self, status="completed", elapsed_s=0.5, error=""):
        self.status = status
        self.elapsed_s = elapsed_s
        self.error = error


class _FakeMemoryClient:
    """Fake memory client for import tests."""

    def __init__(self, fail_on_question=None):
        self.fail_on_question = fail_on_question or set()
        self.open_calls: list[str] = []
        self.message_calls: list[tuple] = []
        self.commit_calls: list[str] = []
        self._next = 0

    def open_session(self, title=""):
        self.open_calls.append(title)
        self._next += 1
        return f"session_{self._next}"

    def add_message(self, session_id, role, content, created_at="", role_id=""):
        self.message_calls.append((session_id, role, content, created_at, role_id))

    def commit_session(self, session_id):
        self.commit_calls.append(session_id)
        return f"archive_{session_id}"

    def poll_commit(self, session_id, archive_id, timeout_s=0, poll_interval_s=2):
        return _CommitResult()


def _sample_item(**overrides):
    """Build a minimal LongMemEval dataset item."""
    item = {
        "question_id": "q1",
        "question": "What color?",
        "answer": "Blue",
        "question_type": "single-session-user",
        "question_date": "2024-01-15T10:00:00Z",
        "haystack_session_ids": ["sess_1"],
        "haystack_dates": ["2024-01-10 14:30"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I like blue"},
                {"role": "assistant", "content": "Nice"},
            ]
        ],
    }
    item.update(overrides)
    return item


def _write_dataset(directory, items):
    path = Path(directory) / "dataset.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


# ------------------------------------------------------------------ #
#  dataset.py                                                         #
# ------------------------------------------------------------------ #

class DatasetTests(unittest.TestCase):

    # -- _compact --

    def test_compact_short_text_unchanged(self):
        self.assertEqual("hello world", _compact("hello world"))

    def test_compact_long_text_truncated(self):
        text = "a" * 500
        result = _compact(text)
        self.assertEqual(320, len(result))
        self.assertTrue(result.endswith("..."))

    def test_compact_none_and_empty(self):
        self.assertEqual("", _compact(None))
        self.assertEqual("", _compact(""))

    def test_compact_whitespace_normalized(self):
        self.assertEqual("a b c", _compact("a   b\n\n  c"))

    def test_compact_custom_limit(self):
        result = _compact("abcdefghij", limit=8)
        self.assertEqual("abcde...", result)

    def test_compact_exact_boundary(self):
        text = "a" * 320
        self.assertEqual(text, _compact(text))

    # -- _token_estimate --

    def test_token_estimate_normal(self):
        self.assertEqual(2, _token_estimate("abcdefgh"))

    def test_token_estimate_empty(self):
        self.assertEqual(0, _token_estimate(""))

    def test_token_estimate_single_char(self):
        self.assertEqual(1, _token_estimate("x"))

    def test_token_estimate_none(self):
        self.assertEqual(0, _token_estimate(None))

    # -- parse_datetime --

    def test_parse_datetime_iso(self):
        dt = parse_datetime("2024-01-15T10:30:00")
        self.assertEqual(datetime(2024, 1, 15, 10, 30, 0), dt)

    def test_parse_datetime_z_suffix(self):
        dt = parse_datetime("2024-01-15T10:30:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(2024, dt.year)
        self.assertEqual(1, dt.month)
        self.assertEqual(15, dt.day)
        self.assertEqual(10, dt.hour)
        self.assertEqual(30, dt.minute)

    def test_parse_datetime_lowercase_z(self):
        dt = parse_datetime("2024-01-15T10:30:00z")
        self.assertIsNotNone(dt)
        self.assertEqual(2024, dt.year)

    def test_parse_datetime_formats(self):
        cases = [
            ("2024/01/15 (Mon) 14:30", datetime(2024, 1, 15, 14, 30)),
            ("2024/01/15 14:30:00", datetime(2024, 1, 15, 14, 30, 0)),
            ("2024/01/15 14:30", datetime(2024, 1, 15, 14, 30)),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(expected, parse_datetime(text))

    def test_parse_datetime_empty_and_none(self):
        for value in ("", None, "   "):
            with self.subTest(value=value):
                self.assertIsNone(parse_datetime(value))

    def test_parse_datetime_invalid(self):
        self.assertIsNone(parse_datetime("not-a-date"))

    # -- _sessions --

    def test_sessions_fallbacks(self):
        for key in ("haystack_sessions", "sessions", "conversation"):
            with self.subTest(key=key):
                item = {key: [["msg"]]}
                self.assertEqual([["msg"]], _sessions(item))

    def test_sessions_non_list(self):
        item = {"haystack_sessions": "not-a-list"}
        self.assertEqual([], _sessions(item))

    def test_sessions_missing(self):
        self.assertEqual([], _sessions({}))

    # -- _messages --

    def test_messages_dict_keys(self):
        for key in ("messages", "conversation", "turns"):
            with self.subTest(key=key):
                session = {key: [{"role": "user"}]}
                self.assertEqual([{"role": "user"}], _messages(session))

    def test_messages_list_session(self):
        msgs = [{"role": "user", "content": "hi"}]
        self.assertEqual(msgs, _messages(msgs))

    def test_messages_non_list_wrapped(self):
        self.assertEqual(["hello"], _messages("hello"))

    def test_messages_none_session(self):
        self.assertEqual([None], _messages(None))

    # -- session_batches --

    def test_session_batches_basic(self):
        item = _sample_item()
        batches = session_batches(item)
        self.assertEqual(1, len(batches))
        batch = batches[0]
        self.assertEqual("sess_1", batch["session_key"])
        self.assertEqual("2024-01-10 14:30", batch["date_time"])
        self.assertEqual(2, len(batch["messages"]))
        self.assertEqual("user", batch["messages"][0]["role"])
        self.assertEqual("I like blue", batch["messages"][0]["content"])
        self.assertEqual("assistant", batch["messages"][1]["role"])

    def test_session_batches_created_at_from_base_time(self):
        item = _sample_item()
        batches = session_batches(item)
        first_created = batches[0]["messages"][0]["created_at"]
        second_created = batches[0]["messages"][1]["created_at"]
        self.assertNotEqual(None, first_created)
        self.assertNotEqual(None, second_created)
        # second message is 1 second after the first
        t1 = datetime.fromisoformat(first_created)
        t2 = datetime.fromisoformat(second_created)
        self.assertEqual(1, (t2 - t1).seconds)

    def test_session_batches_session_id_index_fallback(self):
        item = _sample_item()
        del item["haystack_session_ids"]
        batches = session_batches(item)
        self.assertEqual("session_0", batches[0]["session_key"])

    def test_session_batches_empty_content_skipped(self):
        item = _sample_item(
            haystack_sessions=[[{"role": "user", "content": ""}, {"role": "user", "content": "keep"}]]
        )
        batches = session_batches(item)
        self.assertEqual(1, len(batches[0]["messages"]))
        self.assertEqual("keep", batches[0]["messages"][0]["content"])

    def test_session_batches_role_fallbacks(self):
        for role_key, role_val in [("speaker", "bot"), ("user", "caller")]:
            with self.subTest(role_key=role_key):
                item = _sample_item(
                    haystack_sessions=[[{role_key: role_val, "content": "hi"}]]
                )
                batches = session_batches(item)
                self.assertEqual(role_val, batches[0]["messages"][0]["role"])

    def test_session_batches_non_dict_message(self):
        item = _sample_item(haystack_sessions=[["plain text message"]])
        batches = session_batches(item)
        self.assertEqual(1, len(batches[0]["messages"]))
        self.assertEqual("user", batches[0]["messages"][0]["role"])
        self.assertEqual("plain text message", batches[0]["messages"][0]["content"])

    def test_session_batches_no_sessions(self):
        item = {"question": "q", "haystack_sessions": []}
        self.assertEqual([], session_batches(item))

    def test_session_batches_content_key_fallbacks(self):
        for key in ("text", "message"):
            with self.subTest(key=key):
                item = _sample_item(
                    haystack_sessions=[[{key: "alt content"}]]
                )
                batches = session_batches(item)
                self.assertEqual("alt content", batches[0]["messages"][0]["content"])

    # -- _events --

    def test_events_from_batches(self):
        batches = [
            {
                "session_key": "s1",
                "date_time": "2024-01-10",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                ],
            }
        ]
        events = _events(batches)
        self.assertEqual(2, len(events))
        self.assertEqual("2024-01-10", events[0]["time"])
        self.assertIn("s1", events[0]["text"])
        self.assertIn("turn_0", events[0]["text"])
        self.assertIn("user", events[0]["text"])
        self.assertIn("hello", events[0]["text"])
        self.assertIn("turn_1", events[1]["text"])
        self.assertIn("assistant", events[1]["text"])

    def test_events_empty_batches(self):
        self.assertEqual([], _events([]))

    # -- _documents --

    def test_documents_from_batches(self):
        batches = [
            {
                "session_key": "s1",
                "date_time": "2024-01-10",
                "messages": [{"role": "user", "content": "hello"}],
            }
        ]
        docs = _documents(batches)
        self.assertEqual(1, len(docs))
        doc = docs[0]
        self.assertEqual("s1", doc["doc_id"])
        self.assertEqual("s1", doc["title"])
        self.assertEqual("2024-01-10", doc["time"])
        self.assertIn("source_dataset: LongMemEval", doc["text"])
        self.assertIn("session_id: s1", doc["text"])
        self.assertIn("turn_0 user: hello", doc["text"])

    def test_documents_empty_date_time(self):
        batches = [{"session_key": "s1", "date_time": "", "messages": []}]
        docs = _documents(batches)
        self.assertEqual("", docs[0]["time"])
        self.assertIn("time: -", docs[0]["text"])

    # -- load_dataset --

    def test_load_dataset_basic(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_dataset(d, [_sample_item()])
            jobs, plans = load_dataset(path)
            self.assertEqual(1, len(jobs))
            self.assertEqual(1, len(plans))
            job = jobs[0]
            self.assertEqual("longmemeval", job.dataset_format)
            self.assertEqual("q1", job.sample_id)
            self.assertEqual("q1", job.question_id)
            self.assertEqual("What color?", job.question)
            self.assertEqual("Blue", job.answer)
            self.assertEqual("single-session-user", job.category)
            self.assertEqual(2, job.injection_events)
            self.assertGreater(job.injection_tokens_est, 0)
            plan = plans[0]
            self.assertEqual("q1", plan["sample_id"])
            self.assertEqual(2, plan["event_count"])
            self.assertEqual(2, len(plan["events"]))
            self.assertEqual(2, len(plan["preview_events"]))
            self.assertEqual(1, len(plan["memory_documents"]))
            self.assertEqual(1, len(plan["session_batches"]))

    def test_load_dataset_multiple_items(self):
        items = [
            _sample_item(question_id="q1"),
            _sample_item(question_id="q2", question="Other?", answer="Other"),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = _write_dataset(d, items)
            jobs, plans = load_dataset(path)
            self.assertEqual(2, len(jobs))
            self.assertEqual(["q1", "q2"], [j.question_id for j in jobs])

    def test_load_dataset_sample_filter_by_index(self):
        items = [
            _sample_item(question_id="q1"),
            _sample_item(question_id="q2"),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = _write_dataset(d, items)
            jobs, _ = load_dataset(path, sample_filter="0")
            self.assertEqual(1, len(jobs))
            self.assertEqual("q1", jobs[0].question_id)

    def test_load_dataset_sample_filter_by_id(self):
        items = [
            _sample_item(question_id="q1"),
            _sample_item(question_id="q2"),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = _write_dataset(d, items)
            jobs, _ = load_dataset(path, sample_filter="q2")
            self.assertEqual(1, len(jobs))
            self.assertEqual("q2", jobs[0].question_id)

    def test_load_dataset_field_fallbacks(self):
        item = {
            "id": "fallback_id",
            "query": "What?",
            "gold_answer": "Gold",
            "category": "multi-session",
            "query_time": "2024-03-01",
            "sessions": [[{"role": "user", "content": "hi"}]],
        }
        with tempfile.TemporaryDirectory() as d:
            path = _write_dataset(d, [item])
            jobs, _ = load_dataset(path)
            self.assertEqual("fallback_id", jobs[0].question_id)
            self.assertEqual("What?", jobs[0].question)
            self.assertEqual("Gold", jobs[0].answer)
            self.assertEqual("multi-session", jobs[0].category)

    def test_load_dataset_query_time_parsing(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_dataset(d, [_sample_item(question_date="2024-06-15T08:00:00Z")])
            jobs, _ = load_dataset(path)
            self.assertEqual("2024-06-15", jobs[0].query_time)

    def test_load_dataset_unparseable_query_time(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_dataset(d, [_sample_item(question_date="garbage")])
            jobs, _ = load_dataset(path)
            self.assertEqual("garbage", jobs[0].query_time)

    def test_load_dataset_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_dataset(d, [])
            jobs, plans = load_dataset(path)
            self.assertEqual([], jobs)
            self.assertEqual([], plans)

    def test_load_dataset_dict_with_data_key(self):
        item = _sample_item()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "dataset.json"
            path.write_text(json.dumps({"data": [item]}), encoding="utf-8")
            jobs, _ = load_dataset(path)
            self.assertEqual(1, len(jobs))

    def test_load_dataset_default_category(self):
        item = {"question_id": "q1", "question": "Q", "answer": "A", "haystack_sessions": []}
        with tempfile.TemporaryDirectory() as d:
            path = _write_dataset(d, [item])
            jobs, _ = load_dataset(path)
            self.assertEqual("longmemeval", jobs[0].category)


# ------------------------------------------------------------------ #
#  judge.py                                                           #
# ------------------------------------------------------------------ #

class JudgeTests(unittest.TestCase):

    def test_prompt_single_session_user(self):
        prompt = build_answer_check_prompt(
            "single-session-user", "Q", "A", "R"
        )
        self.assertIn("contains the correct answer", prompt)
        self.assertIn("Question: Q", prompt)
        self.assertIn("Correct Answer: A", prompt)
        self.assertIn("Model Response: R", prompt)

    def test_prompt_single_session_assistant(self):
        prompt = build_answer_check_prompt(
            "single-session-assistant", "Q", "A", "R"
        )
        self.assertIn("contains the correct answer", prompt)

    def test_prompt_multi_session(self):
        prompt = build_answer_check_prompt(
            "multi-session", "Q", "A", "R"
        )
        self.assertIn("contains the correct answer", prompt)

    def test_prompt_temporal_reasoning(self):
        prompt = build_answer_check_prompt(
            "temporal-reasoning", "Q", "A", "R"
        )
        self.assertIn("off-by-one", prompt)
        self.assertIn("number of days", prompt)

    def test_prompt_knowledge_update(self):
        prompt = build_answer_check_prompt(
            "knowledge-update", "Q", "A", "R"
        )
        self.assertIn("updated answer", prompt)

    def test_prompt_single_session_preference(self):
        prompt = build_answer_check_prompt(
            "single-session-preference", "Q", "A", "R"
        )
        self.assertIn("rubric", prompt)
        self.assertIn("personal information", prompt)

    def test_prompt_abstention(self):
        prompt = build_answer_check_prompt(
            "single-session-user", "Q", "A", "R", abstention=True
        )
        self.assertIn("unanswerable", prompt)
        self.assertIn("Explanation: A", prompt)

    def test_prompt_abstention_overrides_known_task(self):
        prompt = build_answer_check_prompt(
            "temporal-reasoning", "Q", "A", "R", abstention=True
        )
        self.assertIn("unanswerable", prompt)
        self.assertNotIn("off-by-one", prompt)

    def test_parse_yes_no_yes(self):
        self.assertTrue(parse_yes_no("yes"))

    def test_parse_yes_no_no(self):
        self.assertFalse(parse_yes_no("no"))

    def test_parse_yes_no_case_insensitive(self):
        self.assertTrue(parse_yes_no("Yes"))
        self.assertFalse(parse_yes_no("No"))
        self.assertTrue(parse_yes_no("YES"))

    def test_parse_yes_no_with_surrounding_text(self):
        self.assertTrue(parse_yes_no("The answer is yes."))
        self.assertFalse(parse_yes_no("I think no."))

    def test_parse_yes_no_both_raises(self):
        with self.assertRaises(ValueError):
            parse_yes_no("yes and no")

    def test_parse_yes_no_neither_raises(self):
        with self.assertRaises(ValueError):
            parse_yes_no("maybe")

    def test_parse_yes_no_empty_raises(self):
        # "" -> ValueError; None -> TypeError (source bug: text[:200] on None)
        with self.assertRaises(ValueError):
            parse_yes_no("")
        with self.assertRaises((ValueError, TypeError)):
            parse_yes_no(None)

    def test_judge_answer_returns_true(self):
        llm = _JudgeLLM(["yes"])
        result = judge_answer(llm, "single-session-user", "Q", "A", "R")
        self.assertTrue(result)

    def test_judge_answer_returns_false(self):
        llm = _JudgeLLM(["no"])
        result = judge_answer(llm, "single-session-user", "Q", "A", "R")
        self.assertFalse(result)

    def test_judge_answer_calls_llm(self):
        llm = _JudgeLLM(["yes"])
        judge_answer(llm, "multi-session", "Q", "A", "R", abstention=True)
        self.assertEqual(1, len(llm.calls))
        messages, temperature, response_format, thinking_disabled, omit_max_tokens = llm.calls[0]
        self.assertIsNone(temperature)
        # Yes/no verdict: JSON off, but thinking off and no max_tokens cap.
        self.assertFalse(response_format)
        self.assertTrue(thinking_disabled)
        self.assertTrue(omit_max_tokens)
        self.assertIn("answer evaluation assistant", messages[0]["content"])
        self.assertIn("unanswerable", messages[1]["content"])

    def test_judge_answer_propagates_parse_error(self):
        llm = _JudgeLLM(["maybe"])
        with patch("shared.llm_client.time.sleep"):
            with self.assertRaises(ValueError):
                judge_answer(llm, "multi-session", "Q", "A", "R")

    def test_judge_answer_recovers_via_repair_retry(self):
        # Empty judge output is retried with a corrective instruction and a
        # higher temperature instead of failing the question.
        llm = _JudgeLLM(["", "yes"])
        with patch("shared.llm_client.time.sleep"):
            result = judge_answer(llm, "single-session-user", "Q", "A", "R")
        self.assertTrue(result)
        self.assertEqual(2, len(llm.calls))
        messages, temperature, *_ = llm.calls[1]
        self.assertEqual(0.3, temperature)
        self.assertIn("single word: yes or no", messages[1]["content"])


# ------------------------------------------------------------------ #
#  evaluate.py                                                        #
# ------------------------------------------------------------------ #

class EvaluateTests(unittest.TestCase):

    def test_task_types_constant(self):
        self.assertEqual(6, len(TASK_TYPES))
        for t in TASK_TYPES:
            with self.subTest(task=t):
                self.assertIsInstance(t, str)

    def test_eval_fields_constant(self):
        self.assertIn("question_id", EVAL_FIELDS)
        self.assertIn("correct", EVAL_FIELDS)
        self.assertIn("judge_error", EVAL_FIELDS)

    def test_evaluate_all_correct(self):
        qa_results = [QAResult("q1", "Q1", "A1", "R1")]
        jobs = [SimpleNamespace(category="single-session-user")]
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_longmemeval(
                qa_results, jobs, _JudgeLLM(["yes"]), Path(d), _Log()
            )
        self.assertEqual(1, report.correct)
        self.assertEqual(1, report.graded)
        self.assertAlmostEqual(1.0, report.overall_accuracy)
        self.assertEqual(0, report.errors)

    def test_evaluate_all_wrong(self):
        qa_results = [QAResult("q1", "Q1", "A1", "R1")]
        jobs = [SimpleNamespace(category="single-session-user")]
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_longmemeval(
                qa_results, jobs, _JudgeLLM(["no"]), Path(d), _Log()
            )
        self.assertEqual(0, report.correct)
        self.assertEqual(1, report.graded)
        self.assertAlmostEqual(0.0, report.overall_accuracy)

    def test_evaluate_llm_error_skipped(self):
        qa_results = [QAResult("q1", "Q1", "A1", "R1", llm_error="timeout")]
        jobs = [SimpleNamespace(category="single-session-user")]
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_longmemeval(
                qa_results, jobs, _JudgeLLM([]), Path(d), _Log()
            )
        self.assertEqual(0, report.correct)
        self.assertEqual(0, report.graded)
        self.assertEqual(1, report.errors)
        self.assertAlmostEqual(0.0, report.overall_accuracy)

    def test_evaluate_retrieval_error_skipped(self):
        qa_results = [QAResult("q1", "Q1", "A1", "R1", retrieval_error="empty")]
        jobs = [SimpleNamespace(category="single-session-user")]
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_longmemeval(
                qa_results, jobs, _JudgeLLM([]), Path(d), _Log()
            )
        self.assertEqual(1, report.errors)
        self.assertEqual(0, report.graded)

    def test_evaluate_judge_exception(self):
        qa_results = [QAResult("q1", "Q1", "A1", "R1")]
        jobs = [SimpleNamespace(category="single-session-user")]
        with tempfile.TemporaryDirectory() as d:
            with patch("shared.llm_client.time.sleep"):
                report = evaluate_longmemeval(
                    qa_results, jobs, _JudgeLLM(["ambiguous"]), Path(d), _Log()
                )
        self.assertEqual(1, report.errors)
        self.assertEqual(0, report.graded)
        rows = report.rows
        self.assertIn("not unambiguous", rows[0]["judge_error"])

    def test_evaluate_csv_output(self):
        qa_results = [QAResult("q1", "Q1", "A1", "R1")]
        jobs = [SimpleNamespace(category="single-session-user")]
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_longmemeval(
                qa_results, jobs, _JudgeLLM(["yes"]), Path(d), _Log()
            )
            csv_path = Path(d) / "eval_results.csv"
            self.assertTrue(csv_path.is_file())
            with csv_path.open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        self.assertEqual(1, len(rows))
        self.assertEqual("q1", rows[0]["question_id"])
        self.assertEqual("single-session-user", rows[0]["question_type"])
        self.assertEqual("True", rows[0]["correct"])
        self.assertEqual("", rows[0]["judge_error"])

    def test_evaluate_per_type_details(self):
        qa_results = [
            QAResult("q1", "Q1", "A1", "R1"),
            QAResult("q2", "Q2", "A2", "R2"),
        ]
        jobs = [
            SimpleNamespace(category="single-session-user"),
            SimpleNamespace(category="multi-session"),
        ]
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_longmemeval(
                qa_results, jobs, _JudgeLLM(["yes", "no"]), Path(d), _Log()
            )
        self.assertIn("single-session-user", report.per_type)
        self.assertIn("multi-session", report.per_type)
        ssu = report.per_type["single-session-user"]
        self.assertEqual(1, ssu["correct"])
        self.assertEqual(1, ssu["total"])
        self.assertAlmostEqual(1.0, ssu["accuracy"])
        ms = report.per_type["multi-session"]
        self.assertEqual(0, ms["correct"])
        self.assertAlmostEqual(0.0, ms["accuracy"])

    def test_evaluate_task_averaged_accuracy(self):
        qa_results = [
            QAResult("q1", "Q1", "A1", "R1"),
            QAResult("q2", "Q2", "A2", "R2"),
        ]
        jobs = [
            SimpleNamespace(category="single-session-user"),
            SimpleNamespace(category="multi-session"),
        ]
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_longmemeval(
                qa_results, jobs, _JudgeLLM(["yes", "no"]), Path(d), _Log()
            )
        self.assertAlmostEqual(0.5, report.task_averaged_accuracy)

    def test_evaluate_empty_results(self):
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_longmemeval(
                [], [], _JudgeLLM([]), Path(d), _Log()
            )
        self.assertEqual(0, report.correct)
        self.assertEqual(0, report.graded)
        self.assertAlmostEqual(0.0, report.overall_accuracy)
        self.assertIsNone(report.task_averaged_accuracy)
        self.assertIsNone(report.abstention_accuracy)
        self.assertEqual(0, report.abstention_count)

    def test_evaluate_abstension_only(self):
        qa_results = [QAResult("q1_abs", "Q", "unknown", "I don't know")]
        jobs = [SimpleNamespace(category="single-session-user")]
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_longmemeval(
                qa_results, jobs, _JudgeLLM(["yes"]), Path(d), _Log()
            )
        self.assertEqual(1, report.abstention_count)
        self.assertAlmostEqual(1.0, report.abstention_accuracy)

    def test_evaluation_report_dataclass(self):
        report = EvaluationReport(
            rows=[],
            correct=5,
            graded=10,
            errors=2,
            overall_accuracy=0.5,
            task_averaged_accuracy=0.6,
            abstention_accuracy=0.8,
            abstention_count=3,
            per_type={},
        )
        self.assertEqual(5, report.correct)
        self.assertEqual(10, report.graded)
        self.assertEqual(2, report.errors)
        self.assertAlmostEqual(0.5, report.overall_accuracy)


# ------------------------------------------------------------------ #
#  qa.py                                                              #
# ------------------------------------------------------------------ #

class QATests(unittest.TestCase):

    def test_qa_fields_includes_retrieval_items(self):
        self.assertIn("retrieval_items_json", QA_FIELDS)
        for field in BASE_QA_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, QA_FIELDS)

    def test_build_qa_tasks_basic(self):
        job = SimpleNamespace(
            question_id="q1",
            sample_id="s1",
            category="single-session-user",
            question="What?",
            answer="Blue",
            query_time="2024-01-15",
        )
        config = EvalConfig(top_k=5, memory_budget_chars=4000)
        tasks = build_qa_tasks([job], {}, config, agent_id="agent_1")
        self.assertEqual(1, len(tasks))
        task = tasks[0]
        self.assertEqual("q1", task["question_id"])
        self.assertEqual("s1", task["sample_id"])
        self.assertEqual("single-session-user", task["category"])
        self.assertEqual("What?", task["question"])
        self.assertEqual("Blue", task["answer"])
        self.assertEqual(5, task["top_k"])
        self.assertEqual(4000, task["memory_budget_chars"])
        self.assertEqual("", task["session_id"])
        self.assertEqual("agent_1", task["agent_id"])
        self.assertEqual("2024-01-15", task["question_time"])

    def test_build_qa_tasks_with_session_mapping(self):
        job = SimpleNamespace(
            question_id="q1",
            sample_id="s1",
            category="cat",
            question="Q",
            answer="A",
            query_time="2024-01-15",
        )
        config = EvalConfig()
        tasks = build_qa_tasks([job], {"q1": "sess_123"}, config)
        self.assertEqual("sess_123", tasks[0]["session_id"])
        self.assertEqual("", tasks[0]["agent_id"])

    def test_build_qa_tasks_empty_jobs(self):
        config = EvalConfig()
        self.assertEqual([], build_qa_tasks([], {}, config))

    @patch("benchmarks.longmemeval.qa.run_concurrent_qa")
    def test_run_qa_writes_csv(self, mock_run):
        result = QAResult(
            question_id="q1", question="Q1", answer="A1", response="R1",
            prompt_tokens=10, completion_tokens=5,
        )
        mock_run.return_value = [result]
        config = EvalConfig()
        with tempfile.TemporaryDirectory() as d:
            returned = run_longmemeval_qa(
                [{"question_id": "q1"}], MagicMock(), config, Path(d), _Log()
            )
            csv_path = Path(d) / "qa_results.csv"
            self.assertTrue(csv_path.is_file())
            with csv_path.open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        self.assertEqual(1, len(returned))
        self.assertEqual("q1", rows[0]["question_id"])
        self.assertEqual("R1", rows[0]["response"])
        self.assertEqual("[]", rows[0]["retrieval_items_json"])

    @patch("benchmarks.longmemeval.qa.run_concurrent_qa")
    def test_run_qa_retrieval_items_json(self, mock_run):
        result = QAResult(
            question_id="q1", question="Q", answer="A", response="R",
            retrieval_items=[{"text": "memory", "score": 0.9}],
        )
        mock_run.return_value = [result]
        config = EvalConfig()
        with tempfile.TemporaryDirectory() as d:
            run_longmemeval_qa(
                [{"question_id": "q1"}], MagicMock(), config, Path(d), _Log()
            )
            csv_path = Path(d) / "qa_results.csv"
            with csv_path.open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        items = json.loads(rows[0]["retrieval_items_json"])
        self.assertEqual(1, len(items))
        self.assertEqual("memory", items[0]["text"])

    @patch("benchmarks.longmemeval.qa.run_concurrent_qa")
    def test_run_qa_empty_tasks(self, mock_run):
        mock_run.return_value = []
        config = EvalConfig()
        with tempfile.TemporaryDirectory() as d:
            results = run_longmemeval_qa(
                [], MagicMock(), config, Path(d), _Log()
            )
        self.assertEqual([], results)


# ------------------------------------------------------------------ #
#  selection.py                                                       #
# ------------------------------------------------------------------ #

class SelectionTests(unittest.TestCase):

    def test_parse_question_ids_basic(self):
        self.assertEqual(["q1", "q2"], parse_question_ids("q1,q2"))

    def test_parse_question_ids_empty_string(self):
        self.assertEqual([], parse_question_ids(""))

    def test_parse_question_ids_none(self):
        self.assertEqual([], parse_question_ids(None))

    def test_parse_question_ids_dedup(self):
        self.assertEqual(["q1", "q2"], parse_question_ids("q1,q2,q1"))

    def test_parse_question_ids_whitespace(self):
        self.assertEqual(["q1", "q2"], parse_question_ids("  q1 , q2  "))

    def _make_jobs(self, count):
        return [
            SimpleNamespace(
                question_id=f"q{i}",
                native_question_id=f"native_{i}",
                sample_id=f"s{i}",
            )
            for i in range(count)
        ]

    def test_select_by_question_id(self):
        jobs = self._make_jobs(5)
        plans = [{"i": i} for i in range(5)]
        selected_jobs, selected_plans = select_jobs_and_plans(
            jobs, plans, question_ids=["q2"]
        )
        self.assertEqual(["q2"], [j.question_id for j in selected_jobs])

    def test_select_by_sample_id(self):
        jobs = self._make_jobs(5)
        plans = [{"i": i} for i in range(5)]
        selected_jobs, _ = select_jobs_and_plans(
            jobs, plans, question_ids=["s3"]
        )
        self.assertEqual(["q3"], [j.question_id for j in selected_jobs])

    def test_select_by_native_question_id(self):
        jobs = self._make_jobs(5)
        plans = [{"i": i} for i in range(5)]
        selected_jobs, _ = select_jobs_and_plans(
            jobs, plans, question_ids=["native_1"]
        )
        self.assertEqual(["q1"], [j.question_id for j in selected_jobs])

    def test_select_unknown_id_raises(self):
        jobs = self._make_jobs(3)
        plans = [{"i": i} for i in range(3)]
        with self.assertRaises(ValueError) as ctx:
            select_jobs_and_plans(jobs, plans, question_ids=["q1", "unknown"])
        self.assertIn("unknown", str(ctx.exception))

    def test_select_with_limit(self):
        jobs = self._make_jobs(5)
        plans = [{"i": i} for i in range(5)]
        selected_jobs, selected_plans = select_jobs_and_plans(
            jobs, plans, limit=2
        )
        self.assertEqual(2, len(selected_jobs))
        self.assertEqual(["q0", "q1"], [j.question_id for j in selected_jobs])

    def test_select_no_filters_returns_all(self):
        jobs = self._make_jobs(3)
        plans = [{"i": i} for i in range(3)]
        selected_jobs, _ = select_jobs_and_plans(jobs, plans)
        self.assertEqual(3, len(selected_jobs))

    def test_select_random_count(self):
        jobs = self._make_jobs(10)
        plans = [{"i": i} for i in range(10)]
        selected_jobs, _ = select_jobs_and_plans(
            jobs, plans, random_count=3, random_seed=42
        )
        self.assertEqual(3, len(selected_jobs))

    def test_select_random_count_exceeds_total(self):
        jobs = self._make_jobs(3)
        plans = [{"i": i} for i in range(3)]
        selected_jobs, _ = select_jobs_and_plans(
            jobs, plans, random_count=10, random_seed=42
        )
        self.assertEqual(3, len(selected_jobs))

    def test_select_limit_zero_returns_all(self):
        jobs = self._make_jobs(3)
        plans = [{"i": i} for i in range(3)]
        selected_jobs, _ = select_jobs_and_plans(jobs, plans, limit=0)
        self.assertEqual(3, len(selected_jobs))


# ------------------------------------------------------------------ #
#  import_memory.py                                                   #
# ------------------------------------------------------------------ #

class ImportMemoryTests(unittest.TestCase):

    def test_import_fields_constant(self):
        expected = ("question_id", "session_id", "status", "messages",
                     "sessions", "elapsed_s", "error")
        self.assertEqual(expected, IMPORT_FIELDS)

    def test_fallback_batches_from_events(self):
        plan = {
            "events": [
                {"text": "hello", "time": "2024-01-01"},
                {"text": "", "time": ""},
                {"text": "world", "time": "2024-01-02"},
            ]
        }
        batches = _fallback_batches(plan)
        self.assertEqual(1, len(batches))
        self.assertEqual("default", batches[0]["session_key"])
        self.assertEqual(2, len(batches[0]["messages"]))
        self.assertEqual("hello", batches[0]["messages"][0]["content"])
        self.assertEqual("2024-01-01", batches[0]["messages"][0]["created_at"])

    def test_fallback_batches_empty_events(self):
        self.assertEqual([], _fallback_batches({"events": []}))
        self.assertEqual([], _fallback_batches({}))

    def test_import_basic(self):
        job = SimpleNamespace(question_id="q1")
        plan = {
            "session_batches": [
                {
                    "session_key": "s1",
                    "date_time": "2024-01-10",
                    "messages": [
                        {"role": "user", "content": "hello", "created_at": "2024-01-10T10:00:00", "speaker": "user"},
                        {"role": "assistant", "content": "hi", "created_at": "2024-01-10T10:00:01", "speaker": "assistant"},
                    ],
                }
            ]
        }
        client = _FakeMemoryClient()
        config = EvalConfig()
        with tempfile.TemporaryDirectory() as d:
            report = import_longmemeval_memory(
                [job], [plan], client, config, Path(d), _Log()
            )
        self.assertEqual(1, report.completed)
        self.assertEqual(1, report.total)
        self.assertEqual(0, report.incomplete)
        self.assertEqual({"q1": "session_1"}, report.question_to_session)
        self.assertEqual(2, len(client.message_calls))

    def test_import_csv_output(self):
        job = SimpleNamespace(question_id="q1")
        plan = {
            "session_batches": [
                {
                    "session_key": "s1",
                    "date_time": "",
                    "messages": [{"role": "user", "content": "hi"}],
                }
            ]
        }
        client = _FakeMemoryClient()
        config = EvalConfig()
        with tempfile.TemporaryDirectory() as d:
            report = import_longmemeval_memory(
                [job], [plan], client, config, Path(d), _Log()
            )
            csv_path = Path(d) / "import_results.csv"
            self.assertTrue(csv_path.is_file())
            with csv_path.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(1, len(rows))
        self.assertEqual("q1", rows[0]["question_id"])
        self.assertEqual("completed", rows[0]["status"])
        self.assertEqual("1", rows[0]["messages"])

    def test_import_handles_exception(self):
        job = SimpleNamespace(question_id="q1")
        plan = {"session_batches": [{"session_key": "s1", "date_time": "", "messages": [{"role": "user", "content": "hi"}]}]}
        client = _FakeMemoryClient()
        client.open_session = MagicMock(side_effect=RuntimeError("connection refused"))
        config = EvalConfig()
        with tempfile.TemporaryDirectory() as d:
            report = import_longmemeval_memory(
                [job], [plan], client, config, Path(d), _Log()
            )
        self.assertEqual(0, report.completed)
        self.assertEqual(1, report.incomplete)
        self.assertEqual("error", report.rows[0]["status"])
        self.assertIn("connection refused", report.rows[0]["error"])

    def test_import_skips_empty_content(self):
        job = SimpleNamespace(question_id="q1")
        plan = {
            "session_batches": [
                {
                    "session_key": "s1",
                    "date_time": "",
                    "messages": [
                        {"role": "user", "content": ""},
                        {"role": "user", "content": "keep"},
                        {"role": "user", "content": None},
                    ],
                }
            ]
        }
        client = _FakeMemoryClient()
        config = EvalConfig()
        with tempfile.TemporaryDirectory() as d:
            import_longmemeval_memory(
                [job], [plan], client, config, Path(d), _Log()
            )
        # Only "keep" should be added (empty string and None content skipped)
        self.assertEqual(1, len(client.message_calls))

    def test_import_uses_fallback_when_no_batches(self):
        job = SimpleNamespace(question_id="q1")
        plan = {
            "events": [
                {"text": "event text", "time": "2024-01-01"},
            ]
        }
        client = _FakeMemoryClient()
        config = EvalConfig()
        with tempfile.TemporaryDirectory() as d:
            report = import_longmemeval_memory(
                [job], [plan], client, config, Path(d), _Log()
            )
        self.assertEqual(1, report.completed)
        self.assertEqual(1, len(client.message_calls))

    def test_import_multiple_jobs(self):
        jobs = [SimpleNamespace(question_id=f"q{i}") for i in range(3)]
        plans = [
            {"session_batches": [{"session_key": f"s{i}", "date_time": "", "messages": [{"role": "user", "content": "msg"}]}]}
            for i in range(3)
        ]
        client = _FakeMemoryClient()
        config = EvalConfig()
        with tempfile.TemporaryDirectory() as d:
            report = import_longmemeval_memory(
                jobs, plans, client, config, Path(d), _Log()
            )
        self.assertEqual(3, report.completed)
        self.assertEqual(3, report.total)
        self.assertEqual(3, len(report.question_to_session))

    def test_import_report_dataclass(self):
        report = ImportReport(
            rows=[{"question_id": "q1", "status": "completed"}],
            question_to_session={"q1": "s1"},
            completed=1,
            total=1,
            incomplete=0,
        )
        self.assertEqual(1, report.completed)
        self.assertEqual(1, report.total)
        self.assertEqual(0, report.incomplete)
        self.assertEqual({"q1": "s1"}, report.question_to_session)


# ------------------------------------------------------------------ #
#  reporting.py                                                       #
# ------------------------------------------------------------------ #

class ReportingTests(unittest.TestCase):

    def _make_import_report(self, incomplete=0):
        return ImportReport(
            rows=[],
            question_to_session={},
            completed=1,
            total=1,
            incomplete=incomplete,
        )

    def _make_eval_report(self, errors=0, accuracy=0.8,
                          task_avg=0.75, abstention_acc=1.0, abstention_count=1):
        return EvaluationReport(
            rows=[],
            correct=8,
            graded=10,
            errors=errors,
            overall_accuracy=accuracy,
            task_averaged_accuracy=task_avg,
            abstention_accuracy=abstention_acc,
            abstention_count=abstention_count,
            per_type={},
        )

    def _make_qa_results(self, llm_error=0, retrieval_error=0):
        results = []
        for i in range(2):
            r = QAResult(f"q{i}", f"Q{i}", f"A{i}", f"R{i}",
                         prompt_tokens=10, completion_tokens=5, elapsed_s=1.5)
            results.append(r)
        if llm_error:
            results[0].llm_error = "timeout"
        if retrieval_error:
            results[0].retrieval_error = "empty"
        return results

    def test_build_summary_completed(self):
        summary = build_summary(
            dataset_path="data.json",
            jobs=[SimpleNamespace()],
            import_report=self._make_import_report(),
            qa_results=self._make_qa_results(),
            evaluation_report=self._make_eval_report(),
            evaluation_identity={"mode": "fresh"},
        )
        self.assertEqual("completed", summary["status"])
        self.assertEqual("longmemeval", summary["benchmark"])
        self.assertEqual("data.json", summary["dataset"])
        self.assertEqual(1, summary["total_questions"])
        self.assertEqual("injected", summary["memory_source"])
        self.assertEqual(2, summary["qa_count"])

    def test_build_summary_failed_import(self):
        summary = build_summary(
            dataset_path="d",
            jobs=[],
            import_report=self._make_import_report(incomplete=1),
            qa_results=[],
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual("failed", summary["status"])

    def test_build_summary_failed_qa_errors(self):
        summary = build_summary(
            dataset_path="d",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=self._make_qa_results(llm_error=1),
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual("failed", summary["status"])
        self.assertEqual(1, summary["qa_errors"])

    def test_build_summary_failed_retrieval_errors(self):
        summary = build_summary(
            dataset_path="d",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=self._make_qa_results(retrieval_error=1),
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual("failed", summary["status"])
        self.assertEqual(1, summary["retrieval_errors"])

    def test_build_summary_failed_judge_errors(self):
        summary = build_summary(
            dataset_path="d",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=self._make_qa_results(),
            evaluation_report=self._make_eval_report(errors=1),
            evaluation_identity={},
        )
        self.assertEqual("failed", summary["status"])
        self.assertEqual(1, summary["judge_errors"])

    def test_build_summary_accuracy_rounding(self):
        summary = build_summary(
            dataset_path="d",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=[],
            evaluation_report=self._make_eval_report(accuracy=0.123456),
            evaluation_identity={},
        )
        self.assertAlmostEqual(0.1235, summary["accuracy"])
        self.assertAlmostEqual(0.1235, summary["overall_accuracy"])

    def test_build_summary_token_totals(self):
        results = [
            QAResult("q1", "Q1", "A1", "R1", prompt_tokens=10, completion_tokens=5),
            QAResult("q2", "Q2", "A2", "R2", prompt_tokens=20, completion_tokens=10),
        ]
        summary = build_summary(
            dataset_path="d",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=results,
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual(30, summary["total_prompt_tokens"])
        self.assertEqual(15, summary["total_completion_tokens"])

    def test_build_summary_avg_qa_elapsed(self):
        results = [
            QAResult("q1", "Q1", "A1", "R1", elapsed_s=2.0),
            QAResult("q2", "Q2", "A2", "R2", elapsed_s=4.0),
        ]
        summary = build_summary(
            dataset_path="d",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=results,
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertAlmostEqual(3.0, summary["avg_qa_elapsed_s"])

    def test_build_summary_none_task_averaged(self):
        summary = build_summary(
            dataset_path="d",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=[],
            evaluation_report=self._make_eval_report(task_avg=None),
            evaluation_identity={},
        )
        self.assertIsNone(summary["task_averaged_accuracy"])

    def test_build_summary_none_abstention(self):
        summary = build_summary(
            dataset_path="d",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=[],
            evaluation_report=self._make_eval_report(abstention_acc=None, abstention_count=0),
            evaluation_identity={},
        )
        self.assertIsNone(summary["abstention_accuracy"])
        self.assertEqual(0, summary["abstention_count"])

    def test_build_summary_empty_qa_avg_elapsed(self):
        summary = build_summary(
            dataset_path="d",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=[],
            evaluation_report=self._make_eval_report(),
            evaluation_identity={"mode": "fresh", "tenant_id": "t", "user_id": "u"},
        )
        self.assertAlmostEqual(0.0, summary["avg_qa_elapsed_s"])
        self.assertEqual({"mode": "fresh", "tenant_id": "t", "user_id": "u"},
                         summary["memory_identity"])


# ------------------------------------------------------------------ #
#  recovery.py                                                        #
# ------------------------------------------------------------------ #

class RecoveryTests(unittest.TestCase):

    def _write_csv(self, path, header, rows):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)

    def test_merge_csv_files_basic(self):
        with tempfile.TemporaryDirectory() as d:
            path1 = Path(d) / "a.csv"
            path2 = Path(d) / "b.csv"
            self._write_csv(path1, ["question_id", "response"],
                            [{"question_id": "q1", "response": "a1"}])
            self._write_csv(path2, ["question_id", "response"],
                            [{"question_id": "q2", "response": "a2"}])
            dest = Path(d) / "merged.csv"
            rows = merge_csv_files([path1, path2], dest)
            self.assertEqual(2, len(rows))
            ids = [r["question_id"] for r in rows]
            self.assertEqual(["q1", "q2"], ids)

    def test_merge_csv_files_prefer_successful(self):
        with tempfile.TemporaryDirectory() as d:
            path1 = Path(d) / "a.csv"
            path2 = Path(d) / "b.csv"
            self._write_csv(path1, ["question_id", "response"],
                            [{"question_id": "q1", "response": ""}])
            self._write_csv(path2, ["question_id", "response"],
                            [{"question_id": "q1", "response": "recovered"}])
            dest = Path(d) / "merged.csv"
            rows = merge_csv_files([path1, path2], dest, prefer_successful=True)
            self.assertEqual(1, len(rows))
            self.assertEqual("recovered", rows[0]["response"])

    def test_merge_csv_files_prefer_successful_keeps_first(self):
        with tempfile.TemporaryDirectory() as d:
            path1 = Path(d) / "a.csv"
            path2 = Path(d) / "b.csv"
            self._write_csv(path1, ["question_id", "response"],
                            [{"question_id": "q1", "response": "good"}])
            self._write_csv(path2, ["question_id", "response"],
                            [{"question_id": "q1", "response": ""}])
            dest = Path(d) / "merged.csv"
            rows = merge_csv_files([path1, path2], dest, prefer_successful=True)
            self.assertEqual("good", rows[0]["response"])

    def test_merge_csv_files_missing_path_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            path1 = Path(d) / "a.csv"
            self._write_csv(path1, ["question_id", "response"],
                            [{"question_id": "q1", "response": "a1"}])
            dest = Path(d) / "merged.csv"
            rows = merge_csv_files([path1, Path(d) / "nonexistent.csv"], dest)
            self.assertEqual(1, len(rows))

    def test_merge_csv_files_anonymous_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path1 = Path(d) / "a.csv"
            self._write_csv(path1, ["question_id", "response"],
                            [{"question_id": "q1", "response": "a1"}])
            path2 = Path(d) / "b.csv"
            self._write_csv(path2, ["response"],
                            [{"response": "no_id"}])
            dest = Path(d) / "merged.csv"
            rows = merge_csv_files([path1, path2], dest)
            self.assertEqual(2, len(rows))
            self.assertEqual("q1", rows[0]["question_id"])
            self.assertEqual("no_id", rows[1]["response"])

    def test_merge_csv_files_prefer_successful_false(self):
        with tempfile.TemporaryDirectory() as d:
            path1 = Path(d) / "a.csv"
            path2 = Path(d) / "b.csv"
            self._write_csv(path1, ["question_id", "response"],
                            [{"question_id": "q1", "response": ""}])
            self._write_csv(path2, ["question_id", "response"],
                            [{"question_id": "q1", "response": "recovered"}])
            dest = Path(d) / "merged.csv"
            rows = merge_csv_files([path1, path2], dest, prefer_successful=False)
            self.assertEqual("", rows[0]["response"])

    def test_merge_shard_artifacts_missing_summary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run_one = root / "one"
            run_two = root / "two"
            output = root / "merged"
            for run in (run_one, run_two):
                run.mkdir()
                # No summary.json in either shard
                (run / "import_results.csv").write_text("question_id,status\n", encoding="utf-8")
            (run_one / "qa_results.csv").write_text(
                "question_id,response,llm_error,retrieval_error,prompt_tokens,completion_tokens\n"
                "q1,answer,,,10,2\n", encoding="utf-8")
            (run_one / "eval_results.csv").write_text(
                "question_id,correct,judge_error\nq1,True,\n", encoding="utf-8")
            (run_two / "qa_results.csv").write_text(
                "question_id,response,llm_error,retrieval_error,prompt_tokens,completion_tokens\n"
                "q2,answer,,,8,1\n", encoding="utf-8")
            (run_two / "eval_results.csv").write_text(
                "question_id,correct,judge_error\nq2,True,\n", encoding="utf-8")
            summary = merge_shard_artifacts([run_one, run_two], output)
            # No summaries at all -> "failed"
            self.assertEqual("failed", summary["status"])
            self.assertEqual(0, summary["shard_summaries"])

    def test_merge_shard_artifacts_corrupt_summary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run_one = root / "one"
            output = root / "merged"
            run_one.mkdir()
            (run_one / "summary.json").write_text("{invalid json", encoding="utf-8")
            (run_one / "import_results.csv").write_text("question_id,status\n", encoding="utf-8")
            (run_one / "qa_results.csv").write_text(
                "question_id,response,llm_error,retrieval_error,prompt_tokens,completion_tokens\n"
                "q1,answer,,,10,2\n", encoding="utf-8")
            (run_one / "eval_results.csv").write_text(
                "question_id,correct,judge_error\nq1,True,\n", encoding="utf-8")
            summary = merge_shard_artifacts([run_one], output)
            self.assertEqual("failed", summary["status"])

    def test_merge_shard_artifacts_qa_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run_one = root / "one"
            output = root / "merged"
            run_one.mkdir()
            (run_one / "summary.json").write_text('{"status":"completed"}', encoding="utf-8")
            (run_one / "import_results.csv").write_text("question_id,status\n", encoding="utf-8")
            (run_one / "qa_results.csv").write_text(
                "question_id,response,llm_error,retrieval_error,prompt_tokens,completion_tokens\n"
                "q1,,timeout,,10,2\n", encoding="utf-8")
            (run_one / "eval_results.csv").write_text(
                "question_id,correct,judge_error\nq1,True,\n", encoding="utf-8")
            summary = merge_shard_artifacts([run_one], output)
            self.assertEqual("failed", summary["status"])
            self.assertEqual(1, summary["qa_errors"])

    def test_recovery_build_parser(self):
        parser = build_recovery_parser()
        # Missing required args should raise
        with self.assertRaises(SystemExit):
            parser.parse_args([])
        args = parser.parse_args([
            "--qa", "qa.csv", "--dataset", "data.json", "--out-dir", "out",
        ])
        self.assertEqual("qa.csv", args.qa)
        self.assertEqual("data.json", args.dataset)
        self.assertEqual("out", args.out_dir)
        self.assertEqual("all", args.sample)
        self.assertEqual("failed-or-missing", args.mode)
        self.assertEqual("", args.retry_qa)
        self.assertEqual("", args.output)

    def test_recovery_main_without_retry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            qa_path = root / "qa_results.csv"
            self._write_csv(qa_path, ["question_id", "response"],
                            [{"question_id": "q1", "response": "answer1"}])
            dataset_path = _write_dataset(d, [
                _sample_item(question_id="q1"),
                _sample_item(question_id="q2"),
            ])
            out_dir = root / "recovery_out"
            with patch.object(sys, "argv", [
                "recovery", "--qa", str(qa_path),
                "--dataset", str(dataset_path),
                "--out-dir", str(out_dir),
            ]):
                recovery_main()
            summary_path = out_dir / "recovery_summary.json"
            self.assertTrue(summary_path.is_file())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual("failed-or-missing", summary["mode"])
            self.assertEqual(2, summary["expected_questions"])
            self.assertEqual(1, summary["observed_questions"])
            self.assertIn("q2", summary["recovery_question_ids"])

    def test_recovery_main_with_retry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            qa_path = root / "qa_results.csv"
            self._write_csv(qa_path, ["question_id", "response"],
                            [{"question_id": "q1", "response": "answer1"},
                             {"question_id": "q2", "response": ""}])
            dataset_path = _write_dataset(d, [
                _sample_item(question_id="q1"),
                _sample_item(question_id="q2"),
            ])
            retry_path = root / "retry.csv"
            self._write_csv(retry_path, ["question_id", "response"],
                            [{"question_id": "q2", "response": "recovered"}])
            out_dir = root / "recovery_out"
            with patch.object(sys, "argv", [
                "recovery", "--qa", str(qa_path),
                "--dataset", str(dataset_path),
                "--out-dir", str(out_dir),
                "--retry-qa", str(retry_path),
            ]):
                recovery_main()
            summary_path = out_dir / "recovery_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(1, summary["merge"]["recovered"])
            recovered_path = Path(summary["output"])
            self.assertTrue(recovered_path.is_file())
            with recovered_path.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            q2_row = [r for r in rows if r["question_id"] == "q2"][0]
            self.assertEqual("recovered", q2_row["response"])


# ------------------------------------------------------------------ #
#  parallel.py                                                        #
# ------------------------------------------------------------------ #

class ParallelTests(unittest.TestCase):

    def test_partition_more_shards_than_ids(self):
        shards = partition_question_ids(["q1", "q2", "q3"], 5)
        self.assertEqual([["q1"], ["q2"], ["q3"]], shards)

    def test_partition_empty_list(self):
        shards = partition_question_ids([], 2)
        self.assertEqual([], shards)

    def test_partition_single_shard(self):
        shards = partition_question_ids(["q1", "q2", "q3"], 1)
        self.assertEqual([["q1", "q2", "q3"]], shards)

    def test_partition_zero_shards(self):
        shards = partition_question_ids(["q1", "q2"], 0)
        self.assertEqual([["q1", "q2"]], shards)

    def test_partition_negative_shards(self):
        shards = partition_question_ids(["q1", "q2"], -1)
        self.assertEqual([["q1", "q2"]], shards)

    def test_clean_forwarded_args_removes_value_options(self):
        for opt in ("--out-dir", "--question-ids", "--questions",
                     "--parallel-shards", "--parallel-workers"):
            with self.subTest(opt=opt):
                result = _clean_forwarded_args([opt, "value", "--keep"])
                self.assertEqual(["--keep"], result)

    def test_clean_forwarded_args_removes_flag_options(self):
        result = _clean_forwarded_args(["--parallel-dry-run", "--keep"])
        self.assertEqual(["--keep"], result)

    def test_clean_forwarded_args_preserves_other_args(self):
        argv = ["--dataset", "data.json", "--sample", "all", "--llm-model", "gpt"]
        result = _clean_forwarded_args(argv)
        self.assertEqual(argv, result)

    def test_clean_forwarded_args_mixed(self):
        argv = [
            "--dataset", "data.json",
            "--out-dir", "old",
            "--parallel-dry-run",
            "--questions", "5",
            "--sample", "all",
        ]
        result = _clean_forwarded_args(argv)
        self.assertEqual(["--dataset", "data.json", "--sample", "all"], result)

    def test_build_shard_commands_structure(self):
        with tempfile.TemporaryDirectory() as d:
            shards = [["q1", "q3"], ["q2"]]
            commands = build_shard_commands(
                ["--dataset", "data.json"], shards, Path(d)
            )
            self.assertEqual(2, len(commands))
            cmd1 = commands[0]
            self.assertEqual(1, cmd1["index"])
            self.assertEqual(["q1", "q3"], cmd1["question_ids"])
            self.assertIn("--question-ids", cmd1["command"])
            self.assertIn("q1,q3", cmd1["command"])
            self.assertIn("--parallel-shards", cmd1["command"])
            self.assertIn("1", cmd1["command"])
            self.assertIn("--out-dir", cmd1["command"])
            # The cleaned base args should be present
            self.assertIn("--dataset", cmd1["command"])
            self.assertIn("data.json", cmd1["command"])

    def test_build_shard_commands_strips_old_options(self):
        with tempfile.TemporaryDirectory() as d:
            shards = [["q1"]]
            commands = build_shard_commands(
                ["--dataset", "data.json", "--out-dir", "old", "--questions", "3"],
                shards, Path(d),
            )
            cmd = commands[0]["command"]
            self.assertNotIn("old", cmd)
            self.assertNotIn("3", cmd)

    def test_run_parallel_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            output_dir = Path(d) / "parallel"
            summary = run_parallel(
                argv=["--dataset", "data.json"],
                question_ids=["q1", "q2"],
                output_dir=output_dir,
                shard_count=2,
                worker_count=2,
                dry_run=True,
            )
            self.assertTrue(summary["dry_run"])
            self.assertEqual(2, summary["shards"])
            self.assertEqual(2, summary["workers"])
            self.assertEqual(2, len(summary["commands"]))
            manifest_path = output_dir / "parallel_manifest.json"
            self.assertTrue(manifest_path.is_file())

    @patch("benchmarks.longmemeval.parallel.merge_shard_artifacts")
    @patch("benchmarks.longmemeval.parallel._run_shard")
    def test_run_parallel_executes_success(self, mock_run, mock_merge):
        mock_run.side_effect = lambda spec: {
            **spec,
            "returncode": 0,
            "log_path": "log",
            "result_dir": str(Path(tempfile.mkdtemp())),
        }
        mock_merge.return_value = {"status": "completed", "overall_accuracy": 1.0}
        with tempfile.TemporaryDirectory() as d:
            output_dir = Path(d) / "parallel"
            summary = run_parallel(
                argv=["--dataset", "data.json"],
                question_ids=["q1", "q2"],
                output_dir=output_dir,
                shard_count=2,
                worker_count=2,
                dry_run=False,
            )
            self.assertEqual("completed", summary["status"])
            self.assertEqual(2, summary["shards"])
            self.assertEqual(0, len(summary["failures"]))
            self.assertTrue((output_dir / "parallel_summary.json").is_file())

    @patch("benchmarks.longmemeval.parallel.merge_shard_artifacts")
    @patch("benchmarks.longmemeval.parallel._run_shard")
    def test_run_parallel_with_failure(self, mock_run, mock_merge):
        def fake_shard(spec):
            return {
                **spec,
                "returncode": 1 if spec["index"] == 1 else 0,
                "log_path": "log",
                "result_dir": str(Path(tempfile.mkdtemp())),
            }
        mock_run.side_effect = fake_shard
        mock_merge.return_value = {"status": "completed"}
        with tempfile.TemporaryDirectory() as d:
            output_dir = Path(d) / "parallel_fail"
            summary = run_parallel(
                argv=["--dataset", "data.json"],
                question_ids=["q1", "q2"],
                output_dir=output_dir,
                shard_count=2,
                worker_count=2,
                dry_run=False,
            )
            self.assertEqual("failed", summary["status"])
            self.assertEqual(1, len(summary["failures"]))


# ------------------------------------------------------------------ #
#  run_eval.py                                                        #
# ------------------------------------------------------------------ #

class RunEvalTests(unittest.TestCase):

    def test_build_parser_has_arguments(self):
        with patch.object(sys, "argv", ["test"]):
            parser = build_eval_parser()
            args = parser.parse_args([
                "--dataset", "data.json",
                "--llm-base-url", "http://api",
                "--llm-api-key", "key",
            ])
            self.assertEqual("data.json", args.dataset)
            self.assertEqual("all", args.sample)
            self.assertEqual(0, args.questions)
            self.assertEqual("", args.question_ids)
            self.assertEqual(0, args.random_count)
            self.assertEqual(30, args.random_seed)
            self.assertEqual(1, args.parallel_shards)
            self.assertEqual(2, args.parallel_workers)
            self.assertFalse(args.parallel_dry_run)
            self.assertEqual("", args.judge_model)
            self.assertEqual("", args.judge_api_key)
            self.assertEqual("", args.judge_base_url)

    def test_build_parser_judge_env_defaults(self):
        with patch.dict("os.environ", {
            "JUDGE_MODEL": "gpt-4",
            "JUDGE_TOKEN": "tok",
            "JUDGE_BASE_URL": "http://judge",
        }):
            with patch.object(sys, "argv", ["test"]):
                parser = build_eval_parser()
                args = parser.parse_args([
                    "--llm-base-url", "u", "--llm-api-key", "k",
                ])
                self.assertEqual("gpt-4", args.judge_model)
                self.assertEqual("tok", args.judge_api_key)
                self.assertEqual("http://judge", args.judge_base_url)


if __name__ == "__main__":
    unittest.main()
