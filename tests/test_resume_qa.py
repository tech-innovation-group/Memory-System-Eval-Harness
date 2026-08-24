"""Unit tests for shared/resume_qa.py resume helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shared.qa import QAResult
from shared.resume_qa import (
    find_qa_resume_csv,
    is_healthy_qa_result,
    load_prior_import_rows,
    load_resume_qa_results,
    manifest_differences,
    parse_qa_result_from_row,
    resolve_resume_csv,
)


class ParseQaResultFromRowTests(unittest.TestCase):
    def test_round_trips_token_and_latency_columns(self):
        row = {
            "question_id": "q1",
            "sample_id": "sample",
            "category": "2",
            "question": "When?",
            "answer": "Yesterday",
            "response": "Yesterday",
            "elapsed_s": "1.25",
            "retrieval_latency_ms": "904.1",
            "llm_total_ms": "1200.5",
            "prompt_tokens": "1652",
            "completion_tokens": "77",
            "answer_total_tokens": "1729",
            "tool_call_count": "3",
            "iterations": "2",
            "retrieval_items_json": json.dumps([
                {"text": "x", "score": 0.9},
            ]),
            "retrieval_status": "ok",
            "answer_status": "ok",
            "model_status": "ok",
            "health_status": "ok",
        }
        result = parse_qa_result_from_row(row)
        self.assertEqual("q1", result.question_id)
        self.assertEqual(1.25, result.elapsed_s)
        self.assertAlmostEqual(0.9041, result.retrieval_latency_s, places=4)
        self.assertAlmostEqual(1.2005, result.llm_latency_s, places=4)
        self.assertEqual(1652, result.prompt_tokens)
        self.assertEqual(77, result.completion_tokens)
        self.assertEqual(3, result.tool_call_count)
        self.assertEqual(2, result.iterations)
        self.assertTrue(result.model_usage_observed)
        self.assertEqual(1, len(result.retrieval_items))
        self.assertEqual("ok", result.health_status)

    def test_empty_values_default(self):
        result = parse_qa_result_from_row({
            "question_id": "q1", "question": "Q", "answer": "A", "response": "R",
        })
        self.assertEqual(0.0, result.elapsed_s)
        self.assertEqual(0, result.prompt_tokens)
        self.assertFalse(result.model_usage_observed)


class HealthyResultTests(unittest.TestCase):
    def test_healthy(self):
        result = QAResult(question_id="q1", question="Q", answer="A", response="R")
        self.assertTrue(is_healthy_qa_result(result))

    def test_unhealthy_when_errors(self):
        result = QAResult(
            question_id="q1", question="Q", answer="A", response="R",
            llm_error="boom",
        )
        self.assertFalse(is_healthy_qa_result(result))

    def test_unhealthy_when_empty_response(self):
        result = QAResult(question_id="q1", question="Q", answer="A", response="")
        self.assertFalse(is_healthy_qa_result(result))


class ResolveResumeCsvTests(unittest.TestCase):
    def test_prefers_final_over_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "qa_results.csv").write_text("question_id\nq1\n", encoding="utf-8")
            (root / "qa_results.checkpoint.csv").write_text("question_id\n", encoding="utf-8")
            self.assertEqual(root / "qa_results.csv", resolve_resume_csv(root))

    def test_falls_back_to_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "qa_results.checkpoint.csv").write_text("question_id\n", encoding="utf-8")
            self.assertEqual(root / "qa_results.checkpoint.csv", resolve_resume_csv(root))

    def test_missing_source_raises(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "nope"
            with self.assertRaises(ValueError):
                resolve_resume_csv(root)

    def test_find_qa_resume_csv_none_when_import_only(self):
        # 导入中断的目录只有 import_results.csv，没有 QA 结果
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "import_results.csv").write_text("question_id\n", encoding="utf-8")
            self.assertIsNone(find_qa_resume_csv(root))

    def test_find_qa_resume_csv_returns_csv_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "qa_results.csv").write_text("question_id\nq1\n", encoding="utf-8")
            self.assertEqual(root / "qa_results.csv", find_qa_resume_csv(root))

    def test_find_qa_resume_csv_falls_back_to_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "qa_results.checkpoint.csv").write_text("question_id\n", encoding="utf-8")
            self.assertEqual(root / "qa_results.checkpoint.csv", find_qa_resume_csv(root))


class LoadResumeQaResultsTests(unittest.TestCase):
    def test_filters_healthy_and_mismatched(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "qa_results.csv").write_text(
                "\n".join([
                    "question_id,sample_id,category,question,answer,response,llm_error",
                    "q1,s,,Q1,A1,R1,",
                    "q2,s,,Q2,A2,,boom",      # empty response -> discarded
                    "q3,s,,Q3,A3,R3,",         # not in tasks -> skipped
                    "q4,s,,OLD,A4,R4,",        # question mismatch -> discarded
                ]),
                encoding="utf-8",
            )
            tasks = [
                {"question_id": "q1", "sample_id": "s", "question": "Q1", "answer": "A1"},
                {"question_id": "q2", "sample_id": "s", "question": "Q2", "answer": "A2"},
                {"question_id": "q4", "sample_id": "s", "question": "Q4", "answer": "A4"},
            ]
            state = load_resume_qa_results(root, tasks)
            self.assertEqual(["q1"], [r.question_id for r in state.results])
            self.assertEqual(sorted(["q2", "q4"]), sorted(state.discarded_question_ids))


class LoadPriorImportRowsTests(unittest.TestCase):
    def test_loads_from_directory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "import_results.csv").write_text(
                "question_id,session_id,status\nq1,s1,completed\n",
                encoding="utf-8",
            )
            rows = load_prior_import_rows(root)
            self.assertEqual(1, len(rows))
            self.assertEqual("completed", rows[0]["status"])

    def test_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual([], load_prior_import_rows(Path(d)))


class ManifestDifferencesTests(unittest.TestCase):
    def test_reports_missing_and_changed(self):
        expected = {"dataset_path": "/a.json", "qa": {"top_k": 25}}
        actual = {"dataset_path": "/b.json", "qa": {"top_k": 10}}
        diffs = manifest_differences(expected, actual)
        self.assertEqual(2, len(diffs))

    def test_matching_no_differences(self):
        expected = {"dataset_path": "/a.json", "qa": {"top_k": 25}}
        self.assertEqual([], manifest_differences(expected, dict(expected)))


if __name__ == "__main__":
    unittest.main()
