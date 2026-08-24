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
    served_models = sorted({
        str(
            iteration.get("model_response", {}).get("response_model") or ""
        )
        for result in qa_results
        for iteration in result.trace.get("iterations", [])
        if str(
            iteration.get("model_response", {}).get("response_model") or ""
        ).strip()
    })
    tool_protocol_hashes = sorted({
        str(result.trace.get("tool_protocol", {}).get("sha256") or "")
        for result in qa_results
        if str(result.trace.get("tool_protocol", {}).get("sha256") or "").strip()
    })
    transcript_read_questions = 0
    transcript_read_calls = 0
    for result in qa_results:
        audit = result.trace.get("tool_audit") if result.trace else {}
        if not isinstance(audit, dict):
            continue
        reads = audit.get("messages_jsonl_reads") or []
        if reads:
            transcript_read_questions += 1
            transcript_read_calls += len(reads)
        else:
            read_files = audit.get("read_files") or []
            matching = [
                row for row in read_files
                if "messages.jsonl" in str(row.get("uri") or "")
            ]
            if matching:
                transcript_read_questions += 1
                transcript_read_calls += len(matching)
    qa_total = len(qa_results)
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
        "tool_call_total": sum(
            result.tool_call_count for result in qa_results
        ),
        "avg_iterations": round(
            sum(result.iterations for result in qa_results)
            / max(len(qa_results), 1),
            2,
        ),
        "served_model_ids": served_models,
        "tool_protocol_sha256": tool_protocol_hashes,
        "messages_jsonl_read_questions": transcript_read_questions,
        "messages_jsonl_read_calls": transcript_read_calls,
        "messages_jsonl_read_rate": round(
            transcript_read_questions / qa_total if qa_total else 0.0,
            4,
        ),
        "memory_identity": evaluation_identity,
    }
