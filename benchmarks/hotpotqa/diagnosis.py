"""Explain HotpotQA failures using QA results, official metrics, and dataset evidence.

HotpotQA has no LLM judge: correctness comes from the official metrics in
eval_results.csv (answer_em / answer_f1). Classification therefore needs the
eval row per question, unlike LoCoMo's judge-based classify_failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.hotpotqa.dataset import load_dataset
from benchmarks.hotpotqa.selection import select_jobs_and_plans
from shared.csv_io import read_dict_rows
from shared.text import normalize_answer


def _read_csv(path: Path) -> list[dict[str, str]]:
    return read_dict_rows(path)


def _retrieval_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    value = row.get("retrieval_items_json") or row.get("retrieval_items") or "[]"
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _evidence_text(items: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(
            item.get("content")
            or item.get("text")
            or item.get("abstract")
            or item.get("uri")
            or ""
        )
        for item in items
    )


def _gold_evidence_overlap(gold: str, evidence: str) -> bool:
    gold_tokens = [
        token
        for token in normalize_answer(gold).split()
        if len(token) > 2
    ]
    normalized_evidence = normalize_answer(evidence)
    if not gold_tokens:
        return False
    required = 1 if len(gold_tokens) <= 2 else max(2, len(gold_tokens) // 2)
    return sum(token in normalized_evidence for token in gold_tokens) >= required


def _eval_float(eval_row: dict[str, Any] | None, key: str) -> float:
    try:
        return float((eval_row or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def classify_failure(
    qa_row: dict[str, Any],
    eval_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Classify a HotpotQA question outcome using its QA and official-metric rows."""
    answer_em = _eval_float(eval_row, "answer_em")
    answer_f1 = _eval_float(eval_row, "answer_f1")
    items = _retrieval_items(qa_row)
    evidence = _evidence_text(items)
    has_evidence = bool(evidence.strip())
    gold_overlap = _gold_evidence_overlap(
        str(qa_row.get("answer") or ""),
        evidence,
    )
    if answer_em >= 1.0:
        mode, label, retryable = "correct", "Correct", False
    elif str(qa_row.get("llm_error") or "").strip():
        mode, label, retryable = "model_error", "Model/API Error", True
    elif str(qa_row.get("retrieval_error") or "").strip():
        mode, label, retryable = "retrieval_error", "Retrieval Error", True
    elif not str(qa_row.get("response") or "").strip():
        mode, label, retryable = "empty_answer", "Empty Answer", True
    elif not items:
        mode, label, retryable = "empty_retrieval", "Empty Retrieval", True
    elif answer_f1 > 0:
        mode, label, retryable = "partial_answer", "Partial Answer", False
    elif gold_overlap:
        mode, label, retryable = "evidence_unused", "Evidence Found but Unused", False
    elif has_evidence:
        mode, label, retryable = "evidence_mismatch", "Retrieved Evidence Mismatch", False
    else:
        mode, label, retryable = "memory_missing", "Memory Missing", False
    return {
        "mode": mode,
        "label": label,
        "retryable": retryable,
        "retrieval_count": len(items),
        "has_evidence": has_evidence,
        "gold_overlap": gold_overlap,
    }


