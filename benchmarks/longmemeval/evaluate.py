"""Official-style LongMemEval answer evaluation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tqdm import tqdm

from benchmarks.longmemeval.judge import judge_answer
from shared.qa import QAResult


TASK_TYPES = (
    "single-session-user",
    "single-session-preference",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
)

EVAL_FIELDS = (
    "question_id",
    "question_type",
    "question",
    "answer",
    "response",
    "correct",
    "judge_error",
)


@dataclass
class EvaluationReport:
    rows: list[dict[str, Any]]
    correct: int
    graded: int
    errors: int
    overall_accuracy: float
    task_averaged_accuracy: float | None
    abstention_accuracy: float | None
    abstention_count: int
    per_type: dict[str, dict[str, Any]]


def _reuse_correct(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def evaluate_longmemeval(
    qa_results: list[QAResult],
    jobs,
    judge_llm,
    result_dir: Path,
    log,
    *,
    existing_rows: list[dict[str, str]] | None = None,
) -> EvaluationReport:
    rows: list[dict[str, Any]] = []
    type_scores: dict[str, list[bool]] = {task: [] for task in TASK_TYPES}
    abstention_scores: list[bool] = []
    existing_by_id: dict[str, dict[str, str]] = {}
    if existing_rows:
        for prior in existing_rows:
            question_id = str(prior.get("question_id") or "").strip()
            if question_id and not str(prior.get("judge_error") or "").strip():
                existing_by_id[question_id] = prior
    for result, job in tqdm(
        list(zip(qa_results, jobs)),
        desc="Judge",
        unit="q",
    ):
        task_type = str(job.category or "")
        abstention = "_abs" in result.question_id
        prior = existing_by_id.get(result.question_id)
        if (
            prior
            and str(prior.get("question") or "") == result.question
            and str(prior.get("answer") or "") == result.answer
            and str(prior.get("response") or "") == result.response
        ):
            correct = _reuse_correct(prior.get("correct"))
            judge_error = ""
        elif result.llm_error or result.retrieval_error:
            correct = False
            judge_error = "skipped because QA or retrieval failed"
        else:
            try:
                correct = judge_answer(
                    judge_llm,
                    task_type,
                    result.question,
                    result.answer,
                    result.response,
                    abstention=abstention,
                )
                judge_error = ""
            except Exception as exc:
                correct = False
                judge_error = str(exc)
                log.error("Judge %s failed: %s", result.question_id, exc)
        rows.append({
            "question_id": result.question_id,
            "question_type": task_type,
            "question": result.question,
            "answer": result.answer,
            "response": result.response,
            "correct": correct,
            "judge_error": judge_error,
        })
        if not judge_error:
            type_scores.setdefault(task_type, []).append(correct)
            if abstention:
                abstention_scores.append(correct)

    output_path = result_dir / "eval_results.csv"
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVAL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    graded_rows = [row for row in rows if not row["judge_error"]]
    correct = sum(1 for row in graded_rows if row["correct"])
    per_type: dict[str, dict[str, Any]] = {}
    task_accuracies: list[float] = []
    for task_type, scores in type_scores.items():
        if not scores:
            continue
        accuracy = sum(scores) / len(scores)
        task_accuracies.append(accuracy)
        per_type[task_type] = {
            "correct": sum(scores),
            "total": len(scores),
            "accuracy": round(accuracy, 4),
        }
    return EvaluationReport(
        rows=rows,
        correct=correct,
        graded=len(graded_rows),
        errors=sum(1 for row in rows if row["judge_error"]),
        overall_accuracy=correct / len(graded_rows) if graded_rows else 0.0,
        task_averaged_accuracy=(
            sum(task_accuracies) / len(task_accuracies)
            if task_accuracies
            else None
        ),
        abstention_accuracy=(
            sum(abstention_scores) / len(abstention_scores)
            if abstention_scores
            else None
        ),
        abstention_count=len(abstention_scores),
        per_type=per_type,
    )
