from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.locomo.judge import (
    judge_locomo_results,
    judge_with_metrics,
    judge_with_retries,
    parse_judge_json,
)
from benchmarks.longmemeval.judge import parse_yes_no
from shared.qa import QAResult


class _Log:
    def info(self, *_args):
        return None

    def error(self, *_args):
        return None


class JudgeParserTests(unittest.TestCase):
    def test_rejects_malformed_locomo_verdict(self) -> None:
        with self.assertRaisesRegex(ValueError, "no JSON"):
            parse_judge_json("probably correct")

    def test_recovers_verdict_from_truncated_json(self) -> None:
        verdict, reasoning = parse_judge_json(
            '{"is_correct": "WRONG", "reasoning": "cut off'
        )
        self.assertEqual("WRONG", verdict)
        self.assertEqual("", reasoning)

    def test_recovers_verdict_from_prose(self) -> None:
        verdict, reasoning = parse_judge_json(
            "The answer is CORRECT because it matches the gold."
        )
        self.assertEqual("CORRECT", verdict)
        self.assertEqual("", reasoning)

    def test_prefers_json_verdict_over_prose(self) -> None:
        verdict, reasoning = parse_judge_json(
            '{"is_correct": "WRONG", "reasoning": "off topic"} '
            "trailing CORRECT prose"
        )
        self.assertEqual("WRONG", verdict)
        self.assertEqual("off topic", reasoning)

    def test_rejects_ambiguous_longmemeval_verdict(self) -> None:
        with self.assertRaisesRegex(ValueError, "not unambiguous"):
            parse_yes_no("yes and no")

    def test_accepts_unambiguous_yes_and_no(self) -> None:
        self.assertTrue(parse_yes_no("Yes."))
        self.assertFalse(parse_yes_no("No."))

    def test_locomo_judge_retries_malformed_model_output(self) -> None:
        class FakeJudge:
            def __init__(self) -> None:
                self.responses = iter([
                    "",
                    '{"is_correct": "MAYBE"}',
                    '{"is_correct": "CORRECT", "reasoning": "matches"}',
                ])

            def judge(self, _system: str, _prompt: str) -> str:
                return next(self.responses)

        with patch("benchmarks.locomo.judge.time.sleep"):
            verdict, reasoning = judge_with_retries(
                FakeJudge(),
                "question",
                "answer",
                "response",
            )

        self.assertEqual("CORRECT", verdict)
        self.assertEqual("matches", reasoning)


class JudgeRepairRetryTests(unittest.TestCase):
    def test_retries_with_repair_prompt_and_higher_temperature(self) -> None:
        calls: list[tuple[list, object]] = []

        class FakeChat:
            def chat(
                self,
                messages,
                *,
                temperature=None,
                response_format=False,
                thinking_disabled=False,
                omit_max_tokens=False,
            ):
                calls.append((
                    messages,
                    temperature,
                    response_format,
                    thinking_disabled,
                    omit_max_tokens,
                ))
                if len(calls) == 1:
                    return SimpleNamespace(
                        content="",
                        prompt_tokens=1,
                        completion_tokens=0,
                        elapsed_s=0.1,
                        error="",
                        retry_count=0,
                        usage_observed=False,
                    )
                return SimpleNamespace(
                    content='{"is_correct": "CORRECT", "reasoning": "ok"}',
                    prompt_tokens=1,
                    completion_tokens=1,
                    elapsed_s=0.1,
                    error="",
                    retry_count=0,
                    usage_observed=True,
                )

        with patch("benchmarks.locomo.judge.time.sleep"):
            verdict, reasoning, metrics = judge_with_metrics(
                FakeChat(),
                "question",
                "answer",
                "response",
            )

        self.assertEqual("CORRECT", verdict)
        self.assertEqual(2, len(calls))
        # First attempt uses the client default; the retry raises temperature
        # and appends a corrective instruction instead of repeating the
        # identical deterministic request.
        self.assertIsNone(calls[0][1])
        self.assertEqual(0.3, calls[1][1])
        self.assertIn("empty or not valid JSON", calls[1][0][1]["content"])
        self.assertEqual(1, metrics["retry_count"])


class JudgeCheckpointTests(unittest.TestCase):
    def test_writes_checkpoint_in_original_question_order(self) -> None:
        class FakeJudge:
            def judge(self, _system: str, prompt: str) -> str:
                verdict = "WRONG" if "generated-q1" in prompt else "CORRECT"
                return (
                    '{"is_correct": "'
                    + verdict
                    + '", "reasoning": "checked"}'
                )

        qa_results = [
            QAResult("q1", "first", "gold-1", "generated-q1"),
            QAResult("q2", "second", "gold-2", "generated-q2"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            report = judge_locomo_results(
                qa_results,
                FakeJudge(),
                Path(directory),
                _Log(),
                concurrency=2,
                checkpoint_interval=1,
            )

            with (
                Path(directory) / "judge_results.checkpoint.csv"
            ).open(encoding="utf-8", newline="") as handle:
                checkpoint_rows = list(csv.DictReader(handle))

        self.assertEqual(["q1", "q2"], [
            row["question_id"] for row in checkpoint_rows
        ])
        self.assertEqual(["WRONG", "CORRECT"], [
            row["verdict"] for row in report.rows
        ])

    def test_reuses_only_exactly_matching_existing_judge_rows(self) -> None:
        class FakeJudge:
            calls = 0

            def judge(self, _system: str, _prompt: str) -> str:
                self.calls += 1
                return (
                    '{"is_correct": "CORRECT", '
                    '"reasoning": "new judgement"}'
                )

        judge = FakeJudge()
        qa_results = [
            QAResult("q1", "first", "gold-1", "same-response"),
            QAResult("q2", "second", "gold-2", "new-response"),
        ]
        existing = [
            {
                "question_id": "q1",
                "question": "first",
                "answer": "gold-1",
                "response": "same-response",
                "verdict": "WRONG",
                "reasoning": "existing judgement",
                "judge_error": "",
                "judge_prompt_tokens": "1",
                "judge_completion_tokens": "1",
                "judge_total_tokens": "2",
                "judge_retry_count": "0",
                "judge_latency_ms": "1.0",
            },
            {
                "question_id": "q2",
                "question": "second",
                "answer": "gold-2",
                "response": "old-response",
                "verdict": "WRONG",
                "reasoning": "stale judgement",
                "judge_error": "",
                "judge_prompt_tokens": "1",
                "judge_completion_tokens": "1",
                "judge_total_tokens": "2",
                "judge_retry_count": "0",
                "judge_latency_ms": "1.0",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            report = judge_locomo_results(
                qa_results,
                judge,
                Path(directory),
                _Log(),
                concurrency=1,
                checkpoint_interval=1,
                existing_rows=existing,
            )

        self.assertEqual(1, judge.calls)
        self.assertEqual("WRONG", report.rows[0]["verdict"])
        self.assertEqual(
            "existing judgement",
            report.rows[0]["reasoning"],
        )
        self.assertEqual("CORRECT", report.rows[1]["verdict"])


if __name__ == "__main__":
    unittest.main()
