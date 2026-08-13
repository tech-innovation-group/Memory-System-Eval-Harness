from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backends.memory_types import CommitResult
from benchmarks.hotpotqa.evaluate import (
    answer_metrics,
    evaluate_hotpotqa,
    predict_supporting_facts,
)
from benchmarks.hotpotqa.import_memory import import_hotpotqa_memory
from benchmarks.hotpotqa.qa import run_hotpotqa_qa
from benchmarks.hotpotqa.recovery import (
    merge_recovered_rows,
    recovery_question_ids,
)
from benchmarks.hotpotqa.selection import select_jobs_and_plans
from shared.eval_base import EvalConfig
from shared.qa import QAResult


class _Log:
    def info(self, *_args):
        return None

    def error(self, *_args):
        return None


class _RecordingClient:
    def __init__(self):
        self.opened = []
        self.messages = []

    def open_session(self, title=""):
        self.opened.append(title)
        return "shared-session"

    def add_message(self, session_id, role, text, created_at=""):
        self.messages.append((session_id, role, text, created_at))

    def commit_session(self, session_id):
        return f"archive-{session_id}"

    def poll_commit(self, session_id, archive_id, **_kwargs):
        return CommitResult(
            session_id=session_id,
            archive_id=archive_id,
            status="completed",
            elapsed_s=0.25,
            polls=1,
        )


