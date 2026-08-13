"""LongMemEval per-question memory import."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tqdm import tqdm

from shared.eval_base import EvalConfig
from shared.import_guard import SUCCESS_STATUSES


IMPORT_FIELDS = (
    "question_id",
    "session_id",
    "status",
    "messages",
    "sessions",
    "elapsed_s",
    "error",
)


@dataclass
class ImportReport:
    rows: list[dict[str, Any]]
    question_to_session: dict[str, str]
    completed: int
    total: int
    incomplete: int


def _fallback_batches(plan: dict[str, Any]) -> list[dict[str, Any]]:
    messages = [
        {
            "role": "user",
            "content": event.get("text", ""),
            "created_at": event.get("time", ""),
        }
        for event in plan.get("events", [])
        if event.get("text")
    ]
    return [{
        "session_key": "default",
        "date_time": "",
        "messages": messages,
    }] if messages else []


def _write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=IMPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def import_longmemeval_memory(
    jobs,
    plans,
    memory_client,
    config: EvalConfig,
    result_dir: Path,
    log,
    *,
    prior_import_rows: list[dict] | None = None,
) -> ImportReport:
    rows: list[dict[str, Any]] = []
    question_to_session: dict[str, str] = {}
    output_path = result_dir / "import_results.csv"

    # Build a map of previously completed imports for --resume.
    completed_map: dict[str, str] = {}
    if prior_import_rows:
        for prior in prior_import_rows:
            status = str(prior.get("status") or "").strip().lower()
            if status in SUCCESS_STATUSES:
                question_id = str(prior.get("question_id") or "").strip()
                session_id = str(prior.get("session_id") or "").strip()
                if question_id and session_id:
                    completed_map[question_id] = session_id

    for job, plan in tqdm(
        list(zip(jobs, plans)),
        desc="导入记忆",
        unit="q",
    ):
        prior_session_id = completed_map.get(job.question_id, "")
        if prior_session_id:
            question_to_session[job.question_id] = prior_session_id
            rows.append({
                "question_id": job.question_id,
                "session_id": prior_session_id,
                "status": "reused",
                "messages": 0,
                "sessions": 0,
                "elapsed_s": 0,
                "error": "",
            })
            log.info(
                "  %s: reused (prior session=%s)",
                job.question_id,
                prior_session_id,
            )
            _write_results(output_path, rows)
            continue

        session_id = ""
        archive_id = ""
        try:
            batches = list(plan.get("session_batches") or [])
            if not batches:
                batches = _fallback_batches(plan)
            session_id = memory_client.open_session(
                title=f"longmemeval_{job.question_id}"
            )
            message_count = 0
            for batch in batches:
                for message in batch.get("messages", []):
                    content = str(message.get("content") or "")
                    if not content:
                        continue
                    memory_client.add_message(
                        session_id,
                        str(message.get("role") or "user"),
                        content,
                        created_at=str(message.get("created_at") or ""),
                        role_id=str(
                            message.get("speaker")
                            or message.get("role_id")
                            or message.get("role")
                            or ""
                        ),
                    )
                    message_count += 1
            archive_id = memory_client.commit_session(session_id)
            result = memory_client.poll_commit(
                session_id,
                archive_id,
                timeout_s=config.commit_timeout_s,
                poll_interval_s=config.commit_poll_interval_s,
            )
            question_to_session[job.question_id] = session_id
            rows.append({
                "question_id": job.question_id,
                "session_id": session_id,
                "status": result.status,
                "messages": message_count,
                "sessions": len(batches),
                "elapsed_s": round(result.elapsed_s, 1),
                "error": result.error,
            })
            log.info(
                "  %s: %s (%.1fs, %d msgs, %d sessions)",
                job.question_id,
                result.status,
                result.elapsed_s,
                message_count,
                len(batches),
            )
        except Exception as exc:
            log.error("  导入 %s 失败: %s", job.question_id, exc)
            rows.append({
                "question_id": job.question_id,
                "session_id": session_id,
                "status": "error",
                "messages": 0,
                "sessions": 0,
                "elapsed_s": 0,
                "error": str(exc),
            })
        _write_results(output_path, rows)

    _write_results(output_path, rows)
    log.info("导入结果已保存: %s", output_path)
    completed = sum(
        1 for row in rows
        if str(row["status"]).strip().lower() in SUCCESS_STATUSES
    )
    return ImportReport(
        rows=rows,
        question_to_session=question_to_session,
        completed=completed,
        total=len(rows),
        incomplete=len(rows) - completed,
    )
