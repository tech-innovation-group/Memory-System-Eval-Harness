"""Shared validation for benchmark memory-import stages."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


SUCCESS_STATUSES = {"completed", "done", "success", "succeeded", "ok", "reused"}


def incomplete_imports(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("status") or "").strip().lower() not in SUCCESS_STATUSES
    ]


def require_complete_imports(
    rows: list[dict[str, Any]],
    *,
    allow_incomplete: bool = False,
) -> None:
    if not rows:
        raise RuntimeError("memory import produced no records; check the dataset and sample filter")
    failed = incomplete_imports(rows)
    if not failed or allow_incomplete:
        return
    details_list = []
    for row in failed[:8]:
        identifier = str(
            row.get("question_id")
            or row.get("sample_id")
            or row.get("session_id")
            or "unknown"
        )
        detail = identifier + "=" + str(row.get("status") or "missing")
        error = str(row.get("error") or "").strip()
        if error:
            detail += f" ({error[:300]})"
        details_list.append(detail)
    details = ", ".join(details_list)
    if len(failed) > 8:
        details += f", ... (+{len(failed) - 8})"
    raise RuntimeError(
        f"memory import incomplete for {len(failed)}/{len(rows)} records: {details}. "
        "QA was not started. Use --allow-diagnostics only for diagnostics."
    )
