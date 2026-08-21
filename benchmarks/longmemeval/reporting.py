"""LongMemEval summary construction."""

from __future__ import annotations

from benchmarks.longmemeval.evaluate import EvaluationReport
from benchmarks.longmemeval.import_memory import ImportReport
from shared.qa import QAResult


def build_summary(
    *,
    dataset_path: str,
    jobs,
    import_report: ImportReport,
    qa_results: list[QAResult],
    evaluation_report: EvaluationReport,
    evaluation_identity: dict[str, str],
    resumed: bool = False,
) -> dict:
    qa_errors = sum(1 for result in qa_results if result.llm_error)
    retrieval_errors = sum(
        1 for result in qa_results if result.retrieval_error
    )
    return {
        "status": (
            "failed"
            if import_report.incomplete
            or qa_errors
            or retrieval_errors
            or evaluation_report.errors
            else "completed"
        ),
        "benchmark": "longmemeval",
        "dataset": dataset_path,
        "total_questions": len(jobs),
        "import_ok": import_report.completed,
        "import_total": import_report.total,
        "incomplete_imports": import_report.incomplete,
        "memory_source": "existing" if resumed else "injected",
        "qa_count": len(qa_results),
        "qa_errors": qa_errors,
        "retrieval_errors": retrieval_errors,
        "judge_errors": evaluation_report.errors,
        "accuracy": round(evaluation_report.overall_accuracy, 4),
        "overall_accuracy": round(evaluation_report.overall_accuracy, 4),
        "task_averaged_accuracy": (
            round(evaluation_report.task_averaged_accuracy, 4)
            if evaluation_report.task_averaged_accuracy is not None
            else None
        ),
        "abstention_accuracy": (
            round(evaluation_report.abstention_accuracy, 4)
            if evaluation_report.abstention_accuracy is not None
            else None
        ),
        "abstention_count": evaluation_report.abstention_count,
        "correct": evaluation_report.correct,
        "total": evaluation_report.graded,
        "per_type": evaluation_report.per_type,
        "avg_qa_elapsed_s": round(
            sum(result.elapsed_s for result in qa_results)
            / max(len(qa_results), 1),
            2,
        ),
        "total_prompt_tokens": sum(
            result.prompt_tokens for result in qa_results
        ),
        "total_completion_tokens": sum(
            result.completion_tokens for result in qa_results
        ),
        "memory_identity": evaluation_identity,
    }
