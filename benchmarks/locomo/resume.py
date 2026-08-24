"""Validated LoCoMo QA checkpoint loading and resume metadata."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.locomo.profiles import profile_source
from benchmarks.locomo.qa import QAOptions, _safe_question_id
from shared.csv_io import read_dict_rows
from shared.eval_base import EvalConfig
from shared.qa import QAResult


MANIFEST_FILENAME = "qa_resume_manifest.json"
JUDGE_MANIFEST_FILENAME = "judge_resume_manifest.json"
QA_CONTRACT_FILES = (
    "plugins/echomem_mcp/plugin.py",
    "plugins/echomem_mcp/runtime.py",
    "plugins/vikingbot/runtime.py",
    "plugins/vikingbot/tools.py",
    "plugins/vikingbot/prompting.py",
    "plugins/vikingbot/vikingboat0411_prompting.py",
    "benchmarks/locomo/profiles/vikingboat0411_natural_no_tools.py",
    "plugins/vikingbot/answers.py",
    "benchmarks/locomo/qa.py",
    "benchmarks/locomo/memory_scope.py",
    "backends/echomem/client.py",
    "backends/memory_types.py",
)


@dataclass(frozen=True)
class QAResumeState:
    source_csv: Path
    source_dir: Path
    results: list[QAResult]
    discarded_question_ids: list[str]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class JudgeResumeState:
    source_csv: Path
    source_dir: Path
    rows: list[dict[str, str]]
    manifest: dict[str, Any]


def build_qa_resume_manifest(
    *,
    dataset_path: str,
    sample_filter: str,
    session_mode: str,
    config: EvalConfig,
    options: QAOptions,
    memory_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    project_root = Path(__file__).resolve().parents[2]
    contract_files: dict[str, str] = {}
    for relative_path in QA_CONTRACT_FILES:
        path = project_root / relative_path
        if path.is_file():
            contract_files[relative_path] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    contract_payload = json.dumps(
        {
            "profile": options.profile,
            "profile_source": profile_source(options.profile),
            "options": {
                "top_k": options.top_k,
                "memory_budget_chars": options.memory_budget_chars,
                "tools_enabled": options.tools_enabled,
                "checkpoint_interval": options.checkpoint_interval,
                "system_prompt_append_sha256": (
                    options.system_prompt_append_sha256
                ),
                "agent_options": options.agent_options,
            },
            "files": contract_files,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return {
        "schema_version": 2,
        "benchmark": "locomo",
        "dataset_path": str(Path(dataset_path).expanduser().resolve()),
        "sample_filter": sample_filter,
        "session_mode": session_mode,
        "memory_identity": memory_identity or {},
        "answer_model": {
            "base_url": config.llm_base_url.rstrip("/"),
            "model": config.llm_model,
            "max_tokens": config.llm_max_tokens,
        },
        "qa": {
            "profile": options.profile,
            "top_k": config.top_k,
            "memory_budget_chars": config.memory_budget_chars,
            "tools_enabled": options.tools_enabled,
            "question_timeout_s": config.question_timeout_s,
            "system_prompt_append_sha256": (
                options.system_prompt_append_sha256
            ),
            "system_prompt_append_source": (
                options.system_prompt_append_source
            ),
            "agent_options": options.agent_options,
        },
        "qa_contract": {
            "sha256": hashlib.sha256(contract_payload).hexdigest(),
            "files": contract_files,
        },
    }


def write_qa_resume_manifest(
    result_dir: Path,
    manifest: dict[str, Any],
) -> Path:
    path = result_dir / MANIFEST_FILENAME
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def build_judge_resume_manifest(
    *,
    base_url: str,
    model: str,
    system_prompt: str,
    prompt_template: str,
) -> dict[str, Any]:
    prompt_payload = json.dumps(
        {
            "system": system_prompt,
            "template": prompt_template,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "benchmark": "locomo",
        "judge": {
            "base_url": base_url.rstrip("/"),
            "model": model,
            "prompt_sha256": hashlib.sha256(prompt_payload).hexdigest(),
        },
    }


def write_judge_resume_manifest(
    result_dir: Path,
    manifest: dict[str, Any],
) -> Path:
    path = result_dir / JUDGE_MANIFEST_FILENAME
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _resolve_resume_csv(source: str | Path) -> Path:
    path = Path(source).expanduser().resolve()
    if path.is_file():
        return path
    if not path.is_dir():
        raise ValueError(f"QA resume source does not exist: {path}")
    for filename in ("qa_results.csv", "qa_results.checkpoint.csv"):
        candidate = path / filename
        if candidate.is_file():
            return candidate
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


def find_judge_resume_csv(source: str | Path) -> Path | None:
    """Return a prior run's judge results CSV if one exists, else None.

    Mirrors ``find_qa_resume_csv`` for the judge phase: a source run with no
    judge_results.csv yet (import-only or QA-only interruption) should run
    the full judge instead of failing.
    """
    path = Path(source).expanduser().resolve()
    if path.is_file():
        return path
    if not path.is_dir():
        return None
    for filename in ("judge_results.csv", "judge_results.checkpoint.csv"):
        candidate = path / filename
        if candidate.is_file():
            return candidate
    return None


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or default))
    except (TypeError, ValueError):
        return default


def _parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _parse_optional_int(value: Any) -> int | None:
    text = str(value or "").strip()
    return _parse_int(text) if text else None


def _parse_retrieval_items(value: Any) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _result_from_row(row: dict[str, str]) -> QAResult:
    return QAResult(
        question_id=str(row.get("question_id") or "").strip(),
        sample_id=str(row.get("sample_id") or ""),
        category=str(row.get("category") or ""),
        question=str(row.get("question") or ""),
        answer=str(row.get("answer") or ""),
        response=str(row.get("response") or ""),
        retrieval_items=_parse_retrieval_items(
            row.get("retrieval_items_json")
        ),
        retrieval_error=str(row.get("retrieval_error") or ""),
        llm_error=str(row.get("llm_error") or ""),
        elapsed_s=_parse_float(row.get("elapsed_s")),
        prompt_tokens=_parse_int(row.get("prompt_tokens")),
        completion_tokens=_parse_int(row.get("completion_tokens")),
        tool_call_count=_parse_int(row.get("tool_call_count")),
        iterations=_parse_int(row.get("iterations"), 1),
        qa_profile=str(row.get("qa_profile") or "vikingboat0411"),
        retrieval_latency_s=(
            _parse_float(row.get("retrieval_latency_ms")) / 1000
        ),
        orchestration_latency_s=max(
            0.0,
            (
                _parse_float(row.get("injection_total_ms"))
                - _parse_float(row.get("retrieval_latency_ms"))
            )
            / 1000,
        ),
        llm_latency_s=_parse_float(row.get("llm_total_ms")) / 1000,
        model_retry_count=_parse_optional_int(
            row.get("model_retry_count")
        ),
        model_usage_observed=bool(
            str(row.get("answer_total_tokens") or "").strip()
        ),
        retrieval_status=str(row.get("retrieval_status") or ""),
        answer_status=str(row.get("answer_status") or ""),
        model_status=str(row.get("model_status") or ""),
        health_status=str(row.get("health_status") or ""),
    )


def _healthy_result(result: QAResult) -> bool:
    retrieval, answer, model, health = result.resolved_statuses()
    return bool(
        result.question_id
        and result.response.strip()
        and not result.retrieval_error
        and not result.llm_error
        and answer == "ok"
        and model == "ok"
    )


def _load_manifest(source_dir: Path) -> dict[str, Any]:
    path = source_dir / MANIFEST_FILENAME
    if not path.is_file():
        raise ValueError(
            f"QA resume source is missing required {MANIFEST_FILENAME}: "
            f"{source_dir}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid QA resume manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"QA resume manifest is not an object: {path}")
    return payload


def _load_named_manifest(
    source_dir: Path,
    filename: str,
) -> dict[str, Any]:
    path = source_dir / filename
    if not path.is_file():
        raise ValueError(
            f"resume source is missing required {filename}: {source_dir}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid resume manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"resume manifest is not an object: {path}")
    return payload


def _manifest_differences(
    expected: dict[str, Any],
    actual: dict[str, Any],
    prefix: str = "",
) -> list[str]:
    differences: list[str] = []
    for key, expected_value in expected.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in actual:
            differences.append(f"{path}: missing")
            continue
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, dict):
                differences.append(
                    f"{path}: expected object, got {actual_value!r}"
                )
            else:
                differences.extend(
                    _manifest_differences(
                        expected_value,
                        actual_value,
                        path,
                    )
                )
        elif actual_value != expected_value:
            differences.append(
                f"{path}: expected {expected_value!r}, got {actual_value!r}"
            )
    return differences


def load_qa_resume_state(
    source: str | Path,
    *,
    tasks: list[dict[str, Any]],
    expected_manifest: dict[str, Any],
) -> QAResumeState:
    source_csv = _resolve_resume_csv(source)
    source_dir = source_csv.parent
    manifest = _load_manifest(source_dir)
    differences = _manifest_differences(expected_manifest, manifest)
    if differences:
        raise ValueError(
            "QA resume configuration mismatch:\n- "
            + "\n- ".join(differences)
        )

    expected_tasks = {
        str(task["question_id"]): task
        for task in tasks
    }
    seen: set[str] = set()
    results: list[QAResult] = []
    discarded: list[str] = []
    for row in read_dict_rows(source_csv):
        result = _result_from_row(row)
        question_id = result.question_id
        if not question_id:
            continue
        if question_id in seen:
            raise ValueError(
                f"duplicate question_id in QA resume CSV: {question_id}"
            )
        seen.add(question_id)
        task = expected_tasks.get(question_id)
        if task is None:
            continue
        if (
            result.question != str(task.get("question") or "")
            or result.answer != str(task.get("answer") or "")
            or result.sample_id != str(task.get("sample_id") or "")
        ):
            raise ValueError(
                "QA resume row does not match current dataset content: "
                f"{question_id}"
            )
        if result.qa_profile != str(
            expected_manifest["qa"]["profile"]
        ):
            raise ValueError(
                "QA resume row profile mismatch for "
                f"{question_id}: {result.qa_profile}"
            )
        if _healthy_result(result):
            results.append(result)
        else:
            discarded.append(question_id)

    return QAResumeState(
        source_csv=source_csv,
        source_dir=source_dir,
        results=results,
        discarded_question_ids=discarded,
        manifest=manifest,
    )


def load_judge_resume_state(
    source: str | Path,
    *,
    expected_manifest: dict[str, Any],
) -> JudgeResumeState:
    path = Path(source).expanduser().resolve()
    if path.is_dir():
        source_dir = path
        for filename in (
            "judge_results.csv",
            "judge_results.checkpoint.csv",
        ):
            candidate = path / filename
            if candidate.is_file():
                source_csv = candidate
                break
        else:
            raise ValueError(
                "Judge resume directory contains neither judge_results.csv "
                f"nor judge_results.checkpoint.csv: {path}"
            )
    elif path.is_file():
        source_csv = path
        source_dir = path.parent
    else:
        raise ValueError(f"Judge resume source does not exist: {path}")

    manifest = _load_named_manifest(
        source_dir,
        JUDGE_MANIFEST_FILENAME,
    )
    differences = _manifest_differences(expected_manifest, manifest)
    if differences:
        raise ValueError(
            "Judge resume configuration mismatch:\n- "
            + "\n- ".join(differences)
        )
    rows = read_dict_rows(source_csv)
    return JudgeResumeState(
        source_csv=source_csv,
        source_dir=source_dir,
        rows=rows,
        manifest=manifest,
    )


def copy_resume_traces(
    state: QAResumeState,
    result_dir: Path,
) -> int:
    """Copy trace files for reused questions into the new result directory.

    Trace files are named ``<sanitized question_id>.json`` (see qa.py
    ``_write_trace``) and their content does NOT carry a ``question_id``
    field, so matching is done on the filename stem rather than the payload.
    This keeps the resumed result directory equivalent to a from-scratch run:
    every reused question keeps its agent trace.
    """
    source = state.source_dir / "agent_traces"
    if not source.is_dir():
        return 0
    destination = result_dir / "agent_traces"
    destination.mkdir(parents=True, exist_ok=True)
    reusable_safe_ids = {
        _safe_question_id(result.question_id)
        for result in state.results
    }
    copied = 0
    for path in source.glob("*.json"):
        if path.stem not in reusable_safe_ids:
            continue
        shutil.copy2(path, destination / path.name)
        copied += 1
    return copied


def restore_resume_traces(
    results: list[QAResult],
    result_dir: Path,
) -> int:
    """Read per-question traces back into reused QAResult objects.

    Reused results are loaded from a prior run's CSV, which carries no
    trace; ``copy_resume_traces`` copies the trace files into the new
    result_dir, and this function loads them so trace-dependent summary
    fields (served models, tool protocol hashes, transcript reads) are
    computed over the whole run instead of only this segment.
    """
    trace_dir = result_dir / "agent_traces"
    if not trace_dir.is_dir():
        return 0
    restored = 0
    for result in results:
        if result.trace:
            continue
        path = trace_dir / f"{_safe_question_id(result.question_id)}.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            result.trace = payload
            restored += 1
    return restored
