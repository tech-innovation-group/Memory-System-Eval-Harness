from __future__ import annotations

import hashlib
import tempfile
import unittest
import csv
import json
from pathlib import Path
from types import SimpleNamespace

from benchmarks.locomo.diagnosis import build_diagnosis
from benchmarks.locomo.evaluate import load_qa_results
from benchmarks.locomo.import_memory import (
    ImportOptions,
    import_locomo_memory,
    resolve_session_mode,
)
from benchmarks.locomo.retry import (
    merge_retry_rows,
    qa_row_failed,
    retry_question_ids,
)
from benchmarks.locomo.selection import parse_question_ids, select_questions
from benchmarks.locomo.stats import summarize_judge_rows
from benchmarks.locomo.profiles import VIKINGBOAT_0411_PROFILE
from benchmarks.locomo.qa import (
    QA_FIELDS,
    QAOptions,
    run_locomo_qa,
)
from benchmarks.locomo.resume import (
    build_judge_resume_manifest,
    build_qa_resume_manifest,
    load_judge_resume_state,
    load_qa_resume_state,
    write_judge_resume_manifest,
    write_qa_resume_manifest,
)
from benchmarks.locomo.run_eval import (
    EpisodePreparationError,
    episode_recall_enabled,
    prepare_episode_recall,
    load_qa_prompt_append,
)
from shared.eval_base import EvalConfig
from shared.qa import QAResult
from plugins.base import AgentResponse


class LocomoCliDefaultsTests(unittest.TestCase):
    def test_local_prompt_file_is_hashed_without_embedding_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.txt"
            path.write_text("  local prompt experiment  \n", encoding="utf-8")

            prompt, digest, source = load_qa_prompt_append(str(path))

        self.assertEqual("local prompt experiment", prompt)
        self.assertEqual(
            hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            digest,
        )
        self.assertEqual("candidate.txt", source)

    def test_episode_recall_enabled_reads_effective_runtime_flag(self):
        self.assertTrue(episode_recall_enabled({
            "engines": [
                {"engine_id": "atomic_engine", "recall_enabled": True},
                {"engine_id": "episode_engine", "recall_enabled": True},
            ],
        }))
        self.assertFalse(episode_recall_enabled({
            "engines": [
                {"engine_id": "episode_engine", "recall_enabled": False},
            ],
        }))
        self.assertFalse(episode_recall_enabled({"engines": []}))

    def test_episode_preparation_skips_when_engine_not_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            class Client:
                def runtime(self):
                    calls.append("runtime")
                    return {"engines": []}

                def generate_episode(self):
                    calls.append("generate")
                    return {}

            result = prepare_episode_recall(Client(), Path(directory), _Log())

            self.assertEqual("skipped", result["generation_status"])
            self.assertEqual("episode_engine_not_loaded", result["skip_reason"])
            self.assertEqual(["runtime"], calls)
            self.assertTrue((Path(directory) / "episode_preparation.json").is_file())

    def test_episode_preparation_generates_once_when_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            class Client:
                def runtime(self):
                    calls.append("runtime")
                    return {
                        "engines": [
                            {"engine_id": "episode_engine", "recall_enabled": True},
                        ],
                    }

                def generate_episode(self):
                    calls.append("generate")
                    return {"status": "generated", "count": 2}

            result = prepare_episode_recall(Client(), Path(directory), _Log())

            self.assertTrue(result["generation_triggered"])
            self.assertEqual("generated", result["generation_status"])
            self.assertEqual(["runtime", "generate"], calls)

    def test_episode_preparation_fails_before_qa_when_runtime_probe_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            class Client:
                def runtime(self):
                    raise RuntimeError("connection refused")

            with self.assertRaises(EpisodePreparationError) as raised:
                prepare_episode_recall(Client(), Path(directory), _Log())

            self.assertEqual(
                "runtime_probe_failed",
                raised.exception.preparation["generation_status"],
            )

    def test_episode_preparation_rejects_empty_or_failed_generation_response(self):
        for response in ({}, {"status": "failed"}):
            with self.subTest(response=response):
                with tempfile.TemporaryDirectory() as directory:
                    class Client:
                        def runtime(self):
                            return {
                                "engines": [
                                    {
                                        "engine_id": "episode_engine",
                                        "recall_enabled": True,
                                    },
                                ],
                            }

                        def generate_episode(self):
                            return response

                    with self.assertRaises(EpisodePreparationError) as raised:
                        prepare_episode_recall(
                            Client(),
                            Path(directory),
                            _Log(),
                        )

                    self.assertEqual(
                        "generation_failed",
                        raised.exception.preparation["generation_status"],
                    )

    def test_vendored_locomo_dataset_has_expected_hash(self):
        dataset = (
            Path(__file__).resolve().parents[1]
            / "benchmarks"
            / "locomo"
            / "data"
            / "locomo10.json"
        )

        self.assertTrue(dataset.is_file())
        # Normalize line endings so the fingerprint is stable across checkouts
        # (Windows autocrlf checks the file out as CRLF, git stores it as LF).
        normalized = dataset.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(
            "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4",
            hashlib.sha256(normalized).hexdigest(),
        )