class HotpotQAWorkflowTests(unittest.TestCase):
    def test_global_mode_maps_every_question_to_shared_session(self):
        jobs = [
            SimpleNamespace(question_id="q1"),
            SimpleNamespace(question_id="q2"),
        ]
        plans = [
            {"events": [{"text": "fact one"}]},
            {"events": [{"text": "fact two"}]},
        ]
        client = _RecordingClient()

        with tempfile.TemporaryDirectory() as directory:
            report = import_hotpotqa_memory(
                jobs,
                plans,
                client,
                EvalConfig(),
                Path(directory),
                _Log(),
                import_mode="global",
            )

            self.assertEqual(["hotpotqa_global"], client.opened)
            self.assertEqual(2, len(client.messages))
            self.assertEqual(
                {"q1": "shared-session", "q2": "shared-session"},
                report.question_to_session,
            )
            self.assertEqual(1, report.completed)

    def test_resume_per_question_skips_completed_imports(self):
        jobs = [
            SimpleNamespace(question_id="q1"),
            SimpleNamespace(question_id="q2"),
        ]
        plans = [
            {"events": [{"text": "fact one"}]},
            {"events": [{"text": "fact two"}]},
        ]
        client = _RecordingClient()
        prior_rows = [
            {"question_id": "q1", "session_id": "prior-session-1", "status": "completed"},
        ]

        with tempfile.TemporaryDirectory() as directory:
            report = import_hotpotqa_memory(
                jobs,
                plans,
                client,
                EvalConfig(),
                Path(directory),
                _Log(),
                import_mode="per_question",
                prior_import_rows=prior_rows,
            )

            # q1 reused without opening/injecting; only q2 injected.
            self.assertEqual(["hotpotqa_q2"], client.opened)
            self.assertEqual({"q1": "prior-session-1", "q2": "shared-session"}, report.question_to_session)
            reused = [r for r in report.rows if r["status"] == "reused"]
            self.assertEqual(["q1"], [r["question_id"] for r in reused])
            self.assertEqual(2, report.completed)

    def test_resume_global_reuses_shared_session(self):
        jobs = [
            SimpleNamespace(question_id="q1"),
            SimpleNamespace(question_id="q2"),
        ]
        plans = [
            {"events": [{"text": "fact one"}]},
            {"events": [{"text": "fact two"}]},
        ]
        client = _RecordingClient()
        prior_rows = [
            {"question_id": "global", "session_id": "prior-global", "status": "completed"},
        ]

        with tempfile.TemporaryDirectory() as directory:
            report = import_hotpotqa_memory(
                jobs,
                plans,
                client,
                EvalConfig(),
                Path(directory),
                _Log(),
                import_mode="global",
                prior_import_rows=prior_rows,
            )

            self.assertEqual([], client.opened)
            self.assertEqual(
                {"q1": "prior-global", "q2": "prior-global"},
                report.question_to_session,
            )

    def test_qa_resume_merges_existing_and_only_runs_pending(self):
        from plugins.base import AgentResponse

        class FakePlugin:
            received_ids: list[str] = []

            def send_message(self, session_id, message, context_path="/", *, extra=None):
                qid = str((extra or {}).get("question_id", ""))
                self.received_ids.append(qid)
                return AgentResponse(text="answer")

        existing = QAResult(
            question_id="q1", question="Q1", answer="A1", response="R1",
            prompt_tokens=500, completion_tokens=200,
        )
        tasks = [
            {"question_id": "q1", "question": "Q1", "answer": "A1", "sample_id": "s"},
            {"question_id": "q2", "question": "Q2", "answer": "A2", "sample_id": "s"},
        ]
        plugin = FakePlugin()

        with tempfile.TemporaryDirectory() as directory:
            results = run_hotpotqa_qa(
                tasks,
                plugin,
                EvalConfig(concurrency=1),
                Path(directory),
                _Log(),
                existing_results=[existing],
            )

            self.assertEqual(["q2"], plugin.received_ids)
            self.assertEqual(["q1", "q2"], [r.question_id for r in results])
            # reused row keeps its tokens so summary accumulates
            self.assertEqual(500, results[0].prompt_tokens)

    def test_answer_metrics_match_hotpot_normalization(self):
        metrics = answer_metrics("The Eiffel Tower.", "Eiffel Tower")

        self.assertEqual(1.0, metrics["em"])
        self.assertEqual(1.0, metrics["f1"])
        self.assertEqual(0.0, answer_metrics("yes", "no")["f1"])

    def test_supporting_fact_and_joint_metrics_use_retrieval_content(self):
        result = QAResult(
            question_id="q1",
            question="Where is the tower?",
            answer="Paris",
            response="Paris",
            retrieval_items=[{
                "uri": "memory://tower",
                "score": 1.0,
                "content": (
                    "title: Eiffel Tower\n"
                    "The Eiffel Tower is in Paris."
                ),
                "type": "memory",
            }],
        )
        references = {
            "q1": {
                "_id": "q1",
                "context": [[
                    "Eiffel Tower",
                    [
                        "The Eiffel Tower is in Paris.",
                        "It opened in 1889.",
                    ],
                ]],
                "supporting_facts": [["Eiffel Tower", 0]],
            }
        }

        with tempfile.TemporaryDirectory() as directory:
            report = evaluate_hotpotqa(
                [result],
                references,
                Path(directory),
            )

            self.assertEqual(1.0, report.answer_em)
            self.assertEqual(1.0, report.supporting_facts_em)
            self.assertEqual(1.0, report.supporting_facts_f1)
            self.assertEqual(1.0, report.joint_f1)

    def test_explicit_supporting_fact_metadata_is_preserved(self):
        predicted = predict_supporting_facts(
            [{
                "content": "opaque evidence",
                "hotpotqa_title": "Doc",
                "hotpotqa_sent_id": 1,
            }],
            {
                "supporting_facts": [["Doc", 1]],
                "context": [["Doc", ["zero", "one"]]],
            },
        )

        self.assertEqual({("Doc", 1)}, predicted)

    def test_explicit_selection_keeps_dataset_order(self):
        jobs = [
            SimpleNamespace(
                question_id="q1",
                native_question_id="native-1",
                sample_id="sample-1",
            ),
            SimpleNamespace(
                question_id="q2",
                native_question_id="native-2",
                sample_id="sample-2",
            ),
        ]
        plans = [{"id": 1}, {"id": 2}]

        selected_jobs, selected_plans = select_jobs_and_plans(
            jobs,
            plans,
            question_ids=["native-2", "q1"],
        )

        self.assertEqual(["q1", "q2"], [job.question_id for job in selected_jobs])
        self.assertEqual([1, 2], [plan["id"] for plan in selected_plans])

    def test_recovery_selects_failed_and_missing_questions(self):
        rows = [
            {"question_id": "q1", "response": "answer"},
            {"question_id": "q2", "response": "", "llm_error": "timeout"},
        ]

        self.assertEqual(
            ["q2", "q3"],
            recovery_question_ids(
                "failed-or-missing",
                rows,
                ["q1", "q2", "q3"],
            ),
        )

    def test_recovery_merge_keeps_only_successful_retries(self):
        merged, stats = merge_recovered_rows(
            [
                {"question_id": "q1", "response": "old"},
                {"question_id": "q2", "response": "", "llm_error": "timeout"},
            ],
            [
                {"question_id": "q2", "response": "recovered"},
                {
                    "question_id": "q3",
                    "response": "",
                    "retrieval_error": "empty",
                },
            ],
        )

        self.assertEqual("recovered", merged[1]["response"])
        self.assertEqual(1, stats["recovered"])
        self.assertEqual(["q3"], stats["retry_failures"])


if __name__ == "__main__":
    unittest.main()
