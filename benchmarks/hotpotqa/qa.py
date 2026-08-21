"""HotpotQA QA execution with retrieval evidence persistence."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from tqdm import tqdm

from shared.benchmark_qa import run_concurrent_qa
from shared.eval_base import EvalConfig
from shared.qa import BASE_QA_FIELDS, QAResult


QA_FIELDS = (*BASE_QA_FIELDS, "retrieval_items_json")


def build_qa_tasks(jobs, question_to_session: dict[str, str], config: EvalConfig, agent_id: str = ""):
    return [{
        "question_id": job.question_id,
        "sample_id": job.sample_id,
        "category": job.category,
        "question": job.question,
        "answer": job.answer,
        "top_k": config.top_k,
        "memory_budget_chars": config.memory_budget_chars,
        "session_id": question_to_session.get(job.question_id, ""),
        "agent_id": agent_id,
        "question_time": job.query_time,
    } for job in jobs]


def _safe_question_id(question_id: str) -> str:
    return re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        question_id,
    ).strip("._") or "question"


def _write_trace(trace_dir: Path, result: QAResult) -> None:
    if not result.trace:
        return
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / f"{_safe_question_id(result.question_id)}.json").write_text(
        json.dumps(result.trace, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _tool_audit_rows(results: list[QAResult]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        audit = result.trace.get("tool_audit") if result.trace else None
        if not isinstance(audit, dict):
            continue
        rows.append({
            "question_id": result.question_id,
            "sample_id": result.sample_id,
            "category": result.category,
            "question": result.question,
            "response": result.response,
            "qa_profile": result.qa_profile,
            **audit,
        })
    return rows


def _write_tool_audits(path: Path, results: list[QAResult]) -> None:
    rows = _tool_audit_rows(results)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    path.with_suffix(".json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_tool_audits(result_dir: Path, results: list[QAResult]) -> None:
    """Write tool_audits.jsonl/.json for the given results.

    Called after resume trace restoration so the resumed result directory
    carries tool audits for reused questions too (equivalent to from-scratch).
    """
    _write_tool_audits(result_dir / "tool_audits.jsonl", results)


def _write_qa_results(path: Path, results: list[QAResult]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QA_FIELDS)
        writer.writeheader()
        for result in results:
            row = result.to_csv_row()
            row["retrieval_items_json"] = json.dumps(
                result.retrieval_items,
                ensure_ascii=False,
            )
            writer.writerow(row)


def run_hotpotqa_qa(
    tasks,
    agent_plugin,
    config: EvalConfig,
    result_dir: Path,
    log,
    existing_results: list[QAResult] | None = None,
    checkpoint_interval: int = 0,
) -> list[QAResult]:
    existing_by_id = {
        result.question_id: result
        for result in (existing_results or [])
    }
    pending_tasks = [
        task
        for task in tasks
        if str(task["question_id"]) not in existing_by_id
    ]
    progress = tqdm(total=len(pending_tasks), desc="QA", unit="q")
    result_order = {
        str(task["question_id"]): index
        for index, task in enumerate(tasks)
    }
    completed_results: dict[str, QAResult] = dict(existing_by_id)
    checkpoint_path = result_dir / "qa_results.checkpoint.csv"
    trace_dir = result_dir / "agent_traces"
    tool_audit_path = result_dir / "tool_audits.jsonl"

    def on_progress(done: int, result: QAResult) -> None:
        progress.update(1)
        completed_results[result.question_id] = result
        _write_trace(trace_dir, result)
        ordered_completed = sorted(
            completed_results.values(),
            key=lambda item: result_order.get(item.question_id, len(tasks)),
        )
        _write_tool_audits(tool_audit_path, ordered_completed)
        if (
            checkpoint_interval > 0
            and done % checkpoint_interval == 0
        ):
            _write_qa_results(checkpoint_path, ordered_completed)
            log.info(
                "  QA checkpoint: %d/%d -> %s",
                len(ordered_completed),
                len(tasks),
                checkpoint_path,
            )
        preview = result.response[:100] if result.response else f"(no response) error={result.llm_error[:200]}"
        log.info("  Q[%s] -> %s", result.question_id, preview)

    try:
        if not pending_tasks:
            results = []
        else:
            results = run_concurrent_qa(
                agent_plugin,
                pending_tasks,
                concurrency=config.concurrency,
                question_timeout_s=config.question_timeout_s,
                progress_callback=on_progress,
            )
    finally:
        progress.close()
        if completed_results:
            partial = sorted(
                completed_results.values(),
                key=lambda item: result_order.get(item.question_id, len(tasks)),
            )
            _write_qa_results(checkpoint_path, partial)
            log.info(
                "QA latest checkpoint saved: %d/%d -> %s",
                len(partial),
                len(tasks),
                checkpoint_path,
            )

    completed_results.update({
        result.question_id: result
        for result in results
    })
    final_results = sorted(
        completed_results.values(),
        key=lambda item: result_order.get(item.question_id, len(tasks)),
    )
    output_path = result_dir / "qa_results.csv"
    _write_qa_results(output_path, final_results)
    if checkpoint_interval > 0:
        _write_qa_results(checkpoint_path, final_results)
    log.info("QA 结果已保存: %s", output_path)
    traced_results = [result for result in final_results if result.trace]
    if traced_results:
        log.info(
            "Agent traces 已保存: %s (%d files)",
            trace_dir,
            len(traced_results),
        )
        _write_tool_audits(tool_audit_path, final_results)
        log.info("Tool audits 已保存: %s", tool_audit_path)
    return final_results
