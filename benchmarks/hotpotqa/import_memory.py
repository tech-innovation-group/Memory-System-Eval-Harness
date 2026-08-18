"""HotpotQA global, per-question, and document import workflows."""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
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
    document_path_titles: dict[str, str] = field(default_factory=dict)


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


def sanitize_resource_id(text: str, limit: int = 80) -> str:
    """Sanitize a string into a safe resource path segment."""
    value = re.sub(r"[^\w.\-]+", "_", str(text or "")).strip("._-")
    value = value[:limit].rstrip(".")
    return value or "doc"


def build_document_corpus(
    plans: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Dedupe context documents across plans into unique resource entries.

    Each unique ``(title, text)`` document becomes one resource. The path
    embeds a content hash so identical paragraphs share one entry and
    different paragraphs never collide.
    """
    seen: dict[str, str] = {}
    corpus: dict[str, dict[str, str]] = {}
    for plan in plans:
        for doc in plan.get("memory_documents") or []:
            title = str(doc.get("title") or "").strip()
            text = str(doc.get("text") or "").strip()
            if not title or not text:
                continue
            key = hashlib.sha256(f"{title}\x00{text}".encode("utf-8")).hexdigest()
            if key in seen:
                continue
            path = f"hotpotqa/{sanitize_resource_id(title)}-{key[:8]}"
            seen[key] = path
            corpus[path] = {"title": title, "text": text}
    return [
        {"path": path, "title": entry["title"], "text": entry["text"]}
        for path, entry in corpus.items()
    ]


def import_hotpotqa_documents(
    jobs,
    plans,
    memory_client,
    result_dir: Path,
    log,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    """Import the selected questions' context documents as EchoMem resources.

    Returns ``(rows, question_to_session, path_title_map)``. This is the
    document-QA mode: each context passage becomes one resource in EchoMem's
    document memory (resource_engine); no session/commit is involved, so the
    import only pays chunk+embed indexing cost.

    The indexing is asynchronous: ``add_resource`` only writes the file and
    returns immediately. Two progress bars make that visible: 「提交文档」
    counts the fast submission loop, 「索引文档」tracks the real indexing
    progress and only completes when every document reached a terminal index
    status - so the eval proceeds to QA exactly when indexing is done.
    """
    if not hasattr(memory_client, "add_resource"):
        raise RuntimeError(
            "documents import mode requires a memory backend with the resource "
            "API (EchoMemClient); pick an agent plugin that provides it"
        )
    corpus = build_document_corpus(plans)
    path_titles: dict[str, str] = {}
    failures: list[tuple[str, str]] = []
    full_paths: list[str] = []
    for entry in tqdm(corpus, desc="提交文档", unit="doc"):
        try:
            memory_client.add_resource(
                entry["path"],
                content=entry["text"],
                name=entry["title"],
                tags=["hotpotqa"],
                metadata={"hotpotqa_title": entry["title"]},
            )
            path_titles[entry["path"]] = entry["title"]
            full_paths.append(f"user/{entry['path']}")
        except Exception as exc:
            failures.append((entry["path"], str(exc)))
            log.error("注入文档 %s 失败: %s", entry["path"], exc)

    if not failures and full_paths:
        # Track the async indexing itself (not the submission loop), so the
        # bar reflects indexed documents and QA starts right after indexing.
        bar: tqdm | None = None
        try:
            bar = tqdm(total=len(full_paths), desc="索引文档", unit="doc")

            def _track_index(done: int, total: int) -> None:
                bar.update(done - bar.n)

            wait = memory_client.wait_for_resource_index(
                full_paths,
                progress=_track_index,
            )
            for path, detail in wait.get("failed", {}).items():
                failures.append((path, f"index failed: {detail}"))
                log.error("文档 %s 索引失败: %s", path, detail)
            log.info(
                "文档索引就绪: %d/%d",
                wait.get("indexed", 0),
                len(full_paths),
            )
        except Exception as exc:
            failures.append(("indexing", str(exc)))
            log.error("等待文档索引失败: %s", exc)
        finally:
            if bar is not None:
                bar.close()
    elif not full_paths:
        log.warning("所选题目无有效文档，语料为空")

    question_to_session = {job.question_id: "" for job in jobs}
    rows = [{
        "question_id": "documents",
        "session_id": "resource_corpus",
        "status": "completed" if not failures else "error",
        "messages": len(corpus) - len(failures),
        "elapsed_s": 0.0,
        "error": "; ".join(f"{p}: {e}" for p, e in failures[:5]),
    }]
    return rows, question_to_session, path_titles


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
    reuse_memory: bool = False,
) -> ImportReport:
    rows: list[dict[str, Any]] = []
    question_to_session: dict[str, str] = {}
    document_path_titles: dict[str, str] = {}
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

    if import_mode == "documents":
        prior_documents_session = completed_map.get("documents", "")
        if reuse_memory or prior_documents_session:
            # --reuse-memory-from / --resume：语料已在记忆后端（同一身份下），
            # 不重新注入，只从数据集重建 path→title 映射供 QA 检索使用。
            corpus = build_document_corpus(plans)
            document_path_titles = {
                entry["path"]: entry["title"] for entry in corpus
            }
            rows = [{
                "question_id": "documents",
                "session_id": prior_documents_session or "resource_corpus",
                "status": "reused",
                "messages": len(corpus),
                "elapsed_s": 0.0,
                "error": "",
            }]
            log.info(
                "documents import reused: %d 篇唯一文档 (path→title 映射 %d 条)",
                len(corpus),
                len(document_path_titles),
            )
        else:
            rows, question_to_session, document_path_titles = import_hotpotqa_documents(
                jobs, plans, memory_client, result_dir, log,
            )
        _write_results(output_path, rows)
    elif import_mode == "global":
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
        document_path_titles=document_path_titles,
    )
