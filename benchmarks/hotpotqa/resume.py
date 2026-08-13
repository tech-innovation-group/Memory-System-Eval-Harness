"""HotpotQA resume manifest construction and validation.

A ``--resume <dir>`` run reuses the prior identity, skips already-completed
import batches, and reuses healthy QA answers.  The manifest guards against
resuming onto a different dataset, import mode, or answer configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared.eval_base import EvalConfig
from shared.resume_qa import (
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
