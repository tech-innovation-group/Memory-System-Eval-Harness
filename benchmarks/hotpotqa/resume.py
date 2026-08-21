"""HotpotQA resume manifest construction and validation.

A ``--resume <dir>`` run reuses the prior identity, skips already-completed
import batches, and reuses healthy QA answers.  The manifest guards against
resuming onto a different dataset, import mode, or answer configuration.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from benchmarks.hotpotqa.qa import _safe_question_id
from shared.eval_base import EvalConfig
from shared.qa import QAResult
from shared.resume_qa import (
    ResumeQAState,
    manifest_differences,
)


MANIFEST_FILENAME = "qa_resume_manifest.json"


def build_resume_manifest(
    *,
    dataset_path: str,
    import_mode: str,
    config: EvalConfig,
    memory_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "benchmark": "hotpotqa",
        "dataset_path": str(Path(dataset_path).expanduser().resolve()),
        "import_mode": import_mode,
        "answer_model": {
            "base_url": config.llm_base_url.rstrip("/"),
            "model": config.llm_model,
            "max_tokens": config.llm_max_tokens,
        },
        "qa": {
            "top_k": config.top_k,
            "memory_budget_chars": config.memory_budget_chars,
        },
        "memory_identity": memory_identity or {},
    }


def write_resume_manifest(
    result_dir: Path,
    manifest: dict[str, Any],
) -> Path:
    path = result_dir / MANIFEST_FILENAME
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_resume_manifest(source_dir: Path) -> dict[str, Any]:
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


def validate_resume_manifest(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> None:
    differences = manifest_differences(expected, actual)
    if differences:
        raise ValueError(
            "QA resume configuration mismatch:\n- "
            + "\n- ".join(differences)
        )


def copy_resume_traces(
    state: ResumeQAState,
    result_dir: Path,
) -> int:
    """Copy trace files for reused questions into the new result directory.

    Trace files are named ``<sanitized question_id>.json`` (see qa.py
    ``_write_trace``) and their content does NOT carry a ``question_id``
    field, so matching is done on the filename stem rather than the payload.
    This keeps the resumed result directory equivalent to a from-scratch run:
    every reused question keeps its agent trace.
    """
    source = state.source_csv.parent / "agent_traces"
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
    fields (tool calls, served models, tool protocol hashes, transcript
    reads) and the tool audit layer cover the whole run instead of only
    this segment.
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
