"""HotpotQA global and per-question import workflows."""

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


def _add_events(memory_client, session_id: str, plan: dict[str, Any]) -> int:
    count = 0
    for event in plan.get("events", []):
        text = str(event.get("text") or "")
        if not text:
            continue
        memory_client.add_message(
            session_id,
            "user",
            text,
            created_at=str(event.get("time") or ""),
        )
        count += 1
    return count


def _write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=IMPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def import_hotpotqa_memory(
    jobs,
    plans,
    memory_client,
    config: EvalConfig,
    result_dir: Path,
    log,
    *,
    import_mode: str,
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

    if import_mode == "global":
        prior_session_id = completed_map.get("global", "")
        if prior_session_id:
            for job in jobs:
                question_to_session[job.question_id] = prior_session_id
            rows.append({
                "question_id": "global",
                "session_id": prior_session_id,
                "status": "reused",
                "messages": 0,
                "elapsed_s": 0,
                "error": "",
            })
            log.info("global import reused (prior session=%s)", prior_session_id)
        else:
            session_id = ""
            try:
                session_id = memory_client.open_session(title="hotpotqa_global")
                message_count = 0
                for plan in tqdm(plans, desc="导入 passages", unit="plan"):
                    message_count += _add_events(memory_client, session_id, plan)
                archive_id = memory_client.commit_session(session_id)
                result = memory_client.poll_commit(
                    session_id,
                    archive_id,
                    timeout_s=config.commit_timeout_s,
                    poll_interval_s=config.commit_poll_interval_s,
                )
                rows.append({
                    "question_id": "global",
                    "session_id": session_id,
                    "status": result.status,
                    "messages": message_count,
                    "elapsed_s": round(result.elapsed_s, 1),
                    "error": result.error,
                })
                for job in jobs:
                    question_to_session[job.question_id] = session_id
            except Exception as exc:
                log.error("HotpotQA global import failed: %s", exc)
                rows.append({
                    "question_id": "global",
                    "session_id": session_id,
                    "status": "error",
                    "messages": 0,
                    "elapsed_s": 0,
                    "error": str(exc),
                })
        _write_results(output_path, rows)
    else:
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
            try:
                session_id = memory_client.open_session(
                    title=f"hotpotqa_{job.question_id}"
                )
                message_count = _add_events(memory_client, session_id, plan)
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
                    "elapsed_s": round(result.elapsed_s, 1),
                    "error": result.error,
                })
            except Exception as exc:
                log.error("  导入 %s 失败: %s", job.question_id, exc)
                rows.append({
                    "question_id": job.question_id,
                    "session_id": session_id,
                    "status": "error",
                    "messages": 0,
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