def build_diagnosis(
    qa_rows: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    jobs,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    evals = {
        str(row.get("question_id") or ""): row
        for row in eval_rows
        if str(row.get("question_id") or "")
    }
    job_by_id = {job.question_id: job for job in jobs}
    traces: list[dict[str, Any]] = []
    duplicate_counts = Counter(
        str(row.get("question_id") or "")
        for row in qa_rows
        if str(row.get("question_id") or "")
    )
    for qa_row in qa_rows:
        question_id = str(qa_row.get("question_id") or "")
        eval_row = evals.get(question_id)
        attribution = classify_failure(qa_row, eval_row)
        job = job_by_id.get(question_id)
        items = _retrieval_items(qa_row)
        traces.append({
            "question_id": question_id,
            "sample_id": str(getattr(job, "sample_id", "") or ""),
            "category": str(getattr(job, "category", "") or ""),
            "question": str(qa_row.get("question") or ""),
            "gold_answer": str(qa_row.get("answer") or ""),
            "response": str(qa_row.get("response") or ""),
            "answer_em": _eval_float(eval_row, "answer_em"),
            "answer_f1": _eval_float(eval_row, "answer_f1"),
            "supporting_facts_em": _eval_float(eval_row, "supporting_facts_em"),
            "supporting_facts_f1": _eval_float(eval_row, "supporting_facts_f1"),
            "retrieval_error": str(qa_row.get("retrieval_error") or ""),
            "llm_error": str(qa_row.get("llm_error") or ""),
            "retrieval_items": items,
            **attribution,
        })

    expected_ids = [job.question_id for job in jobs]
    actual_ids = {
        str(row.get("question_id") or "")
        for row in qa_rows
        if str(row.get("question_id") or "")
    }
    failures = [trace for trace in traces if trace["mode"] != "correct"]
    mode_counts = Counter(trace["mode"] for trace in failures)
    category_counts: dict[str, dict[str, int | float]] = {}
    for trace in traces:
        category = trace["category"] or "unknown"
        stats = category_counts.setdefault(
            category,
            {"total": 0, "correct": 0, "failed": 0},
        )
        stats["total"] += 1
        if trace["mode"] == "correct":
            stats["correct"] += 1
        else:
            stats["failed"] += 1
    for stats in category_counts.values():
        stats["accuracy"] = (
            stats["correct"] / stats["total"] if stats["total"] else 0.0
        )
    correct = sum(trace["mode"] == "correct" for trace in traces)
    summary = {
        "schema_version": 1,
        "total": len(traces),
        "correct": correct,
        "failed": len(failures),
        "accuracy": correct / len(traces) if traces else 0.0,
        "retrieval_coverage": (
            sum(bool(trace["has_evidence"]) for trace in traces) / len(traces)
            if traces
            else 0.0
        ),
        "failure_breakdown": [
            {
                "mode": mode,
                "label": next(
                    trace["label"] for trace in failures if trace["mode"] == mode
                ),
                "count": count,
                "percentage": count / len(failures) if failures else 0.0,
                "question_ids": [
                    trace["question_id"]
                    for trace in failures
                    if trace["mode"] == mode
                ][:20],
            }
            for mode, count in mode_counts.most_common()
        ],
        "category_breakdown": category_counts,
        "retryable_question_ids": [
            trace["question_id"] for trace in failures if trace["retryable"]
        ],
        "missing_question_ids": [
            question_id
            for question_id in expected_ids
            if question_id not in actual_ids
        ],
        "unexpected_question_ids": sorted(actual_ids - set(expected_ids)),
        "duplicate_question_ids": sorted(
            question_id
            for question_id, count in duplicate_counts.items()
            if count > 1
        ),
    }
    return summary, traces


def diagnose_run(
    qa_results_path: Path,
    eval_results_path: Path,
    dataset_path: Path,
    sample: str,
    question_limit: int = 0,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Write diagnosis.json + retrieval_traces.jsonl for a HotpotQA run directory."""
    jobs, plans = load_dataset(dataset_path, sample_filter=sample)
    jobs, plans = select_jobs_and_plans(
        jobs,
        plans,
        question_ids=[],
        limit=question_limit,
    )
    qa_rows = _read_csv(qa_results_path)
    eval_rows = (
        _read_csv(eval_results_path)
        if eval_results_path.is_file()
        else []
    )
    summary, traces = build_diagnosis(qa_rows, eval_rows, jobs)
    summary.update({
        "qa_results": str(qa_results_path),
        "eval_results": str(eval_results_path),
        "dataset": str(dataset_path),
        "sample": sample,
    })
    output_dir = output_dir or qa_results_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "diagnosis.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "retrieval_traces.jsonl").write_text(
        "".join(
            json.dumps(trace, ensure_ascii=False) + "\n"
            for trace in traces
        ),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose a HotpotQA evaluation run")
    parser.add_argument("--qa-results", required=True)
    parser.add_argument("--eval-results", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--sample", default="all")
    parser.add_argument(
        "--questions",
        type=int,
        default=0,
        help="与原运行一致的题数限制 (0=all)",
    )
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    summary = diagnose_run(
        Path(args.qa_results).expanduser().resolve(),
        Path(args.eval_results).expanduser().resolve(),
        Path(args.dataset).expanduser().resolve(),
        args.sample,
        args.questions,
        Path(args.out_dir).expanduser().resolve(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
