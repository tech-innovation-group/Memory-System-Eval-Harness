"""HotpotQA summary construction."""

from __future__ import annotations

from benchmarks.hotpotqa.evaluate import EvaluationReport
from benchmarks.hotpotqa.import_memory import ImportReport
from shared.qa import QAResult


def build_summary(
    *,
    dataset_path: str,
    import_mode: str,
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
            if import_report.incomplete or qa_errors or retrieval_errors
            else "completed"
        ),
        "benchmark": "hotpotqa",
        "dataset": dataset_path,
        "import_mode": import_mode,
        "memory_source": "existing" if resumed else "injected",
        "total_questions": len(jobs),
        "import_ok": import_report.completed,
        "import_total": import_report.total,
        "incomplete_imports": import_report.incomplete,
        "qa_count": len(qa_results),
        "qa_errors": qa_errors,
        "retrieval_errors": retrieval_errors,
        "avg_f1": round(evaluation_report.answer_f1, 4),
        "avg_em": round(evaluation_report.answer_em, 4),
        "answer_f1": round(evaluation_report.answer_f1, 4),
        "answer_em": round(evaluation_report.answer_em, 4),
        "supporting_facts_f1": round(
            evaluation_report.supporting_facts_f1,
            4,
        ),
        "supporting_facts_em": round(
            evaluation_report.supporting_facts_em,
            4,
        ),
        "joint_f1": round(evaluation_report.joint_f1, 4),
        "joint_em": round(evaluation_report.joint_em, 4),
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