class _Log:
    def info(self, *_args):
        return None

    def error(self, *_args):
        return None


class _NoWriteClient:
    def __getattr__(self, name):
        raise AssertionError(f"reuse mode attempted backend call: {name}")


class LocomoSelectionTests(unittest.TestCase):
    def test_selects_explicit_question_ids_in_dataset_order(self):
        jobs = [
            SimpleNamespace(question_id="q1"),
            SimpleNamespace(question_id="q2"),
            SimpleNamespace(question_id="q3"),
        ]

        selected = select_questions(
            jobs,
            question_ids=parse_question_ids("q3,q1,q3"),
        )

        self.assertEqual(["q1", "q3"], [job.question_id for job in selected])

    def test_rejects_unknown_question_ids(self):
        jobs = [SimpleNamespace(question_id="q1")]

        with self.assertRaisesRegex(ValueError, "unknown LoCoMo question ids"):
            select_questions(jobs, question_ids=["missing"])

    def test_session_mode_preserves_single_sample_locomo_sessions(self):
        self.assertEqual("locomo", resolve_session_mode("auto", 1))
        self.assertEqual("single", resolve_session_mode("auto", 2))


class LocomoEvaluateCompatibilityTests(unittest.TestCase):
    def test_success_status_keeps_historical_tool_warning_nonfatal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "qa.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "question_id",
                        "question",
                        "answer",
                        "response",
                        "retrieval_error",
                        "retrieval_status",
                        "model_error",
                        "model_status",
                    ],
                )
                writer.writeheader()
                writer.writerow({
                    "question_id": "q1",
                    "question": "Question",
                    "answer": "Gold",
                    "response": "Generated",
                    "retrieval_error": "one optional URI returned NOT_FOUND",
                    "retrieval_status": "ok",
                    "model_error": "",
                    "model_status": "ok",
                })

            result = load_qa_results(path)[0]

        self.assertEqual("", result.retrieval_error)
        self.assertEqual("", result.llm_error)

    def test_failed_status_preserves_artifact_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "qa.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "question_id",
                        "question",
                        "answer",
                        "response",
                        "retrieval_error",
                        "retrieval_status",
                    ],
                )
                writer.writeheader()
                writer.writerow({
                    "question_id": "q1",
                    "question": "Question",
                    "answer": "Gold",
                    "response": "",
                    "retrieval_error": "retrieval timed out",
                    "retrieval_status": "failed",
                })

            result = load_qa_results(path)[0]

        self.assertEqual("retrieval timed out", result.retrieval_error)


class LocomoImportTests(unittest.TestCase):
    def test_resume_qa_skips_completed_batches(self):
        prior_rows = [{
            "sample_id": "conv-30",
            "session_key": "",
            "session_id": "prior-session-1",
            "status": "completed",
        }]
        with tempfile.TemporaryDirectory() as directory:
            report = import_locomo_memory(
                [{"sample_id": "conv-30", "session_batches": [{"messages": []}]}],
                _NoWriteClient(),
                EvalConfig(),
                ImportOptions(
                    session_mode="locomo",
                    max_sessions=0,
                    resume_qa=True,
                    sample_filter="conv-30",
                    prior_import_rows=prior_rows,
                ),
                Path(directory),
                _Log(),
            )

            self.assertEqual(1, report.total)
            self.assertEqual(1, report.completed)
            self.assertEqual("reused", report.rows[0]["status"])
            self.assertEqual("prior-session-1", report.rows[0]["session_id"])
            self.assertTrue((Path(directory) / "import_results.csv").exists())


