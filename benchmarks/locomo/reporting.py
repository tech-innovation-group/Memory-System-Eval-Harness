"""LoCoMo run summary construction."""

from __future__ import annotations

from benchmarks.locomo.import_memory import ImportReport
from benchmarks.locomo.judge import JudgeReport
from benchmarks.locomo.profiles import (
    profile_reference,
    profile_source,
)
from benchmarks.locomo.qa import QAOptions
from shared.qa import QAResult


def build_summary(
    *,
    dataset_path: str,
    sample_filter: str,
    total_samples: int,
    total_questions: int,
    import_report: ImportReport,
    resume_qa: bool,
    qa_results: list[QAResult],
    judge_report: JudgeReport,
    qa_options: QAOptions,
    session_mode: str,
    evaluation_identity: dict[str, str],
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
            if import_report.incomplete
            or qa_errors
            or retrieval_errors
            or judge_report.errors
            else "completed"
        ),
        "benchmark": "locomo",
        "dataset": dataset_path,
        "sample_filter": sample_filter,
        "total_samples": total_samples,
        "total_questions": total_questions,
        "import_ok": import_report.completed,
        "import_total": import_report.total,
        "incomplete_imports": import_report.incomplete,
        "memory_source": "existing" if resume_qa else "injected",
        "qa_count": len(qa_results),
        "qa_errors": qa_errors,
        "retrieval_errors": retrieval_errors,
        "judge_correct": judge_report.correct,
        "judge_wrong": judge_report.wrong,
        "judge_errors": judge_report.errors,
        "judge_graded": judge_report.graded,
        "accuracy": round(judge_report.accuracy, 4),
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
        "qa_profile": qa_options.profile,
        "tools_enabled": qa_options.tools_enabled,
        "qa_profile_reference": profile_reference(qa_options.profile),
        "qa_profile_source": profile_source(qa_options.profile),
        "qa_prompt_append": {
            "enabled": bool(qa_options.system_prompt_append),
            "source": qa_options.system_prompt_append_source,
            "sha256": qa_options.system_prompt_append_sha256,
        },
        "tool_call_total": sum(
            result.tool_call_count for result in qa_results
        ),
        "avg_iterations": round(
            sum(result.iterations for result in qa_results)
            / max(len(qa_results), 1),
            2,
        ),
        "top_k": qa_options.top_k,
        "memory_budget_chars": qa_options.memory_budget_chars,
        "agent_options": qa_options.agent_options,
        "checkpoint_interval": qa_options.checkpoint_interval,
        "session_mode": session_mode,
        "retrieval_scope": "session" if session_mode == "single" else "account",
        "memory_identity": evaluation_identity,
        "served_model_ids": served_models,
        "tool_protocol_sha256": tool_protocol_hashes,
        "messages_jsonl_read_questions": transcript_read_questions,
        "messages_jsonl_read_calls": transcript_read_calls,
        "messages_jsonl_read_rate": round(
            transcript_read_questions / qa_total if qa_total else 0.0,
            4,
        ),
    }
