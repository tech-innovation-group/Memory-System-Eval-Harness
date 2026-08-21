"""Shared resume helpers for benchmark QA results.

Benchmark resume flows load healthy QA answers from a prior run's
``qa_results.csv`` (or ``qa_results.checkpoint.csv``), merge them with the
current run, and only re-run the missing/unhealthy remainder so metrics
(tokens / latency / accuracy) accumulate over the whole run instead of only
this segment.  hotpotqa and longmemeval both use these helpers; locomo keeps
its own equivalent logic because it additionally restores agent traces.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.csv_io import read_dict_rows
from shared.qa import QAResult


@dataclass
class ResumeQAState:
    """Healthy QA answers loaded from a prior run plus discarded ids."""

    source_csv: Path
    results: list[QAResult]
    discarded_question_ids: list[str]


def parse_qa_result_from_row(row: dict[str, str]) -> QAResult:
    """Reconstruct a QAResult from a ``qa_results.csv`` row."""

    def _float(key: str, default: float = 0.0) -> float:
        try:
            text = str(row.get(key) or "").strip()
            return float(text) if text else default
        except (TypeError, ValueError):
            return default

    def _int(key: str, default: int = 0) -> int:
        try:
            text = str(row.get(key) or "").strip()
            return int(float(text)) if text else default
        except (TypeError, ValueError):
            return default

    def _retrieval_items() -> list[dict[str, Any]]:
        try:
            parsed = json.loads(str(row.get("retrieval_items_json") or "[]"))
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    return QAResult(
        question_id=str(row.get("question_id") or "").strip(),
        sample_id=str(row.get("sample_id") or ""),
        category=str(row.get("category") or ""),
        question=str(row.get("question") or ""),
        answer=str(row.get("answer") or ""),
        response=str(row.get("response") or ""),
        retrieval_items=_retrieval_items(),
        retrieval_error=str(row.get("retrieval_error") or ""),
        llm_error=str(row.get("llm_error") or ""),
        elapsed_s=_float("elapsed_s"),
        prompt_tokens=_int("prompt_tokens"),
        completion_tokens=_int("completion_tokens"),
        tool_call_count=_int("tool_call_count"),
        iterations=_int("iterations", 1),
        qa_profile=str(row.get("qa_profile") or ""),
        retrieval_latency_s=_float("retrieval_latency_ms") / 1000,
        llm_latency_s=_float("llm_total_ms") / 1000,
        model_retry_count=None,
        model_usage_observed=bool(
            str(row.get("answer_total_tokens") or "").strip()
        ),
        retrieval_status=str(row.get("retrieval_status") or ""),
        answer_status=str(row.get("answer_status") or ""),
        model_status=str(row.get("model_status") or ""),
        health_status=str(row.get("health_status") or ""),
    )


def is_healthy_qa_result(result: QAResult) -> bool:
    """A resume-reusable result must have a response and no errors."""
    return bool(
        result.question_id
        and result.response.strip()
        and not result.retrieval_error
        and not result.llm_error
    )


def resolve_resume_csv(source: str | Path) -> Path:
    """Resolve a resume source to a qa_results CSV (final or checkpoint)."""
    found = find_qa_resume_csv(source)
    if found is not None:
        return found
    path = Path(source).expanduser().resolve()
    if not path.is_dir() and not path.is_file():
        raise ValueError(f"QA resume source does not exist: {path}")
    raise ValueError(
        "QA resume directory contains neither qa_results.csv nor "
        f"qa_results.checkpoint.csv: {path}"
    )


def find_qa_resume_csv(source: str | Path) -> Path | None:
    """Return a prior run's QA results CSV if one exists, else None.

    Used to decide whether QA resume applies: a source run interrupted during
    the import phase has no qa_results.csv yet, in which case resume should
    run the full QA instead of failing.
    """
    path = Path(source).expanduser().resolve()
    if path.is_file():
        return path
    if not path.is_dir():
        return None
    for filename in ("qa_results.csv", "qa_results.checkpoint.csv"):
        candidate = path / filename
        if candidate.is_file():
            return candidate
    return None


def load_resume_qa_results(
    source: str | Path,
    expected_tasks: list[dict[str, Any]],
) -> ResumeQAState:
    """Load healthy QA results from a prior run, scoped to the current tasks.

    Rows whose question/answer/sample_id no longer match the current dataset
    are discarded instead of reused, so a resume against a different dataset
    never reuses stale answers.
    """
    source_csv = resolve_resume_csv(source)
    expected = {str(task["question_id"]): task for task in expected_tasks}
    results: list[QAResult] = []
    discarded: list[str] = []
    for row in read_dict_rows(source_csv):
        result = parse_qa_result_from_row(row)
        question_id = result.question_id
        if not question_id:
            continue
        task = expected.get(question_id)
        if task is None:
            continue
        if (
            result.question != str(task.get("question") or "")
            or result.answer != str(task.get("answer") or "")
            or result.sample_id != str(task.get("sample_id") or "")
        ):
            discarded.append(question_id)
            continue
        if is_healthy_qa_result(result):
            results.append(result)
        else:
            discarded.append(question_id)
    return ResumeQAState(
        source_csv=source_csv,
        results=results,
        discarded_question_ids=discarded,
    )


def load_prior_import_rows(source: str | Path) -> list[dict]:
    """Load ``import_results.csv`` from a prior run directory."""
    source_path = Path(source).expanduser().resolve()
    csv_path = (
        source_path / "import_results.csv"
        if source_path.is_dir()
        else source_path.parent / "import_results.csv"
    )
    if not csv_path.is_file():
        return []
    return read_dict_rows(csv_path)


def manifest_differences(
    expected: dict[str, Any],
    actual: dict[str, Any],
    prefix: str = "",
) -> list[str]:
    """Return human-readable differences between an expected and actual manifest."""
    differences: list[str] = []
    for key, expected_value in expected.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in actual:
            differences.append(f"{path}: missing")
            continue
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, dict):
                differences.append(f"{path}: expected object, got {actual_value!r}")
            else:
                differences.extend(
                    manifest_differences(expected_value, actual_value, path)
                )
        elif actual_value != expected_value:
            differences.append(
                f"{path}: expected {expected_value!r}, got {actual_value!r}"
            )
    return differences