class LocomoQACheckpointTests(unittest.TestCase):
    def test_writes_ordered_checkpoint_and_trace_during_run(self):
        with tempfile.TemporaryDirectory() as directory:
            result_dir = Path(directory)
            tasks = [
                {"question_id": "q1"},
                {"question_id": "q2"},
            ]
            class FakePlugin:
                def send_message(self, session_id, message, context_path="/", *, extra=None):
                    qid = (extra or {}).get("question_id", "")
                    if qid == "q2":
                        return AgentResponse(
                            text="two",
                            extra={
                                "trace": {
                                    "question_id": "q2",
                                    "tool_audit": {
                                        "schema_version": 1,
                                        "tools_used": ["memory_list"],
                                        "tool_calls": [],
                                        "discovered_files": [],
                                        "read_files": [],
                                    },
                                },
                            },
                        )
                    return AgentResponse(
                        text="one",
                        extra={"trace": {"question_id": "q1"}},
                    )

            plugin = FakePlugin()
            options = QAOptions(
                profile=VIKINGBOAT_0411_PROFILE,
                checkpoint_interval=1,
            )
            run_locomo_qa(
                tasks,
                plugin,
                EvalConfig(concurrency=1),
                options,
                result_dir,
                _Log(),
            )

            with (
                result_dir / "qa_results.checkpoint.csv"
            ).open(encoding="utf-8", newline="") as handle:
                final_ids = [
                    row["question_id"] for row in csv.DictReader(handle)
                ]
            self.assertEqual(["q1", "q2"], final_ids)
            self.assertTrue(
                (result_dir / "agent_traces" / "q1.json").is_file()
            )
            self.assertTrue(
                (result_dir / "agent_traces" / "q2.json").is_file()
            )
            audit_rows = [
                json.loads(line)
                for line in (result_dir / "tool_audits.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(["q2"], [row["question_id"] for row in audit_rows])
            self.assertEqual(["memory_list"], audit_rows[0]["tools_used"])

    def test_existing_results_are_not_sent_to_agent_again(self):
        with tempfile.TemporaryDirectory() as directory:
            result_dir = Path(directory)
            tasks = [
                {"question_id": "q1"},
                {"question_id": "q2"},
            ]

            class FakePlugin:
                received_ids: list[str] = []

                def send_message(self, session_id, message, context_path="/", *, extra=None):
                    qid = str((extra or {}).get("question_id", ""))
                    self.received_ids.append(qid)
                    return AgentResponse(text="two")

            plugin = FakePlugin()
            options = QAOptions(
                profile=VIKINGBOAT_0411_PROFILE,
                checkpoint_interval=1,
            )
            existing = QAResult(
                question_id="q1",
                question="first",
                answer="one",
                response="one",
            )
            results = run_locomo_qa(
                tasks,
                plugin,
                EvalConfig(concurrency=1),
                options,
                result_dir,
                _Log(),
                existing_results=[existing],
            )

            self.assertEqual(["q2"], plugin.received_ids)
            self.assertEqual(["q1", "q2"], [
                result.question_id for result in results
            ])

    def test_interrupt_persists_latest_completed_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            result_dir = Path(directory)
            tasks = [
                {"question_id": "q1"},
                {"question_id": "q2"},
            ]

            class InterruptingPlugin:
                def send_message(self, session_id, message, context_path="/", *, extra=None):
                    qid = (extra or {}).get("question_id", "")
                    if qid == "q2":
                        raise KeyboardInterrupt
                    return AgentResponse(text="one")

            options = QAOptions(
                profile=VIKINGBOAT_0411_PROFILE,
                checkpoint_interval=10,
            )
            with self.assertRaises(KeyboardInterrupt):
                run_locomo_qa(
                    tasks,
                    InterruptingPlugin(),
                    EvalConfig(concurrency=1),
                    options,
                    result_dir,
                    _Log(),
                )

            with (
                result_dir / "qa_results.checkpoint.csv"
            ).open(encoding="utf-8", newline="") as handle:
                question_ids = [
                    row["question_id"] for row in csv.DictReader(handle)
                ]
            self.assertEqual(["q1"], question_ids)


class LocomoQAResumeTests(unittest.TestCase):
    def _options(self) -> QAOptions:
        return QAOptions(
            profile=VIKINGBOAT_0411_PROFILE,
            checkpoint_interval=10,
        )

    def _config(self) -> EvalConfig:
        return EvalConfig(
            llm_base_url="https://model.test/v1",
            llm_model="model",
            llm_max_tokens=1024,
            top_k=25,
            memory_budget_chars=6000,
            question_timeout_s=600,
        )

    def _tasks(self) -> list[dict[str, str]]:
        return [
            {
                "question_id": "q1",
                "sample_id": "conv-30",
                "question": "first",
                "answer": "one",
            },
            {
                "question_id": "q2",
                "sample_id": "conv-30",
                "question": "second",
                "answer": "two",
            },
        ]

    def _write_rows(
        self,
        path: Path,
        results: list[QAResult],
    ) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=QA_FIELDS)
            writer.writeheader()
            for result in results:
                row = result.to_csv_row()
                row["retrieval_items_json"] = json.dumps(
                    result.retrieval_items
                )
                writer.writerow(row)

    def test_reuses_only_healthy_compatible_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            manifest = build_qa_resume_manifest(
                dataset_path=source / "locomo.json",
                sample_filter="conv-30",
                session_mode="locomo",
                config=self._config(),
                options=self._options(),
            )
            write_qa_resume_manifest(source, manifest)
            self._write_rows(source / "qa_results.checkpoint.csv", [
                QAResult(
                    question_id="q1",
                    sample_id="conv-30",
                    question="first",
                    answer="one",
                    response="one",
                    retrieval_items=[{"uri": "echo://one"}],
                    qa_profile=VIKINGBOAT_0411_PROFILE,
                ),
                QAResult(
                    question_id="q2",
                    sample_id="conv-30",
                    question="second",
                    answer="two",
                    response="",
                    retrieval_error="timeout",
                    qa_profile=VIKINGBOAT_0411_PROFILE,
                ),
            ])

            state = load_qa_resume_state(
                source,
                tasks=self._tasks(),
                expected_manifest=manifest,
            )

            self.assertEqual(["q1"], [
                result.question_id for result in state.results
            ])
            self.assertEqual(["q2"], state.discarded_question_ids)

    def test_manifest_fingerprints_local_qa_contract(self):
        manifest = build_qa_resume_manifest(
            dataset_path="/tmp/locomo.json",
            sample_filter="conv-30",
            session_mode="locomo",
            config=self._config(),
            options=self._options(),
        )

        self.assertEqual(2, manifest["schema_version"])
        self.assertEqual(64, len(manifest["qa_contract"]["sha256"]))
        self.assertIn(
            "plugins/vikingbot/runtime.py",
            manifest["qa_contract"]["files"],
        )
        self.assertIn(
            "plugins/vikingbot/tools.py",
            manifest["qa_contract"]["files"],
        )
        self.assertIn(
            "plugins/echomem_mcp/plugin.py",
            manifest["qa_contract"]["files"],
        )

    def test_rejects_configuration_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            manifest = build_qa_resume_manifest(
                dataset_path=source / "locomo.json",
                sample_filter="conv-30",
                session_mode="locomo",
                config=self._config(),
                options=self._options(),
            )
            write_qa_resume_manifest(source, manifest)
            self._write_rows(source / "qa_results.csv", [])
            changed = json.loads(json.dumps(manifest))
            changed["qa"]["top_k"] = 999

            with self.assertRaisesRegex(
                ValueError,
                "qa.top_k",
            ):
                load_qa_resume_state(
                    source,
                    tasks=self._tasks(),
                    expected_manifest=changed,
                )


class LocomoJudgeResumeTests(unittest.TestCase):
    def test_rejects_different_judge_prompt_or_model(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            manifest = build_judge_resume_manifest(
                base_url="https://judge.test/v1",
                model="judge-a",
                system_prompt="system",
                prompt_template="template",
            )
            write_judge_resume_manifest(source, manifest)
            (source / "judge_results.checkpoint.csv").write_text(
                "question_id,verdict\nq1,CORRECT\n",
                encoding="utf-8",
            )
            changed = build_judge_resume_manifest(
                base_url="https://judge.test/v1",
                model="judge-b",
                system_prompt="system",
                prompt_template="template",
            )

            with self.assertRaisesRegex(
                ValueError,
                "judge.model",
            ):
                load_judge_resume_state(
                    source,
                    expected_manifest=changed,
                )


class LocomoRetryTests(unittest.TestCase):
    def test_detects_failed_and_missing_questions(self):
        rows = [
            {"question_id": "q1", "response": "ok"},
            {"question_id": "q2", "response": "", "llm_error": "timeout"},
        ]

        self.assertFalse(qa_row_failed(rows[0]))
        self.assertEqual(
            ["q2"],
            retry_question_ids("failed", rows, ["q1", "q2", "q3"]),
        )
        self.assertEqual(
            ["q3"],
            retry_question_ids("missing", rows, ["q1", "q2", "q3"]),
        )

    def test_merges_only_recovered_retry_rows(self):
        original = [
            {"question_id": "q1", "response": "old"},
            {"question_id": "q2", "response": "", "llm_error": "timeout"},
        ]
        retries = [
            {"question_id": "q2", "response": "recovered", "llm_error": ""},
            {"question_id": "q3", "response": "", "retrieval_error": "empty"},
        ]

        merged, stats = merge_retry_rows(original, retries)

        self.assertEqual("recovered", merged[1]["response"])
        self.assertEqual(1, stats["recovered"])
        self.assertEqual([], stats["appended"])


class LocomoStatsTests(unittest.TestCase):
    def test_excludes_judge_errors_from_accuracy_denominator(self):
        summary = summarize_judge_rows([
            {"verdict": "CORRECT", "judge_error": ""},
            {"verdict": "WRONG", "judge_error": ""},
            {"verdict": "ERROR", "judge_error": "timeout"},
        ])

        self.assertEqual(2, summary["graded"])
        self.assertEqual(1, summary["errors"])
        self.assertEqual(0.5, summary["accuracy"])


class LocomoDiagnosisTests(unittest.TestCase):
    def test_distinguishes_empty_retrieval_from_unused_evidence(self):
        qa_rows = [
            {
                "question_id": "q1",
                "question": "What drink does Maya prefer?",
                "answer": "jasmine tea",
                "response": "coffee",
                "retrieval_items_json": json.dumps([{
                    "uri": "echo://memory/1",
                    "content": "Maya prefers jasmine tea in the morning.",
                }]),
            },
            {
                "question_id": "q2",
                "question": "Where did Maya travel?",
                "answer": "Paris",
                "response": "London",
                "retrieval_items_json": "[]",
            },
        ]
        judge_rows = [
            {"question_id": "q1", "verdict": "WRONG"},
            {"question_id": "q2", "verdict": "WRONG"},
        ]
        jobs = [
            SimpleNamespace(question_id="q1", sample_id="conv-30", category="1"),
            SimpleNamespace(question_id="q2", sample_id="conv-30", category="2"),
            SimpleNamespace(question_id="q3", sample_id="conv-30", category="3"),
        ]

        summary, traces = build_diagnosis(qa_rows, judge_rows, jobs)

        self.assertEqual("evidence_unused", traces[0]["mode"])
        self.assertEqual("empty_retrieval", traces[1]["mode"])
        self.assertEqual(["q3"], summary["missing_question_ids"])
        self.assertEqual(["q2"], summary["retryable_question_ids"])


if __name__ == "__main__":
    unittest.main()
