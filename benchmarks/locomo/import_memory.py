"""LoCoMo conversation import workflow."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tqdm import tqdm

from shared.eval_base import EvalConfig
from shared.import_guard import SUCCESS_STATUSES


IMPORT_FIELDS = (
    "sample_id",
    "session_key",
    "session_id",
    "archive_id",
    "status",
    "elapsed_s",
    "message_count",
    "submitted_messages",
    "error",
)


@dataclass(frozen=True)
class ImportOptions:
    session_mode: str
    max_sessions: int
    resume_qa: bool
    sample_filter: str
    prior_import_rows: list[dict] | None


@dataclass
class ImportReport:
    rows: list[dict[str, Any]]
    sample_to_session_ids: dict[str, list[str]]
    completed: int
    total: int
    incomplete: int
    expected_messages: int = 0
    submitted_messages: int = 0


def resolve_session_mode(requested: str, plan_count: int) -> str:
    mode = requested
    if mode == "auto":
        mode = "locomo" if plan_count <= 1 else "single"
    if mode == "locomo" and plan_count > 1:
        raise ValueError(
            "session-mode locomo cannot safely isolate multiple samples; "
            "use --session-mode auto or single"
        )
    return mode


def selected_session_batches(
    plan: dict[str, Any],
    *,
    session_mode: str,
    max_sessions: int,
) -> list[dict[str, Any]]:
    batches = list(plan.get("session_batches") or [])
    if max_sessions > 0:
        batches = batches[:max_sessions]
    if session_mode == "single" and batches:
        return [{
            "session_key": "all",
            "date_time": "",
            "messages": [
                message
                for batch in batches
                for message in batch.get("messages", [])
            ],
        }]
    return batches


def _write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=IMPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def load_import_report(source: str | Path) -> ImportReport:
    """Load a completed import report without touching the memory backend.

    This is intentionally separate from ``import_locomo_memory``: QA matrix
    runs must be able to reuse one immutable injection and must not call
    ``open_session``, ``add_message`` or ``commit_session`` again.
    """
    source_path = Path(source).expanduser().resolve()
    run_dir = source_path if source_path.is_dir() else source_path.parent
    csv_path = run_dir / "import_results.csv"
    if not csv_path.is_file():
        raise ValueError(
            f"QA-only source has no import_results.csv: {csv_path}"
        )
    manifest_path = run_dir / "qa_resume_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(
            f"QA-only source has no qa_resume_manifest.json: {manifest_path}"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"QA-only source has invalid qa_resume_manifest.json: {manifest_path}"
        ) from exc
    if manifest.get("benchmark") != "locomo":
        raise ValueError(
            f"QA-only source manifest is not LoCoMo: {manifest_path}"
        )

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"QA-only source import_results.csv is empty: {csv_path}")

    sample_to_session_ids: dict[str, list[str]] = {}
    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        if status not in SUCCESS_STATUSES:
            raise ValueError(
                "QA-only source contains incomplete import batch "
                f"{row.get('sample_id')}/{row.get('session_key')}: "
                f"{row.get('status') or 'missing'}"
            )
        sample_id = str(row.get("sample_id") or "")
        session_id = str(row.get("session_id") or "")
        if not sample_id or not session_id:
            raise ValueError(
                "QA-only source has an import row without sample_id/session_id"
            )
        sample_to_session_ids.setdefault(sample_id, []).append(session_id)

    return ImportReport(
        rows=rows,
        sample_to_session_ids=sample_to_session_ids,
        completed=len(rows),
        total=len(rows),
        incomplete=0,
        expected_messages=sum(int(row.get("message_count") or 0) for row in rows),
        submitted_messages=sum(
            int(row.get("submitted_messages") or 0) for row in rows
        ),
    )


def import_locomo_memory(
    plans: list[dict[str, Any]],
    memory_client,
    config: EvalConfig,
    options: ImportOptions,
    result_dir: Path,
    log,
) -> ImportReport:
    rows: list[dict[str, Any]] = []
    sample_to_session_ids: dict[str, list[str]] = {}
    output_path = result_dir / "import_results.csv"

    # Build a map of previously completed batches for resume-qa
    completed_map: dict[tuple[str, str], str] = {}
    if options.resume_qa and options.prior_import_rows:
        for prior in options.prior_import_rows:
            status = str(prior.get("status") or "").strip().lower()
            if status in SUCCESS_STATUSES:
                key = (
                    str(prior.get("sample_id") or ""),
                    str(prior.get("session_key") or ""),
                )
                session_id = str(prior.get("session_id") or "")
                if session_id:
                    completed_map[key] = session_id

    for plan in tqdm(plans, desc="导入记忆", unit="sample"):
        sample_id = str(plan["sample_id"])
        batches = selected_session_batches(
            plan,
            session_mode=options.session_mode,
            max_sessions=options.max_sessions,
        )
        sample_to_session_ids[sample_id] = []
        if not batches:
            rows.append({
                "sample_id": sample_id,
                "session_key": "",
                "session_id": "",
                "archive_id": "",
                "status": "error",
                "elapsed_s": 0,
                "message_count": 0,
                "submitted_messages": 0,
                "error": "no LoCoMo session batches found",
            })
            continue

        for batch in tqdm(
            batches,
            desc=f"  {sample_id}",
            unit="session",
            leave=False,
        ):
            session_key = str(batch.get("session_key") or "")
            completed_key = (sample_id, session_key)

            # Skip already-completed batches when resuming
            if completed_key in completed_map:
                prior_session_id = completed_map[completed_key]
                sample_to_session_ids[sample_id].append(prior_session_id)
                rows.append({
                    "sample_id": sample_id,
                    "session_key": session_key,
                    "session_id": prior_session_id,
                    "archive_id": "",
                    "status": "reused",
                    "elapsed_s": 0,
                    "message_count": 0,
                    "submitted_messages": 0,
                    "error": "",
                })
                log.info(
                    "  %s/%s: reused (prior session=%s)",
                    sample_id,
                    session_key,
                    prior_session_id,
                )
                continue

            # Normal injection
            session_id = ""
            archive_id = ""
            messages = list(batch.get("messages") or [])
            submitted_messages = 0
            try:
                session_id = memory_client.open_session(
                    title=f"locomo_{sample_id}_{session_key or 'session'}"
                )
                sample_to_session_ids[sample_id].append(session_id)
                for message in messages:
                    content = str(message.get("content") or "")
                    if not content:
                        continue
                    memory_client.add_message(
                        session_id,
                        str(message.get("role") or "user"),
                        content,
                        created_at=str(message.get("created_at") or ""),
                        role_id=str(
                            message.get("role_id")
                            or message.get("role")
                            or ""
                        ),
                    )
                    submitted_messages += 1
                archive_id = memory_client.commit_session(session_id)
                result = memory_client.poll_commit(
                    session_id,
                    archive_id,
                    timeout_s=config.commit_timeout_s,
                    poll_interval_s=config.commit_poll_interval_s,
                )
                rows.append({
                    "sample_id": sample_id,
                    "session_key": session_key,
                    "session_id": session_id,
                    "archive_id": archive_id,
                    "status": result.status,
                    "elapsed_s": round(result.elapsed_s, 1),
                    "message_count": len(messages),
                    "submitted_messages": submitted_messages,
                    "error": result.error,
                })
                log.info(
                    "  %s/%s: %s (%.1fs, %d msgs)",
                    sample_id,
                    session_key,
                    result.status,
                    result.elapsed_s,
                    len(messages),
                )
            except Exception as exc:
                log.error(
                    "  %s/%s 导入失败: %s",
                    sample_id,
                    session_key,
                    exc,
                )
                rows.append({
                    "sample_id": sample_id,
                    "session_key": session_key,
                    "session_id": session_id,
                    "archive_id": archive_id,
                    "status": "error",
                    "elapsed_s": 0,
                    "message_count": len(messages),
                    "submitted_messages": submitted_messages,
                    "error": str(exc),
                })

            # 增量落盘：每完成一个 batch 就写一次，导入中断后 source 目录
            # 仍留有已完成部分，--resume 可据此跳过、只补缺失的 batch。
            _write_results(output_path, rows)

    _write_results(output_path, rows)
    log.info("导入结果已保存: %s", output_path)

    completed = sum(
        1 for row in rows
        if str(row["status"]).strip().lower() in SUCCESS_STATUSES
    )
    return ImportReport(
        rows=rows,
        sample_to_session_ids=sample_to_session_ids,
        completed=completed,
        total=len(rows),
        incomplete=len(rows) - completed,
        expected_messages=sum(
            int(row.get("message_count") or 0) for row in rows
        ),
        submitted_messages=sum(
            int(row.get("submitted_messages") or 0) for row in rows
        ),
    )
